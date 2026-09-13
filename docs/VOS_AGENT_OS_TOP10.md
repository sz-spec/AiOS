# vOS Agent-OS Top 10 — Operating-System-Layer Risks for the AI Agent Era

**Status:** Draft v1.0 — Sprint 15 / Item P6
**Date:** 2026-05-24
**Audience:** operators, regulators, auditors, vOS competitors who want to do the same exercise
**Author:** vOS Engineering Team
**License:** CC-BY 4.0 — copy, adapt, attribute. Pull requests welcome at the public repo.

---

## Why this list exists

OWASP published the **Top 10 for Agentic Applications 2026** (https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) — that list covers *application-layer* risks: prompt injection, tool misuse, memory poisoning, etc. It is the right reference for app teams.

**This list complements OWASP by enumerating the operating-system-layer risks that no application-layer mitigation can fully address.** These are the threats vOS solves at the kernel, sandbox, attestation, and supply-chain layers — and the threats that **any** AI-agent OS (vOS, Linux + agent stack, K8s + Sandbox CRD, Fuchsia, etc.) must answer.

We adopt the OWASP numbering convention (vOA01..vOA10) to make cross-reference easy. Each item carries:

  - **Risk statement** — one sentence
  - **Why the OS, not the app** — what makes this OS-layer rather than app-layer
  - **Sources** — tier-1 references from the May-2026 industry survey
  - **vOS posture** — what we ship today
  - **Operator action** — what the consumer of an agent OS must do

Every item is sourced from the 80-problem catalog in `docs/AGENT_ERA_OS_PROBLEMS.md`; this Top 10 is the *most-severe-and-most-OS-shaped* subset.

---

## vOA01 — Sandbox-escape from agent-generated code execution

**Risk statement.** An agent generates code, the OS executes it, and the executing code escapes the sandbox to access host secrets, sibling-agent state, or the kernel.

**Why the OS, not the app.** The application generated the code in good faith; the failure is the OS's containment primitive. Sandbox escape is unsolved at the app layer because an LLM that can write working code can sometimes write working exploits.

**Industry severity (May 2026).** Frontier models hit 13% success on real-world web exploit chains in the Anthropic eval. The April-2026 Anthropic Claude Chrome extension ShadowPrompt disclosure (March 2026) is the first weaponized example in a production agent runtime.

**Sources.**
- [Quantifying Frontier LLM Capabilities for Container Sandbox Escape](https://arxiv.org/html/2603.02277v1)
- [Multi-Agent gVisor Isolation (gVisor blog, April 2026)](https://gvisor.dev/blog/2026/04/15/magi-multi-agent-gvisor-isolation/)
- [Running Agents on Kubernetes with Agent Sandbox (March 2026)](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox/)

**vOS posture.** ✅ Sprint 15 / C6 + C8 — gVisor MAGI per-agent sentry isolation + Kubernetes Sandbox CRD default. Backend `backend/sandbox/gvisor_config.json` enforces 16 syscall denies (ptrace, init_module, bpf, kexec_load, mount, swapon, …) under runsc + MAGI per-agent kernel pair.

**Operator action.** Provision nodes with `vos3.io/gvisor-installed=true`; apply `infra/k8s/sandboxes/sandbox-crd.yaml` once per cluster.

---

## vOA02 — Indirect prompt injection from tool outputs

**Risk statement.** The agent reads data from a tool (web page, email, API response, calendar invite) that carries hidden instructions; the agent treats them as commands.

**Why the OS, not the app.** Application defenses (spotlighting, instruction-data separators) get >85% bypassed under adaptive attacks per the May-2026 meta-analysis of 78 studies. The OS-layer defense is **dual-LLM isolation**: a quarantined model handles untrusted input, never has tool-call privileges, and its outputs are sanitized before passing to a privileged model.

**Industry severity.** EchoLeak (June 2025, Microsoft 365 Copilot) is the first confirmed real-world weaponization of indirect prompt injection for data exfiltration in production.

**Sources.**
- [Evaluation of Prompt Injection Defenses](https://arxiv.org/html/2604.23887)
- [EchoLeak paper](https://arxiv.org/pdf/2509.10540)
- [Defeating Prompt Injections by Design](https://arxiv.org/pdf/2503.18813)

**vOS posture.** 🟡 Wave 3 / C2 — Dual-LLM Privileged/Quarantined pattern lands in Sprint 15 Wave 3 (this commit ships through `backend/ai/agents/dual_llm_router.py` later this week).

**Operator action.** Plan for Wave 3 deployment; restrict tool outputs from `trust_remote_code: true`–enabled HF models pending Sprint 15 / I6 scanner (already shipped).

---

## vOA03 — Memory disclosure across AI workloads

**Risk statement.** A KV cache from agent A's session leaks into agent B's address space (via swap, side channel, or naïve mmap sharing).

**Why the OS, not the app.** The OS owns memory protection; the app has no visibility into page-table state.

**Industry severity.** GPU power + thermal side channels recover transformer weights at 89% family-ID accuracy from 100 cm through glass (Energon paper, May 2026). The classical mitigation is hardware-level — but the OS-side workload-hint API ensures the kernel doesn't accidentally page out a model weight region to a shared swap file.

**Sources.**
- [Energon: Unveiling Transformers from GPU Power+Thermal Side-Channels](https://arxiv.org/pdf/2508.01768)
- [Kraken: EM Side-Channel Attacks on DNNs](https://arxiv.org/pdf/2603.02891)
- [Composable OS Kernel Architectures for Autonomous Intelligence](https://arxiv.org/pdf/2508.00604)

**vOS posture.** ✅ Sprint 15 / A1 — `vos3_ai_guard_set_workload_hint()` separates MODEL_WEIGHTS (RO + pinned, no swap) from KV_CACHE (RW + swap-eligible) at page-policy level. Hardware-level side channels remain open research (industry-wide).

**Operator action.** Enable NVIDIA Confidential Compute mode on B200+ for residual side-channel mitigation; track Intel TDX 2.0 release.

---

## vOA04 — Cryptographic workload identity, not human identity

**Risk statement.** Agents are not humans — they need cryptographic, attested, short-lived identity tokens. Using long-lived API keys or human OAuth flows for agents is the equivalent of giving every server in a fleet root SSH access via shared password.

**Why the OS, not the app.** The app can't sign its own attestation. The OS (or its runtime) is the trust root that emits the WIT-SVID / IETF AAP token.

**Industry severity.** NIST NCCoE published an explicit concept paper for AI agent identity + authorization in February 2026; IETF has three competing drafts (AAP, AIMS, OIDC-A); the Singapore CSA Addendum mandates short-lived OAuth 2.0/OIDC tokens for agents and **prohibits cross-agent privilege delegation**.

**Sources.**
- [IETF draft-ietf-oauth-spiffe-client-auth-01](https://datatracker.ietf.org/doc/draft-ietf-oauth-spiffe-client-auth/)
- [IETF draft-ietf-wimse-arch-07](https://datatracker.ietf.org/doc/draft-ietf-wimse-arch/)
- [NIST NCCoE: AI Agent Identity Concept Paper (Feb 2026)](https://www.nccoe.nist.gov/sites/default/files/2026-02/accelerating-the-adoption-of-software-and-ai-agent-identity-and-authorization-concept-paper.pdf)
- [Authorization Propagation in Multi-Agent AI Systems](https://arxiv.org/html/2605.05440v1)

**vOS posture.** ✅ Sprint 15 / F1 — `backend/core/security/spiffe_workload_identity.py` implements the IETF draft-ietf-oauth-spiffe-client-auth-01 WIT-SVID verifier (ES256 + EdDSA, trust-bundle-bound, clock-skew tolerant, audience-checked). `AuthenticatedUser.spiffe_id` field carries the SPIFFE ID through every downstream consumer.

**Operator action.** Stand up SPIRE (or equivalent) in your cluster; configure `VOS3_SPIFFE_TRUST_BUNDLE_PATH` + `VOS3_SPIFFE_EXPECTED_AUDIENCE`.

---

## vOA05 — Audit-trail capture of agent decisions, not just syscalls

**Risk statement.** journald/auditd captures syscalls, not "the agent decided to call tool X because of LLM reasoning Y". EU AI Act Article 73 demands the latter for 72h serious-incident reporting.

**Why the OS, not the app.** The audit trail must be tamper-evident at the kernel level — an app-only audit log is suspect because a compromised agent could have written it.

**Sources.**
- [EU AI Act Annex IV](https://artificialintelligenceact.eu/annex/4/)
- [OpenTelemetry GenAI agent spans semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
- [Constant-Size Cryptographic Evidence Structures for Regulated AI Workflows](https://arxiv.org/abs/2511.17118)
- [AEGIS: Pre-Execution Firewall + Audit Layer](https://arxiv.org/abs/2603.12621)

**vOS posture.** ✅ Sprint 14.1 + Sprint 15 / G1 + G5 — Kernel audit ring (`kernel/src/mm/audit_ring.c`) + compliance store + Sprint 15 OpenTelemetry GenAI semantic-conventions emitter (`backend/services/telemetry_genai.py`) with PII-redacted MAIF envelope wrap for SIEM ingestion. Article 73 endpoint at `POST /api/compliance/incident/report` auto-derives 2-day (death-harm) and 15-day (other) SLAs.

**Operator action.** Set `OTEL_EXPORTER_OTLP_ENDPOINT` to your tracing backend; set `VOS3_OTEL_AUDIT_ENABLED=1` for compliance-store mirroring.

---

## vOA06 — Model integrity from disk to RAM to RTMR

**Risk statement.** An attacker swaps model weights between SBOM-generation time and runtime-load time. Without a chain that hashes the bytes at load and binds the hash to the platform attestation, the SBOM is just paperwork.

**Why the OS, not the app.** The hash chain crosses the kernel/user boundary. The kernel must measure the bytes it actually maps; the user-side SBOM/OMS bundle proves what we *expected* those bytes to be; an external attestation verifier compares them.

**Sources.**
- [Sigstore Model Transparency v1.0](https://blog.sigstore.dev/model-transparency-v1.0/)
- [OpenSSF Model Signing case study (Google)](https://openssf.org/blog/2025/07/23/case-study-google-secures-machine-learning-models-with-sigstore/)
- [HF Models: Large-Scale Exploit Study](https://arxiv.org/pdf/2410.04490)
- [A Rusty Link: Detecting Evil Configurations](https://arxiv.org/pdf/2505.01067)
- [Models Are Codes](https://arxiv.org/pdf/2409.09368)

**vOS posture.** ✅ Sprint 14.1 + Sprint 15 / I1 + I3 + I6 —
- **I1**: CycloneDX 1.5 AI/ML-BOM section in `infra/security/build_sbom.py` (machine-learning-model components with SHA-256, OMS bundle ref, model-card ref).
- **I3**: OMS v1.0 verifier in `infra/security/model_signer.py` — `verify_model_signature()` called at SLOT_START; Sprint 14.1 RTMR[2] then extends with the same SHA-384.
- **I6**: HF config-file scanner `backend/security/hf_config_scanner.py` catches the pre-load RCE class (pickle imports, auto_map remote, trust_remote_code, .py shims in tokenizer configs).

**Operator action.** Ship every model with sibling `.sig` + `.bundle.json` files; run `bash scripts/verify_release.sh` before promotion.

---

## vOA07 — Agent-to-agent communication needs a kernel primitive

**Risk statement.** Agent A asks Agent B to call API X. Without a kernel-attested local channel, there's no transitive trust model — and a compromised Agent B can claim Agent A's authority for any downstream call.

**Why the OS, not the app.** The transitive-trust primitive must be cryptographically anchored to a non-forgeable identity (SPIFFE) and a non-forgeable execution context (RTMR-attested sandbox). Neither can be app-level.

**Sources.**
- [MCP/A2A Survey](https://arxiv.org/pdf/2505.02279)
- [MCP 2026-07-28 release candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)
- [Authorization Propagation in Multi-Agent AI Systems](https://arxiv.org/html/2605.05440v1)
- [CNCF Cloud-Native Agentic Standards](https://www.cncf.io/blog/2026/03/23/cloud-native-agentic-standards/)

**vOS posture.** 🟡 Sprint 15 / H5 (mesh labels) + Wave 3 / F2 (MCP RC integration) + Stage 14.B / H3 (A2A over VBus, Q3-Q4 2026). The full kernel A2A primitive is Stage 14 work; today's deployment uses Istio mTLS + WIT-SVID workload identity (Sprint 15 / F1 + H5) as the next-best path.

**Operator action.** Deploy `infra/k8s/agent-mesh-labels.yaml` for Istio-based default-deny; supplement with SPIFFE identity for transitive trust.

---

## vOA08 — DNS rebinding + SSRF revival against agent tool runners

**Risk statement.** An agent has a `fetch_url` tool. The attacker controls `api.example.com` DNS. After we pass the initial private-IP / allowlist gate, they flip the A record to `169.254.169.254` (AWS metadata) and we follow.

**Why the OS, not the app.** The runtime's egress policy is the only thing that sees the IP-level reality. The app sees the URL; the OS sees the socket.

**Sources.**
- [Agentic AI as a Cybersecurity Attack Surface](https://arxiv.org/pdf/2602.19555)
- [Agentic AI Security: Threats, Defenses, Open Challenges](https://arxiv.org/pdf/2510.23883)

**vOS posture.** ✅ Sprint 15 / H4 — DNS pinning in `backend/core/security/connectors/runtime_firewall.py`. First resolution cached + TTL-bounded; subsequent resolutions outside the pinned set return the new `DENY_DNS_REBIND` outcome. Operator opt-out via `VOS3_DNS_PIN_DISABLED=1` for planned CDN rotation; `reset_dns_pin_cache()` for manual reset.

**Operator action.** Configure `VOS3_DNS_PIN_TTL_SECONDS` to match your CDN's TTL profile.

---

## vOA09 — Cryptographic agility under the post-quantum transition

**Risk statement.** A "harvest-now-decrypt-later" attacker records today's TLS sessions. When a CRQC ships, those sessions decrypt — including the model uploads, agent identity rotations, and audit logs.

**Why the OS, not the app.** The TLS stack lives in the OS / runtime, not the app. ML-KEM-768 in user-facing TLS is the OS-vendor (or distro maintainer)'s job.

**Sources.**
- [IETF draft-ietf-tls-ecdhe-mlkem](https://datatracker.ietf.org/doc/draft-ietf-tls-ecdhe-mlkem/)
- [Cryptomathic: OpenSSL 3.5 PQ on RHEL 9.6](https://www.cryptomathic.com/blog/quantum-ready-cryptography-with-openssl-3.5-on-rhel-9.6)
- vOS [`docs/POST_QUANTUM_INVENTORY.md`](POST_QUANTUM_INVENTORY.md)

**vOS posture.** ✅ Sprint 14.1 — userspace X25519+ML-KEM-768 hybrid KEX live in `backend/services/hybrid_kex.py`; kernel TLS offers the hybrid group in `supported_groups`. Sprint 15 / N1 + N4 docs are operator runbooks for the distro readiness gap (RHEL 9.6 ✓, Ubuntu 24.04 partial, Debian 13 not yet) and the CISA scope-interpretation ambiguity.

**Operator action.** Run the readiness matrix in `docs/POST_QUANTUM_INVENTORY.md §8.1`; for distros that don't ship OpenSSL 3.5 yet, fall back to vOS's userspace hybrid_kex.

---

## vOA10 — Standards conformance: ISO 27090 + EU AI Act Annex IV

**Risk statement.** EU AI Act Article 73 enforcement starts 2026-08-02. ISO/IEC FDIS 27090 final-texted on 2026-03-12 and is in publication. Operators need a documented mapping from OS-level controls to standard clauses, audit-ready, before the deadline.

**Why the OS, not the app.** Annex IV-level evidence (Sections V.1 lifecycle, V.7 logs, V.8 monitoring) requires kernel-level attestation. App-only conformance is incomplete by design.

**Sources.**
- [ISO/IEC FDIS 27090](https://www.iso.org/standard/56581.html)
- [EU AI Act Annex IV](https://artificialintelligenceact.eu/annex/4/)
- [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework)

**vOS posture.** ✅ Sprint 15 / P3 + N1 + N4 + P5 + P6 — `docs/compliance/ISO_27090_DRAFT.md` is the pre-compliance technical file (9 sections, every threat class mapped to a vOS control). `docs/POST_QUANTUM_INVENTORY.md` carries the operator-facing guidance. `infra/security/cyclonedx_agent_type_proposal.md` (this Sprint) opens the upstream channel for the missing SBOM `agent` type. This document closes the public-narrative loop.

**Operator action.** Wire the `/api/compliance/audit/failures` + `/api/compliance/annex-iv/export` endpoints into your evidence-collection pipeline; subscribe to the `docs/compliance/ISO_27090_DRAFT.md` watchlist (§8) for the final-publication refresh.

---

## Summary — the Top 10 quick-scan card

| # | Risk | Severity | vOS posture | Closed by |
|---|------|----------|-------------|-----------|
| vOA01 | Sandbox escape from agent code | 🔴 Critical | ✅ Sprint 15 / C6 + C8 | gVisor MAGI + K8s Sandbox CRD |
| vOA02 | Indirect prompt injection from tool outputs | 🔴 Critical | 🟡 Sprint 15 / C2 (Wave 3) | Dual-LLM Privileged/Quarantined |
| vOA03 | Memory disclosure across AI workloads | 🟠 High | ⚪ Sprint 15 / A1 (partial; physical side-channels open) | Workload-hint API |
| vOA04 | Cryptographic workload identity, not human | 🔴 Critical | ✅ Sprint 15 / F1 | SPIFFE WIT-SVID verifier |
| vOA05 | Audit-trail capture of agent decisions | 🔴 Critical | ✅ Sprint 14.1 + Sprint 15 / G1 + G5 | OpenTelemetry GenAI + Article 73 endpoint |
| vOA06 | Model integrity from disk to RAM to RTMR | 🔴 Critical | ✅ Sprint 14.1 + Sprint 15 / I1 + I3 + I6 | OMS v1.0 + RTMR[2] extend + HF config scanner |
| vOA07 | Agent-to-agent communication needs a kernel primitive | 🟠 High | 🟡 Sprint 15 / H5 (partial; full A2A is Stage 14.B) | Istio mesh + SPIFFE identity |
| vOA08 | DNS rebinding + SSRF revival | 🟠 High | ✅ Sprint 15 / H4 | DNS pinning in runtime_firewall |
| vOA09 | Cryptographic agility under PQ transition | 🟠 High | ⚪ Sprint 14.1 (userspace live; kernel pending Stage 14.B.2) | X25519+ML-KEM-768 hybrid KEX |
| vOA10 | Standards conformance (ISO 27090 + EU AI Act) | 🔴 Critical | ✅ Sprint 15 / P3 + N1 + N4 + P5 + P6 | Pre-compliance technical file + operator runbooks |

---

## Honest scope ceiling

This list intentionally omits items that are app-layer or hardware-only:
- **Prompt-injection content classifiers** — app concern (OWASP vO-something).
- **Adversarial-example detection** — model concern, not OS.
- **GPU silicon hardening** — vendor concern, although vOS exposes Confidential Compute modes.

The 80 problems documented in `docs/AGENT_ERA_OS_PROBLEMS.md` and the per-Sprint roadmap in `docs/AGENT_ERA_SOLUTIONS_ROADMAP.md` together cover the full picture; the Top 10 is the **highest-severity OS-shaped subset**.

We invite competing AI-OS projects (Genode, Fuchsia, the Kubernetes Agent Sandbox SIG, Anthropic's deployment hardening team) to publish their own Top-10 mapping and cross-reference. A common framing makes the entire ecosystem safer.

---

## Last reviewed

| Section | Last reviewed | Reviewer | Next trigger |
|---------|---------------|----------|--------------|
| All items | 2026-05-24 | vOS engineering | next Sprint commit that closes a 🟡-marked row |

## License

CC-BY 4.0. Attribution: "vOS Agent-OS Top 10 v1.0, vOS Engineering Team, 2026".
