/* A guest store that crosses a 16 KB page boundary, emulated page by page.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * Need for Speed Underground 2 (7F9ZRF, build 579) copies into an anonymous RWX allocation with FEX's
 * `STP Q16, Q17, [X10], #32` (insn 0xac810421). The allocation's page is physically r-x (the guest-visible
 * write goes through an alias), the access starts at 0x710ee13fe4 and its 32 bytes end at 0x710ee14003:
 * 28 bytes in the aliased page, 4 bytes in the next 16 KB page, which is an ordinary writable page with no
 * alias (Wine's protections: this page RWX, the next RW). The Q-pair emulation (ml629) wanted all 32 bytes
 * in ONE alias run and refused ("REFUSING: 32B span leaves the alias"); a refused store is never
 * completed, so the same STP faulted again, 2000 times, until the redelivery guard ended the process.
 * The other store emulators of the same handler (ml1276, ml1283) already take each 16 KB page on its own;
 * this is the same split for the Q pair, in the branch that refused.
 *
 * WHAT IT DOES
 * ------------
 *   ios_store_split_plan   cut [addr, addr + bytes) at the 16 KB boundary and ask the caller where each
 *                          part is written: through an alias (a pointer the alias lookup vouched for for
 *                          the WHOLE part), or "direct" (the guest address itself, a page the lookup does
 *                          not know). A part the caller cannot place fails the plan.
 *   ios_store_split_commit direct parts first, through a write that reports failure instead of faulting
 *                          (the handler runs on the Mach exception thread: a fault here would wait for
 *                          itself), then the alias parts, which cannot fail. If a direct part cannot be
 *                          written NOTHING has been written, so a refusal is exactly the refusal that
 *                          existed before (the caller redelivers the fault as it did).
 *
 * No Wine type, no Mach type: tests/host/check-stp-straddle.py compiles this header with fake lookups.
 */
#ifndef IOS_STORE_SPLIT_H
#define IOS_STORE_SPLIT_H

#include <stdint.h>
#include <string.h>

#define IOS_STORE_SPLIT_PAGE 0x4000u
#define IOS_STORE_SPLIT_MAX  32u

typedef struct
{
    uintptr_t guest;    /* the guest address of this part */
    uintptr_t dst;      /* where to write it: the alias pointer, or == guest for a direct part */
    unsigned  len;
    unsigned  off;      /* offset of the part inside the store's bytes */
    int       direct;
} ios_store_part;

/* resolve(ctx, guest, len, &direct): the write address for [guest, guest + len), or 0 when it cannot be
 * placed. *direct = 1 when the address is the guest address itself (no alias). */
typedef uintptr_t (*ios_store_resolve_fn)( void *ctx, uintptr_t guest, unsigned len, int *direct );
/* write(ctx, guest, src, len): 1 when the bytes are written, 0 when they are not (nothing written). */
typedef int (*ios_store_direct_fn)( void *ctx, uintptr_t guest, const void *src, unsigned len );

/* Returns the number of parts (1 or 2), or 0 when the store cannot be placed (or is not a store this
 * helper handles: more than 32 bytes, or empty). */
static inline int ios_store_split_plan( uintptr_t addr, unsigned bytes, ios_store_resolve_fn resolve,
                                        void *ctx, ios_store_part parts[2] )
{
    unsigned done = 0;
    int n = 0, ndirect = 0;

    if (!bytes || bytes > IOS_STORE_SPLIT_MAX) return 0;
    while (done < bytes)
    {
        uintptr_t a = addr + done;
        unsigned len = bytes - done;
        unsigned room = IOS_STORE_SPLIT_PAGE - (unsigned)(a & (IOS_STORE_SPLIT_PAGE - 1));
        int direct = 0;
        uintptr_t dst;

        if (len > room) len = room;
        if (n >= 2) return 0;
        dst = resolve( ctx, a, len, &direct );
        if (!dst) return 0;
        if (direct && ndirect++) return 0;   /* one direct part at most: a refusal then leaves nothing written */
        parts[n].guest = a;
        parts[n].dst = dst;
        parts[n].len = len;
        parts[n].off = done;
        parts[n].direct = direct;
        n++;
        done += len;
    }
    return n;
}

/* 1 when the whole store is written; 0 when a direct part could not be written (then no part has been). */
static inline int ios_store_split_commit( const ios_store_part *parts, int n, const void *bytes,
                                          ios_store_direct_fn direct_write, void *ctx )
{
    const uint8_t *src = (const uint8_t *)bytes;
    int i;

    for (i = 0; i < n; i++)
        if (parts[i].direct && !direct_write( ctx, parts[i].guest, src + parts[i].off, parts[i].len ))
            return 0;
    for (i = 0; i < n; i++)
        if (!parts[i].direct) memcpy( (void *)parts[i].dst, src + parts[i].off, parts[i].len );
    return 1;
}

#endif /* IOS_STORE_SPLIT_H */
