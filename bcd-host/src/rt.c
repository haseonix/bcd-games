/* SPDX-License-Identifier: GPL-3.0-only */
/* Copyright 2026 thebcd69 (BCD) */
/* BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md. */

/* The four memory functions a compiler may call, for a build with no C runtime (x86-64 only). */

#include <stddef.h>

void *memcpy(void *dst, const void *src, size_t n)
{
    void *d = dst;
    __asm__ volatile("rep movsb" : "+D"(d), "+S"(src), "+c"(n) : : "memory");
    return dst;
}

void *memset(void *dst, int c, size_t n)
{
    void *d = dst;
    __asm__ volatile("rep stosb" : "+D"(d), "+c"(n) : "a"(c) : "memory");
    return dst;
}

void *memmove(void *dst, const void *src, size_t n)
{
    unsigned char *d = (unsigned char *)dst;
    const unsigned char *s = (const unsigned char *)src;
    if (d == s || n == 0) return dst;
    if (d < s || d >= s + n)
    {
        __asm__ volatile("rep movsb" : "+D"(d), "+S"(s), "+c"(n) : : "memory");
    }
    else
    {
        d += n - 1; s += n - 1;
        __asm__ volatile("std\n\trep movsb\n\tcld" : "+D"(d), "+S"(s), "+c"(n) : : "memory");
    }
    return dst;
}

int memcmp(const void *a, const void *b, size_t n)
{
    const unsigned char *x = (const unsigned char *)a, *y = (const unsigned char *)b;
    while (n--)
    {
        if (*x != *y) return *x < *y ? -1 : 1;
        x++; y++;
    }
    return 0;
}
