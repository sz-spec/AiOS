/**
 * @file audit_ring.c
 * @brief Stage-10 — kernel-side compliance failure audit ring (impl).
 *
 * See ``kernel/include/vos/audit_ring.h`` for the architectural rationale.
 *
 * Implementation notes:
 *
 *   - Storage is a single static array of 64 entries (2 KiB) in BSS.
 *     No heap; no per-CPU; the failure rate is bounded by the
 *     IntentManifest submission rate, which is itself bounded by the
 *     VBus dispatcher's serial single-frame-at-a-time semantics.
 *
 *   - The write index is a monotonic uint32 that never wraps in any
 *     realistic boot lifetime (4 billion failures × ~1 ms minimum
 *     per VBus frame ≈ 50 days of nothing-but-failures before wrap).
 *     We treat any future wrap as a non-issue because by then the
 *     attestation quote has rolled multiple times anyway.
 *
 *   - The compiler memory barrier between field-population and seq-bump
 *     mirrors the pattern in tee.c::ring_record(): readers that observe
 *     seq[i] after the bump are guaranteed to see the entry's body
 *     fully populated. We do not need a stronger barrier because both
 *     producer and consumer are in kernel context (no userspace direct
 *     access; readers come through vos3_audit_snapshot which copies
 *     under the same memory order).
 *
 * @date 2026-05-08
 * @copyright Copyright (c) 2026 vOS Project
 * @license MIT
 */

#include "../../include/vos/audit_ring.h"
#include "../../include/vos/timer.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * Static state
 * ============================================================================ */

static vos3_audit_event_t s_ring[VOS3_AUDIT_RING_SIZE];
static volatile uint32_t  s_total_emitted = 0U;  /* monotonic; never resets */

/* ============================================================================
 * Resilience-Matrix F14 — overspeed gap detector.
 *
 * If the kernel emits compliance failures faster than the userspace
 * backend drains them via vos3_audit_snapshot(), the oldest unread
 * entries are silently overwritten by the ring's wrap. That's a real
 * compliance gap: an EU AI Act Annex IV §V.1 audit asks "show me
 * every IntentManifest rejection in the last 24 h" and the answer
 * "we lost some" is unacceptable.
 *
 * The detector tracks the highest seq the consumer has ever observed
 * (s_last_drained_seq). On each emit, if (s_total_emitted -
 * s_last_drained_seq) exceeds VOS3_AUDIT_RING_SIZE, an entry is about
 * to be lapped — we emit a one-shot WARN so a soak-test or production
 * monitoring picks it up. The warn is rate-limited (once per
 * VOS3_AUDIT_RING_SIZE consecutive lap events) so it doesn't flood
 * the kernel log when the consumer is permanently down.
 * ============================================================================ */
static volatile uint32_t  s_last_drained_seq = 0U;
static volatile uint32_t  s_lap_warn_cooldown = 0U;

/* ============================================================================
 * Helpers
 * ============================================================================ */

/* Load the first 8 bytes of digest48 as a uint64 in big-endian byte order
 * (so the on-the-wire prefix in JSON-LD matches the natural human reading
 * of a SHA-384 hex string — first hex pair is the most significant byte).
 */
static uint64_t load_digest_prefix_be(const uint8_t *digest48)
{
    if (digest48 == (const uint8_t *)0) {
        return 0ULL;
    }
    uint64_t v = 0ULL;
    v |= ((uint64_t)digest48[0]) << 56;
    v |= ((uint64_t)digest48[1]) << 48;
    v |= ((uint64_t)digest48[2]) << 40;
    v |= ((uint64_t)digest48[3]) << 32;
    v |= ((uint64_t)digest48[4]) << 24;
    v |= ((uint64_t)digest48[5]) << 16;
    v |= ((uint64_t)digest48[6]) <<  8;
    v |= ((uint64_t)digest48[7]);
    return v;
}

/* ============================================================================
 * Public API
 * ============================================================================ */

void vos3_audit_emit_failure(uint16_t        category,
                             uint8_t         slot_id,
                             int16_t         rc,
                             const uint8_t  *digest48)
{
    /* Compute slot before the write so the field-population is monotonic
     * w.r.t. the seq bump. */
    const uint32_t seq = s_total_emitted;
    const uint32_t idx = seq % VOS3_AUDIT_RING_SIZE;

    vos3_audit_event_t *e = &s_ring[idx];
    e->tick          = vos3_timer_get_ticks();
    e->digest_prefix = load_digest_prefix_be(digest48);
    e->category      = category;
    e->rc            = rc;
    e->slot_id       = slot_id;
    e->pad[0]        = 0U;
    e->pad[1]        = 0U;
    e->pad[2]        = 0U;
    e->seq           = seq;

    /* Compiler barrier so a reader observing s_total_emitted after the bump
     * sees the entry's body fully populated. Mirrors tee.c::ring_record. */
    __asm__ volatile("" ::: "memory");
    s_total_emitted = seq + 1U;

    /* Visible breadcrumb in the kernel log so a developer running QEMU
     * sees compliance failures immediately, before the userspace JSON-LD
     * endpoint is wired. The %d in DEBUG-class is fine — production
     * builds elide DEBUG below the configured log threshold. */
    VOS3_DEBUG("[AUDIT] cat=%u slot=%u rc=%d seq=%u",
               (unsigned)category,
               (unsigned)slot_id,
               (int)rc,
               (unsigned)seq);

    /* Resilience-Matrix F14 — overspeed gap check. Compute the unread
     * backlog; if it exceeds the ring capacity an entry is about to be
     * lost. Rate-limit the WARN to once per RING_SIZE consecutive laps
     * to avoid log spam when the consumer is permanently dead. */
    {
        const uint32_t backlog = s_total_emitted - s_last_drained_seq;
        if (backlog > (uint32_t)VOS3_AUDIT_RING_SIZE) {
            if (s_lap_warn_cooldown == 0U) {
                VOS3_WARN("[AUDIT] overspeed: total_emitted=%u "
                          "last_drained=%u backlog=%u cap=%u — "
                          "consumer is falling behind, oldest entries "
                          "WILL be overwritten",
                          (unsigned)s_total_emitted,
                          (unsigned)s_last_drained_seq,
                          (unsigned)backlog,
                          (unsigned)VOS3_AUDIT_RING_SIZE);
                s_lap_warn_cooldown = (uint32_t)VOS3_AUDIT_RING_SIZE;
            } else {
                s_lap_warn_cooldown--;
            }
        }
    }
}

uint32_t vos3_audit_snapshot(vos3_audit_event_t *out, uint32_t max)
{
    if (out == (vos3_audit_event_t *)0 || max == 0U) {
        return 0U;
    }

    const uint32_t total = s_total_emitted;
    const uint32_t have  = (total < VOS3_AUDIT_RING_SIZE)
                                ? total
                                : VOS3_AUDIT_RING_SIZE;
    const uint32_t n     = (have < max) ? have : max;

    /* Read oldest-first. If we have wrapped, the oldest is at
     * (total % SIZE); otherwise at index 0. */
    const uint32_t start = (total >= VOS3_AUDIT_RING_SIZE)
                                ? (total % VOS3_AUDIT_RING_SIZE)
                                : 0U;

    for (uint32_t i = 0U; i < n; i++) {
        const uint32_t idx = (start + i) % VOS3_AUDIT_RING_SIZE;
        out[i] = s_ring[idx];
    }

    /* Resilience-Matrix F14 — record consumer progress. The snapshot
     * caller has now seen every entry up to s_total_emitted; advance
     * the drained marker so the emit-side gap detector reflects that.
     * Use a monotonic max so a smaller late-arriving snapshot doesn't
     * regress the high-water mark. */
    if (total > s_last_drained_seq) {
        s_last_drained_seq = total;
    }

    return n;
}

uint32_t vos3_audit_total_emitted(void)
{
    return s_total_emitted;
}
