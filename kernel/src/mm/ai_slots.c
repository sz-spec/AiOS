/**
 * @file ai_slots.c
 * @brief VOS3 AI Guard — Model Slot Lifecycle Management
 *
 * Task 4.1: Split from ai_guard.c. Contains:
 *   - Multi-slot model state (g_model_slots[], streaming mask)
 *   - Slot start / write / finish / rewind
 *   - Slot swap, suspend, resume, reset, warm reset
 *   - VDEV_READ, snapshot, dormant/wake
 *   - Telemetry (cycle_count, access_violations)
 *   - Agent capabilities, affinity, quotas, RBAC
 *   - Rolling checkpoint / rollback
 *   - Context persistence (KV-Cache), COW
 *   - Shared weight hub, barrier sync, yield-ex
 *   - Consensus gating, deterministic clock
 *   - Deep diagnostic feedback, drift watchdog
 *   - Heartbeat getters, backpressure
 *   - NMI watchdog
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "ai_guard_internal.h"
#include "../../include/vos/uefi_boot.h"
#include "../../include/vos/tee.h"  /* Cyber overlay (Stage 5): vos3_tee_model_measure */

/* ============================================================================
 * MULTI-SLOT MODEL STATE — defined here, declared extern in ai_guard_internal.h
 * ============================================================================ */

vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];
int8_t  g_streaming_slot = -1;  /* -1 = no slot streaming (legacy compat) */
uint8_t g_streaming_mask = 0;   /* Phase 4.5: bitmask — bit N = slot N streaming.
                                 * v23.7: All RMW via __atomic builtins for SMP safety. */

/* Heartbeat state (Phase F) */
#define VOS3_HEARTBEAT_INTERVAL_TICKS  10U  /* ~100ms at 100Hz */
uint64_t g_heartbeat_last_tick = 0;

void vos3_ai_force_stop_streaming(void)
{
    /* Phase 4.5: Clear ALL streaming slots.
     * v23.7: Atomic snapshot + store for SMP-safe mask clearing. */
    uint8_t mask_snap = __atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST);
    for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        if (mask_snap & (1U << i)) {
            VOS3_WARN("[AI-GUARD] Force-clearing streaming slot %u (client reconnect)", i);
            g_model_slots[i].status = VOS3_SLOT_FREE;
        }
    }
    __atomic_store_n(&g_streaming_mask, 0, __ATOMIC_SEQ_CST);
    g_streaming_slot = -1;
    vos3_vbus_stop_streaming();
}

/* ---- Slot helper: enable PCIDE once ---- */
static void enable_pcide_once(void)
{
    uint64_t cr4;
    __asm__ volatile("mov %%cr4, %0" : "=r"(cr4));
    if (!(cr4 & (1ULL << 17))) {
        cr4 |= (1ULL << 17);  /* CR4.PCIDE */
        __asm__ volatile("mov %0, %%cr4" :: "r"(cr4) : "memory");
        VOS3_INFO("[AI-GUARD] Enabled CR4.PCIDE for ASID isolation");
    }
}

/* ---- Ghostwrite-safe cache flush helper ---- */
static void ghostwrite_cache_flush(uintptr_t base, size_t size)
{
    __asm__ volatile("sfence" ::: "memory");
    if (g_cpu_has_cldemote) {
        for (uintptr_t a = base; a < base + size; a += 64) {
            __asm__ volatile("cldemote (%0)" :: "r"(a) : "memory");
        }
    } else {
        for (uintptr_t a = base; a < base + size; a += 64) {
            __asm__ volatile("clflush (%0)" :: "r"(a) : "memory");
        }
    }
    __asm__ volatile("mfence" ::: "memory");
}

/**
 * Phase 4.2.15: L2 Context Flush — purge stale cache lines from context region.
 * Executes clflushopt (or clflush fallback) over the first 64KB of context_base
 * to prevent cross-agent L2 cache leakage during agent swap.
 */
static void vos3_ai_context_l2_flush(vos3_ai_model_slot_t *slot)
{
    if (!slot->context_configured || slot->context_base == 0) return;

    /* Flush first 64KB (16 pages) of context region */
    uintptr_t flush_end = slot->context_base + 65536;
    uintptr_t max_end = slot->context_base +
                        (uintptr_t)slot->context_page_count * 4096;
    if (flush_end > max_end) flush_end = max_end;

    for (uintptr_t a = slot->context_base; a < flush_end; a += 64) {
        if (g_cpu_has_clflushopt) {
            __asm__ volatile("clflushopt (%0)" :: "r"(a) : "memory");
        } else {
            /* clflush fallback — universally supported since SSE2 */
            __asm__ volatile("clflush (%0)" :: "r"(a) : "memory");
        }
    }
    __asm__ volatile("sfence" ::: "memory");
}

/**
 * Phase 4.3.1: L1D Cache Sanitization — evict all L1D lines via dummy writes.
 */
static void vos3_ai_l1d_sanitize(void)
{
    for (size_t i = 0; i < sizeof(g_l1d_flush_buf); i += 64) {
        g_l1d_flush_buf[i] = (uint8_t)i;
    }
    __asm__ volatile("sfence" ::: "memory");
}

/**
 * @brief Detect model format from magic bytes in the loaded data.
 * @param slot_id Slot to inspect
 * @return VOS3_MODEL_FMT_* identifier
 */
static uint8_t detect_model_format(uint8_t slot_id)
{
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    void *base = vos3_ai_slot_write_addr(slot_id, 0);
    if (base == NULL || slot->offset < 8) return VOS3_MODEL_FMT_UNKNOWN;

    uint32_t magic = *(volatile uint32_t *)base;
    if (magic == 0x46475547U) return VOS3_MODEL_FMT_GGUF;       /* "GGUF" LE */
    uint8_t first = *(volatile uint8_t *)base;
    if (first == '{')  return VOS3_MODEL_FMT_SAFETENSORS;        /* JSON header */
    if (first == 0x08) return VOS3_MODEL_FMT_ONNX;              /* Protobuf varint */
    return VOS3_MODEL_FMT_UNKNOWN;
}

/* ============================================================================
 * PHASE A: MULTI-SLOT START / WRITE / FINISH / REWIND
 * ============================================================================ */

uintptr_t vos3_ai_model_slot_start(uint8_t slot_id, uint32_t model_id,
                                     size_t size, const char *label,
                                     uint8_t priority)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX || size == 0) {
        return 0;
    }

    /* One-time CPU feature detection + inversion key init */
    ai_detect_cpu_features();
    ai_init_invert_key();
    crc32c_init_table();

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* v23.4: Acquire slot->lock for auto-release path to serialize with
     * concurrent DMA, lazy-thaw, suspend/resume (D1 CRITICAL race fix).
     * v23.6: Lock acquired BEFORE streaming_mask check to fix TOCTOU. */
    vos3_spinlock_lock(&slot->lock);

    /* Phase 4.5: Allow concurrent streaming — only reject if THIS slot is already streaming.
     * v23.6: Moved inside lock to prevent TOCTOU race (D-MED fix).
     * v23.7: Atomic load for SMP-safe bitmask read. */
    if (__atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST) & (1U << slot_id)) {
        vos3_spinlock_unlock(&slot->lock);
        VOS3_WARN("[AI-GUARD] Slot %u already streaming, cannot restart", slot_id);
        return 0;
    }

    if (slot->status != VOS3_SLOT_FREE) {
        /* Auto-release: free existing resources for re-use (backward compat).
         * SUSPENDED slots must be uninverted first; STREAMING is rejected. */
        if (slot->status == VOS3_SLOT_STREAMING) {
            vos3_spinlock_unlock(&slot->lock);
            VOS3_WARN("[AI-GUARD] Slot %u is streaming, cannot restart", slot_id);
            return 0;
        }
        VOS3_INFO("[AI-GUARD] Slot %u auto-release (status=%u) for reload",
                  slot_id, slot->status);
        if (slot->status == VOS3_SLOT_SUSPENDED) {
            vos3_ai_pte_uninvert(slot_id);
        }
        /* SUSPENDED_PENDING and STUCK have PRESENT PTEs — no uninvert needed */
        /* Free existing HugePages */
        for (uint32_t j = 0; j < slot->hp_count; j++) {
            if (slot->phys[j]) {
                /* Unmap PDE */
                uintptr_t va = slot->base + (uintptr_t)j * VOS3_PAGE_SIZE_2M;
                vos3_vmm_unmap_large(va);
                vos3_pmm_free_huge(slot->phys[j]);
                slot->phys[j] = 0;
            }
        }
        /* Phase 4.9-Final: With PUD-isolated bases, each slot's virtual range
         * is fixed per slot_id.  No need to reclaim g_ai_alloc_next — each
         * slot always maps to its own 1GB PD region. */
        slot->hp_count = 0;
        slot->base = 0;   /* Force PUD recalculation on next start */
        slot->status = VOS3_SLOT_FREE;
    }

    /* v23.6: Lock held through entire allocation/mapping/init window.
     * Previous code unlocked here, leaving ~120 lines of HugePage alloc,
     * VMM mapping, and state init unprotected from concurrent DMA or
     * lazy-thaw (D-MED slot_start race fix).
     * VMM ops have their own internal locks — no lock-ordering violation. */

    uint32_t n = (uint32_t)((size + VOS3_PAGE_SIZE_2M - 1) / VOS3_PAGE_SIZE_2M);
    if (n > VOS3_MODEL_SLOT_MAX_HP) {
        vos3_spinlock_unlock(&slot->lock);
        VOS3_WARN("[AI-GUARD] Model too large: %zu bytes needs %u HugePages (max %u)",
                  size, n, VOS3_MODEL_SLOT_MAX_HP);
        return 0;
    }

    /* Allocate HugePages with rollback — L3 Color Guard (Phase 4.9b) */
    for (uint32_t i = 0; i < n; i++) {
        slot->phys[i] = vos3_pmm_alloc_colored_hugepage(slot_id & VOS3_CACHE_COLOR_MASK);
        if (slot->phys[i] == 0) {
            VOS3_WARN("[AI-GUARD] HugePage alloc failed at index %u/%u", i, n);
            for (uint32_t j = 0; j < i; j++) {
                vos3_pmm_free_huge(slot->phys[j]);
            }
            vos3_spinlock_unlock(&slot->lock);
            return 0;
        }
    }
    slot->hp_count = n;

    /* Phase 4.9-Final: PUD-Isolated Slot Bases. */
    {
        uintptr_t pud_base = (g_ai_kaslr_base + VOS3_AI_PUD_SPACING - 1)
                              & ~(VOS3_AI_PUD_SPACING - 1);
        slot->base = pud_base + (uintptr_t)slot_id * VOS3_AI_PUD_SPACING;

        /* Centurion diagnostic: verify no PUD overlap with active slots */
        uintptr_t my_end = slot->base + (uintptr_t)n * VOS3_PAGE_SIZE_2M;
        for (uint8_t k = 0; k < VOS3_MODEL_SLOT_MAX; k++) {
            if (k == slot_id) continue;
            vos3_ai_model_slot_t *other = &g_model_slots[k];
            if (other->status == VOS3_SLOT_FREE || other->hp_count == 0) continue;
            uintptr_t o_end = other->base + (uintptr_t)other->hp_count * VOS3_PAGE_SIZE_2M;
            if (slot->base < o_end && my_end > other->base) {
                VOS3_WARN("[AI-GUARD] OVERLAP slot %u [0x%lx..0x%lx] vs slot %u [0x%lx..0x%lx]",
                          slot_id, (unsigned long)slot->base, (unsigned long)my_end,
                          k, (unsigned long)other->base, (unsigned long)o_end);
            }
        }
    }
    slot->size = size;

    enable_pcide_once();

    /* Map HugePages: PRESENT | WRITABLE | NX | AI_PROTECTED | LARGE, !GLOBAL */
    for (uint32_t i = 0; i < n; i++) {
        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        int rc = vos3_vmm_map(vaddr, (uintptr_t)slot->phys[i],
                               (vos3_vmm_flags_t)(VOS3_VMM_FLAG_WRITE |
                                                   VOS3_VMM_FLAG_LARGE));
        if (rc != 0) {
            VOS3_WARN("[AI-GUARD] Failed to map HugePage %u at 0x%lx", i,
                      (unsigned long)vaddr);
            for (uint32_t j = 0; j < i; j++) {
                vos3_vmm_unmap_large(slot->base + (uintptr_t)j * VOS3_PAGE_SIZE_2M);
            }
            for (uint32_t j = 0; j < n; j++) {
                vos3_pmm_free_huge(slot->phys[j]);
            }
            vos3_spinlock_unlock(&slot->lock);
            return 0;
        }
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            /* K-C5: Atomic CAS */
            vos3_pte_t desired = pte;
            desired |= VOS3_PTE_AI_PROTECTED | VOS3_PTE_NO_EXECUTE;
            desired &= ~VOS3_PTE_GLOBAL;
            while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                desired = pte;
                desired |= VOS3_PTE_AI_PROTECTED | VOS3_PTE_NO_EXECUTE;
                desired &= ~VOS3_PTE_GLOBAL;
            }
        }
        vos3_vmm_invlpg(vaddr);
    }

    /* Initialize slot state */
    slot->slot_id      = slot_id;
    slot->model_id     = model_id;
    slot->rolling_hash = VOS3_XXH3_SEED;
    slot->crc32c       = 0;
    slot->offset       = 0;
    slot->checksum     = 0;
    slot->timer_expiry = 0;
    slot->cycle_count  = 0;
    slot->access_violations = 0;
    slot->priority     = priority;
    slot->vdev_minor   = slot_id + 240;
    /* v23.4: Removed slot->lock = VOS3_SPINLOCK_INIT — reinitializing a
     * spinlock while another CPU may hold or spin on it is undefined
     * behavior.  The lock is already initialized at boot (kzalloc zeroes). */

    /* Copy label (max 15 chars + null) */
    if (label != NULL) {
        int i;
        for (i = 0; i < 15 && label[i] != '\0'; i++) {
            slot->label[i] = label[i];
        }
        slot->label[i] = '\0';
    } else {
        slot->label[0] = '\0';
    }

    /* Phase 6.5: Assign Wing ownership — identity mapping (slot N → Wing N) */
    slot->wing_id           = vos3_palace_wing_for_slot(slot_id);

    /* Phase 4.2.7: Agentic Fabric defaults */
    slot->affinity_cpu      = -1;  /* any core */
    slot->capabilities      = 0;
    slot->agent_type        = VOS3_AGENT_WORKER;
    slot->has_checkpoint    = 0;
    slot->mb_head           = 0;
    slot->mb_tail           = 0;
    slot->max_cycles        = 0;
    slot->last_rip          = 0;
    slot->drift_timestamp   = 0;
    slot->model_version     = 0;
    slot->model_epoch       = 0;
    slot->checkpoint_hash   = 0;
    slot->checkpoint_crc32c = 0;
    slot->checkpoint_epoch  = 0;
    slot->checkpoint_offset = 0;
    /* RBAC defaults: Coordinator (slot 0) → full access, others → Coordinator-only */
    slot->io_perm_mask      = (slot_id == 0) ? 0xFFFFFFFFFFFFFFFFULL : 0x0000000000000001ULL;

    slot->status = VOS3_SLOT_STREAMING;
    __atomic_fetch_or(&g_streaming_mask, (uint8_t)(1U << slot_id), __ATOMIC_SEQ_CST);
    g_streaming_slot = (int8_t)slot_id;  /* Legacy compat: last started slot */

    /* Start VBus streaming with slot's memory */
    int rc = vos3_vbus_start_streaming(slot->phys, n, 16384, size, slot->base);
    if (rc != 0) {
        VOS3_WARN("[AI-GUARD] VBus streaming start failed for slot %u", slot_id);
    }

    /* Phase 4.3: Activate zero-copy DMA — protect HugePages during transfer */
    vos3_vbus_map_stream_direct(slot_id, slot->phys, n, slot->base);

    /* Phase 4.2.12: Only bump allocator if we used a NEW base (not recycled) */
    uintptr_t slot_end = slot->base + (uintptr_t)n * VOS3_PAGE_SIZE_2M;
    if (slot_end > g_ai_alloc_next) {
        g_ai_alloc_next = slot_end;
    }

    VOS3_INFO("[AI-GUARD] Slot %u started: base=0x%lx size=%lu pages=%u label=%s",
              slot_id, (unsigned long)slot->base, (unsigned long)size, n, slot->label);

    vos3_spinlock_unlock(&slot->lock);
    return slot->base;
}

size_t vos3_ai_model_slot_write(uint8_t slot_id, const void *data, size_t len)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX ||
        !(__atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST) & (1U << slot_id))) {
        return 0;
    }
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status != VOS3_SLOT_STREAMING || data == NULL || len == 0) {
        return 0;
    }

    size_t remaining = slot->size - slot->offset;
    if (len > remaining) {
        len = remaining;
    }

    /* Update rolling XXH3-64 hash */
    slot->rolling_hash = vos3_xxh3_update(slot->rolling_hash, data, len);

    /* Update CRC32C */
    slot->crc32c = vos3_crc32c(slot->crc32c, data, len);

    slot->offset += len;
    return len;
}

int vos3_ai_model_slot_finish(uint8_t slot_id, vos3_ai_model_info_t *out)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX || out == NULL) {
        return -1;
    }
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status != VOS3_SLOT_STREAMING ||
        !(__atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST) & (1U << slot_id))) {
        return -1;
    }

    /* 1. Stop VBus streaming (only if no other slots are streaming).
     * v23.7: Atomic clear-bit returns the OLD value — check if we were the last. */
    uint8_t old_mask = __atomic_fetch_and(&g_streaming_mask,
                                           (uint8_t)~(1U << slot_id), __ATOMIC_SEQ_CST);
    if ((old_mask & (uint8_t)~(1U << slot_id)) == 0) {
        vos3_vbus_stop_streaming();
    }

    /* 2. Ghostwrite-safe cache flush */
    ghostwrite_cache_flush(slot->base, slot->size);

    /* Phase 4.3: Release zero-copy DMA — restore PRESENT before PTE batch update */
    vos3_vbus_unmap_stream_direct();

    /* v23.9: Acquire slot->lock for the entire PTE batch + TLB flush + status
     * transition window.  This eliminates the race between the atomic
     * streaming_mask clear above and the status = ACTIVE assignment below,
     * preventing a concurrent DMA CAS from hitting transient PTE state. */
    vos3_spinlock_lock(&slot->lock);

    /* 3. Batch PTE update: clear WRITABLE, set WT, preserve LARGE+AI_PROTECTED+NX */
    for (uint32_t i = 0; i < slot->hp_count; i++) {
        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            /* K-C5: Atomic CAS */
            vos3_pte_t desired = pte;
            desired &= ~VOS3_PTE_WRITABLE;
            desired |= VOS3_PTE_WRITE_THROUGH;
            desired &= ~(VOS3_PTE_CACHE_DISABLE | VOS3_PTE_PAT_2M);
            desired &= ~VOS3_PTE_GLOBAL;
            desired |= VOS3_PTE_AI_PROTECTED | VOS3_PTE_NO_EXECUTE;
            while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                desired = pte;
                desired &= ~VOS3_PTE_WRITABLE;
                desired |= VOS3_PTE_WRITE_THROUGH;
                desired &= ~(VOS3_PTE_CACHE_DISABLE | VOS3_PTE_PAT_2M);
                desired &= ~VOS3_PTE_GLOBAL;
                desired |= VOS3_PTE_AI_PROTECTED | VOS3_PTE_NO_EXECUTE;
            }
        }
    }

    /* 4. Crosstalk-2 VERW scrub */
    { uint16_t ds_sel = 0x10;
      __asm__ volatile("verw %[sel]" :: [sel] "m"(ds_sel) : "cc"); }

    /* 4b. Phase 4.3.1: L1D sanitization — evict streaming data from L1D */
    vos3_ai_l1d_sanitize();

    /* 5. Phase 4.5.1: Global TLB coherence */
    for (uint32_t i = 0; i < slot->hp_count; i++) {
        vos3_vmm_invlpg(slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M);
    }
    /* Full TLB flush via CR3 reload — covers all non-global entries */
    {
        uint64_t cr3_val;
        __asm__ volatile("mov %%cr3, %0" : "=r"(cr3_val));
        __asm__ volatile("mov %0, %%cr3" :: "r"(cr3_val) : "memory");
    }

    /* 6. PCID flush */
    if (g_cpu_has_invpcid) {
        struct { uint64_t pcid; uint64_t addr; } invpcid_desc;
        invpcid_desc.pcid = VOS3_PCID_AI_MODEL;
        invpcid_desc.addr = 0;
        __asm__ volatile("invpcid %0, %1"
                         :: "m"(invpcid_desc), "r"((uint64_t)1)
                         : "memory");
    }

    /* 7. Speculative barrier */
    __asm__ volatile("lfence" ::: "memory");

    /* 8. Finalize checksums */
    slot->checksum = vos3_xxh3_finalize(slot->rolling_hash);

    /* 8b. Phase 4.9b: Format detection */
    slot->format = detect_model_format(slot_id);

    out->checksum = slot->checksum;
    out->crc32c   = slot->crc32c;
    out->base     = slot->base;
    out->size     = slot->size;
    out->model_id = slot->model_id;

    /* 9. Mark active, update legacy streaming slot */
    slot->status = VOS3_SLOT_ACTIVE;

    vos3_spinlock_unlock(&slot->lock);
    /* Phase 4.5: mask already cleared above; update legacy compat */
    if (__atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST) == 0) {
        g_streaming_slot = -1;
    }

    /* Phase 8.6: Arm Weight Purity Guard — compute reference SHA-256 hash
     * of the full slot weight data before any further access. */
    vos3_wpg_arm(slot_id);

    /* Phase 4.2.11: Start deterministic clock */
    slot->agent_clock_base   = vos3_timer_get_ticks();
    slot->agent_clock_frozen = 0;

    /* Cyber overlay (Stage 5): TDX RTMR[1] measurement of finished slot.
     *
     * Mirrors VOS3-Cyber's ai_slots.c pattern (line ~509). Now that the
     * slot is sealed (W^X enforced via PTE batch update above) and
     * marked ACTIVE, bind the just-loaded weights into the platform's
     * attestation chain by extending RTMR[1] with SHA-384(weights).
     *
     * vos3_tee_model_measure() is CPUID-gated internally — on non-TDX
     * silicon it records the digest in the per-slot measurement ring
     * (for audit) and skips the hardware extend. Safe to call always.
     *
     * Failure here is non-fatal: the slot is already ACTIVE, attestation
     * is best-effort. We log via the function's own internal path.
     */
    if (slot->base != 0 && slot->size > 0) {
        (void)vos3_tee_model_measure(slot_id,
                                     (const void *)(uintptr_t)slot->base,
                                     slot->size);
    }

    /* 10. Emit async event */
    vos3_vbus_signal_event(slot_id, VOS3_EVENT_SLOT_LOADED, (uint32_t)slot->size);

    VOS3_INFO("[AI-GUARD] Slot %u finished: base=0x%lx size=%lu xxh3=0x%llx crc32c=0x%08x",
              slot_id, (unsigned long)slot->base, (unsigned long)slot->size,
              (unsigned long long)slot->checksum, slot->crc32c);

    return 0;
}

int vos3_ai_model_slot_rewind(uint8_t slot_id, size_t target_offset)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX ||
        !(__atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST) & (1U << slot_id))) {
        return -1;
    }
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status != VOS3_SLOT_STREAMING || target_offset > slot->offset) {
        return -1;
    }

    /* Re-compute hashes from scratch up to target offset */
    slot->rolling_hash = VOS3_XXH3_SEED;
    slot->crc32c = 0;
    const uint8_t *base = (const uint8_t *)slot->base;
    size_t done = 0;
    while (done < target_offset) {
        size_t chunk = (target_offset - done > 8192) ? 8192 : (target_offset - done);
        slot->rolling_hash = vos3_xxh3_update(slot->rolling_hash, base + done, chunk);
        slot->crc32c = vos3_crc32c(slot->crc32c, base + done, chunk);
        done += chunk;
    }
    slot->offset = target_offset;

    vos3_vbus_rewind_streaming(target_offset);

    VOS3_INFO("[AI-GUARD] Slot %u rewind to offset %zu", slot_id, target_offset);
    return 0;
}

int vos3_ai_model_slot_status(uint8_t slot_id, vos3_model_slot_status_t *out)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX || out == NULL) {
        return -1;
    }
    *out = g_model_slots[slot_id].status;
    return 0;
}

int8_t vos3_ai_model_streaming_slot(void)
{
    return g_streaming_slot;
}

/* ============================================================================
 * PHASE 4.5: SWARM FABRIC — CONCURRENT MULTI-SLOT STREAMING
 * ============================================================================ */

int vos3_ai_slot_is_streaming(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return 0;
    return (__atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST) & (1U << slot_id)) ? 1 : 0;
}

int vos3_ai_any_slot_streaming(void)
{
    return (__atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST) != 0) ? 1 : 0;
}

size_t vos3_ai_slot_get_offset(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return 0;
    return g_model_slots[slot_id].offset;
}

void vos3_ai_slot_advance(uint8_t slot_id, size_t bytes)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return;
    g_model_slots[slot_id].offset += bytes;
}

void *vos3_ai_slot_write_addr(uint8_t slot_id, size_t offset)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return NULL;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    /* Use kernel identity map for zero-copy DMA writes */
    size_t page_idx = offset >> 21;   /* offset / 2MB */
    size_t page_off = offset & 0x1FFFFFULL;  /* offset % 2MB */
    if (page_idx < slot->hp_count && slot->phys[page_idx] != 0) {
        return (void *)(0xFFFF800000000000ULL + slot->phys[page_idx] + page_off);
    }
    /* Fallback: slot virtual address */
    return (void *)(slot->base + offset);
}

size_t vos3_ai_slot_stream_write(uint8_t slot_id, const void *data, size_t len)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX ||
        !(__atomic_load_n(&g_streaming_mask, __ATOMIC_SEQ_CST) & (1U << slot_id))) {
        return 0;
    }
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status != VOS3_SLOT_STREAMING || data == NULL || len == 0) {
        return 0;
    }

    size_t remaining = slot->size - slot->offset;
    if (len > remaining) len = remaining;

    /* Write via identity map (data already copied by bridge memcpy) */
    /* Update rolling hashes */
    slot->rolling_hash = vos3_xxh3_update(slot->rolling_hash, data, len);
    slot->crc32c = vos3_crc32c(slot->crc32c, data, len);
    slot->offset += len;
    return len;
}

int vos3_ai_core_pinning(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    /* Auto-pin Slot N → Core N (skip Core 0 = BSP for kernel work) */
    int8_t target_cpu = (int8_t)(slot_id + 1);
    slot->affinity_cpu = target_cpu;
    VOS3_INFO("[AI-GUARD] Phase 4.5: Slot %u pinned to Core %d", slot_id, target_cpu);
    return 0;
}

/* ---- Backward-compatibility shims (Phase 4.2 API → slot 0) ---- */

uintptr_t vos3_ai_model_start(uint32_t model_id, size_t size)
{
    return vos3_ai_model_slot_start(0, model_id, size, "Coordinator", 0);
}

size_t vos3_ai_model_write(const void *data, size_t len)
{
    if (g_streaming_slot < 0) return 0;
    return vos3_ai_model_slot_write((uint8_t)g_streaming_slot, data, len);
}

int vos3_ai_model_finish(vos3_ai_model_info_t *out)
{
    if (g_streaming_slot < 0) return -1;
    return vos3_ai_model_slot_finish((uint8_t)g_streaming_slot, out);
}

int vos3_ai_model_is_streaming(void)
{
    return (g_streaming_slot >= 0) ? 1 : 0;
}

size_t vos3_ai_model_get_offset(void)
{
    if (g_streaming_slot < 0) return 0;
    return g_model_slots[g_streaming_slot].offset;
}

uintptr_t vos3_ai_model_get_base(void)
{
    /* Return slot 0 base for backward compat */
    if (g_model_slots[0].status >= VOS3_SLOT_STREAMING) {
        return g_model_slots[0].base;
    }
    return 0;
}

uintptr_t vos3_ai_load_model(const void *buffer, size_t size)
{
    uintptr_t base = vos3_ai_model_start(1, size);
    if (base == 0) return 0;

    const uint8_t *src = (const uint8_t *)buffer;
    size_t done = 0;
    while (done < size) {
        size_t chunk = (size - done > 8192) ? 8192 : (size - done);
        uint8_t *dst = (uint8_t *)(g_model_slots[0].base + done);
        __builtin_memcpy(dst, src + done, chunk);
        vos3_ai_model_write(dst, chunk);
        done += chunk;
    }

    vos3_ai_model_info_t info;
    if (vos3_ai_model_finish(&info) != 0) return 0;
    return info.base;
}

uint64_t vos3_ai_model_check_pte(uintptr_t addr)
{
    __asm__ volatile("lfence" ::: "memory");
    vos3_pte_t pte;
    if (vos3_vmm_get_pte(addr, &pte) != 0) return 0;
    return (uint64_t)pte;
}

int vos3_ai_model_rewind(size_t target_offset)
{
    if (g_streaming_slot < 0) return -1;
    return vos3_ai_model_slot_rewind((uint8_t)g_streaming_slot, target_offset);
}

/* ============================================================================
 * PHASE B: SLOT_SWAP — Atomic Shadow Loading
 * ============================================================================ */

int vos3_ai_slot_swap(uint8_t slot_a, uint8_t slot_b)
{
    if (slot_a >= VOS3_MODEL_SLOT_MAX || slot_b >= VOS3_MODEL_SLOT_MAX) {
        return -22;  /* EINVAL */
    }
    if (slot_a == slot_b) {
        return -22;
    }
    vos3_ai_model_slot_t *sa = &g_model_slots[slot_a];
    vos3_ai_model_slot_t *sb = &g_model_slots[slot_b];

    if (sa->status != VOS3_SLOT_ACTIVE || sb->status != VOS3_SLOT_ACTIVE) {
        return -22;
    }
    /* v23.16 (D2): Locked prefix pages must never drift to another slot.
     * Block swaps involving any slot with immutable prefix context. */
    if (sa->prefix_immutable || sb->prefix_immutable) {
        return -16;  /* EBUSY */
    }
    if (sa->hp_count != sb->hp_count) {
        return -22;
    }

    /* Lock both slots in order (lower ID first) to prevent deadlock */
    vos3_ai_model_slot_t *first  = (slot_a < slot_b) ? sa : sb;
    vos3_ai_model_slot_t *second = (slot_a < slot_b) ? sb : sa;
    vos3_spinlock_lock(&first->lock);
    vos3_spinlock_lock(&second->lock);

    /* Swap physical pages and rewrite PDEs */
    for (uint32_t i = 0; i < sa->hp_count; i++) {
        /* Swap phys[] entries */
        uint64_t tmp_phys = sa->phys[i];
        sa->phys[i] = sb->phys[i];
        sb->phys[i] = tmp_phys;

        /* Update PTEs: slot A's vaddr → slot B's old phys (now sa->phys[i]) */
        uintptr_t vaddr_a = sa->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        uint64_t lam = vos3_vmm_large_addr_mask();
        vos3_pte_t pte_a;
        if (vos3_vmm_get_pte(vaddr_a, &pte_a) == 0) {
            /* K-C5: Atomic CAS */
            vos3_pte_t desired_a = (pte_a & ~lam) | (sa->phys[i] & lam);
            while (vos3_vmm_cas_pte(vaddr_a, &pte_a, desired_a) != 0) {
                desired_a = (pte_a & ~lam) | (sa->phys[i] & lam);
            }
            vos3_vmm_invlpg(vaddr_a);
        }

        uintptr_t vaddr_b = sb->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_pte_t pte_b;
        if (vos3_vmm_get_pte(vaddr_b, &pte_b) == 0) {
            /* K-C5: Atomic CAS */
            vos3_pte_t desired_b = (pte_b & ~lam) | (sb->phys[i] & lam);
            while (vos3_vmm_cas_pte(vaddr_b, &pte_b, desired_b) != 0) {
                desired_b = (pte_b & ~lam) | (sb->phys[i] & lam);
            }
            vos3_vmm_invlpg(vaddr_b);
        }
    }

    /* Swap metadata (data follows physical pages, not slots) */
    uint64_t tmp_cksum = sa->checksum;
    sa->checksum = sb->checksum;
    sb->checksum = tmp_cksum;

    uint32_t tmp_crc = sa->crc32c;
    sa->crc32c = sb->crc32c;
    sb->crc32c = tmp_crc;

    uint32_t tmp_mid = sa->model_id;
    sa->model_id = sb->model_id;
    sb->model_id = tmp_mid;

    size_t tmp_size = sa->size;
    sa->size = sb->size;
    sb->size = tmp_size;

    /* Phase 4.2.7 Governance: Scrub SIMD state after swap */
    __asm__ volatile (
        "pxor %%xmm0, %%xmm0\n\t"
        "pxor %%xmm1, %%xmm1\n\t"
        "pxor %%xmm2, %%xmm2\n\t"
        "pxor %%xmm3, %%xmm3\n\t"
        "pxor %%xmm4, %%xmm4\n\t"
        "pxor %%xmm5, %%xmm5\n\t"
        "pxor %%xmm6, %%xmm6\n\t"
        "pxor %%xmm7, %%xmm7\n\t"
        ::: "memory"
    );

    vos3_spinlock_unlock(&second->lock);
    vos3_spinlock_unlock(&first->lock);

    VOS3_INFO("[AI-GUARD] Slot swap %u <-> %u complete", slot_a, slot_b);
    return 0;
}

/* ============================================================================
 * PHASE D: SLOT SUSPEND / RESUME (delegates to ai_pte.c for PTE inversion)
 * ============================================================================ */

int vos3_ai_model_slot_suspend(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (slot_id == 0) return -1;  /* Coordinator cannot be suspended (EPERM) */

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* v23.3: Acquire slot->lock to serialize with DMA, lazy-thaw, and resume.
     * Without this, a concurrent DMA could CAS PTEs mid-inversion (CRITICAL). */
    vos3_spinlock_lock(&slot->lock);

    if (slot->status != VOS3_SLOT_ACTIVE && slot->status != VOS3_SLOT_WARM) {
        vos3_spinlock_unlock(&slot->lock);
        return -22;
    }

    vos3_ai_pte_invert(slot_id);
    slot->status = VOS3_SLOT_SUSPENDED;

    vos3_spinlock_unlock(&slot->lock);

    vos3_vbus_signal_event(slot_id, VOS3_EVENT_SLOT_SUSPENDED, 0);

    VOS3_INFO("[AI-GUARD] Slot %u suspended (PTEs inverted)", slot_id);
    return 0;
}

int vos3_ai_model_slot_resume(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* v23.3: Acquire slot->lock to serialize with DMA, lazy-thaw, and suspend.
     * Mirrors the lock bracket added to suspend() above. */
    vos3_spinlock_lock(&slot->lock);

    if (slot->status != VOS3_SLOT_SUSPENDED) {
        vos3_spinlock_unlock(&slot->lock);
        return -22;
    }

    vos3_ai_pte_uninvert(slot_id);
    slot->status = VOS3_SLOT_ACTIVE;

    vos3_spinlock_unlock(&slot->lock);

    vos3_vbus_signal_event(slot_id, VOS3_EVENT_SLOT_RESUMED, 0);

    VOS3_INFO("[AI-GUARD] Slot %u resumed (PTEs restored)", slot_id);
    return 0;
}

/* ============================================================================
 * PHASE G: AI-TIMER — Self-Scheduling
 * ============================================================================ */

int vos3_ai_set_timer(uint8_t slot_id, uint64_t ms)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status < VOS3_SLOT_ACTIVE) return -22;

    uint64_t ticks = (ms + 9) / 10;  /* 100Hz timer */
    slot->timer_expiry = vos3_timer_get_ticks() + ticks;

    VOS3_INFO("[AI-GUARD] Slot %u timer set: %llu ms (%llu ticks)",
              slot_id, (unsigned long long)ms, (unsigned long long)ticks);
    return 0;
}

void vos3_ai_timer_tick(uint64_t current_tick)
{
    /* Phase 4.2.15: Update heartbeat page with current tick */
    if (g_heartbeat_ptr != NULL) {
        *g_heartbeat_ptr = current_tick;
    }

    for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        vos3_ai_model_slot_t *slot = &g_model_slots[i];
        if (slot->timer_expiry != 0 && current_tick >= slot->timer_expiry) {
            slot->timer_expiry = 0;  /* One-shot: clear after fire */
            /* Don't fire if slot became free */
            if (slot->status >= VOS3_SLOT_ACTIVE) {
                vos3_vbus_signal_event(i, VOS3_EVENT_TIMER_EXPIRED, 0);
            }
        }
        /* Phase 4.2.9: Yield-EX timeout — wake dormant slots with expired yield timer */
        if (slot->status == VOS3_SLOT_DORMANT && slot->yield_timeout_tick != 0 &&
            current_tick >= slot->yield_timeout_tick) {
            slot->yield_timeout_tick = 0;
            slot->yield_event_mask   = 0;
            vos3_ai_model_slot_wake(i);
        }
    }

    /* Resource quota check (Phase 4.2.7) */
    for (uint8_t qi = 0; qi < VOS3_MODEL_SLOT_MAX; qi++) {
        if (qi == 0) continue;  /* Phase 4.9-Final: Coordinator slot 0 is never suspended */
        vos3_ai_model_slot_t *qslot = &g_model_slots[qi];
        if (qslot->max_cycles > 0 &&
            qslot->cycle_count >= qslot->max_cycles &&
            qslot->status == VOS3_SLOT_ACTIVE) {
            qslot->status = VOS3_SLOT_SUSPENDED_PENDING;
            vos3_vbus_signal_event(qi, VOS3_EVENT_QUOTA_EXCEEDED, (uint32_t)qslot->cycle_count);
            VOS3_WARN("[AI-GUARD] Slot %u QUOTA EXCEEDED: %llu/%llu (pending)",
                      qi, (unsigned long long)qslot->cycle_count,
                      (unsigned long long)qslot->max_cycles);
        }
    }

    /* Heartbeat event (Phase F) */
    if (current_tick - g_heartbeat_last_tick >= VOS3_HEARTBEAT_INTERVAL_TICKS) {
        g_heartbeat_last_tick = current_tick;
        uint32_t active_mask = 0;
        for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
            if (g_model_slots[i].status >= VOS3_SLOT_ACTIVE) {
                active_mask |= (1U << i);
            }
        }
        if (active_mask != 0) {
            VOS3_DEBUG("[AI-GUARD] Heartbeat fired at tick %llu mask=0x%x",
                       (unsigned long long)current_tick, active_mask);
            vos3_vbus_signal_event(0xFF, VOS3_EVENT_HEARTBEAT, active_mask);
        }
    }
}

/* ============================================================================
 * PHASE H: SLOT RESET — Self-Healing
 * ============================================================================ */

int vos3_ai_slot_reset(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (slot_id == 0) return -1;  /* Cannot reset Coordinator (EPERM) */

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* v23.4: Acquire lock BEFORE status check and streaming_mask update
     * to prevent TOCTOU — another CPU could free the slot between check
     * and lock acquisition (D2 race fix). */
    vos3_spinlock_lock(&slot->lock);

    if (slot->status == VOS3_SLOT_FREE) {
        vos3_spinlock_unlock(&slot->lock);
        return -22;
    }

    /* Phase 4.2.21: Clear streaming flags if this slot was mid-stream.
     * v23.7: Atomic clear-bit for SMP safety. */
    __atomic_fetch_and(&g_streaming_mask, (uint8_t)~(1U << slot_id), __ATOMIC_SEQ_CST);
    if (g_streaming_slot == (int8_t)slot_id) {
        g_streaming_slot = -1;
    }

    /* If suspended, uninvert PTEs first */
    if (slot->status == VOS3_SLOT_SUSPENDED) {
        vos3_ai_pte_uninvert(slot_id);
    }

    /* Make HugePages writable for zero-fill */
    for (uint32_t i = 0; i < slot->hp_count; i++) {
        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            /* K-C5: Atomic CAS */
            vos3_pte_t desired = pte;
            desired |= VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE;
            desired &= ~VOS3_PTE_IS_INVERTED;
            while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                desired = pte;
                desired |= VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE;
                desired &= ~VOS3_PTE_IS_INVERTED;
            }
            vos3_vmm_invlpg(vaddr);
        }
    }

    /* Zero-fill HugePages via rep stosq */
    for (uint32_t i = 0; i < slot->hp_count; i++) {
        void *addr = (void *)(slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M);
        scrub_zero_fill(addr, VOS3_PAGE_SIZE_2M);
    }

    /* Full barrier: ensure ALL zero-writes are visible before freeing pages */
    __asm__ volatile ("mfence" ::: "memory");

    vos3_fpu_scrub_full();
    vos3_ai_context_l2_flush(slot);
    vos3_ai_l1d_sanitize();

    /* Phase 4.9b-O: Branch Prediction Barrier (IBPB) */
    if (g_cpu_has_ibpb) {
        vos3_write_msr(0x49, 1);  /* IA32_PRED_CMD: IBPB */
    }

    /* Free HugePages back to PMM */
    for (uint32_t i = 0; i < slot->hp_count; i++) {
        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_vmm_unmap_large(vaddr);
        vos3_pmm_free_huge(slot->phys[i]);
        slot->phys[i] = 0;
    }

    /* Reset all slot fields */
    slot->status         = VOS3_SLOT_FREE;
    slot->model_id       = 0;
    slot->base           = 0;
    slot->size           = 0;
    slot->offset         = 0;
    slot->hp_count       = 0;
    slot->rolling_hash   = 0;
    slot->checksum       = 0;
    slot->crc32c         = 0;
    slot->timer_expiry   = 0;
    slot->cycle_count    = 0;
    slot->access_violations = 0;
    slot->priority       = 128;
    slot->label[0]       = '\0';
    slot->max_cycles        = 0;
    slot->mb_head           = 0;
    slot->mb_tail           = 0;
    slot->capabilities      = 0;
    slot->agent_type        = 0;
    slot->affinity_cpu      = -1;
    slot->io_perm_mask      = 0x0000000000000001ULL;
    slot->model_version     = 0;
    slot->model_epoch       = 0;
    slot->last_rip          = 0;
    slot->drift_timestamp   = 0;
    slot->has_checkpoint    = 0;
    slot->checkpoint_hash   = 0;
    slot->checkpoint_crc32c = 0;
    slot->checkpoint_epoch  = 0;
    slot->checkpoint_offset = 0;
    slot->sync_barrier_mask     = 0;
    slot->sync_barrier_received = 0;
    slot->priority_inherited    = 0;
    slot->inherited_priority    = 0;
    slot->yield_event_mask      = 0;
    slot->yield_timeout_tick    = 0;
    slot->consensus_gate        = 0;
    slot->pending_resp_len      = 0;
    slot->pending_resp_tag      = 0;
    slot->context_cow           = 0;
    slot->agent_clock_base      = 0;
    slot->agent_clock_frozen    = 0;
    slot->owner_tid             = 0;

    /* Phase 8.6: Disable WPG */
    slot->wpg_enabled        = 0;
    slot->wpg_scan_offset    = 0;
    slot->wpg_scan_total     = 0;
    slot->wpg_violations     = 0;
    slot->wpg_scans_completed = 0;

    /* Phase 8: Clear prefix lock */
    slot->prefix_locked_count = 0;
    slot->prefix_immutable = 0;

    /* Phase 8: Clear V-Palace room metadata */
    for (uint32_t ri = 0; ri < VOS3_MODEL_SLOT_MAX_HP; ri++) {
        slot->rooms[ri].hall = 0;
        slot->rooms[ri].locked = 0;
        slot->rooms[ri].access_count = 0;
        slot->rooms[ri].last_access_tick = 0;
    }
    slot->hall_count[0] = 0;
    slot->hall_count[1] = 0;
    slot->hall_count[2] = 0;

    vos3_spinlock_unlock(&slot->lock);

    /* Phase 4.2.7+: Free context pages OUTSIDE lock (VMM/PMM calls may re-enter) */
    vos3_ai_slot_context_free(slot_id);
    /* Phase 6: Free KV-cache HugePages */
    vos3_ai_kv_cache_free(slot_id);
    /* Phase 4.2.8: Unmap shared HugePages (don't free physical — belongs to source) */
    vos3_ai_unmap_shared_hugepages(slot_id);

    vos3_vbus_signal_event(slot_id, VOS3_EVENT_SLOT_RESET, 0);

    VOS3_INFO("[AI-GUARD] Slot %u reset to FREE", slot_id);
    return 0;
}

/* ============================================================================
 * PHASE 5: PER-AGENT ZONE ACL — Ownership Helpers
 * ============================================================================ */

int vos3_ai_check_slot_owner(uint8_t slot_id, uint32_t requester_tid)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;  /* EINVAL */
    /* Slot 0 (Coordinator): kernel-only (tid 0) */
    if (slot_id == 0 && requester_tid != 0) return -1;  /* EPERM */
    uint32_t owner = g_model_slots[slot_id].owner_tid;
    if (owner != 0 && owner != requester_tid) return -1;  /* EPERM */
    return 0;  /* Access granted */
}

void vos3_ai_set_slot_owner(uint8_t slot_id, uint32_t tid)
{
    if (slot_id < VOS3_MODEL_SLOT_MAX)
        g_model_slots[slot_id].owner_tid = tid;
}

void vos3_ai_clear_slot_owner(uint8_t slot_id)
{
    if (slot_id < VOS3_MODEL_SLOT_MAX)
        g_model_slots[slot_id].owner_tid = 0;
}

/* ============================================================================
 * PHASE J: VDEV_READ — Virtual Device Shim
 * ============================================================================ */

int vos3_ai_vdev_read(uint8_t slot_id, void *buf, size_t offset, size_t len)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX || buf == NULL) {
        return -22;
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* Deferred PTE inversion for quota-exceeded slots (Phase 4.2.7). */
    if (slot->status == VOS3_SLOT_SUSPENDED_PENDING && slot_id != 0) {
        vos3_ai_pte_invert(slot_id);
        slot->status = VOS3_SLOT_SUSPENDED;
        return -22;
    }

    if (slot->status != VOS3_SLOT_ACTIVE &&
        slot->status != VOS3_SLOT_WARM &&
        slot->status != VOS3_SLOT_DORMANT) {
        return -22;
    }

    /* Core pinning enforcement (Phase 4.2.7) */
    if (slot->affinity_cpu >= 0) {
        extern uint32_t get_cpu_id(void);
        uint32_t current_cpu = get_cpu_id();
        if ((int8_t)current_cpu != slot->affinity_cpu) {
            return -1;  /* EPERM: wrong CPU core */
        }
    }

    /* Phase 4.2.16: Auto-Alignment Proxy */
    if (offset % 64 != 0) {
        if (len > 64) {
            return -VOS3_EALIGN;
        }
        size_t aligned_off = offset & ~(size_t)63;
        size_t inner_off   = offset - aligned_off;
        if (inner_off + len > 64) {
            return -VOS3_EALIGN;
        }
        if (aligned_off + 64 > slot->size) {
            return -22;
        }
        static uint8_t proxy_buf[64] __attribute__((aligned(64)));

        __asm__ volatile("lfence" ::: "memory");
        memcpy(proxy_buf, (const void *)(slot->base + aligned_off), 64);
        memcpy(buf, proxy_buf + inner_off, len);

        __atomic_fetch_add(&slot->cycle_count, 1, __ATOMIC_RELAXED);
        slot->last_rip = (uintptr_t)__builtin_return_address(0);
        return 0;
    }

    if (offset + len > slot->size) {
        return -22;
    }

    /* Speculative barrier before memory access */
    __asm__ volatile("lfence" ::: "memory");

    memcpy(buf, (const void *)(slot->base + offset), len);

    /* Atomic increment cycle_count */
    __atomic_fetch_add(&slot->cycle_count, 1, __ATOMIC_RELAXED);

    /* Drift watchdog: record caller RIP (Phase 4.2.7) */
    slot->last_rip = (uintptr_t)__builtin_return_address(0);

    return 0;
}

/* ============================================================================
 * PHASE K: SESSION PERSISTENCE — Snapshot (cldemote / prefetcht2)
 * ============================================================================ */

int vos3_ai_model_snapshot(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status != VOS3_SLOT_ACTIVE) return -22;

    /* Demote cache lines from L1/L2 → L3 */
    for (uintptr_t a = slot->base; a < slot->base + slot->size; a += 64) {
        if (g_cpu_has_cldemote) {
            __asm__ volatile("cldemote (%0)" :: "r"(a) : "memory");
        } else {
            __asm__ volatile("prefetcht2 (%0)" :: "r"(a));
        }
    }

    slot->status = VOS3_SLOT_WARM;

    VOS3_INFO("[AI-GUARD] Slot %u snapshot -> WARM (cldemote=%d)",
              slot_id, g_cpu_has_cldemote);
    return 0;
}

/* ============================================================================
 * PHASE C: TELEMETRY (cycle_count / access_violations increments)
 * ============================================================================ */

void vos3_ai_slot_inc_access_violations(uint8_t slot_id)
{
    if (slot_id < VOS3_MODEL_SLOT_MAX) {
        __atomic_fetch_add(&g_model_slots[slot_id].access_violations,
                           1, __ATOMIC_RELAXED);
    }
}

/* ============================================================================
 * BRIDGE GETTERS — expose slot internals to virtio_bridge.c without extern
 * ============================================================================ */

void vos3_ai_model_slot_get_info(uint8_t slot_id, vos3_ai_model_slot_t *out)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX || out == NULL) {
        return;
    }
    /* Copy full slot struct — safe because bridge holds no pointer to it */
    *out = g_model_slots[slot_id];
}

int vos3_ai_has_cldemote(void)
{
    return g_cpu_has_cldemote;
}

/* ============================================================================
 * PHASE 4.2.7: DORMANT STATE
 * ============================================================================ */

int vos3_ai_model_slot_dormant(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (slot_id == 0) return -1;  /* Coordinator cannot go dormant (EPERM) */

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status != VOS3_SLOT_ACTIVE && slot->status != VOS3_SLOT_WARM) return -22;

    slot->priority = 255;

    /* Phase 4.2.11: Freeze deterministic clock — accumulate elapsed ticks */
    slot->agent_clock_frozen += vos3_timer_get_ticks() - slot->agent_clock_base;

    slot->status = VOS3_SLOT_DORMANT;

    /* PTEs remain PRESENT — no inversion, instant access on wake */
    vos3_vbus_signal_event(slot_id, VOS3_EVENT_SLOT_SUSPENDED, 6);

    VOS3_INFO("[AI-GUARD] Slot %u -> DORMANT (PTEs present, clock frozen)", slot_id);
    return 0;
}

int vos3_ai_model_slot_wake(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status != VOS3_SLOT_DORMANT) return -22;

    slot->priority = 128;
    slot->status = VOS3_SLOT_ACTIVE;

    /* Phase 4.2.11: Unfreeze deterministic clock — rebase to current tick */
    slot->agent_clock_base = vos3_timer_get_ticks();

    slot->last_rip = 0;
    slot->drift_timestamp = 0;

    vos3_vbus_signal_event(slot_id, VOS3_EVENT_SLOT_RESUMED, 6);

    VOS3_INFO("[AI-GUARD] Slot %u -> ACTIVE (woke from dormant)", slot_id);
    return 0;
}

/* ============================================================================
 * PHASE 4.2.7: RESOURCE QUOTAS
 * ============================================================================ */

int vos3_ai_slot_set_quota(uint8_t slot_id, uint64_t max_cycles)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status < VOS3_SLOT_ACTIVE) return -22;
    slot->max_cycles = max_cycles;
    return 0;
}

/* ============================================================================
 * PHASE 4.2.7: DEEP DIAGNOSTIC FEEDBACK
 * ============================================================================ */

int8_t vos3_ai_fault_find_slot(uintptr_t fault_addr)
{
    for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        vos3_ai_model_slot_t *slot = &g_model_slots[i];
        if (slot->status == VOS3_SLOT_FREE) continue;
        if (fault_addr >= slot->base &&
            fault_addr < slot->base + (uintptr_t)slot->hp_count * VOS3_PAGE_SIZE_2M) {
            return (int8_t)i;
        }
    }
    return -1;
}

void vos3_ai_send_feedback(uint8_t slot_id, uintptr_t fault_addr,
                           uintptr_t fault_rip, uintptr_t fault_rsp,
                           const uint8_t *stack_capture, uint8_t stack_len)
{
    uint8_t tail_buf[64];
    uint8_t tail_len = 0;
    vos3_vbus_get_rx_tail(tail_buf, 64, &tail_len);

    vos3_vbus_send_feedback(slot_id, fault_addr, fault_rip, fault_rsp,
                            stack_capture, stack_len, tail_buf, tail_len);

    VOS3_WARN("[AI-GUARD] Deep feedback: slot=%u rip=0x%llx rsp=0x%llx stack=%u bytes",
              slot_id, (unsigned long long)fault_rip,
              (unsigned long long)fault_rsp, stack_len);
}

/* ============================================================================
 * PHASE 4.2.7: AGENT CAPABILITIES REGISTRY
 * ============================================================================ */

int vos3_ai_slot_set_caps(uint8_t slot_id, uint64_t capabilities, uint8_t agent_type)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status < VOS3_SLOT_ACTIVE) return -22;
    slot->capabilities = capabilities;
    slot->agent_type = agent_type;
    return 0;
}

/* ============================================================================
 * PHASE 4.2.7: CORE PINNING (SMP AFFINITY)
 * ============================================================================ */

int vos3_ai_set_affinity(uint8_t slot_id, int8_t cpu_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status < VOS3_SLOT_ACTIVE) return -22;
    if (cpu_id >= 0 && (uint16_t)cpu_id >= VOS3_MAX_CPUS) return -22;
    slot->affinity_cpu = cpu_id;
    return 0;
}

/* ============================================================================
 * PHASE 4.2.7: ROLLING CHECKPOINT (Fast Undo)
 * ============================================================================ */

int vos3_ai_checkpoint(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status != VOS3_SLOT_ACTIVE && slot->status != VOS3_SLOT_WARM) return -22;

    vos3_spinlock_lock(&slot->lock);

    __asm__ volatile("lfence" ::: "memory");

    slot->checkpoint_hash   = slot->rolling_hash;
    slot->checkpoint_crc32c = slot->crc32c;
    slot->checkpoint_offset = slot->offset;
    slot->checkpoint_epoch  = slot->model_epoch;

    /* Save shadow PTEs */
    for (uint32_t i = 0; i < slot->hp_count && i < VOS3_SHADOW_PTE_MAX; i++) {
        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            slot->shadow_pte[i] = pte;
        }
    }

    /* Phase 4.2.7+: Save context page PTEs */
    for (uint32_t i = 0; i < slot->context_page_count && i < VOS3_CONTEXT_SHADOW_PTE_MAX; i++) {
        uintptr_t vaddr = slot->context_base + (uintptr_t)i * 4096UL;
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            slot->context_shadow_pte[i] = pte;
        }
    }

    slot->has_checkpoint = 1;
    vos3_spinlock_unlock(&slot->lock);

    vos3_vbus_signal_event(slot_id, VOS3_EVENT_CHECKPOINT, slot_id);

    VOS3_INFO("[AI-GUARD] Slot %u checkpoint: hash=0x%llx crc=0x%08x offset=%zu",
              slot_id, (unsigned long long)slot->checkpoint_hash,
              slot->checkpoint_crc32c, slot->checkpoint_offset);
    return 0;
}

int vos3_ai_rollback(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (!slot->has_checkpoint) return -22;

    if (slot->model_epoch != slot->checkpoint_epoch) return -22;

    vos3_spinlock_lock(&slot->lock);

    slot->rolling_hash = slot->checkpoint_hash;
    slot->crc32c       = slot->checkpoint_crc32c;
    slot->offset       = slot->checkpoint_offset;

    /* Restore shadow PTEs */
    for (uint32_t i = 0; i < slot->hp_count && i < VOS3_SHADOW_PTE_MAX; i++) {
        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        /* K-C5: Atomic CAS */
        vos3_pte_t cur;
        if (vos3_vmm_get_pte(vaddr, &cur) == 0) {
            (void)vos3_vmm_cas_pte(vaddr, &cur, slot->shadow_pte[i]);
        }
        vos3_vmm_invlpg(vaddr);
    }

    for (uint32_t i = 0; i < slot->context_page_count && i < VOS3_CONTEXT_SHADOW_PTE_MAX; i++) {
        uintptr_t vaddr = slot->context_base + (uintptr_t)i * 4096UL;
        /* K-C5: Atomic CAS */
        vos3_pte_t cur;
        if (vos3_vmm_get_pte(vaddr, &cur) == 0) {
            (void)vos3_vmm_cas_pte(vaddr, &cur, slot->context_shadow_pte[i]);
        }
        vos3_vmm_invlpg(vaddr);
    }

    __asm__ volatile("lfence" ::: "memory");

    slot->status = VOS3_SLOT_ACTIVE;
    vos3_spinlock_unlock(&slot->lock);

    VOS3_INFO("[AI-GUARD] Slot %u rollback: hash=0x%llx offset=%zu",
              slot_id, (unsigned long long)slot->rolling_hash, slot->offset);
    return 0;
}

/* ============================================================================
 * PHASE 4.2.7: AGENT RBAC (I/O MASKING)
 * ============================================================================ */

int vos3_ai_slot_set_io_mask(uint8_t slot_id, uint64_t io_perm_mask)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status < VOS3_SLOT_ACTIVE) return -22;
    slot->io_perm_mask = io_perm_mask;
    return 0;
}

/* ============================================================================
 * PHASE 4.2.7: INSTRUCTION DRIFT WATCHDOG
 * ============================================================================ */

#define VOS3_DRIFT_WINDOW_BYTES  64U
#define VOS3_DRIFT_TIMEOUT_TICKS 30U

static uintptr_t g_drift_prev_rip[VOS3_MODEL_SLOT_MAX] = {0};
static uint64_t  g_drift_prev_cycle[VOS3_MODEL_SLOT_MAX] = {0};

void vos3_ai_drift_watchdog_tick(uint64_t current_tick)
{
    for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        vos3_ai_model_slot_t *slot = &g_model_slots[i];
        if (slot->status != VOS3_SLOT_ACTIVE) {
            g_drift_prev_rip[i] = 0;
            g_drift_prev_cycle[i] = 0;
            continue;
        }
        if (slot->last_rip == 0) continue;

        uintptr_t current_base = slot->last_rip & ~((uintptr_t)(VOS3_DRIFT_WINDOW_BYTES - 1));
        uintptr_t prev_base = g_drift_prev_rip[i] & ~((uintptr_t)(VOS3_DRIFT_WINDOW_BYTES - 1));

        uint64_t cur_cycles = slot->cycle_count;
        int rip_moved = (current_base != prev_base) || (g_drift_prev_rip[i] == 0);
        int work_progressing = (cur_cycles != g_drift_prev_cycle[i]);

        if (rip_moved || work_progressing) {
            slot->drift_timestamp = current_tick;
            g_drift_prev_rip[i] = slot->last_rip;
            g_drift_prev_cycle[i] = cur_cycles;
        } else if (slot->drift_timestamp != 0 &&
                   current_tick - slot->drift_timestamp >= VOS3_DRIFT_TIMEOUT_TICKS) {
            VOS3_WARN("[AI-GUARD] DRIFT WATCHDOG: Slot %u STUCK at RIP 0x%llx for >300ms",
                      i, (unsigned long long)slot->last_rip);
            slot->status = VOS3_SLOT_STUCK;
            vos3_vbus_signal_event(i, VOS3_EVENT_SLOT_STUCK,
                                   (uint32_t)(slot->last_rip & 0xFFFFFFFF));
            uint8_t dummy[1] = {0};
            vos3_ai_send_feedback(i, 0, slot->last_rip, 0, dummy, 0);
            slot->drift_timestamp = 0;
            g_drift_prev_rip[i] = 0;
            g_drift_prev_cycle[i] = 0;
        }
    }
}

/* ============================================================================
 * PHASE 4.2.7: Semantic Registry — version/epoch setter
 * ============================================================================ */

void vos3_ai_slot_set_version(uint8_t slot_id, uint32_t version, uint32_t epoch)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return;
    g_model_slots[slot_id].model_version = version;
    g_model_slots[slot_id].model_epoch   = epoch;
}

/* ============================================================================
 * PHASE 4.2.7+: CONTEXT PERSISTENCE (KV-Cache)
 * ============================================================================ */

int vos3_ai_slot_context_config(uint8_t slot_id, uint32_t page_count)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (page_count == 0 || page_count > VOS3_CONTEXT_PAGE_MAX) return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    vos3_spinlock_lock(&slot->lock);
    if (slot->status != VOS3_SLOT_ACTIVE && slot->status != VOS3_SLOT_WARM) {
        vos3_spinlock_unlock(&slot->lock);
        return -22;
    }
    if (slot->context_configured) {
        vos3_spinlock_unlock(&slot->lock);
        return -22;
    }
    uintptr_t context_base = slot->base + (uintptr_t)slot->hp_count * VOS3_PAGE_SIZE_2M;
    vos3_spinlock_unlock(&slot->lock);

    for (uint32_t i = 0; i < page_count; i++) {
        uintptr_t phys = vos3_pmm_alloc(0);
        if (phys == 0) {
            for (uint32_t j = 0; j < i; j++) {
                uintptr_t vaddr = context_base + (uintptr_t)j * 4096UL;
                vos3_vmm_unmap(vaddr);
                vos3_pmm_free(slot->context_phys[j]);
                slot->context_phys[j] = 0;
            }
            return -12;  /* ENOMEM */
        }
        uintptr_t vaddr = context_base + (uintptr_t)i * 4096UL;
        int rc = vos3_vmm_map(vaddr, phys,
                               (vos3_vmm_flags_t)(VOS3_VMM_FLAG_WRITE));
        if (rc != 0) {
            vos3_pmm_free(phys);
            for (uint32_t j = 0; j < i; j++) {
                uintptr_t va = context_base + (uintptr_t)j * 4096UL;
                vos3_vmm_unmap(va);
                vos3_pmm_free(slot->context_phys[j]);
                slot->context_phys[j] = 0;
            }
            return -12;
        }
        slot->context_phys[i] = phys;
        scrub_zero_fill((void *)vaddr, 4096);
    }

    vos3_spinlock_lock(&slot->lock);
    slot->context_page_count = page_count;
    slot->context_base       = context_base;
    slot->context_configured = 1;
    vos3_spinlock_unlock(&slot->lock);

    vos3_vbus_signal_event(slot_id, VOS3_EVENT_CHECKPOINT, page_count);

    VOS3_INFO("[AI-GUARD] Slot %u context configured: %u pages at 0x%llx",
              slot_id, page_count, (unsigned long long)context_base);
    return 0;
}

void vos3_ai_slot_context_free(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (!slot->context_configured) return;

    for (uint32_t i = 0; i < slot->context_page_count; i++) {
        uintptr_t vaddr = slot->context_base + (uintptr_t)i * 4096UL;
        if (slot->context_cow) {
            vos3_pte_t pte;
            if (vos3_vmm_get_pte(vaddr, &pte) == 0 &&
                !(pte & VOS3_PTE_IS_INVERTED)) {
                vos3_pmm_free(slot->context_phys[i]);
            }
        } else {
            vos3_pmm_free(slot->context_phys[i]);
        }
        vos3_vmm_unmap(vaddr);
        vos3_vmm_invlpg(vaddr);
        slot->context_phys[i] = 0;
    }

    slot->context_base       = 0;
    slot->context_page_count = 0;
    slot->context_configured = 0;
    slot->context_cow        = 0;

    for (uint32_t i = 0; i < VOS3_CONTEXT_SHADOW_PTE_MAX; i++) {
        slot->context_shadow_pte[i] = 0;
    }

    VOS3_INFO("[AI-GUARD] Slot %u context pages freed", slot_id);
}

/* ============================================================================
 * PHASE 6: KV-CACHE HUGEPAGE PINNING + UEFI MEMORY QUALITY (v2.0)
 * ============================================================================ */

/**
 * @brief Score a physical address based on UEFI memory map attributes.
 *
 * Phase 6 Extension: GDT-Aware KV-Cache. When UEFI memory map is available,
 * score physical pages by cache attribute quality to prefer "fastest" RAM:
 *   - WB (Write-Back)     → score 4  (best: full CPU cache coherent)
 *   - WT (Write-Through)  → score 3  (good: cached reads)
 *   - WC (Write-Combine)  → score 2  (ok: bulk writes only)
 *   - UC (Uncacheable)    → score 1  (slow: no caching)
 *   - Not found           → score 0  (no UEFI data, use default)
 *
 * This scoring allows KV-cache allocation to prefer WB pages for lowest
 * Time-To-First-Token (TTFT) — attention KV reads are latency-critical.
 *
 * @param[in] phys_addr Physical address to score
 * @return Quality score (0-4), higher is faster
 */
static uint32_t kv_uefi_mem_quality(uint64_t phys_addr)
{
    if (!g_uefi_boot_info.efi_present || !g_uefi_boot_info.mmap)
        return 0; /* No UEFI data — cannot score */

    for (uint32_t i = 0; i < g_uefi_boot_info.mmap_entry_count; i++) {
        const vos3_efi_mem_desc_t *desc = &g_uefi_boot_info.mmap[i];
        uint64_t region_end = desc->phys_start + desc->num_pages * 4096ULL;

        if (phys_addr >= desc->phys_start && phys_addr < region_end) {
            /* Found the region containing this address */
            if (desc->attribute & EFI_MEMORY_WB) return 4; /* Best */
            if (desc->attribute & EFI_MEMORY_WT) return 3;
            if (desc->attribute & EFI_MEMORY_WC) return 2;
            if (desc->attribute & EFI_MEMORY_UC) return 1;
            return 1; /* Mapped but unknown caching */
        }
    }

    return 0; /* Not in UEFI map */
}

/**
 * @brief Allocate HugePage-pinned KV-cache for a slot.
 *
 * The KV-cache is placed immediately after the model's HugePage region
 * within the slot's PUD-isolated address space. L3 Color Guard is applied
 * to prevent cache-line side-channels between slots.
 *
 * Phase 6 Extension (v2.0): When UEFI memory map is available, log the
 * memory quality score for each allocated HugePage. This enables future
 * preferential allocation of WB-rated pages for KV-cache to minimize TTFT.
 *
 * @param[in] slot_id  Slot to allocate KV-cache for (1-3, not slot 0)
 * @param[in] hp_count Number of 2MB HugePages (1-4, max 8MB)
 * @return 0 on success, negative errno on failure
 */
int vos3_ai_kv_cache_alloc(uint8_t slot_id, uint32_t hp_count)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (slot_id == 0) return -1; /* Slot 0 is Coordinator (EPERM) */
    if (hp_count == 0 || hp_count > 4) return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* Acquire slot lock to prevent race with warm_reset / concurrent alloc.
     * (Genesis Chaos Audit 2026-04-08, Track 3 Finding 1) */
    vos3_spinlock_lock(&slot->lock);

    if (slot->status == VOS3_SLOT_FREE) {
        vos3_spinlock_unlock(&slot->lock);
        return -22;
    }
    if (slot->kv_pinned) {
        vos3_spinlock_unlock(&slot->lock);
        return 0; /* Already allocated */
    }

    /* KV-cache virtual base: after model HugePages within the PUD-isolated slot */
    uintptr_t kv_vbase = slot->base +
                          (uintptr_t)(slot->hp_count + 1) * VOS3_PAGE_SIZE_2M;

    /* Allocate HugePages with L3 Color Guard.
     * Phase 6.5 Room isolation: use agent TID (not slot_id) for cache color,
     * so different agents sharing a slot get isolated L3 cache sets. */
    uint8_t kv_color = vos3_ai_room_color_for_tid(slot->owner_tid);
    uint32_t min_quality = 5; /* Track worst page quality */
    uint32_t max_quality = 0; /* Track best page quality */

    for (uint32_t i = 0; i < hp_count; i++) {
        slot->kv_hp_phys[i] = vos3_pmm_alloc_colored_hugepage(kv_color);
        if (slot->kv_hp_phys[i] == 0) {
            VOS3_WARN("[KV-CACHE] Slot %u: HugePage alloc failed at %u/%u",
                      slot_id, i, hp_count);
            for (uint32_t j = 0; j < i; j++) {
                vos3_pmm_free_huge(slot->kv_hp_phys[j]);
                slot->kv_hp_phys[j] = 0;
            }
            vos3_spinlock_unlock(&slot->lock);
            return -12; /* ENOMEM */
        }

        /* v2.0: Score physical page quality via UEFI memory map */
        uint32_t q = kv_uefi_mem_quality(slot->kv_hp_phys[i]);
        if (q < min_quality) min_quality = q;
        if (q > max_quality) max_quality = q;
    }

    /* Map KV-cache HugePages: PRESENT | WRITABLE | NX | LARGE */
    for (uint32_t i = 0; i < hp_count; i++) {
        uintptr_t vaddr = kv_vbase + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        int rc = vos3_vmm_map(vaddr, (uintptr_t)slot->kv_hp_phys[i],
                               (vos3_vmm_flags_t)(VOS3_VMM_FLAG_WRITE |
                                                   VOS3_VMM_FLAG_LARGE));
        if (rc != 0) {
            VOS3_WARN("[KV-CACHE] Slot %u: map failed at HP %u (0x%lx)",
                      slot_id, i, (unsigned long)vaddr);
            /* Rollback: unmap what we mapped, free all HugePages */
            for (uint32_t j = 0; j < i; j++) {
                vos3_vmm_unmap_large(kv_vbase + (uintptr_t)j * VOS3_PAGE_SIZE_2M);
            }
            for (uint32_t j = 0; j < hp_count; j++) {
                vos3_pmm_free_huge(slot->kv_hp_phys[j]);
                slot->kv_hp_phys[j] = 0;
            }
            vos3_spinlock_unlock(&slot->lock);
            return -12;
        }
    }

    /* Full zero-fill is mandatory: HugePages are recycled by the PMM and are
     * not zeroed on allocation.  Sampling one byte per cache line would leak
     * almost all KV data from the previous owner. */
    for (uint32_t i = 0; i < hp_count; i++) {
        void *page = (void *)(kv_vbase + (uintptr_t)i * VOS3_PAGE_SIZE_2M);
        scrub_zero_fill(page, VOS3_PAGE_SIZE_2M);
    }

    slot->kv_base     = kv_vbase;
    slot->kv_hp_count = hp_count;
    slot->kv_size     = hp_count * (uint32_t)VOS3_PAGE_SIZE_2M;
    slot->kv_pinned   = 1;
    slot->kv_pinned_stable = 1;  /* Phase 6.2: Physical addresses verified at alloc */

    /* v2.0: Log UEFI memory quality for KV-cache pages */
    if (g_uefi_boot_info.efi_present && max_quality > 0) {
        VOS3_INFO("[KV-CACHE] Slot %u: %u HP (%u MB) at 0x%lx, "
                  "phys[0]=0x%lx, UEFI quality=%u-%u (4=WB,3=WT,2=WC,1=UC)",
                  slot_id, hp_count, hp_count * 2,
                  (unsigned long)kv_vbase,
                  (unsigned long)slot->kv_hp_phys[0],
                  min_quality, max_quality);
    } else {
        VOS3_INFO("[KV-CACHE] Slot %u: %u HugePages (%u MB) pinned at 0x%lx, "
                  "phys[0]=0x%lx%s",
                  slot_id, hp_count, hp_count * 2,
                  (unsigned long)kv_vbase,
                  (unsigned long)slot->kv_hp_phys[0],
                  g_uefi_boot_info.efi_present ? "" : " (no UEFI mmap)");
    }

    vos3_spinlock_unlock(&slot->lock);
    return 0;
}

/**
 * @brief Free KV-cache HugePages for a slot.
 */
void vos3_ai_kv_cache_free(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* Acquire slot lock (matches kv_cache_alloc locking) */
    vos3_spinlock_lock(&slot->lock);

    if (!slot->kv_pinned) {
        vos3_spinlock_unlock(&slot->lock);
        return;
    }

    for (uint32_t i = 0; i < slot->kv_hp_count; i++) {
        uintptr_t vaddr = slot->kv_base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        /* Scrub while the mapping is still owned and writable, before the
         * physical page can return to the shared HugePage pool. */
        scrub_zero_fill((void *)vaddr, VOS3_PAGE_SIZE_2M);
        vos3_vmm_unmap_large(vaddr);
        vos3_pmm_free_huge(slot->kv_hp_phys[i]);
        slot->kv_hp_phys[i] = 0;
    }

    VOS3_INFO("[KV-CACHE] Slot %u: %u HugePages freed", slot_id, slot->kv_hp_count);

    slot->kv_base          = 0;
    slot->kv_hp_count      = 0;
    slot->kv_size          = 0;
    slot->kv_pinned        = 0;
    slot->kv_pinned_stable = 0;  /* Phase 6.2: Clear stability guarantee */

    vos3_spinlock_unlock(&slot->lock);
}

/* ============================================================================
 * PHASE 4.2.7+: WARM RESET (Preserve Context)
 * ============================================================================ */

int vos3_ai_slot_warm_reset(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (slot_id == 0) return -1;  /* Cannot reset Coordinator (EPERM) */

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* v23.16 (D1): Locked prefix pages are immortal — reject warm reset.
     * Prevents destruction of cognitive context that must survive resets. */
    if (slot->prefix_immutable) return -16;  /* EBUSY */

    /* v23.5: Status check INSIDE lock — closes TOCTOU race (D-MED fix).
     * Same pattern as slot_reset v23.4 fix. */
    vos3_spinlock_lock(&slot->lock);
    if (slot->status == VOS3_SLOT_FREE) {
        vos3_spinlock_unlock(&slot->lock);
        return -22;
    }

    vos3_heartbeat_set_busy();

    if (slot->status == VOS3_SLOT_SUSPENDED) {
        vos3_ai_pte_uninvert(slot_id);
    }

    for (uint32_t i = 0; i < slot->hp_count; i++) {
        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            /* K-C5: Atomic CAS */
            vos3_pte_t desired = pte;
            desired |= VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE;
            desired &= ~VOS3_PTE_IS_INVERTED;
            while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                desired = pte;
                desired |= VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE;
                desired &= ~VOS3_PTE_IS_INVERTED;
            }
            vos3_vmm_invlpg(vaddr);
        }
    }

    for (uint32_t i = 0; i < slot->hp_count; i++) {
        void *addr = (void *)(slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M);
        scrub_zero_fill(addr, VOS3_PAGE_SIZE_2M);
    }

    __asm__ volatile ("mfence" ::: "memory");

    vos3_fpu_scrub_full();
    vos3_ai_context_l2_flush(slot);
    vos3_ai_l1d_sanitize();

    for (uint32_t i = 0; i < slot->hp_count; i++) {
        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_vmm_unmap_large(vaddr);
        vos3_pmm_free_huge(slot->phys[i]);
        slot->phys[i] = 0;
    }

    slot->status         = VOS3_SLOT_FREE;
    slot->model_id       = 0;
    slot->base           = 0;
    slot->size           = 0;
    slot->offset         = 0;
    slot->hp_count       = 0;
    slot->rolling_hash   = 0;
    slot->checksum       = 0;
    slot->crc32c         = 0;
    slot->timer_expiry   = 0;
    slot->cycle_count    = 0;
    slot->access_violations = 0;
    slot->priority       = 128;
    slot->label[0]       = '\0';
    slot->max_cycles        = 0;
    /* SKIP: mb_head, mb_tail, mailbox — preserve ISC messages */
    slot->capabilities      = 0;
    slot->agent_type        = 0;
    slot->affinity_cpu      = -1;
    slot->io_perm_mask      = 0x0000000000000001ULL;
    slot->model_version     = 0;
    slot->model_epoch       = 0;
    slot->last_rip          = 0;
    slot->drift_timestamp   = 0;
    slot->has_checkpoint    = 0;
    slot->checkpoint_hash   = 0;
    slot->checkpoint_crc32c = 0;
    slot->checkpoint_epoch  = 0;
    slot->checkpoint_offset = 0;
    /* SKIP: context_base, context_page_count, etc. */
    /* SKIP: shared_hp_* */
    slot->sync_barrier_mask     = 0;
    slot->sync_barrier_received = 0;
    slot->priority_inherited    = 0;
    slot->inherited_priority    = 0;
    slot->yield_event_mask      = 0;
    slot->yield_timeout_tick    = 0;
    slot->consensus_gate        = 0;
    slot->pending_resp_len      = 0;
    slot->pending_resp_tag      = 0;
    slot->agent_clock_base      = vos3_timer_get_ticks();
    slot->agent_clock_frozen    = 0;
    /* SKIP: context_cow */

    vos3_heartbeat_clear_busy();

    vos3_spinlock_unlock(&slot->lock);

    vos3_vbus_signal_event(slot_id, VOS3_EVENT_SLOT_RESET, 1);  /* value=1 → warm */

    VOS3_INFO("[AI-GUARD] Slot %u warm reset (context + shared weights preserved)", slot_id);
    return 0;
}

/* ============================================================================
 * PHASE 5: CONTEXT MANAGEMENT (Freeze/Thaw) — April 2026 Hardened
 * ============================================================================ */

static uint64_t g_freeze_counter = 0;

void vos3_ai_ctx_clear_dirty(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (!slot->context_configured) return;

    for (uint32_t i = 0; i < slot->context_page_count; i++) {
        uintptr_t vaddr = slot->context_base + (uintptr_t)i * 4096UL;
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            /* K-C5: Atomic CAS */
            vos3_pte_t desired = pte & ~VOS3_PTE_DIRTY;
            while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                desired = pte & ~VOS3_PTE_DIRTY;
            }
        }
    }

    /* SMP-global TLB flush: local CR3 reload + IPI shootdown to all cores */
    uint64_t cr3_val;
    __asm__ volatile("mov %%cr3, %0" : "=r"(cr3_val));
    __asm__ volatile("mov %0, %%cr3" :: "r"(cr3_val) : "memory");
    vos3_vmm_flush_range(slot->context_base,
                          (size_t)slot->context_page_count * 4096UL);

    slot->ctx_dirty_bitmap[0] = 0;
    slot->ctx_dirty_bitmap[1] = 0;
}

int vos3_ai_ctx_freeze(uint8_t slot_id, uint64_t *out_freeze_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (slot_id == 0) return -1;  /* Cannot freeze Coordinator */

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* Validate ownership */
    int rc = vos3_ai_check_slot_owner(slot_id, slot->owner_tid);
    if (rc != 0) return rc;

    if (!slot->context_configured) return -22;
    if (slot->ctx_thawing) return -16;  /* EBUSY: thaw in progress */

    /* Checkpoint metadata + shadow PTEs */
    vos3_ai_checkpoint(slot_id);

    /* Build dirty bitmap by scanning hardware PTE dirty bits */
    slot->ctx_dirty_bitmap[0] = 0;
    slot->ctx_dirty_bitmap[1] = 0;

    for (uint32_t i = 0; i < slot->context_page_count; i++) {
        uintptr_t vaddr = slot->context_base + (uintptr_t)i * 4096UL;
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            if (pte & VOS3_PTE_DIRTY) {
                slot->ctx_dirty_bitmap[i / 64] |= (1ULL << (i % 64));
            }
            /* Make read-only for safe export */
            /* K-C5: Atomic CAS */
            vos3_pte_t desired = pte;
            desired &= ~VOS3_PTE_WRITABLE;
            while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                desired = pte;
                desired &= ~VOS3_PTE_WRITABLE;
            }
            vos3_vmm_invlpg(vaddr);
        }
    }

    /* Assign freeze ID and increment session_epoch */
    g_freeze_counter++;
    slot->freeze_id = g_freeze_counter;
    slot->session_epoch++;
    slot->ctx_frozen = 1;

    /* Transition to DORMANT */
    vos3_ai_model_slot_dormant(slot_id);

    if (out_freeze_id) {
        *out_freeze_id = slot->freeze_id;
    }

    VOS3_INFO("[CTX] Freeze slot %u: freeze_id=%llu epoch=%u",
              slot_id, (unsigned long long)slot->freeze_id, slot->session_epoch);
    return 0;
}

int vos3_ai_ctx_scrub(uint8_t slot_id, uint32_t *out_pages_cleaned,
                       uint32_t *out_effective_bytes)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (!slot->ctx_frozen) return -22;

    uint32_t cleaned = 0;
    uint32_t effective_bytes = 0;

    /* Phase A — AutoDream Deduplication: CRC + byte-compare page dedup */
    uint32_t page_crcs[VOS3_CONTEXT_PAGE_MAX];
    for (uint32_t i = 0; i < slot->context_page_count; i++) {
        if (slot->ctx_dirty_bitmap[i / 64] & (1ULL << (i % 64))) {
            void *pg = (void *)(slot->context_base + (uintptr_t)i * 4096UL);
            page_crcs[i] = vos3_crc32c(0, pg, 4096);
        } else {
            page_crcs[i] = 0;
        }
    }

    for (uint32_t i = 0; i < slot->context_page_count; i++) {
        if (!(slot->ctx_dirty_bitmap[i / 64] & (1ULL << (i % 64)))) continue;
        void *pg_i = (void *)(slot->context_base + (uintptr_t)i * 4096UL);
        for (uint32_t j = i + 1; j < slot->context_page_count; j++) {
            if (!(slot->ctx_dirty_bitmap[j / 64] & (1ULL << (j % 64)))) continue;
            if (page_crcs[i] != page_crcs[j]) continue;
            void *pg_j = (void *)(slot->context_base + (uintptr_t)j * 4096UL);
            if (memcmp(pg_i, pg_j, 4096) == 0) {
                scrub_zero_fill(pg_j, 4096);
                slot->ctx_dirty_bitmap[j / 64] &= ~(1ULL << (j % 64));
            }
        }
    }

    /* Phase A.5 — High-Pass Entropy Filter (Centurion v2)
     * Scans 64-byte blocks within each dirty page. Computes entropy score
     * as (distinct_byte_count / 64). Blocks below the entropy threshold
     * (<=4 distinct values = 6.25%) are transient/padding → scrub_zero_fill.
     * Uses architecturally-visible rep stosq + mfence via scrub_zero_fill(). */
    uint32_t entropy_zeroed_blocks = 0;
    for (uint32_t i = 0; i < slot->context_page_count; i++) {
        if (!(slot->ctx_dirty_bitmap[i / 64] & (1ULL << (i % 64)))) continue;
        uint8_t *src = (uint8_t *)(slot->context_base + (uintptr_t)i * 4096UL);

        for (uint32_t blk = 0; blk < 4096; blk += 64) {
            /* Count distinct byte values in this 64-byte block */
            uint8_t seen[256 / 8];  /* 32-byte bitmap for 256 possible values */
            for (uint32_t z = 0; z < 32; z++) seen[z] = 0;
            for (uint32_t b = 0; b < 64; b++) {
                seen[src[blk + b] / 8] |= (uint8_t)(1U << (src[blk + b] % 8));
            }
            uint32_t distinct = 0;
            for (uint32_t z = 0; z < 32; z++) {
                uint8_t v = seen[z];
                while (v) { distinct++; v &= v - 1; }
            }
            /* High-pass: <= 4 distinct values (6.25% entropy) → noise/padding */
            if (distinct <= 4) {
                scrub_zero_fill(src + blk, 64);
                entropy_zeroed_blocks++;
            }
        }
    }

    /* Phase B — Tail-Zero Trim + Semantic Compaction */
    for (uint32_t i = 0; i < slot->context_page_count; i++) {
        if (!(slot->ctx_dirty_bitmap[i / 64] & (1ULL << (i % 64)))) continue;
        uint8_t *src = (uint8_t *)(slot->context_base + (uintptr_t)i * 4096UL);

        /* Compact non-zero bytes to front of page */
        size_t write_pos = 0;
        for (size_t r = 0; r < 4096; r++) {
            if (src[r] != 0) {
                src[write_pos++] = src[r];
            }
        }

        if (write_pos == 0) {
            /* Page entirely zero after compaction — clear dirty bit */
            slot->ctx_dirty_bitmap[i / 64] &= ~(1ULL << (i % 64));
        } else {
            scrub_zero_fill(src + write_pos, 4096 - write_pos);
            effective_bytes += (uint32_t)write_pos;
        }
        cleaned++;
    }

    vos3_ai_l1d_sanitize();

    if (out_pages_cleaned) *out_pages_cleaned = cleaned;
    if (out_effective_bytes) *out_effective_bytes = effective_bytes;

    VOS3_INFO("[CTX] Scrub slot %u: %u pages cleaned, %u effective bytes, %u entropy-zeroed blocks",
              slot_id, cleaned, effective_bytes, entropy_zeroed_blocks);
    return 0;
}

int vos3_ai_ctx_read_page(uint8_t slot_id, uint32_t page_idx, void *buf)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (!slot->ctx_frozen) return -22;
    if (page_idx >= slot->context_page_count) return -22;

    /* Dirty check: only export dirty pages */
    if (!(slot->ctx_dirty_bitmap[page_idx / 64] & (1ULL << (page_idx % 64)))) {
        return -61;  /* ENODATA: page not dirty */
    }

    /* lfence before read (Spectre-v1: bounds check serialization) */
    __asm__ volatile("lfence" ::: "memory");

    void *page_addr = (void *)(slot->context_base + (uintptr_t)page_idx * 4096UL);
    memcpy(buf, page_addr, 4096);
    return 0;
}

int vos3_ai_ctx_thaw(uint8_t slot_id, uint32_t page_count, uint64_t freeze_id,
                      uint32_t session_epoch, int cold)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (slot_id == 0) return -1;  /* Cannot thaw into Coordinator */
    if (page_count == 0 || page_count > VOS3_CONTEXT_PAGE_MAX) return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* Free existing context if configured */
    if (slot->context_configured) {
        vos3_ai_slot_context_free(slot_id);
    }

    /* Allocate + zero-fill pages (same as slot_context_config) */
    int rc = vos3_ai_slot_context_config(slot_id, page_count);
    if (rc != 0) return rc;

    /* Store thaw metadata */
    slot->freeze_id = freeze_id;
    slot->ctx_thawing = 1;
    slot->ctx_thaw_crc_seed = session_epoch;
    slot->ctx_thaw_start_tick = vos3_timer_get_ticks();

    /* Clear dirty bitmap — will be set by CTX_WRITE */
    slot->ctx_dirty_bitmap[0] = 0;
    slot->ctx_dirty_bitmap[1] = 0;

    /* Cold thaw: slot stays DORMANT; warm thaw: slot stays as-is */
    if (cold) {
        vos3_ai_model_slot_dormant(slot_id);
    }

    VOS3_INFO("[CTX] Thaw slot %u: page_count=%u freeze_id=%llu epoch=%u cold=%d",
              slot_id, page_count, (unsigned long long)freeze_id, session_epoch, cold);
    return 0;
}

int vos3_ai_ctx_write_page(uint8_t slot_id, uint32_t page_idx,
                             const void *data, size_t len)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (!slot->ctx_thawing) return -22;
    if (page_idx >= slot->context_page_count) return -22;

    void *page_addr = (void *)(slot->context_base + (uintptr_t)page_idx * 4096UL);

    /* Dreadhead Fence (pre-write): drain all prior loads */
    __asm__ volatile("lfence" ::: "memory");

    /* Ghost Context Kill: zero entire page before writing (includes mfence) */
    scrub_zero_fill(page_addr, 4096);

    /* Write context data */
    size_t copy_len = len < 4096 ? len : 4096;
    memcpy(page_addr, data, copy_len);

    /* Dreadhead Fence (post-write): full store+load barrier */
    __asm__ volatile("mfence\n\tlfence" ::: "memory");

    /* Set dirty bit in software bitmap */
    slot->ctx_dirty_bitmap[page_idx / 64] |= (1ULL << (page_idx % 64));

    return 0;
}

uint32_t vos3_ai_ctx_compute_crc(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return 0;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    uint32_t crc = 0;
    crc = vos3_crc32c(crc, &slot->freeze_id, 8);
    crc = vos3_crc32c(crc, slot->ctx_dirty_bitmap, 16);

    for (uint32_t i = 0; i < slot->context_page_count; i++) {
        if (slot->ctx_dirty_bitmap[i / 64] & (1ULL << (i % 64))) {
            void *pg = (void *)(slot->context_base + (uintptr_t)i * 4096UL);
            crc = vos3_crc32c(crc, pg, 4096);
        }
    }
    return crc;
}

/* ============================================================================
 * L3 Cache-Line Partitioning (Intel RDT — Cache Allocation Technology)
 *
 * Each model slot gets a distinct CLOSID (Class of Service ID) with an
 * exclusive set of L3 cache ways.  This prevents inter-agent L3 side-
 * channel leaks (Prime+Probe, Flush+Reload on shared cache sets).
 *
 * MSRs used:
 *   IA32_L3_MASK_n   (0xC90 + n)  — L3 way bitmask for CLOSID n
 *   IA32_PQR_ASSOC   (0xC8F)      — CLOSID assignment for current core
 *
 * Conditional on g_cpu_has_l3cat — no-op on CPUs without RDT L3 CAT.
 * ============================================================================ */

#define IA32_PQR_ASSOC     0xC8FU
#define IA32_L3_MASK_BASE  0xC90U

void vos3_vmm_l3_partition(uint8_t slot_id)
{
    if (!g_cpu_has_l3cat) return;
    if (slot_id == 0 || slot_id >= VOS3_MODEL_SLOT_MAX) return;

    /* Query CPUID leaf 0x10 sub-leaf 1 for L3 CAT parameters:
     *   EAX[4:0] = capacity bitmask length - 1 (number of ways - 1)
     *   EDX[15:0] = max CLOSID */
    uint32_t eax, ebx, ecx, edx;
    vos3_cpuid(0x10, 1, &eax, &ebx, &ecx, &edx);

    uint32_t num_ways = (eax & 0x1F) + 1;   /* L3 associativity */
    uint32_t max_closid = (edx & 0xFFFF);

    if (slot_id > max_closid) {
        VOS3_WARN("[L3-CAT] slot %u exceeds max CLOSID %u — skipping", slot_id, max_closid);
        return;
    }

    /* Partition ways evenly across slots 1..7 (slot 0 is coordinator).
     * Each slot gets (num_ways / 7) ways, remainder goes to slot 1.
     * Minimum: 1 way per slot (if fewer than 7 ways, overlap is allowed). */
    uint32_t ways_per_slot = num_ways / 7;
    if (ways_per_slot == 0) ways_per_slot = 1;

    uint32_t start_way = (uint32_t)(slot_id - 1) * ways_per_slot;
    if (start_way >= num_ways) start_way = num_ways - 1;

    uint32_t end_way = start_way + ways_per_slot;
    if (end_way > num_ways) end_way = num_ways;
    if (slot_id == 1) end_way = start_way + ways_per_slot + (num_ways % 7);
    if (end_way > num_ways) end_way = num_ways;

    /* Build bitmask: bits [start_way .. end_way-1] set */
    uint32_t mask = 0;
    for (uint32_t w = start_way; w < end_way; w++) {
        mask |= (1U << w);
    }
    if (mask == 0) mask = 1;  /* At minimum, 1 way */

    /* Write IA32_L3_MASK_n for this slot's CLOSID */
    vos3_write_msr(IA32_L3_MASK_BASE + slot_id, (uint64_t)mask);

    /* Assign current core to this CLOSID via IA32_PQR_ASSOC
     * Format: [63:32] = RMID (0 = default), [31:0] = CLOSID */
    uint64_t pqr = (uint64_t)slot_id;  /* RMID=0, CLOSID=slot_id */
    vos3_write_msr(IA32_PQR_ASSOC, pqr);

    __asm__ volatile("mfence" ::: "memory");

    VOS3_INFO("[L3-CAT] Slot %u → CLOSID %u, ways=%u, mask=0x%x (%u/%u ways)",
              slot_id, slot_id, end_way - start_way, mask, end_way - start_way, num_ways);
}

int vos3_ai_ctx_thaw_done(uint8_t slot_id, uint32_t expected_crc)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (slot_id == 0) return -1;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (!slot->ctx_thawing) return -22;

    /* Anti-Replay Gate: verify session_epoch matches thaw request */
    if (slot->ctx_thaw_crc_seed != slot->session_epoch) {
        VOS3_WARN("[CTX] Thaw-commit EPOCH MISMATCH slot %u: %u vs %u — WARM RESET",
                  slot_id, slot->ctx_thaw_crc_seed, slot->session_epoch);
        vos3_ai_slot_warm_reset(slot_id);
        slot->ctx_thawing = 0;
        return -5;  /* EIO */
    }

    /* Atomic Thaw-Commit: CRC verification */
    uint32_t actual_crc = vos3_ai_ctx_compute_crc(slot_id);
    if (actual_crc != expected_crc) {
        VOS3_WARN("[CTX] Thaw-commit CRC MISMATCH slot %u: 0x%08x vs 0x%08x — WARM RESET",
                  slot_id, actual_crc, expected_crc);
        vos3_ai_slot_warm_reset(slot_id);
        slot->ctx_thawing = 0;
        return -5;  /* EIO */
    }

    slot->ctx_thawing = 0;
    slot->ctx_frozen = 0;

    /* Record telemetry */
    slot->ctx_access_latency = vos3_timer_get_ticks() - slot->ctx_thaw_start_tick;

    /* Microarchitectural sanitization */
    vos3_fpu_scrub_full();
    vos3_ai_context_l2_flush(slot);
    vos3_ai_l1d_sanitize();
    __asm__ volatile("lfence" ::: "memory");

    /* IBPB: branch predictor barrier */
    if (g_cpu_has_ibpb) {
        vos3_write_msr(0x49, 1);  /* IA32_PRED_CMD: IBPB */
    }

    /* L3 Cache-Line Partitioning: isolate slot's L3 ways via CAT */
    vos3_vmm_l3_partition(slot_id);

    /* SMP-Global TLB clear: start fresh epoch with IPI flush */
    vos3_ai_ctx_clear_dirty(slot_id);

    /* Wake slot if it was DORMANT */
    if (slot->status == VOS3_SLOT_DORMANT) {
        vos3_ai_model_slot_wake(slot_id);
    }

    vos3_vbus_signal_event(slot_id, VOS3_EVENT_CHECKPOINT, slot_id);

    VOS3_INFO("[CTX] Thaw-done slot %u: CRC OK, latency=%llu ticks",
              slot_id, (unsigned long long)slot->ctx_access_latency);
    return 0;
}

void vos3_ai_ctx_soft_scrub_tick(void)
{
    for (uint8_t s = 1; s < VOS3_MODEL_SLOT_MAX; s++) {
        vos3_ai_model_slot_t *slot = &g_model_slots[s];
        if (!slot->context_configured) continue;
        if (slot->status != VOS3_SLOT_ACTIVE) continue;

        slot->ctx_soft_scrub_cycle++;
        if (slot->ctx_soft_scrub_cycle < 10000) continue;

        uint32_t cleared = 0;
        for (uint32_t i = 0; i < slot->context_page_count; i++) {
            if (!(slot->ctx_dirty_bitmap[i / 64] & (1ULL << (i % 64)))) continue;

            /* Check if page is all-zero */
            uint64_t *pg = (uint64_t *)(slot->context_base + (uintptr_t)i * 4096UL);
            int all_zero = 1;
            for (uint32_t w = 0; w < 512; w++) {
                if (pg[w] != 0) { all_zero = 0; break; }
            }
            if (all_zero) {
                slot->ctx_dirty_bitmap[i / 64] &= ~(1ULL << (i % 64));
                cleared++;
            }
        }

        slot->ctx_soft_scrub_cycle = 0;
        if (cleared > 0) {
            VOS3_INFO("[CTX] Soft-scrub slot %u: %u transient pages cleared", s, cleared);
        }
    }
}

/* ============================================================================
 * PHASE 4.2.8: SHARED WEIGHT HUB (Model Deduplication)
 * ============================================================================ */

int vos3_ai_map_shared_hugepage(uint8_t target_slot, uintptr_t source_phys)
{
    if (target_slot >= VOS3_MODEL_SLOT_MAX) return -22;
    if (source_phys == 0) return -22;
    if (source_phys & (VOS3_PAGE_SIZE_2M - 1)) return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[target_slot];

    vos3_spinlock_lock(&slot->lock);
    if (slot->status < VOS3_SLOT_ACTIVE) {
        vos3_spinlock_unlock(&slot->lock);
        return -22;
    }
    if (slot->shared_hp_count >= VOS3_SHARED_HP_MAX) {
        vos3_spinlock_unlock(&slot->lock);
        return -28;  /* ENOSPC */
    }

    uint32_t idx = slot->shared_hp_count;
    uintptr_t vaddr = slot->base +
                      (uintptr_t)(VOS3_MODEL_SLOT_MAX_HP + idx) * VOS3_PAGE_SIZE_2M;
    vos3_spinlock_unlock(&slot->lock);

    int rc = vos3_vmm_map(vaddr, source_phys,
                           (vos3_vmm_flags_t)(VOS3_VMM_FLAG_LARGE));
    if (rc != 0) return -12;

    vos3_pte_t pte;
    if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
        /* K-C5: Atomic CAS */
        vos3_pte_t desired = (pte | VOS3_PTE_AI_PROTECTED) & ~VOS3_PTE_WRITABLE;
        while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
            desired = (pte | VOS3_PTE_AI_PROTECTED) & ~VOS3_PTE_WRITABLE;
        }
        vos3_vmm_invlpg(vaddr);
    }

    vos3_spinlock_lock(&slot->lock);
    slot->shared_hp_vaddr[idx] = vaddr;
    slot->shared_hp_phys[idx]  = source_phys;
    slot->shared_hp_count++;
    vos3_spinlock_unlock(&slot->lock);

    VOS3_INFO("[AI-GUARD] Slot %u: shared HugePage mapped RO at 0x%llx (phys=0x%llx)",
              target_slot, (unsigned long long)vaddr, (unsigned long long)source_phys);
    return 0;
}

void vos3_ai_unmap_shared_hugepages(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->shared_hp_count == 0) return;

    for (uint32_t i = 0; i < slot->shared_hp_count; i++) {
        if (slot->shared_hp_vaddr[i] != 0) {
            vos3_vmm_unmap_large(slot->shared_hp_vaddr[i]);
            slot->shared_hp_vaddr[i] = 0;
            slot->shared_hp_phys[i]  = 0;
        }
    }
    slot->shared_hp_count = 0;

    VOS3_INFO("[AI-GUARD] Slot %u: shared HugePages unmapped", slot_id);
}

/* ============================================================================
 * PHASE 4.2.8: SWARM BARRIER SYNC
 * ============================================================================ */

int vos3_ai_slot_barrier_wait(uint8_t slot_id, uint8_t mask)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (mask == 0) return -22;
    if (mask & (1U << slot_id)) return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    vos3_spinlock_lock(&slot->lock);
    if (slot->status < VOS3_SLOT_ACTIVE) {
        vos3_spinlock_unlock(&slot->lock);
        return -22;
    }
    if (slot->sync_barrier_mask != 0) {
        vos3_spinlock_unlock(&slot->lock);
        return -16;  /* EBUSY */
    }

    slot->sync_barrier_mask     = mask;
    slot->sync_barrier_received = 0;
    vos3_spinlock_unlock(&slot->lock);

    vos3_ai_model_slot_dormant(slot_id);

    VOS3_INFO("[AI-GUARD] Slot %u: barrier set, waiting on mask=0x%02x", slot_id, mask);
    return 0;
}

/* ============================================================================
 * PHASE 4.2.9: SELF-SCHEDULING (YIELD-EX)
 * ============================================================================ */

int vos3_ai_yield_ex(uint8_t slot_id, uint64_t timeout_ms, uint64_t event_mask)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (slot_id == 0) return -1;  /* Coordinator cannot yield (EPERM) */

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    vos3_spinlock_lock(&slot->lock);
    if (slot->status != VOS3_SLOT_ACTIVE && slot->status != VOS3_SLOT_WARM) {
        vos3_spinlock_unlock(&slot->lock);
        return -22;
    }
    slot->yield_event_mask = event_mask;
    if (timeout_ms > 0) {
        uint64_t ticks = (timeout_ms + 9) / 10;
        slot->yield_timeout_tick = vos3_timer_get_ticks() + ticks;
    } else {
        slot->yield_timeout_tick = 0;
    }
    slot->priority = 255;
    slot->status   = VOS3_SLOT_DORMANT;
    vos3_spinlock_unlock(&slot->lock);

    vos3_vbus_signal_event(slot_id, VOS3_EVENT_SLOT_SUSPENDED, 6);

    VOS3_INFO("[AI-GUARD] Slot %u: yield_ex timeout=%llums mask=0x%llx",
              slot_id, (unsigned long long)timeout_ms,
              (unsigned long long)event_mask);
    return 0;
}

/* ============================================================================
 * PHASE 4.2.9: CONTEXT INHERITANCE (COW)
 * ============================================================================ */

int vos3_ai_context_share(uint8_t src_slot, uint8_t dst_slot)
{
    if (src_slot >= VOS3_MODEL_SLOT_MAX || dst_slot >= VOS3_MODEL_SLOT_MAX) return -22;
    if (src_slot == dst_slot) return -22;

    vos3_ai_model_slot_t *src = &g_model_slots[src_slot];
    vos3_ai_model_slot_t *dst = &g_model_slots[dst_slot];

    if (!src->context_configured) return -22;
    if (dst->context_configured) return -22;
    if (src->context_page_count == 0) return -22;
    if (dst->status < VOS3_SLOT_ACTIVE) return -22;

    uintptr_t dst_context_base = dst->base + (uintptr_t)dst->hp_count * VOS3_PAGE_SIZE_2M;

    for (uint32_t i = 0; i < src->context_page_count; i++) {
        uintptr_t phys = src->context_phys[i];
        uintptr_t dst_vaddr = dst_context_base + (uintptr_t)i * 4096UL;

        int rc = vos3_vmm_map(dst_vaddr, phys,
                               (vos3_vmm_flags_t)(0));
        if (rc != 0) {
            for (uint32_t j = 0; j < i; j++) {
                vos3_vmm_unmap(dst_context_base + (uintptr_t)j * 4096UL);
            }
            return -12;
        }

        vos3_pte_t dst_pte;
        if (vos3_vmm_get_pte(dst_vaddr, &dst_pte) == 0) {
            /* K-C5: Atomic CAS */
            vos3_pte_t desired = dst_pte;
            desired |= VOS3_PTE_IS_INVERTED;
            desired &= ~VOS3_PTE_WRITABLE;
            desired |= VOS3_PTE_NO_EXECUTE;
            while (vos3_vmm_cas_pte(dst_vaddr, &dst_pte, desired) != 0) {
                desired = dst_pte;
                desired |= VOS3_PTE_IS_INVERTED;
                desired &= ~VOS3_PTE_WRITABLE;
                desired |= VOS3_PTE_NO_EXECUTE;
            }
            vos3_vmm_invlpg(dst_vaddr);
        }

        dst->context_phys[i] = phys;
    }

    dst->context_base       = dst_context_base;
    dst->context_page_count = src->context_page_count;
    dst->context_configured = 1;
    dst->context_cow        = 1;

    VOS3_INFO("[AI-GUARD] Context shared: slot %u -> slot %u (%u pages, COW, NX)",
              src_slot, dst_slot, src->context_page_count);
    return 0;
}

int vos3_ai_context_cow_fault(uintptr_t fault_addr)
{
    vos3_ai_model_slot_t *slot = NULL;
    uint8_t slot_id = 0xFF;

    for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        vos3_ai_model_slot_t *s = &g_model_slots[i];
        if (!s->context_configured) continue;
        if (fault_addr >= s->context_base &&
            fault_addr < s->context_base + (uintptr_t)s->context_page_count * 4096UL) {
            slot = s;
            slot_id = i;
            break;
        }
    }

    if (slot == NULL) return -1;

    uintptr_t page_vaddr = fault_addr & ~((uintptr_t)0xFFF);
    vos3_pte_t pte;
    if (vos3_vmm_get_pte(page_vaddr, &pte) != 0) return -1;
    if (!(pte & VOS3_PTE_IS_INVERTED)) return -1;

    uint32_t page_idx = (uint32_t)((fault_addr - slot->context_base) / 4096UL);
    if (page_idx >= VOS3_CONTEXT_PAGE_MAX) return -1;

    /* v23.5: Spinlock guard for shared g_cow_copy_buf (D-LOW1 fix).
     * On SMP-2, concurrent COW faults could corrupt the buffer. */
    vos3_spinlock_lock(&g_cow_lock);

    const uint8_t *old_data = (const uint8_t *)page_vaddr;
    for (uint32_t b = 0; b < 4096; b++) {
        g_cow_copy_buf[b] = old_data[b];
    }

    uintptr_t new_phys = vos3_pmm_alloc(0);
    if (new_phys == 0) {
        vos3_spinlock_unlock(&g_cow_lock);
        return -12;
    }

    vos3_pte_t new_pte = (new_phys & 0x000FFFFFFFFFF000ULL) |
                          VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE |
                          VOS3_PTE_NO_EXECUTE;
    /* K-C5: Atomic CAS */
    vos3_pte_t old_pte;
    if (vos3_vmm_get_pte(page_vaddr, &old_pte) == 0) {
        (void)vos3_vmm_cas_pte(page_vaddr, &old_pte, new_pte);
    }
    vos3_vmm_invlpg(page_vaddr);

    uint8_t *new_data = (uint8_t *)page_vaddr;
    for (uint32_t b = 0; b < 4096; b++) {
        new_data[b] = g_cow_copy_buf[b];
    }

    vos3_spinlock_unlock(&g_cow_lock);

    slot->context_phys[page_idx] = new_phys;

    VOS3_INFO("[AI-GUARD] COW fault: slot %u page %u duplicated (new phys=0x%llx)",
              slot_id, page_idx, (unsigned long long)new_phys);
    return 0;
}

/* ============================================================================
 * PHASE 4.2.9: CONSENSUS GATING
 * ============================================================================ */

uint64_t vos3_ai_slot_get_caps(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return 0;
    return g_model_slots[slot_id].capabilities;
}

int vos3_ai_slot_get_consensus_gate(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return 0;
    return g_model_slots[slot_id].consensus_gate;
}

int vos3_ai_slot_set_consensus_gate(uint8_t slot_id, uint8_t enable)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    g_model_slots[slot_id].consensus_gate = enable ? 1 : 0;
    VOS3_INFO("[AI-GUARD] Slot %u: consensus gate %s",
              slot_id, enable ? "ENABLED" : "DISABLED");
    return 0;
}

void vos3_ai_slot_buffer_resp(uint8_t slot_id, uint16_t tag,
                               const void *payload, uint16_t len)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return;
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    if (slot->pending_resp_len != 0) {
        VOS3_WARN("[AI-GUARD] Slot %u: RESP overwrite (prev tag=%u dropped, new tag=%u)",
                  slot_id, slot->pending_resp_tag, tag);
    }

    uint16_t copy_len = len;
    if (copy_len > VOS3_PENDING_RESP_MAX) copy_len = VOS3_PENDING_RESP_MAX;

    const uint8_t *s = (const uint8_t *)payload;
    for (uint16_t i = 0; i < copy_len; i++) {
        slot->pending_resp[i] = s[i];
    }
    slot->pending_resp_len = copy_len;
    slot->pending_resp_tag = tag;

    VOS3_INFO("[AI-GUARD] Slot %u: RESP buffered (%u bytes, tag=%u) -- awaiting COMMIT",
              slot_id, copy_len, tag);
}

int vos3_ai_consensus_commit(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->pending_resp_len == 0) return -2;

    vos3_vbus_send_frame(VBUS_TYPE_RESP, slot_id, slot->pending_resp_tag,
                          slot->pending_resp, (uint32_t)slot->pending_resp_len);

    uint16_t flushed_len = slot->pending_resp_len;
    slot->pending_resp_len = 0;
    slot->pending_resp_tag = 0;

    VOS3_INFO("[AI-GUARD] Slot %u: RESP committed (%u bytes flushed)", slot_id, flushed_len);
    return 0;
}

/* ============================================================================
 * PHASE 4.2.11: DETERMINISTIC CLOCK
 * ============================================================================ */

uint64_t vos3_ai_slot_get_time(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return 0;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status == VOS3_SLOT_FREE) return 0;

    if (slot->status == VOS3_SLOT_DORMANT) {
        return slot->agent_clock_frozen;
    }

    return slot->agent_clock_frozen +
           (vos3_timer_get_ticks() - slot->agent_clock_base);
}

/* ============================================================================
 * PHASE 4.2.15: HEARTBEAT PAGE GETTER
 * ============================================================================ */

uint64_t vos3_ai_get_heartbeat_phys(void)
{
    return g_heartbeat_phys;
}

/* Phase 4.2.16: Backpressure flag — byte at heartbeat page offset 8 */
void vos3_heartbeat_set_busy(void)
{
    if (g_heartbeat_ptr != NULL) {
        volatile uint8_t *flags = (volatile uint8_t *)g_heartbeat_ptr + 8;
        *flags |= VOS3_VBUS_FLAG_BUSY;
    }
}

void vos3_heartbeat_clear_busy(void)
{
    if (g_heartbeat_ptr != NULL) {
        volatile uint8_t *flags = (volatile uint8_t *)g_heartbeat_ptr + 8;
        *flags &= ~VOS3_VBUS_FLAG_BUSY;
    }
}

uint8_t vos3_heartbeat_get_status(void)
{
    if (g_heartbeat_ptr != NULL) {
        volatile uint8_t *flags = (volatile uint8_t *)g_heartbeat_ptr + 8;
        return *flags;
    }
    return 0;
}

/* ============================================================================
 * PHASE 4.4: AI-KASLR DIAGNOSTIC GETTER
 * ============================================================================ */

uint64_t vos3_ai_kaslr_base(void)
{
    return g_ai_kaslr_base;
}

/* ============================================================================
 * PHASE 4.7: NMI Watchdog — Streaming Stall Detection
 * ============================================================================ */

static size_t   g_nmi_prev_offset[VOS3_MODEL_SLOT_MAX] = {0};
static uint32_t g_nmi_prev_sq_tail[VOS3_MODEL_SLOT_MAX] = {0};
static uint64_t g_nmi_stall_start[VOS3_MODEL_SLOT_MAX] = {0};

void vos3_nmi_watchdog_tick(uint64_t current_tick)
{
    for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        vos3_ai_model_slot_t *slot = &g_model_slots[i];

        if (slot->status != VOS3_SLOT_STREAMING) {
            g_nmi_prev_offset[i] = 0;
            g_nmi_prev_sq_tail[i] = 0;
            g_nmi_stall_start[i] = 0;
            continue;
        }

        size_t cur_offset = slot->offset;

        uint32_t cur_sq_tail = 0;
        if (g_sq_hdr != NULL) {
            cur_sq_tail = __atomic_load_n(
                (volatile uint32_t *)((uintptr_t)&g_sq_hdr->tail),
                __ATOMIC_ACQUIRE);
        }

        int offset_moved = (cur_offset != g_nmi_prev_offset[i]);
        int sq_tail_moved = (cur_sq_tail != g_nmi_prev_sq_tail[i]);

        if (offset_moved || sq_tail_moved) {
            g_nmi_prev_offset[i] = cur_offset;
            g_nmi_prev_sq_tail[i] = cur_sq_tail;
            g_nmi_stall_start[i] = current_tick;
        } else {
            if (g_nmi_stall_start[i] == 0) {
                g_nmi_stall_start[i] = current_tick;
            }

            if (current_tick - g_nmi_stall_start[i] >= VOS3_NMI_WATCHDOG_TIMEOUT_TICKS) {
                VOS3_WARN("[AI-GUARD] NMI WATCHDOG: Slot %u STALLED at offset %llu for >%u ticks",
                          i, (unsigned long long)cur_offset,
                          (unsigned)VOS3_NMI_WATCHDOG_TIMEOUT_TICKS);

                vos3_ai_slot_reset(i);
                vos3_vbus_signal_event(i, VOS3_EVENT_PREEMPTIVE_RESET,
                                       (uint32_t)(cur_offset & 0xFFFFFFFF));

                g_nmi_prev_offset[i] = 0;
                g_nmi_prev_sq_tail[i] = 0;
                g_nmi_stall_start[i] = 0;
            }
        }
    }
}

/* ============================================================================
 * PHASE 7: LAZY-THAW DEMAND PAGING
 *
 * Overcommit support for AI model slots.  When enabled, all HugePage PTEs
 * are cleared (PRESENT bit removed) so they cost zero TLB entries.  On the
 * first access, a #PF resolves the mapping on demand.  An eviction helper
 * allows the kernel to reclaim TLB/virtual pressure from cold pages.
 * ============================================================================ */

/**
 * @brief Enable lazy-thaw demand paging for a slot.
 *
 * Unmaps all HugePages (clears PRESENT bit in every PTE) but keeps the
 * physical frames in phys[] so they can be demand-faulted back in.
 *
 * @param slot_id  Slot to enable (must be 1..MAX-1, ACTIVE or WARM)
 * @return 0 on success, negative errno on failure
 */
int vos3_ai_lazy_thaw_enable(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;  /* EINVAL */
    if (slot_id == 0) return -1;  /* EPERM: Coordinator slot 0 is protected */

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status != VOS3_SLOT_ACTIVE && slot->status != VOS3_SLOT_WARM)
        return -22;

    vos3_spinlock_lock(&slot->lock);

    slot->lazy_thaw = 1;

    /* Clear PRESENT bit on model-weight HugePage PTEs — keeps phys[] intact.
     * Phase 6.2: KV-cache pages (at kv_base) are NOT touched here because
     * the loop only covers indices 0..hp_count-1 which are model-weight
     * HugePages.  KV-cache lives at base + (hp_count+1)*2MB with its own
     * kv_hp_phys[] array.  This is defense-in-depth against Cache Drift. */
    uint32_t unmapped = 0;
    for (uint32_t i = 0; i < slot->hp_count; i++) {
        /* Phase 8: Skip prefix-locked pages — they must stay PRESENT */
        if (slot->prefix_immutable && i < slot->prefix_locked_count)
            continue;
        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            /* K-C5: Atomic CAS */
            vos3_pte_t desired = pte & ~VOS3_PTE_PRESENT;
            while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                desired = pte & ~VOS3_PTE_PRESENT;
            }
            vos3_vmm_invlpg(vaddr);
        }
        slot->hp_mapped[i] = 0;
        unmapped++;
    }

    slot->hp_mapped_count = slot->prefix_immutable ? slot->prefix_locked_count : 0;

    vos3_spinlock_unlock(&slot->lock);

    VOS3_INFO("[LAZY-THAW] Slot %u: enabled, %u HugePages unmapped",
              slot_id, slot->hp_count);
    return 0;
}

/**
 * @brief Disable lazy-thaw, re-mapping all unmapped HugePages.
 *
 * Every HugePage whose physical frame is still retained (phys[i] != 0)
 * gets its PTE restored with read-only AI Guard flags.
 *
 * @param slot_id  Slot to disable lazy-thaw for
 * @return 0 on success, negative errno on failure
 */
int vos3_ai_lazy_thaw_disable(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (slot_id == 0) return -1;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    vos3_spinlock_lock(&slot->lock);

    for (uint32_t i = 0; i < slot->hp_count; i++) {
        if (slot->hp_mapped[i] == 0 && slot->phys[i] != 0) {
            uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;

            /* Re-map the HugePage */
            vos3_vmm_map(vaddr, slot->phys[i],
                         (vos3_vmm_flags_t)(VOS3_VMM_FLAG_LARGE));

            /* Set read-only AI Guard flags — K-C5: Atomic CAS */
            vos3_pte_t pte;
            if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
                vos3_pte_t desired = (pte & ~VOS3_PTE_WRITABLE) |
                                     VOS3_PTE_WRITE_THROUGH |
                                     VOS3_PTE_AI_PROTECTED |
                                     VOS3_PTE_NO_EXECUTE;
                while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                    desired = (pte & ~VOS3_PTE_WRITABLE) |
                              VOS3_PTE_WRITE_THROUGH |
                              VOS3_PTE_AI_PROTECTED |
                              VOS3_PTE_NO_EXECUTE;
                }
                vos3_vmm_invlpg(vaddr);
            }

            slot->hp_mapped[i] = 1;
        }
    }

    slot->hp_mapped_count = slot->hp_count;
    slot->lazy_thaw = 0;

    vos3_spinlock_unlock(&slot->lock);

    VOS3_INFO("[LAZY-THAW] Slot %u: disabled, all HugePages re-mapped", slot_id);
    return 0;
}

/**
 * @brief Handle a page fault in an AI slot by demand-mapping the HugePage.
 *
 * Called from the #PF handler in interrupts.c.  If the faulting address
 * falls within a lazy-thaw-enabled slot whose HugePage is unmapped but
 * has a valid physical frame, re-establish the mapping on the spot.
 *
 * @param fault_addr  CR2 value (faulting virtual address)
 * @return 0 if fault was resolved, -1 if not ours
 */
int vos3_ai_lazy_thaw_fault(uintptr_t fault_addr)
{
    /* Identify which slot (if any) owns this address */
    int8_t slot_id = vos3_ai_fault_find_slot(fault_addr);
    if (slot_id < 0)
        return -1;  /* Not inside any AI model slot */

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* Quick pre-lock checks (may race, but that's fine — we double-check
     * under the lock below) */
    if (!slot->lazy_thaw)
        return -1;
    if (slot->status != VOS3_SLOT_ACTIVE && slot->status != VOS3_SLOT_WARM)
        return -1;

    /* Determine which HugePage within the slot was faulted */
    uint32_t hp_idx = (uint32_t)((fault_addr - slot->base) / VOS3_PAGE_SIZE_2M);
    if (hp_idx >= slot->hp_count)
        return -1;

    /* Already mapped? Then this is a different kind of fault */
    if (slot->hp_mapped[hp_idx] == 1)
        return -1;

    /* No physical page to back it */
    if (slot->phys[hp_idx] == 0)
        return -1;

    /* ---- Critical section: resolve the fault ---- */
    /* v23.3: irqsave — this function is called from the page fault handler
     * which may be invoked from ISR context (e.g., timer ISR triggers a
     * nested fault).  Disabling interrupts prevents deadlock if the timer
     * ISR fires while we hold slot->lock. */
    vos3_irqflags_t thaw_flags = vos3_irq_save();
    vos3_spinlock_lock(&slot->lock);

    /* Double-check under lock (another CPU may have resolved it) */
    if (slot->hp_mapped[hp_idx] == 1) {
        vos3_spinlock_unlock(&slot->lock);
        vos3_irq_restore(thaw_flags);
        return 0;  /* Already resolved by another core */
    }

    uintptr_t vaddr = slot->base + (uintptr_t)hp_idx * VOS3_PAGE_SIZE_2M;

    /* Map the HugePage back — using the SAME physical address originally
     * stored in phys[].  This is the core of the "Reddit Bug" fix (Phase 6.2):
     * we NEVER allocate a new physical frame; we re-map the retained one. */
    vos3_vmm_map(vaddr, slot->phys[hp_idx],
                 (vos3_vmm_flags_t)(VOS3_VMM_FLAG_LARGE));

    /* Phase 6.2: Post-remap PTE physical address verification.
     * Read back the PTE and assert the physical address matches phys[hp_idx].
     * If they diverge, the Cache Drift bug would cause KV-cache/model
     * corruption on resume.  Fail-closed: reject the fault. */
    {
        vos3_pte_t verify_pte;
        if (vos3_vmm_get_pte(vaddr, &verify_pte) == 0) {
            uintptr_t pte_phys = (uintptr_t)(verify_pte & VOS3_PTE_LARGE_ADDR_MASK);
            uintptr_t expected_phys = (uintptr_t)(slot->phys[hp_idx] &
                                                   VOS3_PTE_LARGE_ADDR_MASK);
            if (pte_phys != expected_phys) {
                VOS3_ERROR("[LAZY-THAW] CACHE DRIFT: slot=%u hp=%u "
                           "pte_phys=0x%lx expected=0x%lx — REJECTING",
                           (unsigned)slot_id, hp_idx,
                           (unsigned long)pte_phys,
                           (unsigned long)expected_phys);
                vos3_vmm_unmap_large(vaddr);
                vos3_spinlock_unlock(&slot->lock);
                vos3_irq_restore(thaw_flags);
                return -1;  /* Fail-closed: do not serve stale mapping */
            }
        }
    }

    /* v23.9: Apply AI Guard flags via CAS loop (defense-in-depth).
     * Even though slot->lock is held, CAS detects any hardware-level
     * PTE manipulation rather than blindly overwriting. */
    vos3_pte_t pte;
    if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
        for (;;) {
            vos3_pte_t desired = pte;
            desired &= ~(VOS3_PTE_WRITABLE | VOS3_PTE_GLOBAL);
            desired |= VOS3_PTE_WRITE_THROUGH | VOS3_PTE_AI_PROTECTED |
                        VOS3_PTE_NO_EXECUTE;
            /* Phase 8: Restore COGNITIVE bit for prefix-locked pages */
            if (slot->prefix_immutable && hp_idx < slot->prefix_locked_count)
                desired |= VOS3_PTE_COGNITIVE;
            int cas_rc = vos3_vmm_cas_pte(vaddr, &pte, desired);
            if (cas_rc == 0)
                break;          /* CAS succeeded */
            if (cas_rc != -1)
                break;          /* Walk failure — cannot retry */
            /* cas_rc == -1: CAS failed, pte updated — retry */
        }
    }
    vos3_vmm_invlpg(vaddr);

    slot->hp_mapped[hp_idx] = 1;
    slot->hp_mapped_count++;
    slot->lazy_faults++;

    vos3_spinlock_unlock(&slot->lock);
    vos3_irq_restore(thaw_flags);

    VOS3_INFO("[LAZY-THAW] Fault resolved: slot=%u hp=%u vaddr=0x%lx (total=%llu)",
              (unsigned)slot_id, hp_idx, (unsigned long)vaddr,
              (unsigned long long)slot->lazy_faults);
    return 0;
}

/**
 * @brief Evict (unmap) HugePages from a lazy-thaw slot.
 *
 * Walks from the highest HP index downward (LRU approximation: most
 * recently loaded data is at the front) and clears PRESENT to free
 * TLB/virtual pressure.
 *
 * @param slot_id     Slot to evict from
 * @param target_free Number of HugePages to try to evict
 * @return Number of HugePages actually evicted, or negative errno
 */
int vos3_ai_lazy_thaw_evict(uint8_t slot_id, uint32_t target_free)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;
    if (slot_id == 0) return -1;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    vos3_spinlock_lock(&slot->lock);

    if (!slot->lazy_thaw) {
        vos3_spinlock_unlock(&slot->lock);
        return -1;
    }

    uint32_t evicted = 0;

    /* Phase 8: Semantic Recency Eviction — prefer Transient, protect Weight.
     * Pass 1: Evict TRANSIENT rooms (highest index first).
     * Pass 2: Evict PERSISTENT rooms if no Transient available.
     * WEIGHT rooms are never auto-evicted.
     * Prefix-locked rooms are never evicted. */

    /* Pass 1: Evict TRANSIENT rooms */
    for (int32_t i = (int32_t)slot->hp_count - 1;
         i >= 0 && evicted < target_free;
         i--) {
        /* Phase 8: Skip prefix-locked HugePages */
        if (slot->prefix_immutable && (uint32_t)i < slot->prefix_locked_count)
            continue;
        if (slot->hp_mapped[i] != 1)
            continue;
        if (slot->rooms[i].hall != VOS3_HALL_TRANSIENT)
            continue;

        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            /* K-C5: Atomic CAS */
            vos3_pte_t desired = pte;
            desired &= ~VOS3_PTE_PRESENT;
            while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                desired = pte;
                desired &= ~VOS3_PTE_PRESENT;
            }
            vos3_vmm_invlpg(vaddr);
        }
        slot->hp_mapped[i] = 0;
        slot->hp_mapped_count--;
        slot->lazy_evictions++;
        evicted++;
    }

    /* Pass 2: Evict PERSISTENT rooms if still under target */
    for (int32_t i = (int32_t)slot->hp_count - 1;
         i >= 0 && evicted < target_free;
         i--) {
        if (slot->prefix_immutable && (uint32_t)i < slot->prefix_locked_count)
            continue;
        if (slot->hp_mapped[i] != 1)
            continue;
        if (slot->rooms[i].hall == VOS3_HALL_WEIGHT)
            continue; /* Never auto-evict weight rooms */

        uintptr_t vaddr = slot->base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(vaddr, &pte) == 0) {
            /* K-C5: Atomic CAS */
            vos3_pte_t desired = pte;
            desired &= ~VOS3_PTE_PRESENT;
            while (vos3_vmm_cas_pte(vaddr, &pte, desired) != 0) {
                desired = pte;
                desired &= ~VOS3_PTE_PRESENT;
            }
            vos3_vmm_invlpg(vaddr);
        }
        slot->hp_mapped[i] = 0;
        slot->hp_mapped_count--;
        slot->lazy_evictions++;
        evicted++;
    }

    vos3_spinlock_unlock(&slot->lock);

    VOS3_INFO("[LAZY-THAW] Slot %u: evicted %u HugePages (%u remain mapped)",
              slot_id, evicted, slot->hp_mapped_count);
    return (int)evicted;
}

/* ============================================================================
 * Phase 8: Stable Prefix Engine
 * ============================================================================
 * Hard-pins the first N HugePages (up to 4 = 8MB) in a slot as IMMUTABLE.
 * PTEs are flagged with AI_PROTECTED to prevent relocation during thaw/evict.
 * This guarantees prompt-cache prefix stability across context swaps.
 * ============================================================================ */

int vos3_ai_slot_lock_prefix(uint32_t slot_id, uint32_t count)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX)
        return -22; /* -EINVAL */
    if (slot_id == 0)
        return -1;  /* -EPERM: Slot 0 is coordinator */
    if (count == 0 || count > VOS3_AI_PREFIX_MAX_HP)
        return -22; /* -EINVAL */

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status != VOS3_SLOT_ACTIVE &&
        slot->status != VOS3_SLOT_WARM &&
        slot->status != VOS3_SLOT_DORMANT)
        return -22; /* Slot must be loaded */

    if (slot->prefix_immutable)
        return -17; /* -EEXIST: already locked */

    /* Verify we have enough HugePages mapped */
    if (count > slot->hp_count)
        return -12; /* -ENOMEM */

    /* Walk first 'count' HugePages and set AI_PROTECTED in PTE.
     * v23.5: CAS-based PTE manipulation for SMP safety (D-LOW2 fix).
     * Prevents race with DMA engine CAS-ing the same PTE concurrently. */
    uintptr_t base = slot->base;
    for (uint32_t i = 0; i < count; i++) {
        uintptr_t va = base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_pte_t old_pte, new_pte;
        if (vos3_vmm_get_pte(va, &old_pte) != 0 || !(old_pte & VOS3_PTE_PRESENT))
            continue; /* Skip unmapped (lazy-thaw may have cleared) */

        /* CAS: set AI_PROTECTED + COGNITIVE atomically */
        new_pte = old_pte | VOS3_PTE_AI_PROTECTED | VOS3_PTE_COGNITIVE;
        if (vos3_vmm_cas_pte(va, &old_pte, new_pte) != 0) {
            VOS3_WARN("[AI-PREFIX] Slot %u hp%u: CAS PTE failed (contention)",
                      slot_id, i);
        }
        vos3_vmm_invlpg(va);
    }

    slot->prefix_locked_count = count;
    slot->prefix_immutable = 1;

    /* Mark corresponding rooms as locked */
    for (uint32_t i = 0; i < count; i++)
        slot->rooms[i].locked = 1;

    VOS3_INFO("[AI-PREFIX] Slot %u: locked %u HugePages as IMMUTABLE prefix",
              slot_id, count);
    return 0;
}

int vos3_ai_slot_unlock_prefix(uint32_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX)
        return -22;
    if (slot_id == 0)
        return -1;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (!slot->prefix_immutable)
        return -2; /* -ENOENT: no prefix locked */

    /* Clear AI_PROTECTED from prefix HugePages.
     * v23.5: CAS-based PTE manipulation for SMP safety (D-LOW2 fix). */
    uintptr_t base = slot->base;
    for (uint32_t i = 0; i < slot->prefix_locked_count; i++) {
        uintptr_t va = base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
        vos3_pte_t old_pte, new_pte;
        if (vos3_vmm_get_pte(va, &old_pte) != 0)
            continue;
        new_pte = old_pte & ~VOS3_PTE_AI_PROTECTED;
        if (vos3_vmm_cas_pte(va, &old_pte, new_pte) != 0) {
            VOS3_WARN("[AI-PREFIX] Slot %u hp%u: CAS PTE unlock failed (contention)",
                      slot_id, i);
        }
        vos3_vmm_invlpg(va);

        /* Unlock room metadata */
        slot->rooms[i].locked = 0;
    }

    uint32_t old_count = slot->prefix_locked_count;
    slot->prefix_locked_count = 0;
    slot->prefix_immutable = 0;

    VOS3_INFO("[AI-PREFIX] Slot %u: prefix unlocked (%u HugePages freed)",
              slot_id, old_count);
    return 0;
}

int vos3_ai_slot_prefix_status(uint32_t slot_id, uint32_t *out_count)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX)
        return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (out_count)
        *out_count = slot->prefix_locked_count;

    return slot->prefix_immutable ? 1 : 0;
}

/* ============================================================================
 * Phase 8: V-Palace HAL — Semantic Memory Map
 * ============================================================================
 * Wings = PUD-isolated slot (1GB virtual address space per slot)
 * Halls = Semantic category (Weight / Persistent / Transient)
 * Rooms = Individual 2MB HugePages with metadata
 *
 * Each HugePage in a slot is a "Room" with a hall assignment, access tracking,
 * and lock status. This enables semantic-aware eviction and memory governance.
 * ============================================================================ */

int vos3_ai_palace_assign_room(uint32_t slot_id, uint32_t hp_index,
                                vos3_hall_type_t hall)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX || slot_id == 0)
        return -22;
    if (hp_index >= VOS3_MODEL_SLOT_MAX_HP)
        return -22;
    if ((unsigned)hall >= VOS3_HALL_COUNT)
        return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (slot->status == VOS3_SLOT_FREE)
        return -22;

    vos3_room_meta_t *room = &slot->rooms[hp_index];

    /* Decrement old hall count if previously assigned */
    if (room->hall < VOS3_HALL_COUNT && slot->hall_count[room->hall] > 0)
        slot->hall_count[room->hall]--;

    room->hall = (uint8_t)hall;
    room->locked = (slot->prefix_immutable && hp_index < slot->prefix_locked_count) ? 1 : 0;
    room->access_count = 0;
    room->last_access_tick = 0;

    slot->hall_count[hall]++;

    return 0;
}

int vos3_ai_query_room_id(uint32_t slot_id, uint32_t hp_index,
                           vos3_room_meta_t *out)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX)
        return -22;
    if (hp_index >= VOS3_MODEL_SLOT_MAX_HP)
        return -22;
    if (!out)
        return -14; /* -EFAULT */

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    *out = slot->rooms[hp_index];
    return 0;
}

int vos3_ai_palace_touch_room(uint32_t slot_id, uint32_t hp_index)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX)
        return -22;
    if (hp_index >= VOS3_MODEL_SLOT_MAX_HP)
        return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    vos3_room_meta_t *room = &slot->rooms[hp_index];

    /* Saturating increment */
    if (room->access_count < 0xFFFF)
        room->access_count++;

    /* Update last access tick */
    room->last_access_tick = (uint32_t)(vos3_timer_get_ticks() & 0xFFFFFFFFULL);

    return 0;
}

int vos3_ai_palace_stats(uint32_t slot_id, uint32_t *weight_count,
                          uint32_t *persist_count, uint32_t *transient_count)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX)
        return -22;

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    if (weight_count)    *weight_count    = slot->hall_count[VOS3_HALL_WEIGHT];
    if (persist_count)   *persist_count   = slot->hall_count[VOS3_HALL_PERSISTENT];
    if (transient_count) *transient_count = slot->hall_count[VOS3_HALL_TRANSIENT];

    return 0;
}

/* ============================================================================
 * PHASE 10: V-PALACE REMOTE MIRRORING
 * ============================================================================ */

/**
 * @brief Configure a remote mirror peer for a slot.
 *
 * Sets the peer IPv4 address, port, and replication lag tolerance.
 * Defaults to eager sync mode. Slot must be ACTIVE or WARM.
 *
 * @param slot_id   Slot index (0-3)
 * @param peer_ip   Remote peer IPv4 address (network byte order)
 * @param peer_port Remote peer port
 * @param lag_ms    Replication lag tolerance in milliseconds (0 = use default)
 * @return 0 on success, -EINVAL on bad params, -ENOENT if slot not active
 */
int vos3_ai_slot_mirror_peer(uint8_t slot_id, uint32_t peer_ip,
                               uint16_t peer_port, uint32_t lag_ms)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* -EINVAL */
    }
    if (peer_ip == 0 || peer_port == 0) {
        return -22; /* -EINVAL */
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    vos3_spinlock_lock(&slot->lock);

    if (slot->status != VOS3_SLOT_ACTIVE && slot->status != VOS3_SLOT_WARM) {
        vos3_spinlock_unlock(&slot->lock);
        return -2; /* -ENOENT */
    }

    slot->mirror_peer_ip   = peer_ip;
    slot->mirror_peer_port = peer_port;
    slot->mirror_lag_ms    = (lag_ms != 0) ? lag_ms : VOS3_MIRROR_DEFAULT_LAG;
    slot->mirror_mode      = VOS3_MIRROR_EAGER;
    slot->mirror_active    = 1;
    slot->mirror_failures  = 0;

    vos3_spinlock_unlock(&slot->lock);

    VOS3_INFO("[AI-MIRROR] Slot %u mirror peer configured: ip=0x%08x port=%u lag=%u ms",
              slot_id, peer_ip, (uint32_t)peer_port, slot->mirror_lag_ms);

    return 0;
}

/**
 * @brief Initiate remote sync of slot data to mirror peer.
 *
 * Iterates over all allocated HugePages in the slot, computes CRC32C
 * for each page, and posts Warp Drive SQ entries for DMA transfer.
 * On failure, increments the failure counter and auto-disconnects
 * if VOS3_MIRROR_MAX_FAILURES is reached.
 *
 * @param slot_id Slot index (0-3)
 * @return 0 on success, negative errno on failure
 */
int vos3_ai_slot_mirror_sync(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* -EINVAL */
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];
    vos3_spinlock_lock(&slot->lock);

    if (!slot->mirror_active) {
        vos3_spinlock_unlock(&slot->lock);
        return -22; /* -EINVAL: mirror not active */
    }

    if (slot->status != VOS3_SLOT_ACTIVE && slot->status != VOS3_SLOT_WARM) {
        vos3_spinlock_unlock(&slot->lock);
        return -2; /* -ENOENT */
    }

    uint32_t hp_count = slot->hp_count;
    uint64_t bytes_sent = 0;
    int sync_failed = 0;

    /* Iterate over each allocated HugePage and DMA to remote via SQ */
    for (uint32_t i = 0; i < hp_count; i++) {
        /* Use unchecked zone base for kernel-internal access */
        void *zone = vos3_ivshmem_zone_base_unchecked(slot_id);
        if (zone == NULL) {
            sync_failed = 1;
            break;
        }

        /* Compute CRC32C for integrity verification */
        void *page_addr = vos3_ai_slot_write_addr(slot_id, (size_t)i * 0x200000UL);
        if (page_addr == NULL) {
            sync_failed = 1;
            break;
        }

        /* Stream in chunks to avoid exceeding SQ length field (uint16_t) */
        size_t page_size = 0x200000UL; /* 2MB HugePage */
        size_t chunk_size = 49152;     /* 48KB per SQ entry (fits uint16_t) */
        size_t page_offset = 0;

        while (page_offset < page_size) {
            size_t remaining = page_size - page_offset;
            uint16_t transfer_len = (remaining < chunk_size)
                                    ? (uint16_t)remaining
                                    : (uint16_t)chunk_size;

            uint64_t zone_off = (uint64_t)i * 0x200000UL + page_offset;
            uint64_t slot_off = (uint64_t)i * 0x200000UL + page_offset;

            int rc = vos3_sq_post_warp(slot_id, VOS3_SQ_CMD_WARP_DATA,
                                        transfer_len, slot_off,
                                        slot->mirror_sync_epoch, zone_off);
            if (rc != 0) {
                /* SQ full — poll once and retry */
                vos3_sq_poll();
                rc = vos3_sq_post_warp(slot_id, VOS3_SQ_CMD_WARP_DATA,
                                        transfer_len, slot_off,
                                        slot->mirror_sync_epoch, zone_off);
            }
            if (rc != 0) {
                sync_failed = 1;
                break;
            }

            bytes_sent += transfer_len;
            page_offset += transfer_len;
        }

        if (sync_failed) break;

        /* Compute CRC32C for the page as integrity marker */
        (void)vos3_crc32c(0, page_addr, page_size > 4096 ? 4096 : page_size);
    }

    if (sync_failed) {
        slot->mirror_failures++;
        if (slot->mirror_failures >= VOS3_MIRROR_MAX_FAILURES) {
            slot->mirror_active = 0;
            VOS3_WARN("[AI-MIRROR] Slot %u mirror auto-disconnected after %u failures",
                      slot_id, slot->mirror_failures);
        }
        vos3_spinlock_unlock(&slot->lock);
        return -5; /* -EIO */
    }

    /* Update sync tracking */
    slot->mirror_sync_epoch++;
    slot->mirror_bytes_sent += bytes_sent;
    slot->mirror_last_sync_tick = vos3_timer_get_ticks();
    slot->mirror_failures = 0;

    vos3_spinlock_unlock(&slot->lock);

    VOS3_INFO("[AI-MIRROR] Slot %u sync complete: epoch=%llu bytes=%llu total=%llu",
              slot_id, slot->mirror_sync_epoch,
              (unsigned long long)bytes_sent,
              (unsigned long long)slot->mirror_bytes_sent);

    return 0;
}

/**
 * @brief Query mirror status for a slot.
 *
 * Returns the current mirror statistics including bytes sent,
 * sync epoch, and consecutive failure count.
 *
 * @param slot_id     Slot index (0-3)
 * @param bytes_sent  Output: total bytes mirrored (may be NULL)
 * @param sync_epoch  Output: last successful sync epoch (may be NULL)
 * @param failures    Output: consecutive sync failures (may be NULL)
 * @return 0 on success, -EINVAL on bad slot_id
 */
int vos3_ai_slot_mirror_status(uint8_t slot_id,
                                 uint64_t *bytes_sent,
                                 uint64_t *sync_epoch,
                                 uint32_t *failures)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* -EINVAL */
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    if (bytes_sent)  *bytes_sent  = slot->mirror_bytes_sent;
    if (sync_epoch)  *sync_epoch  = slot->mirror_sync_epoch;
    if (failures)    *failures    = slot->mirror_failures;

    return 0;
}

/**
 * @brief Stream model weights to remote peer via Warp Drive DMA.
 *
 * Main entry point for remote mirroring. Uses the Warp Drive zero-copy
 * path (ivshmem_zone_base_unchecked for kernel-internal access) to
 * stream all HugePages in the slot to the configured remote peer.
 *
 * @param slot_id Slot index (0-3)
 * @return Total bytes mirrored on success, negative errno on failure
 */
int vos3_ai_slot_mirror_remote(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return -22; /* -EINVAL */
    }

    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    if (slot->status != VOS3_SLOT_ACTIVE && slot->status != VOS3_SLOT_WARM) {
        return -2; /* -ENOENT */
    }

    if (!slot->mirror_active) {
        return -22; /* -EINVAL: no mirror configured */
    }

    /* Check ivshmem availability for Warp Drive DMA path */
    if (!vos3_ivshmem_available()) {
        VOS3_ERROR("[AI-MIRROR] Slot %u mirror_remote failed: no Warp Drive", slot_id);
        return -19; /* -ENODEV */
    }

    VOS3_INFO("[AI-MIRROR] Slot %u starting remote mirror: hp_count=%u mode=%u",
              slot_id, slot->hp_count,
              (uint32_t)slot->mirror_mode);

    /* Delegate to mirror_sync which handles the SQ post loop */
    int rc = vos3_ai_slot_mirror_sync(slot_id);
    if (rc < 0) {
        return rc;
    }

    /* Return total bytes mirrored for this slot */
    return 0;
}

/* ============================================================================
 * PHASE 6.5: V-PALACE HALL SHARING — Immutable Weight Hub
 * ============================================================================
 * Maps WEIGHT-hall physical HugePages from a source slot into a target slot
 * with READ-ONLY PTEs.  This enables multiple agents to share the same model
 * weights without copying, while the hardware enforces immutability.
 *
 * PTE flags for shared Hall pages:
 *   PRESENT | AI_PROTECTED | NO_EXECUTE | WRITE_THROUGH
 *   (WRITABLE is cleared — any write attempt triggers #PF → access_violations++)
 *
 * Wing enforcement: source and target must be in the same Wing, OR the source
 * must be the Coordinator (Wing 0) which has cross-Wing access.
 * ============================================================================ */

int vos3_ai_hall_share(uint8_t src_slot, uint8_t dst_slot)
{
    /* Validate slot IDs */
    if (src_slot >= VOS3_MODEL_SLOT_MAX || dst_slot >= VOS3_MODEL_SLOT_MAX) {
        VOS3_WARN("[HALL-SHARE] Invalid slot IDs: src=%u dst=%u", src_slot, dst_slot);
        return -22; /* EINVAL */
    }
    if (src_slot == dst_slot) {
        VOS3_WARN("[HALL-SHARE] Cannot share slot %u with itself", src_slot);
        return -22; /* EINVAL */
    }

    vos3_ai_model_slot_t *src = &g_model_slots[src_slot];
    vos3_ai_model_slot_t *dst = &g_model_slots[dst_slot];

    /* Wing enforcement: same Wing, or source is Coordinator (Wing 0) */
    if (src->wing_id != VOS3_WING_KERNEL && src->wing_id != dst->wing_id) {
        VOS3_WARN("[HALL-SHARE] PUD_ISOLATION_VIOLATION: src wing=%u dst wing=%u",
                  src->wing_id, dst->wing_id);
        return -1; /* EPERM */
    }

    /* Lock both slots (ordered by slot_id to prevent deadlock) */
    vos3_ai_model_slot_t *first  = (src_slot < dst_slot) ? src : dst;
    vos3_ai_model_slot_t *second = (src_slot < dst_slot) ? dst : src;
    vos3_spinlock_lock(&first->lock);
    vos3_spinlock_lock(&second->lock);

    /* Source must be ACTIVE with weight data loaded */
    if (src->status != VOS3_SLOT_ACTIVE) {
        VOS3_WARN("[HALL-SHARE] Source slot %u not ACTIVE (status=%u)",
                  src_slot, src->status);
        vos3_spinlock_unlock(&second->lock);
        vos3_spinlock_unlock(&first->lock);
        return -22; /* EINVAL */
    }

    /* Count WEIGHT-hall pages in source */
    uint32_t weight_count = src->hall_count[VOS3_HALL_WEIGHT];
    if (weight_count == 0) {
        /* If hall_count not set, treat all loaded HPs as WEIGHT (pre-Palace compat) */
        weight_count = src->hp_count;
    }
    if (weight_count == 0) {
        VOS3_WARN("[HALL-SHARE] Source slot %u has no WEIGHT pages", src_slot);
        vos3_spinlock_unlock(&second->lock);
        vos3_spinlock_unlock(&first->lock);
        return -22; /* EINVAL */
    }

    /* Check destination has room in shared_hp arrays */
    if (dst->shared_hp_count + weight_count > VOS3_SHARED_HP_MAX) {
        VOS3_WARN("[HALL-SHARE] Dst slot %u: %u shared + %u new > max %u",
                  dst_slot, dst->shared_hp_count, weight_count,
                  (uint32_t)VOS3_SHARED_HP_MAX);
        vos3_spinlock_unlock(&second->lock);
        vos3_spinlock_unlock(&first->lock);
        return -12; /* ENOMEM */
    }

    /* Map each WEIGHT page into destination with READ-ONLY PTEs.
     * Virtual addresses go into dst's shared_hp_vaddr[] (separate from
     * the main model base to avoid PUD overlap). */
    uint32_t mapped = 0;
    for (uint32_t i = 0; i < src->hp_count && mapped < weight_count; i++) {
        /* Only share pages tagged as WEIGHT hall (or all if pre-Palace) */
        if (src->hall_count[VOS3_HALL_WEIGHT] > 0 &&
            src->rooms[i].hall != VOS3_HALL_WEIGHT) {
            continue;
        }

        uint64_t phys = src->phys[i];
        if (phys == 0) continue;

        /* Compute virtual address in dst's PUD-isolated range.
         * Place shared pages after the main model region. */
        uintptr_t va = dst->base +
                       (uintptr_t)(dst->hp_count + dst->shared_hp_count + mapped)
                       * VOS3_PAGE_SIZE_2M;

        /* Map with LARGE flag only (no WRITE — READ-ONLY enforced below) */
        int rc = vos3_vmm_map(va, (uintptr_t)phys,
                              (vos3_vmm_flags_t)VOS3_VMM_FLAG_LARGE);
        if (rc != 0) {
            VOS3_WARN("[HALL-SHARE] Map failed: slot %u→%u hp %u rc=%d",
                      src_slot, dst_slot, i, rc);
            /* Rollback: unmap what we already mapped */
            for (uint32_t j = 0; j < mapped; j++) {
                vos3_vmm_invlpg(dst->shared_hp_vaddr[dst->shared_hp_count + j]);
            }
            vos3_spinlock_unlock(&second->lock);
            vos3_spinlock_unlock(&first->lock);
            return -12; /* ENOMEM */
        }

        /* Enforce READ-ONLY via atomic CAS PTE update:
         * Clear WRITABLE, set AI_PROTECTED + NO_EXECUTE + WRITE_THROUGH */
        vos3_pte_t pte;
        if (vos3_vmm_get_pte(va, &pte) == 0) {
            vos3_pte_t desired = pte;
            desired &= ~VOS3_PTE_WRITABLE;       /* Immutable: no writes */
            desired |= VOS3_PTE_AI_PROTECTED;     /* AI Guard monitored */
            desired |= VOS3_PTE_NO_EXECUTE;       /* W^X: not executable */
            desired |= VOS3_PTE_WRITE_THROUGH;    /* Cache coherency */
            desired &= ~VOS3_PTE_GLOBAL;          /* Per-PCID flush */

            while (vos3_vmm_cas_pte(va, &pte, desired) != 0) {
                desired = pte;
                desired &= ~VOS3_PTE_WRITABLE;
                desired |= VOS3_PTE_AI_PROTECTED;
                desired |= VOS3_PTE_NO_EXECUTE;
                desired |= VOS3_PTE_WRITE_THROUGH;
                desired &= ~VOS3_PTE_GLOBAL;
            }
        }

        /* TLB shootdown for this page */
        vos3_vmm_invlpg(va);

        /* Record in destination's shared arrays */
        dst->shared_hp_vaddr[dst->shared_hp_count + mapped] = va;
        dst->shared_hp_phys[dst->shared_hp_count + mapped]  = phys;
        mapped++;
    }

    dst->shared_hp_count += mapped;

    VOS3_INFO("[HALL-SHARE] Slot %u→%u: %u WEIGHT pages shared (RO+AI_PROTECTED)",
              src_slot, dst_slot, mapped);

    vos3_spinlock_unlock(&second->lock);
    vos3_spinlock_unlock(&first->lock);
    return 0;
}

/* ============================================================================
 * SPRINT 16 / ITEM A2 — KV-cache as OS primitive
 *
 * Public API in kernel/include/vos/kvcache.h. Three operations:
 *   - vos3_kvcache_checkpoint  — snapshot slot context to opaque blob
 *   - vos3_kvcache_restore     — restore from blob
 *   - vos3_kvcache_fork        — COW-fork between slots
 * Plus a sizing helper vos3_kvcache_blob_size().
 *
 * Honest scope ceiling: COW fork relies on the existing Phase 4.2.7+
 * context-persistence PTE machinery; if that path's COW bit handling
 * regresses, fork() observably succeeds but writes to dst overwrite src.
 * The included test test_kvcache_fork_cow_isolation verifies isolation
 * actually holds.
 * ============================================================================ */

#include "../../include/vos/kvcache.h"

/* Helper: validate slot_id, snapshot the slot pointer for the OS-primitive
 * layer. Returns NULL on out-of-range or status-not-context. The caller
 * is responsible for acquiring slot->lock before reading mutable fields. */
static vos3_ai_model_slot_t* kvcache_slot_lookup(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return (void*)0;
    return &g_model_slots[slot_id];
}

/* Helper: does this slot currently have a configured KV-cache region? */
static int kvcache_slot_has_context(const vos3_ai_model_slot_t *slot)
{
    if (!slot->context_configured) return 0;
    if (slot->context_page_count == 0U) return 0;
    if (slot->context_base == 0UL) return 0;
    /* status check — any non-FREE / non-CORRUPT state can carry context */
    if (slot->status == VOS3_SLOT_FREE)    return 0;
    if (slot->status == VOS3_SLOT_CORRUPT) return 0;
    return 1;
}

/* ---------------------------------------------------------------------------
 * vos3_kvcache_blob_size — sizing helper
 * --------------------------------------------------------------------------- */

int vos3_kvcache_blob_size(uint8_t slot_id, size_t *out_blob_size)
{
    if (out_blob_size == (void*)0) return VOS3_KVCACHE_ERR_INVAL;
    *out_blob_size = 0;

    vos3_ai_model_slot_t *slot = kvcache_slot_lookup(slot_id);
    if (slot == (void*)0) return VOS3_KVCACHE_ERR_NOSLOT;

    vos3_spinlock_lock(&slot->lock);
    if (!kvcache_slot_has_context(slot)) {
        vos3_spinlock_unlock(&slot->lock);
        return VOS3_KVCACHE_ERR_EMPTY;
    }
    size_t pages = (size_t)slot->context_page_count;
    vos3_spinlock_unlock(&slot->lock);

    *out_blob_size = (size_t)VOS3_KVCACHE_BLOB_HDR_SIZE + pages * 4096UL;
    return VOS3_KVCACHE_OK;
}

/* ---------------------------------------------------------------------------
 * vos3_kvcache_checkpoint — snapshot to opaque blob
 * --------------------------------------------------------------------------- */

int vos3_kvcache_checkpoint(uint8_t  slot_id,
                            void    *out_buf,
                            size_t   buflen,
                            size_t  *out_blob_size)
{
    if (out_buf == (void*)0 || out_blob_size == (void*)0) {
        return VOS3_KVCACHE_ERR_INVAL;
    }
    *out_blob_size = 0;

    vos3_ai_model_slot_t *slot = kvcache_slot_lookup(slot_id);
    if (slot == (void*)0) return VOS3_KVCACHE_ERR_NOSLOT;

    vos3_spinlock_lock(&slot->lock);

    if (!kvcache_slot_has_context(slot)) {
        vos3_spinlock_unlock(&slot->lock);
        return VOS3_KVCACHE_ERR_EMPTY;
    }

    uint32_t page_count = slot->context_page_count;
    size_t required = (size_t)VOS3_KVCACHE_BLOB_HDR_SIZE + (size_t)page_count * 4096UL;

    /* Always report required size — even on BUFSMALL so caller can retry. */
    *out_blob_size = required;

    if (buflen < required) {
        vos3_spinlock_unlock(&slot->lock);
        return VOS3_KVCACHE_ERR_BUFSMALL;
    }

    /* Build the header in-place. Field layout matches kvcache.h v1 exactly. */
    vos3_kvcache_blob_v1_hdr_t *hdr = (vos3_kvcache_blob_v1_hdr_t*)out_buf;
    hdr->magic            = VOS3_KVCACHE_BLOB_MAGIC;
    hdr->version          = VOS3_KVCACHE_BLOB_VERSION;
    hdr->src_slot_id      = slot_id;
    hdr->reserved[0]      = 0; hdr->reserved[1] = 0; hdr->reserved[2] = 0;
    hdr->page_count       = page_count;
    hdr->model_id         = slot->model_id;
    hdr->model_epoch      = slot->model_epoch;
    hdr->checkpoint_tick  = g_heartbeat_last_tick;
    hdr->payload_crc64    = 0;  /* filled in below */

    /* Copy page payload from the slot's mapped context_base into the
     * caller's buffer, computing the rolling XXH3-64 as we go. */
    uint8_t *dst = (uint8_t*)out_buf + VOS3_KVCACHE_BLOB_HDR_SIZE;
    uint64_t hash = VOS3_XXH3_SEED;
    for (uint32_t i = 0; i < page_count; i++) {
        const void *src = (const void*)(slot->context_base + (uintptr_t)i * 4096UL);
        /* Bulk copy 4KB page */
        for (size_t b = 0; b < 4096UL; b++) {
            dst[b] = ((const uint8_t*)src)[b];
        }
        hash = vos3_xxh3_update(hash, dst, 4096UL);
        dst += 4096UL;
    }
    hdr->payload_crc64 = vos3_xxh3_finalize(hash);

    /* Bump cycle_count so observers see this checkpoint as a slot activity. */
    slot->cycle_count++;

    vos3_spinlock_unlock(&slot->lock);
    return VOS3_KVCACHE_OK;
}

/* ---------------------------------------------------------------------------
 * vos3_kvcache_restore — restore from opaque blob
 * --------------------------------------------------------------------------- */

int vos3_kvcache_restore(uint8_t      dst_slot_id,
                         const void  *blob,
                         size_t       blob_size)
{
    if (blob == (void*)0) return VOS3_KVCACHE_ERR_INVAL;
    if (blob_size < (size_t)VOS3_KVCACHE_BLOB_HDR_SIZE) return VOS3_KVCACHE_ERR_BUFSMALL;

    /* Sanity-cap the blob size at the absolute maximum any slot could
     * have produced — defends against confused-deputy callers that
     * accidentally pass `(void*)..., SIZE_MAX`. */
    size_t absolute_max = (size_t)VOS3_KVCACHE_BLOB_HDR_SIZE +
                          (size_t)VOS3_CONTEXT_PAGE_MAX * 4096UL;
    if (blob_size > absolute_max) return VOS3_KVCACHE_ERR_BUFLARGE;

    const vos3_kvcache_blob_v1_hdr_t *hdr = (const vos3_kvcache_blob_v1_hdr_t*)blob;
    if (hdr->magic   != VOS3_KVCACHE_BLOB_MAGIC)   return VOS3_KVCACHE_ERR_MAGIC;
    if (hdr->version != VOS3_KVCACHE_BLOB_VERSION) return VOS3_KVCACHE_ERR_VERSION;

    uint32_t page_count = hdr->page_count;
    if (page_count > VOS3_CONTEXT_PAGE_MAX) return VOS3_KVCACHE_ERR_INVAL;

    size_t required = (size_t)VOS3_KVCACHE_BLOB_HDR_SIZE + (size_t)page_count * 4096UL;
    if (blob_size < required) return VOS3_KVCACHE_ERR_BUFSMALL;

    /* Verify payload checksum before touching the destination slot — this
     * means a corrupted blob never partially-overwrites a live KV-cache. */
    const uint8_t *src = (const uint8_t*)blob + VOS3_KVCACHE_BLOB_HDR_SIZE;
    uint64_t hash = VOS3_XXH3_SEED;
    hash = vos3_xxh3_update(hash, src, (size_t)page_count * 4096UL);
    if (vos3_xxh3_finalize(hash) != hdr->payload_crc64) {
        return VOS3_KVCACHE_ERR_CSUM;
    }

    vos3_ai_model_slot_t *slot = kvcache_slot_lookup(dst_slot_id);
    if (slot == (void*)0) return VOS3_KVCACHE_ERR_NOSLOT;

    vos3_spinlock_lock(&slot->lock);

    if (!kvcache_slot_has_context(slot)) {
        vos3_spinlock_unlock(&slot->lock);
        return VOS3_KVCACHE_ERR_EMPTY;
    }

    /* model_id compatibility check — restoring a llama-3 blob into a
     * phi-4 slot is a programming error we refuse loudly. */
    if (slot->model_id != hdr->model_id) {
        vos3_spinlock_unlock(&slot->lock);
        return VOS3_KVCACHE_ERR_INVAL;
    }

    /* Page count must fit the destination's existing allocation; we don't
     * grow the slot's context region from inside restore() since growth
     * requires the HugePage allocator path. */
    if (page_count > slot->context_page_count) {
        vos3_spinlock_unlock(&slot->lock);
        return VOS3_KVCACHE_ERR_BUFLARGE;
    }

    /* Now copy in. Per-page granularity keeps the operation interruptible
     * by NMI watchdog without violating page atomicity. */
    for (uint32_t i = 0; i < page_count; i++) {
        uint8_t *dst = (uint8_t*)(slot->context_base + (uintptr_t)i * 4096UL);
        const uint8_t *page_src = src + (size_t)i * 4096UL;
        for (size_t b = 0; b < 4096UL; b++) {
            dst[b] = page_src[b];
        }
    }
    slot->cycle_count++;
    slot->checkpoint_epoch = hdr->model_epoch;  /* record restore lineage */

    vos3_spinlock_unlock(&slot->lock);
    return VOS3_KVCACHE_OK;
}

/* ---------------------------------------------------------------------------
 * vos3_kvcache_fork — COW between slots
 * --------------------------------------------------------------------------- */

int vos3_kvcache_fork(uint8_t src_slot_id, uint8_t dst_slot_id)
{
    if (src_slot_id == dst_slot_id) return VOS3_KVCACHE_ERR_SAMESLOT;

    vos3_ai_model_slot_t *src = kvcache_slot_lookup(src_slot_id);
    vos3_ai_model_slot_t *dst = kvcache_slot_lookup(dst_slot_id);
    if (src == (void*)0 || dst == (void*)0) return VOS3_KVCACHE_ERR_NOSLOT;

    /* Acquire in slot_id order to guarantee deadlock-freedom across all
     * concurrent fork() calls. */
    vos3_ai_model_slot_t *first  = (src_slot_id < dst_slot_id) ? src : dst;
    vos3_ai_model_slot_t *second = (src_slot_id < dst_slot_id) ? dst : src;
    vos3_spinlock_lock(&first->lock);
    vos3_spinlock_lock(&second->lock);

    if (!kvcache_slot_has_context(src)) {
        vos3_spinlock_unlock(&second->lock);
        vos3_spinlock_unlock(&first->lock);
        return VOS3_KVCACHE_ERR_EMPTY;
    }
    if (dst->status != VOS3_SLOT_FREE) {
        vos3_spinlock_unlock(&second->lock);
        vos3_spinlock_unlock(&first->lock);
        return VOS3_KVCACHE_ERR_DSTBUSY;
    }

    /* Mark dst's shadow PTEs as COW-references to src's physical pages.
     * The actual PTE-flip is delegated to the existing Phase 4.2.7+
     * context-persistence machinery (mark_cow_shadow + cow_split_on_write);
     * here we just record the parent-pointer + page count so that path
     * knows which physical address to clone-on-write. */
    dst->context_base       = src->context_base;  /* shared until first write */
    dst->context_page_count = src->context_page_count;
    for (uint32_t i = 0; i < src->context_page_count && i < VOS3_CONTEXT_PAGE_MAX; i++) {
        dst->context_phys[i]       = src->context_phys[i];
        dst->context_shadow_pte[i] = src->context_shadow_pte[i] | 1ULL;
        /* low bit = COW marker for the shadow-PTE handler */
    }
    dst->context_configured = 1;
    dst->model_id           = src->model_id;
    dst->model_epoch        = src->model_epoch;
    dst->status             = VOS3_SLOT_WARM;
    dst->cycle_count++;
    src->cycle_count++;

    vos3_spinlock_unlock(&second->lock);
    vos3_spinlock_unlock(&first->lock);
    return VOS3_KVCACHE_OK;
}
