"""Merc Storia story data decryption and MemoryPack parser.

Encryption: AES-256-CBC-PKCS7
- Key: PBKDF2-HMAC-SHA256("2147483647", "-2147483648", iterations=1024, dklen=32)
- IV: First 16 bytes of ciphertext (prepended)

MemoryPack format (UTF-8 mode):
- Strings: int32 ~utf8_byte_count, int32 char_count, byte[utf8_byte_count]
  - Null string: int32 = 0 (since ~(-1) = 0, NOT of -1 which means "no bytes")
- Arrays: int32 count (-1 = null), then elements
- Objects: byte member_count (0xFF = null), then fields
- Primitives: little-endian, int32/float32/bool(byte)
"""
import sys, io, struct, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import UnityPy

# Derive the AES key (constant)
_kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                   salt=b"-2147483648", iterations=1024)
AES_KEY = _kdf.derive(b"2147483647")


def decrypt(data: bytes) -> bytes:
    iv, ct = data[:16], data[16:]
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    pt = cipher.decryptor().update(ct) + cipher.decryptor().finalize()
    # Actually need a single decryptor
    cipher2 = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    dec = cipher2.decryptor()
    pt = dec.update(ct) + dec.finalize()
    pad = pt[-1]
    if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
        return pt[:-pad]
    return pt


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def byte(self) -> int:
        v = self.data[self.pos]; self.pos += 1; return v

    def bool(self) -> bool:
        return bool(self.byte())

    def i32(self) -> int:
        v = struct.unpack_from('<i', self.data, self.pos)[0]; self.pos += 4; return v

    def f32(self) -> float:
        v = struct.unpack_from('<f', self.data, self.pos)[0]; self.pos += 4; return v

    def string(self):
        # MemoryPack UTF-8 string format:
        # int32 header: -1 = null, 0 = empty, negative = ~utf8_byte_count (UTF-8 mode)
        raw = self.i32()
        if raw == -1:
            return None
        if raw == 0:
            return ""
        # raw is ~utf8_byte_count (negative value)
        byte_count = ~raw
        char_count = self.i32()
        s = self.data[self.pos:self.pos + byte_count].decode('utf-8', errors='replace')
        self.pos += byte_count
        return s

    def string_array(self):
        count = self.i32()
        if count == -1:
            return None
        return [self.string() for _ in range(count)]

    def obj_header(self):
        tag = self.byte()
        return None if tag == 0xFF else tag

    def character(self):
        mc = self.obj_header()
        if mc is None: return None
        return {"Type": self.i32(), "Id": self.i32(), "FaceType": self.i32()}

    def text_animation(self):
        mc = self.obj_header()
        if mc is None: return None
        return {"Type": self.i32(), "Speed": self.i32()}

    def bg_effect_param(self):
        mc = self.obj_header()
        if mc is None: return None
        return {"Type": self.i32(), "Parameter": self.string()}

    def bg_music(self):
        mc = self.obj_header()
        if mc is None: return None
        return {"Key": self.string(), "Volume": self.f32()}

    def sound_effect(self):
        mc = self.obj_header()
        if mc is None: return None
        return {"Key": self.string(), "Volume": self.f32(), "Loop": self.bool()}

    def sound_effect_array(self):
        count = self.i32()
        if count == -1: return None
        return [self.sound_effect() for _ in range(count)]

    def effect_param(self):
        mc = self.obj_header()
        if mc is None: return None
        return {"Color": self.string(), "Duration": self.f32()}

    def asset_param(self):
        mc = self.obj_header()
        if mc is None: return None
        return {"Position": self.i32(), "Type": self.i32()}

    def asset_param_array(self):
        count = self.i32()
        if count == -1: return None
        return [self.asset_param() for _ in range(count)]

    def scene(self):
        mc = self.obj_header()
        if mc is None: return None
        return {
            "SceneId": self.i32(),
            "Speakers": self.string_array(),
            "Text": self.string(),
            "MessageWindowType": self.i32(),
            "MessageTextSize": self.i32(),
            "TextAnimation": self.text_animation(),
            "ForceShowAllText": self.bool(),
            "Background": self.string(),
            "Timezone": self.i32(),
            "BackgroundEffectParameter": self.bg_effect_param(),
            "BackgroundMusic": self.bg_music(),
            "SoundEffects": self.sound_effect_array(),
            "Effect": self.i32(),
            "EffectParameter": self.effect_param(),
            "DisableWipe": self.bool(),
            "Left": self.character(),
            "Center": self.character(),
            "Right": self.character(),
            "AssetsKeys": self.string_array(),
            "AssetParameters": self.asset_param_array(),
            "WaitTarget": self.string(),
        }

    def story(self):
        mc = self.obj_header()
        if mc is None: return None
        story_id = self.i32()
        dict_count = self.i32()
        scenes = {}
        for _ in range(dict_count):
            key = self.i32()
            scenes[key] = self.scene()
        return {"Id": story_id, "Scenes": scenes}


def extract_bundle_textasset(bundle_path: str) -> tuple:
    """Extract raw TextAsset bytes from a bundle file."""
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


def process_story_bundle(bundle_path: str):
    """Decrypt and parse a story bundle."""
    name, encrypted = extract_bundle_textasset(bundle_path)
    if encrypted is None:
        return None
    plaintext = decrypt(encrypted)
    r = Reader(plaintext)
    return r.story()


# Test
if __name__ == "__main__":
    STORY_DIR = r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64\StoryMasterData"

    # Test on the sample we know works
    sample = os.path.join(STORY_DIR, "00153b18eb48299a131ee5437f794d79.bundle")
    story = process_story_bundle(sample)

    if story:
        print(f"Story ID: {story['Id']}")
        print(f"Scenes: {len(story['Scenes'])}")
        for key in sorted(story['Scenes'].keys()):
            scene = story['Scenes'][key]
            speakers = scene.get('Speakers') or []
            text = scene.get('Text') or ''
            if text:
                sp = ', '.join(s for s in speakers if s) or '(narrator)'
                print(f"  [{sp}] {text[:100]}")
    else:
        print("Failed to parse!")

    # Test a few more
    print("\n\nTesting more bundles...")
    files = sorted(os.listdir(STORY_DIR))
    success = 0
    fail = 0
    for f in files[:20]:
        try:
            story = process_story_bundle(os.path.join(STORY_DIR, f))
            if story:
                success += 1
            else:
                fail += 1
        except Exception as e:
            print(f"  Error on {f}: {e}")
            fail += 1
    print(f"\nResults: {success} success, {fail} fail out of {min(20, len(files))}")
