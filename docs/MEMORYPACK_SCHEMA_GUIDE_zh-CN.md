# MemoryPack Schema 指南 — Merc Storia 故事 Bundle

逆向得到的 Merc Storia `StoryYamlData` MemoryPack schema,以及实现字节级
round-trip 的 Reader/Writer 重写。这是安全增删对话 chunk 的基础(原先只能做
就地字符串替换)。

## 这件事为什么要做

之前 toolkit 里基于 `find_story_strings` + splice 的 splice 路径(现在仍可用,
落在 `scripts/extract_repack.py` 里给未注册 bundle 兜底)只能做就地字符串替换,
**无法**增加或删除场景数。要做插入/删除就必须有完整的序列化器。

于是把 Reader/Writer 重写到 `mercstoria/memorypack.py` 里,让
`serialize_story(read_story_bundle(...)) == decrypted_plaintext`
逐字节相同。这条等式一旦成立,JSON ↔ bytes 就完全可逆,翻译者可以自由插入或
删除条目。

最早 `merc_decrypt.py` 里的 schema 是猜的,字段也不齐,真实 bundle 上的 round-trip
直接 50/50 全失败。修复方法是把 `dump.cs`(Il2CppDumper 输出在
`il2cpp_output/dump.cs`)当作权威来源,把每个 `[MemoryPackable]` 类按声明顺序
字段对字段地照搬。Master-data record 走相同套路,但跟的是
`[MemoryPackConstructor]` 的**参数**顺序——见
[`MASTERDATA_SCHEMA_GUIDE_zh-CN.md`](MASTERDATA_SCHEMA_GUIDE_zh-CN.md)。

## 权威 schema(取自 dump.cs,含 member count)

| 类型 | mc | 说明 |
|---|---|---|
| StoryYamlData | 2 | `int Id`、`Dictionary<int,StorySceneYamlData> table`(= int len + (key, scene)*) |
| StorySceneYamlData | 21 | 完整字段列表见 `Reader.scene` |
| StoryTextAnimationYamlData | **5** | 不是 0 — Type/Size(f32)/Interval/FadeInDuration/ForceWait。字段没有 `readonly`,所以 `private readonly` 的正则会漏掉 |
| StorySceneCharacterYamlData | 11 | TextureId/FaceTextureId/Type/Key/DisplayName/Expression/Emotion/Active/Appearance/Offset(vec3)/Scale(vec3) |
| StoryCharacterAppearanceYamlData | 9 | Type + 6 个 Nullable\<float\> + Duration(TimeSpan) + Active(bool) |
| StoryBackgroundMusicYamlData | 7 | Name/AssetType/AssetId/Mute/FadeIn/FadeOut/ForceFade |
| StorySoundEffectYamlData | 9 | |
| StoryEffectParameterYamlData | 14 | 最后一个字段是 CursorParameter(嵌套对象) |
| StoryCursorParameterYamlData | 7 | Type/Time/Position(vec2)/Direction/TouchPosition(vec2)/TouchScale(vec2)/Image |
| StoryBackgroundEffectParameterYamlData | 6 | Z(Nullable\<float\>)/AutoSkip/Blur/Bright/Sepia/Animation |
| StoryBlur/Bright/Sepia/AnimationBackgroundEffectParameterYamlData | 5/4/6/8 | |
| StoryAssetParameterYamlData | 11 | |

## 关键的 wire-format 事实

1. **对象头**:1 字节 member_count。`0xFF` = null,`0..249` = 实际字段数。
   **截断对象很常见**:writer 可以发出 `mc < 声明字段数`,这种情况下只写前
   `mc` 个字段,其余在读取时取默认值。Reader 必须 `min(mc, len(fields))`,把
   被截掉的字段名记到 `_skipped` 里,Writer 按同样方式回放截断。

2. **集合**(`int length, [values]`):原始 int32,**不是** `~count`。
   `-1 = null`。和字符串规则不同。

3. **字符串**:`int32 ~utf8_byte_count, int32 utf16_len, utf8_bytes`。
   `-1 = null`,`0 = ""`。

4. **`Nullable<float>` 是 unmanaged,8 字节裸内存拷贝**:
   `byte hasValue + 3 字节 padding + float value`。**不是** 5 字节。
   实践中 padding 永远是 0,但 hasValue=0 时的 float 位必须原样保留
   (那是堆上重用的垃圾值)。为此 Reader 在 null 情况下返回
   `{"_null": True, "_bits": "<hex>"}`,这样 Writer 才能写回完全相同的字节。

5. **TimeSpan**:8 字节 i64 tick(unmanaged 结构体)。
6. **Vector2/3**:原始 f32 字段紧挨着排列(unmanaged)。
7. **enum**:原始 int32(CompilerGenerated 底层类型)。

## 最终状态(2026-06-16)

- `mercstoria/memorypack.py`(原 `merc_decrypt.py`)已用完整 schema 重写,
  支持截断对象,修复了 Nullable\<float\> 的 8 字节问题,以及 text_anim
  5 字段问题。**全量 bundle round-trip 4008/4013 字节级一致(99.88%)**。
  剩下 5 个失败的 bundle 都是抛 exception(不是悄悄 diff),说明某个少见的
  嵌套类型还有字段不对——但 4008 已经够用,不再追这 5 个,除非以后有新需求。
- `scripts/extract_repack.py`(原 `merc_storia_toolkit.py`)的
  `extract-story` / `repack-story` 已经接到上面那套 Reader/Writer,
  `FULL_SCHEMA_MASTER` 把 15 个 master bundle 也用同一套机制覆盖。
  增删 chunk 在 4008 个能 round-trip 的 story bundle 上端到端可用。

剩余 5 个失败 bundle,留作未来参考:

- `099e9af067d5eb9d4ac372e4d05a34d1.bundle`
- `63262fa012bb617f9246e131050ed3cc.bundle`
- `7b5f2448d85d1cf86407c0cafd9611cc.bundle`
- `81ddf1d14b7f783411f264e6555539bf.bundle`
- `fec90c149e7b4b6659cb3c7e2dd6362e.bundle`

## 相关文件

- `mercstoria/memorypack.py` — 完整的 Reader/Writer、`serialize_story`、
  `process_story_bundle`。Round-trip CLI:
  `uv run -m mercstoria check-roundtrip [N]`(N 是要扫的 bundle 数,省略
  则跑全部缓存)。
- `scripts/extract_repack.py` — `extract-story` / `repack-story` 直接调上面
  的 Reader/Writer;`FULL_SCHEMA_MASTER` 把同一套机制扩展到 15 个 master
  bundle。
- `il2cpp_output/dump.cs` — 权威 schema 来源。重新 grep 时用正则
  `private\s+(?:readonly\s+)?...<\w+>k__BackingField`(`readonly` 必须可选,
  `TextAnimation` 的字段就没有 `readonly`)。
- [`MASTERDATA_SCHEMA_GUIDE_zh-CN.md`](MASTERDATA_SCHEMA_GUIDE_zh-CN.md) —
  master-data schema 的姊妹文档(15 种 record 类型)。
