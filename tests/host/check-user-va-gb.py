#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_USER_VA_GB = n: a 64-bit game is told its user address space ends at n GiB (build 591); no Wine runs.

FINAL FANTASY TACTICS - The Ivalice Chronicles (DR8613 on build 580, 7QVF78 on 579) asks for one 256 GiB + 64 MiB NOACCESS reservation
at start, gets c0000017 (BCD's largest hole is 32 GiB) and quits with exit code 0. BCD tells every process HighestUserAddress =
0x7fffffffff (the 512 GB this device maps; GlobalMemoryStatusEx adds that up to ullTotalVirtual 524,287 MB), so the game plausibly sizes the
arena from it. The switch lowers only the figure virtual_get_system_info reports (SystemBasicInformation: GetSystemInfo's
lpMaximumApplicationAddress and GlobalMemoryStatusEx's ullTotalVirtual both read it); user_space_limit and every placement keep the
real limit.

The helpers are cut out of virtual_ios.c and compiled on the host with minimal PEB/parameter structures:
  - unset, empty, not a number or trailing junk: the figure is untouched;
  - n = 32: HighestUserAddress becomes 0x7ffffffff (32 GiB - 1) for a game; n below 4 is held to 4, above 4096 to 4096;
  - 0 is off;
  - the switch never RAISES the figure (n = 1024 on a 512 GB map changes nothing);
  - the Steam client, steamwebhelper.exe, SocialClubHelper.exe, Epic's web helper and the Dock host are not touched;
  - a 32-bit process (the wow64 branch) never gets there (source shape).
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


# ---------------------------------------------------------------- source shape
check('the switch is read once, by name', src.count('getenv( "MADEIRA_USER_VA_GB" )') == 1)
call = '    if (!wow64) ios_user_va_cap_report( info );'
check('one call, only for a 64-bit process, right after HighestUserAddress is set from user_space_limit and before the Social Club widening',
      src.count(call) == 1 and
      src.index('else info->HighestUserAddress = (char *)user_space_limit - 1;') < src.index(call) < src.index('madeira-bcd: Social Club layout 2 -- see ios_sc2_reported_highest'))
cl = src[src.index('static void ios_clamp_user_space_limit'):src.index('static void *get_host_addr_space_limit')]
check('ios_clamp_user_space_limit (the real limit) is untouched', 'MADEIRA_USER_VA_GB' not in cl and 'user_space_limit = host_addr_space_limit;' in cl)
check('user_space_limit is never assigned from the switch', 'user_space_limit = ' not in src[src.index('static ULONG_PTR ios_user_va_cap_bytes'):src.index('virtual_get_system_info( SYSTEM_BASIC_INFORMATION')])

# ---------------------------------------------------------------- compiled runs
h0 = src.index('static ULONG_PTR ios_user_va_cap_bytes(void)')
h1 = src.index('/***********************************************************************\n *           virtual_get_system_info')
helpers = src[h0:h1]

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef uint16_t WCHAR;
typedef uintptr_t ULONG_PTR;
typedef struct { uint16_t Length, MaximumLength; WCHAR *Buffer; } FAKE_USTR;
typedef struct { FAKE_USTR ImagePathName; } RTL_USER_PROCESS_PARAMETERS;
typedef struct { RTL_USER_PROCESS_PARAMETERS *ProcessParameters; } PEB;
typedef struct { void *LowestUserAddress, *HighestUserAddress; } SYSTEM_BASIC_INFORMATION;
#define dprintf(fd, ...) do { if (getenv("QUIET_LOG") == NULL) fprintf(stderr, __VA_ARGS__); } while (0)
static PEB fake_peb;
static RTL_USER_PROCESS_PARAMETERS fake_pp;
static void *ios_jit_current_peb(void) { return &fake_peb; }
''' + helpers + r'''
int main(int argc, char **argv)
{
    SYSTEM_BASIC_INFORMATION info;
    const char *image = argc > 1 ? argv[1] : "C:\\Program Files (x86)\\Steam\\steamapps\\common\\FF\\FFT_enhanced.exe";
    WCHAR w[300]; size_t i, n = strlen(image);

    for (i = 0; i < n; i++) w[i] = (unsigned char)image[i];
    fake_pp.ImagePathName.Buffer = w; fake_pp.ImagePathName.Length = (uint16_t)(n * 2);
    fake_peb.ProcessParameters = &fake_pp;
    info.LowestUserAddress = (void *)0x10000;
    info.HighestUserAddress = (void *)0x7fffffffffULL;
    ios_user_va_cap_report(&info);
    printf("%llx\n", (unsigned long long)(uintptr_t)info.HighestUserAddress);
    /* a second call (the figure is read many times a second) gives the same */
    info.HighestUserAddress = (void *)0x7fffffffffULL;
    ios_user_va_cap_report(&info);
    printf("%llx\n", (unsigned long long)(uintptr_t)info.HighestUserAddress);
    return 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wno-implicit-function-declaration', '-o', str(exe), str(c)],
                        capture_output=True, text=True)
    check('the production helpers compile on the host', cc.returncode == 0, cc.stderr[-3000:])
    if cc.returncode == 0:
        def run(value, image=None):
            env = dict(os.environ, QUIET_LOG='1')
            env.pop('MADEIRA_USER_VA_GB', None)
            if value is not None:
                env['MADEIRA_USER_VA_GB'] = value
            args = [str(exe)] + ([image] if image else [])
            r = subprocess.run(args, capture_output=True, text=True, env=env)
            return r.stdout.split()

        full = '7fffffffff'
        check('unset: untouched', run(None) == [full, full])
        check('empty: untouched', run('') == [full, full])
        check('not a number: untouched', run('abc') == [full, full])
        check('trailing junk: untouched', run('32GB') == [full, full])
        check('32 GiB: HighestUserAddress is 32 GiB - 1 (ullTotalVirtual 32 GiB less the first 64 KB), twice the same', run('32') == ['7ffffffff', '7ffffffff'])
        check('1 is held to 4 GiB', run('1') == ['ffffffff', 'ffffffff'])
        check('0 is off, as for the other switches', run('0') == [full, full])
        check('99999 is held to 4096 GiB, which is above this map: untouched', run('99999') == [full, full])
        check('512 GiB on a 512 GB map: nothing to lower', run('512') == [full, full])
        check('1024 GiB never raises the figure', run('1024') == [full, full])
        check('64 GiB lowers it to 64 GiB - 1', run('64') == ['fffffffff', 'fffffffff'])
        for image in ('C:\\Program Files (x86)\\Steam\\steam.exe', 'C:\\Program Files (x86)\\Steam\\bin\\cef\\cef.win64\\steamwebhelper.exe',
                      'C:\\Program Files (x86)\\Steam\\bin\\SteamService.exe', 'C:\\Program Files\\Rockstar Games\\Social Club\\SocialClubHelper.exe',
                      'C:\\Program Files (x86)\\Epic Games\\Launcher\\Portal\\Binaries\\EpicWebHelper.exe', 'C:\\windows\\system32\\dockhost.exe',
                      'D:\\x\\STEAMERRORREPORTER64.EXE'):
            check('not capped: ' + image.rsplit('\\', 1)[-1], run('32', image) == [full, full])
        for image in ('C:\\games\\FFT_enhanced.exe', 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Stardew Valley\\Stardew Valley.exe',
                      'C:\\windows\\system32\\explorer.exe'):
            check('capped: ' + image.rsplit('\\', 1)[-1], run('32', image) == ['7ffffffff', '7ffffffff'])

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
