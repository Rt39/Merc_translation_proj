"""Single-entry CLI dispatcher for the Merc Storia toolkit.

Usage:
    uv run -m mercstoria <subcommand> [args...]

Subcommands forward all remaining args verbatim to the underlying script
under `scripts/`. Run `uv run -m mercstoria` without args to see the list.

Adding a new subcommand: drop a `scripts/<name>.py` and register it in
SUBCOMMANDS below — short description shown in --help only.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

SUBCOMMANDS = {
    "setup":           ("setup",           "End-to-end pre-translation: patch DLL + font swap + extract + bundle cache + deploy launcher"),
    "release":         ("release",         "End-to-end post-translation: repack changed JSONs + deploy to cache"),
    "extract":         ("extract_repack",  "Extract story + misc + inline UI to extracted_data/"),
    "repack":          ("extract_repack",  "Repack story + misc + inline UI to repacked_bundles/"),
    "deploy":          ("deploy",          "Deploy repacked_bundles/ to the live cache"),
    "bundle-cache":    ("bundle_cache",    "Bundle the LocalLow CDN cache into the game folder for offline mode"),
    "font-swap":       ("font_swap",       "Replace the in-game font atlas"),
    "export-chars":    ("export_chars",    "Generate the target_chars.txt for the TMP font bake"),
    "patch-crc":       ("patch_crc",       "Apply the four CRC bypass patches to GameAssembly.dll"),
    "patch-offline":   ("patch_offline",   "Apply the offline-mode patch set to GameAssembly.dll"),
    "patch-metadata":  ("patch_metadata",  "Patch country enum names in global-metadata.dat"),
    "verify-patches":  ("verify_patches",  "Sanity-check CRC + offline-mode patches"),
    "check-roundtrip": ("check_roundtrip", "Round-trip every cached story bundle through Reader/Writer"),
}


def _print_usage() -> None:
    print("Usage: uv run -m mercstoria <subcommand> [args...]\n")
    print("Subcommands:")
    width = max(len(s) for s in SUBCOMMANDS)
    for name, (_script, desc) in SUBCOMMANDS.items():
        print(f"  {name:<{width}}  {desc}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        _print_usage()
        return 0

    subcmd = args[0]
    forward = args[1:]

    if subcmd in SUBCOMMANDS:
        script = SUBCOMMANDS[subcmd][0]
        # extract / repack share the extract_repack.py script — its argparse
        # expects the verb as the first positional.
        if subcmd in ("extract", "repack"):
            forward = [subcmd] + forward
    else:
        print(f"unknown subcommand: {subcmd!r}\n", file=sys.stderr)
        _print_usage()
        return 2

    target = SCRIPTS / f"{script}.py"
    sys.argv = [str(target), *forward]
    runpy.run_path(str(target), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
