/**
 * @file smp.h
 * @brief VOS3 Symmetric Multiprocessing (SMP) Support
 *
 * @details Phase 23 - AP initialization, LAPIC management, and IPI support.
 *          Enables waking Application Processors and coordinating multi-core
 *          operation.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_SMP_H
#define VOS3_SMP_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "cpu.h"   /* canonical EFER flags — VOS3_EFER_LME/LMA/NXE, VOS3_MSR_EFER */

/* ============================================================================
 * SMP CONSTANTS
 * ============================================================================ */

/** @brief Trampoline location in low memory (must be below 1MB) */
#define VOS3_SMP_TRAMPOLINE_ADDR    ((uint64_t)0x1000U)

/** @brief Boot parameters location (just below trampoline end) */
#define VOS3_SMP_BOOT_PARAMS_ADDR   ((uint64_t)0x1F00U)

/** @brief Maximum supported CPUs */
#define VOS3_SMP_MAX_CPUS           256U

/** @brief AP startup timeout in milliseconds (shorter for QEMU) */
#define VOS3_SMP_STARTUP_TIMEOUT_MS 20U

/** @brief Delay after INIT IPI in microseconds */
#define VOS3_SMP_INIT_DELAY_US      10000U

/** @brief Delay between SIPI attempts in microseconds */
#define VOS3_SMP_SIPI_DELAY_US      200U

/* ============================================================================
 * LAPIC REGISTER OFFSETS
 * ============================================================================ */

/** @brief LAPIC Base Address MSR */
#define VOS3_LAPIC_BASE_MSR         ((uint32_t)0x1BU)

/** @brief LAPIC Register Offsets (from base address) */
#define VOS3_LAPIC_ID               ((uint32_t)0x020U)
#define VOS3_LAPIC_VERSION          ((uint32_t)0x030U)
#define VOS3_LAPIC_TPR              ((uint32_t)0x080U)
#define VOS3_LAPIC_APR              ((uint32_t)0x090U)
#define VOS3_LAPIC_PPR              ((uint32_t)0x0A0U)
#define VOS3_LAPIC_EOI              ((uint32_t)0x0B0U)
#define VOS3_LAPIC_RRD              ((uint32_t)0x0C0U)
#define VOS3_LAPIC_LDR              ((uint32_t)0x0D0U)
#define VOS3_LAPIC_DFR              ((uint32_t)0x0E0U)
#define VOS3_LAPIC_SPURIOUS         ((uint32_t)0x0F0U)
#define VOS3_LAPIC_ISR_BASE         ((uint32_t)0x100U)
#define VOS3_LAPIC_TMR_BASE         ((uint32_t)0x180U)
#define VOS3_LAPIC_IRR_BASE         ((uint32_t)0x200U)
#define VOS3_LAPIC_ESR              ((uint32_t)0x280U)
#define VOS3_LAPIC_ICR_LOW          ((uint32_t)0x300U)
#define VOS3_LAPIC_ICR_HIGH         ((uint32_t)0x310U)
#define VOS3_LAPIC_TIMER_LVT        ((uint32_t)0x320U)
#define VOS3_LAPIC_THERMAL_LVT      ((uint32_t)0x330U)
#define VOS3_LAPIC_PERF_LVT         ((uint32_t)0x340U)
#define VOS3_LAPIC_LINT0_LVT        ((uint32_t)0x350U)
#define VOS3_LAPIC_LINT1_LVT        ((uint32_t)0x360U)
#define VOS3_LAPIC_ERROR_LVT        ((uint32_t)0x370U)
#define VOS3_LAPIC_TIMER_INIT       ((uint32_t)0x380U)
#define VOS3_LAPIC_TIMER_CURRENT    ((uint32_t)0x390U)
#define VOS3_LAPIC_TIMER_DIV        ((uint32_t)0x3E0U)

/* ============================================================================
 * ICR (Interrupt Command Register) FIELDS
 * ============================================================================ */

/** @brief ICR Delivery Modes */
#define VOS3_ICR_FIXED              ((uint32_t)0x00000000U)
#define VOS3_ICR_LOWEST             ((uint32_t)0x00000100U)
#define VOS3_ICR_SMI                ((uint32_t)0x00000200U)
#define VOS3_ICR_NMI                ((uint32_t)0x00000400U)
#define VOS3_ICR_INIT               ((uint32_t)0x00000500U)
#define VOS3_ICR_STARTUP            ((uint32_t)0x00000600U)

/** @brief ICR Destination Modes */
#define VOS3_ICR_PHYSICAL           ((uint32_t)0x00000000U)
#define VOS3_ICR_LOGICAL            ((uint32_t)0x00000800U)

/** @brief ICR Delivery Status */
#define VOS3_ICR_IDLE               ((uint32_t)0x00000000U)
#define VOS3_ICR_PENDING            ((uint32_t)0x00001000U)

/** @brief ICR Level */
#define VOS3_ICR_DEASSERT           ((uint32_t)0x00000000U)
#define VOS3_ICR_ASSERT             ((uint32_t)0x00004000U)

/** @brief ICR Trigger Mode */
#define VOS3_ICR_EDGE               ((uint32_t)0x00000000U)
#define VOS3_ICR_LEVEL              ((uint32_t)0x00008000U)

/** @brief ICR Destination Shorthand */
#define VOS3_ICR_NO_SHORTHAND       ((uint32_t)0x00000000U)
#define VOS3_ICR_SELF               ((uint32_t)0x00040000U)
#define VOS3_ICR_ALL_INCL_SELF      ((uint32_t)0x00080000U)
#define VOS3_ICR_ALL_EXCL_SELF      ((uint32_t)0x000C0000U)

/* VOS3_MSR_EFER, VOS3_EFER_LME/LMA/NXE — see cpu.h (included above) */

/* ============================================================================
 * SMP CPU FLAGS
 * ============================================================================ */

#define VOS3_SMP_CPU_PRESENT        ((uint32_t)0x0001U)
#define VOS3_SMP_CPU_BSP            ((uint32_t)0x0002U)
#define VOS3_SMP_CPU_ONLINE         ((uint32_t)0x0004U)
#define VOS3_SMP_CPU_ENABLED        ((uint32_t)0x0008U)

/* ============================================================================
 * SMP INFO STRUCTURE
 * ============================================================================ */

/**
 * @brief SMP CPU information structure
 */
typedef struct vos3_smp_cpu_info {
    uint32_t apic_id;               /**< Hardware APIC ID */
    uint32_t cpu_id;                /**< Logical CPU ID (0-255) */
    uint32_t flags;                 /**< CPU state flags */
    volatile uint32_t started;      /**< Set to 1 when AP is online */
} vos3_smp_cpu_info_t;

/**
 * @brief Boot parameters passed to AP via low memory
 */
typedef struct __attribute__((packed)) vos3_smp_boot_params {
    uint64_t pml4;                  /**< Page table root (CR3) */
    uint64_t stack;                 /**< AP kernel stack pointer */
    uint64_t entry;                 /**< C entry point address */
    uint32_t cpu_id;                /**< Logical CPU ID */
    uint32_t apic_id;               /**< Hardware APIC ID */
    volatile uint32_t ready;        /**< AP sets to 1 when ready */
    uint32_t reserved;              /**< Padding */
    uint64_t gdt_base;              /**< GDT base address */
    uint16_t gdt_limit;             /**< GDT limit */
    uint16_t reserved2;             /**< Padding */
    uint32_t reserved3;             /**< Padding */
} vos3_smp_boot_params_t;

/* ============================================================================
 * SMP API FUNCTIONS
 * ============================================================================ */

/**
 * @brief Initialize SMP subsystem and wake all Application Processors
 * @return 0 on success, negative error code on failure
 */
int vos3_smp_init(void);

/**
 * @brief Get total number of CPUs detected
 * @return Number of CPUs (including BSP)
 */
uint32_t vos3_smp_cpu_count(void);

/**
 * @brief Get number of online CPUs
 * @return Number of CPUs currently online
 */
uint32_t vos3_smp_online_count(void);

/**
 * @brief Check if current CPU is the Bootstrap Processor
 * @return 1 if BSP, 0 if AP
 */
int vos3_smp_is_bsp(void);

/**
 * @brief Get CPU info by logical ID
 * @param[in] cpu_id Logical CPU ID
 * @return Pointer to CPU info, or NULL if invalid
 */
const vos3_smp_cpu_info_t* vos3_smp_get_cpu_info(uint32_t cpu_id);

/* ============================================================================
 * LAPIC API FUNCTIONS
 * ============================================================================ */

/**
 * @brief Initialize the Local APIC for current CPU
 */
void vos3_lapic_init(void);

/**
 * @brief Send End-of-Interrupt to LAPIC
 */
void vos3_lapic_eoi(void);

/**
 * @brief Get current CPU's LAPIC ID
 * @return APIC ID
 */
uint32_t vos3_lapic_id(void);

/**
 * @brief Read LAPIC register
 * @param[in] reg Register offset
 * @return Register value
 */
uint32_t vos3_lapic_read(uint32_t reg);

/**
 * @brief Write LAPIC register
 * @param[in] reg Register offset
 * @param[in] value Value to write
 */
void vos3_lapic_write(uint32_t reg, uint32_t value);

/**
 * @brief Send Inter-Processor Interrupt
 * @param[in] apic_id Destination APIC ID
 * @param[in] vector Interrupt vector
 */
void vos3_lapic_send_ipi(uint32_t apic_id, uint32_t vector);

/**
 * @brief Send INIT IPI to specified AP
 * @param[in] apic_id Destination APIC ID
 */
void vos3_lapic_send_init(uint32_t apic_id);

/**
 * @brief Send Startup IPI to specified AP
 * @param[in] apic_id Destination APIC ID
 * @param[in] vector Startup vector (page number, e.g., 0x01 for 0x1000)
 */
void vos3_lapic_send_sipi(uint32_t apic_id, uint8_t vector);

/* ============================================================================
 * TRAMPOLINE SYMBOLS (defined in trampoline.S)
 * ============================================================================ */

extern char vos3_trampoline_start[];
extern char vos3_trampoline_end[];

/* ============================================================================
 * AP ENTRY POINT (called from trampoline)
 * ============================================================================ */

/**
 * @brief Application Processor C entry point
 * @param[in] cpu_id Logical CPU ID
 * @param[in] apic_id Hardware APIC ID
 * @note This function does not return
 */
__attribute__((noreturn))
void vos3_ap_entry(uint32_t cpu_id, uint32_t apic_id);

/**
 * @brief Send IPI using destination shorthand (no per-CPU loop)
 * @param[in] vector Interrupt vector
 * @param[in] shorthand ICR destination shorthand value (use VOS3_ICR_* constants)
 */
void vos3_lapic_send_ipi_shorthand(uint8_t vector, uint32_t shorthand);

/* ============================================================================
 * SMP TELEMETRY API (Phase 25)
 * ============================================================================ */

/**
 * @brief Initialize SMP telemetry device (/dev/smp)
 * @return 0 on success, negative error code on failure
 */
int vos3_smp_telemetry_init(void);

/**
 * @brief Update CPU tick counters (called from scheduler)
 * @param[in] cpu_id Logical CPU ID
 * @param[in] is_idle 1 if CPU is idle, 0 if busy
 */
void vos3_smp_telemetry_tick(uint32_t cpu_id, int is_idle);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_SMP_H */
