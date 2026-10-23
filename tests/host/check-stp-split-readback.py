#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[stp-split] prints the source bytes and a read-back of the destination (build 591, log only); no Wine runs.

Need for Speed Underground 2 (RT2RHK, 5H4043, 4M13T0) stops on an INT3 in SPEED2.EXE right after the first "[stp-split] build580" line, in
three runs of one tester. The logs cannot tell a split store that left wrong bytes from a game that breaks on its own, so the line now
comes with the 32 source bytes and what the memory holds afterwards, read through the guest addresses (the RX view, the plain page) and
through the alias pointers the store wrote to. This check compiles the production function on the host against real memory (the host
check reads its own task) and shows: equal bytes say "= the source" twice; a byte that differs through the guest address says so and
names the first one; an alias that differs says so; an unreadable part is reported as unreadable. Textually: it is called only for the
first four lines of the success branch, and reads only.
Needs python3 and a C compiler on macOS (mach_vm_read_overwrite).
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


f0 = src.index('static void ios_hex_bytes( char *out, const uint8_t *p, unsigned n )')
f1 = src.index('\n}\n', src.index('static void ios_stp_split_readback')) + 3
fn = src[f0:f1]
forbidden = ('mach_vm_write', 'vm_write', 'mprotect', 'task_terminate', 'thread_suspend', 'abort(', 'exit(')
check('the read-back reads and prints only', not [t for t in forbidden if t in fn])
call = 'if (stp_split_n <= 4) ios_stp_split_readback( sp, sn, sbytes );'
check('called once, inside the success branch, for the first four lines only', src.count(call) == 1
      and src.index('stp_split = 1;') < src.index(call) < src.index('[stp-split] build580 #%d insn=0x%08x addr=0x%llx REFUSED'))

if sys.platform != 'darwin':
    print('skip the compiled runs: they read real memory through mach_vm_read_overwrite (macOS only)')
else:
    harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#define STDERR_FILENO 2
#define dprintf(fd, ...) printf(__VA_ARGS__)
#include "ios_store_split.h"
''' + fn + r'''
int main(int argc, char **argv)
{
    uint8_t *g = mmap(NULL, 0x20000, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    uint8_t alias[64], src[32];
    ios_store_part sp[2];
    int mode = argc > 1 ? atoi(argv[1]) : 0, i;

    for (i = 0; i < 32; i++) src[i] = (uint8_t)(0x10 + i);
    /* NFSU2: 28 bytes before the boundary (an aliased page), 4 after it (a plain page) */
    sp[0].guest = (uintptr_t)g + 0x4000 - 28; sp[0].dst = (uintptr_t)alias; sp[0].len = 28; sp[0].off = 0; sp[0].direct = 0;
    sp[1].guest = (uintptr_t)g + 0x4000; sp[1].dst = sp[1].guest; sp[1].len = 4; sp[1].off = 28; sp[1].direct = 1;
    memcpy(g + 0x4000 - 28, src, 28); memcpy(g + 0x4000, src + 28, 4);   /* what the commit leaves */
    memcpy(alias, src, 28);
    if (mode == 1) g[0x4003] ^= 0x55;                                     /* the plain page holds a wrong byte */
    if (mode == 2) alias[5] ^= 0x01;                                      /* the alias holds a wrong byte */
    if (mode == 3) sp[1].guest = 0x10;                                    /* an unreadable part */
    ios_stp_split_readback(sp, 2, src);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / 't.c'
        c.write_text(harness)
        exe = Path(tmp) / 't'
        cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-I', str(root / 'build/ntdll-unix'), '-o', str(exe), str(c)], capture_output=True, text=True)
        check('the production function compiles on the host', cc.returncode == 0, cc.stderr[-2000:])
        if cc.returncode == 0:
            def run(mode):
                return subprocess.run([str(exe), str(mode)], capture_output=True, text=True).stdout
            hexsrc = ''.join('%02x' % (0x10 + i) for i in range(32))
            o = run(0)
            check('equal bytes: the source, the guest read and the alias read are the same and say so',
                  o.count(hexsrc) == 3 and o.count('= the source') == 2 and 'DIFFERENT' not in o, o)
            o = run(1)
            check('a wrong byte in the plain page (a direct part, which has no alias: the alias line repeats the guest read) is reported with its '
                  'index (31 is the last byte of the store)',
                  o.count('DIFFERENT from the source') == 2 and 'first byte that differs through the guest address: 31' in o, o)
            o = run(2)
            check('a wrong byte in the alias is reported through the alias read only', o.count('DIFFERENT from the source') == 1 and 'alias read' in o.split('DIFFERENT')[0].splitlines()[-1], o)
            o = run(3)
            check('an unreadable part is reported as unreadable', '(a part could not be read)' in o, o)

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
