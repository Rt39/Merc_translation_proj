// junction.h — NTFS mount-point junction creation.
//
// Shared between launcher.c and the standalone test_junction.exe.

#ifndef MERCSTORIA_LAUNCHER_JUNCTION_H
#define MERCSTORIA_LAUNCHER_JUNCTION_H

#define WIN32_LEAN_AND_MEAN
#define UNICODE
#define _UNICODE
#include <windows.h>

// Create a directory junction at `link` pointing at `target`.
// `target` must be an absolute path (drive-letter form).
//
// Returns TRUE on success. On failure, GetLastError() holds the Win32 code
// from whichever step failed and the (empty) link directory is removed so
// the caller can retry cleanly.
BOOL create_junction(const wchar_t* link, const wchar_t* target);

#endif
