# Merc Storia 启动器

自包含的 Windows 启动器，替代以前需要手动跑 `mklink /J` 的步骤。双击即可在
`%LocalLow%/jp_co_happyelements/メルストM/AssetBundle` 与打包目录中的
`<install>/AssetBundle/` 之间建立一个 NTFS mount-point junction，然后转发
启动原版 `メルストM.exe`。幂等 —— 第二次运行发现 junction 已存在就直接启动。

启动器是原版游戏 exe 的**伴生文件**，不是替换：原版 `メルストM.exe` 和
`メルストM_Data/` 保持不动，Steam 的"验证文件完整性"仍然能在原文件上通过。
用户双击 `メルストM_chs.exe` 即可启动汉化版。

自愈：如果游戏文件夹被改名或整体移动，启动器下次运行会检测到 junction 的
目标已经失效并把它改写到新路径（否则 Unity 会从无效的 `AssetBundle/` 加载，
直接进入黑屏）。

junction 的来龙去脉见
[`../docs/OFFLINE_MODE_GUIDE_zh-CN.md`](../docs/OFFLINE_MODE_GUIDE_zh-CN.md)。

## 目录结构

```
launcher/
├── CMakeLists.txt
├── README.md
├── README_zh-CN.md
├── cmake/
│   └── RunJunctionTest.cmake     # ctest 驱动
├── src/
│   ├── junction.c                # FSCTL_SET_REPARSE_POINT 封装
│   ├── junction.h
│   └── launcher.c                # wWinMain —— 创建 junction + 启动 exe
└── test/
    └── test_junction.c           # 独立 CLI 测试器
```

## 构建

### MSVC（默认）

```
cmake -S . -B build -A x64
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
```

产出 `build/Release/launcher.exe`（约 140 KB，无运行时依赖）以及
`build/Release/test_junction.exe`。

### MinGW

```
cmake -S . -B build -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release
cmake --build build
ctest --test-dir build --output-on-failure
```

CMake 配置设置了 `-finput-charset=UTF-8 -fexec-charset=UTF-16LE -municode`，
两套工具链都能正确编译日文 `L"..."` 字面量。

## 部署到游戏目录

把 `launcher.exe` 复制到游戏目录，命名为 `メルストM_chs.exe` 即可。原版 exe
保持不动：

```powershell
$dest = "<game install folder>"
Copy-Item -LiteralPath "build\Release\launcher.exe" -Destination "$dest\メルストM_chs.exe"
```

`mercstoria setup` 的最后一步会自动做这件事。

## 撤销

```powershell
$dest = "<game install folder>"
Remove-Item -LiteralPath "$dest\メルストM_chs.exe"
```

`%LocalLow%/.../AssetBundle` 那个 junction 保持原样；要一并撤销，直接
`Remove-Item -LiteralPath <那个 junction>` —— 删 reparse point **不会**删除
目标目录。

如果在第一次跑启动器之前先跑过原版游戏，启动器会把 LocalLow 里那个真实
缓存目录改名让位到 `AssetBundle.pre_setup`（或 `.pre_setup_N`）。这个备份
就是你"退回原版日文"的退路，会一直留着，直到你用
`mercstoria release --purge-locallow-cache` 回收磁盘 —— 那一步会同时清掉
现役 junction/目录和所有 `.pre_setup*` 备份（各自有独立的 DELETE 确认）。

## 设计要点

* `launcher.c` 走 `/SUBSYSTEM:WINDOWS`（不闪控制台）。错误用 `MessageBoxW`
  弹出，标题为 `メルクストーリア — Launcher`。
* `junction.c` 由启动器和测试器共用，让 reparse buffer 的布局只有一份真
  实来源。
* 静态链接 CRT（MSVC 用 `/MT`，MinGW 用 `-static`），分发的 exe 不需要任
  何 redistributable。
* `ctest` 在 `%TEMP%` 上跑一遍 `create_junction`，验证 link 能正确解析后
  再清理掉。
* Stale junction 的检测走 `FSCTL_GET_REPARSE_POINT`（封装在 `junction.c`
  的 `read_junction_target`），拿到现有目标后用 `_wcsicmp` 与当前安装的
  `AssetBundle/` 路径做大小写不敏感比对。不匹配就原地删链接重建，目标目录
  本身完全不动。
* 启动游戏时附加 `-force-d3d11`。Unity 6000.x 在某些 NVIDIA 驱动配置下会
  fallback 到 OpenGL ES 3，导致最终章片尾 Timeline 字幕成块跳过（帧率与
  `UnscaledGameTime` 推进对不上）。强制 D3D11 能让 cinematic 按设计节奏
  播放。
