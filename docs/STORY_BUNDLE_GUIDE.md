# Story Bundle Guide — Decrypt + MemoryPack Schema + Repack

> 中文版请戳[这里](STORY_BUNDLE_GUIDE_zh-CN.md)。

How story dialogue is stored, how to decrypt and parse the `MemoryPack`
payload byte-identically, and how to repack into a UnityFS bundle the game
accepts. Companion doc to
[`MASTERDATA_SCHEMA_GUIDE.md`](MASTERDATA_SCHEMA_GUIDE.md), which covers the
15 master-data records.

Game environment: see
[`README.md`](../README.md#game-environment-canonical). The CRC patch
([`CRC_PATCH_GUIDE.md`](CRC_PATCH_GUIDE.md)) must be applied first or
modified bundles silently revert.

## Where the text lives

| Path under `AssetBundle/StandaloneWindows64/` | Content |
|---|---|
| `StoryMasterData/<hash>.bundle` | Story dialogue. ~4,008 bundles, one per scenario. Each = one TextAsset = encrypted `StoryYamlData`. |
| `Story/<hash>.bundle` | Per-story metadata (BGM keys, asset refs). Less interesting for translation. |
| `MasterData/<hash>.bundle` | Game-wide master data: chapter names, story titles, character/UI strings. Same encryption, different schemas — see [`MASTERDATA_SCHEMA_GUIDE.md`](MASTERDATA_SCHEMA_GUIDE.md). |

## Encryption

```
plaintext  = MemoryPack(StoryYamlData)
iv         = os.urandom(16)
key        = PBKDF2_HMAC_SHA256(password="2147483647",
                                salt="-2147483648",
                                iterations=1024, length=32)
ciphertext = iv || AES_CBC_PKCS7(key, iv, plaintext)
```

Password and salt are **string literals** (`int.MinValue.ToString()` /
`int.MaxValue.ToString()` copy-pasted). 1024 iterations, intentionally weak
client-side. One global key — verified across 4,000 bundles. IV regenerates
on every repack; the game doesn't check IV stability.

The key derivation lives in [`mercstoria/config.py`](../mercstoria/config.py)
(`derive_aes_key`); the encrypt/decrypt helpers and full Reader/Writer live
in [`mercstoria/memorypack.py`](../mercstoria/memorypack.py).

## MemoryPack wire format

`MemoryPack` (Cysharp) is a schema-driven binary serializer. Source
generators bake one binary layout per `[MemoryPackable]` class, with no
embedded schema — you must know each class's field order.

### Primitive types

| Type | Wire format | Notes |
|---|---|---|
| `byte` / `bool` | 1 byte | bool: 0 or 1 |
| `int32` / `float32` | 4 bytes LE | |
| `int64` / `TimeSpan` | 8 bytes LE | TimeSpan = i64 ticks |
| `enum` | int32 | CompilerGenerated underlying type |
| `Vector2` / `Vector3` | 2 / 3 × f32 | unmanaged, packed |
| `string` (UTF-8 mode) | see below | |
| `T[]` / `List<T>` | `int32 count` (raw, NOT `~count`; `-1` = null) + elements | |
| `Dictionary<K,V>` | `int32 count` + `(K, V)` pairs | |
| `class` / `struct` | 1-byte member count (`0xFF` = null) + fields | see "object header" below |
| `Nullable<float>` | **8 bytes raw memory copy** | `byte hasValue + 3 padding + float value` — see below |

### String encoding (the critical gotcha)

```
int32 header:
   = -1             → null string
   =  0             → empty ""
   = ~byteCount     → followed by: int32 charCount, byte[byteCount] (UTF-8)
                      (~ is BITWISE NOT, not negation: ~10 = -11)
```

The header is the **bitwise NOT** of the UTF-8 byte count, not negation.
Confusing `-byteCount` with `~byteCount` corrupts the very next field and the
entire rest of the buffer.

`charCount` = .NET `string.Length` = UTF-16 code units. Equal to `len(s)` in
Python for BMP text (Japanese, Chinese, basic Latin). Outside BMP:
`len(s.encode('utf-16-le')) // 2`.

### Object header — truncation matters

1 byte member count. `0xFF` = null. `0..249` = real count. **Truncated
objects are common**: writers can emit `mc < declared_field_count`, in which
case only that many leading fields are present (the rest get default values
on read). The Reader must `min(mc, len(fields))` and stash the rest in
`_skipped`; the Writer mirrors with the same truncation.

### `Nullable<float>` is 8 bytes

`byte hasValue + 3 bytes padding + float value`. **NOT 5 bytes.** Padding is
always 0 in practice, but the value bits when null must be preserved (heap
garbage, not zeroed). The Reader returns
`{"_null": True, "_bits": "<hex>"}` for the null case so the Writer can emit
exact bytes back.

## `StoryYamlData` schema (from dump.cs)

| Type | mc | Fields |
|---|---|---|
| StoryYamlData | 2 | `int Id`, `Dictionary<int, StorySceneYamlData> Scenes` |
| StorySceneYamlData | 21 | full field list in `Reader.scene` |
| StoryTextAnimationYamlData | 5 | Type / Size(f32) / Interval / FadeInDuration / ForceWait |
| StorySceneCharacterYamlData | 11 | TextureId / FaceTextureId / Type / Key / DisplayName / Expression / Emotion / Active / Appearance / Offset(vec3) / Scale(vec3) |
| StoryCharacterAppearanceYamlData | 9 | Type + 6 × Nullable\<float\> + Duration(TimeSpan) + Active(bool) |
| StoryBackgroundMusicYamlData | 7 | Name / AssetType / AssetId / Mute / FadeIn / FadeOut / ForceFade |
| StorySoundEffectYamlData | 9 | |
| StoryEffectParameterYamlData | 14 | last field is CursorParameter (nested obj) |
| StoryCursorParameterYamlData | 7 | Type / Time / Position(vec2) / Direction / TouchPosition(vec2) / TouchScale(vec2) / Image |
| StoryBackgroundEffectParameterYamlData | 6 | Z(Nullable\<float\>) / AutoSkip / Blur / Bright / Sepia / Animation |
| StoryBlur/Bright/Sepia/AnimationBackgroundEffectParameterYamlData | 5 / 4 / 6 / 8 | |
| StoryAssetParameterYamlData | 11 | |

For dialogue, the only fields that matter are `Speakers` (string[]) and
`Text` (string) inside `StorySceneYamlData`. Everything else is per-scene
metadata that the Reader/Writer round-trip but the translator never edits.

The authoritative source is `il2cpp_output/dump.cs` (Il2CppDumper output).
Re-grep with regex `private\s+(?:readonly\s+)?...<\w+>k__BackingField` —
note `readonly` must be optional, since `StoryTextAnimationYamlData`'s
fields lack it.

## The UnityFS wrapper

Each story bundle = one `UnityFS` archive with one `TextAsset`. Raw
`TextAsset` on disk:

```
int32  nameLength
byte[] name (UTF-8)
pad to 4-byte boundary
int32  scriptLength
byte[] script (encrypted: iv || AES_CBC ciphertext)
```

UnityPy reads this transparently. `obj.read()` returns a `TextAsset` with
`.script` = raw ciphertext. For repack: set `.script` to the new ciphertext
(same or different length is fine), `text_asset.save()`,
`env.file.save(packer="lz4")`.

## End-to-end pipeline

**A. Extract → JSON.** `mercstoria extract` walks every
`StoryMasterData/`, decrypts, runs the full MemoryPack Reader (only bundles
whose round-trip is byte-identical land in the JSON output), and writes one
JSON per story labelled with chapter/episode metadata pulled from
`StoryMasterData` + `ChapterMasterData`. The same command also covers
all 15 master bundles via the same Reader/Writer
([`MASTERDATA_SCHEMA_GUIDE.md`](MASTERDATA_SCHEMA_GUIDE.md)) and the
inline UI text in `BundleAssets/`. ~4,000
stories, ~120 k dialogue lines, ~25 MB JSON, ~30 s.

**B. Translate.** Out of scope here. Walk the JSONs, send each `Text` and
each speaker to an LLM with surrounding scene context, write back. Harvest
the character-name glossary from `dump.cs`.

**C. Repack.** `mercstoria repack` re-serializes the edited JSON via
the full Writer, encrypts with a fresh IV, and writes a `UnityFS(lz4)`
bundle. Only bundles whose JSON has changed since the last repack are
re-emitted (fingerprint comparison). String length is unconstrained — the
plaintext is rewritten end-to-end, not spliced — so translated text can be
any size.

**D. Deploy.** `mercstoria deploy` copies `repacked_bundles/` into
`<game>/AssetBundle/StandaloneWindows64/`. Refuses if that tree is empty
— run `mercstoria bundle-cache` first. Each replaced original is mirrored, in the same relative layout, under a sibling
`AssetBundle_old/` tree. Roll back by copying `AssetBundle_old/` over
`AssetBundle/`; finalize by deleting `AssetBundle_old/`. The first copy of
each file is the pristine original — re-running deploy never overwrites
an existing backup.

## Inline UI text (Timeline cinematics)

A few `BundleAssets/<hash>.bundle` files contain MonoBehaviour objects
whose `parameter.text` field carries Japanese dialogue baked into Unity
Timeline — the final-chapter cinematic, the credit roll, etc. These do
NOT use AES + MemoryPack; they are plain Unity assets read via TypeTree.

`mercstoria extract` walks `BundleAssets/`, dumps any MonoBehaviour with
a JP `parameter.text` to `extracted_data/inline_ui/<hash>.json` (entries
are `{path_id, name, text}`). `mercstoria repack` writes the new text
back through TypeTree to `repacked_bundles/inline_ui/<hash>.bundle`.
Vanilla = 4 bundles, 44 strings.

## Final state (2026-06-17)

- **4008 / 4013 story bundles round-trip byte-identical** on the unmodified
  cache (99.88%). The 5 bundles that throw during reading are listed in
  `mercstoria/memorypack.py`; they touch a rare nested-type variant whose
  field layout we never tracked down. Not chased — 4008 was deemed enough.
- All 15 master-data bundles round-trip byte-identical
  ([`MASTERDATA_SCHEMA_GUIDE.md`](MASTERDATA_SCHEMA_GUIDE.md)).
- `mercstoria check-roundtrip [N]` re-verifies the Reader/Writer on N
  bundles (omit N for the entire cache).

## Gotchas (in order of how easily each will bite you)

- **`~byteCount`, not `-byteCount`.** Off by one ⇒ corrupted buffer from
  that point on.
- **`charCount` = UTF-16 units**, not UTF-8 bytes and not codepoints.
  Doesn't bite on BMP text.
- **PBKDF2 password / salt are string literals**, not boxed ints. Salt
  looks negative because someone copy-pasted `int.MinValue.ToString()`.
- **`Nullable<float>` is 8 bytes** (not 5) and the value bits when null
  must be preserved.
- **Truncated objects are common.** `min(mc, declared_fields)` on read,
  same `mc` on write.
- **Empty `Speakers` is legit** — narrator lines have `Speakers: []` +
  non-null `Text`. Accept `count = 0`.
- **The 2241 BGM mute issue is a vanilla data bug** — the oversized
  `Mute` at the start of `StoryYamlData_2241` can break BGM after skip;
  it is not introduced by the translation patch.
- **TextAsset name has 4-byte padding** — UnityPy handles it via
  `obj.read()`; don't read raw bytes.
- **`env.file.save(packer="lz4")`, not `"lz4hc"`.** UnityPy's bundled
  lz4hc packer mis-handles the block-info trailer on Unity 6000.x.
- **Some bundles have multiple TextAssets** (especially `MasterData/`).
  Iterate all `obj.type.name == "TextAsset"`, not just the first.

## File reference

| Path | Purpose |
|---|---|
| `mercstoria/memorypack.py` | AES decrypt + full MemoryPack Reader/Writer for `StoryYamlData` and 15 master records |
| `scripts/extract_repack.py` | `mercstoria <subcmd>`: `extract` / `repack` / `test-repack` |
| `scripts/extract_ui.py` | inline UI text helpers — called by `mercstoria extract` / `repack` after the AES + MemoryPack pipeline |
| `scripts/check_roundtrip.py` | `mercstoria check-roundtrip [N]` — sanity-check Reader/Writer on N story bundles |
| `scripts/deploy.py` | `mercstoria deploy` — copy `repacked_bundles/` onto the live cache |
| `scripts/bundle_cache.py` | `mercstoria bundle-cache` — bundle the LocalLow CDN cache into the game folder for shipping |
| `il2cpp_output/dump.cs` | Authoritative schema source |

## External references

- [Cysharp/MemoryPack](https://github.com/Cysharp/MemoryPack) — the binary
  serializer; UTF-8-mode string layout is documented in its README
- [UnityPy](https://github.com/K0lb3/UnityPy) — bundle read/write library
- [pyca/cryptography](https://cryptography.io/) — AES-256-CBC + PBKDF2
- [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) — used
  to confirm the AES password/salt constants in `RijndaelManaged` calls

After this, run [`FONT_REPLACEMENT_GUIDE.md`](FONT_REPLACEMENT_GUIDE.md) to
swap the bundled Japanese font for one with your target script.
