/**
 * @file gdt.c
 * @brief VOS3 Global Descriptor Table Implementation
 *
 * @details Initializes and manages the GDT for x86_64 SMP systems.
 *          Each CPU has its own TSS entry for interrupt handling.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../../include/arch/x86_64/gdt.h"
#include "../../../include/vos/console.h"

/* ============================================================================
 * STATIC DATA
 * ============================================================================ */

/** @brief Global Descriptor Table (shared by all CPUs) */
static vos3_gdt_entry_t g_gdt[VOS3_GDT_ENTRY_COUNT]
    __attribute__((aligned(16)));

/** @brief Per-CPU TSS and GDT data */
static vos3_cpu_gdt_t g_cpu_gdt[VOS3_MAX_CPUS]
    __attribute__((aligned(VOS3_CACHE_LINE_SIZE)));

/** @brief GDT pointer for LGDT instruction */
static vos3_gdt_ptr_t g_gdt_ptr __attribute__((aligned(8)));

/** @brief GDT initialization flag */
static volatile uint32_t g_gdt_initialized = 0U;

/* ============================================================================
 * ASSEMBLY HELPERS
 * ============================================================================ */

/**
 * @brief Load GDT using LGDT instruction
 */
void vos3_gdt_load(const vos3_gdt_ptr_t* gdt_ptr)
{
    __asm__ volatile (
        "lgdt (%0)\n\t"
        /* Reload code segment using far return */
        "pushq %1\n\t"
        "leaq 1f(%%rip), %%rax\n\t"
        "pushq %%rax\n\t"
        "lretq\n\t"
        "1:\n\t"
        /* Reload data segments */
        "movw %2, %%ax\n\t"
        "movw %%ax, %%ds\n\t"
        "movw %%ax, %%es\n\t"
        "movw %%ax, %%fs\n\t"
        "movw %%ax, %%gs\n\t"
        "movw %%ax, %%ss\n\t"
        :
        : "r" (gdt_ptr),
          "i" ((uint64_t)VOS3_GDT_KERNEL_CODE),
          "i" (VOS3_GDT_KERNEL_DATA)
        : "rax", "memory"
    );
}

/**
 * @brief Load TSS using LTR instruction
 */
void vos3_tss_load(uint16_t selector)
{
    __asm__ volatile ("ltr %0" :: "r" (selector));
}

/* ============================================================================
 * INTERNAL FUNCTIONS
 * ============================================================================ */

/**
 * @brief Initialize a TSS with default values
 * @param[out] tss Pointer to TSS structure
 */
static void tss_init(vos3_tss_t* tss)
{
    /* Zero the TSS */
    uint8_t* ptr = (uint8_t*)tss;
    for (size_t i = 0U; i < sizeof(vos3_tss_t); i++) {
        ptr[i] = 0U;
    }

    /* Set I/O map base to beyond TSS (no I/O bitmap) */
    tss->iomap_base = sizeof(vos3_tss_t);
}

/**
 * @brief Install TSS descriptor in GDT
 * @param[in] cpu_id CPU ID
 * @param[in] tss_addr TSS address
 */
static void gdt_install_tss(uint16_t cpu_id, uint64_t tss_addr)
{
    size_t gdt_index = VOS3_GDT_STATIC_ENTRIES + ((size_t)cpu_id * 2U);

    /* Create TSS descriptor */
    vos3_tss_descriptor_t tss_desc = vos3_tss_create_descriptor(
        tss_addr,
        sizeof(vos3_tss_t) - 1U
    );

    /* Copy to GDT (TSS descriptor spans 2 entries) */
    uint64_t* gdt_ptr = (uint64_t*)&g_gdt[gdt_index];
    const uint64_t* tss_ptr = (const uint64_t*)&tss_desc;

    gdt_ptr[0] = tss_ptr[0];
    gdt_ptr[1] = tss_ptr[1];
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

void vos3_gdt_init(void)
{
    if (g_gdt_initialized != 0U) {
        return;
    }

    VOS3_INFO("GDT: Initializing Global Descriptor Table");

    /* Entry 0: Null descriptor */
    g_gdt[0] = (vos3_gdt_entry_t)VOS3_GDT_ENTRY_NULL;

    /* Entry 1: Kernel code segment (64-bit, Ring 0) */
    g_gdt[1] = (vos3_gdt_entry_t)VOS3_GDT_ENTRY_KERNEL_CODE;

    /* Entry 2: Kernel data segment (64-bit, Ring 0) */
    g_gdt[2] = (vos3_gdt_entry_t)VOS3_GDT_ENTRY_KERNEL_DATA;

    /* Entry 3: User code segment (64-bit, Ring 3) */
    g_gdt[3] = (vos3_gdt_entry_t)VOS3_GDT_ENTRY_USER_CODE;

    /* Entry 4: User data segment (64-bit, Ring 3) */
    g_gdt[4] = (vos3_gdt_entry_t)VOS3_GDT_ENTRY_USER_DATA;

    /* Initialize BSP's TSS (CPU 0) */
    tss_init(&g_cpu_gdt[0].tss);

    /* Install BSP TSS descriptor in GDT */
    uint64_t tss_addr = (uint64_t)(uintptr_t)&g_cpu_gdt[0].tss;
    gdt_install_tss(0U, tss_addr);

    /* Set up GDT pointer */
    g_gdt_ptr.limit = (uint16_t)(sizeof(g_gdt) - 1U);
    g_gdt_ptr.base = (uint64_t)(uintptr_t)&g_gdt[0];

    /* Load GDT */
    vos3_gdt_load(&g_gdt_ptr);

    /* Load TSS for BSP */
    vos3_tss_load(VOS3_GDT_TSS(0));

    g_gdt_initialized = 1U;

    VOS3_INFO("GDT: Loaded at 0x%016llx, %u bytes",
              (unsigned long long)g_gdt_ptr.base,
              (unsigned)(g_gdt_ptr.limit + 1U));
    VOS3_INFO("GDT: BSP TSS loaded at selector 0x%04x", VOS3_GDT_TSS(0));
}

void vos3_gdt_init_ap(uint16_t cpu_id)
{
    if (cpu_id == 0U || cpu_id >= VOS3_MAX_CPUS) {
        return;
    }

    VOS3_DEBUG("GDT: Initializing AP %u", cpu_id);

    /* Initialize this CPU's TSS */
    tss_init(&g_cpu_gdt[cpu_id].tss);

    /* Install TSS descriptor in GDT */
    uint64_t tss_addr = (uint64_t)(uintptr_t)&g_cpu_gdt[cpu_id].tss;
    gdt_install_tss(cpu_id, tss_addr);

    /* Load GDT (same GDT for all CPUs) */
    vos3_gdt_load(&g_gdt_ptr);

    /* Load this CPU's TSS */
    vos3_tss_load(VOS3_GDT_TSS(cpu_id));

    VOS3_DEBUG("GDT: AP %u TSS loaded at selector 0x%04x",
               cpu_id, VOS3_GDT_TSS(cpu_id));
}

void vos3_tss_set_rsp0(uint16_t cpu_id, uint64_t rsp0)
{
    if (cpu_id >= VOS3_MAX_CPUS) {
        return;
    }

    g_cpu_gdt[cpu_id].tss.rsp0 = rsp0;
}

void vos3_tss_set_ist(uint16_t cpu_id, uint8_t ist_index, uint64_t stack_ptr)
{
    if (cpu_id >= VOS3_MAX_CPUS || ist_index == 0U || ist_index > 7U) {
        return;
    }

    vos3_tss_t* tss = &g_cpu_gdt[cpu_id].tss;

    switch (ist_index) {
        case 1U:
            tss->ist1 = stack_ptr;
            break;
        case 2U:
            tss->ist2 = stack_ptr;
            break;
        case 3U:
            tss->ist3 = stack_ptr;
            break;
        case 4U:
            tss->ist4 = stack_ptr;
            break;
        case 5U:
            tss->ist5 = stack_ptr;
            break;
        case 6U:
            tss->ist6 = stack_ptr;
            break;
        case 7U:
            tss->ist7 = stack_ptr;
            break;
        default:
            /* Invalid index, do nothing */
            break;
    }
}
