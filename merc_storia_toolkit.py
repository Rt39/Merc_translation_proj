"""Merc Storia Translation Toolkit

End-to-end pipeline for extracting, translating, and repacking every piece of
translatable Japanese text in the game (story dialogue + master data).

Usage
-----
    uv run merc_storia_toolkit.py extract
        Extract everything: stories AND misc MasterData. Equivalent to running
        `extract-story` then `extract-misc`.

    uv run merc_storia_toolkit.py extract-story
        Stories only → extracted_data/story/<story_id>.json (one file per story),
        with title / episode / chapter_name / display_order at the head of each
        file so the translator has context immediately.

    uv run merc_storia_toolkit.py extract-misc
        MasterData bundles with JP text → extracted_data/misc/<AssetName>.json.

    uv run merc_storia_toolkit.py repack
        Repack everything (stories + misc) that has been modified.

    uv run merc_storia_toolkit.py repack-story
        Repack only modified story JSONs → repacked_bundles/story/<bundle>.

    uv run merc_storia_toolkit.py repack-misc
        Repack only modified misc JSONs → repacked_bundles/misc/<bundle>.

    uv run merc_storia_toolkit.py test-repack
        Round-trip a single bundle to confirm the pipeline.

Translation workflow
--------------------
Translators edit the JSON files **in place** — replace the original Japanese
strings with translations in `scenes[*].speakers` / `scenes[*].text` (stories)
or `strings[*].value` (misc). The repacker walks each bundle, finds every
string slot at its original offset, and substitutes the value from the JSON.
Anything the translator left unchanged is preserved byte-for-byte. No separate
"translations" dict needed.

Modification tracking
---------------------
At extract time, the SHA-256 of every output JSON is recorded in
`extracted_data/.fingerprints.pkl`. At repack time, files whose current hash
matches the recorded baseline are treated as untouched and skipped — only the
files the translator actually edited get repacked. Pass `--force` to repack
unconditionally.

Encryption: AES-256-CBC-PKCS7
    Key: PBKDF2-HMAC-SHA256(password="2147483647", salt="-2147483648",
                            iterations=1024, dklen=32)
    IV:  First 16 bytes of ciphertext (prepended)

Data format: MemoryPack (UTF-8 mode)
    Strings: int32 ~utf8_byte_count, int32 char_count, byte[utf8_byte_count]
    Null:    int32(-1)
    Empty:   int32(0)
"""
import sys, struct, os, json, time, argparse, pickle, hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding
import UnityPy

import mercstoria_config as cfg
cfg.enable_utf8_stdout()

# === Crypto ===

AES_KEY = cfg.derive_aes_key()


def decrypt(data: bytes) -> bytes:
    iv, ct = data[:16], data[16:]
    dec = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv)).decryptor()
    pt = dec.update(ct) + dec.finalize()
    pad = pt[-1]
    if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
        return pt[:-pad]
    return pt


def encrypt(pt: bytes) -> bytes:
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    body = padder.update(pt) + padder.finalize()
    enc = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv)).encryptor()
    return iv + enc.update(body) + enc.finalize()


# === MemoryPack ===

def read_string(data: bytes, pos: int):
    """Read one MemoryPack string from `data` at `pos`. Returns (value, next_pos).

    Wire format (UTF-8 mode, see https://github.com/Cysharp/MemoryPack):
        int32  header     ~utf8_byte_count       (-1 = null, 0 = empty)
        int32  char_count UTF-16 unit count      (ignored on read)
        bytes  payload    UTF-8 encoded value

    `value` is `None` on parse failure as well as for the explicit-null
    case; callers that need to disambiguate check whether `next_pos`
    advanced (failure = no advance past the header).
    """
    if pos + 4 > len(data):
        return None, pos
    raw = struct.unpack_from('<i', data, pos)[0]
    pos += 4
    if raw == -1:
        return None, pos
    if raw == 0:
        return "", pos
    # MemoryPack stores ~(byte_count) as the header so non-null/non-empty
    # strings have a negative int32 prefix. `bc` is the real byte length.
    bc = ~raw
    if bc > 0 and bc < 100000 and pos + 4 + bc <= len(data):
        struct.unpack_from('<i', data, pos)[0]  # char_count (UTF-16 units), ignored
        pos += 4
        try:
            return data[pos:pos + bc].decode('utf-8'), pos + bc
        except UnicodeDecodeError:
            return None, pos
    return None, pos


def write_string(s) -> bytes:
    """Encode `s` as a MemoryPack string. Inverse of `read_string`.

    `char_count` is the Python `len(s)` — the count of UTF-16 code points
    that MemoryPack-CSharp would have emitted. For surrogate-pair characters
    this differs from `len(s.encode('utf-16-le')) // 2`; in practice no
    in-game text uses astral codepoints so this approximation is exact.
    """
    if s is None:
        return struct.pack('<i', -1)
    if s == "":
        return struct.pack('<i', 0)
    enc = s.encode('utf-8')
    return struct.pack('<i', ~len(enc)) + struct.pack('<i', len(s)) + enc


# === Bundle I/O ===

def extract_textasset_raw(bundle_path: str):
    """Pull the (name, encrypted_payload) tuple out of a story / master-data bundle.

    Each Merc Storia data bundle contains exactly one Unity `TextAsset` whose
    serialized layout is:

        int32  name_length
        bytes  name           (UTF-8)
        pad to 4-byte boundary
        int32  script_length
        bytes  script         (AES-256-CBC ciphertext with prepended IV)

    Returns (None, None) if no TextAsset is found, which would indicate the
    bundle is structurally different (e.g. a font or scene bundle).
    """
    env = UnityPy.load(bundle_path)
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            obj.reset()
            r = obj.reader
            r.Position = obj.byte_start
            raw = r.read(obj.byte_size)
            pos = 0
            name_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            name = raw[pos:pos + name_len].decode('utf-8', errors='replace'); pos += name_len
            # Unity aligns the script field to a 4-byte boundary.
            pos = (pos + 3) & ~3
            script_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            return name, raw[pos:pos + script_len]
    return None, None


def repack_bundle(original_path: str, output_path: str, mutate_plaintext):
    """Round-trip a bundle through decrypt → mutate → re-encrypt → save.

    Parameters:
        original_path:   path to the unmodified bundle (kept read-only).
        output_path:     where to write the modified bundle.
        mutate_plaintext: callable `bytes -> bytes`. Receives the decrypted
                         MemoryPack payload and returns the new bytes. Free
                         to grow / shrink the payload — the surrounding
                         TextAsset envelope is reconstructed around the new
                         length and the AES re-encryption picks a fresh IV.

    The new bundle has the same CAB / archive layout as the original; only
    the TextAsset's `script_length` and `script` bytes change. CRC patches
    (see `patch_crc.py`) must already be in place or Unity will reject the
    altered bundle and silently re-download from the CDN.
    """
    env = UnityPy.load(original_path)
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        obj.reset()
        r = obj.reader
        r.Position = obj.byte_start
        raw = r.read(obj.byte_size)

        pos = 0
        name_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
        name_bytes = raw[pos:pos + name_len]; pos += name_len
        pos = (pos + 3) & ~3
        script_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
        enc_data = raw[pos:pos + script_len]

        pt = decrypt(enc_data)
        modified = mutate_plaintext(pt)
        new_enc = encrypt(modified)

        new_raw = bytearray()
        new_raw.extend(struct.pack('<i', len(name_bytes)))
        new_raw.extend(name_bytes)
        while len(new_raw) % 4 != 0:
            new_raw.append(0)
        new_raw.extend(struct.pack('<i', len(new_enc)))
        new_raw.extend(new_enc)
        obj.set_raw_data(bytes(new_raw))

    with open(output_path, 'wb') as f:
        f.write(env.file.save())


# === Story dialogue ===

def extract_dialogue(data: bytes):
    """Parse StoryYamlData -> {story_id, scene_count, scenes:[{scene_id, speakers, text}]}."""
    pos = 0
    if not data or data[pos] != 2:
        return None
    pos += 1
    story_id = struct.unpack_from('<i', data, pos)[0]; pos += 4
    dict_count = struct.unpack_from('<i', data, pos)[0]; pos += 4
    if dict_count < 0 or dict_count > 10000:
        return None

    scenes = []
    i = 9
    while i < len(data) - 5:
        key = struct.unpack_from('<i', data, i)[0]
        scene_tag = data[i + 4]
        if scene_tag == 21 and 0 <= key < 10000:
            sp = i + 5
            scene_id = struct.unpack_from('<i', data, sp)[0]; sp += 4
            sc_cnt = struct.unpack_from('<i', data, sp)[0]; sp += 4
            if 0 <= sc_cnt <= 10:
                speakers = []
                ok = True
                cur = sp
                for _ in range(sc_cnt):
                    s, cur = read_string(data, cur)
                    speakers.append(s)
                if ok:
                    text, _ = read_string(data, cur)
                    scenes.append({
                        "scene_id": scene_id,
                        "speakers": speakers,
                        "text": text,
                    })
            i += 5
            continue
        i += 1
    return {"story_id": story_id, "scene_count": dict_count, "scenes": scenes}


def find_story_strings(data: bytes):
    """Walk StoryYamlData and yield (start, end, value, kind, scene_id, idx) for
    every (Speaker, Text) slot. Empty/null slots are skipped — they have nothing
    to replace and the walk's anchor logic doesn't need them."""
    out = []
    i = 9
    while i < len(data) - 5:
        key = struct.unpack_from('<i', data, i)[0]
        scene_tag = data[i + 4]
        if scene_tag == 21 and 0 <= key < 10000:
            sp = i + 5
            scene_id = struct.unpack_from('<i', data, sp)[0]; sp += 4
            speakers_count = struct.unpack_from('<i', data, sp)[0]; sp += 4
            if 0 <= speakers_count <= 10:
                valid = True
                for j in range(speakers_count):
                    s_start = sp
                    s, sp = read_string(data, s_start)
                    if s is not None and s != "":
                        out.append((s_start, sp, s, "speaker", scene_id, j))
                    elif s is None and sp == s_start:
                        valid = False
                        break
                if valid:
                    t_start = sp
                    text, sp = read_string(data, t_start)
                    if text is not None and text != "":
                        out.append((t_start, sp, text, "text", scene_id, 0))
            i += 5
            continue
        i += 1
    return out


def apply_story_json(data: bytes, story: dict) -> bytes:
    """Replace each (Speaker, Text) slot whose JSON value differs from the
    original. Bundle bytes outside string slots are preserved verbatim."""
    lookup = {}
    for sc in story.get("scenes", []):
        sid = sc.get("scene_id")
        for i, sp in enumerate(sc.get("speakers", [])):
            lookup[(sid, "speaker", i)] = sp
        lookup[(sid, "text", 0)] = sc.get("text")

    offsets = find_story_strings(data)
    if not offsets:
        return data

    out = bytearray()
    prev = 0
    for start, end, original, kind, sid, idx in offsets:
        new_value = lookup.get((sid, kind, idx), original)
        if new_value != original:
            out.extend(data[prev:start])
            out.extend(write_string(new_value))
            prev = end
    out.extend(data[prev:])
    return bytes(out)


# === Generic MemoryPack string scan (MasterData) ===

def find_all_strings(data: bytes):
    """Walk the buffer, return [(offset, length, value)] for every MemoryPack
    UTF-8 string. Self-validating via UTF-8 decode + char-count match, so it
    handles arbitrary MemoryPack-emitted blobs without a schema.

    `length` covers the full prefix + payload: int32 header + (for non-null and
    non-empty strings) int32 charCount + utf8 bytes."""
    out = []
    i = 0
    while i < len(data) - 4:
        raw = struct.unpack_from('<i', data, i)[0]
        if raw < -1 and raw > -100000:
            bc = ~raw
            if bc > 0 and i + 8 + bc <= len(data):
                cc = struct.unpack_from('<i', data, i + 4)[0]
                if 0 < cc <= bc:
                    try:
                        s = data[i + 8:i + 8 + bc].decode('utf-8', errors='strict')
                        if len(s) == cc:
                            out.append((i, 8 + bc, s))
                            i += 8 + bc
                            continue
                    except UnicodeDecodeError:
                        pass
        i += 1
    return out


def apply_misc_json(data: bytes, doc: dict) -> bytes:
    """Replace each MemoryPack string whose `strings[i].value` in the JSON
    differs from the original. Matched by byte offset."""
    by_offset = {s["offset"]: s["value"] for s in doc.get("strings", [])}
    items = find_all_strings(data)
    if not items:
        return data

    out = bytearray()
    prev = 0
    for offset, length, original in items:
        new_value = by_offset.get(offset, original)
        if new_value != original:
            out.extend(data[prev:offset])
            out.extend(write_string(new_value))
            prev = offset + length
    out.extend(data[prev:])
    return bytes(out)


# === Metadata: StoryMasterData + ChapterMasterData ===

def parse_story_master(bundle_path: str):
    """Parse StoryMasterData -> list of {chapter_id, story_id, title, episode,
    scene_key, display_order} dicts.

    StoryMasterData is one of the MasterData bundles; it carries the title-
    screen menu metadata for every story (which chapter it belongs to, the
    episode label, the localised title shown in the chapter list). The
    record layout uses MemoryPack with three int32 fields wrapped around
    five strings — discovered empirically by diffing decrypted plaintexts
    against the rendered menu.
    """
    _, enc = extract_textasset_raw(bundle_path)
    pt = decrypt(enc)
    pos = 0
    pos += 1
    count = struct.unpack_from('<i', pt, pos)[0]; pos += 4
    records = []
    for _ in range(count):
        pos += 1
        chapter_id = struct.unpack_from('<i', pt, pos)[0]; pos += 4
        story_id = struct.unpack_from('<i', pt, pos)[0]; pos += 4
        title, pos = read_string(pt, pos)
        episode, pos = read_string(pt, pos)
        scene_key, pos = read_string(pt, pos)
        struct.unpack_from('<i', pt, pos)[0]; pos += 4
        _f6, pos = read_string(pt, pos)
        _f7, pos = read_string(pt, pos)
        display_order = struct.unpack_from('<i', pt, pos)[0]; pos += 4
        records.append({
            "chapter_id": chapter_id,
            "story_id": story_id,
            "title": title,
            "episode": episode,
            "scene_key": scene_key,
            "display_order": display_order,
        })
    return records


def parse_chapter_master(bundle_path: str):
    """Parse ChapterMasterData -> {chapter_id: chapter_name} dict.

    Each record's first byte is the count of MemoryPack members that follow
    (variable, depends on chapter variant). Only the first two fields are
    structurally fixed: int32 chapter_id and string chapter_name. The
    remaining `mc - 2` fields are skipped by inspecting their MemoryPack
    headers without decoding (we don't need their values for the toolkit).
    """
    _, enc = extract_textasset_raw(bundle_path)
    pt = decrypt(enc)
    pos = 0
    pos += 1
    count = struct.unpack_from('<i', pt, pos)[0]; pos += 4
    out = {}
    for _ in range(count):
        mc = pt[pos]; pos += 1
        if pos + 4 > len(pt):
            break
        chapter_id = struct.unpack_from('<i', pt, pos)[0]; pos += 4
        name, pos = read_string(pt, pos)
        for _ in range(max(0, mc - 2)):
            if pos + 4 > len(pt):
                break
            raw = struct.unpack_from('<i', pt, pos)[0]
            if raw == -1 or raw == 0:
                pos += 4
            elif raw < -1 and raw > -100000:
                bc = ~raw
                if pos + 8 + bc <= len(pt):
                    pos += 8 + bc
                else:
                    pos += 4
            else:
                pos += 4
        out[chapter_id] = name
    return out


# === Layout ===
#
# Live cache lives under cfg.cache_root() (auto-detected from the game install
# + USERPROFILE). Extract / repack output is written next to this script.

STORY_DIR  = str(cfg.story_masterdata_dir())
MASTER_DIR = str(cfg.masterdata_dir())

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

EXTRACT_ROOT = os.path.join(OUTPUT_DIR, "extracted_data")
STORY_OUT = os.path.join(EXTRACT_ROOT, "story")
MISC_OUT = os.path.join(EXTRACT_ROOT, "misc")
FINGERPRINTS_PATH = os.path.join(EXTRACT_ROOT, ".fingerprints.pkl")

REPACK_ROOT = os.path.join(OUTPUT_DIR, "repacked_bundles")
REPACK_STORY = os.path.join(REPACK_ROOT, "story")
REPACK_MISC = os.path.join(REPACK_ROOT, "misc")

STORY_MASTER_BUNDLE = "d5f5fd6024911c22fa99b59427270214.bundle"
CHAPTER_MASTER_BUNDLE = "357536dd738af9be5b6f3e5b60d3cc89.bundle"

MISC_BUNDLES = {
    "15dfc167a270133340e8dea7eca1f8bc.bundle": "MonsterMasterData",
    "1a1f221889c7113c4fc81d5269cd2c8f.bundle": "UnitSkillEffectMasterData",
    "200a6b75588cb6f880a05c085cdfa139.bundle": "MemorialQuestMasterData",
    "2aa2ac58c76235a153adfd6824be18d8.bundle": "SquareBackgroundMasterData",
    "357536dd738af9be5b6f3e5b60d3cc89.bundle": "ChapterMasterData",
    "361e6b4412879287d31c75df82baa481.bundle": "UnitMasterData",
    "3baee6fe788b15ee5e1e855dc4a76226.bundle": "StampMasterData",
    "3cb553ddf329cfa9ad0373e6f9843b13.bundle": "MainCharacterStyleMasterData",
    "66fb180f1c0d2c8e7b5fc08d5f1d3822.bundle": "LoadingComicMasterData",
    "89b2fa703971c113719ac402372357d6.bundle": "GuildMapConditionMasterData",
    "9b540a617789744fadadf9265d05d2aa.bundle": "LeaderStyleMasterData",
    "d5f5fd6024911c22fa99b59427270214.bundle": "StoryMasterData",
    "e4d718566461853b73497ce861cbeb76.bundle": "BackgroundMasterData",
    "e79dbe20ad92ab8b77c2738a33353c6d.bundle": "BackgroundMusicMasterData",
    "fc1bc9134e3211e77ec9b57808b84cd8.bundle": "GuildTournamentMasterData",
}


def has_jp(s: str) -> bool:
    """True if `s` contains any CJK code point that we treat as Japanese.

    The ranges cover Hiragana + Katakana (U+3040–U+30FF), CJK Unified
    Ideographs (U+4E00–U+9FFF — used heavily in kanji), and the fullwidth
    block (U+FF00–U+FF9F — half/fullwidth romanji used in UI labels).
    Used to filter MasterData strings down to the ones that look like JP
    UI text rather than ASCII keys / GUIDs / numbers.
    """
    if not s:
        return False
    return any(
        '぀' <= c <= 'ヿ' or '一' <= c <= '鿿' or '＀' <= c <= 'ﾟ'
        for c in s
    )


# === Fingerprints ===
#
# We checksum each extracted JSON the moment we write it and store the hash in
# .fingerprints.pkl. On repack, files whose current hash matches the recorded
# one are treated as untouched and skipped. That way a re-extract (which is
# byte-identical) doesn't trigger a re-repack of every bundle.

def sha256_bytes(b: bytes) -> str:
    """Hex SHA-256 over a bytes buffer."""
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    """Hex SHA-256 of the file at `path`. Loads the whole file into memory —
    fine for translator JSONs (a few hundred KB max)."""
    with open(path, 'rb') as f:
        return sha256_bytes(f.read())


def load_fingerprints() -> dict:
    """Read .fingerprints.pkl. Returns an empty dict if the file is missing
    or unreadable — losing the fingerprint cache forces a full repack but
    is not otherwise destructive."""
    if not os.path.exists(FINGERPRINTS_PATH):
        return {}
    try:
        with open(FINGERPRINTS_PATH, 'rb') as f:
            return pickle.load(f)
    except Exception:
        return {}


def save_fingerprints(fps: dict):
    """Write .fingerprints.pkl, creating the extract-root directory if needed."""
    os.makedirs(EXTRACT_ROOT, exist_ok=True)
    with open(FINGERPRINTS_PATH, 'wb') as f:
        pickle.dump(fps, f)


def write_json_with_fingerprint(path: str, payload: dict, fps: dict, key: str):
    """Write `payload` as pretty-printed JSON and record its SHA-256 under `key`.

    `ensure_ascii=False` so Japanese characters appear in the file literally
    (translators expect to see the Japanese, not `\\uxxxx` escapes). The
    fingerprint is computed over the exact bytes we wrote, so an external
    edit changes the hash deterministically.
    """
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    with open(path, 'wb') as f:
        f.write(body)
    fps[key] = sha256_bytes(body)


# === Commands ===

def cmd_extract_story():
    """`extract-story` command: extract every story bundle to
    extracted_data/story/<story_id>.json.

    Each output file has the metadata (title, episode, chapter name,
    display order, etc.) at the top followed by `scenes[]`. The
    StoryMasterData + ChapterMasterData bundles are parsed first so we
    can join story-id → title/episode. If those bundles can't be
    decoded (e.g. game updated and the layout changed), extraction
    continues with empty metadata rather than failing.
    """
    os.makedirs(STORY_OUT, exist_ok=True)
    fps = load_fingerprints()

    print("Loading metadata (StoryMasterData + ChapterMasterData)...")
    try:
        story_meta = parse_story_master(os.path.join(MASTER_DIR, STORY_MASTER_BUNDLE))
        chapter_names = parse_chapter_master(os.path.join(MASTER_DIR, CHAPTER_MASTER_BUNDLE))
    except Exception as e:
        print(f"  WARN: could not load metadata ({e}); titles will be missing")
        story_meta = []
        chapter_names = {}

    meta_by_id = {m["story_id"]: m for m in story_meta}
    print(f"  loaded {len(story_meta)} story records, {len(chapter_names)} chapter names")

    files = sorted(os.listdir(STORY_DIR))
    print(f"Extracting {len(files)} story bundles -> {STORY_OUT}")

    errors = []
    index = {}
    t0 = time.time()
    written = 0
    for i, fname in enumerate(files):
        if i % 500 == 0:
            print(f"  {i}/{len(files)} ({time.time() - t0:.1f}s, {written} written)")
        fpath = os.path.join(STORY_DIR, fname)
        try:
            name, enc = extract_textasset_raw(fpath)
            if enc is None:
                errors.append({"file": fname, "error": "no TextAsset"})
                continue
            pt = decrypt(enc)
            parsed = extract_dialogue(pt)
            if parsed is None:
                errors.append({"file": fname, "error": "parse failed"})
                continue
            sid = parsed["story_id"]
            meta = meta_by_id.get(sid, {})

            # Metadata first, scenes last — translator sees context up-front.
            payload = {
                "story_id": sid,
                "title": meta.get("title"),
                "episode": meta.get("episode"),
                "chapter_id": meta.get("chapter_id"),
                "chapter_name": chapter_names.get(meta.get("chapter_id")),
                "display_order": meta.get("display_order"),
                "bundle": fname,
                "asset_name": name,
                "scene_count": parsed["scene_count"],
                "scenes": parsed["scenes"],
            }
            index[fname] = sid
            key = f"story/{sid}.json"
            write_json_with_fingerprint(
                os.path.join(STORY_OUT, f"{sid}.json"), payload, fps, key)
            written += 1
        except Exception as e:
            errors.append({"file": fname, "error": str(e)})

    with open(os.path.join(STORY_OUT, "_index.json"), 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    with open(os.path.join(STORY_OUT, "_errors.json"), 'w', encoding='utf-8') as f:
        json.dump(errors, f, ensure_ascii=False, indent=2)
    save_fingerprints(fps)
    print(f"\nDone in {time.time() - t0:.1f}s. Wrote {written} stories. Errors: {len(errors)}.")


def cmd_extract_misc():
    """`extract-misc` command: extract every MasterData bundle that holds JP
    text to extracted_data/misc/<AssetName>.json.

    The bundle whitelist (MISC_BUNDLES) is hardcoded — adding a new one is a
    one-line edit. Each output file records every MemoryPack string with its
    byte offset so the repack step can match exactly without re-walking.
    """
    os.makedirs(MISC_OUT, exist_ok=True)
    fps = load_fingerprints()

    written = 0
    for fname, asset_name in MISC_BUNDLES.items():
        fpath = os.path.join(MASTER_DIR, fname)
        if not os.path.exists(fpath):
            print(f"  SKIP {fname}: not present in MasterData/")
            continue
        try:
            name, enc = extract_textasset_raw(fpath)
            pt = decrypt(enc)
        except Exception as e:
            print(f"  ERROR {fname}: {e}")
            continue

        items = find_all_strings(pt)
        jp_count = sum(1 for _o, _l, s in items if has_jp(s))

        payload = {
            "asset": asset_name,
            "bundle": fname,
            "asset_name_in_bundle": name,
            "total_strings": len(items),
            "jp_strings": jp_count,
            "strings": [
                {"offset": off, "value": s, "is_jp": has_jp(s)}
                for off, _, s in items
            ],
        }
        key = f"misc/{asset_name}.json"
        write_json_with_fingerprint(
            os.path.join(MISC_OUT, f"{asset_name}.json"), payload, fps, key)
        written += 1
        print(f"  {asset_name:32s}  total={len(items):5d}  jp={jp_count:5d}")

    save_fingerprints(fps)
    print(f"\nWrote {written} misc bundles to {MISC_OUT}")


def cmd_extract():
    """`extract` command — both story and misc, sequentially."""
    cmd_extract_story()
    print()
    cmd_extract_misc()


def _is_modified(path: str, key: str, fps: dict, force: bool) -> bool:
    """Decide whether to repack the file at `path`.

    `key` is the per-file fingerprint key (e.g. `story/1621.json`). A file
    is "modified" if its current SHA-256 differs from the one stored at
    extract time, OR if `--force` is in effect, OR if no baseline exists
    (in which case we conservatively SKIP the file, since we can't tell
    whether the translator touched it).
    """
    if force:
        return True
    baseline = fps.get(key)
    if baseline is None:
        return False  # never extracted with current toolkit -> nothing to compare; treat as untouched
    return sha256_file(path) != baseline


def cmd_repack_story(force: bool = False):
    """`repack-story` command: rebuild every modified story JSON into a
    UnityFS bundle under repacked_bundles/story/<bundle>.

    "Modified" is decided by `_is_modified` (fingerprint mismatch). The
    rename / move semantics of repack_bundle keep the original bundle
    name so deploy_bundles.py can drop the new bundle straight onto
    the live cache copy.
    """
    if not os.path.isdir(STORY_OUT):
        print(f"ERROR: {STORY_OUT} does not exist; run `extract-story` first.")
        sys.exit(1)
    os.makedirs(REPACK_STORY, exist_ok=True)
    fps = load_fingerprints()

    repacked = 0
    skipped = 0
    failed = 0
    t0 = time.time()
    for fname in sorted(os.listdir(STORY_OUT)):
        if not fname.endswith(".json") or fname.startswith("_"):
            continue
        fpath = os.path.join(STORY_OUT, fname)
        key = f"story/{fname}"
        if not _is_modified(fpath, key, fps, force):
            skipped += 1
            continue
        with open(fpath, 'rb') as f:
            story = json.loads(f.read().decode('utf-8'))
        bundle = story.get("bundle")
        if not bundle:
            failed += 1
            continue
        src = os.path.join(STORY_DIR, bundle)
        dst = os.path.join(REPACK_STORY, bundle)
        try:
            repack_bundle(src, dst, lambda pt, _s=story: apply_story_json(pt, _s))
            repacked += 1
        except Exception as e:
            print(f"  ERROR {bundle}: {e}")
            failed += 1

    print(f"\nStory repack done in {time.time() - t0:.1f}s.")
    print(f"  repacked: {repacked}")
    print(f"  skipped (unmodified): {skipped}")
    print(f"  failed: {failed}")
    print(f"  output: {REPACK_STORY}")


def cmd_repack_misc(force: bool = False):
    """`repack-misc` command: same idea as `repack-story` but for MasterData.

    Output bundles land in repacked_bundles/misc/<bundle>. Each bundle's
    asset name is printed alongside so a translator scanning the output
    can immediately tell whether the right MasterData asset was touched.
    """
    if not os.path.isdir(MISC_OUT):
        print(f"ERROR: {MISC_OUT} does not exist; run `extract-misc` first.")
        sys.exit(1)
    os.makedirs(REPACK_MISC, exist_ok=True)
    fps = load_fingerprints()

    repacked = 0
    skipped = 0
    failed = 0
    for fname in sorted(os.listdir(MISC_OUT)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(MISC_OUT, fname)
        key = f"misc/{fname}"
        if not _is_modified(fpath, key, fps, force):
            skipped += 1
            continue
        with open(fpath, 'rb') as f:
            doc = json.loads(f.read().decode('utf-8'))
        bundle = doc.get("bundle")
        if not bundle:
            failed += 1
            continue
        src = os.path.join(MASTER_DIR, bundle)
        dst = os.path.join(REPACK_MISC, bundle)
        try:
            repack_bundle(src, dst, lambda pt, _d=doc: apply_misc_json(pt, _d))
            repacked += 1
            print(f"  {doc.get('asset', bundle)} -> {dst}")
        except Exception as e:
            print(f"  ERROR {bundle}: {e}")
            failed += 1

    print(f"\nMisc repack: {repacked} repacked, {skipped} skipped (unmodified), {failed} failed")
    print(f"Output: {REPACK_MISC}")


def cmd_repack(force: bool = False):
    """`repack` command — both story and misc, sequentially."""
    cmd_repack_story(force=force)
    print()
    cmd_repack_misc(force=force)


def cmd_test_repack():
    """Round-trip a single bundle: take its first text line, swap it for a
    sentinel via apply_story_json, re-decrypt, confirm the sentinel survived."""
    test_bundle = os.path.join(STORY_DIR, "eb777f2829400cfced05a3761d77fd6a.bundle")
    _, enc = extract_textasset_raw(test_bundle)
    pt = decrypt(enc)
    parsed = extract_dialogue(pt)
    sentinel = "[TOOLKIT_ROUNDTRIP_OK]"

    # Find the first scene with a non-empty text line and swap it.
    swapped = False
    for sc in parsed["scenes"]:
        if sc.get("text"):
            sc["text"] = sentinel
            swapped = True
            break
    if not swapped:
        print("Test bundle has no translatable text lines.")
        return

    out = os.path.join(OUTPUT_DIR, "test_repacked.bundle")
    repack_bundle(test_bundle, out, lambda pt2, _s=parsed: apply_story_json(pt2, _s))

    _, enc2 = extract_textasset_raw(out)
    pt2 = decrypt(enc2)
    result = extract_dialogue(pt2)
    ok = result and sentinel.encode() in pt2 and any(
        sc.get("text") == sentinel for sc in result["scenes"]
    )
    print("Test repack:", "SUCCESS" if ok else "FAILED")
    if ok:
        for sc in result["scenes"]:
            if sc.get("text") == sentinel:
                sp = ', '.join(s for s in sc["speakers"] if s) or '(narrator)'
                print(f"  [{sp}] {sentinel}")
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merc Storia Translation Toolkit")
    parser.add_argument(
        "command",
        choices=[
            "extract", "extract-story", "extract-misc",
            "repack", "repack-story", "repack-misc",
            "test-repack",
        ],
    )
    parser.add_argument("--force", action="store_true",
                        help="Repack even files whose hash matches the recorded baseline.")
    args = parser.parse_args()
    if args.command == "extract":
        cmd_extract()
    elif args.command == "extract-story":
        cmd_extract_story()
    elif args.command == "extract-misc":
        cmd_extract_misc()
    elif args.command == "repack":
        cmd_repack(force=args.force)
    elif args.command == "repack-story":
        cmd_repack_story(force=args.force)
    elif args.command == "repack-misc":
        cmd_repack_misc(force=args.force)
    elif args.command == "test-repack":
        cmd_test_repack()
