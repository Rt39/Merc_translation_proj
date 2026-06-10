"""Repack modified story data back into encrypted UnityFS bundles.

Pipeline:
1. Read original bundle -> extract TextAsset (name + encrypted data)
2. Decrypt -> MemoryPack binary
3. Modify strings in the MemoryPack binary (find & replace UTF-8 strings)
4. Re-encrypt with same AES key (generate new random IV)
5. Write back into UnityFS bundle

For translation, we need to:
- Parse the MemoryPack data to find all string offsets
- Replace strings with translated versions (adjusting byte counts)
- Re-serialize the modified MemoryPack data
- Re-encrypt and repack into the bundle
"""
import sys, io, struct, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes, padding as sym_padding
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


def encrypt(plaintext):
    """Encrypt with AES-256-CBC, PKCS7 padding, random IV prepended."""
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    enc = cipher.encryptor()
    ct = enc.update(padded) + enc.finalize()
    return iv + ct


def read_string(data, pos):
    """Read a MemoryPack UTF-8 string, return (value, end_pos)."""
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


def write_string(s):
    """Encode a string in MemoryPack UTF-8 format."""
    if s is None:
        return struct.pack('<i', -1)
    if s == "":
        return struct.pack('<i', 0)
    encoded = s.encode('utf-8')
    byte_count = len(encoded)
    char_count = len(s)
    return struct.pack('<i', ~byte_count) + struct.pack('<i', char_count) + encoded


def find_string_offsets(data):
    """Find all translatable string positions in MemoryPack story data.
    Returns list of (offset, end_offset, string_value) tuples."""
    results = []
    i = 9  # skip StoryYamlData header (tag + story_id + dict_count)
    while i < len(data) - 5:
        if i + 5 <= len(data):
            key = struct.unpack_from('<i', data, i)[0]
            scene_tag = data[i + 4]
            if scene_tag == 21 and 0 <= key < 10000:
                scene_pos = i + 5
                scene_id = struct.unpack_from('<i', data, scene_pos)[0]
                scene_pos += 4

                # Speakers array
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
                        # Text field
                        text_start = scene_pos
                        text, scene_pos = read_string(data, scene_pos)
                        if text is not None and text != "":
                            results.append((text_start, scene_pos, text, "text", scene_id, 0))

                i += 5
                continue
        i += 1
    return results


def apply_translations(data, translations):
    """Apply translations to MemoryPack data by rebuilding it with replaced strings.

    translations: dict mapping original_string -> translated_string
    Returns modified binary data.
    """
    offsets = find_string_offsets(data)
    if not offsets:
        return data

    # Sort by offset
    offsets.sort(key=lambda x: x[0])

    # Rebuild binary by replacing string segments
    result = bytearray()
    prev_end = 0
    for start, end, original, stype, scene_id, idx in offsets:
        # Copy everything before this string
        result.extend(data[prev_end:start])
        # Write the (possibly translated) string
        translated = translations.get(original, original)
        result.extend(write_string(translated))
        prev_end = end

    # Copy remainder
    result.extend(data[prev_end:])
    return bytes(result)


def repack_bundle(original_bundle_path, output_bundle_path, translations):
    """Repack a bundle with translated text.

    1. Load original bundle
    2. Extract and decrypt TextAsset
    3. Apply translations to MemoryPack data
    4. Re-encrypt
    5. Write modified bundle
    """
    env = UnityPy.load(original_bundle_path)

    for obj in env.objects:
        if obj.type.name == "TextAsset":
            # Read raw bytes
            obj.reset()
            reader = obj.reader
            reader.Position = obj.byte_start
            raw = reader.read(obj.byte_size)

            # Parse TextAsset manually
            pos = 0
            name_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            name = raw[pos:pos+name_len].decode('utf-8', errors='replace'); pos += name_len
            pos = (pos + 3) & ~3
            script_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            encrypted_data = raw[pos:pos+script_len]

            # Decrypt
            plaintext = decrypt(encrypted_data)

            # Apply translations
            modified = apply_translations(plaintext, translations)

            # Re-encrypt
            new_encrypted = encrypt(modified)

            # Rebuild TextAsset raw bytes
            new_raw = bytearray()
            new_raw.extend(struct.pack('<i', name_len))
            new_raw.extend(name.encode('utf-8'))
            # Align to 4 bytes
            while len(new_raw) % 4 != 0:
                new_raw.append(0)
            new_raw.extend(struct.pack('<i', len(new_encrypted)))
            new_raw.extend(new_encrypted)

            # Write back through UnityPy
            # We need to use the tree/data approach
            ta = obj.read()
            ta.script = new_encrypted
            ta.save()

    # Save modified bundle
    with open(output_bundle_path, 'wb') as f:
        f.write(env.file.save())

    return True


# === TEST ===
STORY_DIR = r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64\StoryMasterData"
TEST_BUNDLE = os.path.join(STORY_DIR, "eb777f2829400cfced05a3761d77fd6a.bundle")
TEST_OUTPUT = r"D:\cs\workshop\test_repacked.bundle"

# First, verify string finding works
print("=== Testing string offset finding ===")
env = UnityPy.load(TEST_BUNDLE)
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
        encrypted_data = raw[pos:pos+script_len]
        pt = decrypt(encrypted_data)

        offsets = find_string_offsets(pt)
        print(f"Found {len(offsets)} translatable strings")
        for o in offsets[:10]:
            print(f"  [{o[3]}] scene {o[4]}: '{o[2][:80]}'")
        print(f"  ...")

# Now test round-trip: decrypt -> re-encrypt -> decrypt and verify
print("\n=== Testing round-trip encryption ===")
test_data = b"Hello World Test Data 12345"
enc = encrypt(test_data)
dec = decrypt(enc)
assert dec == test_data, f"Round-trip failed! {dec} != {test_data}"
print("Round-trip encryption: OK")

# Test with actual story data
enc2 = encrypt(pt)
dec2 = decrypt(enc2)
assert dec2 == pt, "Round-trip with story data failed!"
print("Round-trip with story data: OK")

# Test translation application
print("\n=== Testing translation application ===")
test_translations = {
    "おやおや、あの方の偉大さがわかっていないようですね。\nたいてんき様は百鬼夜行の頭領となられる大妖怪。\nこのしずめきごときの力など、本来必要ないのです。":
    "Oh my, it seems you don't understand that person's greatness.\nLord Taitenki is a great yokai who will become the leader of the Hyakki Yako.\nThe power of a mere shizumeki like myself is not needed.",
    "しずめき": "Shizumeki",
}

modified = apply_translations(pt, test_translations)
print(f"Original size: {len(pt)}, Modified size: {len(modified)}")

# Verify the modified data still has correct structure
def _quick_extract(data):
    pos = 0
    tag = data[pos]; pos += 1
    if tag != 2: return None
    sid = struct.unpack_from('<i', data, pos)[0]; pos += 4
    dc = struct.unpack_from('<i', data, pos)[0]; pos += 4
    result = {"id": sid, "scenes": []}
    i = 9
    while i < len(data) - 5:
        if i + 5 <= len(data):
            key = struct.unpack_from('<i', data, i)[0]
            st = data[i + 4]
            if st == 21 and 0 <= key < 10000:
                sp = i + 5
                scene_id = struct.unpack_from('<i', data, sp)[0]; sp += 4
                sc_cnt = struct.unpack_from('<i', data, sp)[0]; sp += 4
                if 0 <= sc_cnt <= 10:
                    speakers = []; valid = True
                    for _ in range(sc_cnt):
                        s, sp = read_string(data, sp)
                        if sp is None: valid = False; break
                        speakers.append(s)
                    if valid:
                        text, sp = read_string(data, sp)
                        result["scenes"].append({"scene_id": scene_id, "speakers": speakers, "text": text})
                i += 5; continue
        i += 1
    return result

result = _quick_extract(modified)
if result:
    print(f"Modified parse OK: {len(result['scenes'])} scenes")
    for sc in result['scenes']:
        text = sc.get('text', '')
        if text and 'greatness' in text:
            sp = ', '.join(s for s in sc['speakers'] if s) or '(narrator)'
            print(f"  TRANSLATED: [{sp}] {text}")

# Test full repack
print("\n=== Testing full bundle repack ===")
try:
    repack_bundle(TEST_BUNDLE, TEST_OUTPUT, test_translations)
    print(f"Repacked bundle saved to {TEST_OUTPUT}")
    print(f"Original size: {os.path.getsize(TEST_BUNDLE)}")
    print(f"Repacked size: {os.path.getsize(TEST_OUTPUT)}")

    # Verify repacked bundle
    env2 = UnityPy.load(TEST_OUTPUT)
    for obj in env2.objects:
        if obj.type.name == "TextAsset":
            obj.reset()
            reader = obj.reader
            reader.Position = obj.byte_start
            raw = reader.read(obj.byte_size)
            pos = 0
            name_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            name2 = raw[pos:pos+name_len].decode('utf-8', errors='replace'); pos += name_len
            pos = (pos + 3) & ~3
            script_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            enc_data = raw[pos:pos+script_len]
            pt2 = decrypt(enc_data)
            result2 = _quick_extract(pt2)
            if result2:
                print(f"Repacked bundle parse OK: {len(result2['scenes'])} scenes")
                for sc in result2['scenes']:
                    text = sc.get('text', '')
                    if text and ('greatness' in text or 'Shizumeki' in ','.join(str(s) for s in sc['speakers'])):
                        sp = ', '.join(s for s in sc['speakers'] if s) or '(narrator)'
                        print(f"  VERIFIED: [{sp}] {text}")
    print("\nFull repack pipeline: SUCCESS")
except Exception as e:
    import traceback
    print(f"Repack failed: {e}")
    traceback.print_exc()
