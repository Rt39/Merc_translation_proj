"""Inline UI text (Timeline cinematic dialogue) extract / repack helpers.

A handful of `BundleAssets/<hash>.bundle` files contain MonoBehaviour
objects whose `parameter.text` field carries Japanese dialogue baked
straight into the Unity Timeline — final-chapter cinematics, the credit
roll, etc. These do NOT go through the AES + MemoryPack pipeline used
for `StoryMasterData/`; they are plain Unity assets read via TypeTree.

Workflow (entry points are in extract_repack.py — `mercstoria extract`
calls cmd_extract_ui after story+misc; `mercstoria repack` calls
cmd_repack_ui at the end):

    extracted_data/inline_ui/<bundle_hash>.json
        One JSON per BundleAssets bundle that contains JP MonoBehaviour
        text. Each is a list of {path_id, name, text} entries — one per
        MonoBehaviour with translatable text.

    repacked_bundles/inline_ui/<bundle_hash>.bundle
        Output of repack — fed into deploy.py same as story / misc.

Translators edit each entry's `text` field in place. Anything else
(`path_id`, `name`, `bundle`) is metadata used to round-trip the change
back to the right object — leave it alone.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import sys
import time
from pathlib import Path

import UnityPy
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mercstoria import config as cfg

cfg.enable_utf8_stdout()


_HERE = Path(__file__).resolve().parent.parent     # repo root

EXTRACT_ROOT = _HERE / "extracted_data"
INLINE_UI_OUT = EXTRACT_ROOT / "inline_ui"
FINGERPRINTS_PATH = EXTRACT_ROOT / ".fingerprints.pkl"

REPACK_ROOT = _HERE / "repacked_bundles"
REPACK_INLINE_UI = REPACK_ROOT / "inline_ui"


def has_jp(s: str) -> bool:
    """True if `s` contains any Hiragana / Katakana / CJK / halfwidth-kana
    code point. Same definition the misc-text scanner uses."""
    if not s:
        return False
    return any(
        '぀' <= c <= 'ヿ' or '一' <= c <= '鿿' or '＀' <= c <= 'ﾟ'
        for c in s
    )


def _try_read_text(obj):
    """Return the JP text in this MonoBehaviour, or None if it has no
    translatable inline-text payload.

    Filter: must read as TypeTree, must have `parameter.text`, and that
    text must contain Japanese. Skips MonoBehaviours whose typetree
    layout is something else entirely (most BundleAssets MonoBehaviours).
    """
    try:
        tt = obj.read_typetree()
    except Exception:
        return None
    param = tt.get("parameter")
    if not isinstance(param, dict):
        return None
    text = param.get("text")
    if not isinstance(text, str):
        return None
    if not has_jp(text):
        return None
    return tt.get("m_Name", ""), text, tt.get("m_Time", 0.0)


# === Fingerprints (shared with extract_repack.py) ===

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    with open(path, 'rb') as f:
        return sha256_bytes(f.read())


def load_fingerprints() -> dict:
    if not FINGERPRINTS_PATH.exists():
        return {}
    try:
        with open(FINGERPRINTS_PATH, 'rb') as f:
            return pickle.load(f)
    except Exception:
        return {}


def save_fingerprints(fps: dict):
    EXTRACT_ROOT.mkdir(parents=True, exist_ok=True)
    with open(FINGERPRINTS_PATH, 'wb') as f:
        pickle.dump(fps, f)


def write_json_with_fingerprint(path: Path, payload, fps: dict, key: str):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    with open(path, 'wb') as f:
        f.write(body)
    fps[key] = sha256_bytes(body)


# === Confirm overwrite (mirrors extract_repack._confirm_overwrite) ===

def _confirm_overwrite(out_dir: Path, label: str, yes: bool) -> bool:
    if not out_dir.is_dir():
        return True
    if not any(out_dir.iterdir()):
        return True
    print(f"  WARNING: {out_dir} already exists and is not empty.")
    print(f"  Re-extracting {label} will overwrite any in-progress")
    print( "  translations there. The fingerprint baseline in")
    print(f"  {FINGERPRINTS_PATH} will also be replaced — repack")
    print( "  would then treat the just-overwritten files as 'unchanged'.")
    if yes:
        print("  --yes given; proceeding with re-extract.")
        return True
    resp = input("  Continue and overwrite? [y/N]: ").strip().lower()
    if resp not in ("y", "yes"):
        print("  Aborted.")
        return False
    return True


# === Extract ===

def cmd_extract_ui(yes: bool = False, _skip_confirm: bool = False):
    """Walk BundleAssets/, dump every MonoBehaviour with JP `parameter.text`
    to extracted_data/inline_ui/<bundle_hash>.json.

    Bundles with no JP text are skipped entirely (no JSON written). The
    set of bundles that actually contain inline-UI dialogue is small
    (~7 of 241 in the vanilla game), so the resulting directory listing
    doubles as the work-list for translators.
    """
    if not _skip_confirm and not _confirm_overwrite(INLINE_UI_OUT, "inline_ui", yes):
        return
    INLINE_UI_OUT.mkdir(parents=True, exist_ok=True)
    fps = load_fingerprints()

    src_dir = cfg.bundleassets_dir()
    if not src_dir.is_dir():
        raise SystemExit(f"extract-ui: {src_dir} does not exist; populate the cache first.")

    bundles = sorted(p for p in src_dir.iterdir() if p.suffix == '.bundle')
    print(f"Scanning {len(bundles)} BundleAssets bundles -> {INLINE_UI_OUT}")

    written = 0
    total_strings = 0
    t0 = time.time()
    for p in tqdm(bundles, desc="extract-ui", unit="bundle"):
        try:
            env = UnityPy.load(str(p))
        except Exception as e:
            tqdm.write(f"  ERROR loading {p.name}: {e}")
            continue

        entries = []
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            res = _try_read_text(obj)
            if res is None:
                continue
            name, text, time_ = res
            entries.append({
                "path_id": obj.path_id,
                "time": time_,
                "name": name,
                "text": text,
            })

        if not entries:
            continue

        # Sort by Timeline trigger time so translators see lines in
        # playback order. Stable on path_id as a tiebreaker.
        entries.sort(key=lambda e: (e["time"], e["path_id"]))
        payload = {
            "bundle": p.name,
            "subdir": cfg.BUNDLEASSETS_SUBDIR,
            "entries": entries,
        }
        out_path = INLINE_UI_OUT / f"{p.stem}.json"
        key = f"inline_ui/{out_path.name}"
        write_json_with_fingerprint(out_path, payload, fps, key)
        written += 1
        total_strings += len(entries)
        tqdm.write(f"  {p.name}  entries={len(entries)}")

    save_fingerprints(fps)
    print(f"\nDone in {time.time() - t0:.1f}s. "
          f"Wrote {written} inline-UI JSONs, {total_strings} strings total.")


# === Repack ===

def _is_modified(path: Path, key: str, fps: dict, force: bool) -> bool:
    if force:
        return True
    baseline = fps.get(key)
    if baseline is None:
        return False
    return sha256_file(path) != baseline


def cmd_repack_ui(force: bool = False):
    """Read each modified inline_ui JSON, apply text edits via TypeTree,
    write the new bundle to repacked_bundles/inline_ui/.

    Per-entry: look up MonoBehaviour by path_id, read typetree, set
    `parameter.text` to the JSON value, save typetree back. Save the
    whole bundle with `packer="lz4"` (matches the rest of the toolkit).
    Bundles whose JSON hash matches the extract-time baseline are
    skipped — only translator-touched files get repacked.
    """
    if not INLINE_UI_OUT.is_dir():
        print(f"ERROR: {INLINE_UI_OUT} does not exist; run `extract-ui` first.")
        sys.exit(1)
    REPACK_INLINE_UI.mkdir(parents=True, exist_ok=True)
    fps = load_fingerprints()

    src_dir = cfg.bundleassets_dir()
    json_files = sorted(p for p in INLINE_UI_OUT.iterdir()
                        if p.suffix == '.json' and not p.name.startswith('_'))

    repacked = skipped = failed = 0
    t0 = time.time()
    for jp in tqdm(json_files, desc="repack-ui", unit="json"):
        key = f"inline_ui/{jp.name}"
        if not _is_modified(jp, key, fps, force):
            skipped += 1
            continue
        try:
            with open(jp, 'rb') as f:
                payload = json.loads(f.read().decode('utf-8'))
        except Exception as e:
            tqdm.write(f"  ERROR reading {jp.name}: {e}")
            failed += 1
            continue

        bundle_name = payload.get("bundle")
        if not bundle_name:
            tqdm.write(f"  ERROR {jp.name}: missing 'bundle' field")
            failed += 1
            continue
        src = src_dir / bundle_name
        dst = REPACK_INLINE_UI / bundle_name
        if not src.is_file():
            tqdm.write(f"  ERROR {jp.name}: source bundle {src} not found")
            failed += 1
            continue

        try:
            env = UnityPy.load(str(src))
            edits = {e["path_id"]: e["text"] for e in payload.get("entries", [])}
            applied = 0
            for obj in env.objects:
                if obj.path_id not in edits:
                    continue
                tt = obj.read_typetree()
                tt["parameter"]["text"] = edits[obj.path_id]
                obj.save_typetree(tt)
                applied += 1
            if applied != len(edits):
                tqdm.write(f"  WARN {bundle_name}: applied {applied}/{len(edits)} entries")
            with open(dst, 'wb') as f:
                f.write(env.file.save(packer='lz4'))
            repacked += 1
            tqdm.write(f"  {bundle_name} <- {jp.name}  ({applied} edits)")
        except Exception as e:
            tqdm.write(f"  ERROR {bundle_name}: {e}")
            failed += 1

    print(f"\nInline-UI repack done in {time.time() - t0:.1f}s.")
    print(f"  repacked: {repacked}")
    print(f"  skipped (unmodified): {skipped}")
    print(f"  failed: {failed}")
    print(f"  output: {REPACK_INLINE_UI}")
