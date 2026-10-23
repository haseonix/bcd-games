#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[steam-api] (build/ntdll-unix/thread_ios.c): what a process sees of Steam when steam_api says there is none.

The Rockstar Games Launcher prints "[S_API] SteamAPI_Init(): SteamAPI_IsSteamRunning() did not locate a running
instance of Steam.." in 20 of the 40 distinct Rockstar-launcher Dock sessions of 2026-10-19/20 and stops with
LAUNCHER_ERR_STEAM_FAILED; in 13 others the same call loads steamclient64.dll. NtRaiseException now reads the Steam
registry values, the pid's process and the DIPC objects when that debug string passes, and logs them (reads only).

Checks, on the real region of thread_ios.c compiled on the host against stand-ins for the Nt calls:
  - the message test accepts exactly steam_api's text (one safe read, only 80-200 byte strings of code 0x40010006),
    and nothing else: other debug strings, other codes, a null or short or long string;
  - with the values published: the lines say pid, ActiveUser as zero / not zero (the account id never appears),
    the client DLL, the paths, the process's exit status and which objects open; with an empty registry every line says
    why it is empty; no write call exists in the region; at most two reports per process;
  - in thread_ios.c the hook is inside NtRaiseException's WINE_IOS block, before send_debug_event, for a first-chance
    exception only, and the workflow runs this check.

Build 580: a 64-bit (ARM64EC) process handles OutputDebugString inside the PE ntdll, so none of the Rockstar launcher
logs (RGM16D, 0YZKW6) carried the report. ios_steam_api_dbg_hook (same region) is called by unixcall_wine_dbg_write
(tools/patch-ntdll-unix-dbg-write-hook.py, debug.c) with every line the PE ntdll writes, after the write. Checked here:
  - it reacts to the PE ntdll's own line about steam_api's message and to nothing else (other lines, other functions,
    the 32-bit path's "[steam-api]" lines, short and long lines, the message in another function's line);
  - the report it runs is the same text, raw on stderr (no ERR_ prefix), at most twice per session together with the
    32-bit path, and the hook cannot re-enter itself;
  - the patch script applies once to the pinned Wine source (when checked out: set MADEIRA_WINE_SRC to use another
    checkout), keeps the write and its return value, and the hook sits after the write.
"""
from pathlib import Path
import re, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/thread_ios.c").read_text()
workflow = (root / ".github/workflows/build-ipa.yml").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


region = src[src.index("/* steam-api-diag-test:begin"):src.index("/* steam-api-diag-test:end */")]
fn = src[src.index("NTSTATUS WINAPI NtRaiseException("):]
fn = fn[:fn.index("\n}\n")]
check("the region has no write, create, delete or terminate call",
      not re.search(r"NtSetValueKey|NtCreateKey|NtDeleteKey|NtDeleteValueKey|NtWriteVirtualMemory|NtTerminate|NtSuspend|NtSetInformation",
                    region))
check("the hook is in NtRaiseException's WINE_IOS code, for a first-chance exception, before send_debug_event",
      "if (rec && first_chance && ios_steam_api_message( rec )) ios_steam_api_diag();" in fn and
      fn.index("ios_steam_api_message( rec )") < fn.index("status = send_debug_event(") and
      fn.rfind("#ifdef WINE_IOS", 0, fn.index("ios_steam_api_message( rec )")) >
      fn.rfind("#endif", 0, fn.index("ios_steam_api_message( rec )")))
check("the report is capped at two per process", "if (InterlockedIncrement( &told ) > 2) return;" in region)
check("the account id is only reported as zero / not zero",
      '"HKCU ActiveProcess ActiveUser", 1, NULL' in region and 'v ? "not zero" : "zero"' in region)
check("the workflow runs this check", "python3 tests/host/check-steam-api-diag.py" in workflow)
hook_src = region[region.index("void ios_steam_api_dbg_hook("):]
check("the hook only reads the bytes it is handed, writes nothing, and cannot re-enter",
      not re.search(r"NtSetValueKey|NtCreateKey|NtDeleteKey|write\(|NtWriteVirtualMemory|NtTerminate", hook_src)
      and "if (len < 100 || len > 400 || ios_sd_raw) return;" in hook_src and hook_src.index("ios_sd_raw = 1;") < hook_src.index("ios_steam_api_diag();") < hook_src.index("ios_sd_raw = 0;"))
check("the 32-bit path (NtRaiseException) keeps its ERR_ lines: raw output only while the hook runs",
      "#define SD_ERR( ... ) do { if (ios_sd_raw) dprintf( 2, __VA_ARGS__ ); else ERR_(seh)( __VA_ARGS__ ); } while (0)" in region
      and "ERR_(seh)( \"[steam-api]" not in region)
check("the patch script is in the workflow, before ntdll-unix is built",
      "python3 tools/patch-ntdll-unix-dbg-write-hook.py wine/dlls/ntdll/unix/debug.c" in workflow
      and workflow.index("python3 tools/patch-ntdll-unix-dbg-write-hook.py") < workflow.index("name: Build ntdll-unix"))
import os, shutil
wine_dbg = Path(os.environ.get("MADEIRA_WINE_SRC", str(root / "wine"))) / "dlls/ntdll/unix/debug.c"
if wine_dbg.is_file():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "debug.c"
        shutil.copy(wine_dbg, d)
        pp = root / "tools/patch-ntdll-unix-dbg-write-hook.py"
        already = "madeira-bcd: ios_steam_api_dbg_hook" in d.read_text()   # the workflow patched the tree before this run
        r1 = subprocess.run([sys.executable, str(pp), str(d)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(pp), str(d)], capture_output=True, text=True)
        check("the patch applies once to the Wine source and is idempotent" + (" (the tree was patched already)" if already else ""),
              r1.returncode == 0 and r2.returncode == 0 and "already patched" in r2.stdout
              and (("already patched" in r1.stdout) if already else ("applied" in r1.stdout)))
        text = d.read_text()
        fn = text[text.index("NTSTATUS unixcall_wine_dbg_write( void *args )"):]
        fn = fn[:fn.index("\n}\n")]
        check("the write comes first and its return value is returned; the hook is called after it, for WINE_IOS only",
              fn.index("int ret = write( 2, params->str, params->len );") < fn.index("ios_steam_api_dbg_hook( params->str, params->len );")
              < fn.index("return ret;") and "#ifdef WINE_IOS" in fn and "#else\n    return write( 2, params->str, params->len );\n#endif" in fn)
        bad = d.parent / "bad.c"
        bad.write_text(wine_dbg.read_text().replace("unixcall_wine_dbg_write", "something_else").replace("madeira-bcd: ios_steam_api_dbg_hook", "x"))
        r3 = subprocess.run([sys.executable, str(pp), str(bad)], capture_output=True, text=True)
        check("a moved anchor fails by name", r3.returncode != 0 and "anchor" in (r3.stdout + r3.stderr))
else:
    print("SKIP: no wine sources (check out the submodule, or set MADEIRA_WINE_SRC)")

harness = r'''
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
typedef int NTSTATUS; typedef unsigned int ULONG, ACCESS_MASK; typedef unsigned short WCHAR, USHORT;
typedef long LONG; typedef unsigned long long SIZE_T, ULONG_PTR; typedef void *HANDLE; typedef int BOOL;
typedef struct { USHORT Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { ULONG Length; HANDLE RootDirectory; UNICODE_STRING *ObjectName; ULONG Attributes; void *a, *b; } OBJECT_ATTRIBUTES;
#define InitializeObjectAttributes(p, n, at, r, s) do { (p)->Length = sizeof(*(p)); (p)->RootDirectory = (r); \
    (p)->ObjectName = (n); (p)->Attributes = (at); (p)->a = (s); (p)->b = NULL; } while (0)
typedef struct { ULONG TitleIndex, Type, DataLength; unsigned char Data[1]; } KEY_VALUE_PARTIAL_INFORMATION;
typedef struct { HANDLE UniqueProcess, UniqueThread; } CLIENT_ID;
typedef struct { NTSTATUS ExitStatus; void *Peb; ULONG_PTR Affinity; LONG Priority; ULONG_PTR Pid, Parent; } PROCESS_BASIC_INFORMATION;
typedef struct { ULONG ExceptionCode, ExceptionFlags; void *ExceptionRecord, *ExceptionAddress; ULONG NumberParameters;
                 ULONG_PTR ExceptionInformation[15]; } EXCEPTION_RECORD;
#define KEY_QUERY_VALUE 1
#define KEY_WOW64_64KEY 0x100
#define KEY_WOW64_32KEY 0x200
#define OBJ_CASE_INSENSITIVE 0x40
#define REG_SZ 1
#define REG_EXPAND_SZ 2
#define REG_DWORD 4
#define SYNCHRONIZE 0x100000
#define PROCESS_QUERY_LIMITED_INFORMATION 0x1000
#define DBG_PRINTEXCEPTION_C 0x40010006
#define KeyValuePartialInformation 2
#define ProcessBasicInformation 0
#define WINAPI
#define min(a, b) ((a) < (b) ? (a) : (b))
#define NtCurrentProcess() ((HANDLE)(long long)-1)
#define HandleToULong(h) ((ULONG)(ULONG_PTR)(h))
#define ULongToHandle(u) ((HANDLE)(ULONG_PTR)(u))
#define InterlockedIncrement(p) (__sync_add_and_fetch((p), 1))
static struct { struct { HANDLE UniqueProcess, UniqueThread; } ClientId; } fake_teb = {{ (HANDLE)0xd4, 0 }};
#define NtCurrentTeb() (&fake_teb)
static void dbg_err(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vfprintf(stdout, fmt, ap); va_end(ap); }
#define ERR_(ch) dbg_err
#define seh 0

/* A registry of strings: "path|value" -> type + bytes. Scenario "full" has everything, "empty" nothing. */
static int scenario_full;
static const char *last_path;
static NTSTATUS NtOpenKey(HANDLE *key, ACCESS_MASK access, const OBJECT_ATTRIBUTES *attr)
{
    static char path[160]; size_t i, n = attr->ObjectName->Length / 2;
    for (i = 0; i < n; i++) path[i] = (char)attr->ObjectName->Buffer[i];
    path[n] = 0;
    if (!scenario_full) return 0xc0000034;
    if (!strstr(path, "\\Valve\\Steam")) return 0xc0000034;
    if (strstr(path, "\\Registry\\Machine") && !(access & (KEY_WOW64_32KEY | KEY_WOW64_64KEY))) return 0xc0000034;
    if (strstr(path, "\\Registry\\Machine") && (access & KEY_WOW64_64KEY)) return 0xc0000034;   /* Dock wrote the 32-bit view */
    last_path = strstr(path, "ActiveProcess") ? "active" : strstr(path, "\\Registry\\User") ? "user" : "machine";
    *key = (HANDLE)path; return 0;
}
static NTSTATUS NtClose(HANDLE h) { (void)h; return 0; }
static NTSTATUS NtQueryValueKey(HANDLE key, const UNICODE_STRING *name, int cls, void *out, ULONG len, ULONG *got)
{
    char v[64]; size_t i, n = name->Length / 2;
    KEY_VALUE_PARTIAL_INFORMATION *info = out; (void)key; (void)cls; (void)len;
    for (i = 0; i < n; i++) v[i] = (char)name->Buffer[i];
    v[n] = 0; *got = 0;
#define DW(val) do { ULONG x = (val); info->Type = REG_DWORD; info->DataLength = 4; memcpy(info->Data, &x, 4); return 0; } while (0)
#define SZ(text) do { const char *t = (text); WCHAR *w = (WCHAR *)info->Data; size_t k; for (k = 0; t[k]; k++) w[k] = (unsigned char)t[k]; \
    w[k] = 0; info->Type = REG_SZ; info->DataLength = (ULONG)((k + 1) * 2); return 0; } while (0)
    if (!strcmp(last_path, "active"))
    {
        if (!strcmp(v, "pid")) DW(52);
        if (!strcmp(v, "ActiveUser")) DW(123456789u);       /* a made-up account id: must never be printed */
        if (!strcmp(v, "SteamClientDll64")) SZ("C:\\Program Files (x86)\\Steam\\steamclient64.dll");
    }
    else if (!strcmp(last_path, "user") && !strcmp(v, "SteamPath")) SZ("C:\\Program Files (x86)\\Steam");
    else if (!strcmp(last_path, "machine") && !strcmp(v, "SteamPID")) DW(52);
    return 0xc0000034;
}
static NTSTATUS NtOpenProcess(HANDLE *h, ACCESS_MASK a, const OBJECT_ATTRIBUTES *attr, const CLIENT_ID *id)
{ (void)attr; (void)a; if (!scenario_full || HandleToULong(id->UniqueProcess) != 52) return 0xc0000008; *h = (HANDLE)1; return 0; }
static NTSTATUS NtQueryInformationProcess(HANDLE h, int cls, void *out, ULONG len, ULONG *got)
{ PROCESS_BASIC_INFORMATION *p = out; (void)h; (void)cls; (void)len; (void)got; p->ExitStatus = 259; return 0; }
static NTSTATUS object_open(const OBJECT_ATTRIBUTES *attr, int consume_only)
{
    static char name[160]; size_t i, n = attr->ObjectName->Length / 2;
    for (i = 0; i < n; i++) name[i] = (char)attr->ObjectName->Buffer[i];
    name[n] = 0;
    return scenario_full && strstr(name, consume_only ? "CONSUME" : "") && !strstr(name, "PRODUCE") ? 0 : 0xc0000034;
}
static NTSTATUS NtOpenSemaphore(HANDLE *h, ACCESS_MASK a, const OBJECT_ATTRIBUTES *attr)
{ (void)a; if (object_open(attr, 1)) return 0xc0000034; *h = (HANDLE)2; return 0; }
static NTSTATUS NtOpenEvent(HANDLE *h, ACCESS_MASK a, const OBJECT_ATTRIBUTES *attr)
{ (void)h; (void)a; (void)attr; return 0xc0000034; }
static NTSTATUS NtReadVirtualMemory(HANDLE p, const void *addr, void *buf, SIZE_T n, SIZE_T *got)
{ (void)p; if (addr == (const void *)1) return 0xc0000005; memcpy(buf, addr, n); *got = n; return 0; }
''' + region + r'''
static int message(const char *text, ULONG code, int params)
{
    EXCEPTION_RECORD rec; memset(&rec, 0, sizeof(rec));
    rec.ExceptionCode = code; rec.NumberParameters = params;
    rec.ExceptionInformation[0] = strlen(text) + 1; rec.ExceptionInformation[1] = (ULONG_PTR)text;
    return ios_steam_api_message(&rec);
}
int main(int argc, char **argv)
{
    static const char steam[] = "[S_API] SteamAPI_Init(): SteamAPI_IsSteamRunning() did not locate a running instance of Steam..";
    static const char loaded[] = "[S_API] SteamAPI_Init(): Loaded 'C:\\Program Files (x86)\\Steam\\steamclient64.dll' OK..";
    EXCEPTION_RECORD rec; memset(&rec, 0, sizeof(rec));
    if (argc > 1 && !strcmp(argv[1], "message"))
    {
        printf("steam=%d loaded=%d other=%d code=%d params=%d\n", message(steam, DBG_PRINTEXCEPTION_C, 2),
               message(loaded, DBG_PRINTEXCEPTION_C, 2), message("hello from a game", DBG_PRINTEXCEPTION_C, 2),
               message(steam, 0x4001000a, 2), message(steam, DBG_PRINTEXCEPTION_C, 1));
        rec.ExceptionCode = DBG_PRINTEXCEPTION_C; rec.NumberParameters = 2; rec.ExceptionInformation[0] = 100;
        printf("null=%d", ios_steam_api_message(&rec));
        rec.ExceptionInformation[1] = 1;
        printf(" bad_pointer=%d", ios_steam_api_message(&rec));
        rec.ExceptionInformation[0] = 79; rec.ExceptionInformation[1] = (ULONG_PTR)steam;
        printf(" short=%d", ios_steam_api_message(&rec));
        rec.ExceptionInformation[0] = 201;
        printf(" long=%d\n", ios_steam_api_message(&rec));
        /* build 591: the older wording of the legacy SteamStub games (GTA: Vice City D938HM / V85VDT; 74Z00F's 64-bit variant) */
        printf("old32=%d old64=%d pipe=%d running_failed=%d old_without_fail=%d old_wide=%d\n",
               message("[S_API FAIL] SteamAPI_Init() failed; unable to locate a running instance of Steam, or a local steamclient.dll.\n", DBG_PRINTEXCEPTION_C, 2),
               message("[S_API FAIL] SteamAPI_Init() failed; unable to locate a running instance of Steam, or a local steamclient64.dll..", DBG_PRINTEXCEPTION_C, 2),
               message("[S_API FAIL] SteamAPI_Init() failed; create pipe failed. And a few more words so that the string is long enough to pass.", DBG_PRINTEXCEPTION_C, 2),
               message("[S_API FAIL] SteamAPI_Init() failed; SteamAPI_IsSteamRunning() failed.. And a few more words so that the string is long.", DBG_PRINTEXCEPTION_C, 2),
               message("[S_API] SteamAPI_Init() failed; unable to locate a running instance of Steam, or a local steamclient.dll.\n", DBG_PRINTEXCEPTION_C, 2),
               message("[S_API FAIL] SteamAPI_Init() failed; unable to locate a running instance of Steam, or a local steamclient.dll.\n", 0x4001000a, 2));
        return 0;
    }
    if (argc > 1 && !strcmp(argv[1], "hook"))
    {
        /* the PE ntdll's own line, as RGM16D L12416 prints it */
        static const char line[] = "00d8:err:seh:ios_exc_dump_str [exc] ml812   OutputDebugStringA: \"[S_API] SteamAPI_Init(): SteamAPI_IsSteamRunning() did not locate a running instance of Steam..\"\n";
        static const char other_fn[] = "00d8:err:seh:call_seh_handlers [exc] ml812   OutputDebugStringA: \"[S_API] SteamAPI_Init(): SteamAPI_IsSteamRunning() did not locate a running instance of Steam..\"\n";
        static const char other_text[] = "00d8:err:seh:ios_exc_dump_str [exc] ml812   OutputDebugStringA: \"src\\public\\tier1\\interface.h (271) : Assertion Failed: Failed to load SDL3.dll and a little more text.\"\n";
        static const char loaded_line[] = "00d8:err:seh:ios_exc_dump_str [exc] ml812   OutputDebugStringA: \"[S_API] SteamAPI_Init(): Loaded 'C:\\Program Files (x86)\\Steam\\steamclient64.dll' OK..\"\n";
        static const char steam32[] = "00d4:err:seh:ios_sd_value [steam-api] steam_api found no running Steam; process 00d4 sees:\n";
        static const char old_line[] = "0044:err:seh:ios_exc_dump_str [exc] ml812   OutputDebugStringA: \"[S_API FAIL] SteamAPI_Init() failed; unable to locate a running instance of Steam, or a local steamclient64.dll..\"\n";
        static const char pipe_line[] = "008c:err:seh:ios_exc_dump_str [exc] ml812   OutputDebugStringA: \"[S_API FAIL] SteamAPI_Init() failed; create pipe failed. and a few more words of padding\"\n";
        static const char fail_only[] = "0044:err:seh:ios_exc_dump_str [exc] ml812   OutputDebugStringA: \"[S_API FAIL] SteamAPI_Init() failed; SteamAPI_IsSteamRunning() failed.. and a few more words\"\n";
        char longline[600];
        dup2(1, 2);                                          /* raw reports go to stderr: read them from stdout */
        scenario_full = 1;
        ios_steam_api_dbg_hook(other_fn, (unsigned)strlen(other_fn));
        ios_steam_api_dbg_hook(other_text, (unsigned)strlen(other_text));
        ios_steam_api_dbg_hook(loaded_line, (unsigned)strlen(loaded_line));
        ios_steam_api_dbg_hook(steam32, (unsigned)strlen(steam32));
        ios_steam_api_dbg_hook(pipe_line, (unsigned)strlen(pipe_line));       /* [S_API FAIL] but another cause */
        ios_steam_api_dbg_hook(fail_only, (unsigned)strlen(fail_only));
        ios_steam_api_dbg_hook("short\n", 6);
        ios_steam_api_dbg_hook(line, 99);                    /* cut before the text: too short or no text */
        memset(longline, 'x', sizeof longline); memcpy(longline, line, strlen(line));
        ios_steam_api_dbg_hook(longline, 500);               /* over 400 bytes */
        printf("--- nothing above this line\n"); fflush(stdout);
        ios_steam_api_dbg_hook(line, (unsigned)strlen(line));      /* report 1 */
        ios_steam_api_dbg_hook(line, (unsigned)strlen(line));      /* report 2 */
        ios_steam_api_dbg_hook(line, (unsigned)strlen(line));      /* nothing: two per session */
        fflush(stdout); printf("--- end\n");
        return 0;
    }
    if (argc > 1 && !strcmp(argv[1], "hook-old"))
    {
        static const char old_line[] = "0044:err:seh:ios_exc_dump_str [exc] ml812   OutputDebugStringA: \"[S_API FAIL] SteamAPI_Init() failed; unable to locate a running instance of Steam, or a local steamclient64.dll..\"\n";
        dup2(1, 2);
        scenario_full = 1;
        ios_steam_api_dbg_hook(old_line, (unsigned)strlen(old_line));
        ios_steam_api_dbg_hook(old_line, (unsigned)strlen(old_line));
        ios_steam_api_dbg_hook(old_line, (unsigned)strlen(old_line));     /* nothing: two per session */
        fflush(stdout); printf("--- end\n");
        return 0;
    }
    if (argc > 1 && !strcmp(argv[1], "hook-mix"))
    {
        /* the 32-bit path's first report counts against the same two */
        static const char line[] = "00d8:err:seh:ios_exc_dump_str [exc] ml812   OutputDebugStringA: \"[S_API] SteamAPI_Init(): SteamAPI_IsSteamRunning() did not locate a running instance of Steam..\"\n";
        dup2(1, 2);
        scenario_full = 1;
        ios_steam_api_diag();                                /* the 32-bit path: ERR_ lines on stdout */
        fflush(stdout);
        ios_steam_api_dbg_hook(line, (unsigned)strlen(line));
        ios_steam_api_dbg_hook(line, (unsigned)strlen(line));
        fflush(stdout); printf("--- end\n");
        return 0;
    }
    scenario_full = argc > 1 && !strcmp(argv[1], "full");
    ios_steam_api_diag(); ios_steam_api_diag(); ios_steam_api_diag();      /* the third prints nothing */
    return 0;
}
'''
with tempfile.TemporaryDirectory() as t:
    c = Path(t) / "sd.c"; c.write_text(harness)
    exe = Path(t) / "sd"
    r = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-fsanitize=address,undefined",
                        str(c), "-o", str(exe)], capture_output=True, text=True)
    check("the region compiles on the host" + ("" if r.returncode == 0 else ": " + r.stderr[:600]), r.returncode == 0)
    if r.returncode == 0:
        def run(arg):
            p = subprocess.run([str(exe), arg], capture_output=True, text=True)
            assert p.returncode == 0, (arg, p.stderr)
            return p.stdout
        m = run("message")
        check("the test accepts steam_api's text and nothing else: " + m.strip(),
              m.splitlines()[0] == "steam=1 loaded=0 other=0 code=0 params=0" and
              m.splitlines()[1] == "null=0 bad_pointer=0 short=0 long=0")
        check("the older wording ([S_API FAIL] ... unable to locate a running instance of Steam) is accepted for the 32-bit and 64-bit "
              "steamclient names, and nothing near it is: " + m.splitlines()[2],
              m.splitlines()[2] == "old32=1 old64=1 pipe=0 running_failed=0 old_without_fail=0 old_wide=0")
        out = run("hook-old")
        check("the PE ntdll's line with the older wording makes two raw reports, not three",
              out.split("--- end")[0].count("[steam-api] steam_api found no running Steam; process 00d4 sees:") == 2)
        out = run("full")
        lines = [l for l in out.splitlines() if l.startswith("[steam-api]")]
        check("two reports, not three", sum(1 for l in lines if "found no running Steam" in l) == 2)
        one = out.split("found no running Steam", 2)[1]
        check("the pid, the client DLL, the paths and the process are reported",
              "HKCU ActiveProcess pid: DWORD 52" in one and "steamclient64.dll" in one and
              'HKCU Steam SteamPath: SZ "C:\\Program Files (x86)\\Steam"' in one and
              "HKLM(32) Steam SteamPID: DWORD 52" in one and
              "OpenProcess(52): opens, exit status 259 (259 = still running)" in one)
        check("the account id is reported only as not zero, never as a number",
              "ActiveUser: DWORD, not zero" in one and "123456789" not in out)
        check("a missing value or key, and the objects, are reported by name",
              "HKLM(32) Steam InstallPath: no value" in one and "HKLM(64) Steam InstallPath: key not opened" in one and
              "STEAM_DIPC_CONSUME: opens" in one and "STEAM_DIPC_PRODUCE: not opened" in one)
        out = run("hook")
        before, after = out.split("--- nothing above this line\n")
        check("the hook ignores every line that is not the PE ntdll's message (other function, other text, the loaded message, the 32-bit "
              "report's own lines, short and long lines)", "[steam-api]" not in before and before.strip() == "")
        rep = after.split("--- end")[0]
        check("the PE ntdll's line makes two raw reports, not three (no err:seh prefix, same text as the 32-bit path)",
              rep.count("[steam-api] steam_api found no running Steam; process 00d4 sees:") == 2 and "err:seh" not in rep
              and "HKCU ActiveProcess pid: DWORD 52" in rep and "STEAM_DIPC_CONSUME: opens" in rep
              and "123456789" not in rep and "ActiveUser: DWORD, not zero" in rep)
        out = run("hook-mix")
        check("the 32-bit path and the hook share the two reports", out.split("--- end")[0].count("found no running Steam") == 2)
        out = run("empty")
        one = out.split("found no running Steam", 2)[1]
        check("an empty registry: every key says it is not opened, no process line, objects not found",
              one.count("key not opened, status 0xc0000034") == 7 and "OpenProcess" not in one and
              one.count("not opened, status 0xc0000034") == 9)
print("PASS: steam api diag" if ok else "FAILED: steam api diag")
sys.exit(0 if ok else 1)
