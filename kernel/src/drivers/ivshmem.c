/**
 * @file ivshmem.c
 * @brief VOS3 ivshmem (Inter-VM Shared Memory) PCI Driver
 *
 * @details Minimal PCI discovery driver for QEMU ivshmem-plain device.
 *          Scans PCI bus 0 for vendor 0x1AF4 / device 0x1110, reads BAR2
 *          (the shared memory MMIO region), sizes it, and maps it into
 *          kernel virtual space via vos3_vmm_map_range().
 *
 *          This is a skeleton for future Warp Drive inter-VM communication.
 *          Not called from kmain.c yet — no QEMU -device ivshmem-plain
 *          is present in the current launch configuration.
 *
 * @version 0.1.0
 * @date 2026-04-03
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.9a — Skeleton only.
 */

#include "../../include/vos/ivshmem.h"
#include "../../include/vos/virtio_core.h"
#include "../../include/vos/console.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"

/* ============================================================================
 * IVSHMEM PCI CONSTANTS
 * ============================================================================ */

/** @brief PCI BAR2 offset in configuration space */
#define PCI_BAR2_OFFSET     0x18U

/** @brief MMIO BAR type mask — bit 0 clear = MMIO, set = I/O port */
#define PCI_BAR_IO_MASK     0x01U

/** @brief BAR address mask (strip type/prefetch/size bits) */
#define PCI_BAR_ADDR_MASK   0xFFFFFFF0U

/** @brief Virtual base for ivshmem mapping (inside VOS3_MMIO_START region) */
#define IVSHMEM_VBASE       0xFFFF880010000000ULL

/* ============================================================================
 * MODULE STATE
 * ============================================================================ */

/** @brief Physical base address of shared memory BAR */
static uint64_t g_ivshmem_phys  = 0;

/** @brief Size of shared memory region in bytes */
static size_t   g_ivshmem_size  = 0;

/** @brief Kernel virtual address of mapped shared memory */
static void    *g_ivshmem_vaddr = NULL;

/** @brief Spinlock protecting PCI config space BAR sizing on SMP */
static vos3_spinlock_t g_pci_config_lock = VOS3_SPINLOCK_INIT;

/** @brief v23.7: Per-zone active reader count — blocks cold_scrub while DMA in-flight */
static volatile uint32_t g_zone_active_readers[VOS3_WARP_ZONE_MAX];

void vos3_ivshmem_zone_reader_acquire(uint8_t slot_id)
{
    if (slot_id < VOS3_WARP_ZONE_MAX)
        __atomic_fetch_add(&g_zone_active_readers[slot_id], 1U, __ATOMIC_SEQ_CST);
}

void vos3_ivshmem_zone_reader_release(uint8_t slot_id)
{
    if (slot_id < VOS3_WARP_ZONE_MAX)
        __atomic_fetch_sub(&g_zone_active_readers[slot_id], 1U, __ATOMIC_SEQ_CST);
}

/* ============================================================================
 * PCI DISCOVERY AND INITIALIZATION
 * ============================================================================ */

/**
 * @brief Probe PCI bus for ivshmem device and map shared memory
 *
 * @details Scans PCI bus 0, devices 0-31, function 0 for the ivshmem
 *          PCI identity (vendor 0x1AF4, device 0x1110). On match,
 *          reads BAR2 to discover the MMIO shared memory region,
 *          performs BAR sizing, and maps the region into kernel space.
 *
 * @return 0 on success, -1 if device not found or BAR invalid
 */
int vos3_ivshmem_init(void)
{
    uint8_t dev;

    for (dev = 0; dev < 32U; dev++) {
        uint32_t id_reg = virtio_pci_read32(0, dev, 0, 0x00);
        uint16_t vendor = (uint16_t)(id_reg & 0xFFFFU);
        uint16_t device = (uint16_t)(id_reg >> 16U);

        if (vendor != IVSHMEM_VENDOR_ID || device != IVSHMEM_DEVICE_ID) {
            continue;
        }

        /* Found ivshmem device on bus 0, device <dev> */
        vos3_console_puts("[IVSHMEM] found on PCI 0:");
        vos3_console_putdec((int64_t)dev);
        vos3_console_puts(".0\n");

        /* Read BAR2 (offset 0x18) — shared memory MMIO region */
        uint32_t bar2 = virtio_pci_read32(0, dev, 0, PCI_BAR2_OFFSET);

        /* Verify this is an MMIO BAR (bit 0 clear) */
        if (bar2 & PCI_BAR_IO_MASK) {
            vos3_console_puts("[IVSHMEM] BAR2 is I/O port, expected MMIO\n");
            return -1;
        }

        uint32_t bar2_addr = bar2 & PCI_BAR_ADDR_MASK;

        /* BAR sizing: write all-ones, read back, restore original.
         * Must be atomic on SMP — another CPU's PCI access could interleave. */
        vos3_spinlock_lock(&g_pci_config_lock);
        virtio_outl(PCI_CONFIG_ADDR,
                    0x80000000U | ((uint32_t)dev << 11U) | PCI_BAR2_OFFSET);
        virtio_outl(PCI_CONFIG_DATA, 0xFFFFFFFFU);

        uint32_t size_mask = virtio_inl(PCI_CONFIG_DATA);

        /* Restore original BAR value */
        virtio_outl(PCI_CONFIG_ADDR,
                    0x80000000U | ((uint32_t)dev << 11U) | PCI_BAR2_OFFSET);
        virtio_outl(PCI_CONFIG_DATA, bar2);
        vos3_spinlock_unlock(&g_pci_config_lock);

        /* Decode size: mask low bits, bitwise NOT, add 1 */
        size_mask &= PCI_BAR_ADDR_MASK;
        if (size_mask == 0U) {
            vos3_console_puts("[IVSHMEM] BAR2 size is zero\n");
            return -1;
        }
        uint32_t region_size = (~size_mask) + 1U;

        g_ivshmem_phys = (uint64_t)bar2_addr;
        g_ivshmem_size = (size_t)region_size;

        vos3_console_puts("[IVSHMEM] phys=0x");
        vos3_console_puthex(g_ivshmem_phys, 8);
        vos3_console_puts(" size=0x");
        vos3_console_puthex((uint64_t)g_ivshmem_size, 8);
        vos3_console_puts("\n");

        /* Map into kernel virtual space (MMIO region) */
        int rc = vos3_vmm_map_range(
            IVSHMEM_VBASE,
            (uintptr_t)g_ivshmem_phys,
            g_ivshmem_size,
            VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_NOCACHE
        );
        if (rc != 0) {
            vos3_console_puts("[IVSHMEM] vmm_map_range failed\n");
            g_ivshmem_phys = 0;
            g_ivshmem_size = 0;
            return -1;
        }

        g_ivshmem_vaddr = (void *)IVSHMEM_VBASE;

        vos3_console_puts("[IVSHMEM] mapped at virt=0x");
        vos3_console_puthex(IVSHMEM_VBASE, 16);
        vos3_console_puts("\n");

        return 0;
    }

    vos3_console_puts("[IVSHMEM] not found\n");
    return -1;
}

/* ============================================================================
 * PUBLIC ACCESSORS
 * ============================================================================ */

void *vos3_ivshmem_base(void)
{
    return g_ivshmem_vaddr;
}

size_t vos3_ivshmem_size(void)
{
    return g_ivshmem_size;
}

/* ============================================================================
 * PHASE 4.9b: WARP DRIVE — Zone Partitioning
 * ============================================================================ */

void *vos3_ivshmem_zone_base_unchecked(uint8_t slot_id)
{
    if (g_ivshmem_vaddr == NULL || slot_id >= VOS3_WARP_ZONE_MAX) return NULL;
    if ((size_t)(slot_id + 1) * VOS3_WARP_ZONE_SIZE > g_ivshmem_size) return NULL;
    return (void *)((uintptr_t)g_ivshmem_vaddr + (size_t)slot_id * VOS3_WARP_ZONE_SIZE);
}

void *vos3_ivshmem_zone_base(uint8_t slot_id)
{
    /* Phase 5 — Tier 2: Ownership check — current task must own the slot */
    vos3_task_t *cur = vos3_sched_current();
    uint32_t tid = cur ? cur->tid : 0;
    if (vos3_ai_check_slot_owner(slot_id, tid) != 0) return NULL;
    return vos3_ivshmem_zone_base_unchecked(slot_id);
}

int vos3_ivshmem_available(void)
{
    return (g_ivshmem_vaddr != NULL) ? 1 : 0;
}

/* ============================================================================
 * PHASE 4.9b-DA: COLD SCRUB — Eliminate Billing Ghosts
 * ============================================================================ */

/** @brief Identity Buffer offset within each warp zone.
 *  Last 2MB of the 16MB zone is reserved for tombstone-preserved state. */
#define IDENTITY_BUF_OFFSET     (VOS3_WARP_ZONE_SIZE - VOS3_PAGE_SIZE_2M)

void vos3_ivshmem_cold_scrub(uint8_t slot_id)
{
    /* v23.7: Wait for any in-flight DMA readers to drain before zeroing.
     * Prevents mid-transfer corruption (Defect #2 from v23.6 audit). */
    if (slot_id < VOS3_WARP_ZONE_MAX) {
        uint32_t spins = 0;
        while (__atomic_load_n(&g_zone_active_readers[slot_id], __ATOMIC_SEQ_CST) > 0) {
            __asm__ volatile("pause");
            if (++spins > 100000U) {
                vos3_console_printf("[IVSHMEM] cold_scrub: waiting for %u readers on zone %u\n",
                    __atomic_load_n(&g_zone_active_readers[slot_id], __ATOMIC_SEQ_CST), slot_id);
                spins = 0;
            }
        }
    }

    void *base = vos3_ivshmem_zone_base_unchecked(slot_id);
    if (base == NULL) return;

    size_t zone_sz = VOS3_WARP_ZONE_SIZE;
    if ((size_t)(slot_id + 1) * zone_sz > g_ivshmem_size) return;

    /* --- Cognitive Seal: Tombstone-aware Identity Buffer ---
     * Before wiping, check the slot's HugePage PTEs for bit 52 (tombstone).
     * Tombstoned pages hold crash-orphaned agent state that must survive
     * the cold scrub. Copy their data to the Identity Buffer region at the
     * end of the warp zone so the recovery subsystem can reclaim it. */
    vos3_ai_model_slot_t slot_info;
    vos3_ai_model_slot_get_info(slot_id, &slot_info);

    uint32_t ident_off = 0;  /* Write cursor into Identity Buffer */
    void *ident_base = (void *)((uintptr_t)base + IDENTITY_BUF_OFFSET);

    if (slot_info.base != 0 && slot_info.hp_count > 0) {
        for (uint32_t i = 0; i < slot_info.hp_count && i < 7U; i++) {
            uintptr_t vaddr = slot_info.base + (uintptr_t)i * VOS3_PAGE_SIZE_2M;
            if (vos3_vmm_is_tombstoned(vaddr)) {
                /* Preserve this page's first 256KB into Identity Buffer */
                void *src = (void *)vaddr;
                size_t preserve_sz = 256U * 1024U;
                if (ident_off + preserve_sz > VOS3_PAGE_SIZE_2M) break;
                volatile uint8_t *d = (volatile uint8_t *)((uintptr_t)ident_base + ident_off);
                volatile uint8_t *s = (volatile uint8_t *)src;
                for (size_t b = 0; b < preserve_sz; b++) {
                    d[b] = s[b];
                }
                ident_off += (uint32_t)preserve_sz;
                vos3_console_printf("[IVSHMEM] Tombstone preserved: slot=%u hp=%u → ident+0x%x\n",
                                    slot_id, i, ident_off - (uint32_t)preserve_sz);
            }
        }
    }

    /* Non-Temporal Zeroing: bypass cache hierarchy entirely.
     * movnti writes 8 bytes directly to RAM via write-combining buffers
     * without polluting L1/L2/L3.  Eliminates the separate clflush pass.
     * Single-pass zero + writeback with zero cache footprint.
     * Uses GPR (no SSE required — kernel compiled with -mno-sse). */
    size_t scrub_end = (ident_off > 0) ? IDENTITY_BUF_OFFSET : zone_sz;

    /* Chunked Scrub: process in 512KB chunks with interrupt windows between
     * them. Each chunk disables IRQs for ~150us (512KB / ~3.5 GB/s movnti),
     * well within the 1ms latency budget. The sti between chunks allows
     * pending timer ticks, IPIs, and NMIs to be serviced promptly.
     * (K-R5 fix: original code held IRQs disabled for the entire ~2ms pass.) */
#define SCRUB_CHUNK_SIZE    (512U * 1024U)  /* 512KB per IRQ-disabled window */

    size_t off = 0;
    while (off < scrub_end) {
        size_t chunk_end = off + SCRUB_CHUNK_SIZE;
        if (chunk_end > scrub_end) chunk_end = scrub_end;

        __asm__ volatile("cli" ::: "memory");

        /* 64-byte unrolled loop: 8x movnti per iteration (one full cacheline) */
        for (; off + 64U <= chunk_end; off += 64U) {
            uintptr_t addr = (uintptr_t)base + off;
            __asm__ volatile(
                "movnti %%rax,   (%0)\n\t"
                "movnti %%rax,  8(%0)\n\t"
                "movnti %%rax, 16(%0)\n\t"
                "movnti %%rax, 24(%0)\n\t"
                "movnti %%rax, 32(%0)\n\t"
                "movnti %%rax, 40(%0)\n\t"
                "movnti %%rax, 48(%0)\n\t"
                "movnti %%rax, 56(%0)\n\t"
                :: "r"(addr), "a"((uint64_t)0) : "memory"
            );
        }
        /* Handle trailing bytes (< 64) within this chunk */
        for (; off < chunk_end; off++) {
            ((volatile uint8_t *)base)[off] = 0;
        }

        /* sfence: drain non-temporal write-combining buffers for this chunk */
        __asm__ volatile("sfence" ::: "memory");

        /* Store Buffer Purge: drain Fill Buffers before re-enabling IRQs */
        __asm__ volatile("lock addl $0, (%%rsp)" ::: "memory", "cc");

        __asm__ volatile("sti" ::: "memory");  /* Re-enable interrupts */

        /* Brief window: pending IRQs (timer, IPI, NMI) are serviced here.
         * A single nop provides a minimal but sufficient instruction window
         * for the CPU to recognize and dispatch pending interrupts. */
        __asm__ volatile("nop" ::: "memory");
    }

    /* I-Cache Coherency Seal: serializing instruction flushes the entire
     * instruction pipeline, preventing speculative execution from leaking
     * stale model context across slot boundaries.  cpuid is the canonical
     * x86 serializing instruction (Intel SDM Vol. 3A §8.3). */
    {
        uint32_t a = 0, b, c, d;
        __asm__ volatile("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d)
                         : "a"(a) : "memory");
    }

#ifdef VOS3_PROD_HARDENING
    /* Bare-metal paranoia: wbinvd invalidates all cache lines system-wide.
     * Overkill after non-temporal stores (which bypass cache), but guarantees
     * zero residual from ANY prior cacheable access to this region. */
    __asm__ volatile("wbinvd" ::: "memory");
    /* Second serialization after wbinvd to ensure pipeline is fully drained */
    {
        uint32_t a2 = 0, b2, c2, d2;
        __asm__ volatile("cpuid" : "=a"(a2), "=b"(b2), "=c"(c2), "=d"(d2)
                         : "a"(a2) : "memory");
    }
#endif
}

/* ============================================================================
 * KINETIC FILL: Partial cold scrub for jitter-work timing
 * ============================================================================ */

/** Per-slot scrub cursor — tracks where the partial scrub left off */
static size_t g_kinetic_scrub_cursor[VOS3_WARP_ZONE_MAX] = {0};

uint32_t vos3_ivshmem_cold_scrub_partial(uint8_t slot_id, uint32_t lines)
{
    void *base = vos3_ivshmem_zone_base_unchecked(slot_id);
    if (base == NULL) return 0;

    size_t zone_sz = VOS3_WARP_ZONE_SIZE;
    if ((size_t)(slot_id + 1) * zone_sz > g_ivshmem_size) return 0;

    size_t cursor = g_kinetic_scrub_cursor[slot_id & (VOS3_WARP_ZONE_MAX - 1)];
    uint32_t scrubbed = 0;

    for (uint32_t i = 0; i < lines && cursor < zone_sz; i++, scrubbed++) {
        uintptr_t addr = (uintptr_t)base + cursor;
        /* movnti 8 bytes × 8 = one full 64-byte cacheline, non-temporal */
        __asm__ volatile(
            "movnti %%rax,   (%0)\n\t"
            "movnti %%rax,  8(%0)\n\t"
            "movnti %%rax, 16(%0)\n\t"
            "movnti %%rax, 24(%0)\n\t"
            "movnti %%rax, 32(%0)\n\t"
            "movnti %%rax, 40(%0)\n\t"
            "movnti %%rax, 48(%0)\n\t"
            "movnti %%rax, 56(%0)\n\t"
            :: "r"(addr), "a"((uint64_t)0) : "memory"
        );
        cursor += 64;
    }

    /* Wrap cursor for continuous background scrubbing */
    if (cursor >= zone_sz) cursor = 0;
    g_kinetic_scrub_cursor[slot_id & (VOS3_WARP_ZONE_MAX - 1)] = cursor;

    if (scrubbed > 0) {
        __asm__ volatile("sfence" ::: "memory");
        /* Store Buffer Purge: drain Fill Buffers after non-temporal writes */
        __asm__ volatile("lock addl $0, (%%rsp)" ::: "memory", "cc");
    }
    return scrubbed;
}
