#!/usr/bin/env python3
"""BCD's built-in recommended settings per game (owner's decision 2026-10-09).

Source checks (every host): the lists in recommended/recommendations.json (the
app's built-in copy since 2026-10-18, read by GameRecommendationsList.swift)
match the working lists they were taken from (Red Dead Redemption 2's line for
line), every key that is not env.NAME is one Madeira reads (the generated
settings catalog), no list carries DLL overrides for Rockstar's Social Club,
nothing a list or GameProfile writes into a game's config says "Madeira" (owner,
2026-10-18: a list starts "# BCD's recommended settings for ..."), and the app
applies them where the owner asked: a game's first sight (library start-up,
saving an entry, Game details, a start, the madeira-bcd home screen) and Reset
to Recommended at the bottom of both settings pages.

The Swift fixture (macOS CI) compiles the real GameRecommendations*.swift (the
lists read from the JSON) and GameRecommendationsApply.swift against small
stand-ins for the app types and checks how games are recognised (Steam games by
App ID or by their folder under steamapps\\common, every other game by its
program's name, GTA V Enhanced inside or outside a Steam library), the
first-sight, update and reset rules, and that a newer list (a download) reaches
untouched configs and never a changed one. Linux hosts without Swift run the
source checks; --require-swift turns a missing compiler into a failure.
"""
from pathlib import Path
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--require-swift', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
lists_src = (app / 'GameRecommendations.swift').read_text()
apply_src = (app / 'GameRecommendationsApply.swift').read_text()
library = (app / 'Library.swift').read_text()
files = (app / 'GameFiles.swift').read_text()
content = (app / 'ContentView.swift').read_text()
profiles = (app / 'GameProfiles.swift').read_text()
catalog = (app / 'ConfigCatalog.generated.swift').read_text()
project = (root / 'app/Madeira.xcodeproj/project.pbxproj').read_text()


def block(source, start, end):
    i = source.index(start)
    return source[i:source.index(end, i) + len(end)]


# The lists, as the app reads them (GameRecommendationsList.swift): recommended/recommendations.json,
# its rules in order (the first rule that matches a game wins).
recs_path = root / 'recommended/recommendations.json'
recs = json.loads(recs_path.read_text())


def text(line):
    """A config line as the file writes it: a string, or an object for some builds only."""
    return line if isinstance(line, str) else line['line']


def swift_switches(rule):
    """The switches in the words of the Swift literals they replaced (2026-10-18), in
    GameRecommendation's argument order, so the expectations below stay word for word."""
    words = []
    for key in ('avx', 'nvidia', 'wineVCRT', 'fastSync', 'semaphoreFastPath', 'fpsMode', 'resolution', 'display'):
        if key not in rule.get('switches', {}):
            continue
        value = rule['switches'][key]
        if isinstance(value, bool):
            value = 'true' if value else 'false'
        elif isinstance(value, int):
            value = str(value)
        elif key == 'resolution' and value == 'screen@720':
            value = 'GameRecommendation.screen720'
        else:
            value = json.dumps(value)
        words.append(f'{key}: {value}')
    return ', '.join(words)


presets = {}
for r in recs['rules']:
    assert set(r) <= {'id', 'title', 'version', 'minBuild', 'match', 'config', 'smallRAMConfig', 'switches', 'notes'}, r['id']
    assert set(r.get('switches', {})) <= {'avx', 'nvidia', 'wineVCRT', 'fastSync', 'semaphoreFastPath', 'fpsMode', 'resolution',
                                          'display'}, r['id']
    presets[r['id']] = dict(title=r['title'], version=r['version'], lines=[text(line) for line in r['config']],
                            small=[text(line) for line in r.get('smallRAMConfig', [])], switches=swift_switches(r),
                            match=r.get('match'))
assert list(presets) == ['rdr2-steam', 'rdr2-other', 'gta5e-steam', 'gta5e-other', 'gta5l-steam', 'ghost-of-tsushima', 'god-of-war',
                         'horizon-zero-dawn', 'horizon-forbidden-west', 'ratchet-clank-rift-apart', 'crysis-remastered',
                         'crysis-2-remastered', 'crysis-3-remastered', 'msfs-2024', 'counter-strike-2', 'warcraft-2-bne',
                         'breakneck', 'spelunky-2', 'albion-online', 'megabonk', 'no-im-not-a-human', 'over-the-hill', 'fort-solis', 'dragon-ball-sparking-zero', 'animal-well', 'hogwarts-legacy', 'metal-gear-solid-v', 'stellar-blade-demo', 'star-wars-galactic-racer', 'denshattack', 'final-fantasy-resonance-demo', 'no-rest-for-the-wicked', 'quaver', 'dying-light', 'beamng-drive', 'monster-hunter-stories-3-trial', 'pwaat', 'lego-party', '1000xresist', 'american-truck-simulator', 'marvels-spider-man-miles-morales', 'diablo-ii-resurrected', 'clair-obscur-expedition-33', 'gta4-steam', 'gtasa-de-steam', 'unrailed-2-back-on-track', 'resident-evil-2', 'resident-evil-7', 'blazblue-entropy-effect', 'dark-souls-iii', 'marvels-spider-man-remastered', 'raidou-remastered', 'yakuza-like-a-dragon', 'batman-arkham-city-goty', 'metal-gear-solid-peace-walker', 'lonely-mountains-snow-riders', 'trails-in-the-sky-1st-chapter', 'subnautica-2', 'rdr1-steam', 'resident-evil-requiem', 'life-is-strange-remastered', 'life-is-strange-2', 'project-zomboid', 'spiderheck', 'rounds', 'plateup', 'final-fantasy-tactics-ivalice-chronicles', 'age-of-empires-ii-hd', 'fallout-new-vegas', 'digimon-story-cyber-sleuth', 'final-fantasy-xv-demo', 'borderlands-3', 'zenless-zone-zero', 'jetboard-joust', 'sword-and-fairy-3', 'dead-as-disco', 'tung', 'standard'], list(presets)

catalog_keys = set(re.findall(r'ConfigOption\(key: "([^"]+)"', catalog))
for ident, p in presets.items():
    assert p['version'] >= 1, ident
    # The owner, 2026-10-18: the first line says BCD (it said "# Madeira's recommended settings for").
    assert p['lines'][0].startswith("# BCD's recommended settings for ") and p['lines'][0].endswith('.'), (ident, p['lines'][0])
    assert p['lines'][1] == "# Reset to Recommended, at the bottom of the game's settings, brings them back.", (ident, p['lines'][1])
    for lines in (p['lines'][2:], p['small']):
        seen = set()
        for line in lines:
            assert line == line.strip() and not line.startswith('#'), (ident, line)
            key, sep, value = line.partition(' = ')
            assert sep and key and value, (ident, line)
            assert key not in seen, (ident, 'twice', key)
            seen.add(key)
            if key.startswith('env.'):
                assert re.fullmatch(r'env\.[A-Za-z_][A-Za-z0-9_]*', key), (ident, key)
            else:
                assert key in catalog_keys, (ident, 'not a key Madeira reads', key)
            # Madeira's own settings only: nothing that loads a replaced Social Club. SocialClubHelper.exe
            # may be named as a program in a list (env.DXMT_SMALL_RINGS), never in a DLL override.
            named = line.lower()
            if key != 'env.WINEDLLOVERRIDES':
                named = named.replace('socialclubhelper.exe', '')
            assert 'socialclub' not in named and 'MADEIRA_DLL_LOCAL' not in line, (ident, line)
# The owner, 2026-10-18: nothing BCD writes into a game's config says Madeira: no list line (comments
# included; env.MADEIRA_* names are not the name), no title, and not the header GameProfile.set writes
# into a game's empty file.
for ident, p in presets.items():
    for line in p['lines'] + p['small'] + [p['title']]:
        assert 'Madeira' not in line, (ident, line)
header = block(profiles, '        if lines.isEmpty { lines = [', ']')
assert 'Madeira' not in header and '# BCD settings for' in header, header
print('PASS: forty-six lists in order, each starting "# BCD\'s recommended settings for", no key twice, every non-env key in '
      'the settings catalog, no Social Club DLL lines, "Madeira" in no line, title or GameProfile header')

# Red Dead Redemption 2 from Steam: the owner's list of 2026-10-09 (build 463), line for line,
# plus the lines of build 466's test (version 2), build 467's (version 3), build 470's (version 4) and
# build 472's (version 5: pool-mid), build 475's (version 6: ring-share), build 476's (version 7:
# swap-images, small DXMT rings for the launcher and Social Club, the ExecuteIndirect probe off)
# and build 480's (version 9: xinput1_4.dll loaded with the D3D12 device, the pool back to 128 MB of
# image headroom after 479's 192 starved the game's code buffer, the orphan-lock reaper stamped-only)
# and build 514's (version 10: the VC++ setup's elevated engine is not started; it crashed with a lock
# held and the first install waited forever).
rdr2 = '''d3d12-caps-log = 2
d3d12-shader-pack = 1
replay-split = 1
ring-share = 1
pool-low = 1
pool-mid = 1
pool-page-fit = 1
pool-split = 1
vram-mb = 2304
swap-mb = 8192
swap-mode = 2
swap-min-mb = 1
swap-images = 1
pso-warm = 2
fence-chain = 6
avail-phys = 1
indirect-fast = 1
ind-probe = 0
fullscreen-window = 1
env.WINE_D3D_CONFIG = renderer=no3d
env.FEX_VECTORTSOENABLED = 0
env.MADEIRA_BAND_CENSUS = 1
env.MADEIRA_RDR2_VA_HOLD = 1
env.MADEIRA_SWAP_RESERVE_MAX_MB = 9216
env.MADEIRA_THREAD_STACK_SPILL = 1
env.MADEIRA_SMALL_STACK_EXES = RDR2.exe
env.MADEIRA_D3D11_SRC = 1
env.MADEIRA_DOCK_GAME_SCM = 1
env.MADEIRA_DOCK_KEEP_ALIVE = Launcher.exe;RDR2.exe
env.MADEIRA_DXGI_SRC = 1
env.MADEIRA_DXGI_BUDGET_EVENTS = 1
env.DXMT_WSI_MONITOR_IDENTITY = 1
env.DXMT_WSI_MODE_TABLE = 1
env.DXMT_SMALL_RINGS = Launcher.exe;SocialClubHelper.exe
env.MADEIRA_EXECREQ_LEAVE = 1
env.MADEIRA_FEX_AVX = 0
env.MADEIRA_LOCK_ORPHAN = stamped
env.MADEIRA_PIN_GRAPHICS_DLLS = 1
env.MADEIRA_PRELOAD_DLLS = xinput1_4.dll
env.MADEIRA_POOL_HEAD_RESERVE_MB = 128
env.MADEIRA_POOL_LOW_IMAGES = 1
env.MADEIRA_POOL_RECYCLE_IMAGES = 1
env.MADEIRA_SC_PA_POOLS = 2
env.MADEIRA_SPAWN_BLOCK = RockstarErrorHandler;.be\\vc_redist
env.MADEIRA_TOUCH_MOUSE = 1
env.MADEIRA_WOW_MIN_FREE_GB = 2
env.MADEIRA_X64_GRAPHICS_ENTRY = 1
env.MADEIRA_X64_IMAGE_NOCOPY = 1
env.WINEDLLOVERRIDES = video64=
env.MADEIRA_DEVICE_STATS = 1
env.MADEIRA_METAL_HUD_MAIN = 1'''.split('\n')
assert presets['rdr2-steam']['lines'][2:] == rdr2, 'the RDR2 list differs from the owner\'s'
assert presets['rdr2-steam']['version'] == 11, presets['rdr2-steam']['version']   # 11 (2026-10-21): env.MADEIRA_FEX_AVX = 0
assert presets['rdr2-steam']['switches'] == ('avx: false, nvidia: false, wineVCRT: false, fastSync: true, '
                                             'semaphoreFastPath: false, resolution: GameRecommendation.screen720, display: "fit"'), presets['rdr2-steam']['switches']
assert 'static let screen720 = "screen@720"' in lists_src and 'resolution == Self.screen720 ? "this screen\'s shape at 720 lines" : resolution' in lists_src
# Red Dead Redemption 2 outside Steam (owner's request, 2026-10-12, after GTA V Enhanced's two lists): the
# Steam copy's lines without the two only Madeira Dock reads, plus the ec-hook reporter off, which GTA V
# Enhanced's other copy has; the same switches.
rdr2_other = presets['rdr2-other']
dock_only = ['env.MADEIRA_DOCK_GAME_SCM = 1', 'env.MADEIRA_DOCK_KEEP_ALIVE = Launcher.exe;RDR2.exe']
assert all(line in rdr2 for line in dock_only), 'the Dock lines are the Steam copy\'s'
# The AVX pin (v11 of the Steam list, 2026-10-21: auto-tune's fex-avx-on kept AVX on in the Rockstar Launcher's
# process only and every such session failed at the Launcher's service connection) is the Steam lists' alone.
avx_pin = 'env.MADEIRA_FEX_AVX = 0'
assert avx_pin in rdr2 and avx_pin in presets['gta5e-steam']['lines'] and avx_pin in presets['gta5l-steam']['lines']
assert avx_pin not in rdr2_other['lines'] and avx_pin not in presets['gta5e-other']['lines']
assert rdr2_other['lines'][2:] == [line for line in rdr2 if line not in dock_only and line != avx_pin] + ['env.MADEIRA_EC_HOOK_TRACE = 0'], \
    rdr2_other['lines']
assert rdr2_other['version'] == 1 and rdr2_other['switches'] == presets['rdr2-steam']['switches'], rdr2_other
assert 'env.MADEIRA_EC_HOOK_TRACE = 0' in presets['gta5e-other']['lines']
# Every other game (owner's decision, 2026-10-12): the ec-hook reporter off, nothing else, no switches.
standard = presets['standard']
assert standard['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0'] and standard['switches'] == '' and standard['version'] == 1, standard
assert 'if size != GameRecommendation.screen720 { entry.resolution = size }\n            else if let lines720 = ResolutionChoices.screen720Value { entry.resolution = lines720 }' in apply_src
gta = presets['gta5e-steam']
assert 'env.MADEIRA_CHILD_ARGS = PlayGTAV.exe -nobattleye' in gta['lines']
assert 'env.MADEIRA_DOCK_KEEP_ALIVE = Launcher.exe;GTA5_Enhanced.exe' in gta['lines']
assert 'semaphoreFastPath: true, fpsMode: 4' in gta['switches'] and 'nvidia: true' in gta['switches']
assert 'env.MADEIRA_FASTSYNC_SEM = 1' in presets['gta5e-other']['lines']
# Version 2 of both GTA lists (2026-10-12): the orphan-lock reaper's stamped mode, as RDR2 and
# Horizon Zero Dawn have. The default mode released a busy SocialClubHelper.exe lock at Story Mode.
for ident in ('gta5e-steam', 'gta5e-other', 'rdr2-steam', 'rdr2-other', 'horizon-zero-dawn'):
    assert 'env.MADEIRA_LOCK_ORPHAN = stamped' in presets[ident]['lines'], ident
# Version 3 of the Steam list (build 514): the VC++ setup's elevated engine is not started, as for RDR2.
# steam 4 (2026-10-21): env.MADEIRA_FEX_AVX = 0
assert presets['gta5e-steam']['version'] == 4 and presets['gta5e-other']['version'] == 2, \
    (presets['gta5e-steam']['version'], presets['gta5e-other']['version'])
for ident in ('rdr2-steam', 'rdr2-other', 'gta5e-steam'):
    assert 'env.MADEIRA_SPAWN_BLOCK = RockstarErrorHandler;.be\\vc_redist' in presets[ident]['lines'], ident
# GTA V Legacy (Steam) v1: the owner's 2026-10-15 lines; DirectX 11, so no D3D12 lines and no DualSense mode.
legacy = presets['gta5l-steam']
# v2 (owner, build 547): no vram-mb and no swap-mb in the list; a device under 12 GB gets the 3 GB RAM swap.
assert legacy['version'] == 3 and legacy['title'] == 'GTA V Legacy (Steam)', legacy   # 3: env.MADEIRA_FEX_AVX = 0
assert not any(line.startswith(('vram-mb', 'swap-mb')) for line in legacy['lines']), legacy['lines']
assert legacy['small'] == ['swap-mb = 3072'], legacy['small']
assert 'static var smallRAMDevice: Bool { deviceRAMBytes < 10 << 30 }' in lists_src
assert [ident for ident, p in presets.items() if p['small']] == ['gta5l-steam']
assert 'env.MADEIRA_DOCK_KEEP_ALIVE = Launcher.exe;GTA5.exe' in legacy['lines']
assert 'env.MADEIRA_SPAWN_BLOCK = RockstarErrorHandler;.be\\vc_redist' in legacy['lines']
assert 'env.DXMT_WSI_MODE_TABLE = 1' in legacy['lines'] and 'env.MADEIRA_EC_HOOK_TRACE = 0' in legacy['lines']
assert not any(line.startswith(('d3d12-', 'pso-warm', 'env.MADEIRA_PAD_MODE', 'env.MADEIRA_D3D11_SRC', 'env.MADEIRA_GUEST_LOG'))
               for line in legacy['lines']), legacy['lines']
assert legacy['switches'] == ('avx: false, nvidia: true, wineVCRT: false, fastSync: true, semaphoreFastPath: true, fpsMode: 4, '
                              'display: "fit"'), legacy['switches']
assert 'resolution: "1280x720"' in presets['ghost-of-tsushima']['switches'] and 'avx: true' in presets['ghost-of-tsushima']['switches']
assert 'resolution: "1920x1080"' in presets['god-of-war']['switches']
# Horizon Zero Dawn, still being brought up: build 477's test list (register spaces other than 0,
# DXGI lists the virtual monitor's own modes so the game can pick this screen's resolution, its
# window is made foreground while none is, XInput pads reach Windows.Gaming.Input, and the
# shaders the converter refuses are retried with sized ranges, remembered and replaced by
# placeholder pipelines; pipelines are warmed in the background). Version 5 (build 480): the
# orphan-lock reaper releases only locks a live thread has stamped. Version 6 (build 481): shader
# libraries only for pipelines in use, rectangle clears, the game's memory in the swap file
# (broad, from 1 MB), and no pso-warm (its queue held 24,570 pipelines when iOS closed the game).
# Version 7 (build 483): the skin-check and view-census diagnostics for its compute-skinned
# characters (missing faces, missing hair, spikes every few frames on 481, gpu-sync or not).
# Version 8 (build 484): every candidate fix at once, one line each, to be halved if it helps:
# no wave operations, raw views readable as typed, ShaderAtomic on R32 typed UAVs, and FEX's
# x86 ordering for vector, memcpy and split-lock accesses and for images with volatile metadata.
# Version 9 (build 485): none of those helped (owner, 484 test), so they are gone again;
# typed buffer views start at Apple's 16-byte boundary, and skin-check traces whole frames and
# dumps the compute skinning's data, comparing what the GPU read with what the CPU wrote.
# Version 10 (build 486): the characters came out whole on 485; typed views start exactly at
# their first element where the probe allows (plants and lighting still flickered), and the
# skinning diagnostics are gone again (view-census stays, it counts views left with an offset).
# Version 11 (build 487): nothing changed on 486 (the GPU rounds a texture buffer's start down
# to 16 bytes; walls, trees and plants vanished for single frames); typed views of upload
# memory off a 16-byte boundary read an aligned copy, a queue's wait holds until the awaited
# work is committed, and vis-trace logs every frame's draws and the culling pyramid.
# Version 12 (build 489): desc-guard logs descriptors rewritten while a batch on the GPU uses them.
# Version 13 (build 490): the readback buffers (the culling pyramid) on Metal's own memory, not the swap tier.
# Version 14 (build 491): single constant buffers of descriptor tables read when the draw is recorded.
# Version 15 (build 492): the game rendered right on 491; the list without fence-strict, static-cbv,
# readback-swap and the trace diagnostics stayed clean, and without typed-view-shadow it broke again.
# Version 16 (build 494): + quiet-log and MADEIRA_SRV_FGETPATH, for frame rate (493, 2026-10-11).
hzd = presets['horizon-zero-dawn']
# Horizon Forbidden West, version 1: build 517's eleventh try (2026-10-13 10:55), its first list. AVX on,
# frees released at once, its programs from their own image, the swap file from 64 KB, a 3 GB budget with
# budget events, HZD's typed-view alignment without the aligned copies, open render passes across lists,
# and the frame-rate switches (quiet-log, lazy shader libraries, eco, lean diagnostics, quiet fixmes).
# Version 2 (build 526's test): the aligned copies on (vegetation in the sky without them, 525) and
# discarded targets loaded again (black squares in FSR 3's generated frames with rp-discard, 525).
# Version 3 (build 528's test): vegetation still in the sky on 526; the census and skin-check
# diagnostics that found HZD's view, without quiet-log (it hides their lines).
# Version 4 (build 529's test): the GPU read what the CPU wrote (528), but 1,789 typed views were
# R32G32B32 with no Metal format: typed-view-rgb32 on; the diagnostics off and quiet-log back.
# Version 5 (build 534): mip0-full, the fix for FSR 3's black squares (its inpainting pyramid, MipLevels 0;
# build 533: the squares are gone).
hfw = presets['horizon-forbidden-west']
assert hfw['lines'][2:] == ['vram-mb = 3072', 'swap-mb = 8192', 'swap-mode = 2', 'swap-min-mb = 1', 'swap-images = 1',
                            'typed-view-align = 16', 'typed-view-shadow = 1', 'typed-view-rgb32 = 1', 'mip0-full = 1', 'rp-carry = 1',
                            'quiet-log = 1', 'pso-lazy-libs = 1', 'eco = 1',
                            'env.MADEIRA_DXGI_SRC = 1', 'env.MADEIRA_DXGI_BUDGET_EVENTS = 1', 'env.MADEIRA_SWAP_MIN_KB = 64',
                            'env.MADEIRA_SWAP_RESERVE_MAX_MB = 9216', 'env.MADEIRA_INPUT_FOREGROUND = 1',
                            'env.MADEIRA_WGI_HOST_PADS = 1', 'env.MADEIRA_LOCK_ORPHAN = stamped', 'env.MADEIRA_SRV_FGETPATH = 1',
                            'env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_FREE_DELAY_MS = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1',
                            'env.MADEIRA_PAD_MODE = hid', 'env.MADEIRA_LEAN_DIAG = 1',
                            'env.WINEDEBUG = err+all,err-virtual,fixme-all'], hfw['lines']
assert hfw['version'] == 5 and hfw['switches'] == ('avx: true, nvidia: true, wineVCRT: false, fastSync: true, fpsMode: 3, '
                                                   'display: "fit"'), hfw
assert hzd['lines'][2:] == ['dxbc-register-spaces = 1', 'msc-unbounded-retry = 1', 'msc-fail-memo = 1',
                            'pso-placeholder = 1', 'pso-lazy-libs = 1', 'clear-rects = 1',
                            'typed-view-align = 16', 'typed-view-shadow = 1', 'view-census = 1',
                            'quiet-log = 1',
                            'swap-mb = 6144', 'swap-mode = 2', 'swap-min-mb = 1',
                            'env.DXMT_WSI_MONITOR_IDENTITY = 1', 'env.DXMT_WSI_MODE_TABLE = 1',
                            'env.MADEIRA_INPUT_FOREGROUND = 1', 'env.MADEIRA_WGI_HOST_PADS = 1',
                            'env.MADEIRA_LOCK_ORPHAN = stamped', 'env.MADEIRA_SRV_FGETPATH = 1',
                            'env.WINEDEBUG = fixme-input',
                            # Version 17 (2026-10-23, build 591): the Dock start's MFC link; a Dock session of this game cannot start without it
                            'env.MADEIRA_MFC_DOCK = 1'], hzd['lines']
rca = presets['ratchet-clank-rift-apart']
mm = presets['marvels-spider-man-miles-morales']
assert not any(line.startswith('env.MADEIRA_SRV_FGETPATH')
               for p in presets.values() if p not in (hzd, hfw, rca) for line in p['lines']), \
    'the build 494 fgetpath switch is the two Horizon games\' and Rift Apart\'s only'
assert not any(line.startswith('quiet-log')
               for p in presets.values() if p not in (hzd, hfw, rca, mm) for line in p['lines']), \
    'quiet-log is the two Horizon games\', Rift Apart\'s and Miles Morales\' only'
assert hzd['version'] == 17 and hzd['switches'] == 'avx: false, nvidia: false, wineVCRT: false, fastSync: true, display: "fit"', hzd
assert not any(line.startswith(('d3d12-wave-ops', 'd3d12-raw-typed-views', 'typed-uav-atomic', 'ignore-volatile-metadata',
                                 'env.FEX_MEMCPYSETTSOENABLED', 'env.FEX_STRICTINPROCESSSPLITLOCKS'))
               for p in presets.values() for line in p['lines']), 'the build 484 experiment is in no list'
assert not any(line.startswith('clear-rects')
               for p in presets.values() if p is not hzd for line in p['lines']), 'clear-rects is Horizon Zero Dawn\'s only'
assert not any(line.startswith('pso-lazy-libs')
               for p in presets.values() if p not in (hzd, hfw, rca) for line in p['lines']), \
    'pso-lazy-libs is the two Horizon games\' and Rift Apart\'s only'
assert not any(line.startswith(('skin-check', 'skin-dump', 'fence-strict', 'vis-trace', 'readback-far', 'desc-guard', 'readback-swap',
                                 'static-cbv', 'env.FEX_VECTORTSOENABLED = 1'))
               for p in presets.values() for line in p['lines']), 'the build 482-491 experiments are in no list'
# Ghost of Tsushima, version 2 (build 492): Horizon Zero Dawn's proven typed-view fix, with the census.
# Version 3 (build 515, the owner's working list): the ec-hook reporter off, a 4 GB swap file, and
# thread stacks that may take the kernel's pick (a thread the game could not create froze it, 15:39).
got = presets['ghost-of-tsushima']
assert got['lines'][2:] == ['dxil-tess = 0', 'sampler-reduction = 3', 'ind-count = 6000', 'typed-view-align = 16',
                            'typed-view-shadow = 1', 'view-census = 1', 'env.MADEIRA_PAD_MODE = hid', 'swap-mb = 4096',
                            'env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_THREAD_STACK_SPILL = 1'], got['lines']
assert got['version'] == 3, got['version']
assert got['switches'] == ('avx: true, nvidia: true, wineVCRT: false, fastSync: true, semaphoreFastPath: false, '
                           'resolution: "1280x720", display: "fit"'), got['switches']
assert not any(line.startswith('typed-view-rgb32') for p in presets.values() if p not in (hfw, rca)
               for line in p['lines']), 'typed-view-rgb32 is HFW\'s and Rift Apart\'s only'
assert not any(line.startswith('mip0-full') for p in presets.values() if p not in (hfw, rca, mm)
               for line in p['lines']), 'mip0-full is HFW\'s, Rift Apart\'s and Miles Morales\' only'
# Ratchet & Clank: Rift Apart, version 1 (2026-10-14): not yet seen running (a tester's first try on 531 ended
# seconds after start with AVX off). HFW v5's general lines: no Decima typed-view alignment or copies, no
# rp-carry, no eco; AVX and NVIDIA on.
assert rca['lines'][2:] == ['vram-mb = 2048', 'swap-mb = 1024', 'swap-mode = 2', 'swap-min-mb = 1', 'swap-images = 1',
                            'typed-view-rgb32 = 1', 'mip0-full = 1', 'quiet-log = 1', 'pso-lazy-libs = 1', 'fence-early = 1',
                            'env.MADEIRA_DXGI_SRC = 1', 'env.MADEIRA_DXGI_BUDGET_EVENTS = 1', 'env.MADEIRA_SWAP_MIN_KB = 64',
                            'env.MADEIRA_SWAP_RESERVE_MAX_MB = 9216', 'env.MADEIRA_INPUT_FOREGROUND = 1',
                            'env.MADEIRA_WGI_HOST_PADS = 1', 'env.MADEIRA_LOCK_ORPHAN = stamped', 'env.MADEIRA_SRV_FGETPATH = 1',
                            'env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_FREE_DELAY_MS = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1',
                            'env.MADEIRA_PAD_MODE = hid', 'env.MADEIRA_LEAN_DIAG = 1', 'env.MADEIRA_JUMBO_HALF = 1',
                            'env.MADEIRA_CAGE_RELEASE = 1', 'env.MADEIRA_ALIAS_RELEASE = 1', 'env.LC_ALL = en_US.UTF-8',
                            'env.WINEDEBUG = err+all,err-virtual,fixme-all', 'env.MADEIRA_VA_PRESSURE_GAP_OK = 1'], rca['lines']
# Version 2 (build 550): env.MADEIRA_JUMBO_HALF, the owner's 549 try (22:07:15) ran out of 8 GB holes for
# the engine's arena probes after the intro videos. Rift Apart's only.
# Version 3 (build 550, 23:21:48, the game ran): env.MADEIRA_CAGE_RELEASE, the 8 GB V8 cage holdback goes to
# the guest band when it runs out (R6025 at 10-43 s before: secur32.dll found no VA). Rift Apart's only.
# Version 4 (build 553, 10:43:45 and 10:45:10: R6025 again): env.LC_ALL = en_US.UTF-8 back. v3 had left it
# out, and without it the game throws right after loading the Turkish code page 857 (as on 550 22:49:00).
# Version 5 (build 555, 12:32:19: the save that froze on 553 loaded): env.MADEIRA_ALIAS_RELEASE, the JIT pool
# records of a released arena go with it (553 11:21:36 and 11:22:59 froze on the stale ones).
# Version 6 (owner, 2026-10-16, the game set aside): the owner's 558 run (15:58:00) line for line -- vram-mb 2048 and
# swap-mb 1024 as the owner ran them from 553 on, fence-early, and env.MADEIRA_VA_PRESSURE_GAP_OK (8 "not latched",
# 0 [steer], 5.45 GB peak, where 13:23 without it had 15 [steer] and 6.9 GB).
RCA_OWN = {'env.MADEIRA_JUMBO_HALF = 1', 'env.MADEIRA_CAGE_RELEASE = 1', 'env.MADEIRA_ALIAS_RELEASE = 1',
           'env.LC_ALL = en_US.UTF-8', 'fence-early = 1', 'env.MADEIRA_VA_PRESSURE_GAP_OK = 1'}
assert set(rca['lines'][2:]) - RCA_OWN - {'vram-mb = 2048', 'swap-mb = 1024'} < set(hfw['lines'][2:]), \
    'Rift Apart\'s lines are a subset of HFW\'s, apart from its own lines and its vram-mb / swap-mb values'
assert not any(line.startswith(('env.MADEIRA_JUMBO_HALF', 'env.MADEIRA_CAGE_RELEASE', 'env.MADEIRA_ALIAS_RELEASE',
                                'env.LC_ALL', 'fence-early', 'env.MADEIRA_VA_PRESSURE_GAP_OK')) for p in presets.values()
               if p is not rca for line in p['lines']), \
    'jumbo-half, cage-release, alias-release, LC_ALL, fence-early and va-pressure-gap are Rift Apart\'s only'
assert rca['version'] == 6 and rca['switches'] == ('avx: true, nvidia: true, wineVCRT: false, fastSync: true, fpsMode: 3, '
                                                   'display: "fit"'), rca
assert not any(line.startswith(('view-census', 'typed-view-align', 'typed-view-shadow'))
               for p in presets.values() if p not in (hzd, got, hfw) for line in p['lines']), \
    'the typed-view fix is HZD\'s, GoT\'s and HFW\'s only'
# Crysis Remastered, version 1 (build 528, 2026-10-13 22:08, "mükemmel çalıştı"): the standard
# list's line with every switch off, at Fit.
crysis = presets['crysis-remastered']
assert crysis['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0'], crysis['lines']
assert crysis['version'] == 1 and crysis['switches'] == 'avx: false, nvidia: false, wineVCRT: false, display: "fit"', crysis
# Crysis 2 and 3 Remastered, version 1 (owner, 2026-10-15; not yet run with them): build 540's tries stopped with
# AVX off (Crysis 2) and in Microsoft's msvcp140 (Crysis 3), so AVX on, and Wine's C++ runtime for Crysis 3.
c = presets['crysis-2-remastered']
assert c['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0'], c['lines']
assert c['version'] == 1 and c['switches'] == 'avx: true, nvidia: false, wineVCRT: false, display: "fit"', c
# Crysis 3 Remastered, version 2 (547, 15:08:17): the same stop; the game's own x64 vcruntime140.dll next to Wine's
# ARM64EC msvcp140. env.MADEIRA_APP_DLL_SHADOW refuses that copy; Wine's C++ runtime switch off again.
c = presets['crysis-3-remastered']
assert c['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_APP_DLL_SHADOW = vcruntime140.dll'], c['lines']
assert c['version'] == 2 and c['switches'] == 'avx: true, nvidia: false, wineVCRT: false, display: "fit"', c
assert not any(line.startswith('env.MADEIRA_APP_DLL_SHADOW') for p in presets.values() if p is not c for line in p['lines']), \
    'the app-local DLL shadow is Crysis 3 Remastered\'s only'
# Microsoft Flight Simulator 2024, version 1 (for build 538): being brought up. Build 534 stopped in the loader
# (srvcli.dll missing, 2026-10-14 20:27); the owner's test settings: the standard line, AVX and NVIDIA on, Fit.
# Version 2: on 538 (21:20) the JIT pool ran out at CoherentGTCore.dll, so pure-x64 images get no pool copy.
# Version 3: on 538 (21:41) the game asked for a feature level 12_1 device; d3d12-fl-12-1 claims it (539, cancelled).
# Version 4: d3d12-sm-6-7 too; the game requires shader model 6.7 (540).
# Version 5: 540 stopped with the game's "FiberError"; env.MADEIRA_FIBER_TRACE = 1 logged the fiber calls (541's test).
# Version 6 (owner, 2026-10-15): the trace leaves the list; MSFS 2024's fibers are its own (547), the game is on hold.
msfs = presets['msfs-2024']
assert msfs['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'd3d12-fl-12-1 = 1',
                             'd3d12-sm-6-7 = 1'], msfs['lines']
assert not any(line.startswith('env.MADEIRA_FIBER_TRACE') for p in presets.values() for line in p['lines']), \
    'the fiber trace is a diagnostic, in no list'
assert not any(line.startswith(('d3d12-fl-12-1', 'd3d12-sm-6-7')) for p in presets.values() if p is not msfs for line in p['lines']), \
    'd3d12-fl-12-1 and d3d12-sm-6-7 are MSFS 2024\'s only'
assert msfs['version'] == 6 and msfs['switches'] == 'avx: true, nvidia: true, wineVCRT: false, display: "fit"', msfs
# Counter-Strike 2, version 1 (owner, 2026-10-16; not yet run): Direct3D 11 through DXMT like Crysis Remastered, the
# standard line, every switch off (CS2 needs SSE4.2, which FEX reports, and no AVX). Version 2 (owner's runs,
# 2026-10-20): the JIT pool shrank and filled while cs2.exe loaded; pool-split = 1 started it.
cs2 = presets['counter-strike-2']
assert cs2['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'pool-split = 1'], cs2['lines']
assert cs2['version'] == 2 and cs2['switches'] == 'avx: false, nvidia: false, wineVCRT: false, display: "fit"', cs2
# Spelunky 2 and Albion Online's launcher, version 1 (2026-10-20 logs J2YJF8, BYBFCH, PGKTDD): the same full pool;
# the standard line plus the split pool, every switch off.
# Spelunky 2 version 2 (2026-10-21, logs XKGV37 / JBNAZ1): plus the two DXMT monitor lines RDR2's list uses.
# Spelunky 2 version 3 (round 579): NOCOPY instead of the split pool (JBNAZ1 got furthest with NOCOPY alone; the 558 MiB
# exe fits no split-pool region).
q = presets['spelunky-2']
assert q['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'env.DXMT_WSI_MONITOR_IDENTITY = 1',
                          'env.DXMT_WSI_MODE_TABLE = 1'], q['lines']
assert q['version'] == 3 and q['switches'] == 'avx: false, nvidia: false, wineVCRT: false, display: "fit"', q
assert {'line': 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'minBuild': 576} in next(r for r in recs['rules'] if r['id'] == 'spelunky-2')['config']
for ident, extra, ver in (('albion-online', [], 1),):
    q = presets[ident]
    assert q['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'pool-split = 1'] + extra, (ident, q['lines'])
    assert q['version'] == ver and q['switches'] == 'avx: false, nvidia: false, wineVCRT: false, display: "fit"', q
# Warcraft II BNE, version 1 (tester's build 559 logs, 2026-10-16): the standard line and plain RWX memory for its
# self-modifying blitters, every switch off.
wc2 = presets['warcraft-2-bne']
assert wc2['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WOW_RWX_PLAIN = 1'], wc2['lines']
assert wc2['version'] == 1 and wc2['switches'] == 'avx: false, nvidia: false, wineVCRT: false, display: "fit"', wc2
# Breakneck, version 1 (the owner's build 562 runs, 2026-10-17): an old 32-bit Unity game; the same plain RWX memory
# for Mono's JIT chunks, every switch off.
bn = presets['breakneck']
assert bn['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WOW_RWX_PLAIN = 1'], bn['lines']
assert bn['version'] == 1 and bn['switches'] == 'avx: false, nvidia: false, wineVCRT: false, display: "fit"', bn
assert not any(line.startswith('env.MADEIRA_WOW_RWX_PLAIN') for p in presets.values() if p not in (wc2, bn, presets['pwaat']) for line in p['lines']), \
    'plain RWX memory is Warcraft II\'s, Breakneck\'s and PWAAT\'s only'
print('PASS: RDR2 (Steam) is the owner\'s 463 list plus the 466, 467, 470, 472, 475, 476, 480 and 514 test lines and the AVX pin (v11), line for line with its switches (720 lines of this screen), and outside Steam the same without the Dock lines, with the ec-hook reporter off (v1); GTA V Enhanced keeps its tested lines plus the stamped orphan-lock mode (v2) and, from Steam, the VC++ engine block (v3) and the AVX pin (v4), GoW its tested lines; Horizon Zero Dawn has build 491\'s proven list plus build 494\'s frame-rate switches (v16), GoT the owner\'s build 515 list (v3); Horizon Forbidden West its eleventh try\'s list with the aligned copies on and discarded targets loaded (v4: float3 views get a copy too; v5: MipLevels 0 textures get their full chain); Rift Apart HFW\'s general lines with AVX and NVIDIA on (v1); GTA V Legacy from Steam the owner\'s DirectX 11 lines (v1), without vram-mb and swap-mb and with the 3 GB RAM swap only under 12 GB of RAM (v2) and the AVX pin (v3); Crysis Remastered the standard line with every switch off (v1); Crysis 3 Remastered with its own vcruntime140.dll refused (v2); MSFS 2024 the standard line with AVX and NVIDIA on (v1), pure-x64 images without a pool copy (v2), feature level 12_1 (v3), shader model 6.7 (v4), the fiber trace (v5), then without it (v6); Counter-Strike 2 the standard line with every switch off (v1), then the split pool (v2); Spelunky 2 and Albion Online the split pool (v1); Warcraft II BNE plain RWX memory (v1); Breakneck the same (v1); every other game the standard list (v1)')

assert presets['megabonk']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WINTYPES = 1'] and presets['megabonk']['version'] == 1, presets['megabonk']
assert presets['no-im-not-a-human']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WINTYPES = 1'] and presets['no-im-not-a-human']['version'] == 1, presets['no-im-not-a-human']
assert presets['over-the-hill']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WINTYPES = 1'] and presets['over-the-hill']['version'] == 1, presets['over-the-hill']
assert presets['fort-solis']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_OPTIN_DLLS = dxva2'] and presets['fort-solis']['version'] == 1, presets['fort-solis']
assert presets['dragon-ball-sparking-zero']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_OPTIN_DLLS = dxva2'] and presets['dragon-ball-sparking-zero']['version'] == 1, presets['dragon-ball-sparking-zero']
assert presets['animal-well']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_OPTIN_DLLS = xpsprint'] and presets['animal-well']['version'] == 1, presets['animal-well']
assert presets['hogwarts-legacy']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'exe-window-mb = 448'] and presets['hogwarts-legacy']['version'] == 1, presets['hogwarts-legacy']
assert presets['metal-gear-solid-v']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'exe-window-mb = 176'] and presets['metal-gear-solid-v']['version'] == 1, presets['metal-gear-solid-v']
assert presets['stellar-blade-demo']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'exe-window-mb = 320', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['stellar-blade-demo']['version'] == 2, presets['stellar-blade-demo']
assert presets['star-wars-galactic-racer']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'exe-window-mb = 496', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['star-wars-galactic-racer']['version'] == 2, presets['star-wars-galactic-racer']
assert presets['denshattack']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_VCRT_ARM64_KEY = 1'] and presets['denshattack']['version'] == 1, presets['denshattack']
assert presets['final-fantasy-resonance-demo']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_VCRT_ARM64_KEY = 1', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['final-fantasy-resonance-demo']['version'] == 2, presets['final-fantasy-resonance-demo']
assert presets['clair-obscur-expedition-33']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['clair-obscur-expedition-33']['version'] == 1 and presets['clair-obscur-expedition-33']['switches'] == '', presets['clair-obscur-expedition-33']
# Build 579 (2026-10-21): the two Rockstar Launcher titles outside the GTA V / RDR2 lists (Send Logs 4GAWSA, MM0DAY: the Launcher's
# RockstarService.exe found no service manager, "[svc] Unable to open SC manager! (1722)"): GTA V's Launcher lines and no engine line.
_scm = {'line': 'env.MADEIRA_DOCK_GAME_SCM = 1', 'minBuild': 576}
for _id in ('gta4-steam', 'gtasa-de-steam'):
    _rule = next(r for r in recs['rules'] if r['id'] == _id)
    # (no KEEP_ALIVE: GTA V's line, with no evidence in these logs; the lead left it out)
    # (GTA IV's version 2, 2026-10-23, adds Social Club's layout 2 after the AVX pin: the Launcher's browser had no PartitionAlloc pools in
    # J1J40D and 3JTHRV; San Andreas DE and RDR1 stay without it, unproven)
    _sc2 = ['env.MADEIRA_SC_PA_POOLS = 2'] if _id == 'gta4-steam' else []
    assert presets[_id]['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_DOCK_GAME_SCM = 1', 'env.MADEIRA_FEX_AVX = 0'] + _sc2 + [
                                         'env.MADEIRA_SPAWN_BLOCK = RockstarErrorHandler', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'], presets[_id]
    assert presets[_id]['version'] == (2 if _id == 'gta4-steam' else 1) and presets[_id]['switches'] == '' and not presets[_id]['small'], presets[_id]
    # every line after the standard one goes to builds from 576 on; none of GTA V's engine, DXGI, adapter or pool lines (GTA IV: the layout only)
    assert all(isinstance(l, dict) and l.get('minBuild') == 576 for l in _rule['config'][3:]), _id
    assert not any(k in ' '.join(presets[_id]['lines']) for k in ('pool-split', 'DXGI', 'D3D11_SRC', 'KMT_ADAPTER',
                                                                  'DXMT_', 'LOCK_ORPHAN', 'EXECREQ_LEAVE', 'CHILD_ARGS', 'vc_redist')), _id
    assert ('SC_PA_POOLS' in ' '.join(presets[_id]['lines'])) == (_id == 'gta4-steam'), _id
assert _scm in next(r for r in recs['rules'] if r['id'] == 'gta4-steam')['config']
# Senua's Saga: Hellblade II has no list: the only log (XJ7Z6Y) was a copy outside Steam; its DLL and auto-tune rule are engine-level.
# Build 579 (2026-10-21): Unrailed! 2 (Send Log CR8H6H): a .NET 7+ garbage collector reserves 256 GB; DOTNET_GCRegionRange = 16 GB (hex), build 579 on.
assert presets['unrailed-2-back-on-track']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.DOTNET_GCRegionRange = 400000000'] and presets['unrailed-2-back-on-track']['version'] == 1 and presets['unrailed-2-back-on-track']['switches'] == '', presets['unrailed-2-back-on-track']
assert {'line': 'env.DOTNET_GCRegionRange = 400000000', 'minBuild': 579} in next(r for r in recs['rules'] if r['id'] == 'unrailed-2-back-on-track')['config']
# Resident Evil 2, version 1 (2026-10-22; build 579 log re2-ipadm4-579: the ASF byte stream handler's DLL is missing and the game reads NULL after
# its first cutscene): BCD's 64-bit media decoders and the three opt-in DLLs, for build 580 and later; nothing else (docs/MEDIA.md, ASF and WMV).
assert presets['resident-evil-2']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WG_64BIT = 1', 'env.MADEIRA_OPTIN_DLLS = mfasfsrcsnk,wmvdecod,wmadmod'] and presets['resident-evil-2']['version'] == 1 and presets['resident-evil-2']['switches'] == '', presets['resident-evil-2']
_re2 = next(r for r in recs['rules'] if r['id'] == 'resident-evil-2')
assert {'line': 'env.MADEIRA_WG_64BIT = 1', 'minBuild': 580} in _re2['config'] and {'line': 'env.MADEIRA_OPTIN_DLLS = mfasfsrcsnk,wmvdecod,wmadmod', 'minBuild': 580} in _re2['config'], _re2['config']
# Resident Evil 7 biohazard: the same failure chain (log 5MXS1S, build 576), the same two lines; only these two games list them
assert presets['resident-evil-7']['lines'][2:] == presets['resident-evil-2']['lines'][2:] and presets['resident-evil-7']['version'] == 1, presets['resident-evil-7']
assert [r['id'] for r in recs['rules'] if any(n in json.dumps(r['config']) for n in ('mfasfsrcsnk', 'wmvdecod', 'wmadmod', 'MADEIRA_WG_64BIT'))] == ['resident-evil-2', 'resident-evil-7']
# Build 580 list (2026-10-22, the tur580 log wave; docs/DURUM.md section 2): lists for games that fail today, each line from a log. Every
# line that names a key a download may set only from build 580 (FEX_X87REDUCEDPRECISION, MADEIRA_TLS_CLEAR_OWN_PROCESS, MADEIRA_VDCFG_PATH:
# build 579's policy does not know them and would reject the whole download) carries minBuild 580.
_std = 'env.MADEIRA_EC_HOOK_TRACE = 0'
_nocopy = 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'
for _id in ('blazblue-entropy-effect', 'dark-souls-iii', 'marvels-spider-man-remastered', 'raidou-remastered'):
    assert presets[_id]['lines'][2:] == [_std, _nocopy] and presets[_id]['version'] == 1 and presets[_id]['switches'] == '', presets[_id]
    assert {'line': _nocopy, 'minBuild': 576} in next(r for r in recs['rules'] if r['id'] == _id)['config'], _id
assert presets['yakuza-like-a-dragon']['lines'][2:] == [_std, 'exe-window-mb = 416', _nocopy] and presets['yakuza-like-a-dragon']['version'] == 1, presets['yakuza-like-a-dragon']
assert presets['batman-arkham-city-goty']['lines'][2:] == [_std, 'env.FEX_X87REDUCEDPRECISION = 0', 'env.MADEIRA_TLS_CLEAR_OWN_PROCESS = 1'] and presets['batman-arkham-city-goty']['version'] == 1, presets['batman-arkham-city-goty']
_bat = next(r for r in recs['rules'] if r['id'] == 'batman-arkham-city-goty')
assert {'line': 'env.FEX_X87REDUCEDPRECISION = 0', 'minBuild': 580} in _bat['config'] and {'line': 'env.MADEIRA_TLS_CLEAR_OWN_PROCESS = 1', 'minBuild': 580} in _bat['config'], _bat['config']
for _id in ('metal-gear-solid-peace-walker', 'lonely-mountains-snow-riders'):
    assert presets[_id]['lines'][2:] == [_std, 'env.MADEIRA_WINTYPES = 1'] and presets[_id]['version'] == 1, presets[_id]
assert presets['trails-in-the-sky-1st-chapter']['lines'][2:] == [_std, 'env.MADEIRA_VDCFG_PATH = 1'] and presets['trails-in-the-sky-1st-chapter']['version'] == 1, presets['trails-in-the-sky-1st-chapter']
assert {'line': 'env.MADEIRA_VDCFG_PATH = 1', 'minBuild': 580} in next(r for r in recs['rules'] if r['id'] == 'trails-in-the-sky-1st-chapter')['config']
assert presets['subnautica-2']['lines'][2:] == [_std, _nocopy] and presets['subnautica-2']['version'] == 1, presets['subnautica-2']
assert presets['rdr1-steam']['lines'][2:] == [_std, 'env.MADEIRA_DOCK_GAME_SCM = 1', 'env.MADEIRA_SPAWN_BLOCK = RockstarErrorHandler', _nocopy] and presets['rdr1-steam']['version'] == 1, presets['rdr1-steam']
assert all(isinstance(l, dict) and l.get('minBuild') == 576 for l in next(r for r in recs['rules'] if r['id'] == 'rdr1-steam')['config'][3:])
assert not any('FEX_AVX' in l for l in presets['rdr1-steam']['lines']), 'RDR1 leaves out gta4-steam\'s AVX pin (no c000001d in its log)'
# Build 591 list (2026-10-23, the tur591 log wave; docs/DURUM.md section 2): lists for games that fail today, each line from a log. Every line
# whose switch is read only from build 591 on (the first five names below, and _JAVA_OPTIONS) carries minBuild 591; the older switches keep the
# build their neighbours use. The game's own line list, in order, is pinned for each new rule.
def _cfg(_id):
    return next(r for r in recs['rules'] if r['id'] == _id)['config']
_req = presets['resident-evil-requiem']
assert _req['lines'][2:] == [_std, 'exe-window-mb = 592', _nocopy] and _req['version'] == 1 and _req['switches'] == '', _req
assert {'line': 'exe-window-mb = 592', 'minBuild': 575} in _cfg('resident-evil-requiem') and {'line': _nocopy, 'minBuild': 576} in _cfg('resident-evil-requiem')
assert presets['life-is-strange-remastered']['lines'][2:] == [_std, _nocopy] and presets['life-is-strange-remastered']['version'] == 1, presets['life-is-strange-remastered']
assert presets['life-is-strange-2']['lines'][2:] == [_std, 'exe-window-mb = 400', _nocopy] and presets['life-is-strange-2']['version'] == 1, presets['life-is-strange-2']
for _id in ('life-is-strange-remastered', 'life-is-strange-2'):
    assert {'line': _nocopy, 'minBuild': 576} in _cfg(_id), _id
# a fixed-base window is the image plus 16-20 MiB and fits the free run above 0x140000000 (751 MiB in the RE Requiem logs, 721 in Life is Strange 2's)
assert 575.07 < 592 < 720 and 379.2 < 400 < 720
assert presets['project-zomboid']['lines'][2:] == [_std, 'env._JAVA_OPTIONS = -XX:-UseZGC'] and presets['project-zomboid']['version'] == 1, presets['project-zomboid']
assert {'line': 'env._JAVA_OPTIONS = -XX:-UseZGC', 'minBuild': 591} in _cfg('project-zomboid')
_new591 = {
    'spiderheck': 'env.MADEIRA_FAULT_GUARD_RATE = 1', 'rounds': 'env.MADEIRA_FAULT_GUARD_RATE = 1', 'plateup': 'env.MADEIRA_FAULT_GUARD_RATE = 1',
    'final-fantasy-tactics-ivalice-chronicles': 'env.MADEIRA_USER_VA_GB = 32', 'age-of-empires-ii-hd': 'env.MADEIRA_MFC_DOCK = 1',
    'fallout-new-vegas': 'env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1', 'digimon-story-cyber-sleuth': 'env.MADEIRA_OPTIN_DLLS = oledlg',
    'final-fantasy-xv-demo': 'env.MADEIRA_OPTIN_DLLS = fwpuclnt', 'borderlands-3': 'env.MADEIRA_OPTIN_DLLS = sensapi',
    'jetboard-joust': 'env.MADEIRA_ANON_CAS_FLUSH = 1', 'sword-and-fairy-3': 'env.MADEIRA_NLS_EXTRA = 1',
}
for _id, _line in _new591.items():
    assert presets[_id]['lines'][2:] == [_std, _line] and presets[_id]['version'] == 1 and presets[_id]['switches'] == '', (_id, presets[_id])
    assert {'line': _line, 'minBuild': 591} in _cfg(_id), _id
# bcd-games issue #13 (2026-10-23): Unreal start-up programs that ask for the ARM64 VC++ redistributable
assert presets['dead-as-disco']['lines'][2:] == [_std, 'env.MADEIRA_OPTIN_DLLS = tbs', 'env.MADEIRA_VCRT_ARM64_KEY = 1'] and presets['dead-as-disco']['version'] == 1, presets['dead-as-disco']
assert {'line': 'env.MADEIRA_OPTIN_DLLS = tbs', 'minBuild': 580} in _cfg('dead-as-disco') and {'line': 'env.MADEIRA_VCRT_ARM64_KEY = 1', 'minBuild': 576} in _cfg('dead-as-disco')
assert presets['tung']['lines'][2:] == [_std, 'env.MADEIRA_VCRT_ARM64_KEY = 1'] and {'line': 'env.MADEIRA_VCRT_ARM64_KEY = 1', 'minBuild': 576} in _cfg('tung')
# ZZZ: dxva2 is an old opt-in name (Fort Solis, Dragon Ball Sparking Zero carry it at 575)
assert presets['zenless-zone-zero']['lines'][2:] == [_std, 'env.MADEIRA_OPTIN_DLLS = dxva2'] and {'line': 'env.MADEIRA_OPTIN_DLLS = dxva2', 'minBuild': 575} in _cfg('zenless-zone-zero')
# the two rules that already existed: GTA IV gains Social Club's layout 2 (version 2), Horizon Zero Dawn the Dock MFC link (version 17)
assert presets['gta4-steam']['version'] == 2 and 'env.MADEIRA_SC_PA_POOLS = 2' in presets['gta4-steam']['lines'] and {'line': 'env.MADEIRA_SC_PA_POOLS = 2', 'minBuild': 576} in _cfg('gta4-steam')
for _id in ('gtasa-de-steam', 'rdr1-steam'):
    assert not any('SC_PA_POOLS' in l for l in presets[_id]['lines']), _id + ': unproven, left alone'
assert presets['horizon-zero-dawn']['version'] == 17 and presets['horizon-zero-dawn']['lines'][-1] == 'env.MADEIRA_MFC_DOCK = 1'
assert {'line': 'env.MADEIRA_MFC_DOCK = 1', 'minBuild': 591} in _cfg('horizon-zero-dawn') and presets['horizon-zero-dawn']['lines'][2:-1] == [l for l in presets['horizon-zero-dawn']['lines'][2:] if 'MFC_DOCK' not in l]
# no other game of the list carries a build 591 switch: only these rules name them
_names591 = ('MADEIRA_FAULT_GUARD_RATE', 'MADEIRA_USER_VA_GB', 'MADEIRA_MFC_DOCK', 'MADEIRA_DOCK_SCRIPT_REGISTRY', 'MADEIRA_ANON_CAS_FLUSH', 'MADEIRA_NLS_EXTRA', '_JAVA_OPTIONS')
assert sorted(r['id'] for r in recs['rules'] if any(n in json.dumps(r['config']) for n in _names591)) == sorted([i for i in _new591 if 'OPTIN_DLLS' not in _new591[i]] + ['project-zomboid', 'horizon-zero-dawn'])
# the names a list line may use only from build 591 are in the policy, with a value check
_policy_src = (app / 'GameRecommendationsList.swift').read_text()
for _name in _names591:
    assert '"%s"' % _name in _policy_src, _name
assert 'case "MADEIRA_FAULT_GUARD_RATE", "MADEIRA_MFC_DOCK", "MADEIRA_DOCK_SCRIPT_REGISTRY", "MADEIRA_ANON_CAS_FLUSH", "MADEIRA_NLS_EXTRA", "MADEIRA_WG_VIDEO":' in _policy_src
assert 'case "MADEIRA_USER_VA_GB":' in _policy_src and 'guard value == "-XX:-UseZGC" else' in _policy_src
# The three fixed-base / pool games that already had a list keep their old lines and gain the new one at the end.
for _id, _lines in (('star-wars-galactic-racer', [_nocopy]), ('beamng-drive', [_nocopy]), ('no-rest-for-the-wicked', ['env.MADEIRA_WINTYPES = 1'])):
    assert presets[_id]['lines'][-len(_lines):] == _lines, _id
# the names a list line may use only from build 580 are in the policy, with a 0-or-1 check
for _name in ('FEX_X87REDUCEDPRECISION', 'MADEIRA_TLS_CLEAR_OWN_PROCESS', 'MADEIRA_VDCFG_PATH'):
    _policy_src = (app / 'GameRecommendationsList.swift').read_text()
    assert '"%s"' % _name in _policy_src and 'case "FEX_X87REDUCEDPRECISION", "MADEIRA_TLS_CLEAR_OWN_PROCESS", "MADEIRA_VDCFG_PATH":' in _policy_src, _name
    assert any(isinstance(l, dict) and l['line'].startswith('env.%s ' % _name) and l.get('minBuild') == 580 for r in recs['rules'] for l in r['config']), _name
# a new rule's notes quote the log lines they come from
for _id in ['blazblue-entropy-effect', 'dark-souls-iii', 'marvels-spider-man-remastered', 'raidou-remastered', 'yakuza-like-a-dragon', 'batman-arkham-city-goty',
            'metal-gear-solid-peace-walker', 'lonely-mountains-snow-riders', 'trails-in-the-sky-1st-chapter', 'subnautica-2', 'rdr1-steam'] + list(_new591) + ['resident-evil-requiem', 'life-is-strange-remastered', 'life-is-strange-2', 'project-zomboid']:
    _notes = ' '.join(next(r for r in recs['rules'] if r['id'] == _id)['notes'])
    assert re.search(r'\bL[0-9]{3,6}\b', _notes), _id
# Build 579 (2026-10-21): the NOCOPY line of the three games whose images got no JIT-pool copy (Send Logs YBX2WT, AVFMGC, EJDV7Z, YBF7CN)
# goes to builds from 576 on, which know the key (it is older, but the line is new to these games' lists).
for _id in ('stellar-blade-demo', 'final-fantasy-resonance-demo', 'clair-obscur-expedition-33'):
    _rule = next(r for r in recs['rules'] if r['id'] == _id)
    assert {'line': 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'minBuild': 576} in _rule['config'], _id
# Stellar Blade Demo and FINAL FANTASY RESONANCE DEMO carry no pool-split: no region of a split pool takes their 314 / 474 MiB exe.
assert not any('pool-split' in l for _id in ('stellar-blade-demo', 'final-fantasy-resonance-demo', 'clair-obscur-expedition-33') for l in presets[_id]['lines'])
assert presets['no-rest-for-the-wicked']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_FEX_BAND_KEEP_MB = 6144', 'dxmt = d3d11.mipClampBC=1', 'env.MADEIRA_WINTYPES = 1'] and presets['no-rest-for-the-wicked']['version'] == 3, presets['no-rest-for-the-wicked']
assert presets['quaver']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.DOTNET_EnableWriteXorExecute = 0'] and presets['quaver']['version'] == 1, presets['quaver']
assert presets['dying-light']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_OPTIN_DLLS = setupapi'] and presets['dying-light']['version'] == 1, presets['dying-light']
assert presets['beamng-drive']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_MACHINE = 1', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['beamng-drive']['version'] == 2, presets['beamng-drive']
assert presets['diablo-ii-resurrected']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_OPTIN_DLLS = sfc,sfc_os,tbs'] and presets['diablo-ii-resurrected']['version'] == 1 and presets['diablo-ii-resurrected']['switches'] == '', presets['diablo-ii-resurrected']
assert presets['american-truck-simulator']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_TINY_WINDOW_FIX = 1'] and presets['american-truck-simulator']['version'] == 1 and presets['american-truck-simulator']['switches'] == '', presets['american-truck-simulator']
assert presets['monster-hunter-stories-3-trial']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and presets['monster-hunter-stories-3-trial']['version'] == 1, presets['monster-hunter-stories-3-trial']
assert presets['pwaat']['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WOW_RWX_PLAIN = 1'] and presets['pwaat']['version'] == 1, presets['pwaat']
for ident in ('lego-party', '1000xresist'):
    assert presets[ident]['lines'][2:] == ['env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_WINTYPES = 1'] and presets[ident]['version'] == 1, presets[ident]
# Marvel's Spider-Man: Miles Morales, version 1 (2026-10-21; logs PD7S1E 574 and the 10:37 build 576 run end on Metal's "level range (2) must
# be <= mipmapLevelCount (1)" after a MipLevels 0 UAV texture; HQFPWG, build 574; the 548 fixture's JIT pool): mip0-full, quiet-log, the standard
# line and auto-tune's own NOCOPY line; NVIDIA on as in the logs that rendered. Rift Apart's other lines are left out on purpose
# (swap tier, DXGI source, cage/jumbo/alias, LC_ALL, va-pressure-gap: the notes say why for each).
assert mm['lines'][2:] == ['mip0-full = 1', 'quiet-log = 1', 'env.MADEIRA_EC_HOOK_TRACE = 0', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1'] and mm['version'] == 1, mm
assert mm['switches'] == 'avx: false, nvidia: true, wineVCRT: false, display: "fit"', mm['switches']
assert not any(line.startswith(('vram-mb', 'swap-', 'env.MADEIRA_VA_PRESSURE_GAP_OK', 'env.MADEIRA_OPTIN_DLLS', 'env.LC_ALL'))
               for line in mm['lines']), 'Miles Morales\' list has none of Rift Apart\'s memory, locale or address-space lines, and no evr opt-in'
mm_notes = ' '.join(n for r in recs['rules'] if r['id'] == 'marvels-spider-man-miles-morales' for n in r['notes'])
assert all(t in mm_notes for t in ('mip0-full = 1:', 'L83617', 'quiet-log = 1:', 'env.MADEIRA_X64_IMAGE_NOCOPY = 1:', 'Left out on purpose',
                                   'MADEIRA_VA_PRESSURE_GAP_OK', 'MADEIRA_OPTIN_DLLS = evr', 'not tried on a device')), 'the notes give each line\'s reason'
# Recognition: Steam by App ID or steamapps\common folder; everything else by the program's name. Each
# rule's "match" is what match() of the Swift literals did (until 2026-10-18), rule by rule; the first
# rule that matches wins, so Enhanced (GTA5_Enhanced.exe beside the program) comes before Legacy.
MATCH = {
    'rdr2-steam': {'steamAppIDs': [1174180], 'steamFolders': ['red dead redemption 2'],
                   'programs': [{'name': 'rdr2.exe', 'steam': True}, {'name': 'playrdr2.exe', 'steam': True}]},
    'rdr2-other': {'programs': [{'name': 'rdr2.exe', 'steam': False}, {'name': 'playrdr2.exe', 'steam': False}]},
    'gta5e-steam': {'steamAppIDs': [3240220], 'steamFolders': ['grand theft auto v enhanced'],
                    'programs': [{'name': 'playgtav.exe', 'steam': True, 'beside': 'GTA5_Enhanced.exe'},
                                 {'name': 'gta5_enhanced.exe', 'steam': True, 'beside': 'GTA5_Enhanced.exe'}]},
    'gta5e-other': {'programs': [{'name': 'playgtav.exe', 'steam': False, 'beside': 'GTA5_Enhanced.exe'},
                                 {'name': 'gta5_enhanced.exe', 'steam': False, 'beside': 'GTA5_Enhanced.exe'}]},
    'gta5l-steam': {'steamAppIDs': [271590], 'steamFolders': ['grand theft auto v'],
                    'programs': [{'name': 'playgtav.exe', 'steam': True, 'beside': 'GTA5.exe', 'notBeside': 'GTA5_Enhanced.exe'},
                                 {'name': 'gta5.exe', 'steam': True}]},
    'ghost-of-tsushima': {'steamAppIDs': [2215430], 'steamFolders': ["ghost of tsushima director's cut", 'ghost of tsushima'],
                          'programs': [{'name': 'ghostoftsushima.exe'}]},
    'god-of-war': {'steamAppIDs': [1593500], 'steamFolders': ['godofwar', 'god of war'], 'programs': [{'name': 'gow.exe'}]},
    'horizon-zero-dawn': {'steamAppIDs': [1151640], 'steamFolders': ['horizon zero dawn'], 'programs': [{'name': 'horizonzerodawn.exe'}]},
    'horizon-forbidden-west': {'steamAppIDs': [2420110], 'steamFolders': ['horizon forbidden west complete edition'],
                               'programs': [{'name': 'horizonforbiddenwest.exe'}]},
    'ratchet-clank-rift-apart': {'steamAppIDs': [1895880], 'steamFolders': ['ratchet & clank - rift apart'],
                                 'programs': [{'name': 'riftapart.exe'}]},
    'crysis-remastered': {'steamAppIDs': [1715130], 'steamFolders': ['crysis remastered'], 'programs': [{'name': 'crysisremastered.exe'}]},
    'crysis-2-remastered': {'steamAppIDs': [2096600], 'steamFolders': ['crysis 2 remastered'],
                            'programs': [{'name': 'crysis2remastered.exe'}]},
    'crysis-3-remastered': {'steamAppIDs': [2096610], 'steamFolders': ['crysis 3 remastered'],
                            'programs': [{'name': 'crysis3remastered.exe'}]},
    'msfs-2024': {'steamAppIDs': [2537590], 'steamFolders': ['msfs2024'], 'programs': [{'name': 'flightsimulator2024.exe'}]},
    'counter-strike-2': {'steamAppIDs': [730], 'steamFolders': ['counter-strike global offensive'], 'programs': [{'name': 'cs2.exe'}]},
    'warcraft-2-bne': {'programs': [{'name': 'warcraft ii bne.exe'}]},
    'breakneck': {'steamAppIDs': [432940], 'steamFolders': ['breakneck'], 'programs': [{'name': 'breakneck.exe'}]},
    'spelunky-2': {'steamAppIDs': [418530], 'steamFolders': ['spelunky 2'], 'programs': [{'name': 'spel2.exe'}]},
    'albion-online': {'steamAppIDs': [761890], 'steamFolders': ['albion online'], 'programs': [{'name': 'albionlauncher.exe'}]},
    'megabonk': {'steamAppIDs': [3405340], 'steamFolders': ['megabonk'], 'programs': [{'name': 'megabonk.exe'}]},
    'no-im-not-a-human': {'steamAppIDs': [3180070], 'steamFolders': ["no, i'm not a human"], 'programs': [{'name': 'noimnotahuman.exe'}]},
    'over-the-hill': {'steamAppIDs': [2929250], 'steamFolders': ['over the hill'], 'programs': [{'name': 'over the hill.exe'}]},
    'fort-solis': {'steamAppIDs': [1931730], 'steamFolders': ['fort solis'], 'programs': [{'name': 'fortsolis.exe'}]},
    'dragon-ball-sparking-zero': {'steamAppIDs': [1790600], 'steamFolders': ['dragon ball sparking! zero'], 'programs': [{'name': 'sparkingzero-win64-shipping.exe'}]},
    'animal-well': {'steamAppIDs': [813230], 'steamFolders': ['animal well'], 'programs': [{'name': 'animal well.exe'}]},
    'hogwarts-legacy': {'steamAppIDs': [990080], 'steamFolders': ['hogwarts legacy'], 'programs': [{'name': 'hogwartslegacy.exe'}]},
    'metal-gear-solid-v': {'steamAppIDs': [287700], 'steamFolders': ['mgs_tpp'], 'programs': [{'name': 'mgsvtpp.exe'}]},
    'stellar-blade-demo': {'steamAppIDs': [3564860], 'steamFolders': ['stellarbladedemo'], 'programs': [{'name': 'sb-win64-shipping.exe'}]},
    'star-wars-galactic-racer': {'steamAppIDs': [4078430], 'steamFolders': ['star wars galactic racer'], 'programs': [{'name': 'swgr-win64-shipping.exe'}]},
    'denshattack': {'steamAppIDs': [2524850], 'steamFolders': ['denshattack'], 'programs': [{'name': 'denshattack_proto.exe'}]},
    'final-fantasy-resonance-demo': {'steamAppIDs': [4474710], 'steamFolders': ['final fantasy resonance demo'], 'programs': [{'name': 'ffrs.exe'}]},
    'no-rest-for-the-wicked': {'steamAppIDs': [1371980], 'steamFolders': ['norestforthewicked'], 'programs': [{'name': 'norestforthewicked.exe'}]},
    'quaver': {'steamAppIDs': [980610], 'steamFolders': ['quaver'], 'programs': [{'name': 'quaver.exe'}]},
    'dying-light': {'steamAppIDs': [239140], 'steamFolders': ['dying light'], 'programs': [{'name': 'dyinglightgame.exe'}]},
    'beamng-drive': {'steamAppIDs': [284160], 'steamFolders': ['beamng.drive'], 'programs': [{'name': 'beamng.drive.exe'}]},
    'monster-hunter-stories-3-trial': {'steamAppIDs': [3470630], 'steamFolders': ['monster_hunter_stories_3_twisted_reflection_trial'], 'programs': [{'name': 'monster_hunter_stories_3_twisted_reflection_trial.exe'}]},
    'pwaat': {'steamAppIDs': [787480], 'steamFolders': ['phoenix wright ace attorney trilogy'], 'programs': [{'name': 'pwaat.exe'}]},
    'lego-party': {'steamAppIDs': [1969370], 'steamFolders': ['lego party'], 'programs': [{'name': 'lego party.exe'}]},
    '1000xresist': {'steamAppIDs': [1675830], 'steamFolders': ['1000xresist'], 'programs': [{'name': '1000xresist.exe'}]},
    'american-truck-simulator': {'steamAppIDs': [270880], 'steamFolders': ['american truck simulator'], 'programs': [{'name': 'amtrucks.exe'}]},
    'marvels-spider-man-miles-morales': {'steamAppIDs': [1817190], 'steamFolders': ["marvel's spider-man miles morales"], 'programs': [{'name': 'milesmorales.exe'}]},
    'diablo-ii-resurrected': {'steamAppIDs': [2536520], 'steamFolders': ['diablo ii resurrected'], 'programs': [{'name': 'd2r.exe'}]},
    'clair-obscur-expedition-33': {'steamAppIDs': [1903340], 'steamFolders': ['expedition 33'], 'programs': [{'name': 'expedition33_steam.exe'}, {'name': 'sandfall-win64-shipping.exe'}]},
    'gta4-steam': {'steamAppIDs': [12210], 'steamFolders': ['grand theft auto iv'], 'programs': [{'name': 'playgtaiv.exe', 'steam': True}, {'name': 'gtaiv.exe', 'steam': True}]},
    'gtasa-de-steam': {'steamAppIDs': [1547000], 'steamFolders': ['gta san andreas - the definitive edition'], 'programs': [{'name': 'playgtasanandreas.exe', 'steam': True}]},
    'unrailed-2-back-on-track': {'steamAppIDs': [2211170], 'steamFolders': ['unrailed! 2 back on track']},
    'resident-evil-2': {'steamAppIDs': [883710], 'steamFolders': ['resident evil 2  biohazard re2'], 'programs': [{'name': 're2.exe'}]},
    'resident-evil-7': {'steamAppIDs': [418370], 'steamFolders': ['resident evil 7 biohazard'], 'programs': [{'name': 're7.exe'}]},
    'blazblue-entropy-effect': {'steamAppIDs': [2273430], 'steamFolders': ['blazblueentropyeffect'], 'programs': [{'name': 'blazblueentropyeffect.exe', 'steam': True}]},
    'dark-souls-iii': {'steamAppIDs': [374320], 'steamFolders': ['dark souls iii'], 'programs': [{'name': 'darksoulsiii.exe', 'steam': True}]},
    'marvels-spider-man-remastered': {'steamAppIDs': [1817070], 'steamFolders': ["marvel's spider-man remastered"], 'programs': [{'name': 'spider-man.exe', 'steam': True}]},
    'raidou-remastered': {'steamAppIDs': [2288350], 'steamFolders': ['raidou tmotsa'], 'programs': [{'name': 'raidou tmotsa.exe', 'steam': True}]},
    'yakuza-like-a-dragon': {'steamAppIDs': [1235140], 'steamFolders': ['yakuza like a dragon'], 'programs': [{'name': 'yakuzalikeadragon.exe', 'steam': True}]},
    'batman-arkham-city-goty': {'steamAppIDs': [200260], 'steamFolders': ['batman arkham city goty'], 'programs': [{'name': 'batmanac.exe', 'steam': True}]},
    'metal-gear-solid-peace-walker': {'steamAppIDs': [2492660], 'steamFolders': ['mgs_pw']},
    'lonely-mountains-snow-riders': {'steamAppIDs': [2545360], 'steamFolders': ['lmsr'], 'programs': [{'name': 'lmsr_windows64_steam.exe', 'steam': True}]},
    'trails-in-the-sky-1st-chapter': {'steamAppIDs': [3375780], 'steamFolders': ['trails in the sky 1st chapter'], 'programs': [{'name': 'sora_1st.exe', 'steam': True}]},
    'subnautica-2': {'steamAppIDs': [1962700], 'steamFolders': ['subnautica2'], 'programs': [{'name': 'subnautica2-win64-shipping.exe', 'steam': True}]},
    'rdr1-steam': {'steamAppIDs': [2668510], 'steamFolders': ['red dead redemption'], 'programs': [{'name': 'playrdr.exe', 'steam': True}]},
    'resident-evil-requiem': {'steamAppIDs': [3764200], 'steamFolders': ['resident evil requiem biohazard requiem'], 'programs': [{'name': 're9.exe', 'steam': True}]},
    'life-is-strange-remastered': {'steamAppIDs': [1265920], 'steamFolders': ['lis - remastered'], 'programs': [{'name': 'lis-win64-shipping.exe', 'steam': True}]},
    'life-is-strange-2': {'steamAppIDs': [532210], 'steamFolders': ['life is strange 2'], 'programs': [{'name': 'lis2-win64-shipping.exe', 'steam': True}]},
    'project-zomboid': {'steamAppIDs': [108600], 'steamFolders': ['projectzomboid'], 'programs': [{'name': 'projectzomboid64.exe', 'steam': True}]},
    'spiderheck': {'steamAppIDs': [1329500], 'steamFolders': ['spiderheck'], 'programs': [{'name': 'spiderheckapp.exe', 'steam': True}]},
    'rounds': {'steamAppIDs': [1557740], 'steamFolders': ['rounds'], 'programs': [{'name': 'rounds.exe', 'steam': True}]},
    'plateup': {'steamAppIDs': [1599600], 'steamFolders': ['plateup'], 'programs': [{'name': 'plateup.exe', 'steam': True}]},
    'final-fantasy-tactics-ivalice-chronicles': {'steamAppIDs': [1004640], 'steamFolders': ['final fantasy tactics - the ivalice chronicles'], 'programs': [{'name': 'fft_enhanced.exe', 'steam': True}]},
    'age-of-empires-ii-hd': {'steamAppIDs': [221380], 'steamFolders': ['age2hd']},
    'fallout-new-vegas': {'steamAppIDs': [22380], 'steamFolders': ['fallout new vegas'], 'programs': [{'name': 'falloutnv.exe', 'steam': True}, {'name': 'falloutnvlauncher.exe', 'steam': True}]},
    'digimon-story-cyber-sleuth': {'steamAppIDs': [1042550], 'steamFolders': ['digimon story cyber sleuth complete edition'], 'programs': [{'name': 'digimon story cs.exe', 'steam': True}]},
    'final-fantasy-xv-demo': {'steamAppIDs': [671210], 'steamFolders': ['final fantasy xv playable demo'], 'programs': [{'name': 'ffxv_s.exe', 'steam': True}]},
    'borderlands-3': {'steamAppIDs': [397540], 'steamFolders': ['borderlands 3'], 'programs': [{'name': 'borderlands3.exe', 'steam': True}]},
    'zenless-zone-zero': {'steamAppIDs': [4162040], 'steamFolders': ['zenless zone zero'], 'programs': [{'name': 'hyp.exe', 'steam': True}]},
    'jetboard-joust': {'steamAppIDs': [1181600], 'steamFolders': ['jetboardjoust'], 'programs': [{'name': 'jetboardjoust.exe', 'steam': True}]},
    'sword-and-fairy-3': {'programs': [{'name': 'pal3.exe'}]},
    'dead-as-disco': {'steamAppIDs': [3404260], 'programs': [{'name': 'pagoda.exe'}]},
    'tung': {'steamFolders': ['tung'], 'programs': [{'name': 'tung.exe'}]},
}
for ident, p in presets.items():
    assert p['match'] == MATCH.get(ident), (ident, p['match'])
assert presets['standard']['match'] is None and list(MATCH) == list(presets)[:-1]
# The Swift that applies a match (GameRecommendationList.match, GameRecommendations.swift).
match = block(lists_src, '    func match(windowsPath: String, steamAppID: Int?, fileExists: (String) -> Bool) -> GameRecommendation? {\n        let path',
              '\n    }\n')
assert 'guard last.hasSuffix(".exe") else {' in match and 'let installFolder = last == steamFolder ? steamFolder : nil' in match
assert 'if let steamAppID, match.steamAppIDs.contains(steamAppID) { return true }' in match
assert 'if let installFolder, match.steamFolders.contains(installFolder) { return true }' in match
assert 'let inSteam = steamAppID != nil || steamFolder != nil' in match
assert 'for program in match.programs where program.name == last {' in match
assert 'if let steam = program.steam, steam != inSteam { continue }' in match
assert 'if let beside = program.beside, !fileExists(folder + "\\\\" + beside) { continue }' in match
assert 'if let notBeside = program.notBeside, fileExists(folder + "\\\\" + notBeside) { continue }' in match
assert match.index('return rules.first { rule in') < match.index('for rule in rules {'), 'Steam folders before programs'

# Where the app applies them.
save = block(library, '    func save(_ entry: LibraryEntry) {', '\n    }\n')
assert save.index('let isNew = !next.contains { $0.id == entry.id || (entry.steamAppID != nil && $0.steamAppID == entry.steamAppID) }') \
    < save.index('GameRecommendations.prepare(&entry, new: isNew)') < save.index('persist(next)')
init = block(library, '    private init() {', '\n    }\n')
assert init.index('resetPhoneResolution()') < init.index('applyRecommendations()')
apply_all = block(library, '    private func applyRecommendations() {', '\n    }\n')
assert 'guard !readOnly' in apply_all and 'GameRecommendations.prepare(&next[i])' in apply_all and 'persist(next)' in apply_all
# Wave 3 (2026-10-19): the classic Game details page (its first sight, Reset to Recommended and Remove), the classic
# library's Add by hand and the earlier home screen's launch and settings sheet are deleted. First sight is decided where
# the engine decides it (LibraryModel.save, the start-up pass and ContentView.launchLibraryEntry, checked here), and BCD's
# game page shows the list and offers Reset to Recommended through the same calls.
assert 'struct LibraryDetail' not in library and 'GameSettingsSheet' not in library + content
bcd_page = (app / 'BCDGamePageLive.swift').read_text()
assert 'change(game) { entry in _ = GameRecommendations.reset(&entry) }' in bcd_page
assert 'if let rec = GameRecommendations.recommendation(for: entry) {' in bcd_page
launch = block(content, '    private func launchLibraryEntry(_ entry: LibraryEntry) {', '\n    }\n')
assert launch.index('if GameRecommendations.prepare(&entry, new: isNew) { library.save(entry) }') < launch.index('BCDLaunch.applyLibrary(entry, sessionLog: false)')
for name in ('GameRecommendations.swift', 'GameRecommendationsApply.swift'):
    assert project.count(f'/* {name} in Sources */') == 2, name   # PBXBuildFile and the Sources phase
    assert project.count(f'/* {name} */') == 3, name              # fileRef, PBXFileReference and the group
# The rules: first sight only for a game with nothing of its own; switches only on first sight or a reset.
due = block(apply_src, '    private static func due(', '\n    }\n')
assert due.index('guard mark != "", let rec = recommendation(') < due.index('let text = GameProfile(windowsPath: windowsPath).text')
assert 'guard mark != stamp(rec), mark.hasSuffix("#" + fileHash(text)) else { return nil }' in due
assert '!(text + (ownLines ?? "")).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty' in due and 'setMark("", windowsPath)' in due
# The standard list for every game but the Desktop, and later a game's own list over it as on first sight.
fallback = block(apply_src, '    static func recommendation(windowsPath: String, steamAppID: Int?) -> GameRecommendation? {', '\n    }\n')
assert 'if let rec = match(windowsPath: windowsPath, steamAppID: steamAppID, fileExists: driveFileExists) { return rec }' in fallback
assert 'return parts.count > 1 ? standard : nil' in fallback
assert 'let fromStandard = mark.hasPrefix(standardID + "#") && rec.id != standardID' in due
assert 'static let standardID = "standard"' in lists_src
assert 'entry.desktop == true ? nil : recommendation(windowsPath: entry.windowsPath, steamAppID: entry.steamAppID)' in apply_src
put = block(apply_src, '    private static func put(', '\n    }\n')
assert 'guard file.text == rec.fileText' in put and 'if kind != .updated {' in put
assert 'LibraryPrefs.setSafeSync(!on, for: windowsPath)' in put and 'LibraryPrefs.setScreen(size, for: windowsPath)' in put
home_lines = block(apply_src, '    private static func libraryLines(for windowsPath: String) -> String {', '\n    }\n')
assert 'if entry.steamAppID != nil, key.hasPrefix(path + "\\\\")' in home_lines and 'GameProfile(windowsPath: entry.windowsPath).text' in home_lines
assert 'takeEntryFields(rec, &entry)\n            LibraryModel.shared.save(entry)' in apply_src
print('PASS: first sight in the library (start-up, save, start); BCD\'s game page resets to the recommended list')

# The Swift fixture: the real files with small stand-ins for the app types they use.
stable_hash = block(files, '    static func stableHash(_ s: String) -> UInt64 {', '\n    }\n')
profile_text = block(profiles, '    var text: String {', '\n    }\n')
profile_parse = block(profiles, '    static func parse(_ text: String) -> [String: String] {', '\n    }\n')
stubs = r'''import Foundation

enum CoverStore {
''' + stable_hash + r'''}

enum DisplayMode: String { case fit, fill, stretch, aspect }

enum ResolutionChoices { static var screen720Value: String? { "1568x720" } }   // a 19.5:9 iPhone

final class LibraryModel {
    static let shared = LibraryModel()
    var entries: [LibraryEntry] = []
    func save(_ entry: LibraryEntry) {
        if let i = entries.firstIndex(where: { $0.windowsPath == entry.windowsPath }) { entries[i] = entry } else { entries.append(entry) }
    }
    static var drive: URL { URL(fileURLWithPath: ProcessInfo.processInfo.environment["MADEIRA_TEST_DRIVE"]!, isDirectory: true) }
}

final class LogStore {
    static let shared = LogStore()
    enum Level { case info, error }
    var lines: [String] = []
    func log(_ message: String, level: Level = .info) { lines.append(message) }
}

struct ConfigOption {
    enum Kind { case bool, int, text, choice }
    let key: String
    let category: String
    init(key: String, title: String, kind: Kind, defaultValue: String, category: String, note: String,
         choices: [(String, String)], sources: [String]) {
        self.key = key
        self.category = category
    }
}

enum ConfigCatalog {}

struct LibraryEntry {
    var title: String
    var windowsPath: String
    var steamAppID: Int? = nil
    var desktop: Bool? = nil
    var config: String? = nil
    var resolution = "1408x648"
    var display: String? = nil
    var fpsMode = 1
    var fastSync: Bool? = nil
    var semaphoreFastPath: Bool? = nil
}

final class PrefStore {
    static let shared = PrefStore()
    var flags: [String: Bool] = [:]
    var screens: [String: String] = [:]
}

enum LibraryPrefs {
    static let screenSizes = ["", "1280x720", "fill", "fill-mfx15", "1600x900", "1920x1080"]
    static func avx(_ path: String) -> Bool { PrefStore.shared.flags["avx|" + path] ?? false }
    static func setAVX(_ on: Bool, for path: String) { PrefStore.shared.flags["avx|" + path] = on ? true : nil }
    static func nvidia(_ path: String) -> Bool { PrefStore.shared.flags["nvidia|" + path] ?? false }
    static func setNvidia(_ on: Bool, for path: String) { PrefStore.shared.flags["nvidia|" + path] = on ? true : nil }
    static func wineVCRT(_ path: String) -> Bool { PrefStore.shared.flags["vcrt|" + path] ?? false }
    static func setWineVCRT(_ on: Bool, for path: String) { PrefStore.shared.flags["vcrt|" + path] = on ? true : nil }
    static func safeSync(_ path: String) -> Bool { PrefStore.shared.flags["safe|" + path] ?? false }
    static func setSafeSync(_ on: Bool, for path: String) { PrefStore.shared.flags["safe|" + path] = on ? true : nil }
    static func screen(_ path: String) -> String { PrefStore.shared.screens[path] ?? "" }
    static func setScreen(_ size: String, for path: String) { PrefStore.shared.screens[path] = size.isEmpty ? nil : size }
}

struct GameProfile {
    let windowsPath: String
    var url: URL? {
        URL(fileURLWithPath: ProcessInfo.processInfo.environment["MADEIRA_TEST_CONFIGS"]!, isDirectory: true)
            .appendingPathComponent(String(format: "%016llx.cfg", CoverStore.stableHash(windowsPath.lowercased())))
    }
''' + profile_text + profile_parse + '}\n'

main = r'''import Foundation

func check(_ ok: Bool, _ what: String, line: Int = #line) {
    if !ok { print("FAILED (main.swift line \(line)): \(what)"); exit(1) }
}

let R = GameRecommendations.self
let steam = #"C:\Program Files (x86)\Steam\steamapps\common"#

// The lists: the repository's recommended/recommendations.json, the app's bundle resource, read as
// the app reads it (RecommendationsStore.initialList); no download is kept here.
RecommendationsStore.builtInURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["BCD_TEST_RECOMMENDATIONS"]!)
RecommendationsStore.directory = URL(fileURLWithPath: ProcessInfo.processInfo.environment["BCD_TEST_RECS_DIR"]!, isDirectory: true)
RecommendationsStore.build = 600
check(R.active.origin == .builtIn && R.all.count == @RULE_COUNT@ && R.standard?.id == "standard", "the built-in list")

extension GameRecommendations {
    static func rule(_ id: String) -> GameRecommendation { all.first { $0.id == id }! }
    static var rdr2Steam: GameRecommendation { rule("rdr2-steam") }
    static var gta5EnhancedSteam: GameRecommendation { rule("gta5e-steam") }
    static var gta5EnhancedOther: GameRecommendation { rule("gta5e-other") }
    static var gta5LegacySteam: GameRecommendation { rule("gta5l-steam") }
    static var ghostOfTsushima: GameRecommendation { rule("ghost-of-tsushima") }
    static var godOfWar: GameRecommendation { rule("god-of-war") }
}

func id(_ path: String, _ app: Int? = nil, files: [String] = []) -> String {
    let have = Set(files.map { $0.lowercased() })
    return R.match(windowsPath: path, steamAppID: app, fileExists: { have.contains($0.lowercased()) })?.id ?? "none"
}

// How a game is recognised.
let rdr2 = steam + #"\Red Dead Redemption 2"#
check(id(rdr2, 1174180) == "rdr2-steam", "RDR2 by its App ID")
check(id(rdr2) == "rdr2-steam", "RDR2 by its Steam folder")
check(id("D:\\SteamLibrary\\steamapps\\common\\Red Dead Redemption 2\\") == "rdr2-steam", "another Steam library, trailing backslash")
check(id(#"C:\Games\Red Dead Redemption 2\RDR2.exe"#) == "rdr2-other" && id(#"C:\Games\Red Dead Redemption 2\PlayRDR2.exe"#) == "rdr2-other",
      "RDR2 outside Steam: the other copy's list")
check(id(rdr2 + #"\RDR2.exe"#) == "rdr2-steam" && id(rdr2 + #"\PlayRDR2.exe"#) == "rdr2-steam", "RDR2's programs inside Steam")
check(id(rdr2 + #"\Redistributables\vc_redist.x64.exe"#) == "none", "a redistributable in a game's Steam folder")
check(id(rdr2 + #"\start.bat"#) == "none" && id(steam + #"\Grand Theft Auto V Enhanced\launch.cmd"#) == "none",
      "a batch file inside a Steam game's folder")
let gta = steam + #"\Grand Theft Auto V Enhanced"#
check(id(gta, 3240220) == "gta5e-steam", "GTA V Enhanced by its App ID")
check(id(gta) == "gta5e-steam", "GTA V Enhanced by its Steam folder")
check(id(gta + #"\PlayGTAV.exe"#, files: [gta + #"\GTA5_Enhanced.exe"#]) == "gta5e-steam", "PlayGTAV.exe inside Steam is the Steam copy")
let renamed = steam + #"\GTAV Enhanced Copy"#
check(id(renamed + #"\PlayGTAV.exe"#, files: [renamed + #"\GTA5_Enhanced.exe"#]) == "gta5e-steam", "in any Steam library folder")
check(id(gta + #"\_CommonRedist\vc_redist.x64.exe"#, files: [gta + #"\GTA5_Enhanced.exe"#]) == "none", "not its redistributables")
let other = #"C:\Games\GTA V Enhanced"#
check(id(other + #"\PlayGTAV.exe"#, files: [other + #"\GTA5_Enhanced.exe"#]) == "gta5e-other", "PlayGTAV.exe elsewhere")
check(id(other + #"\GTA5_Enhanced.exe"#, files: [other + #"\GTA5_Enhanced.exe"#]) == "gta5e-other", "GTA5_Enhanced.exe elsewhere")
check(id(#"C:\GAMES\gta v enhanced\PLAYGTAV.EXE"#, files: [other + #"\gta5_enhanced.exe"#]) == "gta5e-other", "names in any case")
check(id("C:/Games/GTA V Enhanced/PlayGTAV.exe", files: [other + #"\GTA5_Enhanced.exe"#]) == "gta5e-other", "forward slashes")
check(id(#"C:\Games\GTA V\PlayGTAV.exe"#, files: [#"C:\Games\GTA V\GTA5.exe"#]) == "none", "the legacy edition outside Steam has no list")
check(id(#"C:\Games\GTA V\GTA5.exe"#) == "none", "GTA5.exe outside Steam has no list")
let legacy = steam + #"\Grand Theft Auto V"#
check(id(legacy, 271590) == "gta5l-steam", "GTA V Legacy by its App ID")
check(id(legacy) == "gta5l-steam", "GTA V Legacy by its Steam folder")
check(id(legacy + #"\PlayGTAV.exe"#, files: [legacy + #"\GTA5.exe"#]) == "gta5l-steam", "Legacy's PlayGTAV.exe inside Steam")
check(id(legacy + #"\GTA5.exe"#) == "gta5l-steam", "Legacy's GTA5.exe inside Steam")
check(id(legacy + #"\PlayGTAV.exe"#) == "none", "PlayGTAV.exe inside Steam with neither game program beside it")
check(id(gta + #"\PlayGTAV.exe"#, files: [gta + #"\GTA5_Enhanced.exe"#, gta + #"\GTA5.exe"#]) == "gta5e-steam",
      "GTA5_Enhanced.exe beside PlayGTAV.exe stays Enhanced, even with a GTA5.exe there")
check(id(legacy + #"\_CommonRedist\vc_redist.x64.exe"#, 271590) == "none", "not Legacy's redistributables")
// The device's RAM: GTA V Legacy's 3 GB RAM swap only under 12 GB; every other list the same everywhere.
R.deviceRAMBytes = 11_695 << 20
let onTwelve = R.all.map { $0.fileText }
check(!R.gta5LegacySteam.fileText.contains("swap-mb") && !R.gta5LegacySteam.fileText.contains("vram-mb"),
      "a 12 GB phone: no RAM swap in GTA V Legacy's list")
R.deviceRAMBytes = 16 << 30
check(R.all.map { $0.fileText } == onTwelve, "a 16 GB iPad: the same lists as a 12 GB phone")
R.deviceRAMBytes = 7_500 << 20
check(R.gta5LegacySteam.fileText.hasSuffix("env.MADEIRA_METAL_HUD_MAIN = 1\nswap-mb = 3072\n"),
      "an 8 GB phone: the 3 GB RAM swap after GTA V Legacy's list")
check(zip(R.all, onTwelve).allSatisfy { $0.0.id == "gta5l-steam" || $0.0.fileText == $0.1 },
      "every other list is the same on an 8 GB phone")
R.deviceRAMBytes = 11_695 << 20
check(id(other + #"\PlayGTAV.exe"#) == "none", "PlayGTAV.exe without GTA5_Enhanced.exe beside it")
check(id(#"C:\GhostOfTsushima\GhostOfTsushima.exe"#) == "ghost-of-tsushima", "GoT by its program")
check(id(#"E:\Some Other Folder\ghostoftsushima.EXE"#) == "ghost-of-tsushima", "GoT anywhere, in any case")
check(id(steam + #"\Ghost of Tsushima DIRECTOR'S CUT"#, 2215430) == "ghost-of-tsushima", "GoT from Steam")
check(id(steam + #"\Ghost of Tsushima DIRECTOR'S CUT"#) == "ghost-of-tsushima", "GoT by its Steam folder")
check(id(steam + #"\GodOfWar"#) == "god-of-war" && id("D:\\SteamLibrary\\steamapps\\common\\God of War") == "god-of-war",
      "GoW by its Steam folder")
check(id(steam + #"\Horizon Zero Dawn"#) == "horizon-zero-dawn", "Horizon Zero Dawn by its Steam folder")
check(id(steam + #"\Horizon Zero Dawn Remastered"#, 2561580) != "horizon-zero-dawn", "the remaster is not the original: the original's lines never reach it")
check(id(steam + #"\Ghost"#, 1, files: [steam + #"\Ghost\GhostOfTsushima.exe"#]) == "none", "a library Steam game is its App ID or folder name")
check(id(#"C:\God of War\GoW.exe"#) == "god-of-war", "GoW by its program")
check(id(steam + #"\God of War"#, 1593500) == "god-of-war", "GoW from Steam")
check(id(#"C:\Games\God of War Ragnarok\GoWR.exe"#) == "none", "Ragnarok is another game")
check(id(#"C:\Horizon - Zero Down CE\HorizonZeroDawn.exe"#) == "horizon-zero-dawn", "Horizon Zero Dawn by its program (GOG)")
check(id(steam + #"\Horizon Zero Dawn\HorizonZeroDawn.exe"#) == "horizon-zero-dawn", "its program inside Steam")
check(id(steam + #"\Horizon Zero Dawn"#, 1151640) == "horizon-zero-dawn", "Horizon Zero Dawn from Steam")
check(id(#"C:\Games\Horizon Zero Dawn Remastered\HorizonZeroDawnRemastered.exe"#) == "none", "the remaster is another game")
let hfw = steam + #"\Horizon Forbidden West Complete Edition"#
check(id(hfw, 2420110) == "horizon-forbidden-west", "Horizon Forbidden West by its App ID")
check(id(hfw) == "horizon-forbidden-west", "Horizon Forbidden West by its Steam folder")
check(id(hfw + #"\HorizonForbiddenWest.exe"#) == "horizon-forbidden-west", "its program inside Steam")
check(id(#"D:\Games\HFW\horizonforbiddenwest.EXE"#) == "horizon-forbidden-west", "its program anywhere, in any case")
check(id(hfw + #"\crs-handler.exe"#) == "none", "not its crash reporter")
let crysis = steam + #"\Crysis Remastered"#
check(id(crysis, 1715130) == "crysis-remastered", "Crysis Remastered by its App ID")
check(id(crysis) == "crysis-remastered", "Crysis Remastered by its Steam folder")
check(id(crysis + #"\Bin64\CrysisRemastered.exe"#) == "crysis-remastered", "its program inside Steam")
check(id(#"C:\Games\Crysis\Bin64\Crysis.exe"#) == "none", "the original Crysis is another game")
let crysis2 = steam + #"\Crysis 2 Remastered"#, crysis3 = steam + #"\Crysis 3 Remastered"#
check(id(crysis2, 2096600) == "crysis-2-remastered" && id(crysis3, 2096610) == "crysis-3-remastered", "Crysis 2 and 3 Remastered by their App IDs")
check(id(crysis2) == "crysis-2-remastered" && id(crysis3) == "crysis-3-remastered", "by their Steam folders")
check(id(crysis2 + #"\Bin64\Crysis2Remastered.exe"#) == "crysis-2-remastered"
      && id(crysis3 + #"\Bin64\Crysis3Remastered.exe"#) == "crysis-3-remastered", "their programs inside Steam")
check(id(#"C:\Games\Crysis 2\Bin32\Crysis2.exe"#) == "none", "the original Crysis 2 is another game")
let rca = steam + #"\Ratchet & Clank - Rift Apart"#
check(id(rca, 1895880) == "ratchet-clank-rift-apart", "Rift Apart by its App ID")
check(id(rca) == "ratchet-clank-rift-apart", "Rift Apart by its Steam folder")
check(id(rca + #"\RiftApart.exe"#) == "ratchet-clank-rift-apart", "its program inside Steam")
check(id(#"D:\Games\RC\RIFTAPART.exe"#) == "ratchet-clank-rift-apart", "its program anywhere, in any case")
let msfs = steam + #"\MSFS2024"#
check(id(msfs, 2537590) == "msfs-2024", "MSFS 2024 by its App ID")
check(id(msfs) == "msfs-2024", "MSFS 2024 by its Steam folder")
check(id(msfs + #"\FlightSimulator2024.exe"#) == "msfs-2024", "its program inside Steam")
check(id(#"C:\Games\MSFS2024\FlightSimulator.exe"#) == "none", "Flight Simulator 2020's program is another game")
let milesm = steam + #"\Marvel's Spider-Man Miles Morales"#
check(id(milesm, 1817190) == "marvels-spider-man-miles-morales", "Miles Morales by its App ID")
check(id(milesm) == "marvels-spider-man-miles-morales", "Miles Morales by its Steam folder")
check(id(milesm + #"\MilesMorales.exe"#) == "marvels-spider-man-miles-morales", "its program inside Steam")
check(id(steam + #"\Marvel's Spider-Man Remastered"#, 1817070) == "marvels-spider-man-remastered", "Spider-Man Remastered has its own list (build 580), not Miles Morales'")
// Build 580's lists (tur580): each by App ID, by Steam folder and by its program inside Steam; the same program outside Steam is nobody's
// (no copy gets a list), and the original of a remaster keeps its own.
let list580: [(String, Int, String, String?)] = [
    ("BlazblueEntropyEffect", 2273430, "blazblue-entropy-effect", "BlazblueEntropyEffect.exe"),
    ("DARK SOULS III", 374320, "dark-souls-iii", #"Game\DarkSoulsIII.exe"#),
    ("Marvel's Spider-Man Remastered", 1817070, "marvels-spider-man-remastered", "Spider-Man.exe"),
    ("Raidou TMOTSA", 2288350, "raidou-remastered", "Raidou TMOTSA.exe"),
    ("Yakuza Like a Dragon", 1235140, "yakuza-like-a-dragon", #"runtime\media\YakuzaLikeADragon.exe"#),
    ("Batman Arkham City GOTY", 200260, "batman-arkham-city-goty", #"Binaries\Win32\BatmanAC.exe"#),
    ("MGS_PW", 2492660, "metal-gear-solid-peace-walker", nil),
    ("LMSR", 2545360, "lonely-mountains-snow-riders", "LMSR_Windows64_Steam.exe"),
    ("Trails in the Sky 1st Chapter", 3375780, "trails-in-the-sky-1st-chapter", "sora_1st.exe"),
    ("Subnautica2", 1962700, "subnautica-2", #"Subnautica2\Binaries\Win64\Subnautica2-Win64-Shipping.exe"#),
    ("Red Dead Redemption", 2668510, "rdr1-steam", "PlayRDR.exe"),
]
for (folder, app, rule, exe) in list580 {
    check(id(steam + "\\" + folder, app) == rule, "\(rule) by its App ID")
    check(id(steam + "\\" + folder) == rule, "\(rule) by its Steam folder")
    if let exe {
        check(id(steam + "\\" + folder + "\\" + exe) == rule, "\(rule): its program inside Steam")
        check(id(#"D:\Games\Copy\"# + (exe.split(separator: "\\").last.map(String.init) ?? exe)) == "none", "\(rule): the same program outside Steam gets no list")
    }
}
// Build 591's lists (tur591): the same recognition, for the games that fail today.
let list591: [(String, Int, String, String?)] = [
    ("RESIDENT EVIL requiem BIOHAZARD requiem", 3764200, "resident-evil-requiem", "re9.exe"),
    ("LIS - Remastered", 1265920, "life-is-strange-remastered", #"LIS\Binaries\Win64\LiS-Win64-Shipping.exe"#),
    ("Life is Strange 2", 532210, "life-is-strange-2", #"LIS2\Binaries\Win64\LIS2-Win64-Shipping.exe"#),
    ("ProjectZomboid", 108600, "project-zomboid", "ProjectZomboid64.exe"),
    ("SpiderHeck", 1329500, "spiderheck", "SpiderHeckApp.exe"),
    ("ROUNDS", 1557740, "rounds", "ROUNDS.exe"),
    ("PlateUp", 1599600, "plateup", #"PlateUp\PlateUp.exe"#),
    ("FINAL FANTASY TACTICS - The Ivalice Chronicles", 1004640, "final-fantasy-tactics-ivalice-chronicles", "FFT_enhanced.exe"),
    ("Age2HD", 221380, "age-of-empires-ii-hd", nil),
    ("Fallout New Vegas", 22380, "fallout-new-vegas", "FalloutNVLauncher.exe"),
    ("Digimon Story Cyber Sleuth Complete Edition", 1042550, "digimon-story-cyber-sleuth", #"app_digister\Digimon Story CS.exe"#),
    ("FINAL FANTASY XV PLAYABLE DEMO", 671210, "final-fantasy-xv-demo", "ffxv_s.exe"),
    ("Borderlands 3", 397540, "borderlands-3", #"OakGame\Binaries\Win64\Borderlands3.exe"#),
    ("Zenless Zone Zero", 4162040, "zenless-zone-zero", "HYP.exe"),
    ("JetboardJoust", 1181600, "jetboard-joust", "JetboardJoust.exe"),
]
for (folder, app, rule, exe) in list591 {
    check(id(steam + "\\" + folder, app) == rule, "\(rule) by its App ID")
    check(id(steam + "\\" + folder) == rule, "\(rule) by its Steam folder")
    if let exe {
        check(id(steam + "\\" + folder + "\\" + exe) == rule, "\(rule): its program inside Steam")
        check(id(#"D:\Games\Copy\"# + (exe.split(separator: "\\").last.map(String.init) ?? exe)) == "none", "\(rule): the same program outside Steam gets no list")
    }
}
check(id(steam + #"\Age2HD\Launcher.exe"#) == "none", "Launcher.exe is too generic a name: the Age of Empires II HD rule has no program")
check(id(#"C:\Sword and Fairy 3\PAL3.exe"#) == "sword-and-fairy-3" && id(#"D:\Games\PAL3\PAL3.exe"#) == "sword-and-fairy-3", "Sword and Fairy 3 by its program's name, wherever it is (no App ID: a local game)")
check(id(steam + #"\Fallout 3 goty"#) == "none" && id(steam + #"\Fallout 4"#) == "none", "Fallout: New Vegas' list is not Fallout 3's or 4's")
check(id(steam + #"\Red Dead Redemption 2"#, 1174180) == "rdr2-steam" && id(steam + #"\Red Dead Redemption 2"#) == "rdr2-steam", "RDR1's list does not take RDR2's folder or App ID")
check(id(steam + #"\MGS_PW\launcher\launcher.exe"#) == "none", "a program called launcher.exe inside Steam is nobody's (the rule has no program name)")
let gta4s = steam + #"\Grand Theft Auto IV"#
check(id(gta4s, 12210) == "gta4-steam", "GTA IV: The Complete Edition by its App ID")
check(id(gta4s) == "gta4-steam", "GTA IV: The Complete Edition by its Steam folder")
check(id(gta4s + #"\GTAIV\PlayGTAIV.exe"#) == "gta4-steam" && id(gta4s + #"\GTAIV\GTAIV.exe"#) == "gta4-steam", "its programs inside Steam")
check(id(#"C:\Grand Theft Auto IV\GTAIV.exe"#) == "none" && id(#"C:\Games\GTAIV\PlayGTAIV.exe"#) == "none",
      "GTA IV outside a Steam library (the owner's local entry) is not matched: it needs no Launcher")
let gtasa = steam + #"\GTA San Andreas - The Definitive Edition"#
check(id(gtasa, 1547000) == "gtasa-de-steam" && id(gtasa) == "gtasa-de-steam", "GTA San Andreas DE by its App ID and its Steam folder")
check(id(gtasa + #"\PlayGTASanAndreas.exe"#) == "gtasa-de-steam" && id(#"C:\Games\SA\PlayGTASanAndreas.exe"#) == "none",
      "its program inside Steam only")
let unrailed2 = steam + #"\Unrailed! 2 Back on Track"#
check(id(unrailed2, 2211170) == "unrailed-2-back-on-track" && id(unrailed2) == "unrailed-2-back-on-track", "Unrailed! 2 by its App ID and its Steam folder")
check(id(unrailed2 + #"\data_UnrailedGodot_windows_x86_64\Bootstrap.exe"#) == "none" && id(#"C:\Games\Foo\Bootstrap.exe"#) == "none",
      "Bootstrap.exe is too generic a name to be matched on its own")
let re2 = steam + #"\RESIDENT EVIL 2  BIOHAZARD RE2"#
check(id(re2, 883710) == "resident-evil-2" && id(re2) == "resident-evil-2", "Resident Evil 2 by its App ID and its Steam folder (two spaces in the folder's name)")
check(id(re2 + #"\re2.exe"#) == "resident-evil-2" && id(#"C:\Games\RE2\re2.exe"#) == "resident-evil-2", "its program, wherever it is")
let re7 = steam + #"\RESIDENT EVIL 7 biohazard"#
check(id(re7, 418370) == "resident-evil-7" && id(re7) == "resident-evil-7" && id(re7 + #"\re7.exe"#) == "resident-evil-7", "Resident Evil 7 by its App ID, its Steam folder and its program")
let exp33 = steam + #"\Expedition 33"#
check(id(exp33, 1903340) == "clair-obscur-expedition-33", "Clair Obscur: Expedition 33 by its App ID")
check(id(exp33) == "clair-obscur-expedition-33", "Clair Obscur: Expedition 33 by its Steam folder")
check(id(exp33 + #"\Expedition33_Steam.exe"#) == "clair-obscur-expedition-33"
      && id(exp33 + #"\Sandfall\Binaries\Win64\SandFall-Win64-Shipping.exe"#) == "clair-obscur-expedition-33", "its programs inside Steam")
check(id(#"C:\Games\Foo\foo.exe"#) == "none" && id(steam + #"\Portal 2"#, 620) == "none", "other games")
check(id("") == "none" && id("C:\\") == "none", "no path")
print("PASS: Steam games by App ID or steamapps\\common folder, GTA V Enhanced and RDR2 inside and outside Steam, GoT, GoW, Horizon Zero Dawn, Horizon Forbidden West, Rift Apart, Crysis Remastered and MSFS 2024 by their programs, others none")

// The lists themselves.
check(Set(R.all.map(\.id)).count == R.all.count, "ids are unique")
for rec in R.all {
    check(rec.fileText.hasSuffix("\n") && !rec.fileText.hasSuffix("\n\n"), "\(rec.id): one trailing newline")
    check(GameProfile.parse(rec.config).count == rec.config.split(separator: "\n").count - 2, "\(rec.id): every line but the comments parses")
}
check(R.gta5EnhancedSteam.switchSummary == "AVX off, NVIDIA on, Wine's C++ runtime off, fast semaphore waits on, FPS limit 40",
      R.gta5EnhancedSteam.switchSummary)
check(R.gta5EnhancedSteam.summary(home: true) == "AVX off, NVIDIA on, Wine's C++ runtime off", R.gta5EnhancedSteam.summary(home: true))
check(R.note(R.ghostOfTsushima, home: true).contains("1280x720") && !R.note(R.ghostOfTsushima, home: true).contains("semaphore"),
      R.note(R.ghostOfTsushima, home: true))
check(R.note(R.rdr2Steam).contains("@RDR2_CONFIG_LINES@ config lines") && R.note(R.rdr2Steam).contains("this screen's shape at 720 lines") && R.note(nil).contains("no recommended setup"), R.note(R.rdr2Steam))
// Every other game: the standard list; a path that names nothing: none.
check(R.recommendation(windowsPath: #"C:\Games\Foo\foo.exe"#, steamAppID: nil)?.id == "standard"
      && R.recommendation(windowsPath: steam + #"\Portal 2"#, steamAppID: 620)?.id == "standard", "every other game: the standard list")
check(R.recommendation(windowsPath: "", steamAppID: nil) == nil && R.recommendation(windowsPath: "C:\\", steamAppID: nil) == nil,
      "a path that names nothing has none")
check(R.note(R.standard).contains("standard settings: 1 config line.") && R.note(R.standard, home: true) == R.note(R.standard)
      && R.question(R.standard).contains("standard settings"), R.note(R.standard))

// First sight, the player's changes, newer lists and Reset to Recommended.
let key = "madeira.recommended.applied"
UserDefaults.standard.removeObject(forKey: key)
func mark(_ path: String) -> String? { (UserDefaults.standard.dictionary(forKey: key) as? [String: String])?[path.lowercased()] }
func file(_ path: String) -> String { GameProfile(windowsPath: path).text }

var red = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: rdr2, steamAppID: 1174180)
red.display = "fill"
LibraryPrefs.setNvidia(true, for: rdr2)
check(R.prepare(&red), "first sight takes the entry's fields")
check(file(rdr2) == R.rdr2Steam.fileText, "first sight writes the list")
check(red.fastSync == true && red.semaphoreFastPath == false && red.display == nil && red.fpsMode == 1, "first sight's entry fields")
check(red.resolution == "1568x720", "this screen's shape at 720 lines")
check(!LibraryPrefs.avx(rdr2) && !LibraryPrefs.nvidia(rdr2) && !LibraryPrefs.wineVCRT(rdr2), "first sight's switches")
check(mark(rdr2)?.hasPrefix("rdr2-steam#\(R.rdr2Steam.version)#") == true, "what was written is recorded")
check(!R.prepare(&red) && file(rdr2) == R.rdr2Steam.fileText, "once")
GameProfile(windowsPath: rdr2).text = R.rdr2Steam.fileText + "fence-chain = 1\n"
check(!R.prepare(&red) && file(rdr2).hasSuffix("fence-chain = 1\n"), "a changed config is the player's")
GameProfile(windowsPath: rdr2).text = ""
check(!R.prepare(&red) && file(rdr2).isEmpty, "an emptied config stays empty")
red.fpsMode = 2
LibraryPrefs.setNvidia(true, for: rdr2)
check(R.reset(&red)?.id == "rdr2-steam" && file(rdr2) == R.rdr2Steam.fileText, "Reset writes the list again")
check(!LibraryPrefs.nvidia(rdr2) && red.fpsMode == 2, "Reset writes the list's switches and leaves the rest")
// A list Madeira wrote that nobody changed follows a newer version: the config file only.
let older = "# an older list\nvram-mb = 2048\n"
GameProfile(windowsPath: rdr2).text = older
var marks = (UserDefaults.standard.dictionary(forKey: key) as? [String: String]) ?? [:]
marks[rdr2.lowercased()] = "rdr2-steam#0#" + String(CoverStore.stableHash(older), radix: 16)
UserDefaults.standard.set(marks, forKey: key)
LibraryPrefs.setAVX(true, for: rdr2)
check(!R.prepare(&red) && file(rdr2) == R.rdr2Steam.fileText, "an untouched older list is updated")
check(LibraryPrefs.avx(rdr2) && !R.prepare(&red), "an update leaves the switches, once")
print("PASS: first sight writes the list and its switches once; changed or emptied configs stay; Reset writes it again; untouched lists follow newer versions")

var gtaEntry = LibraryEntry(title: "GTA V Enhanced", windowsPath: gta, steamAppID: 3240220)
GameProfile(windowsPath: gta).text = "vram-mb = 4096\n"
check(!R.prepare(&gtaEntry) && file(gta) == "vram-mb = 4096\n" && mark(gta) == "" && gtaEntry.fpsMode == 1, "a config of its own stays")
GameProfile(windowsPath: gta).text = ""
check(!R.prepare(&gtaEntry) && file(gta).isEmpty, "and is not filled in later")
check(R.reset(&gtaEntry)?.id == "gta5e-steam" && file(gta) == R.gta5EnhancedSteam.fileText, "GTA V Enhanced Reset")
check(gtaEntry.fpsMode == 4 && gtaEntry.semaphoreFastPath == true && LibraryPrefs.nvidia(gta) && !LibraryPrefs.avx(gta), "its switches")
let gowExe = #"C:\Games\God of War\GoW.exe"#
var gow = LibraryEntry(title: "God of War", windowsPath: gowExe)
gow.config = "dxmt = d3d11.mipClampBC=1"
check(!R.prepare(&gow) && file(gowExe).isEmpty && mark(gowExe) == "", "lines in upstream's config field are the game's own")
check(R.reset(&gow)?.id == "god-of-war" && gow.resolution == "1920x1080" && gow.config == nil, "God of War Reset")
check(LibraryPrefs.screen(gowExe) == "1920x1080" && !LibraryPrefs.safeSync(gowExe), "the home screen's forms of the switches too")
var desk = LibraryEntry(title: "Desktop", windowsPath: #"C:\windows\system32\explorer.exe"#)
desk.desktop = true
check(!R.prepare(&desk) && R.recommendation(for: desk) == nil, "the Desktop has none")
let fooExe = #"C:\Games\Foo\foo.exe"#
var foo = LibraryEntry(title: "Foo", windowsPath: fooExe)
GameProfile(windowsPath: fooExe).text = "fence-chain = 6\n"
check(!R.prepare(&foo) && file(fooExe) == "fence-chain = 6\n" && mark(fooExe) == "", "a game without a list keeps a config of its own")
check(R.reset(&foo)?.id == "standard" && file(fooExe) == R.standard!.fileText, "its Reset writes the standard list")
let barExe = #"C:\Games\Bar\bar.exe"#
var bar = LibraryEntry(title: "Bar", windowsPath: barExe)
check(R.prepare(&bar) && file(barExe) == R.standard!.fileText && bar.resolution == "1408x648" && bar.fpsMode == 1
      && !LibraryPrefs.avx(barExe) && mark(barExe)?.hasPrefix("standard#1#") == true,
      "first sight of a game without a list: the standard list, no switches")
// An untouched standard list gives way to the game's own list later, as on first sight: switches too.
let laterExe = #"C:\Later\GoW.exe"#
GameProfile(windowsPath: laterExe).text = R.standard!.fileText
marks = (UserDefaults.standard.dictionary(forKey: key) as? [String: String]) ?? [:]
marks[laterExe.lowercased()] = "standard#1#" + String(CoverStore.stableHash(R.standard!.fileText), radix: 16)
UserDefaults.standard.set(marks, forKey: key)
var later = LibraryEntry(title: "God of War", windowsPath: laterExe)
check(R.prepare(&later) && file(laterExe) == R.godOfWar.fileText && later.resolution == "1920x1080"
      && LibraryPrefs.screen(laterExe) == "1920x1080", "an untouched standard list gives way to the game's list, switches too")
print("PASS: own configs (the file or upstream's field) stay until Reset; Reset writes every switch of the list; no list of its own: the standard list")

// The madeira-bcd home screen: keyed by the program, its own forms of the switches, drive_c looked up.
let drive = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MADEIRA_TEST_DRIVE"]!, isDirectory: true)
let folder = drive.appendingPathComponent("Games/GTA V Enhanced", isDirectory: true)
try! FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
check(FileManager.default.createFile(atPath: folder.appendingPathComponent("gta5_enhanced.EXE").path, contents: Data()), "fixture")
let play = #"C:\Games\GTA V Enhanced\PlayGTAV.exe"#
check(R.recommendation(windowsPath: play, steamAppID: nil)?.id == "gta5e-other", "drive_c is looked up in any case")
check(R.recommendation(windowsPath: #"C:\Games\GTA V\PlayGTAV.exe"#, steamAppID: nil)?.id == "standard", "a missing game file: the standard list")
R.prepare(windowsPath: play, title: "GTA V Enhanced")
check(file(play) == R.gta5EnhancedOther.fileText && LibraryPrefs.nvidia(play) && !LibraryPrefs.safeSync(play)
      && LibraryPrefs.screen(play) == "1280x720", "the home screen's first sight")
LibraryPrefs.setScreen("", for: play)
check(R.reset(windowsPath: play, title: "GTA V Enhanced")?.id == "gta5e-other" && LibraryPrefs.screen(play) == "1280x720", "the home screen's Reset")
var playEntry = LibraryEntry(title: "GTA V Enhanced", windowsPath: play)
check(!R.prepare(&playEntry), "the library's entry for the same program sees it was written")
check(R.prepare(&playEntry, new: true) && playEntry.resolution == "1280x720" && playEntry.fastSync == true,
      "an entry new to the library takes the fields of a list written before")
var page = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: rdr2, steamAppID: 1174180)
page.display = "fill"
check(R.prepare(&page, new: true) && page.display == nil && page.fastSync == true && file(rdr2) == R.rdr2Steam.fileText,
      "a Steam game's first page made before the library stored its entry")
let gotSteam = steam + #"\Ghost of Tsushima DIRECTOR'S CUT"#
let olderGoT = "# an older list\nswap-mb = 2048\n"
GameProfile(windowsPath: gotSteam).text = olderGoT
marks = (UserDefaults.standard.dictionary(forKey: key) as? [String: String]) ?? [:]
marks[gotSteam.lowercased()] = "ghost-of-tsushima#0#" + String(CoverStore.stableHash(olderGoT), radix: 16)
UserDefaults.standard.set(marks, forKey: key)
var gotEntry = LibraryEntry(title: "Ghost of Tsushima", windowsPath: gotSteam, steamAppID: 2215430)
check(R.prepare(&gotEntry, new: true) && file(gotSteam) == R.ghostOfTsushima.fileText && gotEntry.resolution == "1280x720",
      "a new entry whose untouched older list is updated takes the list's fields too")
let ownExe = #"C:\Games\Ghost Own\GhostOfTsushima.exe"#
GameProfile(windowsPath: ownExe).text = "dxil-tess = 1\n"
var own = LibraryEntry(title: "Ghost of Tsushima", windowsPath: ownExe)
check(!R.prepare(&own, new: true) && mark(ownExe) == "" && own.resolution == "1408x648" && !LibraryPrefs.avx(ownExe),
      "a config of its own stays its own for a new entry too")
let got = #"C:\GoT\GhostOfTsushima.exe"#
R.prepare(windowsPath: got, title: "Ghost of Tsushima")
check(LibraryPrefs.avx(got) && LibraryPrefs.nvidia(got) && LibraryPrefs.screen(got) == "1280x720", "GoT on the home screen")
// Lines in the library entry's upstream config field for the same program are the game's own.
let otherGoW = #"C:\Other\GoW.exe"#
LibraryModel.shared.entries = [LibraryEntry(title: "God of War", windowsPath: otherGoW, config: "dxmt = d3d11.mipClampBC=1")]
R.prepare(windowsPath: otherGoW, title: "God of War")
check(file(otherGoW).isEmpty && mark(otherGoW) == "" && LibraryPrefs.screen(otherGoW).isEmpty, "the library's own lines count on the home screen")
let lib2 = #"D:\Library2\steamapps\common\Red Dead Redemption 2"#
GameProfile(windowsPath: lib2).text = "vram-mb = 2304\n"
LibraryModel.shared.entries = [LibraryEntry(title: "Red Dead Redemption 2", windowsPath: lib2, steamAppID: 1174180)]
R.prepare(windowsPath: lib2 + #"\RDR2.exe"#, title: "Red Dead Redemption 2")
check(file(lib2 + #"\RDR2.exe"#).isEmpty && mark(lib2 + #"\RDR2.exe"#) == "",
      "the library Steam game's own config counts for its program on the home screen")
let gowHome = #"C:\Home\GoW.exe"#
LibraryModel.shared.entries = [LibraryEntry(title: "God of War", windowsPath: gowHome, config: "dxmt = d3d11.mipClampBC=1")]
check(R.reset(windowsPath: gowHome, title: "God of War")?.id == "god-of-war" && file(gowHome) == R.godOfWar.fileText
      && LibraryModel.shared.entries[0].resolution == "1920x1080" && LibraryModel.shared.entries[0].config == nil,
      "a home screen Reset reaches the library entry for the same program")
check(LogStore.shared.lines.contains { $0.hasPrefix("[recommended] Red Dead Redemption 2: rdr2-steam v\(R.rdr2Steam.version) written (first sight)") }, "log line")
UserDefaults.standard.removeObject(forKey: key)
print("PASS: the home screen shares the files and records; Safe thread sync and Screen size take the list's values")

// A newer list, as a download brings it (GameRecommendationsSource.swift puts it in use): the same rules
// apply. An untouched config follows it at its next prepare (the file only), a config the player changed
// is never written, a game seen for the first time and Reset to Recommended take it whole.
LibraryModel.shared.entries = []
let libA = #"E:\Lib1\steamapps\common\Red Dead Redemption 2"#, libB = #"E:\Lib2\steamapps\common\Red Dead Redemption 2"#
var untouched = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: libA, steamAppID: 1174180)
var changed = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: libB, steamAppID: 1174180)
check(R.prepare(&untouched) && R.prepare(&changed) && file(libA) == R.rdr2Steam.fileText && file(libB) == R.rdr2Steam.fileText,
      "first sight with the built-in list")
GameProfile(windowsPath: libB).text = R.rdr2Steam.fileText + "fence-chain = 1\n"
let builtInRDR2 = R.rdr2Steam
var newer = try! RecommendationsFile.parse(try! Data(contentsOf: RecommendationsStore.builtInURL!))
let at = newer.rules.firstIndex { $0.id == "rdr2-steam" }!
newer.version += 1
newer.rules[at].version += 1
newer.rules[at].config.append(RecommendationLine(text: "env.MADEIRA_DEVICE_STATS_EXTRA = 1"))
newer.rules[at].config.append(RecommendationLine(text: "env.MADEIRA_LATER_BUILD = 1", minBuild: 601))
newer.rules[at].switches.avx = true
R.install(newer.list(build: RecommendationsStore.build, origin: .downloaded))
check(R.rdr2Steam.version == builtInRDR2.version + 1 && R.rdr2Steam.fileText == builtInRDR2.fileText + "env.MADEIRA_DEVICE_STATS_EXTRA = 1\n",
      "the newer list in use, without the line for a later build")
LibraryPrefs.setAVX(false, for: libA)
check(!R.prepare(&untouched) && file(libA) == R.rdr2Steam.fileText && !LibraryPrefs.avx(libA),
      "an untouched config follows the newer list, its switches stay")
check(file(libB).hasSuffix("fence-chain = 1\n") && !file(libB).contains("EXTRA") && !R.prepare(&changed) && !file(libB).contains("EXTRA"),
      "a config the player changed is never written")
check(R.reset(&changed)?.version == builtInRDR2.version + 1 && file(libB) == R.rdr2Steam.fileText && LibraryPrefs.avx(libB),
      "Reset to Recommended writes the newer list and its switches")
let libC = #"E:\Lib3\steamapps\common\Red Dead Redemption 2"#
var fresh = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: libC, steamAppID: 1174180)
check(R.prepare(&fresh) && file(libC) == R.rdr2Steam.fileText && LibraryPrefs.avx(libC), "a game seen for the first time takes it whole")
check(LogStore.shared.lines.contains("[recommended] Red Dead Redemption 2: rdr2-steam v\(builtInRDR2.version + 1) written (updated)"),
      "the update's log line")
check(R.followsList(windowsPath: libA) == true && R.followsList(windowsPath: libC) == true, "untouched games follow their list")
GameProfile(windowsPath: libA).text = R.rdr2Steam.fileText + "fence-chain = 1\n"
let ownLib = #"E:\Own\steamapps\common\Red Dead Redemption 2"#
GameProfile(windowsPath: ownLib).text = "vram-mb = 2304\n"
var ownEntry = LibraryEntry(title: "Red Dead Redemption 2", windowsPath: ownLib, steamAppID: 1174180)
check(!R.prepare(&ownEntry) && mark(ownLib) == "", "a config of its own is kept")
check(R.followsList(windowsPath: libA) == false && R.followsList(windowsPath: #"E:\Never\Seen.exe"#) == nil
      && R.followsList(windowsPath: ownLib) == false, "a changed config or one of its own does not; a game never seen: nil")
R.reload()
check(R.active.origin == .builtIn && R.rdr2Steam == builtInRDR2, "the next start reads the built-in list again (nothing was kept)")
UserDefaults.standard.removeObject(forKey: key)
print("PASS: a newer list reaches untouched configs (file only), never a changed one; first sight and Reset take it whole")
'''

with tempfile.TemporaryDirectory(prefix='madeira-recommended-') as directory:
    temporary = Path(directory)
    compiler = shutil.which('swiftc')
    if compiler:
        (temporary / 'Stubs.swift').write_text(stubs)
        # the RDR2 list's config lines (its two comment lines are not config)
        rdr2_config_lines = sum(1 for line in presets['rdr2-steam']['lines']
                                if line.strip() and not line.strip().startswith('#'))
        (temporary / 'main.swift').write_text(main.replace('@RDR2_CONFIG_LINES@', str(rdr2_config_lines))
                                              .replace('@RULE_COUNT@', str(len(presets))))
        (temporary / 'drive').mkdir()
        (temporary / 'configs').mkdir()
        (temporary / 'recs').mkdir()
        binary = temporary / 'recommended'
        subprocess.run([compiler] + [str(app / name) for name in ('GameRecommendations.swift', 'GameRecommendationsList.swift',
                                                                  'GameRecommendationsSource.swift', 'GameRecommendationsApply.swift',
                                                                  'ConfigCatalog.generated.swift')]
                       + [str(temporary / 'Stubs.swift'), str(temporary / 'main.swift'), '-o', str(binary)], check=True)
        env = dict(os.environ, MADEIRA_TEST_DRIVE=str(temporary / 'drive'), MADEIRA_TEST_CONFIGS=str(temporary / 'configs'),
                   BCD_TEST_RECOMMENDATIONS=str(recs_path), BCD_TEST_RECS_DIR=str(temporary / 'recs'))
        subprocess.run([str(binary)], check=True, env=env)
    elif args.require_swift:
        raise SystemExit('Swift compiler required for the recommended-settings fixture')
    else:
        print('SKIP: Swift compiler unavailable locally; macOS CI must run this check with --require-swift')
