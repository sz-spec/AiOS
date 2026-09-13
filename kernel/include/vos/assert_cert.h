/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 Certification Assertion Harness — M4 of v21.3 plan
 *
 * VOS3_ASSERT_CERT(id, expr) — emits a parseable line on COM1 (port
 * 0x3F8) carrying a unique certification ID, the PASS/FAIL status, the
 * call site, and the asserted expression text. The Python runner at
 * tools/runner/qemu_assert_runner.py reads the serial stream, collects
 * unique IDs, and produces a JUnit-shaped report.
 *
 * Output format (one line per call):
 *
 *   ~~CERT~~ <id> <PASS|FAIL> <file>:<line> <expr>\n
 *
 * The "~~CERT~~" magic prefix is the runner's anchor — anything before
 * it on the line is ignored, anything after it must parse. The format
 * is grep-line-stable: a single regex captures every emit.
 *
 * GATING:
 *   - All write paths are inside #ifdef VOS3_ASSERT_HARNESS. Default
 *     builds expand the macro to ((void)(expr)) — the asserted
 *     expression is still evaluated (so side effects in the expression
 *     don't disappear) but no serial I/O occurs.
 *   - VOS3_ASSERT_CERT does NOT panic on FAIL. The runner records both
 *     PASS and FAIL and the operator decides whether failures are
 *     blocking. Panic at boot would mask which earlier asserts fired.
 *
 * ID DISCIPLINE:
 *   - IDs are 32-bit but currently allocated 1..255 (see
 *     kernel/include/vos/assert_cert_ids.h).
 *   - Each ID must be used at EXACTLY ONE call site. The runner
 *     reports duplicates as failures.
 *   - Gaps in the ID space are not failures — they document
 *     reserved / future cert points.
 *
 * COMPLETION:
 *   - Boot code calls VOS3_ASSERT_CERT_DONE() once after all cert
 *     points have fired. The runner stops reading on the DONE line.
 *
 * Reference: docs/plans/V21_3_TOTAL_SUPREMACY_PLAN.md §4.
 */

#ifndef VOS3_INCLUDE_VOS_ASSERT_CERT_H
#define VOS3_INCLUDE_VOS_ASSERT_CERT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ----- Public macro ----- */

#ifdef VOS3_ASSERT_HARNESS

#define VOS3_ASSERT_CERT(id, expr) \
    do { \
        const int _vos3_cert_passed = (int)(!!(expr)); \
        vos3_assert_cert_emit((uint32_t)(id), _vos3_cert_passed, \
                              __FILE__, (int)__LINE__, #expr); \
    } while (0)

#define VOS3_ASSERT_CERT_DONE() \
    vos3_assert_cert_done()

#else  /* VOS3_ASSERT_HARNESS undefined */

/* Default builds: evaluate `expr` for side-effect parity, emit nothing. */
#define VOS3_ASSERT_CERT(id, expr) \
    do { (void)(id); (void)(expr); } while (0)
#define VOS3_ASSERT_CERT_DONE() ((void)0)

#endif  /* VOS3_ASSERT_HARNESS */

/* ----- Backing functions (defined in kernel/src/diag/assert_cert.c) -----
 *
 * Always declared so the macro expansion compiles. When VOS3_ASSERT_HARNESS
 * is undefined, the symbols still exist but the definitions are no-ops —
 * the linker GCs them as unreachable. */

void vos3_assert_cert_emit(uint32_t id,
                           int passed,
                           const char *file,
                           int line,
                           const char *expr);

void vos3_assert_cert_done(void);

/* Diagnostic — number of emits this boot. Useful for self-consistency
 * checks; the runner verifies this matches what it parsed. */
uint32_t vos3_assert_cert_emit_count(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_INCLUDE_VOS_ASSERT_CERT_H */
