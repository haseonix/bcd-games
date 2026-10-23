#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Build BCD's interface for the simulator and photograph it on many device sizes.

The preview app is the app's BCD*.swift interface files (the ones that never touch the
engine) plus app/BCDPreview (sample data), compiled with swiftc for the simulator: no
Xcode project, no engine, no signing material. Then every requested screen is opened on
every requested simulator, in landscape and/or portrait, and saved as a PNG; a contact
sheet puts them side by side.

    tools/ui-preview.py                              all screens, the default devices
    tools/ui-preview.py --screens library --devices "BCD iPhone 13 Pro" --orient landscape
    tools/ui-preview.py --focus library.game.wc3     show a controller's focus ring
    tools/ui-preview.py --screens "game-620,game-620@game.main"   one screen with a ring of its own (screen@focus id)
    tools/ui-preview.py --focus library.game.wc3 --press x,down   then a controller's presses (x opens the game's menu)
    tools/ui-preview.py --screens settings.memory    a screen's part: Settings opened on Memory
    tools/ui-preview.py --screens game-620,game-wc3-options   a game's page (-BCDTab game-<id>-<what>)
    tools/ui-preview.py --list                       the simulators this Mac has

Out: /tmp/bcd-ui-preview/ (or --out). Simulators are booted one at a time and shut down
afterwards (an Intel VM has little room).
"""
import argparse
import fcntl
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "Madeira"
PREVIEW = ROOT / "app" / "BCDPreview"
BUNDLE_ID = "com.thebcd69.bcd.preview"
# The interface files that need the engine stay out of the preview build.
ENGINE_BOUND = {"BCDSetupView.swift", "BCDLiveStore.swift", "BCDLiveFrontEnd.swift", "BCDSettingsLive.swift", "BCDGamePageLive.swift", "BCDAddGameLive.swift", "BCDControllerHubLive.swift"}
DEFAULT_DEVICES = ["BCD iPhone 13 Pro", "BCD iPhone 14 Pro", "BCD iPhone 15", "iPhone 16 Pro Max",
                   "iPhone 17 Pro Max", "iPhone SE (3rd generation)", "iPad mini (A17 Pro)",
                   "iPad Air 11-inch (M2)", "iPad Pro 13-inch (M4)"]
SCREENS = ["home", "library", "downloads", "settings", "game", "add"]
# BCD's touch controls on the engine (their views: BCDTouchControls/Editor/Layouts.swift); their screens are
# touch, touch-keys, touch-editor, touch-layouts, touch-picker and touch-menu (app/BCDPreview/BCDTouchPreview.swift).
ENGINE_BOUND |= {"BCDTouchLive.swift"}
# Steam's public store pictures for the sample library (app/BCDPreview/BCDPreviewData.swift),
# fetched once into ~/Library/Caches and copied into the preview app only. The pictures belong
# to their publishers: never commit them.
PREVIEW_STEAM_APPS = [432940, 367520, 620, 894020, 1374970, 271590, 1715130, 2420110, 1895880, 20900]
ART_FILES = ["library_600x900.jpg", "header.jpg", "library_hero.jpg", "logo.png"]
ART_CACHE = Path.home() / "Library" / "Caches" / "bcd-ui-preview-art"
ART_SOURCES = ["https://cdn.cloudflare.steamstatic.com/steam/apps/{app}/{name}",
               "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app}/{name}"]
# Settings › Accounts, JIT and BCD Dock on the engine (their views: BCDAccounts/JIT/Dock.swift).
ENGINE_BOUND |= {"BCDAccountsLive.swift", "BCDJITLive.swift", "BCDDockLive.swift"}

# The in-game layer (BCDSession.swift and its views): its engine side stays out; its screens are
# -BCDTab ingame-menu|starting|overlay, with variants (app/BCDPreview/BCDPreviewSession.swift).
ENGINE_BOUND |= {"BCDSessionLive.swift"}
SCREENS += ["ingame-menu", "starting", "overlay"]


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"ui-preview: {' '.join(map(str, cmd[:4]))} failed:\n{(r.stderr or r.stdout)[-3000:]}")
    return r.stdout


def simulators():
    data = json.loads(run(["xcrun", "simctl", "list", "devices", "available", "-j"]))
    out = {}
    for runtime, devices in data["devices"].items():
        version = runtime.rsplit("iOS-", 1)[-1].replace("-", ".") if "iOS-" in runtime else runtime
        for d in devices:
            # Prefer the newer runtime when a name exists twice (e.g. iPad mini).
            if d["name"] not in out or version > out[d["name"]][1]:
                out[d["name"]] = (d["udid"], version, d["state"])
            # "iPad mini (A17 Pro)@18.1" names one runtime (the iOS 26 iPad shows the app in a window).
            out[f"{d['name']}@{version}"] = (d["udid"], version, d["state"])
    return out


def fetch_art():
    """The sample games' store pictures, fetched once (a missing one is remembered)."""
    for app in PREVIEW_STEAM_APPS:
        folder = ART_CACHE / str(app)
        folder.mkdir(parents=True, exist_ok=True)
        for name in ART_FILES:
            target, missing = folder / name, folder / (name + ".missing")
            if target.exists() or missing.exists():
                continue
            for source in ART_SOURCES:
                r = subprocess.run(["curl", "-sfL", "--max-time", "30", "-o", str(target), source.format(app=app, name=name)])
                if r.returncode == 0 and target.exists() and target.stat().st_size > 0:
                    break
                target.unlink(missing_ok=True)
            else:
                missing.touch()


def build(out):
    app = out / "BCDPreview.app"
    shutil.rmtree(app, ignore_errors=True)
    app.mkdir(parents=True)
    sdk = run(["xcrun", "--sdk", "iphonesimulator", "--show-sdk-path"]).strip()
    arch = "arm64" if os.uname().machine == "arm64" else "x86_64"
    sources = sorted(p for p in APP.glob("BCD*.swift") if p.name not in ENGINE_BOUND) + sorted(PREVIEW.glob("*.swift"))
    run(["xcrun", "swiftc", "-target", f"{arch}-apple-ios18.0-simulator", "-sdk", sdk, "-parse-as-library",
         "-module-name", "BCDPreview", "-Onone", "-o", str(app / "BCDPreview")] + [str(s) for s in sources])
    assets = out / "Assets.xcassets"
    shutil.rmtree(assets, ignore_errors=True)
    assets.mkdir()
    (assets / "Contents.json").write_text('{"info":{"author":"xcode","version":1}}')
    shutil.copytree(APP / "Assets.xcassets" / "BCDLogo.imageset", assets / "BCDLogo.imageset")
    shutil.copytree(APP / "Assets.xcassets" / "AppIcon.appiconset", assets / "AppIcon.appiconset")
    run(["xcrun", "actool", str(assets), "--compile", str(app), "--platform", "iphonesimulator",
         "--minimum-deployment-target", "18.0", "--app-icon", "AppIcon",
         "--output-partial-info-plist", str(out / "assets-info.plist"), "--target-device", "iphone", "--target-device", "ipad"])
    info = {
        "CFBundleIdentifier": BUNDLE_ID, "CFBundleExecutable": "BCDPreview", "CFBundleName": "BCD Preview",
        "CFBundleDisplayName": "BCD Preview", "CFBundlePackageType": "APPL", "CFBundleVersion": "1",
        "CFBundleShortVersionString": "1.0", "MinimumOSVersion": "18.0", "UIDeviceFamily": [1, 2],
        "CFBundleSupportedPlatforms": ["iPhoneSimulator"], "UILaunchScreen": {},
        # An iPad turns an app that can share the screen only when the user turns the device:
        # the preview app takes the whole screen so -BCDOrientation can turn it.
        "UIRequiresFullScreen": True,
        "UIApplicationSceneManifest": {"UIApplicationSupportsMultipleScenes": False},
        "UISupportedInterfaceOrientations": ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationLandscapeLeft",
                                             "UIInterfaceOrientationLandscapeRight"],
        "UISupportedInterfaceOrientations~ipad": ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationPortraitUpsideDown",
                                                  "UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"],
    }
    partial = out / "assets-info.plist"
    if partial.exists():
        info.update(plistlib.loads(partial.read_bytes()))
    (app / "Info.plist").write_bytes(plistlib.dumps(info))
    fetch_art()
    for folder in sorted(ART_CACHE.glob("*")):
        pictures = [f for f in folder.iterdir() if f.name in ART_FILES]
        if pictures:
            (app / "PreviewArt" / folder.name).mkdir(parents=True, exist_ok=True)
            for f in pictures:
                shutil.copy2(f, app / "PreviewArt" / folder.name / f.name)
    run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(app)])
    print(f"ui-preview: built {app} ({len(sources)} Swift files, {arch})")
    return app


def shoot(app, device, udid, screens, orients, focus_all, empty, out, press=None):
    shots = []
    subprocess.run(["xcrun", "simctl", "boot", udid], capture_output=True)
    run(["xcrun", "simctl", "bootstatus", udid, "-b"])
    run(["xcrun", "simctl", "install", udid, str(app)])
    # The same clean status bar on every device (no carrier name in the simulator's language).
    subprocess.run(["xcrun", "simctl", "status_bar", udid, "override", "--time", "9:41", "--batteryState", "charged",
                    "--batteryLevel", "100", "--wifiBars", "3", "--cellularMode", "notSupported", "--operatorName", ""],
                   capture_output=True)
    first = True
    for orient in orients:
        for spec in screens:
            # "screen@focus": this screen with the focus ring on that control (a controller's view), besides --focus.
            screen, _, ring = spec.partition("@")
            focus = ring or focus_all
            # A game's page keeps the dots of its id (game-epic.deathsdoor-options); other screens take ".section".
            tab, _, section = (screen, "", "") if screen.startswith("game-") else screen.partition(".")
            args = ["-BCDTab", tab, "-BCDOrientation", orient, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
            if section:
                args += ["-BCDSection", section]
            if focus:
                args += ["-BCDFocus", focus]
            if empty:
                args += ["-BCDEmpty", "YES"]
            if press:
                args += ["-BCDButtons", press]
            subprocess.run(["xcrun", "simctl", "terminate", udid, BUNDLE_ID], capture_output=True)
            log = out / f".launch-{udid}.txt"
            log.unlink(missing_ok=True)
            # `simctl launch --stdout=` writes nothing with this Xcode's simulator (every run
            # waited out the whole timeout below); --console streams the app's output through
            # simctl, which runs until the app is terminated.
            with open(log, "w") as sink:
                console = subprocess.Popen(["xcrun", "simctl", "launch", "--console", udid, BUNDLE_ID] + args,
                                           stdout=sink, stderr=subprocess.STDOUT)
            # The app prints a line once its first screen is up; a simulator that has just
            # booted can take a minute on this Mac. Then a moment for the turn and the art.
            deadline = time.time() + (150 if first else 60)
            while time.time() < deadline and console.poll() is None and "BCDPreview: ready" not in log.read_text(errors="ignore"):
                time.sleep(1)
            if "BCDPreview: ready" not in log.read_text(errors="ignore"):
                print(f"  ({device}: the app never said it was ready; photographing anyway)")
            # -BCDButtons: the presses start 1.6 s after the screen is up and come every 0.4 s.
            time.sleep(3 + (1.6 + 0.4 * len(press.split(",")) if press else 0))
            first = False
            name = (f"{device.replace(' ', '_').replace('(', '').replace(')', '')}--{screen}{'--ring-' + ring if ring else ''}"
                    f"{'--' + press.replace(',', '-') if press else ''}--{orient}.png")
            run(["xcrun", "simctl", "io", udid, "screenshot", str(out / name)])
            subprocess.run(["xcrun", "simctl", "terminate", udid, BUNDLE_ID], capture_output=True)
            try:
                console.wait(timeout=20)
            except subprocess.TimeoutExpired:
                console.kill()
            if orient == "landscape":
                # simctl saves the panel as mounted (portrait); turn it the way the app stands.
                run(["sips", "-r", "270", str(out / name)])
            shots.append(out / name)
            print(f"  {name}")
    subprocess.run(["xcrun", "simctl", "terminate", udid, BUNDLE_ID], capture_output=True)
    subprocess.run(["xcrun", "simctl", "shutdown", udid], capture_output=True)
    return shots


def _try_lock(f):
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--screens", default=",".join(SCREENS))
    ap.add_argument("--devices", default=",".join(DEFAULT_DEVICES))
    ap.add_argument("--orient", default="landscape,portrait")
    ap.add_argument("--focus")
    ap.add_argument("--press", help="a controller's presses after the screen is up: up,down,left,right,a,b,x,y,lb,rb,menu")
    ap.add_argument("--empty", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("/tmp/bcd-ui-preview"))
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    sims = simulators()
    if a.list:
        for name, (udid, version, state) in sorted(sims.items()):
            print(f"{name:40} iOS {version:6} {state:9} {udid}")
        return
    a.out.mkdir(parents=True, exist_ok=True)
    if a.build_only:
        # A build touches only its own --out folder: no simulator, so no lock. (Host checks
        # build the preview to prove it compiles; they must not queue behind a long photo run.)
        build(a.out)
        return
    # One simulator run at a time on this Mac (several agents may call this): it is heavy.
    lock = open("/tmp/bcd-ui-preview.lock", "w")
    print("ui-preview: waiting for the simulator lock" if not _try_lock(lock) else "ui-preview: lock taken")
    fcntl.flock(lock, fcntl.LOCK_EX)
    app = build(a.out)
    for device in [d.strip() for d in a.devices.split(",") if d.strip()]:
        if device not in sims:
            print(f"ui-preview: no simulator named {device!r} (see --list)")
            continue
        udid, version, _ = sims[device]
        print(f"{device} (iOS {version})")
        shoot(app, device, udid, [s.strip() for s in a.screens.split(",")], [o.strip() for o in a.orient.split(",")],
              a.focus, a.empty, a.out, a.press)
    print(f"ui-preview: screenshots in {a.out}")


if __name__ == "__main__":
    main()
