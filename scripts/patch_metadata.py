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
import shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mercstoria import config as cfg
cfg.enable_utf8_stdout()


# === Translation table — edit these ===
# key = original JP name (do not change), value = translation (empty = keep)
COUNTRY_NAMES: dict[str, str] = {
    "王国":             "",   # 6 B
    "妖精":             "",   # 6 B
    "機械":             "",   # 6 B
    "和":               "",   # 3 B
    "空":               "",   # 3 B
    "西部":             "",   # 6 B
    "エレキ":           "",   # 9 B
    "魔法":             "",   # 6 B
    "恐竜":             "",   # 6 B
    "砂漠":             "",   # 6 B
    "死者":             "",   # 6 B
    "少数民族":         "",   # 12 B
    "動物":             "",   # 6 B
    "常夏":             "",   # 6 B
    "植物":             "",   # 6 B
    "科学":             "",   # 6 B
    "お菓子":           "",   # 9 B
    "雨":               "",   # 3 B
    "雪":               "",   # 3 B
    "マジョマジョ":     "",   # 18 B
    "あんガル":         "",   # 12 B
    "あんスタ":         "",   # 12 B
    "不明":             "",   # 6 B
    "小篇":             "",   # 6 B
    "ラスピリ":         "",   # 12 B
    "特別篇":           "",   # 9 B
    "外伝":             "",   # 6 B
    "カカリアスタジオ": "",   # 24 B
    "雫":               "",   # 3 B
    "その他":           "",   # 9 B
}

# Anchors: the class-name string immediately before each enum's member names.
# Country and CountryFilter share the same JP member names in the string pool
# but store them twice (no deduplication between separate enum classes).
_ANCHORS = [b"Country\x00", b"CountryFilter\x00"]


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

    if not translations:
        print("COUNTRY_NAMES is empty — showing current values:\n")
        for anchor in _ANCHORS:
            print(f"  [{anchor.decode().rstrip(chr(0))}]")
            for off, name, slot in _read_block(bytes(data), anchor):
                print(f"    0x{off:06X}  {slot:2d}B  {name!r}")
        print("\nEdit COUNTRY_NAMES at the top of this script, then re-run.")
        return 0

    # Dry run — validate all replacements fit
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
            ok = len(rb) <= slot
            status = f"OK ({len(rb)}B <= {slot}B)" if ok else f"OVERFLOW ({len(rb)}B > {slot}B)"
            print(f"    0x{off:06X}  {name!r} → {repl!r}  {status}")
            if not ok:
                all_ok = False

    if not all_ok:
        print("\nERROR: shorten the translations marked OVERFLOW and re-run.")
        return 1

    # Apply
    if not bak.exists():
        shutil.copy2(meta, bak)
        print(f"\nBackup: {bak}")

    buf = bytearray(bak.read_bytes())
    for anchor in _ANCHORS:
        for off, name, slot, repl in (
            (off, n, s, translations[n])
            for off, n, s in _read_block(bytes(buf), anchor)
            if n in translations
        ):
            rb = repl.encode("utf-8")
            buf[off:off + slot] = rb + b"\x00" * (slot - len(rb))

    meta.write_bytes(bytes(buf))
    print(f"Patched: {meta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
