#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[smc-loop]: one line when a run of identical faults reaches 256 (build 591, log only); no Wine runs.

Pro Evolution Soccer 2013 (DAN35T) handled "self-modifying code: pc: 16B038E5C fault: 710455C01C" 2,000 times in two seconds and the
redelivery guard ended it; Warcraft II BNE did the same. Nothing in the log said which instruction keeps writing, what the page is
(host protection, alias or not) or whether the code is the pool's. ios_smc_loop_diag prints that once per process at the 256th identical
delivery, next to "[redeliv] 256 identical redeliveries".

Source shape: called once, from the 256 mark of the Mach-path redelivery counter, for every build (not behind a switch: it only reads); it
reads through ios_fault_read_insn (a fault-safe Mach read), mach_vm_region and the lock-free alias lookup, and writes nothing. Compiled run
on macOS against real memory: the page's protection is printed, an alias is reported when the lookup finds one, and a second call prints
nothing.
Needs python3 and a C compiler on macOS.
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


f0 = src.index('static void ios_smc_loop_diag( uint64_t thread, uint64_t pc, uint64_t fault_addr, uint32_t code )')
f1 = src.index('\n}\n', f0) + 3
fn = src[f0:f1]
call = 'ios_smc_loop_diag( thread, pc, fault_addr, rec.ExceptionCode );'
check('called once, from the 256 mark, before the 2000 mark', src.count(call) == 1
      and src.index('else if (++redeliv[rslot].n == 256)') < src.index(call) < src.index('else if (redeliv[rslot].n >= 2000 && !ios_redeliv_terminating &&'))
check('one line per process', 'if (__sync_add_and_fetch( &said, 1 ) > 1) return;' in fn)
check('it reads and prints only', not [t for t in ('mach_vm_write', 'vm_write', 'mprotect', 'task_terminate', 'thread_suspend', 'memcpy', 'abort(', 'exit(') if t in fn])
check('the instruction is read through the fault-safe reader', 'ios_fault_read_insn( pc, &insn )' in fn)

if sys.platform != 'darwin':
    print('skip the compiled run: it reads real memory through mach_vm_region (macOS only)')
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
void *ios_jit_rx_base_global; size_t ios_jit_pool_size_global;
static int have_alias;
static uintptr_t ios_jit_anon_alias_lookup(uintptr_t a) { return have_alias ? a + 1 : 0; }
static int ios_fault_read_insn(uint64_t pc, uint32_t *out) { (void)pc; *out = 0xc8e8ff60; return 1; }
''' + fn + r'''
int main(int argc, char **argv)
{
    uint8_t *g = mmap(NULL, 0x8000, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    have_alias = argc > 1 && argv[1][0] == 'a';
    ios_jit_rx_base_global = (void *)0x150000000ull; ios_jit_pool_size_global = 0x1000000;
    ios_smc_loop_diag(0x1203, 0x150000040ull, (uint64_t)(uintptr_t)g + 0x1c, 0xc0000005);
    ios_smc_loop_diag(0x1203, 0x150000040ull, (uint64_t)(uintptr_t)g + 0x1c, 0xc0000005);   /* nothing */
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / 't.c'
        c.write_text(harness)
        exe = Path(tmp) / 't'
        cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wno-implicit-function-declaration', '-o', str(exe), str(c)], capture_output=True, text=True)
        check('the production function compiles on the host', cc.returncode == 0, cc.stderr[-2000:])
        if cc.returncode == 0:
            o = subprocess.run([str(exe), 'n'], capture_output=True, text=True).stdout
            check('no alias: the pool pc, the instruction, the code, the region protection and the "no alias" note, once',
                  o.count('[smc-loop] build591') == 1 and 'code c0000005' in o and 'host pc 0x150000040 (insn 0xc8e8ff60, in the JIT pool)' in o
                  and 'anonymous RWX alias: no' in o and 'prot=3 ' in o and '(no alias:' in o, o)
            o = subprocess.run([str(exe), 'a'], capture_output=True, text=True).stdout
            check('an alias is reported as such and the "no alias" note is not printed', 'anonymous RWX alias: yes' in o and '(no alias:' not in o, o)

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
