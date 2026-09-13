/**
 * @file multiboot2_stub.c
 * @brief Multiboot2 bootloader protocol stub for VOS3
 *
 * @details Translates Multiboot2 boot information to VOS3 format
 *          for direct QEMU boot support.
 *
 * @version 1.0.0
 * @date 2026-02-16
 */

#include "../../include/vos/boot_info.h"
#include <stdint.h>

/* ============================================================================
 * MULTIBOOT2 DEFINITIONS
 * ============================================================================ */

#define MULTIBOOT2_MAGIC            0x36d76289U
#define MULTIBOOT2_TAG_TYPE_END             0
#define MULTIBOOT2_TAG_TYPE_MMAP            6
#define MULTIBOOT2_TAG_TYPE_BASIC_MEMINFO   4

/* Memory map entry types */
#define MULTIBOOT2_MMAP_TYPE_AVAILABLE      1
#define MULTIBOOT2_MMAP_TYPE_RESERVED       2
#define MULTIBOOT2_MMAP_TYPE_ACPI_RECLAIMABLE 3
#define MULTIBOOT2_MMAP_TYPE_NVS            4
#define MULTIBOOT2_MMAP_TYPE_BADRAM         5

/* Multiboot2 tag header */
struct multiboot2_tag {
    uint32_t type;
    uint32_t size;
};

/* Memory map tag */
struct multiboot2_tag_mmap {
    uint32_t type;
    uint32_t size;
    uint32_t entry_size;
    uint32_t entry_version;
    /* entries follow */
};

/* Memory map entry */
struct multiboot2_mmap_entry {
    uint64_t addr;
    uint64_t len;
    uint32_t type;
    uint32_t zero;
};

/* Basic memory info tag */
struct multiboot2_tag_basic_meminfo {
    uint32_t type;
    uint32_t size;
    uint32_t mem_lower;
    uint32_t mem_upper;
};

/* ============================================================================
 * STATIC STORAGE
 * ============================================================================ */

/** @brief Static boot info structure */
static vos3_boot_info_t g_boot_info;
/* Keep the RSDP beyond reclamation of bootloader-owned information pages. */
static uint8_t g_rsdp[36] __attribute__((aligned(16)));

/** @brief Static memory map entries */
static vos3_boot_mmap_entry_t g_mmap_entries[VOS3_BOOT_MAX_MMAP];

/* External kernel symbols from linker script */
extern char _kernel_phys_start[];
extern char _kernel_phys_end[];
extern char _kernel_virt_start[];
extern char _kernel_virt_end[];

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Convert Multiboot2 memory type to VOS3 type
 */
static uint32_t convert_mmap_type(uint32_t mb2_type)
{
    switch (mb2_type) {
        case MULTIBOOT2_MMAP_TYPE_AVAILABLE:
            return VOS3_MMAP_USABLE;
        case MULTIBOOT2_MMAP_TYPE_RESERVED:
            return VOS3_MMAP_RESERVED;
        case MULTIBOOT2_MMAP_TYPE_ACPI_RECLAIMABLE:
            return VOS3_MMAP_ACPI_RECLAIM;
        case MULTIBOOT2_MMAP_TYPE_NVS:
            return VOS3_MMAP_ACPI_NVS;
        case MULTIBOOT2_MMAP_TYPE_BADRAM:
            return VOS3_MMAP_BAD;
        default:
            return VOS3_MMAP_RESERVED;
    }
}

/* ============================================================================
 * MULTIBOOT2 PROCESSING
 * ============================================================================ */

/* External kernel entry */


/**
 * @brief Process multiboot2 tags and populate boot_info
 * @param mb2_info_addr Physical address of multiboot2 info structure
 */
const vos3_boot_info_t *vos3_multiboot2_parse(uint64_t mb2_info_addr)
{
    /* Initialize boot info */
    g_boot_info.magic = VOS3_BOOT_MAGIC;
    g_boot_info.version = VOS3_BOOT_VERSION;
    g_boot_info.size = sizeof(vos3_boot_info_t);
    g_boot_info.flags = 0;
    g_boot_info.rsdp_addr = 0;

    /* Set kernel addresses from linker symbols */
    g_boot_info.kernel_phys_start = (uint64_t)(uintptr_t)_kernel_phys_start;
    g_boot_info.kernel_phys_end = (uint64_t)(uintptr_t)_kernel_phys_end;
    g_boot_info.kernel_virt_start = (uint64_t)(uintptr_t)_kernel_virt_start;
    g_boot_info.kernel_virt_end = (uint64_t)(uintptr_t)_kernel_virt_end;

    /* Default direct map offset (identity mapping for now) */
    g_boot_info.direct_map_offset = 0xFFFF800000000000ULL;

    /* Process multiboot2 tags */
    /* First 8 bytes are total size and reserved */
    uint8_t* ptr = (uint8_t*)(uintptr_t)mb2_info_addr;
    uint32_t total_size = *(uint32_t*)ptr;
    if (total_size < 8 || mb2_info_addr > UINT64_MAX - total_size) return NULL;
    ptr += 8; /* Skip total_size and reserved */

    uint32_t mmap_count = 0;
    uint64_t total_memory = 0;

    while (total_size >= 8 && (uintptr_t)ptr <= mb2_info_addr + total_size - 8) {
        struct multiboot2_tag* tag = (struct multiboot2_tag*)ptr;

        if (tag->size < 8 || tag->size > mb2_info_addr + total_size - (uintptr_t)ptr) break;

        if (tag->type == MULTIBOOT2_TAG_TYPE_END) {
            break;
        }

        switch (tag->type) {
            case 14: /* ACPI 1.0 RSDP copy */
            case 15: { /* ACPI 2.0+ RSDP copy; prefer it over the old tag. */
                uint32_t length = tag->type == 15 ? 36U : 20U;
                if (tag->size < 8U + length) break;
                if (tag->type == 14 && g_boot_info.rsdp_addr != 0) break;
                const uint8_t *source = ptr + 8;
                for (uint32_t i = 0; i < sizeof(g_rsdp); i++)
                    g_rsdp[i] = i < length ? source[i] : 0;
                g_boot_info.rsdp_addr = (uint64_t)(uintptr_t)g_rsdp
                    - g_boot_info.kernel_virt_start + g_boot_info.kernel_phys_start;
                g_boot_info.flags |= VOS3_BOOT_FLAG_ACPI;
                break;
            }
            case MULTIBOOT2_TAG_TYPE_MMAP: {
                struct multiboot2_tag_mmap* mmap_tag = (struct multiboot2_tag_mmap*)tag;
                if (tag->size < sizeof(*mmap_tag) ||
                    mmap_tag->entry_size < sizeof(struct multiboot2_mmap_entry)) break;
                uint8_t* entry_ptr = (uint8_t*)(mmap_tag + 1);
                uint8_t* entry_end = (uint8_t*)tag + tag->size;

                while ((size_t)(entry_end - entry_ptr) >= mmap_tag->entry_size && mmap_count < VOS3_BOOT_MAX_MMAP) {
                    struct multiboot2_mmap_entry* entry = (struct multiboot2_mmap_entry*)entry_ptr;

                    g_mmap_entries[mmap_count].base = entry->addr;
                    g_mmap_entries[mmap_count].length = entry->len;
                    g_mmap_entries[mmap_count].type = convert_mmap_type(entry->type);
                    g_mmap_entries[mmap_count].attributes = 0;

                    if (entry->type == MULTIBOOT2_MMAP_TYPE_AVAILABLE) {
                        total_memory += entry->len;
                    }

                    mmap_count++;
                    entry_ptr += mmap_tag->entry_size;
                }
                break;
            }

            case MULTIBOOT2_TAG_TYPE_BASIC_MEMINFO: {
                struct multiboot2_tag_basic_meminfo* meminfo =
                    (struct multiboot2_tag_basic_meminfo*)tag;
                if (tag->size < sizeof(*meminfo)) break;
                /* mem_lower and mem_upper are in KiB */
                if (mmap_count == 0) {
                    /* Fallback if no memory map */
                    total_memory = ((uint64_t)meminfo->mem_upper * 1024ULL);
                }
                break;
            }
        }

        /* Move to next tag (8-byte aligned) */
        uint64_t step = ((uint64_t)tag->size + 7ULL) & ~7ULL;
        if (step > mb2_info_addr + total_size - (uintptr_t)ptr) break;
        ptr += step;
    }

    /* Set memory map info */
    g_boot_info.total_memory = total_memory;
    g_boot_info.mem_map_addr = (uint64_t)(uintptr_t)g_mmap_entries;
    g_boot_info.mem_map_entries = mmap_count;
    g_boot_info.mem_map_entry_size = sizeof(vos3_boot_mmap_entry_t);

    /* Single CPU for now */
    g_boot_info.cpu_count = 1;
    g_boot_info.bsp_lapic_id = 0;

    return &g_boot_info;
}
