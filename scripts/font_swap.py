"""Merc Storia font swap — universal pipeline.

Takes a single TMP font bundle (containing one TMP_FontAsset MonoBehaviour + one
4096x4096 Alpha8 SDF atlas Texture2D) and applies all three patches needed to
make the new font render across the entire game:

  A) overwrite the shared atlas pixels in `resources.assets.resS`
  B) transplant the glyph/char tables into the bundle's font asset
     (84ece16f...bundle, pathID 6189425675716077201) — fixes story rendering
  C) byte-diff patch the HIDDEN font asset in `resources.assets` (pid=27)
     — fixes title/menu/home rendering. This MonoBehaviour cannot be parsed
     via UnityPy typetree because the MonoScript binding for TMP_FontAsset is
     not registered in resources.assets's serialized file.

Usage:
    uv run -m mercstoria font-swap <path-to-font-bundle> [--game-dir <steam install path>]

If --game-dir is omitted the default Steam path is used. If a `D:\\mercstoria\\`
sibling install exists the patches are mirrored to it too (useful when you keep
an ASCII-path test copy).

Prerequisites:
  * GameAssembly.dll CRC checks already patched (4 sites: xor edx, edx)
  * .bak files for the three patched paths are auto-created on first run

What it does NOT do:
  * It does not modify the bundle's RocknRollOne atlas slot pixel-for-pixel
    differently from the RocknRollStd slot — both get the same new atlas bytes.
    Six menu materials reference RocknRollOne_Atlas via _MainTex; rendering
    with the new font requires the same atlas content there too.
"""
import argparse
import os
import shutil
import sys

import UnityPy
from UnityPy.streams import EndianBinaryReader

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercstoria import config as cfg
from mercstoria.config import (
    ATLAS_LEN, RESS_ATLAS_OFFSET,
    BUNDLE_RESS_STD_OFFSET, BUNDLE_RESS_ONE_OFFSET,
    BUNDLE_FONT_PATHID, BUNDLE_RESS_CAB,
    RESOURCES_HIDDEN_FONT_PID,
)
cfg.enable_utf8_stdout()

# Optional sibling install for ASCII-path test copy.
MIRROR_DIR = os.environ.get("MERCSTORIA_MIRROR_DIR", r"D:\mercstoria")


def _data_folder(game_dir):
    """Locate the Unity *_Data folder inside `game_dir`.

    Accepts both the pristine name (`メルストM_Data`) and the launcher-
    deployed renamed-after-exe variant (`メルストM_app_Data`). Unity
    derives the data-folder name from the exe basename at startup, so
    deploying the launcher requires the data folder to rename too —
    this helper papers over the rename for everything downstream.
    """
    for name in (cfg.APP_DATA_RENAMED, cfg.APP_DATA_NAME):
        p = os.path.join(game_dir, name)
        if os.path.isdir(p):
            return p
    raise FileNotFoundError(f"Neither {cfg.APP_DATA_NAME} nor {cfg.APP_DATA_RENAMED} under {game_dir}")


def _data_subpath(game_dir, *parts):
    """Return `<game>/<*_Data>/parts...` as an os.path-joined string."""
    return os.path.join(_data_folder(game_dir), *parts)


def _streaming_assets_subpath(game_dir, *parts):
    """Return `<game>/<*_Data>/StreamingAssets/aa/StandaloneWindows64/parts...`.

    This is where Unity puts the pre-baked Addressables bundles that
    ship inside the game install (separate from the CDN cache that's
    downloaded on first run into LocalLow).
    """
    return os.path.join(_data_folder(game_dir),
                        "StreamingAssets", "aa", "StandaloneWindows64", *parts)


# ---------- helpers ----------

def ensure_bak(path):
    """Create `<path>.bak` if it doesn't already exist. Returns the bak path.

    The bak is the canonical "restore point" the script uses for
    idempotency: every patch reads source pixels from the bak rather
    than the live file so re-running gives identical bytes regardless
    of what state the live file was last left in.
    """
    bak = path + ".bak"
    if os.path.exists(path) and not os.path.exists(bak):
        shutil.copy2(path, bak)
        print(f"  [backup] created {os.path.basename(bak)}")
    return bak


def load_source_font(font_bundle_path):
    """Extract from the source font bundle: (font_typetree, atlas_bytes, font_name).

    The source bundle is expected to have one font-asset MonoBehaviour (with
    `m_CharacterTable` and `m_AtlasTextures`) and one Texture2D referenced by it
    (4096x4096 Alpha8, 16 MB of image data).
    """
    env = UnityPy.load(font_bundle_path)

    font_tt = None
    font_name = None
    atlas_pid_referenced = None
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        tt = o.read_typetree()
        if "m_CharacterTable" in tt and "m_AtlasTextures" in tt and tt.get(
            "m_AtlasTextures"
        ):
            font_tt = tt
            font_name = tt.get("m_Name", "?")
            atlas_pid_referenced = tt["m_AtlasTextures"][0]["m_PathID"]
            break
    if font_tt is None:
        raise SystemExit(
            f"[source] no TMP_FontAsset MonoBehaviour with m_AtlasTextures found in"
            f" {font_bundle_path}"
        )

    atlas_bytes = None
    for o in env.objects:
        if o.type.name == "Texture2D" and o.path_id == atlas_pid_referenced:
            data = o.read()
            atlas_bytes = bytes(data.get_image_data())
            break
    if atlas_bytes is None:
        raise SystemExit(
            f"[source] atlas texture pid={atlas_pid_referenced} not found"
        )
    if len(atlas_bytes) != ATLAS_LEN:
        raise SystemExit(
            f"[source] atlas size {len(atlas_bytes)} != expected {ATLAS_LEN}"
            f" (font must be baked at 4096x4096 Alpha8)"
        )

    print(
        f"[source] font='{font_name}' chars={len(font_tt['m_CharacterTable'])}"
        f" glyphs={len(font_tt['m_GlyphTable'])} atlas={len(atlas_bytes)} bytes"
    )
    return font_tt, atlas_bytes


# Fields transplanted from source font into the existing game font asset.
# Everything NOT in this list is preserved from the original — most importantly
# m_FaceInfo (m_LineHeight=64.0, ascender/descender), m_AtlasTextures (PPtrs),
# m_AtlasWidth/Height/Padding, m_AtlasRenderMode, m_Material, and the
# m_FallbackFontAssetTable. The original UI was laid out against the original
# FaceInfo metrics; replacing them produces squished line spacing in dialogue
# (LogoSC's natural LineHeight is 39.68 ≈ 1.24× PointSize vs the original's
# 64.0 = 2× PointSize). Per-glyph m_Metrics still come from the source font —
# those are independent of m_FaceInfo.
TRANSPLANT_KEYS = (
    "m_CharacterTable",
    "m_GlyphTable",
    "m_CharacterLookupDictionary",
    "m_GlyphLookupDictionary",
    "m_UsedGlyphRects",
    "m_FreeGlyphRects",
)


def transplant_keys_into(target_tt, source_tt):
    """Copy glyph-related tables from source to target font asset typetree.

    PRESERVES m_FaceInfo (including m_LineHeight, m_PointSize, ascender,
    descender) so UI layout stays identical to the original. Replacing
    m_FaceInfo with the source font's natural values (e.g. LogoSC's
    m_LineHeight ≈ 39.68 vs the original 64.0) makes every multi-line
    dialogue / menu box squish together — pixels of overlap, text
    overflow, the works.
    """
    for k in TRANSPLANT_KEYS:
        if k in source_tt:
            target_tt[k] = source_tt[k]
    # sanity: never let m_FaceInfo silently slip in via a different key name
    assert "m_FaceInfo" not in TRANSPLANT_KEYS


# ---------- patches ----------

def patch_resources_ress_atlas(game_dir, atlas_bytes):
    """Patch A — overwrite shared atlas pixels in resources.assets.resS."""
    path = str(_data_subpath(game_dir, "resources.assets.resS"))
    bak = ensure_bak(path)
    shutil.copy2(bak, path)  # restore first for idempotency
    with open(path, "r+b") as f:
        f.seek(RESS_ATLAS_OFFSET)
        f.write(atlas_bytes)
    print(
        f"[A] wrote {ATLAS_LEN} bytes at offset {RESS_ATLAS_OFFSET}"
        f" in resources.assets.resS"
    )
    return path


def patch_bundle(game_dir, source_font_tt, atlas_bytes):
    """Patch B — bundle's font asset + both atlas slots inside its archive resS.
    Returns the patched bundle bytes (in addition to writing to disk) so Patch C
    can byte-diff against the original to find the changed regions."""
    bundle_path = str(_streaming_assets_subpath(game_dir, cfg.FONT_BUNDLE_NAME))
    bak = ensure_bak(bundle_path)
    shutil.copy2(bak, bundle_path)

    env = UnityPy.load(bundle_path)

    # font asset glyph tables
    font_obj = next(
        o
        for o in env.objects
        if o.type.name == "MonoBehaviour" and o.path_id == BUNDLE_FONT_PATHID
    )
    tt = font_obj.read_typetree()
    transplant_keys_into(tt, source_font_tt)
    font_obj.save_typetree(tt)

    # both atlas slots inside the bundle's archived .resS
    old_reader = env.file.files[BUNDLE_RESS_CAB]
    raw = bytearray(old_reader.bytes)
    raw[BUNDLE_RESS_STD_OFFSET : BUNDLE_RESS_STD_OFFSET + ATLAS_LEN] = atlas_bytes
    raw[BUNDLE_RESS_ONE_OFFSET : BUNDLE_RESS_ONE_OFFSET + ATLAS_LEN] = atlas_bytes
    new_reader = EndianBinaryReader(bytes(raw), endian=old_reader.endian)
    new_reader.flags = old_reader.flags
    env.file.files[BUNDLE_RESS_CAB] = new_reader

    bundle_bytes = env.file.save(packer="lz4")
    with open(bundle_path, "wb") as f:
        f.write(bundle_bytes)
    fi = tt.get("m_FaceInfo", {})
    print(
        f"[B] bundle patched: font chars={len(tt['m_CharacterTable'])}"
        f"/{len(tt['m_GlyphTable'])}, atlas in 2 slots, total"
        f" {os.path.getsize(bundle_path)} bytes"
        f"   (preserved m_LineHeight={fi.get('m_LineHeight')},"
        f" m_PointSize={fi.get('m_PointSize')})"
    )
    return bundle_path


def _read_bundle_font_raw(bundle_path):
    env = UnityPy.load(bundle_path)
    for o in env.objects:
        if o.type.name == "MonoBehaviour" and o.path_id == BUNDLE_FONT_PATHID:
            return o.get_raw_data()
    raise SystemExit(f"[diff] font asset pathID not found in {bundle_path}")


def patch_resources_hidden_font(game_dir, source_font_tt):
    """Patch C — hidden RocknRollStd SDF MonoBehaviour in resources.assets pid=27.

    Strategy: glyph-table-only byte-diff between original-bundle-font and
    patched-bundle-font (which has the same chars set as the original but with
    new glyph rects/metrics). Apply those diffs in place over pid=27's bytes
    inside resources.assets, preserving the file size and every other field
    (m_Script reference, m_AtlasTextures pid=10, m_FallbackFontAssetTable, etc.).

    The original and resources.assets copies of the font asset are byte-identical
    in the structural / glyph-table region (they differ only by 49 bytes of
    m_Script PPtr near the header), so identical diff offsets apply cleanly.
    """
    bundle_path = str(_streaming_assets_subpath(game_dir, cfg.FONT_BUNDLE_NAME))
    bundle_bak = bundle_path + ".bak"
    if not os.path.exists(bundle_bak):
        raise SystemExit("[C] bundle .bak missing — run patch B first")

    # 1. Original bundle font asset raw bytes
    orig_raw = _read_bundle_font_raw(bundle_bak)

    # 2. Build a "same-chars-new-glyph-rects" font on a fresh copy of the bundle
    #    (we cannot just reuse the result of patch B because that one has the
    #    source font's full char table, which changes the byte size and breaks
    #    the in-place assumption.)
    import tempfile

    env_o = UnityPy.load(bundle_bak)
    font_obj = next(
        o
        for o in env_o.objects
        if o.type.name == "MonoBehaviour" and o.path_id == BUNDLE_FONT_PATHID
    )
    tt = font_obj.read_typetree()

    # Build unicode -> rect/metrics lookup from the source font
    src_gi_to_glyph = {g["m_Index"]: g for g in source_font_tt["m_GlyphTable"]}
    uni_to_rect = {}
    uni_to_metrics = {}
    for c in source_font_tt["m_CharacterTable"]:
        gi = c["m_GlyphIndex"]
        if gi in src_gi_to_glyph:
            uni_to_rect[c["m_Unicode"]] = src_gi_to_glyph[gi]["m_GlyphRect"]
            uni_to_metrics[c["m_Unicode"]] = src_gi_to_glyph[gi]["m_Metrics"]

    # Update each original glyph entry IN PLACE using the source font's rects
    gi_to_orig_entry = {g["m_Index"]: g for g in tt["m_GlyphTable"]}
    updated = 0
    for c in tt["m_CharacterTable"]:
        uni = c["m_Unicode"]
        gi = c["m_GlyphIndex"]
        if uni in uni_to_rect and gi in gi_to_orig_entry:
            gi_to_orig_entry[gi]["m_GlyphRect"] = uni_to_rect[uni]
            gi_to_orig_entry[gi]["m_Metrics"] = uni_to_metrics[uni]
            updated += 1
    print(
        f"[C] in-place glyph rect rebuild: updated {updated}"
        f"/{len(tt['m_CharacterTable'])} chars from source font"
    )
    font_obj.save_typetree(tt)

    # 3. Round-trip through disk to actually flush save_typetree's in-memory
    #    state into bytes (UnityPy's get_raw_data() returns disk content; full
    #    bundle save is the easiest way to extract the serialized result).
    with tempfile.NamedTemporaryFile(
        prefix="merc_font_inplace_", suffix=".bundle", delete=False
    ) as tmp:
        tmp.write(env_o.file.save(packer="lz4"))
        tmp_path = tmp.name
    try:
        patched_raw = _read_bundle_font_raw(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if len(patched_raw) != len(orig_raw):
        raise SystemExit(
            f"[C] in-place font asset size changed ({len(orig_raw)}"
            f"->{len(patched_raw)}); source font has different number of chars"
            f" than original — Patch C requires same chars set. Use only the"
            f" original 7007 chars in the source font, or extend separately."
        )

    diffs = [i for i in range(len(orig_raw)) if orig_raw[i] != patched_raw[i]]
    print(f"[C] glyph-table diff bytes: {len(diffs)}")

    # 4. Apply diffs in place on resources.assets pid=27
    res_path = str(_data_subpath(game_dir, "resources.assets"))
    res_bak = ensure_bak(res_path)
    shutil.copy2(res_bak, res_path)  # restore for idempotency

    env_r = UnityPy.load(res_path)
    pid27 = next(
        o
        for o in env_r.objects
        if o.type.name == "MonoBehaviour" and o.path_id == RESOURCES_HIDDEN_FONT_PID
    )
    pid27_offset_in_file = pid27.byte_start
    orig_pid27_bytes = pid27.get_raw_data()
    if len(orig_pid27_bytes) != len(orig_raw):
        raise SystemExit(
            f"[C] resources.assets pid=27 size {len(orig_pid27_bytes)} !="
            f" bundle font size {len(orig_raw)} — game version mismatch?"
        )

    new_pid27 = bytearray(orig_pid27_bytes)
    for off in diffs:
        new_pid27[off] = patched_raw[off]

    # Write in place; file size unchanged so the SerializedFile header / object
    # table do not need updating.
    with open(res_path, "r+b") as f:
        f.seek(pid27_offset_in_file)
        f.write(bytes(new_pid27))
    print(
        f"[C] wrote {len(new_pid27)} bytes at offset {pid27_offset_in_file}"
        f" in resources.assets (file size unchanged)"
    )
    return res_path


def mirror_to(src_paths, mirror_dir):
    """Mirror each patched file into a sibling test install at `mirror_dir`.

    The Steam install lives under a Japanese-character path which sometimes
    trips ASCII-only tooling. Developers can maintain an ASCII-path copy
    (e.g. `D:\\mercstoria`) and pass `--mirror-dir` so font-swap patches
    land in both places at once. Silently no-ops if the mirror dir
    doesn't exist — passing `--mirror-dir D:\\nope` is safe.

    Mirroring matches paths by their tail under `<exe>_Data` so it works
    with both pristine and launcher-deployed layouts.
    """
    if not os.path.isdir(mirror_dir):
        return
    for src in src_paths:
        # mirror by file basename + relative directory structure under game data
        # we just match the tail under メルストM_Data
        for marker in ("メルストM_Data",):
            idx = src.find(marker)
            if idx >= 0:
                rel = src[idx:]
                dst = os.path.join(mirror_dir, rel)
                if os.path.isdir(os.path.dirname(dst)):
                    shutil.copy2(src, dst)
                    print(f"  [mirror] {dst}")
                break


# ---------- main ----------

def main():
    """Apply patches A + B + C against the live install (and optional mirror).

    Argument parsing is intentionally permissive: `--game-dir` is auto-
    resolved through `cfg.game_dir()` if omitted, `--mirror-dir` is opt-in
    via env override, and `--skip-c` is an escape hatch for users who
    don't want the in-place resources.assets edit (e.g. while debugging
    Patch C against a fresh game install).
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("font_bundle", help="Path to source font bundle (logofont.bundle equivalent)")
    ap.add_argument(
        "--game-dir",
        default=None,
        help="Game install dir (default: auto-detected via mercstoria.config)",
    )
    ap.add_argument(
        "--mirror-dir",
        default=MIRROR_DIR,
        help=f"Mirror copy dir (skipped if missing, default: {MIRROR_DIR})",
    )
    ap.add_argument(
        "--skip-c",
        action="store_true",
        help="Skip Patch C (resources.assets in-place). Use only if you know what you're doing.",
    )
    args = ap.parse_args()

    if not os.path.isfile(args.font_bundle):
        raise SystemExit(f"font bundle not found: {args.font_bundle}")
    if args.game_dir is None:
        args.game_dir = str(cfg.game_dir())
    if not os.path.isdir(args.game_dir):
        raise SystemExit(f"game dir not found: {args.game_dir}")

    source_font_tt, atlas_bytes = load_source_font(args.font_bundle)
    print()

    res_ress = patch_resources_ress_atlas(args.game_dir, atlas_bytes)
    bundle = patch_bundle(args.game_dir, source_font_tt, atlas_bytes)
    if args.skip_c:
        print("[C] skipped by flag — menu/title rendering will still use original font")
        res_assets = None
    else:
        res_assets = patch_resources_hidden_font(args.game_dir, source_font_tt)
    print()

    mirror_to([p for p in (res_ress, bundle, res_assets) if p], args.mirror_dir)
    print("\nDone. Launch the game and verify both story and menu screens.")


if __name__ == "__main__":
    main()
