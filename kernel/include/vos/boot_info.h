/**
 * @file boot_info.h
 * @brief VOS3 Boot Information Structures
 *
 * @details Defines the structures passed from bootloader to kernel
 *          during the handover process.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_BOOT_INFO_H
#define VOS3_BOOT_INFO_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * BOOT CONSTANTS
 * ============================================================================ */

/** @brief Boot magic number ("VOS3" in ASCII) */
#define VOS3_BOOT_MAGIC             ((uint32_t)0x564F5333U)

/** @brief Boot info structure version (1.0.0) */
#define VOS3_BOOT_VERSION           ((uint32_t)0x00010000U)

/** @brief Maximum modules supported */
#define VOS3_BOOT_MAX_MODULES       ((size_t)32U)

/** @brief Maximum CPUs in boot info */
#define VOS3_BOOT_MAX_CPUS          ((size_t)256U)

/** @brief Maximum memory map entries */
#define VOS3_BOOT_MAX_MMAP          ((size_t)256U)

/* ============================================================================
 * BOOT FLAGS
 * ============================================================================ */

/** @brief Framebuffer is available */
#define VOS3_BOOT_FLAG_FRAMEBUFFER  ((uint32_t)(1U << 0))

/** @brief ACPI tables available */
#define VOS3_BOOT_FLAG_ACPI         ((uint32_t)(1U << 1))

/** @brief SMP information available */
#define VOS3_BOOT_FLAG_SMP          ((uint32_t)(1U << 2))

/** @brief Modules loaded */
#define VOS3_BOOT_FLAG_MODULES      ((uint32_t)(1U << 3))

/** @brief Command line available */
#define VOS3_BOOT_FLAG_CMDLINE      ((uint32_t)(1U << 4))

/** @brief UEFI boot */
#define VOS3_BOOT_FLAG_UEFI         ((uint32_t)(1U << 5))

/* ============================================================================
 * MEMORY MAP TYPES
 * ============================================================================ */

/** @brief Usable RAM */
#define VOS3_MMAP_USABLE            ((uint32_t)1U)

/** @brief Reserved/unusable memory */
#define VOS3_MMAP_RESERVED          ((uint32_t)2U)

/** @brief ACPI reclaimable memory */
#define VOS3_MMAP_ACPI_RECLAIM      ((uint32_t)3U)

/** @brief ACPI Non-Volatile Storage */
#define VOS3_MMAP_ACPI_NVS          ((uint32_t)4U)

/** @brief Bad memory */
#define VOS3_MMAP_BAD               ((uint32_t)5U)

/** @brief Bootloader reclaimable */
#define VOS3_MMAP_BOOTLOADER        ((uint32_t)6U)

/** @brief Kernel and modules */
#define VOS3_MMAP_KERNEL            ((uint32_t)7U)

/** @brief Framebuffer memory */
#define VOS3_MMAP_FRAMEBUFFER       ((uint32_t)8U)

/* ============================================================================
 * CPU FLAGS
 * ============================================================================ */

/** @brief This is the Bootstrap Processor */
#define VOS3_CPU_FLAG_BSP           ((uint32_t)(1U << 0))

/** @brief CPU is enabled in ACPI tables */
#define VOS3_CPU_FLAG_ENABLED       ((uint32_t)(1U << 1))

/** @brief CPU is currently online */
#define VOS3_CPU_FLAG_ONLINE        ((uint32_t)(1U << 2))

/* ============================================================================
 * STRUCTURES
 * ============================================================================ */

/**
 * @brief Memory map entry from bootloader
 */
typedef struct __attribute__((packed)) vos3_boot_mmap_entry {
    uint64_t base;          /**< Region base physical address */
    uint64_t length;        /**< Region length in bytes */
    uint32_t type;          /**< Region type (VOS3_MMAP_*) */
    uint32_t attributes;    /**< Additional attributes */
} vos3_boot_mmap_entry_t;

/**
 * @brief CPU information from bootloader
 */
typedef struct __attribute__((packed)) vos3_boot_cpu_info {
    uint32_t lapic_id;      /**< Local APIC ID */
    uint32_t processor_id;  /**< ACPI Processor ID */
    uint32_t flags;         /**< CPU flags (VOS3_CPU_FLAG_*) */
    uint32_t reserved;      /**< Reserved for alignment */
} vos3_boot_cpu_info_t;

/**
 * @brief Loaded module information
 */
typedef struct __attribute__((packed)) vos3_boot_module {
    uint64_t phys_start;    /**< Module physical start address */
    uint64_t phys_end;      /**< Module physical end address */
    uint64_t cmdline_addr;  /**< Module command line (physical) */
    uint64_t reserved;      /**< Reserved */
} vos3_boot_module_t;

/**
 * @brief Framebuffer information
 */
typedef struct __attribute__((packed)) vos3_boot_framebuffer {
    uint64_t address;       /**< Framebuffer physical address */
    uint32_t width;         /**< Width in pixels */
    uint32_t height;        /**< Height in pixels */
    uint32_t pitch;         /**< Bytes per scanline */
    uint32_t bpp;           /**< Bits per pixel */
    uint8_t  red_mask_size;     /**< Red channel mask size */
    uint8_t  red_mask_shift;    /**< Red channel shift */
    uint8_t  green_mask_size;   /**< Green channel mask size */
    uint8_t  green_mask_shift;  /**< Green channel shift */
    uint8_t  blue_mask_size;    /**< Blue channel mask size */
    uint8_t  blue_mask_shift;   /**< Blue channel shift */
    uint8_t  reserved[2];       /**< Reserved */
} vos3_boot_framebuffer_t;

/**
 * @brief Main boot information structure
 * @note Passed from bootloader to kernel entry point
 */
typedef struct __attribute__((packed)) vos3_boot_info {
    /* ===== Header (16 bytes) ===== */
    uint32_t magic;             /**< Magic: VOS3_BOOT_MAGIC */
    uint32_t version;           /**< Structure version */
    uint32_t size;              /**< Total structure size */
    uint32_t flags;             /**< Feature flags */

    /* ===== Memory Information (32 bytes) ===== */
    uint64_t total_memory;      /**< Total usable RAM in bytes */
    uint64_t mem_map_addr;      /**< Physical address of memory map array */
    uint32_t mem_map_entries;   /**< Number of memory map entries */
    uint32_t mem_map_entry_size;/**< Size of each memory map entry */
    uint64_t reserved_mem;      /**< Reserved */

    /* ===== Kernel Location (32 bytes) ===== */
    uint64_t kernel_phys_start; /**< Kernel physical start address */
    uint64_t kernel_phys_end;   /**< Kernel physical end address */
    uint64_t kernel_virt_start; /**< Kernel virtual start address */
    uint64_t kernel_virt_end;   /**< Kernel virtual end address */

    /* ===== Page Tables (16 bytes) ===== */
    uint64_t pml4_phys;         /**< PML4 physical address */
    uint64_t direct_map_offset; /**< Higher-half direct map offset */

    /* ===== Framebuffer (40 bytes) ===== */
    vos3_boot_framebuffer_t framebuffer;

    /* ===== ACPI (16 bytes) ===== */
    uint64_t rsdp_addr;         /**< RSDP physical address */
    uint64_t xsdt_addr;         /**< XSDT physical address (if available) */

    /* ===== SMP Information (24 bytes) ===== */
    uint32_t cpu_count;         /**< Number of CPUs detected */
    uint32_t bsp_lapic_id;      /**< BSP Local APIC ID */
    uint64_t cpu_info_addr;     /**< Physical address of CPU info array */
    uint64_t lapic_addr;        /**< Local APIC base address */

    /* ===== Command Line (16 bytes) ===== */
    uint64_t cmdline_addr;      /**< Command line string physical address */
    uint32_t cmdline_size;      /**< Command line length (excluding null) */
    uint32_t reserved_cmdline;  /**< Reserved */

    /* ===== Modules (16 bytes) ===== */
    uint32_t module_count;      /**< Number of loaded modules */
    uint32_t reserved_modules;  /**< Reserved */
    uint64_t modules_addr;      /**< Physical address of module array */

    /* ===== Timestamps (16 bytes) ===== */
    uint64_t boot_timestamp;    /**< TSC value at boot */
    uint64_t tsc_frequency;     /**< TSC frequency in Hz (if known) */

    /* ===== Reserved (64 bytes) ===== */
    uint8_t  reserved[64];      /**< Reserved for future use */

} vos3_boot_info_t;

/* ============================================================================
 * VALIDATION
 * ============================================================================ */

/**
 * @brief Validate boot information structure
 * @param[in] info Pointer to boot info
 * @return 0 if valid, error code otherwise
 */
static inline int vos3_boot_info_validate(const vos3_boot_info_t* info)
{
    if (info == NULL) {
        return -1;
    }
    if (info->magic != VOS3_BOOT_MAGIC) {
        return -2;
    }
    if (info->version < VOS3_BOOT_VERSION) {
        return -3;
    }
    if (info->mem_map_entries == 0U) {
        return -4;
    }
    return 0;
}

/**
 * @brief Check if boot flag is set
 * @param[in] info Pointer to boot info
 * @param[in] flag Flag to check
 * @return 1 if set, 0 otherwise
 */
static inline int vos3_boot_has_flag(const vos3_boot_info_t* info, uint32_t flag)
{
    return ((info->flags & flag) != 0U) ? 1 : 0;
}

/**
 * @brief Get memory map entry by index
 * @param[in] info Pointer to boot info
 * @param[in] index Entry index
 * @return Pointer to entry, or NULL if invalid
 */
static inline const vos3_boot_mmap_entry_t* vos3_boot_get_mmap_entry(
    const vos3_boot_info_t* info,
    size_t index)
{
    if (index >= info->mem_map_entries) {
        return NULL;
    }
    const uint8_t* base = (const uint8_t*)(uintptr_t)info->mem_map_addr;
    return (const vos3_boot_mmap_entry_t*)(base + (index * info->mem_map_entry_size));
}

/**
 * @brief Get CPU info by index
 * @param[in] info Pointer to boot info
 * @param[in] index CPU index
 * @return Pointer to CPU info, or NULL if invalid
 */
static inline const vos3_boot_cpu_info_t* vos3_boot_get_cpu_info(
    const vos3_boot_info_t* info,
    size_t index)
{
    if (index >= info->cpu_count) {
        return NULL;
    }
    const vos3_boot_cpu_info_t* cpus =
        (const vos3_boot_cpu_info_t*)(uintptr_t)info->cpu_info_addr;
    return &cpus[index];
}

/**
 * @brief Get module by index
 * @param[in] info Pointer to boot info
 * @param[in] index Module index
 * @return Pointer to module info, or NULL if invalid
 */
static inline const vos3_boot_module_t* vos3_boot_get_module(
    const vos3_boot_info_t* info,
    size_t index)
{
    if (index >= info->module_count) {
        return NULL;
    }
    const vos3_boot_module_t* modules =
        (const vos3_boot_module_t*)(uintptr_t)info->modules_addr;
    return &modules[index];
}

#ifdef __cplusplus
}
#endif

#endif /* VOS3_BOOT_INFO_H */
