# Merc Storia (メルストM) — CRC Patch Guide

End-to-end documentation of how the game validates asset bundle CRCs, how to find the four guard sites in `GameAssembly.dll`, and the four-instruction patch that disables them. Without this patch, **every** other thing in this project (font replacement, story repack) silently fails — modified bundles trigger a "data corruption" path that re-downloads the original from the CDN.

## Game environment

- **Engine**: Unity 6000.0.58f2, IL2CPP, Windows x64 (Steam)
- **Game folder**: `<Steam>/steamapps/common/メルクストーリア - 癒術士と心の旋律 -/`
- **Target binary**: `GameAssembly.dll` (the IL2CPP-compiled mono runtime, ~95 MB)
- **Metadata**: `メルストM_Data/il2cpp_data/Metadata/global-metadata.dat`
- **Bundle catalog**: `メルストM_Data/StreamingAssets/aa/catalog.bin` (Addressables 2.3.7)

## Why a CRC patch is needed

Unity's Addressables system validates every bundle on load against a 32-bit CRC stored in the catalog. The check has two trigger points:

1. **Cache load** (LocalLow disk cache, `AssetBundleRequestOptions.get_Crc`): when a bundle was downloaded previously and is being re-read.
2. **Download path** (CDN response): when a bundle is freshly downloaded.

If the CRC mismatches, the bundle is treated as corrupted and the runtime falls back to re-downloading from the CDN. For local modifications this means:

- Modified bundles in `StreamingAssets/aa/StandaloneWindows64/` → silently replaced from CDN.
- Modified bundles in LocalLow cache → silently deleted + re-downloaded.

There is **no error message, no log line, no on-screen indication**. Your patched bundle is simply not used. This was the root cause of two days of "why doesn't anything I change take effect" before we found the CRC checks.

## Anatomy of the CRC check

The IL2CPP runtime calls `UnityEngine.Networking.UnityWebRequestAssetBundle.GetAssetBundle` (or its async variant) and passes a `AssetBundleRequestOptions` struct that carries `Hash` and `Crc` fields. Two call sites read `_options.Crc` (offset `0x30` for the cache path, offset `0x18` after a different struct shape for the download path) and pass it down to the bundle-validation function.

Replacing the `mov edx, [reg+offset]` that loads the CRC into the calling-convention register with `xor edx, edx` makes Unity request "no CRC validation" (the documented `0` sentinel value). The bundle is accepted unconditionally.

We also patch two related sites that load the CRC for hash comparison — without those, certain code paths still call back into the strict validator.

## Step-by-step reproduction

### 1. Dump `GameAssembly.dll` with Il2CppDumper

We need symbol names and RVAs. Use `Il2CppDumper` (bundled under `Il2CppDumper/`):

```bash
cd Il2CppDumper
./Il2CppDumper.exe \
   "<game>/GameAssembly.dll" \
   "<game>/メルストM_Data/il2cpp_data/Metadata/global-metadata.dat" \
   ../il2cpp_output
```

This produces:

- `dump.cs` — every IL2CPP method with its RVA and signature.
- `script.json` — machine-readable mapping `Name → Address` (image base + RVA).
- `il2cpp.h` — C struct layouts (useful later for hooks but not needed here).

The `Il2CppDumper/config.json` we ship is configured with:

```json
{
  "DumpMethod": true, "DumpField": true, "DumpProperty": true,
  "DumpFieldOffset": true, "DumpMethodOffset": true,
  "GenerateDummyDll": true, "GenerateStruct": true,
  "ForceVersion": 16
}
```

`ForceVersion: 16` is needed because Unity 6000.x ships an il2cpp metadata version that Il2CppDumper does not yet auto-detect.

### 2. Locate the CRC accessors

Grep `dump.cs` for `AssetBundleRequestOptions`. You will find:

```csharp
public class AssetBundleRequestOptions
{
    public uint Crc { get; set; }   // get at RVA 0x????, set at RVA 0x????
    public Hash128 Hash { get; set; }
    // ...
}
```

The `get_Crc` getter is a one-instruction stub that returns `[this + 0x18]`. It is **NOT** the patch site. We need the callers.

In Ghidra / IDA, load `GameAssembly.dll` and run the `ghidra.py` / `ida.py` script from `Il2CppDumper/` to apply symbols. Then x-ref `get_Crc`. You will find 4 callers in `UnityEngine.Networking` internals — but they are all inlined! The compiler inlined `[this + 0x18]` as `mov edx, [rsi+0x30]` or `mov edx, [r15+0x18]` directly into the caller. So the actual patch sites are not at `get_Crc` itself but at the inlined reads inside the bundle-loading functions.

The four sites we found (RVAs in `GameAssembly.dll` v1 at time of writing):

| # | RVA | Original | Disassembly |
|---|---|---|---|
| 1 | `0x280ABE8` | `8B 56 30` | `mov edx, dword ptr [rsi + 0x30]` |
| 2 | `0x280C648` | `41 8B 57 18` | `mov edx, dword ptr [r15 + 0x18]` |
| 3 | `0x300E040` | `8B D5` | `mov edx, ebp` (CRC was in ebp from earlier load) |
| 4 | `0x300EFB0` | `8B 50 18` | `mov edx, dword ptr [rax + 0x18]` |

Sites 1–2 are in the cache-load and download paths. Sites 3–4 are in the hash-comparison helper invoked from both. RVAs will shift across game patches; always re-run the dumper and search disassembly for `mov edx, [<reg>+0x18]` followed shortly by a call into the CRC validator.

`patch_crc3.py` in this directory applies all four patches in one pass and is idempotent: re-running it after the patches are already in place is a no-op. Byte patterns are verified before writing, so an outdated DLL aborts cleanly with a "re-dump symbols" hint.

### 3. The patch

For each site, replace the CRC load with `xor edx, edx` (and `nop` if the original is longer):

| # | Original bytes | Patched bytes | Patched disasm |
|---|---|---|---|
| 1 | `8B 56 30` | `31 D2 90` | `xor edx, edx; nop` |
| 2 | `41 8B 57 18` | `31 D2 90 90` | `xor edx, edx; nop; nop` |
| 3 | `8B D5` | `31 D2` | `xor edx, edx` |
| 4 | `8B 50 18` | `31 D2 90` | `xor edx, edx; nop` |

Why `xor edx, edx`? It is exactly the same length as `mov edx, [reg+disp]` for the 3- and 4-byte variants (with one `nop` for alignment), preserves all surrounding registers, and zeros `edx` — which is the documented Unity sentinel for "skip CRC validation". No flag side-effects matter because the next instruction in every case is either a `call` or a register move that overwrites `eflags` usage.

### 4. Apply the patch — `patch_crc3.py`

The script:

1. Reads the original DLL from `GameAssembly.dll.bak` (creates it on first run if missing).
2. Walks the PE section table to convert each RVA to a file offset.
3. Verifies the **original** bytes at each offset match the expected pattern (refuses to patch if not — guards against game updates shifting offsets).
4. Writes the patched bytes.
5. Re-reads the patched DLL and disassembles a few instructions around each site to confirm.

Sample output:

```
Read original from backup (95231488 bytes)

=== Verifying original bytes ===
  Cache CRC load (RVA 0x280C1E8, offset 0x280B5E8):
    Expected: 8b5630
    Found:    8b5630 OK
  Download CRC load (RVA 0x280DC48, offset 0x280D048):
    Expected: 418b5718
    Found:    418b5718 OK
  Applied: Cache CRC load
  Applied: Download CRC load
Patched DLL written: E:\...\GameAssembly.dll

=== Verification ===
Cache CRC load (RVA 0x280C1E8):
  Bytes: 31d290 OK
  0x280C1E4: 4c8d1d3d3a3f00       lea       r11, [rip + 0x3f3a3d]
  0x280C1E8: 31d2                 xor       edx, edx  <-- PATCHED
  0x280C1EA: 90                   nop
  ...
```

Backups are kept in `GameAssembly.dll.bak`. Restore by `copy GameAssembly.dll.bak GameAssembly.dll`.

## Verification (positive control)

1. Patch the DLL.
2. Modify any byte inside `StreamingAssets/aa/StandaloneWindows64/<some-bundle>.bundle` (e.g. flip the first byte of an LZ4 block to corrupt a real bundle — choose one whose content you can visually verify).
3. Launch the game.
4. With **unpatched** DLL: the bundle is silently re-downloaded from CDN; the original content is shown.
5. With **patched** DLL: the corrupted bundle is loaded as-is. You will see broken textures / crash on that specific asset — confirming the CRC check is bypassed and your modification is reaching Unity.

A safer positive control: use the font-swap pipeline (see `FONT_REPLACEMENT_GUIDE.md`) and check whether the font changes. Without the CRC patch, the font does not change. With it, the font changes.

## What we tried that did NOT work

- **Setting `Crc = 0` in `catalog.bin`** — the catalog is signed/hashed at a higher level; the runtime falls back to re-fetching the catalog itself.
- **NOPping the entire `BundleValidator.Validate` function** — Unity uses the return value as part of bundle-state bookkeeping; full NOP causes a null-deref deeper in the loader.
- **Hooking via BepInEx / MelonLoader** — both are broken on Unity 6000.x at the time of writing (BepInEx 6.0.0-preX can't generate IL2CPP proxies; MelonLoader's Il2CppInterop crashes in `Class_FromIl2CppType_Hook` with `AccessViolationException`). Direct binary patching is the only reliable path.
- **Replacing the bundle in LocalLow only** — the StreamingAssets bundle wins; LocalLow is the CDN download cache, not an overlay.

## File reference

| Path | Purpose |
|---|---|
| `Il2CppDumper/Il2CppDumper.exe` | Dump IL2CPP symbols + RVAs |
| `Il2CppDumper/config.json` | Dumper config (forces metadata version 16) |
| `il2cpp_output/dump.cs` | Human-readable IL2CPP class dump |
| `il2cpp_output/script.json` | Machine-readable `Name → Address` map |
| `patch_crc3.py` | Apply the 4 (currently 2) CRC patches to `GameAssembly.dll` |
| `GameAssembly.dll.bak` | Auto-created backup before first patch |

## Step-by-step from scratch

```bash
# 1. Backup
cp "<game>/GameAssembly.dll" "<game>/GameAssembly.dll.bak"

# 2. Dump IL2CPP symbols
cd Il2CppDumper
./Il2CppDumper.exe \
    "<game>/GameAssembly.dll" \
    "<game>/メルストM_Data/il2cpp_data/Metadata/global-metadata.dat" \
    ../il2cpp_output

# 3. Identify CRC sites
#    - open il2cpp_output/dump.cs, search for AssetBundleRequestOptions
#    - in Ghidra/IDA, load GameAssembly.dll + run the dumper's script
#    - search for `mov edx, [<reg>+0x18]` and `mov edx, [<reg>+0x30]`
#      inside any function whose name contains "Bundle"/"AssetBundle"/"DownloadHandlerAssetBundle"

# 4. Patch (uses capstone, already in pyproject.toml)
uv run patch_crc3.py
```

After this, modified asset bundles load without complaint. Move on to `TEXT_EXTRACTION_GUIDE.md` or `FONT_REPLACEMENT_GUIDE.md`.
