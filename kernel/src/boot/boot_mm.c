/**
 * @file boot_mm.c
 * @brief VOS3 Boot — Memory Management Initialization
 *
 * Extracted from kmain.c (Phase 8.5-C Sovereign Consolidation).
 * PMM, UEFI memory map, HugePage pool, VMM, and Heap.
 */

#include "../../include/vos/boot_mm.h"
#include "../../include/vos/console.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * EXTERNAL SYMBOLS (linker script)
 * ============================================================================ */

extern char _kernel_virt_start[];
extern char _kernel_virt_end[];
extern char _kernel_phys_start[];
extern char _kernel_phys_end[];
extern char _text_start[];
extern char _text_end[];
extern char _rodata_start[];
extern char _rodata_end[];
extern char _data_start[];
extern char _data_end[];
extern char _bss_start[];
extern char _bss_end[];

/* UEFI memory map parsing */
extern int vos3_uefi_parse_memory_map(const void *);
extern const char *vos3_uefi_status_str(void);
typedef struct vos3_uefi_boot_info vos3_uefi_boot_info_t;
extern vos3_uefi_boot_info_t g_uefi_boot_info;

/* ============================================================================
 * MEMORY INFO HELPERS (moved from kmain.c)
 * ============================================================================ */

static void print_memory_info(const vos3_boot_info_t *boot_info)
{
    VOS3_INFO("Memory Information:");

    uint64_t total_mb = boot_info->total_memory / (1024ULL * 1024ULL);
    vos3_console_printf("  Total RAM: %llu MiB (%llu bytes)\n",
                        (unsigned long long)total_mb,
                        (unsigned long long)boot_info->total_memory);

    VOS3_INFO("Kernel Layout:");
    vos3_console_printf("  Virtual:  0x%016llx - 0x%016llx\n",
                        (unsigned long long)(uintptr_t)_kernel_virt_start,
                        (unsigned long long)(uintptr_t)_kernel_virt_end);
    vos3_console_printf("  Physical: 0x%016llx - 0x%016llx\n",
                        (unsigned long long)(uintptr_t)_kernel_phys_start,
                        (unsigned long long)(uintptr_t)_kernel_phys_end);

    size_t kernel_size = (size_t)(_kernel_virt_end - _kernel_virt_start);
    vos3_console_printf("  Size: %u KiB\n", (unsigned)(kernel_size / 1024U));

    VOS3_INFO("Sections:");
    vos3_console_printf("  .text:   0x%llx - 0x%llx (%u bytes)\n",
                        (unsigned long long)(uintptr_t)_text_start,
                        (unsigned long long)(uintptr_t)_text_end,
                        (unsigned)(_text_end - _text_start));
    vos3_console_printf("  .rodata: 0x%llx - 0x%llx (%u bytes)\n",
                        (unsigned long long)(uintptr_t)_rodata_start,
                        (unsigned long long)(uintptr_t)_rodata_end,
                        (unsigned)(_rodata_end - _rodata_start));
    vos3_console_printf("  .data:   0x%llx - 0x%llx (%u bytes)\n",
                        (unsigned long long)(uintptr_t)_data_start,
                        (unsigned long long)(uintptr_t)_data_end,
                        (unsigned)(_data_end - _data_start));
    vos3_console_printf("  .bss:    0x%llx - 0x%llx (%u bytes)\n",
                        (unsigned long long)(uintptr_t)_bss_start,
                        (unsigned long long)(uintptr_t)_bss_end,
                        (unsigned)(_bss_end - _bss_start));
}

static const char *mmap_type_name(uint32_t type)
{
    switch (type) {
        case VOS3_MMAP_USABLE:       return "Usable";
        case VOS3_MMAP_RESERVED:     return "Reserved";
        case VOS3_MMAP_ACPI_RECLAIM: return "ACPI Reclaim";
        case VOS3_MMAP_ACPI_NVS:     return "ACPI NVS";
        case VOS3_MMAP_BAD:          return "Bad Memory";
        case VOS3_MMAP_BOOTLOADER:   return "Bootloader";
        case VOS3_MMAP_KERNEL:       return "Kernel";
        case VOS3_MMAP_FRAMEBUFFER:  return "Framebuffer";
        default:                     return "Unknown";
    }
}

static void print_memory_map(const vos3_boot_info_t *boot_info)
{
    VOS3_INFO("Memory Map (%u entries):", boot_info->mem_map_entries);

    for (uint32_t i = 0U; i < boot_info->mem_map_entries; i++) {
        const vos3_boot_mmap_entry_t *entry = vos3_boot_get_mmap_entry(boot_info, i);
        if (entry == NULL) {
            continue;
        }

        uint64_t end = entry->base + entry->length;
        uint64_t size_kb = entry->length / 1024ULL;

        vos3_console_printf("  [%2u] 0x%012llx - 0x%012llx  %8llu KiB  %s\n",
                            i,
                            (unsigned long long)entry->base,
                            (unsigned long long)end,
                            (unsigned long long)size_kb,
                            mmap_type_name(entry->type));
    }
}

/* ============================================================================
 * BOOT MM INIT
 * ============================================================================ */

int boot_mm_init(const vos3_boot_info_t *boot_info)
{
    int result;

    /* ===== Phase 7: PMM ===== */
    VOS3_INFO("Initializing Physical Memory Manager");
    result = vos3_pmm_init(boot_info);
    if (result != 0) {
        VOS3_PANIC("PMM initialization failed (error %d)", result);
    }

    /* Print memory info */
    print_memory_info(boot_info);
    print_memory_map(boot_info);

    /* Print PMM stats */
    vos3_pmm_stats_t pmm_stats;
    vos3_pmm_get_stats(&pmm_stats);
    VOS3_INFO("PMM Statistics:");
    vos3_console_printf("  Total: %llu MiB, Free: %llu MiB, Used: %llu MiB\n",
                        (unsigned long long)(pmm_stats.total_memory / (1024ULL * 1024ULL)),
                        (unsigned long long)(pmm_stats.free_memory / (1024ULL * 1024ULL)),
                        (unsigned long long)(pmm_stats.used_memory / (1024ULL * 1024ULL)));

    /* ===== Phase 9: UEFI Memory Map (if available) ===== */
    vos3_uefi_parse_memory_map(&g_uefi_boot_info);
    VOS3_INFO("Boot mode: %s", vos3_uefi_status_str());

    /* ===== Phase 7b: HugePage Pool ===== */
    vos3_pmm_reserve_hugepages(1024); /* Up to 1024 * 2MB; 50% safety cap clamps to available */

    /* ===== Phase 8: VMM ===== */
    VOS3_INFO("Initializing Virtual Memory Manager");
    result = vos3_vmm_init(boot_info);
    if (result != 0) {
        VOS3_PANIC("VMM initialization failed (error %d)", result);
    }

    /* ===== Phase 9: Heap ===== */
    VOS3_INFO("Initializing Kernel Heap");
    result = vos3_heap_init();
    if (result != 0) {
        VOS3_PANIC("Heap initialization failed (error %d)", result);
    }

    /* Test heap allocation */
    void *test_ptr = vos3_kmalloc(128);
    if (test_ptr != NULL) {
        VOS3_INFO("Heap test: allocated 128 bytes at %p", test_ptr);
        vos3_kfree(test_ptr);
        VOS3_INFO("Heap test: freed successfully");
    }

    return 0;
}
