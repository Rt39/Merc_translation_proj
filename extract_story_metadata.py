"""Extract StoryMasterData and ChapterMasterData to map story IDs to names."""
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


def extract_all_strings(data):
    strings = []
    i = 0
    while i < len(data) - 8:
        raw = struct.unpack_from('<i', data, i)[0]
        if raw < -1 and raw > -100000:
            byte_count = ~raw
            if byte_count > 0 and i + 8 + byte_count <= len(data):
                char_count = struct.unpack_from('<i', data, i + 4)[0]
                if 0 < char_count <= byte_count:
                    try:
                        s = data[i+8:i+8+byte_count].decode('utf-8', errors='strict')
                        if len(s) == char_count:
                            strings.append((i, s))
                            i += 8 + byte_count
                            continue
                    except:
                        pass
        i += 1
    return strings


MASTER_DIR = r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64\MasterData"

# StoryMasterData - maps story IDs to episode/chapter names
story_bundle = os.path.join(MASTER_DIR, "d5f5fd6024911c22fa99b59427270214.bundle")
name, enc = extract_textasset(story_bundle)
pt = decrypt(enc)
print(f"StoryMasterData: {len(pt)} bytes decrypted")

# Parse the StoryMasterData structure
# It's a MemoryPack list of StoryMasterDataRecord
# Each record has: Id, ChapterId, SceneKey, Title, EpisodeName, etc.
# Let's examine the structure more carefully

# First, dump the header
print(f"Header: {pt[:20].hex()}")
print(f"Tag: {pt[0]}")

# Extract all int32 + string pairs to find ID-to-name mappings
# StoryMasterData is an array of records
# Each record has int fields and string fields

# Let's try to parse it as MemoryPack records
pos = 0
tag = pt[pos]; pos += 1
print(f"Root tag (member count): {tag}")

# It might be a list: count first
count = struct.unpack_from('<i', pt, pos)[0]; pos += 4
print(f"Record count: {count}")

# Let's see the first record's structure
print(f"\nFirst record hex: {pt[pos:pos+100].hex()}")

# Try parsing as objects with member count tags
records = []
for rec_idx in range(min(count, 5)):
    rec_start = pos
    mc = pt[pos]; pos += 1
    print(f"\nRecord {rec_idx} at offset {rec_start}, member_count={mc}")

    # Read mc fields - try to identify types from the data
    fields = []
    for fi in range(mc):
        if pos + 4 > len(pt):
            break
        # Check if it's a string
        raw = struct.unpack_from('<i', pt, pos)[0]
        if raw == -1:
            fields.append(("null", None))
            pos += 4
        elif raw == 0:
            fields.append(("empty_str", ""))
            pos += 4
        elif raw < -1 and raw > -100000:
            bc = ~raw
            if bc > 0 and pos + 8 + bc <= len(pt):
                cc = struct.unpack_from('<i', pt, pos + 4)[0]
                if 0 < cc <= bc:
                    try:
                        s = pt[pos+8:pos+8+bc].decode('utf-8')
                        if len(s) == cc:
                            fields.append(("string", s))
                            pos += 8 + bc
                            continue
                    except:
                        pass
            # Fallback: treat as int
            fields.append(("int", raw))
            pos += 4
        else:
            fields.append(("int", raw))
            pos += 4

    for fi, (ftype, fval) in enumerate(fields):
        vstr = repr(fval) if ftype == "string" else str(fval)
        print(f"  field[{fi}]: {ftype} = {vstr[:100]}")
