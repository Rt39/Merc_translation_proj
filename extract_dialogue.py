"""Extract all dialogue from Merc Storia story bundles.

Since the MemoryPack sub-types are complex, we use a simpler approach:
scan the decrypted binary for all UTF-8 strings and identify dialogue patterns.

The StorySceneYamlData format has scenes with:
- Speakers: string[]
- Text: string

Each string in MemoryPack UTF-8 mode is:
  int32: ~utf8_byte_count (negative, bitwise NOT)
  int32: char_count
  byte[utf8_byte_count]: UTF-8 data

We can find all strings and then identify the speaker/text pairs.
"""
import sys, io, struct, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import UnityPy

kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                  salt=b"-2147483648", iterations=1024)
AES_KEY = kdf.derive(b"2147483647")


def decrypt(data: bytes) -> bytes:
    iv, ct = data[:16], data[16:]
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    dec = cipher.decryptor()
    pt = dec.update(ct) + dec.finalize()
    pad = pt[-1]
    if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
        return pt[:-pad]
    return pt


def extract_bundle_textasset(bundle_path: str):
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


def extract_strings(data: bytes):
    """Extract all MemoryPack UTF-8 strings from binary data."""
    strings = []
    i = 0
    while i < len(data) - 8:
        raw = struct.unpack_from('<i', data, i)[0]
        if raw < -1 and raw > -100000:  # ~bytecount is negative
            byte_count = ~raw
            if byte_count > 0 and i + 8 + byte_count <= len(data):
                char_count = struct.unpack_from('<i', data, i + 4)[0]
                if 0 < char_count <= byte_count:  # char_count <= byte_count for UTF-8
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


def extract_dialogue(data: bytes):
    """Extract dialogue by parsing StoryYamlData structure partially.

    We only need the header (Id, dict_count) and then for each scene
    just the Speakers and Text fields.
    """
    pos = 0

    # StoryYamlData header
    tag = data[pos]; pos += 1
    if tag != 2:
        return None

    story_id = struct.unpack_from('<i', data, pos)[0]; pos += 4
    dict_count = struct.unpack_from('<i', data, pos)[0]; pos += 4

    if dict_count < 0 or dict_count > 10000:
        return None

    # Instead of parsing the complex structure, extract all strings
    strings = extract_strings(data)

    # Group strings into scenes
    # Each scene has: speakers (1+ strings), text (1 string), then other fields
    # The pattern is: scene starts with object header (0x15 for 21 fields),
    # then int32 SceneId, then string[] Speakers, then string Text

    result = {"Id": story_id, "SceneCount": dict_count, "Dialogue": []}

    # Find scene boundaries by looking for the member count byte (0x15 = 21)
    # followed by SceneId pattern
    scenes = []
    i = 9  # after StoryYamlData header

    while i < len(data) - 5:
        # Dictionary key (int32) + scene header byte
        if i + 5 <= len(data):
            key = struct.unpack_from('<i', data, i)[0]
            scene_tag = data[i + 4]

            if scene_tag == 21 and 0 <= key < 10000:
                # Possible scene start
                scene_pos = i + 5  # after key + tag
                scene_id = struct.unpack_from('<i', data, scene_pos)[0]
                scene_pos += 4

                # Read Speakers array
                speakers_count = struct.unpack_from('<i', data, scene_pos)[0]
                scene_pos += 4

                if 0 <= speakers_count <= 10:
                    speakers = []
                    valid = True
                    for _ in range(speakers_count):
                        raw = struct.unpack_from('<i', data, scene_pos)[0]
                        scene_pos += 4
                        if raw == -1:
                            speakers.append(None)
                        elif raw == 0:
                            speakers.append("")
                        elif raw < -1:
                            bc = ~raw
                            if bc > 0 and bc < 1000 and scene_pos + 4 + bc <= len(data):
                                cc = struct.unpack_from('<i', data, scene_pos)[0]
                                scene_pos += 4
                                try:
                                    s = data[scene_pos:scene_pos+bc].decode('utf-8')
                                    scene_pos += bc
                                    speakers.append(s)
                                except:
                                    valid = False
                                    break
                            else:
                                valid = False
                                break
                        else:
                            valid = False
                            break

                    if valid:
                        # Read Text
                        raw = struct.unpack_from('<i', data, scene_pos)[0]
                        scene_pos += 4
                        text = None
                        if raw == -1:
                            text = None
                        elif raw == 0:
                            text = ""
                        elif raw < -1:
                            bc = ~raw
                            if bc > 0 and bc < 50000 and scene_pos + 4 + bc <= len(data):
                                cc = struct.unpack_from('<i', data, scene_pos)[0]
                                scene_pos += 4
                                try:
                                    text = data[scene_pos:scene_pos+bc].decode('utf-8')
                                    scene_pos += bc
                                except:
                                    text = None

                        result["Dialogue"].append({
                            "SceneId": scene_id,
                            "Speakers": speakers,
                            "Text": text,
                        })

                # Skip to next potential scene
                i += 5
                continue
        i += 1

    return result


def process_story(bundle_path: str):
    name, encrypted = extract_bundle_textasset(bundle_path)
    if encrypted is None:
        return None
    plaintext = decrypt(encrypted)
    return extract_dialogue(plaintext)


# Test
STORY_DIR = r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64\StoryMasterData"

# Test on known sample
sample = os.path.join(STORY_DIR, "00153b18eb48299a131ee5437f794d79.bundle")
result = process_story(sample)
if result:
    print(f"Story {result['Id']}: {result['SceneCount']} scenes, {len(result['Dialogue'])} dialogue entries\n")
    for d in result['Dialogue']:
        sp = ', '.join(s for s in d['Speakers'] if s) or '(narrator)'
        text = d['Text'] or ''
        if text:
            print(f"[{sp}] {text[:120]}")

# Test batch
print("\n\n=== Batch test ===")
files = sorted(os.listdir(STORY_DIR))
success = 0
total_dialogue = 0
for fname in files[:50]:
    try:
        r = process_story(os.path.join(STORY_DIR, fname))
        if r and r['Dialogue']:
            success += 1
            total_dialogue += len(r['Dialogue'])
    except Exception as e:
        pass

print(f"Parsed {success}/50 bundles, {total_dialogue} total dialogue entries")
