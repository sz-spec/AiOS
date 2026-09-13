# vOS Controls → NIST AI 100-5 / AAGATE IFC Mapping

**Item:** P2 (Sprint 22, V1.3) — *doc-only; NOT a counted moat row.*
**Saved:** 2026-06-04 (Opus 4.8)
**Catalog row:** P2 — "NIST AI RMF lacks OS-level controls; agent-OS
enforcement has no standards hook to map against."
**Status:** living mapping — updated as NIST AI 100-5 and the AAGATE
information-flow-control profile firm up.

> **Honest scope.** This is an interpretation + cross-reference document.
> It does not itself enforce anything; it maps the *enforcing* vOS
> modules (the ones that fail-closed) onto the closest published NIST AI
> control concepts so auditors and regulators have a translation table.
> Where NIST has no concrete control yet, the cell says so rather than
> inventing alignment.

---

## 1. Why this mapping exists

NIST AI 100-1 (AI RMF) and the AI 100-5 control-overlay work describe AI
risk-management *functions* (GOVERN / MAP / MEASURE / MANAGE) but, as the
catalog P2 row notes, they do not yet specify *operating-system-level*
controls for agentic workloads. The AAGATE line of work (agentic-AI
information-flow control) is the closest fit for the byte-level IFC and
delegation primitives vOS enforces. This table is the bridge.

## 2. Mapping table

| vOS enforcing control | Module | NIST AI RMF function | AAGATE / IFC concept | Notes |
|---|---|---|---|---|
| Byte-level taint + kernel write-gate (C7/C3) | `backend/security/taint_engine_v2.py`, `kernel/src/sec/taint_gate.c` | MANAGE 2.x (risk response) | Information-flow control: label propagation + sink policy | The strongest direct AAGATE fit — per-byte labels gate egress at the syscall boundary. |
| Outbound-PII shield + BBS release (O3, Primitive a) | `backend/security/outbound_pii_shield.py`, `backend/services/bbs_selective_disclosure.py` | MEASURE 2.x / MANAGE 4.x | Declassification with cryptographic evidence | Release decisions become auditor-verifiable proofs, not log lines. |
| Model provenance + SLOT_START gate (I2) | `backend/services/maif_v2.py` | MAP 1.x (context) / GOVERN 1.x (accountability) | Provenance / AI-BOM | Hash-chained lineage; gates load on a verified chain. |
| Agent capability table (B1) | `backend/core/security/agent_capability_table.py` | GOVERN 2.x (roles) / MANAGE 2.x | Least-privilege / capability scoping | Unforgeable per-tool scoped rights. |
| Delegation chain + IBCT graph (B2, B6) | `backend/services/intent_manifest_builder.py`, `backend/core/security/ibct_engine.py` | GOVERN 2.x (accountability) | Delegated-authority modeling | Attenuating delegation; cross-agent invoke proven in a graph. |
| Identity federation (F1/F4/B.1/B.2/B.4) | `backend/services/identity_federation_bridge.py`, `backend/services/aims_partner_verifier.py` | GOVERN 3.x (third parties) | Cross-domain workload identity (SPIFFE/AIMS) | Cross-org agent identity governance. |
| GPU/CVM isolation gates (E1/E2/E3/E6/O4/D2) | `backend/security/{gpu_driver_gate,accel_ioctl_filter,iommu_dma_guard,perf_counter_lockdown,gpu_alloc_validator,cvm_launch_gate}.py` | MANAGE 2.x (technical safeguards) | Trusted-execution / isolation boundary | Several are `enforced-pending-silicon` (O4, the hardware-bound siblings). |

## 3. Gaps (where NIST has no concrete OS control yet)

- **No syscall-boundary IFC control** in AI 100-1/100-5 — vOS's
  kernel-gate is ahead of the standard here; tracked as a contribution
  candidate for the AAGATE profile.
- **No agent-delegation control family** — B2/B6 (IBCT) map to GOVERN
  accountability only by analogy; the IETF WIMSE / IBCT drafts (F6) are
  the better technical hook and are cited in those modules.
- **No model-provenance control** with a verifiable-chain requirement —
  I2 anticipates one; AI-BOM standardization is in flight.

## 4. References

- NIST AI RMF (AI 100-1) and the AI 100-5 control-overlay work.
- AAGATE — agentic-AI information-flow-control profile (CSA Labs agentic
  NIST AI RMF profile).
- IETF WIMSE drafts (workload identity, AI-agent identity) — see the
  Cluster B module docstrings.
- vOS catalog: `docs/AGENT_ERA_OS_PROBLEMS.md`, roadmap:
  `docs/V1.3_BACKLOG_MASTER_PLAN.md`.
