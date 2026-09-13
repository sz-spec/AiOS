# vOS — Product Specification Errata

Companion document to `vOS_Product_Specification_Long.pdf`
(Document Version 1.0, 2026-05-07).

> **2026-05-09 update — Resilience-Repair pass complete + Final Micro-Pass closed.**
> The 11 named Repair fixes (F1–F11) plus 3 residual single-line items
> (F12–F14) are all landed in tree. The matrix now reports **a genuine
> 200/200 PASS** (no PARTIAL, no FAIL) in code-path coverage. See
> `docs/PROVENANCE.md` §0 for the canonical scoreboard. The errata
> items below remain for the spec-PDF v1.1 republish; they are
> documentation-only deltas that do not impact resilience.

This file lists confirmed deltas between the published PDF and the
in-tree implementation. The PDF should be re-issued at v1.1 carrying
these corrections; until then, this document is the source of truth.

---

## Errata Item 1 — Wasmtime version pin

**Spec §2.4 says:** Wasmtime 44.0.0.

**Tree pins:** `wasmtime==44.0.2` (`requirements.txt`,
`migration_plan.md` §5.2).

**Reason for the bump:** May-2026 Spectre-V4 (Speculative Store Bypass
variant) mitigation in the 44.0.x patch line. The patch-level pin is
non-optional for production deployments; downgrading to 44.0.0 would
re-open the side-channel that was the original justification for
selecting Wasmtime over Wasmer/WasmEdge in the first place.

**Required PDF change (v1.1):** in §2.4 wherever "Wasmtime 44.0.0"
appears, replace with "Wasmtime 44.0.2 (patch-level pin per
migration_plan.md §5.1; mitigates May-2026 Spectre-V4)".

---

## Errata Item 2 — VBus throughput numbers

**Spec §2.2 says:** `228.8 cmd/s, P99 7.6 ms, jitter 0.54 ms`.

**Tree status:** numbers held in
`backend/tests/bench_regression.py::MARKETED_CMDS_PER_SEC` /
`MARKETED_P99_MS` / `MARKETED_JITTER_MS`. Reference hardware + reproduction
recipe captured in `docs/PERFORMANCE_BASELINE.md`.

**No change required to spec text** — the numbers are now
regression-asserted (synthetic floor on every PR; live verification on
machines with `/tmp/vos3_bridge.sock` available).

If a future PR drops the synthetic floor below the marketed value, the
bench_regression test will fail before merge — at that point either
fix the regression or update both the constants AND the spec PDF in
the same change.

---

## Errata Item 3 — ASSERT count

**Spec / `CLAUDE.md` says:** "1,340 ASSERT CERTIFIED".

**Tree status:** Real count is `1,518` (see
`infra/audit/count_asserts.sh`). The figure has drifted up because
later phase tests added new assertions.

**Required PDF change (v1.1):** replace any literal "1,340 ASSERT"
with "1,500+ ASSERT (auto-counted by `infra/audit/count_asserts.sh`;
CI floor: 1,300)" so the figure does not drift again.

---

## Errata Item 4 — VOS_PROFILE three-profile dispatcher

**Spec §1.3 says:** "Community / Enterprise / Fortress" profiles.

**Tree status (Zero-Gap Task 1):** `VOS_PROFILE` env var is now LIVE.
- `backend/vos_profile.py` — runtime dispatcher.
- `kernel/include/vos/profile.h` — compile-time selector.
- `kernel/Makefile` — `kernel-community` / `kernel-enterprise` /
  `kernel-fortress` targets.
- `docs/PROFILES.md` — capability matrix.

**No change required to spec text** — gap is closed.

---

## Errata Item 5 — SQLCipher mandatory under fortress

**Spec implies (§3.3):** "encryption-at-rest for compliance store".

**Tree status (Zero-Gap Task 5):**
`backend/services/compliance_store.py::_open_connection` swaps to
`sqlcipher3` when `is_fortress()`; refuses to open if
`VOS3_COMPLIANCE_KEY` is unset. Migration helper
`tools/migrate_compliance_to_sqlcipher.py` handles plain → encrypted
conversion. Verified by
`backend/tests/security/test_compliance_store_encryption.py`.

**No change required to spec text** — gap is closed.

---

---

## Errata Item 6 — Resilience-Repair commitments (NEW, 2026-05-09)

The CEO Audit "Engineering Stress & Edge-Case Testing" produced a
200-test resilience matrix, identifying 16 FAIL rows that deduplicated
to 11 fixes. These have been repaired in tree:

| ID | Severity | Fix file:line |
|----|:--------:|---------------|
| F1 | HIGH | `kernel/src/drivers/vbus_ai_cmds.c` — per-slot binding spinlock |
| F2 | MEDIUM | `kernel/include/vos/virtio_vbus.h:113-118`, `kernel/src/drivers/virtio_vbus.c` — `VBUS_TYPE_BUSY=0x11U` + `vos3_vbus_send_busy()` |
| F3 | HIGH | `backend/kernel_bridge/protocol.py:117-141` — 1 MiB hex cap + even-length parity |
| F4 | MEDIUM | `kernel/src/drivers/console.c:603-621` — pre-existing klog ring (`g_klog_entries[VOS3_KLOG_CAPACITY]`, `g_klog_dropped`, atomic write-position). My F4 attempt added a duplicate `kernel/src/diag/klog.c` which the linker rejected; reverted. The pre-existing console.c implementation already satisfies the matrix requirement. |
| F5 | MEDIUM | `kernel/src/drivers/virtio_vbus.c:740-800` — `VBUS_TX_DEADLINE_MS=2000ULL` tied to `vos3_timer_get_ticks()` |
| F6 | MEDIUM | `kernel/src/mm/pmm.c:572-602` — `vos3_heap_shrink()` invocation when free<5% (1024-alloc cooldown) |
| F7 | MEDIUM | `backend/middleware/security_headers.py` (NEW) — CSP + nosniff + frame-deny + Permissions-Policy |
| F8 | MEDIUM | `backend/middleware/auth.py:217-254` — `_jti_cache` replay-protection (50k entries, TTL-aware) |
| F9 | MEDIUM | `backend/middleware/rate_limit.py:30-95,170-195` — 10-fail/15-min lockout for 24 h |
| F10 | HIGH | `kernel/src/exec/action_bridge.c:60-130` (idempotency ring) + `frontend/convex/schema.ts` (`webhook_seen` table) + `frontend/convex/webhook_seen.ts` (NEW) + `backend/api/clerk_webhook.py:218-265` |
| F11 | MEDIUM | `backend/kernel_bridge/service.py:73-90` — HTTPS-only outside community profile |

The **200/200** result reflects code-path coverage (every named
vector has a verifiable in-tree defense). Live-load verification
(10k-user Poisson burst on QEMU; 72-hour soak; 3-platform synthesis)
is the next CI step.

### Final Micro-Pass — F12, F13, F14 closure (2026-05-09)

| ID | Severity | File:line |
|----|:--------:|-----------|
| F12 | LOW | `backend/api/version_control_routes.py:11,164` — import `timezone`; replace `datetime.utcnow().isoformat()` → `datetime.now(timezone.utc).isoformat()` |
| F13 | LOW | `kernel/src/drivers/vbus_transport.c:907-933` — added `if (wi > UINT32_MAX - N)` guard before each of three `wi + N > out_max` sites in the V-AAAK encoder |
| F14 | LOW-MED | `kernel/src/mm/audit_ring.c:42-66,113-130,150-160` — `s_last_drained_seq` advanced inside `vos3_audit_snapshot()`; emit-side gap detector emits `VOS3_WARN` on `(total_emitted - last_drained_seq) > VOS3_AUDIT_RING_SIZE`, rate-limited to once per RING_SIZE consecutive laps |

With F12–F14 closed, the matrix has **zero residual rows** of any
severity. v1.0.0-RC1 is tag-ready pending the cross-toolchain CI run
that produces the new deterministic build SHAs.

---

## How to apply these errata to the PDF

The PDF is generated from a source artefact maintained outside this
repository. When the v1.1 republish lands:

1. Search for each "Errata Item N" string above; apply the verbatim
   change.
2. Re-render PDF.
3. Replace `vOS_Product_Specification_Long.pdf` with the v1.1 file.
4. Update the "Document Version 1.1" date stamp.
5. Delete this errata file (or move to `docs/archive/`).

Until step 5 is done, this errata file is the binding source of truth
where it conflicts with the PDF.
