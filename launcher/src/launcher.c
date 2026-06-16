// launcher.c — Merc Storia self-contained launcher.
//
// Drop-in companion to メルストM.exe. On double-click:
//   1. Resolve game folder = dirname(GetModuleFileNameW).
//   2. Compute <persistent> = %USERPROFILE%\AppData\LocalLow\jp_co_happyelements\メルストM
//      and <persist_ab>     = <persistent>\AssetBundle.
//   3. If <persist_ab> already a reparse point -> trust it.
//      Otherwise create the mount-point junction <persist_ab> -> <game>\AssetBundle.
//      Existing non-junction directory is moved aside to *.pre_setup{N}.
//   4. CreateProcessW the original game exe (メルストM.exe).
//
// Distribution layout (no renaming required):
//
//     <install>/
//        メルストM.exe          (original Unity player — untouched)
//        メルストM_Data/        (original data folder — untouched)
//        メルストM_chs.exe      (THIS launcher — drop-in next to the original)
//        GameAssembly.dll       (CRC + offline patched)
//        AssetBundle/           (bundled CDN cache, ~15 GB)
//        ...
//
// Users double-click メルストM_chs.exe; the launcher installs the junction
// and chains into メルストM.exe. The original exe is untouched so a Steam
// "Verify integrity" still works on the unmodified files.

#define WIN32_LEAN_AND_MEAN
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <wchar.h>

#include "junction.h"

#define APP_EXE       L"メルストM.exe"
#define ASSETBUNDLE   L"AssetBundle"
#define PERSIST_REL   L"AppData\\LocalLow\\jp_co_happyelements\\メルストM"

#define BUF_CHARS 1024

static void die(const wchar_t* msg, DWORD code) {
    wchar_t full[BUF_CHARS];
    if (code) swprintf_s(full, BUF_CHARS, L"%s\n\nLastError = %lu", msg, code);
    else      wcscpy_s(full, BUF_CHARS, msg);
    MessageBoxW(NULL, full, L"メルクストーリア — Launcher", MB_OK | MB_ICONERROR);
    ExitProcess(1);
}

static BOOL dir_exists(const wchar_t* path) {
    DWORD a = GetFileAttributesW(path);
    return (a != INVALID_FILE_ATTRIBUTES) && (a & FILE_ATTRIBUTE_DIRECTORY);
}

static BOOL is_reparse(const wchar_t* path) {
    DWORD a = GetFileAttributesW(path);
    return (a != INVALID_FILE_ATTRIBUTES) && (a & FILE_ATTRIBUTE_REPARSE_POINT);
}

// Walk the path and CreateDirectoryW each missing component.
static BOOL ensure_dir(const wchar_t* path) {
    wchar_t buf[BUF_CHARS];
    wcscpy_s(buf, BUF_CHARS, path);
    wchar_t* p = buf;
    if (wcslen(buf) >= 3 && buf[1] == L':' && buf[2] == L'\\') p = buf + 3;
    for (; *p; p++) {
        if (*p == L'\\') {
            *p = 0;
            if (!dir_exists(buf)) {
                if (!CreateDirectoryW(buf, NULL) && GetLastError() != ERROR_ALREADY_EXISTS)
                    return FALSE;
            }
            *p = L'\\';
        }
    }
    if (!dir_exists(buf)) {
        if (!CreateDirectoryW(buf, NULL) && GetLastError() != ERROR_ALREADY_EXISTS)
            return FALSE;
    }
    return TRUE;
}

int WINAPI wWinMain(HINSTANCE hi, HINSTANCE hp, LPWSTR cmdLine, int show) {
    (void)hi; (void)hp; (void)cmdLine; (void)show;

    // 1. Self path & game folder.
    wchar_t self[BUF_CHARS];
    if (!GetModuleFileNameW(NULL, self, BUF_CHARS))
        die(L"GetModuleFileNameW failed", GetLastError());

    wchar_t game_folder[BUF_CHARS];
    wcscpy_s(game_folder, BUF_CHARS, self);
    wchar_t* last = wcsrchr(game_folder, L'\\');
    if (!last) die(L"could not parse own module path", 0);
    *last = 0;

    wchar_t game_ab[BUF_CHARS];
    swprintf_s(game_ab, BUF_CHARS, L"%s\\%s", game_folder, ASSETBUNDLE);

    if (!dir_exists(game_ab))
        die(L"AssetBundle/ not found in game folder.\n"
            L"The launcher must sit next to the bundled AssetBundle directory.", 0);

    // 2. Persistent path.
    wchar_t userprofile[MAX_PATH];
    if (!GetEnvironmentVariableW(L"USERPROFILE", userprofile, MAX_PATH))
        die(L"USERPROFILE environment variable is not set", GetLastError());

    wchar_t persist_dir[BUF_CHARS];
    swprintf_s(persist_dir, BUF_CHARS, L"%s\\%s", userprofile, PERSIST_REL);

    wchar_t persist_ab[BUF_CHARS];
    swprintf_s(persist_ab, BUF_CHARS, L"%s\\%s", persist_dir, ASSETBUNDLE);

    // 3. Decide what to do at the junction location.
    if (dir_exists(persist_ab)) {
        if (!is_reparse(persist_ab)) {
            // Move the real directory aside before installing our junction.
            wchar_t bak[BUF_CHARS];
            int i = 0;
            for (;;) {
                if (i == 0)
                    swprintf_s(bak, BUF_CHARS, L"%s.pre_setup", persist_ab);
                else
                    swprintf_s(bak, BUF_CHARS, L"%s.pre_setup_%d", persist_ab, i);
                if (!dir_exists(bak)) break;
                if (++i >= 100)
                    die(L"too many .pre_setup_N backups already exist; clean them up first", 0);
            }
            if (!MoveFileW(persist_ab, bak))
                die(L"could not move existing AssetBundle aside to .pre_setup", GetLastError());
            if (!create_junction(persist_ab, game_ab))
                die(L"create_junction failed after move-aside", GetLastError());
        }
        // else: already a reparse point — trust it. (Re-running is a no-op.)
    } else {
        if (!ensure_dir(persist_dir))
            die(L"could not create the persistent-data parent directory", GetLastError());
        if (!create_junction(persist_ab, game_ab))
            die(L"create_junction failed", GetLastError());
    }

    // 4. Launch the real game exe.
    wchar_t real_exe[BUF_CHARS];
    swprintf_s(real_exe, BUF_CHARS, L"%s\\%s", game_folder, APP_EXE);

    if (GetFileAttributesW(real_exe) == INVALID_FILE_ATTRIBUTES)
        die(L"" APP_EXE L" not found next to the launcher.\n"
            L"This launcher must sit in the same folder as the original game exe.", 0);

    wchar_t cmd[BUF_CHARS];
    swprintf_s(cmd, BUF_CHARS, L"\"%s\"", real_exe);

    STARTUPINFOW si = {0}; si.cb = sizeof(si);
    PROCESS_INFORMATION pi = {0};

    if (!CreateProcessW(real_exe, cmd, NULL, NULL, FALSE,
                        0, NULL, game_folder, &si, &pi))
        die(L"CreateProcessW failed launching " APP_EXE, GetLastError());

    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return 0;
}
