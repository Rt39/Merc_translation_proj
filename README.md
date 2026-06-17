# Merc Storia (メルクストーリア) — Translation Project

> 中文版请戳[这里](docs/README_zh-CN.md)。

End-to-end toolkit for translating the Steam release of **メルクストーリア - 癒術士と心の旋律 -** into a non-Japanese language. The game ships with no built-in i18n; everything here is reverse-engineered.

Five independent accomplishments that together make full translation possible:

1. **CRC bypass** — 4 `xor edx, edx` patches in `GameAssembly.dll` so modified bundles are not silently re-downloaded. See [`docs/CRC_PATCH_GUIDE.md`](docs/CRC_PATCH_GUIDE.md).
2. **Text decrypt / extract / repack** — full pipeline for ~4,000 story bundles (AES-256-CBC + MemoryPack). See [`docs/STORY_BUNDLE_GUIDE.md`](docs/STORY_BUNDLE_GUIDE.md).
3. **Font replacement** — TMP font swap across the three physical places the font actually lives. See [`docs/FONT_REPLACEMENT_GUIDE.md`](docs/FONT_REPLACEMENT_GUIDE.md).
4. **Offline mode** — 8 patches (Steam bypass + Cysharp cert-skip + pure file-read GetAsync) for self-contained installs that need no internet and no Steam. See [`docs/OFFLINE_MODE_GUIDE.md`](docs/OFFLINE_MODE_GUIDE.md).
5. **Self-contained launcher** — single-click replacement for `メルストM.exe` that bundles the NTFS-junction setup into the EXE. See [`launcher/README.md`](launcher/README.md).

## Game environment (canonical)

| Item | Value |
|---|---|
| Engine | Unity 6000.0.58f2, IL2CPP, Windows x64 |
| Store | Steam |
| Game folder | `<Steam>/steamapps/common/メルクストーリア - 癒術士と心の旋律 -/` |
| Player exe | `メルストM.exe` + `メルストM_Data/` (untouched; translated build adds `メルストM_chs.exe` alongside) |
| IL2CPP binary | `GameAssembly.dll` (~78 MB) |
| IL2CPP metadata | `<exe>_Data/il2cpp_data/Metadata/global-metadata.dat` |
| Addressables catalog | `<exe>_Data/StreamingAssets/aa/catalog.bin` (Addressables 2.3.7) |
| CDN host | `https://assets.mercstoria-memorial.hekk.org/AssetBundle/StandaloneWindows64/<Category>/<file>` |
| CDN cache (LocalLow) | `%USERPROFILE%/AppData/LocalLow/jp_co_happyelements/メルストM/AssetBundle/StandaloneWindows64/<Category>/<file>` |
| Player log | `%USERPROFILE%/AppData/LocalLow/jp.co.happyelements/メルストM/Player.log` *(dots, not underscores)* |
| Asset format | UnityFS, LZ4(HC) |
| Text format | TextAsset → AES-256-CBC → MemoryPack (UTF-8 mode) |
| Font format | TMP SDF (4096×4096 Alpha8 atlas) |

The toolkit auto-detects the game install. To override, set `MERCSTORIA_GAME_DIR=<path>` before running any script. See [`mercstoria/config.py`](mercstoria/config.py) for the full list of derived paths.

## Workflow (full translation)

The toolkit collapses into two commands:

```
   raw game install
   ─────────────►  mercstoria setup
                     │
                     ├── 1. patch-crc           (4 sites)
                     ├── 2. patch-offline       (8 sites — Steam bypass + cert + GetAsync)
                     ├── 3. font-swap           (atlas + bundle + hidden font, uses logofont.bundle)
                     ├── 4. extract             (4,008 stories + 15 master bundles → extracted_data/)
                     ├── 5. bundle-cache        (LocalLow → <game>/AssetBundle)
                     └── 6. deploy launcher     (drop launcher.exe as メルストM_chs.exe — original untouched)

       (translator edits values in place under extracted_data/)

                  ─────────────►  mercstoria release
                                    ├── 1. repack    (changed JSONs → repacked_bundles/)
                                    └── 2. deploy    (push to live cache)
```

Each step is idempotent — re-run `mercstoria setup` or `mercstoria release` as
many times as you like. Skip individual steps with `--skip-<name>` (e.g.
`mercstoria setup --skip-bundle-cache --skip-launcher` against a pristine dev
install). Both prebuilt artefacts the orchestrator needs ship in the repo:

- `logofont.bundle` — the LogoSC SDF font bundle, baked at the repo root
- `launcher/build/Release/launcher.exe` — build it once with
  `cmake -S launcher -B launcher/build -A x64 && cmake --build launcher/build --config Release`

Underlying steps are also exposed individually (`patch-crc`, `extract`,
`repack`, `deploy`, `font-swap`, ...) — see `mercstoria` with no args for the
full list.

> All scripts are invoked through the `mercstoria` package entry point: `uv run -m mercstoria <subcommand> [args]`. Run with no args to see the full subcommand list.

## Project layout

```
workshop/
├── README.md                   (this file)
├── pyproject.toml              uv/pip deps
│
├── mercstoria/                 Python package (shared library + CLI dispatcher)
│   ├── __main__.py             `uv run -m mercstoria <subcmd>` entry point
│   ├── config.py               central config: paths + RVAs + crypto params
│   └── memorypack.py           AES decrypt + full MemoryPack Reader/Writer
│
├── scripts/                    individual CLI scripts (forwarded to by __main__.py)
│   ├── setup.py                end-to-end pre-translation orchestrator
│   ├── release.py              end-to-end post-translation orchestrator
│   ├── patch_crc.py            CRC bypass (4 sites)
│   ├── patch_offline.py        Steam bypass + cert skip + GetAsync (8 sites)
│   ├── verify_patches.py       read-only check on both patch sets
│   ├── extract_repack.py       extract / repack stories + 15 master bundles
│   ├── extract_ui.py           inline UI text helpers (Timeline cinematic dialogue, called by extract/repack)
│   ├── check_roundtrip.py      sanity-check Reader/Writer on N story bundles
│   ├── deploy.py               push repacked bundles into <game>/AssetBundle (mirrors originals to AssetBundle_old/)
│   ├── bundle_cache.py         copy %LocalLow%/.../AssetBundle → <game>/AssetBundle
│   ├── font_swap.py            TMP font swap (atlas + bundle + hidden font)
│   └── export_chars.py         build target_chars.txt for the TMP font bake
│
├── docs/
│   ├── CRC_PATCH_GUIDE.md            (+ _zh-CN translation)
│   ├── OFFLINE_MODE_GUIDE.md         (+ _zh-CN)
│   ├── STORY_BUNDLE_GUIDE.md         (+ _zh-CN) story bundle decrypt + MemoryPack schema + repack
│   ├── FONT_REPLACEMENT_GUIDE.md     (+ _zh-CN)
│   ├── MASTERDATA_SCHEMA_GUIDE.md    (+ _zh-CN) all 15 master bundle schemas
│   └── README_zh-CN.md               this file in 简体中文
│
└── launcher/
    ├── CMakeLists.txt          MSVC + MinGW
    ├── README.md
    ├── README_zh-CN.md
    ├── src/                    launcher.c, junction.c/.h
    ├── test/                   test_junction.c
    └── cmake/RunJunctionTest.cmake
```

Bundled third-party (gitignored): `Il2CppDumper/`, `il2cpp_output/`, `tools/`.

## Runtime requirement

Python deps (`UnityPy`, `lz4`, `numpy`, `Pillow`, `cryptography`, `capstone`) are declared in [`pyproject.toml`](pyproject.toml). Run any subcommand with:

```bash
uv run -m mercstoria <subcommand> [args]
uv run -m mercstoria              # show full subcommand list
```

For the launcher, install CMake ≥ 3.20 and either MSVC (Visual Studio 2022 Build Tools or newer) or MinGW. See [`launcher/README.md`](launcher/README.md) for build commands.

The only non-Python prerequisite for the patches themselves is **Il2CppDumper**, used once to locate the patch sites.

## External tools and references

- [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) — dump symbols from `GameAssembly.dll` + `global-metadata.dat`
- [UnityPy](https://github.com/K0lb3/UnityPy) — Unity asset bundle reader/writer (text + font pipelines)
- [Cysharp/MemoryPack](https://github.com/Cysharp/MemoryPack) — binary serializer used by the game's text payload
- [Cysharp/YetAnotherHttpHandler](https://github.com/Cysharp/YetAnotherHttpHandler) — the rustls HTTPS client we short-circuit for offline mode
- [TextMeshPro package](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/index.html) — required to bake the source font bundle in Unity
- [Capstone disassembler](https://www.capstone-engine.org/) — used in the patch verification step
- [IDA Pro](https://hex-rays.com/ida-pro) — used for x-ref work on the dumped binary (the `.i64` database lives next to `GameAssembly.dll`)
- Per-guide details: [CRC](docs/CRC_PATCH_GUIDE.md#external-references), [offline](docs/OFFLINE_MODE_GUIDE.md#external-references), [story](docs/STORY_BUNDLE_GUIDE.md#external-references), [font](docs/FONT_REPLACEMENT_GUIDE.md#external-references)

## Status

- [x] CRC bypass — 4 patch sites, stable
- [x] Story text decrypt / extract — 4,008 stories with metadata
- [x] MasterData text — all 15 master bundles parsed via full MemoryPack schema (byte-identical round-trip)
- [x] Repack with translated content — round-trip verified
- [x] Font replacement — Chinese SDF rendering across all screens
- [x] Offline boot end-to-end — 8 patch sites; title → home → story chapter list with no internet, no Steam
- [x] Self-contained install — cache inside the game folder via NTFS junction
- [x] Single-click launcher — bundles junction setup into the EXE (CMake-built, MSVC + MinGW)
- [x] Inline UI text — final-chapter Timeline cinematic dialogue swapped in via TypeTree (4 bundles, 44 strings)
- [ ] Image extraction + translation — find in-game art that contains Japanese text and swap it
- [ ] Translation memory + LLM pipeline for all 4,000+ stories
