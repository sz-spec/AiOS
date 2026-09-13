/**
 * @file boot_drivers.c
 * @brief VOS3 Boot — Hardware Driver Initialization
 *
 * Extracted from kmain.c (Phase 8.5-C Sovereign Consolidation).
 * CRC64, PCI, ACPI, Storage HAL, ivshmem, DMA, HDA, Driver
 * Synthesis, NPU dispatch, and kernel .text integrity hash.
 */

#include "../../include/vos/boot_drivers.h"
#include "../../include/vos/console.h"
#include "../../include/vos/crc64.h"
#include "../../include/vos/pci.h"
#include "../../include/vos/ivshmem.h"

/* External driver init functions */
extern int  vos3_acpi_init(uint64_t rsdp_phys, uint64_t hhdm_off);
extern void vos3_acpi_print_summary(void);
extern int  vos3_storage_hal_init(void);
extern int  vos3_dma_warp_init(void);
extern int  vos3_hda_init(void);
extern int  vos3_driver_auto_probe(void);
extern int  vos3_npu_dispatch_init(void);
extern void vos3_ktext_hash_init(void);

/* v20.2: MMR audit ledger + Hyper-V coexistence + TPM2 */
extern void mmr_init(void);
extern int  hyperv_detect(void);
extern void tpm2_boot_measurement(void);

/* ============================================================================
 * BOOT DRIVERS INIT
 * ============================================================================ */

int boot_drivers_init(const vos3_boot_info_t *boot_info)
{
    /* ===== Phase 9a: CRC64-ECMA Lookup Table ===== */
    VOS3_INFO("Initializing CRC64-ECMA lookup table");
    vos3_crc64_init();

    /* ===== Phase v20.0: Generic PCI Bus Scan ===== */
    VOS3_INFO("Scanning PCI bus (HAL Foundation)");
    vos3_pci_bus_scan();

    /* ===== Phase 8.2: ACPI Table Parser (Sovereign Integrity) =====
     *
     * v21.4.2 (Track A — closes harness-runs IDs 12/13/14/40 on PVH boot):
     * BIOS-region RSDP fallback. When the bootloader did NOT pass an
     * RSDP through the standard channel (Limine response, multiboot2
     * tag), scan the legacy BIOS area 0xE0000-0xFFFFF for the "RSD PTR "
     * signature and validate the ACPI 1.0 checksum. PVH boot via
     * `qemu -kernel` lands in this fallback path because no boot
     * loader runs.
     *
     * The scan uses the kernel's higher-half direct-map offset
     * (0xFFFF800000000000) to read physical addresses; this is set up
     * by the VMM init that runs earlier in boot_mm_init.
     */
    uint64_t rsdp_phys = boot_info->rsdp_addr;
    int from_bios_scan = 0;
    if ((boot_info->flags & VOS3_BOOT_FLAG_ACPI) == 0 || rsdp_phys == 0ULL) {
        const uint64_t hhdm_off  = 0xFFFF800000000000ULL;
        const uint64_t scan_start = 0xE0000ULL;
        const uint64_t scan_end   = 0x100000ULL;  /* exclusive */
        static const uint8_t SIG[8] = {
            'R', 'S', 'D', ' ', 'P', 'T', 'R', ' '
        };
        for (uint64_t p = scan_start; p + 20U <= scan_end; p += 16U) {
            const uint8_t *bytes = (const uint8_t *)(uintptr_t)(hhdm_off + p);
            int match = 1;
            for (int i = 0; i < 8; i++) {
                if (bytes[i] != SIG[i]) { match = 0; break; }
            }
            if (!match) continue;
            /* ACPI 1.0 RSDP: first 20 bytes sum to 0 mod 256. */
            uint8_t sum = 0;
            for (int i = 0; i < 20; i++) sum = (uint8_t)(sum + bytes[i]);
            if (sum != 0) continue;
            rsdp_phys = p;
            from_bios_scan = 1;
            break;
        }
        if (from_bios_scan) {
            VOS3_INFO("[BIOS-RSDP] Recovered RSDP at phys=0x%llx via BIOS-region scan",
                      (unsigned long long)rsdp_phys);
        }
    }

    if (rsdp_phys != 0ULL) {
        VOS3_INFO("Initializing ACPI subsystem");
        int acpi_rc = vos3_acpi_init(rsdp_phys, 0xFFFF800000000000ULL);
        if (acpi_rc == 0) {
            vos3_acpi_print_summary();
        } else {
            VOS3_WARN("ACPI init returned %d — continuing without ACPI", acpi_rc);
        }
    } else {
        VOS3_INFO("No ACPI tables (boot flag not set, BIOS scan empty)");
    }

    /* ===== Phase 9: Storage HAL (AHCI/NVMe/VirtIO) ===== */
    vos3_storage_hal_init();

    /* ===== Phase 9b: ivshmem Warp Drive ===== */
    VOS3_INFO("Probing ivshmem (Warp Drive)");
    {
        int ivshmem_rc = vos3_ivshmem_init();
        if (ivshmem_rc == 0) {
            VOS3_INFO("Warp Drive active: %zu bytes", vos3_ivshmem_size());
        } else {
            VOS3_INFO("Warp Drive not available (VBus fallback)");
        }
    }

    /* ===== Phase 9: Warp-Drive DMA Engine ===== */
    {
        int dma_rc = vos3_dma_warp_init();
        if (dma_rc == 0) {
            VOS3_INFO("Warp-Drive DMA engine active");
        }
    }

    /* ===== Phase 8.5: Intel HD Audio Sovereign Driver ===== */
    {
        int hda_rc = vos3_hda_init();
        if (hda_rc == 0) {
            VOS3_INFO("HD Audio driver active (V-Palace injection ready)");
        } else {
            VOS3_INFO("HD Audio not available (rc=%d)", hda_rc);
        }
    }

    /* ===== Phase 8.5-U: Autonomous Driver Synthesis ===== */
    {
        int synth_count = vos3_driver_auto_probe();
        VOS3_INFO("Driver Synthesis: %d unknown device(s) auto-probed", synth_count);
    }

    /* ===== Phase 8.5-U: NPU Temporal Multi-Slicing ===== */
    {
        int npu_d_rc = vos3_npu_dispatch_init();
        if (npu_d_rc == 0) {
            VOS3_INFO("NPU Temporal Multi-Slicing active (250ms PASID rotation)");
        } else {
            VOS3_INFO("NPU Temporal Multi-Slicing deferred (rc=%d)", npu_d_rc);
        }
    }

    /* ===== Phase v20.0: Kernel .text Integrity Hash ===== */
    vos3_ktext_hash_init();

    /* ===== v20.2: MMR Audit Ledger ===== */
    VOS3_INFO("Initializing Merkle Mountain Range audit ledger");
    mmr_init();

    /* ===== v20.2: Hyper-V Coexistence Detection ===== */
    hyperv_detect();

#ifdef VOS3_TARGET_HYPERV
    /* Stage 14.D.3 — Hyper-V divergent init.
     * Compiled in only when this build was produced with
     * -DVOS3_TARGET_HYPERV (see kernel/Makefile vos3-hyperv.elf rule).
     * Performs the canonical Hyper-V identification + hypercall-page
     * setup + SynIC enable per TLFS v6.0b. The init function is a
     * no-op if hyperv_detect() didn't actually find a hypervisor,
     * so this build is still safe to boot on bare metal / QEMU TCG. */
    {
        extern void vos3_hyperv_init(void);
        vos3_hyperv_init();
    }
#endif

    /* ===== v20.2: TPM 2.0 Boot Measurement (Arrow of Time) ===== */
    tpm2_boot_measurement();

    return 0;
}
