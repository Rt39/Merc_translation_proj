"""Apply the complete offline-mode patch set to GameAssembly.dll.

Does NOT depend on a local server, certificate trust, hosts-file edits, or
network access at runtime. Once applied, the game launches with the Steam
Client closed and the network disconnected, reading every CDN bundle
directly from the bundled cache folder inside the game install.

Patches applied (idempotent — re-running is a no-op):

  S1: SteamApplication.Initialize                              -> ret early
  S2: SteamApplicationImplementation.Initialize                -> ret early
  S3: SteamApplicationImplementation.GetLanguage               -> jmp Stub.GetLanguage
  S4: SteamApplicationImplementation.GetUserDataRootPath       -> jmp Stub.GetUserDataRootPath
       Together: SteamAPI_Init never runs and every Steam accessor
       returns the dev's hardcoded "no Steam" defaults.

  Y1: YetAnotherHttpHandler.get_SkipCertificateVerification    -> mov ax,0x101; ret
  Y2: NativeClientSettings.get_SkipCertificateVerification     -> mov ax,0x101; ret
  Y3: AssetBundleHttpClient.ctor `call set_Http2Only`          -> `call set_SkipCertificateVerification`
       Defense in depth: forces YAHH's rustls layer to skip TLS cert
       verification. Strictly redundant with P below (the HTTP client is
       never invoked once P is in place) but cheap.

  P:  AssetBundleHttpClient.<private 5-arg GetAsync>           -> read from disk
       136 bytes of x64 that calls existing IL2CPP methods to read the URL,
       drop the host prefix, look up the matching file under
       Application.persistentDataPath, and return a synchronously-completed
       ValueTask<byte[]> with the file content.

       For shipping standalone: the launcher creates an NTFS junction
       `<persistent>/AssetBundle` -> `<game>/AssetBundle` on first launch.
       Cache physically lives in the game folder; persistent-path access
       transparently lands there for both this patched HTTP path AND the
       unpatched Unity Addressables runtime cache layer.

CRC patches are applied separately by patch_crc.py and stack with these. Run
patch_crc.py first, then this script.
"""
import sys, shutil

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercstoria import config as cfg
from mercstoria.config import (
    RVA_STEAM_APP_INIT, RVA_IMPL_INIT, RVA_IMPL_GETLANG, RVA_IMPL_GETROOT,
    RVA_STUB_GETLANG, RVA_STUB_GETROOT,
    RVA_YAHH_GET_SKIP, RVA_NCS_GET_SKIP, RVA_CTOR_HTTP2ONLY_CALL,
    RVA_GETASYNC_5ARG,
    RVA_INDEXOF_CHAR, RVA_SUBSTRING_1, RVA_SUBSTRING_2,
    RVA_PATH_COMBINE, RVA_READ_ALL_BYTES, RVA_PERSISTENT_PATH,
    CDN_PREFIX_LEN,
)

cfg.enable_utf8_stdout()


# ============================================================================
#                            Tiny x64 assembler
# ============================================================================

class Asm:
    """Single-pass x64 assembler just expressive enough for the GetAsync body.

    Tracks the load-address (`rva`) so `call_rel32(target_rva)` can compute
    its rel32 displacement relative to the next instruction. Forward `jmp`
    and `js` to a `label("name")` are emitted as 2-byte short-jumps with a
    placeholder displacement and back-patched in `resolve()`.

    The instruction set is intentionally minimal — the patched GetAsync
    body is the only customer. Anything more involved goes back to
    hand-rolling raw bytes via `emit()`.
    """

    def __init__(self, start_rva):
        self.rva = start_rva
        self.buf = bytearray()
        self.labels = {}
        self.short_jumps = []

    def here(self):
        """RVA of the next byte that would be emitted."""
        return self.rva + len(self.buf)

    def emit(self, b):
        """Append raw machine code (anything not covered by the helpers)."""
        self.buf.extend(b)

    def label(self, name):
        """Mark the current position as a jump target for short-jumps."""
        self.labels[name] = len(self.buf)

    def jmp_short(self, name):
        """Emit `jmp rel8` to `name`. The displacement is filled in by resolve()."""
        self.emit(b"\xEB\x00")
        self.short_jumps.append((len(self.buf) - 1, name))

    def js_short(self, name):
        """Emit `js rel8` to `name`. Displacement filled in by resolve()."""
        self.emit(b"\x78\x00")
        self.short_jumps.append((len(self.buf) - 1, name))

    def call_rel32(self, target):
        """Emit `call <rel32>` reaching the absolute RVA `target`."""
        rel = (target - (self.here() + 5)) & 0xFFFFFFFF
        self.emit(b"\xE8" + rel.to_bytes(4, "little"))

    def resolve(self):
        """Patch every short-jump displacement and return the finished buffer.

        Asserts each displacement fits in a signed byte. If you blow that,
        the answer is to convert the offending jump to a near-jump (5 bytes)
        rather than relax the assertion.
        """
        for off, name in self.short_jumps:
            rel = self.labels[name] - (off + 1)
            assert -128 <= rel < 128, f"short jump out of range to {name}: {rel}"
            self.buf[off] = rel & 0xFF
        return bytes(self.buf)


def build_pure_getasync_body(start_rva):
    """Assemble the 5-arg GetAsync replacement.

    Win64 entry: rcx = retslot (24-byte ValueTask<byte[]>),
                 rdx = this, r8 = url, r9 = retryCount, ...

    Behavior (C# equivalent):
        int q = url.IndexOf('?');
        string p = (q < 0) ? url.Substring(44) : url.Substring(44, q - 44);
        string full = Path.Combine(Application.persistentDataPath, p);
        byte[] b = File.ReadAllBytes(full);
        return new ValueTask<byte[]>(b);

    NOTE: We use get_persistentDataPath, not get_dataPath / get_streamingAssetsPath,
    because the latter two icalls hard-crash when called from inside this
    patched method (AV at +0x8) — cold-init issue specific to this IL2CPP
    build. persistentDataPath is hit early by Unity itself during boot, so
    its icall is hot by the time we reach this code.
    """
    a = Asm(start_rva)
    a.emit(b"\x53")                              # push rbx
    a.emit(b"\x48\x83\xEC\x40")                  # sub rsp, 0x40
    a.emit(b"\x48\x89\x4C\x24\x30")              # mov [rsp+0x30], rcx   ; retslot
    a.emit(b"\x49\x8B\xD8")                      # mov rbx, r8           ; rbx = url

    # int q = url.IndexOf('?')
    a.emit(b"\x48\x8B\xCB")                      # mov rcx, rbx
    a.emit(b"\xBA\x3F\x00\x00\x00")              # mov edx, '?'
    a.emit(b"\x45\x33\xC0")                      # xor r8d, r8d          ; MethodInfo*
    a.call_rel32(RVA_INDEXOF_CHAR)

    a.emit(b"\x48\x85\xC0")                      # test rax, rax
    a.js_short("no_query")

    # q >= 0: pathPart = url.Substring(44, q - 44)
    a.emit(b"\x48\x8B\xCB")                      # mov rcx, rbx
    a.emit(bytes([0xBA]) + CDN_PREFIX_LEN.to_bytes(4, "little"))  # mov edx, CDN_PREFIX_LEN
    a.emit(b"\x41\x89\xC0")                      # mov r8d, eax
    a.emit(bytes([0x41, 0x83, 0xE8, CDN_PREFIX_LEN]))             # sub r8d, CDN_PREFIX_LEN
    a.emit(b"\x4D\x33\xC9")                      # xor r9d, r9d
    a.call_rel32(RVA_SUBSTRING_2)
    a.jmp_short("after_substring")

    a.label("no_query")
    a.emit(b"\x48\x8B\xCB")                      # mov rcx, rbx
    a.emit(bytes([0xBA]) + CDN_PREFIX_LEN.to_bytes(4, "little"))  # mov edx, CDN_PREFIX_LEN
    a.emit(b"\x45\x33\xC0")                      # xor r8d, r8d
    a.call_rel32(RVA_SUBSTRING_1)

    a.label("after_substring")
    a.emit(b"\x48\x8B\xD8")                      # mov rbx, rax          ; rbx = pathPart

    # baseDir = Application.persistentDataPath
    a.emit(b"\x33\xC9")                          # xor ecx, ecx          ; MethodInfo*
    a.call_rel32(RVA_PERSISTENT_PATH)

    # fullPath = Path.Combine(baseDir, pathPart)
    a.emit(b"\x48\x8B\xC8")                      # mov rcx, rax
    a.emit(b"\x48\x8B\xD3")                      # mov rdx, rbx
    a.emit(b"\x45\x33\xC0")                      # xor r8d, r8d
    a.call_rel32(RVA_PATH_COMBINE)

    # bytes = File.ReadAllBytes(fullPath)
    a.emit(b"\x48\x8B\xC8")                      # mov rcx, rax
    a.emit(b"\x33\xD2")                          # xor edx, edx
    a.call_rel32(RVA_READ_ALL_BYTES)

    # retslot = new ValueTask<byte[]>(bytes) — _obj=null, _result=bytes, _token=_flags=0
    a.emit(b"\x48\x8B\x4C\x24\x30")              # mov rcx, [rsp+0x30]
    a.emit(b"\x33\xD2")                          # xor edx, edx
    a.emit(b"\x48\x89\x11")                      # mov [rcx], rdx        ; _obj
    a.emit(b"\x48\x89\x41\x08")                  # mov [rcx+8], rax      ; _result
    a.emit(b"\x48\x89\x51\x10")                  # mov [rcx+0x10], rdx   ; _token+_flags
    a.emit(b"\x48\x8B\xC1")                      # mov rax, rcx
    a.emit(b"\x48\x83\xC4\x40")                  # add rsp, 0x40
    a.emit(b"\x5B")                              # pop rbx
    a.emit(b"\xC3")                              # ret
    return a.resolve()


# ============================================================================
#                                Patcher
# ============================================================================

def build_steam_patches():
    """Return the 4 Steam-bypass byte patches as (name, RVA, old, new) tuples.

    `Initialize` stubs (S1, S2) get a single-byte ret (0xC3) followed by NOPs.
    `GetLanguage` / `GetUserDataRootPath` (S3, S4) get rewritten to a 5-byte
    near-jmp into Stub.<same name> — the dev's offline-default implementation
    that ships in every IL2CPP build.
    """
    rel_lang = (RVA_STUB_GETLANG - (RVA_IMPL_GETLANG + 5)) & 0xFFFFFFFF
    rel_root = (RVA_STUB_GETROOT - (RVA_IMPL_GETROOT + 5)) & 0xFFFFFFFF
    return [
        ("S1: SteamApplication.Initialize -> ret",
         RVA_STEAM_APP_INIT,
         bytes.fromhex("4883EC28"),
         bytes.fromhex("C3909090")),
        ("S2: Impl.Initialize -> ret",
         RVA_IMPL_INIT,
         bytes.fromhex("4883EC28"),
         bytes.fromhex("C3909090")),
        ("S3: Impl.GetLanguage -> jmp Stub.GetLanguage",
         RVA_IMPL_GETLANG,
         bytes.fromhex("33C9E9F909E5FD"),
         b"\xE9" + rel_lang.to_bytes(4, "little") + b"\x90\x90"),
        ("S4: Impl.GetUserDataRootPath -> jmp Stub.GetUserDataRootPath",
         RVA_IMPL_GETROOT,
         bytes.fromhex("48895C24085748" + "83EC20"),
         b"\xE9" + rel_root.to_bytes(4, "little") + b"\x90\x90\x90\x90\x90"),
    ]


def build_skipcert_patches():
    """Return the 3 Cysharp YAHH cert-skip patches.

    Y1/Y2 force both getter callsites to return `true` (0x0101 — the .NET
    convention for `Boolean` in `ax` is one byte set to 1). Y3 swaps the
    HTTP/2-only setter callsite for the SkipCertificateVerification setter,
    which causes the constructor to flip the cert-skip flag instead of the
    HTTP/2 flag during initialisation. Strictly defence-in-depth since the
    pure file-read GetAsync (`P`) never hits the HTTP layer.
    """
    patch_ax_true = bytes.fromhex("66B80101C3")  # mov ax, 0x0101 ; ret
    return [
        ("Y1: YAHH.get_SkipCertificateVerification -> 0x0101",
         RVA_YAHH_GET_SKIP,
         bytes.fromhex("4883EC28488B41104885C074090FB74032"),
         patch_ax_true),
        ("Y2: NativeClientSettings.get_SkipCertificateVerification -> 0x0101",
         RVA_NCS_GET_SKIP,
         bytes.fromhex("0FB74132C3"),
         patch_ax_true),
        ("Y3: ctor set_Http2Only call -> set_SkipCertificateVerification",
         RVA_CTOR_HTTP2ONLY_CALL,
         bytes.fromhex("E8674FECFD"),
         bytes.fromhex("E88750ECFD")),
    ]


def apply_byte_patches(dll, sections, patches):
    """Apply each (name, RVA, old, new) patch in `patches` to `dll` in place.

    Refuses to patch if the bytes at `RVA` don't match either `old` (apply)
    or `new` (already applied) — a mismatch means the binary has changed
    (likely a game update) and the RVAs need re-deriving.
    """
    for name, rva, old, new in patches:
        foff = cfg.rva_to_file_offset(rva, sections)
        if foff is None:
            raise SystemExit(f"  [ERROR] RVA 0x{rva:X} ({name}) not in any section")
        cur  = bytes(dll[foff:foff + len(old)])
        head = bytes(dll[foff:foff + len(new)])
        if head == new:
            print(f"  [ALREADY] {name}")
            continue
        if cur != old:
            raise SystemExit(
                f"  [MISMATCH] {name}\n"
                f"    expected: {old.hex()}\n"
                f"    actual:   {cur.hex()}\n"
                f"  (game has been updated — re-dump symbols and re-derive RVAs)")
        dll[foff:foff + len(new)] = new
        print(f"  [APPLIED] {name}")


def apply_pure_patch(dll, sections):
    """Drop the 136-byte file-read GetAsync body at its RVA. Idempotent."""
    body = build_pure_getasync_body(RVA_GETASYNC_5ARG)
    foff = cfg.rva_to_file_offset(RVA_GETASYNC_5ARG, sections)
    cur = bytes(dll[foff:foff + len(body)])
    if cur == body:
        print(f"  [ALREADY] P: pure 5-arg GetAsync body ({len(body)} bytes)")
        return
    dll[foff:foff + len(body)] = body
    print(f"  [APPLIED] P: pure 5-arg GetAsync body ({len(body)} bytes)")


def main() -> int:
    """Apply all 8 patches to the live DLL, leaving a .bak of the pristine
    binary the first time through. Returns 0 on success."""
    dll_path = cfg.dll_path()
    bak_path = cfg.dll_backup_path()
    if not dll_path.exists():
        raise SystemExit(f"ERROR: {dll_path} not found")
    if not bak_path.exists():
        shutil.copy2(dll_path, bak_path)
        print(f"Backed up to {bak_path}")

    dll = bytearray(dll_path.read_bytes())
    _, sections = cfg.parse_pe_sections(bytes(dll))

    print("Steam bypass (S1-S4):")
    apply_byte_patches(dll, sections, build_steam_patches())
    print()
    print("Cysharp YAHH cert skip (Y1-Y3, defense in depth):")
    apply_byte_patches(dll, sections, build_skipcert_patches())
    print()
    print("Pure file-read GetAsync (P):")
    apply_pure_patch(dll, sections)

    dll_path.write_bytes(bytes(dll))
    print(f"\nWrote {len(dll):,} bytes to GameAssembly.dll")
    return 0


if __name__ == "__main__":
    sys.exit(main())
