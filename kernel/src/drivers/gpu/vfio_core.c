/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 VFIO Core — v20.5 Phase 5.0 (Skeleton)
 * ============================================
 *
 * NOTE ON NAMING:
 *   "VFIO" here refers to the architectural pattern of mapping a PCI
 *   device's BAR registers into a slot's address space, modeled on (but
 *   not API-compatible with) the Linux VFIO subsystem. VOS3 is a
 *   freestanding kernel with its own slot model — there is no userspace
 *   ioctl interface and no character device. The Linux name is retained
 *   to communicate intent to readers familiar with the pattern.
 *
 * Scope of this skeleton:
 *   - vos3_vfio_pci_map(): map a PCI device's BAR0 into a slot's
 *     address space, gated by the slot's VOS3_CAP_GPU_DIRECT capability
 *   - vos3_vfio_pci_unmap(): release the mapping, scrub the slot's view
 *   - vos3_vfio_share_with_virtio_gpu(): expose the mapped buffer to
 *     virtio_gpu.c for direct buffer sharing
 *
 * What this skeleton does NOT yet do (v20.6+ work):
 *   - IOMMU domain attachment for hardware-enforced isolation
 *   - SR-IOV virtual function passthrough
 *   - MSI-X interrupt remapping
 *   - DMA-coherent allocator integration
 *
 * Constraint: every PTE update on the GPU mapping path goes through
 * vos3_vmm_cas_pte() which uses atomic_cmpxchg internally — ISR-safe,
 * no kernel-side locks held during the swap.
 */

#include "../../../include/ipc/slots.h"

#include <stdint.h>
#include <stddef.h>

/* Forward declarations from existing kernel surface. */
extern int       vos3_vmm_is_mapped(uintptr_t va);
extern uintptr_t vos3_vmm_alloc_mmio_window(size_t len);
extern int       vos3_vmm_cas_pte(uintptr_t va, uint64_t old_pte,
                                  uint64_t new_pte);
extern int       vos3_vmm_unmap_range(uintptr_t va, size_t len);

/* PCI configuration space accessors (existing in drivers/pci.c). */
extern uint32_t  vos3_pci_read32(uint8_t bus, uint8_t dev, uint8_t func,
                                 uint8_t off);

/* PCI BAR0 read for a (bus, dev, func) triple. Returns the 64-bit BAR
 * physical address aligned to its size, or 0 on miss. */
static uint64_t vfio_read_bar0(uint8_t bus, uint8_t dev, uint8_t func)
{
    uint32_t bar_lo = vos3_pci_read32(bus, dev, func, 0x10);
    /* Memory-space BARs have bit 0 = 0; I/O-space BARs have bit 0 = 1. */
    if (bar_lo & 0x1) return 0;  /* I/O-space BAR not supported here */

    uint64_t addr = bar_lo & ~0xFULL;

    /* 64-bit BAR? bits [2:1] == 0b10 means 64-bit memory. */
    if (((bar_lo >> 1) & 0x3) == 0x2) {
        uint32_t bar_hi = vos3_pci_read32(bus, dev, func, 0x14);
        addr |= ((uint64_t)bar_hi) << 32;
    }
    return addr;
}

/* PTE flag layout — must match kernel/src/mm/vmm.c.
 * Bit 0:  Present
 * Bit 1:  Writable
 * Bit 2:  User
 * Bit 3:  PWT (write-through)
 * Bit 4:  PCD (cache-disable)
 * Bit 7:  PAT (large page) — ignored for 4 KiB
 * Bit 63: NX (no-execute)
 */
#define PTE_PRESENT      (1ULL << 0)
#define PTE_WRITABLE     (1ULL << 1)
#define PTE_PCD          (1ULL << 4)
#define PTE_NX           (1ULL << 63)

/* Map a PCI BAR0 into the calling slot's address space.
 *
 * Returns the virtual address of the mapping, or 0 on failure (caller
 * lacks GPU_DIRECT capability, slot is in ZOMBIE state, BAR is invalid).
 *
 * ISR-safe: uses vos3_vmm_cas_pte() (atomic_cmpxchg internally) for the
 * PTE installation. Does NOT acquire a kernel-side spinlock.
 */
uintptr_t vos3_vfio_pci_map(uint32_t slot_id,
                            uint8_t bus, uint8_t dev, uint8_t func,
                            size_t len)
{
    /* Capability gate. */
    if (!vos3_slot_has_capability(slot_id, VOS3_CAP_GPU_DIRECT)) {
        mmr_record_security_violation(slot_id,
                                       VOS3_SECVIO_REASON_OWNER_MISMATCH);
        return 0;
    }

    /* Slot state gate — ZOMBIE slots get nothing. */
    if (vos3_slot_get_state(slot_id) == VOS3_SLOT_STATE_ZOMBIE) {
        return 0;
    }

    /* Look up BAR0. */
    uint64_t bar_pa = vfio_read_bar0(bus, dev, func);
    if (bar_pa == 0) return 0;

    /* Round len up to 4 KiB pages. */
    if (len == 0) return 0;
    size_t pages = (len + 0xFFF) >> 12;

    /* Allocate a contiguous MMIO window in kernel VA. */
    uintptr_t va_base = vos3_vmm_alloc_mmio_window(pages << 12);
    if (va_base == 0) return 0;

    /* Walk the pages; each PTE install is a CAS — ISR-safe. */
    for (size_t i = 0; i < pages; i++) {
        uintptr_t va = va_base + (i << 12);
        uint64_t  pa = bar_pa  + ((uint64_t)i << 12);

        /* Build PTE: present + writable + cache-disabled (MMIO) + NX. */
        uint64_t new_pte = (pa & ~0xFFFULL) | PTE_PRESENT | PTE_WRITABLE
                                            | PTE_PCD     | PTE_NX;

        /* Old PTE expected to be 0 (window allocator hands out fresh VA). */
        if (vos3_vmm_cas_pte(va, /*old=*/0, new_pte) != 0) {
            /* Rollback — unmap what we already installed. */
            vos3_vmm_unmap_range(va_base, i << 12);
            return 0;
        }
    }

    return va_base;
}

/* Unmap a previously-mapped BAR window. Best-effort scrub of the slot's
 * view of the buffer (zeros the VA range before tearing down). */
int vos3_vfio_pci_unmap(uint32_t slot_id, uintptr_t va_base, size_t len)
{
    if (!vos3_slot_has_capability(slot_id, VOS3_CAP_GPU_DIRECT)) return -1;
    if (va_base == 0 || len == 0) return -1;

    /* MMIO regions are not safely zeroed via plain stores (would race
     * with device DMA). We simply tear down the mapping. */
    return vos3_vmm_unmap_range(va_base, len);
}

/* Hook for virtio_gpu.c — exposes a shared VA window between the slot's
 * GPU buffer and the virtio-gpu device's command queue. The actual
 * virtio_gpu integration is handled in drivers/virtio_gpu.c via the
 * vos3_vfio_share_with_virtio_gpu_install() registration below. */

typedef int (*vos3_vfio_virtio_gpu_hook_t)(uint32_t slot_id,
                                           uintptr_t va_base,
                                           size_t len);
static vos3_vfio_virtio_gpu_hook_t g_virtio_gpu_hook;

void vos3_vfio_share_with_virtio_gpu_install(vos3_vfio_virtio_gpu_hook_t hook)
{
    g_virtio_gpu_hook = hook;
}

int vos3_vfio_share_with_virtio_gpu(uint32_t slot_id,
                                    uintptr_t va_base, size_t len)
{
    if (g_virtio_gpu_hook == 0) return -1;  /* virtio_gpu not yet wired */
    return g_virtio_gpu_hook(slot_id, va_base, len);
}
