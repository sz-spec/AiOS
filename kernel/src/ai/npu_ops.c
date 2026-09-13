/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 NPU Gradient Offload — v20.2.1-TRAIN (Honest Skeleton)
 * ============================================================
 *
 * NAMING / SCOPE NOTE:
 *   This file is the kernel-side counterpart of the SovereignFineTuner's
 *   "NPU Gradient Offloading" feature. The intent is to map fine-tuning
 *   gradient tensors into the NPU clusters discovered via ACPI DSAR (see
 *   `kernel/src/drivers/acpi.c`).
 *
 *   What this file ACTUALLY does today:
 *     - Capability gate: refuses if the calling slot lacks VOS3_CAP_GPU_DIRECT
 *     - Topology lookup: returns the highest-compute_capacity cluster from
 *       the DSAR-parsed table (delegates to vos3_acpi_get_info())
 *     - Size guard: refuses oversized models (NPU SRAM ceiling 9 GiB)
 *     - Logs the routing decision for the v20.6 dispatcher to replay
 *
 *   What this file does NOT do (documented gap, requires v20.6 + vendor SDK):
 *     - Actual physical NPU DMA programming. Each NPU vendor (Intel,
 *       Qualcomm, AMD) ships a binary blob driver. Without that blob loaded,
 *       there is no userspace or kernel API to route a gradient tensor
 *       to NPU SRAM. The honest path forward is to load the vendor's
 *       in-tree NPU driver as a VOS3 driver module — out of scope here.
 *     - IOMMU domain attachment for the NPU device (also vendor-specific).
 *     - Performance claim of "> 80% bare-metal". Cannot be verified
 *       without real NPU hardware in the test rig.
 *
 *   The Python SovereignFineTuner treats `vos3_npu_offload_gradient`'s
 *   negative return as informational and falls back to GPU/CPU paths
 *   transparently. The real win in v20.5 is the structured logging:
 *   when v20.6 dispatcher lands, every "would-have-pinned-to-cluster-X"
 *   log entry can be replayed against the new vendor driver.
 *
 *   Constraint discipline: this file does NOT panic the kernel on any
 *   condition. The "PANIC on memory leak" requirement is being scoped
 *   carefully — heuristic-driven kernel panic is itself a DoS vector.
 *   When implemented (v20.6), it will be opt-in via env flag, with the
 *   default being isolate-kill-and-log.
 */

#include "../../include/ipc/slots.h"
#include "../../include/vos/acpi.h"
#include "../../include/vos/console.h"

#include <stdint.h>
#include <stddef.h>

/* Apr 2026 Intel NPU Panther Lake silicon ceiling. Models that would
 * exceed this on weights alone cannot fit in NPU SRAM. The Python side
 * mirrors this ceiling via _NPU_SRAM_CEILING_GB in tool_provider.py. */
#define VOS3_NPU_SRAM_CEILING_BYTES   (9ULL * 1024 * 1024 * 1024)

/* Forward declaration to keep the header dependency surface minimal. */
extern void mmr_record_security_violation(uint32_t slot_id, uint32_t reason_code);

/* ---- Public API (kernel side; Python wrappers live in finetune_engine.py) ---- */

/**
 * Return the cluster_id of the NPU cluster with the highest compute
 * capacity, or -1 if no DSAR-enumerated clusters are present.
 */
int vos3_npu_select_best_cluster(void)
{
    const vos3_acpi_info_t *info = vos3_acpi_get_info();
    if (!info || info->dsar_cluster_count == 0) {
        return -1;
    }
    int best = -1;
    uint32_t best_cap = 0;
    for (uint8_t i = 0; i < info->dsar_cluster_count && i < 8; i++) {
        if (info->dsar_clusters[i].compute_capacity > best_cap) {
            best_cap = info->dsar_clusters[i].compute_capacity;
            best = (int)info->dsar_clusters[i].cluster_id;
        }
    }
    return best;
}

/**
 * Test whether a model of `weight_bytes` bytes (4-bit quantized estimate)
 * can fit in NPU SRAM. Returns 1 if it fits, 0 otherwise.
 */
int vos3_npu_can_fit(uint64_t weight_bytes)
{
    return weight_bytes <= VOS3_NPU_SRAM_CEILING_BYTES ? 1 : 0;
}

/**
 * Attempt to offload a gradient tensor (described abstractly by its
 * byte size) onto the best available NPU cluster.
 *
 * Capability gate: requires the calling slot to hold VOS3_CAP_GPU_DIRECT.
 * Size gate: refuses tensors that would exceed NPU SRAM.
 * Cluster gate: refuses if no DSAR-enumerated clusters exist.
 *
 * On success: logs an informational NPU_OFFLOAD line and returns the
 *   cluster_id. The caller's gradient-step code then issues the actual
 *   DMA via the (out-of-scope, vendor-binary-driver) NPU command queue.
 * On any gate failure: logs the rejection reason and returns -1.
 *
 * This function is ISR-safe: no spinlocks, no allocation, structured
 * logging only. Safe to call from any kernel context.
 *
 * @param slot_id        Caller's slot id (must hold VOS3_CAP_GPU_DIRECT)
 * @param tensor_bytes   Size of the gradient tensor in bytes
 * @return cluster_id (>= 0) on success, -1 on any gate failure
 */
int vos3_npu_offload_gradient(uint32_t slot_id, uint64_t tensor_bytes)
{
    /* Gate 1: slot capability. */
    if (!vos3_slot_has_capability(slot_id, VOS3_CAP_GPU_DIRECT)) {
        mmr_record_security_violation(slot_id, VOS3_SECVIO_REASON_OWNER_MISMATCH);
        VOS3_INFO("[NPU] offload denied: slot %u lacks VOS3_CAP_GPU_DIRECT",
                  slot_id);
        return -1;
    }

    /* Gate 2: ZOMBIE slot must not be granted any new mappings. */
    if (vos3_slot_get_state(slot_id) == VOS3_SLOT_STATE_ZOMBIE) {
        VOS3_INFO("[NPU] offload denied: slot %u is ZOMBIE", slot_id);
        return -1;
    }

    /* Gate 3: NPU SRAM size guard. */
    if (!vos3_npu_can_fit(tensor_bytes)) {
        VOS3_INFO("[NPU] offload denied: tensor (%llu bytes) exceeds SRAM ceiling",
                  (unsigned long long)tensor_bytes);
        return -1;
    }

    /* Gate 4: actual cluster availability. */
    int cluster = vos3_npu_select_best_cluster();
    if (cluster < 0) {
        VOS3_INFO("[NPU] offload deferred: no DSAR clusters present");
        return -1;
    }

    /* Success path: log the routing decision. The actual DMA programming
     * is the responsibility of the vendor NPU driver (v20.6 work). */
    VOS3_INFO("[NPU] offload OK: slot=%u tensor=%llu B -> cluster=%d",
              slot_id, (unsigned long long)tensor_bytes, cluster);
    return cluster;
}
