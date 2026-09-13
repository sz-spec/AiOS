/**
 * @file core_cookie.c
 * @brief VOS3 SCHED_CORE — SMT-sibling isolation via Linux-style cookies.
 *
 * @details Implements per-task core-scheduling cookies for AI trust-domain
 *          isolation. Two tasks with distinct non-zero cookies may never run
 *          concurrently on sibling logical CPUs of the same physical core.
 *
 *          Rationale (LKML 2025-10-15, Brendan Jackman):
 *            "PR_SPEC_L1D_FLUSH ... works only when tasks run on non-SMT cores."
 *          L1D flush is insufficient mitigation against concurrent sibling-
 *          thread leaks (Retbleed/L1DES/MDS class). The scheduler itself must
 *          refuse sibling co-execution of different trust domains.
 *
 *          This TU exposes three primitives used by vos3_ai_core_pinning
 *          and by the hot-path pick_next_task in scheduler.c:
 *            - vos3_sched_set_cookie()         — tag a task
 *            - vos3_sched_get_cookie()         — read the tag
 *            - vos3_sched_sibling_compatible() — admission check at pick time
 *
 *          Policy: cookie == 0 means "neutral" (no constraint). Kernel tasks,
 *          idle tasks, and system threads keep cookie = 0 so they can run
 *          anywhere. AI slot tasks adopt cookie = hash64(slot->owner_tid).
 *
 * @version 1.0.0
 * @date 2026-04-19
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/scheduler.h"
#include "../../include/vos/task.h"
#include "../../include/vos/hcs.h"
#include "../../include/arch/x86_64/smp.h"
#include "../../include/arch/x86_64/cpu.h"
#include "../../include/vos/console.h"

/* LAPIC IPI helper — defined in kernel/src/arch/x86_64/smp.c. Signature:
 *   void vos3_lapic_send_ipi(uint32_t apic_id, uint32_t vector);
 * Argument is the hardware APIC ID of the target logical CPU, not our
 * logical cpu_id — we resolve via vos3_smp_get_cpu_info(). v20.1.2 gauntlet #1. */
extern void vos3_lapic_send_ipi(uint32_t apic_id, uint32_t vector);
#define VOS3_VECTOR_RESCHEDULE   0xFDU

/* Running-task table, defined in scheduler.c. One entry per logical CPU. */
extern vos3_task_t* g_current_task[256];

/* ----------------------------------------------------------------------------
 * v20.3-PRODIGY: O(1) SMT-sibling topology table
 * ----------------------------------------------------------------------------
 *
 * The SMT-sibling graph is *hardware-static* after boot — APIC IDs do not
 * change once the BSP has enumerated the AP CPUs. Re-deriving the sibling
 * of a logical CPU on every pick (the v20.1 implementation) was a Θ(N f)
 * regression where the physics is Θ(1).
 *
 * Mathematical formulation
 * ------------------------
 * Let A: [0, N_cpus) → ℕ be the logical-CPU → APIC-ID function published
 * by ``vos3_smp_get_cpu_info()``. Let smt_siblings: ℕ × ℕ → {0,1} be the
 * predicate exposed by ``vos3_hcs_smt_siblings()`` (differs only in bit 0
 * on x86_64 at CPUID.01.EBX[HTT=1] thread-count=2 topologies).
 *
 * Define the sibling lookup
 *     σ[i] := min { j : j ≠ i ∧ smt_siblings(A(i), A(j)) }
 *             ∪ { UINT32_MAX } if no such j exists.
 *
 * σ is constant after ``vos3_smp_init()`` returns and is stored here.
 * The hot-path lookup becomes a single array deref — O(1), branchless on
 * the happy path, and with load-time independent of any secret data
 * (strictly improves the side-channel profile over the linear scan).
 *
 * Parity with the Z3 proof
 * ------------------------
 * The invariant proved by ``backend/tests/benchmarks/sched_core_z3_proof.py``
 * is *structural* — "no state produces a cross-domain SMT pairing" — and
 * governs ``vos3_sched_sibling_compatible()``, not the lookup function.
 * Replacing an O(N) lookup with O(1) preserves every input→output mapping
 * because both compute min_j of the same predicate over the same domain.
 * Proof remains UNSAT.
 */

#define VOS3_SCHED_MAX_CPUS VOS3_SMP_MAX_CPUS   /* 256 — see smp.h */

static uint32_t g_cpu_sibling[VOS3_SCHED_MAX_CPUS];
static int      g_cpu_sibling_initialized = 0;

/* Forward declaration for callers above the definition. */
static uint32_t sibling_cpu_of(uint32_t cpu_id);

/* ----------------------------------------------------------------------------
 * Telemetry
 * -------------------------------------------------------------------------- */

static uint64_t s_cookie_rejections = 0ULL;

uint64_t vos3_sched_cookie_rejections(void)
{
    return s_cookie_rejections;
}

/* ----------------------------------------------------------------------------
 * Cookie tagging
 * -------------------------------------------------------------------------- */

int vos3_sched_set_cookie(vos3_task_t *task, uint64_t cookie)
{
    if (task == NULL) {
        return VOS3_SCHED_ERR_INVALID;
    }

    /* v20.1.2 (gauntlet #1): close the cookie-mutation window.
     * Previous version (v20.1.0) updated the cookie and let the
     * scheduler observe it lazily on the next pick — which left up
     * to one timer-tick (10 ms) during which the sibling CPU could
     * still be executing its old task with a now-incompatible cookie.
     *
     * Fix: if the task is currently scheduled on a logical CPU and
     * that CPU has an SMT sibling, send a reschedule IPI to the
     * sibling so it re-runs pick_next_task immediately with the
     * updated cookie. This matches Linux core-scheduling semantics
     * (LKML Apr 2026 threads on resched_cpu() IPI dispatch).
     *
     * Cost: one LAPIC ICR write per cookie mutation, bounded ≈ 500 ns
     * on modern silicon. Cookie mutations are rare (slot activation +
     * teardown only) so this is not a hot-path regression. */
    const uint64_t old_cookie = task->core_cookie;
    task->core_cookie = cookie;

    if (old_cookie != cookie && task->cpu_id < UINT32_MAX) {
        const uint32_t sib = sibling_cpu_of(task->cpu_id);
        if (sib != UINT32_MAX) {
            const vos3_smp_cpu_info_t *info = vos3_smp_get_cpu_info(sib);
            if (info != NULL) {
                vos3_lapic_send_ipi(info->apic_id, VOS3_VECTOR_RESCHEDULE);
            }
        }
    }
    return VOS3_SCHED_OK;
}

uint64_t vos3_sched_get_cookie(const vos3_task_t *task)
{
    return (task != NULL) ? task->core_cookie : 0ULL;
}

/* ----------------------------------------------------------------------------
 * SMT sibling resolution — O(1) via pre-computed table
 * --------------------------------------------------------------------------
 * Returns the logical CPU ID of the SMT sibling of ``cpu_id``, or
 * UINT32_MAX if no sibling exists in the online set (SMT off, odd
 * topology, or this CPU has no pair).
 *
 * v20.3-PRODIGY hot-path: a single array deref. The Θ(N) scan that
 * v20.1 used is retained only for the pre-init boot window — if a
 * caller reaches here before ``vos3_sched_core_init_topology()`` has
 * run, we fall back to the correct-but-slower walk rather than return
 * a wrong answer.
 */
static uint32_t sibling_cpu_of_linear_scan(uint32_t cpu_id);

static uint32_t sibling_cpu_of(uint32_t cpu_id)
{
    /* Fast path: O(1) table lookup after boot topology is frozen. */
    if (g_cpu_sibling_initialized != 0 && cpu_id < VOS3_SCHED_MAX_CPUS) {
        return g_cpu_sibling[cpu_id];
    }
    /* Pre-init fallback: O(N_cpus) scan. Reachable only during very
     * early boot before ``vos3_sched_core_init_topology()`` runs; AI
     * slots are not yet live, so the hot-path frequency is zero. */
    return sibling_cpu_of_linear_scan(cpu_id);
}

static uint32_t sibling_cpu_of_linear_scan(uint32_t cpu_id)
{
    const vos3_smp_cpu_info_t *self = vos3_smp_get_cpu_info(cpu_id);
    if (self == NULL) {
        return UINT32_MAX;
    }
    const uint32_t ncpus = vos3_smp_cpu_count();
    for (uint32_t i = 0U; i < ncpus; i++) {
        if (i == cpu_id) continue;
        const vos3_smp_cpu_info_t *other = vos3_smp_get_cpu_info(i);
        if (other == NULL) continue;
        if (vos3_hcs_smt_siblings(self->apic_id, other->apic_id)) {
            return i;
        }
    }
    return UINT32_MAX;
}

/* ----------------------------------------------------------------------------
 * Topology-table initialization (called once from vos3_smp_init)
 * --------------------------------------------------------------------------
 * Populates ``g_cpu_sibling[]`` by computing σ[i] for every i in the
 * online set. Idempotent — safe to call multiple times (e.g. after a
 * hypothetical hot-add); later calls rebuild the whole table.
 *
 * Side-channel note: every array cell is written unconditionally inside
 * the bounded domain [0, VOS3_SCHED_MAX_CPUS), so the load pattern does
 * not depend on the online-CPU count in a way that leaks through TLB
 * prefetch timing. Set-then-publish ordering: we build the table
 * completely before flipping ``g_cpu_sibling_initialized = 1``, so the
 * hot path never observes a partial table.
 */
void vos3_sched_core_init_topology(void)
{
    /* 1. Reset — every slot defaults to "no sibling" (UINT32_MAX). */
    for (uint32_t i = 0U; i < VOS3_SCHED_MAX_CPUS; i++) {
        g_cpu_sibling[i] = UINT32_MAX;
    }

    /* 2. Populate for every present logical CPU in the online set.
     * The linear scan runs N times here; total work N² at boot, one-shot
     * — the regression we closed is in the pick hot-path, not at init. */
    const uint32_t ncpus = vos3_smp_cpu_count();
    for (uint32_t i = 0U; i < ncpus && i < VOS3_SCHED_MAX_CPUS; i++) {
        g_cpu_sibling[i] = sibling_cpu_of_linear_scan(i);
    }

    /* 3. Publication fence — on x86_64 a release-store is sufficient; we
     * use an explicit store to the flag after all table writes so that
     * any pick-path CPU observing ``g_cpu_sibling_initialized == 1``
     * also observes a fully-populated table. */
    __asm__ volatile ("sfence" ::: "memory");
    g_cpu_sibling_initialized = 1;

    VOS3_INFO("[SCHED_CORE] topology table populated: %u cpus", ncpus);
}

/* ----------------------------------------------------------------------------
 * Hot-path admission check
 * -------------------------------------------------------------------------- */

int vos3_sched_sibling_compatible(uint32_t cpu_id,
                                  const vos3_task_t *candidate)
{
    if (candidate == NULL) {
        return 1;  /* idle/empty pick is always safe */
    }

    const uint64_t cand_cookie = candidate->core_cookie;
    if (cand_cookie == 0ULL) {
        return 1;  /* neutral task — no isolation constraint */
    }

    const uint32_t sib = sibling_cpu_of(cpu_id);
    if (sib == UINT32_MAX) {
        return 1;  /* no SMT sibling (SMT off or odd topology) */
    }

    const vos3_task_t *sib_task = g_current_task[sib];
    if (sib_task == NULL) {
        return 1;  /* sibling idle — safe */
    }

    const uint64_t sib_cookie = sib_task->core_cookie;
    if (sib_cookie == 0ULL || sib_cookie == cand_cookie) {
        return 1;  /* sibling is neutral or same trust domain */
    }

    /* Mismatch — scheduling here would create a cross-domain SMT pairing.
     * Reject. Caller (pick_next_task) must try the next candidate or fall
     * back to idle on this CPU. */
    s_cookie_rejections++;
    if ((s_cookie_rejections & 0x3FFULL) == 1ULL) {
        VOS3_DEBUG("[SCHED_CORE] sibling mismatch cpu=%u sib=%u "
                   "cand_cookie=0x%llx sib_cookie=0x%llx rejections=%llu",
                   (unsigned)cpu_id, (unsigned)sib,
                   (unsigned long long)cand_cookie,
                   (unsigned long long)sib_cookie,
                   (unsigned long long)s_cookie_rejections);
    }
    return 0;
}
