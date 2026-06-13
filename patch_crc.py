"""Apply all four CRC patches to GameAssembly.dll.

Disables Unity Addressables bundle CRC validation at all four sites so that
modified .bundle files load without being silently replaced from the CDN.
See docs/CRC_PATCH_GUIDE.md for the discovery / disassembly story.

Each site loads the bundle CRC into edx before calling into the validator;
replacing each load with `xor edx, edx` makes Unity take the documented
"skip CRC validation" path.

  Site 1-2: cache-load and download paths (UnityWebRequestAssetBundle inlining).
  Site 3-4: hash-comparison helpers invoked from both paths.

Idempotent: re-running is a no-op.
"""
import sys, os, shutil
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

import mercstoria_config as cfg

cfg.enable_utf8_stdout()


def main() -> int:
    dll_path = cfg.dll_path()
    bak_path = cfg.dll_backup_path()

    if not bak_path.exists():
        if not dll_path.exists():
            print(f"ERROR: {dll_path} does not exist. Is the game installed?")
            return 1
        shutil.copy2(dll_path, bak_path)
        print(f"Created backup: {bak_path}")

    dll = bytearray(bak_path.read_bytes())
    print(f"Read original from backup ({len(dll):,} bytes)")

    image_base, sections = cfg.parse_pe_sections(bytes(dll))
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True

    # Verify originals
    print("\n=== Verifying original bytes ===")
    all_ok = True
    already_patched = True
    for name, foff, orig, new in cfg.CRC_PATCHES:
        actual = bytes(dll[foff:foff + len(orig)])
        is_orig = actual == orig
        is_new = actual == new
        if not is_orig and not is_new:
            all_ok = False
        if not is_new:
            already_patched = False
        rva = cfg.file_offset_to_rva(foff, sections)
        status = "OK" if is_orig else ("ALREADY PATCHED" if is_new else "MISMATCH!")
        print(f"  {name} (file 0x{foff:X}, RVA 0x{rva:X}):")
        print(f"    Expected: {orig.hex()}")
        print(f"    Found:    {actual.hex()} {status}")

    if already_patched:
        print("\nAll four sites already patched. Nothing to do.")
        return 0

    if not all_ok:
        print("\nERROR: Some byte patterns do not match the expected originals.")
        print("The game has likely been updated. Re-dump GameAssembly.dll with")
        print("Il2CppDumper and re-locate the four `mov edx, [reg+disp]` sites")
        print("inside the bundle-load functions. See docs/CRC_PATCH_GUIDE.md.")
        return 1

    # Apply patches
    for name, foff, _orig, new in cfg.CRC_PATCHES:
        dll[foff:foff + len(new)] = new
        print(f"  Applied: {name}")

    dll_path.write_bytes(bytes(dll))
    print(f"\nPatched DLL written: {dll_path}")

    # Verify by disassembling patched code
    print("\n=== Verification ===")
    verify = dll_path.read_bytes()
    for name, foff, _orig, new in cfg.CRC_PATCHES:
        actual = verify[foff:foff + len(new)]
        ok = actual == new
        rva = cfg.file_offset_to_rva(foff, sections)
        print(f"\n{name} (file 0x{foff:X}, RVA 0x{rva:X}):")
        print(f"  Bytes: {actual.hex()} {'OK' if ok else 'FAIL!'}")
        ctx_start = foff - 4
        ctx_end = foff + len(new) + 16
        code = verify[ctx_start:ctx_end]
        addr = image_base + rva - 4
        for insn in md.disasm(code, addr):
            if insn.address >= image_base + rva + len(new) + 10:
                break
            marker = " <-- PATCHED" if insn.address == image_base + rva else ""
            print(f"  0x{insn.address - image_base:X}: {insn.bytes.hex():20s} {insn.mnemonic:8s} {insn.op_str}{marker}")

    print("\n=== Done ===")
    print("All four CRC checks patched to pass 0 (disables Unity CRC validation)")
    print(f"Backup: {bak_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
