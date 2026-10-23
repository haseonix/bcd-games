#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_NLS_EXTRA: Wine's extra code page tables, visible only to a game that asks (build 591); no Wine runs.

Sword and Fairy 3 (6FW5J5, build 580) asks for the Simplified-Chinese ANSI code page ("[nls-getptr] type=11 id=936"); BCD's nls
folder holds only c_1252, c_437, c_20127 and c_28591, NtGetNlsSectionPtr (build/ntdll-unix/env_ios.c) answers
STATUS_OBJECT_NAME_NOT_FOUND, GetCPInfo(936) fails and the game's own code then reads the NULL page. Windows (and Wine on Linux) always
has the table. Working sessions ask for such pages too and run without them (every BCD Dock session's Steam client asks for 932; 12
games ran without a 1254 / 1251 / 1256 table), so the tables are NOT put next to the others:

  - app/Madeira/nls/extra/ holds Wine's own c_874, c_932, c_936, c_949, c_950, c_1250, c_1251 and c_1253 ... c_1258 (about 1.3 MB
    in the app, 0.3 MB in the IPA); the Xcode project's `nls` folder reference ships the subfolder as it is;
  - get_nls_file_path() in env_ios.c looks there only when env.MADEIRA_NLS_EXTRA is exactly "1" and nls/ has no table of that name;
    unset (or anything else) the path is the one it always was and the request fails as before;
  - the hook is cut out of env_ios.c and compiled on the host (the real source file; NLS_ENV_IOS points the check at another copy).

env_ios.c and WineProcessBridge.m (the unsetenv at the session's end) belong to other agents (the brief of ajan10/wine): the hook is a
patch, ajan10-wine-nls-env_ios.patch next to the report; this check FAILS until it is applied, on purpose (a switch without its hook
would only look finished).
"""
from pathlib import Path
import importlib.util
import os
import re
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


# ---- the data ----------------------------------------------------------------------------------------------------------------
PAGES = [874, 932, 936, 949, 950, 1250, 1251, 1253, 1254, 1255, 1256, 1257, 1258]
nls = root / 'app/Madeira/nls'
extra = nls / 'extra'
have = sorted(p.name for p in extra.glob('*.nls')) if extra.is_dir() else []
check('nls/extra holds exactly Wine\'s c_874, c_932, c_936, c_949, c_950, c_1250, c_1251 and c_1253 ... c_1258 (%d files)' % len(have),
      have == sorted('c_%d.nls' % n for n in PAGES))
bad = []
for n in PAGES:
    f = extra / ('c_%d.nls' % n)
    if not f.exists():
        continue
    d = f.read_bytes()
    off, cp, maxchar = struct.unpack_from('<HHH', d, 0)
    if off != 0x0d or cp != n or maxchar != (2 if n in (932, 936, 949, 950) else 1) or len(d) < 0x1000:
        bad.append((n, off, cp, maxchar, len(d)))
check('each file is a code page table of its own number (header: offset 0x0d, CodePage, MaximumCharacterSize 1, or 2 for the DBCS pages)', not bad)
wine = Path(os.environ.get('WINE_SRC') or (root / 'wine'))
if (wine / 'nls/c_936.nls').exists():
    check('each file is byte for byte Wine\'s own nls/c_N.nls (nothing derived, nothing renamed)',
          all((extra / ('c_%d.nls' % n)).read_bytes() == (wine / ('nls/c_%d.nls' % n)).read_bytes() for n in PAGES))
else:
    print('note SKIPPED: no Wine sources here (set WINE_SRC); the files are not compared with Wine\'s nls/')
top = sorted(p.name for p in nls.iterdir() if p.is_file())
check('nls/ itself is unchanged: c_1252, c_20127, c_28591, c_437 and the locale, sort and normalisation tables (no extra page next to them)',
      top == sorted(['c_1252.nls', 'c_20127.nls', 'c_28591.nls', 'c_437.nls', 'l_intl.nls', 'locale.nls', 'normidna.nls', 'normnfc.nls',
                     'normnfd.nls', 'normnfkc.nls', 'normnfkd.nls', 'sortdefault.nls']))
total = sum((extra / n).stat().st_size for n in have)
print('     added to the app: %d bytes (%.2f MiB) in %d files' % (total, total / 1048576, len(have)))
check('under 1.5 MB, as announced', total < 1_500_000)
pbx = (root / 'app/Madeira.xcodeproj/project.pbxproj').read_text()
check('the Xcode project ships the nls folder as a folder reference (the subfolder comes with it; no project change needed)',
      'A2000021 /* nls */ = {isa = PBXFileReference; lastKnownFileType = folder; path = nls; sourceTree = "<group>"; };' in pbx and
      'A1000022 /* nls in Resources */' in pbx)

# ---- the hook in env_ios.c ---------------------------------------------------------------------------------------------------
env_ios = Path(os.environ.get('NLS_ENV_IOS') or (root / 'build/ntdll-unix/env_ios.c'))
src = env_ios.read_text()
hooked = 'MADEIRA_NLS_EXTRA' in src


def func(name):
    i = src.index('static char *%s(' % name)
    return src[i:src.index('\n}\n', i) + 3]


if not hooked:
    check('env_ios.c has the MADEIRA_NLS_EXTRA hook -- NOT YET: apply ~/madeira-local/intake/analysis/tur591/ajan10-wine-nls-env_ios.patch '
          '(git apply; the file is the engine agent\'s, so the hook came as a patch)', False)
else:
    check('env_ios.c reads the switch with getenv at every request (not cached: it ends with the session), only for code pages',
          src.count('getenv( "MADEIRA_NLS_EXTRA" )') == 1 and 'if (type == NLS_SECTION_CODEPAGE) path = nls_extra_path( dir, name, path );' in src)
    check('the lookup only looks in nls/extra when the switch is exactly "1" and nls/ has no table of that name',
          'if (!e || strcmp( e, "1" ) || !access( path, R_OK )) return path;' in func('nls_extra_path'))
    check('it logs [nls-extra] (16 lines at most) and, log only, [nls-missing] for a code page not found (8 at most)',
          'reports++ < 16' in func('nls_extra_path') and 'missing_reports++ < 8' in src and '"[nls-missing] code page %u has no table' in src)
    bridge = Path(os.environ.get('NLS_BRIDGE') or (root / 'app/Madeira/WineProcessBridge.m')).read_text()
    check('the switch ends with the session (WineProcessBridge.m unsets it; a later game must not see the tables)',
          'unsetenv("MADEIRA_NLS_EXTRA");' in bridge)
    locale_private = wine / 'dlls/ntdll/locale_private.h'
    values = {'NLS_SECTION_SORTKEYS': 9, 'NLS_SECTION_CASEMAP': 10, 'NLS_SECTION_CODEPAGE': 11, 'NLS_SECTION_NORMALIZE': 12}
    if locale_private.exists():
        t = locale_private.read_text()
        check('the harness uses Wine\'s NLS_SECTION_* values',
              all(re.search(r'%s\s*=\s*%d\b' % (k, v), t) for k, v in values.items()))
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
typedef unsigned int UINT;
enum nls_section_type { NLS_SECTION_SORTKEYS = 9, NLS_SECTION_CASEMAP = 10, NLS_SECTION_CODEPAGE = 11, NLS_SECTION_NORMALIZE = 12 };
enum { NormalizationC = 0x1, NormalizationD = 0x2, NormalizationKC = 0x5, NormalizationKD = 0x6 };
static const char *build_dir, *data_dir;
@FUNCS@
static void show(const char *label, UINT type, UINT id)
{
    char *p = get_nls_file_path( type, id );
    printf( "%s=%s\n", label, p ? p : "(null)" );
    free( p );
}
int main(int argc, char **argv)
{
    unsigned i;
    data_dir = argv[1]; build_dir = NULL;
    show( "cp1252", NLS_SECTION_CODEPAGE, 1252 );
    show( "cp437", NLS_SECTION_CODEPAGE, 437 );
    show( "cp936", NLS_SECTION_CODEPAGE, 936 );
    show( "cp932", NLS_SECTION_CODEPAGE, 932 );
    show( "cp1254", NLS_SECTION_CODEPAGE, 1254 );
    show( "cp999", NLS_SECTION_CODEPAGE, 999 );
    show( "casemap", NLS_SECTION_CASEMAP, 0 );
    show( "sortkeys", NLS_SECTION_SORTKEYS, 0 );
    show( "nfc", NLS_SECTION_NORMALIZE, NormalizationC );
    show( "idna", NLS_SECTION_NORMALIZE, 13 );
    for (i = 0; i < 40; i++) { char *p = get_nls_file_path( NLS_SECTION_CODEPAGE, 936 ); free( p ); }
    return 0;
}
'''.replace('@FUNCS@', func('nls_extra_path') + '\n' + func('get_nls_file_path'))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()
        d = tmp / 'app'
        (d / 'nls/extra').mkdir(parents=True)
        for n in ('c_1252', 'c_437', 'l_intl', 'sortdefault', 'normnfc'):
            (d / 'nls' / (n + '.nls')).write_bytes(b'x')
        for n in ('c_936', 'c_932', 'c_1254', 'c_1252'):      # c_1252 twice: the one in nls/ must win
            (d / 'nls/extra' / (n + '.nls')).write_bytes(b'x')
        c = tmp / 't.c'
        c.write_text(harness)
        exe = tmp / 't'
        cc = subprocess.run(['cc', '-O2', '-Wall', '-Wno-unused-function', '-o', str(exe), str(c)], capture_output=True, text=True)
        check('the lookup functions compile on the host (the real source of env_ios.c)', cc.returncode == 0)
        if cc.returncode:
            print(cc.stderr)
        else:
            def run(value):
                env = {k: v for k, v in os.environ.items() if k != 'MADEIRA_NLS_EXTRA'}
                if value is not None:
                    env['MADEIRA_NLS_EXTRA'] = value
                r = subprocess.run([str(exe), str(d)], capture_output=True, text=True, env=env)
                return dict(l.split('=', 1) for l in r.stdout.splitlines()), r.stderr
            base = str(d / 'nls')
            same = {k: base + '/' + v for k, v in (('cp1252', 'c_1252.nls'), ('cp437', 'c_437.nls'), ('cp936', 'c_936.nls'),
                    ('cp932', 'c_932.nls'), ('cp1254', 'c_1254.nls'), ('cp999', 'c_999.nls'), ('casemap', 'l_intl.nls'),
                    ('sortkeys', 'sortdefault.nls'), ('nfc', 'normnfc.nls'), ('idna', 'normidna.nls'))}
            for value in (None, '', '0', 'yes', '11', ' 1', '1 '):
                got, err = run(value)
                check('switch %s: every path is the one it always was, and nothing is logged' % ('unset' if value is None else repr(value)),
                      got == same and err == '')
            got, err = run('1')
            want = dict(same)
            want.update({'cp936': base + '/extra/c_936.nls', 'cp932': base + '/extra/c_932.nls', 'cp1254': base + '/extra/c_1254.nls'})
            check('switch 1: 936, 932 and 1254 come from nls/extra', all(got[k] == want[k] for k in ('cp936', 'cp932', 'cp1254')))
            check('switch 1: a table nls/ has (1252, 437) stays where it is, even with a copy in nls/extra', got['cp1252'] == want['cp1252'] and got['cp437'] == want['cp437'])
            check('switch 1: a page nowhere (999) keeps the path that fails, as before; case map, sort keys and normalisation are untouched',
                  all(got[k] == want[k] for k in ('cp999', 'casemap', 'sortkeys', 'nfc', 'idna')))
            lines = [l for l in err.split('\n') if l]
            check('switch 1: 43 requests for extra tables log 16 [nls-extra] lines at most (%d), each naming the file' % len(lines),
                  0 < len(lines) <= 16 and all(l.startswith('[nls-extra] c_') and 'MADEIRA_NLS_EXTRA=1' in l for l in lines))
            check('the files the lookup returns exist in the app\'s nls/extra for every page of the list',
                  all((extra / ('c_%d.nls' % n)).exists() for n in PAGES))

# ---- catalog, auto-tune, CI -------------------------------------------------------------------------------------------------
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
ov = gen[gen.index('"env.MADEIRA_NLS_EXTRA": {'):] if '"env.MADEIRA_NLS_EXTRA": {' in gen else ''
ov = ov[:ov.index('"sources"')] if ov else ''
check('the catalog documents env.MADEIRA_NLS_EXTRA as a bool that is off by default', '"kind": "bool", "default": "0"' in ov and 'Default off.' in ov)
spec = importlib.util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
atr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(atr)
rule = {r['id']: r for r in atr.RULES}.get('nls-extra-codepage')
check('auto-tune: one rule, one line, only for a failed direct start that asked for 932, 936, 949, 950 or 874, not once the tables are in use',
      rule and rule['lines'] == ['env.MADEIRA_NLS_EXTRA = 1'] and rule['session'] == 'library' and
      set(rule['outcomes']) == {'crashed', 'exited-early'} and rule['priority'] == 60 and
      [sg['has'][0] for sg in rule['any']] == ['[nls-getptr] type=11 id=%d ' % n for n in (932, 936, 949, 950, 874)] and
      rule['none'] == [{'has': ['[nls-extra]']}])
fx = root / 'tests/fixtures/autotune/sword-and-fairy-3-2026-10-22'
check('its fixture is the tester\'s log (id=936 requested) and proves the rule',
      '[nls-getptr] type=11 id=936 ' in fx.with_suffix('.log').read_text() and '"nls-extra-codepage"' in fx.with_suffix('.expect').read_text())
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-nls-extra.py' in wf)
print('PASS: extra code page tables' if ok else 'FAILED: extra code page tables')
sys.exit(0 if ok else 1)
