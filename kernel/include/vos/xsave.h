/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 XSAVE — public API
 *
 * This header gathers the XSAVE-family entry points so kernel code can
 * include a single canonical declaration set. The implementations live in
 * kernel/src/arch/x86_64/xsave.c (probe + boot-init) and
 * kernel/src/sched/xsave_ctx.c (active save/restore + per-task wrappers).
 *
 * All active-write functions are gated behind VOS3_XSAVE_LIVE at compile
 * time and behind VOS3_HW_XSAVE at the build flag level. Default builds
 * are detection-only.
 */

#ifndef VOS3_INCLUDE_VOS_XSAVE_H
#define VOS3_INCLUDE_VOS_XSAVE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Forward decl — defined in include/vos/task.h */
struct vos3_task;

/* ----- Probe (idempotent, never writes registers) ----- */
int      vos3_xsave_probe(void);
uint32_t vos3_xsave_get_area_size(void);
uint64_t vos3_xsave_supported_features(void);
int      vos3_xsave_has_xsave(void);
int      vos3_xsave_has_avx(void);
int      vos3_xsave_has_avx512f(void);
int      vos3_xsave_has_amx(void);
int      vos3_xsave_is_avx512_safe(void);

/* ----- Boot-time activation (HW-1) -----
 *
 * Sets CR4.OSXSAVE and programs XCR0 with the supported component mask.
 * MUST be called AFTER vos3_xsave_probe() and AFTER CR4.OSFXSR has been
 * set by the existing FPU init path.
 *
 * Returns:
 *   0       on success
 *  -ENODEV  CPU does not advertise XSAVE (kernel stays in scaffold mode)
 *  -EINVAL  probe was not run first
 *
 * No-op (returns -ENODEV without touching CR4/XCR0) when VOS3_HW_XSAVE
 * is not defined.
 */
int vos3_xsave_boot_init(void);

/* ----- Raw save/restore (low-level, prefer per-task wrappers below) ----- */
void vos3_xsave_save(void *area, uint32_t mask_lo, uint32_t mask_hi);
void vos3_xsave_restore(const void *area, uint32_t mask_lo, uint32_t mask_hi);
int  vos3_xsave_live_path_compiled(void);

/* ----- Per-task wrappers (HW-1 context-switch entry points) -----
 *
 * Called by scheduler.c around vos3_context_switch(). NULL-tolerant —
 * tasks without an allocated xsave_area (e.g. the boot idle task before
 * vos3_xsave_get_area_size() returned non-zero) are silently skipped,
 * which matches the v20.x behavior where no extended-register save is
 * performed.
 *
 * Inert when VOS3_HW_XSAVE is not defined.
 */
void vos3_xsave_save_for_task(struct vos3_task *t);
void vos3_xsave_restore_for_task(struct vos3_task *t);

/* ----- Self-test ----- */
int vos3_xsave_self_test(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_INCLUDE_VOS_XSAVE_H */
