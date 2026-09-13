/**
 * @file uefi_boot.c
 * @brief Phase 9: UEFI Boot Foundation + GDT/IDT Validation
 *
 * Provides UEFI memory map parsing and GDT integrity validation
 * for bare-metal boot on Intel/AMD silicon. Currently VOS3 boots
 * via Limine which handles the UEFI->Long Mode transition; this
 * module prepares for direct UEFI stub boot in Phase 9.2.
 *
 * @version 9.0.0
 * @date 2026-04-09
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../include/vos/uefi_boot.h"
#include "../../include/vos/console.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/vmm.h"
#include <stdint.h>

/* Global UEFI boot info */
vos3_uefi_boot_info_t g_uefi_boot_info = { 0 };

/* EFI memory type names for logging */
static const char *efi_mem_type_name(uint32_t type)
{
    switch (type) {
    case EFI_RESERVED:              return "Reserved";
    case EFI_LOADER_CODE:           return "LoaderCode";
    case EFI_LOADER_DATA:           return "LoaderData";
    case EFI_BOOT_SERVICES_CODE:    return "BSCode";
    case EFI_BOOT_SERVICES_DATA:    return "BSData";
    case EFI_RUNTIME_SERVICES_CODE: return "RSCode";
    case EFI_RUNTIME_SERVICES_DATA: return "RSData";
    case EFI_CONVENTIONAL_MEMORY:   return "Conventional";
    case EFI_UNUSABLE_MEMORY:       return "Unusable";
    case EFI_ACPI_RECLAIM:          return "ACPIReclaim";
    case EFI_ACPI_NVS:              return "ACPI_NVS";
    case EFI_MEMORY_MAPPED_IO:      return "MMIO";
    case EFI_MEMORY_MAPPED_IO_PS:   return "MMIO_PS";
    case EFI_PAL_CODE:              return "PAL";
    case EFI_PERSISTENT_MEMORY:     return "Persistent";
    case EFI_UNACCEPTED_MEMORY:     return "Unaccepted";
    default:                        return "Unknown";
    }
}

/* Parse UEFI memory map and log regions */
int vos3_uefi_parse_memory_map(const vos3_uefi_boot_info_t *info)
{
    (void)efi_mem_type_name; /* Suppress unused warning when not UEFI-booted */

    if (!info || !info->efi_present) {
        VOS3_INFO("[UEFI] Not booted via UEFI (legacy/Limine mode)");
        return 0;
    }

    VOS3_INFO("[UEFI] EFI v%u.%u, %u memory map entries",
              info->efi_version >> 16, info->efi_version & 0xFFFF,
              info->mmap_entry_count);

    if (!info->mmap || info->mmap_entry_count == 0)
        return -22; /* EINVAL */

    uint64_t total_conventional = 0;
    uint64_t total_runtime = 0;
    uint64_t total_reserved = 0;

    for (uint32_t i = 0; i < info->mmap_entry_count; i++) {
        const vos3_efi_mem_desc_t *desc = &info->mmap[i];
        uint64_t size = desc->num_pages * 4096;

        switch (desc->type) {
        case EFI_CONVENTIONAL_MEMORY:
        case EFI_BOOT_SERVICES_CODE:
        case EFI_BOOT_SERVICES_DATA:
        case EFI_LOADER_CODE:
        case EFI_LOADER_DATA:
            total_conventional += size;
            break;
        case EFI_RUNTIME_SERVICES_CODE:
        case EFI_RUNTIME_SERVICES_DATA:
            total_runtime += size;
            break;
        default:
            total_reserved += size;
            break;
        }
    }

    VOS3_INFO("[UEFI] Memory: %llu MB conventional, %llu KB runtime, %llu KB reserved",
              (unsigned long long)(total_conventional / (1024 * 1024)),
              (unsigned long long)(total_runtime / 1024),
              (unsigned long long)(total_reserved / 1024));

    return 0;
}

/* ---- GDT Validation ---- */

/* Read the current GDTR */
typedef struct __attribute__((packed)) {
    uint16_t limit;
    uint64_t base;
} gdtr_t;

static inline void sgdt(gdtr_t *out)
{
    __asm__ volatile("sgdt %0" : "=m"(*out));
}

/* Read CS register */
static inline uint16_t read_cs(void)
{
    uint16_t cs;
    __asm__ volatile("mov %%cs, %0" : "=r"(cs));
    return cs;
}

/* Read SS register */
static inline uint16_t read_ss(void)
{
    uint16_t ss;
    __asm__ volatile("mov %%ss, %0" : "=r"(ss));
    return ss;
}

int vos3_gdt_validate(void)
{
    int errors = 0;

    /* 1. Verify GDTR is loaded and sane */
    gdtr_t gdtr;
    sgdt(&gdtr);

    if (gdtr.base == 0 || gdtr.limit < 39) { /* Minimum: 5 entries x 8 bytes - 1 */
        VOS3_INFO("[GDT-AUDIT] FAIL: GDTR invalid (base=0x%lx, limit=%u)",
                  (unsigned long)gdtr.base, gdtr.limit);
        errors++;
    } else {
        VOS3_INFO("[GDT-AUDIT] GDTR: base=0x%lx, limit=%u (%u entries)",
                  (unsigned long)gdtr.base, gdtr.limit,
                  (gdtr.limit + 1) / 8);
    }

    /* 2. Verify CS is kernel code segment (0x08) */
    uint16_t cs = read_cs();
    if (cs != 0x08) {
        VOS3_INFO("[GDT-AUDIT] FAIL: CS=0x%x (expected 0x08)", cs);
        errors++;
    }

    /* 3. Verify SS is kernel data segment (0x10) */
    uint16_t ss = read_ss();
    if (ss != 0x10) {
        VOS3_INFO("[GDT-AUDIT] FAIL: SS=0x%x (expected 0x10)", ss);
        errors++;
    }

    /* 4. Verify null descriptor is zero */
    uint64_t *gdt_entries = (uint64_t *)gdtr.base;
    if (gdt_entries[0] != 0) {
        VOS3_INFO("[GDT-AUDIT] FAIL: Null descriptor non-zero: 0x%lx",
                  (unsigned long)gdt_entries[0]);
        errors++;
    }

    /* 5. Verify kernel code segment (entry 1) has L bit set (64-bit) */
    uint64_t kcs = gdt_entries[1];
    if (!(kcs & (1ULL << 53))) { /* L bit = bit 53 in 64-bit GDT entry encoding */
        VOS3_INFO("[GDT-AUDIT] FAIL: Kernel CS missing L bit (64-bit mode)");
        errors++;
    }

    /* 6. Verify kernel code segment has P bit set (present) */
    if (!(kcs & (1ULL << 47))) {
        VOS3_INFO("[GDT-AUDIT] FAIL: Kernel CS not present");
        errors++;
    }

    /* 7. Verify IDT is loaded */
    struct __attribute__((packed)) {
        uint16_t limit;
        uint64_t base;
    } idtr;
    __asm__ volatile("sidt %0" : "=m"(idtr));

    if (idtr.base == 0 || idtr.limit < 255) {
        VOS3_INFO("[GDT-AUDIT] FAIL: IDTR invalid (base=0x%lx, limit=%u)",
                  (unsigned long)idtr.base, idtr.limit);
        errors++;
    }

    /* 8. Verify CR0 has PE + PG bits set (protected + paging) */
    uint64_t cr0;
    __asm__ volatile("mov %%cr0, %0" : "=r"(cr0));
    if (!(cr0 & 1)) { /* PE bit */
        VOS3_INFO("[GDT-AUDIT] FAIL: CR0.PE not set (not in protected mode)");
        errors++;
    }
    if (!(cr0 & (1ULL << 31))) { /* PG bit */
        VOS3_INFO("[GDT-AUDIT] FAIL: CR0.PG not set (paging disabled)");
        errors++;
    }

    /* 9. Verify CR4 has PAE set (required for 64-bit) */
    uint64_t cr4;
    __asm__ volatile("mov %%cr4, %0" : "=r"(cr4));
    if (!(cr4 & (1ULL << 5))) { /* PAE bit */
        VOS3_INFO("[GDT-AUDIT] FAIL: CR4.PAE not set");
        errors++;
    }

    /* 10. Verify EFER.LME and EFER.LMA (Long Mode Enable/Active) */
    uint32_t efer_lo, efer_hi;
    __asm__ volatile(
        "mov $0xC0000080, %%ecx\n\t"
        "rdmsr"
        : "=a"(efer_lo), "=d"(efer_hi)
        :
        : "ecx"
    );
    uint64_t efer = ((uint64_t)efer_hi << 32) | efer_lo;

    if (!(efer & (1ULL << 8))) { /* LME bit */
        VOS3_INFO("[GDT-AUDIT] FAIL: EFER.LME not set");
        errors++;
    }
    if (!(efer & (1ULL << 10))) { /* LMA bit */
        VOS3_INFO("[GDT-AUDIT] FAIL: EFER.LMA not set (not in long mode!)");
        errors++;
    }
    if (!(efer & (1ULL << 11))) { /* NXE bit */
        VOS3_INFO("[GDT-AUDIT] FAIL: EFER.NXE not set (W^X COMPLETELY DEFEATED)");
        errors++;  /* v23.2: NXE is mandatory — without it NX bit is ignored */
    }

    VOS3_INFO("[GDT-AUDIT] %s: %d errors (CR0=0x%lx, CR4=0x%lx, EFER=0x%lx)",
              errors == 0 ? "PASS" : "FAIL",
              errors,
              (unsigned long)cr0, (unsigned long)cr4, (unsigned long)efer);

    return errors;
}

/* Boot status string */
const char *vos3_uefi_status_str(void)
{
    if (g_uefi_boot_info.efi_present)
        return "UEFI";
    return "Legacy/Limine";
}
