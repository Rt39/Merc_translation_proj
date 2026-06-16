# MemoryPack Schema Guide — Merc Storia Story Bundles

> 中文版请戳[这里](MEMORYPACK_SCHEMA_GUIDE_zh-CN.md)。

Reverse-engineered MemoryPack schema for Merc Storia `StoryYamlData` and the
Reader/Writer rewrite that enables byte-identical round-trip — the foundation
needed to add/remove dialogue chunks safely (rather than only in-place string
replacement).

## Why this exists

The legacy `find_story_strings` + splice path (still callable from
`scripts/extract_repack.py` for unregistered bundles) only does in-place
string replacement; it cannot grow or shrink the scene list. To insert/delete
chunks you need a real serializer.

`mercstoria/memorypack.py` (the rewritten library) makes
`serialize_story(read_story_bundle(...)) == decrypted_plaintext`
byte-for-byte. Once that holds, JSON ↔ bytes is fully invertible and a
translator can insert or delete entries freely.

The original `merc_decrypt.py` schema was guessed and missing fields. Round-trip
on real bundles failed instantly (50/50 mismatched). The fix was to use
`dump.cs` (Il2CppDumper output at `il2cpp_output/dump.cs`) as the authoritative
source and match every `[MemoryPackable]` class field-for-field, in declaration
order. Master-data records use a similar pattern but follow the
`[MemoryPackConstructor]` *parameter* order — see
[`MASTERDATA_SCHEMA_GUIDE_zh-CN.md`](MASTERDATA_SCHEMA_GUIDE_zh-CN.md).

## Authoritative schema (from dump.cs, with member counts)

| Type | mc | Notes |
|---|---|---|
| StoryYamlData | 2 | `int Id`, `Dictionary<int,StorySceneYamlData> table` (= int len + (key, scene)*) |
| StorySceneYamlData | 21 | full field list in `Reader.scene` |
| StoryTextAnimationYamlData | **5** | NOT 0 — Type/Size(f32)/Interval/FadeInDuration/ForceWait. Fields are non-readonly so a `private readonly` regex misses them |
| StorySceneCharacterYamlData | 11 | TextureId/FaceTextureId/Type/Key/DisplayName/Expression/Emotion/Active/Appearance/Offset(vec3)/Scale(vec3) |
| StoryCharacterAppearanceYamlData | 9 | Type + 6 Nullable\<float\> + Duration(TimeSpan) + Active(bool) |
| StoryBackgroundMusicYamlData | 7 | Name/AssetType/AssetId/Mute/FadeIn/FadeOut/ForceFade |
| StorySoundEffectYamlData | 9 | |
| StoryEffectParameterYamlData | 14 | last field is CursorParameter (nested obj) |
| StoryCursorParameterYamlData | 7 | Type/Time/Position(vec2)/Direction/TouchPosition(vec2)/TouchScale(vec2)/Image |
| StoryBackgroundEffectParameterYamlData | 6 | Z(Nullable\<float\>)/AutoSkip/Blur/Bright/Sepia/Animation |
| StoryBlur/Bright/Sepia/AnimationBackgroundEffectParameterYamlData | 5/4/6/8 | |
| StoryAssetParameterYamlData | 11 | |

## Critical wire-format facts

1. **Object header**: 1 byte member_count. `0xFF` = null. `0..249` = real count.
   **Truncated objects are common**: writers can emit
   `mc < declared_field_count`, in which case only that many leading fields are
   present (the rest get default values on read). Reader must
   `min(mc, len(fields))` and stash the rest in `_skipped`. Writer mirrors
   with the same truncation.

2. **Collections (`int length, [values]`)**: raw int32, NOT `~count`.
   `-1 = null`. Differs from strings.

3. **Strings**: `int32 ~utf8_byte_count, int32 utf16_len, utf8_bytes`.
   `-1 = null`, `0 = ""`.

4. **`Nullable<float>` is unmanaged → 8 bytes raw memory copy**:
   `byte hasValue + 3 bytes padding + float value`. NOT 5 bytes. Padding
   always 0 in practice but the value-when-null bits must be preserved
   (heap garbage). Reader returns `{"_null": True, "_bits": "<hex>"}` for
   the null case so Writer can emit exact bytes back.

5. **TimeSpan**: 8 bytes i64 ticks (unmanaged struct).
6. **Vector2/3**: raw f32 fields back-to-back (unmanaged).
7. **enums**: raw int32 (CompilerGenerated underlying type).

## Final state (2026-06-16)

- `mercstoria/memorypack.py` (was `merc_decrypt.py`) ships with full schema +
  truncation support + Nullable\<float\> 8-byte fix + text_anim 5-field fix.
  **4008/4013 byte-identical round-trip on the full bundle set (99.88%)**. The
  5 failing bundles all throw exceptions during reading (not silent diffs),
  meaning some rare nested-type variant still has a wrong field somewhere —
  but 4008 was accepted as good enough. Don't chase the last 5 unless a new
  requirement demands it.
- `scripts/extract_repack.py` (was `merc_storia_toolkit.py`) wires the full
  schema into the toolkit's `extract-story` / `repack-story` paths, plus
  `FULL_SCHEMA_MASTER` for all 15 master bundles. Chunk add/delete works
  end-to-end on the 4008 round-trippable story bundles.

The 5 failing bundles, for future reference:

- `099e9af067d5eb9d4ac372e4d05a34d1.bundle`
- `63262fa012bb617f9246e131050ed3cc.bundle`
- `7b5f2448d85d1cf86407c0cafd9611cc.bundle`
- `81ddf1d14b7f783411f264e6555539bf.bundle`
- `fec90c149e7b4b6659cb3c7e2dd6362e.bundle`

## Files

- `mercstoria/memorypack.py` — full Reader/Writer, `serialize_story`,
  `process_story_bundle`. Round-trip CLI:
  `uv run -m mercstoria check-roundtrip [N]` (N = how many bundles to scan;
  omit for the entire cache).
- `scripts/extract_repack.py` — `extract-story` / `repack-story` use the
  Reader/Writer above; `FULL_SCHEMA_MASTER` adds the same treatment to all
  15 master bundles.
- `il2cpp_output/dump.cs` — authoritative schema source. Re-grep with
  regex `private\s+(?:readonly\s+)?...<\w+>k__BackingField` (the
  `readonly` is optional — `TextAnimation` fields lack it).
- [`MASTERDATA_SCHEMA_GUIDE_zh-CN.md`](MASTERDATA_SCHEMA_GUIDE_zh-CN.md) —
  sister doc covering the master-data schemas (15 record types).
