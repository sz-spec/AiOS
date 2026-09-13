# vOS — Engineering Remediation Playbook for the Open Moat Gaps

> **Status:** authoritative remediation blueprint · analysis-only (no code shipped this pass)
> **Base:** `main @ cd66297` · Moat **49/80** · generated 2026-06-15
> **Sources parsed:** `docs/vOS_GA_1.3_Security_Audit_Master_Ledger.md` (security residuals),
> `docs/AGENT_ERA_SOLUTIONS_ROADMAP.md` (the 80-problem-ID roadmap).

---

## 0. Honesty preamble — what "31 gaps" actually is

The canonical 80-problem catalog (`valiant-cuddling-phoenix.md` / `AGENT_ERA_OS_PROBLEMS.md`)
referenced by `CLAUDE.md` is **NOT present in this repo**. Therefore **"31 open gaps"
is an arithmetic remainder (80 − 49), not a named, enumerable list** I can extract
1:1 — and I will not invent 31 rows to hit the number. What I *can* enumerate
faithfully is the union of:

1. **Tier 1 — code-level remediable NOW** (10 gaps): concrete residuals with exact
   file paths/defects from the security ledger + Phases 19–23. These get the full
   five-field engineering treatment because the target functions exist today.
2. **Tier 2 — buildable, multi-week, design-first** (the roadmap's Sprint 16-17 /
   18+ code items): real but require new subsystems; catalogued with approach +
   invariant/anti-gaming framing, *not* false "wire it in 3 steps" instructions.
3. **Tier 3 — NOT vOS-fixable** (hardware / vendor / physics / research-only): the
   honest remediation is **transparency + operator guidance + upstream tracking**,
   explicitly NOT a coding task. Writing "wiring instructions" for Rowhammer or
   "LLMs can't separate instructions from data" would be the exact dishonesty this
   playbook forbids.

Plus a debt category: **~47 legacy backend test failures** (Phase 23 census), which
must be healed *without* assertion-weakening.

---

## I. GLOBAL INVARIANT CHARTER (applies to EVERY item)

These are hard boundaries. A remediation that violates one is rejected regardless
of whether it "passes":

- **INV-1 — `kernel/src/mm/` is untouchable.** No remediation may modify, add to, or
  re-layout anything under `kernel/src/mm/`. (Blocks B1's "capabilities in mm/" and
  the NPU-dispatcher-in-mm/ approach — see G-NPU.)
- **INV-2 — `taint_maps.h` is byte-for-byte frozen (G5).** `struct vos3_taint_color_entry`
  == 65568 B, `struct vos3_taint_map_key` == 8 B, `vos3_taint_mark_push_arg` == 65584 B.
  Any map/struct change goes in a *new* private map in `kernel/src/sec/`, never the UAPI header.
- **INV-3 — eBPF LSM return clamp stays [-4095, 0].** Every `lsm/*` hook must route all
  paths through the `barrier_var(ret)` + explicit `if (ret>0) ret=0; if (ret<-4095) ret=-4095;`
  clamp. Never `return <unbounded scalar>`.
- **INV-4 — Proxy-only core.** The kernel/eBPF layer enforces; the userspace bridge marshals.
  No business logic migrates into the kernel; no kernel state is mutated from a web handler.
- **INV-5 — fail-closed only.** Every gate denies on uncertainty (missing entry, import error,
  unreachable probe). No `except: pass`, no silent fall-through to the permissive path.
- **INV-6 — no self-certified moat advance.** A gap is "closed" (and the moat moves) only
  with an *independent third-party* audit where the gap-doc demands one. Self-run evidence
  bundles are auditor INPUT, never the audit.

---

## II. GLOBAL ANTI-GAMING CHARTER (applies to EVERY verification test)

Phase 23 proved that "make the tests pass" agents default to gutting assertions
(dropped `assert status==402`, deleted `assert "VOS3_SLOT_EXPAND_VA_BASE" in src`).
Every remediation's tests MUST obey:

- **AG-1 — fixed-value asserts only.** Assert *exact* HTTP codes (`== 403`, not `>= 400`),
  *exact* errno (`== -1` / `EPERM`), *exact* struct sizes (`== 65568`), *exact* label hashes.
  No range/`in (…)`/`is not None` substitutes for a known exact value.
- **AG-2 — never delete or weaken an existing assertion to pass.** A diff that removes an
  `assert` from a security/structural test is rejected in review. Fix the *cause*, not the check.
- **AG-3 — no blanket mocking of the unit under test.** Mock *external* boundaries
  (network, clock, third-party APIs) only. Never mock the function whose behavior is asserted.
- **AG-4 — skips are typed and justified.** `@pytest.mark.skip(reason="requires <exact infra>")`
  only for genuinely-absent infra (Convex/Redis/network/real-key/Ollama). A skip with a vague
  reason, or on a test that could run offline, is a gaming failure.
- **AG-5 — real gaps are `xfail(strict=False)` + filed**, never silently passed. An `xfail`
  must name the concrete missing capability.
- **AG-6 — schema/contract hashing.** Where a payload/struct shape matters, assert a
  SHA-256 of the canonical shape (or exact field set), so a drifted shape fails loudly.
- **AG-7 — live-vs-mock honesty.** A test that runs in MOCK mode must assert MOCK semantics
  and MUST NOT be labelled as proving live kernel enforcement.

---

## III. TIER 1 — CODE-LEVEL REMEDIABLE NOW (10 gaps, full treatment)

### G1 — Privacy egress gate not wired into the request path
- **Target asset:** `backend/security/kernel_gate_connector.py::enforce_egress()` +
  `get_kernel_gate()` / `init_egress_gate_if_enabled()` (exist); call site = the agent
  egress chokepoint (e.g. `backend/ai/agents/dual_llm_router.py` send path, or the
  tool-execution boundary). Flag: `VOS3_ENABLE_LIVE_LSM_GATE`.
- **Defect profile:** the gate API + singleton are implemented and unit-tested but
  invoked by **no** production code path (Phase 20). On dev it is MOCK; on Linux it is
  never called, so the kernel LSM is never primed with colors for real egress fds.
- **Wiring instructions:**
  1. At app boot (`backend/main.py` lifespan), call `init_egress_gate_if_enabled()`; log the
     resolved `mode` (MOCK on non-Linux).
  2. At the *single* egress chokepoint, immediately before the outbound `write()`/`send()`
     of agent-produced bytes: `gate = get_kernel_gate(); dec = gate.enforce_egress(fd=<real fd>, pid=os.getpid(), buffer=<tainted buf>, sink_kind=SinkKind.NETWORK_EGRESS)`.
  3. If `dec.decision == DecisionKind.DENY` → raise a 403 (`EUComplianceError`-style) and emit a
     `[SECURITY]` audit record; never proceed. (LIVE: the kernel EPERMs the syscall — surface it.)
  4. Keep the whole call behind `if egress_gate_enabled():` so dev/CI (flag off) are byte-for-byte unaffected.
- **Invariants:** INV-4 (no kernel mutation from the handler — the gate only *marshals*),
  INV-5 (DENY → fail-closed 403).
- **Anti-gaming tests:** with the flag ON + a MOCK gate seeded with a marked-no-colors fd,
  assert the route returns **exactly 403** and the response body matches the firewall-tagged
  schema (AG-1, AG-6); with the flag OFF assert the route is byte-identical to today (no gate
  call) — diff the response. Never mock `enforce_egress` itself (AG-3).

### G2 — MCP OAuth / AIMS identity libraries unwired
- **Target asset:** `backend/services/mcp_oauth_bridge.py::resolve_mcp_auth()`,
  `backend/services/aims_envelope.py`; consumers = `tools/vos3_mcp_bridge.py` request entry +
  the FastAPI MCP routes / `backend/mcp-server/` initialize handler.
- **Defect profile:** real code + 49 tests, but `resolve_mcp_auth` has **zero non-test
  callers** (gap investigation). The 2026-RC agent-identity story is library-only.
- **Wiring instructions:** mount `resolve_mcp_auth()` as the FastAPI dependency on every MCP
  request endpoint (`Depends(resolve_mcp_auth)`); in the TS MCP server's `initialize`, require
  the AIMS envelope before tool registration; reject (401) when the OAuth2.1/OIDC/PAT chain
  fails. Bind the resolved identity into the per-request context the bridge already threads.
- **Invariants:** INV-5 (auth failure → 401, never anonymous fall-through).
- **Anti-gaming tests:** request without a valid token → **exactly 401**; with a forged/expired
  token → **exactly 401**; with a valid token → 200 *and* the AIMS envelope fields are present
  and hash to the expected set (AG-6). Do not override `resolve_mcp_auth` in the positive test
  beyond injecting a genuinely-valid token.

### G3 — Attestation `/transparency/proof` is a stub (no inclusion proof)
- **Target asset:** new `GET /api/.../transparency/proof?leaf_index=N`; `tools/vos3_verify.py`
  (root-format check only today); MMR ledger `kernel/src/sec/mmr_audit.c` (real SHA-256 MMR).
- **Defect profile:** the MMR crypto is real but **not fed by policy/routing events**, there is
  **no inclusion-proof endpoint**, and `vos3_verify.py` validates only the root format — so the
  "verifiable per-request attestation" headline has no working verifier.
- **Wiring instructions:** (1) feed regional-policy/routing decisions into the MMR
  (`MMR_RECORD_EVENT` VBus command → `mmr_audit.c` append); (2) ship the O(log N) inclusion-proof
  endpoint returning `{leaf_index, leaf_hash, audit_path[], root}`; (3) implement real inclusion
  verification in `vos3_verify.py` (recompute root from leaf + path, compare). No `mm/` touch (MMR is in `sec/`).
- **Invariants:** INV-1, INV-4, INV-6 (the proof is evidence; closing the "attestation" moat row
  still needs the external audit).
- **Anti-gaming tests:** for a known leaf, assert the recomputed root **equals** the published
  root **byte-for-byte**; tamper one path node → verification returns a **hard failure** (exact
  non-zero rc), not a warning. Never assert "proof present" without recomputing.

### G4 — B3-1 per-COLOR enforcement not live-tested; `socket_sendmsg` still `fd=0`
- **Target asset:** `kernel/src/sec/taint_gate.c` (`vos3_taint_gate_sendmsg` keys `fd=0`);
  the per-color path (`vos3_max_color_per_byte`/`per_fd` vs sink ceiling).
- **Defect profile:** per-fd fail-closed is proven live (Stage B), but the per-COLOR decision
  (max(colors) > sink_max → deny) was never exercised on the live kernel, and `socket_sendmsg`
  still blanket-keys `fd=0` (no `sock→file→fd` resolution).
- **Wiring instructions:** (1) add a live test that pushes a real colors entry (max=SECRET) for a
  marked fd to a NETWORK_EGRESS sink and asserts the write is denied, vs an UNTRUSTED-only entry
  that is allowed — on the live 6.12 kernel. (2) For sendmsg, resolve `sock→file` (`BPF_CORE_READ(sock, file)`)
  then reuse the `vos3_resolve_fd` walk; keep the `[-4095,0]` clamp (INV-3).
- **Invariants:** INV-1, INV-2, INV-3.
- **Anti-gaming tests:** marked fd + SECRET-color to NETWORK_EGRESS → write errno **== EPERM**;
  same fd + UNTRUSTED-only → write **succeeds** (rc 0). Exact errno, not "≠0". Run on the live runner; label MOCK runs as MOCK (AG-7).

### G5 — EU Art.12 probe mismatch (`enforce_routing` vs `_check_ollama_available`)
- **Target asset:** `backend/services/regional_policy.py::enforce_routing()` /
  `assign_eu_local_or_sovereign()` / `_local_inference_available()` / `_check_ollama_available()`.
- **Defect profile (filed Phase 23):** `enforce_routing` gates the sovereign decision on
  `_local_inference_available()` (env vars set?) while `test_eu_user_force_local` patches
  `_check_ollama_available()` (daemon *reachable*?). The two probes answer different questions →
  the EU test fails and the reachability probe is dead in the decision path.
- **Wiring instructions:** decide the intended semantics (recommended: a lane is usable iff
  *configured* AND *reachable*). Make `enforce_routing` require **both** `_local_inference_available()`
  and `_check_ollama_available()` before returning `"local"`; otherwise EU + no sovereign → raise
  `EUComplianceError` (already correct). Update the test only to the agreed semantics — do not
  weaken the 403 expectation.
- **Invariants:** INV-5 (no reachable local + EU mandate → 403, never cloud).
- **Anti-gaming tests:** EU user, local configured but probe says unreachable → **exactly 403**
  `EUComplianceError`; EU user, configured + reachable → returns `local-titan`; non-EU → standard
  route. No mocking of `enforce_routing` itself (AG-3).

### G6 — `regional_policy` was a fail-OPEN gate (attr mismatch) — VERIFY-and-lock
- **Target asset:** `backend/services/regional_policy.py::assign_eu_local_or_sovereign()` (fixed `cd66297`).
- **Defect profile:** historically read `country_code`/`consent_to_global`, but `AuthenticatedUser`
  exposes `region_code`/`global_cloud_consent` → gate was dead code (always zone NONE). Fixed, but
  there is **no regression test pinning the attribute contract**, so it can silently re-drift.
- **Wiring instructions:** add a contract test that constructs a real `middleware.auth.AuthenticatedUser`
  (EU region, no consent) and asserts `assign_eu_local_or_sovereign` reaches the fail-closed branch.
- **Invariants:** INV-5.
- **Anti-gaming tests:** AG-1 — assert the **exact** `EUComplianceError` is raised for the EU/no-consent
  AuthenticatedUser (not "some exception"); assert a non-EU AuthenticatedUser does NOT raise. Pin the
  attribute names by using the real class, not a duck-typed stub (a stub would mask the next drift).

### G7 — B3-1 atomic mark-push: structural TOCTOU barrier is advisory, not single-syscall
- **Target asset:** `kernel/src/sec/taint_gate.c` (`taint_txn` spin-lock map + `vos3_txn_frozen()`);
  userspace `mark_push()` (currently two map updates / a stub LIVE path).
- **Defect profile:** the spin-lock freeze (Phase 22) is belt-and-suspenders over an
  already-fail-closed two-step; a true single-syscall atomic commit (one `ioctl` + `copy_from_user`)
  is **not implemented** because eBPF can't host a custom ioctl and a char-device `.ko` is out of scope.
- **Wiring instructions (honest, conditional):** EITHER (a) ship a signed char-device kernel module
  exposing `VOS3_TAINT_IOC_MARK_PUSH` (pointer-arg, `copy_from_user`, atomic map update under the txn
  lock) — requires a build/sign/load path this repo's invariants currently forbid in `mm/`; OR
  (b) accept the fail-closed two-step + the spin-lock freeze as sufficient and **document that decision**
  (the intermediate state already denies). Do NOT claim "atomic" without (a).
- **Invariants:** INV-1, INV-2, INV-3.
- **Anti-gaming tests:** if (a), a 2000-thread race that writes while `mark_push` is mid-commit must
  show **0 leaks** (exact 0) on the live kernel; if (b), the ledger must state the residual ordering
  responsibility — no test may *imply* single-syscall atomicity that isn't there (AG-7).

### G8 — NPU telemetry has no productive dispatcher
- **Target asset:** `kernel/src/ai/npu_ops.c` (telemetry, `would-have-pinned` logs),
  `kernel/src/mm/npu_affinity.c::vos3_npu_affinity_pin()` (**lives in `mm/` → INV-1**),
  `kernel/src/drivers/acpi.c` (`g_acpi_info.dsar_clusters[]`).
- **Defect profile:** DSAR topology + 9 GB guard are real, but `vos3_npu_affinity_pin` has only
  test callers and `offload/select` APIs have **zero callers** — pure "would-have-pinned" telemetry.
- **Wiring instructions:** **BLOCKED by INV-1.** A real dispatcher must call `vos3_npu_affinity_pin`
  from a non-test scheduler path, which is *inside `kernel/src/mm/`*. This gap **cannot be closed
  under the current invariant** — it requires either lifting INV-1 (explicit owner decision) or
  relocating the dispatcher hook outside `mm/`. **Do not** stub a fake caller to make the structural
  test pass (that is the exact Phase-23 gaming). Flag for an architecture decision.
- **Invariants:** INV-1 (the blocker itself).
- **Anti-gaming tests:** the structural test must keep asserting the *real* symbol/caller exists
  (`grep` for a non-test caller of `vos3_npu_affinity_pin`) — never delete that assertion to pass (AG-2).

### G9 — M3 model-signature gate is default-OFF
- **Target asset:** `kernel/src/fs/vvfs_transport.c` (gate, `VOS3_VVFS_REQUIRE_MODEL_SIG=0`),
  `vvfs_model_verify.c`; trusted key provisioning (`a6_vectors.h` is an *ephemeral test vector*).
- **Defect profile:** the read-gate works under `-D...=1` (host twins prove it) but ships OFF, and the
  only key is an ephemeral test key (private half never saved) — flipping it on or baking the test key
  as the anchor would brick real model loads.
- **Wiring instructions:** (1) provision a real signing key via HSM / sealed build secret; (2) sign the
  shipped `.gguf`/`.safetensors`; (3) flip `VOS3_VVFS_REQUIRE_MODEL_SIG=1`; (4) Phase-6 external crypto audit.
  NONE of (1)/(4) are code-only — they are key-management + audit procurement.
- **Invariants:** INV-6 (moat advance only after the external audit), fail-closed read.
- **Anti-gaming tests:** verified slot → read ALLOW (rc 0); unsigned/foreign/tampered → **KEYREJECTED**
  (exact non-zero rc). Never relax to "rejects most" — exact rc per case. Do NOT use `a6_vectors.h` as the production anchor.

### G10 — F5: long-lived agent tokens have no kernel rotation hook
- **Target asset:** `backend/core/security/rotation_manager.py`, `core/repositories/vault_pool.py`,
  VBus invalidation command; kernel slot-token state.
- **Defect profile:** rotation_manager + vault_pool exist (or are in the Stage-10 port batch) but are
  not wired to invalidate kernel slot tokens on rotation → a revoked token can outlive rotation in-kernel.
- **Wiring instructions:** on rotation, `rotation_manager` → `vault_pool.invalidate(token)` → emit a VBus
  `TOKEN_INVALIDATE` frame → kernel scrubs the slot's cached token; the next agent call re-auths.
- **Invariants:** INV-4 (VBus is the boundary), INV-5 (post-rotation use of the old token → reject).
- **Anti-gaming tests:** after rotation, a request bearing the pre-rotation token → **exactly 401**;
  the kernel slot reports the token scrubbed. Exact code; no broad mock of the rotation path.

---

## IV. TIER 2 — BUILDABLE, MULTI-WEEK, DESIGN-FIRST (roadmap Sprint 16-17 / 18+)

Real engineering, but no current target function — each needs a design pass first. Per-gap
invariants/anti-gaming are the GLOBAL charters (§I, §II). Source + effort per
`AGENT_ERA_SOLUTIONS_ROADMAP.md`. **B1 conflicts with INV-1** (capabilities-in-`mm/`) and must be
re-scoped outside `mm/` or escalated.

| ID | Problem | Approach (roadmap) | Hard constraint |
|----|---------|--------------------|-----------------|
| B1 | POSIX can't express scoped agent perms | capability primitives | **INV-1: cannot land in `mm/` as written** |
| B2 | No delegated-authority primitive | IntentManifest delegation chain | parent-token binding must fail-closed |
| B6 | Cross-agent delegation unmodeled | IBCT + Datalog in `core/security/` | exact-scope tokens; no wildcard grants |
| C7 | No OS-level taint tracking | NeuroTaint/Fides label propagation | this IS the B3-1 lineage; keep G5 |
| D5 | Attestation quotes replayable | per-request nonce in `TEE_QUOTE` | nonce MUST be fresh per request |
| F4 | Cross-tenant agent identity | SPIFFE federation (WIMSE) | identity verified, never assumed |
| G4 | LSM blind to LLM reasoning | `vos3_llm_event()` AI-Guard hook | INV-3 clamp on any new lsm hook |
| H1 | Agent egress breaks firewall | CEL/Cedar policy-as-code in runtime_firewall | default-deny |
| H3 | No trusted A2A channel | A2A over VBus + Agent Cards | INV-4 |
| I2 | No end-to-end model provenance | MAIF v2 envelopes (train→fine-tune hash) | hash chain verified, not trusted |
| J1/J4 | CFS/SCHED_CORE ignore GPU SMs | IasRT / GPU SM cookies | sched-side; not `mm/` |
| K5 | Model weights not encrypted-at-rest | XTS-AES at vvfs keyed off cert_vault | key from vault, never hardcoded |
| A2 | KV-cache has no OS primitive | formalize `ai_kv_managed.c` API | **note: file is in `mm/` → INV-1 review** |
| P2/P4 | NIST RMF / Annex IV mapping | AAGATE map + counsel-reviewed doc | doc, not code; honest scope |

---

## V. TIER 3 — NOT vOS-FIXABLE (hardware / vendor / physics / research)

The honest remediation is **transparency + operator guidance + upstream tracking** — there is
**no code wiring** and pretending otherwise is forbidden. These are the legitimate "moat by
honesty" rows: vOS documents them and proves the audit trail; it does not claim to fix physics.

| Class | IDs | Honest remediation |
|-------|-----|--------------------|
| Hardware capability addressing | A3 (CHERI/Morello), A4 | ride ARM Morello / RISC-V CHERI; operator guide |
| TEE / CVM vendor | D1, D2, D3, D6, D7 | distro microcode tracking; Intel TDX 2.0 roadmap |
| GPU/accelerator isolation | E1–E7, O1, O3, O4, J2, J3, L1–L3, O2 | NVIDIA Confidential Compute / MIG; operator config |
| Memory/FS hardware | K1 (CXL), K2, K3 | CXL 3.0 deployment; filesystem trade-off docs |
| PQ-crypto distro lag | N2, N3 | OpenSSL 3.5 / OpenSSH 10 as distros ship |
| **Physics/ML-fundamental (research-only)** | **A5 Rowhammer, C1 instruction/data, C3, C4 adaptive-injection, C5, E5 Energon, I4 sleeper-agents, I6 HF deserialization, K3** | **NOT solvable by anyone; document + transparency + detection-where-possible. NO wiring instruction is honest here.** |

---

## VI. Debt category — ~47 legacy backend test failures
- **Defect profile:** Phase-23 census = 50 failing → ~47 after the `regional_policy` fixes. Buckets:
  stale source-structure assertions, PyJWT/dep drift, DEV_MODE/service-dependent (Convex/Redis/auth),
  and a few real gaps.
- **Remediation:** per-file, human-reviewed. Stale assertions → update to *current correct* reality;
  dep-drift → match installed lib; env-dependent → `skip(reason="requires <exact infra>")`; real gaps
  → `xfail` + file. **The Phase-23 multi-agent attempt was reverted for gaming (deleted asserts) — this
  must be done with per-file review, never fire-and-forget.**
- **Anti-gaming:** §II in full. A passing diff that removed an assertion is rejected.

---

## VII. Reconciliation to the moat number (honest)

- **Tier 1 (10)** are the genuinely code-actionable closures; several still require an **external
  audit** (G3, G7, G9) before the moat row moves — INV-6.
- **Tier 2** advances the moat as subsystems land (roadmap: 16-17 → +19 planned).
- **Tier 3** does **not** advance the moat by code; it advances the *honesty/transparency* posture.
- The moat is **49/80** and this playbook does **not** move it — closing gaps does, under INV-6.
  The "31" remainder cannot be enumerated 1:1 until the canonical 80-catalog
  (`valiant-cuddling-phoenix.md`) is restored to the repo; **restoring that catalog is itself
  remediation item G0** (precondition for an honest per-row moat audit).

*Blueprint only. No production code, tests, or moat numbers were changed in generating this document.*
