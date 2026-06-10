"""Full decryption and MemoryPack parsing of StoryYamlData.

Encryption: AES-256-CBC-PKCS7
- Key: PBKDF2-HMAC-SHA256("2147483647", "-2147483648", 1024, 32)
- IV: First 16 bytes of ciphertext (prepended)
- Ciphertext: Remaining bytes after IV

Post-decryption: MemoryPack binary format with UTF-8 strings.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import struct
import os
import json

# Derive the AES key (constant for all files)
kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                  salt="-2147483648".encode('utf-8'), iterations=1024)
AES_KEY = kdf.derive("2147483647".encode('utf-8'))

def decrypt(encrypted_data):
    """Decrypt AES-256-CBC data with prepended IV."""
    iv = encrypted_data[:16]
    ct = encrypted_data[16:]
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    dec = cipher.decryptor()
    pt = dec.update(ct) + dec.finalize()
    # Remove PKCS7 padding
    pad = pt[-1]
    if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
        return pt[:-pad]
    return pt

class MemoryPackReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read_byte(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def read_int32(self):
        v = struct.unpack_from('<i', self.data, self.pos)[0]
        self.pos += 4
        return v

    def read_bool(self):
        v = self.data[self.pos]
        self.pos += 1
        return bool(v)

    def read_string(self):
        length = self.read_int32()
        if length == -1:
            return None
        s = self.data[self.pos:self.pos+length].decode('utf-8', errors='replace')
        self.pos += length
        return s

    def read_string_array(self):
        count = self.read_int32()
        if count == -1:
            return None
        return [self.read_string() for _ in range(count)]

    def read_nullable_object_header(self):
        """Read MemoryPack object header. Returns member count or None for null."""
        tag = self.read_byte()
        if tag == 0xFF:
            return None
        return tag

    def read_story_scene_character(self):
        """StorySceneCharacterYamlData: Type(int), Id(int), FaceType(int)"""
        mc = self.read_nullable_object_header()
        if mc is None:
            return None
        return {
            "Type": self.read_int32(),
            "Id": self.read_int32(),
            "FaceType": self.read_int32(),
        }

    def read_story_text_animation(self):
        """StoryTextAnimationYamlData"""
        mc = self.read_nullable_object_header()
        if mc is None:
            return None
        result = {}
        result["Type"] = self.read_int32()
        result["Speed"] = self.read_int32()
        return result

    def read_background_effect_parameter(self):
        """StoryBackgroundEffectParameterYamlData"""
        mc = self.read_nullable_object_header()
        if mc is None:
            return None
        result = {}
        result["Type"] = self.read_int32()
        result["Parameter"] = self.read_string()
        return result

    def read_background_music(self):
        """StoryBackgroundMusicYamlData"""
        mc = self.read_nullable_object_header()
        if mc is None:
            return None
        result = {}
        result["Key"] = self.read_string()
        result["Volume"] = struct.unpack_from('<f', self.data, self.pos)[0]
        self.pos += 4
        return result

    def read_sound_effect(self):
        """StorySoundEffectYamlData"""
        mc = self.read_nullable_object_header()
        if mc is None:
            return None
        result = {}
        result["Key"] = self.read_string()
        result["Volume"] = struct.unpack_from('<f', self.data, self.pos)[0]
        self.pos += 4
        result["Loop"] = self.read_bool()
        return result

    def read_sound_effect_array(self):
        count = self.read_int32()
        if count == -1:
            return None
        return [self.read_sound_effect() for _ in range(count)]

    def read_effect_parameter(self):
        """StoryEffectParameterYamlData"""
        mc = self.read_nullable_object_header()
        if mc is None:
            return None
        result = {}
        result["Color"] = self.read_string()
        result["Duration"] = struct.unpack_from('<f', self.data, self.pos)[0]
        self.pos += 4
        return result

    def read_asset_parameter(self):
        """StoryAssetParameterYamlData"""
        mc = self.read_nullable_object_header()
        if mc is None:
            return None
        result = {}
        result["Position"] = self.read_int32()
        result["Type"] = self.read_int32()
        return result

    def read_asset_parameter_array(self):
        count = self.read_int32()
        if count == -1:
            return None
        return [self.read_asset_parameter() for _ in range(count)]

    def read_scene(self):
        """Read StorySceneYamlData (21 MemoryPackInclude fields)."""
        mc = self.read_nullable_object_header()
        if mc is None:
            return None

        scene = {}
        fields = [
            ("SceneId", lambda: self.read_int32()),
            ("Speakers", lambda: self.read_string_array()),
            ("Text", lambda: self.read_string()),
            ("MessageWindowType", lambda: self.read_int32()),
            ("MessageTextSize", lambda: self.read_int32()),
            ("TextAnimation", lambda: self.read_story_text_animation()),
            ("ForceShowAllText", lambda: self.read_bool()),
            ("Background", lambda: self.read_string()),
            ("Timezone", lambda: self.read_int32()),
            ("BackgroundEffectParameter", lambda: self.read_background_effect_parameter()),
            ("BackgroundMusic", lambda: self.read_background_music()),
            ("SoundEffects", lambda: self.read_sound_effect_array()),
            ("Effect", lambda: self.read_int32()),
            ("EffectParameter", lambda: self.read_effect_parameter()),
            ("DisableWipe", lambda: self.read_bool()),
            ("Left", lambda: self.read_story_scene_character()),
            ("Center", lambda: self.read_story_scene_character()),
            ("Right", lambda: self.read_story_scene_character()),
            ("AssetsKeys", lambda: self.read_string_array()),
            ("AssetParameters", lambda: self.read_asset_parameter_array()),
            ("WaitTarget", lambda: self.read_string()),
        ]
        for name, reader in fields:
            before = self.pos
            try:
                scene[name] = reader()
            except Exception as e:
                print(f"  ERROR reading field '{name}' at pos {before}: {e}")
                print(f"  Context: {self.data[before:before+32].hex()}")
                raise
        return scene

    def read_story_yaml_data(self):
        """Read StoryYamlData: Id + Dictionary<int, StorySceneYamlData>"""
        mc = self.read_nullable_object_header()
        if mc is None:
            return None

        story = {}
        story["Id"] = self.read_int32()

        # Dictionary<int, StorySceneYamlData>
        dict_count = self.read_int32()
        scenes = {}
        for _ in range(dict_count):
            key = self.read_int32()
            value = self.read_scene()
            scenes[key] = value

        story["Scenes"] = scenes
        return story


# Test on saved sample
with open(r"D:\cs\workshop\sample_StoryMasterData_StoryYamlData_966.bin", "rb") as f:
    encrypted = f.read()

plaintext = decrypt(encrypted)
print(f"Decrypted {len(plaintext)} bytes")

reader = MemoryPackReader(plaintext)
try:
    story = reader.read_story_yaml_data()
    print(f"\nStory ID: {story['Id']}")
    print(f"Scene count: {len(story['Scenes'])}")

    for key in sorted(story['Scenes'].keys()):
        scene = story['Scenes'][key]
        speakers = scene.get('Speakers', [])
        text = scene.get('Text', '')
        if text:
            speaker_str = ', '.join(s for s in (speakers or []) if s)
            print(f"\n  Scene {key}: [{speaker_str}]")
            print(f"    {text[:200]}")

    print(f"\nParsed up to offset {reader.pos} / {len(plaintext)}")

except Exception as e:
    import traceback
    print(f"\nError at offset {reader.pos}: {e}")
    traceback.print_exc()
    # Show context around error
    start = max(0, reader.pos - 8)
    end = min(len(plaintext), reader.pos + 32)
    print(f"Context: ...{plaintext[start:end].hex()}...")
