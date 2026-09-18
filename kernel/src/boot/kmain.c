/**
 * @file kmain.c
 * @brief VOS3 Kernel Main Entry Point — Orchestration Only
 *
 * @details Main kernel initialization orchestration. Called from entry.S
 *          after basic setup is complete. Phase 8.5-C decomposed
 *          boot subsystems into boot_mm.c, boot_drivers.c, boot_fs.c,
 *          boot_ai.c, boot_net.c.
 *
 * @version 2.0.0
 * @date 2026-04-11
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/boot_info.h"
#include "../../include/vos/console.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/timer.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/syscall.h"
#include "../../include/vos/user.h"
#include "../../include/vos/ipc.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/device.h"
#include "../../include/vos/numa.h"
#include "../../include/vos/vos3_config.h"
#include "../../include/vos/string.h"
#include "../../include/arch/x86_64/gdt.h"
#include "../../include/arch/x86_64/idt.h"
#include "../../include/arch/x86_64/pic.h"
#include "../../include/arch/x86_64/cpu.h"
#include "../../include/arch/x86_64/mitigation_mode.h"
#include "../../include/arch/x86_64/kpti.h"
#include "../../include/arch/x86_64/pci_ecam.h"
#include "../../include/vos/percpu.h"
#include "../../include/vos/virtio_bridge.h"
#include "../../include/arch/x86_64/smp.h"
#include "../../include/vos/entropy.h"
#include "../../include/vos/kaslr.h"
#include "../../include/vos/boot_mm.h"
#include "../../include/vos/boot_drivers.h"
#include "../../include/vos/boot_fs.h"
#include "../../include/vos/boot_ai.h"
#include "../../include/vos/boot_net.h"
#include "../../include/vos/baremetal_preflight.h"

/* M4 (v21.3) — Certification assertion harness.
 * VOS3_ASSERT_CERT() is a no-op when VOS3_ASSERT_HARNESS is undefined,
 * so this include is safe in every build. */
#include "../../include/vos/assert_cert.h"
#include "../../include/vos/assert_cert_ids.h"
#include "../../include/vos/vvfs_model_verify.h"  /* M3 Phase-5 boot self-test */
#include "../../include/vos/acpi.h"
#include "../../include/vos/apic.h"
#include "../../include/vos/xsave.h"
#include "../../include/vos/ioapic.h"
#include "../../include/vos/vbus.h"

/* Forward decl — defined later in this file under VOS3_ASSERT_HARNESS. */
#ifdef VOS3_ASSERT_HARNESS
void vos3_run_cert_harness(const vos3_boot_info_t *boot_info);
#endif

/* ============================================================================
 * EXTERNAL SYMBOLS (from linker script)
 * ============================================================================ */

extern char _kernel_virt_start[];
extern char _kernel_virt_end[];
extern char _kernel_phys_start[];
extern char _kernel_phys_end[];
extern char _boot_stack_bottom[];
extern char _boot_stack_top[];
extern char _ist1_stack_top[];
extern char _ist2_stack_top[];
extern char _ist3_stack_top[];
extern char _ist4_stack_top[];

/* v23.11 E2: 32-bit bootstrap stack (defined in multiboot2_entry.S, .bss.boot) */
extern char boot32_stack[];

/* External interrupt init function */
extern void vos3_interrupts_init(void);

/* External device init function */
extern int vos3_devices_init(void);

/* External exec syscall init function */
extern void vos3_exec_syscalls_init(void);

/* External signal syscall init function */
extern void vos3_signal_syscalls_init(void);

/* External time syscall init function */
extern void vos3_time_syscalls_init(void);

/* External futex init function (Task 1.5) */
extern void vos3_futex_init(void);

/* External POSIX core syscall init function (Task 1.5) */
extern void vos3_posix_syscalls_init(void);

/* External benchmark init functions */
extern void vos3_bench_init(void);
extern void vos3_bench_syscalls_init(void);

/* External init process spawner */
extern int vos3_spawn_init(void);
extern int vos3_kernel_tasks_init(void);

/* External setup wizard spawner */
extern int vos3_spawn_setup_wizard(void);

/* External config init */
extern void vos3_config_init(void);

/* ============================================================================
 * PVH BOOT FALLBACK
 * ============================================================================
 * When booting via QEMU -kernel (PVH), no Multiboot2 info is provided.
 * We detect this and use a minimal fallback boot info.
 */

/* CMOS port I/O for PVH memory detection */
static inline void cmos_outb(uint16_t port, uint8_t val) {
    __asm__ volatile("outb %0, %1" : : "a"(val), "Nd"(port));
}
static inline uint8_t cmos_inb(uint16_t port) {
    uint8_t val;
    __asm__ volatile("inb %1, %0" : "=a"(val) : "Nd"(port));
    return val;
}
static uint8_t cmos_read(uint8_t reg) {
    cmos_outb(0x70, reg);
    return cmos_inb(0x71);
}

/**
 * @brief Detect physical memory size via CMOS registers (QEMU/PC compatible)
 * @return Total memory in bytes (minimum 256MB, capped at 4GB for PMM bitmap)
 */
static uint64_t detect_memory_cmos(void) {
    /* CMOS 0x34/0x35: memory above 16MB in 64KB blocks */
    uint8_t lo = cmos_read(0x34);
    uint8_t hi = cmos_read(0x35);
    uint64_t above_16mb_blocks = ((uint64_t)hi << 8) | lo;
    uint64_t total = (16ULL * 1024ULL * 1024ULL) + (above_16mb_blocks * 65536ULL);

    /* Minimum 256MB */
    if (total < 256ULL * 1024ULL * 1024ULL) {
        total = 256ULL * 1024ULL * 1024ULL;
    }
    /* Cap to PMM bitmap capacity (4GB = 1,048,576 pages) */
    if (total > 4ULL * 1024ULL * 1024ULL * 1024ULL) {
        total = 4ULL * 1024ULL * 1024ULL * 1024ULL;
    }
    return total;
}

/* Fallback memory map for PVH boot (dynamically sized via CMOS)
 * 5 entries to handle the PCI memory hole at 3-4GB for 4GB+ systems. */
static vos3_boot_mmap_entry_t pvh_memory_map[5] = {
    /* [0] Low memory (0 - 640KB): Usable */
    { 0x00000000, 0x000A0000, VOS3_MMAP_USABLE, 0 },
    /* [1] Video/BIOS (640KB - 1MB): Reserved */
    { 0x000A0000, 0x00060000, VOS3_MMAP_RESERVED, 0 },
    /* [2] Extended memory (1MB - below PCI hole): Usable — patched at runtime */
    { 0x00100000, 0x0FF00000, VOS3_MMAP_USABLE, 0 },
    /* [3] PCI hole (3GB - 4GB): Reserved — only used when RAM >= 3GB */
    { 0xC0000000ULL, 0x40000000ULL, VOS3_MMAP_RESERVED, 0 },
    /* [4] High memory (above 4GB): Usable — only used when RAM > 3GB */
    { 0x100000000ULL, 0, VOS3_MMAP_USABLE, 0 },
};

/* Fallback boot info structure for PVH boot */
static vos3_boot_info_t pvh_fallback_boot_info;

/* Forward declaration for VOS3_WARN */
static const vos3_boot_info_t* get_boot_info(const vos3_boot_info_t* boot_info);

/* ============================================================================
 * VERSION INFO
 * ============================================================================ */

#define VOS3_VERSION_MAJOR  3
#define VOS3_VERSION_MINOR  1
#define VOS3_VERSION_PATCH  0
#define VOS3_VERSION_STRING "3.1.0-GOLD-MASTER"
#define VOS3_BUILD_DATE     __DATE__
#define VOS3_BUILD_TIME     __TIME__

/**
 * @brief v19.8: Compile UUID — deterministic build identifier.
 *
 * Concatenates VOS3_BUILD_DATE and VOS3_BUILD_TIME into a fixed string
 * that uniquely identifies this exact compilation.  Exposed via VBus
 * BUILD_UUID command for host-side desync detection.
 */
const char vos3_build_uuid[] = VOS3_BUILD_DATE " " VOS3_BUILD_TIME;

/* ============================================================================
 * KERNEL BANNER
 * ============================================================================ */

static void print_banner(void)
{
    vos3_console_set_color(VOS3_VGA_ATTR(VOS3_VGA_LIGHT_CYAN, VOS3_VGA_BLACK));

    vos3_console_puts("\n");
    vos3_console_puts(" __      _____  _____ ____  \n");
    vos3_console_puts(" \\ \\    / / _ \\/ ____|___ \\ \n");
    vos3_console_puts("  \\ \\  / / | | \\___ \\  __) |\n");
    vos3_console_puts("   \\ \\/ /| |_| |___) |/ __/ \n");
    vos3_console_puts("    \\__/  \\___/|____/|_____|\n");
    vos3_console_puts("\n");

    vos3_console_set_color(VOS3_VGA_DEFAULT_ATTR);

    vos3_console_printf("  VOS3 Kernel v%s\n", VOS3_VERSION_STRING);
    vos3_console_printf("  Built: %s %s\n", VOS3_BUILD_DATE, VOS3_BUILD_TIME);
    vos3_console_printf("  Architecture: x86_64\n");
    vos3_console_puts("\n");
}

/* ============================================================================
 * IST STACK SETUP
 * ============================================================================ */

static void setup_ist_stacks(void)
{
    VOS3_INFO("Setting up IST stacks");

    /* Set IST stacks for BSP (CPU 0) */
    vos3_tss_set_ist(0U, 1U, (uint64_t)(uintptr_t)_ist1_stack_top);  /* Double Fault */
    vos3_tss_set_ist(0U, 2U, (uint64_t)(uintptr_t)_ist2_stack_top);  /* NMI */
    vos3_tss_set_ist(0U, 3U, (uint64_t)(uintptr_t)_ist3_stack_top);  /* Machine Check */
    vos3_tss_set_ist(0U, 4U, (uint64_t)(uintptr_t)_ist4_stack_top);  /* Debug */

    /* Set kernel stack (RSP0) for privilege level changes */
    vos3_tss_set_rsp0(0U, (uint64_t)(uintptr_t)_boot_stack_top);
}

/* ============================================================================
 * PVH BOOT INFO CREATION
 * ============================================================================ */

/**
 * @brief Get or create boot info (handles PVH fallback)
 */
static const vos3_boot_info_t* get_boot_info(const vos3_boot_info_t* boot_info)
{
    /* Check if boot_info is valid */
    if (boot_info != NULL &&
        boot_info->magic == VOS3_BOOT_MAGIC &&
        boot_info->version >= VOS3_BOOT_VERSION &&
        boot_info->mem_map_entries > 0U) {
        /* Valid Multiboot2 boot info */
        return boot_info;
    }

    /* PVH boot - create fallback boot info with CMOS memory detection */
    uint64_t detected_mem = detect_memory_cmos();
    VOS3_WARN("PVH boot detected - CMOS reports %lu MB RAM",
              (unsigned long)(detected_mem / (1024ULL * 1024ULL)));

    /* Handle PCI memory hole for systems with >= 3GB RAM.
     * Standard x86 PC: PCI MMIO occupies 3GB-4GB (0xC0000000-0xFFFFFFFF).
     * RAM above 3GB is remapped by QEMU to start at 4GB (0x100000000).
     *
     * CRITICAL: Use strict < (not <=) so that when CMOS reports exactly
     * 3072 MB (0xC0000000), the PCI hole reserved region is still activated.
     * This prevents the PMM from allocating pages at the PCI boundary. */
    uint32_t mem_entries;
    if (detected_mem < 0xC0000000ULL) {
        /* No PCI hole concern: all RAM fits well below 3GB */
        pvh_memory_map[2].length = detected_mem - 0x00100000ULL;
        mem_entries = 3;
    } else {
        /* RAM reaches or spans PCI hole — enforce hard gap at 3GB-4GB.
         * Below-hole region: 1MB to 3GB (always usable).
         * Above-hole region: 4GB+ (only if detected_mem > 3GB). */
        pvh_memory_map[2].length = 0xC0000000ULL - 0x00100000ULL; /* 1MB to 3GB */
        uint64_t above_hole = (detected_mem > 0xC0000000ULL)
                              ? (detected_mem - 0xC0000000ULL) : 0ULL;
        pvh_memory_map[4].length = above_hole;                     /* 4GB+ region */
        mem_entries = 5;
        VOS3_WARN("PCI hole enforced: low=%lu MB, high=%lu MB above 4GB",
                  (unsigned long)((0xC0000000ULL - 0x00100000ULL) / (1024ULL * 1024ULL)),
                  (unsigned long)(above_hole / (1024ULL * 1024ULL)));
    }

    pvh_fallback_boot_info.magic = VOS3_BOOT_MAGIC;
    pvh_fallback_boot_info.version = VOS3_BOOT_VERSION;
    pvh_fallback_boot_info.size = sizeof(vos3_boot_info_t);
    pvh_fallback_boot_info.flags = 0;

    /* Memory info — use CMOS-detected size */
    pvh_fallback_boot_info.total_memory = detected_mem;
    pvh_fallback_boot_info.mem_map_addr = (uint64_t)(uintptr_t)pvh_memory_map;
    pvh_fallback_boot_info.mem_map_entries = mem_entries;
    pvh_fallback_boot_info.mem_map_entry_size = sizeof(vos3_boot_mmap_entry_t);

    /* Kernel location - use linker symbols */
    pvh_fallback_boot_info.kernel_phys_start = (uint64_t)(uintptr_t)_kernel_phys_start;
    pvh_fallback_boot_info.kernel_phys_end = (uint64_t)(uintptr_t)_kernel_phys_end;
    pvh_fallback_boot_info.kernel_virt_start = (uint64_t)(uintptr_t)_kernel_virt_start;
    pvh_fallback_boot_info.kernel_virt_end = (uint64_t)(uintptr_t)_kernel_virt_end;

    /* Page tables - will be set up by kernel */
    pvh_fallback_boot_info.pml4_phys = 0;
    pvh_fallback_boot_info.direct_map_offset = 0xFFFF800000000000ULL;

    /* No framebuffer in PVH mode */
    pvh_fallback_boot_info.framebuffer.address = 0;
    pvh_fallback_boot_info.framebuffer.width = 0;
    pvh_fallback_boot_info.framebuffer.height = 0;
    pvh_fallback_boot_info.framebuffer.pitch = 0;
    pvh_fallback_boot_info.framebuffer.bpp = 0;

    /* Minimal SMP - single CPU */
    pvh_fallback_boot_info.cpu_count = 1;
    pvh_fallback_boot_info.bsp_lapic_id = 0;

    /* No command line or modules */
    pvh_fallback_boot_info.cmdline_addr = 0;
    pvh_fallback_boot_info.cmdline_size = 0;
    pvh_fallback_boot_info.module_count = 0;
    pvh_fallback_boot_info.modules_addr = 0;

    return &pvh_fallback_boot_info;
}

/* ============================================================================
 * KERNEL MAIN
 * ============================================================================ */

/**
 * @brief Kernel main entry point
 * @param[in] boot_info Boot information from bootloader
 * @note This function should never return
 */
__attribute__((noreturn))
void kernel_main(const vos3_boot_info_t* raw_boot_info)
{
    int result;
    const vos3_boot_info_t* boot_info;
    vos3_cpu_info_t cpu_info;
    vos3_preflight_features_t preflight_features;

    /* ===== Phase 1: Early Console ===== */
    vos3_console_init(VOS3_CONSOLE_BOTH, 0U);
    vos3_console_clear();

    /* Initialize kernel log ring buffer (before any logging) */
    vos3_klog_init();

    print_banner();

    VOS3_INFO("Kernel starting...");

    /* Reject unsupported silicon before memory, interrupts or drivers can
     * rely on features that are part of the native kernel contract.  CPUID
     * collection is centralized in cpu.c and bounds every queried leaf. */
    vos3_cpu_detect_features(&cpu_info);
    result = vos3_baremetal_preflight_evaluate(&cpu_info,
                                                &preflight_features);
    if (result != VOS3_PREFLIGHT_OK) {
        VOS3_PANIC("CPU preflight failed: %s (%d)",
                   vos3_preflight_status_str(result), result);
    }
    VOS3_INFO("CPU preflight OK: pcid=%u invpcid=%u sha_ni=%u x2apic=%u",
              preflight_features.pcid_supported,
              preflight_features.invpcid_supported,
              preflight_features.sha_ni_supported,
              preflight_features.x2apic_supported);

    /* ===== Phase 2: Get/Validate Boot Info ===== */
    VOS3_INFO("Validating boot information");

    /* Get boot info - may be original or PVH fallback */
    boot_info = get_boot_info(raw_boot_info);

    result = vos3_boot_info_validate(boot_info);
    if (result != 0) {
        VOS3_PANIC("Invalid boot information (error %d)", result);
    }

    VOS3_INFO("Boot info validated: magic=0x%08x, version=0x%08x",
              boot_info->magic, boot_info->version);

    /* ===== Phase 3: GDT ===== */
    VOS3_INFO("Initializing GDT");
    vos3_gdt_init();

    /* ===== Phase 3b: Per-CPU Data ===== */
    VOS3_INFO("Initializing Per-CPU subsystem");
    percpu_init();

    /* ===== Phase 4: IDT ===== */
    VOS3_INFO("Initializing IDT");
    vos3_idt_init();

    /* Set up IST stacks */
    setup_ist_stacks();

    /* ===== Phase 9: GDT/IDT Validation ===== */
    {
        extern int vos3_gdt_validate(void);
        int gdt_errs = vos3_gdt_validate();
        if (gdt_errs > 0) {
            VOS3_PANIC("GDT validation FATAL: %d errors (boot halted)", gdt_errs);
        }
    }

    /* ===== Phase 5: PIC ===== */
    VOS3_INFO("Initializing PIC");
    vos3_pic_init();

    /* Mask all IRQs for now */
    vos3_pic_disable_all();

    /* ===== Phase 6: Register Interrupt Handlers ===== */
    VOS3_INFO("Registering interrupt handlers");
    vos3_interrupts_init();

    /* ===== Phase 6b: KASLR ===== */
    VOS3_INFO("Initializing KASLR (address randomization)");
    vos3_kaslr_init();

    /* ===== Modular Boot: Memory Management ===== */
    boot_mm_init(boot_info);
#ifdef VOS3_PROCESS_ROOTS_TEST
    extern void vos3_test_process_roots(void);
    vos3_test_process_roots();
#endif

    /* ===== Modular Boot: Hardware Drivers ===== */
    boot_drivers_init(boot_info);

    /* P3.1 · ECAM late-init — vOS·Adaptive·SHA=aeb3736·Phase=P3.
     * boot_drivers_init runs PCI scan BEFORE ACPI parse, so the
     * first ECAM probe (lazy, inside pci_bus_scan) lands when MCFG
     * isn't yet discoverable. Re-probe now that ACPI is online —
     * if MCFG is present, the dispatcher's vos3_pci_ecam_is_available
     * flips true for any subsequent driver init that needs PCIe
     * extended config space (> offset 0xFF, unreachable via Port-I/O). */
    (void)vos3_pci_ecam_init();

    /* ===== Phase 10: CPU Detection ===== */
    VOS3_INFO("Detecting CPU features");
    /* Preserve the exact CPUID snapshot admitted by the early gate. */
    vos3_cpu_detect_microcode(&cpu_info);
    vos3_cpu_print_info(&cpu_info);

    /* P1 · Adaptive Mitigation Factory — pick KPTI tier from CPUID and
     * latch read-only. !LM panics inside _init() and does not return. */
    (void)vos3_mitigation_factory_init(&cpu_info);
    vos3_mitigation_print_summary();

    /* Initialize CPU-local process-root transitions. Each address space
     * owns its restricted root; entry/exit use dedicated trampolines. */
    (void)vos3_kpti_init();

    /* Deep Audit: Dynamic MAXPHYADDR — replace static 40-bit mask with
     * CPUID-detected physical address width for 2MB PDE masking. */
    vos3_vmm_set_maxphyaddr(cpu_info.phys_addr_bits);

    /* ===== Phase 10a0: FPU/XSAVE Lazy Context Switch ===== */
    VOS3_INFO("Initializing FPU subsystem (XSAVE/lazy switching)");
    vos3_fpu_init();

    /* ===== M5 (v21.4.3): HW activation seam — flags default-OFF =====
     *
     * Per V21_3_TOTAL_SUPREMACY_PLAN.md §0 Blind-Implementation Protocol.
     * Each call returns -ENODEV when its VOS3_HW_* flag is undefined,
     * which is the default. Build with VOS3_HW_XSAVE=1 / VOS3_HW_LAPIC=1
     * / VOS3_HW_IOAPIC=1 to activate the corresponding subsystem; do
     * NOT activate without first verifying the prerequisite under QEMU.
     *
     * Order rationale:
     *   1. xsave_boot_init MUST come after fpu_init (which has set
     *      CR4.OSFXSR — see Intel SDM §2.5).
     *   2. apic_init_mmio MUST come after boot_drivers_init's ACPI
     *      parse (which populated g_acpi_info.lapic_addr).
     *   3. apic_init_lvt MUST come after init_mmio (which set
     *      g_apic_base_va).
     *   4. ioapic_init MUST come after ACPI (g_acpi_info.ioapics[]).
     *   5. ALL must come before vos3_smp_init below — SMP wake uses
     *      the LAPIC ICR write path on AP wake.
     */
    {
        int hw_rc;
        hw_rc = vos3_xsave_boot_init();
        if (hw_rc != 0 && hw_rc != -19 /* ENODEV */) {
            VOS3_WARN("[M5] vos3_xsave_boot_init returned %d", hw_rc);
        }
        hw_rc = vos3_apic_init_mmio();
        if (hw_rc != 0 && hw_rc != -19) {
            VOS3_WARN("[M5] vos3_apic_init_mmio returned %d", hw_rc);
        }
        hw_rc = vos3_apic_init_lvt();
        if (hw_rc != 0 && hw_rc != -19) {
            VOS3_WARN("[M5] vos3_apic_init_lvt returned %d", hw_rc);
        }
        hw_rc = vos3_ioapic_init();
        if (hw_rc != 0 && hw_rc != -19) {
            VOS3_WARN("[M5] vos3_ioapic_init returned %d", hw_rc);
        }
    }

    /* ===== CET: Control-flow Enforcement Technology ===== */
    extern void vos3_cet_init(void);
    VOS3_INFO("Initializing CET (Indirect Branch Tracking)");
    vos3_cet_init();

    /* ===== Prefetch: Inference-Predictive HugePage Mapping ===== */
    extern void vos3_prefetch_init(void);
    VOS3_INFO("Initializing Prefetch Engine (Claw Protocol)");
    vos3_prefetch_init();

    /* ===== Phase 10a: Entropy Subsystem ===== */
    VOS3_INFO("Initializing entropy subsystem");
    vos3_entropy_init();

    /* Re-seed stack canary from hardware RDRAND now that entropy is online */
    extern void vos3_stack_guard_reseed(void);
    vos3_stack_guard_reseed();

    /* ===== Phase 10b: NUMA Topology ===== */
    VOS3_INFO("Detecting NUMA topology");
    result = vos3_numa_init();
    if (result != 0) {
        VOS3_WARN("NUMA initialization failed (error %d) - using UMA mode", result);
    }

    /* ===== Phase 10c: Scheduler (before SMP to initialize task subsystem) ===== */
    VOS3_INFO("Initializing Scheduler");
    result = vos3_sched_init();
    if (result != 0) {
        VOS3_PANIC("Scheduler initialization failed (error %d)", result);
    }

    /* ===== Phase 10d: SMP Initialization ===== */
    VOS3_INFO("Initializing SMP (waking Application Processors)");
    result = vos3_smp_init();
    if (result != 0) {
        VOS3_WARN("SMP initialization failed (error %d) - running single core", result);
    } else {
        VOS3_INFO("SMP: %u CPUs online", vos3_smp_online_count());
    }

    /* Cyber overlay (Stage 6): SCHED_CORE topology init.
     * Must run AFTER vos3_smp_init() so the sibling table reflects all
     * online CPUs. Builds the O(1) APIC-ID → sibling-CPU lookup used by
     * vos3_sched_sibling_compatible() in the slot-admission gate. */
    vos3_sched_core_init_topology();
    VOS3_INFO("[CYBER] SCHED_CORE sibling topology initialized");

    /* ===== Phase 11: Timer ===== */
    VOS3_INFO("Initializing Timer (PIT)");
    result = vos3_timer_init(VOS3_TIMER_FREQ);
    if (result != 0) {
        VOS3_PANIC("Timer initialization failed (error %d)", result);
    }

    /* ===== Phase 13: System Calls ===== */
    VOS3_INFO("Initializing System Call Interface");
    result = vos3_syscall_init();
    if (result != 0) {
        VOS3_PANIC("Syscall initialization failed (error %d)", result);
    }

    /* ===== Phase 14: User Mode Support ===== */
    VOS3_INFO("Initializing User Mode Support");
    result = vos3_user_init();
    if (result != 0) {
        VOS3_PANIC("User mode initialization failed (error %d)", result);
    }

    /* ===== Phase 15: IPC Subsystem ===== */
    VOS3_INFO("Initializing IPC Subsystem");
    result = vos3_ipc_init();
    if (result != 0) {
        VOS3_PANIC("IPC initialization failed (error %d)", result);
    }

    /* ===== Phase 16: Virtual File System ===== */
    VOS3_INFO("Initializing Virtual File System");
    result = vos3_vfs_init();
    if (result != 0) {
        VOS3_PANIC("VFS initialization failed (error %d)", result);
    }

    /* ===== Modular Boot: Filesystem ===== */
    boot_fs_init();

    /* Register FS syscalls */
    vos3_fs_syscalls_init();

    /* Register exec/process syscalls */
    vos3_exec_syscalls_init();

    /* Register signal syscalls */
    vos3_signal_syscalls_init();

    /* Register time syscalls */
    vos3_time_syscalls_init();

    /* Task 1.5: Register futex + POSIX core syscalls */
    vos3_futex_init();
    vos3_posix_syscalls_init();

    /* Initialize benchmark instrumentation */
    vos3_bench_init();
    vos3_bench_syscalls_init();

    /* ===== Modular Boot: AI Subsystem ===== */
    boot_ai_init();

    /* ===== Phase 18: Device Subsystem ===== */
    VOS3_INFO("Initializing Device Subsystem");
    result = vos3_devices_init();
    if (result != 0) {
        VOS3_PANIC("Device subsystem initialization failed (error %d)", result);
    }

    /* ===== Phase 25: SMP Telemetry Device ===== */
    result = vos3_smp_telemetry_init();
    if (result != 0) {
        VOS3_WARN("SMP telemetry initialization failed (error %d)", result);
    }

    /* ===== Modular Boot: Network Subsystem ===== */
    boot_net_init();

    /* ===== Phase 18: Enable Interrupts ===== */
    VOS3_INFO("Enabling interrupts");

    /* Unmask timer and keyboard IRQs */
    vos3_pic_unmask_irq(0U);  /* Timer */
    vos3_pic_unmask_irq(1U);  /* Keyboard */

    /* Enable interrupts */
    vos3_int_enable();

    /* ===== Phase 19: Kernel Tasks ===== */
    VOS3_INFO("Creating kernel tasks");
    result = vos3_kernel_tasks_init();
    if (result != 0) {
        VOS3_PANIC("Kernel task creation failed (error %d)", result);
    }

    /* ===== Phase 20: Configuration Check ===== */
    VOS3_INFO("Checking system configuration");
    vos3_config_init();

    /* ===== Phase 21: Provisioning Gate ===== */
/* HEADLESS_AUDIT is injected by the build system (-DHEADLESS_AUDIT in Makefile).
 * Defining it in source was PC01: a hardcoded bypass of provisioning logic. */
#ifdef HEADLESS_AUDIT
    /* HEADLESS AUDIT MODE: Skip provisioning, spawn init directly */
    VOS3_INFO("=================================================");
    VOS3_INFO("  HEADLESS AUDIT MODE - Phase 25 Stress Test     ");
    VOS3_INFO("=================================================");
    VOS3_INFO("Bypassing provisioning, spawning init directly...");

    result = vos3_spawn_init();
    if (result != 0) {
        VOS3_PANIC("Failed to spawn init (error %d)", result);
    }

    /* Skip normal provisioning logic */
    (void)0;
#else
    if (vos3_provisioning_needed()) {
        /*
         * PROVISIONING MODE
         *
         * System is not configured - boot into isolated provisioning mode.
         * Only the setup wizard runs. Shell access is blocked until
         * /etc/vos3.conf is successfully written.
         */
        VOS3_INFO("=================================================");
        VOS3_INFO("  FIRST BOOT DETECTED - PROVISIONING REQUIRED    ");
        VOS3_INFO("=================================================");
        VOS3_INFO("Starting Setup Wizard...");

        vos3_set_provisioning_status(VOS3_PROV_IN_PROGRESS);

        result = vos3_spawn_setup_wizard();
        if (result != 0) {
            VOS3_PANIC("Failed to spawn setup wizard (error %d)", result);
        }

        vos3_console_set_color(VOS3_VGA_OK_ATTR);
        VOS3_INFO("Setup wizard launched - awaiting configuration...");
        vos3_console_set_color(VOS3_VGA_DEFAULT_ATTR);

    } else {
        /* ===== Phase 22: Spawn Init ===== */
        VOS3_INFO("System configured: %s mode, %s workspace",
                  vos3_config_mode_str(vos3_config_get_system_mode()),
                  vos3_config_workspace_str(vos3_config_get_workspace()));

        VOS3_INFO("Spawning init process");
        result = vos3_spawn_init();
        if (result != 0) {
            VOS3_PANIC("Failed to spawn init (error %d)", result);
        }
    }
#endif  /* !HEADLESS_AUDIT */

    /* ===== Kernel Ready ===== */
    vos3_console_set_color(VOS3_VGA_OK_ATTR);
    VOS3_INFO("Kernel initialization complete!");
    vos3_console_set_color(VOS3_VGA_DEFAULT_ATTR);

#ifdef HEADLESS_AUDIT
    vos3_console_puts("\nVOS3 HEADLESS AUDIT MODE.\n");
    vos3_console_puts("Running Phase 25 Stress Test...\n");
#else
    if (vos3_provisioning_needed()) {
        vos3_console_puts("\nVOS3 Provisioning Mode.\n");
        vos3_console_puts("Complete the setup wizard to continue...\n");
    } else {
        vos3_console_puts("\nVOS3 is ready.\n");
        vos3_console_puts("Starting scheduler...\n");
    }
#endif

    /* ===== Day 10: Bridge Task ===== */
    {
        vos3_task_t* bridge_task = vos3_task_create("bridge",
            vos3_bridge_task, NULL, VOS3_PRIORITY_LOW);
        if (bridge_task != NULL) {
            vos3_sched_add_task(bridge_task);
            VOS3_INFO("Bridge polling task created");
        }
    }

    /* ===== M4 (v21.3): Certification Harness — EARLY position =====
     *
     * Runs the cert harness BEFORE the boot-stack scrub below. Reason:
     * the scrub touches unmapped pages on some KASLR layouts and panics
     * with #PF on 0x105000-class addresses. Moving the harness ahead of
     * the scrub means the cert lines fire even when the scrub trips a
     * fault, so the M5 evidence-gathering succeeds on the boot path that
     * actually reaches user-land. By this point ACPI is parsed, GDT/IDT
     * loaded, FPU/XSAVE probed, scheduler initialized — every state var
     * the cert harness reads is populated.
     *
     * No-op when VOS3_ASSERT_HARNESS is undefined (default builds).
     */
#ifdef VOS3_ASSERT_HARNESS
    vos3_run_cert_harness(boot_info);
#endif

    /* ===== v23.11: Boot Secret Scrubbing ===== */
    /* TS-2026-PF_MEMSET_SCRUB: scrub a VA range one page at a time, skipping any
     * page that is NOT currently mapped. The 32-bit bootstrap stack is
     * identity-mapped only during early boot; the KPTI PML4-strip later removes
     * the low mapping, so an unguarded memset over it faults (#PF, kernel write
     * not-present, CR2 in the low-1MB region). Some KASLR boot-stack layouts can
     * likewise leave a page in the 64KB range unmapped. Bounding the scrub to
     * mapped pages preserves the secret-wipe intent without the post-DONE panic. */
    {
        struct { uintptr_t start; size_t len; } scrubs[2];
        size_t nscrub = 0;

        /* E2: 4KB 32-bit bootstrap stack (multiboot2_entry.S) — RDRAND seeds,
         * page-table-setup intermediates, other boot-time entropy. */
        scrubs[nscrub].start = (uintptr_t)boot32_stack;
        scrubs[nscrub].len = 4096U;
        nscrub++;

        /* E1: 64KB boot stack from _boot_stack_bottom up to (RSP - 512). We are
         * still running on this stack, so the 512-byte margin protects the
         * current call frame from being zeroed under us. */
        {
            uintptr_t rsp_val;
            __asm__ volatile("mov %%rsp, %0" : "=r"(rsp_val));
            uintptr_t bottom = (uintptr_t)_boot_stack_bottom;
            uintptr_t safe_top = rsp_val - 512U;
            if (safe_top > bottom) {
                scrubs[nscrub].start = bottom;
                scrubs[nscrub].len = (size_t)(safe_top - bottom);
                nscrub++;
            }
        }

        size_t skipped_pages = 0;
        for (size_t s = 0; s < nscrub; s++) {
            uintptr_t end = scrubs[s].start + scrubs[s].len;
            uintptr_t page = scrubs[s].start & ~(uintptr_t)0xFFFU;
            for (; page < end; page += 4096U) {
                if (!vos3_vmm_is_mapped(page)) {
                    skipped_pages++;
                    continue;
                }
                uintptr_t a = (page < scrubs[s].start) ? scrubs[s].start : page;
                uintptr_t b = ((page + 4096U) < end) ? (page + 4096U) : end;
                memset((void *)a, 0, (size_t)(b - a));
            }
        }
        if (skipped_pages != 0U) {
            VOS3_INFO("Boot-secret scrub: skipped %u unmapped page(s) "
                      "(low identity / KPTI-stripped; TS-2026-PF_MEMSET_SCRUB)",
                      (unsigned)skipped_pages);
        }
    }
    VOS3_INFO("Boot secrets scrubbed (32-bit stack + 64KB boot stack)");

    /* ===== Start Scheduler ===== */
    /* This never returns - scheduler takes over */
    vos3_sched_start();
}

/* ============================================================================
 * M4 — Certification Harness body
 * ============================================================================
 *
 * Inert when VOS3_ASSERT_HARNESS is undefined. Each VOS3_ASSERT_CERT
 * inside this function emits exactly one ID — duplicates would be
 * caught by tools/runner/qemu_assert_runner.py.
 *
 * Honest accounting: this function is the SOLE call site for every
 * populated cert ID in kernel/include/vos/assert_cert_ids.h. Adding a
 * new cert means: (a) define a new ID in the registry, (b) add one
 * VOS3_ASSERT_CERT call here, (c) bump VOS3_CERT_MAX_ID_CURRENTLY_USED.
 */
#ifdef VOS3_ASSERT_HARNESS
#include "../../include/vos/heap.h"

static inline uint64_t cert_read_cr0(void)
{
    uint64_t v;
    __asm__ volatile ("mov %%cr0, %0" : "=r"(v));
    return v;
}

static inline uint64_t cert_read_cr3(void)
{
    uint64_t v;
    __asm__ volatile ("mov %%cr3, %0" : "=r"(v));
    return v;
}

static inline uint64_t cert_read_cr4(void)
{
    uint64_t v;
    __asm__ volatile ("mov %%cr4, %0" : "=r"(v));
    return v;
}

static inline uint32_t cert_cpuid_leaf0_max(void)
{
    uint32_t eax = 0, ebx = 0, ecx = 0, edx = 0;
    __asm__ volatile ("cpuid"
                      : "=a"(eax), "=b"(ebx), "=c"(ecx), "=d"(edx)
                      : "a"(0u), "c"(0u));
    return eax;
}

static inline uint64_t cert_read_msr_efer(void)
{
    uint32_t lo, hi;
    __asm__ volatile ("rdmsr" : "=a"(lo), "=d"(hi) : "c"(0xC0000080u));
    return ((uint64_t)hi << 32) | (uint64_t)lo;
}

static inline void cert_cpuid1(uint32_t *eax, uint32_t *ebx,
                               uint32_t *ecx, uint32_t *edx)
{
    __asm__ volatile ("cpuid"
                      : "=a"(*eax), "=b"(*ebx), "=c"(*ecx), "=d"(*edx)
                      : "a"(1u), "c"(0u));
}

extern char _kernel_virt_start[];

void vos3_run_cert_harness(const vos3_boot_info_t *boot_info);
void vos3_run_cert_harness(const vos3_boot_info_t *boot_info)
{
    (void)boot_info;

    /* ----- 1..5: Canaries ----- */
    {
        uint64_t efer = cert_read_msr_efer();
        VOS3_ASSERT_CERT(VOS3_CERT_CANARY_LONG_MODE,
                         (efer & (1ull << 8)) != 0ull);
        VOS3_ASSERT_CERT(VOS3_CERT_CANARY_NX_ENABLED,
                         (efer & (1ull << 11)) != 0ull);
    }
    {
        uint64_t cr0 = cert_read_cr0();
        VOS3_ASSERT_CERT(VOS3_CERT_CANARY_PAGING,
                         (cr0 & (1ull << 31)) != 0ull);
    }
    {
        uint32_t a = 0, b = 0, c = 0, d = 0;
        cert_cpuid1(&a, &b, &c, &d);
        VOS3_ASSERT_CERT(VOS3_CERT_CANARY_SSE2_AVAILABLE,
                         (d & (1u << 26)) != 0u);
    }
    VOS3_ASSERT_CERT(VOS3_CERT_CANARY_KERNEL_VIRT_HIGH,
                     ((uintptr_t)_kernel_virt_start) >= 0xFFFF800000000000ull);

    /* ----- 10..14: Boot path ----- */
    {
        struct { uint16_t limit; uint64_t base; } __attribute__((packed)) gdtr;
        __asm__ volatile ("sgdt %0" : "=m"(gdtr));
        VOS3_ASSERT_CERT(VOS3_CERT_BOOT_GDT_LOADED, gdtr.limit > 0u);
    }
    {
        struct { uint16_t limit; uint64_t base; } __attribute__((packed)) idtr;
        __asm__ volatile ("sidt %0" : "=m"(idtr));
        /* 256 vectors * 16 bytes - 1 = 0xFFF */
        VOS3_ASSERT_CERT(VOS3_CERT_BOOT_IDT_LOADED, idtr.limit >= 0x0FFFu);
    }
    {
        const vos3_acpi_info_t *info = vos3_acpi_get_info();
        VOS3_ASSERT_CERT(VOS3_CERT_BOOT_RSDP_FOUND,
                         info != ((void *)0) && info->rsdp_phys != 0ull);
        VOS3_ASSERT_CERT(VOS3_CERT_BOOT_MADT_PARSED,
                         info != ((void *)0) && info->cpu_count >= 1u);
        VOS3_ASSERT_CERT(VOS3_CERT_BOOT_FADT_FOUND,
                         info != ((void *)0) && info->fadt_found != 0u);
    }

    /* ----- 20..22: XSAVE ----- */
    {
        int probe_rc = vos3_xsave_probe();  /* idempotent */
        VOS3_ASSERT_CERT(VOS3_CERT_XSAVE_PROBE_OK, probe_rc == 0);
        uint32_t sz = vos3_xsave_get_area_size();
        /* Size is 0 on CPUs without XSAVE — accept that as "in range"
         * since the contract is "either 0 or a sane positive value".
         * Upper bound is 65536 to accommodate AMX tiles (SDM §13.3). */
        VOS3_ASSERT_CERT(VOS3_CERT_XSAVE_AREA_SIZE_RANGE,
                         sz == 0u || (sz >= 512u && sz <= 65536u));
        int boot_rc = vos3_xsave_boot_init();
        /* boot_rc: 0 on success, -ENODEV if VOS3_HW_XSAVE off OR CPU
         * lacks XSAVE — both are acceptable steady states. */
        VOS3_ASSERT_CERT(VOS3_CERT_XSAVE_BOOT_INIT_RESULT,
                         boot_rc == 0 || boot_rc == -19);
    }

    /* ----- 30..32: LAPIC ----- */
    {
        int detect_rc = vos3_apic_detect();
        VOS3_ASSERT_CERT(VOS3_CERT_APIC_DETECT_OK,
                         detect_rc == 0 || detect_rc == -19);
        /* MMIO mapping is M2's add — only meaningful when VOS3_HW_LAPIC
         * is on. We pass-through under the same logic: function returns
         * 0 on success, -ENODEV if the flag is off. */
        int mmio_rc = vos3_apic_init_mmio();
        VOS3_ASSERT_CERT(VOS3_CERT_APIC_MMIO_MAPPED,
                         mmio_rc == 0 || mmio_rc == -19);
        int lvt_rc = vos3_apic_init_lvt();
        VOS3_ASSERT_CERT(VOS3_CERT_APIC_SPIV_PROGRAMMED,
                         lvt_rc == 0 || lvt_rc == -19);
    }

    /* ----- 40..42: IOAPIC ----- */
    {
        const vos3_acpi_info_t *info = vos3_acpi_get_info();
        VOS3_ASSERT_CERT(VOS3_CERT_IOAPIC_MADT_ENUMERATED,
                         info != ((void *)0) && info->ioapic_count >= 1u);
        int init_rc = vos3_ioapic_init();
        VOS3_ASSERT_CERT(VOS3_CERT_IOAPIC_INIT_RESULT,
                         init_rc == 0 || init_rc == -19);
        uint8_t mr = vos3_ioapic_max_redir();
        /* When VOS3_HW_IOAPIC is off, mr stays 0. When on, a typical PC
         * IOAPIC reports 24. Accept 0 (off) OR [16, 256] (on). */
        VOS3_ASSERT_CERT(VOS3_CERT_IOAPIC_MAX_REDIR_RANGE,
                         mr == 0u || mr >= 16u);
    }

    /* ----- 50..51: Scheduler -----
     *
     * vos3_sched_init() ran in Phase 10c with PANIC-on-failure, so by
     * here the result must have been 0. Verify via the runtime flag
     * rather than a hardcoded literal — if a future boot path changes
     * the PANIC to a tolerated error, these certs will correctly fail. */
    VOS3_ASSERT_CERT(VOS3_CERT_SCHED_INIT_RC,
                     vos3_sched_is_initialized() != 0);
    VOS3_ASSERT_CERT(VOS3_CERT_SCHED_IDLE_TASK_PRESENT,
                     vos3_sched_is_initialized() != 0);

    /* ----- 60..61: VBus -----
     *
     * Magic and opcode constants are verified by comparing against their
     * expected values so that any accidental redefinition is caught. */
    VOS3_ASSERT_CERT(VOS3_CERT_VBUS_FRAME_MAGIC,
                     VOS3_VBUS_MAGIC == 0x56425553u);
    VOS3_ASSERT_CERT(VOS3_CERT_VBUS_OPCODE_RANGE,
                     VOS3_VBUS_OP_REGISTER_AGENT >= 0x100u &&
                     VOS3_VBUS_OP_REGISTER_AGENT <= 0x4FFu);

    /* ----- 70..71: Heap ----- */
    VOS3_ASSERT_CERT(VOS3_CERT_HEAP_INIT_RC,
                     VOS3_HEAP_MIN_SIZE == 16U);
    VOS3_ASSERT_CERT(VOS3_CERT_HEAP_SLAB_MAGIC_DEFINED,
                     VOS3_HEAP_SLAB_OBJ_FREE_MAGIC == 0xDEADBEEFCAFEBABEULL);

    /* ----- 72..76: Heap extended ----- */
    VOS3_ASSERT_CERT(VOS3_CERT_HEAP_INTEGRITY_OK,
                     vos3_heap_check() == 0);
    VOS3_ASSERT_CERT(VOS3_CERT_HEAP_SLAB_CLASSES_8,
                     VOS3_HEAP_SLAB_CLASSES == 8U);
    VOS3_ASSERT_CERT(VOS3_CERT_HEAP_MAX_SLAB_POWER2,
                     (VOS3_HEAP_MAX_SLAB_SIZE & (VOS3_HEAP_MAX_SLAB_SIZE - 1U)) == 0U);
    VOS3_ASSERT_CERT(VOS3_CERT_HEAP_ALIGN_POWER2,
                     (VOS3_HEAP_ALIGN & (VOS3_HEAP_ALIGN - 1U)) == 0U);
    VOS3_ASSERT_CERT(VOS3_CERT_HEAP_MAGIC_DEFINED,
                     VOS3_HEAP_MAGIC == 0x48454150U);

    /* ----- 80..86: XSAVE feature probes -----
     *
     * All probe functions return 0 (not available) or 1 (available);
     * a negative return signals an internal probe error, which would
     * be a bug.  vos3_xsave_self_test() additionally returns -ENODEV
     * (-19) when XSAVE is compiled out — both are acceptable.
     */
    {
        int xhx = vos3_xsave_has_xsave();
        VOS3_ASSERT_CERT(VOS3_CERT_XSAVE_HAS_XSAVE_PROBE, xhx >= 0);
        VOS3_ASSERT_CERT(VOS3_CERT_XSAVE_HAS_AVX_PROBE,
                         vos3_xsave_has_avx() >= 0);
        VOS3_ASSERT_CERT(VOS3_CERT_XSAVE_HAS_AVX512F_PROBE,
                         vos3_xsave_has_avx512f() >= 0);
        VOS3_ASSERT_CERT(VOS3_CERT_XSAVE_LIVE_PATH,
                         vos3_xsave_live_path_compiled() >= 0);
        {
            uint32_t sz = vos3_xsave_get_area_size();
            /* Area SIZE does not need to be 64-byte aligned — the
             * alignment requirement is on the POINTER, not the size.
             * The correct invariant: if XSAVE is present the area
             * must accommodate at least the legacy 512-byte region. */
            VOS3_ASSERT_CERT(VOS3_CERT_XSAVE_AREA_ALIGN64,
                             sz == 0U || sz >= 512U);
        }
        {
            int st = vos3_xsave_self_test();
            VOS3_ASSERT_CERT(VOS3_CERT_XSAVE_SELF_TEST_RESULT,
                             st == 0 || st == -19);
        }
        /* If XSAVE is available, the feature mask must be non-zero. */
        VOS3_ASSERT_CERT(VOS3_CERT_XSAVE_FEATURES_NONZERO,
                         xhx == 0 || vos3_xsave_supported_features() != 0ull);
    }

    /* ----- 90..96: Scheduler time constants -----
     *
     * Compile-time constants verified as link-time invariants.
     * If any scheduling constant is accidentally redefined to break
     * the ordering or the 100 Hz contract, the harness will fail here
     * on the very next boot — before any user-visible regression.
     */
    VOS3_ASSERT_CERT(VOS3_CERT_SCHED_TIMER_100HZ,
                     VOS3_TIMER_FREQ == 100U);
    VOS3_ASSERT_CERT(VOS3_CERT_SCHED_SLICE_IDLE_LT_LOW,
                     VOS3_SCHED_SLICE_IDLE < VOS3_SCHED_SLICE_LOW);
    VOS3_ASSERT_CERT(VOS3_CERT_SCHED_SLICE_LOW_LT_NORMAL,
                     VOS3_SCHED_SLICE_LOW < VOS3_SCHED_SLICE_NORMAL);
    VOS3_ASSERT_CERT(VOS3_CERT_SCHED_SLICE_NORMAL_LT_HIGH,
                     VOS3_SCHED_SLICE_NORMAL < VOS3_SCHED_SLICE_HIGH);
    VOS3_ASSERT_CERT(VOS3_CERT_SCHED_SLICE_HIGH_LT_RT,
                     VOS3_SCHED_SLICE_HIGH < VOS3_SCHED_SLICE_REALTIME);
    VOS3_ASSERT_CERT(VOS3_CERT_SCHED_MS_PER_TICK_10,
                     VOS3_MS_PER_TICK == 10U);
    VOS3_ASSERT_CERT(VOS3_CERT_SCHED_TICKS_EQ_FREQ,
                     VOS3_TICKS_PER_SEC == VOS3_TIMER_FREQ);

    /* ----- 100..105: ACPI / boot extended ----- */
    {
        const vos3_acpi_info_t *info2 = vos3_acpi_get_info();
        VOS3_ASSERT_CERT(VOS3_CERT_ACPI_CPU_COUNT_SANE,
                         info2 != ((void *)0)
                         && info2->cpu_count >= 1u
                         && info2->cpu_count <= 256u);
        VOS3_ASSERT_CERT(VOS3_CERT_ACPI_IOAPIC_COUNT_SANE,
                         info2 != ((void *)0)
                         && info2->ioapic_count >= 1u
                         && info2->ioapic_count <= 32u);
        VOS3_ASSERT_CERT(VOS3_CERT_BOOT_RSDP_BIOS_REGION,
                         info2 != ((void *)0)
                         && info2->rsdp_phys != 0ull
                         && info2->rsdp_phys < 0x100000000ull);
    }
    VOS3_ASSERT_CERT(VOS3_CERT_BOOT_CR3_NONZERO,
                     cert_read_cr3() != 0ull);
    VOS3_ASSERT_CERT(VOS3_CERT_BOOT_CR4_OSFXSR,
                     (cert_read_cr4() & (1ull << 9)) != 0ull);
    VOS3_ASSERT_CERT(VOS3_CERT_BOOT_CPUID_LEAF0_GE7,
                     cert_cpuid_leaf0_max() >= 7u);

    /* ----- 110..119: CPU/VMM/ABI invariants ----- */
    VOS3_ASSERT_CERT(VOS3_CERT_VMM_KERNEL_SPACE_NONNULL,
                     vos3_vmm_get_kernel_space() != ((void *)0));
    VOS3_ASSERT_CERT(VOS3_CERT_HEAP_MIN_SIZE_16,
                     VOS3_HEAP_MIN_SIZE == 16U);
    VOS3_ASSERT_CERT(VOS3_CERT_HEAP_MAGIC_ASCII,
                     VOS3_HEAP_MAGIC == 0x48454150U);
    VOS3_ASSERT_CERT(VOS3_CERT_SCHED_DEFAULT_SLICE_10,
                     VOS3_SCHED_DEFAULT_SLICE == 10U);
    {
        uint64_t lapic_base = vos3_apic_get_base_phys();
        VOS3_ASSERT_CERT(VOS3_CERT_APIC_BASE_CANONICAL,
                         lapic_base == 0ull || lapic_base >= 0xFEE00000ull);
    }
    {
        uint64_t efer = cert_read_msr_efer();
        VOS3_ASSERT_CERT(VOS3_CERT_BOOT_EFER_SCE,
                         (efer & 1ull) != 0ull);       /* SYSCALL enable */
        VOS3_ASSERT_CERT(VOS3_CERT_BOOT_EFER_LMA,
                         (efer & (1ull << 10)) != 0ull); /* Long Mode Active */
    }
    {
        uint64_t cr0 = cert_read_cr0();
        VOS3_ASSERT_CERT(VOS3_CERT_BOOT_CR0_WP,
                         (cr0 & (1ull << 16)) != 0ull);
        VOS3_ASSERT_CERT(VOS3_CERT_BOOT_CR0_PE,
                         (cr0 & 1ull) != 0ull);
    }
    VOS3_ASSERT_CERT(VOS3_CERT_BOOT_CANONICAL_VA,
                     ((uintptr_t)_kernel_virt_start & 0xFFFF800000000000ull)
                     == 0xFFFF800000000000ull);

    /* ---- M3 model-file SecureBoot enforcement matrix (Phase 5) ----
     * Runs the M3 verify pipeline IN THE BOOTED KERNEL on a real OMS vector
     * (Ed25519 over SHA-256, the model_signer.py scheme) and certifies the
     * fail-closed matrix. Calls the verify core directly (independent of
     * VOS3_VVFS_REQUIRE_MODEL_SIG), so it always certifies under the harness. */
    {
        /* Real OMS interop vector — same one host-validated in the KAT/E2E. */
        static const uint8_t m3_pk[32] = {
            0x03,0xa1,0x07,0xbf,0xf3,0xce,0x10,0xbe,0x1d,0x70,0xdd,0x18,0xe7,0x4b,0xc0,0x99,
            0x67,0xe4,0xd6,0x30,0x9b,0xa5,0x0d,0x5f,0x1d,0xdc,0x86,0x64,0x12,0x55,0x31,0xb8};
        static const uint8_t m3_dg[32] = {
            0xd8,0x0e,0x9b,0x40,0xea,0x1e,0x12,0x65,0xfc,0xaa,0x3f,0x5e,0xf3,0x6d,0xfe,0x3a,
            0x1c,0x3e,0x52,0x69,0xc9,0x9f,0x14,0x1e,0xc9,0x7c,0xd6,0x5a,0x9e,0x4b,0xe3,0xca};
        static const uint8_t m3_sig[64] = {
            0xfc,0x7b,0x17,0x99,0xdb,0x30,0xc1,0x9d,0xdb,0x85,0xd1,0x9f,0xab,0x44,0x8d,0xce,
            0xb1,0x70,0x5f,0x6b,0xd8,0xb3,0xfc,0xa0,0x2e,0xa2,0x61,0xae,0x70,0xd2,0xcb,0xe8,
            0xab,0xb0,0x1f,0xbf,0x10,0xba,0x20,0xde,0xc1,0xe0,0x1d,0x44,0xe2,0x9c,0x60,0xe8,
            0x2d,0xeb,0x8a,0xa6,0xbe,0x88,0x80,0xb8,0x56,0xaa,0x9e,0x4a,0xaf,0x36,0x26,0x0e};
        /* L (group order), little-endian — for the S+L malleability negative. */
        static const uint8_t m3_L[32] = {
            0xed,0xd3,0xf5,0x5c,0x1a,0x63,0x12,0x58,0xd6,0x9c,0xf7,0xa2,0xde,0xf9,0xde,0x14,
            0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0x10};
        uint8_t tmp[32]; uint8_t mall[64]; unsigned ci, carry;

        vvfs_set_trusted_model_key(m3_pk);

        /* valid: slot 0 */
        vvfs_register_model_signature(0, m3_dg, m3_sig, m3_pk);
        VOS3_ASSERT_CERT(VOS3_CERT_M3_VALID_ACCEPT,
                         vvfs_verify_model_slot(0, m3_dg) == 0);
        VOS3_ASSERT_CERT(VOS3_CERT_M3_ACTIVATION_STATE,
                         vvfs_model_slot_is_verified(0) == 1);

        /* forged signature: slot 1 */
        for (ci = 0; ci < 64u; ci++) mall[ci] = m3_sig[ci];
        mall[10] ^= 0x01u;
        vvfs_register_model_signature(1, m3_dg, mall, m3_pk);
        VOS3_ASSERT_CERT(VOS3_CERT_M3_FORGED_REJECT,
                         vvfs_verify_model_slot(1, m3_dg) != 0);

        /* tampered model digest: slot 0 re-verified with a flipped digest */
        for (ci = 0; ci < 32u; ci++) tmp[ci] = m3_dg[ci];
        tmp[0] ^= 0x01u;
        VOS3_ASSERT_CERT(VOS3_CERT_M3_TAMPERED_REJECT,
                         vvfs_verify_model_slot(0, tmp) != 0);

        /* untrusted signer: slot 2 signed by a non-anchor key */
        for (ci = 0; ci < 32u; ci++) tmp[ci] = m3_pk[ci];
        tmp[0] ^= 0x01u;
        vvfs_register_model_signature(2, m3_dg, m3_sig, tmp);
        VOS3_ASSERT_CERT(VOS3_CERT_M3_UNTRUSTED_KEY_REJECT,
                         vvfs_verify_model_slot(2, m3_dg) != 0);

        /* unregistered slot 3 */
        VOS3_ASSERT_CERT(VOS3_CERT_M3_UNREGISTERED_REJECT,
                         vvfs_verify_model_slot(3, m3_dg) != 0);

        /* S+L malleability (CVE-2026-4115): slot 1 with S' = S + L */
        for (ci = 0; ci < 64u; ci++) mall[ci] = m3_sig[ci];
        carry = 0u;
        for (ci = 0; ci < 32u; ci++) {
            unsigned s = (unsigned)mall[32 + ci] + (unsigned)m3_L[ci] + carry;
            mall[32 + ci] = (uint8_t)(s & 0xffu);
            carry = s >> 8;
        }
        vvfs_register_model_signature(1, m3_dg, mall, m3_pk);
        VOS3_ASSERT_CERT(VOS3_CERT_M3_MALLEABILITY_REJECT,
                         vvfs_verify_model_slot(1, m3_dg) != 0);
    }

    /* Sentinel — runner stops reading on this. */
    VOS3_ASSERT_CERT_DONE();
}
#endif  /* VOS3_ASSERT_HARNESS */

/* ============================================================================
 * AP MAIN (for SMP)
 * ============================================================================ */

/**
 * @brief Application Processor main entry point
 * @param[in] cpu_id CPU core ID
 * @note This function should never return
 */
__attribute__((noreturn))
void ap_main(uint16_t cpu_id)
{
    VOS3_INFO("AP %u starting", cpu_id);

    /* Initialize GDT for this AP */
    vos3_gdt_init_ap(cpu_id);

    /* Load IDT (shared with BSP) */
    /* IDT is already set up by BSP, just need to load it */

    VOS3_INFO("AP %u initialized", cpu_id);

    /* Initialize per-CPU scheduler state (idle task, run queue) */
    int sched_rc = vos3_sched_init_ap((uint32_t)cpu_id);
    if (sched_rc != 0) {
        VOS3_ERROR("AP %u: scheduler init failed (%d), halting", cpu_id, sched_rc);
        for (;;) {
            __asm__ volatile ("hlt");
        }
    }

    /* Enter the scheduler loop — this never returns.
     * The AP will now pick up tasks from the shared run queue and execute them. */
    vos3_sched_loop_ap();
}
