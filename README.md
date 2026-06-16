# Merc Storia (メルクストーリア) — Translation Project

End-to-end toolkit for translating the Steam release of **メルクストーリア - 癒術士と心の旋律 -** into a non-Japanese language. The game ships with no built-in i18n; everything here is reverse-engineered.

Five independent accomplishments that together make full translation possible:

1. **CRC bypass** — 4 `xor edx, edx` patches in `GameAssembly.dll` so modified bundles are not silently re-downloaded. See [`docs/CRC_PATCH_GUIDE.md`](docs/CRC_PATCH_GUIDE.md).
2. **Text decrypt / extract / repack** — full pipeline for ~4,000 story bundles (AES-256-CBC + MemoryPack). See [`docs/TEXT_EXTRACTION_GUIDE.md`](docs/TEXT_EXTRACTION_GUIDE.md).
3. **Font replacement** — TMP font swap across the three physical places the font actually lives. See [`docs/FONT_REPLACEMENT_GUIDE.md`](docs/FONT_REPLACEMENT_GUIDE.md).
4. **Offline mode** — 8 patches (Steam bypass + Cysharp cert-skip + pure file-read GetAsync) for self-contained installs that need no internet and no Steam. See [`docs/OFFLINE_MODE_GUIDE.md`](docs/OFFLINE_MODE_GUIDE.md).
5. **Self-contained launcher** — single-click replacement for `メルストM.exe` that bundles the NTFS-junction setup into the EXE. See [`launcher/README.md`](launcher/README.md).

## Game environment (canonical)

The five components reference this section instead of duplicating it.

| Item | Value |
|---|---|
| Engine | Unity 6000.0.58f2, IL2CPP, Windows x64 |
| Store | Steam |
| Game folder | `<Steam>/steamapps/common/メルクストーリア - 癒術士と心の旋律 -/` |
| Player exe | `メルストM.exe` + `メルストM_Data/` (or `メルストM_app.exe` + `メルストM_app_Data/` after launcher deploy) |
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

```
   raw game install
   ────────────────►  mercstoria patch-crc      ──►  GameAssembly.dll (CRC patched)
                          │
                          ▼
                mercstoria extract
                ──────────────────────────────►  extracted_data/
                                                   story/<id>.json    (4,008 stories)
                                                   misc/<asset>.json  (15 master bundles, full schema)
                                                   .fingerprints.pkl  (skip untouched on repack)
                          │
                  (translator edits values in place)
                          │
                          ▼
                mercstoria repack ─────────────►  repacked_bundles/<bundle>
                          │
                          ▼
   mercstoria font-swap logofont.bundle ─────────►  font swapped, ready to launch
```

For offline shipping, also run `mercstoria patch-offline`, copy the LocalLow cache into `<install>/AssetBundle/`, build and drop `launcher/build/Release/launcher.exe` from [`launcher/README.md`](launcher/README.md), and ship the whole folder. End-user workflow becomes a single double-click.

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
│   ├── patch_crc.py            CRC bypass (4 sites)
│   ├── patch_offline.py        Steam bypass + cert skip + GetAsync (8 sites)
│   ├── verify_patches.py       read-only check on both patch sets
│   ├── extract_repack.py       extract / repack stories + 15 master bundles
│   ├── check_roundtrip.py      sanity-check Reader/Writer on N story bundles
│   ├── deploy.py               push repacked bundles into the cache (game/persistent auto)
│   ├── bundle_cache.py         copy %LocalLow%/.../AssetBundle → <game>/AssetBundle (bilingual)
│   ├── font_swap.py            TMP font swap (atlas + bundle + hidden font)
│   └── export_chars.py         build target_chars.txt for the TMP font bake
│
├── docs/
│   ├── CRC_PATCH_GUIDE.md            (+ _zh-CN translation)
│   ├── OFFLINE_MODE_GUIDE.md         (+ _zh-CN)
│   ├── TEXT_EXTRACTION_GUIDE.md      (+ _zh-CN)
│   ├── FONT_REPLACEMENT_GUIDE.md     (+ _zh-CN)
│   ├── MEMORYPACK_SCHEMA_GUIDE.md    (+ _zh-CN) story bundle wire format
│   ├── MASTERDATA_SCHEMA_GUIDE_zh-CN.md  all 15 master bundle schemas
│   └── README_zh-CN.md               this file in 简体中文
│
└── launcher/
    ├── CMakeLists.txt          MSVC + MinGW
    ├── README.md
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
- [Ghidra](https://github.com/NationalSecurityAgency/ghidra) — recommended for x-ref work on the dumped binary
- Per-guide details: [CRC](docs/CRC_PATCH_GUIDE.md#external-references), [offline](docs/OFFLINE_MODE_GUIDE.md#external-references), [text](docs/TEXT_EXTRACTION_GUIDE.md#external-references), [font](docs/FONT_REPLACEMENT_GUIDE.md#external-references)

## Status

- [x] CRC bypass — 4 patch sites, stable
- [x] Story text decrypt / extract — 4,008 stories with metadata
- [x] MasterData text — all 15 master bundles parsed via full MemoryPack schema (byte-identical round-trip)
- [x] Repack with translated content — round-trip verified
- [x] Font replacement — Chinese SDF rendering across all screens
- [x] Offline boot end-to-end — 8 patch sites; title → home → story chapter list with no internet, no Steam
- [x] Self-contained install — cache inside the game folder via NTFS junction
- [x] Single-click launcher — bundles junction setup into the EXE (CMake-built, MSVC + MinGW)
- [ ] Path redirection so a translation build can ship as a side-by-side install
- [ ] Translation memory + LLM pipeline for all 4,000+ stories
