// junction.c — NTFS mount-point junction creation via FSCTL_SET_REPARSE_POINT.
//
// REPARSE_DATA_BUFFER lives in <ntifs.h> which is DDK-only — declare the
// mount-point variant locally.

#include "junction.h"
#include <winioctl.h>
#include <wchar.h>
#include <string.h>

#ifndef IO_REPARSE_TAG_MOUNT_POINT
#define IO_REPARSE_TAG_MOUNT_POINT 0xA0000003L
#endif

typedef struct _REPARSE_MOUNT_POINT {
    DWORD  ReparseTag;
    WORD   ReparseDataLength;
    WORD   Reserved;
    WORD   SubstituteNameOffset;
    WORD   SubstituteNameLength;
    WORD   PrintNameOffset;
    WORD   PrintNameLength;
    WCHAR  PathBuffer[1];
} REPARSE_MOUNT_POINT;

#define JUNCTION_BUF_CHARS 1024

BOOL create_junction(const wchar_t* link, const wchar_t* target) {
    // 1) Empty directory at `link` (must exist, must be empty).
    if (!CreateDirectoryW(link, NULL)) {
        if (GetLastError() != ERROR_ALREADY_EXISTS) return FALSE;
    }

    // 2) Open with FILE_FLAG_OPEN_REPARSE_POINT so we can tag it.
    HANDLE h = CreateFileW(link, GENERIC_WRITE, 0, NULL, OPEN_EXISTING,
                           FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                           NULL);
    if (h == INVALID_HANDLE_VALUE) return FALSE;

    // 3) Build the mount-point reparse buffer.
    //    SubstituteName is NT-namespace: \??\<absolute path>
    //    PrintName is the user-facing form (no \??\ prefix).
    wchar_t nt_target[JUNCTION_BUF_CHARS];
    if (swprintf_s(nt_target, JUNCTION_BUF_CHARS, L"\\??\\%s", target) < 0) {
        CloseHandle(h);
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return FALSE;
    }

    size_t nt_chars    = wcslen(nt_target);
    size_t print_chars = wcslen(target);
    size_t nt_bytes    = nt_chars    * sizeof(WCHAR);
    size_t print_bytes = print_chars * sizeof(WCHAR);

    // ReparseDataLength counts only the MountPointReparseBuffer fields
    // (8 header bytes + path data including two NUL terminators).
    size_t data_len = 8 + nt_bytes + sizeof(WCHAR) + print_bytes + sizeof(WCHAR);
    size_t total    = 8 + data_len;  // + outer ReparseTag/Length/Reserved

    BYTE raw[2048];
    if (total > sizeof(raw)) {
        CloseHandle(h);
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return FALSE;
    }
    ZeroMemory(raw, sizeof(raw));

    REPARSE_MOUNT_POINT* rb = (REPARSE_MOUNT_POINT*)raw;
    rb->ReparseTag           = IO_REPARSE_TAG_MOUNT_POINT;
    rb->Reserved             = 0;
    rb->ReparseDataLength    = (WORD)data_len;
    rb->SubstituteNameOffset = 0;
    rb->SubstituteNameLength = (WORD)nt_bytes;
    rb->PrintNameOffset      = (WORD)(nt_bytes + sizeof(WCHAR));
    rb->PrintNameLength      = (WORD)print_bytes;

    WCHAR* pb = rb->PathBuffer;
    memcpy(pb, nt_target, nt_bytes);
    pb[nt_chars] = 0;
    memcpy((BYTE*)pb + nt_bytes + sizeof(WCHAR), target, print_bytes);
    *(WCHAR*)((BYTE*)pb + nt_bytes + sizeof(WCHAR) + print_bytes) = 0;

    DWORD bytes_returned = 0;
    BOOL ok = DeviceIoControl(h, FSCTL_SET_REPARSE_POINT,
                              rb, (DWORD)total,
                              NULL, 0, &bytes_returned, NULL);
    DWORD err = GetLastError();
    CloseHandle(h);

    if (!ok) {
        RemoveDirectoryW(link);   // clean up the empty dir we made
        SetLastError(err);
        return FALSE;
    }
    return TRUE;
}
