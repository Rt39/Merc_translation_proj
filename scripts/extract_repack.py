"""Merc Storia Translation Toolkit

End-to-end pipeline for extracting, translating, and repacking every piece of
translatable Japanese text in the game (story dialogue + master data).

Story path uses a full MemoryPack schema (see `mercstoria.memorypack`) — JSON
↔ bytes is byte-identical for 4008/4013 vanilla bundles, so translators can
insert or remove scenes, not just edit existing ones.

Usage
-----
    uv run -m mercstoria extract
        Extract everything: stories AND misc MasterData. Equivalent to running
        `extract-story` then `extract-misc`.

    uv run -m mercstoria extract-story
        Stories only → extracted_data/story/<story_id>.json. The JSON mirrors
        StoryYamlData verbatim (every field from dump.cs, including _mc and
        _skipped markers used to round-trip exactly). Translators edit
        scenes[*].Text / .Speakers / character DisplayName fields in place.

    uv run -m mercstoria extract-misc
        MasterData bundles with JP text → extracted_data/misc/<AssetName>.json.

    uv run -m mercstoria repack
        Repack everything (stories + misc) that has been modified.

    uv run -m mercstoria repack-story
        Repack only modified story JSONs → repacked_bundles/story/<bundle>.

    uv run -m mercstoria repack-misc
        Repack only modified misc JSONs → repacked_bundles/misc/<bundle>.

    uv run -m mercstoria test-repack
        Round-trip a single bundle to confirm the pipeline.

Translation workflow
--------------------
Translators edit the JSON files **in place**. For stories the JSON has the
full StoryYamlData schema — only edit user-visible string fields (Text,
Speakers, DisplayName) and leave _mc / _skipped / _null / _bits keys alone.
For misc bundles the JSON lists every MemoryPack string by byte offset;
edit `strings[i].value`. Anything the translator left unchanged is preserved
byte-for-byte.

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
from tqdm import tqdm

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercstoria import config as cfg
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
#
# Story extract/repack uses the full MemoryPack schema from mercstoria.memorypack
# (Reader/Writer round-trip is byte-identical on 4008/4013 vanilla bundles).
# That gives translators the freedom to insert or delete scenes, not just
# edit existing strings. The old "splice on byte offsets" path is gone.

from mercstoria import memorypack as _md


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

OUTPUT_DIR = str(Path(__file__).resolve().parent.parent)

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

# Full-schema MasterData bundles — JSON has structured records and translators
# can change string lengths freely (the offset-based path requires the new
# encoded bytes to be the same length as the original). The other entries in
# MISC_BUNDLES still go through the offset-based string-replace path.
# Map: bundle filename → (asset_name, reader_method, serializer_fn).
FULL_SCHEMA_MASTER = {
    "357536dd738af9be5b6f3e5b60d3cc89.bundle":
        ("ChapterMasterData", "chapter_master", _md.serialize_chapter_master),
    "d5f5fd6024911c22fa99b59427270214.bundle":
        ("StoryMasterData",   "story_master",   _md.serialize_story_master),
    "361e6b4412879287d31c75df82baa481.bundle":
        ("UnitMasterData",    "unit_master",    _md.serialize_unit_master),
    "e4d718566461853b73497ce861cbeb76.bundle":
        ("BackgroundMasterData", "background_master", _md.serialize_background_master),
    "e79dbe20ad92ab8b77c2738a33353c6d.bundle":
        ("BackgroundMusicMasterData", "background_music_master", _md.serialize_background_music_master),
    "89b2fa703971c113719ac402372357d6.bundle":
        ("GuildMapConditionMasterData", "guild_map_condition_master", _md.serialize_guild_map_condition_master),
    "fc1bc9134e3211e77ec9b57808b84cd8.bundle":
        ("GuildTournamentMasterData", "guild_tournament_master", _md.serialize_guild_tournament_master),
    "9b540a617789744fadadf9265d05d2aa.bundle":
        ("LeaderStyleMasterData", "leader_style_master", _md.serialize_leader_style_master),
    "66fb180f1c0d2c8e7b5fc08d5f1d3822.bundle":
        ("LoadingComicMasterData", "loading_comic_master", _md.serialize_loading_comic_master),
    "3cb553ddf329cfa9ad0373e6f9843b13.bundle":
        ("MainCharacterStyleMasterData", "main_character_style_master", _md.serialize_main_character_style_master),
    "200a6b75588cb6f880a05c085cdfa139.bundle":
        ("MemorialQuestMasterData", "memorial_quest_master", _md.serialize_memorial_quest_master),
    "15dfc167a270133340e8dea7eca1f8bc.bundle":
        ("MonsterMasterData", "monster_master", _md.serialize_monster_master),
    "2aa2ac58c76235a153adfd6824be18d8.bundle":
        ("SquareBackgroundMasterData", "square_background_master", _md.serialize_square_background_master),
    "3baee6fe788b15ee5e1e855dc4a76226.bundle":
        ("StampMasterData", "stamp_master", _md.serialize_stamp_master),
    "1a1f221889c7113c4fc81d5269cd2c8f.bundle":
        ("UnitSkillEffectMasterData", "unit_skill_effect_master", _md.serialize_unit_skill_effect_master),
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
    extracted_data/story/<story_id>.json with the full MemoryPack schema.

    Each output file has translator-facing metadata (title, episode, chapter
    name, display_order) at the top, followed by the raw StoryYamlData
    structure: list of (key, scene_dict) pairs that mirror dump.cs field-for-
    field. The Reader keeps internal markers (`_mc`, `_skipped`, `_null`/
    `_bits`) so Writer can reproduce the exact bytes — translators should
    leave those alone and edit only the user-visible string fields (Text,
    Speakers, Left/Center/Right.DisplayName).

    Bundles whose plaintext doesn't round-trip byte-identical are skipped
    and listed in `_errors.json`. That guarantees: if a story.json appears
    in the output, repacking it without changes is provably lossless.
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
    for fname in tqdm(files, desc="extract-story", unit="bundle"):
        fpath = os.path.join(STORY_DIR, fname)
        try:
            name, enc = extract_textasset_raw(fpath)
            if enc is None:
                errors.append({"file": fname, "error": "no TextAsset"})
                continue
            pt = decrypt(enc)
            story = _md.Reader(pt).story()
            if story is None:
                errors.append({"file": fname, "error": "parse returned null"})
                continue
            # Reproduce-or-skip: if Writer can't recreate the exact plaintext
            # from our parsed dict, the JSON is poisoned and repack would
            # silently corrupt the bundle. Skip and log.
            rt = _md.serialize_story(story)
            if rt != pt:
                k = 0; m = min(len(rt), len(pt))
                while k < m and rt[k] == pt[k]: k += 1
                errors.append({"file": fname,
                               "error": f"round-trip diverges @ {k}/{len(pt)}"})
                continue

            sid = story["Id"]
            meta = meta_by_id.get(sid, {})
            payload = {
                "story_id": sid,
                "title": meta.get("title"),
                "episode": meta.get("episode"),
                "chapter_id": meta.get("chapter_id"),
                "chapter_name": chapter_names.get(meta.get("chapter_id")),
                "display_order": meta.get("display_order"),
                "bundle": fname,
                "asset_name": name,
                "_mc": story["_mc"],
                "Scenes": [
                    {"key": k, "scene": sc}
                    for k, sc in story["Scenes"]
                ],
            }
            # Some bundles share story_id=0 (placeholder/template stories).
            # Disambiguate by appending the bundle stem so each bundle gets
            # its own JSON file and repack maps 1:1 back to the source.
            stem = os.path.splitext(fname)[0]
            json_name = f"{sid}.json" if sid != 0 else f"{sid}_{stem}.json"
            index[fname] = sid
            key = f"story/{json_name}"
            write_json_with_fingerprint(
                os.path.join(STORY_OUT, json_name), payload, fps, key)
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

    Two paths:
      * full-schema bundles (FULL_SCHEMA_MASTER) — records are parsed via the
        mercstoria.memorypack Reader. JSON has structured records that translators can
        edit freely (string lengths can change). Reader is verified by
        round-tripping the original plaintext byte-identical before writing
        the JSON; mismatches are logged and the file is skipped.
      * offset-based bundles — every MemoryPack string is dumped with its byte
        offset. Translators edit `strings[i].value` IN PLACE; the new encoded
        string must be the same byte length as the original (length is
        preserved by the splice).
    """
    os.makedirs(MISC_OUT, exist_ok=True)
    fps = load_fingerprints()

    written = 0
    errors = []
    for fname, asset_name in tqdm(MISC_BUNDLES.items(), desc="extract-misc",
                                  unit="bundle", total=len(MISC_BUNDLES)):
        fpath = os.path.join(MASTER_DIR, fname)
        if not os.path.exists(fpath):
            tqdm.write(f"  SKIP {fname}: not present in MasterData/")
            continue
        try:
            name, enc = extract_textasset_raw(fpath)
            pt = decrypt(enc)
        except Exception as e:
            tqdm.write(f"  ERROR {fname}: {e}")
            continue

        full = FULL_SCHEMA_MASTER.get(fname)
        if full:
            _, reader_method, serializer = full
            try:
                obj = getattr(_md.Reader(pt), reader_method)()
                rt = serializer(obj)
                if rt != pt:
                    k = 0; m = min(len(rt), len(pt))
                    while k < m and rt[k] == pt[k]: k += 1
                    errors.append({"file": fname,
                                   "error": f"round-trip diverges @ {k}/{len(pt)}"})
                    tqdm.write(f"  ERROR {asset_name}: round-trip diverged at byte {k}")
                    continue
            except Exception as e:
                errors.append({"file": fname, "error": str(e)})
                tqdm.write(f"  ERROR {asset_name}: {e}")
                continue
            payload = {
                "asset": asset_name,
                "bundle": fname,
                "asset_name_in_bundle": name,
                "schema": "full",
                "_mc": obj["_mc"],
                "Records": obj["Records"],
            }
            key = f"misc/{asset_name}.json"
            write_json_with_fingerprint(
                os.path.join(MISC_OUT, f"{asset_name}.json"), payload, fps, key)
            written += 1
            n_rec = len(obj["Records"]) if obj["Records"] is not None else 0
            tqdm.write(f"  {asset_name:32s}  records={n_rec:5d}  (full schema)")
            continue

        items = find_all_strings(pt)
        jp_count = sum(1 for _o, _l, s in items if has_jp(s))
        payload = {
            "asset": asset_name,
            "bundle": fname,
            "asset_name_in_bundle": name,
            "schema": "offset",
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
        tqdm.write(f"  {asset_name:32s}  total={len(items):5d}  jp={jp_count:5d}")

    if errors:
        with open(os.path.join(MISC_OUT, "_errors.json"), 'w', encoding='utf-8') as f:
            json.dump(errors, f, ensure_ascii=False, indent=2)
    save_fingerprints(fps)
    print(f"\nWrote {written} misc bundles to {MISC_OUT}. Errors: {len(errors)}.")


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


def _story_dict_from_json(payload: dict) -> dict:
    """Reconstruct the mercstoria.memorypack-shape story dict from a JSON payload.

    The JSON wraps `Scenes` as a list of `{key, scene}` for human readability;
    Writer wants `[(key, scene_dict), ...]`. Strip the metadata header here
    so anything outside the schema is ignored.
    """
    return {
        "_mc": payload["_mc"],
        "Id": payload["story_id"],
        "Scenes": [(s["key"], s["scene"]) for s in payload["Scenes"]],
    }


def cmd_repack_story(force: bool = False):
    """`repack-story` command: rebuild every modified story JSON into a
    UnityFS bundle under repacked_bundles/story/<bundle>.

    Uses the full MemoryPack schema (mercstoria.memorypack.Writer). Translators can
    add or remove `Scenes[]` entries; the Writer regenerates the bundle's
    plaintext from scratch and the AES-encrypted TextAsset is rebuilt
    around the new length. CRC patches must be in place or the modified
    bundle gets rejected by Unity at load time.

    "Modified" is decided by `_is_modified` (fingerprint mismatch). Bundle
    name is preserved so deploy_bundles.py can drop it onto the live cache.
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
    json_files = [f for f in sorted(os.listdir(STORY_OUT))
                  if f.endswith(".json") and not f.startswith("_")]
    for fname in tqdm(json_files, desc="repack-story", unit="json"):
        fpath = os.path.join(STORY_OUT, fname)
        key = f"story/{fname}"
        if not _is_modified(fpath, key, fps, force):
            skipped += 1
            continue
        with open(fpath, 'rb') as f:
            payload = json.loads(f.read().decode('utf-8'))
        bundle = payload.get("bundle")
        if not bundle:
            failed += 1
            continue
        src = os.path.join(STORY_DIR, bundle)
        dst = os.path.join(REPACK_STORY, bundle)
        try:
            story_dict = _story_dict_from_json(payload)
            new_pt = _md.serialize_story(story_dict)
            repack_bundle(src, dst, lambda _orig, _b=new_pt: _b)
            repacked += 1
        except Exception as e:
            tqdm.write(f"  ERROR {bundle}: {e}")
            failed += 1

    print(f"\nStory repack done in {time.time() - t0:.1f}s.")
    print(f"  repacked: {repacked}")
    print(f"  skipped (unmodified): {skipped}")
    print(f"  failed: {failed}")
    print(f"  output: {REPACK_STORY}")


def cmd_repack_misc(force: bool = False):
    """`repack-misc` command: same idea as `repack-story` but for MasterData.

    Two paths, dispatched on the JSON's `schema` field:
      * `"full"`   — Records[] is fed back through mercstoria.memorypack.serialize_*.
      * `"offset"` — splice strings by byte offset; new bytes must match the
                     original encoded length.

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
    json_files = [f for f in sorted(os.listdir(MISC_OUT))
                  if f.endswith(".json") and not f.startswith("_")]
    for fname in tqdm(json_files, desc="repack-misc", unit="json"):
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
            full = FULL_SCHEMA_MASTER.get(bundle)
            if full and doc.get("schema") == "full":
                _, _, serializer = full
                obj = {"_mc": doc["_mc"], "Records": doc["Records"]}
                new_pt = serializer(obj)
                repack_bundle(src, dst, lambda _orig, _b=new_pt: _b)
            else:
                repack_bundle(src, dst, lambda pt, _d=doc: apply_misc_json(pt, _d))
            repacked += 1
            tqdm.write(f"  {doc.get('asset', bundle)} -> {dst}")
        except Exception as e:
            tqdm.write(f"  ERROR {bundle}: {e}")
            failed += 1

    print(f"\nMisc repack: {repacked} repacked, {skipped} skipped (unmodified), {failed} failed")
    print(f"Output: {REPACK_MISC}")


def cmd_repack(force: bool = False):
    """`repack` command — both story and misc, sequentially."""
    cmd_repack_story(force=force)
    print()
    cmd_repack_misc(force=force)


def cmd_test_repack():
    """Round-trip a single bundle through the full-schema pipeline:
    parse with Reader, mutate the first non-empty Text via the dict, write
    it back through Writer + repack_bundle, then re-read and confirm the
    sentinel survives."""
    test_bundle = os.path.join(STORY_DIR, "eb777f2829400cfced05a3761d77fd6a.bundle")
    _, enc = extract_textasset_raw(test_bundle)
    pt = decrypt(enc)
    story = _md.Reader(pt).story()
    sentinel = "[TOOLKIT_ROUNDTRIP_OK]"

    swapped = False
    for _key, sc in story["Scenes"]:
        if sc and sc.get("Text"):
            sc["Text"] = sentinel
            swapped = True
            break
    if not swapped:
        print("Test bundle has no translatable text lines.")
        return

    out = os.path.join(OUTPUT_DIR, "test_repacked.bundle")
    new_pt = _md.serialize_story(story)
    repack_bundle(test_bundle, out, lambda _orig, _b=new_pt: _b)

    _, enc2 = extract_textasset_raw(out)
    pt2 = decrypt(enc2)
    result = _md.Reader(pt2).story()
    ok = result and any(
        sc and sc.get("Text") == sentinel
        for _k, sc in result["Scenes"]
    )
    print("Test repack:", "SUCCESS" if ok else "FAILED")
    if ok:
        for _key, sc in result["Scenes"]:
            if sc and sc.get("Text") == sentinel:
                speakers = sc.get("Speakers") or []
                sp = ', '.join(s for s in speakers if s) or '(narrator)'
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
