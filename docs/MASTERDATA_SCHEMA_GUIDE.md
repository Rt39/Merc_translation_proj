# MasterData MemoryPack Schema Guide — All 15 MasterData Types

> 中文版请戳[这里](MASTERDATA_SCHEMA_GUIDE_zh-CN.md)。

Full reverse engineering of every MasterData bundle's MemoryPack schema, with
a Reader/Writer pair that round-trips byte-identically. With this in place,
string replacement is no longer constrained to the original encoded length —
translated text can be any size, not just `≤` the original Japanese.

## Why this exists

The legacy misc path scanned the plaintext with `find_all_strings` and spliced
new strings in by byte offset. The fatal flaw: **the new string's encoded
length must match the original**, otherwise every following offset shifts and
the data corrupts. Japanese → Chinese rarely matches in length, so even
swapping `「合戦」→「大战」` required surgical byte counting.

Decoding all 15 MasterData bundles via their full schema converts every
record into a structured object. On serialization the plaintext is rewritten
end-to-end, so individual string lengths can change freely. The Reader/Writer
style mirrors the story path (in `mercstoria/memorypack.py`), reusing the
same `_mc` (member count) header, truncated-object handling, and
8-byte-raw-memory `Nullable<float>` convention.

The `FULL_SCHEMA_MASTER` dispatch table now covers all 15 bundles. The legacy
offset-splice path is still in the codebase as a fallback for any
not-yet-registered bundle, but no bundles currently route through it.

## Authoritative schema (from dump.cs)

Field order = `[MemoryPackConstructor]` **parameter order**, **not** field
declaration order. They differ in `StoryMasterDataRecord` and
`UnitMasterDataRecord` — reading by declaration order will misalign every
field after the swap.

### Outer wrappers (all share one shape)

| Type | mc | Fields |
|---|---|---|
| ChapterMasterData | 1 | `ChapterMasterDataRecord[] Records` |
| StoryMasterData | 1 | `StoryMasterDataRecord[] Records` |
| UnitMasterData | 1 | `UnitMasterDataRecord[] Records` |
| (12 others) | 1 | `<Name>MasterDataRecord[] Records` |

The array uses the collection rule: `int32 length, [values...]` — note this
is a raw `int32` (**not** `~length`, unlike strings); `-1 = null`.

### ChapterMasterDataRecord (mc=9)

ctor: `(int id, string name, StoryType type, int eventId, string eventName,
Country eventCountry, int order, MainStoryFilter mainStoryFilter,
EventStoryFilter eventStoryFilter)`

### StoryMasterDataRecord (mc=9)

ctor: `(int chapterId, int storyId, string title, string eventName,
string subTitle, StoryType type, int unitId, string[] children, int order)`

The field declaration places `subTitle` before `eventName`, but the ctor
puts `eventName` 4th and `subTitle` 5th. **MemoryPack follows ctor order.**
First implementation hit this trap; only round-trip testing exposed it.

### UnitMasterDataRecord (mc=65 = 0x41)

Full 65-parameter ctor (see `dump.cs:488936`):

```
(int id, string prefixName, string mainName, string description,
 Country country, Rarity rarity, Rarity actualRarity,
 CharacterAttribute attribute, Weapon weapon, Growth growth,
 int maxHp, int maxAttack, float speed, TimeSpan attackInterval,
 float reachValue, float toughness, int attackCount, int multiHitCount,
 TimeSpan multiHitInterval, float attackRange, int spBonus, UnitReach reach,
 float fireRate, float waterRate, float windRate, float lightRate, float darkRate,
 string profession, string weaponLabel, Gender gender, string age, int ageOrder,
 string favorite, string personality, int[] skillIds, string[] skillNames,
 SoundType attackSoundEffectType, string attackSoundEffectId,
 string attackEffectAssetName, string attackEffectAnimationName,
 Vector2 attackEffectPosition, bool attackEffectMulti,
 string targetEffectAssetName, string targetEffectAnimationName,
 string[] randomTargetEffectAnimationNames,
 TimeSpan targetEffectAnimationDelay,
 bool targetEffectMulti, bool targetEffectGround,
 bool targetEffectShowHealCommonEffect, Vector2 targetEffectRandomSeed,
 float hitFrame, Vector3 effectPosition, Vector2 offsetPosition,
 ActType actType, int storyId, UnitAuraTraceData auraTrace,
 UnitMasterDataRecord formChangeData,
 NameFilter, GenderFilter, RarityFilter, AttributeFilter,
 WeaponFilter, UnitReachFilter, CountryFilter, int order)
```

The trap is in the middle: `reach` sits next to `reachValue` in the field
declaration (offsets `0x60` / `0x64`), but the ctor places it after
`spBonus` — i.e. `MultiHitInterval / AttackRange / SpBonus / Reach /
FireRate / WaterRate ...`. Once again, the ctor is the truth.

`FormChangeData` is a **self-referencing UnitMasterDataRecord** (a nullable
nested instance of the same type). Header byte `0xFF` denotes null, which is
the case for almost every unit; the rare evolution forms point to a second
record. Reader recurses through the same `unit_record()` method.

`AuraTrace` is `UnitAuraTraceData` (mc=3, `(string Target, Vector3 Offset,
Vector3 Scale)`).

### Nested primitive types

| Type | Wire size | Notes |
|---|---|---|
| Every enum (StoryType/Country/Rarity/Gender/...Filter) | int32 | CompilerGenerated underlying |
| TimeSpan | int64 ticks | 8 bytes |
| Vector2 | f32 ×2 | unmanaged, packed |
| Vector3 | f32 ×3 | unmanaged, packed |
| `int[]` (SkillIds) | int32 length + int32 ×n | -1 = null |
| `string[]` (Children/SkillNames/...) | int32 length + standard string ×n | -1 = null |

### The other 12 MasterData records (mc = ctor parameter count throughout)

Listed in `[MemoryPackConstructor]` parameter order. Every enum is `int32`,
`TimeSpan = i64`, `Vector2/3` is unmanaged packed `f32`.

| Type | mc | ctor key fields |
|---|---|---|
| BackgroundMasterDataRecord | 9 | `id, code, type, name, description, country, order, backgroundFilter, countryFilter` |
| BackgroundMusicMasterDataRecord | 7 | `id, code, name, description, country, order, countryFilter` |
| GuildMapConditionMasterDataRecord | 4 | `id, name, GuildMapConditionObjectData[] objects, GuildMapConditionConstantData constantData` |
| GuildTournamentMasterDataRecord | 5 | `id, identifier(enum), block(enum), rank, guildName` |
| LeaderStyleMasterDataRecord | 5 | `id, name, description, unitId, order` |
| LoadingComicMasterDataRecord | 2 | `id, name` |
| MainCharacterStyleMasterDataRecord | 5 | `id, name, description, unitId, order` |
| MemorialQuestMasterDataRecord | 4 | `id, name, description, bgmId` |
| MonsterMasterDataRecord | 51 | see below |
| SquareBackgroundMasterDataRecord | 3 | `id, name, countryFilter` |
| StampMasterDataRecord | 6 | `id, name, displayName, index, type, iconAssetName` |
| UnitSkillEffectMasterDataRecord | 7 | `id, name, description, category, type, int[] targets, float[][] parameters` |

#### MonsterMasterDataRecord (mc=51)

```
(int id, string name, string description, Rarity rarity,
 CharacterAttribute attribute, MonsterSkillType skillType,
 float scale, float baseScale, MonsterHardness hardness,
 int damagePartsCount, int cost, TimeSpan callInterval,
 int attackCount, int multiHitCount, TimeSpan multiHitInterval,
 float attackRange, int maxHp, int maxAttack, int seedAttack,
 float speed, TimeSpan attackInterval, float reachValue, float toughness,
 float fireRate, float waterRate, float windRate, float lightRate, float darkRate,
 string appearStageNames, SoundType attackSoundEffectType,
 string attackSoundEffectId, string attackEffectAssetName,
 string attackEffectAnimationName, Vector2 attackEffectPosition,
 bool attackEffectMulti, string targetEffectAssetName,
 string targetEffectAnimationName, TimeSpan targetEffectAnimationDelay,
 bool targetEffectMulti, bool targetEffectGround, float hitFrame,
 Vector3 effectPosition, Vector2 offsetPosition,
 NameFilter, RarityFilter, AttributeFilter,
 MonsterHardnessFilter, MonsterReachFilter, MonsterSkillFilter,
 int order)
```

#### GuildMapCondition nested types (all standard MemoryPackable objects)

| Type | mc | ctor fields |
|---|---|---|
| GuildMapConditionObjectData | 5 | `id, key, Vector3 position, isFlip, clickable` |
| GuildMapConditionConstantData | 14 | `bgm, bg1, bg2, bgMountain1, bgMountain2, bgSky1, bgSky2, string[] unmovableSquareIds, SpriteStudioData[] ssObjects, TextureData[] texObjects, PrefabData[] prefabObjects, SpriteStudioLandmarkData[] ssLandmarks, TextureLandmarkData[] texLandmarks, hideBackgroundCloud` |
| GuildMapConditionSpriteStudioData | 7 | `asset, ssName, animationName, Vector2 scale, Vector2 colliderSize, Vector2 colliderCenter, type` |
| GuildMapConditionTextureData | 5 | `asset, Vector2 scale, Vector2 colliderSize, Vector2 colliderCenter, type` |
| GuildMapConditionPrefabData | 5 | identical shape to TextureData |
| GuildMapConditionSpriteStudioLandmarkData | 15 | `GuildMapLandmarkType type, assetName, prefabName, animationName, Vector2 rootPos/ssPos/ssScale/colliderPos/colliderSize/squarePos/squareSize, label, Vector2 labelPos/labelSize, labelFlip` |
| GuildMapConditionTextureLandmarkData | 8 | `string type, asset, Vector2 rootPos/size/colliderPos/colliderSize/squarePos/squareSize` |

Note: `GuildMapConditionTextureLandmarkData.Type` is a **string**, while
`GuildMapConditionSpriteStudioLandmarkData.Type` is an **enum**
(`GuildMapLandmarkType`). Same field name, different wire types — easy to
mix up.

`Parameters: float[][]` is a nested array; both the outer and inner levels
use the standard MemoryPack collection layout (an `int32` length each).

## Implementation map

- `mercstoria/memorypack.py` (was `merc_decrypt.py`)
  - Reader and Writer each have 12 new record methods plus the shared
    GuildMap nested-array helpers.
  - `_master(record_fn)` collapses the outer wrapper (`mc + Records[]`) into
    one line on the Reader side; the Writer uses `_write_master`.
  - Top-level exports: `serialize_chapter_master / serialize_story_master /
    serialize_unit_master / serialize_background_master /
    serialize_background_music_master / serialize_guild_map_condition_master /
    serialize_guild_tournament_master / serialize_leader_style_master /
    serialize_loading_comic_master / serialize_main_character_style_master /
    serialize_memorial_quest_master / serialize_monster_master /
    serialize_square_background_master / serialize_stamp_master /
    serialize_unit_skill_effect_master`.
- `scripts/extract_repack.py` (was `merc_storia_toolkit.py`)
  - `FULL_SCHEMA_MASTER` covers all 15 bundles — every misc bundle now goes
    through full-schema; the offset path remains only as a fallback for
    bundles not yet registered (currently none).
  - `cmd_extract_misc` / `cmd_repack_misc` keep the same dispatch shape: hits
    take the full-schema path (JSON `schema: "full"`, fields are `_mc` plus
    structured `Records[]`); misses go through the offset path
    (`schema: "offset"`).
  - Full-schema extract reuses the story-path "round-trip-verify before
    writing JSON; mismatches go to `_errors.json` and are skipped" pattern,
    so any JSON written is provably losslessly repackable.

## Final state (2026-06-16)

All 15 MasterData bundles round-trip byte-identical, verified on the
unmodified vanilla cache:

| Bundle | Records | JSON size |
|---|---|---|
| ChapterMasterData | 166 | 47 KB |
| StoryMasterData | 4091 | 988 KB |
| UnitMasterData | 2343 | 6.1 MB |
| BackgroundMasterData | 559 | 161 KB |
| BackgroundMusicMasterData | 362 | 87 KB |
| GuildMapConditionMasterData | 3 | 219 KB |
| GuildTournamentMasterData | 2 | <1 KB |
| LeaderStyleMasterData | 3 | <1 KB |
| LoadingComicMasterData | 78 | 6.5 KB |
| MainCharacterStyleMasterData | 19 | 4 KB |
| MemorialQuestMasterData | 12 | 6.7 KB |
| MonsterMasterData | 1044 | 2.0 MB |
| SquareBackgroundMasterData | 168 | 18 KB |
| StampMasterData | 244 | 51 KB |
| UnitSkillEffectMasterData | 132 | 133 KB |

`uv run -m mercstoria extract-misc` writes 15 bundles to
`extracted_data/misc/`, all `(full schema)`, `Errors: 0`. A variable-length
string edit was tested earlier (the first Chapter `Name` was extended from
19 JP chars to 34 CN chars; plaintext grew 16819 → 16864 bytes, and the
Reader parsed back the modified string unchanged).

## Translator workflow

```
uv run -m mercstoria extract-misc
# Edit the string fields under Records[] in extracted_data/misc/<AssetName>.json:
#   ChapterMasterData    -> Records[].Name / EventName
#   StoryMasterData      -> Records[].Title / EventName / SubTitle
#   UnitMasterData       -> Records[].MainName / Description / Profession / ...
#   BackgroundMasterData -> Records[].Name / Description
#   MonsterMasterData    -> Records[].Name / Description / AppearStageNames
#   StampMasterData      -> Records[].DisplayName
#   UnitSkillEffectMasterData -> Records[].Name / Description
#   ...other types analogous; lengths are unconstrained.

uv run -m mercstoria repack-misc
# Only edited JSONs get repacked (fingerprint comparison, same mechanism as
# the story path).

uv run -m mercstoria deploy
# Push to the live cache; the original is auto-backed-up to *.bak.
```

**Don't touch** the markers `_mc` / `_skipped` / `schema` / `bundle` /
`asset_name_in_bundle` in JSON. Non-string fields under `Records[]` (int /
float / enum / Vector) generally shouldn't be touched either — changes there
typically make the game read garbage values.

## Related files

- `mercstoria/memorypack.py` — Reader/Writer + `serialize_*_master` entry
  points.
- `scripts/extract_repack.py` — `FULL_SCHEMA_MASTER` dispatch +
  extract/repack double-path.
- `il2cpp_output/dump.cs` — authoritative schema source:
  - ChapterMasterDataRecord at `dump.cs:477496`
  - StoryMasterDataRecord at `dump.cs:485987`
  - UnitMasterDataRecord at `dump.cs:488461` (ctor at `:488936`, field table
    `:488463-488593`)
  - BackgroundMasterDataRecord at `dump.cs:476882` (ctor `:476955`)
  - BackgroundMusicMasterDataRecord at `dump.cs:477172` (ctor `:477239`)
  - GuildMapConditionMasterDataRecord at `dump.cs:477984` (ctor `:478022`),
    GuildMapConditionConstantData at `:478210` (ctor `:478318`),
    nested SpriteStudio/Texture/Prefab/Landmark types follow from `:478373`
  - GuildTournamentMasterDataRecord at `dump.cs:479133` (ctor `:479178`)
  - LeaderStyleMasterDataRecord at `dump.cs:479388` (ctor `:479441`)
  - LoadingComicMasterDataRecord at `dump.cs:479925` (ctor `:479957`)
  - MainCharacterStyleMasterDataRecord at `dump.cs:480214` (ctor `:480267`)
  - MemorialQuestMasterDataRecord at `dump.cs:480554` (ctor `:480600`)
  - MonsterMasterDataRecord at `dump.cs:481156` (ctor `:481526`, 51 params)
  - SquareBackgroundMasterDataRecord at `dump.cs:482102` (ctor `:482133`)
  - StampMasterDataRecord at `dump.cs:482343` (ctor `:482399`)
  - UnitSkillEffectMasterDataRecord at `dump.cs:489289` (ctor `:489349`)
- [`MEMORYPACK_SCHEMA_GUIDE.md`](MEMORYPACK_SCHEMA_GUIDE.md) — sister doc
  for the story bundles. Wire-format details (`_mc` truncation,
  `Nullable<float>` 8-byte layout, `~utf8_byte_count` strings) are covered
  there and not repeated here.
