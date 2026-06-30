"""Patch metadata enum names and built-in scene UI strings.

IL2CPP stores country enum field names as null-terminated UTF-8 strings inside
global-metadata.dat. Country / CountryFilter are rendered as
Enum.GetName(Country, id) + COUNTRY_SUFFIX ("の国"). Class names appear BEFORE
their field names in the string pool, so _read_block scans forward from the
anchor.

A few home/story/gallery UI labels are serialized directly in built-in Unity
scene files (level5 / level10 / level11). They are patched in place without
changing serialized string byte-lengths; shorter replacements are padded with
spaces so later fields stay aligned.

Edit COUNTRY_NAMES / COUNTRY_SUFFIX / SCENE_TEXT_PATCHES below. Run with no
args — dry-runs first, then applies. Idempotent. Backups are written next to
the original files on first run.
"""
from __future__ import annotations
import shutil, struct, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mercstoria import config as cfg
cfg.enable_utf8_stdout()


# === Translation table — edit these ===
# key = original JP name (do not change), value = translation (empty = keep)
COUNTRY_NAMES: dict[str, str] = {
    "王国":             "",   # 6 B
    "妖精":             "",   # 6 B
    "機械":             "机械",   # 6 B
    "和":               "",   # 3 B
    "空":               "",   # 3 B
    "西部":             "",   # 6 B
    "エレキ":           "电",   # 9 B
    "魔法":             "",   # 6 B
    "恐竜":             "恐龙",   # 6 B
    "砂漠":             "沙漠",   # 6 B
    "死者":             "",   # 6 B
    "少数民族":         "",   # 12 B
    "動物":             "动物",   # 6 B
    "常夏":             "",   # 6 B
    "植物":             "",   # 6 B
    "科学":             "",   # 6 B
    "お菓子":           "点心",   # 9 B
    "雨":               "",   # 3 B
    "雪":               "",   # 3 B
    "マジョマジョ":     "",   # 18 B
    "あんガル":         "女生重奏曲",   # 12 B
    "あんスタ":         "偶像梦幻祭",   # 12 B
    "不明":             "",   # 6 B
    "小篇":             "",   # 6 B
    "ラスピリ":         "最终休止符",   # 12 B
    "特別篇":           "",   # 9 B
    "外伝":             "外传",   # 6 B
    "カカリアスタジオ": "卡卡利亚工作室",   # 24 B
    "雫":               "",   # 3 B
    "その他":           "其他",   # 9 B
}

# Suffix appended by CountryEnum.GetLabel: Enum.GetName(Country, id) + COUNTRY_SUFFIX
# Two occurrences in the stringliteral region (different call sites). Must be <= 6 bytes.
COUNTRY_SUFFIX: str = "之国"   # e.g. "之国" (6B) to replace "の国"

SCENE_TEXT_PATCHES: list[tuple[str, str, str]] = [
    ("level10", "メイン\nストーリー", "主线\n故事"),
    ("level10", "イベント\nストーリー", "活动\n故事"),
    ("level10", "ユニット\nストーリー", "同伴\n故事"),
    ("level10", "ローディング\nマンガ", "加载\n漫画"),
    ("level10", "メモリアル\nストーリー", "纪念\n故事"),
    ("level10", "ストーリー", "故事"),
    ("level5",  "ギャラリー", "画廊"),
    ("level11", "設定", "设置"),
    ("level10", "プロローグ", "序章"),
    ("level10", "第一部オリジン", "第一部原版"),
]

STRING_LITERAL_PATCHES: dict[str, str] = {
    "ユニット一覧": "角色列表",
    "ユニット総数": "角色总数",
    "すべてのイベント": "全部活动",
    "絞り込み": "筛选",
}

STRING_DATA_PATCHES: dict[str, str] = {
    "幻憶": "幻忆",
    "コラボ": "联动",
    "第一部オリジン": "第一部原版",
}
_ANCHORS = [b"Country\x00", b"CountryFilter\x00"]
_SUFFIX_ORIG = "の国".encode("utf-8")

# IL2CPP metadata v31 section indices
_NUM_SEC = 31
_STR_SEC = 2   # StringData (field/type name strings)
_FLD_SEC = 11  # FieldDefinition table (16B entries: nameIdx typeIdx attrIdx token)


def _expand_string(buf: bytearray, abs_off: int, new_bytes: bytes) -> None:
    str_off = struct.unpack_from('<i', buf, 8 + _STR_SEC * 8)[0]
    str_cnt = struct.unpack_from('<i', buf, 8 + _STR_SEC * 8 + 4)[0]
    fld_off = struct.unpack_from('<i', buf, 8 + _FLD_SEC * 8)[0]
    fld_cnt = struct.unpack_from('<i', buf, 8 + _FLD_SEC * 8 + 4)[0]

    old_rel = abs_off - str_off
    new_rel = str_cnt
    payload = new_bytes + b'\x00'
    delta = len(payload)

    buf[str_off + str_cnt:str_off + str_cnt] = payload
    struct.pack_into('<i', buf, 8 + _STR_SEC * 8 + 4, str_cnt + delta)
    for i in range(_NUM_SEC):
        off = struct.unpack_from('<i', buf, 8 + i * 8)[0]
        if off > str_off:
            struct.pack_into('<i', buf, 8 + i * 8, off + delta)

    actual_fld = fld_off + delta
    old_pk, new_pk = struct.pack('<i', old_rel), struct.pack('<i', new_rel)
    pos, found = actual_fld, 0
    while pos < actual_fld + fld_cnt:
        if buf[pos:pos + 4] == old_pk:
            buf[pos:pos + 4] = new_pk
            found += 1
        pos += 12
    if not found:
        raise SystemExit(f"No FieldDef with nameIndex=0x{old_rel:x} — metadata mismatch")


def _read_block(data: bytes, anchor: bytes):
    """Yield (offset, name, slot_bytes) for each JP member name after anchor."""
    pos = data.find(anchor)
    if pos < 0:
        raise SystemExit(f"anchor {anchor!r} not found — game update?")
    pos += len(anchor)
    while pos < len(data):
        end = data.index(0, pos)
        raw = bytes(data[pos:end])
        if not raw or raw[0] < 0x80:
            break
        try:
            yield pos, raw.decode("utf-8"), end - pos
        except UnicodeDecodeError:
            break
        pos = end + 1
        while pos < len(data) and data[pos] == 0:
            pos += 1


def _metadata_raw_string_plan(data: bytes, table: dict[str, str], zero_pad: bool):
    for orig, repl in table.items():
        if not repl or repl == orig:
            continue
        orig_b = orig.encode("utf-8")
        repl_b = repl.encode("utf-8")
        matches = []
        start = 0
        while True:
            off = data.find(orig_b, start)
            if off < 0:
                break
            if off == 0 or data[off - 1] == 0:
                end = off + len(orig_b)
                if end >= len(data) or data[end] == 0:
                    matches.append(off)
            start = off + 1
        if not matches:
            if repl_b in data:
                yield None, orig, repl, len(orig_b), len(repl_b), "already patched", zero_pad
                continue
            raise SystemExit(f"metadata: string {orig!r} not found — game update?")
        for off in matches:
            if len(repl_b) > len(orig_b):
                yield off, orig, repl, len(orig_b), len(repl_b), "OVERFLOW", zero_pad
            else:
                yield off, orig, repl, len(orig_b), len(repl_b), "OK", zero_pad


def _metadata_string_literal_plan(data: bytes, table: dict[str, str]):
    strlit_off, strlit_cnt = struct.unpack_from('<ii', data, 8)
    litdata_off, litdata_cnt = struct.unpack_from('<ii', data, 16)
    litdata_end = litdata_off + litdata_cnt
    region = data[litdata_off:litdata_end]
    for orig, repl in table.items():
        if not repl or repl == orig:
            continue
        orig_b = orig.encode("utf-8")
        repl_b = repl.encode("utf-8")
        matches = []
        start = 0
        while True:
            rel = region.find(orig_b, start)
            if rel < 0:
                break
            entries = []
            for i in range(strlit_cnt // 8):
                length, data_idx = struct.unpack_from('<Ii', data, strlit_off + i * 8)
                if data_idx == rel and length == len(orig_b):
                    entries.append(strlit_off + i * 8)
            matches.append((litdata_off + rel, entries))
            start = rel + 1
        if not matches:
            if repl_b in region:
                yield None, orig, repl, len(orig_b), len(repl_b), "already patched", False, None
                continue
            raise SystemExit(f"metadata: string literal {orig!r} not found — game update?")
        exact = [(off, entries) for off, entries in matches if entries]
        if len(exact) != 1 or len(exact[0][1]) != 1:
            raise SystemExit(f"metadata: string literal {orig!r} did not map to exactly one StringLiteral entry")
        off, entries = exact[0]
        if len(repl_b) > len(orig_b):
            yield off, orig, repl, len(orig_b), len(repl_b), "OVERFLOW", False, entries[0]
        else:
            yield off, orig, repl, len(orig_b), len(repl_b), "OK", False, entries[0]


def _apply_metadata_byte_patches(buf: bytearray, plans) -> None:
    for plan in plans:
        off, orig, repl, orig_len, repl_len, status, zero_pad, *rest = plan
        lit_entry_off = rest[0] if rest else None
        if status == "already patched":
            continue
        if status != "OK":
            raise SystemExit(f"metadata: {orig!r} → {repl!r} {status}")
        repl_b = repl.encode("utf-8")
        pad = b"\x00" if zero_pad else b" "
        buf[off:off + orig_len] = repl_b + pad * (orig_len - len(repl_b))
        if lit_entry_off is not None:
            struct.pack_into('<I', buf, lit_entry_off, len(repl_b))


def _scene_patch_plan(data: bytes, scene_name: str):
    for target_scene, orig, repl in SCENE_TEXT_PATCHES:
        if target_scene != scene_name or not repl or repl == orig:
            continue
        orig_b = orig.encode("utf-8")
        repl_b = repl.encode("utf-8")
        matches = []
        start = 0
        while True:
            off = data.find(orig_b, start)
            if off < 0:
                break
            length_off = off - 4
            if length_off >= 0 and struct.unpack_from('<i', data, length_off)[0] == len(orig_b):
                matches.append(off)
            start = off + 1
        if not matches:
            if repl_b in data:
                yield None, orig, repl, len(orig_b), len(repl_b), "already patched"
                continue
            raise SystemExit(f"{scene_name}: string {orig!r} not found — game update?")
        if len(matches) > 1:
            raise SystemExit(f"{scene_name}: string {orig!r} matched multiple serialized strings: {[hex(x) for x in matches]}")
        off = matches[0]
        if len(repl_b) > len(orig_b):
            yield off, orig, repl, len(orig_b), len(repl_b), "OVERFLOW"
            continue
        length_off = off - 4
        if length_off < 0 or struct.unpack_from('<i', data, length_off)[0] != len(orig_b):
            raise SystemExit(f"{scene_name}: length prefix mismatch for {orig!r} at 0x{off:x}")
        yield off, orig, repl, len(orig_b), len(repl_b), "OK"


def _apply_scene_patches(scene_path: Path) -> bool:
    bak = scene_path.with_name(scene_path.name + ".bak")
    if not bak.exists():
        shutil.copy2(scene_path, bak)
        print(f"\nBackup: {bak}")

    buf = bytearray(bak.read_bytes())
    changed = False
    for off, orig, repl, orig_len, repl_len, status in _scene_patch_plan(bytes(buf), scene_path.name):
        if status == "already patched":
            changed = True
            continue
        if status != "OK":
            raise SystemExit(f"{scene_path.name}: {orig!r} → {repl!r} {status}")
        repl_b = repl.encode("utf-8")
        buf[off:off + orig_len] = repl_b + b" " * (orig_len - len(repl_b))
        changed = True
    if changed:
        scene_path.write_bytes(bytes(buf))
    return changed


def main() -> int:
    meta = cfg.app_data_dir() / "il2cpp_data" / "Metadata" / "global-metadata.dat"
    bak = meta.with_name("global-metadata.dat.bak")
    if not meta.exists():
        print(f"ERROR: {meta} not found.")
        return 1

    data = bytearray(meta.read_bytes())
    translations = {k: v for k, v in COUNTRY_NAMES.items() if v and v != k}
    suffix_repl = COUNTRY_SUFFIX.encode("utf-8") if COUNTRY_SUFFIX else None
    scene_names = sorted({scene for scene, orig, repl in SCENE_TEXT_PATCHES if repl and repl != orig})
    literal_patch_enabled = any(v and v != k for k, v in STRING_LITERAL_PATCHES.items())
    string_data_patch_enabled = any(v and v != k for k, v in STRING_DATA_PATCHES.items())

    if not translations and not suffix_repl and not scene_names and not literal_patch_enabled and not string_data_patch_enabled:
        print("All translation tables are empty — showing current values:\n")
        for anchor in _ANCHORS:
            print(f"  [{anchor.decode().rstrip(chr(0))}]")
            for off, name, slot in _read_block(bytes(data), anchor):
                print(f"    0x{off:06X}  {slot:2d}B  {name!r}")
        suffix_offs = [i for i in range(len(data))
                       if data[i:i + len(_SUFFIX_ORIG)] == _SUFFIX_ORIG]
        print(f"\n  [suffix 'の国']  {len(suffix_offs)} occurrences: "
              f"{[hex(x) for x in suffix_offs]}")
        print("\nEdit COUNTRY_NAMES / COUNTRY_SUFFIX at the top, then re-run.")
        return 0

    print("=== Dry run ===")
    all_ok = True

    for anchor in _ANCHORS:
        label = anchor.decode().rstrip("\x00")
        changes = [(off, name, slot, translations[name])
                   for off, name, slot in _read_block(bytes(data), anchor)
                   if name in translations]
        if not changes:
            continue
        print(f"\n  [{label}]")
        for off, name, slot, repl in changes:
            rb = repl.encode("utf-8")
            if len(rb) <= slot:
                print(f"    0x{off:06X}  {name!r} → {repl!r}  OK ({len(rb)}B <= {slot}B)")
            else:
                print(f"    0x{off:06X}  {name!r} → {repl!r}  EXPAND ({len(rb)}B > {slot}B, append to StringData)")

    for scene_name in scene_names:
        scene_path = cfg.app_data_dir() / scene_name
        if not scene_path.exists():
            raise SystemExit(f"ERROR: {scene_path} not found.")
        scene_data = scene_path.read_bytes()
        scene_changes = list(_scene_patch_plan(scene_data, scene_name))
        if not scene_changes:
            continue
        print(f"\n  [{scene_name}]")
        for off, orig, repl, orig_len, repl_len, status in scene_changes:
            loc = "already" if off is None else f"0x{off:06X}"
            print(f"    {loc}  {orig!r} → {repl!r}  {status} ({repl_len}B <= {orig_len}B)")
            if status == "OVERFLOW":
                all_ok = False

    metadata_byte_plans = []
    metadata_byte_plans.extend(_metadata_string_literal_plan(bytes(data), STRING_LITERAL_PATCHES))
    metadata_byte_plans.extend(_metadata_raw_string_plan(bytes(data), STRING_DATA_PATCHES, zero_pad=True))
    if metadata_byte_plans:
        print(f"\n  [metadata strings]")
        for plan in metadata_byte_plans:
            off, orig, repl, orig_len, repl_len, status, _zero_pad, *_rest = plan
            loc = "already" if off is None else f"0x{off:06X}"
            print(f"    {loc}  {orig!r} → {repl!r}  {status} ({repl_len}B <= {orig_len}B)")
            if status == "OVERFLOW":
                all_ok = False

    if suffix_repl is not None:
        if len(suffix_repl) > len(_SUFFIX_ORIG):
            print(f"\n  [suffix] OVERFLOW: {COUNTRY_SUFFIX!r} is {len(suffix_repl)}B "
                  f"> {len(_SUFFIX_ORIG)}B")
            all_ok = False
        else:
            n = bytes(data).count(_SUFFIX_ORIG)
            print(f"\n  [suffix] 'の国' → {COUNTRY_SUFFIX!r}  "
                  f"({len(suffix_repl)}B <= {len(_SUFFIX_ORIG)}B)  "
                  f"OK  ({n} occurrences)")

    if not all_ok:
        print("\nERROR: shorten the translations marked OVERFLOW and re-run.")
        return 1

    if not bak.exists():
        shutil.copy2(meta, bak)
        print(f"\nBackup: {bak}")

    buf = bytearray(bak.read_bytes())
    patches = []
    for anchor in _ANCHORS:
        for off, name, slot in _read_block(bytes(buf), anchor):
            if name in translations:
                patches.append((off, slot, name, translations[name]))

    for off, slot, name, repl in patches:
        rb = repl.encode("utf-8")
        if len(rb) > slot:
            _expand_string(buf, off, rb)
    for off, slot, name, repl in patches:
        rb = repl.encode("utf-8")
        if len(rb) <= slot:
            buf[off:off + slot] = rb + b"\x00" * (slot - len(rb))

    if suffix_repl is not None:
        padded = suffix_repl + b"\x00" * (len(_SUFFIX_ORIG) - len(suffix_repl))
        pos = 0
        while True:
            i = bytes(buf).find(_SUFFIX_ORIG, pos)
            if i < 0:
                break
            buf[i:i + len(_SUFFIX_ORIG)] = padded
            pos = i + len(_SUFFIX_ORIG)

    metadata_apply_plans = []
    metadata_apply_plans.extend(_metadata_string_literal_plan(bytes(buf), STRING_LITERAL_PATCHES))
    metadata_apply_plans.extend(_metadata_raw_string_plan(bytes(buf), STRING_DATA_PATCHES, zero_pad=True))
    _apply_metadata_byte_patches(buf, metadata_apply_plans)

    meta.write_bytes(bytes(buf))
    print(f"Patched: {meta}")

    for scene_name in scene_names:
        scene_path = cfg.app_data_dir() / scene_name
        if _apply_scene_patches(scene_path):
            print(f"Patched: {scene_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
