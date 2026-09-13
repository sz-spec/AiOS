/**
 * @file uefi_boot.h
 * @brief Phase 9: UEFI Boot Foundation
 *
 * Defines the UEFI memory map structures and handover protocol
 * for transitioning from UEFI firmware to VOS3 kernel.
 * Currently VOS3 boots via Limine (which handles UEFI for us);
 * this header prepares for direct UEFI stub booting.
 *
 * @version 9.0.0
 * @date 2026-04-09
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_UEFI_BOOT_H
#define VOS3_UEFI_BOOT_H

#include <stdint.h>
#include <stddef.h>

/* EFI Memory Types (UEFI Spec 2.10, Table 7.6) */
typedef enum {
    EFI_RESERVED            = 0,
    EFI_LOADER_CODE         = 1,
    EFI_LOADER_DATA         = 2,
    EFI_BOOT_SERVICES_CODE  = 3,
    EFI_BOOT_SERVICES_DATA  = 4,
    EFI_RUNTIME_SERVICES_CODE = 5,
    EFI_RUNTIME_SERVICES_DATA = 6,
    EFI_CONVENTIONAL_MEMORY = 7,
    EFI_UNUSABLE_MEMORY     = 8,
    EFI_ACPI_RECLAIM        = 9,
    EFI_ACPI_NVS            = 10,
    EFI_MEMORY_MAPPED_IO    = 11,
    EFI_MEMORY_MAPPED_IO_PS = 12,
    EFI_PAL_CODE            = 13,
    EFI_PERSISTENT_MEMORY   = 14,
    EFI_UNACCEPTED_MEMORY   = 15,
    EFI_MAX_MEMORY_TYPE     = 16
} vos3_efi_mem_type_t;

/* EFI Memory Descriptor (matches UEFI spec layout) */
typedef struct vos3_efi_mem_desc {
    uint32_t    type;           /* vos3_efi_mem_type_t */
    uint32_t    pad;            /* Alignment padding */
    uint64_t    phys_start;     /* Physical start address */
    uint64_t    virt_start;     /* Virtual start (for SetVirtualAddressMap) */
    uint64_t    num_pages;      /* Number of 4KB pages */
    uint64_t    attribute;      /* Memory attributes */
} vos3_efi_mem_desc_t;

/* EFI Memory Attributes */
#define EFI_MEMORY_UC            0x0000000000000001ULL  /* Uncacheable */
#define EFI_MEMORY_WC            0x0000000000000002ULL  /* Write-Combining */
#define EFI_MEMORY_WT            0x0000000000000004ULL  /* Write-Through */
#define EFI_MEMORY_WB            0x0000000000000008ULL  /* Write-Back */
#define EFI_MEMORY_UCE           0x0000000000000010ULL  /* UC Exported */
#define EFI_MEMORY_WP            0x0000000000001000ULL  /* Write-Protect */
#define EFI_MEMORY_RP            0x0000000000002000ULL  /* Read-Protect */
#define EFI_MEMORY_XP            0x0000000000004000ULL  /* Execute-Protect */
#define EFI_MEMORY_NV            0x0000000000008000ULL  /* Non-Volatile */
#define EFI_MEMORY_MORE_RELIABLE 0x0000000000010000ULL
#define EFI_MEMORY_RO            0x0000000000020000ULL  /* Read-Only */
#define EFI_MEMORY_SP            0x0000000000040000ULL  /* Specific-Purpose */
#define EFI_MEMORY_RUNTIME       0x8000000000000000ULL  /* Needs runtime mapping */

/* VOS3 UEFI Boot Info (parsed from bootloader) */
typedef struct vos3_uefi_boot_info {
    uint32_t    efi_present;        /* 1 = booted via UEFI, 0 = legacy BIOS */
    uint32_t    efi_version;        /* EFI system table version */
    uint64_t    efi_system_table;   /* Physical address of EFI System Table */
    uint64_t    efi_runtime_start;  /* Runtime services physical start */
    uint64_t    efi_runtime_size;   /* Runtime services total size */
    uint32_t    mmap_entry_count;   /* Number of memory map entries */
    uint32_t    mmap_desc_size;     /* Size of each descriptor */
    vos3_efi_mem_desc_t *mmap;      /* Pointer to memory map array */
} vos3_uefi_boot_info_t;

/* UEFI boot info (global, set during early boot) */
extern vos3_uefi_boot_info_t g_uefi_boot_info;

/* Parse UEFI memory map into VOS3 PMM regions */
int vos3_uefi_parse_memory_map(const vos3_uefi_boot_info_t *info);

/* Validate GDT entries post-UEFI handover */
int vos3_gdt_validate(void);

/* Get UEFI boot status string */
const char *vos3_uefi_status_str(void);

#endif /* VOS3_UEFI_BOOT_H */
