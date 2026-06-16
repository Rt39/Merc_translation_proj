# Merc Storia —— 离线模式修补指南

`scripts/patch_offline.py`（CLI：`mercstoria patch-offline`）如何让游戏在 Steam 客户端关闭、网络断开的情况下也能启动并正常玩。本指南叠加在 [`CRC_PATCH_GUIDE_zh-CN.md`](CRC_PATCH_GUIDE_zh-CN.md) 之上 —— 先跑 `mercstoria patch-crc`。

游戏环境：见 [`README_zh-CN.md`](README_zh-CN.md#游戏环境基准)（在 docs/ 同目录）。

## "离线模式"指什么

原版游戏启动时需要两个服务：

1. **Steam** —— `SteamApi_Init` 读取用户语言、构造用户数据根路径、控制部分功能。无 Steam 客户端 → 拒绝启动。
2. **Happy Elements CDN** —— 磁盘上没有的 asset bundle 都通过 `AssetBundleHttpClient.GetAsync` 下载。无网 → "通信に失敗" 弹窗，多数画面无法加载。

修补后两个都不需要了。Steam init 变成 no-op，所有 Steam 访问器返回开发者预设的默认值，每个 CDN GET 都被修补后的二进制以纯文件读取从 LocalLow 缓存里同步返回。无代理。无证书。无本地服务器。

## 修补点

`GameAssembly.dll` 中共 8 处。RVA 来自 `il2cpp_output/dump.cs`。

| ID | 方法 | RVA | 改了什么 |
|---|---|---|---|
| S1 | `SteamApplication.Initialize` | `0x2828740` | 序言 → `ret`。永远到不了 `SteamAPI_Init`。 |
| S2 | `SteamApplicationImplementation.Initialize` | `0x28283D0` | 同上。 |
| S3 | `SteamApplicationImplementation.GetLanguage` | `0x28282C0` | rel32 尾跳到 `Stub.GetLanguage`（`0x28280C0`）。 |
| S4 | `SteamApplicationImplementation.GetUserDataRootPath` | `0x28282D0` | rel32 尾跳到 `Stub.GetUserDataRootPath`（`0x28280F0`）。 |
| Y1 | `YetAnotherHttpHandler.get_SkipCertificateVerification` | `0x6BF200` | `mov ax, 0x0101; ret` —— `Nullable<bool>(true)`。 |
| Y2 | `NativeClientSettings.get_SkipCertificateVerification` | `0x6B1170` | 同上。 |
| Y3 | `AssetBundleHttpClient.ctor` 内的 `call` 点 | `0x27FA4F4` | `call set_Http2Only` → `call set_SkipCertificateVerification`。2 字节 rel32 微调（+`0x120`）。 |
| P  | `AssetBundleHttpClient.<私有 5 参 GetAsync>` | `0x27FA120` | 136 字节 x64 —— 见下文。 |

**S1–S4** 关闭 Steam。**Y1–Y3** 强制 Cysharp/rustls TLS 层接受任何证书。**P 是关键修补** —— 它把网络整体短路；P 之后 HttpClient 根本不会被调用，Y1–Y3 严格意义上是防御性冗余，但一共才 11 字节就保留下来。

### Steam 端为什么只需要 4 处小修补

`dump.cs` 中 grep `SteamApplication`，可以在 `Toto.Memorial.Client.Core.Extension.Steam` 命名空间下找到 4 个相互关联的类型：

```csharp
internal sealed class SteamApplication                            // 单例持有者
public  interface ISteamApplicationImplementation
public  sealed class SteamApplicationImplementation               // 调 Steamworks.NET
public  sealed class SteamApplicationImplementationStub           // 硬编码默认值
```

Stub 在发布版二进制里就已经存在，它的 `Initialize()` 是一个 `ret`，访问器都返回 static field 中"无 Steam"默认值。我们把真正的 `Initialize` 改成裸 `ret`，把真访问器尾跳到 stub 已经正确的版本上。Impl 和 Stub 都没有实例字段，所以 `rcx` 透传是安全的。

rel32 数学：
- S3：`0x28280C0 - (0x28282C0 + 5) = -0x205 → FB FD FF FF`
- S4：`0x28280F0 - (0x28282D0 + 5) = -0x1E5 → 1B FE FF FF`

### CDN 端为什么要重写函数体

HTTPS 客户端是 `Cysharp.Net.Http.YetAnotherHttpHandler`（YAHH）→ `hyper` + `rustls`。rustls 信任的是 Rust 二进制内嵌的 **Mozilla webpki-roots**，**不是** Windows 根证书库。真正的 CDN 证书是有效的（Amazon Trust Services → Amazon Root CA 1），但到 rustls 这里的证书链报 `UnknownIssuer` —— 很可能是用户的出站 TUN 代理（FlClash）改写了证书链。游戏重试 3 次，然后要么弹"通信に失敗"，要么停在黑屏。

我们从源头解决：把私有 5 参 `GetAsync`（拥有 async state machine 的那一个）重写成读磁盘并返回同步完成的 `ValueTask<byte[]>`。公开的 2 参和 4 参重载已经通过现有的 rel32 转发到这个 5 参，所以它们会"免费"享受修补效果。

修补函数体的 C# 等价物：

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

- `44` 是 `https://assets.mercstoria-memorial.hekk.org/` 的长度。前缀后的 URL 路径正好是缓存根目录下的相对路径 —— 连 `Replace('/','\\')` 都不需要。
- `persistentDataPath` → `%LOCALAPPDATA%/../LocalLow/jp_co_happyelements/メルストM`。
- 同步完成的 `ValueTask<byte[]>` 直接构造在调用方分配的 24 字节返回槽里：`_obj=null`、`_result=bytes`、其余清零。

136 字节 x64。所有 IL2CPP 辅助 RVA（`String.IndexOf`、`Substring`、`get_persistentDataPath`、`Path.Combine`、`File.ReadAllBytes`）都列在 `scripts/patch_offline.py` 顶部。

### 三个值得记住的陷阱

- **死代码。** `AssetBundleHttpClient.CreateHttpClient`（静态，RVA `0x27F9FB0`）**从来没被调用过** —— 旧设计的残留。改它没用。真正用的 HttpClient 在实例构造器里（RVA `0x27FA420`）就地内联。*（第一次踩这个坑花了好几个小时。）*
- **P 为什么放在 5 参点而不是公开重载点。** 2 参到邻居的间距只有 80 字节；4 参 96 字节；136 字节的函数体两个都装不下。5 参那里有 464 字节余量。
- **修补函数体里调 `Application.dataPath` / `streamingAssetsPath` 会硬崩**（特征：`c0000005`，错误偏移 `01ED` 结尾，"execution jumped to address 0x8"）。`persistentDataPath` 没事，是因为 Unity 自己在启动初期就为存档系统命中它，等 GetAsync 跑到时 icall 已经解析完。别在这上面浪费时间 —— `persistentDataPath` 是唯一可选项。

### 结构体字段布局（备查）

- `Nullable<bool>` = 2 字节（`HasValue` + `Value`），通过 AX 返回。Y1/Y2 写 `mov ax, 0x0101; ret`。
- `ValueTask<byte[]>` = 24 字节（`_obj`、`_result`、`_token + flags`）。当 `_obj == null` 时被视作同步完成，`_result` 即结果值。
- `NativeClientSettings.SkipCertificateVerification` 在偏移 `0x32`。YAHH 把它的 `NativeClientSettings` 存在 `[handler+0x10]`。

## 应用

```bash
uv run -m mercstoria patch-crc
uv run -m mercstoria patch-offline
uv run -m mercstoria verify-patches
```

`scripts/patch_offline.py` 幂等且自校验：首次会备份到 `.bak`，写入前逐点校验原字节，游戏更新导致 RVA 偏移会以"MISMATCH"清晰退出。

## 自包含安装的打包方案

目标：把安装目录拷到另一台机器，跑一次 setup 脚本，双击 exe 即可玩。无 Steam，无网。

为此 15 GB 缓存必须**实际存在游戏目录内**。上面的修补只重定向了 CDN HTTP 路径。**Unity Addressables 运行时同样会直接在 `persistentDataPath` 下读写自己的缓存**（catalog.hash、下载的 bundle、完整性校验），这些代码路径深埋在 Addressables / ResourceManager 内部，**不**经过 `AssetBundleHttpClient.GetAsync`。把这些位点一个一个修补又脆又没尽头。

所以我们在文件系统层做重定向：自包含启动器
（`launcher/build/Release/launcher.exe`，作为 `メルストM.exe` 的替身）首次
运行时创建 NTFS junction，等价于：

```
mklink /J "%USERPROFILE%\AppData\LocalLow\jp_co_happyelements\メルストM\AssetBundle" "<install>\AssetBundle"
```

之后，`persistentDataPath\AssetBundle` 就是一个 reparse point，指向打包的 `<install>\AssetBundle\`。游戏自己的代码和 Unity 的 Addressables 运行时都会落到打包的缓存上 —— 它们都不知道路径上有 junction，也不在乎。启动器幂等：之后启动会发现 junction 已存在，直接转发到改名后的 `メルストM_app.exe`。

分发目录结构：

```
<install>/
  メルストM.exe             （启动器，作为替身）
  メルストM_app.exe         （改名后的原 Unity player）
  メルストM_app_Data/
  GameAssembly.dll          （已修补）
  AssetBundle/              （打包的 15 GB 缓存）
    StandaloneWindows64/<Category>/...
  ...
```

用户工作流：把文件夹拷到任何位置 → 双击 `メルストM.exe`。

**支撑 junction 决定的诊断。** 不加 junction，procmon 抓到：

```
T+0s    修补后的 GetAsync 读 <game>\AssetBundle\StandaloneWindows64\MasterData\catalog.hash  SUCCESS
T+14s   不相关的代码路径：CreateFile <persistent>\AssetBundle\StandaloneWindows64\MasterData\catalog.hash  NAME NOT FOUND
T+14s   约 80 个线程级联 Thread Exit；进程干净退出
```

第二个调用走的是 `UnityEngine.AddressableAssets` / `ResourceManager` 内部 —— 它从 `persistentDataPath` 解析缓存路径，根本不会经过我们的修补点。启动器创建的 junction 让那个路径"意味着游戏目录"。

（[`../STATE.md`](../STATE.md) 中有为什么修补 Addressables 运行时以消除 junction 步骤不值当的更详细分析。）

## 端到端测试方案

1. 应用 CRC + 离线修补。
2. 断网（关 WiFi 或防火墙阻断游戏）。
3. 完全退出 Steam 客户端。
4. 启动 `メルストM.exe`（启动器首次运行时按需创建 junction，然后转发到真正的 player）。
5. 预期：标题画面（"Merc StoriA" logo，"Game Start!" 按钮）→ Game Start → 主页菜单 → 底部 5 个 tab（Home / Story / Guild / Gallery / Park）都能渲染，立绘和本地化标签齐全。
6. `netstat -ano | findstr <pid>` 应显示零个非环回连接。

如果缓存中缺某个 bundle，`File.ReadAllBytes` 会抛异常，async state machine 会记 `GetAsync: Failed (URL: ...)`，那一屏加载失败。联网状态下进一次缺失 bundle 对应的画面让 CDN 补齐，再断网即可。

## 试过但不行的

- **只改公开的 2 参 / 4 参重载** —— 槽位太小，函数体溢出邻居。改 5 参，公开重载会转发过来。
- **`GetAsync` 返回 `default(ValueTask<byte[]>)`（即 null 字节）** —— 上一版的 `C1`/`C2` 方案。目录刷新拿到 null 字节，把每个 locator 都丢掉，"通信に失敗"或黑屏。
- **本地 HTTPS 服务器 + FlClash hosts 重定向** —— 端到端能跑，但需要额外进程和自签证书。
- **hosts 文件修改 + Fiddler 反向代理** —— 要管理员；Fiddler 上游又会通过 hosts 解析造成环。
- **WinINet / IE 代理** —— YAHH（rustls/hyper）不理它。
- **FlClash PROCESS-NAME 规则把 メルストM.exe 路由到 Fiddler** —— 即使设置 `find-process-mode: always`，规则也抓不到 YAHH 的连接（在 Rust 线程中开启，进程归属路径不标准）。
- **BepInEx / MelonLoader hook** —— 在 Unity 6000.x 上都坏了。

## 文件参考

| 路径 | 用途 |
|---|---|
| `scripts/patch_offline.py` | 应用全部 8 处离线修补；幂等（`mercstoria patch-offline`） |
| `scripts/verify_patches.py` | 只读校验（CRC + 离线）（`mercstoria verify-patches`） |
| `scripts/bundle_cache.py` | 把 `%LocalLow%/.../AssetBundle` 拷到 `<game>/AssetBundle`（双语提示，默认中文）（`mercstoria bundle-cache`） |
| `launcher/` | 自包含启动器（`メルストM.exe` 的替身），首次启动时创建 junction |
| `il2cpp_output/dump.cs` | RVA 真值来源 |

## 外部链接

- [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) —— 提供本文中每个修补点的 RVA
- [Cysharp/YetAnotherHttpHandler](https://github.com/Cysharp/YetAnotherHttpHandler) —— 我们要短路的 rustls HTTPS 客户端（Y1/Y2/Y3）
- [hyperium/hyper](https://github.com/hyperium/hyper) + [rustls](https://github.com/rustls/rustls) —— YAHH 包装的底层传输
- [Mozilla webpki-roots](https://github.com/rustls/webpki-roots) —— rustls 内嵌的证书集合（版本太老导致连不上 CDN）
- [Unity Addressables](https://docs.unity3d.com/Packages/com.unity.addressables@2.3/manual/index.html) 2.3.7 —— 我们刻意不去碰的运行时缓存层（交给 junction 处理）
- [Windows reparse point](https://learn.microsoft.com/en-us/windows/win32/fileio/reparse-points) —— 启动器用来把持久化目录读取重定向到游戏目录的机制
