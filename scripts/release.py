"""One-shot post-translation orchestrator: `mercstoria release`.

After translators finish editing the JSONs under `extracted_data/`, this
runs the two remaining steps:

    1. repack  — repack changed JSONs into UnityFS bundles under
                 `repacked_bundles/{story,misc}/`. Uses fingerprint tracking
                 to skip files the translator left untouched.
    2. deploy  — copy the repacked bundles into the live cache (auto-prefers
                 the game-folder cache over LocalLow when both exist).

Each step is idempotent. Skip individually with `--skip-repack` or
`--skip-deploy`. Pass `--force` to repack every JSON regardless of
fingerprint, and `--target {auto,game,persistent}` to override the deploy
destination.

Optional final cleanup: pass `--purge-locallow-cache` to delete the
LocalLow `AssetBundle/` directory once the cache lives in the game folder.
The flag is intentionally long because the operation is destructive — the
step refuses unless `<game>/AssetBundle/StandaloneWindows64` exists and is
non-empty, and a real (non-junction) LocalLow directory still needs the
user to type `DELETE` (or pass `--yes`).
"""
from __future__ import annotations

import argparse
import os
import runpy
import shutil
import sys
from pathlib import Path

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercstoria import config as cfg

cfg.enable_utf8_stdout()


_HERE = Path(__file__).resolve().parent


def _run_script(script_name: str, argv: list[str]) -> None:
    """Execute `scripts/<name>.py` as if invoked from the CLI.

    Child scripts end with `sys.exit(main())`, so SystemExit fires on every
    successful run. Swallow zero exits, propagate non-zero.
    """
    target = _HERE / f"{script_name}.py"
    if not target.is_file():
        raise SystemExit(f"release: missing {target}")
    saved_argv = sys.argv
    try:
        sys.argv = [str(target), *argv]
        try:
            runpy.run_path(str(target), run_name="__main__")
        except SystemExit as e:
            code = e.code
            if code is None or code == 0:
                return
            raise SystemExit(f"release: step `{script_name}` failed (exit code {code}).")
    finally:
        sys.argv = saved_argv


def _step(label: str) -> None:
    print()
    print("=" * 72)
    print(f"  [release] {label}")
    print("=" * 72)


def step_repack(force: bool) -> None:
    _step("Step 1/2 — repack changed JSONs")
    args = ["repack"]
    if force:
        args.append("--force")
    _run_script("extract_repack", args)


def step_deploy() -> None:
    _step("Step 2/2 — deploy repacked bundles to cache")
    _run_script("deploy", [])


def _is_reparse_point(p: Path) -> bool:
    """True if `p` is a junction / symlink (Windows reparse point)."""
    try:
        if p.is_symlink():
            return True
        # FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        return bool(p.lstat().st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return False


def step_purge_locallow(yes: bool) -> None:
    """Optional final step: reclaim the LocalLow `AssetBundle/`.

    Two states LocalLow can be in:
      * junction (post-launcher) — just unlink the reparse point; the
        actual data lives in <game>/AssetBundle and is untouched.
      * real directory (pre-launcher, post-bundle-cache) — recursive
        delete. Refuses unless <game>/AssetBundle/StandaloneWindows64
        exists and is non-empty, and (without --yes) requires the user
        to type `DELETE` so a stray re-run can't wipe a populated cache.
    """
    _step("Step 3/3 — purge LocalLow cache (--purge-locallow-cache)")

    persist = cfg.persist_assetbundle()
    if not persist.exists():
        print(f"  {persist} does not exist; nothing to purge.")
        return

    game_cache = cfg.game_dir() / "AssetBundle"
    sw64 = game_cache / "StandaloneWindows64"
    if not sw64.is_dir() or not any(sw64.iterdir()):
        raise SystemExit(
            f"  REFUSED: {sw64} is empty or missing. Run\n"
            f"    uv run -m mercstoria bundle-cache\n"
            f"  first so the cache is mirrored into the game folder. "
            f"Otherwise this step would delete the only copy of the "
            f"15 GB CDN cache."
        )

    if _is_reparse_point(persist):
        # Junction — the data lives in <game>/AssetBundle. Removing the
        # link itself is a single os.unlink; the target is unaffected.
        os.unlink(persist)
        print(f"  [unlink] {persist}")
        print(f"  Junction removed; cache in {game_cache} untouched.")
        return

    # Real directory — much higher stakes. Demand explicit confirmation.
    print(f"  About to recursively DELETE: {persist}")
    print(f"  Rollback already lives at {game_cache.parent / 'AssetBundle_old'};")
    print(f"  LocalLow is redundant duplicate storage at this point.")
    if not yes:
        resp = input("  Type DELETE to proceed (anything else aborts): ").strip()
        if resp != "DELETE":
            print("  Aborted; LocalLow left intact.")
            return
    else:
        print("  --yes given; proceeding without prompt.")

    shutil.rmtree(persist)
    print(f"  [purge] {persist}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-repack", action="store_true",
                    help="Skip repack (re-deploy whatever is already under repacked_bundles/).")
    ap.add_argument("--skip-deploy", action="store_true",
                    help="Skip deploy (just rebuild bundles, leave them under repacked_bundles/).")
    ap.add_argument("--force",       action="store_true",
                    help="Repack every JSON regardless of fingerprint.")
    ap.add_argument("--purge-locallow-cache", action="store_true",
                    help="After deploy, delete %%LocalLow%%/.../AssetBundle. "
                         "AssetBundle_old/ is the rollback now; LocalLow is "
                         "redundant duplicate storage. For a real "
                         "(non-junction) LocalLow, also requires typing DELETE "
                         "at the prompt (or --yes).")
    ap.add_argument("--yes", "-y",   action="store_true",
                    help="Auto-confirm the purge prompt. No effect without "
                         "--purge-locallow-cache.")
    args = ap.parse_args()

    print(f"== mercstoria release ==")
    print(f"  game:    {cfg.game_dir()}")

    if not args.skip_repack:
        step_repack(force=args.force)
    if not args.skip_deploy:
        step_deploy()
    if args.purge_locallow_cache:
        step_purge_locallow(yes=args.yes)

    print()
    print("=" * 72)
    print("  release done. Launch the game to verify.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
