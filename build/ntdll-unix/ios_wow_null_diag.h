/* ajan7-arkham (2026-10-21): [wow-null32], a FAILURE-PATH DIAGNOSTIC for "a 32-bit guest used a NULL pointer".
 *
 * Log only. Every read goes through ios_av_args_read (a fault-safe Mach read); nothing is written, protected or stopped. Called
 * from one place: the UNHANDLED report of ios_mach_exception_thread, so only for a fault every earlier handler declined, in JIT
 * code, on a 32-bit guest's access to its first 64 KB (the guest address below 0x10000; ios_av_args_diag.h does the same for
 * 64-bit guests and prints nothing for these). At most 3 reports per process (a repeat of the same host pc is not a report),
 * 5 lines each.
 *
 * Why: BATMAN: ARKHAM CITY GOTY (Steam 200260, build 578, C7DGSN). BatmanAC.exe starts BmLauncher.exe, a .NET Framework program, and
 * Wine Mono dies in the launcher's main thread on `mov dword [ebx+0x10c], 1` with EBX = 0 (guest EIP 0x778635C9 = libmono-2.0-x86.dll
 * +0x2135C9: Mono's SGen allocator marks the thread's critical region through the thread info it reads from a TLS slot, and the
 * slot reads NULL). The log shows the registers as host numbers ([fault-full], and [x86_live] with the 64-bit mapping), the saved
 * context of the LAST SYSCALL (guest_rip=0x777fb4d3, not the faulting instruction), and nothing about the guest's stack or its TLS.
 * Without the libmono binary on the Mac the function cannot be named, and without the thread's TLS cells nobody can say whether
 * the slot was never set (the thread was not attached) or was set and cleared again (another process's TlsFree, see
 * ios_tls_clear.h). This prints, for the faulting thread:
 *   1. the 32-bit registers (mapped as FEX's x32::SRA does it: EAX x4, ECX x7, EDX x5, EBX x6, ESP x8, EBP x9, ESI x10, EDI x11,
 *      window base x19, see ios_x87_int64_diag.h), each with module+RVA when it points into a module of the process;
 *   2. 16 dwords of the guest stack from ESP, the same way: the saved registers, the return address (module+RVA of the caller:
 *      with the module's file the function is named offline) and the arguments;
 *   3. the thread's TEB32 -> PEB32 -> TlsBitmapBits (the allocated TLS indices) and TlsSlots[0..63]: the slots that hold a value
 *      (with module+RVA when they are code or data of a module), and the allocated indices whose cell is NULL.
 * Reading it: an allocated index whose cell is NULL in a thread that must have set it is a cleared cell (look for
 * [tls-cross-clear] lines with that index just before); all the cells of a Mono process set while the code read a NULL one means
 * the slot index the code used was not the one it was given (the stack and the arguments say which function).
 *
 * The module list is the PEB32's own (PEB_LDR_DATA32.InLoadOrderModuleList, LDR_DATA_TABLE_ENTRY32: DllBase +0x18, SizeOfImage
 * +0x20, BaseDllName +0x2c), at most 96 entries, walked once.
 */
#ifndef IOS_WOW_NULL_DIAG_H
#define IOS_WOW_NULL_DIAG_H

/* offsets in the 32-bit structures (include/winternl.h: TEB32, PEB32, PEB_LDR_DATA32; checked against the pinned headers by
 * tests/host/check-wow-null-diag.py) */
#define IOS_WN32_TEB_SELF      0x18
#define IOS_WN32_TEB_PEB       0x30
#define IOS_WN32_TEB_TLSSLOTS  0xe10
#define IOS_WN32_TEB_TLSEXP    0xf94
#define IOS_WN32_PEB_LDR       0x0c
#define IOS_WN32_PEB_TLSBITS   0x44
#define IOS_WN32_LDR_LOADORDER 0x0c
#define IOS_WN32_LDRE_DLLBASE  0x18
#define IOS_WN32_LDRE_SIZE     0x20
#define IOS_WN32_LDRE_NAME     0x2c   /* UNICODE_STRING32: Length u16, MaximumLength u16, Buffer u32 */
#ifndef IOS_WN32_TEB_WOWOFF
#define IOS_WN32_TEB_WOWOFF    offsetof( TEB, WowTebOffset )
#endif

struct ios_wn32_mod { uint32_t base, size; char name[24]; };

/* The process's modules, from the PEB32 loader list. Returns how many were read. */
static unsigned ios_wn32_modules( uint64_t win, uint32_t peb32, struct ios_wn32_mod *mods, unsigned max )
{
    uint32_t ldr = 0, head, e = 0;
    unsigned n = 0, walked = 0;

    if (!ios_av_args_read( win + peb32 + IOS_WN32_PEB_LDR, &ldr, 4 ) || ldr < 0x10000) return 0;
    head = ldr + IOS_WN32_LDR_LOADORDER;
    if (!ios_av_args_read( win + head, &e, 4 )) return 0;
    while (e >= 0x10000 && e != head && n < max && walked++ < 96)
    {
        uint32_t next = 0, nameptr = 0;
        uint16_t namelen = 0;
        struct ios_wn32_mod *m = &mods[n];
        unsigned i;

        if (!ios_av_args_read( win + e + IOS_WN32_LDRE_DLLBASE, &m->base, 4 ) ||
            !ios_av_args_read( win + e + IOS_WN32_LDRE_SIZE, &m->size, 4 ) ||
            !ios_av_args_read( win + e, &next, 4 )) break;
        m->name[0] = '?'; m->name[1] = 0;
        if (ios_av_args_read( win + e + IOS_WN32_LDRE_NAME, &namelen, 2 ) &&
            ios_av_args_read( win + e + IOS_WN32_LDRE_NAME + 4, &nameptr, 4 ) && nameptr >= 0x10000)
        {
            uint16_t w[23];
            unsigned chars = namelen / 2;

            if (chars > 22) chars = 22;
            if (chars && ios_av_args_read( win + nameptr, w, chars * 2 ))
            {
                for (i = 0; i < chars; i++) m->name[i] = w[i] > 0x20 && w[i] < 0x7f ? (char)w[i] : '?';
                m->name[chars] = 0;
            }
        }
        if (m->base) n++;
        e = next;
    }
    return n;
}

/* "name+0xRVA" when va is inside one of the modules, else "". */
static const char *ios_wn32_where( const struct ios_wn32_mod *mods, unsigned n, uint32_t va, char *out, size_t outsz )
{
    unsigned i;

    out[0] = 0;
    for (i = 0; i < n; i++)
        if (va >= mods[i].base && va - mods[i].base < mods[i].size)
        {
            snprintf( out, outsz, "(%s+0x%x)", mods[i].name, va - mods[i].base );
            break;
        }
    return out;
}

/* build 591: the same report from the BSD signal path (bus_handler's "BUS->AV: unreadable target"; Devil May Cry, GKQD2V: a load-acquire
 * of guest address 0x68 reported "BUS->AV: unreadable target addr=0x7900000068" and nothing else, because only the Mach path called this).
 * There the host's temporary x24 need not hold the address (an LDAR/LDAPR address register is whichever FEX picked), so the x24 test is
 * dropped for that call; everything else of the gate stays: a data fault in JIT code, x19 a 4 GB aligned window base, the address in the
 * window's first 64 KB. */
static int ios_wn_loose;

static void ios_wow_null_diag( const arm_thread_state64_t *st, uintptr_t thread_teb, int exception, uintptr_t fault_addr )
{
    static volatile int reports;
    static volatile uint64_t seen[3];
    uint64_t pc = __darwin_arm_thread_state64_get_pc( *st ), base = st->__x[19], teb32_host = 0;
    uint32_t eax, ecx, edx, ebx, esp, ebp, esi, edi, win, teb32, peb32 = 0, bits[2] = { 0, 0 }, tlsexp = 0, slots[64], stack[16];
    int32_t woff = 0;
    struct ios_wn32_mod mods[96];
    unsigned nmods = 0, k, i, nset = 0, nempty = 0, nstack = 0;
    char w[48], line[900], tmp[96];
    int num, have_stack = 0, have_slots = 0;

    if (exception != EXC_BAD_ACCESS || !thread_teb || !ios_av_args_pc_is_jit( pc )) return;
    if (!((base & 0xffffffffULL) == 0 && base >= 0x100000000ULL && fault_addr >= base && fault_addr - base < 0x10000 &&
          (st->__x[24] == fault_addr || ios_wn_loose))) return;
    for (k = 0; k < 3; k++) if (seen[k] == pc) return;
    if ((num = __sync_add_and_fetch( &reports, 1 )) > 3) return;
    seen[num - 1] = pc;

    eax = (uint32_t)st->__x[4]; ecx = (uint32_t)st->__x[7]; edx = (uint32_t)st->__x[5]; ebx = (uint32_t)st->__x[6];
    esp = (uint32_t)st->__x[8]; ebp = (uint32_t)st->__x[9]; esi = (uint32_t)st->__x[10]; edi = (uint32_t)st->__x[11];
    win = (uint32_t)(fault_addr - base);

    if (ios_av_args_read( thread_teb + IOS_WN32_TEB_WOWOFF, &woff, 4 ) && woff > 0) teb32_host = thread_teb + (uint64_t)woff;
    teb32 = teb32_host >= base && teb32_host - base < 0x100000000ULL ? (uint32_t)(teb32_host - base) : 0;
    if (teb32)
    {
        uint32_t self = 0;

        if (!ios_av_args_read( teb32_host + IOS_WN32_TEB_SELF, &self, 4 ) || self != teb32) teb32 = 0;
    }
    if (teb32 && ios_av_args_read( teb32_host + IOS_WN32_TEB_PEB, &peb32, 4 ) && peb32 >= 0x10000)
        nmods = ios_wn32_modules( base, peb32, mods, 96 );

    dprintf( STDERR_FILENO,
             "[wow-null32] rev=ajan7-arkham #%d the 32-bit guest used the NULL page: guest address 0x%x, host pc 0x%llx; "
             "EAX=0x%x ECX=0x%x EDX=0x%x EBX=0x%x ESI=0x%x EDI=0x%x EBP=0x%x ESP=0x%x (%u modules in the PEB32 list)\n",
             num, win, (unsigned long long)pc, eax, ecx, edx, ebx, esi, edi, ebp, esp, nmods );

    /* the registers and the stack, with the module each value points into */
    {
        const struct { const char *name; uint32_t v; } regs[] =
            { { "EAX", eax }, { "ECX", ecx }, { "EDX", edx }, { "EBX", ebx }, { "ESI", esi }, { "EDI", edi }, { "EBP", ebp } };
        size_t len = 0;

        line[0] = 0;
        for (i = 0; i < sizeof(regs) / sizeof(regs[0]); i++)
        {
            ios_wn32_where( mods, nmods, regs[i].v, w, sizeof(w) );
            if (!w[0]) continue;
            len += (size_t)snprintf( line + len, sizeof(line) - len, " %s=0x%x%s", regs[i].name, regs[i].v, w );
            if (len >= sizeof(line) - 80) break;
        }
        if (len) dprintf( STDERR_FILENO, "[wow-null32]   registers that point into a module:%s\n", line );
    }
    if (esp >= 0x10000 && ios_av_args_read( base + esp, stack, sizeof(stack) )) have_stack = 1;
    else if (esp >= 0x10000 && ios_av_args_read( base + esp, stack, 8 * 4 )) { have_stack = 1; nstack = 8; }
    if (have_stack)
    {
        size_t len = 0;
        unsigned count = nstack ? nstack : 16;

        line[0] = 0;
        for (i = 0; i < count && len < sizeof(line) - 80; i++)
        {
            ios_wn32_where( mods, nmods, stack[i], w, sizeof(w) );
            len += (size_t)snprintf( line + len, sizeof(line) - len, " +%02x:%x%s", (unsigned)(i * 4), stack[i], w );
        }
        dprintf( STDERR_FILENO, "[wow-null32]   guest stack from ESP (offset:dword, module+RVA when code or data of a module):%s\n", line );
    }
    else dprintf( STDERR_FILENO, "[wow-null32]   guest stack at ESP=0x%x is not readable\n", esp );

    /* the TLS of this thread */
    if (!teb32 || !peb32)
    {
        dprintf( STDERR_FILENO, "[wow-null32]   no TEB32 (TEB64 0x%llx, WowTebOffset %d): TLS not read\n", (unsigned long long)thread_teb, woff );
        return;
    }
    if (ios_av_args_read( base + peb32 + IOS_WN32_PEB_TLSBITS, bits, 8 ) &&
        ios_av_args_read( teb32_host + IOS_WN32_TEB_TLSSLOTS, slots, sizeof(slots) ))
        have_slots = 1;
    (void)ios_av_args_read( teb32_host + IOS_WN32_TEB_TLSEXP, &tlsexp, 4 );
    if (!have_slots)
    {
        dprintf( STDERR_FILENO, "[wow-null32]   TEB32=0x%x PEB32=0x%x: TlsBitmapBits or TlsSlots not readable\n", teb32, peb32 );
        return;
    }
    {
        size_t lset = 0, lempty = 0;
        char set[1000], empty[200];
        int cut = 0;

        set[0] = 0; empty[0] = 0;
        for (i = 0; i < 64; i++)
        {
            int allocated = (bits[i / 32] >> (i % 32)) & 1;

            if (slots[i])
            {
                nset++;
                if (lset >= sizeof(set) - 100) cut = 1;
                else
                {
                    ios_wn32_where( mods, nmods, slots[i], tmp, sizeof(tmp) );
                    lset += (size_t)snprintf( set + lset, sizeof(set) - lset, " [%u]=0x%x%s%s", i, slots[i], tmp,
                                              allocated ? "" : "(NOT allocated)" );
                }
            }
            else if (allocated)
            {
                nempty++;
                if (lempty < sizeof(empty) - 8) lempty += (size_t)snprintf( empty + lempty, sizeof(empty) - lempty, " %u", i );
            }
        }
        dprintf( STDERR_FILENO,
                 "[wow-null32]   TEB32=0x%x PEB32=0x%x TlsBitmapBits=0x%08x%08x TlsExpansionSlots=0x%x; %u cell(s) hold a value:%s%s\n",
                 teb32, peb32, bits[1], bits[0], tlsexp, nset, set, cut ? " (list cut)" : "" );
        dprintf( STDERR_FILENO, "[wow-null32]   %u allocated index(es) whose cell is NULL in this thread:%s\n", nempty, empty );
    }
}


static void ios_wow_null_diag_bus( const arm_thread_state64_t *st, uintptr_t thread_teb, uintptr_t fault_addr )
{
    ios_wn_loose = 1;
    ios_wow_null_diag( st, thread_teb, EXC_BAD_ACCESS, fault_addr );
    ios_wn_loose = 0;
}

#endif
