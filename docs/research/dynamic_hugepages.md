# Dynamic Hugepage PUD Remapping — Sub-Microsecond Latency Target
## v20.5 Phase 5.0 Research Document
**v0.1 draft | April 2026 | OWNER: Kernel Team**

---

## 1. Problem Statement

VOS3's per-slot KV-cache target dropped from a naive 2 GB allocation to
**200 MB working set** (`VOS3_KV_CACHE_TARGET_MB`, see
`kernel/include/ai/kv_cache.h`). The 10× reduction is only realizable if
we can **remap secondary PUD entries dynamically** as the autoregressive
decode loop advances through context windows — old KV blocks are unmapped
and their backing 2 MiB hugepages returned to the per-slot pool.

The remapping operation must complete in **< 1 microsecond P99** to avoid
becoming the new bottleneck on the decode hot path. At 30–60 tokens/sec
sustained throughput per Decode slot, every microsecond of remap latency
costs measurable wall-clock time.

---

## 2. Why PUD-Level Remapping (Not PMD or PTE)

| Level | Coverage per entry | Remap latency budget | Suitability |
|-------|-------------------|---------------------|-------------|
| PTE   | 4 KiB             | ~50 ns CAS           | Too granular — 250 K entries for 1 GiB |
| PMD   | 2 MiB (huge)      | ~80 ns CAS + invlpg  | Reasonable — 512 entries for 1 GiB |
| PUD   | 1 GiB             | ~80 ns CAS + invlpg + TLB shootdown | Bulk remap target |
| PGD   | 512 GiB           | (not applicable)     | Top of hierarchy |

A **single PUD entry covers 1 GiB** — which is exactly the
allocation granularity at which we want to swap KV regions in/out without
touching individual leaf PTEs. The KV cache for a 4096-token context with
an 8B model fits comfortably under 1 GiB, so one PUD swap migrates the
entire active context.

---

## 3. Architectural Constraints (x86_64 hardware)

### 3.1 INVLPG vs Full TLB Flush

A PUD-level mapping change requires invalidating **all leaf TLB entries
covered by that 1 GiB region**. Two options:

- `invlpg` per-page — 250 K invalidations × 12 ns ≈ 3 ms (unacceptable)
- Full TLB flush via `mov cr3, cr3` — single instruction, ~500 ns; flushes
  ALL pcid contexts → unacceptable for our PCID-isolated agent slots
- **`invpcid type=1`** (single-context flush, current PCID only) — 80 ns
  on Sapphire Rapids and later. **This is the path.**

### 3.2 PCID Errata (Alder Lake + Raptor Lake)

VOS3 already detects the Intel models 0x97/0x9A/0xB7/0xBA/0xBE/0xBF that
have the INVLPG+PCID interaction bug (see
`kernel/src/boot/uefi_bridge.c::detect_invlpg_pcid_bug`). On affected
chips, `g_invpcid_type2_required = 1` forces type-2 (flush all PCIDs).
This adds ~40 ns to the remap budget but stays under the 1 μs ceiling.

### 3.3 Memory Barriers

The PUD entry write must be observed by all logical CPUs before the TLB
flush. Sequence:

```
mov     [pud_entry], %rax     # store new PUD value
mfence                         # serialize stores
invpcid  type=1, [desc]        # local TLB flush, current PCID
```

`sfence` is insufficient — we need ordering with respect to **all**
prior loads from the old mapping, not just stores. `mfence` is mandatory.

### 3.4 Multi-CPU Considerations (TLB Shootdown)

When a slot's context migrates between CPUs (rare but possible under
work-stealing), a remap on CPU A must be visible on CPU B's TLB. The
classic Linux solution is an IPI-driven TLB shootdown — costs ~5 μs P99,
**blowing our budget**.

VOS3 v20.5 design choice: **pin Decode slots to a single CPU** (see
`vos3_slot_get_state` + the affinity field in dispatcher.c). When a slot
is pinned, no shootdown is needed — only the local TLB matters. The cost
is reduced work-stealing flexibility, but Decode workloads are the
hottest path and benefit most from CPU affinity anyway.

---

## 4. Implementation Sketch (v20.5 → v20.6)

### 4.1 Per-Slot PUD Pool

Each slot owns a fixed array of **8 PUD entries** in its top-level page
table. Two are resident (active KV state), six are reserve (free or
holding cold KV blocks awaiting eviction).

```c
struct vos3_slot_pud_pool {
    uintptr_t pud_va_base;          /* virtual address of pool */
    uint64_t  pud_pa[8];            /* physical addresses of 8 PUDs */
    uint8_t   pud_resident_mask;    /* which slots are currently mapped */
    uint64_t  pud_last_used_tsc[8]; /* for LRU eviction */
};
```

### 4.2 The Remap Hot Path

```c
static inline int vos3_pud_remap_atomic(
    uint32_t slot_id,
    uintptr_t va,            /* must be 1 GiB-aligned */
    uint64_t  new_pud_value)
{
    /* 1. Atomic CAS the PUD entry */
    if (vos3_vmm_cas_pte(va, /*old=*/0, new_pud_value) != 0) {
        return -EBUSY;
    }

    /* 2. Local TLB invalidation, single context */
    invpcid_type1_current_pcid_va(va);

    /* 3. Bookkeeping */
    g_slot_pud_pool[slot_id].pud_last_used_tsc[index_of(va)] = rdtsc();
    return 0;
}
```

Target latency budget breakdown:

| Step | Budget | Mechanism |
|------|--------|-----------|
| CAS PUD entry | 80 ns | `lock cmpxchg` on the PUD slot |
| `mfence` | 30 ns | Serialize stores |
| `invpcid` type-1 | 80 ns | Single-context, single-VA flush |
| Per-slot bookkeeping | 50 ns | LRU timestamp update |
| Function overhead | 60 ns | Stack frame + return |
| **Total** | **300 ns** | **3.3× under 1 μs ceiling** |

### 4.3 ISR Safety

The remap function must be safe to call from interrupt context. The
existing `vos3_vmm_cas_pte` is documented ISR-safe (atomic_cmpxchg, no
spinlocks held). The TLB invalidation is a single instruction. No
allocation, no logging, no MMR write on the hot path. **Logging happens
asynchronously** via a per-slot ring buffer drained by a low-priority
kernel thread.

---

## 5. Open Questions

1. **PUD pool sizing.** 8 entries per slot = 8 GiB virtual address space
   per slot. Sufficient for 4096-token Decode workloads with 8B models;
   may need expansion for 70B models or 32K-context. Decision deferred
   to v20.6 benchmarks.

2. **Cross-slot sharing.** When two Decode slots share KV state for an
   ensemble inference, can they share a PUD entry safely? Today: no.
   Investigation: a shared-readonly PUD would need explicit synchronization
   on writes; the v20.4 PCID isolation is the only barrier. Deferred.

3. **Hardware accelerators (NPU/GPU) consuming the PUD.** If a GPU
   buffer is mapped via `vos3_vfio_pci_map` and its PTE is part of a PUD
   that's being remapped, the GPU's IOMMU domain must be flushed too.
   The Intel VT-d `qi_invalidate` path costs ~200 ns. Adds to budget but
   stays under 1 μs. Test with actual hardware required.

4. **Power management interactions.** When a CPU enters C-state, its
   TLB is flushed. A remap performed just before C-state entry is wasted
   work but harmless. Need to verify no race window exists between the
   remap and the C-state transition.

---

## 6. Verification Plan

| Phase | Test | Pass criterion |
|-------|------|----------------|
| Microbench (v20.5) | Single PUD remap timing under `rdtscp` | P99 < 1 μs over 10⁶ iterations |
| Stress (v20.6) | Concurrent remap from 2 Decode slots | No PTE corruption (full page-walk verify) |
| Hardware (v20.6) | NPU + GPU concurrent IOMMU + PUD remap | No DMA fault |
| Production | Full VOS3 inference loop with KV churn | Throughput regression < 2% vs static mapping |

---

## 7. Status

- **Sketch:** This document
- **Static infrastructure:** PUD entry layout in `kernel/src/mm/vmm.c`
  (existing — review needed for 1 GiB-aligned PUD writes)
- **Atomic primitive:** `vos3_vmm_cas_pte` in `kernel/src/mm/vmm.c`
  (existing, ISR-safe)
- **Slot pool:** **NOT YET IMPLEMENTED** — v20.5 follow-up
- **Remap hot path:** **NOT YET IMPLEMENTED** — v20.5 follow-up
- **Microbench:** **NOT YET WRITTEN** — required to validate < 1 μs target
  before v20.5 ship

This doc captures the design intent. Implementation sequence and PR list
will follow once the slot-PUD layout is finalized in vmm.c.

---

*VOS3 Dynamic Hugepages Research — v0.1 — April 28, 2026*
*"The 10× KV cache reduction lives or dies at the PUD remap path."*
*SPDX-License-Identifier: MIT | SPDX-FileCopyrightText: 2026 VOS3 Project*
