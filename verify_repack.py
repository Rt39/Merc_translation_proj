"""Verify the repacked bundle contains translated text."""
import sys, io, struct, os
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


def read_string(data, pos):
    if pos + 4 > len(data):
        return None, pos
    raw = struct.unpack_from('<i', data, pos)[0]; pos += 4
    if raw == -1: return None, pos
    if raw == 0: return "", pos
    bc = ~raw
    if bc > 0 and bc < 100000 and pos + 4 + bc <= len(data):
        cc = struct.unpack_from('<i', data, pos)[0]; pos += 4
        try:
            s = data[pos:pos+bc].decode('utf-8'); pos += bc
            return s, pos
        except: return None, pos
    return None, pos


def extract_dialogue(data):
    pos = 0; tag = data[pos]; pos += 1
    if tag != 2: return None
    sid = struct.unpack_from('<i', data, pos)[0]; pos += 4
    dc = struct.unpack_from('<i', data, pos)[0]; pos += 4
    result = []
    i = 9
    while i < len(data) - 5:
        if i + 5 <= len(data):
            key = struct.unpack_from('<i', data, i)[0]; st = data[i + 4]
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
                        result.append({"speakers": speakers, "text": text})
                i += 5; continue
        i += 1
    return result


# Read repacked bundle
bundle_path = r"D:\cs\workshop\test_repacked.bundle"
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
        enc = raw[pos:pos+script_len]
        pt = decrypt(enc)

        # Check for English text
        if b'greatness' in pt:
            print("SUCCESS: Found translated English text in repacked bundle!")
        else:
            print("WARNING: No English text found")

        dialogue = extract_dialogue(pt)
        print(f"\nAll dialogue from repacked bundle:")
        for d in dialogue:
            sp = ', '.join(s for s in d['speakers'] if s) or '(narrator)'
            text = d['text'] or ''
            if text:
                marker = ""
                if 'greatness' in text or 'Shizumeki' in sp:
                    marker = " <<<< TRANSLATED"
                print(f"[{sp}] {text[:200]}{marker}")
