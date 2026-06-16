# MasterData MemoryPack Schema 指南 — 全部 15 种 MasterData

把游戏里所有 15 个 MasterData bundle 的 MemoryPack schema 完整逆向，落地为
字节级 round-trip 的 Reader/Writer。这样字符串替换不再受原始字节长度限制 ——
翻译后的中文长度可以任意，而不需要严格 ≤ 原日文。

## 这件事为什么要做

老 toolkit 的 misc 路径用 `find_all_strings` 扫一遍 plaintext，按字节偏移做
splice。这条路的死穴是：**新字符串编码后必须和原字符串等长**，否则后续所有
offset 都失效。日译中经常长度不等，于是想替换 `「合戦」→「大战」` 都得抠
字节。

把全部 15 个 MasterData 按完整 schema 解出来，记录变成结构化对象，序列化时
整段重写 plaintext，length 想换多少换多少。和 story 路径走的是同一套
Reader/Writer 风格(在 `mercstoria/memorypack.py` 里)，沿用 `_mc` + 截断对象 +
Nullable\<float\> 的 8 字节裸内存约定。

`FULL_SCHEMA_MASTER` 调度表现在覆盖全部 15 个 bundle，offset 路径仍然保留在
代码里以备未来未注册的 bundle 用，但目前没有任何 bundle 落到那条路径上。

## 权威 schema(取自 dump.cs)

字段顺序 = `[MemoryPackConstructor]` 的**参数顺序**，**不是**字段声明顺序 ——
两者在 `StoryMasterDataRecord` 和 `UnitMasterDataRecord` 上不一致，照声明顺序
读会错位。

### 外层 wrapper(都是同一个 shape)

| 类型 | mc | 字段 |
|---|---|---|
| ChapterMasterData | 1 | `ChapterMasterDataRecord[] Records` |
| StoryMasterData | 1 | `StoryMasterDataRecord[] Records` |
| UnitMasterData | 1 | `UnitMasterDataRecord[] Records` |

数组用集合规则:`int32 length, [values...]`(注意是原始 int32,**不是**
`~length`,和字符串规则不同;`-1 = null`)。

### ChapterMasterDataRecord(mc=9)

ctor: `(int id, string name, StoryType type, int eventId, string eventName,
Country eventCountry, int order, MainStoryFilter mainStoryFilter,
EventStoryFilter eventStoryFilter)`

### StoryMasterDataRecord(mc=9)

ctor: `(int chapterId, int storyId, string title, string eventName,
string subTitle, StoryType type, int unitId, string[] children, int order)`

字段声明顺序里 `subTitle` 在 `eventName` 前面,但 ctor 把 `eventName` 放第
四个、`subTitle` 放第五个。**MemoryPack 走 ctor 顺序**,首次写错过来的人(我)
被坑了一次,实测验证之后才修对。

### UnitMasterDataRecord(mc=65 = 0x41)

ctor 参数表(完整 65 项,见 `dump.cs:488936`):

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

陷阱集中在中段:`reach` 在字段声明里紧跟 `reachValue`(0x60、0x64),但 ctor
把它推到了 `spBonus` 后面 —— 也就是 `MultiHitInterval / AttackRange / SpBonus
/ Reach / FireRate / WaterRate ...`。同样是 ctor 才是真相。

`FormChangeData` 是一个**自引用的 UnitMasterDataRecord**(同一类型的可空嵌套),
头字节 `0xFF` 表示 null,绝大多数 unit 都是 null,少量进化形态会指向另一条
record。Reader 用同一个 `unit_record()` 递归处理。

`AuraTrace` 是 `UnitAuraTraceData`(mc=3,`(string Target, Vector3 Offset,
Vector3 Scale)`)。

### 嵌套类型

| 类型 | wire size | 备注 |
|---|---|---|
| 所有 enum(StoryType/Country/Rarity/Gender/...Filter) | int32 | CompilerGenerated underlying |
| TimeSpan | int64 ticks | 8 字节 |
| Vector2 | f32 ×2 | unmanaged 紧排 |
| Vector3 | f32 ×3 | unmanaged 紧排 |
| `int[]`(SkillIds) | int32 length + int32 ×n | -1 = null |
| `string[]`(Children/SkillNames/...) | int32 length + 标准 string ×n | -1 = null |

### 其余 12 个 MasterData 记录(全部 mc=ctor 参数数)

按 `[MemoryPackConstructor]` 参数顺序列出。enum 全部 int32，TimeSpan = i64，
Vector2/3 = unmanaged f32 紧排。

| 类型 | mc | ctor 关键字段 |
|---|---|---|
| BackgroundMasterDataRecord | 9 | `id, code, type, name, description, country, order, backgroundFilter, countryFilter` |
| BackgroundMusicMasterDataRecord | 7 | `id, code, name, description, country, order, countryFilter` |
| GuildMapConditionMasterDataRecord | 4 | `id, name, GuildMapConditionObjectData[] objects, GuildMapConditionConstantData constantData` |
| GuildTournamentMasterDataRecord | 5 | `id, identifier(enum), block(enum), rank, guildName` |
| LeaderStyleMasterDataRecord | 5 | `id, name, description, unitId, order` |
| LoadingComicMasterDataRecord | 2 | `id, name` |
| MainCharacterStyleMasterDataRecord | 5 | `id, name, description, unitId, order` |
| MemorialQuestMasterDataRecord | 4 | `id, name, description, bgmId` |
| MonsterMasterDataRecord | 51 | 见下文 |
| SquareBackgroundMasterDataRecord | 3 | `id, name, countryFilter` |
| StampMasterDataRecord | 6 | `id, name, displayName, index, type, iconAssetName` |
| UnitSkillEffectMasterDataRecord | 7 | `id, name, description, category, type, int[] targets, float[][] parameters` |

#### MonsterMasterDataRecord(mc=51)

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

#### GuildMapCondition 嵌套类型(都是普通 MemoryPackable 对象)

| 类型 | mc | ctor 字段 |
|---|---|---|
| GuildMapConditionObjectData | 5 | `id, key, Vector3 position, isFlip, clickable` |
| GuildMapConditionConstantData | 14 | `bgm, bg1, bg2, bgMountain1, bgMountain2, bgSky1, bgSky2, string[] unmovableSquareIds, SpriteStudioData[] ssObjects, TextureData[] texObjects, PrefabData[] prefabObjects, SpriteStudioLandmarkData[] ssLandmarks, TextureLandmarkData[] texLandmarks, hideBackgroundCloud` |
| GuildMapConditionSpriteStudioData | 7 | `asset, ssName, animationName, Vector2 scale, Vector2 colliderSize, Vector2 colliderCenter, type` |
| GuildMapConditionTextureData | 5 | `asset, Vector2 scale, Vector2 colliderSize, Vector2 colliderCenter, type` |
| GuildMapConditionPrefabData | 5 | 同 TextureData |
| GuildMapConditionSpriteStudioLandmarkData | 15 | `GuildMapLandmarkType type, assetName, prefabName, animationName, Vector2 rootPos/ssPos/ssScale/colliderPos/colliderSize/squarePos/squareSize, label, Vector2 labelPos/labelSize, labelFlip` |
| GuildMapConditionTextureLandmarkData | 8 | `string type, asset, Vector2 rootPos/size/colliderPos/colliderSize/squarePos/squareSize` |

注意:`GuildMapConditionTextureLandmarkData.Type` 是 **string**,不是 enum;
而 `GuildMapConditionSpriteStudioLandmarkData.Type` 是 enum
(`GuildMapLandmarkType`)。两者很容易混。

`Parameters: float[][]` 是嵌套数组,外层 + 内层都是标准 MemoryPack collection
(int32 length 各一遍)。

## 实现位置

- `mercstoria/memorypack.py`(原 `merc_decrypt.py`)
  - Reader/Writer 各加 12 组 record 方法 + 共用嵌套数组(GuildMap 那一族)。
  - `_master(record_fn)` 把 outer wrapper(`mc + Records[]`)写成一行,Reader
    侧;Writer 侧的对应方法叫 `_write_master`。
  - 顶层导出:`serialize_chapter_master / serialize_story_master /
    serialize_unit_master / serialize_background_master /
    serialize_background_music_master / serialize_guild_map_condition_master /
    serialize_guild_tournament_master / serialize_leader_style_master /
    serialize_loading_comic_master / serialize_main_character_style_master /
    serialize_memorial_quest_master / serialize_monster_master /
    serialize_square_background_master / serialize_stamp_master /
    serialize_unit_skill_effect_master`。
- `scripts/extract_repack.py`(原 `merc_storia_toolkit.py`)
  - `FULL_SCHEMA_MASTER` 调度表覆盖全部 15 个 bundle —— 现在所有 misc
    bundle 都走完整 schema,offset 路径只在表里没注册的 bundle 上保留(目前为
    空,留作后续扩展用)。
  - `cmd_extract_misc` / `cmd_repack_misc` 主循环不变:命中走完整 schema
    (JSON `schema: "full"`,字段是 `_mc` + 结构化 `Records[]`),没命中走
    offset path(`schema: "offset"`)。
  - 完整 schema 的 extract 沿用 story 那边的"读完先 round-trip 校验,不一致
    就跳过并写 `_errors.json`"模式 —— 通过校验的 JSON 反向 repack 是可证
    无损的。

## 最终状态(2026-06-16)

全部 15 个 MasterData bundle round-trip byte-identical,在原始未 patch 的
游戏 cache 上验证:

| bundle | 记录数 | JSON 大小 |
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

`uv run -m mercstoria extract-misc` → 15 bundles 写入 `extracted_data/misc/`,
全部 `(full schema)`,`Errors: 0`。变长字符串 edit 之前实测过(把 Chapter
第一条 Name 从 19 字符日文扩到 34 字符中文,plaintext 从 16819 → 16864 字节,
Reader 重新解析正确)。

## 翻译者使用流程

```
uv run -m mercstoria extract-misc
# extracted_data/misc/<AssetName>.json 里的 Records[] 直接编辑字符串字段:
#   ChapterMasterData    -> Records[].Name / EventName
#   StoryMasterData      -> Records[].Title / EventName / SubTitle
#   UnitMasterData       -> Records[].MainName / Description / Profession / ...
#   BackgroundMasterData -> Records[].Name / Description
#   MonsterMasterData    -> Records[].Name / Description / AppearStageNames
#   StampMasterData      -> Records[].DisplayName
#   UnitSkillEffectMasterData -> Records[].Name / Description
#   ...其它类比,长度任意。

uv run -m mercstoria repack-misc
# 改过的 JSON 才会被重新打包(走 fingerprint 比对,跟 story 一套机制)。

uv run -m mercstoria deploy
# 部署到 live cache,自动备份原 .bak。
```

JSON 里**不要动** `_mc` / `_skipped` / `schema` / `bundle` /
`asset_name_in_bundle` 这些 marker。`Records[]` 里的非字符串字段
(int/float/enum/Vector)一般也不要动 —— 改了多半导致游戏读出乱七八糟的数值。

## 相关文件

- `mercstoria/memorypack.py` — Reader/Writer + `serialize_*_master` 入口。
- `scripts/extract_repack.py` — `FULL_SCHEMA_MASTER` 调度 + extract/repack
  双路径。
- `il2cpp_output/dump.cs` — 权威 schema 来源:
  - ChapterMasterDataRecord 在 `dump.cs:477496`
  - StoryMasterDataRecord 在 `dump.cs:485987`
  - UnitMasterDataRecord 在 `dump.cs:488461`(ctor 在 `:488936`,字段表
    `:488463-488593`)
  - BackgroundMasterDataRecord 在 `dump.cs:476882`(ctor `:476955`)
  - BackgroundMusicMasterDataRecord 在 `dump.cs:477172`(ctor `:477239`)
  - GuildMapConditionMasterDataRecord 在 `dump.cs:477984`(ctor `:478022`),
    GuildMapConditionConstantData 在 `:478210`(ctor `:478318`),
    嵌套 SpriteStudio/Texture/Prefab/Landmark 数据从 `:478373` 起依次排列
  - GuildTournamentMasterDataRecord 在 `dump.cs:479133`(ctor `:479178`)
  - LeaderStyleMasterDataRecord 在 `dump.cs:479388`(ctor `:479441`)
  - LoadingComicMasterDataRecord 在 `dump.cs:479925`(ctor `:479957`)
  - MainCharacterStyleMasterDataRecord 在 `dump.cs:480214`(ctor `:480267`)
  - MemorialQuestMasterDataRecord 在 `dump.cs:480554`(ctor `:480600`)
  - MonsterMasterDataRecord 在 `dump.cs:481156`(ctor `:481526`,51 项)
  - SquareBackgroundMasterDataRecord 在 `dump.cs:482102`(ctor `:482133`)
  - StampMasterDataRecord 在 `dump.cs:482343`(ctor `:482399`)
  - UnitSkillEffectMasterDataRecord 在 `dump.cs:489289`(ctor `:489349`)
- `MEMORYPACK_SCHEMA_GUIDE_zh-CN.md` — story bundle 的姊妹文档,wire format
  细节(`_mc` 截断、Nullable\<float\> 8 字节、字符串 `~utf8_byte_count`)
  在那里讲过,这里不重复。
