/**
 * @file cet.h
 * @brief VOS3 Control-flow Enforcement Technology (CET) Support
 *
 * @details Provides Indirect Branch Tracking (IBT) via ENDBR64, and
 *          Shadow Stack (SHSTK) infrastructure gated behind CPUID.
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_CET_H
#define VOS3_CET_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CET CPUID BITS
 * ============================================================================ */

/** @brief CPUID leaf 7, ECX bit 7: Shadow Stack support */
#define VOS3_CET_CPUID_SHSTK       (1U << 7)

/** @brief CPUID leaf 7, EDX bit 20: IBT support */
#define VOS3_CET_CPUID_IBT         (1U << 20)

/* ============================================================================
 * CET CONTROL REGISTERS / MSRs
 * ============================================================================ */

/** @brief CR4 bit 23: CET enable */
#define VOS3_CR4_CET                (1ULL << 23)

/** @brief MSR_IA32_S_CET — Supervisor CET configuration */
#define VOS3_MSR_IA32_S_CET        0x6A2ULL

/** @brief MSR_IA32_PL0_SSP — Ring 0 Shadow Stack Pointer */
#define VOS3_MSR_IA32_PL0_SSP      0x6A4ULL

/** @brief S_CET bit 0: Shadow Stack Enable */
#define VOS3_S_CET_SHSTK_EN        (1ULL << 0)

/** @brief S_CET bit 2: ENDBR enforcement (IBT) */
#define VOS3_S_CET_ENDBR_EN        (1ULL << 2)

/* ============================================================================
 * CET STATE
 * ============================================================================ */

/** @brief CET capability flags (detected at boot) */
typedef struct vos3_cet_caps {
    int ibt_supported;          /**< 1 if IBT available */
    int shstk_supported;        /**< 1 if SHSTK available */
    int ibt_enabled;            /**< 1 if IBT active */
    int shstk_enabled;          /**< 1 if SHSTK active */
} vos3_cet_caps_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize CET subsystem (CPUID-gated)
 *
 * Detects IBT and SHSTK support via CPUID leaf 7.
 * If IBT supported: enables CR4.CET + MSR_IA32_S_CET.ENDBR_EN.
 * If not supported: logs and returns (safe no-op).
 */
void vos3_cet_init(void);

/**
 * @brief Get CET capability state
 * @return Pointer to global CET capabilities struct
 */
const vos3_cet_caps_t* vos3_cet_get_caps(void);

/**
 * @brief Allocate a shadow stack for a task
 * @param[in] size Size in bytes (0 = default 4 pages)
 * @return Virtual address of shadow stack top, or 0 on failure
 */
uint64_t vos3_cet_shstk_alloc(size_t size);

/**
 * @brief Free a shadow stack
 * @param[in] base Base address returned by alloc
 * @param[in] size Size used during allocation
 */
void vos3_cet_shstk_free(uint64_t base, size_t size);

#endif /* VOS3_CET_H */
