"""Central configuration for the Merc Storia translation toolkit.

Single source of truth for:
  * Game install path (auto-detected or overridden via MERCSTORIA_GAME_DIR)
  * Persistent-data path (cache root in %LocalLow%)
  * Crypto parameters (AES-256-CBC, PBKDF2 salt/password/iterations)
  * Patch-site RVAs and file offsets
  * Constants reused across patches and extractors

Everything that any script needs to know about the game's on-disk layout
should come from here. No script should ever embed `r"E:\\SteamLibrary\\..."`
inline.

Override the game folder by setting MERCSTORIA_GAME_DIR before running any
script:

    set MERCSTORIA_GAME_DIR=D:\\path\\to\\install
    uv run patch_crc.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


# ============================================================================
#                              Path discovery
# ============================================================================

GAME_FOLDER_NAME = "メルクストーリア - 癒術士と心の旋律 -"
APP_EXE_NAME     = "メルストM.exe"
APP_DATA_NAME    = "メルストM_Data"
# Translated-build launcher sits alongside the original exe; original is
# left untouched so a Steam "Verify integrity" still works.
APP_EXE_CHS      = "メルストM_chs.exe"
# Legacy (rename-based) deployment — kept for backwards-compatible discovery
# of installs that were patched with the older orchestrator.
APP_EXE_RENAMED  = "メルストM_app.exe"
APP_DATA_RENAMED = "メルストM_app_Data"

# Persistent-data (Unity's `Application.persistentDataPath`) lives here. The
# directory name uses underscores (jp_co_happyelements) while Player.log uses
# dots (jp.co.happyelements) — Unity treats `.` and `_` differently between
# CompanyName-as-folder and Bundle ID. The two paths are siblings.
PERSIST_COMPANY = "jp_co_happyelements"
PERSIST_PRODUCT = "メルストM"
LOG_COMPANY     = "jp.co.happyelements"

_STEAM_LIBRARY_CANDIDATES = [
    Path("C:/Program Files (x86)/Steam"),
    Path("C:/Program Files/Steam"),
    Path("D:/SteamLibrary"),
    Path("E:/SteamLibrary"),
    Path("F:/SteamLibrary"),
    Path("G:/SteamLibrary"),
]


def _find_game_folder() -> Path | None:
    """Search known Steam library roots for the game folder."""
    for root in _STEAM_LIBRARY_CANDIDATES:
        candidate = root / "steamapps" / "common" / GAME_FOLDER_NAME
        if candidate.is_dir():
            return candidate
    return None


def game_dir() -> Path:
    """Return the game install directory.

    Resolution order:
      1. `$MERCSTORIA_GAME_DIR` environment variable
      2. Known Steam library locations
      3. Raise RuntimeError with a helpful message

    Callers that want optional behavior should check `os.environ` directly
    or catch RuntimeError.
    """
    env = os.environ.get("MERCSTORIA_GAME_DIR")
    if env:
        p = Path(env)
        if not p.is_dir():
            raise RuntimeError(f"MERCSTORIA_GAME_DIR={env!r} does not exist or is not a directory")
        return p

    found = _find_game_folder()
    if found is not None:
        return found

    raise RuntimeError(
        f"Could not locate the game install. Set MERCSTORIA_GAME_DIR to the "
        f"folder containing {APP_EXE_NAME}, or install the game under a "
        f"Steam library at one of: {[str(p) for p in _STEAM_LIBRARY_CANDIDATES]}"
    )


def dll_path() -> Path:
    """Return absolute path to GameAssembly.dll."""
    return game_dir() / "GameAssembly.dll"


def dll_backup_path() -> Path:
    return game_dir() / "GameAssembly.dll.bak"


def app_exe_path() -> Path:
    """Return the player exe path (handles both pristine and launcher-deployed layouts)."""
    g = game_dir()
    for name in (APP_EXE_RENAMED, APP_EXE_NAME):
        p = g / name
        if p.is_file():
            return p
    raise RuntimeError(f"Neither {APP_EXE_NAME} nor {APP_EXE_RENAMED} found in {g}")


def app_data_dir() -> Path:
    """Return the player _Data folder (handles both layouts)."""
    g = game_dir()
    for name in (APP_DATA_RENAMED, APP_DATA_NAME):
        p = g / name
        if p.is_dir():
            return p
    raise RuntimeError(f"Neither {APP_DATA_NAME} nor {APP_DATA_RENAMED} found in {g}")


def streaming_assets_dir() -> Path:
    return app_data_dir() / "StreamingAssets" / "aa" / "StandaloneWindows64"


def resources_assets_path() -> Path:
    return app_data_dir() / "resources.assets"


def resources_ress_path() -> Path:
    return app_data_dir() / "resources.assets.resS"


def persist_root() -> Path:
    """Unity's persistentDataPath: %USERPROFILE%/AppData/LocalLow/<company>/<product>."""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return home / "AppData" / "LocalLow" / PERSIST_COMPANY / PERSIST_PRODUCT


def persist_assetbundle() -> Path:
    return persist_root() / "AssetBundle"


def cache_root() -> Path:
    """Live CDN cache: <persistentDataPath>/AssetBundle/StandaloneWindows64."""
    return persist_assetbundle() / "StandaloneWindows64"


def player_log_path() -> Path:
    """Unity Player.log — note `jp.co.happyelements` with DOTS (Bundle ID,
    not CompanyName-as-folder)."""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return home / "AppData" / "LocalLow" / LOG_COMPANY / PERSIST_PRODUCT / "Player.log"


# ============================================================================
#                              Crypto parameters
# ============================================================================

# AES-256-CBC, PBKDF2-HMAC-SHA256(password="2147483647", salt="-2147483648",
# iterations=1024, dklen=32). IV is the first 16 bytes of each ciphertext.
AES_PASSWORD   = b"2147483647"
AES_SALT       = b"-2147483648"
AES_ITERATIONS = 1024
AES_DKLEN      = 32


def derive_aes_key() -> bytes:
    """Return the constant AES key. Imports happen lazily so toolkit scripts
    that don't decrypt anything don't pay the cryptography-import cost."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=AES_DKLEN,
        salt=AES_SALT,
        iterations=AES_ITERATIONS,
    )
    return kdf.derive(AES_PASSWORD)


# ============================================================================
#                          CDN / Addressables constants
# ============================================================================

CDN_HOST       = "https://assets.mercstoria-memorial.hekk.org/"
CDN_PREFIX_LEN = len(CDN_HOST)


# ============================================================================
#                          Patch-site RVAs (GameAssembly.dll)
# ============================================================================

# CRC bypass — file offsets (not RVAs). Discovered via patch_crc3.py.
CRC_PATCHES = [
    # (name,                                 file_offset, original_bytes,                 patched_bytes)
    ("Site 1 (cache CRC load)",              0x280ABE8,   bytes.fromhex("8B5630"),        bytes.fromhex("31D290")),
    ("Site 2 (download CRC load)",           0x280C648,   bytes.fromhex("418B5718"),      bytes.fromhex("31D29090")),
    ("Site 3 (hash compare CRC reg)",        0x300E040,   bytes.fromhex("8BD5"),          bytes.fromhex("31D2")),
    ("Site 4 (hash compare CRC load)",       0x300EFB0,   bytes.fromhex("8B5018"),        bytes.fromhex("31D290")),
]

# Offline-mode RVAs (resolved to file offsets at runtime via the PE section table).
RVA_STEAM_APP_INIT       = 0x2828740
RVA_IMPL_INIT            = 0x28283D0
RVA_IMPL_GETLANG         = 0x28282C0
RVA_IMPL_GETROOT         = 0x28282D0
RVA_STUB_GETLANG         = 0x28280C0
RVA_STUB_GETROOT         = 0x28280F0

RVA_YAHH_GET_SKIP        = 0x6BF200
RVA_NCS_GET_SKIP         = 0x6B1170
RVA_CTOR_HTTP2ONLY_CALL  = 0x27FA4F4

RVA_GETASYNC_5ARG        = 0x27FA120

RVA_INDEXOF_CHAR         = 0x245FBE0
RVA_SUBSTRING_1          = 0x2464640
RVA_SUBSTRING_2          = 0x2464650
RVA_PATH_COMBINE         = 0x25DBE00
RVA_READ_ALL_BYTES       = 0x25C2BE0
RVA_PERSISTENT_PATH      = 0x3131000


# ============================================================================
#                           Font-swap constants
# ============================================================================

# 4096x4096 Alpha8 SDF atlas (16 MB).
ATLAS_LEN              = 16_777_216
RESS_ATLAS_OFFSET      = 8_690_576       # offset of RocknRollStd Atlas in resources.assets.resS
BUNDLE_RESS_STD_OFFSET = 65_536          # RocknRollStd slot inside bundle's archive .resS
BUNDLE_RESS_ONE_OFFSET = 16_842_752      # RocknRollOne slot
BUNDLE_FONT_PATHID     = 6189425675716077201
BUNDLE_RESS_CAB        = "CAB-76c9bdeb5d9d44abf988d53a1128302c.resS"
RESOURCES_HIDDEN_FONT_PID = 27
FONT_BUNDLE_NAME       = "84ece16f121defbfc5b83acb86f5870c.bundle"


def font_bundle_path() -> Path:
    return streaming_assets_dir() / FONT_BUNDLE_NAME


# ============================================================================
#                         StreamingAssets cache subpaths
# ============================================================================

STORY_MASTERDATA_SUBDIR = "StoryMasterData"
MASTERDATA_SUBDIR       = "MasterData"
BUNDLEASSETS_SUBDIR     = "BundleAssets"


def story_masterdata_dir() -> Path:
    """Live story bundles: <persistent>/AssetBundle/StandaloneWindows64/StoryMasterData."""
    return cache_root() / STORY_MASTERDATA_SUBDIR


def masterdata_dir() -> Path:
    """Live MasterData bundles: <persistent>/AssetBundle/StandaloneWindows64/MasterData."""
    return cache_root() / MASTERDATA_SUBDIR


def bundleassets_dir() -> Path:
    """Live BundleAssets bundles (cinematic Timeline assets, inline UI text):
    <persistent>/AssetBundle/StandaloneWindows64/BundleAssets."""
    return cache_root() / BUNDLEASSETS_SUBDIR


# ============================================================================
#                              PE helpers
# ============================================================================

def parse_pe_sections(dll_bytes: bytes):
    """Return (image_base, sections=[(virtual_addr, virtual_size, raw_off, raw_size), ...]).

    Used by every patch script to convert between file offsets and RVAs.
    """
    import struct
    pe_off = struct.unpack_from("<I", dll_bytes, 0x3C)[0]
    image_base = struct.unpack_from("<Q", dll_bytes, pe_off + 0x30)[0]
    num_sections = struct.unpack_from("<H", dll_bytes, pe_off + 6)[0]
    so = pe_off + 0x18 + struct.unpack_from("<H", dll_bytes, pe_off + 0x14)[0]
    sections = []
    for i in range(num_sections):
        s = so + i * 40
        vaddr = struct.unpack_from("<I", dll_bytes, s + 12)[0]
        vsize = struct.unpack_from("<I", dll_bytes, s + 8)[0]
        roff  = struct.unpack_from("<I", dll_bytes, s + 20)[0]
        rsize = struct.unpack_from("<I", dll_bytes, s + 16)[0]
        sections.append((vaddr, vsize, roff, rsize))
    return image_base, sections


def rva_to_file_offset(rva: int, sections) -> int | None:
    for va, vs, ro, _rs in sections:
        if va <= rva < va + vs:
            return ro + (rva - va)
    return None


def file_offset_to_rva(foff: int, sections) -> int:
    for va, _vs, ro, rs in sections:
        if ro <= foff < ro + rs:
            return foff - ro + va
    return foff  # fall back for display only


# ============================================================================
#                            UTF-8 stdout
# ============================================================================

def enable_utf8_stdout():
    """Reconfigure stdout for UTF-8 so Japanese print() works under Windows
    consoles that default to CP932 / CP65001 surprise modes."""
    if sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    # Quick "did I configure this right?" diagnostic.
    enable_utf8_stdout()
    print("=== Merc Storia config ===")
    try:
        print(f"  game_dir():            {game_dir()}")
        print(f"  dll_path():            {dll_path()}")
        print(f"  app_exe_path():        {app_exe_path()}")
        print(f"  app_data_dir():        {app_data_dir()}")
        print(f"  streaming_assets_dir: {streaming_assets_dir()}")
        print(f"  persist_root():        {persist_root()}")
        print(f"  cache_root():          {cache_root()}")
        print(f"  player_log_path():     {player_log_path()}")
    except RuntimeError as e:
        print(f"  ERROR: {e}")
        sys.exit(1)
