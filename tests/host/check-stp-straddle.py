#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 32-byte STP Q that crosses a 16 KB page boundary is stored page by page (build 580); no Wine runs.

Need for Speed Underground 2 (7F9ZRF, build 579): FEX's `STP Q16, Q17, [X10], #32` (0xac810421) at
0x710ee13fe4 writes 28 bytes into an anonymous RWX page (physically r-x, written through an alias) and
4 bytes into the next 16 KB page, an ordinary RW page with no alias (Wine's protections: this page RWX,
the next RW; the host region ends at +0x4000). ml629's Q-pair emulation wanted all 32 bytes in one alias
run and refused ("REFUSING: 32B span leaves the alias"); the refused store was never completed, so it
faulted 2000 times and the redelivery guard ended the process. build/ntdll-unix/ios_store_split.h cuts the
store at the 16 KB boundary; signal_arm64_ios.c uses it in the refusing branch only (anonymous alias).

Compiled runs: the production header against a simulated guest address space (aliased pages, an unaliased
writable page, an unaliased read-only page, an unmapped hole):
  - the NFSU2 geometry: 28 bytes through the alias, 4 bytes directly, every byte in its place;
  - the split at every start offset 1..31 bytes before the boundary, with a distinct byte pattern;
  - a store that ends exactly on the boundary, and one that does not cross it, stay one part;
  - the next page with its own alias is written through that alias and no direct write happens;
  - a next page that is read-only, or unmapped: the commit fails and NOTHING has been written (not even the
    aliased part), the same refusal as before the change;
  - an alias run that covers the first byte of a part but not its last is refused; more than 32 bytes, zero
    bytes and two unaliased parts are refused.
And textually, in signal_arm64_ios.c: the new code sits inside the refusing branch (`!stp_whole`), only for
`!in_jit` (the JIT pool is untouched), the direct write is mach_vm_write (it reports a page it cannot write
instead of faulting on the Mach exception thread), the accepted whole-alias path still does its two 16-byte
copies and the writeback follows both.
Needs python3 and a C compiler.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
header = root / 'build/ntdll-unix/ios_store_split.h'
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include "ios_store_split.h"

static int failures;
#define EXPECT(c, msg) do { if (!(c)) { printf("FAIL %s\n", msg); failures++; } } while (0)

/* The simulated guest address space: 5 pages of 16 KB at G.
 *   page 0: aliased (alias A0)                 -- the page the store faults in
 *   page 1: kind set per test (aliased by A1 / plain writable / read-only / unmapped)
 *   page 2: aliased, contiguous alias run with page 0 in the "one run" tests */
#define G 0x710ee10000ull
enum { ALIAS, WRITABLE, READONLY, UNMAPPED };
static int kind[5];
static uint8_t alias_buf[5][0x4000];   /* what an alias of that page writes */
static uint8_t direct_buf[5][0x4000];  /* what a direct write to that page writes */
static int direct_calls, partial_page0_end;   /* partial_page0_end: alias run of page 0 stops this many bytes early */

static int page_of(uintptr_t a) { return (int)((a - G) >> 14); }

static uintptr_t resolve(void *ctx, uintptr_t guest, unsigned len, int *direct)
{
    int p = page_of(guest);
    (void)ctx;
    if (kind[p] == ALIAS)
    {
        uintptr_t off = (guest - G) & 0x3fff;
        /* the alias run of a page ends at its page end; partial_page0_end shortens page 0's run */
        if (p == 0 && partial_page0_end && off + len > 0x4000 - partial_page0_end) return 0;
        *direct = 0;
        return (uintptr_t)&alias_buf[p][off];
    }
    *direct = 1;
    return guest;
}

static int dwrite(void *ctx, uintptr_t guest, const void *s, unsigned len)
{
    int p = page_of(guest);
    uintptr_t off = (guest - G) & 0x3fff;
    (void)ctx;
    direct_calls++;
    if (kind[p] != WRITABLE) return 0;
    memcpy(&direct_buf[p][off], s, len);
    return 1;
}

static void reset(int k1)
{
    memset(alias_buf, 0, sizeof alias_buf);
    memset(direct_buf, 0, sizeof direct_buf);
    kind[0] = ALIAS; kind[1] = k1; kind[2] = ALIAS; kind[3] = ALIAS; kind[4] = ALIAS;
    direct_calls = 0; partial_page0_end = 0;
}

static int run(uintptr_t addr, unsigned bytes, const uint8_t *data, ios_store_part *parts, int *np)
{
    *np = ios_store_split_plan(addr, bytes, resolve, NULL, parts);
    if (!*np) return -1;
    return ios_store_split_commit(parts, *np, data, dwrite, NULL);
}

int main(void)
{
    uint8_t data[32]; int i, np; ios_store_part parts[2];
    for (i = 0; i < 32; i++) data[i] = (uint8_t)(0xa0 + i);

    /* NFSU2: 0x710ee13fe4 + 32 -> 28 bytes in page 0 (alias), 4 bytes in page 1 (plain writable page) */
    reset(WRITABLE);
    EXPECT(run(G + 0x3fe4, 32, data, parts, &np) == 1, "NFSU2 store is committed");
    EXPECT(np == 2 && parts[0].len == 28 && !parts[0].direct && parts[1].len == 4 && parts[1].direct, "28 aliased + 4 direct");
    EXPECT(memcmp(&alias_buf[0][0x3fe4], data, 28) == 0, "bytes 0..27 landed through the alias at the right offset");
    EXPECT(memcmp(&direct_buf[1][0], data + 28, 4) == 0, "bytes 28..31 landed at the start of the next page");
    EXPECT(direct_calls == 1, "exactly one direct write");
    {
        int clean = 1;
        for (i = 0; i < 0x3fe4; i++) if (alias_buf[0][i]) clean = 0;
        for (i = 4; i < 0x4000; i++) if (direct_buf[1][i]) clean = 0;
        EXPECT(clean, "nothing else was written");
    }

    /* every straddling start: 1..31 bytes before the boundary */
    for (i = 1; i <= 31; i++)
    {
        reset(WRITABLE);
        uint8_t exp0[32], exp1[32];
        EXPECT(run(G + 0x4000 - i, 32, data, parts, &np) == 1 && np == 2, "straddling store committed");
        memset(exp0, 0, sizeof exp0); memset(exp1, 0, sizeof exp1);
        memcpy(exp0, data, i);
        memcpy(exp1, data + i, 32 - i);
        EXPECT(memcmp(&alias_buf[0][0x4000 - i], exp0, i) == 0, "first part");
        EXPECT(memcmp(&direct_buf[1][0], exp1, 32 - i) == 0, "second part");
        EXPECT(parts[0].len == (unsigned)i && parts[1].len == (unsigned)(32 - i) && parts[1].off == (unsigned)i, "lengths and offsets");
    }

    /* ends exactly on the boundary: one part */
    reset(WRITABLE);
    EXPECT(run(G + 0x3fe0, 32, data, parts, &np) == 1 && np == 1 && direct_calls == 0, "ends on the boundary: one aliased part");
    EXPECT(memcmp(&alias_buf[0][0x3fe0], data, 32) == 0, "all 32 bytes through the alias");
    /* inside a page: one part */
    reset(WRITABLE);
    EXPECT(run(G + 0x100, 32, data, parts, &np) == 1 && np == 1 && direct_calls == 0, "inside a page: one part");

    /* the next page has its own alias: both through aliases, no direct write */
    reset(ALIAS);
    EXPECT(run(G + 0x3ff0, 32, data, parts, &np) == 1 && np == 2 && direct_calls == 0 && !parts[0].direct && !parts[1].direct,
           "next page aliased: both parts through aliases");
    EXPECT(memcmp(&alias_buf[0][0x3ff0], data, 16) == 0 && memcmp(&alias_buf[1][0], data + 16, 16) == 0, "bytes in place");

    /* the next page cannot be written: nothing written at all, the aliased part included */
    reset(READONLY);
    EXPECT(run(G + 0x3fe4, 32, data, parts, &np) == 0 && np == 2, "read-only next page: the commit fails");
    {
        int clean = 1;
        for (i = 0; i < 0x4000; i++) if (alias_buf[0][i] || direct_buf[1][i]) clean = 0;
        EXPECT(clean, "read-only next page: not a byte was written, the aliased part included");
    }
    reset(UNMAPPED);
    EXPECT(run(G + 0x3fe4, 32, data, parts, &np) == 0, "unmapped next page: the commit fails");
    {
        int clean = 1;
        for (i = 0; i < 0x4000; i++) if (alias_buf[0][i]) clean = 0;
        EXPECT(clean, "unmapped next page: nothing written");
    }

    /* an alias run that stops short of the store's end inside the first page: refused */
    reset(WRITABLE); partial_page0_end = 8;
    EXPECT(run(G + 0x3fe4, 32, data, parts, &np) == -1 && np == 0, "alias run shorter than the part: no plan");

    /* odd sizes */
    reset(WRITABLE);
    EXPECT(ios_store_split_plan(G + 0x100, 33, resolve, NULL, parts) == 0, "more than 32 bytes: no plan");
    EXPECT(ios_store_split_plan(G + 0x100, 0, resolve, NULL, parts) == 0, "zero bytes: no plan");
    /* two unaliased parts: a refusal could leave half the store written, so no plan */
    reset(WRITABLE); kind[0] = WRITABLE;
    EXPECT(ios_store_split_plan(G + 0x3ff0, 32, resolve, NULL, parts) == 0, "two direct parts: no plan");
    /* a 16-byte store straddling (not what STP Q does, but the helper is general) */
    reset(WRITABLE);
    EXPECT(run(G + 0x3ffc, 16, data, parts, &np) == 1 && np == 2 && parts[0].len == 4 && parts[1].len == 12, "16-byte store: 4 + 12");

    if (failures) { printf("%d failure(s)\n", failures); return 1; }
    printf("all checks passed\n");
    return 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 'stp_straddle.c'
    c.write_text(harness)
    exe = Path(tmp) / 'stp_straddle'
    cc = subprocess.run(['cc', '-O1', '-Wall', '-I', str(header.parent), '-o', str(exe), str(c)],
                        capture_output=True, text=True)
    check('the production header compiles on the host', cc.returncode == 0)
    if cc.returncode:
        print(cc.stderr)
    else:
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        for line in run.stdout.splitlines():
            print('     ' + line)
        check('the compiled checks pass', run.returncode == 0)

# ---------------------------------------------------------------- the integration, textually
inc = '#include "ios_store_split.h"'
check('the header is included once, before the Mach exception thread',
      src.count(inc) == 1 and src.index(inc) < src.index('static void *ios_mach_exception_thread( void *arg )'))
direct = src[src.index('static int ios_store_split_direct_write('):]
direct = direct[:direct.index('\n}\n')]
check('the direct write is mach_vm_write (reports a page it cannot write instead of faulting)',
      'mach_vm_write( mach_task_self()' in direct and 'memcpy' not in direct and '*(uint' not in direct)
q = src.index('const int mode = (insn >> 23) & 0x3; /* 0 stnp, 1 post, 2 offset, 3 pre */')
body = src[q:src.index('/* STR (immediate, unsigned offset, 64-bit): 1111 1001 00 imm12 Rn Rt */', q)]
check('the refusal condition is the old one (the whole 32 bytes in one alias run)',
      'const int stp_whole = rw_end && rw_end == (uintptr_t)rw_addr + 31;' in body and 'if (!stp_whole)\n' in body)
split_at = body.index('ios_store_split_plan(')
refuse = body.index('if (!stp_whole)')
accept = body.index('if (stp_whole || stp_split)')
check('the split is inside the refusing branch and before the writeback', refuse < split_at < accept)
check('only for an anonymous alias: the JIT pool case is not touched', body.rindex('if (!in_jit)', refuse, split_at) > 0)
check('the old two 16-byte copies remain for the whole-alias case',
      'if (!stp_split)\n                            {\n                                memcpy((void *)rw_addr, &neon_state.__v[rt], 16);\n'
      '                                memcpy((void *)(rw_addr + 16), &neon_state.__v[rt2], 16);' in body)
check('the base writeback follows both ways', body.index('if (wb)', accept) > accept and 'state.__x[rn] = base_new' in body)
check('a refusal is logged (capped)', 'stp_split_fail_n < 4' in body and 'stp_split_n < 8' in body)

print('PASS: stp straddle' if ok else 'FAIL: stp straddle')
raise SystemExit(0 if ok else 1)
