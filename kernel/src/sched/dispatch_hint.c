/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 Dispatch Hint — Hybrid hardware-class detector
 * ====================================================
 *
 *   [QUANTUM-LEAP-SCAFFOLD]  Operation HOME-DOMINANCE v21.0  2026-05-02
 *
 * One-shot detector that classifies the host as a CPU-heavy PC, an
 * NPU-heavy NUT, or a hybrid. The Two-Plane dispatcher (future) uses
 * this to choose between latency-first and throughput-first defaults.
 *
 * Today's heuristic (intentionally minimal):
 *   - If the NPU subsystem reports any registered devices  → NUT
 *   - Else if CPUID feature bits report large vector units → PC
 *     (placeholder — assumes any modern x86_64 is "PC")
 *   - Else                                                  → UNKNOWN
 *
 * v21.x will replace this with: ACPI DSAR cluster count, NPU TOPS
 * estimate, and a tiny sample workload to compare CPU vs NPU latency
 * on a representative AI op. Until then this file exists so the
 * header API is callable without breaking the build.
 */

#include "../../include/vos/dispatch_hint.h"
#include "../../include/vos/console.h"
#include <stdint.h>

/* Forward decl — npu.c may or may not be linked. The weak attribute
 * lets the kernel link without a real NPU driver present. The actual
 * symbol exposed by kernel/src/drivers/npu.c is vos3_npu_device_count. */
__attribute__((weak)) extern uint32_t vos3_npu_device_count(void);

static volatile uint32_t g_hw_class_cached = (uint32_t)VOS3_HW_UNKNOWN;
static volatile uint8_t  g_hw_class_done   = 0u;

vos3_hardware_class_t vos3_hardware_class_detect(void)
{
    if (__atomic_load_n(&g_hw_class_done, __ATOMIC_ACQUIRE)) {
        return (vos3_hardware_class_t)__atomic_load_n(&g_hw_class_cached,
                                                      __ATOMIC_RELAXED);
    }

    vos3_hardware_class_t cls = VOS3_HW_PC;

    /* If npu.c was linked AND reports devices, mark this as a NUT. The
     * weak symbol decays to NULL on builds without the NPU driver. */
    if (&vos3_npu_device_count != ((void *)0)) {
        uint32_t n = vos3_npu_device_count();
        if (n > 0u) {
            cls = VOS3_HW_NUT;
            VOS3_INFO("[DISPATCH] hardware class: NUT (NPU count=%u)",
                      (unsigned)n);
        }
    }
    if (cls == VOS3_HW_PC) {
        VOS3_INFO("[DISPATCH] hardware class: PC (no NPU detected)");
    }

    __atomic_store_n(&g_hw_class_cached, (uint32_t)cls, __ATOMIC_RELAXED);
    __atomic_store_n(&g_hw_class_done,   1u,            __ATOMIC_RELEASE);
    return cls;
}

vos3_hardware_class_t vos3_hardware_class_get(void)
{
    return (vos3_hardware_class_t)__atomic_load_n(&g_hw_class_cached,
                                                  __ATOMIC_RELAXED);
}
