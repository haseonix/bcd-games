#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""bcrypt: an unaligned CBC length answers STATUS_INVALID_BUFFER_SIZE instead of reaching nettle (build 591); no Wine runs.

A Plague Tale: Innocence (3S5DV8, build 580; 7ZGYFT, build 576) ended after about 50 s of play with
    Assertion failed: (!(length % block_size)), function nettle_cbc_decrypt, file cbc.c, line 72.
GnuTLS passes a CBC cipher's input straight to nettle, whose assert aborts the whole app. Wine's bcrypt.dll hands the
unix side one block at a time and rejects an unaligned BCryptEncrypt/BCryptDecrypt itself (STATUS_INVALID_BUFFER_SIZE,
bcrypt_main.c), so the call that arrived with another length is not one Windows would have accepted.

dlls/bcrypt/gnutls.c (the Wine submodule; set WINE_SRC to a Wine tree to run this without the full BCD tree) now has
check_cbc_length(), called in key_symmetric_encrypt and key_symmetric_decrypt after init_cipher_handle and before the
GnuTLS call. The function is cut out of the source and compiled on the host, with the real enums of bcrypt_internal.h:
  - an AES key in CBC or ECB mode (ECB is CBC with an empty IV) and a 3DES key in CBC mode refuse a length that is not
    a multiple of 16 / 8 with STATUS_INVALID_BUFFER_SIZE, and take every multiple (and 0), as before;
  - AES in GCM or CFB mode (GnuTLS CFB8: any length), RC4 and every other key pass whatever the length;
  - a 3DES key in another mode passes too (init_cipher_handle has failed for it before the guard is reached);
  - only the first four refusals are logged.
"""
from pathlib import Path
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


wine = Path(os.environ.get('WINE_SRC') or (root / 'wine'))
gnutls = wine / 'dlls/bcrypt/gnutls.c'
internal = wine / 'dlls/bcrypt/bcrypt_internal.h'
if not gnutls.exists():
    print('note SKIPPED: no Wine sources here (%s); set WINE_SRC to a Wine tree' % gnutls)
else:
    src = gnutls.read_text()
    hdr = internal.read_text()

    def body(name, text=src):
        i = text.index('static NTSTATUS %s(' % name)
        j = text.index('\n}\n', i) + 3
        return text[i:j]

    for fn, call in (('key_symmetric_encrypt', 'pgnutls_cipher_encrypt2'), ('key_symmetric_decrypt', 'pgnutls_cipher_decrypt2')):
        b = body(fn)
        a, g, c = b.index('init_cipher_handle( params->key )'), b.index('check_cbc_length( params->key, params->input_len, __func__ )'), b.index(call)
        check('%s: init_cipher_handle, then check_cbc_length, then %s' % (fn, call), a < g < c)
        check('%s: a refused length returns at once (no GnuTLS call on that path)' % fn,
              'if ((status = check_cbc_length( params->key, params->input_len, __func__ ))) return status;' in b)
    guard = src[src.index('/* madeira-bcd, modified by BCD 2026-10-23'):]
    guard = guard[:guard.index('\nstatic NTSTATUS key_symmetric_encrypt')]
    fn = guard[guard.index('static NTSTATUS check_cbc_length'):]
    check('it answers STATUS_INVALID_BUFFER_SIZE (the status Windows gives; the tests in dlls/bcrypt/tests/bcrypt.c)',
          'return STATUS_INVALID_BUFFER_SIZE;' in fn)
    tests = (wine / 'dlls/bcrypt/tests/bcrypt.c').read_text()
    check('Wine\'s tests, run on Windows, expect that status for an input that is not a multiple of the block size',
          'ok(ret == STATUS_INVALID_BUFFER_SIZE' in tests and tests.count('input size is not a multiple of block size') >= 4)
    enums = ''.join(re.search(r'enum %s\s*\{.*?\};' % e, hdr, re.S).group(0) + '\n' for e in ('alg_id', 'chain_mode'))
    harness = r'''
#include <stdio.h>
typedef unsigned long ULONG;
typedef unsigned char UCHAR;
typedef unsigned int NTSTATUS;
#define STATUS_SUCCESS 0
#define STATUS_INVALID_BUFFER_SIZE ((NTSTATUS)0xC0000206)
#define ERR(...) do { printf("ERR "); printf(__VA_ARGS__); } while (0)
@ENUMS@
struct key_symmetric { enum chain_mode mode; ULONG block_size; UCHAR *vector; ULONG vector_len; UCHAR *secret; unsigned secret_len; };
struct key { enum alg_id alg_id; union { struct key_symmetric s; } u; };
@FUNC@
static const char *mode_names[] = { "CBC", "ECB", "CFB", "CCM", "GCM" };
static const char *st(NTSTATUS s) { return s == STATUS_SUCCESS ? "ok" : s == STATUS_INVALID_BUFFER_SIZE ? "REFUSED" : "other"; }
int main(void)
{
    static const unsigned lens[] = { 0, 1, 7, 8, 12, 15, 16, 17, 24, 31, 32, 48, 4096, 4097 };
    static const struct { enum alg_id alg; const char *name; } algs[] = { { ALG_ID_AES, "AES" }, { ALG_ID_3DES, "3DES" }, { ALG_ID_RC4, "RC4" }, { ALG_ID_SHA256, "SHA256" } };
    unsigned a, m, l;
    for (a = 0; a < 4; a++)
        for (m = CHAIN_MODE_CBC; m <= CHAIN_MODE_GCM; m++)
        {
            struct key k = { 0 };
            k.alg_id = algs[a].alg; k.u.s.mode = m; k.u.s.block_size = (algs[a].alg == ALG_ID_3DES) ? 8 : 16; k.u.s.secret_len = 16;
            NTSTATUS res[sizeof(lens) / sizeof(lens[0])];
            for (l = 0; l < sizeof(lens) / sizeof(lens[0]); l++) res[l] = check_cbc_length(&k, lens[l], "test");
            printf("%s-%s:", algs[a].name, mode_names[m]);
            for (l = 0; l < sizeof(lens) / sizeof(lens[0]); l++) printf(" %u=%s", lens[l], st(res[l]));
            printf("\n");
        }
    return 0;
}
'''.replace('@ENUMS@', enums).replace('@FUNC@', fn)
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / 't.c'
        c.write_text(harness)
        exe = Path(tmp) / 't'
        cc = subprocess.run(['cc', '-O2', '-Wall', '-Wno-unused-function', '-o', str(exe), str(c)], capture_output=True, text=True)
        check('check_cbc_length compiles on the host with the real enums', cc.returncode == 0)
        if cc.returncode:
            print(cc.stderr)
        else:
            out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
            rows = {}
            logs = [l for l in out.split('\n') if l.startswith('ERR ')]
            for l in out.split('\n'):
                m = re.match(r'([A-Z0-9]+-[A-Z]+):(.*)$', l)
                if m:
                    rows[m.group(1)] = dict(re.findall(r' (\d+)=(\w+)', m.group(2)))
            aligned16 = {'0', '16', '32', '48', '4096'}
            aligned8 = {'0', '8', '16', '24', '32', '48', '4096'}
            for mode in ('CBC', 'ECB'):
                r = rows['AES-' + mode]
                check('AES-%s: every multiple of 16 (and 0) passes, every other length is refused' % mode,
                      all((v == 'ok') == (k in aligned16) and v in ('ok', 'REFUSED') for k, v in r.items()))
            r = rows['3DES-CBC']
            check('3DES-CBC: every multiple of 8 (and 0) passes, every other length is refused',
                  all((v == 'ok') == (k in aligned8) and v in ('ok', 'REFUSED') for k, v in r.items()))
            check('AES-GCM and AES-CFB (CFB8) pass every length, as before',
                  all(v == 'ok' for mode in ('GCM', 'CFB') for v in rows['AES-' + mode].values()))
            check('AES-CCM (init_cipher_handle fails it first) and 3DES in the other modes pass the guard',
                  all(v == 'ok' for key in ('AES-CCM', '3DES-ECB', '3DES-CFB', '3DES-CCM', '3DES-GCM') for v in rows[key].values()))
            check('RC4 and a hash algorithm pass every length',
                  all(v == 'ok' for key, r in rows.items() if key.startswith(('RC4-', 'SHA256-')) for v in r.values()))
            check('only the first four refusals are logged, each naming the key and both lengths (%d lines)' % len(logs),
                  len(logs) == 4 and all('[bcrypt-guard] test:' in l and 'input_len' in l and 'STATUS_INVALID_BUFFER_SIZE' in l for l in logs))
            print('     ' + out.replace('\n', '\n     ').rstrip()[:1500])

# ---- the rule and its fixture (no Wine needed) ------------------------------------------------------------------
spec = importlib.util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
atr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(atr)
rule = {r['id']: r for r in atr.RULES}.get('nettle-cbc-unaligned-abort')
check('auto-tune says "known" (a fix in code, no line) from build 591 for the nettle assert, for encrypt and decrypt',
      rule and rule['lines'] == [] and rule['fixedIn'] == 591 and rule['priority'] == 1 and
      rule['all'] == [{'has': ['Assertion failed: (!(length % block_size))', 'nettle_cbc_']}])
fx = root / 'tests/fixtures/autotune/plague-tale-innocence-2026-10-22'
check('the fixture is the tester\'s log (the assert line) and its expect says known',
      'Assertion failed: (!(length % block_size)), function nettle_cbc_decrypt' in fx.with_suffix('.log').read_text() and
      '"status": "known"' in fx.with_suffix('.expect').read_text())
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-bcrypt-cbc-guard.py' in wf)
print('PASS: bcrypt CBC length guard' if ok else 'FAILED: bcrypt CBC length guard')
sys.exit(0 if ok else 1)
