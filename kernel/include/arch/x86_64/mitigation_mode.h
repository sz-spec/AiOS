/**
 * @file mitigation_mode.h
 * @brief VOS3 Adaptive Mitigation Mode — runtime KPTI/PCID tier selection
 *
 * vOS·Adaptive·SHA=aeb3736·Phase=P1
 *
 * Picks the strongest Meltdown / transient-execution mitigation the host
 * silicon can support at boot time, and latches the choice read-only for
 * the lifetime of the kernel. The latched value drives:
 *   - which CR3-swap variant entry_64.S jumps to on syscall/IRQ entry
 *   - which sandbox tier the Universal Hardware Manifest reports
 *   - whether `tests/fortification_v5_scale/test_vbus_throughput.py`
 *     budget is ≤2% or whether the operator accepted a documented hit
 *
 * Honest-scope notes (recorded so the audit trail is honest):
 *   - PROTECTED_FULL: requires PCID+INVPCID+SMEP+SMAP. Per Linux KPTI docs
 *     this gives ~0.5–2% syscall-heavy regression (kernel.org x86/pti.rst).
 *   - PROTECTED_PCID_ONLY: PCID but no SMEP/SMAP. Software-enforces SMEP-
 *     equivalent in entry stubs. Budget ≤3%.
 *   - LEGACY_KAISER: PCID absent → full TLB flush per syscall. This is the
 *     original 2018 Meltdown patch. Kernel docs quote 5–30% regression.
 *     Boots with an explicit operator-visible serial line so nothing is
 *     hidden. Audit row kind="mitigation_mode" reason="legacy_kaiser".
 *   - REFUSE_32BIT: no x86_64 long-mode → kernel panics in early boot
 *     with VOS3_BOOT_REFUSE message. Pre-2003 silicon, sovereign-support
 *     window starts at x86_64.
 */

#ifndef VOS3_ARCH_X86_64_MITIGATION_MODE_H
#define VOS3_ARCH_X86_64_MITIGATION_MODE_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include "arch/x86_64/cpu.h"

typedef enum vos3_mitigation_mode {
    VOS3_MIT_UNINITIALIZED   = 0,  /**< before vos3_mitigation_factory_init() */
    VOS3_MIT_PROTECTED_FULL  = 1,  /**< PCID+INVPCID+SMEP+SMAP — Ivy Bridge+ / Zen+ */
    VOS3_MIT_PROTECTED_PCID  = 2,  /**< PCID present, SMEP/SMAP absent — Westmere/Sandy */
    VOS3_MIT_LEGACY_KAISER   = 3,  /**< x86_64 but no PCID — Nehalem/Phenom II */
    VOS3_MIT_REFUSE_32BIT    = 4   /**< no long-mode — refuse to boot */
} vos3_mitigation_mode_t;

/**
 * @brief Select the strongest mitigation tier the CPU can support.
 *
 * Called exactly once during early boot, *after* vos3_cpu_detect() has
 * populated info. Latches the chosen mode into a static; subsequent reads
 * via vos3_get_mitigation_mode() return the latched value.
 *
 * If !LM (no long-mode), this function does NOT return: it emits the
 * VOS3_BOOT_REFUSE message to the serial port and halts.
 *
 * Tier derating: the selection AND's together PCID + INVPCID + SMEP +
 * SMAP for PROTECTED_FULL. If any one is missing but PCID is present,
 * we land in PROTECTED_PCID (the next-strongest tier). This is the
 * standard "select strongest supported tier" pattern — it is not an
 * anti-spoofing defense, since the same CPUID register is the only
 * input we trust either way. A CPU that lies about all four bits
 * would still land in PROTECTED_FULL and the entry stubs would have
 * to detect the lie via runtime behavior (a future hardening item).
 *
 * Degrade-on-NULL-info: if `info` is NULL, this function logs a very
 * loud kernel-bug banner and latches LEGACY_KAISER so the system can
 * still boot. The trade-off (graceful boot vs. strict fail-closed) is
 * the user-approved policy for this file — see comment block on the
 * NULL branch in the .c file.
 *
 * @param[in] info  populated CPU info from vos3_cpu_detect()
 * @return latched mode, or panics (does not return) on !LM
 */
vos3_mitigation_mode_t vos3_mitigation_factory_init(const vos3_cpu_info_t* info);

/**
 * @brief Read the latched mode. Returns VOS3_MIT_UNINITIALIZED if init
 *        has not yet been called — callers MUST treat that as a bug.
 */
vos3_mitigation_mode_t vos3_get_mitigation_mode(void);

/**
 * @brief Human-readable name for the mode (boot serial line).
 */
const char* vos3_mitigation_mode_name(vos3_mitigation_mode_t mode);

/**
 * @brief One-line operator-visible summary printed to the serial port
 *        immediately after init. Format:
 *          [KPTI] mode=<NAME> pcid=<y/n> invpcid=<y/n> smep=<y/n>
 *                 smap=<y/n> sha-ni=<y/n> budget=<≤2%|≤3%|5-30%>
 */
void vos3_mitigation_print_summary(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ARCH_X86_64_MITIGATION_MODE_H */
