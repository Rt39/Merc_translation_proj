"""Apply both CRC patches to GameAssembly.dll."""
import sys, io, struct, os, shutil
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from capstone import *

dll_path = r"E:\SteamLibrary\steamapps\common\メルクストーリア - 癒術士と心の旋律 -\GameAssembly.dll"
backup_path = dll_path + ".bak"

with open(backup_path, 'rb') as f:
    dll = bytearray(f.read())
print(f"Read original from backup ({len(dll)} bytes)")

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

def rva_to_offset(rva):
    for va, vs, ro, rs in sections:
        if va <= rva < va + vs:
            return rva - va + ro
    raise ValueError(f"RVA 0x{rva:X} not in any section")

md = Cs(CS_ARCH_X86, CS_MODE_64)
md.detail = True

# Patch 1: Cache path - RVA 0x280C1E8
# Original: 8B 56 30           mov edx, dword ptr [rsi + 0x30]   (3 bytes)
# Patched:  31 D2 90           xor edx, edx; nop                 (3 bytes)
p1_rva = 0x280C1E8
p1_off = rva_to_offset(p1_rva)
p1_orig = bytes([0x8B, 0x56, 0x30])
p1_new  = bytes([0x31, 0xD2, 0x90])

# Patch 2: Download path - RVA 0x280DC48
# Original: 41 8B 57 18        mov edx, dword ptr [r15 + 0x18]   (4 bytes)
# Patched:  31 D2 90 90        xor edx, edx; nop; nop            (4 bytes)
p2_rva = 0x280DC48
p2_off = rva_to_offset(p2_rva)
p2_orig = bytes([0x41, 0x8B, 0x57, 0x18])
p2_new  = bytes([0x31, 0xD2, 0x90, 0x90])

patches = [
    ("Cache CRC load",    p1_rva, p1_off, p1_orig, p1_new),
    ("Download CRC load", p2_rva, p2_off, p2_orig, p2_new),
]

# Verify originals
print("\n=== Verifying original bytes ===")
all_ok = True
for name, rva, foff, orig, new in patches:
    actual = bytes(dll[foff:foff + len(orig)])
    ok = actual == orig
    if not ok:
        all_ok = False
    print(f"  {name} (RVA 0x{rva:X}, offset 0x{foff:X}):")
    print(f"    Expected: {orig.hex()}")
    print(f"    Found:    {actual.hex()} {'OK' if ok else 'MISMATCH!'}")

if not all_ok:
    print("\nERROR: Some patches don't match. Aborting.")
    sys.exit(1)

# Also patch the store that saves CRC to local var after patch 2
# 0x280DC4C: 89 53 28  mov [rbx+0x28], edx  -- this stores the CRC we just zeroed, harmless
# But we should also NOP it or leave it (storing 0 is fine)

# Apply patches
patched = bytearray(dll)
for name, rva, foff, orig, new in patches:
    patched[foff:foff + len(new)] = new
    print(f"  Applied: {name}")

with open(dll_path, 'wb') as f:
    f.write(patched)
print(f"\nPatched DLL written: {dll_path}")

# Verify by disassembling patched code
print("\n=== Verification ===")
with open(dll_path, 'rb') as f:
    verify = f.read()

for name, rva, foff, orig, new in patches:
    actual = verify[foff:foff + len(new)]
    ok = actual == new
    print(f"\n{name} (RVA 0x{rva:X}):")
    print(f"  Bytes: {actual.hex()} {'OK' if ok else 'FAIL!'}")

    # Disassemble context
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
print("Both CRC checks patched to pass 0 (disables Unity CRC validation)")
print("Backup: " + backup_path)
