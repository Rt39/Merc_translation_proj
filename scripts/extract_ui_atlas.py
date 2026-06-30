"""Bitmap-baked UI atlas extract / repack.

Some UI text in the game is not engine-rendered TMP_Text but baked into
texture atlases. Those atlases live in 4 bundles:

  CommonUI    — SA  f17951921426b535…  RGBA32  2048×1024  (189 sprites)
  GalleryUI   — SA  6e4d5e586bb1bdff…  RGBA32  1024×2048  ( 62 sprites)
  HomeUI      — SA  fd6c29755bc7150e…  RGBA32  256× 256   (  5 sprites)
  FooterUI    — CDN 6936cdaddf3fa06b…  DXT5    512× 512   ( 22 cells, SpriteStudio)

`extract-ui-atlas` reads each bundle, dumps the full atlas PNG plus one
PNG per sprite / cell into:

    extracted_data/ui_atlas/<AtlasName>/
        _atlas.png         full reference (read-only)
        _meta.json         bundle info + per-sprite rect & rotation
        sprites/<name>.png one PNG per editable sprite / cell

`repack-ui-atlas` re-reads the source bundle's clean atlas, pastes any
edited PNGs back at their recorded rects, and writes the modified bundle
to `repacked_bundles/ui_atlas/<bundle>.bundle`. Only sprites whose PNG
hash differs from the extract-time baseline are pasted; the rest stay
pristine. Bundles with no edits are skipped entirely.

CommonUI / GalleryUI / HomeUI are SpriteAtlas bundles: the per-sprite
rect comes from `SpriteAtlas.m_RenderDataMap[Sprite.m_RenderDataKey]`,
and atlas coords are bottom-left so we Y-flip during extract. FooterUI
is a SpriteStudio `dc_` MonoBehaviour cellmap: rects are already
top-left, no flip needed.

settingsRaw bit layout (Unity 2017+):
    bit 0 packed   bit 1 mode   bits 2-5 rotation   bits 6-11 format
Rotation 0=None, 1=FlipH, 2=FlipV, 3=Rot180, 4=Rot90. All 4 target
atlases have rotation=0 today, but the repack still handles 1-3 for
safety; rotation=4 is rejected (would change cell aspect ratio and
break the rect-paste flow).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import shutil
import sys
import time
from pathlib import Path

import UnityPy
from UnityPy.enums import TextureFormat
from PIL import Image
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mercstoria import config as cfg


# Unity TextureFormat values that lose data on re-encode (DXT/BC/ETC/ASTC
# family — block compression). Repack auto-upgrades these to RGBA32 so
# translator edits don't compound encoder noise. Cost is ~2.5x bundle
# size on the affected texture (e.g. footer_menu_m_512: 107KB → 262KB),
# which is irrelevant for a UI atlas. RGBA32 / RGB24 / Alpha8 etc. are
# already lossless and pass through unchanged.
LOSSY_FORMATS = {
    TextureFormat.DXT1,        # 10
    TextureFormat.DXT5,        # 12
    TextureFormat.BC4,         # 26
    TextureFormat.BC5,         # 27
    TextureFormat.BC6H,        # 24
    TextureFormat.BC7,         # 25
    TextureFormat.DXT1Crunched,
    TextureFormat.DXT5Crunched,
    TextureFormat.ETC_RGB4,
    TextureFormat.ETC2_RGB,
    TextureFormat.ETC2_RGBA1,
    TextureFormat.ETC2_RGBA8,
    TextureFormat.ETC2_RGBA8Crunched,
    TextureFormat.ETC_RGB4Crunched,
    TextureFormat.PVRTC_RGB2,
    TextureFormat.PVRTC_RGBA2,
    TextureFormat.PVRTC_RGB4,
    TextureFormat.PVRTC_RGBA4,
    TextureFormat.ASTC_RGB_4x4,
    TextureFormat.ASTC_RGBA_4x4,
}

cfg.enable_utf8_stdout()


_HERE = Path(__file__).resolve().parent.parent

EXTRACT_ROOT  = _HERE / "extracted_data" / "ui_atlas"
REPACK_ROOT   = _HERE / "repacked_bundles" / "ui_atlas"
# Some SpriteAtlas textures (CommonUI, GalleryUI, HomeUI) also have a
# self-contained duplicate baked into the player's <_Data>/sharedassets*.assets
# files — same m_Name, same pixels, referenced by an embedded SpriteAtlas in
# the same file. Modifying only the Addressables bundle is not enough for
# those: at runtime the game loads whichever copy the scene preloader binds
# first, which for some atlases is the sharedassets one. The repack writes a
# patched .assets per affected sharedassets file here, then deploy copies
# each into <_Data>/. FooterUI has no sharedassets twin (CDN-only).
REPACK_SHAREDASSETS_ROOT = _HERE / "repacked_bundles" / "ui_atlas_sharedassets"
FP_PATH       = _HERE / "extracted_data" / ".ui_atlas_fingerprints.pkl"


# Target bundles. The `source_kind` selects extract/repack code paths.
# `source_dir` resolver: "sa" = StreamingAssets, "ba" = CDN BundleAssets.
TARGETS = [
    {
        "name":        "CommonUI",
        "bundle":      "f17951921426b535e20de01adc4f06c3.bundle",
        "source_dir":  "sa",
        "kind":        "spriteatlas",
    },
    {
        "name":        "GalleryUI",
        "bundle":      "6e4d5e586bb1bdffd38c58f19f8ba84e.bundle",
        "source_dir":  "sa",
        "kind":        "spriteatlas",
    },
    {
        "name":        "HomeUI",
        "bundle":      "fd6c29755bc7150eb79d2d669abd3f6e.bundle",
        "source_dir":  "sa",
        "kind":        "spriteatlas",
    },
    {
        "name":        "FooterUI",
        "bundle":      "6936cdaddf3fa06b26de3570c16593a6.bundle",
        "source_dir":  "ba",
        "kind":        "spritestudio",
    },
]


# ============================================================================
#                              Helpers
# ============================================================================

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    return sha256_bytes(p.read_bytes())


def load_fps() -> dict:
    if not FP_PATH.exists():
        return {}
    try:
        with open(FP_PATH, "rb") as f:
            return pickle.load(f)
    except Exception:
        return {}


def save_fps(fps: dict):
    FP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FP_PATH, "wb") as f:
        pickle.dump(fps, f)


def resolve_source(spec: dict) -> Path:
    if spec["source_dir"] == "sa":
        return cfg.streaming_assets_dir() / spec["bundle"]
    if spec["source_dir"] == "ba":
        return cfg.bundleassets_dir() / spec["bundle"]
    raise ValueError(f"unknown source_dir {spec['source_dir']!r}")


def safe_name(s: str) -> str:
    """Filesystem-safe sprite name. Sprites can contain '/', spaces, etc."""
    import re
    return re.sub(r'[<>:"/\\|?*\s]+', "_", s).strip("._") or "_blank"


def decode_settings(raw: int) -> dict:
    """Decode SpriteAtlasData.settingsRaw under the Unity 2017+ layout."""
    return {
        "packed":   bool(raw & 1),
        "mode":     (raw >> 1) & 0x01,        # 0=Tight, 1=Rectangle
        "rotation": (raw >> 2) & 0x0F,        # 0=None,1=FlipH,2=FlipV,3=Rot180,4=Rot90
        "format":   (raw >> 6) & 0x3F,
    }


def apply_rotation_inverse(img: Image.Image, rotation: int) -> Image.Image:
    """Convert atlas-stored pixels into the orientation a translator sees.
    Packer applied `rotation`; unpacker inverts it."""
    if rotation == 0:
        return img
    if rotation == 1:                       # FlipHorizontal (self-inverse)
        return img.transpose(Image.FLIP_LEFT_RIGHT)
    if rotation == 2:                       # FlipVertical (self-inverse)
        return img.transpose(Image.FLIP_TOP_BOTTOM)
    if rotation == 3:                       # Rotate180 (self-inverse)
        return img.transpose(Image.ROTATE_180)
    if rotation == 4:                       # 90° packing
        return img.transpose(Image.ROTATE_90)
    return img


def apply_rotation_forward(img: Image.Image, rotation: int) -> Image.Image:
    """Inverse of `apply_rotation_inverse` — reapply the packer's transform
    so paste-back lands the pixels in the same orientation Unity expects."""
    if rotation == 0:
        return img
    if rotation in (1, 2, 3):
        return apply_rotation_inverse(img, rotation)  # all self-inverse
    if rotation == 4:                       # 90° CCW was applied at extract; CW reverses it
        return img.transpose(Image.ROTATE_270)
    return img


# ============================================================================
#                       SpriteAtlas: collect sprite rects
# ============================================================================

def _atlas_render_data_map(sa) -> dict:
    """Build a (guid, fileId) → SpriteAtlasData lookup from a SpriteAtlas."""
    def key_repr(k):
        guid, fid = k
        return (guid.data_0_, guid.data_1_, guid.data_2_, guid.data_3_, fid)
    return {key_repr(k): v for k, v in sa.m_RenderDataMap}


def _collect_spriteatlas(env) -> tuple[object, object, Image.Image, str, list[dict]]:
    """Return (atlas_obj, tex_obj, tex_image, tex_name, sprite_entries).
    sprite_entries are pre-extract: each has name, rect (atlas coords),
    settingsRaw, render-key tuple, path_id."""
    sa = None
    sa_obj = None
    tex = None
    tex_obj = None
    sprites_raw = []
    for o in env.objects:
        if o.type.name == "SpriteAtlas":
            sa_obj = o
            sa = o.read()
        elif o.type.name == "Texture2D":
            tex_obj = o
            tex = o.read()
        elif o.type.name == "Sprite":
            sprites_raw.append((o, o.read()))
    if sa is None or tex is None:
        raise RuntimeError("bundle missing SpriteAtlas or Texture2D")

    rd_map = _atlas_render_data_map(sa)
    H = tex.image.height

    out = []
    for o, spr in sprites_raw:
        def key_repr(k):
            guid, fid = k
            return (guid.data_0_, guid.data_1_, guid.data_2_, guid.data_3_, fid)
        k = key_repr(spr.m_RenderDataKey)
        v = rd_map.get(k)
        if v is None:
            print(f"  WARN sprite {spr.m_Name!r} has no RenderDataMap entry; skipping")
            continue
        r = v.textureRect
        x, y, w, h = float(r.x), float(r.y), float(r.width), float(r.height)
        s = decode_settings(int(v.settingsRaw))
        if s["rotation"] == 4:
            print(f"  WARN sprite {spr.m_Name!r} packed at 90°; not yet supported, skipping")
            continue
        # Translate atlas (bottom-left origin) → PIL (top-left)
        # The "extract image" is what the translator sees; we store the
        # PIL crop box so repack pastes at the same coords.
        left   = int(x)
        right  = int(x + w)
        top    = int(H - (y + h))
        bottom = int(H - y)
        out.append({
            "name":            spr.m_Name,
            "path_id":         o.path_id,
            "atlas_rect":      [x, y, w, h],
            "settings_raw":    int(v.settingsRaw),
            "rotation":        s["rotation"],
            "pil_box":         [left, top, right, bottom],   # crop / paste box
            "size":            [int(w), int(h)],
            # render-key kept for reference / debugging only
            "render_key":      [k[0], k[1], k[2], k[3], k[4]],
        })
    return sa_obj, tex_obj, tex.image, tex.m_Name, out


# ============================================================================
#                       SpriteStudio: collect cell rects
# ============================================================================

def _match_atlas_to_tex(atlas_name: str, tex_names: list[str]) -> str | None:
    """Pair a SpriteStudio cellmap atlas Name to a Texture2D m_Name in
    the same bundle. Equality first; otherwise prefix (covers the common
    `<atlas>_<sizeSuffix>` convention, e.g. footer_menu_m → footer_menu_m_512)."""
    if atlas_name in tex_names:
        return atlas_name
    cand = [t for t in tex_names if t.startswith(atlas_name)]
    if len(cand) == 1:
        return cand[0]
    return None


def _collect_spritestudio(env) -> tuple[dict, dict[str, Image.Image], list[dict], dict]:
    """Return (tex_obj_by_name, tex_img_by_name, cell_entries, cellmap_pathids).

    A SpriteStudio bundle's `dc_` cellmap may contain multiple atlases
    (e.g. the 6 sub-atlases inside Storyparts_fairy_1); each pairs with
    its own Texture2D. We resolve each cellmap entry to a texture by
    name (exact / prefix) and emit one entry per cell with the resolved
    texture name embedded — repack uses that to route the paste."""
    tex_obj_by_name: dict[str, object] = {}
    tex_img_by_name: dict[str, Image.Image] = {}
    cellmap = None
    cm_path_id = None
    cm_name = None
    for o in env.objects:
        if o.type.name == "Texture2D":
            t = o.read()
            tex_obj_by_name[t.m_Name] = o
            tex_img_by_name[t.m_Name] = t.image
        elif o.type.name == "MonoBehaviour":
            try:
                d = o.read()
                if (d.m_Name or "").startswith("dc_"):
                    cellmap = o.read_typetree()
                    cm_path_id = o.path_id
                    cm_name = d.m_Name
            except Exception:
                pass
    if not tex_obj_by_name or cellmap is None:
        raise RuntimeError("bundle missing Texture2D or dc_ cellmap")

    out = []
    tex_names = list(tex_obj_by_name)
    for atlas_idx, atlas in enumerate(cellmap["TableCellMap"]):
        matched = _match_atlas_to_tex(atlas["Name"], tex_names)
        if matched is None:
            print(f"  WARN cellmap atlas {atlas['Name']!r} has no matching Texture2D in bundle "
                  f"(textures: {tex_names}); skipping {len(atlas['TableCell'])} cells")
            continue
        for c in atlas["TableCell"]:
            r = c["Rectangle"]
            x, y, w, h = float(r["x"]), float(r["y"]), float(r["width"]), float(r["height"])
            out.append({
                "name":            c["Name"],
                "atlas_index":     atlas_idx,
                "atlas_name":      atlas["Name"],
                "texture_name":    matched,
                "atlas_rect":      [x, y, w, h],
                "rotation":        0,
                "pil_box":         [int(x), int(y), int(x+w), int(y+h)],
                "size":            [int(w), int(h)],
            })
    return tex_obj_by_name, tex_img_by_name, out, {cm_name or "dc_": cm_path_id}


# ============================================================================
#                              Extract
# ============================================================================

def _confirm_overwrite(out_dir: Path, label: str, yes: bool) -> bool:
    if not out_dir.is_dir() or not any(out_dir.iterdir()):
        return True
    print(f"  WARNING: {out_dir} already exists and is not empty.")
    print(f"  Re-extracting {label} will overwrite edited PNGs and replace")
    print(f"  the fingerprint baseline (repack would then see unmodified files).")
    if yes:
        print("  --yes given; proceeding.")
        return True
    resp = input("  Continue and overwrite? [y/N]: ").strip().lower()
    if resp not in ("y", "yes"):
        print("  Aborted.")
        return False
    return True


def cmd_extract(yes: bool = False):
    """Dump full atlas + per-sprite/cell PNGs for each target bundle."""
    if not _confirm_overwrite(EXTRACT_ROOT, "ui_atlas", yes):
        return
    EXTRACT_ROOT.mkdir(parents=True, exist_ok=True)
    fps = load_fps()

    t0 = time.time()
    total_sprites = 0
    for spec in TARGETS:
        name = spec["name"]
        src = resolve_source(spec)
        print(f"\n=== {name}  {src.name}  kind={spec['kind']} ===")
        if not src.is_file():
            print(f"  !! source bundle not found: {src}")
            continue

        env = UnityPy.load(str(src))
        out_dir = EXTRACT_ROOT / name
        # Wipe stale outputs (texture format / sprite list may have
        # changed since last extract; mixing old + new files in the same
        # dir would confuse repack and translators alike).
        if out_dir.exists():
            shutil.rmtree(out_dir)
        spr_dir = out_dir / "sprites"
        spr_dir.mkdir(parents=True, exist_ok=True)

        if spec["kind"] == "spriteatlas":
            sa_obj, tex_obj, tex_img, tex_name, entries = _collect_spriteatlas(env)
            meta = {
                "name":         name,
                "bundle":       spec["bundle"],
                "source_dir":   spec["source_dir"],
                "kind":         spec["kind"],
                "atlas_size":   [tex_img.width, tex_img.height],
                "texture_name": tex_name,
                "texture_path_id":   tex_obj.path_id,
                "atlas_path_id":     sa_obj.path_id,
                "sprites":      entries,
            }
        elif spec["kind"] == "spritestudio":
            tex_obj_by_name, tex_img_by_name, entries, cm_pids = _collect_spritestudio(env)
            meta = {
                "name":         name,
                "bundle":       spec["bundle"],
                "source_dir":   spec["source_dir"],
                "kind":         spec["kind"],
                "textures": {
                    tn: {
                        "size":    [im.width, im.height],
                        "path_id": tex_obj_by_name[tn].path_id,
                    } for tn, im in tex_img_by_name.items()
                },
                "cellmap_path_ids":  cm_pids,
                "sprites":      entries,
            }
        else:
            raise ValueError(spec["kind"])

        # Reference: dump the unmodified full atlas(es). Translators should
        # not edit these files; they are regenerated from source on every
        # extract. SpriteAtlas has 1, SpriteStudio may have several.
        if spec["kind"] == "spriteatlas":
            (out_dir / "_atlas.png").parent.mkdir(parents=True, exist_ok=True)
            tex_img.save(out_dir / "_atlas.png")
            cell_tex_lookup = {None: tex_img}     # only one image
            extract_atlas_kind = "single"
        else:
            for tn, im in tex_img_by_name.items():
                im.save(out_dir / f"_atlas_{safe_name(tn)}.png")
            cell_tex_lookup = tex_img_by_name
            extract_atlas_kind = "multi"

        # Per-sprite/cell PNGs + record fingerprints
        sprite_fps: dict[str, str] = {}
        for s in entries:
            if extract_atlas_kind == "single":
                src_img = cell_tex_lookup[None]
            else:
                src_img = cell_tex_lookup[s["texture_name"]]
            crop = src_img.crop(tuple(s["pil_box"]))
            crop = apply_rotation_inverse(crop, s["rotation"])
            fn = spr_dir / f"{safe_name(s['name'])}.png"
            crop.save(fn)
            s["file"] = f"sprites/{fn.name}"
            sprite_fps[s["file"]] = sha256_file(fn)

        (out_dir / "_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        fps[f"ui_atlas/{name}"] = sprite_fps
        total_sprites += len(entries)
        if spec["kind"] == "spriteatlas":
            atlas_desc = f"{tex_img.width}x{tex_img.height}"
        else:
            atlas_desc = ", ".join(f"{tn}={im.width}x{im.height}"
                                   for tn, im in tex_img_by_name.items())
        print(f"  -> {len(entries)} sprites, atlas {atlas_desc} (-> {out_dir})")

    save_fps(fps)
    print(f"\nExtracted {len(TARGETS)} bundles / {total_sprites} sprites in {time.time()-t0:.1f}s")
    print(f"Edit PNGs under {EXTRACT_ROOT}/<AtlasName>/sprites/ then run `repack-ui-atlas`.")


# ============================================================================
#                              Repack
# ============================================================================

def _list_edited(name: str, entries: list[dict], spr_dir: Path, fps: dict, force: bool) -> list[dict]:
    """Return only entries whose PNG hash differs from extract-time baseline.
    Missing PNGs are silently ignored (treated as 'not edited')."""
    baseline = fps.get(f"ui_atlas/{name}") or {}
    edited = []
    for s in entries:
        fn = spr_dir / Path(s["file"]).name
        if not fn.is_file():
            continue
        current = sha256_file(fn)
        if force or current != baseline.get(s["file"]):
            edited.append((s, fn))
    return edited


def _repack_one(spec: dict, fps: dict, force: bool) -> tuple[bool, int, str, dict[str, Image.Image]]:
    """Repack a single target bundle. Returns (did_repack, n_edits, message,
    canvases_by_texture_name). The last value lets the caller propagate the
    finished pixel data to the sharedassets-embedded duplicate of the same
    Texture2D (matched by m_Name)."""
    name = spec["name"]
    in_dir = EXTRACT_ROOT / name
    spr_dir = in_dir / "sprites"
    meta_path = in_dir / "_meta.json"
    if not meta_path.is_file():
        return False, 0, "not extracted yet", {}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    edited = _list_edited(name, meta["sprites"], spr_dir, fps, force)
    if not edited:
        return False, 0, "no edits", {}

    src = resolve_source(spec)
    if not src.is_file():
        return False, 0, f"source bundle missing: {src}", {}

    env = UnityPy.load(str(src))

    # Build {texture_path_id: (tex_unitypy_obj, canvas_PIL)} so we can
    # paste-and-save each texture exactly once even if many edited
    # sprites share one atlas.
    if meta["kind"] == "spriteatlas":
        pid_by_sprite = {s["name"]: meta["texture_path_id"] for s in meta["sprites"]}
    else:
        # SpriteStudio: route by texture_name → path_id from meta.textures
        tex_pid = {tn: info["path_id"] for tn, info in meta["textures"].items()}
        pid_by_sprite = {s["name"]: tex_pid[s["texture_name"]] for s in meta["sprites"]}

    required_pids = {pid_by_sprite[s["name"]] for s, _ in edited}
    canvases: dict[int, tuple] = {}
    for o in env.objects:
        if o.path_id in required_pids and o.type.name == "Texture2D":
            t = o.read()
            canvases[o.path_id] = (t, t.image.convert("RGBA"))
    missing = required_pids - set(canvases)
    if missing:
        return False, 0, f"texture path_ids not found: {sorted(missing)}", {}

    # Paste each edited sprite back at its recorded box. The original
    # texture is fully overwritten in that rect; surrounding pixels are
    # untouched, so non-edited sprites stay pristine.
    for s, fn in edited:
        edit_img = Image.open(fn).convert("RGBA")
        box = tuple(s["pil_box"])
        expect_w = box[2] - box[0]
        expect_h = box[3] - box[1]
        # Re-apply packer rotation if any.
        edit_img = apply_rotation_forward(edit_img, s["rotation"])
        if (edit_img.width, edit_img.height) != (expect_w, expect_h):
            # Resize to the original rect; translators may have rendered
            # Chinese at a different scale.
            edit_img = edit_img.resize((expect_w, expect_h), Image.LANCZOS)
        tex, canvas = canvases[pid_by_sprite[s["name"]]]
        canvas.paste(edit_img, (box[0], box[1]))

    canvases_by_tex_name: dict[str, Image.Image] = {}
    for tex, canvas in canvases.values():
        # Upgrade block-compressed formats to RGBA32 to avoid lossy
        # re-encoding of translator artwork. The original format is
        # block-compression (DXT5 / BC*) for atlases that didn't need
        # alpha precision; once we replace the pixels, lossless storage
        # is worth the modest size increase.
        try:
            current_fmt = TextureFormat(int(tex.m_TextureFormat))
        except (ValueError, TypeError):
            current_fmt = None
        if current_fmt in LOSSY_FORMATS:
            tex.m_TextureFormat = TextureFormat.RGBA32
        tex.set_image(canvas)
        tex.save()
        canvases_by_tex_name[tex.m_Name] = canvas

    REPACK_ROOT.mkdir(parents=True, exist_ok=True)
    dst = REPACK_ROOT / spec["bundle"]
    dst.write_bytes(env.file.save(packer="lz4"))

    # Update baselines so a subsequent --no-force run skips this bundle
    # until the translator edits another PNG.
    baseline = fps.setdefault(f"ui_atlas/{name}", {})
    for s, fn in edited:
        baseline[s["file"]] = sha256_file(fn)

    return True, len(edited), str(dst), canvases_by_tex_name


def _patch_sharedassets(canvases_by_tex_name: dict[str, Image.Image]) -> tuple[int, list[str]]:
    """Apply each canvas to the matching Texture2D inside _Data/sharedassets*.assets.

    Returns (files_written, missing_names). For every (tex_name, canvas), we
    scan sharedassets*.assets looking for a Texture2D with that m_Name, then
    inline the new pixels into the SerializedFile (UnityPy clears the
    m_StreamData reference automatically on `set_image` + `save`). The
    original .resS sidecar is left untouched — other textures in the same
    .assets file keep loading from it via their stored offsets.
    """
    if not canvases_by_tex_name:
        return 0, []

    data_dir = cfg.app_data_dir()
    # First pass: discover (file, path_id) for each tex name. Cheap enough
    # to do every repack — ~10 sharedassets files, m_Name is read from the
    # serialized header without touching .resS.
    targets_remaining = set(canvases_by_tex_name)
    by_file: dict[Path, list[tuple[int, str]]] = {}
    for assets_path in sorted(data_dir.glob("sharedassets*.assets")):
        if not targets_remaining:
            break
        try:
            env = UnityPy.load(str(assets_path))
        except Exception:
            continue
        for o in env.objects:
            if o.type.name != "Texture2D":
                continue
            try:
                t = o.read()
            except Exception:
                continue
            if t.m_Name in targets_remaining:
                by_file.setdefault(assets_path, []).append((o.path_id, t.m_Name))
                targets_remaining.discard(t.m_Name)
                if not targets_remaining:
                    break

    if not by_file:
        return 0, sorted(targets_remaining)

    REPACK_SHAREDASSETS_ROOT.mkdir(parents=True, exist_ok=True)
    files_written = 0
    for assets_path, ops in by_file.items():
        env = UnityPy.load(str(assets_path))
        pid_to_name = {pid: tex_name for pid, tex_name in ops}
        applied = 0
        for o in env.objects:
            if o.type.name != "Texture2D" or o.path_id not in pid_to_name:
                continue
            tex = o.read()
            canvas = canvases_by_tex_name[pid_to_name[o.path_id]]
            try:
                current_fmt = TextureFormat(int(tex.m_TextureFormat))
            except (ValueError, TypeError):
                current_fmt = None
            if current_fmt in LOSSY_FORMATS:
                tex.m_TextureFormat = TextureFormat.RGBA32
            tex.set_image(canvas)
            tex.save()
            applied += 1
        out = REPACK_SHAREDASSETS_ROOT / assets_path.name
        out.write_bytes(env.file.save())
        names = ", ".join(sorted(n for _, n in ops))
        print(f"  sharedassets  {assets_path.name:24s}  inlined {applied} textures ({names}) -> {out.name}")
        files_written += 1

    return files_written, sorted(targets_remaining)


def cmd_repack(force: bool = False):
    """Read edited PNGs, paste back into a clean copy of each source
    bundle's atlas, write to repacked_bundles/ui_atlas/<bundle>. Also inline
    the same pixels into the matching sharedassets*.assets duplicate (if
    any), staged under repacked_bundles/ui_atlas_sharedassets/."""
    if not EXTRACT_ROOT.is_dir():
        print(f"ERROR: {EXTRACT_ROOT} does not exist; run `extract-ui-atlas` first.")
        sys.exit(1)
    fps = load_fps()
    t0 = time.time()
    repacked = skipped = failed = 0
    total_edits = 0
    canvases_all: dict[str, Image.Image] = {}
    for spec in TARGETS:
        try:
            did, n_edits, msg, canvases = _repack_one(spec, fps, force)
        except Exception as e:
            print(f"  {spec['name']:10s}  ERROR: {e!r}")
            failed += 1
            continue
        if did:
            print(f"  {spec['name']:10s}  repacked  ({n_edits} edits) -> {Path(msg).name}")
            repacked += 1
            total_edits += n_edits
            canvases_all.update(canvases)
        else:
            print(f"  {spec['name']:10s}  skipped   ({msg})")
            skipped += 1

    sa_files_written = 0
    sa_missing: list[str] = []
    if canvases_all:
        sa_files_written, sa_missing = _patch_sharedassets(canvases_all)
        if sa_missing:
            # Expected for FooterUI (no sharedassets twin); silent for those.
            print(f"  (note) no sharedassets duplicate for: {sa_missing}")

    if repacked:
        save_fps(fps)
    print(f"\nDone in {time.time()-t0:.1f}s. "
          f"repacked={repacked} skipped={skipped} failed={failed} "
          f"total_edits={total_edits}  -> {REPACK_ROOT}")
    if sa_files_written:
        print(f"Patched {sa_files_written} sharedassets file(s)  -> {REPACK_SHAREDASSETS_ROOT}")


# ============================================================================
#                              CLI
# ============================================================================

def main():
    ap = argparse.ArgumentParser(prog="extract_ui_atlas",
        description="Extract / repack the 4 bitmap-baked UI atlases.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_e = sub.add_parser("extract", help="Unpack atlases to extracted_data/ui_atlas/")
    ap_e.add_argument("--yes", "-y", action="store_true",
                      help="Overwrite existing extract without asking.")

    ap_r = sub.add_parser("repack", help="Repack edited PNGs into repacked_bundles/ui_atlas/")
    ap_r.add_argument("--force", "-f", action="store_true",
                      help="Repack every bundle regardless of fingerprint state.")

    args = ap.parse_args()
    if args.cmd == "extract":
        cmd_extract(yes=args.yes)
    elif args.cmd == "repack":
        cmd_repack(force=args.force)


if __name__ == "__main__":
    main()
