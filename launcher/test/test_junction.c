// test_junction.c — isolated junction-creation tester.
//
// Usage:  test_junction.exe <link_path> <target_path>

#define WIN32_LEAN_AND_MEAN
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <stdio.h>

#include "junction.h"

int wmain(int argc, wchar_t** argv) {
    if (argc != 3) {
        fwprintf(stderr, L"usage: %s <link> <target>\n", argv[0]);
        return 2;
    }
    if (!create_junction(argv[1], argv[2])) {
        fwprintf(stderr, L"FAILED: GetLastError = %lu\n", GetLastError());
        return 1;
    }
    wprintf(L"OK: junction %s -> %s\n", argv[1], argv[2]);
    return 0;
}
