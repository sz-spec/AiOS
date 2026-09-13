/**
 * @file limine_stub.c
 * @brief Limine bootloader protocol stub for VOS3
 *
 * @details Translates Limine boot information to VOS3 format
 *
 * @version 1.0.0
 * @date 2026-02-16
 */

#include "../../include/vos/boot_info.h"
#include "../../boot/limine.h"

/* ============================================================================
 * LIMINE REQUESTS
 * ============================================================================ */

__attribute__((used, section(".limine_requests_start")))
static volatile LIMINE_REQUESTS_START_MARKER;

__attribute__((used, section(".limine_requests")))
static volatile struct limine_memmap_request memmap_request = {
    .id = LIMINE_MEMMAP_REQUEST,
    .revision = 0
};

__attribute__((used, section(".limine_requests")))
static volatile struct limine_hhdm_request hhdm_request = {
    .id = LIMINE_HHDM_REQUEST,
    .revision = 0
};

__attribute__((used, section(".limine_requests")))
static volatile struct limine_kernel_address_request kernel_address_request = {
    .id = LIMINE_KERNEL_ADDRESS_REQUEST,
    .revision = 0
};

__attribute__((used, section(".limine_requests")))
static volatile struct limine_rsdp_request rsdp_request = {
    .id = LIMINE_RSDP_REQUEST,
    .revision = 0
};

__attribute__((used, section(".limine_requests")))
static volatile struct limine_smp_request smp_request = {
    .id = LIMINE_SMP_REQUEST,
    .revision = 0
};

__attribute__((used, section(".limine_requests")))
static volatile struct limine_framebuffer_request fb_request = {
    .id = LIMINE_FRAMEBUFFER_REQUEST,
    .revision = 0
};

__attribute__((used, section(".limine_requests_end")))
static volatile LIMINE_REQUESTS_END_MARKER;

/* ============================================================================
 * STATIC STORAGE
 * ============================================================================ */

/** @brief Static boot info structure */
static vos3_boot_info_t g_boot_info;

/** @brief Static memory map entries */
static vos3_boot_mmap_entry_t g_mmap_entries[VOS3_BOOT_MAX_MMAP];

/** @brief Static CPU info entries */
static vos3_boot_cpu_info_t g_cpu_info[VOS3_BOOT_MAX_CPUS];

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Convert Limine memory type to VOS3 type
 */
static uint32_t convert_mmap_type(uint64_t limine_type)
{
    switch (limine_type) {
        case LIMINE_MEMMAP_USABLE:
            return VOS3_MMAP_USABLE;
        case LIMINE_MEMMAP_RESERVED:
            return VOS3_MMAP_RESERVED;
        case LIMINE_MEMMAP_ACPI_RECLAIMABLE:
            return VOS3_MMAP_ACPI_RECLAIM;
        case LIMINE_MEMMAP_ACPI_NVS:
            return VOS3_MMAP_ACPI_NVS;
        case LIMINE_MEMMAP_BAD_MEMORY:
            return VOS3_MMAP_BAD;
        case LIMINE_MEMMAP_BOOTLOADER_RECLAIMABLE:
            return VOS3_MMAP_BOOTLOADER;
        case LIMINE_MEMMAP_KERNEL_AND_MODULES:
            return VOS3_MMAP_KERNEL;
        case LIMINE_MEMMAP_FRAMEBUFFER:
            return VOS3_MMAP_FRAMEBUFFER;
        default:
            return VOS3_MMAP_RESERVED;
    }
}

/* ============================================================================
 * LIMINE ENTRY POINT
 * ============================================================================ */

/* External kernel entry */
extern void kernel_main(const vos3_boot_info_t* boot_info);

/**
 * @brief Limine entry point
 * @note Called by Limine bootloader
 */
__attribute__((noreturn))
void _limine_start(void)
{
    /* Initialize boot info */
    g_boot_info.magic = VOS3_BOOT_MAGIC;
    g_boot_info.version = VOS3_BOOT_VERSION;
    g_boot_info.size = sizeof(vos3_boot_info_t);
    g_boot_info.flags = 0;

    /* Process memory map */
    if (memmap_request.response != 0) {
        struct limine_memmap_response* resp = memmap_request.response;
        uint64_t total = 0;
        uint32_t count = 0;

        for (uint64_t i = 0; i < resp->entry_count && count < VOS3_BOOT_MAX_MMAP; i++) {
            struct limine_memmap_entry* entry = resp->entries[i];

            g_mmap_entries[count].base = entry->base;
            g_mmap_entries[count].length = entry->length;
            g_mmap_entries[count].type = convert_mmap_type(entry->type);
            g_mmap_entries[count].attributes = 0;

            if (entry->type == LIMINE_MEMMAP_USABLE) {
                total += entry->length;
            }
            count++;
        }

        g_boot_info.total_memory = total;
        g_boot_info.mem_map_addr = (uint64_t)(uintptr_t)g_mmap_entries;
        g_boot_info.mem_map_entries = count;
        g_boot_info.mem_map_entry_size = sizeof(vos3_boot_mmap_entry_t);
    }

    /* Process HHDM (higher half direct map) */
    if (hhdm_request.response != 0) {
        g_boot_info.direct_map_offset = hhdm_request.response->offset;
    }

    /* Process kernel address */
    if (kernel_address_request.response != 0) {
        struct limine_kernel_address_response* resp = kernel_address_request.response;
        g_boot_info.kernel_phys_start = resp->physical_base;
        g_boot_info.kernel_virt_start = resp->virtual_base;
        /* Estimate end based on typical kernel size (we'll refine later) */
        g_boot_info.kernel_phys_end = resp->physical_base + 0x400000; /* 4MB estimate */
        g_boot_info.kernel_virt_end = resp->virtual_base + 0x400000;
    }

    /* Process RSDP */
    if (rsdp_request.response != 0) {
        g_boot_info.rsdp_addr = (uint64_t)(uintptr_t)rsdp_request.response->address;
        g_boot_info.flags |= VOS3_BOOT_FLAG_ACPI;
    }

    /* Process SMP */
    if (smp_request.response != 0) {
        struct limine_smp_response* resp = smp_request.response;
        g_boot_info.cpu_count = (uint32_t)resp->cpu_count;
        g_boot_info.bsp_lapic_id = resp->bsp_lapic_id;
        g_boot_info.cpu_info_addr = (uint64_t)(uintptr_t)g_cpu_info;
        g_boot_info.flags |= VOS3_BOOT_FLAG_SMP;

        for (uint64_t i = 0; i < resp->cpu_count && i < VOS3_BOOT_MAX_CPUS; i++) {
            struct limine_smp_info* cpu = resp->cpus[i];
            g_cpu_info[i].lapic_id = cpu->lapic_id;
            g_cpu_info[i].processor_id = cpu->processor_id;
            g_cpu_info[i].flags = VOS3_CPU_FLAG_ENABLED;
            if (cpu->lapic_id == resp->bsp_lapic_id) {
                g_cpu_info[i].flags |= VOS3_CPU_FLAG_BSP;
            }
        }
    }

    /* Process framebuffer */
    if (fb_request.response != 0 && fb_request.response->framebuffer_count > 0) {
        struct limine_framebuffer* fb = fb_request.response->framebuffers[0];
        g_boot_info.framebuffer.address = (uint64_t)(uintptr_t)fb->address;
        g_boot_info.framebuffer.width = (uint32_t)fb->width;
        g_boot_info.framebuffer.height = (uint32_t)fb->height;
        g_boot_info.framebuffer.pitch = (uint32_t)fb->pitch;
        g_boot_info.framebuffer.bpp = (uint32_t)fb->bpp;
        g_boot_info.framebuffer.red_mask_size = fb->red_mask_size;
        g_boot_info.framebuffer.red_mask_shift = fb->red_mask_shift;
        g_boot_info.framebuffer.green_mask_size = fb->green_mask_size;
        g_boot_info.framebuffer.green_mask_shift = fb->green_mask_shift;
        g_boot_info.framebuffer.blue_mask_size = fb->blue_mask_size;
        g_boot_info.framebuffer.blue_mask_shift = fb->blue_mask_shift;
        g_boot_info.flags |= VOS3_BOOT_FLAG_FRAMEBUFFER;
    }

    /* Call kernel main */
    kernel_main(&g_boot_info);

    /* Should never reach here */
    for (;;) {
        __asm__ volatile ("hlt");
    }
}
