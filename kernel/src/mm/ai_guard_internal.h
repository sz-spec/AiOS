/**
 * @file ai_guard_internal.h
 * @brief Shared internal state for the AI Guard subsystem split modules.
 *
 * Task 4.1: This header exposes statics from ai_guard.c that are needed
 * by the split-out translation units (ai_pte.c, ai_slots.c, ai_sq.c, ai_isc.c).
 * It must NOT be included by any code outside kernel/src/mm/.
 */

#ifndef VOS3_AI_GUARD_INTERNAL_H
#define VOS3_AI_GUARD_INTERNAL_H

#include "../../include/vos/ai_guard.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/atomic.h"
#include "../../include/vos/console.h"
#include "../../include/vos/timer.h"
#include "../../include/vos/virtio_vbus.h"
#include "../../include/vos/string.h"
#include "../../include/vos/entropy.h"
#include "../../include/vos/sha256.h"
#include "../../include/arch/x86_64/cpu.h"
#include "../../include/vos/ivshmem.h"

/* ============================================================================
 * CPU Feature Flags (defined in ai_pte.c, read by all modules)
 * ============================================================================ */

extern int g_cpu_has_sse42;
extern int g_cpu_has_cldemote;
extern int g_cpu_has_invpcid;
extern int g_cpu_has_avx;
extern int g_cpu_has_ibpb;
extern int g_cpu_has_l3cat;   /* Intel Cache Allocation Technology (RDT L3 CAT) */
extern int g_cpu_has_clflushopt;
extern int g_cpu_has_avx2;
extern int g_cpu_has_avx512f;
extern int g_cpu_has_flush_l1d;  /* Cyber overlay (Stage 2): MSR_IA32_FLUSH_CMD hardware L1D flush */
extern int g_memcpy_path;

/* ============================================================================
 * Heartbeat Page Pointers (defined in ai_guard.c)
 * ============================================================================ */

extern uint64_t  g_heartbeat_phys;
extern volatile uint64_t *g_heartbeat_ptr;

/* ============================================================================
 * Model Slot State (defined in ai_slots.c)
 * ============================================================================ */

extern vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];
extern int8_t  g_streaming_slot;
extern uint8_t g_streaming_mask;

/* Heartbeat tick tracking (ai_slots.c) */
extern uint64_t g_heartbeat_last_tick;

/* ============================================================================
 * PTE Inversion Key (defined in ai_pte.c)
 * ============================================================================ */

extern uint64_t g_pte_invert_key;

/* ============================================================================
 * SQ Globals (defined in ai_sq.c)
 * ============================================================================ */

extern volatile vos3_sq_header_t *g_sq_hdr;
extern volatile vos3_sq_entry_t  *g_sq_entries;
extern uint64_t g_sq_processed;
extern uint64_t g_sq_sentinel_rejects;
extern volatile uint32_t g_validation_bitmask;
extern uint32_t g_sntl_entropy_salt;

/* CQ Globals (defined in ai_sq.c) */
extern volatile vos3_cring_header_t *g_cring_hdr;
extern volatile vos3_cring_entry_t  *g_cring_entries;
extern uint16_t g_cring_frame_seq;

/* Doorbell (defined in ai_sq.c) */
extern volatile vos3_doorbell_matrix_t *g_doorbell;

/* ============================================================================
 * ISC / Event Subscriptions (defined in ai_isc.c)
 * ============================================================================ */

extern uint8_t g_event_subscriptions[256];

/* COW copy buffer (defined in ai_guard.c, used by ai_isc.c, ai_slots.c) */
extern uint8_t g_cow_copy_buf[4096];
/* v23.5: Spinlock for COW buffer SMP safety (D-LOW1 fix) */
extern vos3_spinlock_t g_cow_lock;

/* ============================================================================
 * App Context State (defined in ai_guard.c)
 * ============================================================================ */

extern vos3_ai_guard_ctx_t* g_global_ctx;
extern vos3_ai_guard_ctx_t* g_app_contexts[VOS3_MAX_APP_CONTEXTS];
extern uint8_t g_active_app_id;

/* ============================================================================
 * Internal Helper Functions (defined in ai_guard.c)
 * ============================================================================ */

/** @brief AI Monitor alert (defined in ai_monitor.c) */
extern void vos3_ai_monitor_fire_alert(const vos3_ai_alert_t* alert);

/** @brief Align size up to page boundary */
static inline size_t align_to_page(size_t size)
{
    return (size + VOS3_AI_GUARD_PAGE_SIZE - 1U) & ~(VOS3_AI_GUARD_PAGE_SIZE - 1U);
}

/* ============================================================================
 * Allocator State (defined in ai_guard.c, used by ai_slots.c)
 * ============================================================================ */

extern uintptr_t g_ai_alloc_next;
extern uintptr_t g_ai_kaslr_base;

/* ============================================================================
 * Cross-Module Function Declarations
 * ============================================================================ */

/* ai_pte.c */
void     ai_detect_cpu_features(void);
void     ai_init_invert_key(void);
void     crc32c_init_table(void);
uint64_t vos3_xxh3_update(uint64_t state, const void *data, size_t len);
uint64_t vos3_xxh3_finalize(uint64_t h);

/** @brief XXH3 seed constant (defined in ai_pte.c, used by ai_slots.c and ai_sq.c) */
#define VOS3_XXH3_SEED  0x9E3779B97F4A7C15ULL

/* ai_sq.c */
void    cring_init(void);

/* ai_slots.c — slot helpers used by ai_sq.c */
void   *vos3_ai_slot_write_addr(uint8_t slot_id, size_t offset);

/* ai_guard.c — SIMD features and helpers used by ai_slots.c */
void    ai_detect_simd_features(void);
void    scrub_zero_fill(void *addr, size_t size);
void    vos3_simd_scrub_all(void);
void    vos3_fpu_scrub_full(void);

/* L1D flush buffer — defined in ai_guard.c, used by ai_sq.c for memcpy bench */
extern volatile uint8_t g_l1d_flush_buf[32768];

#endif /* VOS3_AI_GUARD_INTERNAL_H */
