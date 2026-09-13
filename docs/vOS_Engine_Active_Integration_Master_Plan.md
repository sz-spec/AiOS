# vOS Engine — Active Integration Master Plan (3 assets)

**Date:** 2026-06-09 · **Target:** `vos.v1` @ `2fc51f1` (`feat/shield-integration`, core gate 931/931)
**Source:** `/Users/sz/Desktop/vos` (vos4 Engine + track-1) · **Type:** plan/design (no code committed by this doc)

> **Accuracy notes (so the plan is buildable, not aspirational):**
> - **`cr4_pin` does not "permanently freeze" CR4.** x86 has no CR4 lock. It is a
>   *software pin*: it stores a bit-mask, **re-ORs** it on every `pinned_write()`,
>   and `verify()` **detects** rollback. Protection holds only if all CR4 writes
>   route through `pinned_write()` — it is rollback-*detection* + re-assertion,
>   not a hardware freeze.
> - **`semantic_firewall` is a Stage-1 lexical/corpus filter** (ALLOW/DENY/
>   TRANSFORM with fixed confidences), **not** an ML scorer with "model-routing
>   vectors." The `context` arg is accepted but unused in Stage 1.
> - **The kernel runs in a QEMU guest** (bare-metal UEFI = stub). These kernel
>   protections harden the **guest microkernel**, not the host OS — there is no
>   shipped Hyper-V/macOS-HVF native path. "Windows for AI" is a **vision**,
>   labelled as such in Pillar 2; the shipped reality is demarcated.

---

## Pillar 1 — Functional specifications & signatures (verified)

### 1.1 `semantic_firewall.py` (489 lines, complete; stdlib-only)
- `class SemanticFirewallDecision(Enum)`: `ALLOW="allow"`, `DENY="deny"`, `TRANSFORM="transform"`.
- `@dataclass SemanticFirewallResult`: `decision`, `confidence: float ∈[0,1]` (0.9 on a high-risk lexical match, 0.0 on ALLOW), `transformed_text: Optional[str]`, (+ reason/match fields).
- `class SemanticFirewallDenied(Exception)`: raised by callers when `decision == DENY`.
- `scan(text: str, context: Optional[dict]=None) -> SemanticFirewallResult`: corpus/lexical scan; `_corpus_match(text_lower)` is the matcher. Stage 1 ignores `context` (documented). **Contract: pure function, no I/O, no internal deps.**
- **Test integration:** copy track-1's `test_semantic_firewall.py` (**393 lines**) to `backend/tests/` as `test_api_*`-free unit tests (it tests `scan()` directly, not via HTTP, so no conftest auth needed). Run `pytest backend/tests/test_semantic_firewall.py -p no:xdist` → must be green **before** any wiring.

### 1.2 `cr4_pin.c` (86 lines, complete; 0 stubs)
- Pins candidate bits **SMEP | SMAP | CET | UMIP** (per `cr4_pin.h`).
- `void vos3_cr4_pin_init(void)` — idempotent (`g_cr4_pin_initialized` guard); reads current CR4, pins **only already-set** candidate bits (won't force-enable unsupported silicon).
- `uint64_t vos3_cr4_pin_verify(void)` — returns missing-pinned-bits mask (0 == intact); rollback **detection**.
- `void vos3_cr4_pinned_write(uint64_t value)` — `write_cr4_raw(value | g_cr4_pinned_mask)` — re-asserts pinned bits.
- `uint64_t vos3_cr4_pin_get_mask(void)`.
- Includes: `cr4_pin.h`, `vos/console.h`, `arch/x86_64/cpu.h` (all present in vos.v1).
- **Compile defs:** none new (freestanding). Add both files to `kernel/Makefile` `C_SOURCES`.

### 1.3 `smap_smep.c` (87 lines, complete; 0 stubs)
- `void vos3_smap_smep_init(void)` — CPUID leaf-7 EBX detect (`VOS3_CPUID7_EBX_SMEP_BIT`/`SMAP_BIT`); if supported, `cr4 |= VOS3_CR4_SMEP` (bit 20) / `VOS3_CR4_SMAP` (bit 21) then `write_cr4`; skips cleanly if unsupported.
- `const vos3_smap_smep_caps_t* vos3_smap_smep_get_caps(void)` — caps struct (supported/enabled flags).
- **Note:** no explicit re-entry guard in the source — re-running just re-ORs the same bits (**harmless, no panic**), but Phase 4 adds a guard + the overlap check.

---

## Pillar 2 — Target state ("Windows for AI" — VISION vs shipped reality)

> This pillar is the **aspirational architecture**. The shipped reality is
> demarcated per-row so the plan isn't mistaken for current capability.

### 2.1 System integration map (target)
```
        ┌──────────────────────── HOST (Linux/macOS/Win) ─────────────────────┐
        │  Tauri desktop app  ──IPC──►  FastAPI backend (CPython 3.12)         │
        │                               │   ┌── semantic_firewall.scan() ◄─ NEW│  ← SHIPPABLE NOW (Python)
        │                               │   ├── outbound_pii_shield / regional │
        │                               │   └── router (local-titan 503)       │
        │                               ▼                                       │
        │                         VBus (virtio-serial, CRC32C+HMAC)             │
        └───────────────────────────────┼──────────────────────────────────────┘
                                         │  (QEMU child — NOT bare-metal today)
        ┌──────────────── vos3.elf GUEST microkernel (QEMU) ───────────────────┐
        │  kmain: … cet_init → [smap_smep_init NEW] → [cr4_pin_init NEW] …      │  ← guest-kernel hardening
        │  W^X · KPTI · CET(ENDBR) · SMEP/SMAP enable · CR4 pin/verify          │
        │  vVFS (M3 SecureBoot, default-off) · model slots                      │
        └──────────────────────────────────────────────────────────────────────┘
```
**Honest scope:** third-party/user-space binaries encounter the invariant layer
**inside the QEMU guest microkernel** (SMEP/SMAP/CR4-pin/W^X). The host-side
"invariant" is the FastAPI firewall/egress layer. There is **no bare-metal or
native-hypervisor execution today** (UEFI = stub).

### 2.2 Admin/operator surface (over `/api/compliance/`)
- **Prompt-injection blocks** → expose semantic_firewall counters (allow/deny/transform tallies) at a new `GET /api/compliance/firewall/stats` (admin-gated like the AIBOM/leak routes already landed).
- **Active memory tracing** → already shipped: `POST /api/compliance/leaks/snapshot` (Phase 1.2).
- **Kernel security enforcement** → surface `vos3_cr4_pin_verify()` + `smap_smep_get_caps()` via a VBus diag command (`QUERY_CR4` exists) → an admin `GET /api/compliance/kernel/hardening` (read-only). *(Roadmap — VBus plumbing required.)*

### 2.3 Latency / cross-platform (honest)
- **semantic_firewall:** `scan()` is pure CPU, microseconds for lexical match; call it in the agent-input pre-flight. To avoid event-loop starvation on large inputs, run via `run_in_executor` if it ever grows beyond lexical matching (Stage 1 is fast enough inline).
- **kernel CR4/SMAP:** these protect the **guest microkernel under QEMU**. They do **not** "guarantee cross-platform host stability across Windows (Hyper-V)/macOS" — that framing is not supported; the cross-OS story is QEMU-under-Tauri, and Hyper-V/HVF native ports are roadmap, not shipped.

---

## Pillar 3 — Phased zero-regression TDD rollout

### Phase 1 — Port library modules + tests (LOW risk)
1. `git mv`/copy (in `vos.v1`): `semantic_firewall.py` → `backend/services/`; `test_semantic_firewall.py` → `backend/tests/`; the 2 kernel pairs → `kernel/src/core/` + `kernel/include/vos/`.
2. Run `pytest backend/tests/test_semantic_firewall.py -p no:xdist` → **393-line suite must be green** before any wiring.
3. Import-smoke `semantic_firewall`; build the kernel (`make`) to confirm the 2 C files compile (after adding to `C_SOURCES`). **Do not wire yet.** Commit.

### Phase 2 — Wire the semantic firewall (LOW–MED)
- Inject `scan()` at the **agent-input boundary** (chat/codegen pre-flight in the route layer, beside `outbound_pii_shield`), **not** a global `app.py` middleware (avoids intercepting non-agent traffic + matches existing egress-control placement). On `DENY` → raise `SemanticFirewallDenied` → HTTP 4xx.
- Run on a worker thread only if input is large; Stage-1 lexical is fast enough inline.
- **TDD:** add an integration test (deny a known-malicious prompt → blocked; benign → passes). Run core gate (931) + the firewall tests.

### Phase 3 — Wire CR4 pinning (LOW)
- Add `vos3_cr4_pin_init()` call in `kmain` **after** `vos3_cet_init()` (~line 497) **and after** smap_smep init (Phase 4) — so it pins the *final* enabled CR4 state. Route any later CR4 writes through `vos3_cr4_pinned_write()`.
- **Verify under QEMU:** add a boot `~~CERT~~` assertion `vos3_cr4_pin_verify() == 0` post-init; run `tools/runner/qemu_assert_runner.py` (the M3 harness path) → must stay green.

### Phase 4 — Wire SMAP/SMEP (LOW–MED; overlap gate)
- **Pre-req (hard):** locate any existing CR4.SMAP/SMEP enablement in vos.v1 boot (`grep CR4.*SMAP` across `boot/`, `core/`). The audit found none, but confirm — if one exists, reconcile rather than double-enable.
- Add `vos3_smap_smep_init()` in `kmain` **before** `cr4_pin_init()` (set bits, then pin). Add a re-entry guard (`g_smap_smep_initialized`) for cleanliness (re-OR is already panic-free).
- **Verify:** boot `~~CERT~~` for `caps.smap_enabled`/`smep_enabled`; confirm the existing user-copy `stac`/`clac` paths still pass (SMAP now actually enabled). QEMU boot green.

### Gate invariants (every phase)
- Backend gate `security/ + services/` stays **931/931** (kernel changes don't touch it; firewall is inert until wired).
- Kernel changes verified by **clean `make` + QEMU boot + `~~CERT~~`** (not just compile).
- One asset per commit; rollback-safe on `feat/shield-integration`.

---

## Finish ledger
Plan covers all 3 pillars with **verified signatures**, an **honest** target-state
(vision vs shipped demarcated; no false "hardware CR4 freeze" or "cross-OS host
guarantee"), and a 4-phase TDD rollout matching the feasibility risk grades
(`docs/vOS_Engine_Assets_Porting_Feasibility_Analysis.md`). Moat unchanged
(49/80) — these are hardening/security infra ports, not new moat rows. Nothing
executed by this doc.
