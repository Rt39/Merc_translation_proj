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
"""
from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercstoria import config as cfg

cfg.enable_utf8_stdout()


_HERE = Path(__file__).resolve().parent


def _run_script(script_name: str, argv: list[str]) -> None:
    """Execute `scripts/<name>.py` as if invoked from the CLI."""
    target = _HERE / f"{script_name}.py"
    if not target.is_file():
        raise SystemExit(f"release: missing {target}")
    saved_argv = sys.argv
    try:
        sys.argv = [str(target), *argv]
        runpy.run_path(str(target), run_name="__main__")
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


def step_deploy(target: str) -> None:
    _step("Step 2/2 — deploy repacked bundles to cache")
    _run_script("deploy", ["--target", target])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-repack", action="store_true",
                    help="Skip repack (re-deploy whatever is already under repacked_bundles/).")
    ap.add_argument("--skip-deploy", action="store_true",
                    help="Skip deploy (just rebuild bundles, leave them under repacked_bundles/).")
    ap.add_argument("--force",       action="store_true",
                    help="Repack every JSON regardless of fingerprint.")
    ap.add_argument("--target",      choices=("auto", "game", "persistent"), default="auto",
                    help="Deploy target. auto = game folder when present, else %%LocalLow%%.")
    args = ap.parse_args()

    print(f"== mercstoria release ==")
    print(f"  game:    {cfg.game_dir()}")

    if not args.skip_repack:
        step_repack(force=args.force)
    if not args.skip_deploy:
        step_deploy(target=args.target)

    print()
    print("=" * 72)
    print("  release done. Launch the game to verify.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
