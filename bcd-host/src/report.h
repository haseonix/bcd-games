/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright 2026 thebcd69 (BCD) */
/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */

/*
 * BCD Host's report: C:\ProgramData\BCD\host.txt, numeric "key=value" lines only, rewritten at every
 * stage so a run that stops shows the last stage it reached. Never a path, a name, a code or any text
 * from the request: the app reads only the keys below (BCDEpicHostFiles.swift) and logs them as
 * "[bcd-host] ...". Pure C: tests/check.c compiles it on the host.
 *
 * stage:  1 started, 2 request read, 3 request checked, 4 services started, 5 job ready, 6 program
 *         started, 7 program ended, 8 the tree is empty, 9 cleaned up, 10 done
 * wait:   0 the program only, 1 the job polled, 2 the job's completion port
 * lived:  seconds the program ran; late = 1 when that was long (the program was the game, not a launcher that starts it and goes): the
 *         wait for the rest of the tree is then at most ten seconds
 * left/helpers/names: what was in the job at the end (see below)
 * ovt:    0 none asked for, 1 deleted, 2 could not be deleted
 * open:   how the request was opened: 1 delete-on-close and exclusive, 2 delete-on-close shared,
 *         3 read, then deleted by name; 0 not opened
 */
#ifndef BCDHOST_REPORT_H
#define BCDHOST_REPORT_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t stage;
    uint32_t error;        /* BCDHOST_E_* from request.h, or 20 and up from the host itself (below) */
    uint32_t last_error;   /* the Windows error of the call that failed */
    uint32_t flags;
    uint32_t open;
    uint32_t services;     /* 0 not asked, 1 started, 2 could not be started */
    uint32_t job;          /* 0 none, 1 the host is in a job */
    uint32_t wait;
    uint32_t child_pid;
    uint32_t child_exit;   /* the program's exit code; meaningful from stage 7 */
    uint32_t settle;       /* how many times the tree looked empty and was checked again */
    uint32_t ovt;
    uint32_t seconds;      /* since the host started */
    uint32_t lived;        /* how long the program ran (seconds) */
    uint32_t late;         /* 1: the program ran so long that its exit means the game ended: the tree wait was cut to a few seconds */
    uint32_t left;         /* at the end: processes of the job that were not Wine's own helpers or reporters */
    uint32_t helpers;      /* ... and those that were (they do not keep a session open) */
    uint32_t names;        /* 1: the job's processes were told apart by name; 0: only counted */
} bcd_report;

/* The host's own errors (0 to 19 are the request's). */
enum {
    BCDHOST_E_USAGE = 20,     /* not started with --epic */
    BCDHOST_E_NOENV = 21,     /* no BCD_HOST_REQUEST in the environment */
    BCDHOST_E_OPEN = 22,      /* the request could not be opened */
    BCDHOST_E_READ = 23,
    BCDHOST_E_START = 24      /* CreateProcessW failed (last_error says why) */
};

/* Writes the report into `buf` (at most `cap` bytes, not terminated). Returns the length, or 0 when `cap` is too small. */
size_t bcd_report_format(const bcd_report *r, char *buf, size_t cap);

#endif
