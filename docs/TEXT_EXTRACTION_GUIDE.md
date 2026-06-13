# Merc Storia — Text Decrypt / Extract / Repack Guide

How story dialogue is stored, how to decrypt and parse the `MemoryPack` payload, how to mutate strings, and how to repack into a UnityFS bundle the game accepts.

Game environment: see [`README.md`](../README.md#game-environment-canonical). CRC patch ([`CRC_PATCH_GUIDE.md`](CRC_PATCH_GUIDE.md)) must be applied first or modified bundles silently revert.

## Where the text lives

| Path under `AssetBundle/StandaloneWindows64/` | Content |
|---|---|
| `StoryMasterData/<hash>.bundle` | Story dialogue. ~4,008 bundles, one per scenario. Each = one TextAsset = encrypted `StoryYamlData`. |
| `Story/<hash>.bundle` | Per-story metadata (BGM keys, asset refs). Less interesting for translation. |
| `MasterData/<hash>.bundle` | Game-wide master data: chapter names, story titles, item / character / UI strings. Same encryption, different MemoryPack schemas. |

Japanese strings also live baked into MonoBehaviour blobs across the bundle set — `jp_monobehaviours.txt` inventories all 126. Out of scope here: they need per-class typetree work.

## Encryption

```
plaintext  = MemoryPack(StoryYamlData)
iv         = os.urandom(16)
key        = PBKDF2_HMAC_SHA256(password="2147483647",
                                salt="-2147483648",
                                iterations=1024, length=32)
ciphertext = iv || AES_CBC_PKCS7(key, iv, plaintext)
```

Password and salt are **string literals** (found in `dump.cs` by searching `2147483647` / `-2147483648` — stored as ASCII constants, not boxed ints). 1024 iterations (intentionally weak, client-side). One global key — verified across 4,000 bundles.

Reference Python:

```python
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes, padding as sym_padding
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                 salt=b"-2147483648", iterations=1024)
AES_KEY = kdf.derive(b"2147483647")

def decrypt(data):
    iv, ct = data[:16], data[16:]
    pt = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv)).decryptor().update(ct) + b""
    pad = pt[-1]
    return pt[:-pad] if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]) else pt

def encrypt(pt):
    iv = os.urandom(16)
    padded = sym_padding.PKCS7(128).padder().update(pt) + ...
    return iv + Cipher(algorithms.AES(AES_KEY), modes.CBC(iv)).encryptor().update(padded) + ...
```

IV regenerates on every repack — the game doesn't check IV stability, only ciphertext-length stability matters at the bundle level.

## MemoryPack format

`MemoryPack` is a Cysharp schema-driven binary serializer. Source generators bake one binary layout per `[MemoryPackable] class`. No embedded schema — you must know each class's field order.

### Primitive wire formats

| Type | Wire format | Notes |
|---|---|---|
| `byte` / `bool` | 1 byte | bool: 0 or 1 |
| `int32` / `float32` | 4 bytes LE | |
| `string` (UTF-8 mode) | `int32 header` + payload | see below |
| `T[]` / `List<T>` | `int32 count` (`-1` = null) + elements | |
| `class` / `struct` | `byte memberCount` (`0xFF` = null) + fields | useful as a null sentinel and version key |
| `Dictionary<K,V>` | `int32 count` + `(K, V)` pairs | |

### String encoding (UTF-8 mode) — the critical gotcha

```
int32 header:
   = -1             → null string
   =  0             → empty ""
   = ~byteCount     → followed by: int32 charCount, byte[byteCount] (UTF-8)
                      (~ is BITWISE NOT, not negation: ~10 = -11)
```

The header is the **bitwise NOT** of the UTF-8 byte count, not negation. Confusing `-byteCount` with `~byteCount` corrupts the very next field and the entire rest of the buffer.

`char_count` = .NET `string.Length` = UTF-16 code units. Equal to `len(s)` in Python for BMP text (Japanese, Chinese, basic Latin). Outside BMP: `len(s.encode('utf-16-le')) // 2`.

Reader / writer:

```python
def read_string(data, pos):
    raw = struct.unpack_from('<i', data, pos)[0]; pos += 4
    if raw == -1: return None, pos
    if raw == 0:  return "",   pos
    byte_count = ~raw
    char_count = struct.unpack_from('<i', data, pos)[0]; pos += 4
    return data[pos:pos+byte_count].decode('utf-8'), pos + byte_count

def write_string(s):
    if s is None: return struct.pack('<i', -1)
    if s == "":   return struct.pack('<i', 0)
    enc = s.encode('utf-8')
    return struct.pack('<i', ~len(enc)) + struct.pack('<i', len(s)) + enc
```

### `StoryYamlData` schema

From `dump.cs` cross-referenced with the `MemoryPack`-generated `Serialize` method. Summary:

```
StoryYamlData {                              // memberCount = 2
    int32  Id
    Dictionary<int32, StorySceneYamlData> Scenes
}

StorySceneYamlData {                         // memberCount = 21
    int32   SceneId
    string[] Speakers
    string  Text
    int32   MessageWindowType
    int32   MessageTextSize
    StoryTextAnimationYamlData { int32 Type, int32 Speed }
    bool    ForceShowAllText
    string  Background
    int32   Timezone
    StoryBackgroundEffectParameterYamlData { int32 Type, string Parameter }
    StoryBackgroundMusicYamlData { string Key, float Volume }
    StorySoundEffectYamlData[] { string Key, float Volume, bool Loop }
    int32   Effect
    StoryEffectParameterYamlData { string Color, float Duration }
    bool    DisableWipe
    StorySceneCharacterYamlData Left / Center / Right { int32 Type, int32 Id, int32 FaceType }
    string[] AssetsKeys
    StoryAssetParameterYamlData[] { int32 Position, int32 Type }
    string  WaitTarget
}
```

For dialogue, only `Speakers` (string[]) and `Text` (string) matter. Everything else is per-scene metadata.

Implemented in `merc_decrypt.py` as the `Reader` class. Subobjects use `obj_header()` to peel the `memberCount` byte (`0xFF` = null).

### Heuristic scanning (no schema needed)

Full parsing is fragile across game patches (any field's nullability can shift). `extract_dialogue.py` uses a heuristic:

1. Walk buffer 1 byte at a time looking for `int32 key (0..10000)` followed by `byte 0x15` (= 21, `StorySceneYamlData.memberCount`).
2. At each match: try parse `int32 SceneId` → `string[] Speakers` (count 0..10) → `string Text`.
3. If any string read fails or `byteCount` is wildly out of range, treat as false positive and resume.

Recovers 100% of dialogue with negligible false-positive risk — `0x15` is a strong anchor and the speakers/text arrays self-validate via UTF-8 decode.

## The UnityFS wrapper

Each story bundle = one `UnityFS` archive with one `TextAsset`. Raw `TextAsset` on disk:

```
int32  nameLength
byte[] name (UTF-8)
pad to 4-byte boundary
int32  scriptLength
byte[] script (encrypted: iv || AES_CBC ciphertext)
```

UnityPy reads this transparently. `obj.read()` returns a `TextAsset` with `.script` = raw ciphertext. For repack: set `.script` to new ciphertext (same or different length is fine), `text_asset.save()`, `env.file.save(packer="lz4")`.

Length stability is the simplest path: AES-CBC ciphertext = `iv (16) + ceil((plaintext + 1) / 16) * 16` bytes. If the new plaintext fits the same number of 16-byte blocks, the bundle on disk has identical length and offsets → no downstream fixups.

## End-to-end pipeline

**A. Extract → JSON.** `merc_storia_toolkit.py extract` walks every `StoryMasterData/`, decrypts, runs the heuristic scanner, writes one JSON per story. `extract_metadata_full.py` produces `(chapter_id, story_id, title, episode_name)` tuples from `MasterData/`. Merge for a fully labelled translation file. ~4,000 stories, ~120 k dialogue lines, ~25 MB JSON, ~30 s.

**B. Translate.** Out of scope here. Walk the JSONs, send each `Text` (and each speaker) to an LLM with surrounding scene context for consistency, write back. Harvest character-name glossary from `dump.cs`.

**C. Repack.** For each story:

1. Load bundle, extract TextAsset raw bytes, decrypt.
2. Locate every (`Speakers[*]`, `Text`) using the heuristic scanner.
3. Build an in-memory diff: list of `(start_offset, end_offset, new_string)` triples sorted by offset.
4. Rebuild plaintext = concat `prev_end → start` runs of original bytes with new MemoryPack-encoded strings.
5. Re-encrypt with fresh random IV.
6. `text_asset.script = new_ciphertext` → `text_asset.save()` → `env.file.save(packer="lz4")`.

The trick is steps 4–5: **do not** parse and re-serialize the full MemoryPack tree. We only mutate strings in place — everything else is byte-for-byte preserved, so per-scene metadata layouts can change between game patches without breaking the repacker.

Speaker names appear in **two** places: scene `Speakers: string[]` AND `StorySceneCharacterYamlData.Key` / `DisplayName` for on-stage characters. The toolkit's per-story JSON layout exposes both — edit the JSON and both update on repack. For a worked example of the byte-level global string-replace technique (kept separately because it doesn't need extract/repack), see [`translate_1621.py`](../translate_1621.py).

**D. Round-trip verify.** `verify_repack.py` decrypts a repacked bundle, re-runs the heuristic scanner, asserts translations round-trip. `repack.py` has a built-in self-test on a single sample bundle (`eb777f...`, story 1621).

## Gotchas (in order of how easily each will bite you)

- **`~byteCount`, not `-byteCount`.** Off by one ⇒ corrupted buffer from that point on.
- **`char_count` = UTF-16 units, not UTF-8 bytes and not codepoints.** Doesn't bite on BMP text.
- **PBKDF2 password / salt are string literals**, not boxed ints. The salt looks negative because someone copy-pasted `int.MinValue.ToString()`.
- **Empty `Speakers` is legit** — narrator lines have `Speakers: []` + non-null `Text`. Accept `count = 0`.
- **TextAsset name has 4-byte padding** — UnityPy handles transparently via `obj.read()`; don't read raw bytes.
- **AES-CBC IV = 16 fresh random bytes.** Reusing the original is technically valid but trips leak-detection telemetry (no server response, but visible in ProcMon). `os.urandom(16)`.
- **`env.file.save(packer="lz4")`, not `"lz4hc"`.** UnityPy's bundled lz4hc packer mis-handles the block-info trailer on Unity 6000.x.
- **Some bundles have multiple TextAssets** (especially `MasterData/`). Iterate all `obj.type.name == "TextAsset"`, not just the first.
- **Heuristic scanning beats full parsing.** Anchor on `(int32 key 0..10000) + byte 0x15`, validate per-field self-consistency. Fast, robust, patch-tolerant.

## What did NOT work

- **MessagePack instead of MemoryPack.** Leading byte (`0x02`) of `StoryYamlData` is also a valid MessagePack `positive fixint 2` — long detour. Cysharp's MemoryPack is the actual serializer.
- **Full schema-driven (de)serializer.** Written and abandoned: every game update can shift nullability or type. The heuristic in-place mutator is patch-tolerant. Full parser kept in `merc_decrypt.py` for reference / debugging only.
- **Replacing strings without re-encoding the length prefix.** Naïve byte-level find/replace corrupts the length header. Always re-emit `(int32 ~byteCount, int32 charCount, utf8…)`.
- **Hoping the AES key was per-bundle.** It's a single global constant.

## File reference

| Path | Purpose |
|---|---|
| `merc_storia_toolkit.py` | Unified CLI: `extract` / `extract-story` / `extract-misc` / `repack` / `repack-story` / `repack-misc` / `test-repack` |
| `merc_decrypt.py` | Reference: decrypt + full MemoryPack `Reader` for `StoryYamlData` |
| `translate_1621.py` | Worked example: story 1621 → Chinese |
| `deploy_bundles.py` | Copy `repacked_bundles/` onto the live cache (auto-prefers game folder over LocalLow) |
| `bundle_cache.py` | Bundle the LocalLow CDN cache into the game folder for shipping |
| `jp_monobehaviours.txt` | Inventory of all MonoBehaviours containing JP strings |

## External references

- [Cysharp/MemoryPack](https://github.com/Cysharp/MemoryPack) — the binary serializer the game's plaintext uses; the UTF-8-mode string layout is documented in its README
- [UnityPy](https://github.com/K0lb3/UnityPy) — bundle read/write library
- [pyca/cryptography](https://cryptography.io/) — AES-256-CBC + PBKDF2 used to derive the AES key
- [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) — used to confirm the AES password/salt constants in `RijndaelManaged` calls

After this, run [`FONT_REPLACEMENT_GUIDE.md`](FONT_REPLACEMENT_GUIDE.md) to swap the bundled Japanese font for one with your target script.
