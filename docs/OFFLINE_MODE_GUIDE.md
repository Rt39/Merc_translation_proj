# Merc Storia — Offline-Mode Patch Guide

How `scripts/patch_offline.py` (CLI: `mercstoria patch-offline`) makes the game launch and play with the Steam Client closed and the network disconnected. Stacks on top of [`CRC_PATCH_GUIDE.md`](CRC_PATCH_GUIDE.md) — run `mercstoria patch-crc` first.

Game environment: see [`README.md`](../README.md#game-environment-canonical).

## What "offline mode" means

Vanilla game requires two services on launch:

1. **Steam** — `SteamApi_Init` reads user language, builds user-data root, gates features. No Steam Client → refuses to start.
2. **Happy Elements CDN** — every asset bundle not already on disk is downloaded via `AssetBundleHttpClient.GetAsync`. No network → "通信に失敗" modal, most screens never load.

After patching, neither matters. Steam init becomes a no-op, every Steam accessor returns the dev's hardcoded defaults, and every CDN GET is served synchronously from the LocalLow cache via a direct file read inside the patched binary. No proxy. No certificate. No local server.

## The patches

8 sites in `GameAssembly.dll`. RVAs from `il2cpp_output/dump.cs`.

| ID | Method | RVA | What |
|---|---|---|---|
| S1 | `SteamApplication.Initialize` | `0x2828740` | Prologue → `ret`. Never reaches `SteamAPI_Init`. |
| S2 | `SteamApplicationImplementation.Initialize` | `0x28283D0` | Same. |
| S3 | `SteamApplicationImplementation.GetLanguage` | `0x28282C0` | rel32 tail-jump to `Stub.GetLanguage` (`0x28280C0`). |
| S4 | `SteamApplicationImplementation.GetUserDataRootPath` | `0x28282D0` | rel32 tail-jump to `Stub.GetUserDataRootPath` (`0x28280F0`). |
| Y1 | `YetAnotherHttpHandler.get_SkipCertificateVerification` | `0x6BF200` | `mov ax, 0x0101; ret` — `Nullable<bool>(true)`. |
| Y2 | `NativeClientSettings.get_SkipCertificateVerification` | `0x6B1170` | Same. |
| Y3 | `call` site inside `AssetBundleHttpClient.ctor` | `0x27FA4F4` | Re-target `call set_Http2Only` → `call set_SkipCertificateVerification`. 2-byte rel32 nudge (+`0x120`). |
| P  | `AssetBundleHttpClient.<private 5-arg GetAsync>` | `0x27FA120` | 136 bytes of x64 — see below. |

**S1–S4** disable Steam. **Y1–Y3** force the Cysharp/rustls TLS layer to accept any cert. **P is load-bearing** — it short-circuits the network entirely; Y1–Y3 are strictly defense in depth (HttpClient is never invoked once P is in place) but kept because they cost 11 bytes total.

### Why Steam needs only four tiny patches

`dump.cs` grep `SteamApplication` shows four interconnected types in `Toto.Memorial.Client.Core.Extension.Steam`:

```csharp
internal sealed class SteamApplication                            // singleton holder
public  interface ISteamApplicationImplementation
public  sealed class SteamApplicationImplementation               // calls Steamworks.NET
public  sealed class SteamApplicationImplementationStub           // hardcoded defaults
```

The Stub already exists in the shipping binary, its `Initialize()` is a single `ret`, and its accessors return static-field "no Steam" defaults. We make the real `Initialize`s bare `ret`s and tail-jump the real accessors into the stub's already-correct ones. Both Impl and Stub are empty (no instance fields), so passing through `rcx` is safe.

rel32 maths:
- S3: `0x28280C0 - (0x28282C0 + 5) = -0x205 → FB FD FF FF`
- S4: `0x28280F0 - (0x28282D0 + 5) = -0x1E5 → 1B FE FF FF`

### Why the CDN side is a body rewrite

The HTTPS client is `Cysharp.Net.Http.YetAnotherHttpHandler` (YAHH) → `hyper` + `rustls`. rustls trusts the **Mozilla webpki-roots** bundled into the Rust binary, NOT the Windows root store. The real CDN cert is valid (Amazon Trust Services → Amazon Root CA 1) but the chain that arrives at rustls fails with `UnknownIssuer` — likely the user's outbound TUN proxy (FlClash) reshapes the chain. The game retries 3× then either pops a "通信に失敗" modal or sits on a black screen.

We solve at the source: replace the private 5-arg `GetAsync` (the one that owns the async state machine) with code that reads the matching file off disk and returns a synchronously-completed `ValueTask<byte[]>`. The public 2-arg and 4-arg overloads already forward to this 5-arg via existing rel32 calls, so they pick up the patched behaviour for free.

C# equivalent of the patched body:

```csharp
public ValueTask<byte[]> GetAsync(string url, int retryCount, int maxRetry,
                                  ExponentialBackoff backoff, CancellationToken ct)
{
    int q = url.IndexOf('?');
    string pathPart = (q < 0) ? url.Substring(44) : url.Substring(44, q - 44);
    string fullPath = Path.Combine(UnityEngine.Application.persistentDataPath, pathPart);
    byte[] bytes = File.ReadAllBytes(fullPath);
    return new ValueTask<byte[]>(bytes);
}
```

- `44` is the length of `https://assets.mercstoria-memorial.hekk.org/`. The URL path after the prefix is exactly the relative path under the cache root — no `Replace('/','\\')` needed.
- `persistentDataPath` → `%LOCALAPPDATA%/../LocalLow/jp_co_happyelements/メルストM`.
- The synchronously-completed `ValueTask<byte[]>` is built directly in the caller-allocated 24-byte return slot: `_obj=null`, `_result=bytes`, rest zero.

136 bytes of x64. All IL2CPP helper RVAs (`String.IndexOf`, `Substring`, `get_persistentDataPath`, `Path.Combine`, `File.ReadAllBytes`) are listed at the top of `scripts/patch_offline.py`.

### Three traps worth remembering

- **Dead code.** `AssetBundleHttpClient.CreateHttpClient` (static, RVA `0x27F9FB0`) is **never called** — leftover from an earlier design. Patching it does nothing. The HttpClient is built in the instance ctor (RVA `0x27FA420`) which inlines the same logic. *(Multi-hour detour the first time.)*
- **Why P sits at the 5-arg site, not the public overloads.** 2-arg has 80 bytes of slack to its neighbour; 4-arg has 96; the 136-byte body overflows both. 5-arg has 464 bytes available.
- **`Application.dataPath` / `streamingAssetsPath` crash from inside the patched body** (signature: `c0000005`, fault offset ending `01ED`, "execution jumped to address 0x8"). `persistentDataPath` works because Unity itself hits it during boot for save-data setup, so the icall is already resolved by the time GetAsync runs. Don't waste time on the others; `persistentDataPath` is the canonical pick.

### Struct field layout (for reference)

- `Nullable<bool>` = 2 bytes (`HasValue` + `Value`), returned in AX. Y1/Y2 write `mov ax, 0x0101; ret`.
- `ValueTask<byte[]>` = 24 bytes (`_obj`, `_result`, `_token + flags`). When `_obj == null`, treated as synchronously-completed with `_result` as the value.
- `NativeClientSettings.SkipCertificateVerification` at offset `0x32`. YAHH stores its `NativeClientSettings` at `[handler+0x10]`.

## Apply

```bash
uv run -m mercstoria patch-crc
uv run -m mercstoria patch-offline
uv run -m mercstoria verify-patches
```

`scripts/patch_offline.py` is idempotent and self-verifying: backs up to `.bak` on first run, verifies original bytes at every site before writing, aborts cleanly on "MISMATCH" if a game update has shifted RVAs.

## Shipping a self-contained install

Goal: copy the install folder to another machine, run one setup script, double-click the exe. No Steam, no internet.

For that the 15 GB cache must **physically live inside the game folder**. The patches above only redirect the CDN HTTP path. **Unity's Addressables runtime ALSO reads/writes its own cache directly under `persistentDataPath`** (catalog.hash, downloaded bundles, integrity checks) via code paths deep inside Addressables / ResourceManager — not through `AssetBundleHttpClient.GetAsync`. Patching every one of those call sites is brittle and unbounded.

So we redirect at the filesystem layer: the bundled launcher
(`launcher/build/Release/launcher.exe`, drop-in replacement for
`メルストM.exe`) creates an NTFS junction on first run, equivalent to:

```
mklink /J "%USERPROFILE%\AppData\LocalLow\jp_co_happyelements\メルストM\AssetBundle" "<install>\AssetBundle"
```

After that, `persistentDataPath\AssetBundle` is a reparse point pointing at the bundled `<install>\AssetBundle\`. Both the patched HTTP code AND Unity's Addressables runtime land on the bundled cache — neither knows or cares the path traverses a junction. The launcher is idempotent: subsequent launches notice the junction already exists and skip straight to spawning the renamed `メルストM_app.exe`.

Distribution layout:

```
<install>/
  メルストM.exe             (the launcher, drop-in replacement)
  メルストM_app.exe         (original Unity player, renamed by deploy)
  メルストM_app_Data/
  GameAssembly.dll          (patched)
  AssetBundle/              (the bundled 15 GB cache)
    StandaloneWindows64/<Category>/...
  ...
```

User workflow: copy folder anywhere → double-click `メルストM.exe`.

**Diagnostic that motivates the junction.** Without it, procmon trace shows:

```
T+0s    Patched GetAsync reads <game>\AssetBundle\StandaloneWindows64\MasterData\catalog.hash  SUCCESS
T+14s   Unrelated code path: CreateFile <persistent>\AssetBundle\StandaloneWindows64\MasterData\catalog.hash  NAME NOT FOUND
T+14s   Thread Exit cascade across ~80 threads; process terminates cleanly
```

The second call goes through `UnityEngine.AddressableAssets` / `ResourceManager` internals — resolves the cache path from `persistentDataPath` without touching our patch site. The launcher-created junction makes that path mean "the game folder".

(See [`STATE.md`](../STATE.md) for a deeper analysis of why patching Addressables runtime to eliminate the junction step is not worth the effort.)

## End-to-end test plan

1. Apply CRC + offline patches.
2. Disconnect network (WiFi off, or firewall-block the game).
3. Exit Steam Client completely.
4. Launch `メルストM.exe` (the launcher creates the junction on first run if needed, then spawns the real player).
5. Expect: title screen ("Merc StoriA" logo, "Game Start!" button) → Game Start → home menu → bottom tabs (Home / Story / Guild / Gallery / Park) all render with full art and localised labels.
6. `netstat -ano | findstr <pid>` should show zero non-loopback connections.

If a specific bundle is missing from the cache, `File.ReadAllBytes` throws, the async state machine logs `GetAsync: Failed (URL: ...)`, and that screen won't load. Run the game once online with that bundle's screen visited, then disconnect.

## What did NOT work

- **Patching only the public 2-arg / 4-arg overloads** — slot too small; body overflows neighbour. Patch the 5-arg, public overloads forward to it.
- **Returning `default(ValueTask<byte[]>)` (null bytes)** — original `C1`/`C2` approach. Catalog refresh sees null bytes, drops every locator, "通信に失敗" or black screen.
- **Local HTTPS server + FlClash hosts override** — works end-to-end but needs a separate process + self-signed cert.
- **Hosts-file edit + Fiddler reverse proxy** — admin required, Fiddler upstream re-resolves through hosts and loops.
- **WinINet / IE proxy** — YAHH (rustls/hyper) ignores it.
- **FlClash PROCESS-NAME rule routing メルストM.exe to Fiddler** — even with `find-process-mode: always`, rule doesn't catch YAHH connections (opened from a Rust thread with non-standard process attribution).
- **BepInEx / MelonLoader hooking** — both broken on Unity 6000.x.

## File reference

| Path | Purpose |
|---|---|
| `scripts/patch_offline.py` | Apply all 8 offline patches; idempotent (`mercstoria patch-offline`) |
| `scripts/verify_patches.py` | Read-only sanity check (CRC + offline) (`mercstoria verify-patches`) |
| `scripts/bundle_cache.py` | Copy `%LocalLow%/.../AssetBundle` → `<game>/AssetBundle` (bilingual, default zh) (`mercstoria bundle-cache`) |
| `launcher/` | Self-contained launcher that creates the junction on first launch (drop-in `メルストM.exe` replacement) |
| `il2cpp_output/dump.cs` | Truth source for RVAs |

## External references

- [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) — produced the RVAs every patch in this file targets
- [Cysharp/YetAnotherHttpHandler](https://github.com/Cysharp/YetAnotherHttpHandler) — the rustls-backed HTTPS client we short-circuit (Y1/Y2/Y3)
- [hyperium/hyper](https://github.com/hyperium/hyper) + [rustls](https://github.com/rustls/rustls) — underlying transport YAHH wraps
- [Mozilla webpki-roots](https://github.com/rustls/webpki-roots) — the cert bundle that's so out-of-date YAHH can't reach the CDN
- [Unity Addressables](https://docs.unity3d.com/Packages/com.unity.addressables@2.3/manual/index.html) 2.3.7 — the runtime cache layer the patch deliberately does NOT touch (junction handles it)
- [Windows reparse points](https://learn.microsoft.com/en-us/windows/win32/fileio/reparse-points) — junction mechanism the launcher uses to redirect persistent-data reads into the game folder
