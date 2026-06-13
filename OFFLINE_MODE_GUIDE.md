# Merc Storia (メルストM) — Offline-Mode Patch Guide

End-to-end documentation of how `patch_offline.py` makes the game launch and
play with the Steam Client closed and the network disconnected.

The patches in this guide stack on top of the bundle-CRC patches in
[`CRC_PATCH_GUIDE.md`](CRC_PATCH_GUIDE.md). Run `patch_crc3.py` first; then
`patch_offline.py`.

## Game environment

- Engine: Unity 6000.0.58f2, IL2CPP, Windows x64 (Steam)
- Game folder: `<Steam>/steamapps/common/メルクストーリア - 癒術士と心の旋律 -/`
- Target binary: `GameAssembly.dll` (~78 MB, IL2CPP-compiled managed runtime)
- Steam runtime: `メルストM_Data/Plugins/x86_64/steam_api64.dll` (left in place; we only stop the game from *calling* into it)
- CDN that the game tries to reach: `https://assets.mercstoria-memorial.hekk.org/AssetBundle/StandaloneWindows64/<Category>/<file>`
- Bundled cache the patched binary reads from: `<game>/メルストM_Data/StreamingAssets/AssetBundle/StandaloneWindows64/<Category>/<file>` (every CDN bundle was moved here from LocalLow so the install is self-contained)
- Player log: `%LOCALAPPDATA%/../LocalLow/jp.co.happyelements/メルストM/Player.log` *(note the dots vs underscores — Unity has two profile-name conventions)*

## What "offline mode" means here

The vanilla game expects two services on launch:

1. **Steam.** Initialized via `SteamApi_Init` to read user language, build the
   user-data root path, and gate some features.
2. **The Happy Elements CDN.** Every asset bundle that isn't already on disk
   is downloaded through `AssetBundleHttpClient.GetAsync`.

Both are *required* in vanilla. Without a Steam Client the game refuses to
start. Without network, the per-category catalog refresh fails, a
"通信に失敗" modal pops up, and most screens never load.

After this patch, neither matters. Steam init becomes a no-op, every Steam
accessor returns the dev's hardcoded defaults, and every CDN GET is served
synchronously from the bundled StreamingAssets folder via a direct file read in the patched
binary. No proxy. No certificate. No local server.

## The patches

| ID | Method (RVA in `il2cpp_output/dump.cs`) | What it does |
|---|---|---|
| S1 | `SteamApplication.Initialize` (`0x2828740`) | Replace prologue with `ret` — never reaches `SteamAPI_Init`. |
| S2 | `SteamApplicationImplementation.Initialize` (`0x28283D0`) | Same. |
| S3 | `SteamApplicationImplementation.GetLanguage` (`0x28282C0`) | rel32 tail-jump to the existing `Stub.GetLanguage` (`0x28280C0`). |
| S4 | `SteamApplicationImplementation.GetUserDataRootPath` (`0x28282D0`) | rel32 tail-jump to `Stub.GetUserDataRootPath` (`0x28280F0`). |
| Y1 | `YetAnotherHttpHandler.get_SkipCertificateVerification` (`0x6BF200`) | `mov ax, 0x0101 ; ret` — Nullable<bool>(true). |
| Y2 | `NativeClientSettings.get_SkipCertificateVerification` (`0x6B1170`) | Same. |
| Y3 | One `call` site inside `AssetBundleHttpClient.ctor` (`0x27FA4F4`) | Re-target the call from `set_Http2Only` (RVA `0x6BF460`) to `set_SkipCertificateVerification` (RVA `0x6BF580`) — 2-byte rel32 nudge. |
| P  | `AssetBundleHttpClient.<private 5-arg GetAsync>` (`0x27FA120`) | Overwrite with 136 bytes of x64 that read the URL, drop the host prefix, look up the matching file under `Application.streamingAssetsPath`, and return a synchronously-completed `ValueTask<byte[]>` with the file content. |

S1–S4 disable Steam. Y1–Y3 force the Cysharp/rustls TLS layer to accept any
certificate. **P is the load-bearing patch** — it short-circuits the
network entirely, so Y1–Y3 are strictly defense in depth (the HTTP client
is never invoked once P is in place). They're kept because they cost 11
bytes total.

### Why the Steam side is four tiny patches

Grep `dump.cs` for `SteamApplication`. You'll find four interconnected types
in `Toto.Memorial.Client.Core.Extension.Steam`:

```csharp
internal sealed class SteamApplication                            // singleton holder
public  interface ISteamApplicationImplementation                 // abstraction
public  sealed class SteamApplicationImplementation : ISteam...   // calls Steamworks.NET
public  sealed class SteamApplicationImplementationStub : ISteam… // hardcoded defaults
```

The `Stub` exists in the shipping binary already. Its `Initialize()` is a
single `ret`. Its accessors return static-field strings — the dev's chosen
"no Steam" defaults. So we don't have to invent anything: we make the real
`Initialize` methods bare `ret`s, and we tail-jump the real accessors into
the stub's already-correct ones. Both `Impl` and `Stub` are empty
(no instance fields), so passing through `rcx` is safe.

rel32 maths for S3/S4 (sanity-check at home):

- S3: `0x28280C0 - (0x28282C0 + 5) = -0x205 → FB FD FF FF`
- S4: `0x28280F0 - (0x28282D0 + 5) = -0x1E5 → 1B FE FF FF`

### Why the CDN side is a body rewrite, not a stub

The game's HTTPS client is `Cysharp.Net.Http.YetAnotherHttpHandler` (YAHH),
which uses `hyper` + `rustls` (Rust) under the hood. rustls trusts the
Mozilla webpki-roots bundled into the Rust binary, *not* the Windows root
store. The actual CDN cert is valid (Amazon Trust Services → Amazon Root
CA 1, both publicly trusted), but the chain that arrives at rustls fails
with `UnknownIssuer` — most likely because the user's outbound TUN proxy
(FlClash, in our case) reshapes the chain before delivery.

So even with the network reachable and the cert nominally valid, the game
threw `client error (Connect): invalid peer certificate: UnknownIssuer`
once for each catalog hash, gave up after three retries, and either showed
a "通信に失敗" popup or sat on a black screen.

We solve this at the source: replace `AssetBundleHttpClient`'s private
5-arg GetAsync (the one that owns the async state machine) with code that
reads the matching file off disk and returns a synchronously-completed
`ValueTask<byte[]>`. The public 2-arg and 4-arg overloads already forward
to this 5-arg via existing rel32 calls, so they need no patching.

The pure-patch body in C#:

```csharp
public ValueTask<byte[]> GetAsync(string url, int retryCount, int maxRetry,
                                  ExponentialBackoff backoff, CancellationToken ct)
{
    int q = url.IndexOf('?');
    string pathPart = (q < 0)
        ? url.Substring(44)
        : url.Substring(44, q - 44);
    string fullPath = Path.Combine(
        UnityEngine.Application.streamingAssetsPath,
        pathPart);
    byte[] bytes = File.ReadAllBytes(fullPath);
    return new ValueTask<byte[]>(bytes);
}
```

- `44` is the length of the prefix `https://assets.mercstoria-memorial.hekk.org/`.
  The URL path after the prefix is *exactly* the relative path under the
  bundled-cache root, so we don't even need a `Replace('/','\\')`.
- `Application.streamingAssetsPath` resolves to `<gameInstall>/メルストM_Data/StreamingAssets`,
  which is Unity's standard "ship raw files with the build" location. Putting
  the 15 GB CDN cache here makes the install fully self-contained — no LocalLow
  dependency, no per-user state to migrate.
- The synchronously-completed `ValueTask<byte[]>` is built directly in the
  caller-allocated 24-byte return slot: `_obj=null`, `_result=bytes`,
  `_token=_flags=0`.

136 bytes of x64 assembly. All the IL2CPP helper RVAs are listed at the
top of `patch_offline.py`.

#### The dead-code trap

`AssetBundleHttpClient.CreateHttpClient` (static, RVA `0x27F9FB0`) **is
never called**. It's a leftover from an earlier design. The HttpClient
that the game actually uses is built in the instance constructor (RVA
`0x27FA420`), which inlines the same logic. Patching `CreateHttpClient`
does nothing — patch the ctor's call site instead. (This was a
multi-hour detour; spelling it out so you don't repeat it.)

#### Why we put P at the 5-arg site, not the 2-arg/4-arg site

The 2-arg public overload starts at RVA `0x27FA0D0`. The next method
(the private 5-arg) starts at `0x27FA120` — that's only 80 bytes
available. The 4-arg has only 96 bytes. Our 136-byte body fits at the
5-arg site (where the next neighbour, `0x27FA2F0`, is 464 bytes away)
but not at either public site. So we put the body at the 5-arg
and the public overloads' original rel32 calls into 5-arg pick up
the patched behaviour.

### Field offsets of `Nullable<bool>` and `ValueTask<TResult>`

The compiled IL2CPP layout for the records we touch:

- `Nullable<bool>` (`HasValue` byte + `Value` byte): 2 bytes; returned in
  AX. Y1/Y2 write `mov ax, 0x0101; ret`.
- `ValueTask<byte[]>`: 24 bytes (`_obj` ptr, `_result` ptr, `_token` short +
  `_continueOnCapturedContext` byte + padding). When `_obj == null`,
  the ValueTask is treated as synchronously-completed with `_result` as
  the value. So we write `_result = bytes` and zero the rest.
- `NativeClientSettings.SkipCertificateVerification` field: offset
  `0x32` in the instance. YAHH stores its `NativeClientSettings` at
  `[handler+0x10]`.

## Applying the patches

`patch_offline.py` is idempotent and self-verifying:

1. Refuses to run if `GameAssembly.dll` is missing.
2. Backs up the current DLL to `.bak` if no backup exists (preserving any
   CRC patches already applied).
3. Reads the **live** DLL, applies S1–S4, Y1–Y3, and P in place, and
   writes back.
4. Each S/Y site verifies the original prologue bytes before writing —
   if the game has been updated and an RVA has shifted, it aborts with a
   clear "MISMATCH" message instead of corrupting the binary.

Recommended order from a clean install:

```powershell
uv run patch_crc3.py
uv run patch_offline.py
uv run verify_offline_patch.py   # sanity check
```

A second run is a no-op:

```
$ uv run patch_offline.py
Steam bypass (S1-S4):
  [ALREADY] S1: SteamApplication.Initialize -> ret
  ...
Cysharp YAHH cert skip (Y1-Y3, defense in depth):
  [ALREADY] Y1: YAHH.get_SkipCertificateVerification -> 0x0101
  ...
Pure file-read GetAsync (P):
  [ALREADY] P: pure 5-arg GetAsync body (136 bytes)
```

## How we found the sites

1. Run `Il2CppDumper` exactly as in `CRC_PATCH_GUIDE.md`. The output
   `il2cpp_output/dump.cs` is the truth source for every RVA.
2. The RVAs in `patch_offline.py` came from grepping `dump.cs` for the
   relevant class declarations and reading off the `// RVA: 0x...`
   comments.
3. For the pure-patch body, the IL2CPP helper RVAs (string ops, `Path.Combine`,
   `File.ReadAllBytes`, `Application.streamingAssetsPath`) likewise came
   straight from `dump.cs`.

## End-to-end test plan

1. Apply CRC + offline patches.
2. **Disconnect from network** (turn off WiFi, or block the game in firewall).
3. **Exit Steam Client completely** (Steam → Exit).
4. Launch `メルストM.exe` directly (not via Steam).
5. Expect: title screen renders; pressing **Game Start** brings up the home
   menu; tapping any of the bottom tabs (Home / Story / Guild / Gallery /
   Park) brings up the corresponding screen with full art and localised
   labels.
6. Run `netstat -ano | findstr メルストM`'s PID and confirm the game has
   zero non-loopback established connections.

If any specific bundle is missing from the bundled StreamingAssets folder, `File.ReadAllBytes`
throws `FileNotFoundException`, the surrounding async state machine logs
"GetAsync: Failed (URL: ...)" in Player.log, and the screen that needed
that bundle won't load. Re-running the game once with the network on so the
CDN fills in the missing bundle, then disconnecting, fixes it.

## What we tried and abandoned

- **Patching just the public 2-arg and 4-arg overloads.** The 2-arg slot is
  only 80 bytes — our 136-byte body overflows into the private 5-arg's
  prologue and corrupts it. Patching the private 5-arg is the right move;
  the public overloads forward to it.
- **Returning `default(ValueTask<byte[]>)` (i.e. null bytes) from GetAsync.**
  This was the original `C1`/`C2` patch in the previous iteration of this
  guide. It bypasses the network but the catalog refresh code then sees
  null bytes from every category, drops every locator, and the orchestrator
  surfaces "通信に失敗" or sits on a black screen. The pure file-read
  approach above doesn't have this problem.
- **Running a local HTTPS server with FlClash hosts override.** This
  worked end-to-end but required a separate Python process and a
  self-signed cert (auto-generated, never installed by the user, but
  still extra moving pieces). The pure-patch obsoletes it.
- **Hosts-file edit + Fiddler reverse proxy.** Requires admin, and the
  Fiddler upstream re-resolves the hostname through hosts and loops.
- **WinINet/IE proxy.** Unity/IL2CPP's `System.Net.Http.HttpClient` is
  implemented via YAHH (rustls/hyper), not WinHttp. It ignores IE proxy.
- **FlClash PROCESS-NAME rule routing the game's process to Fiddler.**
  Even with `find-process-mode: always` enabled in the profile, the rule
  doesn't catch YAHH's connections — probably because they're opened
  from a Rust thread whose process-attribution path differs from the
  standard Win32 one.
- **Hooking with BepInEx/MelonLoader.** Both are broken on Unity 6000.x
  at the time of writing.

## File reference

| Path | Purpose |
|---|---|
| `patch_offline.py` | Apply all eight offline-mode patches; idempotent. |
| `verify_offline_patch.py` | Read-only sanity check on live DLL (CRC + offline). |
| `il2cpp_output/dump.cs` | Source of truth for RVAs of each target method. |
| `OFFLINE_MODE_GUIDE.md` | This file. |
