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

// Read the print-name target of an existing mount-point junction into `out`
// (which must hold at least `out_chars` WCHARs, NUL terminator included).
// The leading NT-namespace prefix `\??\` is stripped if present, so the
// result is a plain Win32 drive-letter path.
//
// Returns TRUE on success. On failure, GetLastError() holds the Win32 code;
// ERROR_NOT_A_REPARSE_POINT means `link` exists but is not a mount point.
BOOL read_junction_target(const wchar_t* link, wchar_t* out, size_t out_chars);

#endif
