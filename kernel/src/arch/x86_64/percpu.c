/**
 * @file percpu.c
 * @brief VOS3 Per-CPU Implementation (x86_64)
 *
 * @details Implements per-CPU data management for SMP support.
 *          Uses CPUID to determine the current CPU's APIC ID
 *          and maps it to a logical CPU index.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 * @note Phase 22 - Per-CPU Infrastructure Foundation
 */

#include "../../../include/vos/percpu.h"
#include "../../../include/vos/console.h"
#include "../../../include/vos/atomic.h"
#include "../../../include/vos/entry_state.h"
#include "../../../include/arch/x86_64/cpu.h"
#include "../../../include/arch/x86_64/idt.h"

/* ============================================================================
 * STATIC DATA
 * ============================================================================ */

/** @brief Per-CPU data array */
static vos3_cpu_t g_cpus[VOS3_MAX_CPUS] __attribute__((aligned(64)));
static vos3_entry_state_t g_entry_states[VOS3_MAX_CPUS];
static uint8_t g_entry_stacks[VOS3_MAX_CPUS][4096] __attribute__((aligned(4096)));

static void entry_state_init(uint32_t cpu_id)
{
    /* Called on the owning CPU before its first user-mode transition. */
    g_entry_states[cpu_id].kernel_rsp = 0;
    g_entry_states[cpu_id].user_rsp = 0;
    g_entry_states[cpu_id].kernel_cr3 = vos3_read_cr3() & ~0xFFFULL;
    g_entry_states[cpu_id].user_cr3 = 0;
    g_entry_states[cpu_id].trampoline_top = (uintptr_t)&g_entry_stacks[cpu_id][4096];
    vos3_write_msr(VOS3_MSR_GS_BASE, (uintptr_t)&g_entry_states[cpu_id]);
    vos3_write_msr(VOS3_MSR_KERNEL_GS_BASE, 0);
}

void vos3_entry_set_kernel_stack(uint64_t stack_top)
{
    uint32_t cpu_id = get_cpu_id();
    if (cpu_id >= VOS3_MAX_CPUS || stack_top == 0) {
        VOS3_PANIC("Invalid CPU syscall stack binding");
    }
    g_entry_states[cpu_id].kernel_rsp = stack_top;
}

void vos3_entry_bind_roots(uint64_t full, uint64_t restricted)
{
    if (full == 0 || (full & 0xFFF) || (restricted & 0xFFF)) {
        VOS3_PANIC("Invalid CPU page-table binding");
    }
    vos3_entry_state_t* state = &g_entry_states[get_cpu_id()];
    state->kernel_cr3 = full;
    state->user_cr3 = restricted;
}

uint64_t vos3_entry_trampoline_top(void)
{
    return g_entry_states[get_cpu_id()].trampoline_top;
}

uint64_t vos3_entry_get_kernel_cr3(void)
{
    return g_entry_states[get_cpu_id()].kernel_cr3;
}

uint64_t vos3_entry_get_user_cr3(void)
{
    return g_entry_states[get_cpu_id()].user_cr3;
}

vos3_int_frame_t* vos3_entry_move_irq_frame(vos3_int_frame_t* frame)
{
    if ((frame->cs & 3) != 3) return frame;
    uint64_t top = g_entry_states[get_cpu_id()].kernel_rsp;
    if (top == 0) VOS3_PANIC("Missing task stack for user interrupt");
    vos3_int_frame_t* target = (void*)(uintptr_t)((top & ~15ULL) - sizeof(*frame));
    *target = *frame;
    return target;
}
_Static_assert(offsetof(vos3_int_frame_t, cs) == 144, "interrupt CS ABI");

/** @brief Number of online CPUs */
static volatile uint32_t g_online_cpus = 0U;

/** @brief Initialization complete flag */
static volatile int g_percpu_initialized = 0;

/* ============================================================================
 * CPUID & LAPIC HELPERS
 * ============================================================================ */

/** @brief Flag indicating x2APIC support */
static volatile int g_x2apic_supported = 0;

/**
 * @brief Check if x2APIC is supported
 *
 * x2APIC allows for 32-bit APIC IDs, supporting up to 2^32 CPUs.
 * Required for systems with > 255 logical CPUs.
 */
static int cpuid_check_x2apic(void)
{
    uint32_t eax, ebx, ecx, edx;

    __asm__ volatile (
        "cpuid"
        : "=a"(eax), "=b"(ebx), "=c"(ecx), "=d"(edx)
        : "a"(1), "c"(0)
    );

    /* x2APIC support is indicated by ECX bit 21 */
    return (ecx & (1U << 21)) ? 1 : 0;
}

/**
 * @brief Read APIC ID using CPUID leaf 0x0B (x2APIC topology)
 *
 * This method supports up to 2^32 APIC IDs and is the preferred
 * method for enterprise systems with many cores.
 *
 * @return Hardware APIC ID (32-bit)
 */
static uint32_t cpuid_get_x2apic_id(void)
{
    uint32_t eax, ebx, ecx, edx;

    /* CPUID leaf 0x0B, subleaf 0 returns x2APIC ID in EDX */
    __asm__ volatile (
        "cpuid"
        : "=a"(eax), "=b"(ebx), "=c"(ecx), "=d"(edx)
        : "a"(0x0B), "c"(0)
    );

    return edx;
}

/**
 * @brief Read APIC ID using CPUID leaf 0x01 (legacy)
 *
 * Uses CPUID leaf 0x01 to get the initial APIC ID from EBX[31:24].
 * Limited to 8-bit APIC IDs (max 255 CPUs).
 *
 * @return Hardware APIC ID (0-255)
 */
static uint32_t cpuid_get_legacy_apic_id(void)
{
    uint32_t eax, ebx, ecx, edx;

    __asm__ volatile (
        "cpuid"
        : "=a"(eax), "=b"(ebx), "=c"(ecx), "=d"(edx)
        : "a"(1), "c"(0)
    );

    /* APIC ID is in bits 31:24 of EBX */
    return (ebx >> 24) & 0xFFU;
}

/**
 * @brief Read APIC ID (auto-select best method)
 *
 * Automatically uses x2APIC if available, otherwise falls back
 * to legacy CPUID leaf 0x01. Supports up to 256 cores for VOS3.
 *
 * @return Hardware APIC ID
 */
static inline uint32_t cpuid_get_apic_id(void)
{
    if (g_x2apic_supported) {
        return cpuid_get_x2apic_id();
    }
    return cpuid_get_legacy_apic_id();
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

/**
 * @brief Initialize the per-CPU subsystem (BSP)
 *
 * Detects x2APIC support for large core counts (up to 256 cores).
 * Initializes BSP data structure with multi-tenant billing fields.
 */
void percpu_init(void)
{
    uint32_t bsp_apic_id;
    uint32_t i;

    if (g_percpu_initialized) {
        return;
    }

    /* Detect x2APIC support for large core count systems */
    g_x2apic_supported = cpuid_check_x2apic();

    VOS3_INFO("[PERCPU] x2APIC support: %s (max %u CPUs)",
              g_x2apic_supported ? "yes" : "no",
              (unsigned)VOS3_MAX_CPUS);

    /* Clear all CPU structures */
    for (i = 0U; i < VOS3_MAX_CPUS; i++) {
        g_cpus[i].id = i;
        g_cpus[i].apic_id = 0xFFFFFFFFU;  /* Invalid APIC ID marker */
        g_cpus[i].current = NULL;
        g_cpus[i].idle = NULL;
        g_cpus[i].ticks = 0U;
        g_cpus[i].flags = 0U;
        g_cpus[i].irq_count = 0U;
        g_cpus[i].context_switches = 0U;
        /* Multi-tenant fields */
        g_cpus[i].tenant_id = 0U;
        g_cpus[i].numa_node = 0U;
        g_cpus[i].tenant_cycles = 0U;
        g_cpus[i].tenant_start = 0U;
        g_cpus[i].total_runtime = 0U;
    }

    /* Initialize BSP (CPU 0) */
    bsp_apic_id = cpuid_get_apic_id();

    g_cpus[0].id = 0U;
    g_cpus[0].apic_id = bsp_apic_id;
    g_cpus[0].flags = VOS3_PCPU_ONLINE | VOS3_PCPU_BSP;
    g_cpus[0].tenant_id = 0U;  /* Kernel/root tenant */

    g_online_cpus = 1U;
    g_percpu_initialized = 1;
    entry_state_init(0);

    VOS3_INFO("[PERCPU] Initialized CPU 0 (APIC ID: %u)", bsp_apic_id);
}

/**
 * @brief Initialize an Application Processor's per-CPU data
 */
int percpu_init_ap(uint32_t cpu_id, uint32_t apic_id)
{
    if (cpu_id == 0U || cpu_id >= VOS3_MAX_CPUS) {
        return -1;
    }

    if (!g_percpu_initialized) {
        return -2;
    }

    g_cpus[cpu_id].id = cpu_id;
    g_cpus[cpu_id].apic_id = apic_id;
    g_cpus[cpu_id].current = NULL;
    g_cpus[cpu_id].idle = NULL;
    g_cpus[cpu_id].ticks = 0U;
    g_cpus[cpu_id].flags = VOS3_PCPU_ONLINE;
    g_cpus[cpu_id].irq_count = 0U;
    g_cpus[cpu_id].context_switches = 0U;
    entry_state_init(cpu_id);

    /* Atomically increment online CPU count */
    __atomic_add_fetch(&g_online_cpus, 1U, __ATOMIC_RELEASE);

    VOS3_INFO("[PERCPU] Initialized CPU %u (APIC ID: %u)", cpu_id, apic_id);

    return 0;
}

/* ============================================================================
 * CPU ID FUNCTIONS
 * ============================================================================ */

/**
 * @brief Get the current CPU ID
 */
uint32_t get_cpu_id(void)
{
    uint32_t apic_id;
    uint32_t i;

    if (!g_percpu_initialized) {
        return 0U;  /* Fallback to BSP before init */
    }

    /* Get hardware APIC ID */
    apic_id = cpuid_get_apic_id();

    /* Search for matching CPU structure */
    for (i = 0U; i < VOS3_MAX_CPUS; i++) {
        if (g_cpus[i].apic_id == apic_id) {
            return g_cpus[i].id;
        }
    }

    /* Not found - return BSP as fallback */
    return 0U;
}

/**
 * @brief Get per-CPU data for the current CPU
 */
vos3_cpu_t* get_cpu(void)
{
    return &g_cpus[get_cpu_id()];
}

/**
 * @brief Get per-CPU data for a specific CPU
 */
vos3_cpu_t* get_cpu_by_id(uint32_t cpu_id)
{
    if (cpu_id >= VOS3_MAX_CPUS) {
        return NULL;
    }

    /* Only return if CPU is initialized (has valid APIC ID) */
    if (g_cpus[cpu_id].apic_id == 0xFFFFFFFFU) {
        return NULL;
    }

    return &g_cpus[cpu_id];
}

/* ============================================================================
 * STATUS FUNCTIONS
 * ============================================================================ */

/**
 * @brief Get the number of online CPUs
 */
uint32_t percpu_online_count(void)
{
    return g_online_cpus;
}

/**
 * @brief Check if a CPU is online
 */
int percpu_is_online(uint32_t cpu_id)
{
    if (cpu_id >= VOS3_MAX_CPUS) {
        return 0;
    }

    return (g_cpus[cpu_id].flags & VOS3_PCPU_ONLINE) ? 1 : 0;
}

/* ============================================================================
 * CURRENT TASK MANAGEMENT
 * ============================================================================ */

/**
 * @brief Set the current task for the calling CPU
 */
void percpu_set_current(struct vos3_task* task)
{
    vos3_cpu_t* cpu = get_cpu();
    cpu->current = task;
}

/**
 * @brief Get the current task for the calling CPU
 */
struct vos3_task* percpu_get_current(void)
{
    vos3_cpu_t* cpu = get_cpu();
    return cpu->current;
}

/* ============================================================================
 * STATISTICS
 * ============================================================================ */

/**
 * @brief Increment the IRQ count for the calling CPU
 */
void percpu_inc_irq_count(void)
{
    vos3_cpu_t* cpu = get_cpu();
    cpu->irq_count++;
}

/**
 * @brief Increment the context switch count for the calling CPU
 */
void percpu_inc_context_switches(void)
{
    vos3_cpu_t* cpu = get_cpu();
    cpu->context_switches++;
}
