/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright 2026 thebcd69 (BCD) */
/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */

#include "names.h"

#define MAX_NAME 120

static int contains(const char *name, const char *part)
{
    size_t i, k;
    for (i = 0; name[i]; i++)
    {
        for (k = 0; part[k] && name[i + k] == part[k]; k++) {}
        if (!part[k]) return 1;
    }
    return 0;
}

static int starts(const char *s, const char *prefix)
{
    size_t k;
    for (k = 0; prefix[k]; k++) if (s[k] != prefix[k]) return 0;
    return 1;
}

static int equals(const char *a, const char *b)
{
    size_t k;
    for (k = 0; a[k] || b[k]; k++) if (a[k] != b[k]) return 0;
    return 1;
}

int bcd_host_background(const uint16_t *path, size_t length)
{
    static const char *const system[] = { "services.exe", "rpcss.exe", "winedevice.exe", "plugplay.exe", "explorer.exe", "conhost.exe",
                                          "wineboot.exe", "svchost.exe", "tabtip.exe", "winemenubuilder.exe" };
    static const char *const words[] = { "crs-handler", "handler", "report", "helper" };
    static const char *const after_crash[] = { "pad", "rpt", "send", "dump", "log", "monitor", "service", "submit", "upload", "client", "server",
                                               "collect", "watch", "guard", "daemon", "catch", "notif", "telemetry", "analy", "diag", "trace",
                                               "detect", "util", "tool", "info", "manage", "process" };
    char name[MAX_NAME + 1];
    size_t start = 0, i, n = 0;
    const char *p;

    for (i = 0; i < length; i++) if (path[i] == '\\' || path[i] == '/') start = i + 1;
    if (length - start > MAX_NAME || length == start) return 0;
    for (i = start; i < length; i++)
    {
        uint16_t c = path[i];
        if (c >= 'A' && c <= 'Z') c = (uint16_t)(c + 32);
        name[n++] = c < 0x80 ? (char)c : '?';
    }
    name[n] = 0;

    for (i = 0; i < sizeof(system) / sizeof(system[0]); i++) if (equals(name, system[i])) return 1;
    for (i = 0; i < sizeof(words) / sizeof(words[0]); i++) if (contains(name, words[i])) return 1;
    /* "crash" counts only when a helper word follows it (crashpad, crash_service, crashdump ...) or when it is the whole name: never as the
     * beginning of a game's name (CrashBandicoot..., crashlands). */
    for (p = name; *p; p++)
    {
        const char *q;
        if (!starts(p, "crash")) continue;
        q = p + 5;
        if (*q == '.' || !*q) { if (p == name || p[-1] == '_' || p[-1] == '-' || p[-1] == ' ') return 1; continue; }
        if (*q == '_' || *q == '-' || *q == ' ') q++;
        for (i = 0; i < sizeof(after_crash) / sizeof(after_crash[0]); i++) if (starts(q, after_crash[i])) return 1;
    }
    return 0;
}
