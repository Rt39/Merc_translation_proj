"""Extract every story bundle in StoryMasterData/ to its own JSON file.

Output layout:
    <OUT_DIR>/<story_id>.json    — one file per story, named by numeric Id
    <OUT_DIR>/_errors.json       — list of bundles that failed to parse
    <OUT_DIR>/_index.json        — bundle filename ↔ story_id mapping

Each story file is the same shape as one element of extract_all.py's combined
output, minus the redundant wrapper:

    {
      "id":          <int>,
      "bundle":      "<hash>.bundle",
      "asset_name":  "<TextAsset name>",
      "scene_count": <int>,
      "scenes": [
        {"scene_id": <int>, "speakers": [...], "text": "..."},
        ...
      ]
    }

Run with uv (all deps are declared in pyproject.toml):

    uv run extract_all_separate.py [STORY_DIR] [OUT_DIR]
"""
import sys, io, struct, os, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import UnityPy

_kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                  salt=b"-2147483648", iterations=1024)
AES_KEY = _kdf.derive(b"2147483647")


def decrypt(data):
    iv, ct = data[:16], data[16:]
    dec = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv)).decryptor()
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
            return name, raw[pos:pos+script_len]
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
        struct.unpack_from('<i', data, pos)[0]  # char_count, ignored
        pos += 4
        try:
            s = data[pos:pos+bc].decode('utf-8')
            return s, pos + bc
        except UnicodeDecodeError:
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
        key = struct.unpack_from('<i', data, i)[0]
        scene_tag = data[i + 4]
        if scene_tag == 21 and 0 <= key < 10000:
            sp = i + 5
            scene_id = struct.unpack_from('<i', data, sp)[0]; sp += 4
            sc_cnt = struct.unpack_from('<i', data, sp)[0]; sp += 4
            if 0 <= sc_cnt <= 10:
                speakers = []
                valid = True
                for _ in range(sc_cnt):
                    s, sp = read_string(data, sp)
                    if sp is None:
                        valid = False
                        break
                    speakers.append(s)
                if valid:
                    text, sp = read_string(data, sp)
                    result["scenes"].append({
                        "scene_id": scene_id,
                        "speakers": speakers,
                        "text": text,
                    })
            i += 5
            continue
        i += 1
    return result


DEFAULT_STORY_DIR = (
    r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements"
    r"\メルストM\AssetBundle\StandaloneWindows64\StoryMasterData"
)
DEFAULT_OUT_DIR = r"D:\cs\workshop\stories"


def main():
    story_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_STORY_DIR
    out_dir = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT_DIR

    if not os.path.isdir(story_dir):
        print(f"ERROR: story dir does not exist: {story_dir}")
        sys.exit(1)
    os.makedirs(out_dir, exist_ok=True)

    files = sorted(os.listdir(story_dir))
    print(f"Processing {len(files)} story bundles → {out_dir}")

    errors = []
    index = {}  # bundle filename → story id
    t0 = time.time()
    written = 0

    for i, fname in enumerate(files):
        if i % 500 == 0:
            print(f"  {i}/{len(files)} ({time.time() - t0:.1f}s, {written} written)")
        fpath = os.path.join(story_dir, fname)
        try:
            name, enc = extract_bundle_textasset(fpath)
            if enc is None:
                errors.append({"file": fname, "error": "no TextAsset"})
                continue
            pt = decrypt(enc)
            story = extract_dialogue(pt)
            if story is None:
                errors.append({"file": fname, "error": "parse failed"})
                continue

            story["bundle"] = fname
            story["asset_name"] = name
            sid = story["id"]
            index[fname] = sid

            out_path = os.path.join(out_dir, f"{sid}.json")
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(story, f, ensure_ascii=False, indent=2)
            written += 1
        except Exception as e:
            errors.append({"file": fname, "error": str(e)})

    with open(os.path.join(out_dir, "_index.json"), 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "_errors.json"), 'w', encoding='utf-8') as f:
        json.dump(errors, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  Wrote: {written} story files")
    print(f"  Errors: {len(errors)} (see {out_dir}/_errors.json)")


if __name__ == "__main__":
    main()
