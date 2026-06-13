# Merc Storia（メルクストーリア） — 翻译项目

完整工具链，用于将 **メルクストーリア - 癒術士と心の旋律 -** Steam 版本翻译为非日语。游戏自身不带 i18n，本仓库的每一步都是逆向工程得来的。

五项相互独立的技术成果，合在一起即可完成完整翻译：

1. **CRC 绕过** —— 4 处 `xor edx, edx` 修补在 `GameAssembly.dll` 中，修改后的 bundle 不会再被静默重下。见 [`CRC_PATCH_GUIDE_zh-CN.md`](CRC_PATCH_GUIDE_zh-CN.md)。
2. **文本解密 / 提取 / 重打包** —— 大约 4,000 个剧情 bundle 的完整管线（AES-256-CBC + MemoryPack）。见 [`TEXT_EXTRACTION_GUIDE_zh-CN.md`](TEXT_EXTRACTION_GUIDE_zh-CN.md)。
3. **字体替换** —— 同时修补字体实际所在的三个物理位置，TMP 字体可任意替换。见 [`FONT_REPLACEMENT_GUIDE_zh-CN.md`](FONT_REPLACEMENT_GUIDE_zh-CN.md)。
4. **离线模式** —— 8 处修补（Steam 绕过 + Cysharp 证书跳过 + 纯文件读取 GetAsync），让安装包无需联网、无需 Steam。见 [`OFFLINE_MODE_GUIDE_zh-CN.md`](OFFLINE_MODE_GUIDE_zh-CN.md)。
5. **自包含启动器** —— 替换 `メルストM.exe` 的单击启动器，把 NTFS junction 创建步骤打进了 EXE 自身。见 [`../launcher/README.md`](../launcher/README.md)。

## 游戏环境（基准）

五个组件都引用本节，不再重复。

| 项目 | 取值 |
|---|---|
| 引擎 | Unity 6000.0.58f2，IL2CPP，Windows x64 |
| 平台 | Steam |
| 游戏目录 | `<Steam>/steamapps/common/メルクストーリア - 癒術士と心の旋律 -/` |
| 启动器 | `メルストM.exe` + `メルストM_Data/`（部署启动器后变为 `メルストM_app.exe` + `メルストM_app_Data/`） |
| IL2CPP 二进制 | `GameAssembly.dll`（约 78 MB） |
| IL2CPP 元数据 | `<exe>_Data/il2cpp_data/Metadata/global-metadata.dat` |
| Addressables 目录 | `<exe>_Data/StreamingAssets/aa/catalog.bin`（Addressables 2.3.7） |
| CDN 主机 | `https://assets.mercstoria-memorial.hekk.org/AssetBundle/StandaloneWindows64/<Category>/<file>` |
| LocalLow 缓存 | `%USERPROFILE%/AppData/LocalLow/jp_co_happyelements/メルストM/AssetBundle/StandaloneWindows64/<Category>/<file>` |
| 玩家日志 | `%USERPROFILE%/AppData/LocalLow/jp.co.happyelements/メルストM/Player.log`（*点号，不是下划线*） |
| 资源格式 | UnityFS，LZ4(HC) |
| 文本格式 | TextAsset → AES-256-CBC → MemoryPack（UTF-8 模式） |
| 字体格式 | TMP SDF（4096×4096 Alpha8 atlas） |

工具会自动识别游戏安装目录。如需覆盖，运行任何脚本前设置 `MERCSTORIA_GAME_DIR=<路径>`。所有派生路径在 [`../mercstoria_config.py`](../mercstoria_config.py) 中一处定义。

## 工作流（完整翻译）

```
   原版游戏安装
   ────────────────►  patch_crc.py    ──►  GameAssembly.dll（CRC 已修补）
                          │
                          ▼
                merc_storia_toolkit.py extract
                ──────────────────────────────►  extracted_data/
                                                   story/<id>.json    （4,008 个剧情）
                                                   misc/<asset>.json
                                                   .fingerprints.pkl  （重打包时跳过未改动的文件）
                          │
                  （译者就地修改 value 字段）
                          │
                          ▼
                merc_storia_toolkit.py repack ──►  repacked_bundles/<bundle>
                          │
                          ▼
   font_swap.py logofont.bundle ─────────────────►  字体已换，可启动
```

若要发布离线版本，再运行 `patch_offline.py`，把 LocalLow 缓存复制到 `<install>/AssetBundle/`，按 [`../launcher/README.md`](../launcher/README.md) 构建并部署 `launcher.exe`，整个文件夹分发即可。最终用户工作流：双击 1 次。

## 项目结构

```
workshop/
├── README.md                   英文版（本文件位于 docs/）
├── pyproject.toml              uv/pip 依赖
├── mercstoria_config.py        中央配置：路径 + RVA + 加密参数
│
├── patch_crc.py                CRC 绕过（4 处）
├── patch_offline.py            Steam 绕过 + 证书跳过 + GetAsync（8 处）
├── verify_patches.py           两套修补的只读检查
│
├── merc_storia_toolkit.py      统一的 extract / repack CLI
├── merc_decrypt.py             参考实现：解密 + MemoryPack 解析
├── translate_1621.py           完整示例：剧情 1621 端到端翻译
├── deploy_bundles.py           把重打包后的 bundle 推回缓存（自动优先游戏目录 / LocalLow）
├── bundle_cache.py             把 %LocalLow%/.../AssetBundle 拷到 <game>/AssetBundle（双语提示）
├── font_swap.py                TMP 字体替换（atlas + bundle + 隐藏字体）
│
├── docs/
│   ├── CRC_PATCH_GUIDE.md       （+ _zh-CN 译文）
│   ├── OFFLINE_MODE_GUIDE.md    （+ _zh-CN）
│   ├── TEXT_EXTRACTION_GUIDE.md （+ _zh-CN）
│   ├── FONT_REPLACEMENT_GUIDE.md（+ _zh-CN）
│   └── README_zh-CN.md          本文件
│
├── launcher/
│   ├── CMakeLists.txt          MSVC + MinGW 均可
│   ├── README.md
│   ├── src/                    launcher.c, junction.c/.h
│   ├── test/                   test_junction.c
│   └── cmake/RunJunctionTest.cmake
│
└── Setup.cmd                   遗留的一次性 junction 脚本（已被启动器替代）
```

第三方组件（gitignore 屏蔽）：`Il2CppDumper/`、`il2cpp_output/`、`tools/`。

## 运行依赖

所有 Python 依赖（`UnityPy`、`lz4`、`numpy`、`Pillow`、`cryptography`、`capstone`）在 [`../pyproject.toml`](../pyproject.toml) 中声明。运行任何脚本：

```bash
uv run <script>.py
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
- [Ghidra](https://github.com/NationalSecurityAgency/ghidra) —— 推荐用来在 dump 出的二进制里做交叉引用
- 各指南详细链接：[CRC](CRC_PATCH_GUIDE_zh-CN.md#外部链接)、[离线](OFFLINE_MODE_GUIDE_zh-CN.md#外部链接)、[文本](TEXT_EXTRACTION_GUIDE_zh-CN.md#外部链接)、[字体](FONT_REPLACEMENT_GUIDE_zh-CN.md#外部链接)

## 进度

- [x] CRC 绕过 —— 4 处修补点，稳定
- [x] 剧情文本解密 / 提取 —— 4,008 个剧情含元数据
- [x] MasterData 文本 —— 15 个 bundle，约 29k 条日文字符串
- [x] 含译文的重打包 —— 端到端往返验证通过
- [x] 字体替换 —— 中文 SDF 在所有画面上正确渲染
- [x] 离线启动端到端 —— 8 处修补点；无网无 Steam 即可到达 标题 → 主页 → 剧情章节列表
- [x] 自包含安装 —— 缓存通过 NTFS junction 实际位于游戏目录内
- [x] 单击启动器 —— junction 创建步骤打进了 EXE（CMake 构建，支持 MSVC + MinGW）
- [ ] 路径重定向，使翻译版可与原版并行安装
- [ ] 4,000+ 剧情的翻译记忆 + LLM 管线
