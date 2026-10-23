#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
#
# Builds BCD Host (bcd-host/) as a stripped x86-64 PE and stages it in the app bundle:
#   app/Madeira/arm64ec-windows/bcdhost.exe
# A build output (gitignored). The app starts it as C:\windows\system32\bcdhost.exe for a store game
# it starts signed in (Epic Games, docs/research/open-fork-2026-10-18/epic.md section 4.5); without it
# a signed-in start is not offered and every game starts as before.
#
# The host is freestanding: kernel32 only, no C runtime (so no runtime notices to ship), GUI subsystem
# (no console), relocatable, stripped, no PE timestamp (reproducible).
#
# Usage: build/bcd-host/build.sh [--check] [--no-install]
#   --check       first build and run the host tests (bcd-host/tests/check.c) with the host compiler
#                 (AddressSanitizer and UBSan when the compiler has them)
#   --no-install  build into bcd-host/out only
# LLVM_MINGW=<dir with x86_64-w64-mingw32-clang> overrides the toolchain.
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$DIR/../.." && pwd)"
SRC="$REPO_ROOT/bcd-host"
MINGW="${LLVM_MINGW:-$REPO_ROOT/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
CC="$MINGW/x86_64-w64-mingw32-clang"
OUT="$SRC/out"
INSTALL=1
CHECK=0
for a in "$@"; do
    case "$a" in
        --check) CHECK=1 ;;
        --no-install) INSTALL=0 ;;
        *) echo "build-bcd-host: unknown argument $a" >&2; exit 2 ;;
    esac
done

[ -f "$SRC/src/main.c" ] || { echo "build-bcd-host: bcd-host/src/main.c is missing" >&2; exit 1; }
[ -x "$CC" ] || { echo "build-bcd-host: missing cross compiler: $CC (set LLVM_MINGW)" >&2; exit 1; }

if [ "$CHECK" = 1 ]; then
    HCC="${CC_HOST:-cc}"
    mkdir -p "$OUT"
    FLAGS=(-std=c11 -O1 -g -Wall -Wextra -Werror -I"$SRC/src")
    if echo 'int main(void){return 0;}' | "$HCC" -x c - -fsanitize=address,undefined -o "$OUT/probe" 2>/dev/null; then
        FLAGS+=(-fsanitize=address,undefined -fno-sanitize-recover=all)
    fi
    "$HCC" "${FLAGS[@]}" -o "$OUT/check" "$SRC/tests/check.c" "$SRC/src/request.c" "$SRC/src/report.c" "$SRC/src/names.c"
    "$OUT/check"
fi

mkdir -p "$OUT"
# -ffreestanding -nostdlib: no C runtime. rt.c holds the memory functions a compiler may call.
"$CC" -std=c11 -O2 -Wall -Wextra -Werror -ffreestanding -fno-builtin -fno-stack-protector -fno-asynchronous-unwind-tables \
    -nostdlib -mwindows -Wl,-e,bcdhost_start -Wl,--dynamicbase -Wl,--no-insert-timestamp -Wl,--strip-all \
    -I"$SRC/src" -o "$OUT/bcdhost.exe" "$SRC"/src/main.c "$SRC"/src/request.c "$SRC"/src/report.c "$SRC"/src/names.c "$SRC"/src/rt.c -lkernel32

python3 - "$OUT/bcdhost.exe" <<'PY'
import struct, sys
data = open(sys.argv[1], 'rb').read()
pe = struct.unpack_from('<I', data, 0x3c)[0]
assert data[pe:pe + 4] == b'PE\0\0', 'not a PE image'
machine, nsec = struct.unpack_from('<HH', data, pe + 4)
opt = pe + 24
subsystem = struct.unpack_from('<H', data, opt + 68)[0]
dll_flags = struct.unpack_from('<H', data, opt + 70)[0]
assert machine == 0x8664, 'machine %#x, want x86-64' % machine
assert subsystem == 2, 'subsystem %d, want 2 (GUI)' % subsystem
assert dll_flags & 0x40, 'no DYNAMIC_BASE (the image must be relocatable)'
print('build-bcd-host: bcdhost.exe: %d bytes, machine %#x, GUI, relocatable' % (len(data), machine))
PY

if [ "$INSTALL" = 1 ]; then
    mkdir -p "$REPO_ROOT/app/Madeira/arm64ec-windows"
    cp "$OUT/bcdhost.exe" "$REPO_ROOT/app/Madeira/arm64ec-windows/bcdhost.exe"
    if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$OUT/bcdhost.exe"; else sha256sum "$OUT/bcdhost.exe"; fi
    echo "build-bcd-host: installed into app/Madeira/arm64ec-windows"
fi
