#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's ASF byte stream handler (dlls/mfasfsrcsnk, built from dlls/mfsrcsnk/media_source.c) uses winedmo.

Resident Evil 2 (Steam 883710, build 579, log re2-ipadm4-579) plays WMV cutscenes through a Media
Foundation source reader; the ASF byte stream handler's DLL, mfasfsrcsnk.dll, is not in the prefix
(c0000135), so the source resolver has no ASF source and the game, which does not check, reads NULL.
BCD ships mfasfsrcsnk.dll as an opt-in DLL (arm64ec-windows/mfasfsrcsnk-optin.dll, env.MADEIRA_OPTIN_DLLS
= mfasfsrcsnk, ...); it demuxes through winedmo.dll, whose unix side is FFmpeg's libavformat
(build/ntdll-unix/winedmo_unixlib_ios.c).

Wine's handler does not use winedmo unless it is told to.  asf_byte_stream_plugin_factory_CreateInstance
(mfsrcsnk/media_source.c) goes on to the GENERIC handler, CLSID_GStreamerByteStreamHandler, when

    use_gst_byte_stream_handler()

is TRUE, and that is TRUE unless HKCU\\Software\\Wine\\MediaFoundation\\DisableGstByteStreamHandler is a
DWORD that is not 0:

    if (!RegGetValueW( HKEY_CURRENT_USER, L"Software\\\\Wine\\\\MediaFoundation", L"DisableGstByteStreamHandler", ... ))
        return !result;
    return TRUE;                              <- no key: the generic handler

BCD has no way to give one game a registry value, and the generic handler's parser is libavformat's mp3,
wav and mov only, so the ASF file would be refused there.  The patch changes that last statement to
`return FALSE`: with no key, the winedmo demuxer is used when winedmo_demuxer_check accepts the type
(it does where the FFmpeg asf demuxer is built), and the generic handler stays the fallback when the check
fails or the key says 0.  Nothing else in the file changes behaviour; this file is built only into
mfasfsrcsnk.dll (mfsrcsnk.dll is not shipped), and that DLL only runs for a game that opts in.

The patch also makes the handler say which one it chose and why, and says when the demuxer cannot be
created, as ERR lines ("[mfasfsrcsnk] madeira-bcd: ..."), because the app runs with WINEDEBUG=err+all and the
next log should show how far the ASF source got.

Wine's dlls/mfsrcsnk is LGPL 2.1 or later; the patched file carries a "Modified by BCD" notice, and this script
is the source of the change.

Usage:
  patch-wine-mfasfsrcsnk-winedmo.py WINE_SRC        patch the tree (idempotent)
  patch-wine-mfasfsrcsnk-winedmo.py --verify DLL    check a built DLL: ARM64EC/AMD64 PE, carries the marker
tools/build-wine-extra-dlls.sh applies it around the build and restores the tree afterwards.
"""
import os
import struct
import sys

MARKER = "mfasfsrcsnk-winedmo-v1 madeira-bcd"
FILE = "dlls/mfsrcsnk/media_source.c"

NOTICE_ANCHOR = "#include \"ntstatus.h\"\n#include \"mfsrcsnk_private.h\"\n"
NOTICE = '''/*
 * Modified by BCD, 2026-10-22 (tools/patch-wine-mfasfsrcsnk-winedmo.py): the ASF byte stream handler
 * uses the winedmo demuxer unless the registry says otherwise, and logs which handler it chose.
 * Everything else in this file is as Wine's; this file stays under the license above.
 */

'''

EDITS = (
    # the demuxer is used unless HKCU\Software\Wine\MediaFoundation\DisableGstByteStreamHandler is 0
    ('''                       RRF_RT_REG_DWORD, NULL, &result, &size ))
        return !result;

    return TRUE;
}
''', '''                       RRF_RT_REG_DWORD, NULL, &result, &size ))
        return !result;

    return FALSE;
}

static const char madeira_asf_marker[] __attribute__((used)) = "''' + MARKER + '''";
'''),
    ('''    NTSTATUS status;

    if ((status = winedmo_demuxer_check("video/x-ms-asf")) || use_gst_byte_stream_handler())
    {
''', '''    NTSTATUS status;

    status = winedmo_demuxer_check("video/x-ms-asf");
    ERR("[mfasfsrcsnk] madeira-bcd: demuxer check status %#lx, generic handler wanted %d -> %s\\n", status,
        use_gst_byte_stream_handler(),
        (status || use_gst_byte_stream_handler()) ? "generic (GStreamer) byte stream handler" : "winedmo (FFmpeg) ASF source");
    if (status || use_gst_byte_stream_handler())
    {
'''),
    ('''        WARN("Failed to create demuxer, status %#lx\\n", status);
''', '''        ERR("[mfasfsrcsnk] madeira-bcd: failed to create the demuxer, status %#lx\\n", status);
'''),
)


def patch(wine):
    path = os.path.join(wine, FILE)
    if not os.path.isfile(path):
        sys.exit("patch-wine-mfasfsrcsnk-winedmo: no " + path)
    text = open(path).read()
    if MARKER in text:
        print("already patched")
        return
    if text.count(NOTICE_ANCHOR) != 1:
        sys.exit("patch-wine-mfasfsrcsnk-winedmo: notice anchor found %d times in %s" % (text.count(NOTICE_ANCHOR), FILE))
    for old, new in EDITS:
        if text.count(old) != 1:
            sys.exit("patch-wine-mfasfsrcsnk-winedmo: anchor found %d times in %s:\n%s" % (text.count(old), FILE, old[:400]))
        text = text.replace(old, new)
    text = text.replace(NOTICE_ANCHOR, NOTICE + NOTICE_ANCHOR, 1)
    open(path, "w").write(text)
    print("patched %s (%d edits)" % (path, len(EDITS)))


def verify(path):
    d = open(path, "rb").read()
    if d[:2] != b"MZ":
        sys.exit("verify: %s is not a PE image" % path)
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    machine = struct.unpack_from("<H", d, pe + 4)[0]
    bad = []
    if MARKER.encode() not in d:
        bad.append("no '%s' string: not built from the patched tree" % MARKER)
    if machine not in (0xaa64, 0x8664):
        bad.append("machine %#x is neither ARM64 nor AMD64 (ARM64EC images carry 0x8664 or 0xaa64)" % machine)
    if b"winedmo.dll" not in d.lower() and b"WINEDMO.DLL" not in d:
        bad.append("does not import winedmo.dll")
    if bad:
        sys.exit("verify %s:\n  %s" % (path, "\n  ".join(bad)))
    print("verify %s: winedmo patch present, imports winedmo.dll" % path)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--verify":
        verify(sys.argv[2])
    elif len(sys.argv) == 2:
        patch(sys.argv[1])
    else:
        sys.exit(__doc__)
