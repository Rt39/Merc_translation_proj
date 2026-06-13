# Merc Storia (メルクストーリア) — Translation Project

End-to-end toolkit for translating the Steam release of **メルクストーリア - 癒術士と心の旋律 -** (Merc Storia: Healers and the Melody of the Heart) into a non-Japanese language. The game ships with no built-in i18n; every step here is reverse-engineered.

This repository documents four independent technical accomplishments that, together, make full translation possible:

1. **Disabling the game's bundle CRC check** — patches in `GameAssembly.dll` that let us swap in modified asset bundles without triggering Unity's "data corruption" path.
2. **Decrypting, extracting, and repacking all in-game text** — full pipeline for ~4,000 story bundles encrypted with AES-256-CBC + serialized with MemoryPack.
3. **Replacing the bundled Japanese font** with an arbitrary TMP font asset (e.g. Chinese-glyph SDF), including the three physical places the font actually lives.
4. **Removing the Steam dependency and short-circuiting the CDN** — six byte patches that let the game run with no Steam Client and no network, sourcing every asset from the local install + LocalLow cache.

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

### 4. Offline mode — Steam bypass + Cysharp cert-skip + pure file-read GetAsync (see `OFFLINE_MODE_GUIDE.md`)

End-to-end offline, no external moving parts: the game launches with no Steam Client and no internet, reaches the title screen, home menu, and story chapter list with every bundle loading correctly from the LocalLow cache. No local server, no certificate trust changes, no hosts-file edits.

Eight patch sites in `GameAssembly.dll` stack:

1. **Steam wrapper neutered (S1–S4)** — `SteamApplication.Initialize` and `Impl.Initialize` are turned into `ret`, and `Impl.GetLanguage` / `GetUserDataRootPath` tail-jump into the existing stub implementation (`SteamApplicationImplementationStub`). 4 patches. `SteamAPI_Init` is never called; every Steam accessor returns the dev's hardcoded defaults.

2. **Cysharp YAHH accepts any cert (Y1–Y3, defense in depth)** — both `get_SkipCertificateVerification` getters return `Nullable<bool>(true)`, and one `call set_Http2Only` site inside `AssetBundleHttpClient.ctor` (RVA `0x27FA4F4`) is retargeted to `call set_SkipCertificateVerification` (rel32 nudge of `+0x120`). 3 patches. Strictly redundant once the next patch is in place but cheap and protects against any code path that might still go through HttpClient.

3. **Pure file-read GetAsync (P)** — the private 5-arg `AssetBundleHttpClient.GetAsync` (RVA `0x27FA120`) is replaced with 136 bytes of x64 that read the URL, drop the host prefix `https://assets.mercstoria-memorial.hekk.org/` (44 bytes), look up the matching file under `Application.persistentDataPath`, and return a synchronously-completed `ValueTask<byte[]>` with the file content. Calls only existing IL2CPP-compiled methods (`String.IndexOf`, `String.Substring`, `Application.get_persistentDataPath`, `Path.Combine`, `File.ReadAllBytes`). The public 2-arg and 4-arg overloads forward to this 5-arg via existing rel32 calls and propagate its result. 1 patch.

Subtleties documented in the guide:

- The dead-code trap: `AssetBundleHttpClient.CreateHttpClient` (static, RVA `0x27F9FB0`) is **never called** — patching it does nothing. The HttpClient that the game uses is built in the instance ctor at RVA `0x27FA420`.
- Why P targets the private 5-arg, not the public overloads: the 2-arg has 80 bytes of slack and the 4-arg has 96 bytes; a 136-byte body overflows them. The 5-arg has 464 bytes available.
- Why the CDN cert is rejected even though it's valid: rustls (via `Cysharp.Net.Http`) bundles its own Mozilla webpki-roots and ignores the Windows root store. The user's outbound TUN proxy reshapes the chain enough to trigger `UnknownIssuer`. Skipping verification is the simplest fix.

Result: `メルストM.exe` boots end-to-end with the Steam Client closed and the network disconnected. The story chapter list shows up with all character art, localized labels, and fonts intact. No external processes, no certificate trust changes, no system-level config touched.

For shipping the patched build as a single self-contained, copy-and-go directory, an additional one-shot `Setup.cmd` creates an NTFS junction so that the 15 GB CDN cache physically lives inside the game install folder rather than under `%LocalLow%`. The patched HTTP code path AND Unity's own Addressables runtime cache code path both transparently land on the bundled cache via the junction. See [`OFFLINE_MODE_GUIDE.md`](OFFLINE_MODE_GUIDE.md#shipping-a-self-contained-install).

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
| `patch_offline.py` | Apply the 6 offline-mode patches (Steam bypass + CDN short-circuit) |
| `Setup.cmd` | One-shot NTFS-junction setup for self-contained installs (cache-in-game-folder); ships next to `メルストM.exe` |
| `scan_offline_targets.py` | Disassemble the Steam / CDN methods we patch (used during discovery) |
| `verify_offline_patch.py` | Read-only sanity check that both CRC + offline patches are present |
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
| `OFFLINE_MODE_GUIDE.md` | From-scratch guide: Steam wrapper + CDN HTTP client byte patches |

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
- [x] Offline boot end-to-end — 8 patch sites (4 Steam + 3 Cysharp cert-skip + 1 pure-patch GetAsync). Reaches title → home menu → story chapter list with full art, no internet, no Steam.
- [x] Self-contained install — cache physically inside the game folder via NTFS junction created by `Setup.cmd`; copy the install dir, run Setup.cmd once, launch the exe.
- [ ] Path redirection so a translation build can ship as a side-by-side install (orthogonal to above)
- [ ] Translation memory + LLM pipeline for all 4,000+ stories
