# Merc Storia (メルストM) — Text Decrypt / Extract / Repack Guide

End-to-end documentation of how the game stores story text, how to decrypt it, how to parse the `MemoryPack` binary format, how to mutate strings safely, and how to repack everything back into a UnityFS bundle that the game accepts.

## Game environment

- **Engine**: Unity 6000.0.58f2, IL2CPP, Windows x64 (Steam)
- **Game folder**: `<Steam>/steamapps/common/メルクストーリア - 癒術士と心の旋律 -/`
- **Asset format**: Addressables 2.3.7, UnityFS LZ4HC, every text payload wrapped in a `TextAsset`
- **Encryption**: AES-256-CBC, PKCS#7 padding, key derived once via PBKDF2
- **Serialization (plaintext)**: MemoryPack (UTF-8 mode), schema-driven
- **CRC checks**: already neutered in `GameAssembly.dll` (see `CRC_PATCH_GUIDE.md`). Without that patch, modified text bundles silently revert to CDN copies.

## Where the text lives

| Path (relative to LocalLow `AssetBundle/StandaloneWindows64/`) | Content |
|---|---|
| `StoryMasterData/<hash>.bundle` | Story dialogue. ~4,008 bundles, one per story scenario. Each contains one TextAsset = encrypted `StoryYamlData`. |
| `Story/<hash>.bundle` | Per-story metadata (BGM keys, asset refs). Less interesting for translation. |
| `MasterData/<hash>.bundle` | Game-wide master data: chapter names, story titles, item names, character names, UI strings. Same encryption, different MemoryPack schemas. |

There are also Japanese strings baked into `MonoBehaviour` blobs across the bundle set — `jp_monobehaviours.txt` is an inventory of all 126 of them. Those are out of scope for the dialogue pipeline because they live in arbitrary user-defined schemas inside the IL2CPP runtime; translating them requires per-class typetree work.

## The encryption

```
plaintext  = MemoryPack(StoryYamlData)
iv         = os.urandom(16)
key        = PBKDF2_HMAC_SHA256(password="2147483647",
                                 salt="-2147483648",
                                 iterations=1024,
                                 length=32)
ciphertext = iv || AES_CBC_PKCS7(key, iv, plaintext)
```

The password and salt are **string literals**, not derived — they came straight from the IL2CPP dump (search `dump.cs` for `2147483647` / `-2147483648`). Iterations 1024 (intentionally weak, designed for client-side use). Key length is 32 bytes (AES-256).

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
    dec = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv)).decryptor()
    pt = dec.update(ct) + dec.finalize()
    pad = pt[-1]
    if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
        return pt[:-pad]
    return pt

def encrypt(pt):
    iv = os.urandom(16)
    padded = sym_padding.PKCS7(128).padder().update(pt) + b"..."
    enc = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv)).encryptor()
    return iv + enc.update(padded) + enc.finalize()
```

The IV is regenerated on every repack — the game does not check IV stability; ciphertext-length stability is all that matters at the bundle level.

## The MemoryPack format

MemoryPack is a `MessagePack`-style schema-driven binary serializer from Cysharp. The game compiles in `MemoryPack.Generator` source generators that produce one binary layout per `[MemoryPackable] class`. There is no embedded schema — to parse, you must know (or reverse-engineer) the field order of each class.

### Primitive encoding

| Type | Wire format | Notes |
|---|---|---|
| `byte` / `bool` | 1 byte | bool: 0 or 1 |
| `int32` | 4 bytes LE | |
| `float32` | 4 bytes LE | |
| `string` (UTF-8 mode) | `int32 header` + payload | See below |
| `T[]` / `List<T>` | `int32 count` (`-1` = null) + elements | |
| `class` / `struct` | `byte memberCount` (`0xFF` = null) + fields | memberCount is per-type-version; useful only as a null sentinel and as the version key |
| `Dictionary<K,V>` | `int32 count` + `(K, V)` pairs | |

### String encoding (UTF-8 mode)

This is the most subtle bit and the only thing you need to handle to mutate text:

```
int32 header:
   = -1                 → null string
   =  0                 → empty string ""
   = ~byteCount (i.e. negative non-(-1))
                        → followed by:
                          int32 charCount
                          byte[byteCount] (UTF-8)
```

The header is the **bitwise NOT** of the UTF-8 byte count, NOT the negation. `~10 = -11`, not `-10`. Confusing the two corrupts the very next field. Reader:

```python
def read_string(data, pos):
    raw = struct.unpack_from('<i', data, pos)[0]; pos += 4
    if raw == -1: return None, pos
    if raw == 0:  return "",   pos
    byte_count = ~raw
    char_count = struct.unpack_from('<i', data, pos)[0]; pos += 4
    s = data[pos:pos+byte_count].decode('utf-8')
    return s, pos + byte_count
```

Writer:

```python
def write_string(s):
    if s is None: return struct.pack('<i', -1)
    if s == "":   return struct.pack('<i', 0)
    enc = s.encode('utf-8')
    return struct.pack('<i', ~len(enc)) + struct.pack('<i', len(s)) + enc
```

`char_count` is the .NET `string.Length`, i.e. UTF-16 code units. For BMP-only text (Japanese, Chinese, basic Latin) this equals `len(s)` in Python. For text outside the BMP (rare emoji / CJK extension B), use `len(s.encode('utf-16-le')) // 2`.

### `StoryYamlData` layout (the dialogue payload)

Discovered by reading the IL2CPP `dump.cs` (search for `StoryYamlData`) cross-referenced with `MemoryPack`-generated `Serialize` methods. Summary:

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
    StoryTextAnimationYamlData TextAnimation { int32 Type, int32 Speed }
    bool    ForceShowAllText
    string  Background
    int32   Timezone
    StoryBackgroundEffectParameterYamlData BackgroundEffectParameter { int32 Type, string Parameter }
    StoryBackgroundMusicYamlData BackgroundMusic { string Key, float Volume }
    StorySoundEffectYamlData[] SoundEffects   { string Key, float Volume, bool Loop }
    int32   Effect
    StoryEffectParameterYamlData EffectParameter { string Color, float Duration }
    bool    DisableWipe
    StorySceneCharacterYamlData Left  { int32 Type, int32 Id, int32 FaceType }
    StorySceneCharacterYamlData Center
    StorySceneCharacterYamlData Right
    string[] AssetsKeys
    StoryAssetParameterYamlData[] AssetParameters { int32 Position, int32 Type }
    string  WaitTarget
}
```

Implemented in `merc_decrypt.py` as the `Reader` class. Subobjects use `obj_header()` to peel off the `memberCount` byte (`0xFF` means null).

For dialogue extraction you mostly only care about `Speakers` (string array) and `Text` (string). Everything else (sound, BGM, camera, asset overlay) is metadata.

### Heuristic dialogue extraction (no schema needed)

For ad-hoc spelunking, `extract_dialogue.py` scans for the MemoryPack scene pattern without parsing every field:

1. Walk the buffer 1 byte at a time looking for `int32 key (0..10000)` followed by `byte 0x15` (= 21, `StorySceneYamlData.memberCount`).
2. At every match, try to parse `int32 SceneId`, then `string[] Speakers` (count 0..10), then `string Text`.
3. If any string read fails or `byteCount` is wildly out of range, treat as false positive and resume scanning.

This recovers 100% of dialogue with negligible false-positive risk because the `0x15` byte is a strong anchor and the speakers/text arrays self-validate via UTF-8 decode.

## The UnityFS wrapper

Each story bundle is a single `UnityFS` archive containing one `TextAsset`. `TextAsset` raw bytes on disk:

```
int32  nameLength
byte[] name (UTF-8)
pad to 4-byte boundary
int32  scriptLength
byte[] script (the encrypted bytes — IV || AES_CBC ciphertext)
```

UnityPy reads this transparently. `obj.read()` returns a `TextAsset` with `.script` = the raw ciphertext bytes. For repack we set `.script` to new ciphertext bytes (same or different length is fine — UnityPy fixes up the SerializedFile object table) then `env.file.save()`.

Length stability is the easiest path: AES-CBC ciphertext is always `iv (16) + ceil((plaintext + 1) / 16) * 16` bytes. As long as the new plaintext fits the same number of 16-byte blocks as the original, the bundle on disk has identical length and structural offsets; no header fixups are needed downstream.

## End-to-end pipeline

### A. Extract everything → JSON

`extract_all.py` walks every file in `StoryMasterData/`, decrypts, runs the heuristic dialogue extractor, and writes one `dialogue.json` containing:

```json
{
  "stories": [
    {
      "bundle": "00153b18eb48299a131ee5437f794d79.bundle",
      "asset_name": "story_153",
      "id": 153,
      "scene_count": 12,
      "scenes": [
        { "scene_id": 0, "speakers": ["メル"], "text": "おはよう…" },
        ...
      ]
    },
    ...
  ],
  "errors": [...]
}
```

~4,000 stories, ~120k dialogue lines, ~25 MB JSON. Runs in ~30 seconds.

`extract_metadata_full.py` does the same for `MasterData/`:

- `StoryMasterData` bundle gives `(chapter_id, story_id, title, episode_name, scene_key, display_order)` tuples.
- `ChapterMasterData` bundle gives chapter names.

Merge those with the dialogue dump to produce a fully labelled translation file.

### B. Translate

Outside this repo's scope. The structure is mechanical: walk `dialogue.json`, send each `text` (and each speaker name) through an LLM with surrounding scene context for consistency, write back. We used DeepSeek + Claude with a glossary of recurring character names harvested from `dump.cs`.

### C. Repack → translated bundle

`merc_storia_toolkit.py repack <translated.json>` does, for each story:

1. Load original bundle, extract `TextAsset` raw bytes.
2. Decrypt to plaintext.
3. Locate every (Speakers[*], Text) string using the same heuristic scanner.
4. Build an in-memory diff: list of `(start_offset, end_offset, new_string)` triples sorted by offset.
5. Rebuild plaintext by concatenating `prev_end → start` runs of original bytes with new MemoryPack-encoded strings.
6. Re-encrypt with a fresh random IV (PKCS#7 padding).
7. Stuff back into the bundle via UnityPy (`text_asset.script = new_ciphertext; text_asset.save()`).
8. Save the bundle with `env.file.save(packer="lz4")`.

The trick is step 4–5: do **not** parse and re-serialize the full MemoryPack tree. Most fields (sound effects, camera, asset positions) have complex layouts and we do not need to touch them. We only mutate strings in place — everything else is byte-for-byte preserved.

Speaker names appear in **two** places in the same bundle:

- The `Speakers: string[]` field of every scene that has a line by that speaker.
- The `StorySceneCharacterYamlData.Key` / `DisplayName` fields if the character is on stage (Left/Center/Right slots).

`fix_speakers3.py` is a global pass that finds every MemoryPack-encoded string matching a translation key anywhere in the buffer and replaces it. Cheap and effective because MemoryPack strings carry their own length prefix — false positives are essentially impossible.

### D. Round-trip verification

`verify_repack.py` reads a repacked bundle, decrypts it, re-runs the heuristic dialogue extractor, and asserts the translated strings round-trip. `repack.py` has a built-in self-test that performs the full A→C→D loop on a single sample bundle (`eb777f2829400cfced05a3761d77fd6a.bundle`, story 1621 in our test scenario).

## Step-by-step from scratch

Prerequisites:

- CRC patch already applied (`CRC_PATCH_GUIDE.md`).
- LocalLow cache populated by launching the game once to download the bundles. They will appear under `%LOCALAPPDATA%\..\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64\`.
- `uv` available.

All deps (`UnityPy`, `cryptography`, `lz4`, `numpy`, `Pillow`, `capstone`) are declared in `pyproject.toml`; `uv run` resolves them automatically.

```bash
# 1. Sanity check on a single bundle
uv run merc_decrypt.py
#    Expected:
#      Story ID: 153
#      Scenes: 12
#        [メル] おはよう…
#        ...
#      Results: 19 success, 1 fail out of 20

# 2a. Extract everything into ONE big JSON (~25 MB)
uv run extract_all.py
#    → writes merc_storia_dialogue.json

# 2b. ...OR extract one JSON per story (preferred for translation pipelines)
uv run extract_all_separate.py
#    → writes stories/<story_id>.json (×4,008), stories/_index.json, stories/_errors.json

# 3. Extract chapter / story metadata
uv run extract_metadata_full.py
#    → writes merc_metadata.json

# 4. Translate the dialogue JSON(s) (out of scope; produce a parallel file)

# 5. Single-story sanity repack
uv run repack.py
#    → writes test_repacked.bundle, runs self-test

# 6. Full repack
uv run merc_storia_toolkit.py repack <translated.json>

# 7. Copy repacked bundles into the game's StreamingAssets
#    (the same hash filenames; overwrites the originals)

# 8. Launch the game. Translated text appears in story dialogue.
```

## Discoveries / gotchas

- **PBKDF2 password / salt are string literals.** Found in `dump.cs` by searching `2147483647` and `-2147483648`. They are stored as ASCII string constants, not boxed integers. The salt is also negative-looking (`"-2147483648"`) because someone copy-pasted `int.MinValue.ToString()`.
- **`~byteCount`, not `-byteCount`.** MemoryPack uses bitwise NOT to distinguish UTF-8 mode (negative non-(-1)) from UTF-16 mode (positive count). `~10 = -11`. Off-by-one here corrupts the next field length and the whole rest of the buffer.
- **char_count = UTF-16 code units, not UTF-8 bytes and not Unicode codepoints.** For BMP strings these are all equal so it never bites you on Japanese / Chinese / Latin text. Outside BMP, use `len(s.encode('utf-16-le')) // 2`.
- **Heuristic scanning beats full parsing.** The full `StorySceneYamlData` has 21 fields, several with nested objects, and the schema can shift between game patches. Anchoring on `(int32 key in [0,10000]) + byte 0x15` and re-validating per-field self-consistency is fast, robust, and patch-tolerant.
- **TextAsset alignment.** The 4-byte name padding in the raw `TextAsset` layout is easy to forget; UnityPy handles it transparently if you read via `obj.read()` instead of raw bytes.
- **AES-CBC IV must be 16 fresh random bytes.** Reusing the original IV on re-encryption produces valid ciphertext but trips some leak-detection telemetry the game ships with (no actual server response, but visible in ProcMon). `os.urandom(16)` is the safe choice.
- **`env.file.save(packer="lz4")` not `lz4hc`.** UnityPy's bundled `lz4hc` packer mis-handles the block-info trailer on Unity 6000.x. Plain `"lz4"` produces bundles the engine accepts.
- **Empty Speakers array is legit.** Narrator lines have `Speakers: []` and a non-null `Text`. Your scanner must accept `speakers_count = 0`.
- **Some bundles have multiple TextAssets.** `MasterData/` occasionally bundles two TextAssets (e.g. a config + a payload). Iterate all `obj.type.name == "TextAsset"` objects, not just the first one.

## What we tried that did NOT work

- **MessagePack instead of MemoryPack.** The leading byte (`0x02`) of `StoryYamlData` is also a valid MessagePack `positive fixint 2`, which led to a long detour. Cysharp's `MemoryPack` is what's actually being used; it just shares the marker byte by coincidence.
- **Full schema-driven (de)serializer.** We wrote one and abandoned it: every game update could shift a field's nullability or type, and the heuristic in-place mutator is patch-tolerant. The full parser is kept around in `merc_decrypt.py` for reference / debugging only.
- **Replacing strings with longer translations without re-encoding.** Naive byte-level `find/replace` on the decrypted buffer corrupts the length prefix. Always re-emit `(int32 ~byteCount, int32 charCount, utf8…)`.
- **Hoping the AES key was per-bundle.** It is one global constant. Verified across 4,000 bundles.

## File reference

| Path | Purpose |
|---|---|
| `merc_decrypt.py` | Reference implementation: decrypt + full MemoryPack `Reader` for `StoryYamlData` |
| `extract_dialogue.py` | Heuristic dialogue extractor (no schema needed) |
| `extract_all.py` | Batch-extract dialogue from all ~4,000 `StoryMasterData` bundles into one JSON |
| `extract_all_separate.py` | Same, but one JSON per story (`stories/<story_id>.json`) — preferred for per-story translation workflows |
| `extract_masterdata.py` | Dump every `MasterData` bundle's TextAssets |
| `extract_metadata_full.py` | Build `(chapter, story_id, title, episode)` tuples |
| `extract_story_metadata.py` | Lightweight version of the above |
| `repack.py` | Reference single-bundle repack with self-test |
| `merc_storia_toolkit.py` | Unified CLI: `extract`, `extract-meta`, `repack`, `test-repack` |
| `fix_speakers3.py` | Global string-replace for speaker names across all MemoryPack contexts |
| `translate_1621.py` | Worked example: translate story 1621 (one bundle) into Chinese |
| `verify_repack.py` | Decrypt a repacked bundle and visually confirm translated lines |
| `jp_monobehaviours.txt` | Inventory of all MonoBehaviours containing Japanese strings |
| `merc_metadata.json` | Extracted chapter / story metadata (output of `extract_metadata_full.py`) |

After this, run `FONT_REPLACEMENT_GUIDE.md` to swap the bundled Japanese font for one that includes your target script.
