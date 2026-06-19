# Merc Storia —— 字体替换指南

游戏怎么加载 UI 字体，以及把内置 `RocknRollStd SDF` 替换为任意 TMP 字体的修补管线。

游戏环境：见 [`README_zh-CN.md`](README_zh-CN.md#游戏环境基准)（在 docs/ 同目录）。必须先做 CRC 修补（[`CRC_PATCH_GUIDE_zh-CN.md`](CRC_PATCH_GUIDE_zh-CN.md)），否则修改的 bundle 会被静默还原。

## 解剖：字体实际存在**三**个地方

这是核心发现。只修补其中一个会得到部分乱码。

### 1. Bundle 中的字体 asset —— 剧情画面

- **文件**：`StreamingAssets/aa/StandaloneWindows64/84ece16f121defbfc5b83acb86f5870c.bundle`
- **MonoBehaviour pathID**：`6189425675716077201`，名 `RocknRollStd SDF`
- 引用 bundle 内 pathID `-4881587269468215663` 的 atlas Texture2D。
- UnityPy 可以完整解析（MonoScript 引用在 bundle 内部可解析）。
- 剧情对话和剧情标题卡片消费它。

### 2. `resources.assets` 中的隐藏字体 asset —— 标题 / 菜单 / 主页

- **文件**：`メルストM_Data/resources.assets`
- **MonoBehaviour pathID**：`27`，原始大小 630,328 字节（Patch C 后会增大；SerializedFile 重存时下游 offset 自动跟着调整）。
- 同名 `RocknRollStd SDF`，字段布局与 bundle 副本一致 —— 原版只在 49 字节 `m_Script` PPtr 处不同。
- 引用 `resources.assets` 中 pid `10` 的 atlas Texture2D。
- **本文件自身没有 `TMP_FontAsset` 的 TypeTree** —— UnityPy 解析时报 `Expected to read 630328 bytes, but only read 48 bytes`。绕开办法：把 bundle 副本的 `serialized_type.nodes` 传给 `read_typetree` / `save_typetree`（见 Patch C）。
- **标题画面菜单、剧情列表卡、主页文本**消费它。只改 bundle 副本会让这份保持旧版 —— 这就是"剧情正常但菜单 / 标题乱码"的根因。

### 3. Atlas 像素 —— 16 MB Alpha8 4096×4096

磁盘上有三份副本，运行时只采样第一份（直接覆盖测试验证）。另外两份必须保持一致，因为 Texture2D 的 `m_StreamData` 会校验尺寸。

| 位置 | 偏移 | 长度 | 备注 |
|---|---|---|---|
| `resources.assets.resS` | 8,690,576 | 16,777,216 | 菜单和剧情**实际采样**（ProcMon 确认）。 |
| Bundle archive `.resS`（RocknRollStd 槽） | 65,536 | 16,777,216 | 被 bundle Texture2D `m_StreamData` 引用。保持同步。 |
| Bundle archive `.resS`（RocknRollOne 槽） | 16,842,752 | 16,777,216 | 被 6 个 `RocknRollOne (...)` 材质引用。保持同步。 |

### 材质 —— 不用动

bundle 里有 12 个材质（`RocknRollStd SDF (Story)`、`RocknRollOne (Brown Outline)` 等）。每个 `_MainTex` 已经指向三张 atlas 之一。atlas 像素改完后，每个材质都会自动渲染新字体。

## 修补策略 —— 三个正交修补，缺一不可

### Patch A —— `resources.assets.resS` 中的 atlas 像素

在偏移 8,690,576 处覆盖 16,777,216 字节为新的 Alpha8 像素块。文件大小不变 → 不用修改 header / asset 表。

### Patch B —— bundle 中的字体 asset（剧情渲染器）

对 bundle 的 `RocknRollStd SDF` MonoBehaviour 用 `UnityPy.save_typetree`。从源 TMP 字体移植 `m_CharacterTable`、`m_GlyphTable`、查找字典、used / free rect 列表。**保留** `m_FaceInfo`、atlas Texture2D 引用、atlas 尺寸、render mode、fallback 字体 asset 表。保存 bundle（LZ4）。

同时把 bundle archive 内 `.resS` 的两个 16 MB 槽都覆盖为新 atlas 字节。

**保留 `m_FaceInfo` 至关重要。** UI 是按原版 `m_PointSize = 32`、`m_LineHeight = 64.0`（2× PointSize）排版的。新烤的 TMP 字体（例如 LogoSCLongZhuTi）会有 TTF 自然的 `m_LineHeight ≈ 39.68`（约 1.24× PointSize）。直接移植会把所有多行对话框 / 菜单框挤扁，文字重叠。当前脚本的 `transplant_keys_into` 显式枚举要复制的 key，并 assert `m_FaceInfo` 不在列表里。

**推论：源字体必须按 `samplingPointSize = 32` 烘焙** —— 与原 `m_FaceInfo.m_PointSize` 相同。TMP 渲染每个字形时 `quadSize = glyphRect × 请求字号 / m_FaceInfo.m_PointSize`。如果字形表是 28pt 烤的、但保留的 `m_FaceInfo.m_PointSize` 是 32，每个字形显示出来就是预期尺寸的 28/32 ≈ **87.5%**。按 32 烤才能让字形矩形与运行时缩放对齐。32pt 时 4096² atlas 上限约 8,800 字符（28pt 时约 10,631），所以字符集要相应裁剪。

### Patch C —— `resources.assets` 中的隐藏字体 asset ⚠️

`resources.assets` 是 IL2CPP release `SerializedFile`，没有内嵌 `TMP_FontAsset` 的 TypeTree，UnityPy 无法直接解析 pid=27。但 bundle 那份字体 asset *是带 TypeTree* 的（asset bundle 自带类型信息）—— 借过来用：

```python
nodes = bundle_font_obj.serialized_type.nodes
tt    = pid27.read_typetree(nodes)
transplant_keys_into(tt, source_font_tt)        # 与 Patch B 同一组 key
pid27.save_typetree(tt, nodes)
env_resources.file.save()                       # writer 自动修 object table + offset
```

`m_AtlasTextures`（pid=10）、`m_Material`（pid=2）、fallback 表、`m_FaceInfo` 都不在 transplant key 集里，所以原 PPtr 和度量都保留。pid=27 从 7,007 字扩到 7,656 字（807,432 → 860,480 字节），SerializedFile writer 自动修后续对象的 `byte_starts`，无需手动算偏移。

结果：两个字体 asset 共用同一组 atlas 坐标，且现在都覆盖完整 7,656 字 —— 包括原 `RocknRollStd SDF` 没有的简体专用字（如 线/汉/产，过去在标题 / 主页 / 章节列表会回退或乱码）。

## 应用

前置：

- 已构建的 TMP 字体 bundle（例如 `logofont.bundle`），含一个 `TMP_FontAsset` MonoBehaviour + 一张 4096×4096 Alpha8 SDF atlas Texture2D。仓库根目录已经带了一份预构建的 `logofont.bundle`，只有需要换源 TTF 或字符集时才需要自己重烤（见 [构建源字体 bundle](#构建源字体-bundle)）。
- 已做 CRC 修补。
- 三个目标文件 `84ece16f...bundle`、`resources.assets`、`resources.assets.resS` 的 `.bak`（swap 脚本会在缺失时自动创建）。

```bash
uv run -m mercstoria font-swap "<path>/logofont.bundle"
```

脚本处理 A + B + C；若 `$MERCSTORIA_MIRROR_DIR`（默认 `D:\mercstoria\`）存在，会镜像一份。

## 构建源字体 bundle

字体替换需要消费一个 Unity asset bundle，里面要有 1 个 `TMP_FontAsset` MonoBehaviour 和 1 张 4096×4096 Alpha8 SDF atlas Texture2D。这一步没法自动化 —— Unity Editor 是生成 TMP 字体 asset 的官方路径，SDF atlas 的生成依赖 Editor 的 TMP 包。

由前文修补策略推出两个**不可调**的参数：

- **Unity 6000.0.58f2** —— bundle 字体的 TypeTree 被借去解析 `resources.assets` pid=27，所以两份 asset 必须出自同一个 TMP 包；不同的 Unity / TMP 组合可能布局不一致。
- **`samplingPointSize = 32`** —— 必须等于保留下来的 `m_FaceInfo.m_PointSize`，否则字形以错误的比例渲染（见 Patch B）。

烤的步骤大致是：用 `TMP_FontAsset.CreateFontAsset(font, samplingPointSize: 32, atlasPadding: 5, GlyphRenderMode.SDFAA_HINTED, atlasWidth: 4096, atlasHeight: 4096, ...)` 建空字体，`TryAddCharacters(targetChars)` 灌字符，`atlasPopulationMode = Static` 冻结，把 asset 和 atlas Texture 标上同一个 `assetBundleName = "logofont.bundle"`，最后 `BuildPipeline.BuildAssetBundles(..., BuildAssetBundleOptions.ChunkBasedCompression, BuildTarget.StandaloneWindows64)` 出包。具体 API 见：

- [TextMeshPro 包文档](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/index.html) —— `TMP_FontAsset.CreateFontAsset`、`TryAddCharacters`、`HasCharacters`
- [Font Asset Creator 工作流](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/FontAssetsCreator.html) —— Unity Editor 的 GUI 替代方案
- [Unity AssetBundle 工作流](https://docs.unity3d.com/Manual/AssetBundles-Workflow.html) —— 构建 `StandaloneWindows64` LZ4 bundle

### 字符表

`target_chars.txt` 是单行字面字符（UTF-8，无分隔符），`TryAddCharacters` 直接吃。由 [`scripts/export_chars.py`](../scripts/export_chars.py)（CLI：`mercstoria export-chars`）从 `tools/` 下的权威字表生成。脚本将字源分为两类：

- **REQUIRED（必带，永不裁剪）：** ASCII ∪ CJK 标点 ∪ 平假名 ∪ 片假名 ∪ 全/半角符号 ∪ 常用漢字 2,136（日）∪ **通用规范汉字表一级字 3,500** ∪ 仓库根目录下所有 `translate_*.py` 中的每一个码位 ∪（开启 `--include-corpus` 时）`extracted_data/**/*.json` 中的所有字符。
- **FILL（按 7000hanzi 频率序填到上限为止）：** 通用规范汉字表二级字 3,000 ∪ qweyouke "7000" 简中字表。

旧版只按频率截到 top-5,500，导致 ~499 个一级字（赛 / 翼 / 羹 …）被静默丢弃。新版用必带 + 填充的两段式：一级字保证全收，atlas 满了也不掉；若任何一级字未能进入最终集合，脚本以"不变量违反"错误退出。当前输出 7,800 字，刚好打满 atlas 上限。

**重要：** 译文中用到但未在图集中的字会在游戏里显示为随机字形碎片 —— Patch A 清掉了该码位原矩形位置的图集像素，新字符 / 字形表又指向从未烤过的坐标。译文文件扫描正是为了这一点 —— 翻译集扩张时，在仓库根目录新增 `translate_*.py` 然后重跑 `mercstoria export-chars` 即可。

### 验证 bundle

跑 `font-swap` 之前用 UnityPy 简单 sanity-check：1 个 `MonoBehaviour` + 1 个 `Texture2D`，`len(m_CharacterTable) ≈ len(m_GlyphTable) ≈ 7800`，`m_FaceInfo.m_PointSize == 32`，atlas `4096×4096 Alpha8`。若 `m_CharacterTable` 远小于预期，多半是源 TTF 缺这些字形 —— 换覆盖更广的字体。

## 试过但不行的

- **动态 atlas 重生成假设** —— `m_AtlasPopulationMode = 0`（Static）排除。
- **LocalLow CDN 缓存覆盖** —— 扫了 38,331 个下载 bundle，没有字体 asset overlay。
- **通过 NBSP 槽走 TMP fallback 链** —— 把小 NBSP bundle（`08c96b...`）替换为 LogoSC 内容。bundle 字体 asset 的 fallback 链生效，但 `resources.assets` 里隐藏字体 asset 有它自己的 fallback 表，指向 `sharedassets` 里的 stub（Arial SDF / Arial Unicode SDF —— 3 个和 11 个字符）。fallback 触不到 LogoSC，菜单不变。
- **MelonLoader / UnityExplorer / BepInEx 6.0.0-pre** —— Il2CppInterop 在 Unity 6000.0.58f2 上崩溃（`Class_FromIl2CppType_Hook` 中 `AccessViolationException`）；Unhollower 生成不出 IL2CPP 代理。截至撰写时这个 Unity 版本没有可用的运行时工具。
- **不传 nodes 调 `UnityPy.save_typetree(pid=27)`** —— 回退到 48 字节最小 schema，文件损坏。必须显式传 bundle 的 `serialized_type.nodes`。
- **用 `set_raw_data` 替换 pid=27 但不重存 SerializedFile** —— 早期版本试过；object table 没被修复，启动黑屏。`save_typetree` 后跟 `env.file.save()` 才会修。

## 文件参考

| 路径 | 用途 |
|---|---|
| `scripts/font_swap.py` | 通用替换 —— 一次性应用 Patches A + B + C（`mercstoria font-swap <bundle>`） |
| `scripts/export_chars.py` | 为 TMP 字体烘焙生成 `target_chars.txt`（`mercstoria export-chars`） |
| `logofont.bundle`（仓库根目录） | 预构建的源字体 bundle，可直接喂给 `font-swap` |

## 外部链接

- [UnityPy](https://github.com/K0lb3/UnityPy) —— Unity 资源读写库；Patch B / C 都靠它
- [TextMeshPro 包文档](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/index.html) —— `TMP_FontAsset.CreateFontAsset`、`TryAddCharacters`
- [Font Asset Creator 工作流](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/FontAssetsCreator.html) —— Unity Editor GUI 替代烘焙脚本
- [Unity AssetBundle 工作流](https://docs.unity3d.com/Manual/AssetBundles-Workflow.html) —— 构建 `StandaloneWindows64` LZ4 bundle
- [Noto fonts](https://fonts.google.com/noto) —— 免费 OFL 协议，CJK / 拉丁 / 阿拉伯文等全覆盖
- [Smiley Sans / LogoSC](https://github.com/atelier-anchor/smiley-sans) —— 本项目参考构建使用的宽覆盖 CJK 字体
