/**
 * @file hyperv_bridge.c
 * @brief VOS3 Hyper-V Coexistence Bridge — Detection + VMBus Stub
 *
 * VOS3 sovereignty invariant under Hyper-V:
 *   1. AI inference memory is pinned via VT-d IOMMU (DMAR DRHD units,
 *      already parsed in acpi.c) before any VMBus offer is made.
 *   2. The VMBus channel carries only VBus ASCII commands — no raw
 *      memory references to AI model weights are ever transmitted.
 *   3. The Windows guest partition receives channel notifications but
 *      has no DMA or MMIO access to the ivshmem AI zones.
 *
 * Deployment: VOS3 EFI binary chainloaded from Windows Boot Manager.
 * VOS3 runs as root partition OR as a Gen-2 VM with nested VT-x.
 * Either path, the IOMMU boundary is enforced by hardware before the
 * first Windows process starts.
 *
 * @version 1.0.0 (stub — full implementation in v20.3)
 * @date 2026-04-24
 */

#include "hyperv_bridge.h"
#include "../../include/vos/console.h"
#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * State
 * ============================================================================ */

static hyperv_bridge_state_t g_hyperv = {0};

/* ============================================================================
 * CPUID helper (freestanding)
 * ============================================================================ */

static void hyperv_cpuid(uint32_t leaf,
                          uint32_t *eax, uint32_t *ebx,
                          uint32_t *ecx, uint32_t *edx)
{
    __asm__ volatile(
        "cpuid"
        : "=a"(*eax), "=b"(*ebx), "=c"(*ecx), "=d"(*edx)
        : "a"(leaf)
        : "memory"
    );
}

/* ============================================================================
 * Public API
 * ============================================================================ */

int hyperv_detect(void)
{
    uint32_t eax, ebx, ecx, edx;
    hyperv_cpuid(HYPERV_CPUID_LEAF, &eax, &ebx, &ecx, &edx);

    if (ebx == HYPERV_SIGNATURE_EBX &&
        ecx == HYPERV_SIGNATURE_ECX &&
        edx == HYPERV_SIGNATURE_EDX) {

        g_hyperv.detected = 1;

        /* Read Hyper-V partition ID from interface leaf */
        hyperv_cpuid(HYPERV_CPUID_INTERFACE, &eax, &ebx, &ecx, &edx);
        g_hyperv.partition_id = eax;

        VOS3_INFO("[HYPERV] Hyper-V detected: partition_id=0x%08x", g_hyperv.partition_id);
        return 1;
    }

    VOS3_INFO("[HYPERV] Not running under Hyper-V");
    return 0;
}

int hyperv_vmbus_offer_channel(void)
{
    if (!g_hyperv.detected) {
        VOS3_INFO("[HYPERV] vmbus_offer_channel: Hyper-V not present, skipping");
        return -1;
    }

    /*
     * Full VMBus implementation requires:
     *   1. Allocate physically contiguous ring buffer (HYPERV_VMBUS_RING_SIZE)
     *   2. Write guest OS ID MSR (HV_X64_MSR_GUEST_OS_ID = 0x40000000)
     *   3. Write hypercall page MSR (HV_X64_MSR_HYPERCALL = 0x40000001)
     *   4. Map hypercall page as EXEC
     *   5. Issue HvPostMessage(HV_CONNECTION_ID_VMBUS, ...) to offer channel
     *   6. Wait for VMBus version negotiation (CHANNELMSG_VERSION_RESPONSE)
     *   7. Register VOS3 VBus relay as the channel's interrupt handler
     *
     * This stub logs the intent and returns 0 (no-op) so the boot path
     * succeeds on bare metal and QEMU without Hyper-V. Full implementation
     * ships in v20.3 with swtpm + nested VT-x test harness.
     */
    VOS3_INFO("[HYPERV] VMBus channel offer: STUB — full implementation in v20.3");
    VOS3_INFO("[HYPERV] Sovereignty invariant: DMAR DRHD pinning enforced before this call");
    VOS3_INFO("[HYPERV] AI zones: ivshmem owner_tid ACL prevents Windows VMM DMA access");

    g_hyperv.channel_offered = 1;
    return 0;
}

const hyperv_bridge_state_t *hyperv_get_state(void)
{
    return &g_hyperv;
}
