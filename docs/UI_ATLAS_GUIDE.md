# UI Atlas Guide — Bitmap-baked UI text

> 中文版请戳[这里](UI_ATLAS_GUIDE_zh-CN.md)。

Some in-game UI text is not rendered by TMP_Text at runtime — it is **painted
directly into texture atlases** and shipped as pixel data. Translating these
strings means editing the atlas pixels and writing back into every place the
game might load them from. Companion to
[`FONT_REPLACEMENT_GUIDE.md`](FONT_REPLACEMENT_GUIDE.md) (engine-rendered
text) and [`STORY_BUNDLE_GUIDE.md`](STORY_BUNDLE_GUIDE.md) (encrypted text
payloads).

Game environment: see [`README.md`](../README.md#game-environment-canonical).
The CRC patch ([`CRC_PATCH_GUIDE.md`](CRC_PATCH_GUIDE.md)) must be applied
first or modified bundles silently revert.

## The four atlases

| Atlas      | Bundle hash                       | Storage       | Atlas size  | Format  | Sprites |
|------------|-----------------------------------|---------------|-------------|---------|---------|
| CommonUI   | `f17951921426b535e20de01adc4f06c3` | StreamingAssets | 2048×1024 | RGBA32 | 189 |
| GalleryUI  | `6e4d5e586bb1bdffd38c58f19f8ba84e` | StreamingAssets | 1024×2048 | RGBA32 |  62 |
| HomeUI     | `fd6c29755bc7150eb79d2d669abd3f6e` | StreamingAssets |  256× 256 | RGBA32 |   5 |
| FooterUI   | `6936cdaddf3fa06b26de3570c16593a6` | CDN (BundleAssets) | 512× 512 | DXT5 →   22 cells (SpriteStudio) |

CommonUI / GalleryUI / HomeUI are Unity **SpriteAtlas** bundles. FooterUI is a
SpriteStudio `dc_` cellmap (its 22 cells live in a single 512×512 texture
named `footer_menu_m_512`); the repack auto-promotes DXT5 → RGBA32 to avoid
compounding encoder noise on translator artwork.

The `TARGETS` list at the top of
[`scripts/extract_ui_atlas.py`](../scripts/extract_ui_atlas.py) holds the
canonical name / hash / storage tier for each.

## Where the pixels live (three places, not one)

Crucial: **CommonUI, GalleryUI, and HomeUI each have a second, self-contained
copy baked into the player's main asset files.** Modifying only the
Addressables bundle is not enough — the game preloads `sharedassets` and
binds Sprites to the embedded atlas first, leaving the bundle copy
unconsulted for the affected screens.

| Atlas | Addressables bundle | sharedassets duplicate |
|---|---|---|
| CommonUI  | `<_Data>/StreamingAssets/aa/StandaloneWindows64/<hash>.bundle` | `<_Data>/sharedassets5.assets` (Texture2D pid 3, SpriteAtlas pid 256) |
| GalleryUI | `<_Data>/StreamingAssets/aa/StandaloneWindows64/<hash>.bundle` | `<_Data>/sharedassets5.assets` (Texture2D pid 4, SpriteAtlas pid 257) |
| HomeUI    | `<_Data>/StreamingAssets/aa/StandaloneWindows64/<hash>.bundle` | `<_Data>/sharedassets7.assets` (Texture2D pid 9, SpriteAtlas pid 20) |
| FooterUI  | `<game>/AssetBundle/StandaloneWindows64/BundleAssets/<hash>.bundle` | *(none — CDN-only)* |

The sharedassets `SpriteAtlas` has `m_RenderDataMap` entries that point to a
texture with `file_id=0` (same file), so each duplicate is fully
self-contained. Pixel data lives in the matching `.resS` sidecar.

`repack-ui-atlas` patches **both** locations in one pass.

## Pipeline

```
  source bundle (StreamingAssets/aa or BundleAssets)
                       │
                       │ extract-ui-atlas
                       ▼
  extracted_data/ui_atlas/<Atlas>/
      _meta.json            bundle info + per-sprite rect / rotation / path_id
      _atlas.png            full reference image (read-only)
      sprites/<name>.png    one PNG per editable sprite or cell
  extracted_data/.ui_atlas_fingerprints.pkl   per-PNG hash baseline

         (translator edits / overwrites sprites/<name>.png)

                       │ repack-ui-atlas
                       ▼
  repacked_bundles/ui_atlas/<hash>.bundle                      modified bundle
  repacked_bundles/ui_atlas_sharedassets/sharedassets*.assets  patched <_Data> twins

                       │ deploy
                       ▼
  <_Data>/StreamingAssets/aa/StandaloneWindows64/<hash>.bundle  ← CommonUI / GalleryUI / HomeUI
  <game>/AssetBundle/StandaloneWindows64/BundleAssets/<hash>.bundle ← FooterUI
  <_Data>/sharedassets{5,7}.assets                              ← inlined duplicates
       (originals mirrored once: …_old / .bak)
```

### Commands

```bash
uv run -m mercstoria extract-ui-atlas             # dumps all 4 atlases
uv run -m mercstoria repack-ui-atlas              # only atlases with edited PNGs
uv run -m mercstoria repack-ui-atlas --force      # repack everything (first-time bootstrap)
uv run -m mercstoria deploy                       # pushes both bundles and sharedassets
```

`repack-ui-atlas` skips any atlas whose `sprites/` PNGs all hash to the
extract-time baseline. Pass `--force` to repack regardless — required on the
first run after extending the toolkit, since the existing baselines were
captured before the sharedassets-patch path existed.

## How extract works

For each `SpriteAtlas` bundle:

1. Read the `SpriteAtlas` and build `(guid, fileId) → SpriteAtlasData` from
   `m_RenderDataMap`. The map's `textureRect` is the packed atlas rect
   (origin: bottom-left of the texture); `settingsRaw` carries the
   rotation/format flags.
2. For each `Sprite`, look up its `m_RenderDataKey` in the map. Translate
   atlas-coordinate `(x, y, w, h)` to a top-left PIL crop box via
   `top = atlas_h - (y + h)`. Apply the inverse of any packer rotation so
   the translator sees the sprite as it appears on screen.
3. Crop the atlas image and save as `sprites/<safe_name>.png`. Record the
   crop box and rotation in `_meta.json` so repack pastes back at the exact
   same pixels.

For the FooterUI SpriteStudio bundle, the cellmap is a `dc_` MonoBehaviour
holding `TableCellMap[].TableCell[].Rectangle` entries. Rects are
already top-left; rotation is always 0; multiple sub-atlases per cellmap
are paired to textures by name (exact / prefix match).

`settingsRaw` bit layout (Unity 2017+): `bit 0 packed, bit 1 mode,
bits 2-5 rotation, bits 6-11 format`. Rotation 0–3 are self-inverse
(flip / 180°) so extract and repack share one routine; rotation 4 (90° pack)
changes cell aspect and is rejected at extract time. All four shipped
atlases have rotation 0 today.

## How repack works

1. For each target, hash the PNGs under `sprites/` against the
   `.ui_atlas_fingerprints.pkl` baseline; an atlas is considered "edited"
   only if at least one PNG has changed (or `--force` is set).
2. Load the source bundle, locate its `Texture2D`, build a fresh RGBA32
   `canvas = tex.image.convert("RGBA")` so unedited regions stay pristine.
3. For each edited sprite: open the PNG, re-apply the packer rotation,
   resize to the recorded rect if the translator rendered at a different
   scale, then `canvas.paste(edit_img, (left, top))`.
4. If the texture format is in the block-compressed `LOSSY_FORMATS` set
   (DXT/BC/ETC/ASTC/PVRTC + crunched variants), promote to RGBA32. The cost
   is ~2.5× the affected texture's bytes; trivial for a UI atlas, and it
   avoids a round of encoder noise on translator artwork.
5. `tex.set_image(canvas)` + `tex.save()` writes the new pixels into the
   asset; `env.file.save(packer="lz4")` writes the bundle out. Match the
   rest of the toolkit on **`lz4`**, not `lz4hc` — UnityPy's lz4hc is
   misimplemented and produces bundles the game refuses.
6. Aggregate every modified canvas keyed by `Texture2D.m_Name`, then call
   `_patch_sharedassets`:
   - Scan `<_Data>/sharedassets*.assets`, match Texture2D by `m_Name`
     (the `sactx-0-<W>x<H>-Uncompressed-<Atlas>-<hashId>` convention).
   - For each match, set the same canvas via `tex.set_image` + `tex.save`.
     This inlines the pixels into the `.assets` itself and clears
     `m_StreamData.offset` for that texture. Other textures in the same
     file keep their `m_StreamData` pointers and continue reading from the
     unchanged `.resS`.
   - Write the patched `.assets` to
     `repacked_bundles/ui_atlas_sharedassets/<name>.assets`.

The match is **by texture name**, not hardcoded path_ids — robust against
future game updates that renumber objects, as long as the
`sactx-0-…-<Atlas>-<hashId>` naming convention holds.

## Deploy

`scripts/deploy.py` routes each output by `source_dir` in `TARGETS`:

- `sa` (CommonUI, GalleryUI, HomeUI) → `<_Data>/StreamingAssets/aa/StandaloneWindows64/`
- `ba` (FooterUI)                    → `<game>/AssetBundle/StandaloneWindows64/BundleAssets/`

Each replaced bundle is mirrored once into `…_old/` next to the live tree
(`StandaloneWindows64_old/` for SA, `AssetBundle_old/` for BA); the mirror is
never overwritten on subsequent deploys — first-seen wins.

Patched sharedassets go to `<_Data>/sharedassets*.assets` with the original
mirrored once to `<name>.assets.bak` alongside. The `.resS` sidecar is **not**
touched.

### Rollback

```bat
cd "<game>\<APP>_Data"
copy sharedassets5.assets.bak sharedassets5.assets /Y
copy sharedassets7.assets.bak sharedassets7.assets /Y
```

For bundles, restore the matching files from the `…_old/` mirrors.

## Adding more atlases

1. Drop the bundle name / hash / `source_dir` / `kind` into `TARGETS` in
   [`scripts/extract_ui_atlas.py`](../scripts/extract_ui_atlas.py).
2. Run `extract-ui-atlas` — generates `_meta.json` + `sprites/`.
3. Edit (or overwrite) the relevant `sprites/<name>.png` files.
4. `repack-ui-atlas --force` (or omit `--force` once the fingerprint baseline
   is current).
5. `deploy`.

If the new atlas is a SpriteStudio cellmap with rotation = 4 anywhere, the
extract aborts that sprite with a warning — extend `apply_rotation_*` first.

## Diagnostics

- **Translation not visible in-game:** the bundle deployed correctly but the
  sharedassets duplicate is still pristine. Either run `repack-ui-atlas
  --force` and re-`deploy`, or grep `<_Data>/sharedassets*.assets` for the
  atlas's `sactx-…` texture name to confirm where the duplicate lives.
- **`source bundle missing` from repack:** the source bundle is read from
  the LIVE game folder (not from `repacked_bundles/`). Run `deploy` once to
  populate the live tree, then re-extract.
- **Bundle decodes but game refuses to load it:** check that you packed with
  `packer="lz4"` (not `lz4hc`) and that the CRC patch is still applied
  (`verify-patches`).

## File map

```
scripts/extract_ui_atlas.py             extract + repack + sharedassets patch
scripts/deploy.py::_deploy_ui_atlas     bundle routing (sa / ba)
scripts/deploy.py::_deploy_ui_atlas_sharedassets   patched .assets → <_Data>/
extracted_data/ui_atlas/<Atlas>/        per-atlas dump (meta + sprites)
extracted_data/.ui_atlas_fingerprints.pkl   per-PNG sha256 baseline
repacked_bundles/ui_atlas/              modified bundles
repacked_bundles/ui_atlas_sharedassets/ patched sharedassets*.assets
```

## External references

- [Unity SpriteAtlas v2 — settingsRaw bit layout](https://docs.unity3d.com/Manual/sprite-atlas.html)
- [UnityPy](https://github.com/K0lb3/UnityPy) — SpriteAtlas / Texture2D / SerializedFile read+write
- [SpriteStudio6 player runtime](https://github.com/SpriteStudio/SpriteStudio6-SDK) — `dc_` cellmap shape reference
