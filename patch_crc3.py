"""Apply all four CRC patches to GameAssembly.dll.

Disables Unity Addressables bundle CRC validation at all four sites so that
modified .bundle files load without being silently replaced from the CDN.

The four sites all load the bundle CRC into edx right before calling into the
validator. Replacing each load with `xor edx, edx` makes Unity request the
documented "skip CRC validation" path. See CRC_PATCH_GUIDE.md for the
discovery / disassembly story.

Sites 1-2: cache-load and download paths (UnityWebRequestAssetBundle inlining).
Sites 3-4: hash-comparison helpers invoked from both paths.

Offsets are FILE offsets into GameAssembly.dll. Byte patterns are verified
before patching, so the script aborts cleanly if a game update has shifted
anything. RVAs are recovered from the PE section table only for the
disassembly display.
"""
import sys, io, struct, os, shutil
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

dll_path = r"E:\SteamLibrary\steamapps\common\メルクストーリア - 癒術士と心の旋律 -\GameAssembly.dll"
backup_path = dll_path + ".bak"

if not os.path.exists(backup_path):
    if not os.path.exists(dll_path):
        print(f"ERROR: {dll_path} does not exist. Is the game installed?")
        sys.exit(1)
    shutil.copy2(dll_path, backup_path)
    print(f"Created backup: {backup_path}")

with open(backup_path, 'rb') as f:
    dll = bytearray(f.read())
print(f"Read original from backup ({len(dll)} bytes)")

# Parse PE header just for image_base + section table (used by disassembly display).
pe_offset = struct.unpack_from('<I', dll, 0x3C)[0]
image_base = struct.unpack_from('<Q', dll, pe_offset + 0x30)[0]
num_sections = struct.unpack_from('<H', dll, pe_offset + 6)[0]
section_offset = pe_offset + 0x18 + struct.unpack_from('<H', dll, pe_offset + 0x14)[0]
sections = []
for i in range(num_sections):
    s = section_offset + i * 40
    vaddr = struct.unpack_from('<I', dll, s + 12)[0]
    vsize = struct.unpack_from('<I', dll, s + 8)[0]
    roff = struct.unpack_from('<I', dll, s + 20)[0]
    rsize = struct.unpack_from('<I', dll, s + 16)[0]
    sections.append((vaddr, vsize, roff, rsize))


def offset_to_rva(foff):
    for va, vs, ro, rs in sections:
        if ro <= foff < ro + rs:
            return foff - ro + va
    return foff  # fall back, only used for display

md = Cs(CS_ARCH_X86, CS_MODE_64)
md.detail = True

# Each patch entry: (name, file_offset, original_bytes, patched_bytes)
#
# Site 1 — cache CRC load (UnityWebRequestAssetBundle cache path)
#   mov edx, [rsi+0x30]   →   xor edx, edx; nop
# Site 2 — download CRC load (UnityWebRequestAssetBundle download path)
#   mov edx, [r15+0x18]   →   xor edx, edx; nop; nop
# Site 3 — hash comparison helper (CRC moved from ebp earlier)
#   mov edx, ebp          →   xor edx, edx
# Site 4 — secondary hash-compare entry
#   mov edx, [rax+0x18]   →   xor edx, edx; nop
patches = [
    ("Site 1 (cache CRC load)",          0x280ABE8, bytes.fromhex('8B5630'),   bytes.fromhex('31D290')),
    ("Site 2 (download CRC load)",       0x280C648, bytes.fromhex('418B5718'), bytes.fromhex('31D29090')),
    ("Site 3 (hash compare CRC reg)",    0x300E040, bytes.fromhex('8BD5'),     bytes.fromhex('31D2')),
    ("Site 4 (hash compare CRC load)",   0x300EFB0, bytes.fromhex('8B5018'),   bytes.fromhex('31D290')),
]

# Verify originals
print("\n=== Verifying original bytes ===")
all_ok = True
already_patched = True
for name, foff, orig, new in patches:
    actual = bytes(dll[foff:foff + len(orig)])
    ok = actual == orig
    is_already = actual == new
    if not ok and not is_already:
        all_ok = False
    if not is_already:
        already_patched = False
    rva = offset_to_rva(foff)
    status = 'OK' if ok else ('ALREADY PATCHED' if is_already else 'MISMATCH!')
    print(f"  {name} (file 0x{foff:X}, RVA 0x{rva:X}):")
    print(f"    Expected: {orig.hex()}")
    print(f"    Found:    {actual.hex()} {status}")

if already_patched:
    print("\nAll four sites already patched. Nothing to do.")
    sys.exit(0)

if not all_ok:
    print("\nERROR: Some byte patterns do not match the expected originals.")
    print("The game has likely been updated. Re-dump GameAssembly.dll with")
    print("Il2CppDumper and re-locate the four `mov edx, [reg+disp]` sites")
    print("inside the bundle-load functions. See CRC_PATCH_GUIDE.md.")
    sys.exit(1)

# Apply patches
patched = bytearray(dll)
for name, foff, orig, new in patches:
    patched[foff:foff + len(new)] = new
    print(f"  Applied: {name}")

with open(dll_path, 'wb') as f:
    f.write(patched)
print(f"\nPatched DLL written: {dll_path}")

# Verify by disassembling patched code
print("\n=== Verification ===")
with open(dll_path, 'rb') as f:
    verify = f.read()

for name, foff, orig, new in patches:
    actual = verify[foff:foff + len(new)]
    ok = actual == new
    rva = offset_to_rva(foff)
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
print("Backup: " + backup_path)
