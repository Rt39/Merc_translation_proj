# Merc Storia — Font Replacement Guide

> 中文版请戳[这里](FONT_REPLACEMENT_GUIDE_zh-CN.md)。

How the game loads its UI font, and the patch pipeline that swaps the bundled `RocknRollStd SDF` for an arbitrary TMP font.

Game environment: see [`README.md`](../README.md#game-environment-canonical). CRC patch ([`CRC_PATCH_GUIDE.md`](CRC_PATCH_GUIDE.md)) must be applied first or modified bundles silently revert.

## Anatomy: the font lives in THREE places

The central finding. Patching only one produces partial garble.

### 1. Bundle font asset — story screens

- **File**: `StreamingAssets/aa/StandaloneWindows64/84ece16f121defbfc5b83acb86f5870c.bundle`
- **MonoBehaviour pathID**: `6189425675716077201`, name `RocknRollStd SDF`
- References atlas Texture2D at bundle-local pathID `-4881587269468215663`.
- UnityPy can parse this fully (MonoScript reference resolvable inside the bundle).
- Consumed by in-game dialogue and story title cards.

### 2. Hidden font asset in `resources.assets` — title / menu / home

- **File**: `メルストM_Data/resources.assets`
- **MonoBehaviour pathID**: `27`, byte offset `173728`, size `630328`
- Same name `RocknRollStd SDF` — byte-identical to the bundle copy **except 49 bytes of `m_Script` PPtr**.
- References atlas Texture2D at pid `10` (texture in `resources.assets`).
- **UnityPy CANNOT parse via typetree** — the `TMP_FontAsset` MonoScript class is not registered for this serialized file (`Expected to read 630328 bytes, but only read 48 bytes`). Raw bytes available via `obj.get_raw_data()`.
- Consumed by **title screen menu, story list cards, home screen text**. Patching only the bundle copy leaves this stale — root cause of "story renders correctly but menu/title is garble".

### 3. Atlas pixels — 16 MB Alpha8 4096×4096

Three copies on disk; only the first is sampled at runtime (verified by direct overwrite). The other two must stay consistent because Texture2D `m_StreamData` validates dimensions.

| Location | Offset | Length | Notes |
|---|---|---|---|
| `resources.assets.resS` | 8,690,576 | 16,777,216 | **Actually sampled** by menu and story (ProcMon-confirmed). |
| Bundle archive `.resS` (RocknRollStd slot) | 65,536 | 16,777,216 | Referenced by bundle Texture2D `m_StreamData`. Keep in sync. |
| Bundle archive `.resS` (RocknRollOne slot) | 16,842,752 | 16,777,216 | Referenced by 6 `RocknRollOne (...)` materials. Keep in sync. |

### Materials — no changes needed

12 materials in the bundle (`RocknRollStd SDF (Story)`, `RocknRollOne (Brown Outline)`, etc.). Each `_MainTex` already points to one of the three atlases. Once the atlas pixels are rewritten, every material renders the new font.

## Patch strategy — three orthogonal patches, all required

### Patch A — atlas pixels in `resources.assets.resS`

Overwrite 16,777,216 bytes at offset 8,690,576 with the new Alpha8 pixel block. File size unchanged → no header / asset-table fixups.

### Patch B — bundle font asset (story renderer)

`UnityPy.save_typetree` on the bundle's `RocknRollStd SDF` MonoBehaviour. Transplant `m_CharacterTable`, `m_GlyphTable`, lookup dictionaries, used/free rect lists from the source TMP font. **Preserve** `m_FaceInfo`, atlas Texture2D refs, atlas dimensions, render mode, fallback font asset table. Save bundle (LZ4).

Also overwrite both 16 MB slots inside the bundle's `.resS` with the new atlas bytes.

**`m_FaceInfo` preservation is critical.** The UI was designed against original `m_PointSize = 32` and `m_LineHeight = 64.0` (2× PointSize). A freshly-baked TMP font (e.g. LogoSCLongZhuTi) has TTF-natural `m_LineHeight ≈ 39.68` (~1.24× PointSize). Transplanting that squishes every multi-line dialogue/menu box and causes line overlap. The current script's `transplant_keys_into` enumerates keys to copy and asserts `m_FaceInfo` is not in the list.

**Corollary: bake the source font at `samplingPointSize = 32`** — same as the original `m_FaceInfo.m_PointSize`. TMP renders each glyph as `quadSize = glyphRect × requestedFontSize / m_FaceInfo.m_PointSize`. If the glyph table is baked at 28pt but the preserved `m_FaceInfo.m_PointSize` is 32, every glyph displays at 28/32 ≈ **87.5%** of the intended size. Sampling at 32 makes glyph rects line up with the runtime scale. Atlas character ceiling at 32pt is ~8,800 vs ~10,631 at 28pt, so the charset has to be trimmed accordingly.

### Patch C — hidden font asset in `resources.assets` ⚠️

This is the part the rest of the internet misses. The MonoBehaviour cannot be modified via typetree (UnityPy doesn't have the schema). Because Patch B didn't touch `m_FaceInfo`, those bytes are identical between original and patched → the byte-diff is empty there → `m_LineHeight = 64.0` is auto-preserved. Use the **byte-diff trick**:

1. Build the **same** glyph table transplant on a fresh copy of the bundle's font asset in memory.
2. Serialize that bundle (`env.file.save(packer="lz4")`) and re-load to extract the rewritten 630,328-byte MonoBehaviour blob → `patched_raw`.
3. Diff `patched_raw` against the original bundle font asset → list of byte offsets that changed (all in the glyph-table region).
4. Read the original `resources.assets` pid=27 bytes. The two MonoBehaviours start byte-identical (except the 49-byte `m_Script` PPtr near the header), so the diff offsets line up exactly.
5. **In-place** overwrite only those bytes inside `resources.assets`. File size unchanged → no SerializedFile header or object-table fixups needed.

Ends up modifying ~100 KB of glyph rect / metrics data. Structure, PPtr references, m_Script binding, every other field untouched.

Result: both font assets point into the same atlas positions, atlas has new pixels at those positions, both renderers display the new font.

## Apply

Prereqs:
- A built TMP font bundle (e.g. `logofont.bundle`) with one `TMP_FontAsset` MonoBehaviour + one 4096×4096 Alpha8 SDF atlas Texture2D. The repo ships a prebuilt `logofont.bundle` at the root — build a different one only if you need a different source TTF or character set (see [Building the source font bundle](#building-the-source-font-bundle)).
- CRC patch applied.
- `.bak` of `84ece16f...bundle`, `resources.assets`, `resources.assets.resS` (the swap script auto-creates them if missing).

```bash
uv run -m mercstoria font-swap "<path>/logofont.bundle"
```

Handles A + B + C; mirrors to `$MERCSTORIA_MIRROR_DIR` (defaults to `D:\mercstoria\`) if it exists.

## Building the source font bundle

The font swap consumes a Unity asset bundle containing one `TMP_FontAsset` MonoBehaviour and one 4096×4096 Alpha8 SDF atlas Texture2D. There is no automated path to bake this — Unity Editor is the supported way to produce a TMP font asset, and SDF atlas generation requires the Editor's TMP package.

Two non-negotiable parameters when baking, derived from the patch strategy above:

- **Unity 6000.0.58f2** — the in-place byte-diff in Patch C only works because the source and target MonoBehaviour layouts are bit-identical, which depends on the exact TMP package version shipped with this Unity version.
- **`samplingPointSize = 32`** — must equal the preserved `m_FaceInfo.m_PointSize`, otherwise glyphs render at the wrong scale (see Patch B).

Bake an empty `TMP_FontAsset` via `TMP_FontAsset.CreateFontAsset(font, samplingPointSize: 32, atlasPadding: 5, GlyphRenderMode.SDFAA_HINTED, atlasWidth: 4096, atlasHeight: 4096, ...)`, populate it with `TryAddCharacters(targetChars)`, freeze `atlasPopulationMode = Static`, tag the asset + atlas texture for the same `assetBundleName = "logofont.bundle"`, and `BuildPipeline.BuildAssetBundles(..., BuildAssetBundleOptions.ChunkBasedCompression, BuildTarget.StandaloneWindows64)`. References:

- [TextMeshPro package docs](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/index.html) — `TMP_FontAsset.CreateFontAsset`, `TryAddCharacters`, `HasCharacters`
- [Font Asset Creator workflow](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/FontAssetsCreator.html) — Editor GUI alternative
- [Unity AssetBundle workflow](https://docs.unity3d.com/Manual/AssetBundles-Workflow.html) — building a `StandaloneWindows64` LZ4 bundle

### Character set

`target_chars.txt` is a single line of literal characters (UTF-8, no separators) consumed by `TryAddCharacters`. Generated by [`scripts/export_chars.py`](../scripts/export_chars.py) (CLI: `mercstoria export-chars`) from authoritative source lists in `tools/`. The script splits its sources into two classes:

- **REQUIRED** (never trimmed): ASCII ∪ CJK punctuation ∪ hiragana ∪ katakana ∪ fullwidth/halfwidth symbols ∪ Joyo 2,136 (JP) ∪ **通用规范汉字表一级字 3,500** ∪ every codepoint in any `translate_*.py` at the repo root ∪ (with `--include-corpus`) chars in `extracted_data/**/*.json`.
- **FILL** (capped at remaining headroom, added in 7000hanzi frequency order): 通用规范汉字表二级字 3,000 ∪ the qweyouke "7000" SC list.

The previous frequency-only cap silently dropped ~499 L1 chars (赛 / 翼 / 羹 …). The split guarantees L1 lands in the atlas even when the atlas saturates — the script aborts with an invariant-violated error if any L1 char ends up missing. Current output: 7,800 chars exactly at the atlas ceiling.

**Important:** anything used in a CN translation but NOT in the atlas renders as a random glyph fragment at runtime (Patch A wipes the atlas pixel at that glyph's original rect, but Patch C keeps the rect pointing there). The translation-file scan exists for exactly this reason — add new `translate_*.py` files at the repo root and re-run `mercstoria export-chars` whenever the translation set grows.

### Verifying the bundle

Before running `font-swap`, check the bake reports the expected shape with UnityPy: 1 `MonoBehaviour` + 1 `Texture2D`, `len(m_CharacterTable) ≈ len(m_GlyphTable) ≈ 7800`, `m_FaceInfo.m_PointSize == 32`, atlas `4096×4096 Alpha8`. If `m_CharacterTable` is much smaller than expected, the source TTF is missing those glyphs — pick a wider-coverage font.

## What did NOT work

- **Dynamic atlas regeneration hypothesis** — ruled out by `m_AtlasPopulationMode = 0` (Static).
- **LocalLow CDN cache shadowing** — scanned 38,331 downloaded bundles, no font asset overlay.
- **TMP fallback chain via NBSP slot** — replaced small NBSP bundle (`08c96b...`) with LogoSC content. Works for the bundle font asset's chain, but the hidden font asset in `resources.assets` has its own fallback table pointing into `sharedassets` stubs (Arial SDF / Arial Unicode SDF — 3 and 11 chars). Fallback does NOT reach LogoSC for the menu.
- **MelonLoader / UnityExplorer / BepInEx 6.0.0-pre** — Il2CppInterop crashes on Unity 6000.0.58f2 (`AccessViolationException` in `Class_FromIl2CppType_Hook`); Unhollower can't generate IL2CPP proxies. No usable runtime tool for this Unity version at the time of writing.
- **`UnityPy.save_typetree` on pid=27** — typetree falls back to 48-byte minimal schema and corrupts the file. The byte-diff approach (Patch C) sidesteps this.
- **Replacing pid=27 with patched bytes via `set_raw_data` + `env.file.save()`** — SerializedFile object table / inter-object PPtrs not fixed up when size changes → black-screen on launch. Always preserve original size by patching only the glyph-table region.

## File reference

| Path | Purpose |
|---|---|
| `scripts/font_swap.py` | Universal swap — Patches A + B + C from one source bundle (`mercstoria font-swap <bundle>`) |
| `scripts/export_chars.py` | Build `target_chars.txt` for the TMP bake (`mercstoria export-chars`) |
| `logofont.bundle` (repo root) | Prebuilt source font bundle, ready to feed `font-swap` |

## External references

- [UnityPy](https://github.com/K0lb3/UnityPy) — Unity asset reader/writer; powers Patches B and C
- [TextMeshPro package docs](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/index.html) — `TMP_FontAsset.CreateFontAsset`, `TryAddCharacters`
- [Font Asset Creator workflow](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/FontAssetsCreator.html) — Unity Editor GUI alternative
- [Unity AssetBundle workflow](https://docs.unity3d.com/Manual/AssetBundles-Workflow.html) — building the `StandaloneWindows64` LZ4 bundle the swap consumes
- [Noto fonts](https://fonts.google.com/noto) — free OFL-licensed CJK / Latin / Arabic / etc. coverage
- [Smiley Sans / LogoSC](https://github.com/atelier-anchor/smiley-sans) — wide-coverage CJK font used in our reference build
