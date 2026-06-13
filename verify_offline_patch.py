"""Quick sanity check: confirm the live DLL has BOTH the CRC patches
(from patch_crc3.py) AND the offline-mode patches (from patch_offline.py).

Just re-reads each known patch site and reports what's there.
"""
import sys, io, struct
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

dll_path = r"E:\SteamLibrary\steamapps\common\メルクストーリア - 癒術士と心の旋律 -\GameAssembly.dll"
dll = open(dll_path, 'rb').read()

pe = struct.unpack_from('<I', dll, 0x3C)[0]
ns = struct.unpack_from('<H', dll, pe + 6)[0]
so = pe + 0x18 + struct.unpack_from('<H', dll, pe + 0x14)[0]
sections = [
    (struct.unpack_from('<I', dll, so + i*40 + 12)[0],
     struct.unpack_from('<I', dll, so + i*40 + 8)[0],
     struct.unpack_from('<I', dll, so + i*40 + 20)[0])
    for i in range(ns)
]
def rva2off(rva):
    for va, vs, ro in sections:
        if va <= rva < va + vs:
            return ro + (rva - va)
    return None

# CRC patches (file offsets — these are the live offsets used in patch_crc3.py)
crc_sites = [
    ("CRC site 1 (cache)",         0x280ABE8, bytes.fromhex('31D290')),
    ("CRC site 2 (download)",      0x280C648, bytes.fromhex('31D29090')),
    ("CRC site 3 (hash cmp reg)",  0x300E040, bytes.fromhex('31D2')),
    ("CRC site 4 (hash cmp load)", 0x300EFB0, bytes.fromhex('31D290')),
]

# S1-S4: Steam bypass. Y1-Y3: Cysharp cert skip (defense in depth).
# P: pure file-read GetAsync. We check just the head of each — full bodies are
# longer.
offline_sites = [
    ("S1: SteamApplication.Initialize -> ret",      0x2828740, bytes.fromhex('C3909090')),
    ("S2: Impl.Initialize -> ret",                  0x28283D0, bytes.fromhex('C3909090')),
    ("S3: Impl.GetLanguage -> Stub",                0x28282C0, bytes.fromhex('E9FBFDFFFF')),
    ("S4: Impl.GetUserDataRootPath -> Stub",        0x28282D0, bytes.fromhex('E91BFEFFFF')),
    ("Y1: YAHH.get_SkipCertVerification = true",    0x6BF200,  bytes.fromhex('66B80101C3')),
    ("Y2: NCS.get_SkipCertVerification = true",     0x6B1170,  bytes.fromhex('66B80101C3')),
    ("Y3: ctor call -> set_SkipCertVerification",   0x27FA4F4, bytes.fromhex('E88750ECFD')),
    ("P:  GetAsync(5-arg) -> file-read prologue",   0x27FA120, bytes.fromhex('534883EC4048894C2430498BD8')),
]

print("=== CRC patches (file offsets) ===")
for name, foff, exp in crc_sites:
    got = dll[foff:foff+len(exp)]
    print(f"  {name:35s} foff 0x{foff:08X}  {got.hex():<10s}  {'OK' if got == exp else 'MISSING'}")

print("\n=== Offline patches (RVA -> file) ===")
for name, rva, exp in offline_sites:
    foff = rva2off(rva)
    got = dll[foff:foff+len(exp)]
    ok = 'OK' if got == exp else 'MISSING'
    print(f"  {name:45s} RVA 0x{rva:08X}  {got.hex():<26s}  {ok}")
