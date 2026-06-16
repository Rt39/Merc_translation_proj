# Merc Storia — CRC Patch Guide

Disable the asset-bundle CRC validator in `GameAssembly.dll`. Without this, every other patch in this project silently fails — modified bundles trigger a "data corruption" path that re-downloads the original from the CDN.

Game environment: see [`README.md`](../README.md#game-environment-canonical).

## Why a CRC patch is needed

Unity's Addressables validates every bundle on load against a 32-bit CRC stored in the catalog. Two trigger points:

1. **Cache load** — bundle previously downloaded, being re-read (`AssetBundleRequestOptions.get_Crc`, field offset `0x30`).
2. **Download path** — bundle freshly downloaded (same accessor, different surrounding struct, field offset `0x18`).

CRC mismatch → bundle treated as corrupted → silently re-fetched from CDN (or, with offline mode, the screen fails to render). **No error message, no log line, no on-screen indication** — this was two days of "why doesn't anything I change take effect" before we found it.

## The patch

Replace each `mov edx, [reg+offset]` (which loads the on-disk CRC) with `xor edx, edx`. Unity treats `0` as the documented "skip CRC validation" sentinel and accepts the bundle unconditionally.

Four sites found by grepping `il2cpp_output/dump.cs` for `AssetBundleRequestOptions` and tracing inlined `get_Crc` reads in the bundle-loading and hash-comparison helpers:

| # | RVA | Original bytes | Original disasm | Patched bytes | Patched disasm |
|---|---|---|---|---|---|
| 1 | `0x280ABE8` | `8B 56 30`    | `mov edx, [rsi+0x30]` | `31 D2 90`    | `xor edx, edx; nop` |
| 2 | `0x280C648` | `41 8B 57 18` | `mov edx, [r15+0x18]` | `31 D2 90 90` | `xor edx, edx; nop; nop` |
| 3 | `0x300E040` | `8B D5`       | `mov edx, ebp`        | `31 D2`       | `xor edx, edx` |
| 4 | `0x300EFB0` | `8B 50 18`   | `mov edx, [rax+0x18]` | `31 D2 90`    | `xor edx, edx; nop` |

Sites 1–2 are cache-load and download paths. Sites 3–4 are inside the hash-comparison helper invoked from both. Same length as the original load (with one or two padding `nop`s) → no surrounding-code shifts, no flag side-effects that matter (next instruction is always a `call` or register-overwriting `mov`).

RVAs **will shift across game patches**. Re-run the dumper and re-grep on a future build.

## Apply

```bash
uv run -m mercstoria patch-crc        # idempotent; verifies original bytes before writing
```

`scripts/patch_crc.py`:
1. Backs up to `GameAssembly.dll.bak` on first run (if missing).
2. Reads the live DLL.
3. Verifies the **original** bytes at each offset match the expected pattern — aborts cleanly with "MISMATCH" if not (guards against game updates).
4. Writes the 4 patches.
5. Disassembles a few instructions around each site for visual confirmation.

A second run is a no-op.

## Discovery path (when RVAs shift on a future patch)

1. Dump symbols: `Il2CppDumper.exe GameAssembly.dll global-metadata.dat il2cpp_output/`.
   Tool: [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) — grab the latest release zip and extract next to the game files.
   - `Il2CppDumper/config.json` sets `ForceVersion: 16` — Unity 6000.x ships a metadata version the dumper does not auto-detect.
2. Grep `dump.cs` for `AssetBundleRequestOptions`. `get_Crc` is a stub returning `[this+0x18]` — **not** the patch site; the compiler inlined it.
3. In Ghidra / IDA, load `GameAssembly.dll`, run the dumper's symbol-import script, then x-ref `get_Crc`.
4. The 4 callers are in `UnityEngine.Networking` bundle-loader internals. Look for `mov edx, [<reg>+0x18]` or `mov edx, [<reg>+0x30]` followed shortly by a call into the CRC validator.

## Verification (positive control)

- Corrupt any byte inside `StreamingAssets/aa/StandaloneWindows64/<some-bundle>.bundle`.
- Unpatched DLL: bundle silently re-downloaded; original content shown.
- Patched DLL: corrupted bundle loaded as-is — broken textures or asset-specific crash confirms the bypass works.

Easier in practice: run `mercstoria font-swap`. Without the CRC patch, the font does not change. With it, it does.

## What did NOT work

- **Setting `Crc = 0` in `catalog.bin`** — the catalog is signed at a higher level; the runtime falls back to re-fetching the catalog itself.
- **NOPping the whole `BundleValidator.Validate`** — Unity uses the return value in bundle-state bookkeeping; full NOP causes a null-deref deeper in the loader.
- **BepInEx 6.0.0-pre / MelonLoader (Il2CppInterop)** — both crash on Unity 6000.x at the time of writing. Direct binary patching is the only reliable path.
- **Replacing the bundle in LocalLow only** — StreamingAssets wins; LocalLow is the CDN download cache, not an overlay.

## File reference

| Path | Purpose |
|---|---|
| `scripts/patch_crc.py` | Apply the 4 CRC patches; idempotent (`mercstoria patch-crc`) |
| `scripts/verify_patches.py` | Read-only check (CRC + offline patches) (`mercstoria verify-patches`) |
| `Il2CppDumper/` | Dump symbols (one-time per game build); see [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) |
| `il2cpp_output/dump.cs` | Source of truth for RVAs |
| `GameAssembly.dll.bak` | Auto-created backup before first patch |

## External references

- [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) — extracts dump.cs + symbol map from `GameAssembly.dll` + `global-metadata.dat`
- [Capstone disassembler](https://www.capstone-engine.org/) — used by `scripts/patch_crc.py` to verify each patched site
- [Ghidra](https://github.com/NationalSecurityAgency/ghidra) — recommended for x-ref work on the dumped binary

After this guide, modified asset bundles load without complaint. Continue with [`TEXT_EXTRACTION_GUIDE.md`](TEXT_EXTRACTION_GUIDE.md) or [`FONT_REPLACEMENT_GUIDE.md`](FONT_REPLACEMENT_GUIDE.md).
