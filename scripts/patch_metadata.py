"""Patch country / nationality enum names in global-metadata.dat.

IL2CPP stores enum field names as null-terminated UTF-8 strings inside
global-metadata.dat. The game renders country labels at runtime by calling
Enum.GetName(Country, id) and appending "の国" (defined as CountryEnum.CountrySuffix).
Patching these strings in-place makes every reference update at once — unit
detail panel, filter UI, sort labels — without touching any bundle.

Constraint: each replacement must encode to <= the original byte count.
If shorter, remaining bytes are zeroed (still null-terminated, safe).
In practice this is rarely a problem: katakana names (3 bytes/char) are
almost always longer than their CJK translations.

Edit COUNTRY_NAMES below: keys are the original Japanese names, values are
your translations.  Leave a value empty to keep the original unchanged.
Run with no args — it dry-runs first and prints a diff, then applies.
Idempotent: re-running after patching is a no-op (shows current state).

Backup: written to global-metadata.dat.bak on first run (never overwritten).
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

_ANCHORS = [b"Country\x00", b"CountryFilter\x00"]
_SUFFIX_ORIG = "の国".encode("utf-8")

# IL2CPP metadata v31 section indices
_NUM_SEC = 31
_STR_SEC = 2   # StringData (field/type name strings)
_FLD_SEC = 11  # FieldDefinition table (16B entries: nameIdx typeIdx attrIdx token)


def _expand_string(buf: bytearray, abs_off: int, new_bytes: bytes) -> None:
    """Append new_bytes to end of StringData, redirect the FieldDef nameIndex
    that referenced abs_off to the new location; shift all post-StringData
    section offsets in the header to account for the inserted bytes."""
    str_off = struct.unpack_from('<i', buf, 8 + _STR_SEC * 8)[0]
    str_cnt = struct.unpack_from('<i', buf, 8 + _STR_SEC * 8 + 4)[0]
    fld_off = struct.unpack_from('<i', buf, 8 + _FLD_SEC * 8)[0]
    fld_cnt = struct.unpack_from('<i', buf, 8 + _FLD_SEC * 8 + 4)[0]

    old_rel = abs_off - str_off
    new_rel = str_cnt
    payload = new_bytes + b'\x00'
    delta = len(payload)

    buf[str_off + str_cnt:str_off + str_cnt] = payload  # insert at StringData end
    struct.pack_into('<i', buf, 8 + _STR_SEC * 8 + 4, str_cnt + delta)
    for i in range(_NUM_SEC):
        off = struct.unpack_from('<i', buf, 8 + i * 8)[0]
        if off > str_off:
            struct.pack_into('<i', buf, 8 + i * 8, off + delta)

    # Update FieldDef nameIndex (v31: 12B entries = nameIdx+typeIdx+token, step 12)
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
        if not raw or raw[0] < 0x80:   # empty or ASCII → left the JP block
            break
        try:
            yield pos, raw.decode("utf-8"), end - pos
        except UnicodeDecodeError:
            break
        pos = end + 1


def main() -> int:
    meta = cfg.app_data_dir() / "il2cpp_data" / "Metadata" / "global-metadata.dat"
    bak  = meta.with_name("global-metadata.dat.bak")
    if not meta.exists():
        print(f"ERROR: {meta} not found.")
        return 1

    data = bytearray(meta.read_bytes())
    translations = {k: v for k, v in COUNTRY_NAMES.items() if v and v != k}

    suffix_repl = COUNTRY_SUFFIX.encode("utf-8") if COUNTRY_SUFFIX else None

    if not translations and not suffix_repl:
        print("COUNTRY_NAMES and COUNTRY_SUFFIX are empty — showing current values:\n")
        for anchor in _ANCHORS:
            print(f"  [{anchor.decode().rstrip(chr(0))}]")
            for off, name, slot in _read_block(bytes(data), anchor):
                print(f"    0x{off:06X}  {slot:2d}B  {name!r}")
        suffix_offs = [i for i in range(len(data))
                       if data[i:i+len(_SUFFIX_ORIG)] == _SUFFIX_ORIG]
        print(f"\n  [suffix 'の国']  {len(suffix_offs)} occurrences: "
              f"{[hex(x) for x in suffix_offs]}")
        print("\nEdit COUNTRY_NAMES / COUNTRY_SUFFIX at the top of this script, then re-run.")
        return 0

    # Dry run
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

    # Apply
    if not bak.exists():
        shutil.copy2(meta, bak)
        print(f"\nBackup: {bak}")

    buf = bytearray(bak.read_bytes())

    # Collect all (abs_off, slot, name, repl) across both anchors
    patches = []
    for anchor in _ANCHORS:
        for off, name, slot in _read_block(bytes(buf), anchor):
            if name in translations:
                patches.append((off, slot, name, translations[name]))

    # Expansions first (they insert bytes and shift the file), then in-place
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

    meta.write_bytes(bytes(buf))
    print(f"Patched: {meta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
