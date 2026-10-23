#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_MFC_DOCK = 1: MFC (mfc140.dll, mfc140u.dll) in a BCD Dock session; no Wine runs.

A Dock session's target is the aarch64 explorer.exe, so the VC++ overlay (which links MFC into system32) is skipped
for it, and the x64 game Valve's client starts stops with c0000135 ("import_dll Library mfc140.dll ... not found";
Horizon Zero Dawn TH3BJ8, Age of Empires II HD B2NWDX / GYYAAC / MXMRJ3). That game's ARM64EC ntdll falls back to
C:\\windows\\sysx64 for a name system32 lacks (wine/dlls/ntdll/loader.c "mixed-arch fallback"), so the switch links
the two MFC DLLs there. WineProcessBridge.m's madeira_mfc_dock is compiled on the host and run on synthetic folders:
  - unset: nothing is linked and the VC++ folder is not even looked at (no log line at all);
  - set, not ARM64EC: both DLLs linked from the downloaded (or the bundled) VC++ folder, one log line; one DLL only
    when only one is there; nothing and a "not downloaded" line when the runtime was not downloaded;
  - set in an ARM64EC session (the direct start HZD works in today), or with MADEIRA_MFC=0: nothing linked;
  - a link an earlier session made (into the VC++ folders) goes when the session does not ask for it; a link
    elsewhere and a real file stay; a stale link from before a reinstall is replaced;
  - the call site: after the overlay and the exported game lines, outside the ARM64EC branch, with the session's flag.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if (detail and not cond) else ""))
    ok &= bool(cond)


bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
CC = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang")

# ---------------------------------------------------------------- the function, compiled on the host
vc = re.search(r"static NSString \*madeira_vcruntime_dir\(NSString \*bundle, NSString \*support\)\n\{\n.*?\n\}\n", bridge, re.S)
mf = re.search(r"static void madeira_mfc_dock\(NSFileManager \*fm, NSString \*sysx64, NSString \*bundle, NSString \*support, BOOL ec_session\)\n\{\n.*?\n\}\n", bridge, re.S)
check("WineProcessBridge.m has madeira_vcruntime_dir and madeira_mfc_dock", vc is not None and mf is not None)
check("getenv(\"MADEIRA_MFC_DOCK\") is read once, inside madeira_mfc_dock",
      bridge.count('getenv("MADEIRA_MFC_DOCK")') == 1 and mf is not None and 'getenv("MADEIRA_MFC_DOCK")' in mf.group(0))

T = Path(tempfile.mkdtemp(prefix="mfc-dock-"))
if vc and mf:
    harness = T / "mfc.m"
    harness.write_text("#import <Foundation/Foundation.h>\n#include <unistd.h>\n#include <stdio.h>\n#include <stdlib.h>\n"
                       + vc.group(0) + mf.group(0) + r'''
int main(int argc, char **argv)
{
    if (argc < 5)
        return 2;
    @autoreleasepool {
        madeira_mfc_dock([NSFileManager defaultManager], [NSString stringWithUTF8String:argv[1]],
                         [NSString stringWithUTF8String:argv[2]],
                         argv[3][0] ? [NSString stringWithUTF8String:argv[3]] : nil, argv[4][0] == '1');
    }
    return 0;
}
''')
    hb = subprocess.run([CC, "-x", "objective-c", "-fobjc-arc", "-Wall", "-Wextra", "-Werror", "-framework", "Foundation",
                         str(harness), "-o", str(T / "mfc")], capture_output=True, text=True)
    check("madeira_mfc_dock builds on the host with -Wall -Wextra -Werror", hb.returncode == 0, hb.stderr)
    if hb.returncode == 0:
        def scenario(download=(), bundled=(), dock=None, mfc=None, ec=False, before=None, sysx64=True):
            """Runs the function on a synthetic bundle / Application Support / sysx64. Returns (stderr lines, sysx64, bundle, support)."""
            base = Path(tempfile.mkdtemp(dir=T))
            bundle, support, farm = base / "BCD.app", base / "Application Support", base / "wine/drive_c/windows/sysx64"
            bundle.mkdir(parents=True)
            if sysx64:
                farm.mkdir(parents=True)
            if bundled:
                (bundle / "x86_64-vcruntime").mkdir()
                for n in bundled:
                    (bundle / "x86_64-vcruntime" / n).write_bytes(b"MZ")
            if download:
                (support / "VCRuntime/x86_64").mkdir(parents=True)
                for n in download:
                    (support / "VCRuntime/x86_64" / n).write_bytes(b"MZ")
            if before:
                before(farm, bundle, support, base)
            env = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
            if dock is not None:
                env["MADEIRA_MFC_DOCK"] = dock
            if mfc is not None:
                env["MADEIRA_MFC"] = mfc
            r = subprocess.run([str(T / "mfc"), str(farm), str(bundle), str(support), "1" if ec else "0"],
                               capture_output=True, text=True, env=env)
            check("  (the function returns normally)", r.returncode == 0, r.stderr)
            return r.stderr.splitlines(), farm, bundle, support

        def links(farm):
            out = {}
            for n in ("mfc140.dll", "mfc140u.dll"):
                p = farm / n
                if p.is_symlink():
                    out[n] = os.readlink(p)
                elif p.exists():
                    out[n] = "FILE"
            return out

        FULL = ("vcruntime140.dll", "mfc140.dll", "mfc140u.dll")

        err, farm, bundle, support = scenario(download=FULL)
        check("unset: nothing linked and no log line (the VC++ folder is not even looked at)", links(farm) == {} and err == [], (err, links(farm)))
        err, farm, _, _ = scenario(download=FULL, dock="0")
        check("MADEIRA_MFC_DOCK=0 is the same as unset", links(farm) == {} and err == [], (err, links(farm)))
        err, farm, _, support = scenario(download=FULL, dock="1")
        want = {n: str(support / "VCRuntime/x86_64" / n) for n in ("mfc140.dll", "mfc140u.dll")}
        check("set, downloaded copy: both DLLs linked into sysx64, to the download", links(farm) == want, links(farm))
        check("  one log line says what was linked and from where",
              err == ["[WineProc] VC++ runtime: downloaded",
                      "[WineProc] MADEIRA_MFC_DOCK=1: mfc140.dll mfc140u.dll linked into sysx64 (from the downloaded VC++ runtime)"], err)
        check("  only the two MFC DLLs, none of the rest of the runtime", sorted(p.name for p in farm.iterdir()) == ["mfc140.dll", "mfc140u.dll"])
        err, farm, bundle, _ = scenario(bundled=FULL, download=FULL, dock="1")
        check("set, the bundle holds the runtime (the owner's builds): linked from the bundle",
              links(farm) == {n: str(bundle / "x86_64-vcruntime" / n) for n in ("mfc140.dll", "mfc140u.dll")}
              and err[-1].endswith("(from the bundled VC++ runtime)"), (err, links(farm)))
        err, farm, _, _ = scenario(dock="1")
        check("set, runtime not downloaded: nothing linked, one line says so",
              links(farm) == {} and err == ["[WineProc] VC++ runtime: none",
                                           "[WineProc] MADEIRA_MFC_DOCK=1: no mfc140.dll / mfc140u.dll in the VC++ runtime folder (not downloaded): nothing linked"], err)
        err, farm, _, support = scenario(download=("vcruntime140.dll", "mfc140u.dll"), dock="1")
        check("only mfc140u.dll there: only it is linked",
              links(farm) == {"mfc140u.dll": str(support / "VCRuntime/x86_64/mfc140u.dll")} and err[-1].startswith("[WineProc] MADEIRA_MFC_DOCK=1: mfc140u.dll linked"), (err, links(farm)))
        err, farm, _, _ = scenario(download=FULL, dock="1", ec=True)
        check("set in an ARM64EC session (HZD's direct start): nothing linked, the VC++ folder not looked at, one line",
              links(farm) == {} and err == ["[WineProc] MADEIRA_MFC_DOCK=1: an ARM64EC session already links MFC into system32: nothing linked into sysx64"], err)
        err, farm, _, _ = scenario(download=FULL, dock="1", mfc="0")
        check("MADEIRA_MFC=0 keeps MFC out here too",
              links(farm) == {} and err == ["[WineProc] MADEIRA_MFC_DOCK=1 but MADEIRA_MFC=0: MFC stays out"], err)
        err, farm, _, _ = scenario(download=FULL, dock="1", sysx64=False)
        check("no sysx64 folder yet: no crash, nothing linked", links(farm) == {})

        def earlier(farm, bundle, support, base):
            for n in ("mfc140.dll", "mfc140u.dll"):
                os.symlink(str(support / "VCRuntime/x86_64" / n), farm / n)
        err, farm, _, _ = scenario(download=FULL, before=earlier)
        check("unset after a session that linked MFC: the leftover links go (one game's line never reaches the next game)",
              links(farm) == {} and len([e for e in err if "removed from sysx64" in e]) == 2, (err, links(farm)))
        err, farm, _, _ = scenario(download=FULL, before=earlier, dock="1", ec=True)
        check("an ARM64EC session clears them too", links(farm) == {})
        err, farm, _, _ = scenario(download=FULL, before=earlier, dock="1", mfc="0")
        check("MADEIRA_MFC=0 clears them too", links(farm) == {})
        err, farm, _, _ = scenario(before=earlier, dock="1")
        check("set, but the download is gone: the leftover links go, 'not downloaded' is said",
              links(farm) == {} and err[-1].endswith("nothing linked"), (err, links(farm)))

        def elsewhere(farm, bundle, support, base):
            os.symlink(str(base / "somewhere/mfc140.dll"), farm / "mfc140.dll")
            (farm / "mfc140u.dll").write_bytes(b"real file")
        err, farm, _, _ = scenario(download=FULL, before=elsewhere)
        check("a link elsewhere and a real file are left alone when unset",
              links(farm).get("mfc140.dll", "").endswith("somewhere/mfc140.dll") and links(farm).get("mfc140u.dll") == "FILE" and err == [], (err, links(farm)))
        err, farm, _, support = scenario(download=FULL, before=elsewhere, dock="1")
        check("set: a real file stays (said), the link elsewhere is replaced by the VC++ one",
              links(farm).get("mfc140u.dll") == "FILE" and (farm / "mfc140u.dll").read_bytes() == b"real file"
              and links(farm).get("mfc140.dll") == str(support / "VCRuntime/x86_64/mfc140.dll")
              and any("mfc140u.dll in sysx64 is a file, not a link" in e for e in err), (err, links(farm)))

        def old_bundle(farm, bundle, support, base):
            for n in ("mfc140.dll", "mfc140u.dll"):
                os.symlink("/private/var/containers/Bundle/Application/OLD-UUID/BCD.app/x86_64-vcruntime/" + n, farm / n)
        err, farm, bundle, _ = scenario(bundled=FULL, dock="1", before=old_bundle)
        check("a link from before a reinstall (dangling, the old bundle) is replaced by the current one",
              links(farm) == {n: str(bundle / "x86_64-vcruntime" / n) for n in ("mfc140.dll", "mfc140u.dll")}, links(farm))
        err, farm, _, _ = scenario(download=FULL, before=old_bundle)
        check("and removed when unset", links(farm) == {})
shutil.rmtree(T, ignore_errors=True)

# ---------------------------------------------------------------- the call site
call = re.search(r"madeira_mfc_dock\(fm, \[prefix stringByAppendingPathComponent:@\"drive_c/windows/sysx64\"\], bundlePath,\n"
                 r"\s+NSSearchPathForDirectoriesInDomains\(NSApplicationSupportDirectory, NSUserDomainMask, YES\)\.firstObject,\n"
                 r"\s+use_arm64ec\);", bridge)
check("the session calls it with sysx64, the bundle, Application Support and the session's ARM64EC flag", call is not None)
if call:
    at = call.start()
    check("  after the VC++ overlay, outside its ARM64EC branch",
          bridge.index('[WineProc] Symlinked %d MS VC++ Runtime DLLs over arm64ec builtins') < at
          and re.search(r"\(skipped %d for native EC SEH\)\\n\", vcrtLinked, vcrtSkipped\);\n            \}\n\n            /\* madeira-bcd \(round 591\)",
                        bridge[:at + 40]) is not None)
    check("  after the game's own lines were exported (so the game's line is seen)",
          bridge.index('const char *gameCfg = getenv("MADEIRA_CFG_GAME");') < at
          and bridge.index('setenv(k.UTF8String, v.UTF8String, 1);') < at)
    check("  in the block that has the prefix, the bundle and the file manager",
          bridge.rindex("NSString *bundlePath = [[NSBundle mainBundle] bundlePath];\n            NSString *dllSource", 0, at) > bridge.rindex("// Ensure Wine prefix has system32 directory", 0, at))
check("the overlay itself still sits behind use_arm64ec (a direct start is as before)",
      'if (use_arm64ec && wineVcrt && wineVcrt[0] == \'1\') {' in bridge and re.search(r"\} else\n\s+if \(use_arm64ec\) \{\n\s+NSString \*vcrtSource = vcrtDir;", bridge) is not None
      and "NSString *vcrtDir = use_arm64ec ? madeira_vcruntime_dir(" in bridge)

# ---------------------------------------------------------------- catalog and auto-tune
gen = (root / "build/tools/gen-config-catalog.py").read_text()
check("the settings catalog documents env.MADEIRA_MFC_DOCK, off by default",
      re.search(r'"env\.MADEIRA_MFC_DOCK": \{[^}]*"kind": "bool", "default": "0"', gen, re.S) is not None)
rules = (root / "build/tools/autotune_rules.py").read_text()
block = rules[rules.index('"id": "mfc-missing-dock"'):] if '"id": "mfc-missing-dock"' in rules else ""
block = block[:block.index("\n    },\n")] if block else ""
check("auto-tune: rule mfc-missing-dock adds env.MADEIRA_MFC_DOCK = 1 for a Dock session whose game could not import MFC",
      '"lines": ["env.MADEIRA_MFC_DOCK = 1"]' in block and '"session": "dock"' in block and 'import_dll Library mfc140' in block
      and '(which is needed by' in block and '[madeira-dock] starting the host' in block and 'failed, status c0000135' in block, block[:300])
check("  with a fixture of each game that stopped on it", all((root / "tests/fixtures/autotune" / n).exists() for n in
      ("horizon-zero-dawn-mfc-dock-2026-10-22.log", "horizon-zero-dawn-mfc-dock-2026-10-22.expect",
       "age-of-empires-ii-hd-mfc-dock-2026-10-22.log", "age-of-empires-ii-hd-mfc-dock-2026-10-22.expect")))

print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
