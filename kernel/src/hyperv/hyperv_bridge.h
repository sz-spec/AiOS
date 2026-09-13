/**
 * @file hyperv_bridge.h
 * @brief VOS3 Hyper-V Coexistence Bridge
 *
 * Enables VOS3 to detect a Hyper-V hypervisor and negotiate a VMBus
 * channel for zero-copy VBus command delivery to Windows userspace,
 * while preserving the AI memory sovereignty invariant via VT-d IOMMU.
 *
 * @version 1.0.0 (stub — v20.2 design)
 * @date 2026-04-24
 */

#ifndef VOS3_HYPERV_BRIDGE_H
#define VOS3_HYPERV_BRIDGE_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * Hyper-V Identification
 * ============================================================================ */

/* CPUID leaf for Hyper-V hypervisor presence:
 * EAX=0x40000000 → EBX:ECX:EDX == "Microsoft Hv" if Hyper-V is present */
#define HYPERV_CPUID_LEAF        0x40000000U
#define HYPERV_SIGNATURE_EBX     0x7263694DU   /* "Micr" */
#define HYPERV_SIGNATURE_ECX     0x666F736FU   /* "osof" */
#define HYPERV_SIGNATURE_EDX     0x76482074U   /* "t Hv" */

/* Hyper-V interface CPUID leaf */
#define HYPERV_CPUID_INTERFACE   0x40000001U

/* ============================================================================
 * VMBus Channel
 * ============================================================================ */

/* VOS3 allocates a 64KB ring for the VBus → VMBus bridge */
#define HYPERV_VMBUS_RING_SIZE   (64U * 1024U)

/* VOS3 synthetic interrupt source for VMBus notifications */
#define HYPERV_SINT_VOS3_VBUS    7U

typedef struct hyperv_bridge_state {
    uint8_t  detected;         /* 1 = running under Hyper-V */
    uint8_t  channel_offered;  /* 1 = VMBus channel offer sent */
    uint32_t partition_id;     /* Hyper-V partition ID (from CPUID) */
    void    *ring_va;          /* Virtual address of VMBus ring buffer */
    uint64_t ring_pa;          /* Physical address of VMBus ring buffer */
} hyperv_bridge_state_t;

/* ============================================================================
 * API
 * ============================================================================ */

/**
 * Detect Hyper-V via CPUID. Must be called before mmio mapping.
 * Returns 1 if running under Hyper-V Gen-2, 0 otherwise.
 */
int hyperv_detect(void);

/**
 * Initialize the VMBus channel offer.
 * Maps the VBus ring buffer as a VMBus channel to Windows guest partition.
 * AI inference memory pages are pinned via DMAR DRHD before this call,
 * ensuring the Windows VMM cannot access VOS3 model weights.
 *
 * Returns 0 on success, negative errno on failure.
 */
int hyperv_vmbus_offer_channel(void);

/**
 * Return a pointer to the bridge state (for diagnostics).
 */
const hyperv_bridge_state_t *hyperv_get_state(void);

#endif /* VOS3_HYPERV_BRIDGE_H */
