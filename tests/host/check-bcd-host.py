#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD Host (bcd-host/, build/bcd-host/build.sh): the helper that starts a store game signed in, inside Wine.

Source checks (every host): BCD's licence header, exactly, on every file; the build script is the one the CI step
runs and stages bcdhost.exe in the app bundle's arm64ec-windows folder, which is gitignored; the request format's
constants agree between request.h and BCDEpicHostFiles.swift; the host reads no argument but --epic, prints and
logs nothing (no console, no OutputDebugString), keeps no value of the request in the report, and deletes only
what its request names and only under C:\\ProgramData\\BCD.

Behaviour (a C compiler on the host): bcd-host/tests/check.c with AddressSanitizer and UBSan: the request's
reader (every rule, strict UTF-8, hostile and damaged requests, a deterministic fuzz) and the report's bounds.

The Windows build (llvm-mingw): builds bcdhost.exe freestanding and checks the PE itself: x86-64, GUI subsystem,
relocatable, imports only kernel32.dll (no C runtime), small, and holds no text a signed-in start must not
leak. --require-mingw makes a missing toolchain a failure (LLVM_MINGW=<bin dir> names it; the build clone's
toolchains/ folder is tried too).

  check-bcd-host.py [--require-mingw]
"""
import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--require-mingw", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


HEADER_C = ("/* SPDX-License-Identifier: GPL-3.0-only */\n"
            "/* Copyright 2026 thebcd69 (BCD) */\n"
            "/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */\n")
HEADER_SH = ("# SPDX-License-Identifier: GPL-3.0-only\n"
             "# Copyright 2026 thebcd69 (BCD)\n"
             "# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
HOST = R / "bcd-host"
C_FILES = ["src/main.c", "src/names.c", "src/names.h", "src/request.c", "src/request.h", "src/report.c", "src/report.h", "src/rt.c", "tests/check.c", "tests/parse_file.c"]

for name in C_FILES:
    check((HOST / name).read_text().startswith(HEADER_C), f"bcd-host/{name}: BCD's licence header, exactly")
build_sh = (R / "build/bcd-host/build.sh").read_text()
check(build_sh.startswith("#!/bin/bash\n" + HEADER_SH), "build/bcd-host/build.sh: BCD's licence header, exactly")
check(os.access(R / "build/bcd-host/build.sh", os.X_OK), "build/bcd-host/build.sh is executable")
check(sorted(p.name for p in (HOST / "src").iterdir()) == sorted(Path(n).name for n in C_FILES if n.startswith("src/")),
      "bcd-host/src holds exactly the host's files")

main = (HOST / "src/main.c").read_text()
req_h = (HOST / "src/request.h").read_text()
req_c = (HOST / "src/request.c").read_text()
rep_h = (HOST / "src/report.h").read_text()
main_code = re.sub(r"/\*.*?\*/", "", main, flags=re.S)
check("-nostdlib" in build_sh and "-ffreestanding" in build_sh and "-lkernel32" in build_sh and "-mwindows" in build_sh,
      "the host links kernel32 only, with no C runtime, as a GUI program")
check("-Wl,--no-insert-timestamp" in build_sh and "-Wl,--strip-all" in build_sh and "-Wl,--dynamicbase" in build_sh,
      "the build is reproducible (no timestamp), stripped and relocatable")
check("app/Madeira/arm64ec-windows" in build_sh and 'cp "$OUT/bcdhost.exe"' in build_sh, "the build stages bcdhost.exe in the bundle's arm64ec-windows folder")
gitignore = (R / ".gitignore").read_text()
check("app/Madeira/arm64ec-windows/bcdhost.exe" in gitignore and "bcd-host/out/" in gitignore, "the output and the build folder are gitignored")
check(not re.search(r"OutputDebugString|MessageBox|printf|AllocConsole|GetStdHandle|SetConsole", main_code), "the host prints and shows nothing")
check(main_code.count("GetCommandLineW()") == 1 and "has_mode(GetCommandLineW())" in main_code and 'L"--epic"' in main_code,
      "the host reads its command line only to see --epic")
check(main_code.count("DeleteFileW(") == 3 and main_code.count("DeleteFileW(Q.ovt)") == 1 and main_code.count("DeleteFileW(EnvPath)") == 2,
      "the host deletes only its request (by name when delete-on-close was refused) and the one file the request names")
check('"C:\\\\ProgramData\\\\BCD\\\\"' in req_c, "...and that file must lie under C:\\ProgramData\\BCD\\ (request.c)")
check("FILE_FLAG_DELETE_ON_CLOSE" in main_code, "the request is opened delete-on-close")
check("bcd_wipe(Raw" in main_code and "bcd_wipe(Q.cmdline" in main_code and "bcd_wipe(EnvPath" in main_code,
      "the raw request, the command line and the request's path are wiped")
check(main_code.index("bcd_wipe(Q.cmdline") > main_code.index("CreateProcessW(Q.program"), "...the command line right after the program has it")
check("R.child_exit = exit_code" in main_code and "finish(exit_code)" in main_code, "the host exits with the program's own exit code")
check("CREATE_SUSPENDED" not in main_code, "the program is not started suspended (the host joins the job first)")
check(main_code.index("AssignProcessToJobObject(job, GetCurrentProcess())") < main_code.index("CreateProcessW(Q.program"),
      "the host joins the job before it starts the program, so the program's tree is in it")
check(main_code.index("CreateProcessW(ServicesExe") < main_code.index("CreateJobObjectW"), "services.exe, when asked for, starts before the job: it does not hold the tree open")
check(re.search(r"GetQueuedCompletionStatus\(port, &bytes, &key, &overlapped, wait_ms\)", main_code) and "DWORD wait_ms = port ? 10000 : 1000;" in main_code and "Sleep(2000)" in main_code,
      "the tree wait has a ten-second ceiling on every notice and looks once more after two seconds")
check(main_code.count("report(7);") == 2 and main_code.rindex("report(7);") > main_code.index("int others = others_running(job);"),
      "the report is rewritten at every look at the job: a wait that never ends shows what keeps it open")
check("#define BCDHOST_LATE_SECONDS 180" in main_code and "#define BCDHOST_LATE_GRACE_MS 10000" in main_code and "R.lived >= BCDHOST_LATE_SECONDS" in main_code,
      "a program that ran three minutes or more was the game: the wait for the rest of the tree ends ten seconds after it")
check("bcd_host_background(" in main_code and "JobObjectBasicProcessIdList" in main_code and "JobObjectBasicAccountingInformation" in main_code,
      "the job's processes are told apart by name (Wine's helpers and reporters do not keep a session open), with the job's own count as the fallback")
names_c = (HOST / "src/names.c").read_text()
check(all(f'"{n}"' in names_c for n in ("services.exe", "rpcss.exe", "winedevice.exe", "plugplay.exe", "explorer.exe", "conhost.exe", "wineboot.exe", "svchost.exe")),
      "the names list holds Wine's own helper programs")
# The report holds numbers only: its struct has only uint32_t fields.
fields = re.search(r"typedef struct \{(.*?)\} bcd_report;", rep_h, re.S).group(1)
check(re.findall(r"^\s*(\w+)\s+\w+;", fields, re.M) == ["uint32_t"] * 18, "the report's fields are all 32-bit numbers (no text can reach it)")
tags = dict(re.findall(r"BCDHOST_TAG_(\w+) = (\d+)", req_h))
check(tags == {"PROGRAM": "1", "CWD": "2", "ARGS": "3", "OVT": "4", "ROOT": "5"}, "the request's tags", tags)
check("#define BCDHOST_MAX_REQUEST 32768u" in req_h and "#define BCDHOST_VERSION 1u" in req_h, "the request's version and size limit")

# The Swift side's constants (once the writer exists).
files_swift = R / "app/Madeira/BCDEpic/BCDEpicHostFiles.swift"
if files_swift.exists():
    swift = files_swift.read_text()
    check('"BCDHOST1"' in swift and "static let version: UInt32 = 1" in swift and "static let maxSize = 32768" in swift
          and all(f"case {n.lower()} = {v}" in swift or f"static let {n.lower()}: UInt16 = {v}" in swift for n, v in tags.items()),
          "the Swift writer and request.h agree on the magic, the version, the size limit and the tags")

# ---- the host tests ------------------------------------------------------------------------
cc = shutil.which(os.environ.get("CC_HOST", "cc"))
if not cc:
    check(False, "a C compiler is available for the host tests")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-host-check-") as tmp:
        exe = Path(tmp) / "check"
        flags = [cc, "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-I", str(HOST / "src")]
        sanitize = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
        srcs = [str(HOST / "tests/check.c"), str(HOST / "src/request.c"), str(HOST / "src/report.c"), str(HOST / "src/names.c"), "-o", str(exe)]
        built = subprocess.run(flags + sanitize + srcs, capture_output=True, text=True)
        if built.returncode != 0:
            built = subprocess.run(flags + srcs, capture_output=True, text=True)
        check(built.returncode == 0, "the host tests compile (request.c and report.c, -Wall -Wextra -Werror)", built.stderr[-3000:])
        if built.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
            last = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else ""
            fails = [l for l in run.stdout.splitlines() if l.startswith("FAIL")]
            check(run.returncode == 0 and re.fullmatch(r"bcd-host checks: all \d+ passed", last), "the host tests pass: " + last, "\n".join(fails[:10]) + run.stderr[-2000:])

# ---- the Windows build ---------------------------------------------------------------------
def toolchain():
    candidates = []
    if os.environ.get("LLVM_MINGW"):
        candidates.append(Path(os.environ["LLVM_MINGW"]))
    candidates.append(R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
    candidates.append(Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
    return next((c for c in candidates if (c / "x86_64-w64-mingw32-clang").exists()), None)


def imports(data):
    """The DLL names a PE image imports (a small reader: the import directory only)."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    rva = struct.unpack_from("<I", data, opt + 112 + 8)[0]
    sections = [struct.unpack_from("<8sIIII", data, opt + opt_size + 40 * i) for i in range(nsec)]

    def off(r):
        for _, vsize, vaddr, rawsize, rawptr in sections:
            if vaddr <= r < vaddr + max(vsize, rawsize):
                return r - vaddr + rawptr
        raise ValueError("rva outside the sections")
    names = []
    p = off(rva)
    while True:
        name_rva = struct.unpack_from("<I", data, p + 12)[0]
        if not name_rva:
            break
        q = off(name_rva)
        names.append(data[q:data.index(b"\0", q)].decode())
        p += 20
    return names


tc = toolchain()
if not tc:
    check(not args.require_mingw, "llvm-mingw is available for the Windows build (set LLVM_MINGW; --require-mingw makes this a failure)")
else:
    env = dict(os.environ, LLVM_MINGW=str(tc))
    built = subprocess.run(["bash", str(R / "build/bcd-host/build.sh"), "--no-install"], capture_output=True, text=True, env=env)
    check(built.returncode == 0, "build/bcd-host/build.sh builds bcdhost.exe (-Wall -Wextra -Werror, freestanding)", (built.stdout + built.stderr)[-3000:])
    exe = HOST / "out/bcdhost.exe"
    if built.returncode == 0 and exe.exists():
        data = exe.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        machine = struct.unpack_from("<H", data, pe + 4)[0]
        characteristics = struct.unpack_from("<H", data, pe + 22)[0]
        opt = pe + 24
        subsystem = struct.unpack_from("<H", data, opt + 68)[0]
        dll = struct.unpack_from("<H", data, opt + 70)[0]
        check(machine == 0x8664 and subsystem == 2, "bcdhost.exe is an x86-64 GUI program (no console)")
        check(dll & 0x40 and not (characteristics & 0x1), "...relocatable (dynamic base, relocations not stripped)")
        check([n.lower() for n in imports(data)] == ["kernel32.dll"], "...importing kernel32.dll only (no C runtime)", imports(data))
        check(len(data) < 40_000, f"...small ({len(data)} bytes)")
        image_size = struct.unpack_from("<I", data, opt + 56)[0]
        check(image_size <= 160 * 1024, f"...an image of {image_size // 1024} KB (what its JIT-pool copy takes)")
        for text in (b"AUTH_PASSWORD", b"epicusername", b"egoc1", b"eg1~", b"exchange", b"authorizationCode", b"bcd.epic"):
            check(text not in data, f"...holds no {text.decode()!r} string")
        check(b"BCD_HOST_REQUEST" not in data and "BCD_HOST_REQUEST".encode("utf-16-le") in data, "...reads BCD_HOST_REQUEST (as UTF-16)")
        # The compiler must not have called a CRT or probe function the freestanding link lacks: it linked, so none are missing.

print(f"check-bcd-host: {failures} FAILED" if failures else "check-bcd-host: all passed")
sys.exit(1 if failures else 0)
