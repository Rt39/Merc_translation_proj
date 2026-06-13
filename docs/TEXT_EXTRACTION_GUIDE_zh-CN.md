# Merc Storia —— 文本解密 / 提取 / 重打包指南

剧情对话怎么存的、怎么解密、怎么解析 `MemoryPack` 二进制、怎么安全替换字符串、怎么重新打包成游戏可接受的 UnityFS bundle。

游戏环境：见 [`README_zh-CN.md`](README_zh-CN.md#游戏环境基准)（在 docs/ 同目录）。必须先做 CRC 修补（[`CRC_PATCH_GUIDE_zh-CN.md`](CRC_PATCH_GUIDE_zh-CN.md)），否则修改的 bundle 会被静默还原。

## 文本在哪里

| `AssetBundle/StandaloneWindows64/` 下的相对路径 | 内容 |
|---|---|
| `StoryMasterData/<hash>.bundle` | 剧情对话。约 4,008 个 bundle，每剧情一个。每个 bundle = 一个 TextAsset = 加密的 `StoryYamlData`。 |
| `Story/<hash>.bundle` | 单剧情元数据（BGM 键、资源引用）。翻译关注度较低。 |
| `MasterData/<hash>.bundle` | 游戏级 master data：章节名、剧情标题、物品 / 角色 / UI 字符串。同样加密，MemoryPack schema 不同。 |

还有不少日文字符串以裸 bytes 嵌在 bundle 集合的 MonoBehaviour blob 中 —— `jp_monobehaviours.txt` 列出全部 126 个。本指南不覆盖，因为它们用的是 IL2CPP 运行时里的用户自定义 schema，翻译需要做每个类的 typetree 工作。

## 加密

```
plaintext  = MemoryPack(StoryYamlData)
iv         = os.urandom(16)
key        = PBKDF2_HMAC_SHA256(password="2147483647",
                                salt="-2147483648",
                                iterations=1024, length=32)
ciphertext = iv || AES_CBC_PKCS7(key, iv, plaintext)
```

密码和盐都是**字符串字面量**（在 `dump.cs` 中搜 `2147483647` / `-2147483648` 找到 —— 作为 ASCII 常量存储，不是装箱 int）。1024 迭代（故意弱，客户端用）。一个全局密钥 —— 4,000 个 bundle 全部验证一致。

Python 参考：

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

每次重打包重新生成 IV —— 游戏不校验 IV 稳定性，bundle 层面只看密文长度稳定。

## MemoryPack 格式

`MemoryPack` 是 Cysharp 的 schema 驱动二进制序列化器。源生成器为每个 `[MemoryPackable] class` 烘焙一种二进制布局。**没有内嵌 schema** —— 解析时必须知道每个类的字段顺序。

### 基本类型线格式

| 类型 | 线格式 | 说明 |
|---|---|---|
| `byte` / `bool` | 1 字节 | bool：0 或 1 |
| `int32` / `float32` | 4 字节 LE | |
| `string`（UTF-8 模式） | `int32 header` + 载荷 | 见下文 |
| `T[]` / `List<T>` | `int32 count`（`-1` = null）+ 元素 | |
| `class` / `struct` | `byte memberCount`（`0xFF` = null）+ 字段 | 同时作 null 哨兵和版本键 |
| `Dictionary<K,V>` | `int32 count` + `(K, V)` 对 | |

### 字符串编码（UTF-8 模式）—— 最关键的坑

```
int32 header:
   = -1             → null 字符串
   =  0             → 空 ""
   = ~byteCount     → 后面跟：int32 charCount，byte[byteCount]（UTF-8）
                      （~ 是按位取反，不是负号：~10 = -11）
```

header 是 UTF-8 字节数的**按位取反**，不是负号。把 `-byteCount` 当成 `~byteCount` 会把紧接着的字段及之后整个缓冲全部错位。

`char_count` = .NET `string.Length` = UTF-16 code unit 数。BMP 文本（日文、中文、基本拉丁）下等于 Python 的 `len(s)`。BMP 外字符（罕见 emoji / CJK Extension B）：`len(s.encode('utf-16-le')) // 2`。

读写：

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

来自 `dump.cs` 与 `MemoryPack` 生成的 `Serialize` 方法交叉对照。摘要：

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

对话翻译只关心 `Speakers`（string[]）和 `Text`（string）。其他全部是每场景的元数据。

在 `merc_decrypt.py` 中以 `Reader` 类实现。子对象用 `obj_header()` 剥 `memberCount` 字节（`0xFF` = null）。

### 启发式扫描（无需 schema）

完整解析对游戏更新很脆（任何字段的可空性都可能变）。`extract_dialogue.py` 用启发式：

1. 逐字节走缓冲，找 `int32 key (0..10000)` 后紧跟 `byte 0x15`（= 21，`StorySceneYamlData.memberCount`）。
2. 每个匹配处尝试解析 `int32 SceneId` → `string[] Speakers`（count 0..10）→ `string Text`。
3. 任何字符串读失败或 `byteCount` 离谱，当作假阳性继续扫。

可以 100% 找回对话，假阳性几乎为零 —— `0x15` 锚点足够强，speakers / text 数组又通过 UTF-8 解码自校验。

## UnityFS 外壳

每个剧情 bundle = 一个 `UnityFS` archive，里面一个 `TextAsset`。磁盘上 raw `TextAsset` 布局：

```
int32  nameLength
byte[] name（UTF-8）
对齐到 4 字节边界
int32  scriptLength
byte[] script（密文：iv || AES_CBC ciphertext）
```

UnityPy 透明处理。`obj.read()` 返回 `TextAsset`，`.script` 是 raw 密文字节。重打包：`.script` 设为新密文（同长或变长均可）、`text_asset.save()`、`env.file.save(packer="lz4")`。

长度不变是最省事路径：AES-CBC 密文长度 = `iv (16) + ceil((plaintext + 1) / 16) * 16` 字节。新明文落在同样多的 16 字节块里 → 磁盘上 bundle 长度和偏移完全一致 → 下游不用修任何东西。

## 端到端管线

**A. 提取 → JSON。** `merc_storia_toolkit.py extract` 遍历每个 `StoryMasterData/`，解密、跑启发式扫描，为每个剧情写一份 JSON。`extract_metadata_full.py` 从 `MasterData/` 产出 `(chapter_id, story_id, title, episode_name)` 元组。合并后得到完整标注的翻译文件。约 4,000 剧情，约 120k 行对话，约 25 MB JSON，30 秒跑完。

**B. 翻译。** 本仓库不涵盖。遍历这些 JSON，把每条 `Text`（和 speaker）连同场景上下文交给 LLM 以保持一致性，再写回。角色名词表可从 `dump.cs` 采集。

**C. 重打包。** 每个剧情：

1. 加载 bundle、抽 TextAsset raw bytes、解密。
2. 用同一个启发式扫描器定位每个（`Speakers[*]`、`Text`）。
3. 在内存里构造 diff：按偏移排序的 `(start_offset, end_offset, new_string)` 三元组列表。
4. 重建明文 = `prev_end → start` 段原字节 + 新 MemoryPack 编码字符串 拼接。
5. 用全新随机 IV 重新加密。
6. `text_asset.script = new_ciphertext` → `text_asset.save()` → `env.file.save(packer="lz4")`。

第 4–5 步的诀窍：**不要**解析再重新序列化整棵 MemoryPack 树。我们只就地改字符串，其余全部按字节保留，所以每场景元数据布局即便随游戏更新而变化也不影响重打包。

Speaker 名同时出现在**两处**：场景的 `Speakers: string[]` 和在场角色的 `StorySceneCharacterYamlData.Key` / `DisplayName`。Toolkit 抽出的每剧情 JSON 同时暴露这两处 —— 编辑 JSON 后重打包时两处一并更新。如需在字节层面做全局字符串替换的样例（这种做法不依赖 extract/repack 流程），见 [`translate_1621.py`](../translate_1621.py)。

**D. 往返验证。** `verify_repack.py` 解密重打包后的 bundle，重跑启发式扫描，断言译文往返。`repack.py` 自带单 bundle 自检（`eb777f...`，对应剧情 1621）。

## 坑（按你最容易被坑到的顺序）

- **`~byteCount` 而非 `-byteCount`。** 错一位 ⇒ 从该位置起整个缓冲损坏。
- **`char_count` = UTF-16 单位，不是 UTF-8 字节，也不是 codepoint。** BMP 文本下不会咬到你。
- **PBKDF2 密码 / 盐是字符串字面量**，不是装箱 int。盐看起来像负数是因为有人复制了 `int.MinValue.ToString()`。
- **空 `Speakers` 是合法的** —— 旁白行 `Speakers: []` + 非 null `Text`。扫描器要接受 `count = 0`。
- **TextAsset 名字有 4 字节对齐填充** —— UnityPy 通过 `obj.read()` 透明处理，别去读 raw bytes。
- **AES-CBC IV = 16 字节全新随机。** 复用原 IV 在技术上有效但会触发泄漏检测遥测（不会有服务器响应，但 ProcMon 里看得到）。用 `os.urandom(16)`。
- **`env.file.save(packer="lz4")`，不是 `"lz4hc"`。** UnityPy 自带的 lz4hc packer 在 Unity 6000.x 上 block-info trailer 处理有误。
- **有些 bundle 包含多个 TextAsset**（特别是 `MasterData/`）。要遍历所有 `obj.type.name == "TextAsset"`，别只取第一个。
- **启发式扫描胜过完整解析。** 锚点 `(int32 key 0..10000) + byte 0x15`，逐字段自洽校验。快、稳、对游戏更新友好。

## 试过但不行的

- **MessagePack 取代 MemoryPack。** `StoryYamlData` 首字节 `0x02` 同时是 MessagePack 的合法 `positive fixint 2` —— 走了很长弯路。Cysharp 的 MemoryPack 才是实际序列化器。
- **完整 schema 驱动的 (反)序列化器。** 写完弃用：每次游戏更新都可能挪字段可空性或类型。启发式就地替换对游戏更新友好。完整解析器保留在 `merc_decrypt.py` 仅供参考 / 调试。
- **不重编长度前缀的字符串替换。** 朴素字节级 find/replace 会破坏长度 header。务必重新发出 `(int32 ~byteCount, int32 charCount, utf8…)`。
- **指望 AES 密钥按 bundle 不同。** 是单个全局常量。

## 文件参考

| 路径 | 用途 |
|---|---|
| `merc_storia_toolkit.py` | 统一 CLI：`extract` / `extract-story` / `extract-misc` / `repack` / `repack-story` / `repack-misc` / `test-repack` |
| `merc_decrypt.py` | 参考：解密 + 完整 `StoryYamlData` 的 MemoryPack `Reader` |
| `translate_1621.py` | 完整示例：剧情 1621 → 中文 |
| `deploy_bundles.py` | 把 `repacked_bundles/` 推回实时缓存（自动优先游戏目录，回退 LocalLow） |
| `bundle_cache.py` | 把 LocalLow CDN 缓存打包进游戏目录，便于分发 |
| `jp_monobehaviours.txt` | 含日文字符串的所有 MonoBehaviour 清单 |

## 外部链接

- [Cysharp/MemoryPack](https://github.com/Cysharp/MemoryPack) —— 游戏明文用的二进制序列化器；UTF-8 模式的字符串布局在其 README 中有文档说明
- [UnityPy](https://github.com/K0lb3/UnityPy) —— bundle 读写库
- [pyca/cryptography](https://cryptography.io/) —— AES-256-CBC + PBKDF2，用于派生 AES key
- [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) —— 用来确认 `RijndaelManaged` 调用中的 AES 密码 / 盐常量

之后跑 [`FONT_REPLACEMENT_GUIDE_zh-CN.md`](FONT_REPLACEMENT_GUIDE_zh-CN.md) 把日文字体替换成支持你的目标书写系统的字体。
