"""Apply the complete offline-mode patch set to GameAssembly.dll.

Does NOT depend on a local server, certificate trust, hosts-file edits, or
network access at runtime. Once applied, the game launches with the Steam
Client closed and the network disconnected, reading every CDN bundle
directly from the bundled cache folder inside the game install (next to the
exe).

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
       The actual mechanism. Replaces the async state-machine setup with
       136 bytes of x64 that calls existing IL2CPP methods to read the URL,
       drop the host prefix, look up the matching file under
       Application.persistentDataPath, and return a synchronously-completed
       ValueTask<byte[]> with the file content. The public 2-arg and 4-arg
       overloads call this 5-arg via existing rel32 calls and propagate
       its result.

       For shipping standalone: Setup.cmd in the game folder creates an
       NTFS junction `<persistent>/AssetBundle` -> `<game>/AssetBundle`.
       After running Setup.cmd once, the cache physically lives in the
       game folder; persistent-path access (this patched code AND the
       unpatched Unity Addressables runtime cache layer) transparently
       lands there. Cache is bundled with the install.

CRC patches (4 sites) are applied separately by patch_crc3.py and stack
with these. Run patch_crc3.py first; then this script.
"""
import os, shutil, struct, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DLL_PATH = r"E:\SteamLibrary\steamapps\common\メルクストーリア - 癒術士と心の旋律 -\GameAssembly.dll"
BAK_PATH = DLL_PATH + ".bak"

# -- Steam-side RVAs --
RVA_STEAM_APP_INIT       = 0x2828740
RVA_IMPL_INIT            = 0x28283D0
RVA_IMPL_GETLANG         = 0x28282C0
RVA_IMPL_GETROOT         = 0x28282D0
RVA_STUB_GETLANG         = 0x28280C0
RVA_STUB_GETROOT         = 0x28280F0

# -- Cysharp YAHH (skip cert defense in depth) --
RVA_YAHH_GET_SKIP        = 0x6BF200
RVA_NCS_GET_SKIP         = 0x6B1170
RVA_CTOR_HTTP2ONLY_CALL  = 0x27FA4F4   # site inside AssetBundleHttpClient.ctor

# -- AssetBundleHttpClient.GetAsync (5-arg private) — the pure file-read body --
RVA_GETASYNC_5ARG        = 0x27FA120

# -- IL2CPP helpers the pure-patch body calls --
RVA_INDEXOF_CHAR         = 0x245FBE0   # System.String.IndexOf(char)
RVA_SUBSTRING_1          = 0x2464640   # System.String.Substring(int)
RVA_SUBSTRING_2          = 0x2464650   # System.String.Substring(int, int)
RVA_PATH_COMBINE         = 0x25DBE00   # System.IO.Path.Combine(string, string)
RVA_READ_ALL_BYTES       = 0x25C2BE0   # System.IO.File.ReadAllBytes(string)
RVA_PERSISTENT_PATH      = 0x3131000   # UnityEngine.Application.get_persistentDataPath()

URL_PREFIX_LEN           = 44          # len("https://assets.mercstoria-memorial.hekk.org/")


# ============================================================================
#                            Tiny x64 assembler
# ============================================================================

class Asm:
    def __init__(self, start_rva):
        self.rva = start_rva
        self.buf = bytearray()
        self.labels = {}
        self.short_jumps = []

    def here(self):
        return self.rva + len(self.buf)

    def emit(self, b):
        self.buf.extend(b)

    def label(self, name):
        self.labels[name] = len(self.buf)

    def jmp_short(self, name):
        self.emit(b"\xEB\x00")
        self.short_jumps.append((len(self.buf) - 1, name))

    def js_short(self, name):
        self.emit(b"\x78\x00")
        self.short_jumps.append((len(self.buf) - 1, name))

    def call_rel32(self, target):
        rel = (target - (self.here() + 5)) & 0xFFFFFFFF
        self.emit(b"\xE8" + rel.to_bytes(4, "little"))

    def resolve(self):
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

    NOTE: We use get_persistentDataPath, not get_dataPath or
    get_streamingAssetsPath, because the latter two icalls hard-crash
    when called from inside this patched method ("Faulting module: unknown",
    AV at +0x8) — likely a cold-init issue specific to this IL2CPP build.
    get_persistentDataPath is hit early by Unity itself during boot, so
    its icall is hot by the time we reach this code.

    NOTE on path location: cache files PHYSICALLY live in
    <game>/AssetBundle/StandaloneWindows64/... after Setup.cmd creates an
    NTFS junction at <persistentDataPath>/AssetBundle -> <game>/AssetBundle.
    persistentDataPath access then transparently lands in the game folder
    for BOTH this patched HTTP path AND the unpatched Unity Addressables
    runtime cache code (which also reads/writes catalog.hash and bundle
    files at persistentDataPath subpaths). Procmon confirmed the
    Addressables code path is what crashes the game if cache is moved
    away from persistentDataPath without the junction in place.
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
    a.emit(b"\xBA\x2C\x00\x00\x00")              # mov edx, 44
    a.emit(b"\x41\x89\xC0")                      # mov r8d, eax
    a.emit(b"\x41\x83\xE8\x2C")                  # sub r8d, 44
    a.emit(b"\x4D\x33\xC9")                      # xor r9d, r9d
    a.call_rel32(RVA_SUBSTRING_2)
    a.jmp_short("after_substring")

    a.label("no_query")
    a.emit(b"\x48\x8B\xCB")                      # mov rcx, rbx
    a.emit(b"\xBA\x2C\x00\x00\x00")              # mov edx, 44
    a.emit(b"\x45\x33\xC0")                      # xor r8d, r8d
    a.call_rel32(RVA_SUBSTRING_1)

    a.label("after_substring")
    a.emit(b"\x48\x8B\xD8")                      # mov rbx, rax          ; rbx = pathPart

    # baseDir = Application.persistentDataPath
    #   = <persistentRoot>\jp_co_happyelements\メルストM
    # On a shipped install, persistentRoot\<...>\AssetBundle is an NTFS
    # junction pointing at <game install>\AssetBundle (Setup.cmd creates it
    # on first run). So the bundles physically live in the game folder,
    # and persistent-path access transparently lands there for both this
    # patched HTTP path AND the unpatched Addressables cache code path.
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
#                            PE helpers and patcher
# ============================================================================

def parse_pe(dll):
    pe = struct.unpack_from("<I", dll, 0x3C)[0]
    ns = struct.unpack_from("<H", dll, pe + 6)[0]
    so = pe + 0x18 + struct.unpack_from("<H", dll, pe + 0x14)[0]
    secs = []
    for i in range(ns):
        s = so + i * 40
        secs.append((
            struct.unpack_from("<I", dll, s + 12)[0],
            struct.unpack_from("<I", dll, s + 8)[0],
            struct.unpack_from("<I", dll, s + 20)[0]))
    return secs


def rva_to_off_factory(secs):
    def f(rva):
        for va, vs, ro in secs:
            if va <= rva < va + vs:
                return ro + (rva - va)
        raise ValueError(f"RVA 0x{rva:X} not in any section")
    return f


def build_steam_patches():
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


def apply_byte_patches(dll, rva_to_off, patches):
    for name, rva, old, new in patches:
        foff = rva_to_off(rva)
        cur = bytes(dll[foff:foff + len(old)])
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


def apply_pure_patch(dll, rva_to_off):
    body = build_pure_getasync_body(RVA_GETASYNC_5ARG)
    foff = rva_to_off(RVA_GETASYNC_5ARG)
    cur = bytes(dll[foff:foff + len(body)])
    if cur == body:
        print(f"  [ALREADY] P: pure 5-arg GetAsync body ({len(body)} bytes)")
        return
    dll[foff:foff + len(body)] = body
    print(f"  [APPLIED] P: pure 5-arg GetAsync body ({len(body)} bytes)")


def main():
    if not os.path.exists(DLL_PATH):
        raise SystemExit(f"ERROR: {DLL_PATH} not found")
    if not os.path.exists(BAK_PATH):
        shutil.copy2(DLL_PATH, BAK_PATH)
        print(f"Backed up to {BAK_PATH}")

    with open(DLL_PATH, "rb") as f:
        dll = bytearray(f.read())
    secs = parse_pe(dll)
    rva_to_off = rva_to_off_factory(secs)

    print("Steam bypass (S1-S4):")
    apply_byte_patches(dll, rva_to_off, build_steam_patches())
    print()
    print("Cysharp YAHH cert skip (Y1-Y3, defense in depth):")
    apply_byte_patches(dll, rva_to_off, build_skipcert_patches())
    print()
    print("Pure file-read GetAsync (P):")
    apply_pure_patch(dll, rva_to_off)

    with open(DLL_PATH, "wb") as f:
        f.write(bytes(dll))
    print(f"\nWrote {len(dll):,} bytes to GameAssembly.dll")


if __name__ == "__main__":
    main()
