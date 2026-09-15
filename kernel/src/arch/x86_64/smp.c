/**
 * @file smp.c
 * @brief VOS3 Symmetric Multiprocessing (SMP) Implementation
 *
 * @details Phase 23 - Application Processor initialization using LAPIC
 *          and INIT-SIPI-SIPI protocol. Wakes all APs and coordinates
 *          them with the BSP.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../../include/arch/x86_64/smp.h"
#include "../../../include/arch/x86_64/gdt.h"
#include "../../../include/arch/x86_64/cpu.h"
#include "../../../include/arch/x86_64/idt.h"
#include "../../../include/vos/percpu.h"
#include "../../../include/vos/console.h"
#include "../../../include/vos/timer.h"
#include "../../../include/vos/pmm.h"
#include "../../../include/vos/vmm.h"
#include "../../../include/vos/string.h"
#include "../../../include/vos/scheduler.h"
#include "../../../include/vos/syscall.h"
#include "../../../include/vos/acpi.h"

/* ============================================================================
 * GLOBALS
 * ============================================================================ */

/** @brief LAPIC base virtual address (memory-mapped) */
static volatile uint32_t* g_lapic_base = NULL;

/** @brief Physical address of LAPIC (typically 0xFEE00000) */
static uint64_t g_lapic_phys = 0xFEE00000ULL;

/** @brief Array of CPU information */
static vos3_smp_cpu_info_t g_smp_cpus[VOS3_SMP_MAX_CPUS];

/** @brief Total number of CPUs detected */
static uint32_t g_smp_cpu_count = 0;

/** @brief Number of online CPUs */
static volatile uint32_t g_smp_online_count = 0;

/* Per-CPU handshake remains valid after shared low boot parameters are reused. */
enum { AP_STARTING, AP_PREPARED, AP_RELEASED, AP_FAILED, AP_CANCELED };
static uint32_t g_ap_start_state[VOS3_SMP_MAX_CPUS];

static __attribute__((noreturn)) void park_ap(void)
{
    __asm__ volatile ("cli" ::: "memory");
    for (;;) __asm__ volatile ("hlt");
}


/** @brief BSP APIC ID */
static uint32_t g_bsp_apic_id = 0;

/** @brief SMP initialized flag */
static int g_smp_initialized = 0;

/** @brief Boot parameters for AP startup (in low memory) */
static vos3_smp_boot_params_t* g_boot_params = NULL;
/* Four private bootstrap tables, retained for AP startup. Runtime kernel and
 * process roots never acquire the low identity mapping. */
static uintptr_t g_ap_boot_root = 0;

/* ============================================================================
 * MSR ACCESS
 * ============================================================================ */

static inline uint64_t rdmsr(uint32_t msr)
{
    uint32_t low, high;
    __asm__ volatile ("rdmsr" : "=a"(low), "=d"(high) : "c"(msr));
    return ((uint64_t)high << 32) | (uint64_t)low;
}

static inline void wrmsr(uint32_t msr, uint64_t value)
{
    uint32_t low = (uint32_t)(value & 0xFFFFFFFFULL);
    uint32_t high = (uint32_t)(value >> 32);
    __asm__ volatile ("wrmsr" : : "c"(msr), "a"(low), "d"(high));
}

/* ============================================================================
 * CR3 ACCESS
 * ============================================================================ */

static inline uint64_t read_cr3(void)
{
    uint64_t cr3;
    __asm__ volatile ("mov %%cr3, %0" : "=r"(cr3));
    return cr3;
}

/* ============================================================================
 * DELAY FUNCTIONS
 * ============================================================================ */

/**
 * @brief Microsecond delay using busy-wait
 * @note Calibrated for ~1GHz QEMU virtual CPU (pause ~10 cycles)
 */
static void delay_us(uint32_t us)
{
    /*
     * In QEMU, the pause instruction takes approximately 10 cycles.
     * At 1 GHz, 1us = 1000 cycles = ~100 pause instructions.
     * We use a smaller multiplier (10) to account for loop overhead.
     */
    volatile uint32_t target = us * 10U;
    volatile uint32_t i;
    for (i = 0; i < target; i++) {
        __asm__ volatile ("pause");
    }
}

/**
 * @brief Millisecond delay
 */
static void delay_ms(uint32_t ms)
{
    for (uint32_t i = 0; i < ms; i++) {
        delay_us(1000);
    }
}

/* ============================================================================
 * LAPIC FUNCTIONS
 * ============================================================================ */

uint32_t vos3_lapic_read(uint32_t reg)
{
    if (g_lapic_base == NULL) {
        return 0;
    }
    return g_lapic_base[reg / 4];
}

void vos3_lapic_write(uint32_t reg, uint32_t value)
{
    if (g_lapic_base == NULL) {
        return;
    }
    g_lapic_base[reg / 4] = value;
    /* Read back to ensure write completes (memory barrier) */
    (void)g_lapic_base[reg / 4];
}

uint32_t vos3_lapic_id(void)
{
    return (vos3_lapic_read(VOS3_LAPIC_ID) >> 24) & 0xFFU;
}

void vos3_lapic_eoi(void)
{
    vos3_lapic_write(VOS3_LAPIC_EOI, 0);
}

void vos3_lapic_init(void)
{
    /* Get LAPIC base from MSR */
    uint64_t lapic_msr = rdmsr(VOS3_LAPIC_BASE_MSR);
    g_lapic_phys = lapic_msr & 0xFFFFF000ULL;

    /* Enable LAPIC via MSR if not already enabled */
    if ((lapic_msr & (1ULL << 11)) == 0) {
        lapic_msr |= (1ULL << 11);  /* Global enable */
        wrmsr(VOS3_LAPIC_BASE_MSR, lapic_msr);
    }

    /* Map LAPIC to virtual address if not already mapped */
    if (g_lapic_base == NULL) {
        /* Use direct physical mapping */
        g_lapic_base = (volatile uint32_t*)(0xFFFF800000000000ULL + g_lapic_phys);
    }

    /* Set Spurious Interrupt Vector Register */
    /* Enable APIC (bit 8) and set spurious vector to 0xFF */
    vos3_lapic_write(VOS3_LAPIC_SPURIOUS, 0x1FF);

    /* Set Task Priority to 0 (accept all interrupts) */
    vos3_lapic_write(VOS3_LAPIC_TPR, 0);

    /* Clear Error Status Register */
    vos3_lapic_write(VOS3_LAPIC_ESR, 0);
    vos3_lapic_write(VOS3_LAPIC_ESR, 0);

    /* Send EOI to clear any pending interrupts */
    vos3_lapic_eoi();
}

/**
 * @brief Wait for ICR to be idle
 */
static void lapic_wait_icr_idle(void)
{
    while ((vos3_lapic_read(VOS3_LAPIC_ICR_LOW) & VOS3_ICR_PENDING) != 0) {
        __asm__ volatile ("pause");
    }
}

void vos3_lapic_send_ipi(uint32_t apic_id, uint32_t vector)
{
    lapic_wait_icr_idle();

    /* Set destination APIC ID */
    vos3_lapic_write(VOS3_LAPIC_ICR_HIGH, apic_id << 24);

    /* Send IPI: Fixed delivery, physical destination, edge triggered */
    vos3_lapic_write(VOS3_LAPIC_ICR_LOW, vector | VOS3_ICR_FIXED | VOS3_ICR_PHYSICAL |
                     VOS3_ICR_ASSERT | VOS3_ICR_EDGE);

    lapic_wait_icr_idle();
}

int vos3_lapic_send_ipi_checked(uint32_t apic_id, uint32_t vector, uint32_t budget)
{
    if (apic_id > 255U || vector < 16U || vector > 255U || budget == 0)
        return -1;
    uint32_t remaining = budget;
    while ((vos3_lapic_read(VOS3_LAPIC_ICR_LOW) & VOS3_ICR_PENDING) != 0) {
        if (--remaining == 0) return -1;
        __asm__ volatile("pause" ::: "memory");
    }
    vos3_lapic_write(VOS3_LAPIC_ICR_HIGH, apic_id << 24);
    vos3_lapic_write(VOS3_LAPIC_ICR_LOW, vector | VOS3_ICR_FIXED | VOS3_ICR_PHYSICAL |
                     VOS3_ICR_ASSERT | VOS3_ICR_EDGE);
    remaining = budget;
    while ((vos3_lapic_read(VOS3_LAPIC_ICR_LOW) & VOS3_ICR_PENDING) != 0) {
        if (--remaining == 0) return -1;
        __asm__ volatile("pause" ::: "memory");
    }
    return 0;
}

void vos3_lapic_send_init(uint32_t apic_id)
{
    lapic_wait_icr_idle();

    /* Set destination APIC ID */
    vos3_lapic_write(VOS3_LAPIC_ICR_HIGH, apic_id << 24);

    /* Send INIT IPI: level triggered, assert */
    vos3_lapic_write(VOS3_LAPIC_ICR_LOW, VOS3_ICR_INIT | VOS3_ICR_PHYSICAL |
                     VOS3_ICR_LEVEL | VOS3_ICR_ASSERT);

    lapic_wait_icr_idle();

    /* Deassert INIT */
    vos3_lapic_write(VOS3_LAPIC_ICR_HIGH, apic_id << 24);
    vos3_lapic_write(VOS3_LAPIC_ICR_LOW, VOS3_ICR_INIT | VOS3_ICR_PHYSICAL |
                     VOS3_ICR_LEVEL | VOS3_ICR_DEASSERT);

    lapic_wait_icr_idle();
}

void vos3_lapic_send_sipi(uint32_t apic_id, uint8_t vector)
{
    lapic_wait_icr_idle();

    /* Set destination APIC ID */
    vos3_lapic_write(VOS3_LAPIC_ICR_HIGH, apic_id << 24);

    /* Send SIPI: startup vector in bits 0-7 (page number) */
    vos3_lapic_write(VOS3_LAPIC_ICR_LOW, VOS3_ICR_STARTUP | VOS3_ICR_PHYSICAL |
                     VOS3_ICR_EDGE | VOS3_ICR_ASSERT | (uint32_t)vector);

    lapic_wait_icr_idle();
}

/* ============================================================================
 * CPU DETECTION
 * ============================================================================ */

/** Enumerate firmware-advertised CPUs; never send IPIs to guessed IDs. */
static int detect_cpus(void)
{
    g_bsp_apic_id = vos3_lapic_id();
    VOS3_INFO("[SMP] BSP APIC ID: %u", g_bsp_apic_id);

    /* First, register the BSP */
    g_smp_cpus[0].apic_id = g_bsp_apic_id;
    g_smp_cpus[0].cpu_id = 0;
    g_smp_cpus[0].flags = VOS3_SMP_CPU_PRESENT | VOS3_SMP_CPU_BSP |
                          VOS3_SMP_CPU_ONLINE | VOS3_SMP_CPU_ENABLED;
    g_smp_cpus[0].started = 1;

    g_smp_cpu_count = 1;
    g_smp_online_count = 1;

    const vos3_acpi_info_t *acpi = vos3_acpi_get_info();
    if (acpi == NULL || acpi->cpu_count == 0) {
        VOS3_WARN("[SMP] No ACPI CPU topology; retaining BSP only");
        return 0;
    }
    for (uint32_t i = 0; i < acpi->cpu_count && i < VOS3_ACPI_MAX_CPUS; i++) {
        uint32_t apic_id = acpi->cpus[i].apic_id;
        if (!acpi->cpus[i].enabled) continue;
        int duplicate = 0;
        for (uint32_t j = 0; j < g_smp_cpu_count; j++) {
            if (g_smp_cpus[j].apic_id == apic_id) duplicate = 1;
        }
        if (duplicate) continue;
        if (g_smp_cpu_count >= VOS3_SMP_MAX_CPUS) break;
        uint32_t cpu_id = g_smp_cpu_count++;
        g_smp_cpus[cpu_id].apic_id = apic_id;
        g_smp_cpus[cpu_id].cpu_id = cpu_id;
        g_smp_cpus[cpu_id].flags = VOS3_SMP_CPU_PRESENT | VOS3_SMP_CPU_ENABLED;
        g_smp_cpus[cpu_id].started = 0;
    }
    VOS3_INFO("[SMP] ACPI enumerated %u CPUs", g_smp_cpu_count);

    return 0;
}

/* ============================================================================
 * AP STARTUP
 * ============================================================================ */

/**
 * @brief Copy trampoline code to low memory
 */
static int setup_trampoline(void)
{
    size_t trampoline_size = (size_t)(vos3_trampoline_end - vos3_trampoline_start);

    VOS3_DEBUG("[SMP] Trampoline size: %zu bytes", trampoline_size);

    if (trampoline_size > 0xF00) {  /* Must fit below boot params at 0x1F00 */
        VOS3_ERROR("[SMP] Trampoline too large!");
        return -1;
    }

    /* Get virtual address for low memory (physical 0x1000) */
    volatile uint8_t* trampoline_dest = (volatile uint8_t*)(0xFFFF800000000000ULL + VOS3_SMP_TRAMPOLINE_ADDR);

    /* Copy trampoline code */
    memcpy((void*)trampoline_dest, vos3_trampoline_start, trampoline_size);

    /* Set up boot parameters at 0x1F00 */
    g_boot_params = (vos3_smp_boot_params_t*)(0xFFFF800000000000ULL + VOS3_SMP_BOOT_PARAMS_ADDR);

    uintptr_t pages[4] = {0};
    for (size_t i = 0; i < 4; ++i) {
        pages[i] = vos3_pmm_alloc(VOS3_PMM_FLAG_ZERO | VOS3_PMM_FLAG_DMA32);
        if (pages[i] == 0 || pages[i] >= 0x100000000ULL) {
            for (size_t j = 0; j <= i; ++j) {
                if (pages[j] != 0) vos3_pmm_free(pages[j]);
            }
            VOS3_ERROR("[SMP] Cannot allocate 32-bit bootstrap page tables");
            return -1;
        }
    }
    uint64_t* root = (void*)vos3_phys_to_virt(pages[0]);
    uint64_t* pdpt = (void*)vos3_phys_to_virt(pages[1]);
    uint64_t* pd = (void*)vos3_phys_to_virt(pages[2]);
    uint64_t* pt = (void*)vos3_phys_to_virt(pages[3]);
    vos3_address_space_t* kernel = vos3_vmm_get_kernel_space();
    for (size_t i = 256; i < 512; ++i) root[i] = kernel->pml4[i];
    root[0] = pages[1] | VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE;
    pdpt[0] = pages[2] | VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE;
    pd[0] = pages[3] | VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE;
    /* One supervisor read/execute page contains code, GDT and read-only boot
     * arguments. AP switches to its high stack before calling into C. */
    pt[VOS3_SMP_TRAMPOLINE_ADDR / 4096] = VOS3_SMP_TRAMPOLINE_ADDR | VOS3_PTE_PRESENT;
    g_ap_boot_root = pages[0];

    VOS3_DEBUG("[SMP] Trampoline copied to 0x%llx", (unsigned long long)VOS3_SMP_TRAMPOLINE_ADDR);

    return 0;
}

/**
 * @brief Wake a single AP
 */
static int wake_ap(uint32_t cpu_id)
{
    if (cpu_id == 0 || cpu_id >= g_smp_cpu_count) {
        return -1;
    }

    vos3_smp_cpu_info_t* cpu = &g_smp_cpus[cpu_id];

    VOS3_INFO("[SMP] Waking CPU %u (APIC ID: %u)...", cpu_id, cpu->apic_id);

    /* Allocate stack for this AP (16 pages = 64 KB) */
    uint64_t stack_phys = vos3_pmm_alloc_pages(16, VOS3_PMM_FLAG_ZERO);
    if (stack_phys == 0) {
        VOS3_ERROR("[SMP] Failed to allocate stack for CPU %u", cpu_id);
        return -1;
    }

    /* Stack top (grows down) */
    uint64_t stack_top = 0xFFFF800000000000ULL + stack_phys + (16 * 4096) - 16;

    /* Set up boot parameters */
    g_boot_params->pml4 = g_ap_boot_root;
    g_boot_params->stack = stack_top;
    g_boot_params->entry = (uint64_t)(uintptr_t)vos3_ap_entry;
    g_boot_params->cpu_id = cpu_id;
    g_boot_params->apic_id = cpu->apic_id;
    g_boot_params->ready = 0;
    __atomic_store_n(&g_ap_start_state[cpu_id], AP_STARTING, __ATOMIC_RELEASE);

    /* Memory barrier to ensure parameters are visible */
    __asm__ volatile ("mfence" ::: "memory");

    /* Send INIT IPI */
    vos3_lapic_send_init(cpu->apic_id);

    /* Wait 10ms after INIT */
    delay_ms(10);

    /* Send first SIPI (vector = 0x01 for address 0x1000) */
    vos3_lapic_send_sipi(cpu->apic_id, 0x01);

    /* Wait 200us */
    delay_us(200);

    /* Check if AP started */
    if (__atomic_load_n(&g_ap_start_state[cpu_id], __ATOMIC_ACQUIRE) == AP_STARTING) {
        /* Send second SIPI */
        vos3_lapic_send_sipi(cpu->apic_id, 0x01);

        /* Wait for AP to start (up to 200ms) */
        for (uint32_t timeout = 0; timeout < VOS3_SMP_STARTUP_TIMEOUT_MS; timeout++) {
            if (__atomic_load_n(&g_ap_start_state[cpu_id], __ATOMIC_ACQUIRE) != AP_STARTING) {
                break;
            }
            delay_ms(1);
        }
    }

    uint32_t state = __atomic_load_n(&g_ap_start_state[cpu_id], __ATOMIC_ACQUIRE);
    if (state == AP_PREPARED) {
        cpu->flags |= VOS3_SMP_CPU_ONLINE;
        cpu->started = 1;
        g_smp_online_count++;
        __atomic_store_n(&g_ap_start_state[cpu_id], AP_RELEASED, __ATOMIC_RELEASE);
        VOS3_INFO("[SMP] CPU %u Online (APIC ID: %u)", cpu_id, cpu->apic_id);
        return 0;
    } else {
        VOS3_WARN("[SMP] CPU %u did not respond (APIC ID: %u)", cpu_id, cpu->apic_id);
        /* Firmware presence is independent of startup success. Cancel late
         * completion so a timed-out AP cannot enter the scheduler uncounted. */
        __atomic_exchange_n(&g_ap_start_state[cpu_id], AP_CANCELED, __ATOMIC_ACQ_REL);
        cpu->flags &= ~VOS3_SMP_CPU_ONLINE;
        return -1;
    }
}

/* ============================================================================
 * AP ENTRY POINT
 * ============================================================================ */

__attribute__((noreturn))
void vos3_ap_entry(uint32_t cpu_id, uint32_t apic_id)
{
    /* Execution and stack are now in the higher half. Drop the private low
     * bootstrap mapping before installing this CPU's runtime state. */
    vos3_write_cr3(vos3_vmm_get_kernel_space()->pml4_phys);
    /* Load segments before installing GS.Base: GDT reload clears GS.Base. */
    vos3_gdt_init_ap((uint16_t)cpu_id);

    /* Initialize this CPU's entry state and kernel GS after segment reload. */
    percpu_init_ap(cpu_id, apic_id);

    /* 3. v23.12 CRITICAL: Load shared IDT on this AP
     * Without this, any interrupt on the AP causes #GP or triple-fault
     * because IDTR still points to the real-mode IVT at 0x0000. */
    vos3_idt_load_ap();

    /* 4. v23.12 CRITICAL: Allocate per-CPU IST stacks and set RSP0
     * Must happen after GDT/TSS init (which zeroes the TSS) and
     * before signaling ready (BSP may wake next AP and overwrite boot_params). */
    {
        /* Save AP stack top before boot_params is reused by next AP wake */
        uint64_t ap_stack_top = g_boot_params->stack;

        /* RSP0: Ring 3 → Ring 0 transition stack */
        vos3_tss_set_rsp0((uint16_t)cpu_id, ap_stack_top);

        /* IST1-4: Dedicated stacks for critical exceptions
         * IST1=Double Fault, IST2=NMI, IST3=Machine Check, IST4=Debug
         * Each stack: 4 pages (16 KB) = VOS3_IST_STACK_SIZE */
        for (uint8_t ist = 1U; ist <= 4U; ist++) {
            uint64_t ist_phys;
#if defined(VOS3_TEST_AP_IST_FAILURE_CPU)
            /* Explicit fault-injection build only; absent in normal kernels. */
            if (cpu_id == VOS3_TEST_AP_IST_FAILURE_CPU && ist == 1U)
                ist_phys = 0;
            else
#endif
                ist_phys = vos3_pmm_alloc_pages(4, VOS3_PMM_FLAG_ZERO);
            if (ist_phys != 0) {
                uint64_t ist_top = 0xFFFF800000000000ULL + ist_phys + VOS3_IST_STACK_SIZE;
                vos3_tss_set_ist((uint16_t)cpu_id, ist, ist_top);
            } else {
                VOS3_ERROR("[SMP] CPU %u: IST%u alloc failed; CPU parked", cpu_id, ist);
                __atomic_store_n(&g_ap_start_state[cpu_id], AP_FAILED, __ATOMIC_RELEASE);
                park_ap();
            }
        }
    }

    /* 5. v23.12 HIGH: Harden CPU silicon (UMIP, SMEP, SMAP, WP)
     * Ensures AP has identical security posture to BSP. */
    vos3_cpu_harden_silicon();

    /* SYSCALL MSRs are not inherited from the BSP. Configure only this
     * CPU; the BSP owns initialization of the shared dispatch table. */
    vos3_syscall_init_cpu();

    /* 6. Initialize LAPIC for this AP */
    vos3_lapic_init();

    /*
     * 8. VOS3_MODE Identity Inheritance
     *
     * The system identity (Private/Enterprise/Managed) is automatically
     * inherited because:
     * - All CPUs share the same kernel address space
     * - g_config is a global variable accessible to all CPUs
     * - No per-CPU copy is needed
     *
     * All kernel functions like vos3_config_get_system_mode() will
     * return the same value on all CPUs.
     */

    /* 9. Initialize scheduler for this AP (creates idle task) */
    int result = vos3_sched_init_ap(cpu_id);
    if (result != 0) {
        VOS3_ERROR("[SMP] CPU %u scheduler init failed", cpu_id);
        __atomic_store_n(&g_ap_start_state[cpu_id], AP_FAILED, __ATOMIC_RELEASE);
        park_ap();
    }

    /* Publish readiness only after every prerequisite succeeds. A timeout
     * changes STARTING to CANCELED; never overwrite it with late readiness. */
    uint32_t expected = AP_STARTING;
    if (!__atomic_compare_exchange_n(&g_ap_start_state[cpu_id], &expected,
                                    AP_PREPARED, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
        park_ap();
    while (__atomic_load_n(&g_ap_start_state[cpu_id], __ATOMIC_ACQUIRE) == AP_PREPARED)
        __asm__ volatile ("pause");
    if (__atomic_load_n(&g_ap_start_state[cpu_id], __ATOMIC_ACQUIRE) != AP_RELEASED)
        park_ap();

    VOS3_INFO("[SMP] CPU %u entering scheduler after BSP release", cpu_id);
    vos3_sched_loop_ap();

    /* Never reached */
}

/* ============================================================================
 * SMP PUBLIC API
 * ============================================================================ */

int vos3_smp_init(void)
{
    int result;

    if (g_smp_initialized != 0) {
        return -1;  /* Already initialized */
    }

    VOS3_INFO("[SMP] Initializing SMP subsystem");

    /* 1. Initialize BSP's LAPIC */
    vos3_lapic_init();
    VOS3_INFO("[SMP] BSP LAPIC initialized");

    /* 2. Detect available CPUs */
    result = detect_cpus();
    if (result != 0) {
        VOS3_WARN("[SMP] CPU detection failed");
        return result;
    }

    /* If only BSP, we're done */
    if (g_smp_cpu_count <= 1) {
        VOS3_INFO("[SMP] Single CPU system, SMP not needed");
        g_smp_initialized = 1;
        return 0;
    }

    /* 3. Set up trampoline in low memory */
    result = setup_trampoline();
    if (result != 0) {
        VOS3_ERROR("[SMP] Trampoline setup failed");
        return result;
    }

    /* 4. Pre-create idle tasks for all APs (avoids heap contention) */
    result = vos3_sched_create_ap_idle_tasks(g_smp_cpu_count);
    if (result != 0) {
        VOS3_WARN("[SMP] Failed to pre-create idle tasks, APs will create on-demand");
    }

    /* 5. Wake each AP */
    /* Stop on failure: shared bootstrap parameters cannot safely be reused
     * while a timed-out AP may still be executing. */
    uint32_t woken = 0;
    for (uint32_t i = 1; i < g_smp_cpu_count; i++) {
        if (wake_ap(i) == 0) {
            woken++;
        } else {
            /* Late-AP isolation is required before continuing after a timeout. */
            VOS3_DEBUG("[SMP] Stopping startup after CPU %u timeout", i);
            break;
        }
    }

    /* Preserve firmware topology indices. Online count tracks usable CPUs;
     * compressing the table here aliases IDs after a failed startup. */

    g_smp_initialized = 1;

    VOS3_INFO("[SMP] Initialization complete: %u CPUs online", g_smp_online_count);

    return 0;
}

uint32_t vos3_smp_cpu_count(void)
{
    return g_smp_cpu_count;
}

uint32_t vos3_smp_online_count(void)
{
    return g_smp_online_count;
}

int vos3_smp_is_bsp(void)
{
    return (vos3_lapic_id() == g_bsp_apic_id) ? 1 : 0;
}

const vos3_smp_cpu_info_t* vos3_smp_get_cpu_info(uint32_t cpu_id)
{
    if (cpu_id >= g_smp_cpu_count) {
        return NULL;
    }
    return &g_smp_cpus[cpu_id];
}

/* ============================================================================
 * Phase 4.2: Short-Hand IPI Broadcast
 * ============================================================================ */

void vos3_lapic_send_ipi_shorthand(uint8_t vector, uint32_t shorthand)
{
    lapic_wait_icr_idle();

    /* Short-Hand modes don't need destination — write ICR low only.
     * Vector in bits [7:0], shorthand already has correct bit position. */
    uint32_t icr_low = (uint32_t)vector | shorthand |
                       VOS3_ICR_FIXED | VOS3_ICR_ASSERT | VOS3_ICR_EDGE;
    vos3_lapic_write(VOS3_LAPIC_ICR_LOW, icr_low);

    lapic_wait_icr_idle();
}
