# global-metadata 与内置场景 UI 修补指南

`uv run -m mercstoria patch-metadata` 负责一批不属于以下管线的小 UI 字符串：

- 加密剧情文本；
- MasterData JSON；
- Addressables 的 `ui_labels`；
- 位图烘焙 UI 图集。

这些字符串主要藏在两处：

1. `global-metadata.dat` —— IL2CPP enum 名 / string literal。
2. `<APP>_Data/level5`、`level10`、`level11` —— Unity 内置场景序列化字符串。

实现文件是 [`../scripts/patch_metadata.py`](../scripts/patch_metadata.py)。

## 当前修补内容

### 国家名

国家名是 metadata 字符串池里的 IL2CPP enum 字段名：

- `Country`
- `CountryFilter`

运行时显示近似为：

```csharp
Enum.GetName(Country, id) + "の国"
```

所以脚本修补：

- `COUNTRY_NAMES` 里的国家名；
- `COUNTRY_SUFFIX`（`の国` → `之国`）。

短译文原地写入并用 `\0` 补齐。超过原槽位的国家 enum 字段名会追加到
metadata `StringData` 末尾，并重定向对应 `FieldDefinition.nameIndex`。

### metadata StringLiteral

部分 UI 标签是 IL2CPP string literal，位于 metadata section 1
(`StringLiteralData`)，由 section 0 (`StringLiteral`) 表项引用。脚本会改字节，
同时把对应 StringLiteral 表项的 length 改成译文字节长度，避免 UI 显示尾部空格。

当前条目：

| 日文 | 中文 | 备注 |
|---|---|---|
| `ユニット一覧` | `角色列表` | Unit 在非 `Unit Story` 场景统一译为“角色”。 |
| `ユニット総数` | `角色总数` | 角色总数标签。 |
| `すべてのイベント` | `全部活动` | 活动筛选项。 |
| `絞り込み` | `筛选` | 筛选按钮 / 标题。 |

### metadata StringData

部分筛选标签是 metadata section 2 (`StringData`) 里的 enum 字段名或辅助标签。
这些是 null-terminated 字符串；短译文原地写入并用 `\0` 补齐。

当前条目：

| 日文 | 中文 | 备注 |
|---|---|---|
| `幻憶` | `幻忆` | `EventStoryFilter`。 |
| `コラボ` | `联动` | `EventStoryFilter`。 |
| `第一部オリジン` | `第一部原版` | Story filter/category 标签。 |

不要修补 `イベント幻憶` / `イベントコラボ`：这两个曾经测试过，后来保留原文，
实际可见 UI 只需要较短的筛选名。

### 内置场景字符串

一些菜单标题和故事分类 tab 文字直接序列化在 Unity 内置场景文件里，不在
`ui_labels`、Addressables bundle 或 metadata 里。脚本从 `.bak` 备份重建场景
文件，保持原始 serialized string 的字节长度不变，译文较短时用空格补齐，避免后续
字段错位。

当前条目：

| 文件 | 日文 | 中文 |
|---|---|---|
| `level10` | `メイン\nストーリー` | `主线\n故事` |
| `level10` | `イベント\nストーリー` | `活动\n故事` |
| `level10` | `ユニット\nストーリー` | `同伴\n故事` |
| `level10` | `ローディング\nマンガ` | `加载\n漫画` |
| `level10` | `メモリアル\nストーリー` | `纪念\n故事` |
| `level10` | `ストーリー` | `故事` |
| `level10` | `プロローグ` | `序章` |
| `level10` | `第一部オリジン` | `第一部原版` |
| `level5` | `ギャラリー` | `画廊` |
| `level11` | `設定` | `设置` |

注意：Unity 场景字符串的 UTF-8 字节前有 4 字节 serialized length。不要只把这个
length 改短，除非同时重建并重新对齐整个 serialized object。之前试过缩短 length，
进入 Story 页面会崩溃。安全的原地规则是：**保持原 length，用空格填满剩余字节**。

## 备份与幂等性

`patch-metadata` 每次都从备份重建输出：

- `global-metadata.dat.bak`
- `level5.bak`
- `level10.bak`
- `level11.bak`

备份第一次运行时创建在原文件旁边，之后不会覆盖。重复运行命令是预期用法，应得到
同样的 patched 输出。

## 搜索新字符串

常用搜索位置：

- `extracted_data/ui_labels/` —— Addressables TMP 标签；
- `extracted_data/misc/` —— MasterData JSON；
- `<APP>_Data/level*` —— 内置场景序列化字符串；
- `<APP>_Data/il2cpp_data/Metadata/global-metadata.dat` —— IL2CPP `StringData`
  和 `StringLiteralData`。

新增 metadata string literal 补丁时，最好确认它能唯一映射到一个 StringLiteral
表项，并把该表项 length 改成译文字节长度。新增内置场景补丁时，先确认字符串前
4 字节等于原 UTF-8 字节长度，说明它是独立的 Unity serialized string。
