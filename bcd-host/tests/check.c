/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright 2026 thebcd69 (BCD) */
/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */

/*
 * Host tests of BCD Host's pure parts (request.c, report.c): the request's reader, its strict UTF-8,
 * every rule it checks, a deterministic fuzz of damaged requests (run under AddressSanitizer and
 * UBSan by build/bcd-host/build.sh --check), and the report's bounds. Every value in here is made up.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "names.h"
#include "report.h"
#include "request.h"

static int failures, checks;

#define CHECK(cond, what) do { checks++; if (!(cond)) { failures++; printf("FAIL: %s\n", what); } } while (0)

typedef struct { uint8_t data[BCDHOST_MAX_REQUEST + 64]; size_t size; } buf;

static void put32(buf *b, uint32_t v) { for (int i = 0; i < 4; i++) b->data[b->size++] = (uint8_t)(v >> (8 * i)); }
static void head(buf *b, uint32_t version, uint32_t flags)
{
    b->size = 0;
    memcpy(b->data, "BCDHOST1", 8); b->size = 8;
    put32(b, version); put32(b, flags);
}
static void field(buf *b, uint16_t tag, const void *value, size_t length)
{
    b->data[b->size++] = (uint8_t)tag; b->data[b->size++] = (uint8_t)(tag >> 8);
    put32(b, (uint32_t)length);
    memcpy(b->data + b->size, value, length); b->size += length;
}
static void text(buf *b, uint16_t tag, const char *s) { field(b, tag, s, strlen(s)); }

static bcd_request Q;
static int parse(const buf *b) { memset(&Q, 0x5A, sizeof(Q)); return bcd_request_parse(b->data, b->size, &Q); }

static int wide_equals(const uint16_t *w, const char *s)
{
    size_t i;
    for (i = 0; s[i]; i++) if (w[i] != (uint16_t)(unsigned char)s[i]) return 0;
    return w[i] == 0;
}

static buf base(void)
{
    buf b;
    head(&b, 1, BCDHOST_FLAG_TREE);
    text(&b, BCDHOST_TAG_PROGRAM, "C:\\Program Files\\Epic Games\\Some Game\\Game.exe");
    text(&b, BCDHOST_TAG_CWD, "C:\\Program Files\\Epic Games\\Some Game");
    text(&b, BCDHOST_TAG_ARGS, "-windowed -AUTH_LOGIN=unused -AUTH_PASSWORD=0123456789abcdef0123456789abcdef -epicusername=\"Some One\"");
    text(&b, BCDHOST_TAG_OVT, "C:\\ProgramData\\BCD\\Epic\\token.ovt");
    text(&b, BCDHOST_TAG_ROOT, "C:\\Program Files\\Epic Games");
    return b;
}

static void valid(void)
{
    buf b = base();
    CHECK(parse(&b) == BCDHOST_OK, "a full request is read");
    CHECK(Q.flags == BCDHOST_FLAG_TREE, "the flags are kept");
    CHECK(wide_equals(Q.program, "C:\\Program Files\\Epic Games\\Some Game\\Game.exe"), "the program");
    CHECK(wide_equals(Q.cwd, "C:\\Program Files\\Epic Games\\Some Game"), "the working folder");
    CHECK(wide_equals(Q.ovt, "C:\\ProgramData\\BCD\\Epic\\token.ovt"), "the file to delete");
    CHECK(wide_equals(Q.cmdline, "\"C:\\Program Files\\Epic Games\\Some Game\\Game.exe\" -windowed -AUTH_LOGIN=unused "
                                 "-AUTH_PASSWORD=0123456789abcdef0123456789abcdef -epicusername=\"Some One\""), "the command line: the quoted program, a space, the arguments as given");
    CHECK(Q.cmdline_length == strlen("\"C:\\Program Files\\Epic Games\\Some Game\\Game.exe\" -windowed -AUTH_LOGIN=unused -AUTH_PASSWORD=0123456789abcdef0123456789abcdef -epicusername=\"Some One\""), "the command line's length");

    head(&b, 1, 0);
    text(&b, BCDHOST_TAG_PROGRAM, "C:\\Game.exe");
    CHECK(parse(&b) == BCDHOST_OK, "only a program: no arguments, no file, no root");
    CHECK(wide_equals(Q.cmdline, "\"C:\\Game.exe\""), "no arguments: no trailing space");
    CHECK(wide_equals(Q.cwd, "C:\\"), "no working folder: the program's folder (a drive's root)");
    CHECK(Q.ovt[0] == 0, "no file to delete");

    head(&b, 1, BCDHOST_FLAG_SERVICES | BCDHOST_FLAG_TREE);
    text(&b, BCDHOST_TAG_PROGRAM, "d:\\games\\x\\run.EXE");
    text(&b, BCDHOST_TAG_ARGS, "");
    CHECK(parse(&b) == BCDHOST_OK && Q.flags == 3, "a lower-case drive, an upper-case .EXE and empty arguments");
    CHECK(wide_equals(Q.cwd, "d:\\games\\x"), "the program's folder");
    CHECK(wide_equals(Q.cmdline, "\"d:\\games\\x\\run.EXE\""), "empty arguments: no trailing space");

    head(&b, 1, 0);
    text(&b, BCDHOST_TAG_PROGRAM, "C:\\Program Files\\Epic Games\\\xC3\xA9t\xC3\xA9\\\xE6\x97\xA5\xE6\x9C\xAC.exe");
    text(&b, BCDHOST_TAG_ARGS, "-name=\xF0\x9F\x8E\xAE");
    CHECK(parse(&b) == BCDHOST_OK, "non-ASCII names and an emoji (a surrogate pair) are read");
    CHECK(Q.program[28] == 0xE9 && Q.program[29] == 't' && Q.program[30] == 0xE9 && Q.program[32] == 0x65E5, "UTF-16 as Windows wants it: the program's names");
    CHECK(Q.cmdline[Q.cmdline_length - 2] == 0xD83C && Q.cmdline[Q.cmdline_length - 1] == 0xDFAE, "...and a surrogate pair");

    head(&b, 1, 0);
    text(&b, BCDHOST_TAG_ARGS, "-a");
    text(&b, BCDHOST_TAG_PROGRAM, "C:\\Game.exe");
    CHECK(parse(&b) == BCDHOST_OK, "fields in any order");
}

static void invalid_header(void)
{
    buf b = base();
    CHECK(bcd_request_parse(b.data, 15, &Q) == BCDHOST_E_SHORT, "shorter than the header");
    CHECK(bcd_request_parse(b.data, 0, &Q) == BCDHOST_E_SHORT, "empty");
    b.data[0] = 'X'; CHECK(parse(&b) == BCDHOST_E_MAGIC, "wrong magic");
    b = base(); b.data[8] = 2; CHECK(parse(&b) == BCDHOST_E_VERSION, "another version");
    b = base(); b.data[12] = 0x08; CHECK(parse(&b) == BCDHOST_E_FLAGS, "an unknown flag");
    b = base(); b.data[12] = 0x04; CHECK(parse(&b) == BCDHOST_E_FLAGS, "the presence flag is not accepted in this version");
    b = base(); b.size = BCDHOST_MAX_REQUEST + 1; CHECK(parse(&b) == BCDHOST_E_SIZE, "over 32 KB");
    head(&b, 1, 0); CHECK(parse(&b) == BCDHOST_E_MISSING, "no program");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_ARGS, "-x"); CHECK(parse(&b) == BCDHOST_E_MISSING, "arguments but no program");
}

static void invalid_fields(void)
{
    buf b;
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\a.exe"); text(&b, BCDHOST_TAG_PROGRAM, "C:\\b.exe");
    CHECK(parse(&b) == BCDHOST_E_DUP, "a tag twice");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\a.exe"); text(&b, 9, "x");
    CHECK(parse(&b) == BCDHOST_E_TAG, "an unknown tag");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\a.exe"); text(&b, 0, "x");
    CHECK(parse(&b) == BCDHOST_E_TAG, "tag zero");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\a.exe"); b.size -= 2;
    CHECK(parse(&b) == BCDHOST_E_TRUNC, "a field running past the end");
    head(&b, 1, 0); b.data[b.size++] = 1; b.data[b.size++] = 0; b.data[b.size++] = 0;
    CHECK(parse(&b) == BCDHOST_E_TRUNC, "half a field header");
    head(&b, 1, 0); b.data[b.size++] = 1; b.data[b.size++] = 0; put32(&b, 0xFFFFFFFFu);
    CHECK(parse(&b) == BCDHOST_E_TRUNC, "a length of four billion");

    const char *bad_programs[] = {
        "", "a.exe", "C:a.exe", "C:/a.exe", "\\a.exe", "C:\\", "C:\\a.dll", "C:\\a.exe.txt", "C:\\a", "C:\\.exe\\",
        "C:\\x\\..\\a.exe", "C:\\x\\.\\a.exe", "C:\\x\\\\a.exe", "C:\\x\\a.exe\\", "C:\\x\\a b.exe ", "C:\\x\\a\".exe", "C:\\x\\a?.exe",
        "C:\\x\\a*.exe", "C:\\x\\a<.exe", "C:\\x\\a|.exe", "C:\\x\\a:b.exe", "\\\\?\\unix\\tmp\\a.exe", "\\\\server\\share\\a.exe", "1:\\a.exe",
        "C:\\ . \\a.exe", "C:\\...\\a.exe", "C:\\dir.\\a.exe", "C:\\dir \\a.exe" };
    for (size_t i = 0; i < sizeof(bad_programs) / sizeof(bad_programs[0]); i++)
    {
        head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, bad_programs[i]);
        char what[160]; snprintf(what, sizeof(what), "a program refused: %s", bad_programs[i]);
        CHECK(parse(&b) == BCDHOST_E_PROGRAM, what);
    }
    char long_path[700];
    memset(long_path, 'a', sizeof(long_path)); memcpy(long_path, "C:\\", 3); strcpy(long_path + 600, ".exe");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, long_path); CHECK(parse(&b) == BCDHOST_E_PROGRAM, "a program path over 520 units");
    memset(long_path, 'a', 516); memcpy(long_path, "C:\\", 3); strcpy(long_path + 516, ".exe"); long_path[520] = 0;
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, long_path); CHECK(parse(&b) == BCDHOST_OK, "a program path of exactly 520 units");

    const char *bad_cwds[] = { "", "x", "C:", "C:\\a\\", "C:\\a\\..", "C:\\a\\..\\b", "C:\\a|b", "/tmp" };
    for (size_t i = 0; i < sizeof(bad_cwds) / sizeof(bad_cwds[0]); i++)
    {
        head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\a.exe"); text(&b, BCDHOST_TAG_CWD, bad_cwds[i]);
        char what[160]; snprintf(what, sizeof(what), "a working folder refused: %s", bad_cwds[i]);
        CHECK(parse(&b) == BCDHOST_E_CWD, what);
    }
    const char *bad_ovts[] = { "", "C:\\ProgramData\\BCD\\a.txt", "C:\\ProgramData\\other\\a.ovt", "C:\\Windows\\a.ovt", "C:\\ProgramData\\BCD\\..\\a.ovt",
                               "C:\\ProgramData\\BCD\\", "C:\\ProgramData\\BCDx\\a.ovt", "D:\\ProgramData\\BCD\\a.ovt", "C:\\ProgramData\\BCD\\a.ovt\\" };
    for (size_t i = 0; i < sizeof(bad_ovts) / sizeof(bad_ovts[0]); i++)
    {
        head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\a.exe"); text(&b, BCDHOST_TAG_OVT, bad_ovts[i]);
        char what[160]; snprintf(what, sizeof(what), "a file to delete refused: %s", bad_ovts[i]);
        CHECK(parse(&b) == BCDHOST_E_OVT, what);
    }
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\a.exe"); text(&b, BCDHOST_TAG_OVT, "c:\\programdata\\bcd\\Epic\\x.OVT");
    CHECK(parse(&b) == BCDHOST_OK, "the file to delete: any letter case");

    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\Games\\a.exe"); text(&b, BCDHOST_TAG_ROOT, "C:\\Games");
    CHECK(parse(&b) == BCDHOST_OK, "a program under its root");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\games\\a.exe"); text(&b, BCDHOST_TAG_ROOT, "C:\\GAMES");
    CHECK(parse(&b) == BCDHOST_OK, "...in any letter case");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\a.exe"); text(&b, BCDHOST_TAG_ROOT, "C:\\");
    CHECK(parse(&b) == BCDHOST_OK, "a drive's root holds a program in it");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\Gamesx\\a.exe"); text(&b, BCDHOST_TAG_ROOT, "C:\\Games");
    CHECK(parse(&b) == BCDHOST_E_OUTSIDE, "a folder that only starts like the root is outside it");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\Other\\a.exe"); text(&b, BCDHOST_TAG_ROOT, "C:\\Games");
    CHECK(parse(&b) == BCDHOST_E_OUTSIDE, "a program outside the root");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "D:\\Games\\a.exe"); text(&b, BCDHOST_TAG_ROOT, "C:\\Games");
    CHECK(parse(&b) == BCDHOST_E_OUTSIDE, "another drive is outside the root");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\Games\\a.exe"); text(&b, BCDHOST_TAG_ROOT, "C:\\Games\\a.exe");
    CHECK(parse(&b) == BCDHOST_E_OUTSIDE, "a program is not under itself");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\Games\\a.exe"); text(&b, BCDHOST_TAG_ROOT, "C:\\Games\\..");
    CHECK(parse(&b) == BCDHOST_E_ROOT, "a root with ..");

    /* UTF-8 and control characters. */
    const struct { const char *bytes; size_t n; int rc; const char *what; } utf[] = {
        { "-\xC0\x80", 3, BCDHOST_E_UTF8, "an overlong NUL" },
        { "-\xC1\xBF", 3, BCDHOST_E_UTF8, "an overlong 7-bit character" },
        { "-\xE0\x80\xAF", 4, BCDHOST_E_UTF8, "an overlong slash" },
        { "-\xED\xA0\x80", 4, BCDHOST_E_UTF8, "a surrogate" },
        { "-\xF4\x90\x80\x80", 5, BCDHOST_E_UTF8, "above U+10FFFF" },
        { "-\xF8\x88\x80\x80\x80", 6, BCDHOST_E_UTF8, "a five-byte form" },
        { "-\xE6\x97", 3, BCDHOST_E_UTF8, "a cut sequence" },
        { "-\x80", 2, BCDHOST_E_UTF8, "a lone continuation byte" },
        { "-\xFF", 2, BCDHOST_E_UTF8, "0xFF" },
        { "-a\x01", 3, BCDHOST_E_CHAR, "a control character" },
        { "-a\tb", 4, BCDHOST_E_CHAR, "a tab" },
        { "-a\nb", 4, BCDHOST_E_CHAR, "a line feed" },
        { "-a\x7F", 3, BCDHOST_E_CHAR, "DEL" },
        { "-a\xC2\x85", 4, BCDHOST_E_CHAR + 100, "a C1 control character is allowed (above the ASCII controls)" },
    };
    for (size_t i = 0; i < sizeof(utf) / sizeof(utf[0]); i++)
    {
        head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\a.exe"); field(&b, BCDHOST_TAG_ARGS, utf[i].bytes, utf[i].n);
        int want = utf[i].rc == BCDHOST_E_CHAR + 100 ? BCDHOST_OK : utf[i].rc;
        CHECK(parse(&b) == want, utf[i].what);
    }
    {
        char with_nul[8] = { 'C', ':', '\\', 'a', 0, '.', 'e', 'x' };
        head(&b, 1, 0); field(&b, BCDHOST_TAG_PROGRAM, with_nul, sizeof(with_nul));
        CHECK(parse(&b) == BCDHOST_E_CHAR, "a NUL inside a value");
    }

    /* The request's size: the command line's own limit (32766 units) can never be reached inside 32 KB of request. */
    static char big[BCDHOST_MAX_REQUEST + 100];
    memset(big, 'x', sizeof(big));
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\a.exe");
    size_t room = BCDHOST_MAX_REQUEST - b.size - 6;
    field(&b, BCDHOST_TAG_ARGS, big, room);
    CHECK(b.size == BCDHOST_MAX_REQUEST && parse(&b) == BCDHOST_OK && Q.cmdline_length == strlen("\"C:\\a.exe\" ") + room, "arguments that fill the 32 KB request exactly");
    head(&b, 1, 0); text(&b, BCDHOST_TAG_PROGRAM, "C:\\a.exe"); field(&b, BCDHOST_TAG_ARGS, big, room + 1);
    CHECK(parse(&b) == BCDHOST_E_SIZE, "one byte more is refused as too big");
    CHECK(bcd_request_parse(b.data, 0, &Q) == BCDHOST_E_SHORT, "and an empty request is too short");
}

/* A deterministic fuzz: damaged copies of valid requests never crash, never read outside, and whenever they are accepted they
 * satisfy every rule the host relies on. */
static uint64_t rng = 0x9E3779B97F4A7C15ull;
static uint32_t next(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 16); }

static void invariants(const char *what)
{
    size_t n = 0;
    while (Q.program[n]) n++;
    CHECK(n >= 8 && n <= BCDHOST_PATH_MAX, what);
    CHECK(Q.program[1] == ':' && Q.program[2] == '\\', what);
    CHECK(Q.cmdline[0] == '"' && Q.cmdline[n + 1] == '"' && Q.cmdline_length >= n + 2 && Q.cmdline_length <= BCDHOST_CMDLINE_MAX && Q.cmdline[Q.cmdline_length] == 0, what);
    for (size_t i = 0; i < Q.cmdline_length; i++) if (Q.cmdline[i] < 0x20) { CHECK(0, what); break; }
    if (Q.ovt[0])
    {
        static const char prefix[] = "C:\\ProgramData\\BCD\\";
        int ok = 1;
        for (size_t i = 0; prefix[i]; i++) { uint16_t c = Q.ovt[i]; if (c >= 'A' && c <= 'Z') c += 32; uint16_t p = (uint16_t)prefix[i]; if (p >= 'A' && p <= 'Z') p += 32; if (c != p) ok = 0; }
        CHECK(ok, what);
    }
}

static void fuzz(void)
{
    buf seed = base();
    int accepted = 0;
    for (int round = 0; round < 60000; round++)
    {
        buf b = seed;
        int hits = 1 + (int)(next() % 4);
        for (int h = 0; h < hits && b.size; h++)
        {
            switch (next() % 5)
            {
            case 0: b.data[next() % b.size] = (uint8_t)next(); break;
            case 1: b.data[next() % b.size] ^= (uint8_t)(1u << (next() % 8)); break;
            case 2: b.size = next() % (b.size + 1); break;
            case 3: { size_t at = next() % b.size; size_t n = next() % 8; if (at + n < b.size) memmove(b.data + at, b.data + at + n, b.size - at - n), b.size -= n; break; }
            case 4: if (b.size + 4 < sizeof(b.data)) { size_t at = next() % (b.size + 1); memmove(b.data + at + 1, b.data + at, b.size - at); b.data[at] = (uint8_t)next(); b.size++; } break;
            }
        }
        if (b.size > BCDHOST_MAX_REQUEST) b.size = BCDHOST_MAX_REQUEST;
        int rc = bcd_request_parse(b.data, b.size, &Q);
        if (rc == BCDHOST_OK) { accepted++; if (accepted <= 2000) invariants("an accepted damaged request keeps the rules"); }
        else CHECK(rc >= BCDHOST_E_SHORT && rc <= BCDHOST_E_ARGS, "an error number in range");
    }
    CHECK(accepted > 100, "the fuzz also reached accepted requests");
    printf("fuzz: 60000 damaged requests, %d accepted\n", accepted);
}

static void reporting(void)
{
    bcd_report r;
    char out[512];
    memset(&r, 0, sizeof(r));
    r.stage = 7; r.error = 0; r.flags = 3; r.open = 1; r.job = 1; r.wait = 2; r.child_pid = 4294967295u; r.child_exit = 3221225477u; r.seconds = 12;
    r.lived = 200; r.late = 1; r.left = 2; r.helpers = 5; r.names = 1;
    size_t n = bcd_report_format(&r, out, sizeof(out));
    out[n] = 0;
    CHECK(n > 0 && strstr(out, "v=1\nstage=7\nerror=0\n") == out, "the report starts with its version and stage");
    CHECK(strstr(out, "child-pid=4294967295\n") && strstr(out, "child-exit=3221225477\n"), "32-bit values in full");
    CHECK(strstr(out, "wait=2\n") && strstr(out, "seconds=12\n"), "the wait mode and the seconds");
    CHECK(strstr(out, "lived=200\nlate=1\nleft=2\nhelpers=5\nnames=1\n"), "how long the program ran, whether the wait was cut, and what was left in the job");
    int numeric = 1;
    for (size_t i = 0; i < n; i++)
        if (!((out[i] >= '0' && out[i] <= '9') || (out[i] >= 'a' && out[i] <= 'z') || out[i] == '=' || out[i] == '-' || out[i] == '\n')) numeric = 0;
    CHECK(numeric, "the report holds only lower-case keys, digits, '=' and line feeds");
    CHECK(bcd_report_format(&r, out, 10) == 0, "a buffer that is too small gives nothing");
    CHECK(bcd_report_format(&r, out, n - 1) == 0, "a buffer one byte short gives nothing");
    CHECK(bcd_report_format(&r, out, n) == n, "a buffer of exactly the size is enough");
}

static int bg(const char *path)
{
    uint16_t w[300];
    size_t n = 0;
    for (; path[n] && n < 299; n++) w[n] = (uint16_t)(unsigned char)path[n];
    return bcd_host_background(w, n);
}

static void names(void)
{
    const char *background[] = { "C:\\windows\\system32\\services.exe", "C:\\windows\\system32\\RPCSS.EXE", "winedevice.exe", "C:\\windows\\system32\\plugplay.exe",
                                 "C:\\windows\\system32\\explorer.exe", "conhost.exe", "wineboot.exe", "svchost.exe", "C:\\x\\SocialClubHelper.exe", "C:\\x\\crashpad_handler.exe",
                                 "C:\\x\\UnityCrashHandler64.exe", "C:\\x\\crs-handler.exe", "C:\\x\\RockstarErrorHandler.exe", "C:\\x\\steamerrorreporter64.exe",
                                 "C:\\x\\CrashReportClient.exe", "C:\\x\\crash.exe", "C:\\x\\foo_crash.exe", "C:\\x\\crash_service.exe", "C:\\x\\crashdump.exe", "C:\\x/mixed/CRASHPAD.exe" };
    const char *game[] = { "C:\\Program Files\\Epic Games\\G\\Game.exe", "C:\\x\\CrashBandicootNSaneTrilogy.exe", "C:\\x\\crashlands.exe", "C:\\x\\Launcher.exe", "C:\\x\\PlayGTAV.exe",
                           "C:\\x\\GTA5.exe", "C:\\x\\cmd.exe", "C:\\windows\\system32\\services.exe.bak", "C:\\x\\myservices.exe", "C:\\x\\Paradox Launcher.exe", "dowser.exe", "", "C:\\x\\" };
    for (size_t i = 0; i < sizeof(background) / sizeof(background[0]); i++)
    {
        char what[200]; snprintf(what, sizeof(what), "a helper or reporter does not keep a session open: %s", background[i]);
        CHECK(bg(background[i]) == 1, what);
    }
    for (size_t i = 0; i < sizeof(game) / sizeof(game[0]); i++)
    {
        char what[200]; snprintf(what, sizeof(what), "a game or launcher counts: %s", game[i]);
        CHECK(bg(game[i]) == 0, what);
    }
    uint16_t odd[] = { 'C', ':', '\\', 0x65E5, 0x672C, '.', 'e', 'x', 'e' };
    CHECK(bcd_host_background(odd, sizeof(odd) / sizeof(odd[0])) == 0, "a non-ASCII name counts as a program");
    uint16_t longname[300];
    for (size_t i = 0; i < 300; i++) longname[i] = 'a';
    CHECK(bcd_host_background(longname, 300) == 0, "a name over 120 characters counts");
    CHECK(bcd_host_background(NULL, 0) == 0, "no name counts");
}

static void wiping(void)
{
    uint8_t x[64];
    memset(x, 0xAB, sizeof(x));
    bcd_wipe(x, sizeof(x));
    int zero = 1;
    for (size_t i = 0; i < sizeof(x); i++) if (x[i]) zero = 0;
    CHECK(zero, "bcd_wipe zeroes");
}

int main(void)
{
    valid();
    invalid_header();
    invalid_fields();
    reporting();
    names();
    wiping();
    fuzz();
    if (failures) { printf("bcd-host checks: %d of %d FAILED\n", failures, checks); return 1; }
    printf("bcd-host checks: all %d passed\n", checks);
    return 0;
}
