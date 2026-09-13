/**
 * @file gdt.h
 * @brief VOS3 Global Descriptor Table (GDT) for x86_64 SMP
 *
 * @details Defines the GDT structure and segment descriptors for the
 *          VOS3 kernel supporting Symmetric Multi-Processing (SMP).
 *          Each CPU core has its own TSS entry for task state.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_ARCH_X86_64_GDT_H
#define VOS3_ARCH_X86_64_GDT_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include "memory_map.h"

/* ============================================================================
 * GDT SEGMENT SELECTORS
 * ============================================================================
 *
 * x86_64 Long Mode GDT Layout:
 * Index 0: Null descriptor (required)
 * Index 1: Kernel Code Segment (64-bit, Ring 0)
 * Index 2: Kernel Data Segment (64-bit, Ring 0)
 * Index 3: User Code Segment (64-bit, Ring 3)
 * Index 4: User Data Segment (64-bit, Ring 3)
 * Index 5+: TSS entries (one per CPU, 16 bytes each = 2 GDT entries)
 *
 * ============================================================================ */

/** @brief Null segment selector */
#define VOS3_GDT_NULL           ((uint16_t)0x0000U)

/** @brief Kernel code segment selector (Ring 0) */
#define VOS3_GDT_KERNEL_CODE    ((uint16_t)0x0008U)

/** @brief Kernel data segment selector (Ring 0) */
#define VOS3_GDT_KERNEL_DATA    ((uint16_t)0x0010U)

/** @brief User code segment selector (Ring 3) */
#define VOS3_GDT_USER_CODE      ((uint16_t)0x0018U | 0x0003U)

/** @brief User data segment selector (Ring 3) */
#define VOS3_GDT_USER_DATA      ((uint16_t)0x0020U | 0x0003U)

/** @brief First TSS selector offset */
#define VOS3_GDT_TSS_BASE       ((uint16_t)0x0028U)

/** @brief TSS selector for specific CPU */
#define VOS3_GDT_TSS(cpu_id)    ((uint16_t)(VOS3_GDT_TSS_BASE + ((cpu_id) * 16U)))

/** @brief Number of static GDT entries (before per-CPU TSS) */
#define VOS3_GDT_STATIC_ENTRIES ((size_t)5U)

/** @brief Total GDT entries (static + TSS per CPU) */
#define VOS3_GDT_ENTRY_COUNT    (VOS3_GDT_STATIC_ENTRIES + (VOS3_MAX_CPUS * 2U))

/* ============================================================================
 * GDT ACCESS BYTE FLAGS
 * ============================================================================ */

/** @brief Segment present */
#define VOS3_GDT_PRESENT        ((uint8_t)0x80U)

/** @brief Descriptor Privilege Level 0 (Ring 0) */
#define VOS3_GDT_DPL0           ((uint8_t)0x00U)

/** @brief Descriptor Privilege Level 3 (Ring 3) */
#define VOS3_GDT_DPL3           ((uint8_t)0x60U)

/** @brief Code/Data segment (not system) */
#define VOS3_GDT_SEGMENT        ((uint8_t)0x10U)

/** @brief Executable segment (code) */
#define VOS3_GDT_EXECUTABLE     ((uint8_t)0x08U)

/** @brief Direction/Conforming bit */
#define VOS3_GDT_DC             ((uint8_t)0x04U)

/** @brief Readable (code) / Writable (data) */
#define VOS3_GDT_RW             ((uint8_t)0x02U)

/** @brief Accessed bit */
#define VOS3_GDT_ACCESSED       ((uint8_t)0x01U)

/* ============================================================================
 * GDT FLAGS (Upper nibble of limit_flags byte)
 * ============================================================================ */

/** @brief Granularity: 4 KiB blocks */
#define VOS3_GDT_GRANULARITY    ((uint8_t)0x80U)

/** @brief 32-bit protected mode (not used in long mode) */
#define VOS3_GDT_SIZE32         ((uint8_t)0x40U)

/** @brief 64-bit long mode */
#define VOS3_GDT_LONG_MODE      ((uint8_t)0x20U)

/* ============================================================================
 * TSS TYPE FLAGS
 * ============================================================================ */

/** @brief TSS Available (64-bit) */
#define VOS3_TSS_TYPE_AVAIL     ((uint8_t)0x09U)

/** @brief TSS Busy (64-bit) */
#define VOS3_TSS_TYPE_BUSY      ((uint8_t)0x0BU)

/* ============================================================================
 * GDT STRUCTURES
 * ============================================================================ */

/**
 * @brief GDT entry (8 bytes)
 * @note Standard segment descriptor format
 */
typedef struct __attribute__((packed)) vos3_gdt_entry {
    uint16_t limit_low;     /**< Segment limit (bits 0-15) */
    uint16_t base_low;      /**< Base address (bits 0-15) */
    uint8_t  base_mid;      /**< Base address (bits 16-23) */
    uint8_t  access;        /**< Access byte */
    uint8_t  limit_flags;   /**< Limit (16-19) and flags */
    uint8_t  base_high;     /**< Base address (bits 24-31) */
} vos3_gdt_entry_t;

VOS3_STATIC_ASSERT(sizeof(vos3_gdt_entry_t) == 8U,
                   "GDT entry must be 8 bytes");

/**
 * @brief TSS descriptor (16 bytes for 64-bit mode)
 * @note In long mode, TSS descriptor spans two GDT entries
 */
typedef struct __attribute__((packed)) vos3_tss_descriptor {
    uint16_t limit_low;     /**< Segment limit (bits 0-15) */
    uint16_t base_low;      /**< Base address (bits 0-15) */
    uint8_t  base_mid;      /**< Base address (bits 16-23) */
    uint8_t  access;        /**< Access byte (type + DPL + present) */
    uint8_t  limit_flags;   /**< Limit (16-19) and flags */
    uint8_t  base_high;     /**< Base address (bits 24-31) */
    uint32_t base_upper;    /**< Base address (bits 32-63) */
    uint32_t reserved;      /**< Reserved, must be zero */
} vos3_tss_descriptor_t;

VOS3_STATIC_ASSERT(sizeof(vos3_tss_descriptor_t) == 16U,
                   "TSS descriptor must be 16 bytes");

/**
 * @brief Task State Segment (TSS) for 64-bit mode
 * @note Contains RSP values for privilege level changes
 */
typedef struct __attribute__((packed)) vos3_tss {
    uint32_t reserved0;         /**< Reserved */
    uint64_t rsp0;              /**< Stack pointer for Ring 0 */
    uint64_t rsp1;              /**< Stack pointer for Ring 1 */
    uint64_t rsp2;              /**< Stack pointer for Ring 2 */
    uint64_t reserved1;         /**< Reserved */
    uint64_t ist1;              /**< Interrupt Stack Table 1 */
    uint64_t ist2;              /**< Interrupt Stack Table 2 */
    uint64_t ist3;              /**< Interrupt Stack Table 3 */
    uint64_t ist4;              /**< Interrupt Stack Table 4 */
    uint64_t ist5;              /**< Interrupt Stack Table 5 */
    uint64_t ist6;              /**< Interrupt Stack Table 6 */
    uint64_t ist7;              /**< Interrupt Stack Table 7 */
    uint64_t reserved2;         /**< Reserved */
    uint16_t reserved3;         /**< Reserved */
    uint16_t iomap_base;        /**< I/O Map Base Address */
} vos3_tss_t;

VOS3_STATIC_ASSERT(sizeof(vos3_tss_t) == 104U,
                   "TSS must be 104 bytes");

/**
 * @brief GDT Pointer structure for LGDT instruction
 */
typedef struct __attribute__((packed)) vos3_gdt_ptr {
    uint16_t limit;     /**< GDT size - 1 */
    uint64_t base;      /**< GDT base address */
} vos3_gdt_ptr_t;

VOS3_STATIC_ASSERT(sizeof(vos3_gdt_ptr_t) == 10U,
                   "GDT pointer must be 10 bytes");

/* ============================================================================
 * PER-CPU GDT STRUCTURE
 * ============================================================================ */

/**
 * @brief Per-CPU GDT and TSS data
 * @note Each CPU has its own TSS for interrupt handling
 */
typedef struct __attribute__((aligned(VOS3_CACHE_LINE_SIZE))) vos3_cpu_gdt {
    vos3_tss_t      tss;            /**< Task State Segment */
    uint8_t         padding[24];    /**< Pad to cache line boundary */
} vos3_cpu_gdt_t;

VOS3_STATIC_ASSERT(sizeof(vos3_cpu_gdt_t) == 128U,
                   "Per-CPU GDT data should be 128 bytes");

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the Global Descriptor Table
 * @note Must be called by BSP during early boot
 */
void vos3_gdt_init(void);

/**
 * @brief Initialize GDT for an Application Processor (AP)
 * @param[in] cpu_id CPU core ID
 * @note Called by each AP during SMP initialization
 */
void vos3_gdt_init_ap(uint16_t cpu_id);

/**
 * @brief Load the GDT on the current CPU
 * @param[in] gdt_ptr Pointer to GDT pointer structure
 */
void vos3_gdt_load(const vos3_gdt_ptr_t* gdt_ptr);

/**
 * @brief Load the TSS for the current CPU
 * @param[in] selector TSS selector value
 */
void vos3_tss_load(uint16_t selector);

/**
 * @brief Set kernel stack pointer in TSS
 * @param[in] cpu_id CPU core ID
 * @param[in] rsp0 New kernel stack pointer
 */
void vos3_tss_set_rsp0(uint16_t cpu_id, uint64_t rsp0);

/**
 * @brief Set Interrupt Stack Table entry
 * @param[in] cpu_id CPU core ID
 * @param[in] ist_index IST index (1-7)
 * @param[in] stack_ptr Stack pointer for the IST entry
 */
void vos3_tss_set_ist(uint16_t cpu_id, uint8_t ist_index, uint64_t stack_ptr);

/**
 * @brief Create a GDT entry
 * @param[in] base Segment base address
 * @param[in] limit Segment limit
 * @param[in] access Access byte
 * @param[in] flags Flags (granularity, size, long mode)
 * @return Configured GDT entry
 */
static inline vos3_gdt_entry_t vos3_gdt_create_entry(
    uint32_t base,
    uint32_t limit,
    uint8_t access,
    uint8_t flags)
{
    vos3_gdt_entry_t entry;

    entry.limit_low   = (uint16_t)(limit & 0xFFFFU);
    entry.base_low    = (uint16_t)(base & 0xFFFFU);
    entry.base_mid    = (uint8_t)((base >> 16U) & 0xFFU);
    entry.access      = access;
    entry.limit_flags = (uint8_t)(((limit >> 16U) & 0x0FU) | (flags & 0xF0U));
    entry.base_high   = (uint8_t)((base >> 24U) & 0xFFU);

    return entry;
}

/**
 * @brief Create a TSS descriptor
 * @param[in] tss_addr TSS structure address
 * @param[in] size TSS size
 * @return Configured TSS descriptor
 */
static inline vos3_tss_descriptor_t vos3_tss_create_descriptor(
    uint64_t tss_addr,
    uint32_t size)
{
    vos3_tss_descriptor_t desc;

    desc.limit_low   = (uint16_t)(size & 0xFFFFU);
    desc.base_low    = (uint16_t)(tss_addr & 0xFFFFU);
    desc.base_mid    = (uint8_t)((tss_addr >> 16U) & 0xFFU);
    desc.access      = VOS3_GDT_PRESENT | VOS3_TSS_TYPE_AVAIL;
    desc.limit_flags = (uint8_t)((size >> 16U) & 0x0FU);
    desc.base_high   = (uint8_t)((tss_addr >> 24U) & 0xFFU);
    desc.base_upper  = (uint32_t)((tss_addr >> 32U) & 0xFFFFFFFFU);
    desc.reserved    = 0U;

    return desc;
}

/* ============================================================================
 * PREDEFINED GDT ENTRIES
 * ============================================================================ */

/** @brief Null descriptor (index 0) */
#define VOS3_GDT_ENTRY_NULL \
    { 0U, 0U, 0U, 0U, 0U, 0U }

/** @brief Kernel code segment (index 1) */
#define VOS3_GDT_ENTRY_KERNEL_CODE \
    { 0xFFFFU, 0U, 0U, \
      (VOS3_GDT_PRESENT | VOS3_GDT_DPL0 | VOS3_GDT_SEGMENT | \
       VOS3_GDT_EXECUTABLE | VOS3_GDT_RW), \
      (VOS3_GDT_GRANULARITY | VOS3_GDT_LONG_MODE | 0x0FU), 0U }

/** @brief Kernel data segment (index 2) */
#define VOS3_GDT_ENTRY_KERNEL_DATA \
    { 0xFFFFU, 0U, 0U, \
      (VOS3_GDT_PRESENT | VOS3_GDT_DPL0 | VOS3_GDT_SEGMENT | VOS3_GDT_RW), \
      (VOS3_GDT_GRANULARITY | VOS3_GDT_LONG_MODE | 0x0FU), 0U }

/** @brief User code segment (index 3) */
#define VOS3_GDT_ENTRY_USER_CODE \
    { 0xFFFFU, 0U, 0U, \
      (VOS3_GDT_PRESENT | VOS3_GDT_DPL3 | VOS3_GDT_SEGMENT | \
       VOS3_GDT_EXECUTABLE | VOS3_GDT_RW), \
      (VOS3_GDT_GRANULARITY | VOS3_GDT_LONG_MODE | 0x0FU), 0U }

/** @brief User data segment (index 4) */
#define VOS3_GDT_ENTRY_USER_DATA \
    { 0xFFFFU, 0U, 0U, \
      (VOS3_GDT_PRESENT | VOS3_GDT_DPL3 | VOS3_GDT_SEGMENT | VOS3_GDT_RW), \
      (VOS3_GDT_GRANULARITY | VOS3_GDT_LONG_MODE | 0x0FU), 0U }

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ARCH_X86_64_GDT_H */
