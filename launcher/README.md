# Merc Storia Launcher

中文版：[`README_zh-CN.md`](README_zh-CN.md)

Self-contained Windows launcher that replaces what used to be a manual
`mklink /J` setup step. On double-click it creates an NTFS mount-point
junction from
`%LocalLow%/jp_co_happyelements/メルストM/AssetBundle` to the bundled
`<install>/AssetBundle/`, then starts `メルストM_app.exe`. Idempotent — second
run sees the junction and just launches.

See [`../docs/OFFLINE_MODE_GUIDE.md`](../docs/OFFLINE_MODE_GUIDE.md) for why
this junction is needed.

## Layout

```
launcher/
├── CMakeLists.txt
├── README.md
├── cmake/
│   └── RunJunctionTest.cmake     # ctest driver
├── src/
│   ├── junction.c                # FSCTL_SET_REPARSE_POINT wrapper
│   ├── junction.h
│   └── launcher.c                # wWinMain — install junction + spawn exe
└── test/
    └── test_junction.c           # standalone CLI tester
```

## Build

### MSVC (default)

```
cmake -S . -B build -A x64
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
```

Outputs `build/Release/launcher.exe` (~140 KB, no runtime deps) and
`build/Release/test_junction.exe`.

### MinGW

```
cmake -S . -B build -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release
cmake --build build
ctest --test-dir build --output-on-failure
```

The CMake config sets `-finput-charset=UTF-8 -fexec-charset=UTF-16LE -municode`
so the Japanese L"..." literals compile correctly under both toolchains.

## Deploy to a game folder

Rename the original exe + data folder, then drop the launcher in their place:

```powershell
$dest = "<game install folder>"
Rename-Item -LiteralPath "$dest\メルストM.exe"      -NewName "メルストM_app.exe"
Rename-Item -LiteralPath "$dest\メルストM_Data"     -NewName "メルストM_app_Data"
Copy-Item   -LiteralPath "build\Release\launcher.exe" -Destination "$dest\メルストM.exe"
```

Unity derives the `_Data` folder name from the exe basename — renaming the
exe to `メルストM_app.exe` makes it look for `メルストM_app_Data/`, which is
why the data folder rename is mandatory.

## Revert

```powershell
$dest = "<game install folder>"
Remove-Item -LiteralPath "$dest\メルストM.exe"
Rename-Item -LiteralPath "$dest\メルストM_app.exe"   -NewName "メルストM.exe"
Rename-Item -LiteralPath "$dest\メルストM_app_Data"  -NewName "メルストM_Data"
```

The junction at `%LocalLow%/.../AssetBundle` is left untouched; if you want
to undo that too, simply `Remove-Item -LiteralPath <that junction>` — removing
a reparse point does NOT delete the target.

## Design notes

* `launcher.c` is `/SUBSYSTEM:WINDOWS` (no console flash). Errors go through
  `MessageBoxW` titled `メルクストーリア — Launcher`.
* `junction.c` is shared between the launcher and the test harness so the
  reparse-buffer layout has exactly one source of truth.
* Static CRT (`/MT` on MSVC, `-static` on MinGW) so the shipped exe needs no
  redistributable.
* `ctest` exercises `create_junction` against `%TEMP%` and verifies the link
  resolves before tearing it down.
