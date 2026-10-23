#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[alloc-fail] ml814 lines: unchanged up to 65536 failures, powers of two beyond (build 580); no Wine runs.

Spelunky 2 (DB0SA6, 8AGKA2, RC82CH on one iPad; builds 578/579): FMOD's thread 0xa8 asks for one 32 MB reservation
at hints of 7 to 8 TiB, outside BCD's 512 GB address ceiling, gets c0000018 and asks again, 21 to 225 million times.
The old rule printed the first 64 failures and then every 64th: 3.5 million lines, a 549 MB log that Send Log cannot
carry. The loop itself is not changed (see the report: no way to show a breaker would only ever act in that loop).

virtual_ios.c's condition is cut out of the source and compiled on the host:
  - for every failure count up to 65536 it prints exactly what the old condition printed (so a session of fewer
    failures gets the same log, and the auto-tune rule free-delay-off, which wants 200 such lines, sees the same
    number of lines: 200 lines is still ~8,700 failures);
  - beyond that only the powers of two print: 225,596,992 failures (RC82CH) are about 1,100 lines.
"""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


i = src.index('static unsigned long af_n;')
m = re.search(r'if \((n <= 64 .*?)\)\n\s+dprintf\( 2, "\[alloc-fail\] ml814 #%lu', src[i:], re.S)
check('the condition is there, in front of the [alloc-fail] ml814 line', bool(m))
cond = m.group(1) if m else '0'
harness = r'''
#include <stdio.h>
static int old_cond(unsigned long n) { return n <= 64 || (n % 64) == 0; }
static int new_cond(unsigned long n) { return @COND@; }
int main(void)
{
    unsigned long n, lines_new_65536 = 0, lines_old_65536 = 0, lines_total = 0, differ = 0;
    for (n = 1; n <= 65536; n++)
    {
        if (old_cond(n) != new_cond(n)) differ++;
        lines_old_65536 += old_cond(n); lines_new_65536 += new_cond(n);
    }
    printf("differ_up_to_65536=%lu lines_old=%lu lines_new=%lu\n", differ, lines_old_65536, lines_new_65536);
    for (n = 1; n <= 225596992ul; n++) lines_total += new_cond(n);
    printf("lines_for_225596992=%lu\n", lines_total);
    {
        unsigned long k, to200 = 0;
        for (k = 1; k <= 20000; k++) { if (new_cond(k)) to200++; if (to200 == 200) { printf("200th_line_at_failure=%lu\n", k); break; } }
    }
    n = 131072; printf("131072=%d 131073=%d 262144=%d 262145=%d 65600=%d 65536=%d\n", new_cond(n), new_cond(n + 1), new_cond(262144), new_cond(262145), new_cond(65600), new_cond(65536));
    return 0;
}
'''.replace('@COND@', cond)

with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run(['cc', '-O2', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the condition compiles on the host', cc.returncode == 0)
    if cc.returncode:
        print(cc.stderr)
    else:
        out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
        print('     ' + out.replace('\n', '\n     ').rstrip())
        vals = dict(re.findall(r'(\w+)=(\d+)', out))
        check('up to 65536 failures: the same failures print as before', vals.get('differ_up_to_65536') == '0')
        check('225,596,992 failures (RC82CH) print about a thousand lines, not 3.5 million',
              1000 <= int(vals.get('lines_for_225596992', 0)) <= 1200)
        check('the 200th line (free-delay-off wants 200) comes at the same failure count as before (8,768)',
              vals.get('200th_line_at_failure') == '8768')
        check('only the powers of two print beyond 65536',
              '131072=1 131073=0 262144=1 262145=0 65600=0 65536=1' in out)
check('the rest of the line is untouched (same format, same fields)',
      '"[alloc-fail] ml814 #%lu status=%08x hint=%p size=0x%llx type=0x%x "' in src
      and '"protect=0x%x tid=%04x | STAGE=%s err=%d at 0x%llx+0x%llx\\n"' in src)
print('PASS: alloc-fail throttle' if ok else 'FAIL: alloc-fail throttle')
raise SystemExit(0 if ok else 1)
