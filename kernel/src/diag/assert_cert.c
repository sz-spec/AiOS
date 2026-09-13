/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 Certification Assertion Harness — implementation
 *
 * COM1 (port 0x3F8) direct-write emit. Bypasses the regular console
 * subsystem to avoid:
 *   1. Lock contention with normal kernel logging.
 *   2. Format-string side effects in early boot before printf is ready.
 *   3. Confusion between cert lines and regular log noise (the
 *      "~~CERT~~" magic prefix is the runner's anchor).
 *
 * Spec lives in kernel/include/vos/assert_cert.h. ID registry lives
 * in kernel/include/vos/assert_cert_ids.h.
 */

#include "../../include/vos/assert_cert.h"

#include <stdint.h>

/* ----- COM1 register layout ----- */
#define VOS3_CERT_COM1          0x3F8u
#define VOS3_CERT_COM1_LSR      (VOS3_CERT_COM1 + 5u)
#define VOS3_CERT_COM1_LSR_THRE (1u << 5)   /* TX holding register empty */

/* ----- Local atomic counter (not the global atomic.h — keep harness
 * self-contained and freestanding-correct). ----- */
static volatile uint32_t g_cert_emit_count = 0u;

/* ----- I/O port primitives ----- */

#ifdef VOS3_ASSERT_HARNESS

static inline void cert_outb(uint16_t port, uint8_t value)
{
    __asm__ volatile ("outb %0, %1" :: "a"(value), "Nd"(port));
}

static inline uint8_t cert_inb(uint16_t port)
{
    uint8_t value;
    __asm__ volatile ("inb %1, %0" : "=a"(value) : "Nd"(port));
    return value;
}

static void cert_putc(char c)
{
    /* Bounded busy-wait. ~1M iterations is ample even at 9600 baud
     * (which is faster than that). If TX never drains, we abandon
     * the byte rather than hanging the kernel. */
    uint32_t spin = 1000000u;
    while (spin-- &&
           !(cert_inb(VOS3_CERT_COM1_LSR) & VOS3_CERT_COM1_LSR_THRE)) {
        /* idle */
    }
    cert_outb(VOS3_CERT_COM1, (uint8_t)c);
}

static void cert_puts(const char *s)
{
    if (s == ((void *)0)) {
        cert_puts("(null)");
        return;
    }
    while (*s) {
        cert_putc(*s++);
    }
}

static void cert_putu32(uint32_t v)
{
    /* Decimal, no leading zeros, no padding. */
    char buf[11];
    int  len = 0;
    if (v == 0u) {
        cert_putc('0');
        return;
    }
    while (v) {
        buf[len++] = (char)('0' + (v % 10u));
        v /= 10u;
    }
    while (len--) {
        cert_putc(buf[len]);
    }
}

#endif /* VOS3_ASSERT_HARNESS */

/* ----- Public API ----- */

void vos3_assert_cert_emit(uint32_t id,
                           int passed,
                           const char *file,
                           int line,
                           const char *expr)
{
#ifdef VOS3_ASSERT_HARNESS
    /* Format: "~~CERT~~ <id> <PASS|FAIL> <file>:<line> <expr>\n" */
    cert_puts("~~CERT~~ ");
    cert_putu32(id);
    cert_putc(' ');
    cert_puts(passed ? "PASS" : "FAIL");
    cert_putc(' ');
    cert_puts(file != ((void *)0) ? file : "?");
    cert_putc(':');
    cert_putu32((uint32_t)line);
    cert_putc(' ');
    cert_puts(expr != ((void *)0) ? expr : "?");
    cert_putc('\n');

    /* Single-CPU bump is safe; SMP fan-out is fine for emit-counting
     * because over-counts cannot under-report. */
    g_cert_emit_count++;
#else
    (void)id; (void)passed; (void)file; (void)line; (void)expr;
#endif
}

void vos3_assert_cert_done(void)
{
#ifdef VOS3_ASSERT_HARNESS
    /* Sentinel line — runner stops reading on this. Format:
     *   "~~CERT~~ DONE <count>\n"
     */
    cert_puts("~~CERT~~ DONE ");
    cert_putu32(g_cert_emit_count);
    cert_putc('\n');
#endif
}

uint32_t vos3_assert_cert_emit_count(void)
{
    return g_cert_emit_count;
}
