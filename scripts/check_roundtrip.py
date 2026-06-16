"""Round-trip every cached story bundle through Reader → Writer.

CLI:
    uv run scripts/check_roundtrip.py [N]

Round-trips the first N story bundles and reports byte-identical pass count.
Without N, processes the full set. Returns non-zero exit code on any mismatch.

Bundles whose plaintext doesn't reproduce byte-for-byte are listed with the
divergence offset and surrounding bytes so you can locate which struct went
wrong. See `docs/STORY_BUNDLE_GUIDE_zh-CN.md` for the schema.
"""
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercstoria import config as cfg
from mercstoria.memorypack import process_story_bundle, serialize_story

cfg.enable_utf8_stdout()


def main() -> int:
    story_dir = str(cfg.story_masterdata_dir())
    files = sorted(f for f in os.listdir(story_dir) if f.endswith(".bundle"))
    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(files)
    print(f"Round-trip {n} of {len(files)} story bundles...")
    ok = 0
    bad = []
    for i, f in enumerate(files[:n]):
        try:
            story, pt = process_story_bundle(os.path.join(story_dir, f))
            if story is None:
                bad.append((f, "no TextAsset / parse failed"))
                continue
            rt = serialize_story(story)
            if rt == pt:
                ok += 1
            else:
                k, m = 0, min(len(rt), len(pt))
                while k < m and rt[k] == pt[k]:
                    k += 1
                bad.append((f, f"diverge@{k}/{len(pt)} (rt={len(rt)}) "
                              f"orig={pt[max(0,k-4):k+8].hex()} "
                              f"rt={rt[max(0,k-4):k+8].hex()}"))
        except Exception as e:
            bad.append((f, f"exc: {e}"))
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{n}  ok={ok}  bad={len(bad)}")
    print(f"\nDone: {ok}/{n} byte-identical, {len(bad)} mismatched.")
    for f, why in bad[:20]:
        print(f"  {f}: {why}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
