/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 KV-Cache Configuration — v20.5 Phase 5.0
 * ==============================================
 *
 * Sizing constants for the per-slot KV cache that backs autoregressive
 * inference. The target value is the planned working-set size per Decode
 * slot under typical agent workloads (8B model, 4096-token context, INT8
 * quantization). Final allocation is bounded by the slot's hugepage
 * budget — see `mm/hugepage.c` and `docs/research/dynamic_hugepages.md`.
 */

#ifndef VOS3_AI_KV_CACHE_H
#define VOS3_AI_KV_CACHE_H

#include <stdint.h>

/* Target working-set size per Decode slot (megabytes).
 * 200 MB ≈ 2 GB / 10 — leaves headroom for weights, activations, and
 * the scratchpad zone in a 2 GB-per-slot budget. Lower than the v20.4
 * naive 2 GB allocation by 10× via dynamic hugepage remapping. */
#define VOS3_KV_CACHE_TARGET_MB        200U

/* Hard ceiling per slot. Allocation requests above this are clipped and
 * logged. Prevents a misconfigured Decode slot from starving others. */
#define VOS3_KV_CACHE_CEILING_MB       512U

/* v20.5.1 — Phase 5.1 Dynamic Hugepage Expansion. Maximum total per-slot
 * KV-cache footprint when dynamic expansion is enabled. 10 GiB is the
 * working ceiling for long-context (>32k) inference; further expansion
 * requires either a larger NPU SRAM budget or an iGPU/system-RAM
 * fallback path. See docs/research/dynamic_hugepages.md. */
#define VOS3_KV_CACHE_MAX_GB           10U
#define VOS3_KV_CACHE_MAX_BYTES        ((uint64_t)VOS3_KV_CACHE_MAX_GB * 1024U * 1024U * 1024U)

/* Master switch for the v20.5.1 expansion path. When 0, vos3_vmm_expand_slot_memory
 * is a safe no-op returning -1 (legacy v20.5 behavior preserved). */
#define VOS3_DYNAMIC_EXPANSION_ENABLED 1U

/* Long-context threshold — when an agent request carries context_length
 * above this number of tokens, the orchestrator requests memory expansion
 * via vos3_vmm_expand_slot_memory before dispatch. */
#define VOS3_LONG_CONTEXT_THRESHOLD_TOKENS  32768U

/* Backing-page size for KV-cache allocations. 2 MiB hugepages are used
 * because they fit within the NPU SRAM budget (9 GB ceiling) and amortize
 * TLB pressure during streaming attention. */
#define VOS3_KV_CACHE_PAGE_SIZE        (2U * 1024U * 1024U)

/* Number of 2 MiB hugepages required to satisfy the target. */
#define VOS3_KV_CACHE_PAGES_TARGET     \
    ((VOS3_KV_CACHE_TARGET_MB * 1024U * 1024U) / VOS3_KV_CACHE_PAGE_SIZE)

#endif /* VOS3_AI_KV_CACHE_H */
