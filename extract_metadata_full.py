"""Extract full story and chapter metadata mappings."""
import sys, io, struct, os, json
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


def extract_textasset(bundle_path):
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
            s = data[pos:pos+bc].decode('utf-8')
            pos += bc
            return s, pos
        except:
            return None, pos
    return None, pos


MASTER_DIR = r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64\MasterData"

# === StoryMasterData ===
story_bundle = os.path.join(MASTER_DIR, "d5f5fd6024911c22fa99b59427270214.bundle")
_, enc = extract_textasset(story_bundle)
pt = decrypt(enc)

pos = 0
tag = pt[pos]; pos += 1  # 1 = member count of wrapper
count = struct.unpack_from('<i', pt, pos)[0]; pos += 4

story_records = []
for _ in range(count):
    mc = pt[pos]; pos += 1
    # 9 fields: ChapterId(int), StoryId(int), Title(str), EpisodeName(str),
    #           SceneKey(str), UnlockType(int), ?, ?, DisplayOrder(int)
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
        "chapter_id": chapter_id,
        "story_id": story_id,
        "title": title,
        "episode": episode,
        "scene_key": scene_key,
        "display_order": display_order,
    })

print(f"StoryMasterData: {len(story_records)} records")

# Find our example story (ID 1621)
for r in story_records:
    if r["story_id"] == 1621:
        print(f"  Story 1621: chapter={r['chapter_id']}, title='{r['title']}', episode='{r['episode']}'")
        break

# === ChapterMasterData ===
chapter_bundle = os.path.join(MASTER_DIR, "357536dd738af9be5b6f3e5b60d3cc89.bundle")
_, enc = extract_textasset(chapter_bundle)
pt = decrypt(enc)

pos = 0
tag = pt[pos]; pos += 1
count = struct.unpack_from('<i', pt, pos)[0]; pos += 4

# Examine first record to understand structure
print(f"\nChapterMasterData: {count} records")
print(f"First record starts at {pos}, hex: {pt[pos:pos+80].hex()}")

chapter_records = []
for _ in range(count):
    mc = pt[pos]; pos += 1
    # Need to figure out field types
    # Let's try: Id(int), Name(str), Type(int), ...
    fields = []
    for fi in range(mc):
        if pos + 4 > len(pt):
            break
        raw = struct.unpack_from('<i', pt, pos)[0]
        if raw == -1:
            fields.append(None)
            pos += 4
        elif raw == 0:
            fields.append("")
            pos += 4
        elif raw < -1 and raw > -100000:
            bc = ~raw
            if bc > 0 and pos + 8 + bc <= len(pt):
                cc = struct.unpack_from('<i', pt, pos + 4)[0]
                if 0 < cc <= bc:
                    try:
                        s = pt[pos+8:pos+8+bc].decode('utf-8')
                        if len(s) == cc:
                            fields.append(s)
                            pos += 8 + bc
                            continue
                    except:
                        pass
            fields.append(raw)
            pos += 4
        else:
            fields.append(raw)
            pos += 4

    chapter_records.append(fields)

# Print first few
for i, fields in enumerate(chapter_records[:5]):
    print(f"  Chapter {i}: {fields}")

# Find chapter for story 1621
target_chapter_id = None
for r in story_records:
    if r["story_id"] == 1621:
        target_chapter_id = r["chapter_id"]
        break

if target_chapter_id:
    print(f"\nLooking for chapter ID {target_chapter_id}...")
    for fields in chapter_records:
        if fields and fields[0] == target_chapter_id:
            print(f"  Found: {fields}")
            break

# Save metadata
metadata = {
    "stories": story_records,
    "chapters": [{"fields": f} for f in chapter_records],
}
out_path = r"D:\cs\workshop\merc_metadata.json"
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(metadata, f, ensure_ascii=False, indent=2)
print(f"\nSaved metadata to {out_path}")
