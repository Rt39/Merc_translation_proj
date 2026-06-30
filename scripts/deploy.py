"""Deploy repacked bundles into the live cache.

Used after `mercstoria repack` produces translated bundles
under `repacked_bundles/{story,misc}/`. This script overwrites the
game-folder cache copies. Each replaced original is mirrored, in the
same relative layout, under a sibling `AssetBundle_old/` tree alongside
the live `AssetBundle/`. Rolling back means copying that tree back over
the live one; deleting it discards the originals in one shot.

Backup invariant: a file under `AssetBundle_old/` is the **pristine
original** the very first time it was overwritten. Subsequent deploys
NEVER touch it — re-running deploy keeps the first-seen original intact.

Target: always `<game>/AssetBundle/StandaloneWindows64`. Deploy refuses
if the game-folder cache is empty — run `mercstoria bundle-cache` first.
The LocalLow cache is no longer the rollback (`AssetBundle_old/` is);
it can be purged for disk reclaim via `mercstoria release
--purge-locallow-cache`, which also clears any launcher `.pre_setup` backups.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tqdm import tqdm

from mercstoria import config as cfg

cfg.enable_utf8_stdout()


# Output of `mercstoria repack` — always at the repo root, not next
# to this script (which now lives in scripts/).
_HERE = Path(__file__).resolve().parent.parent
REPACKED_STORY     = _HERE / "repacked_bundles" / "story"
REPACKED_MISC      = _HERE / "repacked_bundles" / "misc"
REPACKED_INLINE_UI = _HERE / "repacked_bundles" / "inline_ui"
REPACKED_UI_LABELS = _HERE / "repacked_bundles" / "ui_labels"
REPACKED_UI_ATLAS  = _HERE / "repacked_bundles" / "ui_atlas"
# Patched <_Data>/sharedassets*.assets (CommonUI/GalleryUI/HomeUI duplicates,
# inlined by `repack-ui-atlas`). The matching .resS sidecars are NOT touched
# — UnityPy inlines the modified texture into the .assets itself.
REPACKED_UI_ATLAS_SHAREDASSETS = _HERE / "repacked_bundles" / "ui_atlas_sharedassets"


def _resolve_paths() -> tuple[Path, Path]:
    """Return (live cache root, backup mirror root).

    Both rooted at `<game>/AssetBundle{,_old}/StandaloneWindows64`.
    """
    live = cfg.game_dir() / "AssetBundle" / "StandaloneWindows64"
    backup = cfg.game_dir() / "AssetBundle_old" / "StandaloneWindows64"
    return live, backup


def deploy(src_dir: Path, dst_dir: Path, backup_dir: Path, label: str) -> int:
    """Copy every bundle in `src_dir` over the matching name in `dst_dir`.

    Idempotency / safety:
        * If `src_dir` doesn't exist, skip silently (translator might only
          have repacked one of {story, misc}).
        * Per file, mirror an existing destination into `backup_dir/<name>`
          ONCE — if the backup already exists it is **never** overwritten
          (the first copy is the pristine original).
        * `shutil.copy2` preserves mtime so re-bundling later sees stable
          timestamps.

    Returns the number of files copied.
    """
    if not src_dir.is_dir():
        print(f"  ({label}) {src_dir} — empty, nothing to deploy")
        return 0

    dst_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(os.listdir(src_dir))
    count = 0
    for fname in tqdm(files, desc=f"deploy-{label}", unit="bundle"):
        src = src_dir / fname
        dst = dst_dir / fname
        bak = backup_dir / fname
        if dst.exists() and not bak.exists():
            shutil.copy2(dst, bak)
        shutil.copy2(src, dst)
        count += 1
    print(f"  ({label}) {count} bundles deployed to {dst_dir}")
    return count


def _deploy_ui_atlas_sharedassets() -> int:
    """Copy patched <_Data>/sharedassets*.assets files into the live install.

    Each file is backed up to `<name>.bak` next to the original on first
    deploy; subsequent runs never overwrite the .bak (same first-seen invariant
    as `deploy()`). The .resS sidecars are intentionally left alone — the
    repack step inlines the modified Texture2D into the .assets, and other
    textures in the same file still reference the unchanged .resS.
    """
    if not REPACKED_UI_ATLAS_SHAREDASSETS.is_dir():
        return 0
    data_dir = cfg.app_data_dir()
    count = 0
    for fname in sorted(os.listdir(REPACKED_UI_ATLAS_SHAREDASSETS)):
        if not fname.endswith(".assets"):
            continue
        src = REPACKED_UI_ATLAS_SHAREDASSETS / fname
        dst = data_dir / fname
        bak = data_dir / f"{fname}.bak"
        if not dst.exists():
            print(f"  (ui-atlas-sa) {fname}: target does not exist at {dst}, skipping")
            continue
        if not bak.exists():
            shutil.copy2(dst, bak)
        shutil.copy2(src, dst)
        print(f"  (ui-atlas-sa) {fname} -> {dst}")
        count += 1
    if count:
        print(f"  (ui-atlas-sa) {count} sharedassets file(s) patched in {data_dir}")
    return count


def _deploy_ui_atlas(cache_ui: Path, sa_live: Path,
                     backup_ui: Path, sa_backup: Path) -> int:
    """Route each repacked ui_atlas bundle to BundleAssets or StreamingAssets
    based on its TARGETS entry's source_dir. Same per-file backup invariant
    as `deploy()`."""
    if not REPACKED_UI_ATLAS.is_dir():
        print(f"  (ui-atlas) {REPACKED_UI_ATLAS} — empty, nothing to deploy")
        return 0
    from scripts.extract_ui_atlas import TARGETS  # local import: optional dep
    by_bundle = {t["bundle"]: t for t in TARGETS}

    count = 0
    for fname in sorted(os.listdir(REPACKED_UI_ATLAS)):
        spec = by_bundle.get(fname)
        if spec is None:
            print(f"  (ui-atlas) {fname}: no TARGETS entry, skipping")
            continue
        if spec["source_dir"] == "sa":
            dst_dir, bak_dir = sa_live, sa_backup
        elif spec["source_dir"] == "ba":
            dst_dir, bak_dir = cache_ui, backup_ui
        else:
            print(f"  (ui-atlas) {fname}: unknown source_dir {spec['source_dir']!r}")
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        bak_dir.mkdir(parents=True, exist_ok=True)
        src = REPACKED_UI_ATLAS / fname
        dst = dst_dir / fname
        bak = bak_dir / fname
        if dst.exists() and not bak.exists():
            shutil.copy2(dst, bak)
        shutil.copy2(src, dst)
        print(f"  (ui-atlas) {spec['name']:10s} -> {dst}")
        count += 1
    print(f"  (ui-atlas) {count} bundles deployed")
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args()

    root, backup_root = _resolve_paths()
    if not root.is_dir() or not any(root.iterdir()):
        raise SystemExit(
            f"deploy: {root} is empty or missing. Run\n"
            f"    uv run -m mercstoria bundle-cache\n"
            f"first so the cache lives in the game folder. Deploy refuses\n"
            f"to write to LocalLow — that copy is reserved as the rollback\n"
            f"snapshot."
        )

    cache_story  = root / cfg.STORY_MASTERDATA_SUBDIR
    cache_master = root / cfg.MASTERDATA_SUBDIR
    cache_ui     = root / cfg.BUNDLEASSETS_SUBDIR
    backup_story  = backup_root / cfg.STORY_MASTERDATA_SUBDIR
    backup_master = backup_root / cfg.MASTERDATA_SUBDIR
    backup_ui     = backup_root / cfg.BUNDLEASSETS_SUBDIR

    print(f"Deploy target: {root}")
    print(f"Backup mirror: {backup_root}")
    n_story = deploy(REPACKED_STORY,     cache_story,  backup_story,  "story")
    n_misc  = deploy(REPACKED_MISC,      cache_master, backup_master, "misc")
    n_ui    = deploy(REPACKED_INLINE_UI, cache_ui,     backup_ui,     "inline-ui")

    sa_live   = cfg.streaming_assets_dir()
    sa_backup = cfg.app_data_dir() / "StreamingAssets" / "aa" / "StandaloneWindows64_old"
    n_labels  = deploy(REPACKED_UI_LABELS, sa_live, sa_backup, "ui-labels")

    n_atlas      = _deploy_ui_atlas(cache_ui, sa_live, backup_ui, sa_backup)
    n_atlas_sa   = _deploy_ui_atlas_sharedassets()

    print(f"\nTotal: {n_story + n_misc + n_ui + n_labels + n_atlas + n_atlas_sa} files. Originals mirrored under")
    print(f"  {backup_root.parent}  (story/misc/inline-ui/ui-atlas-ba)")
    print(f"  {sa_backup.parent}  (ui-labels / ui-atlas-sa → StreamingAssets/aa/)")
    if n_atlas_sa:
        print(f"  {cfg.app_data_dir()}\\sharedassets*.assets.bak  (ui-atlas-sa duplicates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
