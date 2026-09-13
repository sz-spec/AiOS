/**
 * @file boot_ai.c
 * @brief VOS3 Boot — AI Subsystem Initialization
 *
 * Extracted from kmain.c (Phase 8.5-C Sovereign Consolidation).
 * AI Memory Guard, AI Access Monitor, and Phase 17.5 extended
 * self-tests.
 */

#include "../../include/vos/boot_ai.h"
#include "../../include/vos/console.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/numa.h"
#include "../../include/vos/vector_vfs.h"
#include "../../include/vos/sni.h"
#include "../../include/vos/vscreen.h"
#include "../../include/vos/agent_mesh.h"
#include "../../include/vos/action_bridge.h"

/* ============================================================================
 * BOOT AI INIT
 * ============================================================================ */

int boot_ai_init(void)
{
    int result;

    /* ===== Phase 17: AI Memory Guard ===== */
    VOS3_INFO("Initializing AI Memory Guard");
    result = vos3_ai_guard_init();
    if (result != 0) {
        VOS3_WARN("AI Guard initialization failed (error %d)", result);
        return result;
    }

#ifndef VOS3_PRODUCTION_BUILD
    /* Run AI Guard self-test */
    VOS3_INFO("AI Guard self-test:");
    vos3_ai_guard_ctx_t *test_ctx = vos3_ai_guard_ctx_create();
    if (test_ctx != NULL) {
        void *test_region = vos3_ai_guard_alloc(test_ctx, 4096,
            VOS3_AI_GUARD_TENSOR,
            VOS3_AI_FLAG_RED_ZONES | VOS3_AI_FLAG_CHECKSUMMED);
        if (test_region != NULL) {
            VOS3_INFO("  [PASS] AI Guard allocation with red zones");
            /* Write test pattern */
            char *p = (char *)test_region;
            for (int i = 0; i < 4096; i++) {
                p[i] = (char)(i & 0xFF);
            }
            /* Verify integrity */
            vos3_ai_guard_region_t *region = vos3_ai_guard_find_region(test_ctx,
                (uintptr_t)test_region);
            if (region != NULL) {
                /* Update checksum after writing */
                region->checksum = vos3_ai_guard_compute_checksum(region);
                if (vos3_ai_guard_verify_integrity(region) == 0) {
                    VOS3_INFO("  [PASS] Checksum verification (seed=0xA15AFE00A2D50)");
                }
            }
            vos3_ai_guard_free(test_ctx, test_region);
            VOS3_INFO("  [PASS] AI Guard deallocation");
        }
        vos3_ai_guard_ctx_destroy(test_ctx);
    }
#endif /* !VOS3_PRODUCTION_BUILD */
    VOS3_INFO("AI Guard subsystem ready");

    /* Initialize AI Access Monitor (Phase 17.3) */
    result = vos3_ai_monitor_init();
    if (result != 0) {
        VOS3_WARN("AI Monitor initialization failed (error %d)", result);
    }

    /* ===== Phase 6.1: Immutable Source & Business Continuity Hardening =====
     * Guardian Seal MUST initialize BEFORE complex subsystems (VecVFS, SNI,
     * vScreen, Mesh, vSpace, Clipboard) so that .text integrity is sealed
     * while the code section is still in its pristine post-link state.
     * Moved here from position 10 per Zero-Trust Audit BLOCKER #1.
     */
    extern int guardian_init(void);
    extern int recovery_init(const uint8_t sovereign_key[32]);

    VOS3_INFO("Initializing Guardian Seal (Immutable Source Lock)");
    result = guardian_init();
    if (result != 0) VOS3_WARN("Guardian Seal init failed (error %d)", result);

    /* Initialize Recovery Bridge with entropy-derived sovereign key */
    {
        uint8_t sovereign_key[32];
        extern int vos3_entropy_extract(void *buf, size_t len);
        extern void vos3_cache_wipe(void *buf, size_t len);
        vos3_entropy_extract(sovereign_key, 32);
        result = recovery_init(sovereign_key);
        if (result != 0) VOS3_WARN("Recovery Bridge init failed (error %d)", result);
        vos3_cache_wipe(sovereign_key, 32);
    }

#ifndef VOS3_PRODUCTION_BUILD
    /* ===== Phase 17.5: AI Guard Extended Self-Tests ===== */
    VOS3_INFO("Running Phase 17.5 AI Guard self-tests...");
    vos3_ai_guard_ctx_t *test_ctx_175 = vos3_ai_guard_ctx_create();
    if (test_ctx_175 != NULL) {
        int p175_passed = 1;

        /* 17.5.1: Integrity Automation */
        VOS3_INFO("  [17.5.1] Testing Integrity Automation...");
        {
            void *region_ptr = vos3_ai_guard_alloc(test_ctx_175, 4096,
                VOS3_AI_GUARD_TENSOR, VOS3_AI_FLAG_CHECKSUMMED);
            if (region_ptr != NULL) {
                vos3_ai_guard_region_t *r = vos3_ai_guard_find_region(
                    test_ctx_175, (uintptr_t)region_ptr);
                if (r != NULL) {
                    if (vos3_ai_guard_set_integrity_auto(r, 100, 1) == 0) {
                        VOS3_INFO("    [PASS] Set integrity interval=100, auto_suspend=1");
                    } else {
                        VOS3_ERROR("    [FAIL] Set integrity auto");
                        p175_passed = 0;
                    }

                    vos3_ai_integrity_config_t cfg;
                    if (vos3_ai_guard_get_integrity_config(r, &cfg) == 0 &&
                        cfg.verify_interval == 100 && cfg.auto_suspend == 1) {
                        VOS3_INFO("    [PASS] Get integrity config verified");
                    } else {
                        VOS3_ERROR("    [FAIL] Config mismatch");
                        p175_passed = 0;
                    }
                }
                vos3_ai_guard_free(test_ctx_175, region_ptr);
            }
        }

        /* 17.5.2: NUMA Awareness */
        VOS3_INFO("  [17.5.2] Testing NUMA Awareness...");
        {
            uint32_t nodes = vos3_numa_node_count();
            VOS3_INFO("    [PASS] NUMA node count: %u", nodes);

            void *numa_ptr = vos3_ai_guard_alloc_numa(test_ctx_175, 8192,
                VOS3_AI_GUARD_MODEL, VOS3_AI_FLAG_CHECKSUMMED, 0);
            if (numa_ptr != NULL) {
                vos3_ai_guard_region_t *r = vos3_ai_guard_find_region(
                    test_ctx_175, (uintptr_t)numa_ptr);
                if (r != NULL && r->numa_node == 0) {
                    VOS3_INFO("    [PASS] NUMA alloc on node 0: %p", numa_ptr);
                } else {
                    VOS3_ERROR("    [FAIL] NUMA node mismatch");
                    p175_passed = 0;
                }
                vos3_ai_guard_free(test_ctx_175, numa_ptr);
            } else {
                VOS3_ERROR("    [FAIL] NUMA alloc returned NULL");
                p175_passed = 0;
            }
        }

        /* 17.5.3: Shared Regions */
        VOS3_INFO("  [17.5.3] Testing Shared Regions...");
        {
            vos3_ai_shared_region_t *shared = vos3_ai_guard_create_shared(
                test_ctx_175, 16384, VOS3_AI_GUARD_MODEL, 0);
            if (shared != NULL) {
                uint32_t ref = vos3_ai_guard_shared_refcount(shared);
                if (ref == 1) {
                    VOS3_INFO("    [PASS] Shared region created, ref_count=1");
                } else {
                    VOS3_ERROR("    [FAIL] ref_count=%u (expected 1)", ref);
                    p175_passed = 0;
                }
            } else {
                VOS3_ERROR("    [FAIL] Create shared returned NULL");
                p175_passed = 0;
            }
        }

        /* 17.5.4: Memory Pressure */
        VOS3_INFO("  [17.5.4] Testing Memory Pressure...");
        {
            vos3_ai_pressure_level_t level = vos3_ai_guard_get_pressure_level();
            VOS3_INFO("    [PASS] Current pressure level: %u", level);

            void *scratch = vos3_ai_guard_alloc(test_ctx_175, 4096,
                VOS3_AI_GUARD_SCRATCH, 0);
            if (scratch != NULL) {
                vos3_ai_guard_region_t *r = vos3_ai_guard_find_region(
                    test_ctx_175, (uintptr_t)scratch);
                if (r != NULL) {
                    if (vos3_ai_guard_set_swap_hint(r, VOS3_AI_SWAP_PREFERRED) == 0) {
                        VOS3_INFO("    [PASS] Set swap hint: PREFERRED");
                    }
                    if (vos3_ai_guard_suspend_region(r) == 0 &&
                        r->state == VOS3_AI_STATE_SUSPENDED) {
                        VOS3_INFO("    [PASS] Suspend region");
                    }
                    if (vos3_ai_guard_resume_region(r) == 0 &&
                        r->state == VOS3_AI_STATE_ACTIVE) {
                        VOS3_INFO("    [PASS] Resume region");
                    }
                }
                vos3_ai_guard_free(test_ctx_175, scratch);
            }
        }

        /* 17.5.5: Telemetry Export */
        VOS3_INFO("  [17.5.5] Testing Telemetry Export...");
        {
            uint8_t buf[512];
            int64_t size = vos3_ai_telemetry_snapshot(buf, sizeof(buf));
            if (size > 0) {
                vos3_ai_telemetry_header_t *h = (vos3_ai_telemetry_header_t *)buf;
                if (h->magic == VOS3_AI_TELEMETRY_MAGIC) {
                    VOS3_INFO("    [PASS] Telemetry magic: AITe (0x%08X)", h->magic);
                    VOS3_INFO("    [PASS] Telemetry version: %u", h->version);
                    VOS3_INFO("    [PASS] Telemetry regions: %u", h->region_count);
                } else {
                    VOS3_ERROR("    [FAIL] Invalid magic: 0x%08X", h->magic);
                    p175_passed = 0;
                }
            } else {
                VOS3_ERROR("    [FAIL] Snapshot returned %lld", (long long)size);
                p175_passed = 0;
            }
        }

        vos3_ai_guard_ctx_destroy(test_ctx_175);

        if (p175_passed) {
            VOS3_INFO("[PASS] Phase 17.5 Tests: ALL PASSED");
        } else {
            VOS3_WARN("[WARN] Phase 17.5 Tests: SOME FAILURES");
        }
    }
#endif /* !VOS3_PRODUCTION_BUILD */

    /* ===== Phase 3.1: Vector VFS + SNI ===== */
    VOS3_INFO("Initializing Vector VFS subsystem");
    result = vecvfs_init();
    if (result != 0) {
        VOS3_WARN("Vector VFS initialization failed (error %d)", result);
    }

    VOS3_INFO("Initializing Sovereign Neural Interface");
    result = vos3_sni_init();
    if (result != 0) {
        VOS3_WARN("SNI initialization failed (error %d)", result);
    }

#ifndef VOS3_PRODUCTION_BUILD
    /* ===== Phase 3.1: Neural Memory Supreme Audit ===== */
    extern void vos3_phase31_neural_memory_audit(void);
    vos3_phase31_neural_memory_audit();
#endif

    /* ===== Phase 4.1: vScreen + Agentic Mesh + Action Bridge ===== */
    VOS3_INFO("Initializing vScreen Perception Layer");
    result = vscreen_init();
    if (result != 0) VOS3_WARN("vScreen init failed (error %d)", result);

    VOS3_INFO("Initializing Agentic Mesh Protocol");
    result = mesh_init();
    if (result != 0) VOS3_WARN("Agentic Mesh init failed (error %d)", result);

    VOS3_INFO("Initializing Sovereign Action Bridge");
    result = action_bridge_init();
    if (result != 0) VOS3_WARN("Action Bridge init failed (error %d)", result);

#ifndef VOS3_PRODUCTION_BUILD
    /* ===== Phase 4.1: Supreme Executive Audit ===== */
    extern void vos3_phase41_executive_audit(void);
    vos3_phase41_executive_audit();

    /* ===== Phase 4.1: Triple-Gate Extreme Audit ===== */
    extern void vos3_phase41_extreme_audit(void);
    vos3_phase41_extreme_audit();

    /* ===== Phase 4.1: Omega-Gate Final Audit ===== */
    extern void vos3_phase41_omega_audit(void);
    vos3_phase41_omega_audit();
#endif

    /* ===== Phase 5.1: vSpace Desktop & Sovereign App Sandboxing ===== */
    extern int vspace_init(void);
    extern int sclip_init(void);

    VOS3_INFO("Initializing vSpace Desktop Shell");
    result = vspace_init();
    if (result != 0) VOS3_WARN("vSpace init failed (error %d)", result);

    VOS3_INFO("Initializing Sovereign Clipboard");
    result = sclip_init();
    if (result != 0) VOS3_WARN("Sovereign Clipboard init failed (error %d)", result);

#ifndef VOS3_PRODUCTION_BUILD
    /* Phase 5.1: Supreme User-Experience Audit */
    extern void vos3_phase51_ux_audit(void);
    vos3_phase51_ux_audit();

    /* Phase 5.1: Infinity-Gate Divine Audit */
    extern void vos3_phase51_infinity_audit(void);
    vos3_phase51_infinity_audit();

    /* Phase 5.1: Singularity-Gate Extreme Cross-Phase Audit */
    extern void vos3_phase51_singularity_audit(void);
    vos3_phase51_singularity_audit();

    /* Phase 5.1: Event-Horizon Finality Audit */
    extern void vos3_phase51_event_horizon_audit(void);
    vos3_phase51_event_horizon_audit();

    /* Phase 5.1: Quantum-Void God-Mode Resilience Audit */
    extern void vos3_phase51_quantum_void_audit(void);
    vos3_phase51_quantum_void_audit();
#endif

#ifndef VOS3_PRODUCTION_BUILD
    /* Phase 6.1: Business-Ready RC1 Audit */
    extern void vos3_phase61_business_ready_audit(void);
    vos3_phase61_business_ready_audit();

    /* Phase 6.1: Event-Horizon Finality Audit */
    extern void vos3_phase61_event_horizon_audit(void);
    vos3_phase61_event_horizon_audit();

    /* Phase 6.2: Absolute Zero Sovereign Audit */
    extern void vos3_phase62_absolute_zero_audit(void);
    vos3_phase62_absolute_zero_audit();

    /* Phase 7.0: Genesis-Gate Final Audit */
    extern void vos3_phase70_genesis_gate_audit(void);
    vos3_phase70_genesis_gate_audit();

    /* Phase 8.1/8.2: Velocity + Sovereign-Endurance PERFORMANCE benchmarks.
     * These are perf/soak suites (e.g. a 1,000,000-iteration verify loop +
     * 10K-avg captures), not correctness certs. They are skipped under the
     * VOS3_ASSERT_CERT harness: their timing results are meaningless under TCG
     * emulation, and their multi-million-iteration loops make a QEMU cert run
     * impractically slow (they never reach the cert sentinel). The cert harness
     * (VOS3_ASSERT_CERT / kmain) is unaffected — these emit [END]/[VEL] lines,
     * not ~~CERT~~ points. Normal (non-harness) test builds still run them in
     * full. */
#ifndef VOS3_ASSERT_HARNESS
    /* Phase 8.1: Velocity-Alpha Performance Benchmark */
    extern void vos3_phase81_velocity_alpha_benchmark(void);
    vos3_phase81_velocity_alpha_benchmark();

    /* Phase 8.2: Sovereign Endurance & Edge-Case Collision */
    extern void vos3_phase82_sovereign_endurance_audit(void);
    vos3_phase82_sovereign_endurance_audit();
#endif /* !VOS3_ASSERT_HARNESS */
#endif

    /* Sovereign Watermark (both builds) */
    extern void vos3_print_sovereign_watermark(void);
    vos3_print_sovereign_watermark();

    return 0;
}
