# Sprint 15 Swarm Setup — 12-Agent Orchestration Plan

**Date:** 2026-05-23
**Scope:** Execute the 21 Sprint-15 items from `docs/AGENT_ERA_SOLUTIONS_ROADMAP.md` in parallel
**Spec from CEO:** 12 distinct agent roles, zero file-path overlap, git-atomic-commits, verify_release.sh every 3 pushes
**Author:** Claude Opus 4.7 (1M context, multi-tool parallel execution)

---

## 0. Honest scope ceiling — read this before approving

Two things the May-2026 web research surfaced that contradict parts of the spec:

1. **Anthropic's own documented sweet spot is 3–5 parallel Claude sessions, not 12.** See [Claude Code best practices](https://www.anthropic.com/engineering/claude-code-best-practices) and [Claude sub-agents docs](https://code.claude.com/docs/en/sub-agents). Above 5, the orchestrator's coordination overhead (dispatching, conflict-resolving, verifying) starts to exceed the throughput gained from extra workers. Anthropic's own multi-agent research system uses 3–5 workers per orchestrator. The [parallel C-compiler engineering post](https://www.anthropic.com/engineering/building-c-compiler) used a small team of Claudes, not 12.
2. **"ZERO file-path overlap" is mathematically impossible for this sprint.** The 21 items collectively touch at least 8 shared files: `backend/router_registry.py`, `backend/tests/conftest.py`, `backend/pytest.ini`, `CLAUDE.md`, `docs/POST_QUANTUM_INVENTORY.md`, `requirements.txt`, `kernel/src/drivers/virtio_bridge.c`, `infra/security/build_sbom.py`. Per-agent worktree isolation prevents in-flight conflict but produces merge conflicts at orchestrator-merge time.

**Recommendation:** I provide BOTH paths below. **Path B (recommended)** is the 5-wave rolling design that compresses the 12 logical roles into 4–5 simultaneously-live workers. **Path A** is the strict 12-parallel design you asked for, with explicit warnings on each conflict point.

You pick which path to execute. The 12 logical roles are preserved either way — only the simultaneity differs.

---

## 1. Best-practice baseline (May 2026 sources)

| Practice | Source | Why it matters here |
|----------|--------|--------------------|
| 3–5 parallel sessions = sweet spot | [Claude Code Power User Tips](https://support.claude.com/en/articles/14554000-claude-code-power-user-tips) | Above 5, orchestrator becomes the bottleneck |
| `isolation: worktree` for parallel edits | [Run parallel sessions with worktrees](https://code.claude.com/docs/en/worktrees) | Mandatory for any 2+ agents touching code |
| Opus 4.7 spawns fewer subagents by default | [Best practices for Opus 4.7](https://claude.com/blog/best-practices-for-using-claude-opus-4-7-with-claude-code) | Steerable via explicit prompting — but the default reflects empirical lessons |
| Multi-agent research orchestrator-worker pattern | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) | Orchestrator coordinates, workers execute in their own context |
| LAMaS — critical-path optimization | [arXiv 2601.10560](https://arxiv.org/html/2601.10560) | 38–46% critical-path reduction via latency-aware scheduling |
| Worktree auto-cleanup if no changes | [Claude worktree docs](https://code.claude.com/docs/en/worktrees) | Idle agents leave zero trace |
| Each subagent gets independent 1M context | [Multiagent sessions docs](https://platform.claude.com/docs/en/managed-agents/multi-agent) | Context is per-agent, not pooled |

---

## 2. The 12 logical agent roles — scope, files, dependencies

For each agent: directory it owns, files it MUST modify, files it CANNOT modify, depends-on list, exit criteria.

### Agent 1 — Orchestrator (me, this conversation)

- **Owns:** dispatch, merge, verify, communicate to user
- **Mutates:** nothing directly during agent execution; merges agent worktrees on each completion
- **Dispatches:** Agents 2–12 via the Agent tool with `isolation: worktree`
- **Verify gate:** after every 3 worker pushes, runs `bash scripts/verify_release.sh` on the merged tip; rolls back on failure
- **Exit criteria:** all 21 items committed, `pytest tests/contracts/ tests/governance/ tests/apex_sim/ tests/crypto/` green, PR #1 updated

### Agent 2 — Identity Alpha (SPIFFE / WIMSE)

- **Items:** F1 (SPIFFE WIT-SVID + WIMSE in clerk_auth)
- **Scope dir:** `backend/middleware/`, `backend/core/security/`
- **Must modify:** `backend/middleware/clerk_auth.py` (add WIT-SVID verifier), `backend/core/security/spiffe_workload_identity.py` (NEW)
- **Must NOT modify:** `backend/router_registry.py` (Agent 3 owns this), `backend/middleware/auth.py` (shared — orchestrator merges)
- **Depends on:** none
- **Exit criteria:** `pytest tests/integration/test_spiffe_wit_svid.py` (new) passes; AuthenticatedUser dataclass gains optional `spiffe_id` field
- **Source:** [IETF OAuth SPIFFE Client Auth draft-01](https://datatracker.ietf.org/doc/draft-ietf-oauth-spiffe-client-auth/), [WIMSE Architecture draft-07](https://datatracker.ietf.org/doc/draft-ietf-wimse-arch/)

### Agent 3 — Identity Beta (MCP 2026-07-28 RC)

- **Items:** F2 (MCP RC alignment), G2 (MCP-aware audit decision schema)
- **Scope dir:** `backend/mcp-server/`, `backend/services/`
- **Must modify:** `backend/mcp-server/src/auth.ts` (OAuth/OIDC stateless core), `backend/services/mcp_oauth_bridge.py` (NEW)
- **Must NOT modify:** Agent 2's spiffe files; `clerk_auth.py`
- **Depends on:** Agent 2's `spiffe_workload_identity.py` interface (read-only)
- **Exit criteria:** MCP RC test suite (vendored from upstream) passes
- **Source:** [MCP 2026-07-28 release candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)

### Agent 4 — Model Security (OMS + AI/ML-BOM)

- **Items:** I1 (CycloneDX AI/ML-BOM gen), I3 (OMS signature verify at SLOT_START), I6 (HF config scanner)
- **Scope dir:** `infra/security/`, `backend/services/`
- **Must modify:** `infra/security/build_sbom.py` (add ML-BOM section), `backend/services/oms_verifier.py` (NEW), `backend/services/hf_config_scanner.py` (NEW)
- **Must NOT modify:** `backend/services/vbus_driver.py` (kernel bridge — Agent 9 owns)
- **Depends on:** none
- **Exit criteria:** signed test model verifies via OMS; HF config scanner blocks the known malicious YAML test corpus
- **Source:** [Sigstore model-signing v1.0](https://blog.sigstore.dev/model-transparency-v1.0/), [Rusty Link config attacks](https://arxiv.org/pdf/2505.01067)

### Agent 5 — Observability (OpenTelemetry GenAI)

- **Items:** G1 (gen_ai.* spans → compliance_store), G5 (PII-redacted MAIF envelope wrapper)
- **Scope dir:** `backend/services/`, `backend/middleware/`
- **Must modify:** `backend/services/telemetry_genai.py` (NEW), `backend/middleware/timing.py` (add `gen_ai.agent.*` span emission), `backend/services/compliance_store.py` (add OTel-format ingestion endpoint)
- **Must NOT modify:** `backend/services/integrity_worker.py`
- **Depends on:** none
- **Exit criteria:** `otel-genai-test-harness` emits 9 mandatory spans; redacted PII roundtrip in MAIF envelope verified
- **Source:** [OpenTelemetry GenAI agent spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/), [MAIF](https://arxiv.org/pdf/2511.15097)

### Agent 6 — Hardening (Dual-LLM defense + spotlighting)

- **Items:** C2 (dual-LLM Privileged/Quarantined pattern for tool outputs)
- **Scope dir:** `backend/ai/agents/`, `backend/services/`
- **Must modify:** `backend/ai/agents/dual_llm_router.py` (NEW), `backend/services/quarantined_llm.py` (NEW), `backend/ai/agents/multi_agent.py` (hook the dual-LLM pattern into tool-output handling)
- **Must NOT modify:** anything outside `backend/ai/`
- **Depends on:** none
- **Exit criteria:** test_dual_llm_blocks_indirect_injection passes against the EchoLeak-class corpus
- **Source:** [Evaluation of Prompt Injection Defenses](https://arxiv.org/html/2604.23887), [Defeating Prompt Injections by Design](https://arxiv.org/pdf/2503.18813)

### Agent 7 — Sandbox (gVisor MAGI + K8s Sandbox CRD)

- **Items:** C6 (MAGI integration), C8 (K8s Sandbox CRD as default)
- **Scope dir:** `infra/k8s/` (NEW), `desktop/`, `scripts/`
- **Must modify:** `infra/k8s/agent-sandbox-crd.yaml` (NEW), `scripts/deploy_with_magi.sh` (NEW), `desktop/src-tauri/src/qemu.rs` (add MAGI-mode toggle)
- **Must NOT modify:** any `backend/` Python file
- **Depends on:** none
- **Exit criteria:** local minikube + agent-sandbox CRD launches a vOS agent in MAGI-isolated gVisor
- **Source:** [gVisor MAGI April 2026](https://gvisor.dev/blog/2026/04/15/magi-multi-agent-gvisor-isolation/), [K8s Agent Sandbox March 2026](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox/)

### Agent 8 — Net-Sec (DNS pinning + service mesh labels)

- **Items:** H4 (DNS pinning in runtime_firewall), H5 (CNCF agentic-standards mesh labels)
- **Scope dir:** `backend/core/security/connectors/`, `infra/k8s/`
- **Must modify:** `backend/core/security/connectors/runtime_firewall.py` (extend `_resolves_to_private` with first-resolution pinning), `infra/k8s/agent-mesh-labels.yaml` (NEW)
- **Must NOT modify:** Agent 7's `agent-sandbox-crd.yaml`
- **Depends on:** existing runtime_firewall from Sprint 14.1
- **Exit criteria:** test_dns_rebinding_blocked passes; mesh-label CRD applies
- **Source:** [CNCF Cloud native agentic standards March 2026](https://www.cncf.io/blog/2026/03/23/cloud-native-agentic-standards/)

### Agent 9 — Kernel-Link (memory hints + ai_slots extensions)

- **Items:** A1 (MAP_POPULATE + huge-page hint), K4 (MAIF as canonical container)
- **Scope dir:** `kernel/src/mm/`, `backend/services/`
- **Must modify:** `kernel/src/mm/ai_slots.c` (add `vos3_ai_slot_madvise()` for KV cache), `backend/services/maif_envelope.py` (NEW)
- **Must NOT modify:** any other kernel C file in this sprint (Agent 1 verifies kernel SHA stable)
- **Depends on:** none
- **Exit criteria:** reproducible kernel SHA (deterministic build), MAIF envelope roundtrip test passes
- **Source:** [Composable OS Kernel Architectures](https://arxiv.org/pdf/2508.00604), [MAIF](https://arxiv.org/pdf/2511.15097)

### Agent 10 — Compliance (ISO 27090 pre-compliance + Annex IV updates)

- **Items:** P3 (ISO 27090 pre-comply), N1+N4 (PQ documentation closure)
- **Scope dir:** `docs/`
- **Must modify:** `docs/ISO_27090_PRE_COMPLIANCE.md` (NEW), `docs/POST_QUANTUM_INVENTORY.md` (add CISA scope-clarity table), `COMPLIANCE_REPORT.md` (cross-ref §4)
- **Must NOT modify:** any code file
- **Depends on:** none
- **Exit criteria:** doc passes link-check; compliance store cross-ref schema validates
- **Source:** [ISO/IEC FDIS 27090](https://www.iso.org/standard/56581.html)

### Agent 11 — Documentation (vOS-Agent-OS Top-10 + public security roadmap)

- **Items:** P5 (CycloneDX agent type proposal), P6 (vOS-Agent-OS Top-10 whitepaper)
- **Scope dir:** `docs/`, `infra/security/`
- **Must modify:** `docs/VOS_AGENT_OS_TOP10.md` (NEW), `infra/security/cyclonedx_agent_type_proposal.md` (NEW), `README.md` (link the public security URL)
- **Must NOT modify:** `docs/POST_QUANTUM_INVENTORY.md` (Agent 10 owns)
- **Depends on:** Agent 10's ISO 27090 doc for cross-ref
- **Exit criteria:** whitepaper PDF builds via pandoc; CycloneDX proposal opens upstream issue
- **Source:** [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/), [CycloneDX spec](https://cyclonedx.org/specification/overview/)

### Agent 12 — QA / Red-Teamer

- **Items:** F3 (actor_type provenance in audit), regression sweep against EchoLeak/ShadowPrompt corpus
- **Scope dir:** `backend/tests/`, `backend/middleware/`
- **Must modify:** `backend/middleware/auth.py` (add `actor_type` field — coordinate with orchestrator merge), `backend/tests/red_team/` (NEW dir with EchoLeak + ShadowPrompt fixtures)
- **Must NOT modify:** anything in `kernel/` or `infra/`
- **Depends on:** Agent 6's dual_llm_router (test target), Agent 5's telemetry_genai (test instrumentation)
- **Exit criteria:** 30+ red-team prompts blocked by Agent 6's defense; `actor_type` populated on every audit row

---

## 3. File-conflict matrix (must read before dispatch)

The 12 agents touch the following SHARED files. Orchestrator (me) manages the merge:

| File | Agents that need to touch | Merge strategy |
|------|----------------------------|----------------|
| `backend/router_registry.py` | A3 (MCP routes), A4 (OMS+HF routes), A8 (firewall route) | Sequential — orchestrator merges in A3 → A4 → A8 order |
| `backend/tests/conftest.py` | A5 (OTel fixture), A6 (dual-LLM fixture), A12 (red-team fixture) | Append-only sections; orchestrator does textual merge |
| `backend/middleware/auth.py` | A2 (spiffe field), A12 (actor_type field) | Orchestrator merges both ADD operations on AuthenticatedUser dataclass |
| `requirements.txt` | A2 (`pyspiffe`), A4 (`model-signing`, `oms`), A5 (`opentelemetry-instrumentation-genai`), A6 (`pydantic-ai` if needed), A7 (`gvisor-cli`), A10 (none) | Orchestrator merges all `+` lines after final dispatch wave |
| `CLAUDE.md` | ALL agents update Sprint 15 progress section | Orchestrator owns CLAUDE.md edits; agents pass a one-line status string back |
| `docs/POST_QUANTUM_INVENTORY.md` | A10 (CISA + PQ) | Single-agent, no conflict |
| `kernel/src/drivers/virtio_bridge.c` | A9 (madvise command) | Single-agent, no conflict |
| `infra/security/build_sbom.py` | A4 (ML-BOM section) | Single-agent, no conflict |

---

## 4. Path A — Strict 12-agent parallel (as requested)

### Dispatch protocol

1. Orchestrator (me) creates a single message with **11 simultaneous Agent tool calls** (Agents 2–12), each with `isolation: worktree`.
2. Each agent works in its temp worktree on its assigned items.
3. Agents push to the orchestrator via the return value (summary + diff stat + branch ref).
4. Orchestrator merges in dependency order: A2, A9, A10 first (no depends-on) → A3, A11 (depend on A2/A10) → A4, A5, A7, A8 → A6, A12.
5. After every 3 successful merges: `bash scripts/verify_release.sh` + `pytest tests/contracts/ tests/governance/ tests/apex_sim/ tests/crypto/ tests/silicon/ -q`.
6. Rollback any merge whose verify fails; re-dispatch that agent with conflict feedback.

### Known risks of Path A

- **Context blast** — 11 simultaneous Opus 4.7 agents at 1M context each = up to 11M tokens of working memory across the swarm. Token cost spikes proportionally.
- **Orchestrator becomes the bottleneck** — Anthropic's docs warn this happens above 5 workers. Merge + verify cycles every 3 pushes means the orchestrator is sequentializing what was supposed to be parallel.
- **Worktree merge conflicts** — 8 shared files (listed above) WILL conflict; orchestrator must hand-merge them. Realistically adds 30–45 min of orchestrator work per merge wave.
- **Agent drift** — each agent's 1M context produces its own mental model; when merged, the result may not be coherent (e.g., A2 and A12 both touch the AuthenticatedUser dataclass with different field-naming conventions).
- **No documented precedent** — Anthropic's published examples top out at 5 (sometimes 7) parallel sessions. 12 is unexplored territory; behavior under load is empirical only.

### Estimated wall-clock for Path A

| Phase | Time |
|-------|------|
| Wave 1 dispatch + agents execute | 60–90 min (longest agent = A6 dual-LLM, with red-team fixtures) |
| Wave 1 orchestrator merge of 11 worktrees | 45 min (8 shared-file conflicts) |
| Wave 1 verify (release.sh + pytest matrix) | 10 min |
| Wave 2 re-dispatch for any 3 verify failures | 30–60 min |
| **Total Sprint 15** | **~3–4 hours wall-clock**, all 21 items |

### Estimated cost for Path A

- 11 worker agents × ~200k tokens each in working session = 2.2M worker tokens
- Orchestrator ~500k tokens for merge + verify cycles
- Total: ~2.7M tokens × Opus 4.7 pricing ≈ **$135–200 for the swarm execution**

---

## 5. Path B — Recommended 5-wave rolling (4–5 concurrent)

Same 12 logical roles, but only 4–5 are live at any moment. Anthropic-documented sweet spot.

### Dispatch waves

**Wave 1 (independent, no shared-file conflict): 4 agents in parallel**
- Agent 9 — Kernel-Link (kernel/mm/ + maif_envelope.py)
- Agent 10 — Compliance (docs/ only)
- Agent 4 — Model Security (infra/security/ + new service files)
- Agent 7 — Sandbox (infra/k8s/ NEW + desktop/qemu.rs)

→ Orchestrator merges. Verify. ~60 min wall.

**Wave 2 (depends on Wave 1): 4 agents in parallel**
- Agent 2 — Identity Alpha (clerk_auth.py + NEW spiffe file)
- Agent 5 — Observability (NEW telemetry_genai + timing.py + compliance_store.py extension)
- Agent 8 — Net-Sec (runtime_firewall.py extension + NEW mesh-labels.yaml)
- Agent 11 — Documentation (depends on Agent 10's docs; cross-ref)

→ Orchestrator merges + verify. ~60 min wall.

**Wave 3 (depends on Waves 1+2): 4 agents in parallel**
- Agent 3 — Identity Beta (MCP — depends on Agent 2's SPIFFE interface)
- Agent 6 — Hardening (dual-LLM — independent file area)
- Agent 12 — QA (depends on Agent 6's dual_llm_router for tests, Agent 5's telemetry for instrumentation, Agent 2's auth changes)
- *(slot 4 reserved for re-dispatch of any Wave-1/2 verify failure)*

→ Orchestrator final merge + verify_release.sh + full pytest contracts/governance/apex_sim/crypto/silicon. ~75 min wall.

### Wall-clock for Path B

| Phase | Time |
|-------|------|
| Wave 1 (4 parallel × 60 min) | 60 min |
| Wave 1 merge + verify | 20 min |
| Wave 2 (4 parallel × 60 min) | 60 min |
| Wave 2 merge + verify | 20 min |
| Wave 3 (3–4 parallel × 75 min) | 75 min |
| Wave 3 final merge + full-suite verify | 30 min |
| **Total Sprint 15** | **~4.5 hours wall-clock**, all 21 items |

### Cost for Path B

- ~12 worker-agent invocations × 150k tokens each ≈ 1.8M
- Orchestrator ~400k for 3-wave merging + verifying
- Total: ~2.2M tokens ≈ **$110–160**

### Why Path B is recommended

- 25–35% cheaper than Path A
- Wall-clock is only 15–30 min longer (due to wave staging vs. all-at-once)
- Merge conflicts are smaller per wave (4 worktrees vs 11) → orchestrator can hand-merge more carefully
- Each wave's verify gate catches regressions early; under Path A you find them all at the end
- Anthropic-documented pattern with empirical track record
- Headroom for the inevitable re-dispatch of 1–2 agents

---

## 6. Worktree isolation contract (both paths)

For each dispatched agent, the orchestrator's Agent tool call carries:

```
Agent(
  description: "<role-name>",
  subagent_type: "general-purpose",
  isolation: "worktree",     # ← MANDATORY for any code-mutating agent
  prompt: <full instructions including>:
    - Items to implement (e.g., "I1, I3, I6 from AGENT_ERA_SOLUTIONS_ROADMAP.md")
    - Allowed files (whitelist)
    - Forbidden files (blacklist with exact paths)
    - Exit criteria (specific pytest commands that must pass)
    - Source URLs to cite in every implementation file's docstring
    - Commit-message format: "feat(sprint-15.<wave>.<role>): <item-id> — <one-line>"
    - Push to: refs/heads/sprint-15/agent-<N>-<role>
)
```

After the agent returns, orchestrator:
1. Fetches the agent's branch.
2. Verifies the agent stayed within its scope (`git diff --stat` against allowed-files list).
3. Cherry-picks or merges into `sprint-15` integration branch.
4. On every 3rd merge: runs `bash scripts/verify_release.sh` and the test matrix.
5. On failure: rolls back the offending merge and re-dispatches with the failure log.

---

## 7. Commit + push protocol

Each agent's commits follow:
```
feat(sprint-15.<wave>.<role>): <item-id> — <one-line summary>

<3-5 sentence body covering: what changed, why, source URL>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Example:
```
feat(sprint-15.1.identity-alpha): F1 — SPIFFE WIT-SVID verifier in clerk_auth

Implements IETF draft-ietf-oauth-spiffe-client-auth-01 WIT-SVID verification
path in middleware/clerk_auth.py. Adds spiffe_id field to AuthenticatedUser
dataclass for downstream consumers. New file: core/security/spiffe_workload_identity.py
wrapping pyspiffe for SVID parse and trust-domain validation.

Closes Sprint-15 item F1.

Source: https://datatracker.ietf.org/doc/draft-ietf-oauth-spiffe-client-auth/

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Each agent pushes one branch with N commits (one per item). Orchestrator merges with `--no-ff` to preserve agent attribution in the graph.

---

## 8. Verify gate — `verify_release.sh` after every 3 worker pushes

```bash
# Orchestrator runs after every 3rd successful merge:
cd /Users/sz/Desktop/95%ֿ/vos7220206/vos.v1
git checkout sprint-15
bash scripts/verify_release.sh                   # signed artifact check
cd backend
.venv_p312/bin/pytest tests/contracts/ tests/governance/ tests/apex_sim/ tests/crypto/ -q
# tests/silicon/ stays SKIP without --silicon flag (per Sprint 14.2)
```

On failure: orchestrator runs `git reset --hard HEAD~1` (or as many as the failing wave), notifies CEO with the failing test + offending agent, and re-dispatches that agent with a remediation prompt.

---

## 9. Decision points for the CEO before swarm launches

I need you to pick before I dispatch:

1. **Path A (12 parallel) vs Path B (5-wave rolling) vs custom?** Path B recommended.
2. **Authorize the cost** ($110–200)? — both paths sit in that range.
3. **Authorize git ops?** Orchestrator will create branch `sprint-15`, merge 12 agent branches, push to origin, update PR #1 (or open PR #2).
4. **Push to origin or stay local?** Path B can stay local for review; Path A's wall-clock benefit comes from auto-pushing.
5. **Hand-tune any role?** E.g., should Agent 12 (QA) also write tests for Agents 4, 7, 8 (not just 5, 6, 2)?

---

## 10. What I will NOT do without explicit approval

- Dispatch agents without your "go" on Path A or B
- Push to origin / open PRs (per CLAUDE.md "git ops require explicit user authorization")
- Merge anything that fails `verify_release.sh`
- Modify files outside the per-agent whitelist
- Use any model other than Opus 4.7 (per environment)
- Spawn additional agents beyond the 12 specified roles

---

## 11. Summary table — quick scan

| # | Agent | Items | Dir | New files | Modifies shared | Wave (Path B) |
|---|-------|-------|-----|-----------|------------------|----------------|
| 1 | Orchestrator | dispatch+merge | (cross-repo) | — | — | continuous |
| 2 | Identity Alpha | F1 | core/security/, middleware/ | spiffe_workload_identity.py | auth.py, router_registry.py | Wave 2 |
| 3 | Identity Beta | F2, G2 | mcp-server/, services/ | mcp_oauth_bridge.py | router_registry.py, conftest.py | Wave 3 |
| 4 | Model Security | I1, I3, I6 | infra/security/, services/ | oms_verifier.py, hf_config_scanner.py | build_sbom.py, requirements.txt | Wave 1 |
| 5 | Observability | G1, G5 | services/, middleware/ | telemetry_genai.py | timing.py, compliance_store.py, conftest.py, requirements.txt | Wave 2 |
| 6 | Hardening | C2 | ai/agents/, services/ | dual_llm_router.py, quarantined_llm.py | multi_agent.py, conftest.py, requirements.txt | Wave 3 |
| 7 | Sandbox | C6, C8 | infra/k8s/, desktop/, scripts/ | agent-sandbox-crd.yaml, deploy_with_magi.sh | qemu.rs, requirements.txt | Wave 1 |
| 8 | Net-Sec | H4, H5 | core/security/connectors/, infra/k8s/ | agent-mesh-labels.yaml | runtime_firewall.py | Wave 2 |
| 9 | Kernel-Link | A1, K4 | kernel/src/mm/, services/ | maif_envelope.py | ai_slots.c, virtio_bridge.c | Wave 1 |
| 10 | Compliance | P3, N1, N4 | docs/ | ISO_27090_PRE_COMPLIANCE.md | POST_QUANTUM_INVENTORY.md, COMPLIANCE_REPORT.md | Wave 1 |
| 11 | Documentation | P5, P6 | docs/, infra/security/ | VOS_AGENT_OS_TOP10.md, cyclonedx_agent_type_proposal.md | README.md | Wave 2 |
| 12 | QA / Red-Teamer | F3, regressions | tests/, middleware/ | tests/red_team/ (NEW dir + fixtures) | auth.py, conftest.py | Wave 3 |

---

## 12. Sources cited in this setup plan

- [Claude Code best practices](https://www.anthropic.com/engineering/claude-code-best-practices)
- [Claude Code Power User Tips (3–5 parallel sweet spot)](https://support.claude.com/en/articles/14554000-claude-code-power-user-tips)
- [Claude Code sub-agents docs (isolation: worktree)](https://code.claude.com/docs/en/sub-agents)
- [Run parallel sessions with worktrees](https://code.claude.com/docs/en/worktrees)
- [Best practices for Claude Opus 4.7](https://claude.com/blog/best-practices-for-using-claude-opus-4-7-with-claude-code)
- [How we built our multi-agent research system (Anthropic)](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Building a C compiler with a team of parallel Claudes](https://www.anthropic.com/engineering/building-c-compiler)
- [Multiagent sessions docs (Claude API)](https://platform.claude.com/docs/en/managed-agents/multi-agent)
- [LAMaS — Latency-Aware Orchestration arXiv 2601.10560](https://arxiv.org/html/2601.10560)
- [Swarm Skills: Multi-Agent System Specification arXiv 2605.10052](https://arxiv.org/html/2605.10052v1)

Plus the 21 source URLs from `docs/AGENT_ERA_SOLUTIONS_ROADMAP.md` Sprint-15 section — one per item, embedded in each agent's prompt so the worker cites it in the implementation file's docstring.

---

**End of setup plan.** Awaiting your decision on Path A vs Path B (or custom) before dispatch.
