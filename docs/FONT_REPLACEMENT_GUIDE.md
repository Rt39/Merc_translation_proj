# Merc Storia — Font Replacement Guide

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

## Exploration route (how we got here)

1. ProcMon: single 16 MB read from `resources.assets.resS` at offset 8,690,576 covers both title and story renders → atlas is shared.
2. Story works after swapping the bundle font, menu does not → menu samples the shared atlas with a **stale char → glyph-rect mapping**.
3. Grep bytes `RocknRollStd SDF` across every game file:
   - `resources.assets` — **2 hits**
   - `sharedassets4.assets` — 8 hits (all materials, no font asset)
   - `catalog.bin` — 6 hits (Addressables references)
4. The two `resources.assets` hits = (a) Texture2D `m_Name`, (b) a fully-formed `TMP_FontAsset` MonoBehaviour UnityPy silently skipped because the MonoScript binding was unavailable. **That second hit IS the menu's mapping.**

## Patch strategy — three orthogonal patches, all required

### Patch A — atlas pixels in `resources.assets.resS`

Overwrite 16,777,216 bytes at offset 8,690,576 with the new Alpha8 pixel block. File size unchanged → no header / asset-table fixups.

### Patch B — bundle font asset (story renderer)

`UnityPy.save_typetree` on the bundle's `RocknRollStd SDF` MonoBehaviour. Transplant `m_CharacterTable`, `m_GlyphTable`, lookup dictionaries, used/free rect lists from the source TMP font. **Preserve** `m_FaceInfo`, atlas Texture2D refs, atlas dimensions, render mode, fallback font asset table. Save bundle (LZ4).

Also overwrite both 16 MB slots inside the bundle's `.resS` with the new atlas bytes.

**`m_FaceInfo` preservation is critical.** The UI was designed against original `m_LineHeight = 64.0` (2× PointSize). A freshly-baked TMP font (e.g. LogoSCLongZhuTi) has TTF-natural `m_LineHeight ≈ 39.68` (~1.24× PointSize). Transplanting that squishes every multi-line dialogue/menu box and causes line overlap. The current script's `transplant_keys_into` enumerates keys to copy and asserts `m_FaceInfo` is not in the list.

### Patch C — hidden font asset in `resources.assets` ⚠️

This is the part the rest of the internet misses. The MonoBehaviour cannot be modified via typetree (UnityPy doesn't have the schema). Because Patch B didn't touch `m_FaceInfo`, those bytes are identical between original and patched → the byte-diff is empty there → `m_LineHeight = 64.0` is auto-preserved. Use the **byte-diff trick**:

1. Build the **same** glyph table transplant on a fresh copy of the bundle's font asset in memory.
2. Serialize that bundle (`env.file.save(packer="lz4")`) and re-load to extract the rewritten 630,328-byte MonoBehaviour blob → `patched_raw`.
3. Diff `patched_raw` against the original bundle font asset → list of byte offsets that changed (all in the glyph-table region).
4. Read the original `resources.assets` pid=27 bytes. The two MonoBehaviours start byte-identical (except the 49-byte `m_Script` PPtr near the header), so the diff offsets line up exactly.
5. **In-place** overwrite only those bytes inside `resources.assets`. File size unchanged → no SerializedFile header or object-table fixups needed.

Ends up modifying ~100 KB of glyph rect / metrics data. Structure, PPtr references, m_Script binding, every other field untouched.

Result: both font assets point into the same atlas positions, atlas has new pixels at those positions, both renderers display the new font.

## Reproduce

Prereqs:
- A built TMP font bundle (e.g. `logofont.bundle`) with one `TMP_FontAsset` MonoBehaviour + one 4096×4096 Alpha8 SDF atlas Texture2D. **Baking this bundle is the only manual Unity Editor step** — see [Building the source font bundle](#building-the-source-font-bundle) below.
- CRC patch applied.
- `.bak` of `84ece16f...bundle`, `resources.assets`, `resources.assets.resS` (the swap script auto-creates them if missing).

Applying:

```bash
uv run font_swap.py "<path>/logofont.bundle"
```

Handles A + B + C; mirrors to `$MERCSTORIA_MIRROR_DIR` (defaults to `D:\mercstoria\`) if it exists.

## Building the source font bundle

The font swap consumes a Unity asset bundle containing one `TMP_FontAsset` MonoBehaviour and one 4096×4096 Alpha8 SDF atlas Texture2D. There's no automated path to bake this — Unity Editor is the supported way to produce a TMP font asset, and SDF atlas generation requires the Editor's TMP package. Below is the full procedure starting from a vanilla Unity install.

### What you need

| Tool | Version / source | Why |
|---|---|---|
| [Unity Hub](https://unity.com/download) | latest | install + license the Editor |
| Unity Editor | **6000.0.58f2** | matches the game; same TMP package version → identical typetree shape |
| [TextMeshPro](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/index.html) package | bundled with 6000.x as `com.unity.ugui` | `TMP_FontAsset.CreateFontAsset` + `TryAddCharacters` API |
| Source font | any `.ttf` / `.otf` covering your target script | e.g. [LogoSC Long Zhu](https://github.com/atelier-anchor/smiley-sans) for CJK, [Noto Sans](https://fonts.google.com/noto) for everything |
| `target_chars.txt` | comma-separated decimal codepoints | the character set to bake into the atlas |

The Unity version is non-negotiable. The exact MonoBehaviour layout depends on the TMP package version — baking with TMP 4.x produces a typetree with a different field order, and the in-place `resources.assets` byte-diff in Patch C only works because the layouts are bit-identical between source and target.

### Step 1: create the project

1. **Unity Hub → New project** → **3D (Built-In Render Pipeline)** template.
2. Pick **6000.0.58f2** as the editor version. Earlier 6000.x builds *might* work but the patch RVAs were derived against 0.58f2 — don't deviate without re-verifying.
3. Project name: anything ASCII. Avoid Japanese characters in the path — TMP's font baker has historically had issues with non-ASCII paths.
4. After it opens: **Window → TextMeshPro → Import TMP Essential Resources**. Required for the default shader and dynamic atlas materials.

### Step 2: drop in the font and character set

Create the following under `Assets/`:

```
Assets/
├── <your-font>.ttf                 source font, copied in
├── target_chars.txt                comma-separated decimal codepoints
├── Editor/
│   └── RegenAndBuildFont.cs        the bake script (below)
└── AssetBundles/                   output goes here (created by the script)
```

`target_chars.txt` is a single line of decimal codepoints, comma-separated. Generate it from the original JP character set unioned with whatever your target script needs. For CJK translations:

```
# Quick recipe — union of:
#   * 7,007 original JP characters from the game (extracted from RocknRollStd SDF)
#   * GB2312 (~6,763 chars) for simplified Chinese
#   * any chars that appeared in your translation drafts
# Resulting list lands around 10,000-11,000 chars. PointSize 28 fits ~10,631
# into a 4096×4096 atlas; 32pt drops the ceiling to ~8,800.
```

A working `export_chars.py` is left to the reader; the canonical reference is the [TMP_FontAsset.HasCharacters](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/api/TMPro.TMP_FontAsset.html#TMPro_TMP_FontAsset_HasCharacters_System_String_) docs.

### Step 3: write the bake script

Paste into `Assets/Editor/RegenAndBuildFont.cs`:

```csharp
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
using TMPro;

public static class RegenAndBuildFont
{
    // Invoked via: Unity.exe -batchmode -executeMethod RegenAndBuildFont.RegenAndBuild
    public static void RegenAndBuild()
    {
        // ----- 1. Locate the source TTF dropped into Assets/ ------------------
        var ttfPath = Directory
            .GetFiles("Assets", "*.ttf", SearchOption.TopDirectoryOnly)
            .FirstOrDefault();
        if (ttfPath == null)
            throw new System.Exception("No .ttf found in Assets/");

        var ttf = AssetDatabase.LoadAssetAtPath<Font>(ttfPath);
        if (ttf == null)
            throw new System.Exception($"Failed to load font at {ttfPath}");

        // ----- 2. Bake the empty font asset -----------------------------------
        // These constants must match the game's original RocknRollStd SDF or
        // the byte-diff Patch C will not line up.
        var fontAsset = TMP_FontAsset.CreateFontAsset(
            font:             ttf,
            samplingPointSize: 28,
            atlasPadding:      5,
            renderMode:        GlyphRenderMode.SDFAA_HINTED,
            atlasWidth:        4096,
            atlasHeight:       4096,
            atlasPopulationMode: AtlasPopulationMode.Dynamic,
            enableMultiAtlasSupport: false);

        // ----- 3. Add the target characters -----------------------------------
        var chars = File.ReadAllText("Assets/target_chars.txt")
            .Split(',')
            .Select(s => s.Trim())
            .Where(s => s.Length > 0)
            .Select(int.Parse)
            .Select(System.Char.ConvertFromUtf32)
            .ToArray();
        var charSet = string.Concat(chars);
        if (!fontAsset.TryAddCharacters(charSet, out string missing))
            Debug.LogWarning($"[bake] missing {missing.Length} chars: {missing}");

        // ----- 4. Freeze the atlas as Static so the game's runtime trusts it --
        fontAsset.atlasPopulationMode = AtlasPopulationMode.Static;
        EditorUtility.SetDirty(fontAsset);
        AssetDatabase.CreateAsset(fontAsset, "Assets/logofont.asset");
        AssetDatabase.SaveAssets();

        // ----- 5. Tag for asset-bundle build ----------------------------------
        var importer = AssetImporter.GetAtPath("Assets/logofont.asset");
        importer.assetBundleName = "logofont.bundle";

        var atlasTex = fontAsset.atlasTextures.FirstOrDefault();
        if (atlasTex != null)
        {
            var atlasPath = AssetDatabase.GetAssetPath(atlasTex);
            AssetImporter.GetAtPath(atlasPath).assetBundleName = "logofont.bundle";
        }

        // ----- 6. Build the bundle in StandaloneWindows64 format --------------
        Directory.CreateDirectory("Assets/AssetBundles");
        BuildPipeline.BuildAssetBundles(
            "Assets/AssetBundles",
            BuildAssetBundleOptions.ChunkBasedCompression, // → LZ4, matches game
            BuildTarget.StandaloneWindows64);

        Debug.Log("[bake] OK — Assets/AssetBundles/logofont.bundle");
    }
}
```

### Step 4: run the build

Headless from a shell:

```powershell
& "C:\Program Files\Unity\Hub\Editor\6000.0.58f2\Editor\Unity.exe" `
    -batchmode -nographics -quit `
    -projectPath "<absolute path to project>" `
    -executeMethod RegenAndBuildFont.RegenAndBuild `
    -logFile build.log
```

Or just hit **Assets → Build → RegenAndBuildFont.RegenAndBuild** from inside the Editor (after wiring a custom menu, or by calling it from a one-shot Editor menu item).

Build output: `<project>/Assets/AssetBundles/logofont.bundle`. This is what `font_swap.py` consumes.

### Step 5: verify the bundle

Before running `font_swap.py`, sanity-check the bake:

```bash
uv run python -c "
import UnityPy
env = UnityPy.load(r'<project>/Assets/AssetBundles/logofont.bundle')
fonts   = [o for o in env.objects if o.type.name == 'MonoBehaviour']
atlases = [o for o in env.objects if o.type.name == 'Texture2D']
print(f'fonts: {len(fonts)}, atlases: {len(atlases)}')
for f in fonts:
    tt = f.read_typetree()
    if 'm_CharacterTable' in tt:
        fi = tt.get('m_FaceInfo', {})
        print(f'  chars={len(tt[\"m_CharacterTable\"])}'
              f' glyphs={len(tt[\"m_GlyphTable\"])}'
              f' line_height={fi.get(\"m_LineHeight\")}'
              f' point_size={fi.get(\"m_PointSize\")}')
for t in atlases:
    d = t.read()
    print(f'  atlas {d.m_Width}x{d.m_Height} fmt={d.m_TextureFormat}')
"
```

Expected:

```
fonts: 1, atlases: 1
  chars=10631 glyphs=10631 line_height=39.something point_size=28
  atlas 4096x4096 fmt=Alpha8
```

If `chars` is much smaller than expected, your `target_chars.txt` failed `TryAddCharacters` for most codepoints — usually because the source TTF doesn't have them. Pick a font with wider coverage or split into multiple `TryAddCharacters` calls and accept a smaller subset.

### Step 6: swap it in

```bash
uv run font_swap.py "<project>/Assets/AssetBundles/logofont.bundle"
```

This handles A + B + C automatically. Launch the game; verify both story dialogue (Patch B) and the title screen / chapter list (Patch C) render with the new font.

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
| `font_swap.py` | Universal swap — Patches A + B + C from one source bundle |

## External references

- [UnityPy](https://github.com/K0lb3/UnityPy) — Unity asset reader/writer; powers Patches B and C
- [TextMeshPro package docs](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/index.html) — `TMP_FontAsset.CreateFontAsset`, `TryAddCharacters`
- [Font Asset Creator workflow](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/manual/FontAssetsCreator.html) — Unity Editor GUI alternative to the bake script
- [Noto fonts](https://fonts.google.com/noto) — free OFL-licensed CJK / Latin / Arabic / etc. coverage
- [Smiley Sans / LogoSC](https://github.com/atelier-anchor/smiley-sans) — wide-coverage CJK font used in our reference build
