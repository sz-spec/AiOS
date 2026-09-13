# Feature-Parity & AI Agent Readiness Audit

**Date:** 2026-03-30
**Scope:** VOS3 vs V-Creator multi-agent pipeline, AI worker management, dependency parity

---

## 1. FEATURE MAPPING — Multi-Agent Pipeline

### Node-by-Node Comparison

| # | V-Creator Node | VOS3 Equivalent | Parity? |
|---|---------------|-----------------|---------|
| 1 | `architect` — System design from requirements | `_architect_node` — Same + auto-detects auth strategy | VOS3 superior |
| 2 | `expander` — Expand architecture → detailed blueprints (Gemini 3 Pro, 1M ctx) | **MISSING** | **GAP** |
| 3 | `dispatcher` — Fan-out via `Send()` to parallel branches | `_dispatch_parallel` — Same pattern | Parity |
| 4 | `frontend` — React/TS generation with escalation | `_frontend_node` — Same + auth template injection | VOS3 superior |
| 5 | `backend` — API/business logic with escalation | `_backend_node` — Same + auth template injection | VOS3 superior |
| 6 | `aggregator` — Merge parallel branch results | `_aggregator_node` — Same | Parity |
| 7 | `tester` — Test generation | `_tester_node` — Same + auth-specific test scenarios | VOS3 superior |
| 8 | `reviewer` — Cross-model review + Ralph Loop detection | `_reviewer_node` — Same + semantic loop detection (embeddings) + 3-stage escalation | VOS3 superior |
| 9 | `finalize` — Package project | `_finalize_node` — Same + calls export_service for deployment files | VOS3 superior |
| 10 | `production_readiness` — 8 OWASP security checks post-finalize | **MISSING** | **GAP** |

### VOS3-Only Nodes (No V-Creator Equivalent)

| # | VOS3 Node | Purpose |
|---|-----------|---------|
| 11 | `_guardrails_gate_node` | AST validation against 10 Iron Rules before tester |
| 12 | `_db_migrations_node` | Schema diff + migration file generation |
| 13 | `_visual_qa_node` | Render frontend → screenshot → multimodal LLM analysis (Gemini + Claude) |

### Pipeline Topology

**V-Creator (10 nodes):**
```
START → architect → expander → dispatcher
  ├→ frontend ──┐
  └→ backend  ──┤
            aggregator → tester → reviewer → finalize → production_readiness → END
                                    ↑              |
                                    └── retry ─────┘
```

**VOS3 (12 nodes, parallel mode):**
```
START → architect → dispatcher
  ├→ frontend ──┐
  └→ backend  ──┤
            aggregator → guardrails_gate → db_migrations → tester → visual_qa → reviewer → finalize → END
                  |                                                                  ↑           |
                  └── (CRITICAL violation fix) → frontend ───────────────────────────┘           |
                                                                                     ↑           |
                                                                                     └── retry ──┘
```

### Verdict: Feature Map

**VOS3 has 12 of 14 total unique nodes across both repos.**

| Category | Count |
|----------|-------|
| V-Creator nodes replicated in VOS3 | 8 of 10 |
| V-Creator nodes MISSING from VOS3 | **2** (`expander`, `production_readiness`) |
| VOS3-exclusive nodes | 3 (`guardrails_gate`, `db_migrations`, `visual_qa`) |

---

### Capability Gap 1: `expander` Node

**What V-Creator has:** A dedicated `expander` node using Gemini 3 Pro (1M context) that sits between `architect` and `dispatcher`. It transforms high-level architecture into execution-ready JSON blueprints — detailed file-by-file implementation plans that frontend/backend agents follow.

**Impact:** Without this, VOS3's frontend/backend agents receive architecture directly from the architect node and must infer implementation details themselves. This means:
- More LLM hallucination in generated code structure
- No file-level scope protection (V-Creator's `_filter_files_by_scope()` uses blueprint-defined paths)
- Architect output must be more verbose to compensate

**Recommendation:** Add `_expander_node` between architect and dispatcher. Use Gemini 3 Pro for its 1M context window. Store blueprint in `state["blueprints"]`.

### Capability Gap 2: `production_readiness` Node

**What V-Creator has:** A post-finalize node that runs 8 security checks against generated code:
1. `db_rls` — Row-level security enabled
2. `no_hardcoded_secrets` — No `sk-`, `api_key =`, etc.
3. `auth_on_all_routes` — Authentication middleware present
4. `jwt_secret_not_default` — No "secret123" or "change-in-production"
5. `cors_configured` — No wildcard `*` origins
6. `rate_limiting` — Rate limiter present
7. `sql_parameterized` — No string-concatenated SQL
8. `no_eval` — No `eval()`/`exec()` usage

**Impact:** VOS3's `guardrails_gate` covers AST validation pre-review, but has no post-finalize security sweep. Generated projects may ship with hardcoded secrets or missing auth.

**Recommendation:** Add `_production_readiness_node` after `finalize`. Port V-Creator's 8 checks. Chain: `finalize → production_readiness → END`.

---

### auth_injector Comparison

| Aspect | VOS3 | V-Creator |
|--------|------|-----------|
| **Exists?** | Yes — `agents/auth_injector.py` | No dedicated module |
| **Detection** | Keyword-based (zero LLM calls): Clerk, NextAuth, custom_jwt | None — auth detected only by `production_readiness` post-hoc |
| **Injection** | Pre-tested template files overwrite AI-generated auth code | None — relies on LLM to generate auth correctly |
| **Template manifest** | `templates/auth/manifest.json` per strategy | N/A |
| **Test scenarios** | `get_test_scenarios()` feeds auth-specific tests to tester node | `auth_on_all_routes` check (post-hoc only) |
| **Dependencies** | `get_auth_dependencies()` injects into package.json | N/A |

**Verdict: VOS3 is significantly superior.** Template-based auth injection eliminates hallucination risk for authentication — the most security-critical component.

### export_service Comparison

| Aspect | VOS3 | V-Creator |
|--------|------|-----------|
| **Exists?** | Yes — `services/export_service.py` (759 lines) | No dedicated module |
| **Output files** | 9: package.json, tsconfig, .env.example, .gitignore, Dockerfile, docker-compose, README, GitHub Actions workflow, SECRETS_SETUP.md | Merged dict only (frontend + backend + tests) |
| **CI/CD** | Full GitHub Actions with SHA-pinned actions + OIDC auth | None |
| **Deploy targets** | Vercel, Fly.io, Railway, Docker/GHCR | None |
| **Security** | SHA-pinned actions registry, env var classification (secrets vs variables), OIDC migration guide | None |
| **Supply chain** | SEC-EXP-01: pinned action SHAs; SEC-EXP-02: secret classification | None |

**Verdict: VOS3 is dramatically superior.** V-Creator outputs raw files; VOS3 outputs deployment-ready projects with CI/CD, containerization, and supply-chain hardening.

---

## 2. AI WORKER MANAGEMENT READINESS

### Current State

| Component | Can Assign PID? | Can Isolate Memory? | Can Read Other Agent's Output? |
|-----------|----------------|--------------------|-----------------------------|
| `VosEngine` (vos/engine.py) | No — stateless intent processor | No | No — no shared state |
| `KernelAppManager` | Fixed slots 0-7 only | Yes (kernel contexts) | Via kernel SHM only |
| `AppRegistry` (services/app_registry.py) | String app_id (OAuth) | No | Via Convex tables |
| `AppScope` (core/app_scopes.py) | N/A — API permissions | No — request-level only | Via scope grants |
| `ExpertPoolService` | UUID request_id | No | Via request context dict |
| `LangGraph ProjectState` | N/A | No — shared dict | All agents see full state |

### Process ID Assignment — NOT READY

**Finding:** No mechanism exists to assign a unique, trackable Process ID to a dynamically spawned AI agent.

- `VosEngine` is a stateless intent classifier — it classifies and routes, doesn't register processes
- `KernelAppManager` has only 8 fixed slots (0-7), not dynamic allocation
- `AppRegistry` assigns string IDs for OAuth apps, but these are installation records, not running process handles
- There is no `AgentRegistry` or `ProcessTable` in the backend

**What's needed:** An `AgentProcess` registry that can:
```
agent_id = registry.spawn(role="vision_node", parent="ai_manager", capabilities=[...])
registry.get_state(agent_id)  # → "running" | "blocked" | "completed"
registry.terminate(agent_id)
```

### Memory Scope Isolation — NOT READY

**Finding:** `core/app_scopes.py` defines **OAuth API permission scopes** (`vos3:entities:read`, etc.), NOT memory isolation scopes.

Current scopes:
```python
class AppScope(str, Enum):
    ENTITIES_READ = "vos3:entities:read"
    ENTITIES_WRITE = "vos3:entities:write"
    RECORDS_READ = "vos3:records:read"
    RECORDS_WRITE = "vos3:records:write"
    WORKFLOWS_EXECUTE = "vos3:workflows:execute"
    AI_GENERATE = "vos3:ai:generate"
    FILES_READ = "vos3:files:read"
    FILES_WRITE = "vos3:files:write"
    KERNEL_EXECUTE = "vos3:kernel:execute"
```

These control which API endpoints an app can call, not which data an agent can see. All agents share:
- Same Convex tables (no row-level filtering by agent)
- Same DevMemory ChromaDB (no namespace isolation)
- Same LangGraph `ProjectState` dict during execution

**What's needed:** Per-agent memory namespacing:
```
scope = memory.create_scope(agent_id="vision_node_42")
scope.write("analysis_result", data)  # Only visible within this scope
scope.share("analysis_result", target="ai_manager")  # Explicit sharing
```

### vision_node Storage Compatibility — PARTIALLY READY

**Finding:** The `vision_node` can store its output, but no structured channel exists for an AI Manager Agent to consume it.

**Available storage paths:**

| Storage | Write | AI Manager Read | Structured? |
|---------|-------|----------------|-------------|
| LangGraph `ProjectState["visual_qa"]` | Yes — `_visual_qa_node` writes here | Yes — any downstream node reads state | Yes, but ephemeral (destroyed after graph execution) |
| Convex `projects` table | Yes — via API call | Yes — via query | Yes, but no per-agent filtering |
| DevMemory (ChromaDB) | Yes — `memory.add(content, type="visual_qa")` | Yes — `memory.recall("visual qa results")` | Fuzzy (vector search, not exact key lookup) |
| Kernel SHM | No — no Python SHM write API | No — no Python SHM read API | N/A |

**The problem:** LangGraph state is the natural place for vision_node output, but it's destroyed after the build graph completes. For a persistent AI Manager Agent to read it later, the vision_node must explicitly write to Convex or DevMemory — but neither has per-agent namespacing.

**What's needed:**
1. A `vision_outputs` Convex table (or namespace within existing tables) with `agent_id`, `build_id`, `analysis_type`, `data` fields
2. A query pattern: `get_vision_output(build_id, analysis_type)` for the AI Manager
3. Or: extend `ProjectState` to persist to Convex after graph execution (not just in-memory)

---

### AI Worker Readiness Summary

| Requirement | Status | Gap |
|-------------|--------|-----|
| Unique Process ID per agent | **NOT READY** | No dynamic PID allocation; KernelAppManager limited to 8 slots |
| Isolated Memory Scope per agent | **NOT READY** | `app_scopes.py` is OAuth permissions, not memory isolation |
| vision_node → AI Manager data flow | **PARTIAL** | Can write to LangGraph state (ephemeral) or Convex (no namespace) |
| Agent lifecycle tracking | **NOT READY** | No process table tracking running/blocked/completed agents |
| Inter-agent messaging | **NOT READY** | Must poll Convex tables; no event/queue mechanism |

---

## 3. DEPENDENCY PARITY

### Backend Python — V-Creator Has, VOS3 Missing

| Package | Capability | Severity |
|---------|-----------|----------|
| `sqlalchemy[asyncio]>=2.0` | Async ORM for production database | **HIGH** — VOS3 relies on in-memory dicts |
| `asyncpg>=0.29.0` | Native PostgreSQL async driver | **HIGH** — needed for production persistence |
| `aiosqlite>=0.20.0` | SQLite async driver for dev | LOW — dev convenience |
| `tokencost>=0.1.0` | LLM token cost estimation per request | MEDIUM — VOS3 has metrics but no cost breakdown |
| `langgraph-checkpoint-redis` | Redis-backed LangGraph state persistence | **HIGH** — VOS3 loses state on crash |
| `langmem>=0.0.5` | Long-term memory SDK | MEDIUM — VOS3 has ChromaDB alternative |

### Backend Python — VOS3 Has, V-Creator Missing

| Package | Capability |
|---------|-----------|
| `strawberry-graphql[fastapi]` | GraphQL API layer |
| `opentelemetry-api/sdk` | Distributed tracing |
| `chromadb>=0.4.22` | Vector database for RAG |
| `pgvector>=0.2.4` | PostgreSQL vector extension |
| `sentence-transformers>=2.2.2` | Local embedding models |
| `networkx>=3.0` | Graph algorithms (GraphRAG) |
| `crewai-tools>=0.14.0` | CrewAI tool ecosystem |
| `langchain-tavily` + `tavily-python` | Web search integration |
| `langchain-ollama>=0.1.0` | Local model support |
| `ragas>=0.1.0` | RAG evaluation framework |
| `memory-profiler>=0.61.0` | Memory profiling |

### Frontend JS — V-Creator Has, VOS3 Missing

| Package | Capability | Severity |
|---------|-----------|----------|
| `@codesandbox/sandpack-react` | Live code preview/execution | **HIGH** — core IDE feature |
| `@monaco-editor/react` | Code editor | **HIGH** — core IDE feature |
| `xterm` + addons | Terminal emulator | **HIGH** — core IDE feature |
| 10+ `@radix-ui/*` packages | Accessible headless UI components | MEDIUM — UI quality |
| `@stripe/stripe-js` | Payment form UI | MEDIUM — billing feature |
| `zustand>=4.5.0` | State management | MEDIUM — V-Creator uses for editor/chat/UI stores |
| `immer>=10.0.3` | Immutable state updates | LOW — Zustand companion |
| `next-themes` | Dark mode | LOW — UX polish |
| `react-syntax-highlighter` | Code highlighting | MEDIUM — display quality |
| `react-resizable-panels` | Split panes | MEDIUM — IDE layout |
| `@playwright/test` | E2E testing framework | **HIGH** — zero E2E coverage in VOS3 |
| `@axe-core/playwright` | Accessibility testing | MEDIUM — compliance |
| `convex-test` | Convex mutation/query testing | MEDIUM — DB test coverage |

### Frontend JS — VOS3 Has, V-Creator Missing

| Package | Capability |
|---------|-----------|
| `yjs>=13.6.0` | Real-time collaborative editing (CRDT) |
| `y-monaco>=0.1.6` | Yjs ↔ Monaco binding |

### Version Divergence

| Package | VOS3 | V-Creator | Note |
|---------|------|-----------|------|
| `langgraph` | `>=0.2.60` | `>=1.0.8` | V-Creator requires 1.0+ features (`Command`, `Send`, `interrupt`) |
| `convex` (frontend) | `^1.13.0` | `^1.32.0` | V-Creator 19 versions ahead |
| `next` | `14.2.25` | `14.1.0` | VOS3 slightly newer |

---

## CAPABILITY GAPS — Where V-Creator Has an Advantage

### CRITICAL (Blocks production readiness)

| # | Gap | V-Creator | VOS3 Status | Impact |
|---|-----|-----------|-------------|--------|
| G1 | **Expander Node** | Gemini 3 Pro blueprint expansion between architect and code gen | Missing | Agents get raw architecture, not file-level blueprints; more hallucination |
| G2 | **Production Readiness Checks** | 8 OWASP security checks post-finalize | Missing | Generated projects may ship with hardcoded secrets, missing auth, eval() |
| G3 | **LangGraph State Persistence** | `langgraph-checkpoint-redis` | In-memory only | Crash = total state loss mid-build; no resume capability |
| G4 | **Production Database** | SQLAlchemy + AsyncPG | In-memory dicts | Cannot scale beyond single-process demos |

### HIGH (Degrades user experience)

| # | Gap | V-Creator | VOS3 Status | Impact |
|---|-----|-----------|-------------|--------|
| G5 | **Code Editor (Monaco)** | `@monaco-editor/react` + `monaco-editor` | Missing | No in-browser code editing |
| G6 | **Live Preview (Sandpack)** | `@codesandbox/sandpack-react` | Missing | No live code execution/preview |
| G7 | **Terminal (xterm)** | `xterm` + fit/web-links addons | Missing | No in-browser terminal |
| G8 | **E2E Testing** | Full Playwright suite + a11y | Zero | No automated UI test coverage |
| G9 | **LangGraph Version** | Pinned `>=1.0.8` | `>=0.2.60` | May miss `Command`, `interrupt`, `Send` improvements |

### MEDIUM (Polish / completeness)

| # | Gap | V-Creator | VOS3 | Impact |
|---|-----|-----------|------|--------|
| G10 | Token cost tracking | `tokencost` | Aggregate metrics only | No per-request cost visibility |
| G11 | UI component library | 10+ Radix UI packages | Basic React | Accessibility and design quality |
| G12 | State management | Zustand + Immer | No declared library | Inconsistent frontend state |
| G13 | Dark mode | `next-themes` | Not implemented | UX polish |

---

### Where VOS3 Has an Advantage Over V-Creator

| # | Capability | VOS3 | V-Creator |
|---|-----------|------|-----------|
| A1 | **Auth Injection** | Zero-hallucination template-based (Clerk, NextAuth, JWT) | None — LLM generates auth code |
| A2 | **Export Service** | 9-file deployment package with CI/CD, OIDC, SHA-pinned Actions | Raw file dict only |
| A3 | **Guardrails Gate** | AST validation against 10 Iron Rules pre-review | None |
| A4 | **DB Migrations** | Auto schema diff + migration generation | None |
| A5 | **Visual QA** | Render → screenshot → multimodal LLM analysis | None |
| A6 | **Semantic Loop Detection** | Cosine similarity on issue embeddings | MD5 hash of issue strings |
| A7 | **3-Stage Escalation** | Switch provider → thinking model → expert SOS | Simple retry with iteration limit |
| A8 | **RAG Pipeline** | ChromaDB + PgVector + RAGAS evaluation | Lightweight RAG without vector store |
| A9 | **Distributed Tracing** | OpenTelemetry SDK | Langfuse/LangSmith only |
| A10 | **Collaborative Editing** | Yjs CRDT + Monaco binding | Single-user only |
| A11 | **Kernel Bridge** | QEMU serial → run native binaries | None |
| A12 | **GraphQL API** | Strawberry-GraphQL | REST only |
| A13 | **Local Models** | Ollama as primary support | Ollama as fallback only |
| A14 | **Web Search** | Tavily + CrewAI tools | Minimal |

---

## RECOMMENDATIONS — Priority Order

### Phase 3.5 Blockers (Address Before Vision-to-Vibe)

1. **Add `_expander_node`** — Port from V-Creator. Use Gemini 3 Pro. Insert between architect and dispatcher. Store blueprint in `state["blueprints"]`. This directly affects how vision_node output gets translated into implementation plans.

2. **Add `_production_readiness_node`** — Port V-Creator's 8 OWASP checks. Chain after finalize. Prevents shipping insecure generated code.

3. **Create `AgentRegistry`** — New service for dynamic PID assignment, lifecycle tracking, and capability declaration. Required for AI Manager Agent to spawn/track/terminate worker agents.

4. **Create per-agent memory namespacing** — Extend Convex schema or DevMemory with `agent_id` field. Add `create_scope()` / `share()` API. Required for vision_node → AI Manager data isolation.

### Post-3.5 (Production Readiness)

5. **Add `langgraph-checkpoint-redis`** — State persistence for crash recovery
6. **Add SQLAlchemy + AsyncPG** — Production database layer
7. **Pin LangGraph `>=1.0.8`** — Ensure `Command`/`Send`/`interrupt` compatibility
8. **Add Playwright E2E** — Frontend test coverage
9. **Add Monaco + Sandpack + xterm** — IDE experience (may be Phase 4+)
