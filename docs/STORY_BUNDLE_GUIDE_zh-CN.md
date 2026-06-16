# Story Bundle 指南 — 解密 + MemoryPack Schema + 重打包

剧情对话在游戏里是怎么存的、怎么字节级解出 `MemoryPack` 载荷,以及怎么重打包成
游戏接受的 UnityFS bundle。配套文档:
[`MASTERDATA_SCHEMA_GUIDE_zh-CN.md`](MASTERDATA_SCHEMA_GUIDE_zh-CN.md)
负责 15 种 master-data record。

游戏环境见 [`README.md`](../README.md#game-environment-canonical)。CRC 修补
([`CRC_PATCH_GUIDE_zh-CN.md`](CRC_PATCH_GUIDE_zh-CN.md))必须先打,否则改过的
bundle 会被静默重下。

## 文本存在哪里

| `AssetBundle/StandaloneWindows64/` 下的路径 | 内容 |
|---|---|
| `StoryMasterData/<hash>.bundle` | 剧情对话,~4,008 个 bundle,一个剧本一个。每个 = 一个 TextAsset = 加密后的 `StoryYamlData`。 |
| `Story/<hash>.bundle` | 单剧情元数据(BGM key、资源引用等)。翻译用不太上。 |
| `MasterData/<hash>.bundle` | 全局 master 数据:章节名、剧情标题、角色 / UI 文案。同一套加密,schema 不同 —— 见 [`MASTERDATA_SCHEMA_GUIDE_zh-CN.md`](MASTERDATA_SCHEMA_GUIDE_zh-CN.md)。 |

## 加密

```
plaintext  = MemoryPack(StoryYamlData)
iv         = os.urandom(16)
key        = PBKDF2_HMAC_SHA256(password="2147483647",
                                salt="-2147483648",
                                iterations=1024, length=32)
ciphertext = iv || AES_CBC_PKCS7(key, iv, plaintext)
```

password 和 salt 都是**字符串字面量**(直接抄了 `int.MinValue.ToString()` /
`int.MaxValue.ToString()`)。1024 轮 PBKDF2,故意弱化的客户端用法。全局一把
key,4,000 个 bundle 全部验过。每次重打包 IV 重新生成,游戏不会校验 IV。

key 派生在 [`mercstoria/config.py`](../mercstoria/config.py)
(`derive_aes_key`),解密 / 加密 helper + 完整 Reader/Writer 在
[`mercstoria/memorypack.py`](../mercstoria/memorypack.py) 里。

## MemoryPack wire format

`MemoryPack`(Cysharp)是 schema 驱动的二进制序列化器。源生成器为每个
`[MemoryPackable]` 类生成一份固定字段顺序的二进制布局,数据里**不带 schema** ——
读取方必须事先知道每个类的字段顺序。

### 基础类型

| 类型 | wire format | 备注 |
|---|---|---|
| `byte` / `bool` | 1 byte | bool: 0 或 1 |
| `int32` / `float32` | 4 字节 LE | |
| `int64` / `TimeSpan` | 8 字节 LE | TimeSpan = i64 ticks |
| `enum` | int32 | CompilerGenerated 底层类型 |
| `Vector2` / `Vector3` | 2 / 3 × f32 | unmanaged 紧排 |
| `string`(UTF-8 模式) | 见下文 | |
| `T[]` / `List<T>` | `int32 count`(原始,**不是** `~count`;`-1` = null) + 元素 | |
| `Dictionary<K,V>` | `int32 count` + `(K, V)` 序列 | |
| `class` / `struct` | 1 字节 member count(`0xFF` = null) + 字段 | 见下面"对象头" |
| `Nullable<float>` | **8 字节裸内存拷贝** | `byte hasValue + 3 padding + float value` —— 见下文 |

### 字符串编码(关键陷阱)

```
int32 header:
   = -1             → null 字符串
   =  0             → 空 ""
   = ~byteCount     → 后接:int32 charCount, byte[byteCount](UTF-8)
                      (~ 是按位取反,不是取负:~10 = -11)
```

header 是 UTF-8 字节数的**按位取反**,不是取负。把 `-byteCount` 当成
`~byteCount` 写,下一个字段以及之后整段缓冲区都会错位。

`charCount` = .NET `string.Length` = UTF-16 代码单元数。BMP 字符(日文、
中文、基本拉丁)在 Python 里就是 `len(s)`;BMP 外要用
`len(s.encode('utf-16-le')) // 2`。

### 对象头 —— 截断字段是常态

1 字节 member count。`0xFF` = null,`0..249` = 实际字段数。**截断对象很常见**:
writer 可以发出 `mc < 声明字段数`,这种情况下只写前 `mc` 个字段,其余在读取时
取默认值。Reader 必须 `min(mc, 声明字段数)`,把被截掉的字段名记到 `_skipped`
里,Writer 按相同 `mc` 回放截断。

### `Nullable<float>` 是 8 字节

`byte hasValue + 3 字节 padding + float value`。**不是** 5 字节。padding
实践中永远是 0,但 hasValue=0 时的 float 位**必须原样保留**(那是堆上重用的
垃圾值,不是清零)。Reader 在 null 情况下返回
`{"_null": True, "_bits": "<hex>"}`,这样 Writer 才能写回完全相同的字节。

## `StoryYamlData` schema(取自 dump.cs)

| 类型 | mc | 字段 |
|---|---|---|
| StoryYamlData | 2 | `int Id`、`Dictionary<int, StorySceneYamlData> Scenes` |
| StorySceneYamlData | 21 | 完整字段列表见 `Reader.scene` |
| StoryTextAnimationYamlData | 5 | Type / Size(f32) / Interval / FadeInDuration / ForceWait |
| StorySceneCharacterYamlData | 11 | TextureId / FaceTextureId / Type / Key / DisplayName / Expression / Emotion / Active / Appearance / Offset(vec3) / Scale(vec3) |
| StoryCharacterAppearanceYamlData | 9 | Type + 6 个 Nullable\<float\> + Duration(TimeSpan) + Active(bool) |
| StoryBackgroundMusicYamlData | 7 | Name / AssetType / AssetId / Mute / FadeIn / FadeOut / ForceFade |
| StorySoundEffectYamlData | 9 | |
| StoryEffectParameterYamlData | 14 | 最后一个字段是 CursorParameter(嵌套对象) |
| StoryCursorParameterYamlData | 7 | Type / Time / Position(vec2) / Direction / TouchPosition(vec2) / TouchScale(vec2) / Image |
| StoryBackgroundEffectParameterYamlData | 6 | Z(Nullable\<float\>) / AutoSkip / Blur / Bright / Sepia / Animation |
| StoryBlur/Bright/Sepia/AnimationBackgroundEffectParameterYamlData | 5 / 4 / 6 / 8 | |
| StoryAssetParameterYamlData | 11 | |

对话翻译只关心 `StorySceneYamlData` 里的 `Speakers`(string[])和 `Text`
(string)两个字段,其它都是场景元数据 —— Reader/Writer 会原样 round-trip,
翻译者不动。

权威来源是 `il2cpp_output/dump.cs`(Il2CppDumper 输出)。重新 grep 时用正则
`private\s+(?:readonly\s+)?...<\w+>k__BackingField` —— 注意 `readonly`
**必须可选**,因为 `StoryTextAnimationYamlData` 的字段没有 `readonly`。

## UnityFS 外壳

每个故事 bundle = 一个 `UnityFS` 归档,里面装一个 `TextAsset`。`TextAsset`
在磁盘上的原始布局:

```
int32  nameLength
byte[] name(UTF-8)
4 字节对齐 padding
int32  scriptLength
byte[] script(加密内容:iv || AES_CBC ciphertext)
```

UnityPy 能透明读到。`obj.read()` 返回 `TextAsset`,`.script` 就是密文。
重打包:`.script` 设新密文(长度可变),`text_asset.save()`,
`env.file.save(packer="lz4")`。

## 端到端流水线

**A. Extract → JSON**。`mercstoria extract-story` 走遍 `StoryMasterData/`,
解密、跑完整 MemoryPack Reader(只有 round-trip 字节级一致的 bundle 才会进
JSON 输出),给每个剧情写一份 JSON,带从 `StoryMasterData` + `ChapterMasterData`
拉到的章节 / 集数元数据。`mercstoria extract-misc` 用同一套 Reader/Writer
处理 15 个 master bundle
([`MASTERDATA_SCHEMA_GUIDE_zh-CN.md`](MASTERDATA_SCHEMA_GUIDE_zh-CN.md))。
~4,000 剧情,~12 万对话行,~25 MB JSON,~30 秒。

**B. 翻译**。本文不展开。遍历 JSON,把每条 `Text` 和每个 speaker 连同上下文
丢给 LLM,写回。从 `dump.cs` 提角色名词表保证一致。

**C. Repack**。`mercstoria repack-story` 用完整 Writer 重新序列化编辑过的
JSON,新 IV 加密,写出 `UnityFS(lz4)` bundle。只重打 JSON 自上次 repack 后改过
的(走 fingerprint 比对)。字符串长度无限制 —— plaintext 整段重写而非 splice,
译文想多长多长。

**D. Deploy**。`mercstoria deploy` 把 `repacked_bundles/` 拷进 live cache
(自动优先 `<game>/AssetBundle`,其次 LocalLow),原文件备份到 `*.bak`。

## 最终状态(2026-06-17)

- **4008 / 4013 个故事 bundle 字节级 round-trip 一致**(99.88%),原始未 patch
  cache 上验证。剩余 5 个在 read 时抛异常的 bundle 写在
  `mercstoria/memorypack.py` 里,触及一个少见的嵌套类型变体,字段布局没追下去。
  4008 已经够用,不再追。
- 15 个 master-data bundle 全部字节级 round-trip
  ([`MASTERDATA_SCHEMA_GUIDE_zh-CN.md`](MASTERDATA_SCHEMA_GUIDE_zh-CN.md))。
- `mercstoria check-roundtrip [N]` 在 N 个 bundle 上回归 Reader/Writer
  (省略 N 跑全量)。

## 容易踩到的坑

- **`~byteCount`,不是 `-byteCount`**。差一位之后整个缓冲区错位。
- **`charCount` = UTF-16 单元数**,不是 UTF-8 字节数也不是码点数。BMP 文字
  上不会出问题。
- **PBKDF2 password / salt 是字符串字面量**,不是 boxed int。salt 看着像负数
  是因为有人直接抄了 `int.MinValue.ToString()`。
- **`Nullable<float>` 是 8 字节**(不是 5),hasValue=0 时的 float 位必须原样
  保留。
- **截断对象很常见**:读时 `min(mc, 声明字段数)`,写时按同样 `mc` 回放。
- **空 `Speakers` 是合法的** —— 旁白行就是 `Speakers: []` + 非空 `Text`。
  接受 `count = 0`。
- **TextAsset name 有 4 字节对齐 padding** —— UnityPy `obj.read()` 透明处理,
  别去读 raw bytes。
- **`env.file.save(packer="lz4")`,不要用 `"lz4hc"`**。UnityPy 自带的 lz4hc
  packer 在 Unity 6000.x 上把 block-info trailer 写错。
- **有些 bundle 里有多个 TextAsset**(尤其是 `MasterData/`)。要遍历所有
  `obj.type.name == "TextAsset"`,别只取第一个。

## 文件参考

| 路径 | 用途 |
|---|---|
| `mercstoria/memorypack.py` | AES 解密 + `StoryYamlData` 和 15 个 master record 的完整 MemoryPack Reader/Writer |
| `scripts/extract_repack.py` | `mercstoria <subcmd>`:`extract` / `extract-story` / `extract-misc` / `repack` / `repack-story` / `repack-misc` / `test-repack` |
| `scripts/check_roundtrip.py` | `mercstoria check-roundtrip [N]` —— 在 N 个 bundle 上回归 Reader/Writer |
| `scripts/deploy.py` | `mercstoria deploy` —— 把 `repacked_bundles/` 拷进 live cache |
| `scripts/bundle_cache.py` | `mercstoria bundle-cache` —— 把 LocalLow CDN cache 打进游戏目录 |
| `il2cpp_output/dump.cs` | 权威 schema 来源 |

## 外部链接

- [Cysharp/MemoryPack](https://github.com/Cysharp/MemoryPack) —— 二进制
  序列化器;UTF-8 模式的字符串布局在它 README 里
- [UnityPy](https://github.com/K0lb3/UnityPy) —— Unity 资源 bundle 读写
- [pyca/cryptography](https://cryptography.io/) —— AES-256-CBC + PBKDF2
- [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) —— 用来
  在 `RijndaelManaged` 调用里确认 AES 的 password / salt 常量

跑完之后去
[`FONT_REPLACEMENT_GUIDE_zh-CN.md`](FONT_REPLACEMENT_GUIDE_zh-CN.md) 把日文
字体替换成你目标书写系统的字体。
