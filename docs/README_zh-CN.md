# 梅露可物语（メルクストーリア） — 翻译项目

完整工具链，用于将 **梅露可物语 - 愈术师与心之旋律 -（メルクストーリア - 癒術士と心の旋律 -）** Steam 版本翻译为非日语。游戏自身不带 i18n，本仓库的每一步都是逆向工程得来的。

五项相互独立的技术成果，合在一起即可完成完整翻译：

1. **CRC 绕过** —— 4 处 `xor edx, edx` 修补在 `GameAssembly.dll` 中，修改后的 bundle 不会再被静默重下。见 [`CRC_PATCH_GUIDE_zh-CN.md`](CRC_PATCH_GUIDE_zh-CN.md)。
2. **文本解密 / 提取 / 重打包** —— 大约 4,000 个剧情 bundle 的完整管线（AES-256-CBC + MemoryPack）。见 [`STORY_BUNDLE_GUIDE_zh-CN.md`](STORY_BUNDLE_GUIDE_zh-CN.md)。
3. **字体替换** —— 同时修补字体实际所在的三个物理位置，TMP 字体可任意替换。见 [`FONT_REPLACEMENT_GUIDE_zh-CN.md`](FONT_REPLACEMENT_GUIDE_zh-CN.md)。
4. **离线模式** —— 8 处修补（Steam 绕过 + Cysharp 证书跳过 + 纯文件读取 GetAsync），让安装包无需联网、无需 Steam。见 [`OFFLINE_MODE_GUIDE_zh-CN.md`](OFFLINE_MODE_GUIDE_zh-CN.md)。
5. **自包含启动器** —— 替换 `メルストM.exe` 的单击启动器，把 NTFS junction 创建步骤打进了 EXE 自身。见 [`../launcher/README_zh-CN.md`](../launcher/README_zh-CN.md)。

## 游戏环境（基准）

| 项目 | 取值 |
|---|---|
| 引擎 | Unity 6000.0.58f2，IL2CPP，Windows x64 |
| 平台 | Steam |
| 游戏目录 | `<Steam>/steamapps/common/メルクストーリア - 癒術士と心の旋律 -/` |
| 启动器 | `メルストM.exe` + `メルストM_Data/`（保持不动；汉化版会在旁边新增 `メルストM_chs.exe`） |
| IL2CPP 二进制 | `GameAssembly.dll`（约 78 MB） |
| IL2CPP 元数据 | `<exe>_Data/il2cpp_data/Metadata/global-metadata.dat` |
| Addressables 目录 | `<exe>_Data/StreamingAssets/aa/catalog.bin`（Addressables 2.3.7） |
| CDN 主机 | `https://assets.mercstoria-memorial.hekk.org/AssetBundle/StandaloneWindows64/<Category>/<file>` |
| LocalLow 缓存 | `%USERPROFILE%/AppData/LocalLow/jp_co_happyelements/メルストM/AssetBundle/StandaloneWindows64/<Category>/<file>` |
| 玩家日志 | `%USERPROFILE%/AppData/LocalLow/jp.co.happyelements/メルストM/Player.log`（*点号，不是下划线*） |
| 资源格式 | UnityFS，LZ4(HC) |
| 文本格式 | TextAsset → AES-256-CBC → MemoryPack（UTF-8 模式） |
| 字体格式 | TMP SDF（4096×4096 Alpha8 atlas） |

工具会自动识别游戏安装目录。如需覆盖，运行任何脚本前设置 `MERCSTORIA_GAME_DIR=<路径>`。所有派生路径在 [`../mercstoria/config.py`](../mercstoria/config.py) 中一处定义。

## 工作流（完整翻译）

整个工具链折成两条命令：

```
   原版游戏安装
   ─────────────►  mercstoria setup
                     │
                     ├── 1. patch-crc           （4 处 CRC 绕过）
                     ├── 2. patch-offline       （8 处：Steam 绕过 + 证书跳过 + GetAsync）
                     ├── 3. font-swap           （atlas + bundle + 隐藏字体，复用 logofont.bundle）
                     ├── 4. extract             （4,008 剧情 + 15 master bundle → extracted_data/）
                     ├── 5. bundle-cache        （LocalLow → <game>/AssetBundle）
                     └── 6. deploy launcher     （把 launcher.exe 复制为 メルストM_chs.exe，原版不动）

       （译者就地修改 extracted_data/ 下的 JSON）

                  ─────────────►  mercstoria release
                                    ├── 1. repack    （改过的 JSON → repacked_bundles/）
                                    └── 2. deploy    （推回正在运行的缓存）
```

每一步都幂等 —— `mercstoria setup` 和 `mercstoria release` 想跑几次都行。
单步跳过用 `--skip-<name>`，例如 `mercstoria setup --skip-bundle-cache --skip-launcher`
适用于纯开发环境。orchestrator 需要的两个产物都已随仓库提供：

- `logofont.bundle` —— LogoSC SDF 字体 bundle，烘焙好后放在仓库根目录
- `launcher/build/Release/launcher.exe` —— 用一行 cmake 构建：
  `cmake -S launcher -B launcher/build -A x64 && cmake --build launcher/build --config Release`

底层各步骤也都对外暴露（`patch-crc`、`extract`、`repack`、`deploy`、`font-swap`……），
不带参数运行 `mercstoria` 可看完整子命令列表。

> 所有脚本都通过 `mercstoria` 包入口调用：`uv run -m mercstoria <subcmd> [args]`。不带参数运行可看到完整子命令列表。

## 项目结构

```
workshop/
├── README.md                   英文版（本文件位于 docs/）
├── pyproject.toml              uv/pip 依赖
│
├── mercstoria/                 Python 包（共享库 + CLI 派发器）
│   ├── __main__.py             `uv run -m mercstoria <subcmd>` 入口
│   ├── config.py               中央配置：路径 + RVA + 加密参数
│   └── memorypack.py           AES 解密 + 完整 MemoryPack Reader/Writer
│
├── scripts/                    各子命令的 CLI 脚本（由 __main__.py 转发）
│   ├── setup.py                端到端译前 orchestrator
│   ├── release.py              端到端译后 orchestrator
│   ├── patch_crc.py            CRC 绕过（4 处）
│   ├── patch_offline.py        Steam 绕过 + 证书跳过 + GetAsync（8 处）
│   ├── verify_patches.py       两套修补的只读检查
│   ├── extract_repack.py       剧情 + 15 个 master bundle 的 extract / repack
│   ├── extract_ui.py           内嵌 UI 文本 + UI 标签辅助（被 extract/repack/deploy 调用）
│   ├── check_roundtrip.py      对前 N 个 story bundle 做 Reader/Writer 一致性检查
│   ├── deploy.py               把重打包的 bundle 推到 <game>/AssetBundle（原文件镜像到 AssetBundle_old/）
│   ├── bundle_cache.py         把 %LocalLow%/.../AssetBundle 拷到 <game>/AssetBundle
│   ├── font_swap.py            TMP 字体替换（atlas + bundle + 隐藏字体）
│   └── export_chars.py         为 TMP 字体烘焙生成 target_chars.txt
│
├── docs/
│   ├── CRC_PATCH_GUIDE.md            （+ _zh-CN 译文）
│   ├── OFFLINE_MODE_GUIDE.md         （+ _zh-CN）
│   ├── STORY_BUNDLE_GUIDE.md         （+ _zh-CN）剧情 bundle 解密 + MemoryPack schema + 重打包
│   ├── FONT_REPLACEMENT_GUIDE.md     （+ _zh-CN）
│   ├── MASTERDATA_SCHEMA_GUIDE.md    （+ _zh-CN）全部 15 个 master bundle 的 schema
│   └── README_zh-CN.md               本文件
│
└── launcher/
    ├── CMakeLists.txt          MSVC + MinGW 均可
    ├── README.md
    ├── README_zh-CN.md
    ├── src/                    launcher.c, junction.c/.h
    ├── test/                   test_junction.c
    └── cmake/RunJunctionTest.cmake
```

第三方组件（gitignore 屏蔽）：`Il2CppDumper/`、`il2cpp_output/`、`tools/`。

## 运行依赖

所有 Python 依赖（`UnityPy`、`lz4`、`numpy`、`Pillow`、`cryptography`、`capstone`）在 [`../pyproject.toml`](../pyproject.toml) 中声明。运行子命令：

```bash
uv run -m mercstoria <subcommand> [args]
uv run -m mercstoria              # 显示完整子命令列表
```

启动器需要 CMake ≥ 3.20，以及 MSVC（Visual Studio 2022 Build Tools 或更新）或 MinGW 二选一。构建命令见 [`../launcher/README.md`](../launcher/README.md)。

修补脚本本身的唯一非 Python 依赖是 **Il2CppDumper**，仅在定位修补点时使用一次。

## 外部工具与参考

- [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) —— 从 `GameAssembly.dll` + `global-metadata.dat` dump 符号
- [UnityPy](https://github.com/K0lb3/UnityPy) —— Unity 资源 bundle 读写库（文本 + 字体管线都靠它）
- [Cysharp/MemoryPack](https://github.com/Cysharp/MemoryPack) —— 游戏文本载荷使用的二进制序列化器
- [Cysharp/YetAnotherHttpHandler](https://github.com/Cysharp/YetAnotherHttpHandler) —— 离线模式短路的 rustls HTTPS 客户端
- [TextMeshPro 包](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/index.html) —— 在 Unity 里烤源字体 bundle 必备
- [Capstone 反汇编引擎](https://www.capstone-engine.org/) —— 修补验证步骤会用
- [IDA Pro](https://hex-rays.com/ida-pro) —— 用来在 dump 出的二进制里做交叉引用
- 各指南详细链接：[CRC](CRC_PATCH_GUIDE_zh-CN.md#外部链接)、[离线](OFFLINE_MODE_GUIDE_zh-CN.md#外部链接)、[剧情](STORY_BUNDLE_GUIDE_zh-CN.md#外部链接)、[字体](FONT_REPLACEMENT_GUIDE_zh-CN.md#外部链接)

## 进度

- [x] CRC 绕过 —— 4 处修补点，稳定
- [x] 剧情文本解密 / 提取 —— 4,008 个剧情含元数据
- [x] MasterData 文本 —— 全部 15 个 master bundle 走完整 MemoryPack schema（字节级 round-trip）
- [x] 含译文的重打包 —— 端到端往返验证通过；repack 为增量式（指纹存于 `extracted_data/.fingerprints.pkl`，每次成功打包后推进，再跑只处理此后改过的文件）
- [x] 字体替换 —— 中文 SDF 在所有画面上正确渲染
- [x] 离线启动端到端 —— 8 处修补点；无网无 Steam 即可到达 标题 → 主页 → 剧情章节列表
- [x] 自包含安装 —— 缓存通过 NTFS junction 实际位于游戏目录内
- [x] 单击启动器 —— junction 创建步骤打进了 EXE（CMake 构建，支持 MSVC + MinGW）；启动时强制 D3D11，避免在 NVIDIA fallback 到 OpenGL ES 3 的机器上最终章片尾字幕成块跳过
- [x] 内嵌 UI 文本 —— 最终章片尾 Timeline 字幕通过 TypeTree 替换（4 个 bundle，44 行）
- [x] UI 标签 —— 游戏内所有菜单 / HUD / 详情面板标签从 Addressables bundle（`StreamingAssets/aa/`）提取；50 个 bundle，264 条字符串，位于 `extracted_data/ui_labels/`
- [ ] 国家名 —— 作为 IL2CPP enum 字段名字面量存储在 `global-metadata.dat`（`Country` enum ~0x1371F2，`CountryFilter` ~0x138DBC）；运行时显示 = `Enum.GetName(Country, id) + "の国"`；需直接 patch 二进制文件里的两张表（不涉及 bundle）
- [ ] 图片提取与翻译 —— 找出游戏中含日文的美术资源并替换
- [ ] 4,000+ 剧情的翻译记忆 + LLM 管线
