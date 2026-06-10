"""Merc Storia Translation Toolkit

Complete pipeline for extracting, translating, and repacking story text.

Usage:
    python merc_storia_toolkit.py extract          - Extract all dialogue to JSON
    python merc_storia_toolkit.py extract-meta      - Extract chapter/story metadata
    python merc_storia_toolkit.py repack <json>     - Repack translated JSON into bundles
    python merc_storia_toolkit.py test-repack       - Test repack on example story

Encryption: AES-256-CBC-PKCS7
    Key: PBKDF2-HMAC-SHA256(password="2147483647", salt="-2147483648", iterations=1024, dklen=32)
    IV: First 16 bytes of ciphertext (prepended)

Data format: MemoryPack (UTF-8 mode)
    Strings: int32 ~utf8_byte_count, int32 char_count, byte[utf8_byte_count]
    Null: int32(-1), Empty: int32(0)

Bundle format: UnityFS containing TextAsset with encrypted MemoryPack data
"""
import sys, io, struct, os, json, time, argparse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes, padding as sym_padding
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import UnityPy

# === Crypto ===

_kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                   salt=b"-2147483648", iterations=1024)
AES_KEY = _kdf.derive(b"2147483647")


def decrypt(data: bytes) -> bytes:
    iv, ct = data[:16], data[16:]
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    dec = cipher.decryptor()
    pt = dec.update(ct) + dec.finalize()
    pad = pt[-1]
    if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
        return pt[:-pad]
    return pt


def encrypt(plaintext: bytes) -> bytes:
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    enc = cipher.encryptor()
    ct = enc.update(padded) + enc.finalize()
    return iv + ct


# === MemoryPack ===

def read_string(data: bytes, pos: int):
    if pos + 4 > len(data):
        return None, pos
    raw = struct.unpack_from('<i', data, pos)[0]
    pos += 4
    if raw == -1:
        return None, pos
    if raw == 0:
        return "", pos
    bc = ~raw
    if bc > 0 and bc < 100000 and pos + 4 + bc <= len(data):
        cc = struct.unpack_from('<i', data, pos)[0]
        pos += 4
        try:
            s = data[pos:pos + bc].decode('utf-8')
            pos += bc
            return s, pos
        except:
            return None, pos
    return None, pos


def write_string(s) -> bytes:
    if s is None:
        return struct.pack('<i', -1)
    if s == "":
        return struct.pack('<i', 0)
    encoded = s.encode('utf-8')
    return struct.pack('<i', ~len(encoded)) + struct.pack('<i', len(s)) + encoded


# === Bundle I/O ===

def extract_textasset_raw(bundle_path: str):
    env = UnityPy.load(bundle_path)
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            obj.reset()
            reader = obj.reader
            reader.Position = obj.byte_start
            raw = reader.read(obj.byte_size)
            pos = 0
            name_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            name = raw[pos:pos + name_len].decode('utf-8', errors='replace'); pos += name_len
            pos = (pos + 3) & ~3
            script_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            script_data = raw[pos:pos + script_len]
            return name, script_data
    return None, None


# === Dialogue Extraction ===

def find_translatable_strings(data: bytes):
    results = []
    i = 9
    while i < len(data) - 5:
        if i + 5 <= len(data):
            key = struct.unpack_from('<i', data, i)[0]
            scene_tag = data[i + 4]
            if scene_tag == 21 and 0 <= key < 10000:
                scene_pos = i + 5
                scene_id = struct.unpack_from('<i', data, scene_pos)[0]
                scene_pos += 4
                speakers_count = struct.unpack_from('<i', data, scene_pos)[0]
                scene_pos += 4
                if 0 <= speakers_count <= 10:
                    valid = True
                    for sp_idx in range(speakers_count):
                        sp_start = scene_pos
                        s, scene_pos = read_string(data, scene_pos)
                        if s is not None and s != "":
                            results.append((sp_start, scene_pos, s, "speaker", scene_id, sp_idx))
                        elif s is None and scene_pos == sp_start:
                            valid = False
                            break
                    if valid:
                        text_start = scene_pos
                        text, scene_pos = read_string(data, scene_pos)
                        if text is not None and text != "":
                            results.append((text_start, scene_pos, text, "text", scene_id, 0))
                i += 5
                continue
        i += 1
    return results


def extract_dialogue(data: bytes):
    pos = 0
    tag = data[pos]; pos += 1
    if tag != 2:
        return None
    story_id = struct.unpack_from('<i', data, pos)[0]; pos += 4
    dict_count = struct.unpack_from('<i', data, pos)[0]; pos += 4
    if dict_count < 0 or dict_count > 10000:
        return None

    result = {"id": story_id, "scene_count": dict_count, "scenes": []}
    i = 9
    while i < len(data) - 5:
        if i + 5 <= len(data):
            key = struct.unpack_from('<i', data, i)[0]
            scene_tag = data[i + 4]
            if scene_tag == 21 and 0 <= key < 10000:
                scene_pos = i + 5
                scene_id = struct.unpack_from('<i', data, scene_pos)[0]
                scene_pos += 4
                speakers_count = struct.unpack_from('<i', data, scene_pos)[0]
                scene_pos += 4
                if 0 <= speakers_count <= 10:
                    speakers = []
                    valid = True
                    for _ in range(speakers_count):
                        s, scene_pos = read_string(data, scene_pos)
                        if scene_pos is None:
                            valid = False
                            break
                        speakers.append(s)
                    if valid:
                        text, scene_pos = read_string(data, scene_pos)
                        result["scenes"].append({
                            "scene_id": scene_id,
                            "speakers": speakers,
                            "text": text,
                        })
                i += 5
                continue
        i += 1
    return result


# === Translation Application ===

def apply_translations(data: bytes, translations: dict) -> bytes:
    offsets = find_translatable_strings(data)
    if not offsets:
        return data
    offsets.sort(key=lambda x: x[0])
    result = bytearray()
    prev_end = 0
    for start, end, original, stype, scene_id, idx in offsets:
        result.extend(data[prev_end:start])
        translated = translations.get(original, original)
        result.extend(write_string(translated))
        prev_end = end
    result.extend(data[prev_end:])
    return bytes(result)


# === Bundle Repacking ===

def repack_bundle(original_path: str, output_path: str, translations: dict):
    env = UnityPy.load(original_path)
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            obj.reset()
            reader = obj.reader
            reader.Position = obj.byte_start
            raw = reader.read(obj.byte_size)

            pos = 0
            name_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            name_bytes = raw[pos:pos + name_len]; pos += name_len
            pos = (pos + 3) & ~3
            script_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            encrypted_data = raw[pos:pos + script_len]

            pt = decrypt(encrypted_data)
            modified = apply_translations(pt, translations)
            new_encrypted = encrypt(modified)

            new_raw = bytearray()
            new_raw.extend(struct.pack('<i', len(name_bytes)))
            new_raw.extend(name_bytes)
            while len(new_raw) % 4 != 0:
                new_raw.append(0)
            new_raw.extend(struct.pack('<i', len(new_encrypted)))
            new_raw.extend(new_encrypted)

            obj.set_raw_data(bytes(new_raw))

    with open(output_path, 'wb') as f:
        f.write(env.file.save())


# === Commands ===

BASE_DIR = r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64"
STORY_DIR = os.path.join(BASE_DIR, "StoryMasterData")
MASTER_DIR = os.path.join(BASE_DIR, "MasterData")
OUTPUT_DIR = r"D:\cs\workshop"


def cmd_extract():
    files = sorted(os.listdir(STORY_DIR))
    print(f"Extracting dialogue from {len(files)} story bundles...")

    all_stories = []
    errors = []
    t0 = time.time()

    for i, fname in enumerate(files):
        if i % 200 == 0:
            print(f"  {i}/{len(files)} ({time.time() - t0:.1f}s)...")

        fpath = os.path.join(STORY_DIR, fname)
        try:
            name, encrypted = extract_textasset_raw(fpath)
            if encrypted is None:
                errors.append({"file": fname, "error": "no TextAsset"})
                continue
            pt = decrypt(encrypted)
            result = extract_dialogue(pt)
            if result is None:
                errors.append({"file": fname, "error": "parse failed"})
                continue
            result["bundle"] = fname
            result["asset_name"] = name
            all_stories.append(result)
        except Exception as e:
            errors.append({"file": fname, "error": str(e)})

    elapsed = time.time() - t0
    total_dialogue = sum(1 for s in all_stories for sc in s["scenes"] if sc["text"])

    print(f"\nDone in {elapsed:.1f}s")
    print(f"Stories: {len(all_stories)}/{len(files)}")
    print(f"Total dialogue lines: {total_dialogue}")
    print(f"Errors: {len(errors)}")

    out_path = os.path.join(OUTPUT_DIR, "merc_storia_dialogue.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({"stories": all_stories, "errors": errors}, f, ensure_ascii=False, indent=2)
    print(f"Saved to {out_path} ({os.path.getsize(out_path) / 1024 / 1024:.1f} MB)")


def cmd_extract_meta():
    # StoryMasterData
    story_bundle = os.path.join(MASTER_DIR, "d5f5fd6024911c22fa99b59427270214.bundle")
    _, enc = extract_textasset_raw(story_bundle)
    pt = decrypt(enc)

    pos = 0
    tag = pt[pos]; pos += 1
    count = struct.unpack_from('<i', pt, pos)[0]; pos += 4

    story_records = []
    for _ in range(count):
        mc = pt[pos]; pos += 1
        chapter_id = struct.unpack_from('<i', pt, pos)[0]; pos += 4
        story_id = struct.unpack_from('<i', pt, pos)[0]; pos += 4
        title, pos = read_string(pt, pos)
        episode, pos = read_string(pt, pos)
        scene_key, pos = read_string(pt, pos)
        unlock_type = struct.unpack_from('<i', pt, pos)[0]; pos += 4
        field6, pos = read_string(pt, pos)
        field7, pos = read_string(pt, pos)
        display_order = struct.unpack_from('<i', pt, pos)[0]; pos += 4
        story_records.append({
            "chapter_id": chapter_id, "story_id": story_id,
            "title": title, "episode": episode, "scene_key": scene_key,
            "display_order": display_order,
        })

    # ChapterMasterData
    chapter_bundle = os.path.join(MASTER_DIR, "357536dd738af9be5b6f3e5b60d3cc89.bundle")
    _, enc = extract_textasset_raw(chapter_bundle)
    pt = decrypt(enc)

    pos = 0
    tag = pt[pos]; pos += 1
    count = struct.unpack_from('<i', pt, pos)[0]; pos += 4

    chapter_records = []
    for _ in range(count):
        mc = pt[pos]; pos += 1
        fields = []
        for fi in range(mc):
            if pos + 4 > len(pt):
                break
            raw = struct.unpack_from('<i', pt, pos)[0]
            if raw == -1:
                fields.append(None); pos += 4
            elif raw == 0:
                fields.append(""); pos += 4
            elif raw < -1 and raw > -100000:
                bc = ~raw
                if bc > 0 and pos + 8 + bc <= len(pt):
                    cc = struct.unpack_from('<i', pt, pos + 4)[0]
                    if 0 < cc <= bc:
                        try:
                            s = pt[pos + 8:pos + 8 + bc].decode('utf-8')
                            if len(s) == cc:
                                fields.append(s); pos += 8 + bc; continue
                        except:
                            pass
                fields.append(raw); pos += 4
            else:
                fields.append(raw); pos += 4
        chapter_records.append({
            "id": fields[0] if len(fields) > 0 else None,
            "name": fields[1] if len(fields) > 1 else None,
            "fields": fields,
        })

    metadata = {"stories": story_records, "chapters": chapter_records}
    out_path = os.path.join(OUTPUT_DIR, "merc_metadata.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"Stories: {len(story_records)}, Chapters: {len(chapter_records)}")
    print(f"Saved to {out_path}")

    # Build chapter name lookup
    ch_map = {c["id"]: c["name"] for c in chapter_records}
    # Show some examples
    for s in story_records[:5]:
        ch_name = ch_map.get(s["chapter_id"], "?")
        print(f"  Story {s['story_id']}: [{ch_name}] {s['episode']} - {s['title']}")


def cmd_repack(translation_json_path: str):
    with open(translation_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    output_dir = os.path.join(OUTPUT_DIR, "repacked")
    os.makedirs(output_dir, exist_ok=True)

    stories = data.get("stories", [])
    print(f"Repacking {len(stories)} stories...")
    t0 = time.time()

    for i, story in enumerate(stories):
        if i % 100 == 0:
            print(f"  {i}/{len(stories)} ({time.time() - t0:.1f}s)...")

        bundle = story.get("bundle")
        if not bundle:
            continue

        # Build translations dict from scenes
        translations = {}
        has_translation = False
        for scene in story.get("scenes", []):
            orig_text = scene.get("text")
            trans_text = scene.get("translated_text")
            if orig_text and trans_text and orig_text != trans_text:
                translations[orig_text] = trans_text
                has_translation = True

            # Speaker translations
            for j, speaker in enumerate(scene.get("speakers", [])):
                trans_speaker = None
                trans_speakers = scene.get("translated_speakers")
                if trans_speakers and j < len(trans_speakers):
                    trans_speaker = trans_speakers[j]
                if speaker and trans_speaker and speaker != trans_speaker:
                    translations[speaker] = trans_speaker

        if not has_translation:
            continue

        src = os.path.join(STORY_DIR, bundle)
        dst = os.path.join(output_dir, bundle)
        try:
            repack_bundle(src, dst, translations)
        except Exception as e:
            print(f"  ERROR repacking {bundle}: {e}")

    elapsed = time.time() - t0
    repacked = len([f for f in os.listdir(output_dir) if f.endswith('.bundle')])
    print(f"\nDone in {elapsed:.1f}s, {repacked} bundles repacked")
    print(f"Output: {output_dir}")
    print(f"\nTo install: copy repacked bundles to {STORY_DIR}")


def cmd_test_repack():
    test_bundle = os.path.join(STORY_DIR, "eb777f2829400cfced05a3761d77fd6a.bundle")

    # Extract
    _, encrypted = extract_textasset_raw(test_bundle)
    pt = decrypt(encrypted)
    offsets = find_translatable_strings(pt)

    target_key = None
    for o in offsets:
        if 'おやおや' in (o[2] or ''):
            target_key = o[2]

    translations = {
        target_key: "Oh my, it seems you don't understand that person's greatness.\r\nLord Taitenki is a great yokai who will become the leader of the Hyakki Yako.\r\nThe power of a mere shizumeki like myself is not truly needed.",
        "しずめき": "Shizumeki",
        "たいてんき": "Taitenki",
        "メルク": "Merc",
    }

    output = os.path.join(OUTPUT_DIR, "test_repacked.bundle")
    repack_bundle(test_bundle, output, translations)

    # Verify
    _, enc2 = extract_textasset_raw(output)
    pt2 = decrypt(enc2)
    result = extract_dialogue(pt2)
    if result and b'greatness' in pt2:
        print("Test repack: SUCCESS")
        print(f"  Story {result['id']}: {len(result['scenes'])} scenes")
        for sc in result['scenes']:
            sp = ', '.join(s for s in sc['speakers'] if s) or '(narrator)'
            text = sc['text'] or ''
            if 'greatness' in text or 'Shizumeki' in sp:
                print(f"  [{sp}] {text[:150]}")
    else:
        print("Test repack: FAILED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merc Storia Translation Toolkit")
    parser.add_argument("command", choices=["extract", "extract-meta", "repack", "test-repack"])
    parser.add_argument("args", nargs="*")
    args = parser.parse_args()

    if args.command == "extract":
        cmd_extract()
    elif args.command == "extract-meta":
        cmd_extract_meta()
    elif args.command == "repack":
        if not args.args:
            print("Usage: python merc_storia_toolkit.py repack <translation.json>")
            sys.exit(1)
        cmd_repack(args.args[0])
    elif args.command == "test-repack":
        cmd_test_repack()
