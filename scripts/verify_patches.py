"""Sanity-check both CRC and offline-mode patches in the live GameAssembly.dll.

Read-only. Reports OK / MISSING for every site. Useful after a game update
to confirm the patch set still applies cleanly.
"""
import sys

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercstoria import config as cfg

cfg.enable_utf8_stdout()


def main() -> int:
    dll_path = cfg.dll_path()
    if not dll_path.exists():
        print(f"ERROR: {dll_path} not found")
        return 1

    dll = dll_path.read_bytes()
    _, sections = cfg.parse_pe_sections(dll)

    # CRC patches live at file offsets.
    print("=== CRC patches (file offsets) ===")
    crc_ok = True
    for name, foff, _orig, new in cfg.CRC_PATCHES:
        got = dll[foff:foff + len(new)]
        status = "OK" if got == new else "MISSING"
        if got != new:
            crc_ok = False
        print(f"  {name:35s} foff 0x{foff:08X}  {got.hex():<10s}  {status}")

    # Offline patches — verify by head bytes at RVA.
    offline_sites = [
        ("S1: SteamApplication.Initialize -> ret",      cfg.RVA_STEAM_APP_INIT,      bytes.fromhex("C3909090")),
        ("S2: Impl.Initialize -> ret",                  cfg.RVA_IMPL_INIT,           bytes.fromhex("C3909090")),
        ("S3: Impl.GetLanguage -> Stub",                cfg.RVA_IMPL_GETLANG,        bytes.fromhex("E9FBFDFFFF")),
        ("S4: Impl.GetUserDataRootPath -> Stub",        cfg.RVA_IMPL_GETROOT,        bytes.fromhex("E91BFEFFFF")),
        ("Y1: YAHH.get_SkipCertVerification = true",    cfg.RVA_YAHH_GET_SKIP,       bytes.fromhex("66B80101C3")),
        ("Y2: NCS.get_SkipCertVerification = true",     cfg.RVA_NCS_GET_SKIP,        bytes.fromhex("66B80101C3")),
        ("Y3: ctor call -> set_SkipCertVerification",   cfg.RVA_CTOR_HTTP2ONLY_CALL, bytes.fromhex("E88750ECFD")),
        ("P:  GetAsync(5-arg) -> file-read prologue",   cfg.RVA_GETASYNC_5ARG,       bytes.fromhex("534883EC4048894C2430498BD8")),
    ]
    print("\n=== Offline patches (RVA -> file) ===")
    off_ok = True
    for name, rva, exp in offline_sites:
        foff = cfg.rva_to_file_offset(rva, sections)
        got = dll[foff:foff + len(exp)] if foff is not None else b""
        ok = got == exp
        if not ok:
            off_ok = False
        status = "OK" if ok else "MISSING"
        print(f"  {name:45s} RVA 0x{rva:08X}  {got.hex():<26s}  {status}")

    print()
    return 0 if (crc_ok and off_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
