/**
 * @file ai_guard.h
 * @brief VOS3 AI Memory Guard Subsystem
 *
 * @details Memory protection and monitoring for AI workloads.
 *          Implements red zone guard pages and integrity verification.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_AI_GUARD_H
#define VOS3_AI_GUARD_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "atomic.h"
#include "model_registry.h"
#include "vmm.h"      /* VOS3_PTE_AI_MONITORED/PROTECTED/GUARD_PAGE/MASK live here now */
#include "vos3_sdk.h"  /* Task 4.4: canonical SQ/CQ/Doorbell struct definitions */
#include "ai_kv_managed.h" /* Phase 2.1: Managed KV Cache types */
#include "ai_spec.h"       /* Phase 2.2: Speculative Decoding types */

/* ============================================================================
 * AI GUARD PTE BITS — defined in vmm.h (Task 4.5: layering violation fix)
 * ============================================================================
 *
 * The AI PTE bit constants (VOS3_PTE_AI_MONITORED, VOS3_PTE_AI_PROTECTED,
 * VOS3_PTE_AI_GUARD_PAGE, VOS3_PTE_AI_MASK) are now canonical in vmm.h
 * and imported via the #include above. This eliminates the circular
 * dependency where vmm.c had to include ai_guard.h.
 */

/* ============================================================================
 * AI GUARD CONFIGURATION
 * ============================================================================ */

/** @brief Maximum per-app contexts for isolation (Phase N) */
#define VOS3_MAX_APP_CONTEXTS       ((uint8_t)8U)

/** @brief Maximum AI guard regions per task */
#define VOS3_AI_GUARD_MAX_REGIONS   ((size_t)64U)

/** @brief Guard page size (matches page size) */
#define VOS3_AI_GUARD_PAGE_SIZE     ((size_t)0x1000U)

/** @brief Default red zone size (one page on each side) */
#define VOS3_AI_RED_ZONE_SIZE       ((size_t)0x1000U)

/** @brief Checksum seed for integrity verification */
#define VOS3_AI_CHECKSUM_SEED       ((uint64_t)0xA15AFE00A2D50ULL)

/* ============================================================================
 * AI GUARD TYPES
 * ============================================================================ */

/**
 * @brief AI memory region types
 */
typedef enum vos3_ai_guard_type {
    VOS3_AI_GUARD_MODEL     = 0U,   /**< Neural network model weights (read-heavy) */
    VOS3_AI_GUARD_TENSOR    = 1U,   /**< Tensor computation buffers (read-write) */
    VOS3_AI_GUARD_SCRATCH   = 2U,   /**< Temporary scratch space (write-heavy) */
    VOS3_AI_GUARD_TYPE_COUNT = 3U
} vos3_ai_guard_type_t;

/**
 * @brief AI guard region flags
 */
typedef enum vos3_ai_guard_flags {
    VOS3_AI_FLAG_NONE           = 0U,
    VOS3_AI_FLAG_READ_ONLY      = (1U << 0),    /**< Region is read-only */
    VOS3_AI_FLAG_EXEC           = (1U << 1),    /**< Region is executable (rare) */
    VOS3_AI_FLAG_SHARED         = (1U << 2),    /**< Region can be shared */
    VOS3_AI_FLAG_LOCKED         = (1U << 3),    /**< Region is locked in memory */
    VOS3_AI_FLAG_CHECKSUMMED    = (1U << 4),    /**< Integrity checksums enabled */
    VOS3_AI_FLAG_RED_ZONES      = (1U << 5),    /**< Guard pages on boundaries */
    VOS3_AI_FLAG_MONITOR_ACCESS = (1U << 6),    /**< Monitor all accesses */
} vos3_ai_guard_flags_t;

/**
 * @brief Workload hint for memory-mapping policy (Sprint 15 / Item A1).
 *
 * Tells the AI guard which OS-level page-table policy to apply to a
 * region. Per the May-2026 industry survey of agent-era OS problems
 * (docs/AGENT_ERA_OS_PROBLEMS.md item A1), the kernel's default RSS
 * assumptions ("RSS > RAM ⇒ swap thrash") are wrong for AI workloads:
 * a 70-GB model weight file is supposed to be RSS-committed and TLB-
 * prefetched, not paged out.
 *
 * Three hints corresponding to the three workload classes documented
 * in arXiv:2508.00604 (Composable OS Kernel Architectures for
 * Autonomous Intelligence):
 *
 *   VOS3_AI_HINT_MODEL_WEIGHTS — long-lived, read-only, large-page-
 *     preferred. Pre-fault all pages on bind so RSS stabilizes.
 *     Disable swap eligibility.
 *
 *   VOS3_AI_HINT_KV_CACHE — short-to-medium-lived, RW, attention-
 *     hot. Keep small-page (4 KiB) so partial eviction is cheap. KV
 *     pages may swap if quota pressure rises.
 *
 *   VOS3_AI_HINT_SCRATCH — regular allocations (intermediate tensors,
 *     normalisation buffers). No special policy; the default RSS
 *     accounting applies.
 *
 * Called via:
 *   - userspace API: backend `services.vbus_driver.send_command("SLOT_MADVISE|...")`
 *   - kernel-side  : vos3_ai_guard_set_workload_hint(region, hint)
 *
 * Source: https://arxiv.org/pdf/2508.00604
 */
typedef enum vos3_ai_workload_hint {
    VOS3_AI_HINT_SCRATCH       = 0U,   /**< Default — no special policy */
    VOS3_AI_HINT_MODEL_WEIGHTS = 1U,   /**< Long-lived RO; pre-fault + large pages */
    VOS3_AI_HINT_KV_CACHE      = 2U,   /**< Short-lived RW; small pages, swap-eligible */
} vos3_ai_workload_hint_t;

/**
 * @brief AI guard region state
 */
typedef enum vos3_ai_guard_state {
    VOS3_AI_STATE_FREE      = 0U,   /**< Region slot is free */
    VOS3_AI_STATE_ACTIVE    = 1U,   /**< Region is active and monitored */
    VOS3_AI_STATE_SUSPENDED = 2U,   /**< Monitoring temporarily suspended */
    VOS3_AI_STATE_VIOLATED  = 3U,   /**< Integrity violation detected */
} vos3_ai_guard_state_t;

/* ============================================================================
 * PHASE 17.5 FORWARD DECLARATIONS AND TYPES (must be before region struct)
 * ============================================================================ */

/** @brief Forward declarations for circular references */
struct vos3_ai_guard_region;
struct vos3_ai_guard_ctx;
struct vos3_ai_shared_region;

/** @brief NUMA node type (Phase 17.5.2) */
typedef uint8_t vos3_numa_node_t;

/** @brief Maximum NUMA nodes supported */
#define VOS3_NUMA_MAX_NODES     8U

/** @brief Any NUMA node (no preference) */
#define VOS3_NUMA_NODE_ANY      ((vos3_numa_node_t)0xFFU)

/** @brief NUMA-local allocation flag */
#define VOS3_AI_FLAG_NUMA_LOCAL     (1U << 7)

/**
 * @brief Integrity automation configuration (Phase 17.5.1)
 */
typedef struct vos3_ai_integrity_config {
    uint32_t    verify_interval;     /**< Ticks between verifications (0=disabled) */
    uint32_t    auto_suspend;        /**< 1=suspend on violation */
    uint32_t    retry_count;         /**< Re-verify attempts before failure */
    uint32_t    _padding;            /**< 64-bit alignment */
} vos3_ai_integrity_config_t;

/**
 * @brief Swap hints for eviction policy (Phase 17.5.4)
 */
typedef enum vos3_ai_swap_hint {
    VOS3_AI_SWAP_ALLOWED    = 0U,           /**< Can be evicted */
    VOS3_AI_SWAP_PINNED     = (1U << 0),    /**< Never evict */
    VOS3_AI_SWAP_PREFERRED  = (1U << 1),    /**< Prefer to evict */
} vos3_ai_swap_hint_t;

/**
 * @brief Reclaim callback for custom eviction logic (Phase 17.5.4)
 */
typedef int (*vos3_ai_reclaim_callback_t)(struct vos3_ai_guard_region* region,
                                           size_t bytes_needed);

/* ============================================================================
 * AI GUARD STRUCTURES
 * ============================================================================ */

/**
 * @brief AI guard region statistics (extended for Phase 17.4)
 */
typedef struct vos3_ai_guard_stats {
    /* === Core Counters === */
    uint64_t    read_count;         /**< Number of read accesses */
    uint64_t    write_count;        /**< Number of write accesses */
    uint64_t    fault_count;        /**< Number of guard page faults */
    uint64_t    violation_count;    /**< Number of integrity violations */
    uint64_t    last_access_time;   /**< Timestamp of last access */
    uint64_t    last_write_time;    /**< Timestamp of last write */

    /* === Anomaly Tracking (Phase 17.4) === */
    uint64_t    anomaly_count;      /**< Number of anomalies detected */
    uint64_t    last_anomaly_time;  /**< Timestamp of last anomaly */

    /* === Rolling Window (Phase 17.4) === */
    uint64_t    window_accesses[4]; /**< Last 4 window access counts */
    uint32_t    window_index;       /**< Current window index */
    uint32_t    _padding;           /**< 64-bit alignment padding */
} vos3_ai_guard_stats_t;

/**
 * @brief AI guard region descriptor
 *
 * Tracks a protected memory region with optional red zones
 * and integrity verification.
 */
typedef struct vos3_ai_guard_region {
    /* ===== Identity ===== */
    uint32_t                id;             /**< Region ID */
    vos3_ai_guard_type_t    type;           /**< Region type (MODEL/TENSOR/SCRATCH) */
    vos3_ai_guard_state_t   state;          /**< Current state */
    vos3_ai_guard_flags_t   flags;          /**< Region flags */

    /* ===== Memory Range ===== */
    uintptr_t               base;           /**< Base virtual address */
    size_t                  size;           /**< Size in bytes (excluding guards) */
    uintptr_t               guard_lo;       /**< Lower red zone address */
    uintptr_t               guard_hi;       /**< Upper red zone address */

    /* ===== Integrity ===== */
    uint64_t                checksum;       /**< Content checksum */
    uint64_t                checksum_time;  /**< When checksum was computed */

    /* ===== Integrity Automation (Phase 17.5.1) ===== */
    vos3_ai_integrity_config_t integrity_config; /**< Auto-verify config */
    uint64_t                last_verify_tick;   /**< Last verification time */
    uint64_t                next_verify_tick;   /**< Next scheduled verification */

    /* ===== NUMA (Phase 17.5.2) ===== */
    vos3_numa_node_t        numa_node;      /**< NUMA node allocation */

    /* ===== Shared Region (Phase 17.5.3) ===== */
    struct vos3_ai_shared_region* shared;   /**< Shared region (if any) */
    uint32_t                is_owner;       /**< 1=owner of shared region */

    /* ===== Memory Pressure (Phase 17.5.4) ===== */
    vos3_ai_swap_hint_t     swap_hint;      /**< Eviction hint */
    vos3_ai_reclaim_callback_t reclaim_cb;  /**< Custom reclaim callback */
    uint64_t                last_active_tick; /**< LRU tracking */

    /* ===== Monitor Reprotect (Phase F) ===== */
    uint32_t                needs_reprotect; /**< 1=page was temporarily made writable, re-protect on tick */

    /* ===== Statistics ===== */
    vos3_ai_guard_stats_t   stats;          /**< Access statistics */

    /* ===== Workload Hint (Sprint 15 / Item A1) ===== */
    vos3_ai_workload_hint_t workload_hint;  /**< Page-policy hint: SCRATCH / MODEL_WEIGHTS / KV_CACHE */
    uint32_t                hint_pinned_rss;/**< 1 if MODEL_WEIGHTS hint pre-faulted all pages */

    /* ===== Linked List ===== */
    struct vos3_ai_guard_region* next;      /**< Next region in context */
} vos3_ai_guard_region_t;

/**
 * @brief AI guard context for a task
 *
 * Each task with AI workloads has one context containing
 * all its protected regions.
 */
typedef struct vos3_ai_guard_ctx {
    /* ===== Lock ===== */
    uint64_t                lock;           /**< Spinlock for thread safety */

    /* Registry lock protects lifetime, distinct from region mutation. */
    uint32_t                lifetime_refs;  /**< One owner plus acquired pins */
    uint32_t                closing;        /**< Detached; rejects new readers */
    struct vos3_ai_guard_ctx* retired_next; /**< Zero-ref safe-point queue */

    /* ===== Regions ===== */
    vos3_ai_guard_region_t* regions;        /**< Head of regions list */
    size_t                  region_count;   /**< Number of active regions */

    /* ===== Global Statistics ===== */
    uint64_t                total_protected;/**< Total bytes protected */
    uint64_t                total_faults;   /**< Total guard faults */
    uint64_t                total_violations; /**< Total violations */

    /* ===== Configuration ===== */
    uint32_t                flags;          /**< Context-level flags */
    uint32_t                max_regions;    /**< Maximum allowed regions */

    /* ===== Quota (Phase 17.4) ===== */
    uint64_t                quota_limit;    /**< Max bytes (0=unlimited) */
    uint64_t                quota_used;     /**< Current usage in bytes */
    uint32_t                quota_enforce;  /**< 1=hard limit, 0=warn only */
    uint32_t                quota_violations; /**< Number of quota exceeded */
} vos3_ai_guard_ctx_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the AI guard subsystem
 * @return 0 on success, negative error code on failure
 */
int vos3_ai_guard_init(void);

/**
 * @brief Create an AI guard context for a task
 * @return Context pointer, or NULL on failure
 */
vos3_ai_guard_ctx_t* vos3_ai_guard_ctx_create(void);

/**
 * @brief Close a creator-owned context and consume its owner reference
 * Publication is detached immediately; acquired readers keep storage alive.
 * Caller must own a valid reference. Final reclamation is deferred.
 */
void vos3_ai_guard_ctx_destroy(vos3_ai_guard_ctx_t* ctx);

/** Registry lookup and retain are atomic. Every success requires ctx_put.
 * A pin protects context destruction, not concurrent region removal or
 * mutation. Region APIs still require caller serialization. The caller must
 * reach ctx_put: asynchronous task cancellation does not unwind these pins. */
vos3_ai_guard_ctx_t* vos3_ai_guard_acquire_global_ctx(void);
vos3_ai_guard_ctx_t* vos3_ai_guard_acquire_app_ctx(uint8_t app_id);
void vos3_ai_guard_ctx_put(vos3_ai_guard_ctx_t* ctx);

/** Final reclamation requires a task continuation that completes, with IRQs
 * enabled. No current task or IRQ-off is a no-op; IF alone is not an ISR test.
 * Asynchronous cancellation of a reclamation continuation is not supported. */
void vos3_ai_guard_reap_contexts(void);
#ifdef AI_CONTEXT_LIFETIME_TEST
uint64_t vos3_ai_guard_test_finalized_contexts(void);
#endif

/**
 * @brief Allocate AI-protected memory with guard pages
 * @param[in] ctx Guard context
 * @param[in] size Size in bytes (will be page-aligned)
 * @param[in] type Region type (MODEL/TENSOR/SCRATCH)
 * @param[in] flags Region flags
 * @return Virtual address of allocated region, or NULL on failure
 */
void* vos3_ai_guard_alloc(vos3_ai_guard_ctx_t* ctx,
                          size_t size,
                          vos3_ai_guard_type_t type,
                          vos3_ai_guard_flags_t flags);

/**
 * @brief Free AI-protected memory
 * @param[in] ctx Guard context
 * @param[in] addr Address returned by vos3_ai_guard_alloc
 */
void vos3_ai_guard_free(vos3_ai_guard_ctx_t* ctx, void* addr);

/**
 * @brief Find region containing an address
 * @param[in] ctx Guard context
 * @param[in] addr Address to search for
 * @return Region pointer, or NULL if not found
 */
vos3_ai_guard_region_t* vos3_ai_guard_find_region(vos3_ai_guard_ctx_t* ctx,
                                                   uintptr_t addr);

/**
 * @brief Check if address is in a guard page
 * @param[in] ctx Guard context
 * @param[in] addr Address to check
 * @return 1 if in guard page, 0 otherwise
 */
int vos3_ai_guard_is_guard_page(vos3_ai_guard_ctx_t* ctx, uintptr_t addr);

/**
 * @brief Compute checksum for a region
 * @param[in] region Region to checksum
 * @return Computed checksum
 */
uint64_t vos3_ai_guard_compute_checksum(vos3_ai_guard_region_t* region);

/**
 * @brief Verify region integrity
 * @param[in] region Region to verify
 * @return 0 if integrity OK, -1 if violated
 */
int vos3_ai_guard_verify_integrity(vos3_ai_guard_region_t* region);

/**
 * @brief Handle page fault in AI guard region
 * @param[in] ctx Guard context
 * @param[in] fault_addr Faulting address
 * @param[in] error_code Page fault error code
 * @return 0 if handled, -1 if unhandled
 */
int vos3_ai_guard_handle_fault(vos3_ai_guard_ctx_t* ctx,
                                uintptr_t fault_addr,
                                uint64_t error_code);

/**
 * @brief Set protection flags for a region
 * @param[in] region Region to modify
 * @param[in] flags New flags
 * @return 0 on success, negative error code on failure
 */
int vos3_ai_guard_set_protection(vos3_ai_guard_region_t* region,
                                  vos3_ai_guard_flags_t flags);

/**
 * @brief Get statistics for a region
 * @param[in] region Region to query
 * @param[out] stats Statistics output
 * @return 0 on success, negative error code on failure
 */
int vos3_ai_guard_get_stats(vos3_ai_guard_region_t* region,
                             vos3_ai_guard_stats_t* stats);

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_AI_GUARD_OK            (0)
#define VOS3_AI_GUARD_ERR_NOMEM     (-1)
#define VOS3_AI_GUARD_ERR_INVALID   (-2)
#define VOS3_AI_GUARD_ERR_NOTFOUND  (-3)
#define VOS3_AI_GUARD_ERR_LIMIT     (-4)
#define VOS3_AI_GUARD_ERR_VIOLATED  (-5)
#define VOS3_AI_GUARD_ERR_PERM      (-6)

/* Phase 4.2.15: Alignment violation — offset not 64-byte aligned */
#define VOS3_EALIGN  515

/* Phase 4.2.15: Global Neural Sync — fixed VA for shared read-only TSC page */
#define VOS3_HEARTBEAT_PAGE_VADDR  0xFFFFFFFFFFFFF000ULL

/* Phase 4.2.16: Backpressure flag — heartbeat page offset 8, bit 0 */
#define VOS3_VBUS_FLAG_BUSY  0x01

/* ============================================================================
 * PHASE 4.4: UNIVERSAL SHARD — Completion Ring in Heartbeat Page
 * ============================================================================ */

/** @brief Completion ring starts at heartbeat page offset 16 */
#define VOS3_CRING_OFFSET       16U

/** @brief Number of completion ring entries (fits in 4KB page with header) */
#define VOS3_CRING_ENTRIES      62U

/* Task 4.4: CQ types — canonical definitions in vos3_sdk.h, backward-compat typedefs here */
typedef vos3_sdk_cring_entry_t   vos3_cring_entry_t;
typedef vos3_sdk_cring_header_t  vos3_cring_header_t;

/* ============================================================================
 * PHASE 4.6: INFINITY LOOP — Submission Queue in Heartbeat Page
 * ============================================================================ */

/** @brief Sentinel identifier for SQ entry validation */
#define VOS3_SNTL               0x534E544CU  /**< "SNTL" in ASCII (LE) */

/** @brief SQ starts at heartbeat page offset 1024 (3072 bytes available) */
#define VOS3_SQ_OFFSET          1024U

/** @brief SQ entry count — 31 entries fit in offset 1024–2031 (31×32+16=1008) */
#define VOS3_SQ_ENTRIES         31U

/** @brief SQ command types */
#define VOS3_SQ_CMD_DATA        0x01U   /**< Bulk DATA write (memcpy to HugePage) */
#define VOS3_SQ_CMD_SYNC        0x02U   /**< Sync request (report offset) */
#define VOS3_SQ_CMD_FINISH      0x03U   /**< Finalize slot loading */
#define VOS3_SQ_CMD_WARP_DATA   0x04U   /**< Zero-copy ivshmem warp read (Phase 4.9b) */
#define VOS3_SQ_CMD_NOP         0x00U   /**< No operation (padding/sentinel check) */

/* Task 4.4: SQ types — canonical definitions in vos3_sdk.h, backward-compat typedefs here */
typedef vos3_sdk_sq_entry_t   vos3_sq_entry_t;
typedef vos3_sdk_sq_header_t  vos3_sq_header_t;

/** @brief SQ poll — process pending SQ entries on Core 0 (non-blocking) */
uint32_t  vos3_sq_poll(void);

/** @brief SQ post — add entry to submission queue */
int       vos3_sq_post(uint8_t slot_id, uint8_t cmd, uint16_t length,
                        uint64_t offset, uint64_t tag);

/** @brief SQ post with payload_addr — for WARP_DATA zero-copy (Phase 4.9b) */
int       vos3_sq_post_warp(uint8_t slot_id, uint8_t cmd, uint16_t length,
                             uint64_t offset, uint64_t tag, uint64_t payload_addr);

/** @brief SQ init — initialize submission queue at heartbeat+1024 */
void      vos3_sq_init(void);

/** @brief SQ status — return head and tail for diagnostics */
void      vos3_sq_status(uint32_t *head_out, uint32_t *tail_out);

/** @brief SQ security stats — return processed count and sentinel rejection count */
void      vos3_sq_security_stats(uint64_t *processed_out, uint64_t *sentinel_rejects_out);

/**
 * @brief Kinetic Fill Work — productive jitter-work replacing dead-wait.
 *
 * @details Burns `target_cycles` TSC cycles by calling
 *          vos3_ivshmem_cold_scrub_partial() (16 lines per call) for each
 *          active slot. Converts dead CPU time into background warp zone
 *          sanitization. Returns the total cachelines scrubbed.
 *
 * @param[in] target_cycles TSC cycles to fill with productive work
 * @return Total cachelines scrubbed across all slots
 */
uint32_t  vos3_kinetic_fill_work(uint64_t target_cycles);

/**
 * @brief AVX2 Vectorized Sentinel Validation — validate up to 8 SQ entries.
 *
 * @details Uses AVX2 vpcmpeqd to compare 8 sentinel values simultaneously.
 *          Returns a bitmask where bit N is set if entry N passed validation.
 *          Falls back to scalar comparison if AVX2 is not available.
 *
 * @param[in] start_idx Ring buffer starting index
 * @param[in] count     Number of entries to validate (1-8)
 * @param[in] salt      Current entropy salt for sentinel computation
 * @return Bitmask of valid entries (bit 0 = first entry)
 */
uint8_t   vos3_sq_validate_vectorized(uint32_t start_idx, uint32_t count, uint32_t salt);

/* ============================================================================
 * PHASE 4.7: HYPERSCALE PROTOCOL — Atomic Doorbell Matrix
 * ============================================================================ */

/** @brief Doorbell matrix starts at heartbeat page offset 2048 */
#define VOS3_DOORBELL_OFFSET    2048U

/* Task 4.4: Doorbell type — canonical definition in vos3_sdk.h, backward-compat typedef */
typedef vos3_sdk_doorbell_t  vos3_doorbell_matrix_t;

void      vos3_doorbell_init(void);
void      vos3_doorbell_ring(uint8_t slot_id);
void      vos3_doorbell_clear(uint8_t slot_id);
uint32_t  vos3_doorbell_check(void);
uint32_t  vos3_doorbell_drain(void);

/* PHASE 4.7: NMI Watchdog — streaming stall detection */
#define VOS3_NMI_WATCHDOG_TIMEOUT_TICKS  500U
#define VOS3_EVENT_PREEMPTIVE_RESET      12U
void vos3_nmi_watchdog_tick(uint64_t current_tick);

/* ============================================================================
 * INLINE HELPERS
 * ============================================================================ */

/**
 * @brief Get region type name
 */
static inline const char* vos3_ai_guard_type_name(vos3_ai_guard_type_t type)
{
    static const char* names[] = { "MODEL", "TENSOR", "SCRATCH" };
    return (type < VOS3_AI_GUARD_TYPE_COUNT) ? names[type] : "UNKNOWN";
}

/**
 * @brief Get region state name
 */
static inline const char* vos3_ai_guard_state_name(vos3_ai_guard_state_t state)
{
    static const char* names[] = { "FREE", "ACTIVE", "SUSPENDED", "VIOLATED" };
    return (state <= VOS3_AI_STATE_VIOLATED) ? names[state] : "UNKNOWN";
}

/**
 * @brief Check if region has red zones
 */
static inline int vos3_ai_guard_has_red_zones(const vos3_ai_guard_region_t* region)
{
    return (region->flags & VOS3_AI_FLAG_RED_ZONES) != 0U ? 1 : 0;
}

/**
 * @brief Check if region is read-only
 */
static inline int vos3_ai_guard_is_read_only(const vos3_ai_guard_region_t* region)
{
    return (region->flags & VOS3_AI_FLAG_READ_ONLY) != 0U ? 1 : 0;
}

/* ============================================================================
 * PHASE 17.5.3: SHARED AI REGIONS
 * ============================================================================ */

/** @brief Maximum tasks sharing a region */
#define VOS3_AI_SHARED_MAX_TASKS    16U

/** @brief Process ID type */
typedef uint32_t vos3_pid_t;

/**
 * @brief Shared region reference
 */
typedef struct vos3_ai_shared_ref {
    vos3_pid_t              pid;            /**< Process ID */
    struct vos3_ai_guard_ctx* ctx;          /**< Guard context */
    uint64_t                map_time;       /**< When mapped */
    uint32_t                access_flags;   /**< Access permissions */
    uint32_t                _padding;       /**< Alignment */
} vos3_ai_shared_ref_t;

/**
 * @brief Shared AI region (multi-task)
 */
typedef struct vos3_ai_shared_region {
    uint32_t                region_id;      /**< Original region ID */
    uint32_t                ref_count;      /**< Reference count */
    uint32_t                max_refs;       /**< Maximum references */
    uint32_t                flags;          /**< Shared flags */
    vos3_ai_shared_ref_t    refs[VOS3_AI_SHARED_MAX_TASKS];
    uint64_t                total_accesses; /**< Total accesses */
    uint64_t                lock;           /**< Spinlock */
    uintptr_t               base;           /**< Base address */
    size_t                  size;           /**< Region size */
    uint8_t                 type;           /**< Region type */
    uint8_t                 numa_node;      /**< NUMA node */
    uint8_t                 _pad[6];        /**< Alignment padding */
    struct vos3_ai_shared_region* next;     /**< Linked list */
} vos3_ai_shared_region_t;

/* ============================================================================
 * PHASE 17.5.4: MEMORY PRESSURE
 * ============================================================================ */

/**
 * @brief Memory pressure levels
 */
typedef enum vos3_ai_pressure_level {
    VOS3_AI_PRESSURE_NONE       = 0U,
    VOS3_AI_PRESSURE_LOW        = 1U,
    VOS3_AI_PRESSURE_MEDIUM     = 2U,
    VOS3_AI_PRESSURE_HIGH       = 3U,
    VOS3_AI_PRESSURE_CRITICAL   = 4U,
} vos3_ai_pressure_level_t;

/**
 * @brief Memory pressure configuration
 */
typedef struct vos3_ai_pressure_config {
    uint64_t    soft_threshold;         /**< Soft pressure threshold (bytes free) */
    uint64_t    warning_threshold;      /**< Warning threshold */
    uint64_t    critical_threshold;     /**< Critical threshold */
    uint32_t    check_interval;         /**< Ticks between checks */
    uint32_t    evict_scratch_first;    /**< 1=evict SCRATCH first */
} vos3_ai_pressure_config_t;

/* ============================================================================
 * PHASE 17.5.5: TELEMETRY EXPORT
 * ============================================================================ */

/** @brief Telemetry magic number "AITe" */
#define VOS3_AI_TELEMETRY_MAGIC     0x41495465U

/** @brief Telemetry format version */
#define VOS3_AI_TELEMETRY_VERSION   1U

/**
 * @brief Telemetry snapshot header
 */
typedef struct vos3_ai_telemetry_header {
    uint32_t    magic;          /**< Magic number (AITe) */
    uint32_t    version;        /**< Format version */
    uint64_t    timestamp;      /**< Snapshot timestamp */
    uint32_t    region_count;   /**< Number of regions */
    uint32_t    alert_count;    /**< Pending alerts */
    uint64_t    total_bytes;    /**< Total protected bytes */
} vos3_ai_telemetry_header_t;

/**
 * @brief Telemetry region entry
 */
typedef struct vos3_ai_telemetry_region {
    uint32_t    region_id;          /**< Region identifier */
    uint32_t    pid;                /**< Owner process */
    uint8_t     type;               /**< Region type */
    uint8_t     state;              /**< Current state */
    uint8_t     numa_node;          /**< NUMA node */
    uint8_t     flags;              /**< Region flags */
    uint64_t    base;               /**< Base address */
    uint64_t    size;               /**< Size in bytes */
    uint64_t    read_count;         /**< Read operations */
    uint64_t    write_count;        /**< Write operations */
    uint64_t    fault_count;        /**< Guard faults */
    uint64_t    last_access_time;   /**< Last access tick */
    uint32_t    ref_count;          /**< Shared references */
    uint32_t    _padding;           /**< Alignment */
} vos3_ai_telemetry_region_t;

/**
 * @brief Telemetry operating mode
 */
typedef enum vos3_ai_telemetry_mode {
    VOS3_AI_TELEMETRY_POLL  = 0U,   /**< Polling mode */
    VOS3_AI_TELEMETRY_EVENT = 1U,   /**< Event-driven mode */
} vos3_ai_telemetry_mode_t;

/* ============================================================================
 * AI MONITOR CONFIGURATION (Phase 17.4)
 * ============================================================================ */

/**
 * @brief AI Monitor runtime configuration
 */
typedef struct vos3_ai_monitor_config {
    uint64_t    threshold;              /**< Accesses before anomaly */
    uint64_t    window;                 /**< Window in timer ticks */
    uint64_t    type_thresholds[3];     /**< Per-type threshold overrides */
    uint64_t    type_windows[3];        /**< Per-type window overrides */
    uint32_t    type_override_mask;     /**< Bitmask: which types use override */
    uint32_t    telemetry_interval;     /**< Ticks between telemetry reports */
} vos3_ai_monitor_config_t;

/* ============================================================================
 * AI ALERT SYSTEM (Phase 17.4)
 * ============================================================================ */

/**
 * @brief Alert severity levels
 */
typedef enum vos3_ai_alert_severity {
    VOS3_AI_ALERT_INFO      = 0U,
    VOS3_AI_ALERT_WARNING   = 1U,
    VOS3_AI_ALERT_ERROR     = 2U,
    VOS3_AI_ALERT_CRITICAL  = 3U,
} vos3_ai_alert_severity_t;

/**
 * @brief Alert types
 */
typedef enum vos3_ai_alert_type {
    VOS3_AI_ALERT_ANOMALY           = 0U,   /**< Anomalous access pattern */
    VOS3_AI_ALERT_QUOTA_EXCEEDED    = 1U,   /**< Memory quota exceeded */
    VOS3_AI_ALERT_GUARD_FAULT       = 2U,   /**< Guard page violation */
    VOS3_AI_ALERT_INTEGRITY_FAIL    = 3U,   /**< Checksum mismatch */
} vos3_ai_alert_type_t;

/**
 * @brief Alert event structure
 */
typedef struct vos3_ai_alert {
    uint64_t                    timestamp;  /**< When alert occurred */
    vos3_ai_alert_type_t        type;       /**< Alert type */
    vos3_ai_alert_severity_t    severity;   /**< Severity level */
    uint32_t                    region_id;  /**< Affected region (or 0) */
    uint32_t                    pid;        /**< Affected process */
    uint64_t                    value1;     /**< Context: count/used */
    uint64_t                    value2;     /**< Context: threshold/limit */
} vos3_ai_alert_t;

/**
 * @brief Alert callback function type
 */
typedef void (*vos3_ai_alert_callback_t)(const vos3_ai_alert_t* alert);

/* ============================================================================
 * AI MONITOR FUNCTIONS (Phase 17.3)
 * ============================================================================ */

/**
 * @brief Initialize AI access monitor
 * @return 0 on success
 */
int vos3_ai_monitor_init(void);

/**
 * @brief Start monitoring a region
 * @param[in] region Region to monitor
 * @return 0 on success
 */
int vos3_ai_monitor_start(vos3_ai_guard_region_t* region);

/**
 * @brief Stop monitoring a region
 * @param[in] region Region to stop monitoring
 * @return 0 on success
 */
int vos3_ai_monitor_stop(vos3_ai_guard_region_t* region);

/**
 * @brief Record a memory access event (atomic-safe)
 * @param[in] region Region accessed (may be NULL)
 * @param[in] addr Access address
 * @param[in] is_write 1 if write, 0 if read
 */
void vos3_ai_monitor_record_access(vos3_ai_guard_region_t* region,
                                    uintptr_t addr, int is_write);

/**
 * @brief Record a fault event
 * @param[in] region Region where fault occurred (may be NULL)
 * @param[in] addr Fault address
 */
void vos3_ai_monitor_record_fault(vos3_ai_guard_region_t* region, uintptr_t addr);

/**
 * @brief Print monitor report for a region
 * @param[in] region Region to report on
 */
void vos3_ai_monitor_report(vos3_ai_guard_region_t* region);

/**
 * @brief Periodic monitor tick (called from timer interrupt)
 */
void vos3_ai_monitor_tick(void);

/**
 * @brief Get monitor statistics
 * @param[out] accesses Total access count
 * @param[out] reads Total read count
 * @param[out] writes Total write count
 * @param[out] faults Total fault count
 * @param[out] anomalies Total anomaly count
 */
void vos3_ai_monitor_get_stats(uint64_t* accesses, uint64_t* reads,
                                uint64_t* writes, uint64_t* faults,
                                uint64_t* anomalies);

/**
 * @brief Check if monitor is initialized
 * @return 1 if initialized, 0 otherwise
 */
int vos3_ai_monitor_is_initialized(void);

/* ============================================================================
 * AI MONITOR CONFIGURATION FUNCTIONS (Phase 17.4)
 * ============================================================================ */

/**
 * @brief Set monitor configuration
 * @param[in] config New configuration
 * @return 0 on success
 */
int vos3_ai_monitor_set_config(const vos3_ai_monitor_config_t* config);

/**
 * @brief Get current monitor configuration
 * @param[out] config Output configuration
 * @return 0 on success
 */
int vos3_ai_monitor_get_config(vos3_ai_monitor_config_t* config);

/**
 * @brief Set anomaly detection threshold
 * @param[in] threshold Accesses before anomaly
 * @param[in] window Window in timer ticks
 * @return 0 on success
 */
int vos3_ai_monitor_set_threshold(uint64_t threshold, uint64_t window);

/* ============================================================================
 * AI ALERT FUNCTIONS (Phase 17.4)
 * ============================================================================ */

/**
 * @brief Register alert callback
 * @param[in] callback Function to call on alerts (NULL to disable)
 * @return 0 on success
 */
int vos3_ai_monitor_set_alert_callback(vos3_ai_alert_callback_t callback);

/**
 * @brief Fire an alert (calls callback + logs)
 * @param[in] alert Alert to fire
 */
void vos3_ai_monitor_fire_alert(const vos3_ai_alert_t* alert);

/* ============================================================================
 * AI QUOTA FUNCTIONS (Phase 17.4)
 * ============================================================================ */

/**
 * @brief Set memory quota for a context
 * @param[in] ctx Guard context
 * @param[in] limit Max bytes (0=unlimited)
 * @param[in] enforce 1=hard limit, 0=warn only
 * @return 0 on success
 */
int vos3_ai_guard_set_quota(vos3_ai_guard_ctx_t* ctx,
                             uint64_t limit, int enforce);

/**
 * @brief Get quota status for a context
 * @param[in] ctx Guard context
 * @param[out] limit Current limit
 * @param[out] used Current usage
 * @return 0 on success
 */
int vos3_ai_guard_get_quota(vos3_ai_guard_ctx_t* ctx,
                             uint64_t* limit, uint64_t* used);

/**
 * @brief Get available quota
 * @param[in] ctx Guard context
 * @return Bytes available (UINT64_MAX if unlimited)
 */
uint64_t vos3_ai_guard_quota_available(vos3_ai_guard_ctx_t* ctx);

/* ============================================================================
 * WORKLOAD HINTS (Sprint 15 / Item A1)
 * ============================================================================ */

/**
 * @brief Apply a workload hint to an AI guard region.
 *
 * Adjusts the region's page-table policy + swap eligibility + TLB
 * prefetch behavior according to the documented hint class. See the
 * vos3_ai_workload_hint_t enum comment block for the per-hint policy.
 *
 * @param[in,out] region  Region to annotate. MUST be in ACTIVE state.
 * @param[in]     hint    One of VOS3_AI_HINT_SCRATCH | _MODEL_WEIGHTS |
 *                        _KV_CACHE.
 * @return 0 on success, VOS3_AI_GUARD_ERR_INVALID on NULL region,
 *         VOS3_AI_GUARD_ERR_PERM if region is not ACTIVE.
 *
 * Idempotent — calling twice with the same hint is a no-op. Calling
 * with a different hint replaces the prior policy (no incremental
 * page-table churn unless flags actually change).
 *
 * Source: https://arxiv.org/pdf/2508.00604 (Composable OS Kernel
 * Architectures for Autonomous Intelligence)
 */
int vos3_ai_guard_set_workload_hint(struct vos3_ai_guard_region* region,
                                     vos3_ai_workload_hint_t hint);

/**
 * @brief Read the current workload hint of a region.
 *
 * @param[in]  region   Region to query.
 * @param[out] hint_out Caller-supplied hint pointer. Set to the
 *                      region's current hint on success.
 * @return 0 on success, negative on error.
 */
int vos3_ai_guard_get_workload_hint(struct vos3_ai_guard_region* region,
                                     vos3_ai_workload_hint_t* hint_out);

/* ============================================================================
 * INTEGRITY AUTOMATION FUNCTIONS (Phase 17.5.1)
 * ============================================================================ */

/**
 * @brief Configure automatic integrity verification
 * @param[in] region Region to configure
 * @param[in] interval Ticks between verifications (0=disable)
 * @param[in] auto_suspend 1=suspend region on violation
 * @return 0 on success
 */
int vos3_ai_guard_set_integrity_auto(vos3_ai_guard_region_t* region,
                                      uint32_t interval, int auto_suspend);

/**
 * @brief Get integrity configuration
 * @param[in] region Region to query
 * @param[out] config Configuration output
 * @return 0 on success
 */
int vos3_ai_guard_get_integrity_config(vos3_ai_guard_region_t* region,
                                        vos3_ai_integrity_config_t* config);

/**
 * @brief Process periodic integrity checks (call from timer)
 * @param[in] current_tick Current timer tick
 */
void vos3_ai_guard_integrity_tick(uint64_t current_tick);

/**
 * @brief Weight Purity Guard tick — incremental SHA-256 verification of
 *        active model slot HugePages.  Called from integrity_tick.
 *
 * Each tick hashes VOS3_WPG_CHUNK_SIZE bytes of the current scan.  When the
 * full slot has been scanned, the resulting digest is compared against the
 * reference hash captured at slot_finish time.  A mismatch sets the slot
 * to VOS3_SLOT_CORRUPT and fires a CRITICAL integrity alert.
 *
 * @param[in] current_tick Current timer tick
 */
void vos3_wpg_tick(uint64_t current_tick);

/**
 * @brief Compute and store the WPG reference hash for a slot.
 *        Called once when a slot transitions to ACTIVE.
 * @param[in] slot_id Slot to hash (0..VOS3_MODEL_SLOT_MAX-1)
 */
void vos3_wpg_arm(uint8_t slot_id);

/**
 * @brief Re-protect monitored pages that were temporarily made writable
 *
 * Called from timer tick handler. Walks all regions with needs_reprotect
 * flag set, removes WRITABLE from PTE, issues invlpg, clears the flag.
 */
void vos3_ai_guard_reprotect_tick(void);

/* ============================================================================
 * NUMA FUNCTIONS (Phase 17.5.2)
 * ============================================================================ */

/**
 * @brief Allocate NUMA-aware AI memory
 * @param[in] ctx Guard context
 * @param[in] size Size in bytes
 * @param[in] type Region type
 * @param[in] flags Region flags
 * @param[in] numa_node Preferred NUMA node
 * @return Virtual address or NULL on failure
 */
void* vos3_ai_guard_alloc_numa(vos3_ai_guard_ctx_t* ctx, size_t size,
                                vos3_ai_guard_type_t type,
                                vos3_ai_guard_flags_t flags,
                                vos3_numa_node_t numa_node);

/**
 * @brief Get per-NUMA-node memory usage
 * @param[in] ctx Guard context
 * @param[out] per_node Array of VOS3_NUMA_MAX_NODES bytes
 * @return 0 on success
 */
int vos3_ai_guard_numa_stats(vos3_ai_guard_ctx_t* ctx, uint64_t* per_node);

/* ============================================================================
 * SHARED REGION FUNCTIONS (Phase 17.5.3)
 * ============================================================================ */

/**
 * @brief Create a shared AI region
 * @param[in] ctx Owner context
 * @param[in] size Size in bytes
 * @param[in] type Region type
 * @param[in] flags Region flags
 * @return Shared region pointer, or NULL on failure
 */
vos3_ai_shared_region_t* vos3_ai_guard_create_shared(
    vos3_ai_guard_ctx_t* ctx, size_t size,
    vos3_ai_guard_type_t type, vos3_ai_guard_flags_t flags);

/**
 * @brief Map an existing shared region into a context
 * @param[in] shared Shared region to map
 * @param[in] ctx Target context
 * @param[in] access_flags Access permissions
 * @return Region pointer in context, or NULL on failure
 */
vos3_ai_guard_region_t* vos3_ai_guard_map_shared(
    vos3_ai_shared_region_t* shared,
    vos3_ai_guard_ctx_t* ctx, uint32_t access_flags);

/**
 * @brief Unmap a shared region from a context
 * @param[in] ctx Context
 * @param[in] region Region to unmap
 * @return 0 on success
 */
int vos3_ai_guard_unmap_shared(vos3_ai_guard_ctx_t* ctx,
                                vos3_ai_guard_region_t* region);

/**
 * @brief Find shared region by ID
 * @param[in] region_id Region ID
 * @return Shared region pointer, or NULL if not found
 */
vos3_ai_shared_region_t* vos3_ai_guard_find_shared(uint32_t region_id);

/**
 * @brief Get shared region reference count
 * @param[in] shared Shared region
 * @return Reference count
 */
uint32_t vos3_ai_guard_shared_refcount(vos3_ai_shared_region_t* shared);

/* ============================================================================
 * MEMORY PRESSURE FUNCTIONS (Phase 17.5.4)
 * ============================================================================ */

/**
 * @brief Set memory pressure configuration
 * @param[in] config Pressure configuration
 * @return 0 on success
 */
int vos3_ai_guard_set_pressure_config(const vos3_ai_pressure_config_t* config);

/**
 * @brief Get current memory pressure level
 * @return Pressure level
 */
vos3_ai_pressure_level_t vos3_ai_guard_get_pressure_level(void);

/**
 * @brief Set swap hint for a region
 * @param[in] region Region to configure
 * @param[in] hint Swap hint
 * @return 0 on success
 */
int vos3_ai_guard_set_swap_hint(vos3_ai_guard_region_t* region,
                                 vos3_ai_swap_hint_t hint);

/**
 * @brief Set reclaim callback for a region
 * @param[in] region Region
 * @param[in] callback Callback function
 * @return 0 on success
 */
int vos3_ai_guard_set_reclaim_callback(vos3_ai_guard_region_t* region,
                                        vos3_ai_reclaim_callback_t callback);

/**
 * @brief Attempt to reclaim memory
 * @param[in] bytes_needed Bytes to free
 * @return Bytes actually freed
 */
size_t vos3_ai_guard_reclaim(size_t bytes_needed);

/**
 * @brief Suspend a region (mark inactive, release resources)
 * @param[in] region Region to suspend
 * @return 0 on success
 */
int vos3_ai_guard_suspend_region(vos3_ai_guard_region_t* region);

/**
 * @brief Resume a suspended region
 * @param[in] region Region to resume
 * @return 0 on success
 */
int vos3_ai_guard_resume_region(vos3_ai_guard_region_t* region);

/**
 * @brief Periodic pressure check (call from timer)
 */
void vos3_ai_guard_pressure_tick(void);

/* ============================================================================
 * TELEMETRY FUNCTIONS (Phase 17.5.5)
 * ============================================================================ */

/**
 * @brief Initialize telemetry subsystem
 * @return 0 on success
 */
int vos3_ai_telemetry_init(void);

/**
 * @brief Generate telemetry snapshot
 * @param[out] buffer Output buffer
 * @param[in] buffer_size Buffer size
 * @return Bytes written, or negative error code
 */
int64_t vos3_ai_telemetry_snapshot(void* buffer, size_t buffer_size);

/**
 * @brief Set telemetry mode
 * @param[in] mode Operating mode
 * @return 0 on success
 */
int vos3_ai_telemetry_set_mode(vos3_ai_telemetry_mode_t mode);

/**
 * @brief Queue alert for telemetry export
 * @param[in] alert Alert to queue
 */
void vos3_ai_telemetry_queue_alert(const vos3_ai_alert_t* alert);

/* ============================================================================
 * PHASE N: PER-APP MEMORY ISOLATION
 * ============================================================================ */

/**
 * @brief Create a per-app AI guard context
 * @param[in] app_id Application ID (0-7, where 0 is system context)
 * @return 0 on success, negative error code on failure
 */
int vos3_ai_guard_create_app_ctx(uint8_t app_id);

/**
 * @brief Destroy a per-app AI guard context
 * @param[in] app_id Application ID (0-7)
 * @return 0 on success, negative error code on failure
 */
int vos3_ai_guard_destroy_app_ctx(uint8_t app_id);

/**
 * @brief Switch active AI guard context to app
 * @param[in] app_id Application ID (0-7)
 * @return 0 on success, negative error code on failure
 */
int vos3_ai_guard_switch_ctx(uint8_t app_id);

/**
 * @brief Get the currently active app_id
 * @return Current active app_id
 */
uint8_t vos3_ai_guard_get_active_app_id(void);

/**
 * @brief Check memory access against app_id in PTE
 * @param[in] fault_addr Address being accessed
 * @param[in] current_app_id The current app context ID
 * @return 0 if access allowed, -EPERM if cross-app violation
 */
int vos3_ai_guard_check_app_access(uintptr_t fault_addr, uint8_t current_app_id);

/**
 * @brief Check if current task is authorized to map device MMIO
 * @param[in] phys_addr Device physical address
 * @param[in] size Mapping size
 * @return 0 if authorized, negative error if denied
 */
int vos3_ai_guard_check_hardware_access(uintptr_t phys_addr, size_t size);

/**
 * @brief Get app status info for a given app_id
 * @param[in] app_id Application ID (0-7)
 * @param[out] mem_used Output: memory used in bytes
 * @param[out] region_count Output: number of active regions
 * @return 0 on success, negative error code on failure
 */
int vos3_ai_guard_get_app_status(uint8_t app_id, uint64_t* mem_used,
                                  size_t* region_count);

/* ============================================================================
 * PHASE 4.1: MODEL REGION SCRUB
 * ============================================================================ */

/**
 * @brief Zero-fill all MODEL regions for an app context
 *
 * Walks the region linked list for the given app_id, finds all regions
 * of type VOS3_AI_GUARD_MODEL in state VOS3_AI_STATE_ACTIVE, temporarily
 * makes them writable, zero-fills using rep stosq (8 bytes/cycle), restores
 * read-only PTEs, and flushes TLB.
 *
 * Called from bridge APPKILL when retention policy is SCRUB (code 0).
 *
 * @param[in] app_id Application ID (0-7)
 * @return Total bytes scrubbed, or 0 if no regions found
 */
uint64_t vos3_ai_guard_scrub_model_regions(uint8_t app_id);

/* ============================================================================
 * PHASE 4.2: AI MODEL WEIGHT LOADING & PROTECTION
 * ============================================================================ */

/**
 * @brief AI model load result information
 */
typedef struct vos3_ai_model_info {
    uintptr_t base;       /**< Virtual base address of model memory */
    size_t    size;        /**< Total model size in bytes */
    uint64_t  checksum;    /**< Rolling XXH3-64 checksum (no re-scan needed) */
    uint32_t  model_id;    /**< MCP model identifier */
    uint32_t  crc32c;      /**< Hardware CRC32C secondary checksum (Phase 4.2.5) */
} vos3_ai_model_info_t;

/* ============================================================================
 * PHASE 4.2.5: MULTI-AGENT ORCHESTRATION
 * ============================================================================ */

#define VOS3_MODEL_SLOT_MAX       4U
#define VOS3_MODEL_SLOT_MAX_HP  128U  /* Phase 4.7.1: support 256MB models */
#define VOS3_AI_PREFIX_MAX_HP     4U  /* Phase 8: Max HugePages lockable as prefix (8MB) */

/** Phase 4.9-Final: PUD isolation — each model slot gets its own 1GB-aligned
 *  virtual address range to occupy a distinct Page Directory (PD) page.
 *  Prevents TLB aliasing and cross-slot PTE interference under SMP. */
#define VOS3_AI_PUD_SPACING       0x40000000ULL   /* 1 GiB per slot */

/* ============================================================================
 * PHASE 8.6: WEIGHT PURITY GUARD (WPG) CONSTANTS
 * ============================================================================ */

/** @brief WPG scan chunk per timer tick (256 KiB) — at 100 Hz this gives
 *  ~25.6 MB/s scan rate.  A full 256 MB slot completes in ~10 seconds. */
#define VOS3_WPG_CHUNK_SIZE       (256U * 1024U)

/* ============================================================================
 * PHASE 6.5: V-PALACE WING HIERARCHY
 * ============================================================================
 * Wings are the top-level semantic domains of the V-Palace architecture.
 * Each Wing occupies a distinct PUD entry (1GB boundary), preventing any
 * virtual address overlap between domains.  Slot assignment:
 *   Wing 0 (KERNEL)   → Slot 0 (Coordinator)
 *   Wing 1 (BACKEND)  → Slot 1
 *   Wing 2 (FRONTEND) → Slot 2
 *   Wing 3 (INFRA)    → Slot 3
 * With VOS3_MODEL_SLOT_MAX=4, each Wing maps 1:1 to a slot.
 */
#define VOS3_WING_KERNEL    0U   /**< Coordinator + kernel-only inference */
#define VOS3_WING_BACKEND   1U   /**< Backend agent models */
#define VOS3_WING_FRONTEND  2U   /**< Frontend agent models */
#define VOS3_WING_INFRA     3U   /**< Infrastructure / monitoring agents */
#define VOS3_PALACE_WING_COUNT  4U

/** Map a slot_id to its owning Wing.  With 4 slots, identity mapping. */
static inline uint8_t vos3_palace_wing_for_slot(uint8_t slot_id)
{
    return (slot_id < VOS3_PALACE_WING_COUNT) ? slot_id : 0xFFU;
}

/** L3 cache color count (4 colors = 4 model slots) */
#define VOS3_CACHE_COLOR_COUNT  4U

/** L3 color bits: physical addr bits [22:21] for 2MB hugepages.
 *  With 2MB pages, bits [0:20] are the page offset.
 *  Bits [21:22] select the color (4 colors from 2 bits). */
#define VOS3_CACHE_COLOR_SHIFT  21U
#define VOS3_CACHE_COLOR_MASK   0x3U  /* 2 bits -> 4 colors */

/** @brief PTE inversion marker — bit 11 repurposed for suspended AI slot PTEs */
#define VOS3_PTE_IS_INVERTED      VOS3_PTE_AI_GUARD_PAGE  /* Bit 11 */

/** @brief Cognitive Priority / Sticky Cache marker — bit 52 (software-available).
 *  Set on HugePage PTEs to signal the Cognitive Pager that this page holds
 *  semantically important AI agent state (model weights, KV-cache, context).
 *  Pages with this bit are preserved by cold scrub and receive prefetch
 *  priority in the Predictive Prefetch Engine. Bit 52 is in the
 *  ignored/available range [52:58] on Intel/AMD x86_64 PTEs. */
#define VOS3_PTE_COGNITIVE        ((uint64_t)(1ULL << 52))
#define VOS3_PTE_TOMBSTONE        VOS3_PTE_COGNITIVE  /* backward compat */

/** @brief Physical address mask for PTE inversion XOR */
#define VOS3_PTE_PHYS_MASK        0x000FFFFFFFFFF000ULL

/* VOS3_PTE_LARGE_ADDR_MASK is defined in vmm.h (canonical source) */

/** @brief Model slot status */
typedef enum vos3_model_slot_status {
    VOS3_SLOT_FREE      = 0U,
    VOS3_SLOT_STREAMING = 1U,
    VOS3_SLOT_ACTIVE    = 2U,
    VOS3_SLOT_WARM      = 3U,   /**< Session persistence (L3 cache hint) */
    VOS3_SLOT_SUSPENDED = 4U,   /**< PTEs inverted (IS_INVERTED bit set) */
    VOS3_SLOT_CORRUPT   = 5U,   /**< IPI failure — slot locked, data untrusted */
    VOS3_SLOT_DORMANT   = 6U,   /**< Low-power: PTEs PRESENT, excluded from quota, instant wake */
    VOS3_SLOT_STUCK     = 7U,   /**< Drift watchdog triggered — auto-suspended + feedback sent */
    VOS3_SLOT_SUSPENDED_PENDING = 8U, /**< Quota exceeded in ISR — deferred PTE inversion */
} vos3_model_slot_status_t;

/** @brief Async VBus event codes (kernel→backend) */
typedef enum vos3_vbus_event_code {
    VOS3_EVENT_SLOT_LOADED          = 1U,
    VOS3_EVENT_SLOT_SUSPENDED       = 2U,
    VOS3_EVENT_SLOT_RESUMED         = 3U,
    VOS3_EVENT_PROTECTION_VIOLATION = 4U,
    VOS3_EVENT_TIMER_EXPIRED        = 5U,
    VOS3_EVENT_HEARTBEAT            = 6U,
    VOS3_EVENT_SLOT_RESET           = 7U,
    VOS3_EVENT_QUOTA_EXCEEDED       = 8U,
    VOS3_EVENT_ISC_MESSAGE          = 9U,
    VOS3_EVENT_CHECKPOINT           = 10U,
    VOS3_EVENT_SLOT_STUCK           = 11U,  /**< Drift watchdog: agent frozen */
    /* Phase 4.1: Agentic Mesh & vScreen Perception */
    VOS3_EVENT_VSCREEN_CAPTURE      = 13U,  /**< vScreen frame captured */
    VOS3_EVENT_MESH_DISPATCH        = 14U,  /**< Mesh task dispatched */
    VOS3_EVENT_MESH_RESULT          = 15U,  /**< Mesh task result */
    VOS3_EVENT_ACTION_SUBMITTED     = 16U,  /**< Action bridge submission */
    VOS3_EVENT_ACTION_DENIED        = 17U,  /**< Action bridge denial */
    VOS3_EVENT_ACTION_EXECUTED      = 18U,  /**< Action bridge execution */
} vos3_vbus_event_code_t;

/* ============================================================================
 * PHASE 4.2.7: AGENTIC FABRIC DEFINES
 * ============================================================================ */

/* Agent capability flags */
#define VOS3_CAP_INFERENCE      (1ULL << 0)   /**< Can run model inference */
#define VOS3_CAP_TOOL_USE       (1ULL << 1)   /**< Can invoke external tools */
#define VOS3_CAP_CODE_GEN       (1ULL << 2)   /**< Can generate code */
#define VOS3_CAP_REASONING      (1ULL << 3)   /**< Deep reasoning / chain-of-thought */
#define VOS3_CAP_VISION         (1ULL << 4)   /**< Image/vision processing */
#define VOS3_CAP_MEMORY         (1ULL << 5)   /**< Long-term memory access */
#define VOS3_CAP_NETWORK        (1ULL << 6)   /**< Network/API access */
#define VOS3_CAP_SUPERVISOR     (1ULL << 7)   /**< Can manage other slots */
#define VOS3_CAP_WORKER         (1ULL << 8)   /**< Worker slot (subject to consensus gating) */
/* Q2-2026 Hardening: EXEC capability — required for VBus EXEC/APPLOAD */
#define VOS3_CAP_EXEC           (1ULL << 9)   /**< Can execute binaries via VBus */

/* Agent type IDs */
#define VOS3_AGENT_COORDINATOR  0U
#define VOS3_AGENT_WORKER       1U
#define VOS3_AGENT_SECURITY     2U
#define VOS3_AGENT_MONITOR      3U

/* Checkpoint shadow PTE table size */
#define VOS3_SHADOW_PTE_MAX     VOS3_MODEL_SLOT_MAX_HP  /* 128 */

/* Phase 4.2.7+: Context Persistence (KV-Cache) */
#define VOS3_CONTEXT_PAGE_MAX       128U   /* Max 4KB context pages per slot (512KB) */
#define VOS3_CONTEXT_SHADOW_PTE_MAX 128U   /* Shadow PTEs for context page checkpoint */

/* Phase 4.2.8: Shared Weight Hub (Model Deduplication) */
#define VOS3_SHARED_HP_MAX          8U     /* Max shared HugePages mapped per slot */

/* Phase 4.2.9: Consensus Gating */
#define VOS3_PENDING_RESP_MAX       512U   /* Max buffered RESP payload bytes */

/* ============================================================================
 * ISC MESSAGE HEADER (Phase 4.2 — VBus 2.0 session-aware routing)
 * ============================================================================ */

/**
 * @brief ISC message header with context_id for BG_SESSION routing
 */
typedef struct vos3_isc_msg_hdr {
    uint32_t context_id;   /**< BG_SESSION context for session-aware routing */
    uint16_t payload_len;  /**< Bytes following this header */
    uint16_t _reserved;    /**< Alignment / future flags */
} vos3_isc_msg_hdr_t;

#define VOS3_ISC_HDR_SIZE  8U

/* Phase 8: V-Palace Hall Types */
typedef enum {
    VOS3_HALL_WEIGHT     = 0,  /**< Model weight data (immutable after load) */
    VOS3_HALL_PERSISTENT = 1,  /**< KV-cache, conversation state */
    VOS3_HALL_TRANSIENT  = 2,  /**< Scratch/temporary computation */
    VOS3_HALL_COUNT      = 3
} vos3_hall_type_t;

/* Phase 8: V-Palace Room Metadata */
typedef struct {
    uint8_t  hall;             /**< vos3_hall_type_t */
    uint8_t  locked;           /**< 1 = prefix-locked (immutable) */
    uint16_t access_count;     /**< Saturating access counter */
    uint32_t last_access_tick; /**< Scheduler tick at last access */
} vos3_room_meta_t;

/* ============================================================================
 * PHASE 10: REMOTE MIRRORING DEFINES
 * ============================================================================ */

/** @brief Mirror sync modes */
#define VOS3_MIRROR_IDLE    0U
#define VOS3_MIRROR_EAGER   1U  /**< Sync after every write (strong consistency) */
#define VOS3_MIRROR_LAZY    2U  /**< Sync at checkpoint boundaries (eventual) */

/** @brief Maximum mirror sync failures before auto-disconnect */
#define VOS3_MIRROR_MAX_FAILURES  5U

/** @brief Default replication lag tolerance (ms) */
#define VOS3_MIRROR_DEFAULT_LAG   100U

/** @brief Per-slot model descriptor */
typedef struct vos3_ai_model_slot {
    uint8_t                     slot_id;
    uint32_t                    model_id;
    vos3_model_slot_status_t    status;
    char                        label[16];     /**< Agent name: "Coordinator", etc. */
    uint8_t                     format;        /**< VOS3_MODEL_FMT_* from model_registry.h */
    uint8_t                     priority;      /**< 0=Critical (Coordinator), 255=Background */
    uintptr_t                   base;          /**< Virtual base (2MB-aligned) */
    size_t                      size;          /**< Total model size */
    size_t                      offset;        /**< Streaming offset */
    uint32_t                    hp_count;      /**< HugePages allocated */
    uint64_t                    phys[VOS3_MODEL_SLOT_MAX_HP];
    uint64_t                    rolling_hash;  /**< XXH3-64 state */
    uint64_t                    checksum;      /**< Finalized XXH3-64 checksum */
    uint32_t                    crc32c;        /**< HW CRC32C accumulator */
    uint64_t                    timer_expiry;  /**< Timer tick deadline (0=disabled) */
    uint64_t                    cycle_count;       /**< Total inference/access cycles */
    uint64_t                    access_violations; /**< Protection violation attempts */
    uint32_t                    vdev_minor;    /**< Virtual char device minor (slot_id + 240) */
    vos3_spinlock_t             lock;
    /* === Phase 4.2.7: Agentic Fabric fields === */
    /* Semantic Registry */
    uint32_t                    model_version;     /**< Model semantic version (e.g., 0x010200 = 1.2.0) */
    uint32_t                    model_epoch;       /**< Training epoch for rollback validation */
    /* ISC Mailbox */
    uint8_t                     mailbox[4096];     /**< Circular message buffer */
    uint16_t                    mb_head;           /**< Read pointer */
    uint16_t                    mb_tail;           /**< Write pointer */
    /* Agent RBAC — I/O Permission Mask */
    uint64_t                    io_perm_mask;      /**< Bit N=1 → can ISC_SEND to slot N */
    /* Resource Quota */
    uint64_t                    max_cycles;        /**< 0=unlimited, >0=auto-suspend budget */
    /* Agent Registry */
    uint64_t                    capabilities;      /**< Bitmask of VOS3_CAP_* flags */
    uint8_t                     agent_type;        /**< VOS3_AGENT_* type ID */
    /* Core Pinning */
    int8_t                      affinity_cpu;      /**< -1=any, 0..N=pinned to core */
    /* Drift Watchdog */
    uintptr_t                   last_rip;          /**< Last VDEV_READ caller RIP */
    uint64_t                    drift_timestamp;   /**< Tick when last_rip changed significantly */
    /* Rolling Checkpoint */
    uint64_t                    shadow_pte[VOS3_SHADOW_PTE_MAX];  /**< PTE backup for rollback */
    uint64_t                    checkpoint_hash;   /**< rolling_hash at checkpoint time */
    uint32_t                    checkpoint_crc32c; /**< crc32c at checkpoint time */
    uint32_t                    checkpoint_epoch;  /**< model_epoch at checkpoint time */
    size_t                      checkpoint_offset; /**< streaming offset at checkpoint */
    uint8_t                     has_checkpoint;    /**< 1=checkpoint data valid */
    /* === Phase 4.2.7+: Context Persistence (KV-Cache) === */
    uintptr_t                   context_base;          /**< Virtual base of 4KB context pages (after HugePages) */
    uint32_t                    context_page_count;    /**< Number of 4KB pages allocated */
    uint64_t                    context_phys[VOS3_CONTEXT_PAGE_MAX];       /**< Physical addresses */
    uint64_t                    context_shadow_pte[VOS3_CONTEXT_SHADOW_PTE_MAX]; /**< PTE backup for checkpoint */
    uint8_t                     context_configured;    /**< 1=context region active */
    /* === Phase 4.2.8: Swarm Governance Primitives === */
    /* Shared Weight Hub (Model Deduplication) */
    uintptr_t                   shared_hp_vaddr[VOS3_SHARED_HP_MAX]; /**< Virtual addrs of RO shared mappings */
    uintptr_t                   shared_hp_phys[VOS3_SHARED_HP_MAX];  /**< Physical addrs (not owned) */
    uint32_t                    shared_hp_count;                      /**< Number of shared HugePages mapped */
    /* Swarm Barrier Sync */
    uint8_t                     sync_barrier_mask;     /**< Bitmask: slots we wait for (0=no barrier) */
    uint8_t                     sync_barrier_received; /**< Bitmask: slots that have sent ISC to us */
    /* Priority Inheritance */
    uint8_t                     priority_inherited;    /**< 1=priority boosted by ISC sender */
    uint8_t                     inherited_priority;    /**< Original priority before inheritance */
    /* === Phase 4.2.9: Autonomous Agentic Primitives === */
    /* Self-Scheduling (Yield-EX) */
    uint64_t                    yield_event_mask;      /**< Bitmask: slot IDs whose ISC triggers wake */
    uint64_t                    yield_timeout_tick;    /**< Absolute tick deadline for yield timer */
    /* Consensus Gating */
    uint8_t                     consensus_gate;        /**< 1=RESP frames buffered until COMMIT_SLOT */
    uint8_t                     pending_resp[VOS3_PENDING_RESP_MAX]; /**< Buffered RESP payload */
    uint16_t                    pending_resp_len;      /**< Length of buffered response */
    uint16_t                    pending_resp_tag;      /**< Tag of buffered response */
    /* Context Inheritance (COW) */
    uint8_t                     context_cow;           /**< 1=context pages are COW-borrowed */
    /* Phase 4.2.11: Deterministic Clock — prevents side-channel timing attacks */
    uint64_t                    agent_clock_base;      /**< Tick count at slot activation */
    uint64_t                    agent_clock_frozen;    /**< Accumulated ticks before DORMANT (0=running) */
    /* Phase 5 — Tier 2: Per-Agent Zone ACL */
    uint32_t                    owner_tid;             /**< Task ID that owns this slot (0=unowned) */
    /* Phase 5 — Context Management (Freeze/Thaw) — April 2026 Hardened */
    uint64_t                    freeze_id;             /**< Monotonic ID of last freeze snapshot (0=never frozen) */
    uint32_t                    session_epoch;         /**< Freeze/thaw cycle count (anti-replay) */
    uint64_t                    ctx_dirty_bitmap[2];   /**< 128-bit dirty map: bit N=1 → page N written since last epoch */
    uint32_t                    ctx_thaw_crc_seed;     /**< Expected session_epoch for in-flight thaw (anti-replay gate) */
    uint64_t                    ctx_access_latency;    /**< Ticks from CTX_THAW_COLD to CTX_THAW_DONE (performance telemetry) */
    uint64_t                    ctx_thaw_start_tick;   /**< Tick when thaw began (for latency measurement) */
    uint32_t                    ctx_soft_scrub_cycle;  /**< Bridge poll cycle counter for periodic soft-scrub */
    uint8_t                     ctx_frozen;            /**< 1=context pages frozen (PTE read-only for export) */
    uint8_t                     ctx_thawing;           /**< 1=accepting CTX_WRITE data (cold-thaw in progress) */
    /* === Phase 6: KV-Cache HugePage Pinning === */
    uintptr_t                   kv_base;               /**< Virtual base of KV-cache HugePages */
    uint32_t                    kv_hp_count;           /**< Number of HugePages allocated for KV-cache */
    uint64_t                    kv_hp_phys[4];         /**< Physical addresses (max 4 × 2MB = 8MB) */
    uint32_t                    kv_size;               /**< Total KV-cache size in bytes */
    uint8_t                     kv_pinned;             /**< 1=KV-cache HugePages allocated and pinned */
    uint8_t                     kv_pinned_stable;      /**< Phase 6.2: 1=KV phys addrs verified stable after alloc */
    /* === Phase 7: Lazy-Thaw Demand Paging === */
    uint8_t                     lazy_thaw;             /**< 1=demand-paging enabled for this slot */
    uint8_t                     hp_mapped[VOS3_MODEL_SLOT_MAX_HP]; /**< Per-HP: 1=PTE present, 0=unmapped */
    uint32_t                    hp_mapped_count;       /**< Number of currently-mapped HugePages */
    uint64_t                    lazy_faults;           /**< Total lazy-thaw page faults resolved */
    uint64_t                    lazy_evictions;        /**< Total pages evicted (unmapped) for overcommit */
    /* === Phase 8: Stable Prefix Engine === */
    uint32_t                    prefix_locked_count;   /**< Number of HugePages locked as prefix */
    uint8_t                     prefix_immutable;      /**< 1 = prefix lock active */
    /* === Phase 6.5: V-Palace Wing Ownership === */
    uint8_t                     wing_id;               /**< VOS3_WING_* — domain that owns this slot */
    /* === Phase 8: V-Palace HAL === */
    vos3_room_meta_t            rooms[128];            /**< One per HugePage (VOS3_MODEL_SLOT_MAX_HP) */
    uint32_t                    hall_count[3];         /**< Pages per hall: [WEIGHT, PERSISTENT, TRANSIENT] */
    /* === Phase 10: Remote Mirroring === */
    uint32_t                    mirror_peer_ip;         /**< Remote peer IPv4 (network byte order) */
    uint16_t                    mirror_peer_port;       /**< Remote peer port */
    uint8_t                     mirror_active;          /**< 1 if mirroring to remote */
    uint8_t                     mirror_mode;            /**< 0=idle, 1=eager, 2=lazy */
    uint64_t                    mirror_sync_epoch;      /**< Last successful sync epoch */
    uint64_t                    mirror_bytes_sent;      /**< Total bytes mirrored */
    uint64_t                    mirror_last_sync_tick;  /**< Tick of last successful sync */
    uint32_t                    mirror_lag_ms;          /**< Replication lag tolerance */
    uint32_t                    mirror_failures;        /**< Consecutive sync failures */
    /* === Phase 8.6: Weight Purity Guard (WPG) === */
    uint8_t                     wpg_reference_hash[32]; /**< SHA-256 digest of full slot weight data */
    uint8_t                     wpg_enabled;            /**< 1=WPG active for this slot */
    uint32_t                    wpg_scan_offset;        /**< Current byte offset in incremental scan */
    uint32_t                    wpg_scan_total;         /**< Total bytes to scan (slot size at activation) */
    uint32_t                    wpg_violations;         /**< Cumulative bit-flip detections */
    uint64_t                    wpg_last_verify_tick;   /**< Tick when last full verification completed */
    uint64_t                    wpg_scans_completed;    /**< Total full-scan cycles completed */
    /* === Phase 2.1: Managed KV Cache === */
    vos3_kv_managed_t           kv_managed;             /**< 3-tier managed KV state */
    uint32_t                    kv_prefix_refcount;     /**< Shared prefix page refcount */
    uint8_t                     kv_prefix_shared;       /**< 1=this slot shares prefix from another */
    uint8_t                     kv_prefix_src_slot;     /**< Source slot ID for shared prefix */
    uint32_t                    kv_prefix_shared_count; /**< Phase 2.1 Fix 5: pages shared at share-time (for correct unshare decrement) */
    /* === Phase 2.2: Speculative Decoding === */
    uint8_t                     spec_role;              /**< VOS3_SPEC_ROLE_{NONE,DRAFT,TARGET} */
    uint8_t                     spec_partner_slot;      /**< Partner slot ID (0xFF if none) */
    uint8_t                     spec_active;            /**< 1=speculation session active */
    uint8_t                     spec_k;                 /**< Speculation depth K (1..8) */
    uint32_t                    spec_tokens_drafted;    /**< Total tokens drafted */
    uint32_t                    spec_tokens_accepted;   /**< Total tokens accepted by target */
    uint32_t                    spec_tokens_rejected;   /**< Total tokens rejected */
    uint32_t                    spec_batch_id;          /**< Monotonic batch counter */
    /* === Phase 2.2: Rolling Context Window === */
    vos3_context_window_t       ctx_window;             /**< Sliding window state */
} vos3_ai_model_slot_t;

/* Multi-slot model management (Phase 4.2.5) */
uintptr_t vos3_ai_model_slot_start(uint8_t slot_id, uint32_t model_id, size_t size,
                                     const char *label, uint8_t priority);
size_t    vos3_ai_model_slot_write(uint8_t slot_id, const void *data, size_t len);
int       vos3_ai_model_slot_finish(uint8_t slot_id, vos3_ai_model_info_t *out);
int       vos3_ai_model_slot_rewind(uint8_t slot_id, size_t target_offset);
int       vos3_ai_model_slot_suspend(uint8_t slot_id);
int       vos3_ai_model_slot_resume(uint8_t slot_id);
int       vos3_ai_model_slot_status(uint8_t slot_id, vos3_model_slot_status_t *out);

/* PTE Inversion (CVE-2026-4031 speculative hardening) */
int       vos3_ai_pte_invert(uint8_t slot_id);
int       vos3_ai_pte_uninvert(uint8_t slot_id);

/* Phase 4.2.19: PTE-Gated DMA — mark pages inaccessible during DMA reassembly */
int       vos3_ai_pte_protect_region(uint8_t slot_id, uintptr_t base, size_t len);
int       vos3_ai_pte_unprotect_region(uint8_t slot_id, uintptr_t base, size_t len);

/* Hardware CRC32C (SSE4.2) */
uint32_t  vos3_crc32c_hw(uint32_t crc, const void *data, size_t len);
uint32_t  vos3_crc32c_sw(uint32_t crc, const void *data, size_t len);
uint32_t  vos3_crc32c(uint32_t crc, const void *data, size_t len);

/* Session persistence */
int       vos3_ai_model_snapshot(uint8_t slot_id);

/* AI-Timer (self-scheduling) */
int       vos3_ai_set_timer(uint8_t slot_id, uint64_t ms);
void      vos3_ai_timer_tick(uint64_t current_tick);

/* Slot Reset (self-healing) */
int       vos3_ai_slot_reset(uint8_t slot_id);

/* Phase 5 — Tier 2: Per-Agent Zone ACL */
int       vos3_ai_check_slot_owner(uint8_t slot_id, uint32_t requester_tid);
void      vos3_ai_set_slot_owner(uint8_t slot_id, uint32_t tid);
void      vos3_ai_clear_slot_owner(uint8_t slot_id);

/* Atomic Shadow Loading (zero-downtime model swap) */
int       vos3_ai_slot_swap(uint8_t slot_a, uint8_t slot_b);

/* Virtual Device Shim (Universal AI API) */
int       vos3_ai_vdev_read(uint8_t slot_id, void *buf, size_t offset, size_t len);

/* Streaming slot accessor */
int8_t    vos3_ai_model_streaming_slot(void);
void      vos3_ai_force_stop_streaming(void);

/* Bridge getters — expose slot internals without extern */
void      vos3_ai_model_slot_get_info(uint8_t slot_id, vos3_ai_model_slot_t *out);
int       vos3_ai_has_cldemote(void);
void      vos3_ai_slot_inc_access_violations(uint8_t slot_id);

/* ============================================================================
 * PHASE 4.2.7: AGENTIC FABRIC FUNCTIONS
 * ============================================================================ */

/* Inter-Slot Communication (v2: session-aware with context_id) */
int       vos3_ai_isc_send(uint8_t from_slot, uint8_t to_slot,
                            uint32_t context_id, const void *msg, uint16_t len);
int       vos3_ai_isc_recv(uint8_t slot_id, void *buf, uint16_t buf_len,
                            uint16_t *out_len, uint32_t *context_id_out);

/* Dormant State */
int       vos3_ai_model_slot_dormant(uint8_t slot_id);
int       vos3_ai_model_slot_wake(uint8_t slot_id);

/* Resource Quota */
int       vos3_ai_slot_set_quota(uint8_t slot_id, uint64_t max_cycles);

/* Deep Diagnostic Feedback */
void      vos3_ai_send_feedback(uint8_t slot_id, uintptr_t fault_addr,
                                uintptr_t fault_rip, uintptr_t fault_rsp,
                                const uint8_t *stack_capture, uint8_t stack_len);
int8_t    vos3_ai_fault_find_slot(uintptr_t fault_addr);

/* Agent Capabilities */
int       vos3_ai_slot_set_caps(uint8_t slot_id, uint64_t capabilities, uint8_t agent_type);

/* Core Pinning / SMP Affinity */
int       vos3_ai_set_affinity(uint8_t slot_id, int8_t cpu_id);

/* Rolling Checkpoint */
int       vos3_ai_checkpoint(uint8_t slot_id);
int       vos3_ai_rollback(uint8_t slot_id);

/* Agent RBAC */
int       vos3_ai_slot_set_io_mask(uint8_t slot_id, uint64_t io_perm_mask);

/* Drift Watchdog — called from ai_monitor.c tick */
void      vos3_ai_drift_watchdog_tick(uint64_t current_tick);

/* Phase 4.2.7+: Context Persistence (KV-Cache) */
int       vos3_ai_slot_context_config(uint8_t slot_id, uint32_t page_count);
void      vos3_ai_slot_context_free(uint8_t slot_id);
int       vos3_ai_slot_warm_reset(uint8_t slot_id);

/* Phase 6: KV-Cache HugePage Pinning */
int       vos3_ai_kv_cache_alloc(uint8_t slot_id, uint32_t hp_count);
void      vos3_ai_kv_cache_free(uint8_t slot_id);

/* Phase 7: Lazy-Thaw Demand Paging */
int       vos3_ai_lazy_thaw_enable(uint8_t slot_id);
int       vos3_ai_lazy_thaw_disable(uint8_t slot_id);
int       vos3_ai_lazy_thaw_fault(uintptr_t fault_addr);
int       vos3_ai_lazy_thaw_evict(uint8_t slot_id, uint32_t target_free);

/* Phase 8: Stable Prefix Engine */
int       vos3_ai_slot_lock_prefix(uint32_t slot_id, uint32_t count);
int       vos3_ai_slot_unlock_prefix(uint32_t slot_id);
int       vos3_ai_slot_prefix_status(uint32_t slot_id, uint32_t *out_count);

/* Phase 8: V-Palace HAL */
int       vos3_ai_palace_assign_room(uint32_t slot_id, uint32_t hp_index,
                                      vos3_hall_type_t hall);
int       vos3_ai_query_room_id(uint32_t slot_id, uint32_t hp_index,
                                  vos3_room_meta_t *out);
int       vos3_ai_palace_touch_room(uint32_t slot_id, uint32_t hp_index);
int       vos3_ai_palace_stats(uint32_t slot_id, uint32_t *weight_count,
                                uint32_t *persist_count, uint32_t *transient_count);

/* Phase 6.5: V-Palace Wing Hierarchy — Hall Sharing & Room Isolation */

/**
 * @brief Share WEIGHT-hall HugePages from source slot into target slot as READ-ONLY.
 *
 * Maps the same physical frames into the target slot's virtual address space
 * with PTE flags: PRESENT | AI_PROTECTED | NO_EXECUTE (WRITABLE cleared).
 * Both slots must be in the same Wing or source must be the Coordinator (Wing 0).
 *
 * @param src_slot  Source slot (must have WEIGHT-hall pages loaded)
 * @param dst_slot  Destination slot (receives READ-ONLY shared mappings)
 * @return 0 on success, -EINVAL/-EPERM/-ENOMEM on failure
 */
int       vos3_ai_hall_share(uint8_t src_slot, uint8_t dst_slot);

/**
 * @brief Compute L3 cache color for a given agent task ID (Room isolation).
 *
 * Uses bits [1:0] of the agent TID to select one of 4 L3 colors, ensuring
 * different agents in the same slot get isolated cache sets for KV-cache pages.
 *
 * @param agent_tid  Task ID of the agent
 * @return Cache color (0-3) for HugePage allocation
 */
static inline uint8_t vos3_ai_room_color_for_tid(uint32_t agent_tid)
{
    return (uint8_t)(agent_tid & VOS3_CACHE_COLOR_MASK);
}

/**
 * @brief Validate that a slot's Wing matches the expected Wing domain.
 * @return 0 if Wing matches, -EPERM if Wing mismatch (PUD_ISOLATION_VIOLATION)
 */
static inline int vos3_ai_check_wing(uint8_t slot_id, uint8_t expected_wing)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22; /* EINVAL */
    /* Coordinator (Wing 0) has cross-Wing access */
    if (expected_wing == VOS3_WING_KERNEL) return 0;
    return (vos3_palace_wing_for_slot(slot_id) == expected_wing) ? 0 : -1;  /* EPERM */
}

/* Phase 10: V-Palace Remote Mirroring */

/**
 * @brief Configure a remote mirror peer for a slot
 */
int vos3_ai_slot_mirror_peer(uint8_t slot_id, uint32_t peer_ip,
                               uint16_t peer_port, uint32_t lag_ms);

/**
 * @brief Initiate remote sync of slot data to mirror peer
 */
int vos3_ai_slot_mirror_sync(uint8_t slot_id);

/**
 * @brief Query mirror status for a slot
 */
int vos3_ai_slot_mirror_status(uint8_t slot_id,
                                 uint64_t *bytes_sent,
                                 uint64_t *sync_epoch,
                                 uint32_t *failures);

/**
 * @brief Stream model weights to remote peer via Warp Drive DMA
 */
int vos3_ai_slot_mirror_remote(uint8_t slot_id);

/* Phase 5 — Context Management (Freeze/Thaw) — April 2026 Hardened */
int       vos3_ai_ctx_freeze(uint8_t slot_id, uint64_t *out_freeze_id);
int       vos3_ai_ctx_scrub(uint8_t slot_id, uint32_t *out_pages_cleaned,
                              uint32_t *out_effective_bytes);
int       vos3_ai_ctx_read_page(uint8_t slot_id, uint32_t page_idx, void *buf);
int       vos3_ai_ctx_thaw(uint8_t slot_id, uint32_t page_count, uint64_t freeze_id,
                             uint32_t session_epoch, int cold);
int       vos3_ai_ctx_write_page(uint8_t slot_id, uint32_t page_idx,
                                   const void *data, size_t len);
int       vos3_ai_ctx_thaw_done(uint8_t slot_id, uint32_t expected_crc);
void      vos3_ai_ctx_clear_dirty(uint8_t slot_id);
uint32_t  vos3_ai_ctx_compute_crc(uint8_t slot_id);
void      vos3_ai_ctx_soft_scrub_tick(void);

/* Phase 4.2.7+: Event Pub/Sub (External Triggers) */
int       vos3_ai_event_subscribe(uint8_t event_type, uint8_t slot_id);
int       vos3_ai_event_unsubscribe(uint8_t event_type);
int       vos3_ai_event_deliver(uint8_t event_type, const void *payload, uint16_t len);

/* Kernel-internal ISC (RBAC bypass for kernel-originated messages) */
int       vos3_ai_isc_deliver_kernel(uint8_t to_slot, uint32_t context_id,
                                      const void *msg, uint16_t len);

/* Phase 4.2.8: Shared Weight Hub (Model Deduplication) */
int       vos3_ai_map_shared_hugepage(uint8_t target_slot, uintptr_t source_phys);
void      vos3_ai_unmap_shared_hugepages(uint8_t slot_id);

/* Phase 4.2.8: Swarm Barrier Sync */
int       vos3_ai_slot_barrier_wait(uint8_t slot_id, uint8_t mask);

/* Phase 4.2.9: Self-Scheduling (Yield-EX) */
int       vos3_ai_yield_ex(uint8_t slot_id, uint64_t timeout_ms, uint64_t event_mask);

/* Phase 4.2.9: Context Inheritance (COW) */
int       vos3_ai_context_share(uint8_t src_slot, uint8_t dst_slot);
int       vos3_ai_context_cow_fault(uintptr_t fault_addr);

/* Phase 4.2.9: Consensus Gating */
int       vos3_ai_consensus_commit(uint8_t slot_id);
uint64_t  vos3_ai_slot_get_caps(uint8_t slot_id);
int       vos3_ai_slot_get_consensus_gate(uint8_t slot_id);
int       vos3_ai_slot_set_consensus_gate(uint8_t slot_id, uint8_t enable);
void      vos3_ai_slot_buffer_resp(uint8_t slot_id, uint16_t tag,
                                    const void *payload, uint16_t len);

/* Phase 4.2.11: Deterministic Clock */
uint64_t  vos3_ai_slot_get_time(uint8_t slot_id);

/* Phase 4.2.15: Heartbeat page getter */
uint64_t  vos3_ai_get_heartbeat_phys(void);

/* Phase 4.2.16: Backpressure flag helpers */
void vos3_heartbeat_set_busy(void);
void vos3_heartbeat_clear_busy(void);
uint8_t vos3_heartbeat_get_status(void);

/* Phase 4.4: Universal Shard */
void  vos3_memcpy_optimized(void *dst, const void *src, size_t len);
const char *vos3_memcpy_path_name(void);     /**< "AVX-512" / "AVX2" / "ERMS" */
void  vos3_memcpy_bench(uint64_t *erms_cycles, uint64_t *opt_cycles);
void  vos3_cring_post(uint8_t slot_id, uint8_t flags, uint16_t frame_seq);
uint64_t vos3_ai_kaslr_base(void);           /**< Return current KASLR base for diagnostics */

/* Phase 4.5: Swarm Fabric — concurrent multi-slot streaming */
int       vos3_ai_slot_is_streaming(uint8_t slot_id);  /**< 1 if slot is streaming */
int       vos3_ai_any_slot_streaming(void);             /**< 1 if any slot is streaming */
size_t    vos3_ai_slot_stream_write(uint8_t slot_id, const void *data, size_t len);
void     *vos3_ai_slot_write_addr(uint8_t slot_id, size_t offset);
size_t    vos3_ai_slot_get_offset(uint8_t slot_id);
void      vos3_ai_slot_advance(uint8_t slot_id, size_t bytes);
int       vos3_ai_core_pinning(uint8_t slot_id);       /**< Auto-pin Slot N → Core N */

/**
 * @brief Start streaming model load into HugePage-protected memory
 *
 * Allocates HugePages, maps with PCID-tagged ASID isolation,
 * initializes zero-copy DMA streaming via VBus.
 *
 * @param[in] model_id  MCP model identifier
 * @param[in] size      Total model size in bytes
 * @return Virtual base address, or 0 on failure
 */
uintptr_t vos3_ai_model_start(uint32_t model_id, size_t size);

/**
 * @brief Update rolling XXH3-64 hash with new model data
 *
 * In zero-copy mode, data is already in HugePage (DMA'd by virtio).
 * Just advances offset and updates rolling hash state.
 *
 * @param[in] data  Pointer to model weight data (HugePage virtual address)
 * @param[in] len   Length of data chunk
 * @return Bytes processed
 */
size_t vos3_ai_model_write(const void *data, size_t len);

/**
 * @brief Finalize model loading: lock pages, flush caches, IPI broadcast
 *
 * Applies Ghostwrite-safe cache flush (sfence→clflushopt→mfence),
 * Crosstalk-2 VERW scrub, IPI TLB flush with acknowledge barrier,
 * PCID flush, PAT WB→WT transition, speculative barrier (lfence).
 *
 * @param[out] out  Model info output (base, size, checksum, model_id)
 * @return 0 on success, negative error code on failure
 */
int vos3_ai_model_finish(vos3_ai_model_info_t *out);

/**
 * @brief One-shot model load from buffer (syscall 500 path)
 * @param[in] buffer  Source buffer
 * @param[in] size    Model size in bytes
 * @return Virtual base address, or 0 on failure
 */
uintptr_t vos3_ai_load_model(const void *buffer, size_t size);

/**
 * @brief Check PTE flags for model address with speculative barrier
 * @param[in] addr  Address to check
 * @return Raw PTE value (caller verifies bits), or 0 on error
 */
uint64_t vos3_ai_model_check_pte(uintptr_t addr);

/**
 * @brief Rewind model streaming to a previous offset (Smart-Retry)
 *
 * Re-computes XXH3-64 hash state from scratch up to target offset
 * by re-reading HugePage data, resets streaming descriptor.
 *
 * @param[in] target_offset  Byte offset to rewind to
 * @return 0 on success, -1 on invalid offset
 */
int vos3_ai_model_rewind(size_t target_offset);

/**
 * @brief Check if model streaming is in progress
 * @return 1 if streaming, 0 otherwise
 */
int vos3_ai_model_is_streaming(void);

/**
 * @brief Get current model streaming offset
 * @return Bytes received so far
 */
size_t vos3_ai_model_get_offset(void);

/**
 * @brief Get model base virtual address
 * @return Virtual base, or 0 if no model loaded
 */
uintptr_t vos3_ai_model_get_base(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_AI_GUARD_H */
