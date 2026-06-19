"""Merc Storia font swap — universal pipeline.

Takes a single TMP font bundle (containing one TMP_FontAsset MonoBehaviour + one
4096x4096 Alpha8 SDF atlas Texture2D) and applies all three patches needed to
make the new font render across the entire game:

  A) overwrite the shared atlas pixels in `resources.assets.resS`
  B) transplant the glyph/char tables into the bundle's font asset
     (84ece16f...bundle, pathID 6189425675716077201) — fixes story rendering
  C) full TypeTree transplant of the HIDDEN font asset in `resources.assets`
     (pid=27). resources.assets has no embedded TypeTree for TMP_FontAsset
     (IL2CPP release builds strip it), so we borrow the bundle's TypeTree
     nodes to read+write pid=27 and let UnityPy resave the SerializedFile.
     Atlas / material / fallback PPtrs (pid=10, pid=2, etc.) are preserved
     so they keep pointing inside resources.assets — only the char/glyph
     tables are transplanted from the source font.

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
    """Patch B — bundle's font asset + both atlas slots inside its archive resS."""
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


def patch_resources_hidden_font(game_dir, source_font_tt):
    """Patch C — hidden RocknRollStd SDF MonoBehaviour in resources.assets pid=27.

    resources.assets is an IL2CPP release SerializedFile with no embedded
    TypeTree for TMP_FontAsset, so UnityPy cannot parse pid=27 on its own.
    The bundle copy of the font asset (`84ece16f...bundle`, pathID
    BUNDLE_FONT_PATHID) DOES embed the TypeTree because asset bundles
    serialize their own type info. We borrow those nodes to read+write
    pid=27, then let UnityPy resave the whole resources.assets — the
    SerializedFile writer recomputes the object table and downstream
    byte_starts so pid=27 growing past its original size is fine.

    Atlas + material PPtrs in pid=27 must be preserved — they reference
    pid=10 (atlas Texture2D in resources.assets) and pid=2 (material), not
    the bundle's internal atlas / material. m_FaceInfo is preserved too,
    same rationale as Patch B (UI was laid out against the original metrics).
    """
    bundle_path = str(_streaming_assets_subpath(game_dir, cfg.FONT_BUNDLE_NAME))
    bundle_bak = bundle_path + ".bak"
    if not os.path.exists(bundle_bak):
        raise SystemExit("[C] bundle .bak missing — run patch B first")

    env_b = UnityPy.load(bundle_bak)
    bundle_font_obj = next(
        o
        for o in env_b.objects
        if o.type.name == "MonoBehaviour" and o.path_id == BUNDLE_FONT_PATHID
    )
    nodes = bundle_font_obj.serialized_type.nodes
    if nodes is None:
        raise SystemExit(
            "[C] bundle font has no embedded TypeTree — cannot parse pid=27"
        )

    res_path = str(_data_subpath(game_dir, "resources.assets"))
    res_bak = ensure_bak(res_path)
    shutil.copy2(res_bak, res_path)  # restore for idempotency

    env_r = UnityPy.load(res_path)
    pid27 = next(
        o
        for o in env_r.objects
        if o.type.name == "MonoBehaviour" and o.path_id == RESOURCES_HIDDEN_FONT_PID
    )

    tt = pid27.read_typetree(nodes)
    transplant_keys_into(tt, source_font_tt)
    pid27.save_typetree(tt, nodes)

    new_bytes = env_r.file.save()
    with open(res_path, "wb") as f:
        f.write(new_bytes)

    fi = tt.get("m_FaceInfo", {})
    print(
        f"[C] resources.assets pid=27 transplanted: chars="
        f"{len(tt['m_CharacterTable'])}/{len(tt['m_GlyphTable'])} glyphs,"
        f" atlas={tt['m_AtlasTextures']}, material={tt['m_Material']}"
        f"   (preserved m_LineHeight={fi.get('m_LineHeight')},"
        f" m_PointSize={fi.get('m_PointSize')})"
    )
    print(
        f"[C] resources.assets resaved: {len(new_bytes)} bytes"
        f" (was {os.path.getsize(res_bak)})"
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
