/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright 2026 thebcd69 (BCD) */
/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */

/*
 * BCD Host: the request a store's start hands the host (docs/research/open-fork-2026-10-18/epic.md
 * section 4.5). One small file the app writes right before a session and the host opens once:
 *
 *   8 bytes   "BCDHOST1"
 *   u32 LE    version (1)
 *   u32 LE    flags: 1 = wait for the whole process tree, 2 = start services.exe first
 *   then tagged fields, each: u16 LE tag, u32 LE length, that many bytes of UTF-8:
 *     1 program (an absolute drive path ending in .exe)         required
 *     2 working folder (an absolute drive path)                 optional (the program's folder)
 *     3 arguments (the command line after the program, already quoted)   optional
 *     4 file to delete when the program is done (C:\ProgramData\BCD\..., ends in .ovt)   optional
 *     5 root the program must be under (an absolute drive path)  optional
 *
 * At most 32 KB in all, no tag twice, no unknown tag, no control character in any value. Pure C
 * with no Windows header: tests/check.c compiles it on the host and fuzzes it. Values are never
 * printed anywhere (they may carry a one-time sign-in code).
 */
#ifndef BCDHOST_REQUEST_H
#define BCDHOST_REQUEST_H

#include <stddef.h>
#include <stdint.h>

#define BCDHOST_VERSION 1u
#define BCDHOST_MAX_REQUEST 32768u
#define BCDHOST_PATH_MAX 520u
/* CreateProcessW takes at most 32767 UTF-16 units with the terminating NUL. */
#define BCDHOST_CMDLINE_MAX 32766u

#define BCDHOST_FLAG_TREE 1u
#define BCDHOST_FLAG_SERVICES 2u
#define BCDHOST_FLAGS_KNOWN (BCDHOST_FLAG_TREE | BCDHOST_FLAG_SERVICES)

enum {
    BCDHOST_TAG_PROGRAM = 1,
    BCDHOST_TAG_CWD = 2,
    BCDHOST_TAG_ARGS = 3,
    BCDHOST_TAG_OVT = 4,
    BCDHOST_TAG_ROOT = 5
};

/* What is wrong with a request: the number goes into the report as error=N. 0 is fine. */
enum {
    BCDHOST_OK = 0,
    BCDHOST_E_SHORT = 1,      /* shorter than the fixed header */
    BCDHOST_E_MAGIC = 2,
    BCDHOST_E_VERSION = 3,
    BCDHOST_E_FLAGS = 4,      /* a flag this host does not know */
    BCDHOST_E_SIZE = 5,       /* over 32 KB */
    BCDHOST_E_TRUNC = 6,      /* a field runs past the end */
    BCDHOST_E_TAG = 7,        /* an unknown tag */
    BCDHOST_E_DUP = 8,        /* a tag twice */
    BCDHOST_E_UTF8 = 9,       /* a value that is not UTF-8 */
    BCDHOST_E_CHAR = 10,      /* a control character in a value */
    BCDHOST_E_MISSING = 11,   /* no program */
    BCDHOST_E_PROGRAM = 12,
    BCDHOST_E_CWD = 13,
    BCDHOST_E_OVT = 14,
    BCDHOST_E_ROOT = 15,
    BCDHOST_E_OUTSIDE = 16,   /* the program is not under the root */
    BCDHOST_E_ARGS = 17       /* the command line is too long */
};

typedef struct {
    uint32_t flags;
    /* UTF-16 (the Windows API's), each with a terminating zero. cwd is the program's folder and ovt is empty when not given. */
    uint16_t program[BCDHOST_PATH_MAX + 1];
    uint16_t cwd[BCDHOST_PATH_MAX + 1];
    uint16_t ovt[BCDHOST_PATH_MAX + 1];
    /* "<program>" and the arguments: what CreateProcessW takes. */
    uint16_t cmdline[BCDHOST_CMDLINE_MAX + 1];
    size_t cmdline_length;
} bcd_request;

/* Reads and checks a request. Returns BCDHOST_OK or what is wrong; `out` is only complete on BCDHOST_OK. */
int bcd_request_parse(const uint8_t *data, size_t size, bcd_request *out);

/* Strict UTF-8 to UTF-16 (no overlong form, no surrogate, nothing above U+10FFFF, no control character).
 * Returns BCDHOST_OK, BCDHOST_E_UTF8, BCDHOST_E_CHAR or BCDHOST_E_ARGS (the output is full); `*units` is the count written (no terminator). */
int bcd_utf8_to_utf16(const uint8_t *in, size_t size, uint16_t *out, size_t capacity, size_t *units);

/* Wipes memory in a way the compiler keeps. */
void bcd_wipe(void *p, size_t size);

#endif
