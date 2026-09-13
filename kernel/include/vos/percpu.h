/**
 * @file percpu.h
 * @brief VOS3 Per-CPU Data Structures
 *
 * @details Provides per-CPU data structures and accessor functions
 *          for SMP (Symmetric Multi-Processing) support.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 * @note Phase 22 - Per-CPU Infrastructure Foundation
 */

#ifndef VOS3_PERCPU_H
#define VOS3_PERCPU_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "../arch/x86_64/memory_map.h"

/* ============================================================================
 * CPU STATE FLAGS
 * ============================================================================ */

/*
 * Per-CPU runtime state flags.  Named VOS3_PCPU_* to avoid collision with
 * the boot-time CPU descriptor flags (VOS3_CPU_FLAG_*) in boot_info.h,
 * which have different bit values and different semantics.
 */

/** @brief CPU is online and running */
#define VOS3_PCPU_ONLINE            ((uint64_t)0x0001ULL)

/** @brief CPU is the Bootstrap Processor */
#define VOS3_PCPU_BSP               ((uint64_t)0x0002ULL)

/** @brief CPU is currently idle */
#define VOS3_PCPU_IDLE              ((uint64_t)0x0004ULL)

/** @brief CPU is handling an interrupt */
#define VOS3_PCPU_IN_IRQ            ((uint64_t)0x0008ULL)

/** @brief CPU is in a critical section */
#define VOS3_PCPU_CRITICAL          ((uint64_t)0x0010ULL)

/* ============================================================================
 * PER-CPU DATA STRUCTURE
 * ============================================================================ */

/* Forward declaration */
struct vos3_task;

/**
 * @brief Per-CPU data structure
 *
 * Contains CPU-local state that should not be shared between processors.
 * This structure is designed to fit within two cache lines (128 bytes).
 * Extended for enterprise multi-tenant isolation support.
 */
typedef struct vos3_cpu {
    /* === Cache Line 1: Hot Path === */
    uint32_t    id;             /**< Logical CPU ID (0 to VOS3_MAX_CPUS-1) */
    uint32_t    apic_id;        /**< Hardware APIC ID */
    struct vos3_task* current;  /**< Currently running task */
    struct vos3_task* idle;     /**< Idle task for this CPU */
    uint64_t    ticks;          /**< Per-CPU tick counter */
    uint64_t    flags;          /**< CPU state flags */
    uint64_t    irq_count;      /**< Number of IRQs handled */
    uint64_t    context_switches; /**< Number of context switches */

    /* === Cache Line 2: Multi-Tenant & Billing === */
    uint32_t    tenant_id;      /**< Current tenant ID (0 = kernel/root) */
    uint32_t    numa_node;      /**< NUMA node this CPU belongs to */
    uint64_t    tenant_cycles;  /**< CPU cycles consumed by current tenant */
    uint64_t    tenant_start;   /**< Timestamp when tenant started on CPU */
    uint64_t    total_runtime;  /**< Total accumulated runtime (billing) */
    uint64_t    _reserved[2];   /**< Reserved for future expansion */
} __attribute__((aligned(128))) vos3_cpu_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the per-CPU subsystem
 *
 * Must be called early during BSP boot before any CPU-local
 * data is accessed. Initializes BSP (CPU 0) data structure.
 */
void percpu_init(void);

/**
 * @brief Get the current CPU ID
 *
 * Reads the APIC ID via CPUID and maps it to a logical CPU ID.
 * This is a relatively expensive operation; cache the result
 * when possible.
 *
 * @return Logical CPU ID (0 for BSP, 1+ for APs)
 */
uint32_t get_cpu_id(void);

/**
 * @brief Get per-CPU data for the current CPU
 *
 * Returns a pointer to the cpu_t structure for the calling CPU.
 *
 * @return Pointer to current CPU's data structure
 */
vos3_cpu_t* get_cpu(void);

/**
 * @brief Get per-CPU data for a specific CPU
 *
 * @param[in] cpu_id  Logical CPU ID
 * @return Pointer to the specified CPU's data, or NULL if invalid
 */
vos3_cpu_t* get_cpu_by_id(uint32_t cpu_id);

/**
 * @brief Initialize an Application Processor's per-CPU data
 *
 * Called by each AP during its startup sequence.
 *
 * @param[in] cpu_id   Logical CPU ID for this AP
 * @param[in] apic_id  Hardware APIC ID for this AP
 * @return 0 on success, negative error code on failure
 */
int percpu_init_ap(uint32_t cpu_id, uint32_t apic_id);

/**
 * @brief Get the number of online CPUs
 *
 * @return Number of CPUs that have completed initialization
 */
uint32_t percpu_online_count(void);

/**
 * @brief Check if a CPU is online
 *
 * @param[in] cpu_id  Logical CPU ID to check
 * @return 1 if online, 0 if offline
 */
int percpu_is_online(uint32_t cpu_id);

/**
 * @brief Set the current task for the calling CPU
 *
 * @param[in] task  Pointer to the current task
 */
void percpu_set_current(struct vos3_task* task);

/**
 * @brief Get the current task for the calling CPU
 *
 * @return Pointer to the current task, or NULL if none
 */
struct vos3_task* percpu_get_current(void);

/**
 * @brief Increment the IRQ count for the calling CPU
 */
void percpu_inc_irq_count(void);

/**
 * @brief Increment the context switch count for the calling CPU
 */
void percpu_inc_context_switches(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_PERCPU_H */
