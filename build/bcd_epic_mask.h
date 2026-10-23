/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright 2026 thebcd69 (BCD) */
/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */

/*
 * Epic Games, milestone 2 (docs/research/open-fork-2026-10-18/epic.md section 4.6, hooks 10 and 11): a started game's
 * command line carries Epic's launcher arguments, among them a one-time sign-in code (-AUTH_PASSWORD=), the
 * account's ID (-epicuserid=) and its display name (-epicusername=). The runtime prints every new process's command
 * line to the app's log (build/ntdll-unix/process_ios.c, "NtCreateUserProcess: ...") and the bridge prints its own
 * arguments (app/Madeira/WineProcessBridge.m, "[WineProc] argv[...]"): both print a masked copy, with each of
 * those three values replaced by "*". A line with none of them is printed exactly as it was.
 *
 * Header-only and pure (no library call), in a narrow (char) and a wide (16-bit) form, so tests/host/check-epic-mask.py
 * compiles both on the host and holds them to the same cases. The keys are matched in any letter case, at the start
 * of the text or after a space, a tab or a quote. A value runs to the next space or tab (or the quote the key sat in); one
 * that starts with a quote runs to the closing quote (a display name with spaces), or to the end when it is not closed.
 *
 *   long bcd_epic_mask_a(const char *in, size_t len, char *out)      returns the masked length, -1 when nothing was masked
 *   long bcd_epic_mask_w(const unsigned short *in, size_t len, unsigned short *out)
 *
 * `out` needs `len` characters of room: a masked text is never longer than its original (an empty value is not masked).
 * Nothing is written to `out` when the result is -1.
 */
#ifndef BCD_EPIC_MASK_H
#define BCD_EPIC_MASK_H

#include <stddef.h>

#define BCD_EPIC_UNUSED __attribute__((unused))

BCD_EPIC_UNUSED static const char bcd_epic_mask_keys[][16] = { "-auth_password=", "-epicuserid=", "-epicusername=" };
#define BCD_EPIC_MASK_KEY_COUNT 3

/* The key at `at` in a narrow text: its length, or 0. */
BCD_EPIC_UNUSED static size_t bcd_epic_key_a(const char *s, size_t at, size_t len)
{
    size_t k, i;

    if (at > 0 && s[at - 1] != ' ' && s[at - 1] != '\t' && s[at - 1] != '"' && s[at - 1] != '\'') return 0;
    for (k = 0; k < BCD_EPIC_MASK_KEY_COUNT; k++)
    {
        for (i = 0; bcd_epic_mask_keys[k][i]; i++)
        {
            char c = at + i < len ? s[at + i] : 0;
            if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
            if (c != bcd_epic_mask_keys[k][i]) break;
        }
        if (!bcd_epic_mask_keys[k][i]) return i;
    }
    return 0;
}

BCD_EPIC_UNUSED static long bcd_epic_mask_a(const char *in, size_t len, char *out)
{
    size_t i = 0, o = 0;
    long masked = 0;

    while (i < len)
    {
        size_t key = in[i] == '-' ? bcd_epic_key_a(in, i, len) : 0;
        size_t end = i + key;

        if (key)
        {
            /* The value: a quoted one runs to its closing quote, any other to the next space or tab. An empty value is left alone. */
            if (end < len && in[end] == '"')
            {
                end++;
                while (end < len && in[end] != '"') end++;
                if (end < len) end++;
            }
            else
            {
                /* A key that sits inside quotes ("-AUTH_PASSWORD=abc") ends at that quote too. */
                int quote = i > 0 && (in[i - 1] == '"' || in[i - 1] == '\'') ? in[i - 1] : 0;
                while (end < len && in[end] != ' ' && in[end] != '\t' && in[end] != quote) end++;
            }
            if (end == i + key) key = 0;
        }
        if (!key) { if (masked) out[o] = in[i]; o++; i++; continue; }
        /* The key stays, its value becomes "*": a masked text is never longer than its original. */
        if (masked == 0) for (size_t j = 0; j < o; j++) out[j] = in[j];   /* the text so far, not copied while nothing was masked */
        for (size_t j = 0; j < key; j++) out[o++] = in[i + j];
        out[o++] = '*';
        i = end;
        masked++;
    }
    return masked ? (long)o : -1;
}

BCD_EPIC_UNUSED static size_t bcd_epic_key_w(const unsigned short *s, size_t at, size_t len)
{
    size_t k, i;

    if (at > 0 && s[at - 1] != ' ' && s[at - 1] != '\t' && s[at - 1] != '"' && s[at - 1] != '\'') return 0;
    for (k = 0; k < BCD_EPIC_MASK_KEY_COUNT; k++)
    {
        for (i = 0; bcd_epic_mask_keys[k][i]; i++)
        {
            unsigned short c = at + i < len ? s[at + i] : 0;
            if (c >= 'A' && c <= 'Z') c = (unsigned short)(c + 32);
            if (c != (unsigned short)bcd_epic_mask_keys[k][i]) break;
        }
        if (!bcd_epic_mask_keys[k][i]) return i;
    }
    return 0;
}

BCD_EPIC_UNUSED static long bcd_epic_mask_w(const unsigned short *in, size_t len, unsigned short *out)
{
    size_t i = 0, o = 0;
    long masked = 0;

    while (i < len)
    {
        size_t key = in[i] == '-' ? bcd_epic_key_w(in, i, len) : 0;
        size_t end = i + key;

        if (key)
        {
            /* The value: a quoted one runs to its closing quote, any other to the next space or tab. An empty value is left alone. */
            if (end < len && in[end] == '"')
            {
                end++;
                while (end < len && in[end] != '"') end++;
                if (end < len) end++;
            }
            else
            {
                /* A key that sits inside quotes ("-AUTH_PASSWORD=abc") ends at that quote too. */
                int quote = i > 0 && (in[i - 1] == '"' || in[i - 1] == '\'') ? in[i - 1] : 0;
                while (end < len && in[end] != ' ' && in[end] != '\t' && in[end] != quote) end++;
            }
            if (end == i + key) key = 0;
        }
        if (!key) { if (masked) out[o] = in[i]; o++; i++; continue; }
        /* The key stays, its value becomes "*": a masked text is never longer than its original. */
        if (masked == 0) for (size_t j = 0; j < o; j++) out[j] = in[j];   /* the text so far, not copied while nothing was masked */
        for (size_t j = 0; j < key; j++) out[o++] = in[i + j];
        out[o++] = '*';
        i = end;
        masked++;
    }
    return masked ? (long)o : -1;
}

#endif
