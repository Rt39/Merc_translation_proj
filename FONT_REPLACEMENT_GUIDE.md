# Merc Storia (メルストM) — Font Replacement Guide

End-to-end documentation of how the game loads its UI font, what we discovered in the process, and the patch pipeline that swaps the bundled Japanese font (`RocknRollStd SDF`) for an arbitrary TMP font.

## Game environment

- **Engine**: Unity 6000.0.58f2, IL2CPP, Windows x64 (Steam)
- **Game folder**: `<Steam>/steamapps/common/メルクストーリア - 癒術士と心の旋律 -/`
- **Player path**: `メルストM.exe` + `メルストM_Data/`
- **CDN cache** (LocalLow downloads, ~38k bundles): `%LOCALAPPDATA%\..\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64\`
- **Bundle CRC checks**: already neutered in `GameAssembly.dll` at 4 sites (`xor edx, edx`). Without this patch, modifying any bundle triggers a "data corruption" path.

## Anatomy of the UI font system

The game does NOT store its primary UI font in a single place. It is **split** across three locations, and patching only one produces partial garble.

### 1. Bundle font asset — used by story screens

- **File**: `StreamingAssets/aa/StandaloneWindows64/84ece16f121defbfc5b83acb86f5870c.bundle`
- **MonoBehaviour pathID**: `6189425675716077201`
- **Name**: `RocknRollStd SDF`
- **References atlas Texture2D** at bundle-local pathID `-4881587269468215663`
- **UnityPy can parse this fully** (MonoScript reference resolvable inside the bundle).
- Story screens (in-game dialogue, story title cards) consume this font asset.

### 2. Hidden font asset in `resources.assets` — used by title / menu / home

- **File**: `メルストM_Data/resources.assets`
- **MonoBehaviour pathID**: `27`, byte offset `173728`, size `630328`
- **Name**: `RocknRollStd SDF` (same name as the bundle copy — they are byte-identical except 49 bytes of `m_Script` PPtr)
- **References atlas Texture2D** at pid `10` (the texture in `resources.assets`).
- **UnityPy CANNOT parse via typetree** — the MonoScript class for `TMP_FontAsset` is not registered for this serialized file. You will see `Expected to read 630328 bytes, but only read 48 bytes`. The raw bytes are available via `obj.get_raw_data()`.
- This is the font asset the **title screen menu, story list cards, and home screen text** actually consume. Patching only the bundle copy leaves this stale, which is the root cause of "story renders correctly but menu/title is garble."

### 3. Atlas pixel data — 16 MB Alpha8 4096×4096

Three physical copies exist on disk, but only the first is sampled by the shader at runtime (verified by direct overwrite tests). The other two must still be kept consistent because the *Texture2D referenced by some materials* is `m_StreamData`-backed and Unity validates dimensions.

| Location | Offset | Length | Notes |
|---|---|---|---|
| `resources.assets.resS` | 8,690,576 | 16,777,216 | **Actually sampled** by both menu and story. Confirmed by ProcMon. |
| Bundle archive `.resS` (RocknRollStd slot) | 65,536 | 16,777,216 | Referenced by bundle Texture2D `m_StreamData`. Keep in sync. |
| Bundle archive `.resS` (RocknRollOne slot) | 16,842,752 | 16,777,216 | Referenced by 6 `RocknRollOne (...)` materials. Keep in sync. |

### 4. Materials (no changes needed)

The bundle holds 12 materials (`RocknRollStd SDF (Story)`, `RocknRollOne (Brown Outline)`, etc.). Each `_MainTex` already points to one of the three atlas textures above. After the atlas content is rewritten, every material renders the new font automatically — no material edits are required.

## What the menu was actually doing

Findings, in the order they were established:

1. **Atlas is shared**: ProcMon shows a single 16 MB read from `resources.assets.resS` at offset 8,690,576 covering both title-screen render and story-screen render.
2. **Story works after bundle font swap** but menu does not → menu sampled the shared atlas with **a stale char→glyph rect mapping**.
3. **Search for the stale mapping**: grep the bytes `RocknRollStd SDF` across every game file:
   - `resources.assets` — **2 hits**
   - `sharedassets4.assets` — 8 hits (all materials, no font asset)
   - `catalog.bin` — 6 hits (Addressables references)
4. The two hits in `resources.assets` correspond to (a) the Texture2D `m_Name` field, and (b) a fully-formed `TMP_FontAsset` MonoBehaviour that UnityPy silently skipped because the MonoScript binding was unavailable.

That second hit IS the menu's mapping.

## Patch strategy

Three orthogonal patches, all needed:

### Patch A — atlas pixels in `resources.assets.resS`

Overwrite 16,777,216 bytes at offset 8,690,576 with the new Alpha8 pixel block from the source font bundle. The file size stays exactly identical → no header / asset-table fixups required.

### Patch B — bundle font asset (story renderer)

Use `UnityPy.save_typetree` on the bundle's `RocknRollStd SDF` MonoBehaviour: transplant `m_CharacterTable`, `m_GlyphTable`, lookup dictionaries, and used/free rect lists from the source TMP font.

**Preserve** `m_FaceInfo`, atlas Texture2D references, atlas dimensions, render mode, and the fallback font asset table.

The `m_FaceInfo` preservation is critical: the UI was designed against the original `RocknRollStd m_LineHeight = 64.0` (2× PointSize). A freshly-baked LogoSCLongZhuTi font has the TTF's natural `m_LineHeight ≈ 39.68` (~1.24× PointSize). Transplanting that would squish every multi-line dialogue / menu box and cause line overlap. The current script's `transplant_keys_into` explicitly enumerates the keys to copy and asserts `m_FaceInfo` is not in the list.

Then save the bundle (LZ4).

Also overwrite both 16 MB slots inside the bundle's archived `.resS` with the new atlas bytes.

### Patch C — hidden font asset in `resources.assets` (menu / title renderer) ⚠️

This is the part the rest of the internet misses. The MonoBehaviour can NOT be modified via typetree — UnityPy doesn't have the schema. Because the patched bundle from Patch B did NOT touch `m_FaceInfo`, those bytes are identical between original and patched, the byte-diff is empty there, and `m_LineHeight = 64.0` is automatically preserved in `resources.assets` too. Use the **byte-diff** trick:

1. Build the **same** glyph table transplant on a fresh copy of the bundle's font asset in memory.
2. Serialize that bundle (`env.file.save(packer="lz4")`) and re-load to extract the rewritten 630,328-byte MonoBehaviour blob (call it `patched_raw`).
3. Diff `patched_raw` against the original bundle font asset → list of byte offsets that changed (all in the glyph-table region).
4. Read the original `resources.assets` pid=27 bytes. Because the two MonoBehaviours start byte-identical (except 49 bytes of `m_Script` PPtr near the header), the diff offsets line up exactly.
5. **In-place** overwrite only those bytes inside `resources.assets`. The file size is unchanged → no SerializedFile header or object-table fixups needed.

The patch ends up modifying ~100 KB of glyph rect / metrics data inside `resources.assets`. The structure, the PPtr references, the m_Script binding, and every other field stay untouched.

Result: both font assets now point into the same atlas positions, the atlas has new pixels at those positions, and both renderers display the new font.

## Steps to reproduce

Prerequisites:
- A built TMP font bundle (e.g. `logofont.bundle`) containing one `TMP_FontAsset` MonoBehaviour and one 4096×4096 Alpha8 SDF atlas Texture2D.
- `uv` available; we always run `uv run --with UnityPy --with lz4 --with numpy --with Pillow script.py` — never `pip install`.
- One-time CRC patch on `GameAssembly.dll` (4 sites `xor edx, edx`). Without it Unity refuses the modified bundle silently.
- `.bak` of the three target files: `84ece16f...bundle`, `resources.assets`, `resources.assets.resS`. The swap script auto-creates them if missing.

Generating a suitable font bundle (Unity Editor, batch-mode):

```bash
# In a Unity 6000.0.58f2 project containing:
#  - Assets/<your-font>.ttf
#  - Assets/target_chars.txt  (comma-separated decimal unicode codepoints)
#  - Assets/Editor/RegenAndBuildFont.cs  (uses TMP_FontAsset.CreateFontAsset + TryAddCharacters)
"C:/Program Files/Unity 6000.0.58f2/Editor/Unity.exe" \
  -batchmode -nographics -quit \
  -projectPath "<path/to/project>" \
  -executeMethod RegenAndBuildFont.RegenAndBuild \
  -logFile build.log
```

The included script bakes at `samplingPointSize: 28, atlasPadding: 5, GlyphRenderMode.SDFAA_HINTED, 4096×4096, AtlasPopulationMode.Dynamic→Static`. PointSize 28 fits 10,631 chars (the union of 7007 original Japanese + GB2312 + chars seen in translation). At 32pt, ~8,800 chars is the ceiling.

Applying the swap:

```bash
uv run --with UnityPy --with lz4 --with numpy --with Pillow \
  font_swap.py "<path>/logofont.bundle"
```

The script handles A, B, and C and mirrors patches to a `D:\mercstoria\` copy if it exists.

## What we tried that did NOT work

- **Dynamic atlas regeneration hypothesis** — ruled out by `m_AtlasPopulationMode = 0` (Static).
- **LocalLow CDN cache shadowing** — scanned 38,331 downloaded bundles, no font asset overlay exists.
- **TMP fallback chain via NBSP slot** — replaced the small NBSP bundle (`08c96b...`) with LogoSC content. Fallback works for the bundle font asset's chain, but the hidden font asset in `resources.assets` has its own fallback table pointing into `sharedassets` stubs (Arial SDF, Arial Unicode SDF — 3 and 11 chars). Fallback does NOT reach LogoSC for the menu.
- **MelonLoader / UnityExplorer runtime introspection** — Il2CppInterop crashes on Unity 6000.0.58f2's metadata format inside `Class_FromIl2CppType_Hook` (AccessViolationException). No usable runtime tool for this Unity version at the time of writing.
- **BepInEx 6.0.0-pre.x** — Unhollower can't generate IL2CPP proxy assemblies (`MonoPosixHelper` missing), can't run on Unity 6000.x at all.
- **`UnityPy.save_typetree` on pid=27** — fails because MonoScript class for `TMP_FontAsset` isn't registered in the resources.assets serialized file; the typetree falls back to a 48-byte minimal schema and reading or saving corrupts the file. The in-place byte-diff approach (Patch C above) sidesteps this entirely.
- **Replacing the whole pid=27 with patched bytes via `set_raw_data` then `env.file.save()`** — the SerializedFile object table and inter-object PPtrs aren't fixed up correctly when the size changes, producing a black-screen on launch. Always preserve the original size by patching only the glyph-table region.

## File reference (for future work)

| Path | Purpose |
|---|---|
| `D:\cs\workshop\font_swap.py` | Universal swap script (Patches A+B+C from one source bundle) |
| `D:\cs\workshop\inplace_byte_patch3.py` | Patch C standalone (resources.assets pid=27 in-place) |
| `D:\cs\workshop\do_font_swap_final.py` | Patches A + B (atlas + bundle font asset) |
| `D:\cs\workshop\export_chars2.py` | Build the union character set for font regeneration |
| `D:\cs\workshop\unity_proj\My project\Assets\Editor\RegenAndBuildFont.cs` | Unity batch-mode font asset generator |
| `D:\cs\workshop\merc_storia_toolkit.py` | Story-text extraction + translation pipeline (encrypted MemoryPack TextAssets) |
| `D:\cs\workshop\dump\dump.cs` | Il2CppDumper output — for symbol lookup |
