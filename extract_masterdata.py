"""Extract MasterData bundles (chapter names, story titles, etc.)."""
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


def extract_all_textassets(bundle_path):
    """Extract all TextAssets from a bundle."""
    results = []
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
            results.append((name, script_data))
    return results


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


def extract_all_strings(data):
    """Extract all MemoryPack UTF-8 strings from binary data."""
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

files = sorted(os.listdir(MASTER_DIR))
print(f"MasterData directory: {len(files)} files")

for fname in files:
    if not fname.endswith('.bundle'):
        continue
    fpath = os.path.join(MASTER_DIR, fname)
    try:
        assets = extract_all_textassets(fpath)
        for name, enc_data in assets:
            pt = decrypt(enc_data)
            strings = extract_all_strings(pt)
            print(f"\n{fname} -> {name}: {len(enc_data)} bytes enc, {len(pt)} bytes dec, {len(strings)} strings")
            if len(strings) <= 30:
                for offset, s in strings:
                    print(f"  [{offset}] {s[:200]}")
            else:
                for offset, s in strings[:10]:
                    print(f"  [{offset}] {s[:200]}")
                print(f"  ... ({len(strings)} total)")
                for offset, s in strings[-5:]:
                    print(f"  [{offset}] {s[:200]}")
    except Exception as e:
        print(f"ERROR {fname}: {e}")
