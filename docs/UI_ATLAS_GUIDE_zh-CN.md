# UI 图集指南 —— 以位图烘焙的 UI 文字

游戏里部分 UI 文字并非运行时由 TMP_Text 渲染，而是**直接画进了纹理图集**作为像素数据发布。翻译这些字串意味着改图集像素，并写回到游戏可能加载它们的**每一处**。是 [`FONT_REPLACEMENT_GUIDE_zh-CN.md`](FONT_REPLACEMENT_GUIDE_zh-CN.md)（引擎渲染文字）和 [`STORY_BUNDLE_GUIDE_zh-CN.md`](STORY_BUNDLE_GUIDE_zh-CN.md)（加密文本载荷）的姊妹篇。

游戏环境见 [`README_zh-CN.md`](README_zh-CN.md#游戏环境基准)。CRC 修补（[`CRC_PATCH_GUIDE_zh-CN.md`](CRC_PATCH_GUIDE_zh-CN.md)）必须先打，否则修改后的 bundle 会被静默重下。

## 四个图集

| 图集      | bundle hash                       | 储存位置         | 图集尺寸    | 格式    | sprite 数 |
|-----------|-----------------------------------|------------------|-------------|---------|-----------|
| CommonUI  | `f17951921426b535e20de01adc4f06c3` | StreamingAssets  | 2048×1024   | RGBA32 | 189 |
| GalleryUI | `6e4d5e586bb1bdffd38c58f19f8ba84e` | StreamingAssets  | 1024×2048   | RGBA32 |  62 |
| HomeUI    | `fd6c29755bc7150eb79d2d669abd3f6e` | StreamingAssets  |  256× 256   | RGBA32 |   5 |
| FooterUI  | `6936cdaddf3fa06b26de3570c16593a6` | CDN (BundleAssets) | 512× 512  | DXT5 →   22 cells（SpriteStudio）|

CommonUI / GalleryUI / HomeUI 是 Unity **SpriteAtlas** bundle。FooterUI 是 SpriteStudio 的 `dc_` cellmap（22 个 cell 共用一张 512×512 纹理 `footer_menu_m_512`）；repack 会自动把 DXT5 升级到 RGBA32，避免在译者图上叠加新的编码噪声。

[`scripts/extract_ui_atlas.py`](../scripts/extract_ui_atlas.py) 顶部的 `TARGETS` 列表是名字 / hash / 储存层级的唯一权威。

## 像素藏在哪里（三处，不是一处）

**关键：CommonUI、GalleryUI、HomeUI 在玩家主资源文件里各有一份自包含的副本**。光改 Addressables bundle 不够 —— 游戏会先加载 `sharedassets`，Sprite 直接绑到内嵌图集上，相关画面再也用不到 bundle 那份。

| 图集 | Addressables bundle | sharedassets 副本 |
|---|---|---|
| CommonUI  | `<_Data>/StreamingAssets/aa/StandaloneWindows64/<hash>.bundle` | `<_Data>/sharedassets5.assets`（Texture2D pid 3，SpriteAtlas pid 256）|
| GalleryUI | `<_Data>/StreamingAssets/aa/StandaloneWindows64/<hash>.bundle` | `<_Data>/sharedassets5.assets`（Texture2D pid 4，SpriteAtlas pid 257）|
| HomeUI    | `<_Data>/StreamingAssets/aa/StandaloneWindows64/<hash>.bundle` | `<_Data>/sharedassets7.assets`（Texture2D pid 9，SpriteAtlas pid 20）|
| FooterUI  | `<game>/AssetBundle/StandaloneWindows64/BundleAssets/<hash>.bundle` | *(没有 —— 只走 CDN)* |

sharedassets 里的 `SpriteAtlas` 的 `m_RenderDataMap` 条目指向 `file_id=0`（同文件）的纹理，所以每份副本都是完全自包含的。像素本身在同名的 `.resS` 副边文件里。

`repack-ui-atlas` 一次把**两处**都补上。

## 管线

```
  源 bundle（StreamingAssets/aa 或 BundleAssets）
                       │
                       │ extract-ui-atlas
                       ▼
  extracted_data/ui_atlas/<Atlas>/
      _meta.json            bundle 信息 + 每个 sprite 的矩形 / 旋转 / path_id
      _atlas.png            完整参考图（只读）
      sprites/<name>.png    每个可编辑 sprite / cell 一张 PNG
  extracted_data/.ui_atlas_fingerprints.pkl   每个 PNG 的 hash 基线

         （译者编辑 / 覆盖 sprites/<name>.png）

                       │ repack-ui-atlas
                       ▼
  repacked_bundles/ui_atlas/<hash>.bundle                      修改后的 bundle
  repacked_bundles/ui_atlas_sharedassets/sharedassets*.assets  补好的 <_Data> 副本

                       │ deploy
                       ▼
  <_Data>/StreamingAssets/aa/StandaloneWindows64/<hash>.bundle  ← CommonUI / GalleryUI / HomeUI
  <game>/AssetBundle/StandaloneWindows64/BundleAssets/<hash>.bundle ← FooterUI
  <_Data>/sharedassets{5,7}.assets                              ← 内联好的副本
       （原文件一次性镜像：…_old / .bak）
```

### 命令

```bash
uv run -m mercstoria extract-ui-atlas             # dump 全部 4 个图集
uv run -m mercstoria repack-ui-atlas              # 只重打有改动的图集
uv run -m mercstoria repack-ui-atlas --force      # 全部重打（首次接入时必用）
uv run -m mercstoria deploy                       # 同时投递 bundle 和 sharedassets
```

`repack-ui-atlas` 会跳过所有 `sprites/` PNG 都与 extract 时基线 hash 相同的图集。加 `--force` 强制重打 —— 工具链接入 sharedassets 补丁路径后首次跑必须 `--force`，因为旧基线是在补丁路径不存在时存的。

## extract 做了什么

`SpriteAtlas` bundle：

1. 读 `SpriteAtlas`，从 `m_RenderDataMap` 建 `(guid, fileId) → SpriteAtlasData` 索引。map 里的 `textureRect` 是已打包的图集矩形（坐标系：纹理左下角为原点）；`settingsRaw` 携带旋转 / 格式位。
2. 对每个 `Sprite`，按 `m_RenderDataKey` 查 map。把图集坐标 `(x, y, w, h)` 换成 PIL 左上角原点的裁剪框：`top = atlas_h - (y + h)`。逆向应用 packer 的旋转，让译者看到的图就是屏幕上呈现的方向。
3. 从图集图像里裁出来，存成 `sprites/<safe_name>.png`。裁剪框和旋转写进 `_meta.json`，repack 时按完全相同的像素位置贴回去。

FooterUI（SpriteStudio）的 cellmap 是 `dc_` 开头的 MonoBehaviour，内含 `TableCellMap[].TableCell[].Rectangle`。矩形已经是左上角原点；旋转始终为 0；一个 cellmap 里多个子图集按名字（精确 / 前缀）匹配到对应纹理。

`settingsRaw` 位布局（Unity 2017+）：`bit 0 packed, bit 1 mode, bits 2-5 rotation, bits 6-11 format`。rotation 0–3 自反（翻转 / 180°），extract 和 repack 共用一套；rotation 4（90° 打包）改变 cell 长宽比，extract 阶段拒收。当前 4 个图集 rotation 全是 0。

## repack 做了什么

1. 对每个目标，把 `sprites/` 下 PNG 的 hash 与 `.ui_atlas_fingerprints.pkl` 基线对比；只要有一个 PNG 变过就标记为"已编辑"（`--force` 时全部视作已编辑）。
2. 加载源 bundle，找到 `Texture2D`，建一张 RGBA32 的 `canvas = tex.image.convert("RGBA")` —— 这样未编辑区域保持原样。
3. 对每个已编辑 sprite：打开 PNG，重应用 packer 旋转，若译者画的尺寸跟原矩形不一致就 resize 回去，然后 `canvas.paste(edit_img, (left, top))`。
4. 如果纹理格式属于块压缩 `LOSSY_FORMATS` 集合（DXT/BC/ETC/ASTC/PVRTC + crunched 变体），提升为 RGBA32。代价是该纹理体积约 ×2.5，对 UI 图集来说微不足道，换来译者图无新编码噪声。
5. `tex.set_image(canvas)` + `tex.save()` 把新像素写入资源；`env.file.save(packer="lz4")` 写出 bundle。要跟工具链其它部分对齐用 **`lz4`**，不要 `lz4hc` —— UnityPy 的 lz4hc 实现有 bug，会让游戏拒收。
6. 把所有修改过的 canvas 按 `Texture2D.m_Name` 聚合，调用 `_patch_sharedassets`：
   - 扫 `<_Data>/sharedassets*.assets`，按 `m_Name` 匹配 Texture2D（`sactx-0-<W>x<H>-Uncompressed-<Atlas>-<hashId>` 这套命名）。
   - 命中后用同一份 canvas 调 `tex.set_image` + `tex.save`。这一步会把像素 inline 进 `.assets` 自身、清掉该纹理的 `m_StreamData.offset`。同文件其它纹理保留各自的 `m_StreamData` 指针，继续从未被改动的 `.resS` 读。
   - 把改好的 `.assets` 写到 `repacked_bundles/ui_atlas_sharedassets/<name>.assets`。

匹配是**按纹理名**，不是写死的 path_id —— 只要 `sactx-0-…-<Atlas>-<hashId>` 这个命名约定不变，未来游戏更新即使对象编号变了也不会断。

## deploy

[`scripts/deploy.py`](../scripts/deploy.py) 按 `TARGETS` 里的 `source_dir` 分流：

- `sa`（CommonUI、GalleryUI、HomeUI）→ `<_Data>/StreamingAssets/aa/StandaloneWindows64/`
- `ba`（FooterUI）                     → `<game>/AssetBundle/StandaloneWindows64/BundleAssets/`

每个被替换的 bundle 都会一次性镜像到目录旁的 `…_old/`（SA 是 `StandaloneWindows64_old/`，BA 是 `AssetBundle_old/`）；后续 deploy 永远不会再覆盖这个镜像 —— 第一份保留。

补好的 sharedassets 写到 `<_Data>/sharedassets*.assets`，原文件一次性镜像为同目录的 `<name>.assets.bak`。`.resS` 副边文件**不动**。

### 回滚

```bat
cd "<game>\<APP>_Data"
copy sharedassets5.assets.bak sharedassets5.assets /Y
copy sharedassets7.assets.bak sharedassets7.assets /Y
```

bundle 回滚把对应 `…_old/` 里的文件拷回去即可。

## 加入新图集

1. 把 bundle 名 / hash / `source_dir` / `kind` 加到 [`scripts/extract_ui_atlas.py`](../scripts/extract_ui_atlas.py) 的 `TARGETS`。
2. 跑 `extract-ui-atlas` —— 生成 `_meta.json` 和 `sprites/`。
3. 编辑（或覆盖）对应 `sprites/<name>.png`。
4. `repack-ui-atlas --force`（指纹基线对齐之后可去掉 `--force`）。
5. `deploy`。

如果新图集是 SpriteStudio cellmap，且任意 cell 的 rotation = 4，extract 会跳过这个 cell 并打 warning —— 先扩展 `apply_rotation_*` 再处理。

## 排错

- **游戏里看不到翻译**：bundle 部署成功，但 sharedassets 副本仍是原图。要么 `repack-ui-atlas --force` 再 `deploy`，要么在 `<_Data>/sharedassets*.assets` 里 grep 该图集的 `sactx-…` 纹理名确认副本在哪。
- **repack 报 `source bundle missing`**：源 bundle 是从**实际游戏目录**读的，不是从 `repacked_bundles/`。先跑一次 `deploy` 让实际目录就位，再 extract。
- **bundle 能解码但游戏不肯加载**：检查是否用了 `packer="lz4"`（**不是** `lz4hc`），以及 CRC 修补是否还在（`verify-patches`）。

## 文件路径

```
scripts/extract_ui_atlas.py             extract + repack + sharedassets 补丁
scripts/deploy.py::_deploy_ui_atlas     bundle 路由（sa / ba）
scripts/deploy.py::_deploy_ui_atlas_sharedassets   补好的 .assets → <_Data>/
extracted_data/ui_atlas/<Atlas>/        每个图集的 dump（meta + sprites）
extracted_data/.ui_atlas_fingerprints.pkl   每个 PNG 的 sha256 基线
repacked_bundles/ui_atlas/              修改后的 bundle
repacked_bundles/ui_atlas_sharedassets/ 补好的 sharedassets*.assets
```

## 外部链接

- [Unity SpriteAtlas v2 —— settingsRaw 位布局](https://docs.unity3d.com/Manual/sprite-atlas.html)
- [UnityPy](https://github.com/K0lb3/UnityPy) —— SpriteAtlas / Texture2D / SerializedFile 读写
- [SpriteStudio6 player runtime](https://github.com/SpriteStudio/SpriteStudio6-SDK) —— `dc_` cellmap 结构参考
