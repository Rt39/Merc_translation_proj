"""
export_chars.py — produce target_chars.txt for the Unity TMP font bake.

Output: tools/target_chars.txt  (literal characters, sorted by codepoint, single line).

The 4096x4096 SDF atlas at samplingPointSize=32 fits ~7,900 glyphs.

Two tier classes:

  REQUIRED — always fully included; never trimmed regardless of headroom.
    1. ASCII printable      (U+0020..U+007E)
    2. CJK punctuation      (U+3000..U+303F  - 「」、。… etc.)
    3. Hiragana / Katakana  (U+3040..U+30FF)
    4. Halfwidth + fullwidth symbols   (tools/symbols_*.txt)
    5. Joyo kanji 2,136     (tools/joyo.tsv, first column)
    6. 通用规范汉字表一级字 3,500 (tools/tgh_level1.txt)   ← CRITICAL
    7. Every codepoint in translate_*.py at the repo root.
       These hold the literal Chinese translation strings; if a char used
       there isn't baked into the atlas it renders as a glyph fragment.
    8. Optional --include-corpus: every codepoint found in
       extracted_data/**/*.json `value` fields (the JP source corpus —
       captures rare kanji like 雛 / 傾 that the SC lists never carry).

  FILL — added in 7000hanzi frequency order, capped at remaining headroom.
    9. 通用规范汉字表二级字 3,000 (tools/tgh_level2.txt) ∪ 7000hanzi extras.

Trimming the FILL tier first preserves L1 in full — the previous 7000hanzi
top-5500 cap silently dropped 499 L1 chars (赛 / 翼 / 羹 …); they now ride
the REQUIRED tier and can never be trimmed.

Sources:
  - https://github.com/hiroshi-manabe/Joyo-Kanji-List
  - https://github.com/shengdoushi/common-standard-chinese-characters-table
  - https://github.com/qweyouke/Unity-TextMeshPro-Chinese-Characters-Set
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
OUT = TOOLS / "target_chars.txt"

ATLAS_CEILING = 8000  # empirical: real 32pt SDF + padding=5 tops out around
                      # 8,000 glyphs. 7800 + ~180 missing-from-corpus chars
                      # just fits under that ceiling.


def _load_lines_first_column(path: Path) -> set[str]:
    """Read a TSV; collect the first column character of every non-empty line."""
    chars: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        chars.add(line.split("\t", 1)[0])
    return chars


def _load_all_chars(path: Path) -> set[str]:
    """Read a plain text file; return every codepoint (drop newlines / CR)."""
    raw = path.read_text(encoding="utf-8")
    return {c for c in raw if c not in ("\n", "\r")}


def _load_ordered_chars(path: Path, limit: int | None = None) -> list[str]:
    """Read a plain text file preserving char order, optional length cap.
    Used for the SC 7000 set which is frequency-sorted — capping trims the
    rarest tail first."""
    raw = path.read_text(encoding="utf-8")
    seen: set[str] = set()
    out: list[str] = []
    for c in raw:
        if c in ("\n", "\r"):
            continue
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
        if limit is not None and len(out) >= limit:
            break
    return out


def _range(lo: int, hi_inclusive: int) -> set[str]:
    return {chr(cp) for cp in range(lo, hi_inclusive + 1)}


def _scan_translation_files(root: Path) -> set[str]:
    """Union of every codepoint that appears in any translate_*.py at the
    repo root. These files hold the CN translation strings — every char
    used there must be in the atlas or it renders as garbage in-game.
    Reading as plain text picks up both JP keys and CN values; ASCII
    overlap with Python syntax is harmless (ASCII tier already included)."""
    chars: set[str] = set()
    for pf in sorted(root.glob("translate_*.py")):
        try:
            chars.update(pf.read_text(encoding="utf-8"))
        except OSError:
            continue
    return chars


def _scan_corpus(corpus_dir: Path) -> set[str]:
    """Union of every codepoint that appears in any string leaf of any
    extracted JSON. Walks the full tree (not just `value` keys) so the
    story bundles' `Text` / `Speakers` / scene strings and the inline-UI
    `text` / ui-labels payloads are all covered alongside the offset-
    schema misc `strings[*].value`. Picks up ASCII / metadata too, which
    is harmless — those codepoints are already in the REQUIRED tier."""
    chars: set[str] = set()
    for jf in corpus_dir.rglob("*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        def _walk(node):  # noqa: ANN001
            if isinstance(node, dict):
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for v in node:
                    _walk(v)
            elif isinstance(node, str):
                chars.update(node)

        _walk(data)
    return chars


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument(
        "--include-corpus",
        action="store_true",
        help="union with codepoints found in extracted_data/**/*.json `value` fields",
    )
    ap.add_argument(
        "--corpus-dir",
        default=str(ROOT / "extracted_data"),
        help="root to scan when --include-corpus is set",
    )
    ap.add_argument(
        "--ceiling",
        type=int,
        default=ATLAS_CEILING,
        help=f"hard cap on final set size; FILL tier is trimmed first "
             f"(default {ATLAS_CEILING})",
    )
    args = ap.parse_args()

    # ---- REQUIRED tiers (no cap, always full) -----------------------------
    required: dict[str, set[str]] = {
        "ascii":         _range(0x0020, 0x007E),
        "cjk_punct":     _range(0x3000, 0x303F),
        "hiragana":      _range(0x3040, 0x309F),
        "katakana":      _range(0x30A0, 0x30FF),
        "sym_fullwidth": _load_all_chars(TOOLS / "symbols_fullwidth.txt"),
        "sym_halfwidth": _load_all_chars(TOOLS / "symbols_halfwidth.txt"),
        "joyo_2136":     _load_lines_first_column(TOOLS / "joyo.tsv"),
        "tgh_level1":    _load_all_chars(TOOLS / "tgh_level1.txt"),
        "translations":  _scan_translation_files(ROOT),
    }
    if args.include_corpus:
        corpus_dir = Path(args.corpus_dir)
        if corpus_dir.is_dir():
            required["corpus_jp"] = _scan_corpus(corpus_dir)
        else:
            print(f"!! --include-corpus set but {corpus_dir} does not exist", file=sys.stderr)

    # ---- FILL candidates (ordered by 7000hanzi frequency rank) -----------
    # 7000hanzi.txt is frequency-sorted. We walk it in order, take any char
    # we haven't already covered (and that is in L2 or the freq list) until
    # the ceiling is reached. L2 chars not appearing in 7000hanzi at all
    # (rare radicals; ~100 chars) are appended last, in stroke order.
    sc_freq_order = _load_ordered_chars(TOOLS / "7000hanzi.txt")
    sc_set = set(sc_freq_order)
    l2_only = [c for c in _load_ordered_chars(TOOLS / "tgh_level2.txt") if c not in sc_set]
    fill_pool: list[str] = sc_freq_order + l2_only
    # 7000hanzi (freq-sorted) first, then any L2 chars it misses last.

    # ---- assemble & report -----------------------------------------------
    all_chars: set[str] = set()
    print(f"{'tier':<16} {'size':>6} {'new':>6}  (required)")
    print("-" * 44)
    for name, s in required.items():
        new = s - all_chars
        all_chars |= s
        print(f"{name:<16} {len(s):>6} {len(new):>6}")

    # Drop control chars before counting headroom.
    all_chars = {c for c in all_chars if ord(c) >= 0x20}

    print("-" * 44)
    print(f"{'REQUIRED total':<16} {len(all_chars):>6}")

    if len(all_chars) > args.ceiling:
        print(
            f"\n!! REQUIRED tiers alone ({len(all_chars)}) exceed ceiling "
            f"{args.ceiling}; FILL tier disabled.\n"
            f"   The 32pt SDF atlas may drop high-codepoint chars.",
            file=sys.stderr,
        )
        fill_added = 0
    else:
        headroom = args.ceiling - len(all_chars)
        fill_added = 0
        for c in fill_pool:
            if fill_added >= headroom:
                break
            if c in all_chars:
                continue
            all_chars.add(c)
            fill_added += 1
        print(f"{'FILL (capped)':<16} {len(fill_pool):>6} {fill_added:>6}")
        print("-" * 44)
        print(f"{'TOTAL':<16} {len(all_chars):>6}  (ceiling {args.ceiling})")

    # Sanity-check the critical guarantee.
    l1_missing = required["tgh_level1"] - all_chars
    if l1_missing:
        print(
            f"\n!! INVARIANT VIOLATED: {len(l1_missing)} L1 chars missing "
            f"from final set: {''.join(sorted(l1_missing))[:60]}...",
            file=sys.stderr,
        )
        return 1
    print(f"\nL1 coverage: 3500 / 3500  (guaranteed)")

    # ---- emit ------------------------------------------------------------
    # Literal characters, sorted by codepoint. Unity's TMP bake script reads
    # the file as a string and feeds it to TryAddCharacters() directly.
    ordered = "".join(chr(cp) for cp in sorted(ord(c) for c in all_chars))
    OUT.write_text(ordered, encoding="utf-8")
    print(f"\nwrote {OUT}  ({len(ordered)} chars)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
