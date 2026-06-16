# Merc Storia —— CRC 修补指南

关闭 `GameAssembly.dll` 中的 asset bundle CRC 校验器。如果不做这步，本项目其他所有修补都会静默失败 —— 修改过的 bundle 会触发"数据损坏"路径，从 CDN 重下原版。

游戏环境：见 [`README_zh-CN.md`](README_zh-CN.md#游戏环境基准)（在 docs/ 同目录）。

## 为什么需要 CRC 修补

Unity Addressables 加载每个 bundle 时都会拿目录中存的 32 位 CRC 校验它。有两个触发点：

1. **缓存加载** —— bundle 之前已下载，正被重读（`AssetBundleRequestOptions.get_Crc`，字段偏移 `0x30`）。
2. **下载路径** —— bundle 刚下载完（同一个访问器，外围结构不同，字段偏移 `0x18`）。

CRC 不匹配 → bundle 被当作损坏 → 静默从 CDN 重取（或在离线模式下，那一屏渲染失败）。**没有错误信息、没有日志、屏幕上没有任何提示** —— 这就是为什么找到 CRC 检查之前花了两天的"为什么我改的东西没生效"。

## 修补

把每处加载磁盘 CRC 的 `mov edx, [reg+offset]` 替换成 `xor edx, edx`。Unity 将 `0` 视为已文档化的"跳过 CRC 校验"哨兵值，无条件接受 bundle。

在 `il2cpp_output/dump.cs` 中 grep `AssetBundleRequestOptions`，再追踪 bundle 加载与哈希比对辅助函数中被内联的 `get_Crc` 读取，得到 4 个修补点：

| # | RVA | 原字节 | 原汇编 | 修补后字节 | 修补后汇编 |
|---|---|---|---|---|---|
| 1 | `0x280ABE8` | `8B 56 30`    | `mov edx, [rsi+0x30]` | `31 D2 90`    | `xor edx, edx; nop` |
| 2 | `0x280C648` | `41 8B 57 18` | `mov edx, [r15+0x18]` | `31 D2 90 90` | `xor edx, edx; nop; nop` |
| 3 | `0x300E040` | `8B D5`       | `mov edx, ebp`        | `31 D2`       | `xor edx, edx` |
| 4 | `0x300EFB0` | `8B 50 18`    | `mov edx, [rax+0x18]` | `31 D2 90`    | `xor edx, edx; nop` |

1–2 号是缓存加载和下载路径。3–4 号在哈希比对辅助函数内部，从两边都会被调用。长度与原加载一致（再补一两个 `nop` 对齐）→ 不会移位后续代码；标志位副作用无关紧要（下一条指令始终是 `call` 或覆盖寄存器的 `mov`）。

**RVA 在游戏更新时会变化**。新版本上重新跑 dumper 并重新 grep。

## 应用

```bash
uv run -m mercstoria patch-crc        # 幂等；写入前会校验原字节
```

`scripts/patch_crc.py` 流程：
1. 首次运行（`.bak` 不存在）会先备份到 `GameAssembly.dll.bak`。
2. 读取当前 DLL。
3. 逐处校验**原字节**与预期一致 —— 不一致时以"MISMATCH"清晰退出（防止游戏更新偏移）。
4. 写入 4 处修补。
5. 反汇编每处周围几条指令，便于人眼确认。

再次运行是 no-op。

## 发现路径（游戏更新偏移后照此重做）

1. dump 符号：`Il2CppDumper.exe GameAssembly.dll global-metadata.dat il2cpp_output/`。
   工具：[perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) —— GitHub release 页下载最新 zip，解压到游戏目录旁即可。
   - `Il2CppDumper/config.json` 设了 `ForceVersion: 16` —— Unity 6000.x 的元数据版本 dumper 还没自动识别。
2. 在 `dump.cs` 中 grep `AssetBundleRequestOptions`。`get_Crc` 是返回 `[this+0x18]` 的简单 getter —— **不是**修补点，因为编译器把它内联了。
3. 在 IDA Pro 中加载 `GameAssembly.dll`，跑 dumper 的符号导入脚本，然后交叉引用 `get_Crc`。
4. 4 个调用方都在 `UnityEngine.Networking` 的 bundle loader 内部。找 `mov edx, [<reg>+0x18]` 或 `mov edx, [<reg>+0x30]`，紧接着会调用 CRC 校验函数。

## 验证（正向对照）

- 在 `StreamingAssets/aa/StandaloneWindows64/<某个>.bundle` 内随便改一个字节。
- 未修补 DLL：bundle 被静默重下，看到的还是原版。
- 修补后 DLL：损坏的 bundle 被原样加载 —— 看到贴图损坏或那个 asset 触发崩溃，就证明绕过生效。

实操中更省事：跑一次 `mercstoria font-swap`。CRC 修补前字体不变，修补后字体变了。

## 试过但不行的

- **改 `catalog.bin` 里的 `Crc = 0`** —— 目录在更高层签名，运行时会回去重取目录本身。
- **整段 NOP 掉 `BundleValidator.Validate`** —— Unity 在 bundle 状态记账时会用返回值；全 NOP 会在加载器更深处触发 null 解引用。
- **BepInEx 6.0.0-pre / MelonLoader（Il2CppInterop）** —— 截至撰写时在 Unity 6000.x 上都崩溃。直接二进制修补是唯一可靠路径。
- **只在 LocalLow 替换 bundle** —— StreamingAssets 优先级更高；LocalLow 是 CDN 下载缓存，不是 overlay。

## 文件参考

| 路径 | 用途 |
|---|---|
| `scripts/patch_crc.py` | 应用 4 处 CRC 修补；幂等（`mercstoria patch-crc`） |
| `scripts/verify_patches.py` | 只读检查（CRC + 离线修补）（`mercstoria verify-patches`） |
| `Il2CppDumper/` | dump 符号（每个游戏版本只做一次），见 [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) |
| `il2cpp_output/dump.cs` | RVA 真值来源 |
| `GameAssembly.dll.bak` | 首次修补前自动创建的备份 |

## 外部链接

- [perfare/Il2CppDumper](https://github.com/Perfare/Il2CppDumper) —— 从 `GameAssembly.dll` + `global-metadata.dat` 提取 dump.cs 与符号表
- [Capstone 反汇编引擎](https://www.capstone-engine.org/) —— `scripts/patch_crc.py` 用它验证每处修补
- [IDA Pro](https://hex-rays.com/ida-pro) —— 用来在 dump 出的二进制里做交叉引用

走完本指南后，修改过的 asset bundle 不再被拒绝。继续看 [`TEXT_EXTRACTION_GUIDE.md`](TEXT_EXTRACTION_GUIDE.md) 或 [`FONT_REPLACEMENT_GUIDE.md`](FONT_REPLACEMENT_GUIDE.md)。
