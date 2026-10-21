# Pinned Wine Mono for the app bundle. Sourced by fetch.sh and bundle.sh; not run directly.
#
# The version does not follow WINE_MONO_VERSION in wine/dlls/mscoree/mscoree_private.h: mscoree finds the
# bundled runtime through C:\windows\mono\mono-2.0, a path it does not check the version of, so
# a newer Wine still loads it; the scripts only warn about the mismatch.
#
# bundle.sh also patches the bundled mscorlib.dll (ml1281, a stopgap, see the TODO there), and that
# patch is verified for exactly this build of mscorlib.dll and nothing else. The device game run
# still needs to be repeated when changing the pinned runtime.
#
# Changing the version means new hashes below, a new COPYING from that release's source, redoing
# and re-verifying the mscorlib patch (new offset and both mscorlib hashes), and a device run of a
# .NET Framework game.

WINE_MONO_VER=11.3.0

# wine-mono-11.3.0-x86.tar.xz from https://dl.winehq.org/wine/wine-mono/11.3.0/, downloaded
# 2026-10-21. Its contents equal, file for file, wine-mono-11.3.0-x86.msi, whose SHA-256
# (df2dfc1665c2511882e7cabd56eafd0c0a3d94e5a7e86f969277f6c189d418d3) Wine pins in addons.c.
WINE_MONO_TAR_SHA256=54a1b0111c3fe4b785eae688af94d27e64995707ad648b6fee8000381b80d298

# wine-mono-11.3.0-src.tar.xz from the same folder (293 MiB): the source of that build, for the
# LGPL parts (THIRD-PARTY-NOTICES.md). `fetch.sh --source` downloads and checks it.
WINE_MONO_SRC_SHA256=9f4f03285fbeed484980efbb1eba1c19069244aea401d2ff1585e3990782edf7

# lib/mono/4.5/mscorlib.dll of that build, before and after bundle.sh's patch (ml1281).
WINE_MONO_MSCORLIB_SHA256=b715602dfd62e0eec01c8f2167fb6afee859ea7e6513eed2c7b8aeafc6da33d9
WINE_MONO_MSCORLIB_PATCHED_SHA256=5affb26c82c6499d46074d904f130a1a1c8de8b774372ebdac6fc03720b10105
