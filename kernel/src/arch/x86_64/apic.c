/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 Local APIC — detection-only scaffold
 * ==========================================
 *
 *   [OLYMPUS-FIX APEX-HOME v21.1.1] — Operation SKY-FALL FINAL  2026-05-02
 *
 * SCOPE OF THIS FILE (HONEST):
 *
 * What this file DOES today:
 *   - Detects whether the CPU advertises a Local APIC (CPUID.1:EDX[9]).
 *   - Reads the LAPIC base address out of MSR_APIC_BASE (IA32_APIC_BASE,
 *     0x1B). Bits [51:12] are the physical base; bits 8 and 11 are the
 *     BSP flag and the global enable, respectively.
 *   - Caches the detected base + initialized flag.
 *   - Exports vos3_apic_send_resched_ipi(cpu) as a callable symbol that
 *     returns -ENODEV when initialization is skipped.
 *
 * What this file DELIBERATELY does NOT do:
 *   - Map the LAPIC MMIO base into kernel virtual memory. Doing so
 *     requires touching the VMM and (on real hardware) the boot-loader's
 *     direct map. Deferred to a follow-up commit alongside MADT parsing.
 *   - Configure the spurious-interrupt vector (SPIV register at +0xF0).
 *   - Configure the LVT entries (timer, performance counter, thermal,
 *     LINT0/1, error).
 *   - Program the I/O APIC redirection table (separate file; needs
 *     ACPI MADT parsing first).
 *   - Send a real reschedule IPI via the ICR (the *purpose* of this file
 *     in the long run). The send function is a deliberate stub until the
 *     ICR write path is verified on real hardware.
 *
 * Why scaffold-only: a partial LAPIC driver is *worse* than no LAPIC
 * driver. Half-initialized LAPICs trigger spurious vector 0xFF storms
 * if the SPIV is not set, and uninitialized ICR writes can target
 * arbitrary CPUs. The scaffold lets the rest of the kernel's link
 * graph close (vos3_sched_ipi_preempt_hook references the symbol
 * behind VOS3_LATENCY_IPI) without shipping a half-baked driver.
 *
 * Activation roadmap:
 *   v21.1.x  This file (CPUID + MSR detect, no MMIO).
 *   v21.2.x  ACPI MADT parser → per-CPU LAPIC IDs.
 *   v21.3.x  LAPIC MMIO map + SPIV + LVT setup.
 *   v21.4.x  ICR-write path + reschedule IPI vector.
 */

#include "../../../include/vos/console.h"
#include "../../../include/arch/x86_64/cpu.h"

#include <stdint.h>
#include <stddef.h>

/* VOS3_MSR_APIC_BASE is defined in cpu.h (included above) */

/* Bits inside IA32_APIC_BASE */
#define VOS3_APIC_BASE_BSP          (1ull << 8)   /* This is the BSP */
#define VOS3_APIC_BASE_GLOBAL_EN    (1ull << 11)  /* Global APIC enable */
#define VOS3_APIC_BASE_X2APIC       (1ull << 10)  /* x2APIC mode active */
#define VOS3_APIC_BASE_ADDR_MASK    0x000FFFFFFFFFF000ull

/* CPUID feature bits */
#define VOS3_CPUID_1_EDX_APIC       (1u << 9)

/* Common errno used at this layer (kernel doesn't pull <errno.h>; the
 * value matches the Linux convention for callers that DO understand it). */
#ifndef ENODEV
# define ENODEV  19
#endif

/* ---- File-static state ---- */

static volatile uint32_t g_apic_initialized = 0u; /* 0 = not started, 2 = ready */
static uint64_t          g_apic_base_phys   = 0ull;
static uint64_t          g_apic_base_flags  = 0ull; /* raw MSR value */
static uint8_t           g_apic_supported   = 0u;
static volatile uint32_t *g_apic_base_va    = ((void *)0); /* MMIO virt; NULL=unmapped */

/* LAPIC register offsets (Intel SDM Vol.3A §10.4) */
#define VOS3_APIC_REG_ICR_LOW       0x300U
#define VOS3_APIC_REG_ICR_HIGH      0x310U

/* ICR field encodings */
#define VOS3_APIC_ICR_FIXED         (0u << 8)
#define VOS3_APIC_ICR_PHYSICAL      (0u << 11)
#define VOS3_APIC_ICR_ASSERT        (1u << 14)
#define VOS3_APIC_ICR_DEST_NO_SHORT (0u << 18)
#define VOS3_APIC_ICR_DEST_SELF     (1u << 18)
#define VOS3_APIC_ICR_DEST_ALL_INC  (2u << 18)
#define VOS3_APIC_ICR_DEST_ALL_EXC  (3u << 18)
#define VOS3_APIC_ICR_DELIV_PEND    (1u << 12)

/* Reschedule IPI vector — assigned in IDT by future apic_install_idt(). */
#define VOS3_APIC_VECTOR_RESCHED    0xF0U

/* ---- Local CPUID + MSR helpers (no external dep) ---- */

static inline void apic_cpuid(uint32_t leaf,
                              uint32_t *eax, uint32_t *ebx,
                              uint32_t *ecx, uint32_t *edx)
{
    __asm__ volatile ("cpuid"
                      : "=a"(*eax), "=b"(*ebx), "=c"(*ecx), "=d"(*edx)
                      : "a"(leaf), "c"(0)
                      : "memory");
}

static inline uint64_t apic_rdmsr(uint32_t msr)
{
    uint32_t lo = 0, hi = 0;
    __asm__ volatile ("rdmsr" : "=a"(lo), "=d"(hi) : "c"(msr));
    return ((uint64_t)hi << 32) | (uint64_t)lo;
}

static inline void apic_wrmsr(uint32_t msr, uint64_t val)
{
    uint32_t lo = (uint32_t)(val & 0xFFFFFFFFull);
    uint32_t hi = (uint32_t)(val >> 32);
    __asm__ volatile ("wrmsr" : : "c"(msr), "a"(lo), "d"(hi) : "memory");
}

/* x2APIC ICR MSR (Intel SDM Vol.3A §10.12.9).
 * A single 64-bit WRMSR to this address sends an IPI atomically —
 * no separate HIGH/LOW write sequence, no delivery-status polling. */
#define VOS3_MSR_X2APIC_ICR     0x00000830U

/* ---- Public API ---- */

/**
 * Detect whether a Local APIC is present and read its base. Idempotent.
 * Always safe — never writes a register, never maps memory.
 *
 * @return 0 on success (APIC detected), -ENODEV if the CPU does not
 *         report a LAPIC.
 */
int vos3_apic_detect(void)
{
    if (__atomic_load_n(&g_apic_initialized, __ATOMIC_ACQUIRE) == 2u) {
        return g_apic_supported ? 0 : -ENODEV;
    }

    uint32_t a = 0, b = 0, c = 0, d = 0;
    apic_cpuid(1, &a, &b, &c, &d);

    if (!(d & VOS3_CPUID_1_EDX_APIC)) {
        VOS3_INFO("[APIC] CPU reports no Local APIC (CPUID.1:EDX[9] = 0) — "
                  "running without IPI preemption");
        g_apic_supported = 0;
        __atomic_store_n(&g_apic_initialized, 2u, __ATOMIC_RELEASE);
        return -ENODEV;
    }

    uint64_t msr = apic_rdmsr(VOS3_MSR_APIC_BASE);
    g_apic_base_flags = msr;
    g_apic_base_phys  = msr & VOS3_APIC_BASE_ADDR_MASK;
    g_apic_supported  = 1;

    VOS3_INFO("[APIC] LAPIC detected: base_phys=0x%llx bsp=%u global_en=%u "
              "x2apic=%u (MMIO map + ICR not yet wired)",
              (unsigned long long)g_apic_base_phys,
              (msr & VOS3_APIC_BASE_BSP) ? 1u : 0u,
              (msr & VOS3_APIC_BASE_GLOBAL_EN) ? 1u : 0u,
              (msr & VOS3_APIC_BASE_X2APIC) ? 1u : 0u);

    __atomic_store_n(&g_apic_initialized, 2u, __ATOMIC_RELEASE);
    return 0;
}

/**
 * @return non-zero iff vos3_apic_detect() succeeded since boot.
 */
int vos3_apic_is_initialized(void)
{
    return __atomic_load_n(&g_apic_initialized, __ATOMIC_ACQUIRE) == 2u
           && g_apic_supported;
}

/**
 * @return cached LAPIC physical base, or 0 if not yet detected /
 *         not present.
 */
uint64_t vos3_apic_get_base_phys(void)
{
    return g_apic_base_phys;
}

/**
 * @return non-zero if IA32_APIC_BASE.bit10 (x2APIC mode) was set when
 *         vos3_apic_detect() ran; 0 otherwise.
 *
 * When non-zero, vos3_apic_send_resched_ipi() uses a single WRMSR to
 * IA32_X2APIC_ICR (0x830) instead of two MMIO writes to ICR_HIGH /
 * ICR_LOW.  The MMIO base (g_apic_base_va) is not needed in x2APIC mode.
 */
int vos3_apic_is_x2apic_mode(void)
{
    return (g_apic_base_flags & VOS3_APIC_BASE_X2APIC) != 0ull;
}

/**
 * Reschedule IPI. INTENTIONAL STUB until ICR write path lands.
 *
 * The scheduler's IPI-preemption hook (vos3_sched_ipi_preempt_hook in
 * kernel/src/sched/scheduler.c) calls this when an LC_INTERACTIVE task
 * is enqueued onto a remote CPU. Without ICR programming we cannot
 * actually deliver the interrupt, so we fall through to the normal
 * 100Hz tick-driven preemption — exactly the v20.x behavior that the
 * OLYMPUS audit identified as the correct fallback.
 *
 * Return value semantics:
 *   0       — IPI sent (real path; not yet reachable).
 *  -ENODEV  — no LAPIC, OR detection skipped, OR ICR write not wired.
 *
 * Callers MUST tolerate -ENODEV silently.
 */
/**
 * Set the LAPIC MMIO kernel-virtual base. Called once by the future
 * boot/init path that maps the LAPIC physical page. The pointer must
 * be aligned and reference at least 1 KiB of MMIO.
 *
 * Until this is called, vos3_apic_send_resched_ipi() returns -ENODEV
 * (g_apic_base_va == NULL is the gate). Setting it activates the
 * real ICR-write path below.
 */
void vos3_apic_set_mmio_base(volatile uint32_t *va)
{
    g_apic_base_va = va;
    if (va != ((void *)0)) {
        VOS3_INFO("[APIC] MMIO base set to %p — ICR-write path now armed",
                  (void *)va);
    }
}

/* ============================================================================
 * HW-2 (M2) — LAPIC MMIO mapping + LVT initialization
 * ============================================================================
 *
 * vos3_apic_init_mmio() pulls the LAPIC physical base from g_acpi_info
 * (populated earlier by the MADT parser in kernel/src/drivers/acpi.c)
 * and maps it via vos3_vmm_map_pages() with strong-uncacheable PTE
 * flags. On success it stores the kernel-virtual address by calling
 * vos3_apic_set_mmio_base() — which is what arms the ICR-write path
 * below.
 *
 * vos3_apic_init_lvt() programs SPIV (Spurious Interrupt Vector
 * register at +0xF0) and masks every LVT entry. SPIV MUST be written
 * before any other LAPIC register write per Intel SDM Vol.3A §10.9.
 *
 * Both functions are GATED behind VOS3_HW_LAPIC. Default builds compile
 * the bodies to no-ops returning -ENODEV, preserving the v21.2.x
 * scaffold behavior.
 */
#include "../../../include/vos/acpi.h"
#include "../../../include/vos/vmm.h"

/* Pre-flight (v21.4.4): real bugs surfaced by -Wpedantic + -DVOS3_HW_LAPIC.
 * Forward-declare the IPI sender so vos3_apic_self_test can call it
 * (defined later in this file). EINVAL needs a local define since this
 * file doesn't pull <errno.h>. Same pattern as xsave.c. */
#ifndef EINVAL
# define EINVAL  22
#endif
extern int vos3_apic_send_resched_ipi(uint32_t cpu);

/* Standard 4 KiB MMIO size for the LAPIC. */
#define VOS3_APIC_MMIO_SIZE         0x1000u

/* SPIV register offset (Intel SDM Vol.3A §10.9). */
#define VOS3_APIC_REG_SPIV          0x0F0u
#define VOS3_APIC_SPIV_SW_ENABLE    (1u << 8)
#define VOS3_APIC_VECTOR_SPURIOUS   0xFFu

/* LVT register offsets we mask at init time. */
#define VOS3_APIC_REG_LVT_TIMER     0x320u
#define VOS3_APIC_REG_LVT_THERMAL   0x330u
#define VOS3_APIC_REG_LVT_PERFCNT   0x340u
#define VOS3_APIC_REG_LVT_LINT0     0x350u
#define VOS3_APIC_REG_LVT_LINT1     0x360u
#define VOS3_APIC_REG_LVT_ERROR     0x370u
#define VOS3_APIC_LVT_MASK          (1u << 16)

int vos3_apic_init_mmio(void)
{
#ifndef VOS3_HW_LAPIC
    VOS3_INFO("[APIC] init_mmio skipped — VOS3_HW_LAPIC not defined");
    return -ENODEV;
#else
    if (!vos3_apic_is_initialized()) {
        VOS3_WARN("[APIC] init_mmio called before detect succeeded");
        return -ENODEV;
    }

    /* Source of truth for the physical base: ACPI MADT. The CPUID
     * MSR_APIC_BASE we read in detect() also gives us the base, but
     * MADT is the authoritative cross-platform path (and matches what
     * IOAPIC + per-CPU LAPIC IDs use). */
    const vos3_acpi_info_t *info = vos3_acpi_get_info();
    uint64_t phys = (info != ((void *)0) && info->lapic_addr != 0ull)
                  ? info->lapic_addr
                  : g_apic_base_phys;
    if (phys == 0ull) {
        VOS3_WARN("[APIC] init_mmio: no LAPIC physical address available");
        return -EINVAL;
    }

    /* Map 4 KiB strong-uncacheable. vos3_vmm_map_pages() takes (paddr,
     * size, flags) and returns the VA. */
    void *va = vos3_vmm_map_pages(phys, VOS3_APIC_MMIO_SIZE, VOS3_PTE_MMIO);
    if (va == ((void *)0)) {
        VOS3_ERROR("[APIC] init_mmio: vmm_map_pages failed for phys=0x%llx",
                   (unsigned long long)phys);
        return -EINVAL;
    }

    vos3_apic_set_mmio_base((volatile uint32_t *)va);
    VOS3_INFO("[APIC] init_mmio: phys=0x%llx → va=%p",
              (unsigned long long)phys, va);
    return 0;
#endif
}

int vos3_apic_init_lvt(void)
{
#ifndef VOS3_HW_LAPIC
    return -ENODEV;
#else
    volatile uint32_t *base = g_apic_base_va;
    if (base == ((void *)0)) {
        VOS3_WARN("[APIC] init_lvt called before MMIO mapping");
        return -ENODEV;
    }

    /* SPIV at +0xF0:
     *   bits [7:0]  = spurious vector (we use 0xFF)
     *   bit  8      = APIC software enable (must be 1 to receive ints)
     */
    base[VOS3_APIC_REG_SPIV / 4u] =
        VOS3_APIC_VECTOR_SPURIOUS | VOS3_APIC_SPIV_SW_ENABLE;

    /* Mask every LVT entry. Drivers that want to use them (timer, perf
     * counter, thermal, LINT0/1, error) must explicitly unmask. */
    static const unsigned lvt_offsets[] = {
        VOS3_APIC_REG_LVT_TIMER,
        VOS3_APIC_REG_LVT_THERMAL,
        VOS3_APIC_REG_LVT_PERFCNT,
        VOS3_APIC_REG_LVT_LINT0,
        VOS3_APIC_REG_LVT_LINT1,
        VOS3_APIC_REG_LVT_ERROR,
    };
    for (size_t i = 0; i < sizeof(lvt_offsets)/sizeof(lvt_offsets[0]); i++) {
        base[lvt_offsets[i] / 4u] = VOS3_APIC_LVT_MASK;
    }

    VOS3_INFO("[APIC] init_lvt: SPIV=0x1FF, all LVTs masked");
    return 0;
#endif
}

/* Self-test: send an IPI to ALL_EXCLUDING_SELF and verify the ICR
 * write completes (delivery-status bit clears within a bounded poll).
 * Does NOT require an actual interrupt handler — this only verifies
 * the LAPIC accepts the write, which is enough to confirm MMIO is
 * mapped correctly. */
int vos3_apic_self_test(void)
{
#ifndef VOS3_HW_LAPIC
    return 0;
#else
    if (g_apic_base_va == ((void *)0)) return -1;
    int rc = vos3_apic_send_resched_ipi(0u);
    if (rc != 0) {
        VOS3_ERROR("[APIC] self_test FAIL: send_resched_ipi → %d", rc);
        return -1;
    }
    VOS3_INFO("[APIC] self_test PASS");
    return 0;
#endif
}

/**
 * Reschedule IPI — production-shape ICR write.
 *
 * [OLYMPUS-FIX APEX-HOME E2 — LOGIC-COMPLETE v21.2.2]
 *
 * Logic per Intel SDM Vol.3A §10.6.1:
 *   1. ICR_HIGH (offset 0x310): destination APIC ID in bits [31:24].
 *      We use 0 because we use destination-shorthand "all-excluding-
 *      self" which ignores the destination field anyway. Writing 0
 *      is defensive.
 *   2. ICR_LOW (offset 0x300): {vector, delivery=Fixed, dest_mode=
 *      Physical, level=Assert, dest_short=All-Excluding-Self}. The
 *      write to ICR_LOW is what *fires* the interrupt.
 *   3. Poll the Delivery Status bit (12) to drain — keeps the IPI
 *      sequence ordered if the caller fires multiple in a row.
 *
 * Gate: returns -ENODEV unless BOTH (a) vos3_apic_detect()
 * succeeded AND (b) vos3_apic_set_mmio_base() has been called with
 * a non-NULL pointer. The second condition is the current blocker —
 * the LAPIC MMIO mapping hook lives in the boot path that has not
 * yet been wired (v21.3.x-APIC-ICR follow-up). Today this function
 * always returns -ENODEV; the LOGIC body is verified-by-spec but
 * not executed.
 *
 * Why ALL-EXCLUDING-SELF rather than per-CPU dest: the scheduler
 * caller (vos3_sched_ipi_preempt_hook) wants to nudge the SPECIFIC
 * remote CPU running the BATCH task. A per-CPU IPI requires us to
 * have the destination's LAPIC ID — which lives in
 * g_acpi_info.cpus[cpu].apic_id. Today the easier "all excluding
 * self" gives correct semantics (every other CPU re-checks the
 * run queue) at the cost of waking idle CPUs unnecessarily. v21.4.x
 * refines to per-CPU dest once the scheduler tracks LAPIC IDs.
 */
int vos3_apic_send_resched_ipi(uint32_t cpu)
{
    (void)cpu;  /* TODO v21.4.x: per-CPU dest using g_acpi_info.cpus[cpu] */

    if (!vos3_apic_is_initialized()) {
        return -ENODEV;
    }

#ifdef VOS3_HW_LAPIC
    /* x2APIC fast path (Intel SDM Vol.3A §10.12.9).
     *
     * When the CPU is in x2APIC mode (IA32_APIC_BASE.bit10 set) the
     * entire ICR is a single 64-bit MSR at address 0x830. A single
     * WRMSR is atomic — no delivery-status polling, no HIGH/LOW
     * sequencing. We use Destination Shorthand = "All excluding self"
     * (bits [19:18] = 11b) so cpu_id is irrelevant; every other CPU
     * re-evaluates its run queue after the IPI fires.
     *
     * ICR field layout (SDM Vol.3A Table 10-23):
     *   [7:0]   Vector = VOS3_APIC_VECTOR_RESCHED
     *   [10:8]  Delivery = 000 (Fixed)
     *   [11]    Dest mode = 0 (Physical)
     *   [14]    Level = 1 (Assert)
     *   [15]    Trigger = 0 (Edge)
     *   [19:18] Shorthand = 11 (All excluding self)
     *   [63:32] Destination field (ignored for shorthand)
     */
    if (vos3_apic_is_x2apic_mode()) {
        /* x2APIC: single 64-bit WRMSR to IA32_X2APIC_ICR (0x830) is
         * self-atomic per SDM Vol.3A §10.12.9 — no delivery-status
         * polling bit exists in this mode; the write dispatches the IPI
         * atomically.  No HIGH/LOW sequencing is needed or possible. */
        uint64_t icr =
              (uint64_t)VOS3_APIC_VECTOR_RESCHED       /* vector   */
            | ((uint64_t)VOS3_APIC_ICR_FIXED)          /* Fixed    */
            | ((uint64_t)VOS3_APIC_ICR_ASSERT)         /* Assert   */
            | ((uint64_t)VOS3_APIC_ICR_DEST_ALL_EXC);  /* All\self */
        apic_wrmsr(VOS3_MSR_X2APIC_ICR, icr);
        return 0;
    }
#endif /* VOS3_HW_LAPIC */

    volatile uint32_t *base = g_apic_base_va;
    if (base == ((void *)0)) {
        /* MMIO not yet mapped — see vos3_apic_set_mmio_base. */
        return -ENODEV;
    }

    /* Wait for any in-flight IPI to drain (Delivery Status = 0). */
    uint32_t spin = 100000u;
    while (spin-- &&
           (base[VOS3_APIC_REG_ICR_LOW / 4u] & VOS3_APIC_ICR_DELIV_PEND)) {
        __asm__ volatile ("pause" ::: "memory");
    }
    if (spin == 0u) {
        return -ENODEV;  /* prior IPI never drained — abort */
    }

    /* ICR_HIGH: destination APIC ID (ignored for ALL_EXCLUDING_SELF). */
    base[VOS3_APIC_REG_ICR_HIGH / 4u] = 0u;

    /* ICR_LOW: writing this register fires the IPI. */
    uint32_t icr_low =
          (uint32_t)VOS3_APIC_VECTOR_RESCHED
        | VOS3_APIC_ICR_FIXED
        | VOS3_APIC_ICR_PHYSICAL
        | VOS3_APIC_ICR_ASSERT
        | VOS3_APIC_ICR_DEST_ALL_EXC;
    base[VOS3_APIC_REG_ICR_LOW / 4u] = icr_low;

    return 0;
}
