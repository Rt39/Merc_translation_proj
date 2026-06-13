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
- **MonoBehaviour pathID**：`27`，字节偏移 `173728`，大小 `630328`
- 同名 `RocknRollStd SDF` —— 与 bundle 副本**逐字节相同，除了 49 字节 `m_Script` PPtr**。
- 引用 `resources.assets` 中 pid `10` 的 atlas Texture2D。
- **UnityPy 用 typetree 无法解析** —— `TMP_FontAsset` 的 MonoScript 类没在这个序列化文件里注册（`Expected to read 630328 bytes, but only read 48 bytes`）。raw bytes 可通过 `obj.get_raw_data()` 拿到。
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

## 探索路线（怎么走到这里的）

1. ProcMon：标题和剧情渲染都对应 `resources.assets.resS` 偏移 8,690,576 的同一次 16 MB 读 → atlas 是共享的。
2. 换完 bundle 字体后剧情正常但菜单不正常 → 菜单采样共享 atlas 时使用了**过期的字符 → 字形矩形映射**。
3. 在所有游戏文件中 grep 字节 `RocknRollStd SDF`：
   - `resources.assets` —— **2 处命中**
   - `sharedassets4.assets` —— 8 处（全是材质，没有字体 asset）
   - `catalog.bin` —— 6 处（Addressables 引用）
4. `resources.assets` 的两处命中 = (a) Texture2D 的 `m_Name`，(b) 一个完整的 `TMP_FontAsset` MonoBehaviour，由于 MonoScript 绑定不可用 UnityPy 静默跳过了。**第二处就是菜单的映射。**

## 修补策略 —— 三个正交修补，缺一不可

### Patch A —— `resources.assets.resS` 中的 atlas 像素

在偏移 8,690,576 处覆盖 16,777,216 字节为新的 Alpha8 像素块。文件大小不变 → 不用修改 header / asset 表。

### Patch B —— bundle 中的字体 asset（剧情渲染器）

对 bundle 的 `RocknRollStd SDF` MonoBehaviour 用 `UnityPy.save_typetree`。从源 TMP 字体移植 `m_CharacterTable`、`m_GlyphTable`、查找字典、used / free rect 列表。**保留** `m_FaceInfo`、atlas Texture2D 引用、atlas 尺寸、render mode、fallback 字体 asset 表。保存 bundle（LZ4）。

同时把 bundle archive 内 `.resS` 的两个 16 MB 槽都覆盖为新 atlas 字节。

**保留 `m_FaceInfo` 至关重要。** UI 是按原版 `m_LineHeight = 64.0`（2× PointSize）排版的。新烤的 TMP 字体（例如 LogoSCLongZhuTi）会有 TTF 自然的 `m_LineHeight ≈ 39.68`（约 1.24× PointSize）。直接移植会把所有多行对话框 / 菜单框挤扁，文字重叠。当前脚本的 `transplant_keys_into` 显式枚举要复制的 key，并 assert `m_FaceInfo` 不在列表里。

### Patch C —— `resources.assets` 中的隐藏字体 asset ⚠️

这是互联网上别的人漏掉的部分。这个 MonoBehaviour **无法**通过 typetree 修改（UnityPy 没有 schema）。因为 Patch B 没动 `m_FaceInfo`，那段字节在原版和修补版之间相同，字节 diff 在那里为空，`m_LineHeight = 64.0` 在 `resources.assets` 中自动保留。用**字节 diff 戏法**：

1. 在内存中对 bundle 字体 asset 的新副本做同一个字形表移植。
2. 序列化那个 bundle（`env.file.save(packer="lz4")`），再加载提取改写后的 630,328 字节 MonoBehaviour blob → `patched_raw`。
3. 把 `patched_raw` 与原 bundle 字体 asset 字节 diff → 变化的字节偏移列表（都在字形表区域）。
4. 读原 `resources.assets` 的 pid=27 字节。两个 MonoBehaviour 起始字节完全相同（除了 header 附近的 49 字节 `m_Script` PPtr），所以 diff 偏移完美对齐。
5. **就地**只覆盖 `resources.assets` 里那些字节。文件大小不变 → 不用修 SerializedFile header 或 object table。

最终改动约 100 KB 的字形矩形 / 度量数据。结构、PPtr 引用、m_Script 绑定、其他每个字段都不动。

结果：两个字体 asset 都指向 atlas 中同样的位置，atlas 在那些位置有了新像素，两个渲染器都显示新字体。

## 复现

前置：
- 一个已构建的 TMP 字体 bundle（例如 `logofont.bundle`），含一个 `TMP_FontAsset` MonoBehaviour + 一张 4096×4096 Alpha8 SDF atlas Texture2D。**烤这个 bundle 是流程里唯一需要手动用 Unity Editor 完成的一步** —— 见下方 [构建源字体 bundle](#构建源字体-bundle)。
- 已做 CRC 修补。
- 三个目标文件 `84ece16f...bundle`、`resources.assets`、`resources.assets.resS` 的 `.bak`（swap 脚本会在缺失时自动创建）。

应用：

```bash
uv run font_swap.py "<path>/logofont.bundle"
```

脚本处理 A + B + C；若 `$MERCSTORIA_MIRROR_DIR`（默认 `D:\mercstoria\`）存在，会镜像一份。

## 构建源字体 bundle

字体替换需要消费一个 Unity asset bundle，里面要有 1 个 `TMP_FontAsset` MonoBehaviour 和 1 张 4096×4096 Alpha8 SDF atlas Texture2D。这一步没法自动化 —— Unity Editor 是生成 TMP 字体 asset 的官方路径，SDF atlas 的生成也依赖 Editor 的 TMP 包。下面是从一台干净的 Unity 开始的完整步骤。

### 你需要的

| 工具 | 版本 / 来源 | 用途 |
|---|---|---|
| [Unity Hub](https://unity.com/download) | 最新 | 安装并激活 Editor |
| Unity Editor | **6000.0.58f2** | 必须与游戏一致，同 TMP 包版本 → typetree 形状一致 |
| [TextMeshPro](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/index.html) 包 | 6000.x 自带（隶属 `com.unity.ugui`） | 提供 `TMP_FontAsset.CreateFontAsset` + `TryAddCharacters` API |
| 源字体 | 任意覆盖目标文字的 `.ttf` / `.otf` | CJK 推荐 [LogoSC Long Zhu](https://github.com/atelier-anchor/smiley-sans)，多语种推荐 [Noto Sans](https://fonts.google.com/noto) |
| `target_chars.txt` | 逗号分隔的十进制 codepoint | 要烤进 atlas 的字符集 |

Unity 版本不能换。MonoBehaviour 布局完全取决于 TMP 包版本 —— 用 TMP 4.x 烤出来的 typetree 字段顺序就不一样了，而 Patch C 的字节 diff 之所以能成立，正是因为源和目标的字节布局完全一致。

### 步骤 1：建项目

1. **Unity Hub → New project** → **3D (Built-In Render Pipeline)** 模板。
2. 选 **6000.0.58f2**。其他 6000.x 版本可能也行，但本项目的 RVA 是在 0.58f2 上验证过的，换版本前请自己核对。
3. 项目名用 ASCII。路径里别带日文 —— TMP 字体烘焙器历史上对非 ASCII 路径有过问题。
4. 打开后：**Window → TextMeshPro → Import TMP Essential Resources**，默认 shader 和动态 atlas 材质都要靠它。

### 步骤 2：放字体和字符表

在 `Assets/` 下建：

```
Assets/
├── <your-font>.ttf                 拷贝进来的源字体
├── target_chars.txt                逗号分隔的十进制 codepoint
├── Editor/
│   └── RegenAndBuildFont.cs        烘焙脚本（见下）
└── AssetBundles/                   输出目录（脚本会建）
```

`target_chars.txt` 是单行的十进制 codepoint，逗号分隔。一般做法是把游戏原版字符集和你的目标文字集合并：

```
# 推荐配方：
#   * 游戏原 RocknRollStd SDF 的 7,007 日文字符
#   * GB2312（约 6,763 字符），覆盖简中
#   * 翻译草稿里出现过的所有字
# 合起来大约 10,000–11,000 字符。PointSize 28 在 4096×4096 atlas 上能装下约
# 10,631；32pt 上限约 8,800。
```

构造字符集的 `export_chars.py` 留给读者；权威参考是 [TMP_FontAsset.HasCharacters](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/api/TMPro.TMP_FontAsset.html#TMPro_TMP_FontAsset_HasCharacters_System_String_) 文档。

### 步骤 3：烘焙脚本

粘贴到 `Assets/Editor/RegenAndBuildFont.cs`：

```csharp
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
using TMPro;

public static class RegenAndBuildFont
{
    // 调用方式：Unity.exe -batchmode -executeMethod RegenAndBuildFont.RegenAndBuild
    public static void RegenAndBuild()
    {
        // ----- 1. 找到 Assets/ 里的 TTF ---------------------------------------
        var ttfPath = Directory
            .GetFiles("Assets", "*.ttf", SearchOption.TopDirectoryOnly)
            .FirstOrDefault();
        if (ttfPath == null)
            throw new System.Exception("No .ttf found in Assets/");

        var ttf = AssetDatabase.LoadAssetAtPath<Font>(ttfPath);
        if (ttf == null)
            throw new System.Exception($"Failed to load font at {ttfPath}");

        // ----- 2. 烘焙空字体 asset --------------------------------------------
        // 这些常量必须和游戏原 RocknRollStd SDF 一致，否则 Patch C 的字节 diff 对不齐。
        var fontAsset = TMP_FontAsset.CreateFontAsset(
            font:             ttf,
            samplingPointSize: 28,
            atlasPadding:      5,
            renderMode:        GlyphRenderMode.SDFAA_HINTED,
            atlasWidth:        4096,
            atlasHeight:       4096,
            atlasPopulationMode: AtlasPopulationMode.Dynamic,
            enableMultiAtlasSupport: false);

        // ----- 3. 添加目标字符 ------------------------------------------------
        var chars = File.ReadAllText("Assets/target_chars.txt")
            .Split(',')
            .Select(s => s.Trim())
            .Where(s => s.Length > 0)
            .Select(int.Parse)
            .Select(System.Char.ConvertFromUtf32)
            .ToArray();
        var charSet = string.Concat(chars);
        if (!fontAsset.TryAddCharacters(charSet, out string missing))
            Debug.LogWarning($"[bake] missing {missing.Length} chars: {missing}");

        // ----- 4. 冻结 atlas 为 Static --------------------------------------
        fontAsset.atlasPopulationMode = AtlasPopulationMode.Static;
        EditorUtility.SetDirty(fontAsset);
        AssetDatabase.CreateAsset(fontAsset, "Assets/logofont.asset");
        AssetDatabase.SaveAssets();

        // ----- 5. 给 asset bundle 打标签 --------------------------------------
        var importer = AssetImporter.GetAtPath("Assets/logofont.asset");
        importer.assetBundleName = "logofont.bundle";

        var atlasTex = fontAsset.atlasTextures.FirstOrDefault();
        if (atlasTex != null)
        {
            var atlasPath = AssetDatabase.GetAssetPath(atlasTex);
            AssetImporter.GetAtPath(atlasPath).assetBundleName = "logofont.bundle";
        }

        // ----- 6. 以 StandaloneWindows64 格式构建 bundle ----------------------
        Directory.CreateDirectory("Assets/AssetBundles");
        BuildPipeline.BuildAssetBundles(
            "Assets/AssetBundles",
            BuildAssetBundleOptions.ChunkBasedCompression, // → LZ4，和游戏一致
            BuildTarget.StandaloneWindows64);

        Debug.Log("[bake] OK — Assets/AssetBundles/logofont.bundle");
    }
}
```

### 步骤 4：构建

命令行无窗口跑：

```powershell
& "C:\Program Files\Unity\Hub\Editor\6000.0.58f2\Editor\Unity.exe" `
    -batchmode -nographics -quit `
    -projectPath "<项目绝对路径>" `
    -executeMethod RegenAndBuildFont.RegenAndBuild `
    -logFile build.log
```

或者在 Editor 里挂个菜单项直接点。

输出：`<项目>/Assets/AssetBundles/logofont.bundle`，这就是 `font_swap.py` 要的输入。

### 步骤 5：验证 bundle

跑 `font_swap.py` 前先 sanity-check：

```bash
uv run python -c "
import UnityPy
env = UnityPy.load(r'<项目>/Assets/AssetBundles/logofont.bundle')
fonts   = [o for o in env.objects if o.type.name == 'MonoBehaviour']
atlases = [o for o in env.objects if o.type.name == 'Texture2D']
print(f'fonts: {len(fonts)}, atlases: {len(atlases)}')
for f in fonts:
    tt = f.read_typetree()
    if 'm_CharacterTable' in tt:
        fi = tt.get('m_FaceInfo', {})
        print(f'  chars={len(tt[\"m_CharacterTable\"])}'
              f' glyphs={len(tt[\"m_GlyphTable\"])}'
              f' line_height={fi.get(\"m_LineHeight\")}'
              f' point_size={fi.get(\"m_PointSize\")}')
for t in atlases:
    d = t.read()
    print(f'  atlas {d.m_Width}x{d.m_Height} fmt={d.m_TextureFormat}')
"
```

预期：

```
fonts: 1, atlases: 1
  chars=10631 glyphs=10631 line_height=39.something point_size=28
  atlas 4096x4096 fmt=Alpha8
```

如果 `chars` 远小于预期，多半是 `target_chars.txt` 里大部分 codepoint 在源 TTF 里没有 glyph，`TryAddCharacters` 就跳过了 —— 换一个覆盖更广的字体，或者分多次 `TryAddCharacters` 调用、接受较小子集。

### 步骤 6：替换

```bash
uv run font_swap.py "<项目>/Assets/AssetBundles/logofont.bundle"
```

会自动处理 A + B + C。启动游戏，确认剧情对话（Patch B）和标题画面 / 章节列表（Patch C）都用了新字体。

## 试过但不行的

- **动态 atlas 重生成假设** —— `m_AtlasPopulationMode = 0`（Static）排除。
- **LocalLow CDN 缓存覆盖** —— 扫了 38,331 个下载 bundle，没有字体 asset overlay。
- **通过 NBSP 槽走 TMP fallback 链** —— 把小 NBSP bundle（`08c96b...`）替换为 LogoSC 内容。bundle 字体 asset 的 fallback 链生效，但 `resources.assets` 里隐藏字体 asset 有它自己的 fallback 表，指向 `sharedassets` 里的 stub（Arial SDF / Arial Unicode SDF —— 3 个和 11 个字符）。fallback 触不到 LogoSC，菜单不变。
- **MelonLoader / UnityExplorer / BepInEx 6.0.0-pre** —— Il2CppInterop 在 Unity 6000.0.58f2 上崩溃（`Class_FromIl2CppType_Hook` 中 `AccessViolationException`）；Unhollower 生成不出 IL2CPP 代理。截至撰写时这个 Unity 版本没有可用的运行时工具。
- **对 pid=27 用 `UnityPy.save_typetree`** —— typetree 回退到 48 字节最小 schema，文件损坏。Patch C 的字节 diff 绕过了这个问题。
- **用 `set_raw_data` + `env.file.save()` 替换 pid=27 整体** —— 大小变化时 SerializedFile object table / 跨对象 PPtr 没被正确修复 → 启动黑屏。务必只改字形表区域以保持原大小。

## 文件参考

| 路径 | 用途 |
|---|---|
| `font_swap.py` | 通用替换 —— 一次性应用 Patches A + B + C |

## 外部链接

- [UnityPy](https://github.com/K0lb3/UnityPy) —— Unity 资源读写库；Patch B / C 都靠它
- [TextMeshPro 包文档](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/index.html) —— `TMP_FontAsset.CreateFontAsset`、`TryAddCharacters`
- [Font Asset Creator 工作流](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/FontAssetsCreator.html) —— Unity Editor GUI 替代烘焙脚本
- [Noto fonts](https://fonts.google.com/noto) —— 免费 OFL 协议，CJK / 拉丁 / 阿拉伯文等全覆盖
- [Smiley Sans / LogoSC](https://github.com/atelier-anchor/smiley-sans) —— 本项目参考构建使用的宽覆盖 CJK 字体
