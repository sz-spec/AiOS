/**
 * @file ai_guard.c
 * @brief VOS3 AI Memory Guard Implementation
 *
 * @details Implements protected memory allocation with red zone guard pages
 *          for AI workloads. Provides integrity verification and access monitoring.
 *
 * Task 4.1: Slimmed down — slot lifecycle moved to ai_slots.c,
 *   SQ/CQ/Doorbell to ai_sq.c, ISC/events to ai_isc.c,
 *   PTE/CRC32C/XXH3 to ai_pte.c.
 *
 * Remaining here:
 *   - Guard region allocation, red zones
 *   - vos3_ai_guard_init() (subsystem bootstrap)
 *   - Context create/destroy, alloc/free, find_region, is_guard_page
 *   - Checksum compute/verify, handle_fault, set_protection, get_stats
 *   - Quota management
 *   - Integrity automation, reprotect tick
 *   - NUMA allocation
 *   - Shared AI regions
 *   - Memory pressure handling
 *   - Per-app memory isolation
 *   - Model region scrub
 *   - vos3_simd_scrub_all(), scrub_zero_fill() (shared helpers)
 *   - g_l1d_flush_buf, g_cow_copy_buf (shared BSS buffers)
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "ai_guard_internal.h"
#include "../../include/vos/entropy.h"  /* Stage 8: CPUID-gated entropy_extract for AI-KASLR */

/* ============================================================================
 * v23.12: CPU SILICON HARDENING — SHARED BSP/AP HELPER
 * ============================================================================
 *
 * Enables UMIP, SMEP, SMAP, and WP on the CURRENT CPU.
 * Called from vos3_ai_guard_init() (BSP) and vos3_ap_entry() (APs).
 * Uses CPUID feature detection — safe to call on any x86_64 CPU.
 * ============================================================================ */

void vos3_cpu_harden_silicon(void)
{
    uint32_t eax7, ebx7, ecx7, edx7;
    vos3_cpuid(VOS3_CPUID_EXTENDED_FEAT, 0U, &eax7, &ebx7, &ecx7, &edx7);

    uint64_t cr4 = vos3_read_cr4();

    /* UMIP: Block SGDT/SIDT/SLDT/STR/SMSW from Ring 3 */
    if (ecx7 & VOS3_CPU_EXT7C_UMIP) {
        cr4 |= VOS3_CR4_UMIP;
        VOS3_DEBUG("[SILICON] UMIP enabled (CR4 bit 11)");
    }

    /* SMEP: Prevent kernel from executing user-mapped pages */
    if (ebx7 & VOS3_CPU_EXT7_SMEP) {
        cr4 |= VOS3_CR4_SMEP;
        VOS3_DEBUG("[SILICON] SMEP enabled (CR4 bit 20)");
    }

    /* SMAP: Prevent kernel from reading/writing user pages without stac/clac */
    if (ebx7 & VOS3_CPU_EXT7_SMAP) {
        cr4 |= VOS3_CR4_SMAP;
        VOS3_DEBUG("[SILICON] SMAP enabled (CR4 bit 21)");
    }

    vos3_write_cr4(cr4);

    /* WP: Enforce write protection in Ring 0 (required for COW) */
    uint64_t cr0 = vos3_read_cr0();
    cr0 |= (1ULL << 16);  /* CR0.WP */
    vos3_write_cr0(cr0);

    VOS3_INFO("[SILICON] CR4=0x%llx CR0.WP=1 — hardened",
              (unsigned long long)vos3_read_cr4());
}

/* ============================================================================
 * PHASE 4.2.11: ZERO-STATE SIMD SCRUB
 * ============================================================================ */

/**
 * @brief Phase 4.2.11: Zero-State SIMD scrub — wipe ALL vector registers.
 *
 * If AVX is available, vzeroall clears YMM0-15 (and XMM0-15 lower halves)
 * in a single instruction. Otherwise, pxor wipes all 16 XMM registers.
 * Prevents SIMD register leakage between slot tenants.
 */
void vos3_simd_scrub_all(void)
{
    /* Phase 4.2.16: Pre-fence — drain store buffer so all pending memory
     * writes from the outgoing agent are committed to cache before scrub. */
    __asm__ volatile("sfence" ::: "memory");

    if (g_cpu_has_avx) {
        /* vzeroall: zero YMM0-15 (clears XMM0-15 implicitly) */
        __asm__ volatile (".byte 0xC5, 0xFC, 0x77" ::: "memory");
    } else {
        /* SSE2 fallback: pxor all 16 XMM registers */
        __asm__ volatile (
            "pxor %%xmm0, %%xmm0\n\t"
            "pxor %%xmm1, %%xmm1\n\t"
            "pxor %%xmm2, %%xmm2\n\t"
            "pxor %%xmm3, %%xmm3\n\t"
            "pxor %%xmm4, %%xmm4\n\t"
            "pxor %%xmm5, %%xmm5\n\t"
            "pxor %%xmm6, %%xmm6\n\t"
            "pxor %%xmm7, %%xmm7\n\t"
            "pxor %%xmm8, %%xmm8\n\t"
            "pxor %%xmm9, %%xmm9\n\t"
            "pxor %%xmm10, %%xmm10\n\t"
            "pxor %%xmm11, %%xmm11\n\t"
            "pxor %%xmm12, %%xmm12\n\t"
            "pxor %%xmm13, %%xmm13\n\t"
            "pxor %%xmm14, %%xmm14\n\t"
            "pxor %%xmm15, %%xmm15\n\t"
            ::: "memory"
        );
    }

    /* Phase 4.2.16: Post-fence — ensure scrub is fully ordered before
     * the next agent begins execution. */
    __asm__ volatile("sfence" ::: "memory");
}

/**
 * @brief v19.8: Full FPU/SSE/AVX state scrub via XRSTOR with zeroed area.
 *
 * Loads a zeroed XSAVE image into the processor, clearing all FPU, SSE,
 * and AVX register state atomically.  This prevents any residual FPU/AVX
 * data from leaking between agent context switches.
 *
 * - Reads XCR0 at runtime via XGETBV (no dependency on static globals)
 * - Uses XRSTOR with a properly initialized XSAVE header (zeroed components)
 * - Falls back to FNINIT + vzeroall/pxor if XSAVE is unavailable
 * - Always calls vos3_simd_scrub_all() as a final belt-and-suspenders pass
 *
 * Must NOT be called from ISR context (uses stack-allocated XSAVE area).
 */
void vos3_fpu_scrub_full(void)
{
    __asm__ volatile("sfence" ::: "memory");

    /* Check if OS has enabled XSAVE (CPUID.01H:ECX bit 27 = OSXSAVE) */
    uint32_t ecx_feat;
    __asm__ volatile("cpuid" : "=c"(ecx_feat) : "a"(1) : "ebx", "edx");
    int has_osxsave = (ecx_feat >> 27) & 1;

    if (has_osxsave) {
        /* Read XCR0 to know which components are enabled */
        uint32_t xcr0_lo, xcr0_hi;
        __asm__ volatile("xgetbv" : "=a"(xcr0_lo), "=d"(xcr0_hi) : "c"(0));

        /*
         * Allocate a zeroed XSAVE area on the stack (1024 bytes, 64-aligned).
         * kzalloc would work too, but this avoids heap allocation in a hot path.
         * The XSAVE header at offset 512 must have XCOMP_BV=0 and XSTATE_BV=0
         * so that XRSTOR loads the "init" state for all components.
         *
         * Layout: 512 bytes legacy (x87+SSE) + 64 bytes XSAVE header + rest.
         * We zero the entire area, which sets XSTATE_BV=0 — telling XRSTOR
         * that no components have been modified, so it loads init values.
         *
         * NOTE: The legacy region (bytes 0-511) must have a valid x87 control
         * word and MXCSR, otherwise XRSTOR will #GP.
         */
        uint8_t __attribute__((aligned(64))) xsave_area[1024];
        for (int i = 0; i < 1024; i++)
            xsave_area[i] = 0;

        /* Set x87 FCW to 0x037F (default init value) at offset 0 */
        xsave_area[0] = 0x7F;
        xsave_area[1] = 0x03;

        /* Set MXCSR to 0x1F80 (default) at offset 24 */
        xsave_area[24] = 0x80;
        xsave_area[25] = 0x1F;

        /* XSTATE_BV at offset 512 stays 0 — XRSTOR will init all components */
        /* Phase 20: Verify XSTATE_BV == 0 before XRSTOR (defense-in-depth) */
        {
            uint64_t xstate_bv = 0;
            for (int b = 0; b < 8; b++)
                xstate_bv |= ((uint64_t)xsave_area[512 + b]) << (b * 8);
            if (xstate_bv != 0) {
                VOS3_ERROR("fpu_scrub: XSTATE_BV non-zero (0x%llx), forcing to 0",
                           (unsigned long long)xstate_bv);
                for (int b = 0; b < 8; b++)
                    xsave_area[512 + b] = 0;
            }
        }

        __asm__ volatile(
            "xrstor (%0)"
            :
            : "r"(xsave_area), "a"(xcr0_lo), "d"(xcr0_hi)
            : "memory"
        );

        /* Phase 20: Post-XRSTOR MXCSR verification */
        {
            uint32_t mxcsr_live;
            __asm__ volatile("stmxcsr %0" : "=m"(mxcsr_live));
            if (mxcsr_live != 0x1F80U) {
                VOS3_WARN("fpu_scrub: MXCSR=0x%x after XRSTOR (expected 0x1F80), fixing",
                          (unsigned)mxcsr_live);
                mxcsr_live = 0x1F80U;
                __asm__ volatile("ldmxcsr %0" :: "m"(mxcsr_live));
            }
        }
    } else {
        /* No XSAVE — manual FPU reset */
        __asm__ volatile("fninit" ::: "memory");
    }

    /* Belt-and-suspenders: also zero all vector registers explicitly */
    vos3_simd_scrub_all();
}

/* ============================================================================
 * SHARED BSS BUFFERS (used by ai_slots.c and ai_sq.c)
 * ============================================================================ */

/**
 * Phase 4.3.1: L1D Cache Sanitization buffer.
 * 32KB volatile array forces eviction of all prior L1D contents.
 * Also used by ai_sq.c for memcpy benchmark.
 */
volatile uint8_t g_l1d_flush_buf[32768] __attribute__((aligned(64)));

/* Phase 4.2.9: Static copy buffer for COW page duplication.
 * v23.5: Spinlock added for SMP-2 safety (D-LOW1 fix). */
uint8_t g_cow_copy_buf[4096] __attribute__((aligned(4096)));
vos3_spinlock_t g_cow_lock = VOS3_SPINLOCK_INIT;

/* ============================================================================
 * INTERNAL DATA
 * ============================================================================ */

/** @brief Global AI guard subsystem initialized flag */
static volatile int g_ai_guard_initialized = 0;

/* Phase 4.2.15: Global Neural Sync — shared read-only TSC page */
uint64_t  g_heartbeat_phys = 0;       /* Physical address of heartbeat page */
volatile uint64_t *g_heartbeat_ptr = NULL;  /* Virtual pointer for kernel writes */

/** @brief Global statistics */
static struct {
    uint64_t total_contexts;
    uint64_t total_regions;
    uint64_t total_allocs;
    uint64_t total_frees;
    uint64_t total_faults;
    uint64_t total_violations;
} g_ai_guard_stats = {0};

/** @brief Next region ID */
static uint32_t g_next_region_id = 1U;

/** @brief Next AI allocation virtual address (Phase 4.4: randomized at init) */
uintptr_t g_ai_alloc_next = 0xFFFF888100000000ULL;

/** @brief Phase 4.4: AI-KASLR recorded base for diagnostics */
uintptr_t g_ai_kaslr_base = 0;

/* ============================================================================
 * INTERNAL FUNCTIONS
 * ============================================================================ */

/**
 * @brief Simple FNV-1a hash for checksum
 */
static uint64_t fnv1a_hash(const uint8_t* data, size_t len)
{
    uint64_t hash = 14695981039346656037ULL;  /* FNV offset basis */
    const uint64_t prime = 1099511628211ULL;  /* FNV prime */

    for (size_t i = 0; i < len; i++) {
        hash ^= (uint64_t)data[i];
        hash *= prime;
    }

    return hash ^ VOS3_AI_CHECKSUM_SEED;
}

/**
 * @brief Allocate a new region structure
 */
static vos3_ai_guard_region_t* region_alloc(void)
{
    vos3_ai_guard_region_t* region = vos3_kzalloc(sizeof(vos3_ai_guard_region_t));
    if (region != NULL) {
        region->id = g_next_region_id++;
        region->state = VOS3_AI_STATE_FREE;
    }
    return region;
}

/**
 * @brief Free a region structure
 */
static void region_free(vos3_ai_guard_region_t* region)
{
    if (region != NULL) {
        vos3_kfree(region);
    }
}

/**
 * @brief Helper to get physical address from virtual
 */
static uintptr_t get_phys_addr(uintptr_t virt)
{
    uintptr_t phys = 0;
    if (vos3_vmm_virt_to_phys(virt, &phys) == 0) {
        return phys;
    }
    return 0;
}

/**
 * @brief Set PTE flags for AI guard pages
 */
static int set_ai_pte_flags(uintptr_t addr, size_t pages, uint64_t ai_flags)
{
    /* For each page, get current PTE and update AI flags directly */
    for (size_t i = 0; i < pages; i++) {
        uintptr_t page_addr = addr + (i * VOS3_PAGE_SIZE);

        /* Get current PTE and update AI flags in place */
        vos3_pte_t pte_val;
        if (vos3_vmm_get_pte(page_addr, &pte_val) != 0) {
            return VOS3_AI_GUARD_ERR_INVALID;
        }

        /* Clear old AI bits, apply new ones, preserve base mapping */
        vos3_pte_t desired = pte_val;
        desired &= ~VOS3_PTE_AI_MASK;
        desired |= (ai_flags & VOS3_PTE_AI_MASK);

        /* Apply protection changes (writable/read-only) */
        if (ai_flags & VOS3_PTE_WRITABLE) {
            desired |= VOS3_PTE_WRITABLE;
        } else {
            desired &= ~VOS3_PTE_WRITABLE;
        }

        /* K-C5: Atomic CAS */
        while (vos3_vmm_cas_pte(page_addr, &pte_val, desired) != 0) {
            desired = pte_val;
            desired &= ~VOS3_PTE_AI_MASK;
            desired |= (ai_flags & VOS3_PTE_AI_MASK);
            if (ai_flags & VOS3_PTE_WRITABLE) {
                desired |= VOS3_PTE_WRITABLE;
            } else {
                desired &= ~VOS3_PTE_WRITABLE;
            }
        }
    }

    return VOS3_AI_GUARD_OK;
}

/**
 * @brief Create guard pages (red zones) around a region
 */
static int create_guard_pages(uintptr_t addr, size_t size)
{
    /* Lower guard page */
    uintptr_t guard_lo = addr - VOS3_AI_RED_ZONE_SIZE;
    uintptr_t phys_lo = vos3_pmm_alloc(VOS3_PMM_FLAG_ZERO);
    if (phys_lo == 0) {
        return VOS3_AI_GUARD_ERR_NOMEM;
    }

    /* Map lower guard as present, read-only (no write) */
    int ret = vos3_vmm_map(guard_lo, phys_lo, (vos3_vmm_flags_t)0);
    if (ret == 0) {
        /* Apply AI guard page PTE bit */
        vos3_pte_t gpte;
        if (vos3_vmm_get_pte(guard_lo, &gpte) == 0) {
            vos3_pte_t desired = gpte | VOS3_PTE_AI_GUARD_PAGE;
            /* K-C5: Atomic CAS */
            while (vos3_vmm_cas_pte(guard_lo, &gpte, desired) != 0) {
                desired = gpte | VOS3_PTE_AI_GUARD_PAGE;
            }
        }
    }
    (void)0; /* ensure next statement parses */
    if (ret != 0) {
        vos3_pmm_free(phys_lo);
        return VOS3_AI_GUARD_ERR_NOMEM;
    }

    /* Upper guard page */
    uintptr_t guard_hi = addr + size;
    uintptr_t phys_hi = vos3_pmm_alloc(VOS3_PMM_FLAG_ZERO);
    if (phys_hi == 0) {
        vos3_vmm_unmap(guard_lo);
        vos3_pmm_free(phys_lo);
        return VOS3_AI_GUARD_ERR_NOMEM;
    }

    ret = vos3_vmm_map(guard_hi, phys_hi, (vos3_vmm_flags_t)0);
    if (ret == 0) {
        vos3_pte_t gpte_hi;
        if (vos3_vmm_get_pte(guard_hi, &gpte_hi) == 0) {
            vos3_pte_t desired = gpte_hi | VOS3_PTE_AI_GUARD_PAGE;
            /* K-C5: Atomic CAS */
            while (vos3_vmm_cas_pte(guard_hi, &gpte_hi, desired) != 0) {
                desired = gpte_hi | VOS3_PTE_AI_GUARD_PAGE;
            }
        }
    }
    (void)0;
    if (ret != 0) {
        vos3_pmm_free(phys_hi);
        vos3_vmm_unmap(guard_lo);
        vos3_pmm_free(phys_lo);
        return VOS3_AI_GUARD_ERR_NOMEM;
    }

    return VOS3_AI_GUARD_OK;
}

/**
 * @brief Remove guard pages
 */
static void remove_guard_pages(uintptr_t guard_lo, uintptr_t guard_hi)
{
    if (guard_lo != 0) {
        uintptr_t phys = get_phys_addr(guard_lo);
        vos3_vmm_unmap(guard_lo);
        if (phys != 0) {
            vos3_pmm_free(phys);
        }
    }

    if (guard_hi != 0) {
        uintptr_t phys = get_phys_addr(guard_hi);
        vos3_vmm_unmap(guard_hi);
        if (phys != 0) {
            vos3_pmm_free(phys);
        }
    }
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

int vos3_ai_guard_init(void)
{
    if (g_ai_guard_initialized) {
        return VOS3_AI_GUARD_OK;
    }

    /* Initialize global stats */
    g_ai_guard_stats.total_contexts = 0;
    g_ai_guard_stats.total_regions = 0;
    g_ai_guard_stats.total_allocs = 0;
    g_ai_guard_stats.total_frees = 0;
    g_ai_guard_stats.total_faults = 0;
    g_ai_guard_stats.total_violations = 0;

    g_next_region_id = 1U;

    /* v23.7: AI-KASLR redesigned — PUD-aligned (1GB) from generation.
     * Previous code used 2MB alignment → PUD round-up collapsed entropy to ~1 position.
     * New design: 32GB window, 1GB-aligned = 32 distinct PUD bases (5 bits entropy).
     *
     * Stage 8 fix: route entropy through the CPUID-gated entropy subsystem
     * instead of issuing a raw RDRAND inline-asm. The previous code faulted
     * with #UD on CPU models that don't expose RDRAND (e.g., QEMU's default
     * qemu64), because the asm dispatched the instruction *before* checking
     * CPUID. The vos3_entropy_extract() API performs the canonical
     *    RDSEED → RDRAND → ChaCha20-CSPRNG → RDTSC jitter
     * fallback hierarchy with proper feature gating, so this path is now
     * safe on every x86_64 silicon variant. */
    {
        uint64_t entropy = 0;
        int got_hw = 0;

        if (vos3_entropy_extract(&entropy, sizeof(entropy)) == 0) {
            got_hw = 1;
        } else {
            /* Defensive fallback: only reachable if vos3_entropy_extract
             * returns -1, which would mean the entropy subsystem is not
             * yet initialized — vanishingly unlikely under the normal
             * kmain init order (entropy_init runs before ai_guard_init). */
            uint32_t tsc_lo, tsc_hi;
            __asm__ volatile("rdtsc" : "=a"(tsc_lo), "=d"(tsc_hi));
            entropy = ((uint64_t)tsc_hi << 32) | tsc_lo;
            entropy ^= (entropy >> 17);
            entropy ^= (entropy << 13);
            entropy ^= (entropy >> 7);
        }

        /* 32GB window (0x800000000), 1GB-aligned (0x40000000) = 32 positions.
         * Mask: 0x7C0000000 selects bits [34:30] → 32 distinct 1GB slots. */
        uint64_t kaslr_offset = (entropy & 0x7C0000000ULL); /* 0..31GB, 1GB-aligned */
        g_ai_alloc_next = 0xFFFF888100000000ULL + kaslr_offset;
        g_ai_kaslr_base = g_ai_alloc_next;
        VOS3_INFO("[AI-GUARD] AI-KASLR v2: base=0x%llx (offset=0x%llx, %s)",
                  (unsigned long long)g_ai_alloc_next,
                  (unsigned long long)kaslr_offset,
                  got_hw ? "entropy-subsystem" : "TSC-fallback");
    }

    /* Phase 4.2.7+: Initialize event subscription table — all unsubscribed */
    for (int i = 0; i < 256; i++)
        g_event_subscriptions[i] = 0xFF;

    g_ai_guard_initialized = 1;

    /* Phase 4.2.15: Global Neural Sync — allocate heartbeat page */
    g_heartbeat_phys = vos3_pmm_alloc(0);   /* Single 4KB page */
    if (g_heartbeat_phys != 0) {
        /* Map at fixed VA: PRESENT | NX | read-only (no WRITE, no EXEC) */
        vos3_vmm_map(VOS3_HEARTBEAT_PAGE_VADDR, g_heartbeat_phys,
                     VOS3_VMM_FLAG_NONE);
        /* Phase 4.2.20: Set PWT (Page-Level Write-Through) for zero-jitter
         * heartbeat reads — removes cache latency from global neural sync. */
        {
            vos3_pte_t hb_pte;
            if (vos3_vmm_get_pte(VOS3_HEARTBEAT_PAGE_VADDR, &hb_pte) == 0) {
                vos3_pte_t desired = hb_pte | VOS3_PTE_WRITE_THROUGH;
                /* K-C5: Atomic CAS */
                while (vos3_vmm_cas_pte(VOS3_HEARTBEAT_PAGE_VADDR, &hb_pte, desired) != 0) {
                    desired = hb_pte | VOS3_PTE_WRITE_THROUGH;
                }
                vos3_vmm_invlpg(VOS3_HEARTBEAT_PAGE_VADDR);
            }
        }
        /* Kernel writes via PHYS_MAP_OFFSET identity map */
        g_heartbeat_ptr = (volatile uint64_t *)(0xFFFF800000000000ULL + g_heartbeat_phys);
        *g_heartbeat_ptr = vos3_timer_get_ticks();
        VOS3_INFO("[AI-GUARD] Heartbeat page at VA=0x%llx phys=0x%llx (PWT)",
                  (unsigned long long)VOS3_HEARTBEAT_PAGE_VADDR,
                  (unsigned long long)g_heartbeat_phys);

        /* Phase 4.4: Initialize Completion Ring in heartbeat page */
        cring_init();

        /* Phase 4.6: Initialize Submission Queue at offset 1024 */
        vos3_sq_init();

        /* Phase 4.7: Initialize Doorbell Matrix at offset 2048 */
        vos3_doorbell_init();
    }

    /* Phase 4.4: Detect SIMD features for optimized memcpy dispatch */
    ai_detect_simd_features();

    /* Phase 4.2.20 / v23.12: CPU silicon hardening (shared BSP/AP helper) */
    vos3_cpu_harden_silicon();

    vos3_console_printf("[AI-GUARD] Subsystem initialized\n");
    vos3_console_printf("[AI-GUARD] PTE bits: MONITORED=%d PROTECTED=%d GUARD=%d\n",
                        9, 10, 11);

    return VOS3_AI_GUARD_OK;
}

/* Forward declaration for registration */
static void register_global_ctx(vos3_ai_guard_ctx_t* ctx);

vos3_ai_guard_ctx_t* vos3_ai_guard_ctx_create(void)
{
    if (!g_ai_guard_initialized) {
        vos3_ai_guard_init();
    }

    vos3_ai_guard_ctx_t* ctx = vos3_kzalloc(sizeof(vos3_ai_guard_ctx_t));
    if (ctx == NULL) {
        return NULL;
    }

    ctx->lock = 0;
    ctx->regions = NULL;
    ctx->region_count = 0;
    ctx->total_protected = 0;
    ctx->total_faults = 0;
    ctx->total_violations = 0;
    ctx->flags = 0;
    ctx->max_regions = VOS3_AI_GUARD_MAX_REGIONS;

    /* Initialize quota fields (Phase 17.4) */
    ctx->quota_limit = 0;       /* 0 = unlimited */
    ctx->quota_used = 0;
    ctx->quota_enforce = 0;     /* Default: warn only */
    ctx->quota_violations = 0;

    __atomic_fetch_add(&g_ai_guard_stats.total_contexts, 1, __ATOMIC_RELAXED);

    /* Register as global context for integrity/pressure ticks (Phase 17.5) */
    register_global_ctx(ctx);

    return ctx;
}

void vos3_ai_guard_ctx_destroy(vos3_ai_guard_ctx_t* ctx)
{
    if (ctx == NULL) {
        return;
    }

    /* Free all regions */
    vos3_ai_guard_region_t* region = ctx->regions;
    while (region != NULL) {
        vos3_ai_guard_region_t* next = region->next;

        /* Remove guard pages if present */
        if (vos3_ai_guard_has_red_zones(region)) {
            remove_guard_pages(region->guard_lo, region->guard_hi);
        }

        /* Free the region memory */
        if (region->base != 0) {
            size_t pages = align_to_page(region->size) / VOS3_PAGE_SIZE;
            for (size_t i = 0; i < pages; i++) {
                uintptr_t addr = region->base + (i * VOS3_PAGE_SIZE);
                uintptr_t phys = get_phys_addr(addr);
                vos3_vmm_unmap(addr);
                if (phys != 0) {
                    vos3_pmm_free(phys);
                }
            }
        }

        region_free(region);
        region = next;
    }

    vos3_kfree(ctx);
    __atomic_fetch_sub(&g_ai_guard_stats.total_contexts, 1, __ATOMIC_RELAXED);
}

void* vos3_ai_guard_alloc(vos3_ai_guard_ctx_t* ctx,
                          size_t size,
                          vos3_ai_guard_type_t type,
                          vos3_ai_guard_flags_t flags)
{
    if (ctx == NULL || size == 0) {
        return NULL;
    }

    if (ctx->region_count >= ctx->max_regions) {
        vos3_console_printf("[AI-GUARD] Region limit reached (%zu)\n", ctx->max_regions);
        return NULL;
    }

    /* Align size to page boundary */
    size_t aligned_size = align_to_page(size);

    /* ===== Quota Enforcement (Phase 17.4) ===== */
    if (ctx->quota_limit > 0U) {
        if (ctx->quota_used + aligned_size > ctx->quota_limit) {
            ctx->quota_violations++;

            /* Fire quota exceeded alert */
            vos3_ai_alert_t alert = {
                .timestamp = vos3_timer_get_ticks(),
                .type = VOS3_AI_ALERT_QUOTA_EXCEEDED,
                .severity = (ctx->quota_enforce != 0U) ?
                    VOS3_AI_ALERT_ERROR : VOS3_AI_ALERT_WARNING,
                .region_id = 0U,
                .pid = 0U,
                .value1 = ctx->quota_used + aligned_size,
                .value2 = ctx->quota_limit,
            };
            vos3_ai_monitor_fire_alert(&alert);

            if (ctx->quota_enforce != 0U) {
                vos3_console_printf("[AI-GUARD] Quota exceeded: %llu + %zu > %llu (HARD)\n",
                                    (unsigned long long)ctx->quota_used,
                                    aligned_size,
                                    (unsigned long long)ctx->quota_limit);
                return NULL;  /* Hard limit - deny allocation */
            }

            vos3_console_printf("[AI-GUARD] Quota warning: %llu + %zu > %llu (SOFT)\n",
                                (unsigned long long)ctx->quota_used,
                                aligned_size,
                                (unsigned long long)ctx->quota_limit);
            /* Soft limit - allow but warned */
        }
    }
    size_t num_pages = aligned_size / VOS3_PAGE_SIZE;

    /* Calculate total size including guard pages */
    size_t total_size = aligned_size;
    if (flags & VOS3_AI_FLAG_RED_ZONES) {
        total_size += 2 * VOS3_AI_RED_ZONE_SIZE;  /* Lower + upper guards */
    }

    /* Allocate region structure */
    vos3_ai_guard_region_t* region = region_alloc();
    if (region == NULL) {
        return NULL;
    }

    /* Find virtual address space */
    uintptr_t base_addr;

    if (flags & VOS3_AI_FLAG_RED_ZONES) {
        base_addr = g_ai_alloc_next + VOS3_AI_RED_ZONE_SIZE;
    } else {
        base_addr = g_ai_alloc_next;
    }

    /* Allocate and map physical pages */
    for (size_t i = 0; i < num_pages; i++) {
        uintptr_t phys = vos3_pmm_alloc(VOS3_PMM_FLAG_ZERO);
        if (phys == 0) {
            /* Rollback on failure */
            for (size_t j = 0; j < i; j++) {
                uintptr_t addr = base_addr + (j * VOS3_PAGE_SIZE);
                uintptr_t p = get_phys_addr(addr);
                vos3_vmm_unmap(addr);
                vos3_pmm_free(p);
            }
            region_free(region);
            return NULL;
        }

        /* Map with appropriate VMM flags (NOT raw PTE flags!) */
        vos3_vmm_flags_t vmm_flags = 0;

        if (!(flags & VOS3_AI_FLAG_READ_ONLY)) {
            vmm_flags |= VOS3_VMM_FLAG_WRITE;
        }

        uintptr_t addr = base_addr + (i * VOS3_PAGE_SIZE);
        int ret = vos3_vmm_map(addr, phys, vmm_flags);
        if (ret == 0) {
            /* Apply AI-specific PTE bits after mapping */
            vos3_pte_t pte_val;
            if (vos3_vmm_get_pte(addr, &pte_val) == 0) {
                vos3_pte_t desired = pte_val | VOS3_PTE_AI_PROTECTED;
                if (flags & VOS3_AI_FLAG_MONITOR_ACCESS) {
                    desired |= VOS3_PTE_AI_MONITORED;
                }
                /* K-C5: Atomic CAS */
                while (vos3_vmm_cas_pte(addr, &pte_val, desired) != 0) {
                    desired = pte_val | VOS3_PTE_AI_PROTECTED;
                    if (flags & VOS3_AI_FLAG_MONITOR_ACCESS) {
                        desired |= VOS3_PTE_AI_MONITORED;
                    }
                }
            }
        }
        /* (ret check continues below) */
        (void)0; /* statement after label-like construct */
        if (ret != 0) {
            vos3_pmm_free(phys);
            for (size_t j = 0; j < i; j++) {
                uintptr_t a = base_addr + (j * VOS3_PAGE_SIZE);
                uintptr_t p = get_phys_addr(a);
                vos3_vmm_unmap(a);
                vos3_pmm_free(p);
            }
            region_free(region);
            return NULL;
        }
    }

    /* Create guard pages if requested */
    if (flags & VOS3_AI_FLAG_RED_ZONES) {
        int ret = create_guard_pages(base_addr, aligned_size);
        if (ret != 0) {
            /* Rollback */
            for (size_t i = 0; i < num_pages; i++) {
                uintptr_t addr = base_addr + (i * VOS3_PAGE_SIZE);
                uintptr_t phys = get_phys_addr(addr);
                vos3_vmm_unmap(addr);
                vos3_pmm_free(phys);
            }
            region_free(region);
            return NULL;
        }
        region->guard_lo = base_addr - VOS3_AI_RED_ZONE_SIZE;
        region->guard_hi = base_addr + aligned_size;
    }

    /* Fill in region structure */
    region->type = type;
    region->state = VOS3_AI_STATE_ACTIVE;
    region->flags = flags;
    region->base = base_addr;
    region->size = size;  /* Original requested size */
    region->checksum = 0;
    region->checksum_time = 0;

    /* Compute initial checksum if requested */
    if (flags & VOS3_AI_FLAG_CHECKSUMMED) {
        region->checksum = vos3_ai_guard_compute_checksum(region);
    }

    /* Add to context list */
    region->next = ctx->regions;
    ctx->regions = region;
    ctx->region_count++;
    ctx->total_protected += aligned_size;

    /* Update quota usage (Phase 17.4) */
    ctx->quota_used += aligned_size;

    /* Update next allocation address */
    g_ai_alloc_next += total_size;
    if (g_ai_alloc_next % VOS3_PAGE_SIZE != 0) {
        g_ai_alloc_next = align_to_page(g_ai_alloc_next);
    }

    /* Update global stats */
    __atomic_fetch_add(&g_ai_guard_stats.total_regions, 1, __ATOMIC_RELAXED);
    __atomic_fetch_add(&g_ai_guard_stats.total_allocs, 1, __ATOMIC_RELAXED);

    vos3_console_printf("[AI-GUARD] Allocated %s region: %p (%zu bytes)\n",
                        vos3_ai_guard_type_name(type), (void*)base_addr, size);

    return (void*)base_addr;
}

void vos3_ai_guard_free(vos3_ai_guard_ctx_t* ctx, void* addr)
{
    if (ctx == NULL || addr == NULL) {
        return;
    }

    uintptr_t target = (uintptr_t)addr;

    /* Find and remove region from list */
    vos3_ai_guard_region_t** pp = &ctx->regions;
    while (*pp != NULL) {
        vos3_ai_guard_region_t* region = *pp;

        if (region->base == target) {
            /* Remove from list */
            *pp = region->next;
            ctx->region_count--;
            ctx->total_protected -= align_to_page(region->size);

            /* Update quota usage (Phase 17.4) */
            if (ctx->quota_used >= align_to_page(region->size)) {
                ctx->quota_used -= align_to_page(region->size);
            } else {
                ctx->quota_used = 0;
            }

            /* Remove guard pages */
            if (vos3_ai_guard_has_red_zones(region)) {
                remove_guard_pages(region->guard_lo, region->guard_hi);
            }

            /* Free region memory */
            size_t pages = align_to_page(region->size) / VOS3_PAGE_SIZE;
            for (size_t i = 0; i < pages; i++) {
                uintptr_t page_addr = region->base + (i * VOS3_PAGE_SIZE);
                uintptr_t phys = get_phys_addr(page_addr);
                vos3_vmm_unmap(page_addr);
                if (phys != 0) {
                    vos3_pmm_free(phys);
                }
            }

            vos3_console_printf("[AI-GUARD] Freed region: %p\n", addr);

            region_free(region);
            __atomic_fetch_sub(&g_ai_guard_stats.total_regions, 1, __ATOMIC_RELAXED);
            __atomic_fetch_add(&g_ai_guard_stats.total_frees, 1, __ATOMIC_RELAXED);
            return;
        }

        pp = &region->next;
    }

    vos3_console_printf("[AI-GUARD] Warning: Free of unknown address %p\n", addr);
}

vos3_ai_guard_region_t* vos3_ai_guard_find_region(vos3_ai_guard_ctx_t* ctx,
                                                   uintptr_t addr)
{
    if (ctx == NULL) {
        return NULL;
    }

    vos3_ai_guard_region_t* region = ctx->regions;
    while (region != NULL) {
        /* Check if addr is within this region (including guards) */
        uintptr_t start = region->guard_lo ? region->guard_lo : region->base;
        uintptr_t end = region->guard_hi ? region->guard_hi + VOS3_AI_RED_ZONE_SIZE
                                         : region->base + align_to_page(region->size);

        if (addr >= start && addr < end) {
            return region;
        }

        region = region->next;
    }

    return NULL;
}

int vos3_ai_guard_is_guard_page(vos3_ai_guard_ctx_t* ctx, uintptr_t addr)
{
    if (ctx == NULL) {
        return 0;
    }

    vos3_ai_guard_region_t* region = ctx->regions;
    while (region != NULL) {
        if (vos3_ai_guard_has_red_zones(region)) {
            /* Check lower guard */
            if (addr >= region->guard_lo &&
                addr < region->guard_lo + VOS3_AI_RED_ZONE_SIZE) {
                return 1;
            }

            /* Check upper guard */
            if (addr >= region->guard_hi &&
                addr < region->guard_hi + VOS3_AI_RED_ZONE_SIZE) {
                return 1;
            }
        }
        region = region->next;
    }

    return 0;
}

uint64_t vos3_ai_guard_compute_checksum(vos3_ai_guard_region_t* region)
{
    if (region == NULL || region->base == 0) {
        return 0;
    }

    return fnv1a_hash((const uint8_t*)region->base, region->size);
}

int vos3_ai_guard_verify_integrity(vos3_ai_guard_region_t* region)
{
    if (region == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    if (!(region->flags & VOS3_AI_FLAG_CHECKSUMMED)) {
        /* No checksum to verify */
        return VOS3_AI_GUARD_OK;
    }

    uint64_t current = vos3_ai_guard_compute_checksum(region);

    if (current != region->checksum) {
        region->state = VOS3_AI_STATE_VIOLATED;
        region->stats.violation_count++;
        vos3_console_printf("[AI-GUARD] INTEGRITY VIOLATION: region %u (expected %llx, got %llx)\n",
                           region->id, (unsigned long long)region->checksum,
                           (unsigned long long)current);
        return VOS3_AI_GUARD_ERR_VIOLATED;
    }

    return VOS3_AI_GUARD_OK;
}

int vos3_ai_guard_handle_fault(vos3_ai_guard_ctx_t* ctx,
                                uintptr_t fault_addr,
                                uint64_t error_code)
{
    (void)error_code;  /* Unused for now */

    if (ctx == NULL) {
        return -1;
    }

    /* Check if fault is in a guard page */
    if (vos3_ai_guard_is_guard_page(ctx, fault_addr)) {
        vos3_ai_guard_region_t* region = vos3_ai_guard_find_region(ctx, fault_addr);

        if (region != NULL) {
            region->stats.fault_count++;
            region->state = VOS3_AI_STATE_VIOLATED;

            vos3_console_printf("[AI-GUARD] RED ZONE VIOLATION: addr=%p region=%u type=%s\n",
                               (void*)fault_addr, region->id,
                               vos3_ai_guard_type_name(region->type));

            ctx->total_faults++;
            __atomic_fetch_add(&g_ai_guard_stats.total_faults, 1, __ATOMIC_RELAXED);
        }

        /* Return 0 to indicate we handled it (by detecting the violation) */
        return 0;
    }

    /* Not our fault */
    return -1;
}

int vos3_ai_guard_set_protection(vos3_ai_guard_region_t* region,
                                  vos3_ai_guard_flags_t flags)
{
    if (region == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    /* Update PTE flags based on new protection */
    uint64_t pte_flags = VOS3_PTE_PRESENT | VOS3_PTE_NO_EXECUTE | VOS3_PTE_AI_PROTECTED;

    if (!(flags & VOS3_AI_FLAG_READ_ONLY)) {
        pte_flags |= VOS3_PTE_WRITABLE;
    }

    if (flags & VOS3_AI_FLAG_MONITOR_ACCESS) {
        pte_flags |= VOS3_PTE_AI_MONITORED;
    }

    size_t pages = align_to_page(region->size) / VOS3_PAGE_SIZE;
    int ret = set_ai_pte_flags(region->base, pages, pte_flags);

    if (ret == VOS3_AI_GUARD_OK) {
        region->flags = flags;
    }

    return ret;
}

int vos3_ai_guard_get_stats(vos3_ai_guard_region_t* region,
                             vos3_ai_guard_stats_t* stats)
{
    if (region == NULL || stats == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    *stats = region->stats;
    return VOS3_AI_GUARD_OK;
}

/* ============================================================================
 * QUOTA MANAGEMENT (Phase 17.4)
 * ============================================================================ */

int vos3_ai_guard_set_quota(vos3_ai_guard_ctx_t* ctx,
                             uint64_t limit,
                             int enforce)
{
    if (ctx == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    ctx->quota_limit = limit;
    ctx->quota_enforce = (enforce != 0) ? 1U : 0U;

    vos3_console_printf("[AI-GUARD] Quota set: limit=%llu enforce=%s\n",
                        (unsigned long long)limit,
                        enforce ? "HARD" : "SOFT");

    return VOS3_AI_GUARD_OK;
}

int vos3_ai_guard_get_quota(vos3_ai_guard_ctx_t* ctx,
                             uint64_t* limit,
                             uint64_t* used)
{
    if (ctx == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    if (limit != NULL) {
        *limit = ctx->quota_limit;
    }

    if (used != NULL) {
        *used = ctx->quota_used;
    }

    return VOS3_AI_GUARD_OK;
}

uint64_t vos3_ai_guard_quota_available(vos3_ai_guard_ctx_t* ctx)
{
    if (ctx == NULL) {
        return 0;
    }

    if (ctx->quota_limit == 0U) {
        /* No limit set - return max */
        return UINT64_MAX;
    }

    if (ctx->quota_used >= ctx->quota_limit) {
        return 0;
    }

    return ctx->quota_limit - ctx->quota_used;
}

/* ============================================================================
 * WORKLOAD HINTS (Sprint 15 / Item A1)
 *
 * The kernel's default page-table policy assumes a typical desktop workload:
 * pages start anonymous, are RW, are eligible for swap once cold. For AI
 * workloads, those defaults are wrong:
 *
 *   MODEL_WEIGHTS  — long-lived, read-only after load, 10-100 GB ranges.
 *                    Treat as if MAP_POPULATE + MADV_HUGEPAGE + MADV_DONTNEED
 *                    were set: pre-fault on bind (commits RSS), mark
 *                    read-only via the existing protection-management path,
 *                    and disable swap eligibility (these pages MUST stay
 *                    resident; paging out a 70 GB model out is fatal to
 *                    inference SLA).
 *
 *   KV_CACHE       — short-lived, RW, attention-hot. Small pages (4 KiB)
 *                    are better than large pages here because eviction is
 *                    fine-grained. Swap-eligible (the slot manager handles
 *                    KV LRU separately at the slot abstraction; the kernel
 *                    only needs to NOT pin them).
 *
 *   SCRATCH        — default — intermediate buffers, normalisation tensors.
 *                    Regular RSS accounting; no special policy.
 *
 * Source: https://arxiv.org/pdf/2508.00604 (Composable OS Kernel
 * Architectures for Autonomous Intelligence)
 *
 * Note: this routine is **idempotent + cheap** — it only mutates page-table
 * bits or swap_hint if the new hint differs from the existing one. No
 * region-wide page walk on no-op transitions.
 * ============================================================================ */

int vos3_ai_guard_set_workload_hint(vos3_ai_guard_region_t* region,
                                     vos3_ai_workload_hint_t hint)
{
    if (region == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }
    if (region->state != VOS3_AI_STATE_ACTIVE) {
        return VOS3_AI_GUARD_ERR_PERM;  /* state-precondition violation */
    }

    /* Idempotent — same hint already applied is a no-op. We check both
     * the hint enum value AND the hint_pinned_rss flag because a region
     * may have its hint set but the pre-fault not yet performed (e.g.
     * when set during region creation but before the pages are mapped
     * in). */
    uint32_t expected_pinned =
        (hint == VOS3_AI_HINT_MODEL_WEIGHTS) ? 1U : 0U;
    if (region->workload_hint == hint &&
        region->hint_pinned_rss == expected_pinned) {
        return VOS3_AI_GUARD_OK;
    }

    region->workload_hint = hint;

    switch (hint) {
    case VOS3_AI_HINT_MODEL_WEIGHTS:
        /* Pre-fault the entire range so RSS commits up front. We do this
         * by touching one byte per 4 KiB page; the page-fault path will
         * map them in. We also flip swap_hint to "pinned" so the eviction
         * scanner skips this region under quota pressure.
         *
         * Why touch instead of issuing a hypercall? We're already in a
         * kernel context that owns these pages — a single byte read per
         * page is faster than the IPI cost of a population request, and
         * it's exactly what MAP_POPULATE does on the Linux side.
         */
        if (region->base != 0 && region->size > 0) {
            volatile uint8_t* p = (volatile uint8_t*)region->base;
            size_t i;
            for (i = 0; i < region->size; i += 4096U) {
                (void)p[i];  /* read-only touch — does not dirty the page */
            }
            region->hint_pinned_rss = 1U;
        }
        region->swap_hint = VOS3_AI_SWAP_PINNED;
        vos3_console_printf(
            "[AI-GUARD] region=%u hint=MODEL_WEIGHTS pre-faulted RSS=%zuB\n",
            region->id, region->size);
        break;

    case VOS3_AI_HINT_KV_CACHE:
        /* Keep small pages; mark swap-eligible. The slot-manager-level KV
         * LRU handles which slots get evicted; the kernel just needs to
         * NOT pin these pages (the SCRATCH default keeps swap_hint at the
         * region's existing value, which may have been left pinned by a
         * prior MODEL_WEIGHTS pass — explicit reset is the safe call). */
        region->swap_hint = VOS3_AI_SWAP_ALLOWED;
        region->hint_pinned_rss = 0U;
        vos3_console_printf(
            "[AI-GUARD] region=%u hint=KV_CACHE swap=normal pages=4KiB\n",
            region->id);
        break;

    case VOS3_AI_HINT_SCRATCH:
    default:
        /* Default policy — no special behavior. Reset any prior pinning
         * so the region's pages are reclaim-eligible like normal anon. */
        region->swap_hint = VOS3_AI_SWAP_ALLOWED;
        region->hint_pinned_rss = 0U;
        break;
    }

    return VOS3_AI_GUARD_OK;
}

int vos3_ai_guard_get_workload_hint(vos3_ai_guard_region_t* region,
                                     vos3_ai_workload_hint_t* hint_out)
{
    if (region == NULL || hint_out == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }
    *hint_out = region->workload_hint;
    return VOS3_AI_GUARD_OK;
}

/* ============================================================================
 * PHASE 17.5.1: INTEGRITY AUTOMATION
 * ============================================================================ */

/** @brief Global list of all contexts (for integrity tick) */
vos3_ai_guard_ctx_t* g_global_ctx = NULL;

/** @brief Provide global context for telemetry */
vos3_ai_guard_ctx_t* vos3_ai_guard_get_global_ctx(void)
{
    return g_global_ctx;
}

int vos3_ai_guard_set_integrity_auto(vos3_ai_guard_region_t* region,
                                      uint32_t interval, int auto_suspend)
{
    if (region == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    region->integrity_config.verify_interval = interval;
    region->integrity_config.auto_suspend = (auto_suspend != 0) ? 1U : 0U;
    region->integrity_config.retry_count = 3U;  /* Default retry count */

    if (interval > 0U) {
        /* Schedule first verification */
        uint64_t now = vos3_timer_get_ticks();
        region->next_verify_tick = now + interval;
        region->last_verify_tick = now;

        /* Compute initial checksum if not already set */
        if (region->checksum == 0U &&
            (region->flags & VOS3_AI_FLAG_CHECKSUMMED)) {
            region->checksum = vos3_ai_guard_compute_checksum(region);
            region->checksum_time = now;
        }

        vos3_console_printf("[AI-GUARD] Integrity auto enabled: region=%u interval=%u\n",
                            region->id, interval);
    } else {
        region->next_verify_tick = 0;
        vos3_console_printf("[AI-GUARD] Integrity auto disabled: region=%u\n",
                            region->id);
    }

    return VOS3_AI_GUARD_OK;
}

int vos3_ai_guard_get_integrity_config(vos3_ai_guard_region_t* region,
                                        vos3_ai_integrity_config_t* config)
{
    if (region == NULL || config == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    *config = region->integrity_config;
    return VOS3_AI_GUARD_OK;
}

void vos3_ai_guard_integrity_tick(uint64_t current_tick)
{
    vos3_ai_guard_ctx_t* ctx = g_global_ctx;
    if (ctx == NULL) {
        return;
    }

    /* Iterate all regions in global context */
    vos3_ai_guard_region_t* region = ctx->regions;
    while (region != NULL) {
        /* Skip if not scheduled or suspended */
        if (region->integrity_config.verify_interval == 0U ||
            region->state == VOS3_AI_STATE_SUSPENDED) {
            region = region->next;
            continue;
        }

        /* Check if verification is due */
        if (current_tick >= region->next_verify_tick) {
            /* Perform integrity verification */
            int result = vos3_ai_guard_verify_integrity(region);

            if (result == VOS3_AI_GUARD_OK) {
                vos3_console_printf("[AI-GUARD] Integrity check passed: region=%u\n",
                                    region->id);
            } else {
                /* Fire integrity failure alert */
                vos3_ai_alert_t alert = {
                    .timestamp = current_tick,
                    .type = VOS3_AI_ALERT_INTEGRITY_FAIL,
                    .severity = VOS3_AI_ALERT_CRITICAL,
                    .region_id = region->id,
                    .pid = 0U,
                    .value1 = region->checksum,
                    .value2 = vos3_ai_guard_compute_checksum(region),
                };
                vos3_ai_monitor_fire_alert(&alert);

                if (region->integrity_config.auto_suspend != 0U) {
                    region->state = VOS3_AI_STATE_SUSPENDED;
                    vos3_console_printf("[AI-GUARD] Region %u auto-suspended on integrity fail\n",
                                        region->id);
                }
            }

            /* Schedule next verification */
            region->last_verify_tick = current_tick;
            region->next_verify_tick = current_tick +
                                        region->integrity_config.verify_interval;
        }

        region = region->next;
    }

    /* Phase 8.6: Weight Purity Guard — incremental SHA-256 scan */
    vos3_wpg_tick(current_tick);
}

/* ============================================================================
 * PHASE 8.6: WEIGHT PURITY GUARD (WPG) — BACKGROUND SHA-256 VALIDATOR
 * ============================================================================
 *
 * Incrementally hashes all HugePages of active model slots using SHA-256.
 * Each timer tick processes VOS3_WPG_CHUNK_SIZE bytes.  When a full scan
 * completes, the resulting digest is compared against the reference hash
 * that was captured when the slot transitioned to ACTIVE.
 *
 * On mismatch (bit-flip / corruption): slot → CORRUPT, alert fired.
 *
 * Lock-free design: reads slot status atomically, accesses read-only
 * HugePage data (ACTIVE slots have WRITABLE cleared from PTEs).  Safe
 * for timer-ISR context.
 * ============================================================================ */

void vos3_wpg_arm(uint8_t slot_id)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) {
        return;
    }
    vos3_ai_model_slot_t *slot = &g_model_slots[slot_id];

    /* Only arm for slots with actual data */
    if (slot->size == 0 || slot->base == 0) {
        return;
    }

    /* Compute full SHA-256 of the slot's weight data */
    vos3_sha256_ctx_t ctx;
    vos3_sha256_init(&ctx);

    const uint8_t *data = (const uint8_t *)slot->base;
    size_t remaining = slot->size;
    size_t offset = 0;

    while (remaining > 0) {
        size_t chunk = (remaining > 8192) ? 8192 : remaining;
        vos3_sha256_update(&ctx, data + offset, chunk);
        offset    += chunk;
        remaining -= chunk;
    }

    vos3_sha256_final(&ctx, slot->wpg_reference_hash);

    /* Initialize WPG scan state */
    slot->wpg_scan_offset    = 0;
    slot->wpg_scan_total     = (uint32_t)slot->size;
    slot->wpg_violations     = 0;
    slot->wpg_last_verify_tick = 0;
    slot->wpg_scans_completed  = 0;
    slot->wpg_enabled        = 1;

    VOS3_INFO("[WPG] Armed slot %u: size=%u SHA-256=%02x%02x%02x%02x...",
              slot_id, slot->wpg_scan_total,
              slot->wpg_reference_hash[0], slot->wpg_reference_hash[1],
              slot->wpg_reference_hash[2], slot->wpg_reference_hash[3]);
}

/**
 * @brief Per-slot incremental WPG scan.  Returns 1 if a full scan just
 *        completed (digest comparison needed), 0 otherwise.
 */
static int wpg_scan_slot(vos3_ai_model_slot_t *slot)
{
    /* Static SHA-256 contexts — one per slot.  Using file-scope statics
     * avoids adding a ~112-byte vos3_sha256_ctx_t to the already-large
     * slot struct, and there are only VOS3_MODEL_SLOT_MAX (4) slots. */
    static vos3_sha256_ctx_t wpg_ctx[VOS3_MODEL_SLOT_MAX];

    uint8_t sid = slot->slot_id;

    /* First chunk of a new scan cycle → initialize SHA-256 context */
    if (slot->wpg_scan_offset == 0) {
        vos3_sha256_init(&wpg_ctx[sid]);
    }

    const uint8_t *base = (const uint8_t *)slot->base;
    uint32_t left = slot->wpg_scan_total - slot->wpg_scan_offset;
    uint32_t chunk = (left > VOS3_WPG_CHUNK_SIZE) ? VOS3_WPG_CHUNK_SIZE : left;

    vos3_sha256_update(&wpg_ctx[sid], base + slot->wpg_scan_offset, chunk);
    slot->wpg_scan_offset += chunk;

    if (slot->wpg_scan_offset >= slot->wpg_scan_total) {
        /* Full scan complete — finalize and compare */
        uint8_t digest[32];
        vos3_sha256_final(&wpg_ctx[sid], digest);

        /* Reset for next cycle */
        slot->wpg_scan_offset = 0;
        return memcmp(digest, slot->wpg_reference_hash, 32) != 0;
    }

    return 0;  /* scan in progress */
}

void vos3_wpg_tick(uint64_t current_tick)
{
    for (uint8_t i = 0; i < VOS3_MODEL_SLOT_MAX; i++) {
        vos3_ai_model_slot_t *slot = &g_model_slots[i];

        /* Skip slots without WPG enabled */
        if (!slot->wpg_enabled) {
            continue;
        }

        /* Only scan ACTIVE or WARM slots — data is read-only and stable.
         * STREAMING slots are being written to, SUSPENDED/CORRUPT already handled. */
        vos3_model_slot_status_t st =
            (vos3_model_slot_status_t)__atomic_load_n((uint32_t *)&slot->status,
                                                       __ATOMIC_ACQUIRE);
        if (st != VOS3_SLOT_ACTIVE && st != VOS3_SLOT_WARM) {
            continue;
        }

        /* Run one scan chunk */
        int corrupted = wpg_scan_slot(slot);

        if (corrupted) {
            /* HALL_LOCKDOWN: bit-flip detected */
            slot->wpg_violations++;
            slot->status = VOS3_SLOT_CORRUPT;
            slot->wpg_enabled = 0;  /* stop further scanning of corrupt slot */

            vos3_ai_alert_t alert = {
                .timestamp  = current_tick,
                .type       = VOS3_AI_ALERT_INTEGRITY_FAIL,
                .severity   = VOS3_AI_ALERT_CRITICAL,
                .region_id  = (uint32_t)i,
                .pid        = slot->owner_tid,
                .value1     = slot->wpg_violations,
                .value2     = slot->wpg_scans_completed,
            };
            vos3_ai_monitor_fire_alert(&alert);

            VOS3_WARN("[WPG] HALL_LOCKDOWN: Slot %u SHA-256 mismatch! "
                      "violations=%u scans_completed=%llu — slot set to CORRUPT",
                      i, slot->wpg_violations,
                      (unsigned long long)slot->wpg_scans_completed);
        } else if (slot->wpg_scan_offset == 0) {
            /* A full cycle just completed successfully (offset was reset to 0) */
            slot->wpg_scans_completed++;
            slot->wpg_last_verify_tick = current_tick;
        }
    }
}

/* ============================================================================
 * PHASE F: MONITOR REPROTECT TICK
 * ============================================================================ */

void vos3_ai_guard_reprotect_tick(void)
{
    vos3_ai_guard_ctx_t* ctx = g_global_ctx;
    if (ctx == NULL) {
        return;
    }

    vos3_ai_guard_region_t* region = ctx->regions;
    while (region != NULL) {
        if (region->needs_reprotect != 0U) {
            /* Remove WRITABLE from each page in the region */
            size_t pages = align_to_page(region->size) / VOS3_PAGE_SIZE;
            for (size_t i = 0; i < pages; i++) {
                uintptr_t page_addr = region->base + (i * VOS3_PAGE_SIZE);
                /* AG-B fix: Re-map as read-only but keep USER bit so
                 * user-space can still access the page.  VOS3_VMM_FLAG_NONE
                 * strips USER, making the page kernel-only. */
                vos3_vmm_update_flags(page_addr,
                    (vos3_vmm_flags_t)(VOS3_VMM_FLAG_USER));
            }
            region->needs_reprotect = 0U;
        }
        region = region->next;
    }
}

/* ============================================================================
 * PHASE 17.5.2: NUMA-AWARE ALLOCATION
 * ============================================================================ */

#include "../../include/vos/numa.h"

void* vos3_ai_guard_alloc_numa(vos3_ai_guard_ctx_t* ctx, size_t size,
                                vos3_ai_guard_type_t type,
                                vos3_ai_guard_flags_t flags,
                                vos3_numa_node_t numa_node)
{
    if (ctx == NULL || size == 0) {
        return NULL;
    }

    /* Perform standard allocation first */
    void* addr = vos3_ai_guard_alloc(ctx, size, type,
                                      flags | VOS3_AI_FLAG_NUMA_LOCAL);
    if (addr == NULL) {
        return NULL;
    }

    /* Find the region we just created */
    vos3_ai_guard_region_t* region = vos3_ai_guard_find_region(ctx,
                                                                (uintptr_t)addr);
    if (region != NULL) {
        region->numa_node = numa_node;

        vos3_console_printf("[AI-GUARD] Allocated on NUMA node %u: %p (%zu bytes)\n",
                            numa_node, addr, size);
    }

    return addr;
}

int vos3_ai_guard_numa_stats(vos3_ai_guard_ctx_t* ctx, uint64_t* per_node)
{
    if (ctx == NULL || per_node == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    /* Initialize to zero */
    for (uint32_t i = 0; i < VOS3_NUMA_MAX_NODES; i++) {
        per_node[i] = 0;
    }

    /* Sum up memory per node */
    vos3_ai_guard_region_t* region = ctx->regions;
    while (region != NULL) {
        vos3_numa_node_t node = region->numa_node;
        if (node < VOS3_NUMA_MAX_NODES) {
            per_node[node] += region->size;
        }
        region = region->next;
    }

    return VOS3_AI_GUARD_OK;
}

/* ============================================================================
 * PHASE 17.5.3: SHARED AI REGIONS
 * ============================================================================ */

/** @brief Global shared region registry */
static vos3_ai_shared_region_t* g_shared_regions = NULL;
static vos3_spinlock_t g_shared_lock = VOS3_SPINLOCK_INIT;

static inline void shared_lock(void)
{
    vos3_spinlock_acquire(&g_shared_lock);
}

static inline void shared_unlock(void)
{
    vos3_spinlock_release(&g_shared_lock);
}

vos3_ai_shared_region_t* vos3_ai_guard_create_shared(
    vos3_ai_guard_ctx_t* ctx, size_t size,
    vos3_ai_guard_type_t type, vos3_ai_guard_flags_t flags)
{
    if (ctx == NULL || size == 0) {
        return NULL;
    }

    /* Allocate shared region structure */
    vos3_ai_shared_region_t* shared = vos3_kzalloc(sizeof(vos3_ai_shared_region_t));
    if (shared == NULL) {
        return NULL;
    }

    /* Allocate the actual memory */
    void* addr = vos3_ai_guard_alloc(ctx, size, type, flags | VOS3_AI_FLAG_SHARED);
    if (addr == NULL) {
        vos3_kfree(shared);
        return NULL;
    }

    /* Find the region */
    vos3_ai_guard_region_t* region = vos3_ai_guard_find_region(ctx, (uintptr_t)addr);
    if (region == NULL) {
        vos3_ai_guard_free(ctx, addr);
        vos3_kfree(shared);
        return NULL;
    }

    /* Initialize shared structure */
    shared->region_id = region->id;
    shared->ref_count = 1U;
    shared->max_refs = VOS3_AI_SHARED_MAX_TASKS;
    shared->flags = flags;
    shared->total_accesses = 0;
    shared->lock = 0;
    shared->base = (uintptr_t)addr;
    shared->size = size;
    shared->type = (uint8_t)type;
    shared->numa_node = region->numa_node;

    /* First reference is the creator */
    shared->refs[0].pid = 0;  /* TODO: Get current PID */
    shared->refs[0].ctx = ctx;
    shared->refs[0].map_time = vos3_timer_get_ticks();
    shared->refs[0].access_flags = flags;

    /* Link region to shared */
    region->shared = shared;
    region->is_owner = 1U;

    /* Add to global registry */
    shared_lock();
    shared->next = g_shared_regions;
    g_shared_regions = shared;
    shared_unlock();

    vos3_console_printf("[AI-GUARD] Created shared region: id=%u size=%zu ref_count=1\n",
                        shared->region_id, size);

    return shared;
}

vos3_ai_guard_region_t* vos3_ai_guard_map_shared(
    vos3_ai_shared_region_t* shared,
    vos3_ai_guard_ctx_t* ctx, uint32_t access_flags)
{
    if (shared == NULL || ctx == NULL) {
        return NULL;
    }

    shared_lock();

    /* Check if we can add another reference */
    if (shared->ref_count >= shared->max_refs) {
        shared_unlock();
        vos3_console_printf("[AI-GUARD] Shared region %u: max refs reached\n",
                            shared->region_id);
        return NULL;
    }

    /* Create a local region descriptor (doesn't allocate new memory) */
    vos3_ai_guard_region_t* region = vos3_kzalloc(sizeof(vos3_ai_guard_region_t));
    if (region == NULL) {
        shared_unlock();
        return NULL;
    }

    /* Copy info from shared */
    region->id = shared->region_id;
    region->type = (vos3_ai_guard_type_t)shared->type;
    region->state = VOS3_AI_STATE_ACTIVE;
    region->flags = (vos3_ai_guard_flags_t)(shared->flags | VOS3_AI_FLAG_SHARED);
    region->base = shared->base;
    region->size = shared->size;
    region->shared = shared;
    region->is_owner = 0U;  /* Not the owner */
    region->numa_node = shared->numa_node;

    /* Add reference */
    uint32_t idx = shared->ref_count;
    shared->refs[idx].pid = 0;  /* TODO: Get current PID */
    shared->refs[idx].ctx = ctx;
    shared->refs[idx].map_time = vos3_timer_get_ticks();
    shared->refs[idx].access_flags = access_flags;
    shared->ref_count++;

    shared_unlock();

    /* Add to context's region list */
    region->next = ctx->regions;
    ctx->regions = region;
    ctx->region_count++;

    vos3_console_printf("[AI-GUARD] Mapped shared region: id=%u ref_count=%u\n",
                        shared->region_id, shared->ref_count);

    return region;
}

int vos3_ai_guard_unmap_shared(vos3_ai_guard_ctx_t* ctx,
                                vos3_ai_guard_region_t* region)
{
    if (ctx == NULL || region == NULL || region->shared == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    vos3_ai_shared_region_t* shared = region->shared;

    shared_lock();

    /* Find and remove this context's reference */
    for (uint32_t i = 0; i < shared->ref_count; i++) {
        if (shared->refs[i].ctx == ctx) {
            /* Shift remaining refs down */
            for (uint32_t j = i; j < shared->ref_count - 1; j++) {
                shared->refs[j] = shared->refs[j + 1];
            }
            shared->ref_count--;
            break;
        }
    }

    uint32_t remaining = shared->ref_count;

    shared_unlock();

    /* Remove from context's region list */
    vos3_ai_guard_region_t** pp = &ctx->regions;
    while (*pp != NULL) {
        if (*pp == region) {
            *pp = region->next;
            ctx->region_count--;
            break;
        }
        pp = &(*pp)->next;
    }

    /* If this was the owner and no refs remain, free everything */
    if (remaining == 0U && region->is_owner) {
        /* Remove from global registry */
        shared_lock();
        vos3_ai_shared_region_t** sp = &g_shared_regions;
        while (*sp != NULL) {
            if (*sp == shared) {
                *sp = shared->next;
                break;
            }
            sp = &(*sp)->next;
        }
        shared_unlock();

        /* Free the actual memory */
        vos3_ai_guard_free(ctx, (void*)shared->base);
        vos3_kfree(shared);

        vos3_console_printf("[AI-GUARD] Shared region freed: memory released\n");
    } else {
        /* Just free the local region descriptor */
        vos3_kfree(region);

        vos3_console_printf("[AI-GUARD] Unmapped shared region: ref_count=%u\n",
                            remaining);
    }

    return VOS3_AI_GUARD_OK;
}

vos3_ai_shared_region_t* vos3_ai_guard_find_shared(uint32_t region_id)
{
    shared_lock();

    vos3_ai_shared_region_t* shared = g_shared_regions;
    while (shared != NULL) {
        if (shared->region_id == region_id) {
            shared_unlock();
            return shared;
        }
        shared = shared->next;
    }

    shared_unlock();
    return NULL;
}

uint32_t vos3_ai_guard_shared_refcount(vos3_ai_shared_region_t* shared)
{
    if (shared == NULL) {
        return 0;
    }
    return shared->ref_count;
}

/* ============================================================================
 * PHASE 17.5.4: MEMORY PRESSURE HANDLING
 * ============================================================================ */

/** @brief Memory pressure configuration */
static vos3_ai_pressure_config_t g_pressure_config = {
    .soft_threshold = 64ULL * 1024ULL * 1024ULL,      /* 64 MB */
    .warning_threshold = 32ULL * 1024ULL * 1024ULL,   /* 32 MB */
    .critical_threshold = 16ULL * 1024ULL * 1024ULL,  /* 16 MB */
    .check_interval = 100U,                            /* 100 ticks */
    .evict_scratch_first = 1U,
};

/** @brief Current pressure level */
static vos3_ai_pressure_level_t g_pressure_level = VOS3_AI_PRESSURE_NONE;

/** @brief Last pressure check tick */
static uint64_t g_last_pressure_tick = 0;

int vos3_ai_guard_set_pressure_config(const vos3_ai_pressure_config_t* config)
{
    if (config == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    g_pressure_config = *config;

    vos3_console_printf("[AI-GUARD] Pressure config: soft=%lluMB warn=%lluMB crit=%lluMB\n",
                        (unsigned long long)(config->soft_threshold / (1024ULL * 1024ULL)),
                        (unsigned long long)(config->warning_threshold / (1024ULL * 1024ULL)),
                        (unsigned long long)(config->critical_threshold / (1024ULL * 1024ULL)));

    return VOS3_AI_GUARD_OK;
}

vos3_ai_pressure_level_t vos3_ai_guard_get_pressure_level(void)
{
    return g_pressure_level;
}

int vos3_ai_guard_set_swap_hint(vos3_ai_guard_region_t* region,
                                 vos3_ai_swap_hint_t hint)
{
    if (region == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    region->swap_hint = hint;

    vos3_console_printf("[AI-GUARD] Region %u swap hint: %s\n",
                        region->id,
                        (hint == VOS3_AI_SWAP_PINNED) ? "PINNED" :
                        (hint == VOS3_AI_SWAP_PREFERRED) ? "PREFERRED" : "ALLOWED");

    return VOS3_AI_GUARD_OK;
}

int vos3_ai_guard_set_reclaim_callback(vos3_ai_guard_region_t* region,
                                        vos3_ai_reclaim_callback_t callback)
{
    if (region == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    region->reclaim_cb = callback;
    return VOS3_AI_GUARD_OK;
}

/**
 * @brief Find best eviction candidate based on policy
 *
 * Eviction order:
 * 1. SCRATCH with SWAP_PREFERRED
 * 2. SCRATCH (LRU)
 * 3. TENSOR (LRU)
 * 4. Never: PINNED or MODEL
 */
static vos3_ai_guard_region_t* find_eviction_candidate(void)
{
    vos3_ai_guard_ctx_t* ctx = g_global_ctx;
    if (ctx == NULL) {
        return NULL;
    }

    vos3_ai_guard_region_t* candidate = NULL;
    uint64_t oldest_tick = UINT64_MAX;

    /* Pass 1: SCRATCH with SWAP_PREFERRED */
    vos3_ai_guard_region_t* region = ctx->regions;
    while (region != NULL) {
        if (region->type == VOS3_AI_GUARD_SCRATCH &&
            region->swap_hint == VOS3_AI_SWAP_PREFERRED &&
            region->state == VOS3_AI_STATE_ACTIVE) {
            return region;
        }
        region = region->next;
    }

    /* Pass 2: SCRATCH (LRU) */
    region = ctx->regions;
    while (region != NULL) {
        if (region->type == VOS3_AI_GUARD_SCRATCH &&
            region->swap_hint != VOS3_AI_SWAP_PINNED &&
            region->state == VOS3_AI_STATE_ACTIVE) {
            if (region->last_active_tick < oldest_tick) {
                oldest_tick = region->last_active_tick;
                candidate = region;
            }
        }
        region = region->next;
    }
    if (candidate != NULL) {
        return candidate;
    }

    /* Pass 3: TENSOR (LRU) */
    oldest_tick = UINT64_MAX;
    region = ctx->regions;
    while (region != NULL) {
        if (region->type == VOS3_AI_GUARD_TENSOR &&
            region->swap_hint != VOS3_AI_SWAP_PINNED &&
            region->state == VOS3_AI_STATE_ACTIVE) {
            if (region->last_active_tick < oldest_tick) {
                oldest_tick = region->last_active_tick;
                candidate = region;
            }
        }
        region = region->next;
    }

    /* Never evict MODEL or PINNED regions */
    return candidate;
}

size_t vos3_ai_guard_reclaim(size_t bytes_needed)
{
    size_t freed = 0;

    while (freed < bytes_needed) {
        vos3_ai_guard_region_t* victim = find_eviction_candidate();
        if (victim == NULL) {
            break;  /* No more candidates */
        }

        /* Try custom reclaim callback first */
        if (victim->reclaim_cb != NULL) {
            int result = victim->reclaim_cb(victim, bytes_needed - freed);
            if (result > 0) {
                freed += (size_t)result;
                continue;
            }
        }

        /* Suspend the region */
        size_t region_size = victim->size;
        if (vos3_ai_guard_suspend_region(victim) == VOS3_AI_GUARD_OK) {
            freed += region_size;
            vos3_console_printf("[AI-GUARD] Evicted region %u (%s): %zu bytes\n",
                                victim->id,
                                vos3_ai_guard_type_name(victim->type),
                                region_size);
        } else {
            break;  /* Failed to evict */
        }
    }

    return freed;
}

int vos3_ai_guard_suspend_region(vos3_ai_guard_region_t* region)
{
    if (region == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    if (region->state == VOS3_AI_STATE_SUSPENDED) {
        return VOS3_AI_GUARD_OK;  /* Already suspended */
    }

    /* Mark as suspended */
    region->state = VOS3_AI_STATE_SUSPENDED;

    /* In a full implementation, we would:
     * 1. Save region content to swap
     * 2. Unmap physical pages
     * 3. Free physical memory
     */

    vos3_console_printf("[AI-GUARD] Suspended region %u\n", region->id);

    return VOS3_AI_GUARD_OK;
}

int vos3_ai_guard_resume_region(vos3_ai_guard_region_t* region)
{
    if (region == NULL) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    if (region->state != VOS3_AI_STATE_SUSPENDED) {
        return VOS3_AI_GUARD_OK;  /* Not suspended */
    }

    /* In a full implementation, we would:
     * 1. Allocate physical pages
     * 2. Restore content from swap
     * 3. Re-map pages
     */

    region->state = VOS3_AI_STATE_ACTIVE;
    region->last_active_tick = vos3_timer_get_ticks();

    vos3_console_printf("[AI-GUARD] Resumed region %u\n", region->id);

    return VOS3_AI_GUARD_OK;
}

void vos3_ai_guard_pressure_tick(void)
{
    uint64_t now = vos3_timer_get_ticks();

    /* Check interval */
    if (now - g_last_pressure_tick < g_pressure_config.check_interval) {
        return;
    }
    g_last_pressure_tick = now;

    /* Get current free memory */
    vos3_pmm_stats_t pmm_stats;
    vos3_pmm_get_stats(&pmm_stats);
    uint64_t free_memory = pmm_stats.free_memory;

    /* Determine pressure level */
    vos3_ai_pressure_level_t new_level;
    if (free_memory > g_pressure_config.soft_threshold) {
        new_level = VOS3_AI_PRESSURE_NONE;
    } else if (free_memory > g_pressure_config.warning_threshold) {
        new_level = VOS3_AI_PRESSURE_LOW;
    } else if (free_memory > g_pressure_config.critical_threshold) {
        new_level = VOS3_AI_PRESSURE_MEDIUM;
    } else if (free_memory > g_pressure_config.critical_threshold / 2) {
        new_level = VOS3_AI_PRESSURE_HIGH;
    } else {
        new_level = VOS3_AI_PRESSURE_CRITICAL;
    }

    /* Report level changes */
    if (new_level != g_pressure_level) {
        static const char* level_names[] = {
            "NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"
        };
        vos3_console_printf("[AI-GUARD] Memory pressure: %s (free=%llu MB)\n",
                            level_names[new_level],
                            (unsigned long long)(free_memory / (1024ULL * 1024ULL)));

        /* Fire alert on high/critical */
        if (new_level >= VOS3_AI_PRESSURE_HIGH) {
            vos3_ai_alert_t alert = {
                .timestamp = now,
                .type = VOS3_AI_ALERT_ANOMALY,
                .severity = (new_level == VOS3_AI_PRESSURE_CRITICAL) ?
                    VOS3_AI_ALERT_CRITICAL : VOS3_AI_ALERT_WARNING,
                .region_id = 0,
                .pid = 0,
                .value1 = free_memory,
                .value2 = g_pressure_config.warning_threshold,
            };
            vos3_ai_monitor_fire_alert(&alert);
        }

        g_pressure_level = new_level;
    }

    /* Auto-reclaim on HIGH or CRITICAL */
    if (g_pressure_level >= VOS3_AI_PRESSURE_HIGH) {
        size_t needed = g_pressure_config.warning_threshold - free_memory;
        if (needed > 0 && needed < (1ULL << 30)) {  /* Sanity check */
            size_t freed = vos3_ai_guard_reclaim(needed);
            if (freed > 0) {
                vos3_console_printf("[AI-GUARD] Auto-reclaimed %zu bytes\n", freed);
            }
        }
    }
}

/* ============================================================================
 * CONTEXT REGISTRATION (for integrity/pressure ticks)
 * ============================================================================ */

/**
 * @brief Register context as global (called from ctx_create)
 */
static void register_global_ctx(vos3_ai_guard_ctx_t* ctx)
{
    if (g_global_ctx == NULL) {
        g_global_ctx = ctx;
    }
}

/* ============================================================================
 * PHASE N: PER-APP MEMORY ISOLATION
 * ============================================================================ */

/** @brief Per-app context array (app_id 0 = system context = g_global_ctx) */
vos3_ai_guard_ctx_t* g_app_contexts[VOS3_MAX_APP_CONTEXTS] = { NULL };

/** @brief Currently active app_id */
uint8_t g_active_app_id = 0U;

/*
 * NOTE (Phase F): tag_pte_with_app_id() and get_app_id_from_pte() were removed.
 * PTE bits 9-11 are reserved for AI guard flags (MONITORED/PROTECTED/GUARD).
 * App_id is stored in task->app_id (per-task metadata), NOT in PTE bits.
 */

int vos3_ai_guard_create_app_ctx(uint8_t app_id)
{
    if (app_id >= VOS3_MAX_APP_CONTEXTS) {
        vos3_console_printf("[AI-GUARD] Invalid app_id %u (max %u)\n",
                            app_id, VOS3_MAX_APP_CONTEXTS - 1U);
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    if (g_app_contexts[app_id] != NULL) {
        vos3_console_printf("[AI-GUARD] App context %u already exists\n", app_id);
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    vos3_ai_guard_ctx_t* ctx = vos3_ai_guard_ctx_create();
    if (ctx == NULL) {
        return VOS3_AI_GUARD_ERR_NOMEM;
    }

    g_app_contexts[app_id] = ctx;

    /* App_id 0 is the system context */
    if (app_id == 0U && g_global_ctx == NULL) {
        g_global_ctx = ctx;
    }

    vos3_console_printf("[AI-GUARD] Created app context %u\n", app_id);
    return VOS3_AI_GUARD_OK;
}

int vos3_ai_guard_destroy_app_ctx(uint8_t app_id)
{
    if (app_id >= VOS3_MAX_APP_CONTEXTS) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    if (g_app_contexts[app_id] == NULL) {
        return VOS3_AI_GUARD_ERR_NOTFOUND;
    }

    /* Switch to system context if we're destroying the active one */
    if (g_active_app_id == app_id) {
        g_active_app_id = 0U;
    }

    vos3_ai_guard_ctx_destroy(g_app_contexts[app_id]);

    /* Clear the global context pointer if this was it */
    if (g_app_contexts[app_id] == g_global_ctx) {
        g_global_ctx = NULL;
    }

    g_app_contexts[app_id] = NULL;

    vos3_console_printf("[AI-GUARD] Destroyed app context %u\n", app_id);
    return VOS3_AI_GUARD_OK;
}

int vos3_ai_guard_switch_ctx(uint8_t app_id)
{
    if (app_id >= VOS3_MAX_APP_CONTEXTS) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    if (g_app_contexts[app_id] == NULL) {
        return VOS3_AI_GUARD_ERR_NOTFOUND;
    }

    g_active_app_id = app_id;

    vos3_console_printf("[AI-GUARD] Switched to app context %u\n", app_id);
    return VOS3_AI_GUARD_OK;
}

uint8_t vos3_ai_guard_get_active_app_id(void)
{
    return g_active_app_id;
}

vos3_ai_guard_ctx_t* vos3_ai_guard_get_app_ctx(uint8_t app_id)
{
    if (app_id >= VOS3_MAX_APP_CONTEXTS) {
        return NULL;
    }
    return g_app_contexts[app_id];
}

int vos3_ai_guard_check_app_access(uintptr_t fault_addr, uint8_t current_app_id)
{
    /*
     * Cross-app memory barrier check.
     * If a page has been tagged with an app_id (via PTE bits 9-11),
     * only the matching app context may access it.
     *
     * NOTE: In a full implementation, we would read the actual PTE from the
     * page table. Here we check against all app contexts' region lists
     * to determine ownership, which is the safe approach for our current
     * memory management model.
     */
    for (uint8_t id = 0; id < VOS3_MAX_APP_CONTEXTS; id++) {
        if (id == current_app_id) {
            continue;  /* Skip our own context */
        }

        vos3_ai_guard_ctx_t* ctx = g_app_contexts[id];
        if (ctx == NULL) {
            continue;
        }

        /* Check if fault_addr falls within any region of a different app */
        vos3_ai_guard_region_t* region = ctx->regions;
        while (region != NULL) {
            uintptr_t start = region->base;
            uintptr_t end = region->base + region->size;
            if (fault_addr >= start && fault_addr < end) {
                /* Cross-app violation! */
                __atomic_fetch_add(&g_ai_guard_stats.total_violations, 1,
                                   __ATOMIC_RELAXED);
                ctx->total_violations++;

                vos3_console_printf(
                    "[AI-GUARD] CROSS-APP VIOLATION: app %u tried to access "
                    "app %u memory at %p\n",
                    current_app_id, id, (void*)fault_addr);

                return -1;  /* -EPERM */
            }
            region = region->next;
        }
    }

    return 0;  /* Access allowed */
}

int vos3_ai_guard_check_hardware_access(uintptr_t phys_addr, size_t size)
{
    (void)phys_addr;
    (void)size;

    /* Get the active app context */
    uint8_t app_id = vos3_ai_guard_get_active_app_id();
    vos3_ai_guard_ctx_t* ctx = vos3_ai_guard_get_app_ctx(app_id);

    /* Tasks with an AI guard context are authorized for hardware access */
    if (ctx != NULL) {
        return VOS3_AI_GUARD_OK;
    }

    /* No context = not an AI workload = deny hardware access */
    VOS3_WARN("AI Guard: hardware access denied (no AI context, app_id=%u)",
              (unsigned)app_id);
    return VOS3_AI_GUARD_ERR_PERM;
}

int vos3_ai_guard_get_app_status(uint8_t app_id, uint64_t* mem_used,
                                  size_t* region_count)
{
    if (app_id >= VOS3_MAX_APP_CONTEXTS) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    vos3_ai_guard_ctx_t* ctx = g_app_contexts[app_id];
    if (ctx == NULL) {
        return VOS3_AI_GUARD_ERR_NOTFOUND;
    }

    if (mem_used != NULL) {
        *mem_used = ctx->total_protected;
    }
    if (region_count != NULL) {
        *region_count = ctx->region_count;
    }

    return VOS3_AI_GUARD_OK;
}

/* ============================================================================
 * PHASE 4.1: MODEL REGION SCRUB (rep stosq)
 * ============================================================================ */

/**
 * @brief Zero-fill a memory range using rep stosq (8 bytes per iteration)
 *
 * @note Uses rep stosq -- the canonical kernel-mode zeroing method.
 *       Kernel CFLAGS include -mno-sse -mno-sse2, so AVX/SSE
 *       non-temporal stores (vmovntdq) are not available.
 *
 * @param[in] addr  Start address (must be 8-byte aligned)
 * @param[in] size  Size in bytes (must be multiple of 8)
 */
void scrub_zero_fill(void *addr, size_t size)
{
    size_t qwords = size / 8;
    __asm__ volatile (
        "rep stosq"
        : "+D"(addr), "+c"(qwords)
        : "a"((uint64_t)0)
        : "memory"
    );
    /* Full memory fence -- ensure zero-fill is visible before PTE change */
    __asm__ volatile ("mfence" ::: "memory");
}

uint64_t vos3_ai_guard_scrub_model_regions(uint8_t app_id)
{
    if (app_id >= VOS3_MAX_APP_CONTEXTS) {
        return 0;
    }

    vos3_ai_guard_ctx_t* ctx = g_app_contexts[app_id];
    if (ctx == NULL) {
        return 0;
    }

    /* Lock the context to prevent concurrent region list modification */
    vos3_spinlock_acquire((vos3_spinlock_t *)&ctx->lock);

    uint64_t total_scrubbed = 0;
    vos3_ai_guard_region_t* region = ctx->regions;

    while (region != NULL) {
        /* Only scrub MODEL regions that are ACTIVE */
        if (region->type == VOS3_AI_GUARD_MODEL &&
            region->state == VOS3_AI_STATE_ACTIVE &&
            region->base != 0 && region->size > 0) {

            size_t aligned_size = align_to_page(region->size);
            size_t num_pages = aligned_size / VOS3_PAGE_SIZE;

            /* Step 1: Temporarily make region writable */
            for (size_t i = 0; i < num_pages; i++) {
                uintptr_t page_addr = region->base + (i * VOS3_PAGE_SIZE);
                vos3_vmm_update_flags(page_addr,
                    (vos3_vmm_flags_t)(VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_USER));
            }

            /* Step 2: Zero-fill using rep stosq */
            scrub_zero_fill((void *)region->base, aligned_size);

            /* Step 3: Restore read-only PTEs and flush TLB */
            for (size_t i = 0; i < num_pages; i++) {
                uintptr_t page_addr = region->base + (i * VOS3_PAGE_SIZE);
                vos3_vmm_update_flags(page_addr,
                    (vos3_vmm_flags_t)(VOS3_VMM_FLAG_USER));
                vos3_vmm_invlpg(page_addr);
            }

            total_scrubbed += aligned_size;

            VOS3_DEBUG("[AI-GUARD] Scrubbed MODEL region %u: base=0x%lx size=%zu",
                       region->id, (unsigned long)region->base, aligned_size);
        }

        region = region->next;
    }

    vos3_spinlock_release((vos3_spinlock_t *)&ctx->lock);

    if (total_scrubbed > 0) {
        VOS3_INFO("[AI-GUARD] App %u: scrubbed %llu bytes across MODEL regions",
                  app_id, (unsigned long long)total_scrubbed);
    }

    return total_scrubbed;
}
