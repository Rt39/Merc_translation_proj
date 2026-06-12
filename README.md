# Merc Storia (メルクストーリア) — Translation Project

End-to-end toolkit for translating the Steam release of **メルクストーリア - 癒術士と心の旋律 -** (Merc Storia: Healers and the Melody of the Heart) into a non-Japanese language. The game ships with no built-in i18n; every step here is reverse-engineered.

This repository documents three independent technical accomplishments that, together, make full translation possible:

1. **Disabling the game's bundle CRC check** — patches in `GameAssembly.dll` that let us swap in modified asset bundles without triggering Unity's "data corruption" path.
2. **Decrypting, extracting, and repacking all in-game text** — full pipeline for ~4,000 story bundles encrypted with AES-256-CBC + serialized with MemoryPack.
3. **Replacing the bundled Japanese font** with an arbitrary TMP font asset (e.g. Chinese-glyph SDF), including the three physical places the font actually lives.

## Game environment

| Item | Value |
|---|---|
| Engine | Unity 6000.0.58f2, IL2CPP, Windows x64 |
| Store | Steam |
| Game folder | `<Steam>/steamapps/common/メルクストーリア - 癒術士と心の旋律 -/` |
| Player | `メルストM.exe` + `メルストM_Data/` |
| CDN cache (LocalLow) | `%LOCALAPPDATA%\..\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64\` |
| Asset format | Addressables 2.3.7 (UnityFS, LZ4HC) |
| Text format | TextAsset → AES-256-CBC → MemoryPack (UTF-8 mode) |
| Font format | TMP SDF (4096×4096 Alpha8 atlas, single bundle) |

## What this project delivers

### 1. CRC patch — `GameAssembly.dll` (see `CRC_PATCH_GUIDE.md`)

The IL2CPP-compiled Unity runtime validates every asset bundle against a CRC stored in the catalog. Any modification to a `.bundle` causes silent fallback to a "redownload" code path. Four `xor edx, edx` patches at well-known sites neutralise this check without breaking signed bundles.

Result: any bundle on disk (font, story text, master data) can be modified freely.

### 2. Text decrypt / extract / repack — story bundles (see `TEXT_EXTRACTION_GUIDE.md`)

Every story is stored as one UnityFS bundle containing a single `TextAsset`. The `TextAsset` is:

- `AES-256-CBC-PKCS7` encrypted, with key derived from `PBKDF2-HMAC-SHA256(password="2147483647", salt="-2147483648", iterations=1024, length=32)`.
- The first 16 bytes of ciphertext are the IV.
- Plaintext is `MemoryPack` (UTF-8 mode), a complex `StoryYamlData` structure containing per-scene `Speakers`, `Text`, character configuration, BGM, SFX, background, effect params, etc.

We:

- Decrypt ~4,000 story bundles end-to-end.
- Parse the MemoryPack structure to extract all dialogue and speaker names.
- Mutate strings to translated equivalents (variable UTF-8 byte counts handled correctly).
- Re-serialize and re-encrypt with a fresh random IV.
- Repack into the original UnityFS container (LZ4 compressed).
- Same approach works for `MasterData` bundles (chapter names, story titles, etc.).

End-to-end round-trip verified.

### 3. Font replacement — TMP SDF swap (see `FONT_REPLACEMENT_GUIDE.md`)

The Japanese-only `RocknRollStd SDF` font lives in **three** physical places. Patching only one produces partial garble. We swap all three (font asset in bundle, hidden font asset in `resources.assets`, shared atlas pixels in `resources.assets.resS`) atomically with one script, while preserving the original `m_FaceInfo` `m_LineHeight` so that pre-laid-out dialogue boxes do not collapse.

Result: arbitrary TMP fonts (Chinese, Korean, Latin) render correctly across both story screens and the title/home/menu UI.

## Workflow (full translation)

The three accomplishments stack:

```
                                                  ┌────────────────────┐
                                                  │ GameAssembly.dll   │
                                       ┌─────────►│ CRC patches        │
                                       │          └────────────────────┘
                                       │
   raw game install      patch_crc3.py │
   ────────────────────────────────────┘
                          │
                          ▼
                merc_storia_toolkit.py extract
                ──────────────────────────────►
                       extracted_data/
                         story/<id>.json        (4,008 stories, metadata
                         misc/<AssetName>.json   first, then scenes / strings)
                         .fingerprints.pkl      (SHA-256 of every output;
                                                 lets repack skip untouched)
                          │
                          ▼     (translator edits values in place — no
                          │      separate "translations" dict; just
                          │      replace the original text)
                          │
                merc_storia_toolkit.py repack
                ──────────────────────────────►
                       repacked_bundles/
                         story/<bundle>         (only files whose SHA-256
                         misc/<bundle>           drifted from the baseline)
                          │
                          ▼
   font_swap.py logofont.bundle
   ──────────────────────────────────────────────► font swapped, ready to launch
```

## File reference

| Path | Purpose |
|---|---|
| `patch_crc3.py` | Apply the 4 CRC-disable patches to `GameAssembly.dll` |
| `merc_decrypt.py` | Decrypt + parse a single story bundle (reference implementation) |
| `merc_storia_toolkit.py` | Unified CLI: `extract` / `extract-story` / `extract-misc` / `repack` / `repack-story` / `repack-misc` / `test-repack`. Fingerprints every extracted JSON; repack only touches files the translator edited. |
| `scan_masterdata.py` | One-shot inventory of MasterData bundles, classifying which carry JP text |
| `extract_all.py` | Legacy: dump all stories into one big JSON (superseded by `toolkit extract`) |
| `extract_all_separate.py` | Legacy: per-story JSON without metadata (superseded by `toolkit extract`) |
| `extract_dialogue.py` | Heuristic-based dialogue parser (no MemoryPack schema needed) |
| `extract_masterdata.py` | Raw dump of MasterData bundles (superseded by `toolkit extract-misc`) |
| `extract_metadata_full.py` | Build story-id → chapter / title mapping |
| `extract_story_metadata.py` | Same, lighter-weight version |
| `repack.py` | Reference repack: TextAsset → encrypt → bundle |
| `fix_speakers3.py` | Global string-replace pass for speaker names (covers `CharacterYamlData.Key` / `.DisplayName`) |
| `translate_1621.py` | Example: translate story 1621 into Chinese, end-to-end |
| `verify_repack.py` | Decrypt a repacked bundle and confirm translated text round-trips |
| `font_swap.py` | One-shot font replacement (atlas + bundle font + hidden font in `resources.assets`) |
| `Il2CppDumper/` | Il2CppDumper tool (used to locate CRC sites in `GameAssembly.dll`) |
| `il2cpp_output/` | Dumper output for the current `GameAssembly.dll` / `global-metadata.dat` |
| `jp_monobehaviours.txt` | Inventory of all MonoBehaviours containing Japanese text (used to locate font / dialogue / UI strings) |
| `CRC_PATCH_GUIDE.md` | From-scratch guide: how to find and disable the CRC checks |
| `TEXT_EXTRACTION_GUIDE.md` | From-scratch guide: AES key + MemoryPack + bundle repack pipeline |
| `FONT_REPLACEMENT_GUIDE.md` | From-scratch guide: TMP font asset surgery, atlas atlas atlas |

## Runtime requirement

All dependencies are declared in `pyproject.toml` (`UnityPy`, `lz4`, `numpy`, `Pillow`, `cryptography`, `capstone`). Run any script with:

```bash
uv run <script>.py
```

`uv` resolves the venv from `pyproject.toml` / `uv.lock` on first run; no manual install required.

The only non-Python prerequisite is **Il2CppDumper** (bundled under `Il2CppDumper/`), used once to locate the CRC sites — see `CRC_PATCH_GUIDE.md`.

## Status

- [x] CRC bypass — stable, 4 patch sites
- [x] Story text decrypt / extract — 4,008 stories with title / episode / chapter metadata
- [x] MasterData text decrypt / extract — 15 bundles (~29 k JP strings: monsters, units, chapters, stamps, BG, BGM…)
- [x] Story + MasterData repack with translated content — verified end-to-end
- [x] Font replacement — Chinese SDF rendering correctly in both story and menu
- [ ] Network layer disable (offline mode)
- [ ] Path redirection so a translation build can ship as a side-by-side install
- [ ] Translation memory + LLM pipeline for all 4,000+ stories
