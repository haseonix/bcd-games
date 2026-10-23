/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright 2026 thebcd69 (BCD) */
/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */

/*
 * BCD Host ("bcdhost.exe"): a store's signed-in start of a game, inside Wine
 * (docs/research/open-fork-2026-10-18/epic.md section 4.5, milestone 2).
 *
 * The app writes one request file right before the session (request.h) and names it in
 * BCD_HOST_REQUEST (a path through Wine's \\?\unix namespace). The host:
 *   1. opens the request exclusively, delete-on-close, reads it and wipes its buffers;
 *   2. checks it (request.c): the program, its folder, its arguments, the one file it may delete;
 *   3. starts services.exe first, outside the job, when the request asks;
 *   4. joins a job, so every process the game starts joins it too, and starts the program;
 *   5. waits for the program, then (when asked) for every process of the job to end, so a launcher
 *      that starts the real game and exits does not end the session. Wine's own helpers (services.exe,
 *      rpcss.exe ...) and crash reporters join the job and never end by themselves: they do not count (names.c).
 *      A program that ran for three minutes or more was the game itself: its exit ends the wait after ten seconds;
 *   6. deletes the one file it was told to (an ownership-token file), writes its report and exits
 *      with the program's own exit code.
 * Nothing it was given is printed or kept: the report (report.h) holds numbers only.
 *
 * x86-64, GUI subsystem (no console), kernel32 only, no C runtime. Built by build/bcd-host/build.sh.
 * It runs only for a store game BCD starts signed in; no other launch ever starts it.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "names.h"
#include "request.h"
#include "report.h"

static bcd_report R;
static bcd_request Q;
static uint8_t Raw[BCDHOST_MAX_REQUEST + 1];
static WCHAR EnvPath[1200];
static char Text[512];
static ULONGLONG Started;

static const WCHAR ReportDir1[] = L"C:\\ProgramData";
static const WCHAR ReportDir2[] = L"C:\\ProgramData\\BCD";
static const WCHAR ReportFile[] = L"C:\\ProgramData\\BCD\\host.txt";
static const WCHAR ServicesExe[] = L"C:\\windows\\system32\\services.exe";

/* Rewrites the report: whoever reads it after a stop sees the last stage reached. */
static void report(DWORD stage)
{
    HANDLE file;
    size_t n;
    DWORD written;

    R.stage = stage;
    R.seconds = (uint32_t)((GetTickCount64() - Started) / 1000);
    n = bcd_report_format(&R, Text, sizeof(Text));
    if (!n) return;
    file = CreateFileW(ReportFile, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return;
    WriteFile(file, Text, (DWORD)n, &written, NULL);
    CloseHandle(file);
}

__attribute__((noreturn)) static void finish(DWORD code)
{
    bcd_wipe(Raw, sizeof(Raw));
    bcd_wipe(&Q, sizeof(Q));
    ExitProcess(code);
}

__attribute__((noreturn)) static void fail(DWORD error, DWORD stage)
{
    R.error = error;
    R.last_error = GetLastError();
    report(stage);
    finish(2);
}

/* "--epic" as a whole word of the command line (the one mode of milestone 2). */
static int has_mode(const WCHAR *cmd)
{
    static const WCHAR mode[] = L"--epic";
    const WCHAR *p;

    for (p = cmd; *p; p++)
    {
        int k = 0;
        while (mode[k] && p[k] == mode[k]) k++;
        if (!mode[k] && (p == cmd || p[-1] == L' ' || p[-1] == L'"') && (p[k] == 0 || p[k] == L' ' || p[k] == L'"')) return 1;
    }
    return 0;
}

/* Opens the request so that nothing is left of it: exclusive and delete-on-close, then the same shared,
 * then plain (deleted by name after the read). `open` says which worked. */
static HANDLE open_request(const WCHAR *path, int *by_name)
{
    HANDLE h;

    *by_name = 0;
    h = CreateFileW(path, GENERIC_READ | DELETE, 0, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_DELETE_ON_CLOSE, NULL);
    if (h != INVALID_HANDLE_VALUE) { R.open = 1; return h; }
    h = CreateFileW(path, GENERIC_READ | DELETE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL | FILE_FLAG_DELETE_ON_CLOSE, NULL);
    if (h != INVALID_HANDLE_VALUE) { R.open = 2; return h; }
    h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) { R.open = 3; *by_name = 1; }
    return h;
}

#define BCDHOST_LATE_SECONDS 180
#define BCDHOST_LATE_GRACE_MS 10000
#define BCDHOST_MAX_IDS 255

static BYTE IdList[sizeof(JOBOBJECT_BASIC_PROCESS_ID_LIST) + (BCDHOST_MAX_IDS - 1) * sizeof(ULONG_PTR)];
static WCHAR ImageName[520];

/* 1: the job holds a process that is not this one and not a Wine helper or a reporter; 0: nothing but those; -1: the job cannot be asked.
 * The processes are told apart by their image names when the job lists them; a process whose name cannot be read counts. Without the list
 * the job's own count is used (every process but this one). */
static int others_running(HANDLE job)
{
    JOBOBJECT_BASIC_PROCESS_ID_LIST *list = (JOBOBJECT_BASIC_PROCESS_ID_LIST *)IdList;
    JOBOBJECT_BASIC_ACCOUNTING_INFORMATION info;
    DWORD i, self = GetCurrentProcessId(), left = 0, helpers = 0;

    if (QueryInformationJobObject(job, JobObjectBasicProcessIdList, list, sizeof(IdList), NULL) &&
        list->NumberOfAssignedProcesses <= list->NumberOfProcessIdsInList)
    {
        for (i = 0; i < list->NumberOfProcessIdsInList; i++)
        {
            DWORD pid = (DWORD)list->ProcessIdList[i], length = sizeof(ImageName) / sizeof(ImageName[0]);
            HANDLE process;

            if (pid == self) continue;
            process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
            if (process && QueryFullProcessImageNameW(process, 0, ImageName, &length) && bcd_host_background((const uint16_t *)ImageName, length)) helpers++;
            else left++;
            if (process) CloseHandle(process);
        }
        R.names = 1;
        R.left = left;
        R.helpers = helpers;
        return left > 0;
    }
    R.names = 0;
    if (!QueryInformationJobObject(job, JobObjectBasicAccountingInformation, &info, sizeof(info), NULL)) return -1;
    R.left = info.ActiveProcesses > 1 ? info.ActiveProcesses - 1 : 0;
    return info.ActiveProcesses > 1;
}

__attribute__((noreturn)) void bcdhost_start(void)
{
    HANDLE request, job = NULL, port = NULL;
    DWORD got = 0, total = 0, exit_code = 0;
    ULONGLONG started_child;
    int by_name = 0, rc;
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;

    Started = GetTickCount64();
    CreateDirectoryW(ReportDir1, NULL);
    CreateDirectoryW(ReportDir2, NULL);
    report(1);

    if (!has_mode(GetCommandLineW())) fail(BCDHOST_E_USAGE, 1);
    got = GetEnvironmentVariableW(L"BCD_HOST_REQUEST", EnvPath, sizeof(EnvPath) / sizeof(EnvPath[0]));
    if (!got || got >= sizeof(EnvPath) / sizeof(EnvPath[0])) fail(BCDHOST_E_NOENV, 1);

    request = open_request(EnvPath, &by_name);
    if (request == INVALID_HANDLE_VALUE) fail(BCDHOST_E_OPEN, 1);
    while (total < sizeof(Raw) && ReadFile(request, Raw + total, (DWORD)(sizeof(Raw) - total), &got, NULL) && got)
        total += got;
    if (total == 0) { CloseHandle(request); if (by_name) DeleteFileW(EnvPath); fail(BCDHOST_E_READ, 2); }
    CloseHandle(request);                 /* delete-on-close: the file is gone now */
    if (by_name) DeleteFileW(EnvPath);
    bcd_wipe(EnvPath, sizeof(EnvPath));
    report(2);

    rc = bcd_request_parse(Raw, total, &Q);
    bcd_wipe(Raw, sizeof(Raw));
    if (rc) fail((DWORD)rc, 2);
    R.flags = Q.flags;
    report(3);

    if (Q.flags & BCDHOST_FLAG_SERVICES)
    {
        /* Outside the job: the service manager lives as long as the session and must not hold the tree open. */
        static WCHAR services[sizeof(ServicesExe) / sizeof(WCHAR)];
        size_t i;
        for (i = 0; i < sizeof(services) / sizeof(WCHAR); i++) services[i] = ServicesExe[i];
        ZeroMemory(&si, sizeof(si)); si.cb = sizeof(si);
        ZeroMemory(&pi, sizeof(pi));
        if (CreateProcessW(ServicesExe, services, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi))
        {
            R.services = 1;
            CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
        }
        else R.services = 2;
        report(4);
    }

    if (Q.flags & BCDHOST_FLAG_TREE)
    {
        /* The host joins the job first, so the program and everything it starts are in it without a suspended start. */
        job = CreateJobObjectW(NULL, NULL);
        if (job && AssignProcessToJobObject(job, GetCurrentProcess()))
        {
            JOBOBJECT_ASSOCIATE_COMPLETION_PORT assoc;
            R.job = 1;
            R.wait = 1;
            port = CreateIoCompletionPort(INVALID_HANDLE_VALUE, NULL, 0, 1);
            if (port)
            {
                assoc.CompletionKey = job;
                assoc.CompletionPort = port;
                if (SetInformationJobObject(job, JobObjectAssociateCompletionPortInformation, &assoc, sizeof(assoc))) R.wait = 2;
                else { CloseHandle(port); port = NULL; }
            }
        }
        else job = NULL;
    }
    report(5);

    ZeroMemory(&si, sizeof(si)); si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(Q.program, Q.cmdline, NULL, NULL, FALSE, 0, NULL, Q.cwd, &si, &pi)) fail(BCDHOST_E_START, 5);
    bcd_wipe(Q.cmdline, sizeof(Q.cmdline));   /* the arguments (a one-time code) are the child's now */
    R.child_pid = pi.dwProcessId;
    CloseHandle(pi.hThread);
    report(6);

    started_child = GetTickCount64();
    WaitForSingleObject(pi.hProcess, INFINITE);
    R.lived = (uint32_t)((GetTickCount64() - started_child) / 1000);
    if (!GetExitCodeProcess(pi.hProcess, &exit_code)) exit_code = 0;
    CloseHandle(pi.hProcess);
    R.child_exit = exit_code;
    report(7);

    if (job)
    {
        /* A launcher that starts the real game and exits leaves the job busy: wait until it holds nothing but helpers, and look once more
         * after two seconds (a launcher that restarts itself has a moment with nothing running). A program that ran three minutes or more was
         * the game: whatever else is left (a helper that does not look like one) gets ten seconds, not forever. */
        int settled = 0;
        ULONGLONG deadline = 0;

        if (R.lived >= BCDHOST_LATE_SECONDS) { R.late = 1; deadline = GetTickCount64() + BCDHOST_LATE_GRACE_MS; }
        for (;;)
        {
            int others = others_running(job);
            DWORD wait_ms = port ? 10000 : 1000;

            report(7);   /* what the job holds now: a wait that never ends shows how many processes keep it open */

            if (others == 0 || others == -1)
            {
                if (others == -1) R.wait = 0;
                if (others == -1 || settled) break;
                settled = 1;
                R.settle++;
                Sleep(2000);
                continue;
            }
            settled = 0;
            if (deadline)
            {
                ULONGLONG now = GetTickCount64();
                if (now >= deadline) break;
                if (deadline - now < wait_ms) wait_ms = (DWORD)(deadline - now);
            }
            if (port)
            {
                DWORD bytes; ULONG_PTR key; LPOVERLAPPED overlapped;
                GetQueuedCompletionStatus(port, &bytes, &key, &overlapped, wait_ms);   /* a notice, or the time */
            }
            else Sleep(wait_ms);
        }
    }
    report(8);

    if (Q.ovt[0])
    {
        if (DeleteFileW(Q.ovt) || GetLastError() == ERROR_FILE_NOT_FOUND) R.ovt = 1;
        else R.ovt = 2;
    }
    report(9);
    report(10);
    finish(exit_code);
}
