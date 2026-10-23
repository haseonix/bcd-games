#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A hook behind the PE ntdll's log writes, so a 64-bit process can report what it sees of Steam (logging only).

The Rockstar Games Launcher says "SteamAPI_Init(): SteamAPI_IsSteamRunning() did not locate a running instance of
Steam.." and stops (RGM16D, 0YZKW6; 20 of the 40 Rockstar Dock sessions of the 577 wave). build/ntdll-unix/thread_ios.c
has a [steam-api] report for exactly that moment (what the registry, the Steam process and the IPC objects look like to
the failing process), but it is called from NtRaiseException, which only a 32-bit process reaches with
OutputDebugString: in a 64-bit (ARM64EC) process the exception is raised and handled inside the PE ntdll
(RtlRaiseException -> call_seh_handlers), and no log of a 64-bit process ever carried the report. The shipped ntdll.dll
is a committed binary the workflow does not rebuild; but it does print the text ("[exc] ml812   OutputDebugStringA: ...",
ios_exc_dump_str) and every PE log line reaches the unix side through unixcall_wine_dbg_write (dlls/ntdll/unix/debug.c).

This makes that function call ios_steam_api_dbg_hook( str, len ) (thread_ios.c, WINE_IOS only) AFTER it has written the
line. The hook answers at once for any line that is not that message and runs the report for that one. The write and its
return value are untouched.

Applied to the wine checkout (wine/dlls/ntdll/unix/debug.c) by CI before ntdll-unix is built; the submodule is not
committed to. Idempotent; exits non-zero when an anchor is missing. Not named patch-wine-*.py on purpose: the i386 farm's
cache key hashes that glob, and the farm (Wine's i386 PE DLLs) does not contain ntdll's unix side, so this file must not
make it rebuild.
Usage: patch-ntdll-unix-dbg-write-hook.py [path/to/wine/dlls/ntdll/unix/debug.c]
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/debug.c"
src = open(path).read()
marker = "madeira-bcd: ios_steam_api_dbg_hook"
if marker in src:
    print("patch-ntdll-unix-dbg-write-hook: already patched")
    sys.exit(0)

old = '''NTSTATUS unixcall_wine_dbg_write( void *args )
{
    struct wine_dbg_write_params *params = args;

    return write( 2, params->str, params->len );
}
'''
new = '''#ifdef WINE_IOS
/* madeira-bcd: ios_steam_api_dbg_hook (tools/patch-ntdll-unix-dbg-write-hook.py; thread_ios.c): the [steam-api] report for a
 * 64-bit process, triggered by the PE ntdll's own log line about steam_api's message. Called after the write. */
extern void ios_steam_api_dbg_hook( const char *str, unsigned int len );
#endif

NTSTATUS unixcall_wine_dbg_write( void *args )
{
    struct wine_dbg_write_params *params = args;
#ifdef WINE_IOS
    int ret = write( 2, params->str, params->len );
    ios_steam_api_dbg_hook( params->str, params->len );
    return ret;
#else
    return write( 2, params->str, params->len );
#endif
}
'''
if src.count(old) != 1:
    sys.exit("patch-ntdll-unix-dbg-write-hook: unixcall_wine_dbg_write anchor not found exactly once")
open(path, "w").write(src.replace(old, new))
print("patch-ntdll-unix-dbg-write-hook: applied")
