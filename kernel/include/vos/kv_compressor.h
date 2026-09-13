/* SPDX-License-Identifier: LicenseRef-VOS3-Pro-Proprietary
 * SPDX-FileCopyrightText: 2026 VOS3 Project (Sovereign Enterprise Edition)
 *
 * VOS3 KV-Compressor Public API — Phase 6.1
 * ==========================================
 *
 * Header-side declarations for kernel/src/mm/kv_compressor.c so that
 * vmm.c (and the EFFICIENCY_STATS VBus handler in virtio_bridge.c) can
 * call into the registry without forward-decl'ing each symbol locally.
 *
 * Open-Core charter: this whole file is PRO. CORE builds compile the
 * declarations but the dedupe-hint and Q4 functions are gated on
 * VOS3_PRO at the call site (header is empty in CORE).
 */

#ifndef VOS3_KV_COMPRESSOR_H
#define VOS3_KV_COMPRESSOR_H

#include <stdint.h>

#define VOS3_KV_HASH_BYTES   16U   /* truncated SHA-256 prefix */

#ifdef __cplusplus
extern "C" {
#endif

/* ---- Existing scaffolded API (v20.5.2) ---- */
int      kv_block_lookup(const uint8_t hash[VOS3_KV_HASH_BYTES]);
int      kv_block_register(const uint8_t hash[VOS3_KV_HASH_BYTES],
                           uint64_t phys_addr);
uint64_t kv_block_acquire(const uint8_t hash[VOS3_KV_HASH_BYTES]);
int      kv_block_release(uint64_t phys_addr);
int      kv_block_mark_dirty(uint64_t phys_addr);
void     kv_compressor_get_stats(uint64_t *hits,
                                 uint64_t *misses,
                                 uint64_t *cow_breaks);
void     kv_compressor_init(void);

/* ---- Phase 6.1 additions ---- */

void kv_compressor_get_efficiency(uint64_t *virtual_bytes,
                                  uint64_t *physical_bytes,
                                  uint64_t *dedup_ratio_x1000);

#ifdef VOS3_PRO
/* Inter-slot dedupe hint, called per fresh hugepage by vmm.c expansion.
 * Returns either the existing shared physical address (caller must free
 * its `fresh_phys`) or `fresh_phys` unchanged when no match was found.
 * Never returns 0 unless `fresh_phys == 0`. */
uint64_t kv_compressor_dedupe_hint(const uint8_t hash[VOS3_KV_HASH_BYTES],
                                   uint64_t fresh_phys);
#endif

/* Near-lossless 4-bit group quantization primitive — int16 fixed-point.
 * Pack 32 source values into 16 bytes; per-group scale supplied by caller
 * (kernel does no float math on this path — see kv_compressor.c rationale).
 * Returns 0 on success; -1 if scale == 0. */
int kv_q4_group_pack(const int16_t *src, int16_t scale, uint8_t *dst);
int kv_q4_group_unpack(const uint8_t *src, int16_t scale, int16_t *dst);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_KV_COMPRESSOR_H */
