"""One-shot pre-translation orchestrator: `mercstoria setup`.

Runs every step a translator needs BEFORE editing JSONs:

    1. patch-crc      — disable Unity's bundle CRC validation
    2. patch-offline  — Steam bypass + cert skip + file-read GetAsync
    3. font-swap      — replace TMP font (atlas + bundle + hidden font)
    4. extract        — dump every story + 15 master bundles to extracted_data/
    5. bundle-cache   — copy LocalLow CDN cache into <game>/AssetBundle/    (optional)
    6. deploy-launcher — drop launcher.exe into the game folder              (optional)

Each step is idempotent — re-running skips work that's already done. Skip
individual steps with `--skip-<name>` (e.g. `--skip-bundle-cache` if you
haven't populated the LocalLow cache yet, or `--skip-launcher` if you're
running against a pristine install rather than building a redistributable).

After this finishes, edit the JSONs under `extracted_data/`, then run
`mercstoria release` to repack and deploy.

Prerequisites:
  * Game installed under one of the known Steam library roots (or
    MERCSTORIA_GAME_DIR set to point at it).
  * `logofont.bundle` checked in at the repo root (this is the prebuilt
    TMP font asset bundle — see docs/FONT_REPLACEMENT_GUIDE.md for how to
    rebake it from a different source TTF).
  * `launcher/build/Release/launcher.exe` exists (build with cmake first,
    or pass `--skip-launcher`).
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


_HERE = Path(__file__).resolve().parent          # scripts/
_ROOT = _HERE.parent                              # repo root
LOGOFONT_BUNDLE = _ROOT / "logofont.bundle"
LAUNCHER_EXE    = _ROOT / "launcher" / "build" / "Release" / "launcher.exe"


def _run_script(script_name: str, argv: list[str]) -> None:
    """Execute `scripts/<name>.py` as if it were invoked from the CLI.

    Child scripts end with `sys.exit(main())`, which raises SystemExit even
    when main() returns 0. We swallow the zero exits so the orchestrator
    keeps going, but propagate non-zero (and non-int) codes so a real
    failure aborts the whole sequence.
    """
    target = _HERE / f"{script_name}.py"
    if not target.is_file():
        raise SystemExit(f"setup: missing {target}")
    saved_argv = sys.argv
    try:
        sys.argv = [str(target), *argv]
        try:
            runpy.run_path(str(target), run_name="__main__")
        except SystemExit as e:
            code = e.code
            if code is None or code == 0:
                return
            raise SystemExit(f"setup: step `{script_name}` failed (exit code {code}).")
    finally:
        sys.argv = saved_argv


def _step(label: str) -> None:
    print()
    print("=" * 72)
    print(f"  [setup] {label}")
    print("=" * 72)


def step_patch_crc() -> None:
    _step("Step 1/6 — CRC bypass (4 sites)")
    _run_script("patch_crc", [])


def step_patch_offline() -> None:
    _step("Step 2/6 — offline-mode patches (8 sites)")
    _run_script("patch_offline", [])


def step_font_swap() -> None:
    _step("Step 3/6 — font swap (atlas + bundle + hidden font)")
    if not LOGOFONT_BUNDLE.is_file():
        raise SystemExit(
            f"setup: {LOGOFONT_BUNDLE} not found. The repo ships a prebuilt "
            f"font bundle; if it's missing, rebake one per "
            f"docs/FONT_REPLACEMENT_GUIDE.md or pass --skip-font."
        )
    _run_script("font_swap", [str(LOGOFONT_BUNDLE)])


def step_extract() -> None:
    _step("Step 4/6 — extract stories + 15 master bundles")
    _run_script("extract_repack", ["extract"])


def step_bundle_cache(yes: bool) -> None:
    _step("Step 5/6 — bundle LocalLow cache into the game folder")
    args = []
    if yes:
        args.append("--yes")
    _run_script("bundle_cache", args)


def step_deploy_launcher() -> None:
    _step("Step 6/6 — deploy launcher to the game folder")
    if not LAUNCHER_EXE.is_file():
        raise SystemExit(
            f"setup: {LAUNCHER_EXE} not found. Build it with\n"
            f"    cmake -S launcher -B launcher/build -A x64\n"
            f"    cmake --build launcher/build --config Release\n"
            f"or pass --skip-launcher."
        )

    game = cfg.game_dir()
    pristine_exe   = game / cfg.APP_EXE_NAME
    chs_launcher   = game / cfg.APP_EXE_CHS

    if not pristine_exe.is_file():
        raise SystemExit(
            f"setup: {pristine_exe} not found — the original game exe must be "
            f"present so the launcher can chain into it."
        )

    shutil.copy2(LAUNCHER_EXE, chs_launcher)
    print(f"  [copy] {LAUNCHER_EXE} -> {chs_launcher}")
    print(f"  Original {cfg.APP_EXE_NAME} left untouched. Double-click "
          f"{cfg.APP_EXE_CHS} to launch the translated build.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-crc",          action="store_true", help="Skip CRC bypass.")
    ap.add_argument("--skip-offline",      action="store_true", help="Skip offline-mode patches.")
    ap.add_argument("--skip-font",         action="store_true", help="Skip font swap.")
    ap.add_argument("--skip-extract",      action="store_true", help="Skip JSON extraction.")
    ap.add_argument("--skip-bundle-cache", action="store_true", help="Skip LocalLow→game cache copy.")
    ap.add_argument("--skip-launcher",     action="store_true", help="Skip launcher deploy.")
    ap.add_argument("--yes", "-y",         action="store_true",
                    help="Auto-confirm the bundle-cache prompt.")
    args = ap.parse_args()

    print(f"== mercstoria setup ==")
    print(f"  game:    {cfg.game_dir()}")
    print(f"  cwd:     {Path.cwd()}")

    if not args.skip_crc:
        step_patch_crc()
    if not args.skip_offline:
        step_patch_offline()
    if not args.skip_font:
        step_font_swap()
    if not args.skip_extract:
        step_extract()
    if not args.skip_bundle_cache:
        step_bundle_cache(yes=args.yes)
    if not args.skip_launcher:
        step_deploy_launcher()

    print()
    print("=" * 72)
    print("  setup done. Edit JSONs under extracted_data/, then run:")
    print("    uv run -m mercstoria release")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
