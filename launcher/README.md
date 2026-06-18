# Merc Storia Launcher

> 中文版请戳[这里](README_zh-CN.md)。

Self-contained Windows launcher that replaces what used to be a manual
`mklink /J` setup step. On double-click it creates an NTFS mount-point
junction from
`%LocalLow%/jp_co_happyelements/メルストM/AssetBundle` to the bundled
`<install>/AssetBundle/`, then chains into the original `メルストM.exe`.
Idempotent — second run sees the junction and just launches.

The launcher is a **drop-in companion** to the original game exe, not a
replacement: original `メルストM.exe` and `メルストM_Data/` stay untouched
so a Steam "Verify integrity" pass still works on the unmodified files.
Users double-click `メルストM_chs.exe` to launch the translated build.

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

Just drop `launcher.exe` into the game folder as `メルストM_chs.exe`. The
original game exe is left in place:

```powershell
$dest = "<game install folder>"
Copy-Item -LiteralPath "build\Release\launcher.exe" -Destination "$dest\メルストM_chs.exe"
```

`mercstoria setup` does this automatically as its last step.

## Revert

```powershell
$dest = "<game install folder>"
Remove-Item -LiteralPath "$dest\メルストM_chs.exe"
```

The junction at `%LocalLow%/.../AssetBundle` is left untouched; if you want
to undo that too, simply `Remove-Item -LiteralPath <that junction>` — removing
a reparse point does NOT delete the target.

If the original game ran before the launcher's first launch, the launcher
moved the real LocalLow cache aside to `AssetBundle.pre_setup` (or
`.pre_setup_N`). That backup is your "revert to vanilla Japanese" copy. It is
left in place until you reclaim the disk with `mercstoria release
--purge-locallow-cache`, which removes both the live junction/dir and every
`.pre_setup*` backup (each behind its own DELETE confirmation).

## Design notes

* `launcher.c` is `/SUBSYSTEM:WINDOWS` (no console flash). Errors go through
  `MessageBoxW` titled `メルクストーリア — Launcher`.
* `junction.c` is shared between the launcher and the test harness so the
  reparse-buffer layout has exactly one source of truth.
* Static CRT (`/MT` on MSVC, `-static` on MinGW) so the shipped exe needs no
  redistributable.
* `ctest` exercises `create_junction` against `%TEMP%` and verifies the link
  resolves before tearing it down.
* Spawns `メルストM.exe -force-d3d11`. Unity 6000.x falls back to OpenGL ES 3
  on some NVIDIA driver configurations, which makes the final-chapter
  Timeline cinematic skip subtitles in chunks (frame pacing mismatch with
  `UnscaledGameTime`). Forcing D3D11 keeps the cinematic at the intended
  cadence.
