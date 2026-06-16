"""Deploy repacked bundles into the live cache.

Used after `mercstoria repack` produces translated bundles
under `repacked_bundles/{story,misc}/`. This script overwrites the live
cache copies and keeps a `.bak` of each replaced original so a rollback
is one `shutil.copy2` away.

Layout decision (post-launcher):

    Pristine install                Bundled / launcher-deployed install
    -----------------               -----------------------------------
    No `<game>/AssetBundle/`        `<game>/AssetBundle/` is the source
    -> cache lives only at          of truth. Persistent path is a
       `<persistent>/AssetBundle`      junction back into the game folder
                                       (created by `launcher.exe` on first
                                       run), so writing through either
                                       location reaches the same file.

We prefer the game-folder location when it exists because:
  * after `bundle_cache.py`, that's the canonical home of the cache;
  * if the user hasn't run the launcher yet, the persistent-path side
    may not even have a `StoryMasterData/` subfolder.

Override either side explicitly with --target.
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

from mercstoria import config as cfg

cfg.enable_utf8_stdout()


# Output of `mercstoria repack` — always at the repo root, not next
# to this script (which now lives in scripts/).
_HERE = Path(__file__).resolve().parent.parent
REPACKED_STORY = _HERE / "repacked_bundles" / "story"
REPACKED_MISC  = _HERE / "repacked_bundles" / "misc"


def _resolve_cache_root(target: str | None) -> Path:
    """Pick the cache root we'll write into.

    `target` is the user's --target flag:
        "auto"      (default): prefer <game>/AssetBundle if it has the
                    expected StandaloneWindows64 subtree; otherwise fall
                    back to <persistent>/AssetBundle.
        "game"      force the game-folder location.
        "persistent" force the LocalLow location.

    Returns the resolved `AssetBundle/StandaloneWindows64` directory.
    """
    game_root    = cfg.game_dir() / "AssetBundle" / "StandaloneWindows64"
    persist_root = cfg.cache_root()

    if target == "game":
        return game_root
    if target == "persistent":
        return persist_root
    # auto:
    if game_root.is_dir():
        return game_root
    return persist_root


def deploy(src_dir: Path, dst_dir: Path, label: str) -> int:
    """Copy every bundle in `src_dir` over the matching name in `dst_dir`.

    Idempotency / safety:
        * If `src_dir` doesn't exist, skip silently (translator might only
          have repacked one of {story, misc}).
        * Per file, back up an existing destination to `<dst>.bak` once —
          subsequent runs don't clobber the original .bak.
        * `shutil.copy2` preserves mtime so re-bundling later sees stable
          timestamps.

    Returns the number of files copied.
    """
    if not src_dir.is_dir():
        print(f"  ({label}) {src_dir} — empty, nothing to deploy")
        return 0

    dst_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for fname in sorted(os.listdir(src_dir)):
        src = src_dir / fname
        dst = dst_dir / fname
        bak = dst.with_suffix(dst.suffix + ".bak")
        if dst.exists() and not bak.exists():
            shutil.copy2(dst, bak)
        shutil.copy2(src, dst)
        count += 1
    print(f"  ({label}) {count} bundles deployed to {dst_dir}")
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--target",
        choices=("auto", "game", "persistent"),
        default="auto",
        help="Where to write. 'auto' = game folder if it has the expected "
             "subtree, else %%LocalLow%%. 'game' / 'persistent' force one side.",
    )
    args = ap.parse_args()

    root = _resolve_cache_root(args.target)
    cache_story  = root / cfg.STORY_MASTERDATA_SUBDIR
    cache_master = root / cfg.MASTERDATA_SUBDIR

    print(f"Deploy target ({args.target}): {root}")
    n_story = deploy(REPACKED_STORY, cache_story,  "story")
    n_misc  = deploy(REPACKED_MISC,  cache_master, "misc")
    print(f"\nTotal: {n_story + n_misc} bundles. Originals backed up to *.bak "
          f"next to each replaced file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
