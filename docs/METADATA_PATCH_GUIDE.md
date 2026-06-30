# Global Metadata and Built-in Scene UI Patch Guide

> 中文版请戳[这里](METADATA_PATCH_GUIDE_zh-CN.md)。

`uv run -m mercstoria patch-metadata` handles small UI strings that are not part
of the encrypted story payload, MasterData JSONs, Addressables UI-label bundles,
or bitmap UI atlases. These strings live in two places:

1. `global-metadata.dat` — IL2CPP enum names and string literals.
2. Built-in Unity scene files (`level5`, `level10`, `level11`) under
   `<APP>_Data/`.

The implementation is `scripts/patch_metadata.py`.

## What it patches

### Country labels

Country names are IL2CPP enum field names in the metadata string pool:

- `Country`
- `CountryFilter`

Runtime display is approximately:

```csharp
Enum.GetName(Country, id) + "の国"
```

So the script patches:

- selected entries in `COUNTRY_NAMES`
- the `COUNTRY_SUFFIX` literal (`の国` → `之国`)

Short replacements are zero-padded in place. Longer country enum field names are
appended to the end of metadata `StringData`, and matching `FieldDefinition`
`nameIndex` values are redirected.

### Metadata string literals

Some UI labels are direct IL2CPP string literals, stored in metadata section 1
(`StringLiteralData`) and referenced by section 0 (`StringLiteral`). The script
patches the bytes and also shortens the corresponding StringLiteral length so
padding bytes are not rendered.

Current trial entries:

| Japanese | Chinese | Notes |
|---|---|---|
| `ユニット一覧` | `角色列表` | Unit UI label; Unit outside `Unit Story` is translated as 角色. |
| `ユニット総数` | `角色总数` | Unit count label. |
| `すべてのイベント` | `全部活动` | Event filter label. |
| `絞り込み` | `筛选` | Filter label. |

### Metadata `StringData` entries

Some filter labels are enum field names or related helper labels in metadata
section 2 (`StringData`). These are null-terminated strings; short replacements
are zero-padded.

Current entries:

| Japanese | Chinese | Notes |
|---|---|---|
| `幻憶` | `幻忆` | `EventStoryFilter`. |
| `コラボ` | `联动` | `EventStoryFilter`. |
| `第一部オリジン` | `第一部原版` | Story filter/category label. |

Do **not** patch `イベント幻憶` / `イベントコラボ`: those were tested and then
left as vanilla strings because the visible UI only needed the shorter filter
names.

### Built-in scene strings

Several menu titles and story tab labels are serialized directly in built-in
Unity scene files. They are not present in `ui_labels`, Addressables bundles, or
metadata. The script patches them from scene backups and preserves the original
serialized string byte length by padding shorter translations with spaces. This
keeps later serialized fields aligned.

Current entries:

| File | Japanese | Chinese |
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

Important: Unity scene strings have a 4-byte serialized length prefix before
the UTF-8 bytes. Do **not** shorten the length prefix unless you also rebuild and
realign the entire serialized object. A prior attempt shortened the prefix and
made the Story screen crash. The safe in-place rule is: keep the original length
and fill unused bytes with spaces.

## Backups and idempotency

`patch-metadata` rebuilds modified files from backups each run:

- `global-metadata.dat.bak`
- `level5.bak`
- `level10.bak`
- `level11.bak`

The backups are created next to the originals on first run and are not
overwritten. Re-running the command is expected and should produce the same
patched output.

## Searching for new strings

Useful places to search:

- `extracted_data/ui_labels/` — Addressables TMP labels.
- `extracted_data/misc/` — MasterData JSONs.
- `<APP>_Data/level*` — built-in scene serialized strings.
- `<APP>_Data/il2cpp_data/Metadata/global-metadata.dat` — IL2CPP `StringData`
  and `StringLiteralData`.

When adding a metadata string literal patch, prefer mapping it to exactly one
StringLiteral table entry and update that entry's length to the replacement byte
length. When adding a built-in scene patch, verify the string is a standalone
Unity serialized string by checking the 4 bytes before it equal the original
UTF-8 byte length.
