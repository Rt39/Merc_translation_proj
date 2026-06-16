"""Bundle the LocalLow CDN cache into the game folder.

Once-per-machine pre-shipping step. After running this script, the entire
`<persistent>/AssetBundle/` tree (~15 GB downloaded from the game's CDN)
physically lives at `<game>/AssetBundle/` and ships with the game install.
The launcher then creates a junction from the persistent path back into
the game folder on first run, so Unity Addressables and the patched
GetAsync both transparently land on the bundled cache without any network.

Without this step:
  - `patch_offline.py` alone is not enough; Unity still reads from the
    persistent cache, which is empty on a copied-elsewhere install
  - The launcher creates the junction, but the junction target is empty,
    so the game's first bundle load fails

Workflow:
  1. Play the game normally for long enough to populate the LocalLow cache
     (or, for QA: visit every chapter that the translated build needs).
  2. Apply CRC + offline patches (`patch_crc.py`, `patch_offline.py`).
  3. Run THIS script — copies LocalLow -> game folder. Idempotent: re-running
     skips files that already exist with the same size.
  4. Build and deploy the launcher (see `launcher/README.md`).
  5. Zip the entire game folder and distribute.

Prompts and progress are bilingual; default is 简体中文 (zh). Override:
    uv run bundle_cache.py --lang en
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Iterable

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mercstoria import config as cfg

cfg.enable_utf8_stdout()


# ============================================================================
#                              i18n
# ============================================================================

# Each message has a {key: (zh, en)} pair. Lookup goes through `_t(key, **kw)`
# which substitutes kwargs into the selected language.
_MESSAGES: dict[str, tuple[str, str]] = {
    "title":              ("== 缓存打包：LocalLow → 游戏目录 ==",
                           "== Bundle cache: LocalLow -> game folder =="),
    "source":             ("来源：{path}",
                           "Source: {path}"),
    "dest":               ("目标：{path}",
                           "Dest:   {path}"),
    "source_missing":     ("错误：来源目录不存在。\n请先正常启动游戏，让 Unity 把 CDN 资源下载到 LocalLow。",
                           "ERROR: source directory does not exist.\nLaunch the game normally first so Unity downloads the CDN bundles into LocalLow."),
    "dest_missing":       ("错误：游戏安装目录不存在。",
                           "ERROR: game install directory does not exist."),
    "source_is_link":     ("错误：来源 AssetBundle 已是 reparse point（junction / symlink）。\n说明此机器上 launcher 已运行过，缓存已经在游戏目录里。\n如需重新打包，先删掉那个 junction，让游戏重新下载到 LocalLow。",
                           "ERROR: source AssetBundle is already a reparse point (junction / symlink).\nThe launcher has already run on this machine — the cache is already in the game folder.\nTo re-bundle, delete that junction first and let the game re-download to LocalLow."),
    "scanning":           ("正在扫描来源... ",
                           "Scanning source... "),
    "scan_result":        ("找到 {count:,} 个文件，共 {bytes_gb:.2f} GB",
                           "found {count:,} files, {bytes_gb:.2f} GB"),
    "confirm":            ("继续？[Y/n]：",
                           "Continue? [Y/n]: "),
    "aborted":            ("用户取消。",
                           "Aborted by user."),
    "progress":           ("[{done:>6,} / {total:,}]  {pct:5.1f}%  {gb:6.2f} GB  {speed_mb:6.1f} MB/s  {rel}",
                           "[{done:>6,} / {total:,}]  {pct:5.1f}%  {gb:6.2f} GB  {speed_mb:6.1f} MB/s  {rel}"),
    "skipped":            ("跳过 {count} 个已存在的文件（大小一致）。",
                           "Skipped {count} files that already exist with the same size."),
    "done":               ("\n完成：复制 {copied:,} 个文件，{bytes_gb:.2f} GB，用时 {seconds:.1f} 秒。",
                           "\nDone: {copied:,} files, {bytes_gb:.2f} GB, in {seconds:.1f} s."),
    "next_step":          ("下一步：构建并部署 launcher，详见 launcher/README.md。",
                           "Next step: build and deploy the launcher — see launcher/README.md."),
}


def _t(key: str, lang: str, **kw) -> str:
    """Return the localised message for `key` in the chosen language."""
    zh, en = _MESSAGES[key]
    template = zh if lang == "zh" else en
    return template.format(**kw)


# ============================================================================
#                              Scan + copy
# ============================================================================

def _walk_files(root: Path) -> Iterable[Path]:
    """Yield every file under `root` recursively as absolute Paths.

    Symlinks and reparse points encountered partway through the walk are
    followed (cache layout has plain files only — if a reparse point appears
    something is already wrong upstream and the caller's reparse check has
    failed).
    """
    for cur, _dirs, files in os.walk(root):
        for f in files:
            yield Path(cur) / f


def _scan(src: Path) -> tuple[int, int]:
    """Walk `src` and return (file_count, total_bytes)."""
    count = 0
    total = 0
    for f in _walk_files(src):
        try:
            total += f.stat().st_size
            count += 1
        except OSError:
            # Vanished mid-scan — Unity might have just deleted a temp file.
            # Best-effort: ignore and continue.
            pass
    return count, total


def _copy_tree(src: Path, dst: Path, total_files: int, total_bytes: int,
               lang: str) -> tuple[int, int, int]:
    """Mirror `src` to `dst`. Returns (copied_files, skipped_files, copied_bytes).

    Skips a file when the destination already exists with the same byte size
    (cheap idempotency: a partial / corrupt copy would have a different size).
    The progress line is rewritten in place via \\r so the terminal shows one
    moving status row.
    """
    copied_files = 0
    skipped = 0
    copied_bytes = 0
    done_count = 0
    start = time.monotonic()

    for src_file in _walk_files(src):
        rel = src_file.relative_to(src)
        dst_file = dst / rel

        try:
            src_size = src_file.stat().st_size
        except OSError:
            continue

        done_count += 1

        # Idempotency: skip files whose destination already matches in size.
        if dst_file.exists():
            try:
                if dst_file.stat().st_size == src_size:
                    skipped += 1
                    continue
            except OSError:
                pass

        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        copied_files += 1
        copied_bytes += src_size

        # Throttle progress output to ~5 Hz so we don't spam the terminal.
        elapsed = time.monotonic() - start
        if done_count % 100 == 0 or done_count == total_files:
            speed_mb = (copied_bytes / 1_048_576) / elapsed if elapsed > 0 else 0.0
            pct = 100.0 * done_count / max(total_files, 1)
            rel_short = str(rel)
            if len(rel_short) > 60:
                rel_short = "…" + rel_short[-59:]
            line = _t("progress", lang,
                      done=done_count, total=total_files, pct=pct,
                      gb=copied_bytes / 1_073_741_824,
                      speed_mb=speed_mb, rel=rel_short)
            # \r so the next print overwrites this one. Use a trailing space-
            # pad so leftover characters from a longer previous filename don't
            # bleed through.
            print(f"\r{line:<140}", end="", flush=True)

    print()  # finish the \r-ed progress line
    return copied_files, skipped, copied_bytes


# ============================================================================
#                                Main
# ============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bundle the LocalLow CDN cache into the game folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--lang", choices=("zh", "en"), default="zh",
                    help="Prompt language (default: zh / 简体中文).")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="Don't prompt for confirmation.")
    ap.add_argument("--source", type=Path, default=None,
                    help="Override source AssetBundle path "
                         "(default: %%LocalLow%%/.../AssetBundle).")
    ap.add_argument("--dest", type=Path, default=None,
                    help="Override destination game folder "
                         "(default: <game>/AssetBundle).")
    args = ap.parse_args()

    src = args.source or cfg.persist_assetbundle()
    dst = args.dest   or (cfg.game_dir() / "AssetBundle")

    print(_t("title", args.lang))
    print(_t("source", args.lang, path=src))
    print(_t("dest",   args.lang, path=dst))
    print()

    if not src.is_dir():
        print(_t("source_missing", args.lang))
        return 1
    # If the source is itself a junction, the user has already bundled.
    try:
        if src.is_symlink() or (src.exists() and (src.lstat().st_file_attributes & 0x400)):
            # 0x400 = FILE_ATTRIBUTE_REPARSE_POINT
            print(_t("source_is_link", args.lang))
            return 1
    except (AttributeError, OSError):
        # st_file_attributes is Windows-only; on other platforms is_symlink
        # is enough.
        pass
    if not dst.parent.is_dir():
        print(_t("dest_missing", args.lang))
        return 1

    print(_t("scanning", args.lang), end="", flush=True)
    n_files, n_bytes = _scan(src)
    print(_t("scan_result", args.lang,
             count=n_files, bytes_gb=n_bytes / 1_073_741_824))
    print()

    if not args.yes:
        resp = input(_t("confirm", args.lang)).strip().lower()
        if resp and resp not in ("y", "yes", "是", "是的"):
            print(_t("aborted", args.lang))
            return 0

    start = time.monotonic()
    copied, skipped, copied_bytes = _copy_tree(src, dst, n_files, n_bytes, args.lang)
    elapsed = time.monotonic() - start

    if skipped:
        print(_t("skipped", args.lang, count=skipped))
    print(_t("done", args.lang,
             copied=copied,
             bytes_gb=copied_bytes / 1_073_741_824,
             seconds=elapsed))
    print(_t("next_step", args.lang))
    return 0


if __name__ == "__main__":
    sys.exit(main())
