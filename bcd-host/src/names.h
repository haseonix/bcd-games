/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright 2026 thebcd69 (BCD) */
/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */

/*
 * Which processes of a started game's job do not keep a session open. Wine starts its own helpers on demand from inside the
 * game's process (services.exe, rpcss.exe, winedevice.exe, ...), so they join the game's job and never end by themselves; a
 * crash reporter or a store's helper lives as long as the game does. The host waits for the game's tree, and these do not count
 * (the same words BCD's runtime uses for its own launcher-stub wait: build/ntdll-unix/process_ios.c, ios_child_is_helper).
 * Pure C: tests/check.c compiles it on the host.
 */
#ifndef BCDHOST_NAMES_H
#define BCDHOST_NAMES_H

#include <stddef.h>
#include <stdint.h>

/* 1 when the image path (UTF-16, any separators) names a Wine system process or a crash reporter or helper; 0 otherwise. */
int bcd_host_background(const uint16_t *path, size_t length);

#endif
