/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright 2026 thebcd69 (BCD) */
/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */

/*
 * Reads one request file with BCD Host's own reader (request.c) and prints what it understood, one "key=value" line each
 * (UTF-16 turned back into UTF-8), or "error=N". tests/host/check-epic.py runs it on the files the app's Swift writer
 * (BCDEpicHostFiles.swift, tests/host/epic_launch_checks.swift) produces, so the two halves of the format are held together.
 * Test code only: it prints what a request holds, which the host itself never does.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "request.h"

static void put(const char *key, const uint16_t *w)
{
    printf("%s=", key);
    for (; *w; w++)
    {
        uint32_t c = *w;
        if (c >= 0xD800 && c < 0xDC00 && w[1] >= 0xDC00 && w[1] < 0xE000) { c = 0x10000 + ((c - 0xD800) << 10) + (w[1] - 0xDC00); w++; }
        if (c < 0x80) putchar((int)c);
        else if (c < 0x800) { putchar((int)(0xC0 | (c >> 6))); putchar((int)(0x80 | (c & 0x3F))); }
        else if (c < 0x10000) { putchar((int)(0xE0 | (c >> 12))); putchar((int)(0x80 | ((c >> 6) & 0x3F))); putchar((int)(0x80 | (c & 0x3F))); }
        else { putchar((int)(0xF0 | (c >> 18))); putchar((int)(0x80 | ((c >> 12) & 0x3F))); putchar((int)(0x80 | ((c >> 6) & 0x3F))); putchar((int)(0x80 | (c & 0x3F))); }
    }
    putchar('\n');
}

int main(int argc, char **argv)
{
    static uint8_t data[BCDHOST_MAX_REQUEST + 1];
    static bcd_request q;
    FILE *f;
    size_t n;
    int rc;

    if (argc != 2 || !(f = fopen(argv[1], "rb"))) return 2;
    n = fread(data, 1, sizeof(data), f);
    fclose(f);
    rc = bcd_request_parse(data, n, &q);
    if (rc) { printf("error=%d\n", rc); return 0; }
    printf("flags=%u\n", q.flags);
    put("program", q.program);
    put("cwd", q.cwd);
    put("cmdline", q.cmdline);
    put("ovt", q.ovt);
    return 0;
}
