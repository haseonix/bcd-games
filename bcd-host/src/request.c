/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright 2026 thebcd69 (BCD) */
/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */

/* The request's reader. See request.h for the format. Pure C, no library calls. */

#include "request.h"

void bcd_wipe(void *p, size_t size)
{
    volatile uint8_t *b = (volatile uint8_t *)p;
    size_t i;
    for (i = 0; i < size; i++) b[i] = 0;
}

static uint32_t u32le(const uint8_t *p) { return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }
static uint32_t u16le(const uint8_t *p) { return (uint32_t)p[0] | ((uint32_t)p[1] << 8); }

int bcd_utf8_to_utf16(const uint8_t *in, size_t size, uint16_t *out, size_t capacity, size_t *units)
{
    size_t i = 0, n = 0;

    while (i < size)
    {
        uint32_t c = in[i];
        size_t need = 0;
        uint32_t min = 0;

        if (c < 0x80) { need = 0; }
        else if (c >= 0xC2 && c <= 0xDF) { need = 1; c &= 0x1F; min = 0x80; }
        else if (c >= 0xE0 && c <= 0xEF) { need = 2; c &= 0x0F; min = 0x800; }
        else if (c >= 0xF0 && c <= 0xF4) { need = 3; c &= 0x07; min = 0x10000; }
        else return BCDHOST_E_UTF8;
        if (need > size - i - 1) return BCDHOST_E_UTF8;
        for (size_t k = 1; k <= need; k++)
        {
            if ((in[i + k] & 0xC0) != 0x80) return BCDHOST_E_UTF8;
            c = (c << 6) | (in[i + k] & 0x3F);
        }
        i += need + 1;
        if (need && c < min) return BCDHOST_E_UTF8;
        if (c > 0x10FFFF || (c >= 0xD800 && c <= 0xDFFF)) return BCDHOST_E_UTF8;
        if (c < 0x20 || c == 0x7F) return BCDHOST_E_CHAR;
        if (c < 0x10000)
        {
            if (n + 1 > capacity) return BCDHOST_E_ARGS;
            out[n++] = (uint16_t)c;
        }
        else
        {
            if (n + 2 > capacity) return BCDHOST_E_ARGS;
            c -= 0x10000;
            out[n++] = (uint16_t)(0xD800 + (c >> 10));
            out[n++] = (uint16_t)(0xDC00 + (c & 0x3FF));
        }
    }
    *units = n;
    return BCDHOST_OK;
}

static uint16_t fold(uint16_t c) { return (c >= 'A' && c <= 'Z') ? (uint16_t)(c + 32) : c; }

/* `s` starts with the ASCII `prefix`, case-insensitively. */
static int starts_with(const uint16_t *s, size_t n, const char *prefix)
{
    size_t i;
    for (i = 0; prefix[i]; i++)
        if (i >= n || fold(s[i]) != fold((uint16_t)(unsigned char)prefix[i])) return 0;
    return 1;
}

static int ends_with(const uint16_t *s, size_t n, const char *suffix)
{
    size_t len = 0, i;
    while (suffix[len]) len++;
    if (n < len) return 0;
    for (i = 0; i < len; i++)
        if (fold(s[n - len + i]) != fold((uint16_t)(unsigned char)suffix[i])) return 0;
    return 1;
}

/* An absolute drive path: X:\ then names, no "." or ".." name, no empty name, none of the characters Windows
 * forbids in a name, no second colon, no trailing backslash (except the drive's root "X:\"). */
static int path_ok(const uint16_t *s, size_t n)
{
    size_t i, start;

    if (n < 3 || n > BCDHOST_PATH_MAX) return 0;
    if (!((s[0] >= 'A' && s[0] <= 'Z') || (s[0] >= 'a' && s[0] <= 'z')) || s[1] != ':' || s[2] != '\\') return 0;
    if (n == 3) return 1;
    for (i = 3; i < n; i++)
    {
        uint16_t c = s[i];
        if (c < 0x20 || c == '/' || c == '*' || c == '?' || c == '"' || c == '<' || c == '>' || c == '|' || c == ':') return 0;
    }
    if (s[n - 1] == '\\') return 0;
    for (start = 3, i = 3; i <= n; i++)
    {
        if (i == n || s[i] == '\\')
        {
            size_t len = i - start, dots = 0, k;
            if (len == 0) return 0;
            for (k = start; k < i; k++) if (s[k] == '.') dots++;
            if (dots == len) return 0;                 /* ".", ".." and runs of dots */
            if (s[i - 1] == ' ' || s[i - 1] == '.') return 0;   /* Windows drops these: another name than it looks */
            start = i + 1;
        }
    }
    return 1;
}

static int folder_of(const uint16_t *program, size_t n, uint16_t *out)
{
    size_t i = n;
    while (i > 3 && program[i - 1] != '\\') i--;
    /* program is "X:\name.exe": the folder is "X:\" */
    size_t len = i > 3 ? i - 1 : 3;
    for (size_t k = 0; k < len; k++) out[k] = program[k];
    out[len] = 0;
    return (int)len;
}

int bcd_request_parse(const uint8_t *data, size_t size, bcd_request *out)
{
    static const char magic[8] = { 'B', 'C', 'D', 'H', 'O', 'S', 'T', '1' };
    size_t pos = 16, i;
    const uint8_t *value[BCDHOST_TAG_ROOT + 1];
    size_t length[BCDHOST_TAG_ROOT + 1];
    int have[BCDHOST_TAG_ROOT + 1];
    uint16_t root[BCDHOST_PATH_MAX + 1];
    size_t program_n = 0, cwd_n = 0, ovt_n = 0, root_n = 0, args_n = 0, n;
    int rc;

    if (size > BCDHOST_MAX_REQUEST) return BCDHOST_E_SIZE;
    if (size < 16) return BCDHOST_E_SHORT;
    for (i = 0; i < 8; i++) if (data[i] != (uint8_t)magic[i]) return BCDHOST_E_MAGIC;
    if (u32le(data + 8) != BCDHOST_VERSION) return BCDHOST_E_VERSION;
    out->flags = u32le(data + 12);
    if (out->flags & ~BCDHOST_FLAGS_KNOWN) return BCDHOST_E_FLAGS;
    out->program[0] = out->cwd[0] = out->ovt[0] = out->cmdline[0] = 0;
    out->cmdline_length = 0;
    for (i = 0; i <= BCDHOST_TAG_ROOT; i++) { have[i] = 0; value[i] = data; length[i] = 0; }

    /* First the fields' places (nothing is converted yet). */
    while (pos < size)
    {
        uint32_t tag, len;

        if (size - pos < 6) return BCDHOST_E_TRUNC;
        tag = u16le(data + pos);
        len = u32le(data + pos + 2);
        pos += 6;
        if (len > size - pos) return BCDHOST_E_TRUNC;
        if (tag < BCDHOST_TAG_PROGRAM || tag > BCDHOST_TAG_ROOT) return BCDHOST_E_TAG;
        if (have[tag]) return BCDHOST_E_DUP;
        have[tag] = 1;
        value[tag] = data + pos;
        length[tag] = len;
        pos += len;
    }
    if (!have[BCDHOST_TAG_PROGRAM]) return BCDHOST_E_MISSING;

    /* Then the values. */
    if ((rc = bcd_utf8_to_utf16(value[BCDHOST_TAG_PROGRAM], length[BCDHOST_TAG_PROGRAM], out->program, BCDHOST_PATH_MAX, &program_n))) return rc == BCDHOST_E_ARGS ? BCDHOST_E_PROGRAM : rc;
    out->program[program_n] = 0;
    if (have[BCDHOST_TAG_CWD])
    {
        if ((rc = bcd_utf8_to_utf16(value[BCDHOST_TAG_CWD], length[BCDHOST_TAG_CWD], out->cwd, BCDHOST_PATH_MAX, &cwd_n))) return rc == BCDHOST_E_ARGS ? BCDHOST_E_CWD : rc;
        out->cwd[cwd_n] = 0;
    }
    if (have[BCDHOST_TAG_OVT])
    {
        if ((rc = bcd_utf8_to_utf16(value[BCDHOST_TAG_OVT], length[BCDHOST_TAG_OVT], out->ovt, BCDHOST_PATH_MAX, &ovt_n))) return rc == BCDHOST_E_ARGS ? BCDHOST_E_OVT : rc;
        out->ovt[ovt_n] = 0;
    }
    root[0] = 0;
    if (have[BCDHOST_TAG_ROOT])
    {
        if ((rc = bcd_utf8_to_utf16(value[BCDHOST_TAG_ROOT], length[BCDHOST_TAG_ROOT], root, BCDHOST_PATH_MAX, &root_n))) return rc == BCDHOST_E_ARGS ? BCDHOST_E_ROOT : rc;
        root[root_n] = 0;
    }

    if (!path_ok(out->program, program_n) || !ends_with(out->program, program_n, ".exe") || program_n < 8) return BCDHOST_E_PROGRAM;
    if (have[BCDHOST_TAG_CWD]) { if (!path_ok(out->cwd, cwd_n)) return BCDHOST_E_CWD; }
    else folder_of(out->program, program_n, out->cwd);
    if (have[BCDHOST_TAG_OVT])
    {
        if (!path_ok(out->ovt, ovt_n) || !starts_with(out->ovt, ovt_n, "C:\\ProgramData\\BCD\\") || !ends_with(out->ovt, ovt_n, ".ovt")) return BCDHOST_E_OVT;
    }
    if (have[BCDHOST_TAG_ROOT])
    {
        if (!path_ok(root, root_n)) return BCDHOST_E_ROOT;
        /* The program lies under the root: the root, a backslash, then more. (A root of "X:\" already ends in one.) */
        if (root[root_n - 1] != '\\')
        {
            if (program_n <= root_n || out->program[root_n] != '\\') return BCDHOST_E_OUTSIDE;
        }
        else if (program_n <= root_n) return BCDHOST_E_OUTSIDE;
        for (i = 0; i < root_n; i++) if (fold(out->program[i]) != fold(root[i])) return BCDHOST_E_OUTSIDE;
    }

    /* The command line: the quoted program, then a space and the arguments (converted in place) when there are any. */
    if (program_n + 2 > BCDHOST_CMDLINE_MAX) return BCDHOST_E_ARGS;
    n = 0;
    out->cmdline[n++] = '"';
    for (i = 0; i < program_n; i++) out->cmdline[n++] = out->program[i];
    out->cmdline[n++] = '"';
    if (have[BCDHOST_TAG_ARGS] && length[BCDHOST_TAG_ARGS])
    {
        out->cmdline[n] = ' ';
        rc = bcd_utf8_to_utf16(value[BCDHOST_TAG_ARGS], length[BCDHOST_TAG_ARGS], out->cmdline + n + 1, BCDHOST_CMDLINE_MAX - n - 1, &args_n);
        if (rc) { bcd_wipe(out->cmdline, sizeof(out->cmdline)); return rc; }
        n += 1 + args_n;
    }
    out->cmdline[n] = 0;
    out->cmdline_length = n;
    return BCDHOST_OK;
}
