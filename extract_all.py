"""Extract ALL dialogue from all 4015 StoryMasterData bundles to JSON."""
import sys, io, struct, os, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import UnityPy

kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                  salt=b"-2147483648", iterations=1024)
AES_KEY = kdf.derive(b"2147483647")


def decrypt(data):
    iv, ct = data[:16], data[16:]
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    dec = cipher.decryptor()
    pt = dec.update(ct) + dec.finalize()
    pad = pt[-1]
    if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
        return pt[:-pad]
    return pt


def extract_bundle_textasset(bundle_path):
    env = UnityPy.load(bundle_path)
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            obj.reset()
            reader = obj.reader
            reader.Position = obj.byte_start
            raw = reader.read(obj.byte_size)
            pos = 0
            name_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            name = raw[pos:pos+name_len].decode('utf-8', errors='replace'); pos += name_len
            pos = (pos + 3) & ~3
            script_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            script_data = raw[pos:pos+script_len]
            return name, script_data
    return None, None


def read_string(data, pos):
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
            s = data[pos:pos+bc].decode('utf-8')
            pos += bc
            return s, pos
        except:
            return None, pos
    return None, pos


def extract_dialogue(data):
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


STORY_DIR = r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64\StoryMasterData"

files = sorted(os.listdir(STORY_DIR))
print(f"Processing {len(files)} story bundles...")

all_stories = []
errors = []
t0 = time.time()

for i, fname in enumerate(files):
    if i % 200 == 0:
        elapsed = time.time() - t0
        print(f"  {i}/{len(files)} ({elapsed:.1f}s)...")

    fpath = os.path.join(STORY_DIR, fname)
    try:
        name, encrypted = extract_bundle_textasset(fpath)
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
total_scenes = sum(len(s["scenes"]) for s in all_stories)
total_dialogue = sum(1 for s in all_stories for sc in s["scenes"] if sc["text"])

print(f"\nDone in {elapsed:.1f}s")
print(f"Stories: {len(all_stories)}/{len(files)}")
print(f"Total scenes: {total_scenes}")
print(f"Total dialogue lines: {total_dialogue}")
print(f"Errors: {len(errors)}")
if errors:
    for e in errors[:10]:
        print(f"  {e['file']}: {e['error']}")
    if len(errors) > 10:
        print(f"  ... and {len(errors)-10} more")

out_path = r"D:\cs\workshop\merc_storia_dialogue.json"
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump({"stories": all_stories, "errors": errors}, f, ensure_ascii=False, indent=2)
print(f"\nSaved to {out_path}")
print(f"File size: {os.path.getsize(out_path) / 1024 / 1024:.1f} MB")
