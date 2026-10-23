/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright 2026 thebcd69 (BCD) */
/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */

#include "report.h"

static size_t put_text(char *buf, size_t cap, size_t at, const char *text)
{
    while (*text)
    {
        if (at >= cap) return (size_t)-1;
        buf[at++] = *text++;
    }
    return at;
}

static size_t put_line(char *buf, size_t cap, size_t at, const char *key, uint32_t value)
{
    char digits[11];
    int n = 0;

    if (at == (size_t)-1) return at;
    at = put_text(buf, cap, at, key);
    if (at == (size_t)-1) return at;
    at = put_text(buf, cap, at, "=");
    if (at == (size_t)-1) return at;
    do { digits[n++] = (char)('0' + value % 10); value /= 10; } while (value);
    while (n--)
    {
        if (at >= cap) return (size_t)-1;
        buf[at++] = digits[n];
    }
    if (at >= cap) return (size_t)-1;
    buf[at++] = '\n';
    return at;
}

size_t bcd_report_format(const bcd_report *r, char *buf, size_t cap)
{
    size_t at = 0;

    at = put_line(buf, cap, at, "v", 1);
    at = put_line(buf, cap, at, "stage", r->stage);
    at = put_line(buf, cap, at, "error", r->error);
    at = put_line(buf, cap, at, "last-error", r->last_error);
    at = put_line(buf, cap, at, "flags", r->flags);
    at = put_line(buf, cap, at, "open", r->open);
    at = put_line(buf, cap, at, "services", r->services);
    at = put_line(buf, cap, at, "job", r->job);
    at = put_line(buf, cap, at, "wait", r->wait);
    at = put_line(buf, cap, at, "child-pid", r->child_pid);
    at = put_line(buf, cap, at, "child-exit", r->child_exit);
    at = put_line(buf, cap, at, "settle", r->settle);
    at = put_line(buf, cap, at, "ovt", r->ovt);
    at = put_line(buf, cap, at, "seconds", r->seconds);
    at = put_line(buf, cap, at, "lived", r->lived);
    at = put_line(buf, cap, at, "late", r->late);
    at = put_line(buf, cap, at, "left", r->left);
    at = put_line(buf, cap, at, "helpers", r->helpers);
    at = put_line(buf, cap, at, "names", r->names);
    return at == (size_t)-1 ? 0 : at;
}
