# VBuilder Technical Implementation Specification (TIS)

> **Document Status**: Phase 1.0 COMPLETE | Phase 2.0 COMPLETE | Phase 2.5 COMPLETE | Phase 3.0 COMPLETE | Phase 3.5 DETAILED | Phases 4.0–7.0 SKELETON ONLY
> **Last Updated**: 2026-03-30
> **Approval Workflow**: Each phase requires explicit "PHASE X.0 APPROVED" before implementation begins.

---

## Table of Contents

- [Phase 1.0: Foundation](#phase-10-foundation) — **COMPLETE** (46/46 audit PASS)
- [Phase 2.0: Competitive Advantages](#phase-20-competitive-advantages) — **DETAILED BELOW**
- [Phase 2.5: Reliability Gap (IE-4, IE-5)](#phase-25-reliability-gap) — **COMPLETE** (171/171 tests PASS)
- [Phase 3.0: Production Features](#phase-30-production-features) — **COMPLETE** (2633/2633 tests PASS)
- [Phase 3.5: Creative Power (IE-6)](#phase-35-creative-power) — **DETAILED BELOW**
- [Phase 4.0: VOS3 Native Integration](#phase-40-vos3-native-integration) — skeleton
- [Phase 4.5: Enterprise Ready (IE-7)](#phase-45-enterprise-ready) — skeleton
- [Phase 5.0: The Living App (IE-8)](#phase-50-the-living-app) — skeleton
- [Phase 5.5: Content & Scale (IE-9)](#phase-55-content--scale) — skeleton
- [Phase 6.0: Full Transparency (IE-10)](#phase-60-full-transparency) — skeleton
- [Phase 6.5: Connectivity (IE-11, IE-12)](#phase-65-connectivity) — skeleton
- [Phase 7.0: Go-to-Market (IE-13, IE-14, IE-15)](#phase-70-go-to-market) — skeleton

---

## Existing Infrastructure Inventory

Before specifying Phase 1, here is what **already exists** and is functional:

| Component | File(s) | Status |
|-----------|---------|--------|
| Convex schema (frontend — VOS3 main) | `frontend/convex/schema.ts` (474 lines, 34 tables) | **PRODUCTION** — includes users, projects, projectFiles, chatMessages, billing, orgs, apps, etc. |
| Convex schema (backend — VBuilder agent) | `backend/convex/schema.ts` (314 lines, 24 tables) | **PRODUCTION** — includes projects, projectFiles, chatMessages, projectMemory, teams, billing, audit |
| Convex auth config | `frontend/convex/auth.config.ts` | **PRODUCTION** — Clerk JWT provider configured |
| ConvexClerkProvider | `frontend/components/providers/ConvexClerkProvider.tsx` | **PRODUCTION** — wraps entire app with `ConvexProviderWithClerk` |
| Python Convex client | `backend/db/convex.py` — `ConvexDB` class | **PRODUCTION** — HTTP client with dev-mode fallback. Has `insert`, `get`, `update`, `delete`, `list`, `find`, `query`, `mutation`, `action` |
| Convex projects CRUD (TS) | `backend/convex/projects.ts` | **PRODUCTION** — `create`, `update`, `remove`, `listByOrg`, `listByUser`, `listByStatus`, `listFiles`, `createFile`, `updateFile`, `deleteFile`, `createMessage` |
| FastAPI server | `backend/api/server.py` | **PRODUCTION** — REST + WebSocket, already uses `get_convex_db()` for all CRUD. Projects are Convex-persisted (NOT in-memory). |
| Sandpack LivePreview | `frontend/components/preview/LivePreview.tsx` | **PRODUCTION** — `SandpackProvider` with `react-ts` template, device frames, zoom, Tailwind CDN |
| Dockerfile (backend) | `backend/Dockerfile` | **PRODUCTION** — multi-stage, health check, non-root user |
| Railway config | `backend/railway.toml` | **EXISTS** — points to `api.server_enhanced:app` (needs fixing to `main:app`) |
| Frontend deps | `frontend/package.json` | **PRODUCTION** — `convex@^1.13.0`, `@clerk/nextjs@^5.0.0`, `next@14.2.25` |
| Frontend layout | `frontend/app/layout.tsx` | **PRODUCTION** — wraps children with `ConvexClerkProvider` |

### Key Finding: What the Plan Says Is Missing vs Reality

| Plan Claim | Reality |
|------------|---------|
| "Projects stored in-memory, lost on restart" | **FALSE** — `server.py:182-193` uses `db.insert("projects", ...)` via `get_convex_db()`. Projects survive restarts. |
| "Need to set up Convex schema" | **PARTIALLY FALSE** — Both `frontend/convex/schema.ts` and `backend/convex/schema.ts` already define `projects`, `projectFiles`, `chatMessages`. But `builds` and `agentStatus` tables are missing. |
| "Need ConvexProvider with Clerk" | **FALSE** — `ConvexClerkProvider.tsx` already exists and is used in `layout.tsx`. |
| "Need Python Convex client" | **FALSE** — `backend/db/convex.py` already has `ConvexClient` and `ConvexDB` classes. |
| "Need Sandpack integration" | **PARTIALLY FALSE** — `LivePreview.tsx` already works with Sandpack. But it's not wired to receive build output automatically. |

---

## Phase 1.0: Foundation — "Make It Accessible"

### 1.0.0 Scope Adjustment Based on Inventory

Since the core Convex schema, auth, Python client, and Sandpack preview **already exist**, Phase 1.0 focuses on:

1. **Schema Extension** — Add `builds` and `agentStatus` tables for build tracking
2. **Backend Convex Client Hardening** — Replace dev-mode CRUD in `ConvexDB` with real Convex function calls via HTTP API (currently `insert/get/update/delete` are dev-mode only — they use `self._db` dict, never hit Convex)
3. **Auth Middleware** — Add Clerk JWT validation to FastAPI endpoints (currently no auth)
4. **Build Progress Wiring** — Connect LangGraph agent pipeline output to Convex `builds`/`agentStatus` tables for real-time UI updates
5. **Sandpack Auto-Wire** — Push generated files from build output into `LivePreview` automatically
6. **Railway/Vercel Deployment Fixes** — Fix `railway.toml` entry point, add `vercel.json`

---

### 1.1 Convex Schema Extension

#### 1.1.1 New Tables (add to `backend/convex/schema.ts`)

```typescript
// ===== BUILDS — tracks each build invocation =====
builds: defineTable({
  projectId: v.id("projects"),
  userId: v.id("users"),
  status: v.union(
    v.literal("queued"),
    v.literal("running"),
    v.literal("completed"),
    v.literal("failed"),
    v.literal("cancelled")
  ),
  requirements: v.string(),
  // Cost tracking (from BuildCostTracker)
  totalCost: v.optional(v.number()),      // USD
  totalTokens: v.optional(v.number()),
  totalDuration: v.optional(v.number()),   // ms
  // Per-node cost breakdown (JSON)
  costBreakdown: v.optional(v.any()),
  // Result metadata
  filesGenerated: v.optional(v.number()),
  securityScore: v.optional(v.number()),   // 0-100
  completenessScore: v.optional(v.number()), // 0-100
  errorMessage: v.optional(v.string()),
  // VOS3 kernel resource tracking (populated when DEPLOYMENT_MODE=vos3)
  vos3Metadata: v.optional(v.object({
    kernelPid: v.optional(v.number()),           // VOS3 kernel PID of the build supervisor process
    resourceQuotaUsed: v.optional(v.object({
      cpuTimeMs: v.number(),                     // CPU time consumed by build agents on VOS3
      memoryPagesAllocated: v.number(),          // PMM pages allocated during build
      memoryPeakBytes: v.number(),               // Peak memory usage (bytes)
      ipcMessagesSent: v.number(),               // IPC messages between build agents
      syscallsExecuted: v.number(),              // Total syscalls made by build process
    })),
    osSecurityContext: v.optional(v.object({
      sandboxId: v.optional(v.string()),         // VOS3 sandbox ID (if build runs sandboxed)
      allowedSyscalls: v.optional(v.array(v.string())), // Syscall whitelist applied
      securityViolations: v.optional(v.number()), // Count of blocked syscall attempts
      isolationLevel: v.optional(v.string()),    // "full" | "partial" | "none"
      uid: v.optional(v.number()),               // VOS3 UID under which build ran
      gid: v.optional(v.number()),               // VOS3 GID under which build ran
    })),
  })),
  createdAt: v.number(),
  completedAt: v.optional(v.number()),
})
  .index("by_project", ["projectId"])
  .index("by_user", ["userId"])
  .index("by_status", ["status"])
  .index("by_project_status", ["projectId", "status"]),

// ===== AGENT STATUS — real-time per-node progress during a build =====
agentStatus: defineTable({
  buildId: v.id("builds"),
  agentName: v.string(),     // "architect" | "frontend" | "backend" | "tester" | "reviewer" | etc.
  model: v.optional(v.string()),    // "claude-opus-4-6" | "gpt-5.2-pro" | etc.
  status: v.union(
    v.literal("idle"),
    v.literal("running"),
    v.literal("completed"),
    v.literal("failed"),
    v.literal("skipped")
  ),
  progressPercent: v.optional(v.number()),   // 0-100
  progressMessage: v.optional(v.string()),   // "Designing API schema..."
  cost: v.optional(v.number()),      // USD for this node
  tokens: v.optional(v.number()),
  duration: v.optional(v.number()),  // ms
  filesCreated: v.optional(v.array(v.string())),
  errorMessage: v.optional(v.string()),
  // VOS3 kernel resource tracking per-agent (populated when DEPLOYMENT_MODE=vos3)
  vos3Metadata: v.optional(v.object({
    kernelPid: v.optional(v.number()),           // VOS3 PID of this agent's worker process
    resourceQuotaUsed: v.optional(v.object({
      cpuTimeMs: v.number(),                     // CPU time consumed by this specific agent
      memoryPagesAllocated: v.number(),          // PMM pages used by this agent
      memoryPeakBytes: v.number(),               // Peak memory for this agent
    })),
    osSecurityContext: v.optional(v.object({
      sandboxId: v.optional(v.string()),         // VOS3 sandbox ID for this agent process
      allowedSyscalls: v.optional(v.array(v.string())), // Agent-specific syscall whitelist
      securityViolations: v.optional(v.number()), // Blocked syscalls by this agent
      isolationLevel: v.optional(v.string()),    // "full" | "partial" | "none"
    })),
  })),
  startedAt: v.optional(v.number()),
  completedAt: v.optional(v.number()),
})
  .index("by_build", ["buildId"])
  .index("by_build_agent", ["buildId", "agentName"]),
```

#### 1.1.2 Context Resilience Table (add to `backend/convex/schema.ts`)

The LLM session driving a build can be interrupted at any time (Railway restart, timeout, OOM kill). Without state persistence, the entire build must restart from scratch — wasting all prior LLM calls and cost.

The `contextSnapshots` table stores serialized agent state so builds can **rehydrate** from the last checkpoint.

```typescript
// ===== CONTEXT SNAPSHOTS — agent state checkpoints for build resilience =====
contextSnapshots: defineTable({
  buildId: v.id("builds"),
  // Which pipeline node produced this snapshot
  checkpointNode: v.string(),    // "architect" | "frontend" | "backend" | "aggregator" | etc.
  // Serialized LangGraph ProjectState (JSON)
  // Contains: requirements, architecture, frontend_code, backend_code, tests, review_results, iteration, errors
  serializedState: v.string(),   // JSON.stringify(ProjectState) — stored as string to avoid Convex 1MB doc limit on nested objects
  // Decision log — why the agent made the choices it did (for audit + rehydration context)
  decisionLog: v.array(
    v.object({
      agent: v.string(),           // which agent made the decision
      decision: v.string(),        // "Selected React + Tailwind for frontend"
      reasoning: v.optional(v.string()),  // "User specified React in requirements"
      timestamp: v.number(),
    })
  ),
  // Task progress — which items are done vs pending
  taskProgress: v.object({
    totalTasks: v.number(),        // total pipeline nodes
    completedTasks: v.number(),    // nodes that finished successfully
    currentTask: v.string(),       // node currently executing (or last completed)
    completedNodes: v.array(v.string()),  // ["architect", "frontend", "backend"]
    pendingNodes: v.array(v.string()),    // ["tester", "reviewer", "finalize"]
  }),
  // Cost accumulated up to this checkpoint (so we don't double-count on resume)
  accumulatedCost: v.optional(v.number()),    // USD
  accumulatedTokens: v.optional(v.number()),
  // Snapshot metadata
  createdAt: v.number(),
  sizeBytes: v.optional(v.number()),  // size of serializedState for monitoring
})
  .index("by_build", ["buildId"])
  .index("by_build_node", ["buildId", "checkpointNode"]),
```

**Snapshot Lifecycle**:
```
1. Build starts → no snapshot exists
2. Architect completes → snapshot created with architecture + decision log
3. Frontend completes → snapshot updated (or new snapshot at "frontend" node)
4. [BUILD INTERRUPTED — Railway restart]
5. Build resumes:
   a. Query contextSnapshots:getLatestByBuild({ buildId })
   b. Deserialize ProjectState from serializedState
   c. Read taskProgress.pendingNodes → resume from first pending node
   d. Inject decisionLog into agent context → agents understand prior decisions
   e. Set accumulatedCost as cost offset → no double-counting
6. Build continues from checkpoint (e.g., skip architect+frontend, start at backend)
```

**Rehydration Logic** (in `multi_agent.py`):
```python
async def resume_build(self, build_id: str) -> ProjectState:
    """Resume a build from the latest context snapshot."""
    db = get_convex_db()
    snapshot = await db.query("contextSnapshots:getLatestByBuild", {"buildId": build_id})
    if not snapshot:
        raise ValueError(f"No checkpoint found for build {build_id}")

    state = json.loads(snapshot["serializedState"])
    state["_resume_from"] = snapshot["taskProgress"]["currentTask"]
    state["_completed_nodes"] = snapshot["taskProgress"]["completedNodes"]
    state["_cost_offset"] = snapshot.get("accumulatedCost", 0)
    state["_decision_log"] = snapshot["decisionLog"]
    return state
```

**Design Decisions**:
- `serializedState` is a **string** (not `v.any()`) because Convex documents have a 1MB limit on nested objects. JSON-stringifying the full ProjectState avoids hitting this limit for large codebases.
- One snapshot per node completion (not per LLM token) — this keeps write frequency low (~5-9 snapshots per build).
- `decisionLog` is stored alongside state so that when an agent resumes, it can read *why* previous agents made their choices — preventing contradictory decisions.

**New Convex Functions** (add to `backend/convex/contextSnapshots.ts`):

```typescript
// === QUERIES ===
export const getLatestByBuild  // args: { buildId: Id<"builds"> } → most recent snapshot for a build
export const listByBuild       // args: { buildId: Id<"builds"> } → all snapshots (checkpoint history)

// === MUTATIONS ===
export const create            // args: { buildId, checkpointNode, serializedState, decisionLog, taskProgress, accumulatedCost?, accumulatedTokens? }
export const deleteByBuild     // args: { buildId: Id<"builds"> } → cleanup after build completes successfully
```

#### 1.1.3 New Convex Functions (new file: `backend/convex/builds.ts`)

```typescript
// === QUERIES ===
export const getById           // args: { id: Id<"builds"> }
export const listByProject     // args: { projectId: Id<"projects"> } → index by_project
export const listByUser        // args: { userId: Id<"users"> } → index by_user
export const getLatestByProject // args: { projectId: Id<"projects"> } → latest by createdAt

// === MUTATIONS ===
export const create            // args: { projectId, userId, requirements, status: "queued" }
export const updateStatus      // args: { id, status, errorMessage?, completedAt? }
export const updateCost        // args: { id, totalCost, totalTokens, totalDuration, costBreakdown }
export const updateScores      // args: { id, securityScore?, completenessScore?, filesGenerated? }
```

#### 1.1.3 New Convex Functions (new file: `backend/convex/agentStatus.ts`)

```typescript
// === QUERIES ===
export const listByBuild       // args: { buildId: Id<"builds"> } → all agents for a build

// === MUTATIONS ===
export const upsert            // args: { buildId, agentName, status, model?, progressPercent?, progressMessage?, cost?, tokens?, duration?, filesCreated?, startedAt?, completedAt? }
                               // Implementation: query by_build_agent → patch if exists, insert if not
```

#### 1.1.4 Index Design Rationale

| Index | Query Pattern | Justification |
|-------|--------------|---------------|
| `builds.by_project` | "Show all builds for project X" | Project detail page, build history |
| `builds.by_user` | "Show my builds across projects" | Dashboard, billing usage |
| `builds.by_status` | "How many builds are running?" | Admin monitoring, rate limiting |
| `builds.by_project_status` | "Is project X currently building?" | Prevent concurrent builds |
| `agentStatus.by_build` | "Show all agent cards for build Y" | Real-time agent dashboard |
| `agentStatus.by_build_agent` | "Update architect status for build Y" | Upsert during build progress |
| `contextSnapshots.by_build` | "Get all checkpoints for build Y" | Resume build, checkpoint history |
| `contextSnapshots.by_build_node` | "Get checkpoint at architect node for build Y" | Resume from specific node |

#### 1.1.5 Schema Relationship Diagram

```
users ─────┐
           │ userId
           ▼
projects ──┬── projectFiles  (by_project, by_project_path)
           │
           ├── chatMessages  (by_project)
           │
           └── builds ──┬── agentStatus       (by_build, by_build_agent)
                │       │
                │       └── contextSnapshots   (by_build, by_build_node)
                │
                (by_project, by_user, by_status)
```

---

### 1.2 Backend Convex Client Hardening

#### 1.2.1 Problem Statement

The existing `ConvexDB` class in `backend/db/convex.py` has **two modes**:

1. **Dev mode** (`self.dev_mode = True`): Uses `self._db` dict (in-memory). The `insert()`, `get()`, `update()`, `delete()`, `list()`, `find()` methods all operate on this dict.
2. **Production mode**: The parent `ConvexClient` class has `query()`, `mutation()`, `action()` methods that call Convex HTTP API. But `ConvexDB.insert()` etc. **never use these** — they always use the in-memory dict regardless of mode.

**Evidence**: `ConvexDB.insert()` at line 183 directly writes to `self._db` — there's no conditional check for `self.dev_mode`. Same for `get()`, `update()`, `delete()`, `list()`, `find()`.

**Consequence**: Even with `CONVEX_URL` and `CONVEX_DEPLOY_KEY` set, `server.py` calls like `db.insert("projects", {...})` go to in-memory storage, not Convex. The `ConvexClient.query()/mutation()` methods work with Convex, but `server.py` doesn't use them — it uses the CRUD methods on `ConvexDB`.

#### 1.2.2 Solution: Route CRUD Through Convex Functions

Modify `ConvexDB` so that in production mode, CRUD methods delegate to Convex functions via HTTP:

```python
# backend/db/convex.py — ConvexDB (modified)

async def insert(self, table: str, data: dict) -> str:
    if self.dev_mode:
        return self._dev_insert(table, data)
    # Route to Convex mutation: e.g. "projects:create"
    return await self.mutation(f"{table}:create", data)

async def get(self, table: str, doc_id: str) -> Optional[dict]:
    if self.dev_mode:
        return self._dev_get(table, doc_id)
    return await self.query(f"{table}:getById", {"id": doc_id})

async def update(self, table: str, doc_id: str, data: dict) -> Optional[dict]:
    if self.dev_mode:
        return self._dev_update(table, doc_id, data)
    return await self.mutation(f"{table}:update", {"id": doc_id, **data})

async def delete(self, table: str, doc_id: str) -> bool:
    if self.dev_mode:
        return self._dev_delete(table, doc_id)
    await self.mutation(f"{table}:remove", {"id": doc_id})
    return True

async def list(self, table: str) -> list:
    if self.dev_mode:
        return self._dev_list(table)
    return await self.query(f"{table}:list", {})
```

#### 1.2.3 Rate Limit Protection for High-Frequency Agent Updates

During a build, the LangGraph pipeline fires rapid status updates (potentially 10-50/s during streaming). Convex has rate limits (~100 mutations/s on Pro, ~25/s on Free).

**Strategy: Batched Write Buffer**

```python
# New class in backend/db/convex.py

class ConvexWriteBuffer:
    """
    Batches high-frequency writes to avoid Convex rate limits.

    Usage:
        buffer = ConvexWriteBuffer(client, flush_interval_ms=500, max_batch_size=10)
        await buffer.enqueue("agentStatus:upsert", {...})
        # Buffer auto-flushes every 500ms or when 10 items queued
    """

    def __init__(self, client: ConvexDB, flush_interval_ms: int = 500, max_batch_size: int = 10):
        self._client = client
        self._queue: list[tuple[str, dict]] = []
        self._flush_interval = flush_interval_ms / 1000.0
        self._max_batch = max_batch_size
        self._flush_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def enqueue(self, function_name: str, args: dict) -> None:
        """Add a mutation to the buffer. Flushes automatically."""
        async with self._lock:
            # Coalesce: if same function+key exists, replace (last-write-wins)
            key = (function_name, args.get("buildId", ""), args.get("agentName", ""))
            self._queue = [(fn, a) for fn, a in self._queue if (fn, a.get("buildId", ""), a.get("agentName", "")) != key]
            self._queue.append((function_name, args))

            if len(self._queue) >= self._max_batch:
                await self._flush()
            elif self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self):
        await asyncio.sleep(self._flush_interval)
        async with self._lock:
            await self._flush()

    async def _flush(self):
        if not self._queue:
            return
        batch = self._queue[:]
        self._queue.clear()
        # Fire mutations concurrently (bounded)
        tasks = [self._client.mutation(fn, args) for fn, args in batch]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def flush_now(self):
        """Force-flush all pending writes. Call at build completion."""
        async with self._lock:
            await self._flush()
```

**Key Design Decisions**:
- **Coalescing**: If `agentStatus:upsert` for the same `(buildId, agentName)` is enqueued twice before flush, only the latest state is sent. This dramatically reduces writes for streaming progress updates.
- **Flush interval**: 500ms default → max 2 writes/second per agent. With 9 agents, worst case = 18 mutations/s (well under Convex Pro limit of 100/s).
- **Force flush**: Called at build completion to ensure final state is persisted.

#### 1.2.4 `server.py` Impact

The current `server.py` uses `get_convex_db()` and calls `db.insert("projects", {...})`. With the changes above, these calls will transparently route to Convex functions in production. **No changes needed to `server.py`** — the interface stays the same.

However, the Convex function name convention must match: `server.py` calls `db.insert("projects", data)` which maps to `projects:create`. The existing `backend/convex/projects.ts` already exports `create` — this mapping works.

For tables where no Convex function exists yet (e.g. `sessions`), the client falls back to dev-mode storage. We need to either:
- (a) Create Convex functions for every table used by `server.py`, or
- (b) Add a `_generic_mutation` fallback that uses a generic Convex action

**Recommendation**: Option (a) for `builds` and `agentStatus` (critical path). Keep dev-mode fallback for non-critical tables like `sessions` until Phase 2.

---

### 1.3 Clerk Auth Integration

#### 1.3.1 Current State

- `frontend/convex/auth.config.ts` already configures Clerk as JWT provider for Convex
- `ConvexClerkProvider.tsx` wraps the app with `ConvexProviderWithClerk` + `useAuth`
- **Missing**: Backend (FastAPI) has **no auth middleware** — any request to `/api/*` is unauthenticated

#### 1.3.2 Shared Auth Context (VOS3 ↔ VBuilder)

Both VOS3 and VBuilder use the **same Clerk tenant** and the **same Convex deployment**. This means:

```
User signs into VOS3 at vos3.app
  → Clerk issues JWT with claims: { sub: "user_xxx", org_id: "org_yyy" }
  → Frontend stores JWT in cookie/localStorage
  → Convex validates JWT via auth.config.ts

User navigates to VBuilder at vbuilder.vercel.app (or /builder route in VOS3)
  → SAME Clerk tenant → SAME JWT → already authenticated
  → ConvexProviderWithClerk passes same JWT to Convex
  → Backend API receives same JWT in Authorization header
```

**No additional auth setup needed on the frontend** — `ConvexClerkProvider` handles it.

#### 1.3.3 Backend Auth Middleware (new file: `backend/middleware/clerk_auth.py`)

```python
"""
Clerk JWT validation middleware for FastAPI.

Validates the JWT from the Authorization: Bearer header against Clerk's JWKS endpoint.
Extracts user identity (clerk_id, email, org_id) and makes it available via dependency injection.

Environment Variables:
  CLERK_ISSUER_URL  — e.g. "https://your-domain.clerk.accounts.dev"
  CLERK_SECRET_KEY  — Clerk secret key (for JWKS endpoint auth)
"""

from dataclasses import dataclass

@dataclass
class AuthUser:
    """Authenticated user extracted from Clerk JWT."""
    clerk_id: str          # Clerk user ID (sub claim)
    email: str | None      # Email from JWT claims
    org_id: str | None     # Active organization ID (org_id claim)
    convex_user_id: str | None  # Resolved Convex user _id (lazy, cached)
```

**JWT Validation Flow**:

```
1. Extract token from `Authorization: Bearer <token>` header
2. Fetch JWKS from `{CLERK_ISSUER_URL}/.well-known/jwks.json` (cached 1 hour)
3. Decode JWT using JWKS public key, verify:
   - `iss` matches CLERK_ISSUER_URL
   - `exp` not expired
   - `aud` if set (optional)
4. Extract claims: sub (clerk_id), email, org_id
5. Return AuthUser dataclass
6. If validation fails: return 401 Unauthorized
```

**FastAPI Dependency**:

```python
async def get_current_user(request: Request) -> AuthUser:
    """FastAPI dependency that validates Clerk JWT and returns AuthUser."""
    # ... validation logic ...

async def get_optional_user(request: Request) -> AuthUser | None:
    """Same as above but returns None instead of 401 for unauthenticated requests."""
    # ... used for public endpoints that optionally benefit from auth ...
```

**Libraries**: `PyJWT>=2.8.0` + `cryptography>=41.0.0` (for RS256 JWKS validation)

#### 1.3.4 Endpoint Auth Matrix

| Endpoint | Auth | Rationale |
|----------|------|-----------|
| `GET /api/health` | None | Health checks must be unauthenticated |
| `GET /` | None | Root health check |
| `POST /api/projects` | **Required** | User must be identified for project ownership |
| `GET /api/projects` | **Required** | List user's own projects only |
| `GET /api/projects/{id}` | **Required** | Verify user owns project or is collaborator |
| `PUT /api/projects/{id}/files` | **Required** | Verify user has write access |
| `DELETE /api/projects/{id}` | **Required** | Verify user is owner |
| `POST /api/chat` | **Required** | Identify user for memory/billing |
| `POST /api/chat/stream` | **Required** | Same |
| `WS /ws/chat/{session_id}` | **Required** | Validate JWT from query param or first message |
| `GET /api/memory` | **Required** | User-scoped memory |
| `GET /api/approvals` | **Required** | User-scoped approvals |

#### 1.3.5 Convex User Resolution

When the backend receives a Clerk JWT, it gets `clerk_id` (e.g., `"user_2abc123"`). To create projects in Convex, it needs the Convex `_id` for that user.

**Resolution flow**:
```
1. Backend receives JWT → extracts clerk_id
2. Query Convex: users:getByClerkId({ clerkId: "user_2abc123" })
3. If found → cache convex_user_id for this session
4. If NOT found → user hasn't been synced to Convex yet
   → Call users:syncFromClerk to create the user record
   → This happens automatically via Clerk webhook (clerk_webhook.py) but may race
```

**Caching**: Per-request resolution is fine — Convex queries are fast (~5ms). For optimization, an LRU cache (`clerk_id → convex_user_id`, TTL 5min) avoids repeated lookups.

---

### 1.4 Build Progress Wiring

#### 1.4.1 Problem

The LangGraph pipeline in `backend/ai/agents/multi_agent.py` runs builds synchronously (or via background task). There's no mechanism to push per-node progress to the frontend in real time.

Currently, `server.py:207-215` runs `builder.build(requirements)` as a background task and updates the project status to "ready" or "error" when done. The frontend has no visibility into which agent is running, what it's doing, or how much it costs.

#### 1.4.2 Architecture: Build Event Bus

```
LangGraph Pipeline (multi_agent.py)
     │
     │ emits BuildEvent objects
     ▼
BuildEventBus (in-process async queue)
     │
     ├──► ConvexWriteBuffer → Convex mutations (agentStatus:upsert, builds:updateStatus)
     │                         ↓
     │                    Frontend useQuery(api.agentStatus.listByBuild) → reactive UI
     │
     └──► SSE Stream → /api/builds/{id}/stream (for streaming LLM text output)
```

**Key Insight**: Convex's `useQuery` provides **automatic real-time updates** — when the backend mutates `agentStatus`, all frontend clients subscribed via `useQuery` see the change instantly. This replaces the need for SSE for state updates (agent status, cost, files created). SSE is still used for streaming LLM text tokens.

#### 1.4.3 BuildEvent Types

```python
@dataclass
class BuildEvent:
    """Event emitted by the LangGraph pipeline."""
    build_id: str
    event_type: str  # agent_start | agent_progress | agent_complete | agent_error | build_complete | build_error | file_created | cost_update
    agent_name: str | None = None
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
```

#### 1.4.4 Pipeline Integration Points

Each node in `multi_agent.py` gets wrapped with event emission:

```python
# Pseudocode — applied to each _*_node method

async def _architect_node(self, state: ProjectState) -> ProjectState:
    self.event_bus.emit(BuildEvent(
        build_id=state.build_id,
        event_type="agent_start",
        agent_name="architect",
        data={"model": "gpt-5.2-pro", "phase": "System Design"}
    ))

    # ... existing architect logic ...

    self.event_bus.emit(BuildEvent(
        build_id=state.build_id,
        event_type="agent_complete",
        agent_name="architect",
        data={"duration_ms": elapsed, "cost": node_cost, "tokens": token_count}
    ))
    return state
```

#### 1.4.5 Backend API: Build Endpoint

Modify `server.py` to:

1. Create a `builds` record when project generation starts
2. Initialize `agentStatus` records for all pipeline nodes
3. Return `build_id` to the frontend
4. Provide SSE endpoint for streaming text output

```python
# Modified server.py flow:

@app.post("/api/projects")
async def create_project(project: ProjectCreate, user: AuthUser = Depends(get_current_user)):
    db = get_convex_db()

    # 1. Create project
    project_id = await db.mutation("projects:create", {
        "userId": user.convex_user_id,
        "orgId": user.org_id,
        "name": project.name,
        # ...
    })

    # 2. Create build record
    build_id = await db.mutation("builds:create", {
        "projectId": project_id,
        "userId": user.convex_user_id,
        "requirements": project.requirements,
        "status": "queued",
    })

    # 3. Start build in background
    background_tasks.add_task(run_build, project_id, build_id, project.requirements)

    return {"project_id": project_id, "build_id": build_id, "status": "queued"}

# New SSE endpoint for streaming LLM output
@app.get("/api/builds/{build_id}/stream")
async def stream_build(build_id: str, user: AuthUser = Depends(get_current_user)):
    async def generate():
        async for event in event_bus.subscribe(build_id):
            yield f"data: {json.dumps(event.to_dict())}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")
```

---

### 1.5 UI Architecture — Key React Components for Phase 1

#### 1.5.1 Component Hierarchy

```
app/builder/page.tsx (or /projects/[id]/page.tsx)
├── BuilderLayout
│   ├── ChatPanel (left)
│   │   ├── ChatInput
│   │   └── ChatMessages (useQuery → chatMessages:listByProject)
│   │
│   ├── EditorPanel (center)
│   │   ├── FileTree (useQuery → projects:listFiles)
│   │   └── Monaco Editor
│   │
│   ├── PreviewPanel (right)
│   │   ├── PreviewToolbar (device, zoom)
│   │   └── LivePreview (Sandpack — ALREADY EXISTS)
│   │
│   └── BuildStatusBar (bottom)
│       ├── AgentStatusCards (useQuery → agentStatus:listByBuild)
│       ├── CostIndicator (from builds record)
│       └── BuildProgress (from builds.status)
```

#### 1.5.2 Convex Subscriptions (Frontend Queries)

| Component | Convex Query | Subscription Key | Update Frequency |
|-----------|-------------|------------------|------------------|
| Project list page | `useQuery(api.projects.listByUser, { userId })` | Per-user | On project CRUD |
| File tree | `useQuery(api.projects.listFiles, { projectId })` | Per-project | On file create/update/delete during build |
| Chat history | `useQuery(api.projects.listMessages, { projectId })` | Per-project | On each chat message |
| Agent dashboard | `useQuery(api.agentStatus.listByBuild, { buildId })` | Per-build | High-frequency during build (coalesced by WriteBuffer) |
| Build status | `useQuery(api.builds.getById, { id: buildId })` | Per-build | On status transitions |
| Build history | `useQuery(api.builds.listByProject, { projectId })` | Per-project | On build completion |

#### 1.5.3 New Components for Phase 1

| Component | File | Purpose | Convex Subscription |
|-----------|------|---------|---------------------|
| `BuildStatusBar` | `frontend/components/build/BuildStatusBar.tsx` | Bottom bar showing current build progress | `builds.getById` |
| `AgentStatusCards` | `frontend/components/build/AgentStatusCards.tsx` | Grid of agent cards (name, model, status, cost) | `agentStatus.listByBuild` |
| `BuildHistory` | `frontend/components/build/BuildHistory.tsx` | List of past builds for a project | `builds.listByProject` |

#### 1.5.4 Sandpack Auto-Wire

The existing `LivePreview.tsx` accepts a `files: Record<string, string>` prop. To auto-wire build output:

```typescript
// In the builder page component:

const project = useQuery(api.projects.getById, { id: projectId });
const projectFiles = useQuery(api.projects.listFiles, { projectId });

// Transform projectFiles into the format LivePreview expects
const previewFiles = useMemo(() => {
  if (!projectFiles) return {};
  const files: Record<string, string> = {};
  for (const file of projectFiles) {
    files[file.path] = file.content;
  }
  return files;
}, [projectFiles]);

// LivePreview re-renders automatically when Convex pushes file updates
return <LivePreview files={previewFiles} />;
```

**Key advantage**: When the build pipeline creates/updates files via `projects:createFile` or `projects:updateFile` mutations, the frontend `useQuery(api.projects.listFiles)` subscription fires automatically, and `LivePreview` re-renders with the new files. **No manual polling or SSE needed for file updates.**

---

### 1.6 Deployment Configuration

#### 1.6.1 Railway (Backend)

**Fix**: `railway.toml` currently points to `api.server_enhanced:app` which doesn't exist. Fix to `main:app`.

```toml
# backend/railway.toml
[build]
builder = "DOCKERFILE"
dockerfilePath = "Dockerfile"

[deploy]
numReplicas = 1
startCommand = "uvicorn main:app --host 0.0.0.0 --port $PORT --workers 2"
healthcheckPath = "/api/health"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

**Required env vars on Railway**:
```
CONVEX_URL=https://your-deployment.convex.cloud
CONVEX_DEPLOY_KEY=prod:xxx
CLERK_ISSUER_URL=https://your-domain.clerk.accounts.dev
CLERK_SECRET_KEY=sk_live_xxx
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
GOOGLE_API_KEY=xxx
REDIS_URL=redis://default:xxx@xxx.railway.internal:6379
```

#### 1.6.2 Vercel (Frontend)

```json
// vercel.json (project root or frontend/)
{
  "framework": "nextjs",
  "buildCommand": "cd frontend && npm run build",
  "outputDirectory": "frontend/.next",
  "installCommand": "cd frontend && npm install",
  "env": {
    "NEXT_PUBLIC_CONVEX_URL": "@convex-url",
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": "@clerk-publishable-key",
    "NEXT_PUBLIC_API_URL": "@api-url"
  }
}
```

**Required env vars on Vercel**:
```
NEXT_PUBLIC_CONVEX_URL=https://your-deployment.convex.cloud
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_xxx
NEXT_PUBLIC_API_URL=https://vbuilder-api.up.railway.app
```

#### 1.6.3 Deployment Sequence

```
1. Deploy Convex schema: cd frontend && npx convex deploy
   → Pushes schema.ts + all function files to Convex cloud

2. Deploy Backend to Railway: cd backend && railway up
   → Builds Docker image, starts uvicorn
   → Verify: curl https://vbuilder-api.up.railway.app/api/health

3. Deploy Frontend to Vercel: vercel --prod
   → Builds Next.js, deploys to CDN
   → Verify: https://vbuilder.vercel.app loads

4. Verify end-to-end:
   → Sign in via Clerk → JWT issued
   → Create project → Convex mutation succeeds
   → Start build → agentStatus updates visible in UI
   → Build completes → files appear in preview
```

---

### 1.7 Phase 1.0 — File Change Manifest

| File | Action | Description |
|------|--------|-------------|
| `backend/convex/schema.ts` | **MODIFY** | Add `builds` and `agentStatus` table definitions |
| `backend/convex/builds.ts` | **NEW** | Convex queries + mutations for build tracking |
| `backend/convex/agentStatus.ts` | **NEW** | Convex queries + mutations for agent status |
| `backend/convex/contextSnapshots.ts` | **NEW** | Convex queries + mutations for build resilience checkpoints |
| `backend/db/convex.py` | **MODIFY** | Route CRUD to Convex functions in prod mode; add `ConvexWriteBuffer` |
| `backend/middleware/clerk_auth.py` | **NEW** | Clerk JWT validation + `get_current_user` dependency |
| `backend/api/server.py` | **MODIFY** | Add auth middleware to endpoints; add `/api/builds/{id}/stream` SSE; create build records |
| `backend/railway.toml` | **MODIFY** | Fix startCommand to `main:app` |
| `frontend/components/build/BuildStatusBar.tsx` | **NEW** | Build progress bar with agent cards |
| `frontend/components/build/AgentStatusCards.tsx` | **NEW** | Per-agent status cards grid |
| `frontend/components/build/BuildHistory.tsx` | **NEW** | Build history list |
| `vercel.json` | **NEW** | Vercel deployment config (project root) |

**Total**: 6 new files, 4 modified files, 0 deleted files.

---

### 1.8 Phase 1.0 — Expert Acceptance Criteria

> **Gate**: ALL criteria below must be met before Phase 1.0 is considered complete. Each criterion has a specific test procedure and expected outcome. Items are grouped by subsystem.

#### AC-1: Authentication & Authorization

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-1.1 | Clerk JWT validated on ALL authenticated backend routes | Send requests to every endpoint in the Auth Matrix (Section 1.3.4) without `Authorization` header | All return `401 Unauthorized` with JSON error body `{"detail": "Missing or invalid authentication token"}` | **BLOCKER** |
| AC-1.2 | Valid Clerk JWT grants access | Obtain JWT from Clerk via frontend login, send to `POST /api/projects` | Returns `200` with `project_id` and `build_id` | **BLOCKER** |
| AC-1.3 | Expired JWT rejected | Send request with JWT where `exp` < current time | Returns `401` with `{"detail": "Token expired"}` | **BLOCKER** |
| AC-1.4 | Shared auth session between VOS3 and VBuilder | Log into VOS3 frontend, navigate to VBuilder route (`/builder`) | No re-authentication required. Same `clerk_id` appears in both Convex subscriptions. | **BLOCKER** |
| AC-1.5 | Convex user resolution works | Authenticated request from a user whose `clerkId` exists in Convex `users` table | `get_current_user()` returns `AuthUser` with valid `convex_user_id` (not `None`) | **BLOCKER** |
| AC-1.6 | WebSocket auth validated | Connect to `WS /ws/chat/{session_id}` without auth | Connection rejected with close code `4001` | HIGH |

#### AC-2: Convex Client Hardening

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-2.1 | `ConvexDB.insert()` routes to Convex in production mode | Set `CONVEX_URL` + `CONVEX_DEPLOY_KEY`, call `db.insert("projects", {...})` | Record appears in Convex dashboard. `self._db` dict remains empty. | **BLOCKER** |
| AC-2.2 | `ConvexDB.get()` reads from Convex in production mode | Insert via Convex dashboard, call `db.get("projects", id)` | Returns the Convex document (not `None`) | **BLOCKER** |
| AC-2.3 | Dev mode fallback preserved | Unset `CONVEX_URL`, call `db.insert()`, `db.get()`, `db.list()` | All operations work against in-memory `self._db` dict. No HTTP calls made. | HIGH |
| AC-2.4 | ConvexWriteBuffer prevents rate limiting during 5+ parallel agent runs | Simulate 5 concurrent builds, each with 9 agents emitting status updates every 100ms (= 450 updates/s raw) | WriteBuffer coalesces to ≤50 mutations/s total. No Convex 429 errors. All final states correct. | **BLOCKER** |
| AC-2.5 | ConvexWriteBuffer coalescing correctness | Enqueue 10 rapid updates for same `(buildId, agentName)` within one flush interval | Only 1 mutation sent to Convex, containing the latest state | HIGH |
| AC-2.6 | ConvexWriteBuffer `flush_now()` delivers final state | Call `flush_now()` at build completion | All pending writes flushed. `agentStatus` records match expected final state. | **BLOCKER** |

#### AC-3: Schema & Data Layer

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-3.1 | `builds` table created with all fields and indexes | Run `npx convex deploy`, check Convex dashboard | Table exists with fields: `projectId`, `userId`, `status`, `requirements`, `totalCost`, `vos3Metadata`, etc. All 4 indexes present. | **BLOCKER** |
| AC-3.2 | `agentStatus` table created with all fields and indexes | Same as above | Table exists with `vos3Metadata` field. Both indexes present. | **BLOCKER** |
| AC-3.3 | `contextSnapshots` table created | Same as above | Table exists with `serializedState` (string), `decisionLog`, `taskProgress` fields. Both indexes present. | **BLOCKER** |
| AC-3.4 | `vos3Metadata` accepts null gracefully | Create a build with `vos3Metadata: undefined` (cloud mode) | Build created successfully. Field is absent (not null). | HIGH |
| AC-3.5 | `vos3Metadata` stores VOS3 resource data | Create a build with full `vos3Metadata` object (mocked VOS3 mode) | All nested fields (`kernelPid`, `resourceQuotaUsed.cpuTimeMs`, `osSecurityContext.sandboxId`) stored and retrievable | HIGH |
| AC-3.6 | Context snapshot stores and retrieves full ProjectState | Serialize a ProjectState with 20+ files (100KB+ JSON), store as `serializedState` | Snapshot stored. `getLatestByBuild` returns it. JSON.parse recovers identical state. | HIGH |
| AC-3.7 | Context snapshot `decisionLog` captures agent decisions | Build with 3 agents making decisions | `decisionLog` array has 3+ entries, each with `agent`, `decision`, `timestamp` | MEDIUM |

#### AC-4: Build Progress & Real-Time Updates

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-4.1 | Build creates `builds` record on start | Call `POST /api/projects` with valid requirements | Convex `builds` table has new record with `status: "queued"`, correct `projectId` and `userId` | **BLOCKER** |
| AC-4.2 | Agent status updates during build | Start a build, observe Convex `agentStatus` table | Records created/updated per pipeline node. At least 5 records (architect, frontend, backend, tester, reviewer). | **BLOCKER** |
| AC-4.3 | Frontend receives real-time agent status | Subscribe via `useQuery(api.agentStatus.listByBuild, { buildId })` in browser | Agent cards update in <1s after backend mutates `agentStatus` | **BLOCKER** |
| AC-4.4 | Sandpack renders real-time from Convex `projectFiles` | Start a build, watch LivePreview panel | As `projects:createFile` mutations fire, `useQuery(api.projects.listFiles)` triggers re-render. Generated app appears in Sandpack without page refresh. | **BLOCKER** |
| AC-4.5 | Build completion updates final state | Wait for build to finish | `builds.status` = `"completed"`, `builds.completedAt` set, `builds.filesGenerated` > 0 | HIGH |
| AC-4.6 | Build failure records error | Trigger a build that fails (e.g., empty requirements after validation bypass) | `builds.status` = `"failed"`, `builds.errorMessage` contains error description | HIGH |

#### AC-5: Build Resilience (Context Snapshots)

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-5.1 | Checkpoint created after each pipeline node | Run a full build to completion | `contextSnapshots` table has one snapshot per completed node. `taskProgress.completedNodes` grows with each. | HIGH |
| AC-5.2 | Build resumes from checkpoint | Start a build, kill backend after architect completes, restart backend, call resume endpoint | Build skips architect, resumes from frontend node. Final output identical to uninterrupted build. | HIGH |
| AC-5.3 | Cost not double-counted on resume | Resume a build from checkpoint | `accumulatedCost` from snapshot used as offset. Final `builds.totalCost` equals sum of pre-interrupt + post-resume costs (not re-counted). | MEDIUM |
| AC-5.4 | Snapshots cleaned up after successful build | Build completes successfully | `contextSnapshots:deleteByBuild` called. No orphan snapshots remain. | MEDIUM |

#### AC-6: Deployment & Infrastructure

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-6.1 | Railway deployment health check | `curl https://vbuilder-api.up.railway.app/api/health` | Returns `200` with `{"status": "healthy"}` | **BLOCKER** |
| AC-6.2 | Vercel frontend loads | Navigate to `https://vbuilder.vercel.app` | Page loads, Clerk sign-in button visible, no console errors | **BLOCKER** |
| AC-6.3 | Railway `startCommand` fixed | Inspect `railway.toml` | `startCommand` = `"uvicorn main:app ..."` (not `api.server_enhanced:app`) | **BLOCKER** |
| AC-6.4 | End-to-end: sign in → create project → preview | Full user flow on deployed environment | User signs in via Clerk, creates project, build starts, agent cards update, preview shows generated app | **BLOCKER** |

#### Severity Definitions

| Severity | Definition | Gate Rule |
|----------|-----------|-----------|
| **BLOCKER** | Phase cannot be approved if this fails | Must pass |
| HIGH | Significant functionality gap, but workaround exists | Should pass, exceptions require documented rationale |
| MEDIUM | Quality/polish issue, acceptable for Phase 1 MVP | Best effort |

---

## Phase 2.0: Competitive Advantages — "Solve Every Industry Problem"

> **Document Status**: Phase 2.0 DETAILED
> **Last Updated**: 2026-03-26
> **Prerequisite**: Phase 1.0 COMPLETE (46/46 audit PASS)
> **Approval Workflow**: Requires explicit "PHASE 2.0 APPROVED" before implementation begins.

### 2.0.0 Scope Overview

Phase 2.0 focuses on four subsystems that address the critical weaknesses shared by every competing AI builder (Bolt, Lovable, v0, Replit):

| Subsystem | Problem Solved | Key File(s) |
|-----------|---------------|-------------|
| **IE-1: Architectural Guardrails** | AI code is unmaintainable spaghetti | `backend/ai/agents/guardrails.py` |
| **IE-2: Expert-in-the-Loop** | AI gets stuck with no escape hatch | `backend/api/expert_routes.py`, `backend/services/expert_pool.py` |
| **Semantic Loop Detection** | AI wastes tokens repeating the same fix | `backend/ai/agents/multi_agent.py` (modified) |
| **Context Budget Manager** | AI loses context after 15-20 components | `backend/ai/agents/context_manager.py` |

**Existing Infrastructure Leveraged**:

| Component | File | What Phase 2.0 Uses |
|-----------|------|---------------------|
| `MemoryManager` | `backend/memory/__init__.py` | `EmbeddingProvider.embed()` for cosine similarity, `store_fact/recall_facts` for cross-build learning |
| `SemanticStore._cosine_similarity()` | `backend/memory/__init__.py:454-463` | Reused directly for loop detection |
| `StaticAnalyzer` | `backend/code_review/rules.py` | Extended with ARCH001-ARCH010 rules |
| `ReviewFinding`, `ReviewSeverity`, `ReviewCategory` | `backend/code_review/service.py` | Data structures for guardrails violations |
| `@monitor_node` decorator | `backend/ai/agents/multi_agent.py:53-76` | Applied to all new pipeline nodes |
| `ProjectState` TypedDict | `backend/ai/agents/multi_agent.py:224-244` | Extended with new fields for loop detection + context budget |
| `_route_after_review()` | `backend/ai/agents/multi_agent.py:833-846` | Rewritten with 3-stage escalation |
| `ConvexWriteBuffer` | `backend/db/convex.py` | Used for expert request status updates |
| `router.yaml` role mappings | `backend/config/router.yaml` | Used by model switcher for alternate model selection |

---

### 2.1 IE-1: Architectural Guardrails — "Iron Rules Engine"

#### 2.1.1 Problem Statement

Every AI builder generates "code junk" — business logic inside UI components, API calls inside render functions, state management mixed with presentation. The code works on first generation but is unmaintainable. VBuilder's multi-agent pipeline can enforce architectural quality **before code reaches the user**.

**Current state**: The `ReviewerAgent` in `multi_agent.py:517-531` does a generic quality review but has no structured architectural enforcement. The `StaticAnalyzer` in `code_review/rules.py` has 27 regex-based rules (SEC001-008, PERF001-007, STYLE001-006, BP001-006) but none for architectural separation of concerns.

#### 2.1.2 Iron Rules Definition

Ten architectural rules enforced via AST analysis. Each rule has a unique ID, a detection strategy, and an auto-fix action.

```python
# backend/ai/agents/guardrails.py — Rule definitions

@dataclass
class IronRule:
    """An architectural rule enforced by the guardrails engine."""
    id: str                          # "ARCH001"
    title: str                       # "Business Logic in UI Component"
    description: str                 # Human-readable explanation
    severity: ReviewSeverity         # CRITICAL or HIGH — guardrails never use MEDIUM/LOW
    detect: Callable[[str, str, Dict], List[Violation]]  # (file_path, content, project_context) -> violations
    fix_instruction: str             # Natural-language instruction sent to the agent for rewrite
    # AST node types this rule inspects (for early filtering)
    target_node_types: List[str]     # ["ImportFrom", "Call", "Assign"]
```

| Rule ID | Title | Detection Strategy | Fix Instruction (sent to agent) | Severity |
|---------|-------|-------------------|--------------------------------|----------|
| ARCH001 | Business logic in UI component | AST: scan files in `components/` or `src/components/` for `fetch()`, `axios.*`, database query imports (`prisma`, `mongoose`, `drizzle`), or functions >20 lines that don't return JSX | "Move the business logic from `{file}:{line}` into `lib/api/{service_name}.ts` or `hooks/use{Name}.ts`. The component should import and call the extracted function." | CRITICAL |
| ARCH002 | API call outside api/services layer | AST: scan files NOT in `lib/api/`, `services/`, `hooks/` for `fetch()` or HTTP client calls (`axios.get`, `$.ajax`) | "Move the API call at `{file}:{line}` into `lib/api/`. Export a typed function that the component can import." | CRITICAL |
| ARCH003 | File exceeds 300 lines | Line count check (trivial, no AST needed) | "Split `{file}` ({line_count} lines) into smaller modules. Extract logical sections into separate files in the same directory." | HIGH |
| ARCH004 | Prop drilling > 2 levels | AST: trace prop names through component trees. If same prop name appears in >2 nested components without transformation, flag. | "Replace the prop drilling of `{prop_name}` through {depth} components with a React Context or a Zustand/Jotai store in `lib/stores/`." | HIGH |
| ARCH005 | Direct DOM manipulation | AST: detect `document.getElementById`, `document.querySelector`, `.innerHTML =`, `.appendChild` in React/Vue files | "Replace the direct DOM manipulation at `{file}:{line}` with React state/refs. Use `useRef()` if DOM access is truly needed." | HIGH |
| ARCH006 | Inconsistent naming | Regex + file system: PascalCase components not matching filename, hooks without `use` prefix, non-kebab-case API routes | "Rename `{entity}` to `{suggested_name}` to match project naming conventions." | HIGH |
| ARCH007 | Missing TypeScript types | AST: detect `any` type annotations, untyped function parameters in `.ts`/`.tsx` files, untyped API response handlers | "Add explicit TypeScript types to `{file}:{line}`. Replace `any` with a proper interface defined in `types/{domain}.ts`." | HIGH |
| ARCH008 | Circular dependency | Import graph: build directed graph of all file imports, detect cycles using DFS | "Break the circular dependency: {cycle_path}. Extract the shared interface into a new file `types/{name}.ts` that both modules import." | CRITICAL |
| ARCH009 | Unused imports/exports | AST: collect all imports, track usage across file. Cross-reference exports with all import statements in other files. | "Remove unused import `{import_name}` from `{file}:{line}`." | HIGH |
| ARCH010 | Missing error boundary at route level | AST: check top-level route components for `ErrorBoundary` wrapping (React) or `error.tsx` files in Next.js app dir | "Add an `error.tsx` file to `app/{route}/` or wrap the route component in an `<ErrorBoundary>` with a user-friendly fallback." | HIGH |

#### 2.1.3 AST Analysis Engine

The guardrails engine uses Python's `ast` module for Python code and a lightweight TypeScript/JSX parser for frontend code. We do NOT shell out to `tsc` or `eslint` — the guardrails run inside the LangGraph pipeline process.

```python
# backend/ai/agents/guardrails.py

import ast
import re
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from code_review.service import ReviewSeverity, ReviewCategory, ReviewFinding, CodeLocation


@dataclass
class Violation:
    """A single architectural violation."""
    rule_id: str
    file_path: str
    line: int
    message: str
    fix_instruction: str
    severity: ReviewSeverity


class ArchitecturalGuardrails:
    """
    AST-based architectural enforcement engine.

    Runs AFTER the Aggregator merges frontend + backend code,
    BEFORE the Tester generates tests.

    If violations are found:
      - CRITICAL: Route back to the originating agent (frontend/backend) with fix instructions
      - HIGH: Include in Reviewer context as mandatory review criteria
    """

    # Directories that classify as "UI layer" — business logic forbidden here
    UI_DIRS = {"components", "pages", "app", "views", "screens"}

    # Directories that classify as "service layer" — API calls allowed here
    SERVICE_DIRS = {"lib", "services", "hooks", "api", "utils", "helpers"}

    # Patterns that indicate business logic (forbidden in UI layer)
    BUSINESS_LOGIC_PATTERNS = [
        r'\bfetch\s*\(',                          # fetch() calls
        r'\baxios\b',                              # axios usage
        r'\b(?:prisma|mongoose|drizzle|knex)\b',  # ORM imports
        r'(?:SELECT|INSERT|UPDATE|DELETE)\s+',     # Raw SQL
    ]

    def __init__(self, rules: Optional[List[IronRule]] = None):
        self.rules = rules or self._default_rules()
        self._import_graph: Dict[str, List[str]] = {}  # file -> [imported_files]

    def validate_project(self, files: Dict[str, str]) -> List[Violation]:
        """
        Run all Iron Rules against a complete project file set.

        Args:
            files: Dict mapping file paths to file contents.
                   e.g. {"src/components/Dashboard.tsx": "import ...", "src/lib/api/users.ts": "..."}

        Returns:
            List of Violation objects, sorted by severity (CRITICAL first).
        """
        violations: List[Violation] = []

        # Phase 1: Build import graph (needed for ARCH004, ARCH008, ARCH009)
        self._import_graph = self._build_import_graph(files)

        # Phase 2: Per-file analysis (ARCH001-007, ARCH009, ARCH010)
        for file_path, content in files.items():
            for rule in self.rules:
                if rule.id == "ARCH008":
                    continue  # Cross-file rule, handled in Phase 3
                file_violations = rule.detect(file_path, content, {"files": files, "import_graph": self._import_graph})
                violations.extend(file_violations)

        # Phase 3: Cross-file analysis (ARCH008 — circular deps)
        arch008 = next((r for r in self.rules if r.id == "ARCH008"), None)
        if arch008:
            violations.extend(arch008.detect("", "", {"files": files, "import_graph": self._import_graph}))

        # Sort: CRITICAL first, then HIGH
        violations.sort(key=lambda v: 0 if v.severity == ReviewSeverity.CRITICAL else 1)
        return violations

    def _build_import_graph(self, files: Dict[str, str]) -> Dict[str, List[str]]:
        """
        Parse all files to build a directed import graph.

        For TypeScript/JavaScript: extract `import ... from '...'` statements via regex.
        For Python: use ast.parse to extract `import` and `from ... import` statements.

        Returns: Dict mapping each file path to a list of file paths it imports.
        """
        graph: Dict[str, List[str]] = {}

        for file_path, content in files.items():
            imports: List[str] = []

            if file_path.endswith(('.ts', '.tsx', '.js', '.jsx')):
                # Regex for: import X from './path' | import './path' | require('./path')
                for match in re.finditer(r'''(?:import\s+.*?\s+from\s+|import\s+|require\s*\(\s*)['"]([./][^'"]+)['"]''', content):
                    raw_path = match.group(1)
                    resolved = self._resolve_ts_import(file_path, raw_path, set(files.keys()))
                    if resolved:
                        imports.append(resolved)

            elif file_path.endswith('.py'):
                try:
                    tree = ast.parse(content)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom) and node.module:
                            # Convert module path to file path
                            module_path = node.module.replace('.', '/') + '.py'
                            if module_path in files:
                                imports.append(module_path)
                except SyntaxError:
                    pass  # Skip unparseable files

            graph[file_path] = imports

        return graph

    def _detect_circular_deps(self, graph: Dict[str, List[str]]) -> List[List[str]]:
        """
        DFS cycle detection on the import graph.
        Returns list of cycles, where each cycle is a list of file paths.
        """
        visited = set()
        rec_stack = set()
        cycles = []

        def dfs(node: str, path: List[str]):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, path)
                elif neighbor in rec_stack:
                    # Found a cycle — extract it
                    cycle_start = path.index(neighbor)
                    cycles.append(path[cycle_start:] + [neighbor])

            path.pop()
            rec_stack.discard(node)

        for node in graph:
            if node not in visited:
                dfs(node, [])

        return cycles

    def _classify_file_layer(self, file_path: str) -> str:
        """
        Classify a file as 'ui', 'service', or 'other' based on its directory.

        Returns: "ui" | "service" | "other"
        """
        parts = PurePosixPath(file_path).parts
        for part in parts:
            if part.lower() in self.UI_DIRS:
                return "ui"
            if part.lower() in self.SERVICE_DIRS:
                return "service"
        return "other"
```

#### 2.1.4 LangGraph Integration — The Rewrite Loop

The guardrails engine inserts as a **conditional gate** between Aggregator and Tester. If CRITICAL violations are found, the pipeline routes back to the responsible agent with targeted fix instructions — NOT the full original prompt.

**Modified Pipeline Graph**:

```
START → architect → [frontend || backend] → aggregator
    → guardrails_gate ──(clean)──→ tester → reviewer → finalize → END
         │
         └──(CRITICAL violations)──→ frontend or backend (with fix instructions)
                                        │
                                        └──→ aggregator → guardrails_gate (retry, max 2 cycles)
```

**New ProjectState fields** (add to `multi_agent.py:224-244`):

```python
class ProjectState(TypedDict):
    # ... existing fields ...

    # Phase 2.0: Guardrails
    guardrails_violations: Optional[List[Dict[str, Any]]]  # Serialized Violation objects
    guardrails_iteration: int                                # How many guardrails fix cycles (max 2)
```

**New node — `_guardrails_gate_node`** (add to `multi_agent.py`):

```python
@monitor_node("guardrails_gate")
def _guardrails_gate_node(self, state: ProjectState) -> dict:
    """
    Run architectural guardrails on merged code.
    If CRITICAL violations found, inject fix instructions into state for re-routing.
    """
    all_code = {}
    all_code.update(state.get("frontend_code") or {})
    all_code.update(state.get("backend_code") or {})

    if not all_code:
        return {"guardrails_violations": [], "guardrails_iteration": state.get("guardrails_iteration", 0)}

    guardrails = ArchitecturalGuardrails()
    violations = guardrails.validate_project(all_code)

    serialized = [
        {
            "rule_id": v.rule_id,
            "file_path": v.file_path,
            "line": v.line,
            "message": v.message,
            "fix_instruction": v.fix_instruction,
            "severity": v.severity.value,
        }
        for v in violations
    ]

    return {
        "guardrails_violations": serialized,
        "guardrails_iteration": state.get("guardrails_iteration", 0) + 1,
    }
```

**Routing function — `_route_after_guardrails`**:

```python
def _route_after_guardrails(self, state: ProjectState) -> str:
    """
    After guardrails analysis, decide: proceed to tester or route back for fixes.

    Decision logic:
      1. If no CRITICAL violations → proceed to "tester"
      2. If CRITICAL violations AND guardrails_iteration < 3 → route to "frontend" or "backend"
         (based on which layer the violations are in)
      3. If CRITICAL violations AND guardrails_iteration >= 3 → proceed anyway (log warning)
         (prevent infinite loops — 2 fix attempts is the max)
    """
    violations = state.get("guardrails_violations") or []
    critical = [v for v in violations if v["severity"] == "critical"]
    iteration = state.get("guardrails_iteration", 0)

    if not critical:
        return "tester"

    if iteration >= 3:
        lg_logger.warning(
            f"[GUARDRAILS] {len(critical)} CRITICAL violations remain after {iteration} fix cycles. "
            f"Proceeding to tester anyway. Violations: {[v['rule_id'] for v in critical]}"
        )
        return "tester"

    # Determine which agent should fix: check file paths
    frontend_violations = [v for v in critical if _is_frontend_file(v["file_path"])]
    backend_violations = [v for v in critical if not _is_frontend_file(v["file_path"])]

    # Route to whichever has more violations (or frontend by default)
    if backend_violations and not frontend_violations:
        return "backend"
    return "frontend"


def _is_frontend_file(file_path: str) -> bool:
    """Heuristic: frontend files are .tsx, .jsx, or in src/components, app/, pages/."""
    if file_path.endswith(('.tsx', '.jsx')):
        return True
    parts = PurePosixPath(file_path).parts
    return any(p in {"components", "pages", "app", "views", "hooks"} for p in parts)
```

**Agent prompt injection for fixes** (modify `_frontend_node` and `_backend_node`):

When the pipeline routes back to an agent for guardrails fixes, the agent receives a targeted prompt instead of the original generation prompt:

```python
@monitor_node("frontend")
def _frontend_node(self, state: ProjectState) -> dict:
    violations = state.get("guardrails_violations") or []
    frontend_violations = [v for v in violations if v["severity"] == "critical" and _is_frontend_file(v["file_path"])]

    if frontend_violations and state.get("guardrails_iteration", 0) > 1:
        # REWRITE MODE — targeted fix, not full regeneration
        fix_prompt = self._build_guardrails_fix_prompt(
            existing_code=state.get("frontend_code", {}),
            violations=frontend_violations,
        )
        # Use the same model but with fix-specific prompt
        result = self.frontend_agent.invoke(fix_prompt)
        # Merge fixes into existing code (don't replace everything)
        return {"frontend_code": self._apply_fixes(state.get("frontend_code", {}), result)}

    # Normal generation path (unchanged from Phase 1)
    # ... existing logic ...
```

```python
def _build_guardrails_fix_prompt(self, existing_code: Dict[str, str], violations: List[Dict]) -> str:
    """
    Build a targeted fix prompt. This is NOT the full generation prompt.
    Only the violated files are included, with specific fix instructions.
    """
    prompt_parts = [
        "You must fix the following architectural violations in existing code.",
        "Do NOT rewrite the entire project. Only modify the specific files and lines indicated.",
        "Return ONLY the modified files as a JSON dict {filename: content}.\n",
    ]

    for v in violations:
        file_path = v["file_path"]
        code = existing_code.get(file_path, "")
        # Include only the violated file (not the entire project)
        prompt_parts.append(f"--- VIOLATION {v['rule_id']} in {file_path}:{v['line']} ---")
        prompt_parts.append(f"Message: {v['message']}")
        prompt_parts.append(f"Fix: {v['fix_instruction']}")
        prompt_parts.append(f"Current code:\n```\n{code}\n```\n")

    return "\n".join(prompt_parts)
```

#### 2.1.5 Static Analysis Extension (`code_review/rules.py`)

Add a new `ArchitecturalRules` class to `code_review/rules.py` alongside the existing `SecurityRules`, `PerformanceRules`, etc. These rules run in the `StaticAnalyzer` (which the Reviewer agent already uses) as a second enforcement layer.

```python
class ArchitecturalRules:
    """
    Static analysis rules for architectural quality.
    Lighter-weight than the full AST guardrails — regex-based, runs per-file.
    Complement the ArchitecturalGuardrails class (which does cross-file analysis).
    """

    RULES = [
        Rule(id="ARCH001", title="Business Logic in UI Component", ...),
        Rule(id="ARCH002", title="API Call Outside Service Layer", ...),
        Rule(id="ARCH003", title="File Exceeds 300 Lines", ...),
        # ARCH004-008 require cross-file analysis — handled by ArchitecturalGuardrails, not here
        Rule(id="ARCH009", title="Unused Import", ...),
        Rule(id="ARCH010", title="Missing Error Boundary", ...),
    ]
```

Register in `StaticAnalyzer.RULE_SETS`:

```python
class StaticAnalyzer:
    RULE_SETS = [
        SecurityRules,
        PerformanceRules,
        StyleRules,
        BestPracticeRules,
        ArchitecturalRules,  # NEW — Phase 2.0
    ]
```

#### 2.1.6 Convex Integration

Guardrails results are stored in the `builds` table for frontend display:

```python
# After guardrails run, update build record:
await write_buffer.enqueue("builds:update", {
    "id": state["build_id"],
    "guardrailsResult": {
        "totalViolations": len(violations),
        "criticalCount": len([v for v in violations if v.severity == ReviewSeverity.CRITICAL]),
        "highCount": len([v for v in violations if v.severity == ReviewSeverity.HIGH]),
        "violations": serialized[:20],  # Cap at 20 for Convex doc size
        "fixCyclesUsed": state.get("guardrails_iteration", 0),
        "passed": len([v for v in violations if v.severity == ReviewSeverity.CRITICAL]) == 0,
    }
})
```

**Frontend component** (`frontend/components/build/GuardrailsReport.tsx`):

Subscribes to `useQuery(api.builds.getById, { id: buildId })` and renders the `guardrailsResult` field. Shows:
- Pass/fail badge
- Violation count by severity
- Expandable violation list with file, line, rule ID, fix instruction
- Fix cycle counter ("Guardrails: 2/2 fix cycles used")

---

### 2.2 IE-2: Expert-in-the-Loop — "Human SOS"

#### 2.2.1 Problem Statement

When the AI gets stuck in a loop or hits a complex edge case, users have no escape hatch. In Bolt/Lovable, you burn credits retrying. VBuilder adds a **human expert** option: a button that packages the entire build context and connects the user to a developer who can fix the problem directly.

**Trigger conditions**:
1. User clicks the SOS button manually (always available)
2. `stuck_count >= 3` in the Semantic Loop Detector (Section 2.3) — system suggests SOS automatically

#### 2.2.2 Data Flow: SOS Signal → Expert Dashboard

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER'S BROWSER                              │
│                                                                     │
│  BuildStatusBar                                                     │
│    └── SOSButton (red, pulsing when stuck_count >= 3)              │
│          │                                                          │
│          │ onClick → POST /api/v1/expert/request                   │
│          │   body: { buildId, projectId, problemDescription }      │
│          │                                                          │
└──────────┼──────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      BACKEND (expert_routes.py)                     │
│                                                                     │
│  POST /api/v1/expert/request                                       │
│    1. Validate auth (get_current_user)                             │
│    2. Load build record from Convex (builds:getById)               │
│    3. Load all agent statuses (agentStatus:listByBuild)            │
│    4. Load project files (projects:listFiles)                      │
│    5. Load context snapshot if available (contextSnapshots:get)     │
│    6. Load chat history (projects:listMessages, last 50)           │
│    7. Package into ExpertContext (see 2.2.3)                       │
│    8. Create Convex record: expertRequests:create                  │
│       { buildId, projectId, userId, status: "pending",            │
│         contextSnapshot: <serialized ExpertContext>,                │
│         problemDescription, createdAt }                            │
│    9. Return { requestId, estimatedWaitMinutes }                   │
│                                                                     │
└──────────┬──────────────────────────────────────────────────────────┘
           │
           │ Convex reactivity: useQuery(api.expertRequests.listPending)
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    EXPERT DASHBOARD (browser)                       │
│                                                                     │
│  /expert-dashboard page                                            │
│    ├── PendingRequests list (real-time via Convex)                 │
│    │     └── Each row: user, project, problem, time waiting        │
│    │                                                                │
│    ├── Expert clicks "Claim" →                                     │
│    │     POST /api/v1/expert/requests/{id}/claim                   │
│    │     → Convex mutation: expertRequests:claim { expertId }      │
│    │     → Status: "pending" → "claimed"                           │
│    │     → User sees: "Expert connected! ETA: 2 minutes"          │
│    │                                                                │
│    ├── ExpertContextViewer                                         │
│    │     ├── File tree (all project files, read-only)              │
│    │     ├── Error log (agent errors + stack traces)               │
│    │     ├── Agent history (which agents ran, what they produced)  │
│    │     ├── Architecture spec (from Architect agent output)       │
│    │     └── Chat history (user's conversation with VBuilder)      │
│    │                                                                │
│    └── Expert resolves → POST /api/v1/expert/requests/{id}/resolve│
│          body: { resolution, patchedFiles: Dict<string, string> }  │
│          → Status: "claimed" → "resolved"                          │
│          → Backend applies patched files to project                │
│          → Convex mutation: projects:updateFile (for each file)    │
│          → Store fix pattern in MemoryManager.update_procedure()   │
│          → User sees: "Expert fixed your issue!" + diff view       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

#### 2.2.3 ExpertContext — The Context Package

The key to useful expert intervention is **context completeness**. The expert must see everything the AI saw, plus everything the AI tried and failed.

```python
# backend/services/expert_pool.py

@dataclass
class ExpertContext:
    """
    Complete build context packaged for an expert developer.
    Serialized to JSON and stored in Convex expertRequests table.
    """

    # Project identity
    project_id: str
    project_name: str
    build_id: str

    # What the user asked for
    requirements: str               # Original user requirements
    problem_description: str        # User's description of the problem (from SOS form)

    # Architecture
    architecture: Dict[str, Any]    # Architect agent output (tech stack, components, data models)

    # Current code state (all generated files)
    files: Dict[str, str]           # {file_path: content} — complete project snapshot

    # Error information
    errors: List[Dict[str, Any]]    # All errors from the build, each with:
    #   { "agent": str, "error": str, "stack_trace": str | None, "timestamp": float }

    # Agent execution history
    agent_history: List[Dict[str, Any]]  # Per-agent execution record:
    #   { "agent": str, "model": str, "status": str, "duration_ms": int,
    #     "cost": float, "prompt_summary": str, "output_summary": str,
    #     "files_created": List[str], "error": str | None }

    # What the AI tried (for the expert to avoid repeating)
    failed_fix_attempts: List[Dict[str, Any]]  # Each failed fix cycle:
    #   { "iteration": int, "agent": str, "attempted_fix": str,
    #     "result": str, "why_failed": str }

    # Review results (if reviewer ran)
    review_results: Optional[Dict[str, Any]]

    # Guardrails violations (if guardrails ran)
    guardrails_violations: Optional[List[Dict[str, Any]]]

    # Chat history (user ↔ VBuilder conversation)
    chat_messages: List[Dict[str, Any]]  # Last 50 messages:
    #   { "role": "user" | "assistant", "content": str, "timestamp": float }

    # Context snapshot (serialized LangGraph state, if available)
    langgraph_state: Optional[str]  # JSON-serialized ProjectState

    # Metadata
    total_cost_so_far: float        # USD spent on this build before SOS
    total_duration_ms: int          # Wall clock time since build started
    stuck_count: int                # How many loop iterations before SOS
    created_at: float               # Timestamp of SOS request

    def to_dict(self) -> dict:
        """Serialize for Convex storage. Cap total size at 900KB (Convex 1MB doc limit)."""
        d = asdict(self)
        serialized = json.dumps(d)
        if len(serialized) > 900_000:
            # Trim: drop file contents for files > 5KB, keep paths
            d["files"] = {
                path: content if len(content) < 5000 else f"[TRUNCATED — {len(content)} chars]"
                for path, content in d["files"].items()
            }
            # Trim chat to last 20 messages
            d["chat_messages"] = d["chat_messages"][-20:]
        return d
```

#### 2.2.4 Expert Request API (`backend/api/expert_routes.py`)

```python
# backend/api/expert_routes.py

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from middleware.auth import AuthenticatedUser, verify_auth
from db.convex import get_convex_db, ConvexWriteBuffer

router = APIRouter(prefix="/api/v1/expert", tags=["expert"])


class ExpertRequestCreate(BaseModel):
    build_id: str
    project_id: str
    problem_description: str  # User's description of what went wrong


class ExpertRequestClaim(BaseModel):
    expert_id: str  # Clerk user ID of the expert


class ExpertRequestResolve(BaseModel):
    resolution: str                        # What the expert did to fix it
    patched_files: Dict[str, str]          # {file_path: new_content} — only changed files


@router.post("/request")
async def create_expert_request(
    body: ExpertRequestCreate,
    user: AuthenticatedUser = Depends(verify_auth),
):
    """
    Create an SOS expert request. Packages full build context.

    Flow:
      1. Load build, agents, files, chat, snapshot from Convex
      2. Assemble ExpertContext
      3. Store in Convex expertRequests table
      4. Return request ID + estimated wait
    """
    db = get_convex_db()

    # Parallel fetch all context
    build = await db.query("builds:getById", {"id": body.build_id})
    agents = await db.query("agentStatus:listByBuild", {"buildId": body.build_id})
    files = await db.query("projects:listFiles", {"projectId": body.project_id})
    messages = await db.query("projects:listMessages", {"projectId": body.project_id})
    snapshot = await db.query("contextSnapshots:getLatestByBuild", {"buildId": body.build_id})

    if not build:
        raise HTTPException(404, "Build not found")

    # Assemble context
    context = ExpertContext(
        project_id=body.project_id,
        project_name=build.get("name", "Untitled"),
        build_id=body.build_id,
        requirements=build.get("requirements", ""),
        problem_description=body.problem_description,
        architecture=build.get("architecture") or {},
        files={f["path"]: f["content"] for f in (files or [])},
        errors=[a for a in (agents or []) if a.get("errorMessage")],
        agent_history=[
            {
                "agent": a["agentName"],
                "model": a.get("model"),
                "status": a["status"],
                "duration_ms": a.get("duration"),
                "cost": a.get("cost"),
                "files_created": a.get("filesCreated", []),
                "error": a.get("errorMessage"),
            }
            for a in (agents or [])
        ],
        failed_fix_attempts=[],  # Populated from ProjectState if snapshot available
        review_results=build.get("reviewResults"),
        guardrails_violations=build.get("guardrailsResult", {}).get("violations"),
        chat_messages=[{"role": m["role"], "content": m["content"], "timestamp": m["createdAt"]} for m in (messages or [])[-50:]],
        langgraph_state=snapshot.get("serializedState") if snapshot else None,
        total_cost_so_far=build.get("totalCost", 0),
        total_duration_ms=build.get("totalDuration", 0),
        stuck_count=build.get("stuckCount", 0),
        created_at=time.time(),
    )

    # Store in Convex
    request_id = await db.mutation("expertRequests:create", {
        "buildId": body.build_id,
        "projectId": body.project_id,
        "userId": user.convex_user_id,
        "status": "pending",
        "problemDescription": body.problem_description,
        "contextSnapshot": json.dumps(context.to_dict()),
        "createdAt": time.time(),
    })

    return {"requestId": request_id, "status": "pending", "estimatedWaitMinutes": 5}


@router.post("/requests/{request_id}/claim")
async def claim_expert_request(
    request_id: str,
    body: ExpertRequestClaim,
    user: AuthenticatedUser = Depends(verify_auth),
):
    """Expert claims a pending request."""
    db = get_convex_db()
    await db.mutation("expertRequests:claim", {
        "id": request_id,
        "expertId": body.expert_id,
        "claimedAt": time.time(),
    })
    return {"status": "claimed"}


@router.post("/requests/{request_id}/resolve")
async def resolve_expert_request(
    request_id: str,
    body: ExpertRequestResolve,
    user: AuthenticatedUser = Depends(verify_auth),
):
    """
    Expert resolves the request. Applies patched files to the project.
    Stores the fix pattern in procedural memory for future builds.
    """
    db = get_convex_db()
    request = await db.query("expertRequests:getById", {"id": request_id})
    if not request:
        raise HTTPException(404, "Request not found")

    # Apply patched files to project
    for file_path, content in body.patched_files.items():
        await db.mutation("projects:updateFile", {
            "projectId": request["projectId"],
            "path": file_path,
            "content": content,
        })

    # Store fix pattern in procedural memory
    from memory import MemoryManager
    memory = MemoryManager()
    memory.update_procedure(
        instruction=f"When encountering: {request['problemDescription']}\nFix: {body.resolution}",
        agent_id="expert_fixes",
        priority=10,  # High priority — human-verified fix
    )

    # Update request status
    await db.mutation("expertRequests:resolve", {
        "id": request_id,
        "resolution": body.resolution,
        "patchedFileCount": len(body.patched_files),
        "resolvedAt": time.time(),
    })

    return {"status": "resolved", "filesPatched": len(body.patched_files)}
```

#### 2.2.5 Convex Schema Extension

Add to `frontend/convex/schema.ts` and `backend/convex/schema.ts`:

```typescript
expertRequests: defineTable({
    buildId: v.id("builds"),
    projectId: v.id("projects"),
    userId: v.id("users"),                     // User who requested help
    expertId: v.optional(v.string()),          // Clerk ID of assigned expert
    status: v.union(
        v.literal("pending"),
        v.literal("claimed"),
        v.literal("resolved"),
        v.literal("cancelled"),
        v.literal("expired")                   // Auto-expire after 30 min unclaimed
    ),
    problemDescription: v.string(),
    contextSnapshot: v.string(),               // JSON-serialized ExpertContext
    resolution: v.optional(v.string()),
    patchedFileCount: v.optional(v.number()),
    createdAt: v.number(),
    claimedAt: v.optional(v.number()),
    resolvedAt: v.optional(v.number()),
})
    .index("by_status", ["status"])
    .index("by_build", ["buildId"])
    .index("by_user", ["userId"]),
```

#### 2.2.6 Phase 2.0 Scope Limitation

The full Expert-in-the-Loop system (live shared coding session via Yjs, cursor sharing, real-time chat) is a **Phase 3.4 feature** (Real-time Collaboration). Phase 2.0 implements:

- **SOS button** — UI component + API endpoint
- **Context packaging** — Full build state serialized for expert
- **Expert dashboard** — Read-only context viewer + file patch submission
- **Procedural memory** — Expert fixes stored for future AI learning

What Phase 2.0 does NOT include:
- Live shared editing (requires Yjs infrastructure from Phase 3.4)
- Expert pool management / matching / SLA tracking
- Per-minute billing for expert time
- Real-time chat between user and expert

---

### 2.3 Semantic Loop Detection — "Break the AI Loop"

#### 2.3.1 Problem Statement

The current `_route_after_review()` uses a simple iteration counter (max 3 cycles) and only checks for issues with `severity == "critical"`. This has two problems:

1. **No semantic awareness**: If the AI produces the same error message worded differently across iterations, the counter treats them as separate issues. The AI may cycle through 3 iterations making zero progress.
2. **No model switching**: When one model is stuck, using the same model again rarely helps. Switching to a different provider gives a "fresh perspective."

**Current code** (`multi_agent.py:833-846`):
```python
def _route_after_review(self, state: ProjectState) -> str:
    review = state.get("review_results", {})
    iteration = state.get("iteration", 0)
    issues = review.get("issues", [])
    critical = [i for i in issues if i.get("severity") == "critical"]
    if critical and iteration < 3:
        return "frontend"
    return "finalize"
```

#### 2.3.2 Solution: Cosine Similarity + 3-Stage Escalation

Replace the simple iteration counter with a semantic loop detector that embeds error messages and compares them across iterations using cosine similarity.

**New ProjectState fields** (add to `multi_agent.py:224-244`):

```python
class ProjectState(TypedDict):
    # ... existing fields ...

    # Phase 2.0: Loop Detection
    stuck_count: int                                       # Semantic loop counter (distinct from iteration)
    previous_issues_embeddings: Optional[List[List[float]]]  # Embeddings of previous iteration's issues
    previous_issues_text: Optional[List[str]]              # Raw text of previous issues (for debugging)
    model_switch_history: Optional[List[Dict[str, str]]]   # [{from: "claude-sonnet", to: "gpt-5.2-pro", reason: "..."}]
```

**Semantic Loop Detector** (new method in `MultiAgentBuilder`):

```python
# backend/ai/agents/multi_agent.py — new method

def _detect_semantic_loop(self, state: ProjectState) -> Tuple[bool, float]:
    """
    Compare current iteration's issues with previous iteration's issues
    using cosine similarity of embeddings.

    Returns:
        (is_stuck: bool, max_similarity: float)

    Algorithm:
        1. Extract current issues as text strings
        2. Embed each issue using EmbeddingProvider
        3. Compare each current embedding against each previous embedding
        4. If ANY pair has cosine_similarity > 0.85 → semantically same issue
        5. If >50% of current issues match previous issues → stuck loop
    """
    from memory import EmbeddingProvider, SemanticStore

    review = state.get("review_results", {})
    current_issues = [
        f"{i.get('title', '')}: {i.get('description', '')}"
        for i in review.get("issues", [])
        if i.get("severity") in ("critical", "high")
    ]

    if not current_issues:
        return (False, 0.0)

    previous_embeddings = state.get("previous_issues_embeddings")
    if not previous_embeddings:
        # First iteration — nothing to compare against. Just store embeddings.
        return (False, 0.0)

    # Embed current issues
    embedder = EmbeddingProvider(provider="auto")
    current_embeddings = [embedder.embed(text) for text in current_issues]

    # Compare: for each current issue, find best match in previous issues
    match_count = 0
    max_similarity = 0.0

    for curr_emb in current_embeddings:
        best_sim = max(
            SemanticStore._cosine_similarity(curr_emb, prev_emb)
            for prev_emb in previous_embeddings
        )
        max_similarity = max(max_similarity, best_sim)
        if best_sim > 0.85:
            match_count += 1

    # Stuck if >50% of current issues are semantically identical to previous
    is_stuck = (match_count / len(current_issues)) > 0.5

    return (is_stuck, max_similarity)
```

**Store embeddings after each review cycle** (modify `_reviewer_node`):

```python
@monitor_node("reviewer")
def _reviewer_node(self, state: ProjectState) -> dict:
    # ... existing review logic (lines 517-558) ...

    # After review, embed current issues for next-iteration comparison
    from memory import EmbeddingProvider

    issues = result.get("issues", [])
    issue_texts = [
        f"{i.get('title', '')}: {i.get('description', '')}"
        for i in issues
        if i.get("severity") in ("critical", "high")
    ]

    embedder = EmbeddingProvider(provider="auto")
    embeddings = [embedder.embed(text) for text in issue_texts] if issue_texts else []

    return {
        # ... existing return fields ...
        "previous_issues_embeddings": embeddings,
        "previous_issues_text": issue_texts,
    }
```

#### 2.3.3 Three-Stage Escalation (replace `_route_after_review`)

```python
def _route_after_review(self, state: ProjectState) -> str:
    """
    3-stage escalation for stuck builds:

    Stage 1 (stuck_count == 1): Switch model, send ONLY buggy code + error + requirements.
            Drop all prior failed fix attempts from context.
    Stage 2 (stuck_count == 2): Switch to thinking model (gpt-5.2-pro-thinking / claude-opus),
            include failed approaches as "what NOT to do".
    Stage 3 (stuck_count >= 3): HITL escalation. Present user with options:
            [a] Retry with user guidance, [b] Skip this issue, [c] SOS expert, [d] Abort build.
    """
    review = state.get("review_results", {})
    iteration = state.get("iteration", 0)
    issues = review.get("issues", [])
    critical = [i for i in issues if i.get("severity") == "critical"]

    if not critical:
        return "finalize"

    # Run semantic loop detection
    is_stuck, similarity = self._detect_semantic_loop(state)

    if not is_stuck and iteration < 3:
        # Not stuck yet — normal retry
        return "frontend"

    # We're stuck. Increment stuck_count.
    stuck_count = state.get("stuck_count", 0) + 1

    if stuck_count == 1:
        # STAGE 1: Switch model for fresh perspective
        current_model = self._get_current_model("frontend")
        alternate = self._get_alternate_model(current_model)

        lg_logger.info(
            f"[LOOP] Semantic loop detected (similarity={similarity:.2f}). "
            f"Stage 1: Switching {current_model} → {alternate}"
        )

        # Store switch in state for audit trail
        switch_history = state.get("model_switch_history") or []
        switch_history.append({
            "from": current_model,
            "to": alternate,
            "reason": f"Semantic loop (similarity={similarity:.2f})",
            "stage": 1,
        })

        # Update state with new model and cleared context
        state["model_switch_history"] = switch_history
        state["stuck_count"] = stuck_count
        state["_override_model"] = alternate  # Checked by _frontend_node / _backend_node
        state["_clear_failed_context"] = True  # Signal to drop failed fix attempts

        return "frontend"

    elif stuck_count == 2:
        # STAGE 2: Thinking model with anti-pattern context
        thinking_model = "gpt-5.2-pro"  # High-reasoning model

        lg_logger.info(
            f"[LOOP] Stage 2: Escalating to thinking model ({thinking_model}). "
            f"Including {len(state.get('previous_issues_text', []))} anti-patterns."
        )

        state["stuck_count"] = stuck_count
        state["_override_model"] = thinking_model
        state["_include_anti_patterns"] = True  # Signal to include "what NOT to do"

        return "frontend"

    else:
        # STAGE 3: HITL — offer SOS
        lg_logger.warning(
            f"[LOOP] Stage 3: stuck_count={stuck_count}. Requesting HITL intervention."
        )

        state["stuck_count"] = stuck_count

        # Update build record so frontend can show SOS option
        # The ConvexWriteBuffer will flush this to the builds table
        # Frontend checks: if build.stuckCount >= 3, show SOS button prominently

        return "finalize"  # Don't loop infinitely — finalize with whatever we have
```

#### 2.3.4 Model Switcher (`backend/src/smart_routing.py` — new function)

```python
# Add to backend/src/smart_routing.py (or create if only efficiency.py exists)

from config import load_router_config  # router.yaml

# Pre-computed alternate model map. Key rule: switch PROVIDER, not just model.
# If Claude is stuck, switch to GPT (different training data = different perspective).
# If GPT is stuck, switch to Claude.
_ALTERNATE_MODELS: Dict[str, str] = {
    # Current model → Alternate model (different provider)
    "claude-sonnet-4-6": "gpt-5.2-pro",
    "claude-opus-4-6": "gpt-5.2-pro",
    "gpt-5.2-pro": "claude-sonnet-4-6",
    "gpt-5.3-codex": "claude-sonnet-4-6",
    "gemini-3-flash-preview": "claude-sonnet-4-6",
    "gemini-3.1-pro": "claude-opus-4-6",
}

def get_alternate_model(current_model_id: str) -> str:
    """
    Return a model from a DIFFERENT provider for loop-breaking.

    The insight: when Claude is stuck on a problem, GPT often has a different
    internal representation that avoids the same dead-end. And vice versa.
    Switching within the same provider (e.g., Sonnet → Opus) is less effective.
    """
    return _ALTERNATE_MODELS.get(current_model_id, "gpt-5.2-pro")
```

#### 2.3.5 SSE Events for Loop Detection

```python
# Emitted when loop is detected:
{"event": "loop_detected", "data": {
    "stuck_count": 2,
    "similarity": 0.91,
    "stage": 2,
    "action": "model_switch",
    "from_model": "claude-sonnet-4-6",
    "to_model": "gpt-5.2-pro",
    "message": "AI loop detected. Switching to GPT for a fresh perspective."
}}

# Emitted when HITL triggered:
{"event": "hitl_required", "data": {
    "stuck_count": 3,
    "build_id": "...",
    "options": ["retry_with_guidance", "skip_issue", "sos_expert", "abort"],
    "message": "AI has been unable to fix this issue after 3 attempts. Would you like to connect with an expert?"
}}
```

---

### 2.4 Context Budget Manager — "Never Lose Context"

#### 2.4.1 Problem Statement

The current pipeline has no token counting or context management (`multi_agent.py` has zero token budget logic). Each agent receives the full accumulated state — requirements, architecture, all generated code, test results, review findings. For a 20+ component project, this easily exceeds 60K tokens, causing:

1. **Context overflow**: LLM silently drops early context, causing duplicated components
2. **Cost explosion**: Sending 60K tokens per agent call across 8 agents = 480K input tokens per build
3. **Degraded quality**: LLMs perform worse when context is cluttered with irrelevant information

**Current context reduction** (the only existing strategy):
- `TesterAgent._format_code_samples()`: limits to 5 files, 50 lines each
- `ReviewerAgent._format_code_samples()`: limits to 100 lines per file

These are hardcoded heuristics, not intelligent context management.

#### 2.4.2 Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    ContextBudgetManager                       │
│                                                              │
│  Inputs:                                                     │
│    ├── current_agent: str        ("frontend", "backend", etc)│
│    ├── project_state: ProjectState (full state)              │
│    ├── max_tokens: int           (model's context limit)     │
│    └── project_rag_index: ProjectRAGIndex (indexed files)    │
│                                                              │
│  Algorithm:                                                  │
│    1. Estimate token count of full state                     │
│    2. If under budget (< 60% of max_tokens): return as-is   │
│    3. If over budget: apply priority tiers                   │
│                                                              │
│  Priority Tiers:                                             │
│    P1 (ALWAYS KEEP — never trimmed):                        │
│      ├── requirements (original user prompt)                 │
│      ├── architecture spec                                   │
│      ├── current file being worked on                        │
│      ├── error messages from current iteration               │
│      └── guardrails violations (if any)                      │
│                                                              │
│    P2 (SUMMARIZE — replace with RAG summaries):             │
│      ├── other generated files (→ "15 files generated,      │
│      │    including App.tsx, Dashboard.tsx, api/users.ts.    │
│      │    Key patterns: React + Tailwind, REST API,          │
│      │    Prisma ORM.")                                      │
│      ├── test results (→ "12/15 tests passing. Failures:    │
│      │    UserProfile.test.tsx (assertion error on line 42)")│
│      └── review results (→ "3 critical issues: SEC001 in    │
│           api/auth.ts:23, ARCH001 in Dashboard.tsx:45,      │
│           PERF001 in users.ts:88")                           │
│                                                              │
│    P3 (DROP — removed entirely):                            │
│      ├── conversation messages beyond last 5 turns           │
│      ├── decision log entries older than current phase       │
│      └── context snapshot metadata                           │
│                                                              │
│  Output:                                                     │
│    ├── trimmed_state: Dict  (state within token budget)      │
│    ├── token_count: int     (estimated tokens used)          │
│    └── trimmed_report: str  (what was dropped/summarized)    │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

#### 2.4.3 Token Estimation

We use the `len(text) // 4` heuristic (already present in `efficiency.py:444-446`) as the primary estimator. This is consistently within 10% of tiktoken for English text and requires zero dependencies.

```python
# backend/ai/agents/context_manager.py

def estimate_tokens(text: str) -> int:
    """
    Estimate token count using the chars/4 heuristic.

    Accuracy: ~90% vs tiktoken for English text.
    Advantage: zero dependencies, instant, works for all models.

    For more accuracy, we could use tiktoken, but it adds 50ms per call
    and requires model-specific tokenizers. Not worth it for budget estimation.
    """
    return len(text) // 4
```

#### 2.4.4 ProjectRAGIndex — Per-Project File Index

When files are trimmed from context (P2 tier), the agent may need information from those files to make correct decisions. The `ProjectRAGIndex` provides on-demand retrieval.

```python
# backend/ai/agents/context_manager.py

class ProjectRAGIndex:
    """
    Per-build FAISS index of all generated project files.

    Created once after Aggregator merges code.
    Queried by ContextBudgetManager when P2 files are summarized.

    NOT persistent — lives only for the duration of one build.
    """

    def __init__(self):
        self._store: Optional[VectorStore] = None
        self._file_metadata: Dict[str, Dict] = {}  # path -> {imports, exports, component_name, line_count}

    def index_project(self, files: Dict[str, str]) -> None:
        """
        Index all project files into a FAISS vector store.

        Chunking strategy:
          - Chunk size: 500 tokens (~2000 chars)
          - Overlap: 100 tokens (~400 chars)
          - Metadata per chunk: {file_path, chunk_index, imports, exports}
        """
        from ai.rag import VectorStore, DocumentLoader, EmbeddingProvider

        loader = DocumentLoader(chunk_size=2000, chunk_overlap=400)
        documents = []

        for file_path, content in files.items():
            # Extract metadata
            self._file_metadata[file_path] = self._extract_metadata(file_path, content)

            # Chunk the file
            chunks = loader.load_texts([content])
            for i, chunk in enumerate(chunks):
                chunk.metadata = {
                    "file_path": file_path,
                    "chunk_index": i,
                    **self._file_metadata[file_path],
                }
                documents.append(chunk)

        # Build FAISS index
        embedder = EmbeddingProvider(provider="auto")
        self._store = VectorStore(backend="faiss", embeddings=embedder)
        self._store.add_documents(documents)

    def query(self, question: str, k: int = 5) -> List[Dict]:
        """
        Retrieve relevant code chunks for a question.

        Returns list of {file_path, content, score, metadata}.
        """
        if not self._store:
            return []

        results = self._store.similarity_search(question, k=k)
        return [
            {
                "file_path": doc.metadata["file_path"],
                "content": doc.page_content,
                "metadata": doc.metadata,
            }
            for doc in results
        ]

    def get_project_summary(self) -> str:
        """
        Generate a compact summary of the entire project for P2 context.

        Format:
            "15 files generated. Key files:
             - src/App.tsx (142 lines, imports: React, Router, Dashboard)
             - src/components/Dashboard.tsx (89 lines, imports: React, useUsers, Chart)
             - src/lib/api/users.ts (45 lines, exports: getUsers, createUser, deleteUser)
             ..."
        """
        lines = [f"{len(self._file_metadata)} files generated. Key files:"]
        # Sort by line count descending, show top 10
        sorted_files = sorted(
            self._file_metadata.items(),
            key=lambda x: x[1].get("line_count", 0),
            reverse=True,
        )[:10]

        for path, meta in sorted_files:
            imports = ", ".join(meta.get("imports", [])[:5])
            exports = ", ".join(meta.get("exports", [])[:5])
            line_count = meta.get("line_count", 0)
            parts = [f"- {path} ({line_count} lines"]
            if imports:
                parts.append(f", imports: {imports}")
            if exports:
                parts.append(f", exports: {exports}")
            parts.append(")")
            lines.append("".join(parts))

        return "\n".join(lines)

    def _extract_metadata(self, file_path: str, content: str) -> Dict:
        """Extract imports, exports, component name, line count from a file."""
        metadata = {"line_count": content.count("\n") + 1, "imports": [], "exports": []}

        # TypeScript/JavaScript
        if file_path.endswith(('.ts', '.tsx', '.js', '.jsx')):
            # Imports: import X from 'Y' or import { X } from 'Y'
            for match in re.finditer(r'import\s+(?:\{([^}]+)\}|(\w+))\s+from', content):
                names = match.group(1) or match.group(2)
                metadata["imports"].extend(n.strip() for n in names.split(","))

            # Exports: export function X, export const X, export default
            for match in re.finditer(r'export\s+(?:default\s+)?(?:function|const|class|interface|type)\s+(\w+)', content):
                metadata["exports"].append(match.group(1))

        # Python
        elif file_path.endswith('.py'):
            for match in re.finditer(r'from\s+\S+\s+import\s+(.+)', content):
                metadata["imports"].extend(n.strip() for n in match.group(1).split(","))
            for match in re.finditer(r'^(?:def|class)\s+(\w+)', content, re.MULTILINE):
                metadata["exports"].append(match.group(1))

        return metadata
```

#### 2.4.5 ContextBudgetManager

```python
# backend/ai/agents/context_manager.py

# Token budgets per model (from router.yaml max_tokens + known context windows)
MODEL_CONTEXT_LIMITS: Dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "gpt-5.2-pro": 128_000,
    "gpt-5.3-codex": 128_000,
    "gemini-3-flash-preview": 1_048_576,
    "gemini-3.1-pro": 1_048_576,
}

# Budget allocation: use 60% of context window for input, reserve 40% for output
BUDGET_RATIO = 0.6


@dataclass
class BudgetResult:
    """Result of context budget trimming."""
    agent_prompt: str          # The final prompt to send to the LLM
    token_count: int           # Estimated tokens in the prompt
    budget_limit: int          # Max tokens allowed
    trimmed: bool              # Whether any trimming occurred
    trimmed_report: str        # Human-readable report of what was trimmed
    rag_queries_made: int      # How many RAG retrievals were done to build summaries


class ContextBudgetManager:
    """
    Manages token budget for each agent call in the LangGraph pipeline.

    Called BEFORE every LLM invocation. Decides what to keep, summarize, or drop.

    Usage in pipeline nodes:
        budget_mgr = ContextBudgetManager(rag_index=self._project_rag_index)
        result = budget_mgr.build_prompt(
            agent_name="frontend",
            model_id="claude-sonnet-4-6",
            state=state,
            base_prompt=prompt,      # The agent's normal prompt template
        )
        # result.agent_prompt is the trimmed prompt, ready to send to LLM
    """

    def __init__(self, rag_index: Optional[ProjectRAGIndex] = None):
        self._rag_index = rag_index
        self._rag_queries = 0

    def build_prompt(
        self,
        agent_name: str,
        model_id: str,
        state: ProjectState,
        base_prompt: str,
    ) -> BudgetResult:
        """
        Build a token-budget-aware prompt for the given agent.

        Steps:
            1. Calculate budget from model context limit
            2. Estimate tokens for P1 content (always kept)
            3. If P1 fits in budget: add P2 content (full or summarized)
            4. If still over: drop P3 content
            5. If still over: progressively trim P2 summaries
        """
        context_limit = MODEL_CONTEXT_LIMITS.get(model_id, 128_000)
        budget = int(context_limit * BUDGET_RATIO)

        # === P1: Always keep ===
        p1_sections = self._build_p1(agent_name, state, base_prompt)
        p1_tokens = sum(estimate_tokens(s) for s in p1_sections.values())

        if p1_tokens > budget:
            # P1 alone exceeds budget — this is unusual but possible for huge requirements
            # Truncate the largest P1 section (usually "current_file")
            return self._emergency_truncate(p1_sections, budget)

        remaining = budget - p1_tokens

        # === P2: Summarize if needed ===
        p2_sections = self._build_p2(agent_name, state)
        p2_tokens = sum(estimate_tokens(s) for s in p2_sections.values())

        if p2_tokens <= remaining:
            # Everything fits — no trimming needed
            prompt = self._assemble_prompt(p1_sections, p2_sections, {})
            return BudgetResult(
                agent_prompt=prompt,
                token_count=p1_tokens + p2_tokens,
                budget_limit=budget,
                trimmed=False,
                trimmed_report="No trimming needed.",
                rag_queries_made=0,
            )

        # P2 doesn't fit — summarize
        p2_summaries = self._summarize_p2(agent_name, state, remaining)
        p2_summary_tokens = sum(estimate_tokens(s) for s in p2_summaries.values())

        prompt = self._assemble_prompt(p1_sections, p2_summaries, {})
        return BudgetResult(
            agent_prompt=prompt,
            token_count=p1_tokens + p2_summary_tokens,
            budget_limit=budget,
            trimmed=True,
            trimmed_report=self._generate_trim_report(p2_sections, p2_summaries),
            rag_queries_made=self._rag_queries,
        )

    def _build_p1(self, agent_name: str, state: ProjectState, base_prompt: str) -> Dict[str, str]:
        """
        P1 content — ALWAYS kept in context, never trimmed.
        """
        sections = {}

        # Base prompt (agent's role + instructions)
        sections["base_prompt"] = base_prompt

        # Requirements
        sections["requirements"] = f"## Requirements\n{state.get('requirements', '')}"

        # Architecture spec
        arch = state.get("architecture")
        if arch:
            sections["architecture"] = f"## Architecture\n{json.dumps(arch, indent=2)}"

        # Errors from current iteration (critical for fixing)
        errors = state.get("errors") or []
        if errors:
            sections["errors"] = f"## Current Errors\n" + "\n".join(f"- {e}" for e in errors[-10:])

        # Guardrails violations (if routing back for fix)
        violations = state.get("guardrails_violations") or []
        if violations:
            sections["guardrails"] = f"## Architectural Violations to Fix\n" + "\n".join(
                f"- [{v['rule_id']}] {v['file_path']}:{v['line']} — {v['fix_instruction']}"
                for v in violations
                if v["severity"] == "critical"
            )

        # Current file being worked on (if re-fixing a specific file)
        current_files = self._get_relevant_files(agent_name, state)
        if current_files:
            for path, content in list(current_files.items())[:3]:  # Max 3 files at full fidelity
                sections[f"file:{path}"] = f"## Current File: {path}\n```\n{content}\n```"

        return sections

    def _build_p2(self, agent_name: str, state: ProjectState) -> Dict[str, str]:
        """
        P2 content — included at full fidelity if budget allows, summarized if not.
        """
        sections = {}

        # All other generated files (not in P1)
        all_code = {}
        all_code.update(state.get("frontend_code") or {})
        all_code.update(state.get("backend_code") or {})
        relevant_files = self._get_relevant_files(agent_name, state)
        other_files = {k: v for k, v in all_code.items() if k not in relevant_files}

        if other_files:
            sections["other_files"] = "\n\n".join(
                f"--- {path} ---\n{content}" for path, content in other_files.items()
            )

        # Test results
        tests = state.get("tests")
        if tests:
            sections["tests"] = "\n\n".join(
                f"--- {path} ---\n{content}" for path, content in tests.items()
            )

        # Review results
        review = state.get("review_results")
        if review:
            sections["review"] = json.dumps(review, indent=2)

        return sections

    def _summarize_p2(self, agent_name: str, state: ProjectState, token_budget: int) -> Dict[str, str]:
        """
        Generate compact summaries of P2 content using the ProjectRAGIndex.

        Instead of including 50 full files, include:
          "15 files generated. Key patterns: React + Tailwind, REST API, Prisma ORM.
           Most relevant files to current task: [RAG query results]"
        """
        summaries = {}

        # Project file summary
        if self._rag_index:
            summaries["project_summary"] = (
                f"## Project Files (summarized — full files available via RAG)\n"
                f"{self._rag_index.get_project_summary()}"
            )

            # RAG: retrieve files most relevant to the current agent's task
            agent_query = self._get_agent_rag_query(agent_name, state)
            if agent_query:
                relevant = self._rag_index.query(agent_query, k=5)
                self._rag_queries += 1
                if relevant:
                    summaries["rag_context"] = (
                        f"## Most Relevant Code (retrieved via RAG)\n" +
                        "\n\n".join(
                            f"--- {r['file_path']} ---\n{r['content']}"
                            for r in relevant
                        )
                    )
        else:
            # No RAG index — use basic summary
            all_code = {}
            all_code.update(state.get("frontend_code") or {})
            all_code.update(state.get("backend_code") or {})
            file_list = "\n".join(f"- {path} ({len(content)} chars)" for path, content in all_code.items())
            summaries["project_summary"] = f"## Project Files (summarized)\n{file_list}"

        # Test result summary
        tests = state.get("tests")
        if tests:
            summaries["test_summary"] = f"## Test Results (summarized)\n{len(tests)} test files generated."

        # Review result summary (compact)
        review = state.get("review_results")
        if review:
            issues = review.get("issues", [])
            by_severity = {}
            for i in issues:
                sev = i.get("severity", "unknown")
                by_severity[sev] = by_severity.get(sev, 0) + 1

            summaries["review_summary"] = (
                f"## Review Results (summarized)\n"
                f"Score: {review.get('score', 'N/A')}/100\n"
                f"Issues: {', '.join(f'{count} {sev}' for sev, count in by_severity.items())}\n"
                f"Critical issues:\n" +
                "\n".join(
                    f"- {i['title']}: {i.get('description', '')[:100]}"
                    for i in issues
                    if i.get("severity") == "critical"
                )
            )

        return summaries

    def _get_agent_rag_query(self, agent_name: str, state: ProjectState) -> str:
        """Build a RAG query relevant to what the current agent is working on."""
        queries = {
            "frontend": "React components, hooks, state management, UI layout",
            "backend": "API routes, database models, middleware, server configuration",
            "tester": "test files, component tests, API tests, test utilities",
            "reviewer": "security patterns, error handling, type safety, code quality",
        }
        base = queries.get(agent_name, "project architecture and key components")

        # Add error context if available
        errors = state.get("errors") or []
        if errors:
            base += f". Related errors: {errors[-1][:200]}"

        return base

    def _get_relevant_files(self, agent_name: str, state: ProjectState) -> Dict[str, str]:
        """Get files most relevant to the current agent (P1 candidates)."""
        all_code = {}

        if agent_name == "frontend":
            all_code = state.get("frontend_code") or {}
        elif agent_name == "backend":
            all_code = state.get("backend_code") or {}
        elif agent_name in ("tester", "reviewer"):
            all_code.update(state.get("frontend_code") or {})
            all_code.update(state.get("backend_code") or {})

        # If there are guardrails violations, prioritize violated files
        violations = state.get("guardrails_violations") or []
        violated_paths = {v["file_path"] for v in violations if v.get("severity") == "critical"}
        if violated_paths:
            return {k: v for k, v in all_code.items() if k in violated_paths}

        return all_code
```

#### 2.4.6 Pipeline Integration

The `ContextBudgetManager` is instantiated once per build and injected into each node:

```python
# In MultiAgentBuilder.__init__ or build() method:

class MultiAgentBuilder:
    def __init__(self, ...):
        # ... existing init ...
        self._project_rag_index: Optional[ProjectRAGIndex] = None
        self._context_budget_mgr: Optional[ContextBudgetManager] = None

    async def build(self, requirements: str, build_id: str, ...):
        # ... existing build setup ...

        # After aggregator merges code, index for RAG
        # (done in _aggregator_node or after first frontend+backend complete)
```

Modify `_aggregator_node` to build the RAG index:

```python
@monitor_node("aggregator")
def _aggregator_node(self, state: ProjectState) -> dict:
    # ... existing merge logic ...

    # Build project RAG index from merged code
    all_code = {}
    all_code.update(state.get("frontend_code") or {})
    all_code.update(state.get("backend_code") or {})

    if all_code:
        self._project_rag_index = ProjectRAGIndex()
        self._project_rag_index.index_project(all_code)
        self._context_budget_mgr = ContextBudgetManager(rag_index=self._project_rag_index)

    return {/* existing return */}
```

Modify each agent node to use the budget manager:

```python
@monitor_node("frontend")
def _frontend_node(self, state: ProjectState) -> dict:
    model_id = state.get("_override_model") or "claude-sonnet-4-6"  # From router.yaml

    base_prompt = self._build_frontend_prompt(state)  # Existing prompt builder

    if self._context_budget_mgr:
        result = self._context_budget_mgr.build_prompt(
            agent_name="frontend",
            model_id=model_id,
            state=state,
            base_prompt=base_prompt,
        )
        prompt = result.agent_prompt
        if result.trimmed:
            lg_logger.info(f"[BUDGET] frontend: {result.token_count}/{result.budget_limit} tokens. {result.trimmed_report}")
    else:
        prompt = base_prompt

    # ... invoke LLM with trimmed prompt ...
```

---

### 2.5 Phase 2.0 — File Change Manifest

| File | Action | Subsystem | Description |
|------|--------|-----------|-------------|
| `backend/ai/agents/guardrails.py` | **NEW** | IE-1 | AST-based architectural enforcement engine with 10 Iron Rules |
| `backend/code_review/rules.py` | **MODIFY** | IE-1 | Add `ArchitecturalRules` class (ARCH001-ARCH010) to `StaticAnalyzer.RULE_SETS` |
| `backend/api/expert_routes.py` | **NEW** | IE-2 | Expert-in-the-Loop API (create, claim, resolve requests) |
| `backend/services/expert_pool.py` | **NEW** | IE-2 | `ExpertContext` dataclass + context packaging logic |
| `backend/ai/agents/context_manager.py` | **NEW** | Budget | `ContextBudgetManager`, `ProjectRAGIndex`, token estimation |
| `backend/src/smart_routing.py` | **NEW** | Loop | `get_alternate_model()` function + `_ALTERNATE_MODELS` map |
| `backend/ai/agents/multi_agent.py` | **MODIFY** | All | Add `_guardrails_gate_node`, `_detect_semantic_loop`, rewrite `_route_after_review` with 3-stage escalation, integrate `ContextBudgetManager` into all nodes, extend `ProjectState` with new fields |
| `frontend/convex/schema.ts` | **MODIFY** | IE-2 | Add `expertRequests` table |
| `backend/convex/schema.ts` | **MODIFY** | IE-2 | Add `expertRequests` table (mirror) |
| `frontend/convex/expertRequests.ts` | **NEW** | IE-2 | Convex queries + mutations for expert requests |
| `frontend/components/build/GuardrailsReport.tsx` | **NEW** | IE-1 | Guardrails violation report UI |
| `frontend/components/expert/SOSButton.tsx` | **NEW** | IE-2 | SOS button component (red, pulsing at stuck_count >= 3) |
| `frontend/components/build/LoopDetectionBanner.tsx` | **NEW** | Loop | Model switch notification banner |

**Total**: 8 new files, 5 modified files, 0 deleted files.

---

### 2.6 Phase 2.0 — Expert Acceptance Criteria

> **Gate**: ALL BLOCKER criteria must pass. HIGH criteria should pass with documented rationale for exceptions.

#### AC-7: Architectural Guardrails (IE-1)

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-7.1 | ARCH001 detects business logic in UI | Generate a component with `fetch()` call inside `components/Dashboard.tsx` | `ArchitecturalGuardrails.validate_project()` returns violation with `rule_id="ARCH001"`, correct file and line | **BLOCKER** |
| AC-7.2 | ARCH008 detects circular dependency | Create files A.tsx importing B.tsx, B.tsx importing A.tsx | `_detect_circular_deps()` returns cycle `[A.tsx, B.tsx, A.tsx]` | **BLOCKER** |
| AC-7.3 | ARCH003 detects files >300 lines | Generate a file with 350 lines | Violation returned with `rule_id="ARCH003"` and `line=350` | HIGH |
| AC-7.4 | Guardrails gate routes back to frontend on CRITICAL violation | Run pipeline with code containing ARCH001 violation | `_route_after_guardrails` returns `"frontend"`, not `"tester"` | **BLOCKER** |
| AC-7.5 | Fix loop limited to 2 cycles | Inject unfixable ARCH001 violation (agent always regenerates with fetch) | After `guardrails_iteration >= 3`, route proceeds to `"tester"` anyway. Build completes (with logged warning). | **BLOCKER** |
| AC-7.6 | Fix prompt is targeted, not full regeneration | Trigger guardrails fix cycle | Agent receives prompt containing ONLY violated files + fix instructions, NOT the full original generation prompt | HIGH |
| AC-7.7 | Guardrails results stored in Convex | Complete a build with 2 HIGH violations | `builds` record has `guardrailsResult` field with `totalViolations: 2`, `passed: true` (no CRITICAL) | HIGH |
| AC-7.8 | `ArchitecturalRules` registered in `StaticAnalyzer` | Call `StaticAnalyzer.analyze(code, filename)` on code with `fetch()` in a component file | Returns `ReviewFinding` with `id` starting with `ARCH` | HIGH |

#### AC-8: Expert-in-the-Loop (IE-2)

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-8.1 | SOS request creates Convex record | `POST /api/v1/expert/request` with valid `buildId`, `projectId` | Returns `201` with `requestId`. Convex `expertRequests` table has record with `status: "pending"` | **BLOCKER** |
| AC-8.2 | Context package includes all required fields | Inspect `contextSnapshot` field of created expert request | JSON contains: `requirements`, `files` (>0 entries), `agent_history` (>0 entries), `errors`, `chat_messages` | **BLOCKER** |
| AC-8.3 | Context package respects 1MB Convex limit | Create SOS request for a 50-file project | `contextSnapshot` JSON < 900KB. Large files truncated with `[TRUNCATED]` marker. | HIGH |
| AC-8.4 | Expert claim updates status | `POST /api/v1/expert/requests/{id}/claim` | Status changes from `"pending"` to `"claimed"`. `claimedAt` timestamp set. | **BLOCKER** |
| AC-8.5 | Expert resolve applies patches to project | `POST /api/v1/expert/requests/{id}/resolve` with `patchedFiles: {"src/App.tsx": "..."}` | `projects:updateFile` mutation called for each patched file. Content matches. | **BLOCKER** |
| AC-8.6 | Expert fix stored in procedural memory | Resolve a request with a fix | `MemoryManager.update_procedure()` called with fix pattern. `get_procedures("expert_fixes")` returns the fix. | HIGH |
| AC-8.7 | Unauthenticated SOS request rejected | `POST /api/v1/expert/request` without Authorization header | Returns `401 Unauthorized` | **BLOCKER** |

#### AC-9: Semantic Loop Detection

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-9.1 | Identical issues detected as semantic loop | Submit 2 review iterations with same issues worded differently: "Missing error handling in auth" then "Auth module lacks error handling" | `_detect_semantic_loop()` returns `(True, similarity > 0.85)` | **BLOCKER** |
| AC-9.2 | Different issues NOT detected as loop | Submit 2 review iterations with genuinely different issues: "SQL injection in query" then "Missing CSRF token" | `_detect_semantic_loop()` returns `(False, similarity < 0.5)` | **BLOCKER** |
| AC-9.3 | Stage 1: Model switch on first stuck detection | Trigger stuck_count == 1 | `_route_after_review` returns `"frontend"`. `state["_override_model"]` is a different provider's model. `model_switch_history` has 1 entry. | **BLOCKER** |
| AC-9.4 | Stage 2: Thinking model on second stuck detection | Trigger stuck_count == 2 | `state["_override_model"]` is `"gpt-5.2-pro"` (thinking model). `state["_include_anti_patterns"]` is `True`. | HIGH |
| AC-9.5 | Stage 3: Build finalizes on stuck_count >= 3 | Trigger stuck_count >= 3 | `_route_after_review` returns `"finalize"`. Build completes with available code (not infinite loop). `state["stuck_count"]` == 3. | **BLOCKER** |
| AC-9.6 | Embeddings stored between iterations | Run reviewer twice | After first review, `state["previous_issues_embeddings"]` is a non-empty list of float lists. After second review, comparison uses these stored embeddings. | HIGH |
| AC-9.7 | `get_alternate_model()` always returns different provider | Call with each model in `_ALTERNATE_MODELS` | Every returned model is from a different provider than the input. Claude → GPT, GPT → Claude, Gemini → Claude. | HIGH |

#### AC-10: Context Budget Manager

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-10.1 | Small project passes through untrimmed | Build a 5-file project (~2000 tokens total) | `BudgetResult.trimmed == False`. Full code in prompt. | **BLOCKER** |
| AC-10.2 | Large project triggers P2 summarization | Build a 30-file project (~80K tokens) with `claude-sonnet` (200K context) | `BudgetResult.trimmed == True`. P1 content present (requirements, arch, errors). P2 content replaced with summary from `ProjectRAGIndex.get_project_summary()`. | **BLOCKER** |
| AC-10.3 | ProjectRAGIndex retrieves relevant files | Index 20 files, query "React component for user authentication" | Returns files related to auth (e.g., `LoginForm.tsx`, `auth.ts`) in top 5 results | HIGH |
| AC-10.4 | P1 content never trimmed | Build a project where P1 content alone = 50K tokens. Budget = 60K. | P1 included in full. P2 content summarized to fit in remaining 10K. | **BLOCKER** |
| AC-10.5 | Token estimation within 20% of actual | Estimate tokens for 10 diverse code files, compare with tiktoken | All estimates within 20% of tiktoken count | HIGH |
| AC-10.6 | Budget manager integrated into all agent nodes | Run a full build, check logs | Every agent node logs `[BUDGET]` line showing token count and budget limit | HIGH |
| AC-10.7 | RAG index built after aggregation | Run parallel build, check `_project_rag_index` after aggregator | Index is not None. Contains documents from both frontend and backend code. | HIGH |

#### Severity Definitions (same as Phase 1.0)

| Severity | Definition | Gate Rule |
|----------|-----------|-----------|
| **BLOCKER** | Phase cannot be approved if this fails | Must pass |
| HIGH | Significant functionality gap, but workaround exists | Should pass, exceptions require documented rationale |
| MEDIUM | Quality/polish issue, acceptable for Phase 2 MVP | Best effort |

---

### 2.7 Phase 2.0 — Dependency Map

```
┌─────────────────┐
│  IE-1 Guardrails │◄─── Depends on: StaticAnalyzer (code_review/rules.py) ✅ exists
│  guardrails.py   │◄─── Depends on: ReviewFinding, ReviewSeverity ✅ exists
│                  │───► Modifies: multi_agent.py (new node + routing)
└────────┬────────┘
         │ guardrails_violations stored in builds table
         ▼
┌─────────────────┐
│  IE-2 Expert SOS │◄─── Depends on: Convex builds/agentStatus queries ✅ Phase 1.0
│  expert_routes.py│◄─── Depends on: verify_auth middleware ✅ Phase 1.0
│                  │◄─── Depends on: MemoryManager.update_procedure() ✅ exists
│                  │───► New table: expertRequests (Convex schema extension)
└─────────────────┘

┌─────────────────┐
│  Loop Detection  │◄─── Depends on: EmbeddingProvider.embed() ✅ exists (memory/__init__.py)
│  multi_agent.py  │◄─── Depends on: SemanticStore._cosine_similarity() ✅ exists
│  smart_routing.py│◄─── Depends on: router.yaml model definitions ✅ exists
│                  │───► Modifies: _route_after_review(), ProjectState
└────────┬────────┘
         │ stuck_count >= 3 triggers SOS suggestion
         ▼
         IE-2 Expert SOS (above)

┌─────────────────┐
│  Context Budget  │◄─── Depends on: VectorStore (ai/rag/__init__.py) ✅ exists
│  context_manager │◄─── Depends on: EmbeddingProvider ✅ exists
│                  │◄─── Depends on: DocumentLoader (chunking) ✅ exists
│                  │───► Injected into: all 8 pipeline nodes in multi_agent.py
└─────────────────┘
```

**Implementation order** (recommended):
1. **Context Budget Manager** (no pipeline changes, purely additive)
2. **Semantic Loop Detection** (modifies `_route_after_review` + `ProjectState`)
3. **IE-1 Guardrails** (adds new pipeline node + routing)
4. **IE-2 Expert SOS** (depends on stuck_count from Loop Detection + Convex schema)

---

## Phase 2.5: Reliability Gap — "From Code Generator to Product Builder"

> **Document Status**: Phase 2.5 DETAILED
> **Last Updated**: 2026-03-28
> **Prerequisite**: Phase 2.0 COMPLETE (174/174 tests PASS)
> **Approval Workflow**: Requires explicit "PHASE 2.5 APPROVED" before implementation begins.

### 2.5.0 Scope Overview

Phase 2.5 bridges the gap between "generates code" and "generates a product". Two subsystems address reliability problems that no competing AI builder solves:

| Subsystem | Problem Solved | Key File(s) |
|-----------|---------------|-------------|
| **IE-4: Visual QA Agent** | AI code passes logic tests but has visual regressions — overlapping elements, cut-off text, broken responsive layouts, WCAG violations | `backend/ai/agents/visual_qa.py` |
| **IE-5: Semantic Schema Migrations** | AI builders do `DROP TABLE + CREATE TABLE` on every schema change, destroying user data | `backend/ai/agents/db_architect.py` |

**Pipeline Position**: Both subsystems are inserted into the existing 8-node LangGraph pipeline:

```
CURRENT (Phase 2.0):
  architect → [frontend || backend] → aggregator → guardrails_gate → tester → reviewer → finalize

PHASE 2.5 (10-node):
  architect → [frontend || backend] → aggregator → guardrails_gate
    → db_migrations → tester → visual_qa → reviewer → finalize
```

- `db_migrations` runs **before** tester (it modifies backend_code by adding migration files)
- `visual_qa` runs **after** tester, **before** reviewer (reviewer sees visual QA results as mandatory review criteria)

**Existing Infrastructure Leveraged**:

| Component | File | What Phase 2.5 Uses |
|-----------|------|---------------------|
| `@monitor_node` decorator | `multi_agent.py:53-76` | Applied to both new nodes |
| `ProjectState` TypedDict | `multi_agent.py:224-271` | Extended with 4 new fields |
| `contextSnapshots` Convex table | `frontend/convex/schema.ts:589-614` | Previous build state for schema diff |
| `MemoryManager.store_fact()` | `memory/__init__.py` | Store schema snapshots for cross-build recall |
| `ReviewFinding`, `ReviewSeverity` | `code_review/service.py:27-97` | Visual QA findings use same data structures |
| `AICodeReviewService` | `code_review/service.py:144` | Pattern for multimodal LLM analysis |
| `ConvexDB` | `db/convex.py` | Query previous build schemas from Convex |
| `builds` Convex table | `frontend/convex/schema.ts:504-547` | Link current build to previous build's schema |

---

### 2.5.1 IE-4: Visual QA Agent — "The Eyes of the System"

#### 2.5.1.1 Problem Statement

AI-generated code passes unit tests and static analysis but suffers from **visual regressions** invisible to text-based review:

- Components that overlap or are clipped by parent containers
- Text that overflows its bounds or is unreadable against its background
- Buttons too small to tap on mobile (< 44×44px touch target)
- Color contrast below WCAG AA thresholds (< 4.5:1 for normal text)
- Responsive layouts that collapse incorrectly at tablet/mobile breakpoints
- Dark mode that renders white text on white background

**Current state**: The `_tester_node` (line 724) generates functional tests and the `_reviewer_node` (line 729) does a text-based code review. Neither can detect visual issues because they never **render** the generated code.

**No competitor solves this**: Bolt, Lovable, v0, and Replit all rely on users to visually inspect the preview and report issues manually. VBuilder will be the first to have an automated visual QA pipeline integrated into the build process.

#### 2.5.1.2 Solution: Headless Rendering + Multimodal Analysis

The Visual QA Agent renders the generated frontend code in a headless browser (Playwright), captures screenshots at multiple viewports, and sends them to a multimodal LLM (Claude Opus 4.6 Vision) for analysis against design system tokens and WCAG standards.

```
Generated Frontend Code (from aggregator)
       ↓
   Playwright Headless Browser
     • Desktop: 1440×900
     • Tablet: 768×1024
     • Mobile: 375×812
       ↓
   Screenshots (PNG, base64-encoded)
       ↓
   Multimodal LLM Analysis (per screenshot):
     1. Layout consistency — no overlaps, no clipped content, no empty areas
     2. Color contrast — WCAG AA (4.5:1 for text, 3:1 for large text)
     3. Touch targets — min 44×44px for interactive elements
     4. Responsive integrity — content readable at all breakpoints
     5. Design system compliance — spacing, typography, color tokens
     6. Dark mode — no invisible text, proper contrast in both themes
       ↓
   VisualQAReport:
     • Per-route, per-device: PASS / WARN / FAIL
     • Annotated screenshot descriptions (issue location, severity)
     • Auto-fix suggestions as ReviewFinding entries
```

#### 2.5.1.3 File: `backend/ai/agents/visual_qa.py`

```python
"""
Visual QA Agent — IE-4 Phase 2.5
=================================
Headless rendering + multimodal LLM analysis of generated UI.
"""

import base64
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# =========================================================================
# Constants
# =========================================================================

# Viewport presets for responsive testing
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "tablet":  {"width": 768,  "height": 1024},
    "mobile":  {"width": 375,  "height": 812},
}

# Max screenshots per build (3 viewports × N routes, capped)
MAX_SCREENSHOTS = 15

# Max base64 image size to send to LLM (500 KB per screenshot)
MAX_IMAGE_BYTES = 500 * 1024

# WCAG AA contrast ratios
WCAG_AA_NORMAL_TEXT = 4.5
WCAG_AA_LARGE_TEXT = 3.0
WCAG_MIN_TOUCH_TARGET = 44  # px


# =========================================================================
# Data Structures
# =========================================================================

class VisualSeverity(str, Enum):
    """Severity levels for visual issues."""
    CRITICAL = "critical"   # Invisible/unreadable content, broken layout
    HIGH = "high"           # WCAG violation, touch target too small
    MEDIUM = "medium"       # Minor spacing/alignment issue
    INFO = "info"           # Suggestion for improvement


@dataclass
class VisualIssue:
    """A single visual issue detected by the QA agent."""
    id: str                     # "VIS001", "A11Y001", etc.
    title: str                  # "Text overflows container"
    description: str            # Detailed description of the issue
    severity: VisualSeverity
    viewport: str               # "desktop", "tablet", "mobile"
    route: str                  # "/" or "/dashboard"
    location_hint: str          # "top-left area, navigation bar"
    fix_suggestion: str         # "Add overflow-hidden and text-ellipsis to the nav container"

    def to_dict(self) -> dict:
        return {**asdict(self), "severity": self.severity.value}


@dataclass
class VisualQAReport:
    """Complete visual QA report for a build."""
    issues: List[VisualIssue] = field(default_factory=list)
    screenshots_analyzed: int = 0
    routes_tested: List[str] = field(default_factory=list)
    viewports_tested: List[str] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)  # {"pass": N, "warn": N, "fail": N}
    # Per-route, per-viewport grade
    grades: Dict[str, Dict[str, str]] = field(default_factory=dict)  # {route: {viewport: "pass"|"warn"|"fail"}}

    def to_dict(self) -> dict:
        return {
            "issues": [i.to_dict() for i in self.issues],
            "screenshots_analyzed": self.screenshots_analyzed,
            "routes_tested": self.routes_tested,
            "viewports_tested": self.viewports_tested,
            "summary": self.summary,
            "grades": self.grades,
        }

    @property
    def has_critical(self) -> bool:
        return any(i.severity == VisualSeverity.CRITICAL for i in self.issues)


class VisualQAAgent:
    """
    Headless rendering + multimodal LLM analysis.

    Pipeline position: after Tester, before Reviewer.
    """

    def __init__(self, llm_provider: str = "auto"):
        self.llm_provider = llm_provider

    # ---- Route Detection ----

    def detect_routes(self, frontend_files: Dict[str, str]) -> List[str]:
        """
        Detect routes from the generated frontend code.

        Heuristics:
        - Next.js App Router: files in `app/**/page.tsx` → route = directory path
        - React Router: scan for <Route path="..."> patterns
        - Fallback: ["/"] (just the root route)
        """
        ...

    # ---- Screenshot Capture ----

    async def capture_screenshots(
        self,
        frontend_files: Dict[str, str],
        routes: List[str],
        viewports: Dict[str, Dict[str, int]] = None,
    ) -> Dict[str, Dict[str, bytes]]:
        """
        Render each route at each viewport using Playwright headless.

        Returns: {route: {viewport_name: png_bytes}}

        Implementation strategy:
        1. Write frontend_files to a temp directory
        2. Install deps + start dev server (via subprocess)
        3. For each route × viewport:
           a. page.set_viewport_size(viewport)
           b. page.goto(f"http://localhost:{port}{route}")
           c. page.wait_for_load_state("networkidle")
           d. screenshot = page.screenshot(full_page=True)
        4. Clean up temp dir + kill dev server
        """
        ...

    # ---- Multimodal Analysis ----

    async def analyze_screenshot(
        self,
        screenshot_bytes: bytes,
        route: str,
        viewport: str,
        design_tokens: Optional[Dict[str, Any]] = None,
    ) -> List[VisualIssue]:
        """
        Send screenshot to multimodal LLM with structured analysis prompt.

        The prompt instructs the model to return JSON with issues found.
        See VISUAL_QA_PROMPT below for the exact prompt template.
        """
        ...

    # ---- Full Pipeline ----

    async def run(
        self,
        frontend_files: Dict[str, str],
        architecture: Optional[Dict[str, Any]] = None,
    ) -> VisualQAReport:
        """
        Full Visual QA pipeline:
        1. Detect routes
        2. Capture screenshots (all routes × all viewports)
        3. Analyze each screenshot
        4. Compile VisualQAReport
        """
        ...
```

#### 2.5.1.4 Screenshot Capture Strategy

The Visual QA Agent needs to actually **render** the generated code. Two strategies are supported (tried in order):

**Strategy A: Sandpack In-Process** (preferred, no external deps)

If the generated frontend code is a simple React/Next.js app that Sandpack can render:
1. Use `@codesandbox/sandpack-react` (already in `frontend/package.json`) server-side
2. Render into a headless Chromium instance via Playwright
3. No npm install needed — Sandpack bundles in-browser

**Strategy B: Full Dev Server** (fallback for complex apps)

If the code requires server-side rendering or complex dependencies:
1. Write files to temp directory
2. `npm install && npm run dev` in subprocess (timeout: 30s)
3. Playwright navigates to `localhost:{port}`
4. Capture screenshots, kill server, clean up

**Strategy selection** (in `capture_screenshots()`):
```python
# Heuristic: if only frontend files with standard React deps → Sandpack
# If backend_code present or complex deps (prisma, etc.) → full dev server
if _has_complex_deps(frontend_files):
    return await self._capture_via_dev_server(frontend_files, routes, viewports)
else:
    return await self._capture_via_sandpack(frontend_files, routes, viewports)
```

**Graceful degradation**: If both strategies fail (e.g., code won't compile), the Visual QA node returns an empty `VisualQAReport` with `screenshots_analyzed=0` and a descriptive error in `issues`. The pipeline continues — Visual QA failures are not blocking.

#### 2.5.1.5 Multimodal Analysis Prompt

The prompt sent to the multimodal LLM is critical. It must be structured to return parseable JSON.

```python
VISUAL_QA_PROMPT = """You are a Visual QA expert analyzing a screenshot of a web application.

**Viewport**: {viewport} ({width}×{height}px)
**Route**: {route}

Analyze this screenshot for the following issues:

## 1. Layout Consistency
- Are any elements overlapping inappropriately?
- Is any text or content cut off or clipped?
- Are there any large empty/blank areas that suggest missing content?
- Is the layout balanced and well-structured?

## 2. Color Contrast (WCAG AA)
- Does all normal text (< 18pt) have at least 4.5:1 contrast ratio against its background?
- Does all large text (>= 18pt or >= 14pt bold) have at least 3:1 contrast ratio?
- Are there any areas where text is hard to read against its background?

## 3. Touch Targets (Mobile/Tablet only)
- Are all interactive elements (buttons, links, inputs) at least 44×44px?
- Is there sufficient spacing between interactive elements?

## 4. Responsive Design
- Does the content fit within the viewport without horizontal scrolling?
- Are images and containers properly sized for this viewport?
- Is navigation accessible at this viewport size?

## 5. General Visual Quality
- Are fonts consistent across the page?
- Is spacing consistent (margins, padding)?
- Do colors match a cohesive design system?
- Are loading/skeleton states visible where appropriate?

Return ONLY a JSON array of issues found. Each issue must have:
- "id": unique identifier (VIS001, VIS002, A11Y001, etc.)
- "title": short title (< 60 chars)
- "description": detailed description
- "severity": "critical" | "high" | "medium" | "info"
- "location_hint": where in the screenshot the issue appears
- "fix_suggestion": specific CSS/component fix

If no issues are found, return an empty array: []

{design_context}
"""
```

**Design context injection**: If the architecture spec includes design tokens (colors, spacing, typography), they are appended as:
```
## Design System Reference
Primary color: {primary}
Background: {background}
Font family: {font_family}
Base spacing: {spacing}
Border radius: {border_radius}
```

#### 2.5.1.6 Pipeline Integration

**New `ProjectState` fields** (add to TypedDict at line 270):

```python
    # Phase 2.5 — IE-4 Visual QA
    visual_qa_report: Optional[Dict[str, Any]]      # VisualQAReport.to_dict()
    visual_qa_screenshots: Optional[int]             # Count of screenshots analyzed
```

**New node**: `_visual_qa_node` — added to `_build_graph()`:

```python
@monitor_node("visual_qa")
def _visual_qa_node(self, state: ProjectState) -> Dict[str, Any]:
    """
    Visual QA node — renders generated frontend, captures screenshots,
    analyzes with multimodal LLM.

    Position: after tester, before reviewer.
    Non-blocking: failures result in empty report, not pipeline abort.
    """
    frontend_code = state.get("frontend_code") or {}
    if not frontend_code:
        return {
            "visual_qa_report": {"issues": [], "screenshots_analyzed": 0, "summary": {"pass": 0}},
            "visual_qa_screenshots": 0,
        }

    try:
        from agents.visual_qa import VisualQAAgent
        agent = VisualQAAgent(llm_provider="auto")
        report = asyncio.run(agent.run(
            frontend_files=frontend_code,
            architecture=state.get("architecture"),
        ))
        return {
            "visual_qa_report": report.to_dict(),
            "visual_qa_screenshots": report.screenshots_analyzed,
        }
    except Exception as e:
        lg_logger.warning("[VISUAL_QA] Failed: %s — pipeline continues", e)
        return {
            "visual_qa_report": {
                "issues": [{"id": "VQA_ERROR", "title": f"Visual QA failed: {e}",
                           "severity": "info", "viewport": "n/a", "route": "/",
                           "location_hint": "n/a", "description": str(e),
                           "fix_suggestion": "Manual visual inspection recommended"}],
                "screenshots_analyzed": 0,
                "summary": {"error": 1},
            },
            "visual_qa_screenshots": 0,
        }
```

**Edge modifications** in `_build_graph()`:

```python
# BEFORE (Phase 2.0):
graph.add_edge("tester", "reviewer")

# AFTER (Phase 2.5):
graph.add_edge("tester", "visual_qa")
graph.add_edge("visual_qa", "reviewer")
```

**Reviewer integration**: The reviewer node already reads `state.get("review_results")`. We extend it to also read `visual_qa_report` and inject critical visual issues as mandatory review criteria:

```python
# In _reviewer_node, prepend visual QA context to reviewer prompt
vqa = state.get("visual_qa_report") or {}
vqa_issues = vqa.get("issues", [])
critical_visual = [i for i in vqa_issues if i.get("severity") in ("critical", "high")]
if critical_visual:
    # Inject as system context for the reviewer
    visual_context = "\n".join(
        f"- [{i['id']}] {i['title']} ({i['viewport']}): {i['fix_suggestion']}"
        for i in critical_visual
    )
    # Prepend to messages
    state["messages"].append(SystemMessage(
        content=f"VISUAL QA ISSUES (must be addressed in review):\n{visual_context}"
    ))
```

#### 2.5.1.7 Graceful Degradation

Visual QA is **non-blocking** by design:

| Scenario | Behavior |
|----------|----------|
| Playwright not installed | Return empty report, log warning. Pipeline continues. |
| Code won't compile/render | Return report with `VQA_ERROR` issue (severity: info). Pipeline continues. |
| Multimodal LLM call fails | Return report with screenshots count but no issues. Pipeline continues. |
| All screenshots captured but analysis times out | Return partial report with analyzed screenshots. Pipeline continues. |
| No frontend code in build | Skip entirely, return empty report. |

**Rationale**: Visual QA adds value but should never be the reason a build fails. The reviewer and user can always inspect the preview manually.

#### 2.5.1.8 Test Strategy for Visual QA

All tests **mock** Playwright and LLM calls. We do NOT run a real headless browser in CI.

| Test Class | Tests | What It Verifies |
|------------|-------|------------------|
| `TestRouteDetection` | 5 | Next.js App Router pages detected, React Router patterns, fallback to "/" |
| `TestVisualQAReport` | 4 | Report construction, `to_dict()` serialization, `has_critical` property, empty report |
| `TestVisualIssue` | 3 | Issue `to_dict()`, severity enum values, field validation |
| `TestVisualQANode` | 6 | Node returns valid state, handles empty frontend_code, handles exceptions gracefully, reviewer receives visual context |
| `TestMultimodalPrompt` | 3 | Prompt includes viewport dimensions, route, design tokens when available |
| `TestScreenshotCapture` | 4 | Mock Playwright: correct viewport sizes, correct URLs, max screenshot cap respected |
| `TestGracefulDegradation` | 5 | Each failure mode returns empty/partial report without raising |
| **Total** | **30** | |

---

### 2.5.2 IE-5: Semantic Schema Migrations — "Data Memory"

#### 2.5.2.1 Problem Statement

AI builders destroy data on every schema change. The typical pattern:

```sql
-- What Bolt/Lovable/v0 generate:
DROP TABLE users;
CREATE TABLE users (id SERIAL, name TEXT, email TEXT, avatar_url TEXT);
-- All existing data: GONE
```

In a real development workflow, schema changes require **migrations** that preserve existing data:

```sql
-- What VBuilder should generate:
ALTER TABLE users ADD COLUMN avatar_url TEXT;
CREATE INDEX idx_users_email ON users(email);
-- Existing data: PRESERVED
```

**Current state**: The `_backend_node` (line 710) generates fresh database schema code on every build iteration. There is no mechanism to compare the new schema with the previous one or generate migration files.

**VBuilder's advantage**: The `contextSnapshots` table (Convex schema line 589) stores serialized build state after each pipeline node. This means we can retrieve the **previous build's schema** and diff it against the new one.

#### 2.5.2.2 Solution: Schema Diff Engine + Migration Generator

The DB Architect Agent compares the current build's data models against the previous build's models (retrieved from Convex `contextSnapshots` or `MemoryManager`), generates a structural diff, and produces migration files.

```
Previous Build Schema (from MemoryManager or contextSnapshots)
       ↓                                    ↓
   Schema Parser ◄──────────────── Current Backend Code
       ↓                                    ↓
   Schema Diff Engine
     • Added tables/columns
     • Removed tables/columns
     • Modified columns (type change, constraint change)
     • Added/removed indexes
     • Added/removed foreign keys
       ↓
   Migration Generator
     • SQL migration file (Prisma Migrate, Alembic, or raw SQL)
     • Rollback script (inverse of each migration step)
     • Validation: check for potential data loss
       ↓
   Output:
     • migrations/{timestamp}_{description}.sql     (added to backend_code)
     • migrations/{timestamp}_{description}_down.sql (rollback)
     • Schema snapshot stored in MemoryManager for next build
```

#### 2.5.2.3 File: `backend/ai/agents/db_architect.py`

```python
"""
DB Architect Agent — IE-5 Phase 2.5
====================================
Schema diffing + migration generation.
"""

import re
import json
import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone


# =========================================================================
# Data Structures
# =========================================================================

class ColumnType(str, Enum):
    """Common column types across ORMs."""
    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"
    JSON = "json"
    UUID = "uuid"
    SERIAL = "serial"
    BIGINT = "bigint"
    DECIMAL = "decimal"
    BLOB = "blob"
    UNKNOWN = "unknown"


@dataclass
class Column:
    """A single column in a table."""
    name: str
    type: ColumnType
    nullable: bool = True
    default: Optional[str] = None
    primary_key: bool = False
    unique: bool = False
    references: Optional[str] = None  # "other_table.column"

    def to_dict(self) -> dict:
        return {**asdict(self), "type": self.type.value}


@dataclass
class Index:
    """A database index."""
    name: str
    table: str
    columns: List[str]
    unique: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Table:
    """A database table definition."""
    name: str
    columns: List[Column] = field(default_factory=list)
    indexes: List[Index] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "columns": [c.to_dict() for c in self.columns],
            "indexes": [i.to_dict() for i in self.indexes],
        }

    def column_names(self) -> set:
        return {c.name for c in self.columns}

    def get_column(self, name: str) -> Optional[Column]:
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class Schema:
    """Complete database schema."""
    tables: List[Table] = field(default_factory=list)
    orm_type: str = "unknown"  # "prisma" | "sqlalchemy" | "drizzle" | "raw_sql" | "unknown"

    def to_dict(self) -> dict:
        return {
            "tables": [t.to_dict() for t in self.tables],
            "orm_type": self.orm_type,
        }

    def table_names(self) -> set:
        return {t.name for t in self.tables}

    def get_table(self, name: str) -> Optional[Table]:
        return next((t for t in self.tables if t.name == name), None)

    def fingerprint(self) -> str:
        """Deterministic hash of the schema for quick equality check."""
        canonical = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# =========================================================================
# Schema Diff
# =========================================================================

class DiffType(str, Enum):
    TABLE_ADDED = "table_added"
    TABLE_REMOVED = "table_removed"
    COLUMN_ADDED = "column_added"
    COLUMN_REMOVED = "column_removed"
    COLUMN_MODIFIED = "column_modified"
    INDEX_ADDED = "index_added"
    INDEX_REMOVED = "index_removed"


@dataclass
class SchemaDiff:
    """A single diff entry between two schemas."""
    diff_type: DiffType
    table_name: str
    column_name: Optional[str] = None
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None
    # Risk assessment
    data_loss_risk: bool = False  # True if this diff might lose data
    risk_description: str = ""

    def to_dict(self) -> dict:
        return {**asdict(self), "diff_type": self.diff_type.value}


@dataclass
class MigrationFile:
    """A generated migration file."""
    filename: str        # "migrations/20260328_add_avatar_column.sql"
    content: str         # The SQL/ORM migration code
    rollback_filename: str   # "migrations/20260328_add_avatar_column_down.sql"
    rollback_content: str    # The rollback SQL
    description: str     # Human-readable description of what this migration does
    diffs: List[SchemaDiff] = field(default_factory=list)
    has_data_loss_risk: bool = False

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "content": self.content,
            "rollback_filename": self.rollback_filename,
            "rollback_content": self.rollback_content,
            "description": self.description,
            "diffs": [d.to_dict() for d in self.diffs],
            "has_data_loss_risk": self.has_data_loss_risk,
        }
```

#### 2.5.2.4 Schema Parser

The parser extracts `Schema` from generated backend code. It supports three ORM patterns (detected by file content heuristics):

**Prisma** (`.prisma` files):
```python
def parse_prisma(content: str) -> Schema:
    """
    Parse a Prisma schema file into our Schema model.

    Regex-based parsing of:
    - model User { ... } blocks
    - Field definitions: name Type @attributes
    - @@index([columns]) directives
    - Relations: @relation(fields: [...], references: [...])
    """
    ...
```

Pattern matched:
```prisma
model User {
  id        String   @id @default(cuid())
  email     String   @unique
  name      String?
  posts     Post[]
  createdAt DateTime @default(now())

  @@index([email])
}
```

**SQLAlchemy** (Python `class Model(Base):`):
```python
def parse_sqlalchemy(content: str) -> Schema:
    """
    Parse SQLAlchemy model definitions.

    Regex-based parsing of:
    - class ModelName(Base): blocks
    - Column(Type, ...) assignments
    - relationship() definitions
    - Index() and UniqueConstraint() in __table_args__
    """
    ...
```

**Drizzle / Raw SQL** (`.sql` files or `CREATE TABLE` statements):
```python
def parse_sql(content: str) -> Schema:
    """
    Parse CREATE TABLE statements from SQL files.

    Regex-based parsing of:
    - CREATE TABLE name (columns...)
    - Column definitions with types and constraints
    - PRIMARY KEY, FOREIGN KEY, UNIQUE, NOT NULL
    - CREATE INDEX statements
    """
    ...
```

**ORM Detection** (called before parsing):
```python
def detect_orm_type(backend_files: Dict[str, str]) -> str:
    """
    Detect which ORM is used in the backend code.

    Heuristics:
    - Any file ending in .prisma or containing 'model ... {' → "prisma"
    - Python file with 'from sqlalchemy' or 'Column(' → "sqlalchemy"
    - TypeScript file with 'pgTable(' or 'sqliteTable(' → "drizzle"
    - .sql files with CREATE TABLE → "raw_sql"
    - Otherwise → "unknown"
    """
    ...
```

**`extract_schema(backend_files: Dict[str, str]) -> Schema`**:
The main entry point. Detects ORM type, finds relevant files, parses them into a unified `Schema` model.

#### 2.5.2.5 Schema Diff Engine

```python
def diff_schemas(old_schema: Schema, new_schema: Schema) -> List[SchemaDiff]:
    """
    Compute the structural diff between two schemas.

    Algorithm:
    1. Find added tables (in new but not in old)
    2. Find removed tables (in old but not in new)
    3. For tables in both:
       a. Find added columns
       b. Find removed columns (DATA LOSS RISK!)
       c. Find modified columns (type change = DATA LOSS RISK!)
       d. Find added/removed indexes

    Data loss risk flagged when:
    - A table is removed (DROP TABLE)
    - A column is removed (DROP COLUMN)
    - A column type changes from wider to narrower (e.g., TEXT → INTEGER)
    - A non-nullable column is added without a default
    """
    diffs = []

    old_tables = old_schema.table_names()
    new_tables = new_schema.table_names()

    # 1. Added tables
    for name in new_tables - old_tables:
        diffs.append(SchemaDiff(
            diff_type=DiffType.TABLE_ADDED,
            table_name=name,
            new_value=new_schema.get_table(name).to_dict(),
        ))

    # 2. Removed tables (DATA LOSS!)
    for name in old_tables - new_tables:
        diffs.append(SchemaDiff(
            diff_type=DiffType.TABLE_REMOVED,
            table_name=name,
            old_value=old_schema.get_table(name).to_dict(),
            data_loss_risk=True,
            risk_description=f"Table '{name}' will be dropped. All data will be lost.",
        ))

    # 3. Modified tables
    for name in old_tables & new_tables:
        old_table = old_schema.get_table(name)
        new_table = new_schema.get_table(name)

        old_cols = old_table.column_names()
        new_cols = new_table.column_names()

        # 3a. Added columns
        for col_name in new_cols - old_cols:
            col = new_table.get_column(col_name)
            risk = not col.nullable and col.default is None
            diffs.append(SchemaDiff(
                diff_type=DiffType.COLUMN_ADDED,
                table_name=name,
                column_name=col_name,
                new_value=col.to_dict(),
                data_loss_risk=risk,
                risk_description=(
                    f"Non-nullable column '{col_name}' added without default. "
                    f"Existing rows will fail." if risk else ""
                ),
            ))

        # 3b. Removed columns (DATA LOSS!)
        for col_name in old_cols - new_cols:
            diffs.append(SchemaDiff(
                diff_type=DiffType.COLUMN_REMOVED,
                table_name=name,
                column_name=col_name,
                old_value=old_table.get_column(col_name).to_dict(),
                data_loss_risk=True,
                risk_description=f"Column '{name}.{col_name}' will be dropped.",
            ))

        # 3c. Modified columns
        for col_name in old_cols & new_cols:
            old_col = old_table.get_column(col_name)
            new_col = new_table.get_column(col_name)
            if old_col.type != new_col.type or old_col.nullable != new_col.nullable:
                risk = old_col.type != new_col.type
                diffs.append(SchemaDiff(
                    diff_type=DiffType.COLUMN_MODIFIED,
                    table_name=name,
                    column_name=col_name,
                    old_value=old_col.to_dict(),
                    new_value=new_col.to_dict(),
                    data_loss_risk=risk,
                    risk_description=(
                        f"Type change from {old_col.type.value} to {new_col.type.value} "
                        f"may cause data loss." if risk else ""
                    ),
                ))

        # 3d. Index diffs
        old_idx_names = {i.name for i in old_table.indexes}
        new_idx_names = {i.name for i in new_table.indexes}
        for idx_name in new_idx_names - old_idx_names:
            idx = next(i for i in new_table.indexes if i.name == idx_name)
            diffs.append(SchemaDiff(
                diff_type=DiffType.INDEX_ADDED,
                table_name=name,
                new_value=idx.to_dict(),
            ))
        for idx_name in old_idx_names - new_idx_names:
            idx = next(i for i in old_table.indexes if i.name == idx_name)
            diffs.append(SchemaDiff(
                diff_type=DiffType.INDEX_REMOVED,
                table_name=name,
                old_value=idx.to_dict(),
            ))

    return diffs
```

#### 2.5.2.6 Migration Generator

```python
def generate_migration(
    diffs: List[SchemaDiff],
    orm_type: str = "raw_sql",
    project_name: str = "vbuilder",
) -> Optional[MigrationFile]:
    """
    Generate a migration file from schema diffs.

    If no diffs, returns None (no migration needed).

    Supported ORM types:
    - "raw_sql": Plain SQL (ALTER TABLE, CREATE TABLE, CREATE INDEX)
    - "prisma": Prisma Migrate format (SQL + metadata)
    - "sqlalchemy": Alembic revision format (Python)

    For all types, also generates a rollback script (inverse operations).
    """
    if not diffs:
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    description = _summarize_diffs(diffs)
    slug = _slugify(description)

    # Generate forward migration
    up_statements = []
    down_statements = []

    for diff in diffs:
        up, down = _diff_to_sql(diff)
        up_statements.append(up)
        down_statements.append(down)

    migration_sql = (
        f"-- Migration: {description}\n"
        f"-- Generated by VBuilder DB Architect (IE-5)\n"
        f"-- Timestamp: {timestamp}\n\n"
        + "\n".join(up_statements)
    )

    rollback_sql = (
        f"-- Rollback: {description}\n"
        f"-- Generated by VBuilder DB Architect (IE-5)\n\n"
        + "\n".join(reversed(down_statements))
    )

    return MigrationFile(
        filename=f"migrations/{timestamp}_{slug}.sql",
        content=migration_sql,
        rollback_filename=f"migrations/{timestamp}_{slug}_down.sql",
        rollback_content=rollback_sql,
        description=description,
        diffs=diffs,
        has_data_loss_risk=any(d.data_loss_risk for d in diffs),
    )


def _diff_to_sql(diff: SchemaDiff) -> Tuple[str, str]:
    """Convert a single SchemaDiff to (forward_sql, rollback_sql)."""

    if diff.diff_type == DiffType.TABLE_ADDED:
        table = diff.new_value
        cols_sql = ", ".join(
            _column_to_sql(c) for c in table.get("columns", [])
        )
        up = f"CREATE TABLE {diff.table_name} ({cols_sql});"
        down = f"DROP TABLE IF EXISTS {diff.table_name};"

    elif diff.diff_type == DiffType.TABLE_REMOVED:
        # Risky! Include comment warning.
        up = f"-- WARNING: Data loss! Dropping table.\nDROP TABLE IF EXISTS {diff.table_name};"
        # Rollback: recreate (requires stored old schema)
        table = diff.old_value
        cols_sql = ", ".join(
            _column_to_sql(c) for c in table.get("columns", [])
        )
        down = f"CREATE TABLE {diff.table_name} ({cols_sql});"

    elif diff.diff_type == DiffType.COLUMN_ADDED:
        col = diff.new_value
        col_sql = _column_to_sql(col)
        up = f"ALTER TABLE {diff.table_name} ADD COLUMN {col_sql};"
        down = f"ALTER TABLE {diff.table_name} DROP COLUMN {diff.column_name};"

    elif diff.diff_type == DiffType.COLUMN_REMOVED:
        col = diff.old_value
        up = f"-- WARNING: Data loss!\nALTER TABLE {diff.table_name} DROP COLUMN {diff.column_name};"
        col_sql = _column_to_sql(col)
        down = f"ALTER TABLE {diff.table_name} ADD COLUMN {col_sql};"

    elif diff.diff_type == DiffType.COLUMN_MODIFIED:
        new_col = diff.new_value
        old_col = diff.old_value
        new_type = new_col.get("type", "text").upper()
        old_type = old_col.get("type", "text").upper()
        up = f"ALTER TABLE {diff.table_name} ALTER COLUMN {diff.column_name} TYPE {new_type};"
        down = f"ALTER TABLE {diff.table_name} ALTER COLUMN {diff.column_name} TYPE {old_type};"

    elif diff.diff_type == DiffType.INDEX_ADDED:
        idx = diff.new_value
        cols = ", ".join(idx.get("columns", []))
        unique = "UNIQUE " if idx.get("unique") else ""
        up = f"CREATE {unique}INDEX {idx['name']} ON {diff.table_name} ({cols});"
        down = f"DROP INDEX IF EXISTS {idx['name']};"

    elif diff.diff_type == DiffType.INDEX_REMOVED:
        idx = diff.old_value
        cols = ", ".join(idx.get("columns", []))
        unique = "UNIQUE " if idx.get("unique") else ""
        up = f"DROP INDEX IF EXISTS {idx['name']};"
        down = f"CREATE {unique}INDEX {idx['name']} ON {diff.table_name} ({cols});"

    else:
        up = f"-- Unknown diff type: {diff.diff_type}"
        down = f"-- Unknown diff type: {diff.diff_type}"

    return up, down
```

#### 2.5.2.7 Previous Schema Retrieval

The DB Architect needs the **previous build's schema** to compute diffs. Three retrieval strategies (tried in order):

**Strategy 1: MemoryManager (preferred)**

```python
def recall_previous_schema(project_id: str) -> Optional[Schema]:
    """
    Retrieve the most recent schema for this project from MemoryManager.

    Stored as a SEMANTIC memory with namespace "schema:{project_id}".
    """
    try:
        from memory import MemoryManager
        mm = MemoryManager()
        results = mm.search(
            query="database schema",
            namespace=f"schema:{project_id}",
            limit=1,
        )
        if results:
            schema_dict = json.loads(results[0].get("content", "{}"))
            return _dict_to_schema(schema_dict)
    except Exception:
        pass
    return None
```

**Strategy 2: Convex contextSnapshots (fallback)**

If MemoryManager has no schema stored (first build after Phase 2.5), fall back to parsing the previous build's backend code from the most recent `contextSnapshot`:

```python
async def recall_schema_from_convex(project_id: str) -> Optional[Schema]:
    """
    Retrieve schema from the latest contextSnapshot for this project.

    1. Query Convex: builds.getLatestByProject(projectId) → get buildId
    2. Query Convex: contextSnapshots.getByBuildNode(buildId, "finalize") → serializedState
    3. Parse serializedState → extract backend_code → parse schema
    """
    ...
```

**Strategy 3: No previous schema**

If this is the first-ever build for the project, there is no previous schema. In this case, `diff_schemas()` receives an empty `Schema()` as `old_schema`, and all tables appear as `TABLE_ADDED` — which means the migration is just `CREATE TABLE` statements (functionally identical to what the backend agent already generates, so the migration file is informational only).

**Schema storage after build** (in `_finalize_node`):

```python
# In _finalize_node, after packaging files:
schema = extract_schema(state.get("backend_code") or {})
if schema.tables:
    try:
        from memory import MemoryManager
        mm = MemoryManager()
        mm.store(
            content=json.dumps(schema.to_dict()),
            namespace=f"schema:{project_id}",
            memory_type="semantic",
            metadata={"fingerprint": schema.fingerprint(), "table_count": len(schema.tables)},
        )
    except Exception:
        pass
```

#### 2.5.2.8 Pipeline Integration

**New `ProjectState` fields** (add to TypedDict at line 270):

```python
    # Phase 2.5 — IE-5 Schema Migrations
    schema_migration: Optional[Dict[str, Any]]       # MigrationFile.to_dict() or None
    previous_schema_fingerprint: Optional[str]        # SHA256[:16] of previous schema
```

**New node**: `_db_migrations_node` — added to `_build_graph()`:

```python
@monitor_node("db_migrations")
def _db_migrations_node(self, state: ProjectState) -> Dict[str, Any]:
    """
    DB Migration node — extracts schema from backend code, diffs against
    previous build's schema, generates migration files.

    Position: after guardrails_gate, before tester.
    Non-blocking: if no schema found or no changes detected, passes through.
    """
    backend_code = state.get("backend_code") or {}
    if not backend_code:
        return {"schema_migration": None, "previous_schema_fingerprint": None}

    try:
        from agents.db_architect import (
            extract_schema,
            diff_schemas,
            generate_migration,
            recall_previous_schema,
            Schema,
        )

        # 1. Extract current schema
        current_schema = extract_schema(backend_code)
        if not current_schema.tables:
            return {"schema_migration": None, "previous_schema_fingerprint": None}

        # 2. Retrieve previous schema
        project_id = (state.get("architecture") or {}).get("project_id", "unknown")
        previous_schema = recall_previous_schema(project_id) or Schema()

        # 3. Quick check: if fingerprints match, no migration needed
        if current_schema.fingerprint() == previous_schema.fingerprint():
            return {
                "schema_migration": None,
                "previous_schema_fingerprint": previous_schema.fingerprint(),
            }

        # 4. Compute diff
        diffs = diff_schemas(previous_schema, current_schema)
        if not diffs:
            return {"schema_migration": None, "previous_schema_fingerprint": None}

        # 5. Generate migration file
        migration = generate_migration(diffs, orm_type=current_schema.orm_type)
        if not migration:
            return {"schema_migration": None, "previous_schema_fingerprint": None}

        # 6. Add migration files to backend_code so they appear in the final project
        updated_backend = dict(backend_code)
        updated_backend[migration.filename] = migration.content
        updated_backend[migration.rollback_filename] = migration.rollback_content

        return {
            "backend_code": updated_backend,
            "schema_migration": migration.to_dict(),
            "previous_schema_fingerprint": previous_schema.fingerprint(),
        }

    except Exception as e:
        lg_logger.warning("[DB_MIGRATIONS] Failed: %s — pipeline continues", e)
        return {"schema_migration": None, "previous_schema_fingerprint": None}
```

**Edge modifications** in `_build_graph()`:

```python
# BEFORE (Phase 2.0, parallel mode):
graph.add_edge("aggregator", "guardrails_gate")
graph.add_conditional_edges("guardrails_gate", self._route_after_guardrails, ["tester", "frontend"])

# AFTER (Phase 2.5):
graph.add_edge("aggregator", "guardrails_gate")
graph.add_conditional_edges("guardrails_gate", self._route_after_guardrails,
                           ["db_migrations", "frontend"])  # Changed: "tester" → "db_migrations"
graph.add_edge("db_migrations", "tester")

# BEFORE (Phase 2.0, sequential mode):
graph.add_edge("backend", "guardrails_gate")

# AFTER (Phase 2.5, sequential mode):
graph.add_edge("backend", "guardrails_gate")
# guardrails_gate already routes to "tester" or "frontend"
# Change "tester" target to "db_migrations":
# In _route_after_guardrails: return "db_migrations" instead of "tester"
```

**Updated `_route_after_guardrails`**:
```python
# Change return values:
# "tester" → "db_migrations" (db_migrations then feeds into tester)
```

#### 2.5.2.9 Data Loss Protection

When the migration has `has_data_loss_risk=True`, the finalize node includes a warning in the build summary:

```python
# In _finalize_node:
migration = state.get("schema_migration")
if migration and migration.get("has_data_loss_risk"):
    summary["warnings"] = summary.get("warnings", [])
    risky_diffs = [d for d in migration.get("diffs", []) if d.get("data_loss_risk")]
    summary["warnings"].append({
        "type": "schema_data_loss_risk",
        "message": f"Migration includes {len(risky_diffs)} operations with data loss risk",
        "details": [d.get("risk_description") for d in risky_diffs],
    })
```

#### 2.5.2.10 Test Strategy for Schema Migrations

| Test Class | Tests | What It Verifies |
|------------|-------|------------------|
| `TestSchemaParser` | 8 | Prisma parsing, SQLAlchemy parsing, raw SQL parsing, ORM detection, column types, indexes, foreign keys, empty input |
| `TestSchemaDiff` | 8 | Table added/removed, column added/removed/modified, index added/removed, data loss flags, empty diff for identical schemas |
| `TestMigrationGenerator` | 6 | SQL generation for each diff type, rollback generation, timestamp format, slug generation, data loss warnings |
| `TestSchemaRetrieval` | 4 | MemoryManager recall, empty memory, schema storage after finalize, fingerprint comparison |
| `TestDBMigrationsNode` | 6 | Node returns valid state, no backend code → None, no schema → None, identical schema → None, new table generates CREATE TABLE, modified column generates ALTER TABLE |
| `TestEdgeCases` | 3 | Schema with no tables, malformed Prisma input, concurrent column add + remove |
| **Total** | **35** | |

---

### 2.5.3 Updated Pipeline — 10 Nodes

After Phase 2.5, the LangGraph pipeline has 10 nodes (8 from Phase 2.0 + 2 new):

```
┌─────────┐     ┌──────────┐     ┌─────────┐
│architect │────►│frontend  │────►│         │
│          │     │          │     │aggre-   │
│          │────►│backend   │────►│gator    │
└─────────┘     └──────────┘     └────┬────┘
                                      │
                                      ▼
                               ┌──────────────┐
                               │guardrails    │
                               │    gate      │
                               └──────┬───────┘
                                      │ (clean code)
                                      ▼
                               ┌──────────────┐
                               │db_migrations │  ◄── NEW (IE-5)
                               │              │
                               └──────┬───────┘
                                      │
                                      ▼
                               ┌──────────────┐
                               │   tester     │
                               └──────┬───────┘
                                      │
                                      ▼
                               ┌──────────────┐
                               │ visual_qa    │  ◄── NEW (IE-4)
                               └──────┬───────┘
                                      │
                                      ▼
                               ┌──────────────┐
                               │  reviewer    │
                               └──────┬───────┘
                    ┌──────────────────┤
                    │ (critical issues)│ (clean / finalize)
                    ▼                  ▼
              ┌──────────┐      ┌──────────┐
              │ frontend │      │ finalize │───► END
              │ (retry)  │      │          │
              └──────────┘      └──────────┘
```

**Node execution order**:
1. `architect` — System design
2. `frontend` / `backend` — Code generation (parallel)
3. `aggregator` — Merge parallel results
4. `guardrails_gate` — IE-1 architectural enforcement
5. `db_migrations` — **NEW** IE-5 schema diff + migration generation
6. `tester` — Test generation
7. `visual_qa` — **NEW** IE-4 screenshot-based visual testing
8. `reviewer` — Code review (sees guardrails + visual QA + test results)
9. `finalize` — Package project + store schema in memory

**Edge definitions** (complete, Phase 2.5):

```python
# Parallel mode:
graph.add_edge(START, "architect")
graph.add_conditional_edges("architect", self._dispatch_parallel, ["frontend", "backend", "finalize"])
graph.add_edge("frontend", "aggregator")
graph.add_edge("backend", "aggregator")
graph.add_edge("aggregator", "guardrails_gate")
graph.add_conditional_edges("guardrails_gate", self._route_after_guardrails,
                           ["db_migrations", "frontend"])  # CHANGED: tester → db_migrations
graph.add_edge("db_migrations", "tester")                   # NEW
graph.add_edge("tester", "visual_qa")                        # CHANGED: was tester → reviewer
graph.add_edge("visual_qa", "reviewer")                      # NEW
graph.add_conditional_edges("reviewer", self._route_after_review, ["frontend", "finalize"])
graph.add_edge("finalize", END)

# Sequential mode:
graph.add_edge(START, "architect")
graph.add_conditional_edges("architect", self._route_after_architect, ["frontend", "finalize"])
graph.add_conditional_edges("frontend", self._route_after_frontend, ["backend", "tester"])
graph.add_edge("backend", "guardrails_gate")
graph.add_conditional_edges("guardrails_gate", self._route_after_guardrails,
                           ["db_migrations", "frontend"])  # CHANGED
graph.add_edge("db_migrations", "tester")                   # NEW
graph.add_edge("tester", "visual_qa")                        # CHANGED
graph.add_edge("visual_qa", "reviewer")                      # NEW
graph.add_conditional_edges("reviewer", self._route_after_review, ["frontend", "finalize"])
graph.add_edge("finalize", END)
```

**ProjectState additions** (4 new fields):

```python
class ProjectState(TypedDict):
    # ... existing 24 fields from Phase 2.0 ...

    # Phase 2.5 — IE-4 Visual QA
    visual_qa_report: Optional[Dict[str, Any]]
    visual_qa_screenshots: Optional[int]

    # Phase 2.5 — IE-5 Schema Migrations
    schema_migration: Optional[Dict[str, Any]]
    previous_schema_fingerprint: Optional[str]
```

---

### 2.5.4 Phase 2.5 File Manifest (5 new, 1 modified)

| File | Action | Subsystem |
|------|--------|-----------|
| `backend/ai/agents/visual_qa.py` | NEW | IE-4 Visual QA |
| `backend/ai/agents/db_architect.py` | NEW | IE-5 Schema Migrations |
| `backend/ai/agents/multi_agent.py` | MODIFY | Both (new nodes, new edges, new ProjectState fields) |
| `backend/tests/test_visual_qa.py` | NEW | IE-4 tests |
| `backend/tests/test_db_architect.py` | NEW | IE-5 tests |

---

### 2.5.5 Phase 2.5 — Expert Acceptance Criteria

> **Gate**: ALL BLOCKER criteria must pass. HIGH criteria should pass with documented rationale for exceptions.

#### AC-11: Visual QA Agent (IE-4)

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-11.1 | Route detection finds Next.js pages | Provide frontend_files with `app/page.tsx`, `app/dashboard/page.tsx`, `app/settings/page.tsx` | `detect_routes()` returns `["/", "/dashboard", "/settings"]` | **BLOCKER** |
| AC-11.2 | VisualQAReport serializable | Create report with 3 issues across 2 viewports | `report.to_dict()` returns valid JSON. `json.dumps()` succeeds. | **BLOCKER** |
| AC-11.3 | `has_critical` property correct | Report with 1 CRITICAL + 2 HIGH issues | `report.has_critical == True`. Report with only HIGH issues: `report.has_critical == False`. | HIGH |
| AC-11.4 | Visual QA node handles empty frontend code | Run `_visual_qa_node` with `frontend_code={}` | Returns `visual_qa_report` with `screenshots_analyzed=0`. No exception raised. | **BLOCKER** |
| AC-11.5 | Visual QA node handles Playwright failure | Mock Playwright to raise `BrowserNotInstalled` | Returns report with `VQA_ERROR` issue. Pipeline continues. No crash. | **BLOCKER** |
| AC-11.6 | Visual QA node handles LLM failure | Mock LLM to raise `APIError` | Returns report with screenshots count > 0 but empty issues. Pipeline continues. | HIGH |
| AC-11.7 | Reviewer receives visual QA context | Complete build where visual QA finds 2 CRITICAL issues | `_reviewer_node` messages contain `"VISUAL QA ISSUES"` system message with issue IDs and fix suggestions. | **BLOCKER** |
| AC-11.8 | Multimodal prompt includes viewport and route | Generate prompt for tablet viewport on `/dashboard` route | Prompt contains `"768×1024"` and `"/dashboard"`. | HIGH |
| AC-11.9 | Max screenshot cap enforced | Provide 10 routes × 3 viewports = 30 screenshots | Only `MAX_SCREENSHOTS` (15) captured. Report notes truncation. | HIGH |
| AC-11.10 | Pipeline edge: tester → visual_qa → reviewer | Inspect graph edges after `_build_graph()` | `"tester"` connects to `"visual_qa"`. `"visual_qa"` connects to `"reviewer"`. No direct `"tester" → "reviewer"` edge. | **BLOCKER** |

#### AC-12: Semantic Schema Migrations (IE-5)

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-12.1 | Prisma schema parsed correctly | Provide Prisma schema with 3 models, 2 indexes, 1 relation | `parse_prisma()` returns `Schema` with 3 tables, correct columns and indexes | **BLOCKER** |
| AC-12.2 | SQLAlchemy models parsed correctly | Provide Python file with 2 SQLAlchemy models | `parse_sqlalchemy()` returns `Schema` with 2 tables, correct column types | **BLOCKER** |
| AC-12.3 | Schema diff detects added table | Old schema: 2 tables. New schema: 3 tables (1 added). | `diff_schemas()` returns 1 diff with `diff_type=TABLE_ADDED` | **BLOCKER** |
| AC-12.4 | Schema diff flags data loss for removed column | Old schema: `users` with `email` column. New schema: `users` without `email`. | Diff has `data_loss_risk=True`, `risk_description` mentions "dropped" | **BLOCKER** |
| AC-12.5 | Migration generates ALTER TABLE (not DROP+CREATE) | Diff with 1 added column (`avatar_url`) | Migration SQL contains `ALTER TABLE users ADD COLUMN avatar_url TEXT`. Does NOT contain `DROP TABLE` or `CREATE TABLE users`. | **BLOCKER** |
| AC-12.6 | Rollback script is inverse of migration | Generate migration for added column | Rollback SQL contains `ALTER TABLE users DROP COLUMN avatar_url` | **BLOCKER** |
| AC-12.7 | Identical schemas produce no migration | Same schema for old and new | `diff_schemas()` returns empty list. `generate_migration()` returns `None`. | **BLOCKER** |
| AC-12.8 | Migration files added to backend_code | Run `_db_migrations_node` with schema change | `state["backend_code"]` contains new key matching `migrations/*.sql` with valid SQL content | HIGH |
| AC-12.9 | Schema stored in MemoryManager after build | Complete a build with backend models | `MemoryManager.store()` called with `namespace=f"schema:{project_id}"`. Content is valid JSON schema. | HIGH |
| AC-12.10 | Schema fingerprint enables quick equality check | Two identical schemas | `schema1.fingerprint() == schema2.fingerprint()`. Two different schemas: fingerprints differ. | HIGH |
| AC-12.11 | DB migrations node handles no backend code | Run `_db_migrations_node` with `backend_code={}` | Returns `schema_migration=None`. No exception. | **BLOCKER** |
| AC-12.12 | Pipeline edge: guardrails_gate → db_migrations → tester | Inspect graph edges after `_build_graph()` | `_route_after_guardrails` returns `"db_migrations"` (not `"tester"`) when clean. `"db_migrations"` connects to `"tester"`. | **BLOCKER** |

#### Severity Definitions (same as Phase 1.0 and 2.0)

| Severity | Definition | Gate Rule |
|----------|-----------|-----------|
| **BLOCKER** | Phase cannot be approved if this fails | Must pass |
| HIGH | Significant functionality gap, but workaround exists | Should pass, exceptions require documented rationale |

---

### 2.5.6 Phase 2.5 — Dependency Map

```
┌──────────────────────┐
│  IE-4 Visual QA      │◄─── Depends on: Playwright (pip install playwright)
│  visual_qa.py        │◄─── Depends on: Multimodal LLM (Claude Opus Vision)
│                      │◄─── Depends on: ReviewFinding/ReviewSeverity ✅ exists
│                      │◄─── Depends on: @monitor_node decorator ✅ exists
│                      │───► Modifies: multi_agent.py (new node + edges)
│                      │───► Extends: _reviewer_node (visual context injection)
└──────────────────────┘

┌──────────────────────┐
│  IE-5 DB Architect   │◄─── Depends on: MemoryManager.store/search ✅ exists
│  db_architect.py     │◄─── Depends on: @monitor_node decorator ✅ exists
│                      │◄─── Depends on: contextSnapshots Convex table ✅ Phase 1.0
│                      │───► Modifies: multi_agent.py (new node + edges)
│                      │───► Modifies: _route_after_guardrails (return "db_migrations")
│                      │───► Modifies: _finalize_node (schema storage)
└──────────────────────┘
```

**Implementation order** (recommended):
1. **IE-5 DB Architect** — modifies pipeline routing (guardrails → db_migrations → tester), purely backend. Testable in isolation.
2. **IE-4 Visual QA** — depends on pipeline edges being correct (tester → visual_qa → reviewer), needs Playwright mock. Testable in isolation.

Both subsystems are **non-blocking** — they enhance the pipeline without changing the failure behavior. If either subsystem encounters an error, it returns empty/partial results and the pipeline continues.

---

## Phase 3.0: Production Features — "From IDE to Product"

> **Document Status**: Phase 3.0 DETAILED
> **Last Updated**: 2026-03-28
> **Prerequisite**: Phase 2.5 COMPLETE (2250/2250 tests PASS)
> **Planning Document**: `docs/PHASE_3_PLAN.md` — full implementation & verification plan
> **Approval Workflow**: Requires explicit "PHASE 3.0 APPROVED" before implementation begins.

### 3.0.0 Scope Overview

Phase 3.0 transforms VBuilder from an AI code generator into a production-ready IDE. Four subsystems address the gap between "code is generated" and "product is deployable":

| # | Subsystem | Problem Solved | Key Deliverables |
|---|-----------|---------------|-----------------|
| 3.1 | **Pre-Tested Auth Module** | AI generates broken auth 60%+ of the time | Template library (Clerk/NextAuth/JWT), Architect detection, auth tests |
| 3.2 | **Debugging Tools** | No competitor has real debugging | Sandpack console capture, error overlay, AI error analysis endpoint |
| 3.3 | **Code Export & Zero Lock-in** | Competitors lock users in with vendor deps | ZIP/GitHub/Vercel/Railway export, standard project output |
| 3.4 | **Real-Time Collaboration** | Bolt/v0 are solo tools | Yjs CRDT + Convex Presence, cursor sharing, team roles |

**Pipeline Change**: Phase 3.0 does **not** modify the 10-node LangGraph pipeline. All changes are **pre-pipeline** (auth template injection into existing nodes) and **post-pipeline** (export, debug, collab).

```
PIPELINE (unchanged from Phase 2.5):
  architect → [frontend || backend] → aggregator → guardrails_gate
    → db_migrations → tester → visual_qa → reviewer → finalize

PHASE 3.0 ADDITIONS (outside pipeline):
  PRE-PIPELINE:  Auth template detection in _architect_node → injection in frontend/backend nodes
  POST-PIPELINE: Export endpoints, debug routes, Yjs collaboration layer
```

### 3.0.1 Existing Infrastructure Leveraged

| Component | File | Status | Phase 3.0 Usage |
|-----------|------|--------|-----------------|
| Clerk JWT validation (3-strategy) | `backend/middleware/auth.py` (425 lines) | PRODUCTION | Auth templates share validation pattern |
| Convex HTTP client | `backend/db/convex.py` | PRODUCTION | Export stores artifacts; collab uses Convex presence |
| Collab service (basic invite) | `backend/services/collab_service.py` (108 lines) | PARTIAL | Extended with Yjs awareness |
| Team routes | `backend/api/team_routes.py` | EXISTS (not mounted) | Extended with role enforcement |
| Sandpack LivePreview | `frontend/components/preview/LivePreview.tsx` | PRODUCTION | Console capture hooks into Sandpack iframe |
| GitHub sync routes | `backend/api/github_sync_routes.py` | EXISTS | Export to GitHub reuses patterns |
| `_finalize_node` | `multi_agent.py:926-950` | PRODUCTION | Modified to produce standard output |
| `_architect_node` | `multi_agent.py:720-723` | PRODUCTION | Extended with auth detection |

---

### 3.1 Pre-Tested Auth Module

#### 3.1.1 Problem Statement

Auth is the #1 source of broken builds across all AI builders. When users request "build me a todo app with login," the AI generates fresh auth code every time — OAuth callbacks misconfigured, JWT refresh missing, password hashing weak. VBuilder already has Clerk JWT validation working. The fix: **inject pre-tested templates, don't generate**.

#### 3.1.2 Template Library Structure

```
backend/templates/auth/
├── clerk/
│   ├── frontend/
│   │   ├── components/SignIn.tsx
│   │   ├── components/SignUp.tsx
│   │   ├── components/UserButton.tsx
│   │   ├── middleware.ts
│   │   └── app/sign-in/page.tsx
│   └── backend/
│       ├── middleware.py
│       └── routes/auth.py
├── nextauth/
│   ├── frontend/
│   │   ├── app/api/auth/[...nextauth]/route.ts
│   │   ├── lib/auth.ts
│   │   └── components/AuthProvider.tsx
│   └── backend/
│       ├── middleware.py
│       └── routes/auth.py
├── custom_jwt/
│   ├── frontend/
│   │   ├── components/LoginForm.tsx
│   │   ├── components/RegisterForm.tsx
│   │   ├── lib/auth-client.ts
│   │   └── hooks/useAuth.ts
│   └── backend/
│       ├── middleware.py
│       ├── routes/auth.py
│       ├── models/user.py
│       └── utils/tokens.py
└── manifest.json
```

#### 3.1.3 Architect Selection Logic

```python
AUTH_KEYWORDS = {
    "clerk": ["clerk", "clerk.com", "clerkprovider"],
    "nextauth": ["nextauth", "next-auth", "authjs"],
    "custom_jwt": ["login", "register", "signup", "authentication", "password", "auth", "user account"],
}

def detect_auth_strategy(requirements: str, architecture: Dict) -> Optional[str]:
    req_lower = requirements.lower()
    for strategy, keywords in AUTH_KEYWORDS.items():
        if any(kw in req_lower for kw in keywords[:3]):
            return strategy
    if any(kw in req_lower for kw in AUTH_KEYWORDS["custom_jwt"]):
        return "custom_jwt"
    return None
```

Detection runs inside `_architect_node`. Result stored as `architecture["auth_strategy"]`. Frontend/Backend nodes receive template files as mandatory includes. Tester node gets auth-specific test scenarios from `manifest.json`.

#### 3.1.4 Template Manifest Schema

```json
{
  "name": "custom_jwt",
  "display_name": "Custom JWT Authentication",
  "files": {
    "frontend": [
      {"src": "components/LoginForm.tsx", "dest": "src/components/auth/LoginForm.tsx"}
    ],
    "backend": [
      {"src": "routes/auth.py", "dest": "routes/auth.py"}
    ]
  },
  "dependencies": {
    "frontend": {"@clerk/nextjs": "^5.0.0"},
    "backend": {"pyjwt": "^2.8.0", "bcrypt": "^4.1.0"}
  },
  "env_vars": ["JWT_SECRET", "JWT_REFRESH_SECRET"],
  "test_scenarios": [
    "Login with valid credentials returns JWT token",
    "Protected route without token returns 401"
  ]
}
```

#### 3.1.5 New Files

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `backend/templates/auth/clerk/` | ~400 | Pre-tested Clerk auth template |
| `backend/templates/auth/nextauth/` | ~350 | Pre-tested NextAuth template |
| `backend/templates/auth/custom_jwt/` | ~500 | Pre-tested JWT auth template |
| `backend/templates/auth/manifest.json` | ~80 | Template metadata + test scenarios |
| `backend/ai/agents/auth_injector.py` | ~200 | Template loading + injection logic |
| `multi_agent.py` | +30 lines | Auth detection in `_architect_node` |

---

### 3.2 Debugging Tools

#### 3.2.1 Problem Statement

When generated code fails, users have zero debugging capability — they type "fix this" into chat, often triggering error loops (mitigated by Phase 2.0, not eliminated). No competitor offers console capture, source-mapped stack traces, or AI-assisted root cause analysis.

#### 3.2.2 Console Capture Architecture

```
Sandpack iframe (renders generated app)
       ↓ postMessage / useSandpackConsole()
useConsoleCapture hook:
  - Captures console.log/error/warn
  - Parses stack traces → extracts file:line:column
  - Maps Sandpack virtual paths → project file paths
  - Stores in circular buffer (max 500 entries)
       ↓
ConsolePanel component:
  - Displays log entries with severity icons
  - Click error → jump to file:line in Monaco
       ↓
ErrorOverlay component:
  - Red underline + hover tooltip in Monaco at error line
  - "Send to AI" button → pre-fills chat with error context
       ↓
AIDebugPanel component (on user click):
  - POST /api/v1/debug/analyze-error
  - Receives: root_cause, suggested_fix, confidence
  - "Apply Fix" button applies code change
```

#### 3.2.3 AI Error Analysis Endpoint

```python
# POST /api/v1/debug/analyze-error
# Input: error_message, stack_trace, relevant_code (Dict[str, str])
# Output: {root_cause, explanation, suggested_fix: {file, line, before, after}, confidence}
# Model: researcher role (Gemini 3.1 Pro) for cost efficiency
```

#### 3.2.4 New Files

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `frontend/hooks/useConsoleCapture.ts` | ~120 | Sandpack console hook |
| `frontend/components/debug/ConsolePanel.tsx` | ~180 | Console log viewer |
| `frontend/components/debug/ErrorOverlay.tsx` | ~150 | Monaco inline error annotations |
| `frontend/components/debug/AIDebugPanel.tsx` | ~200 | AI analysis result display |
| `backend/api/debug_routes.py` | ~150 | `/api/v1/debug/analyze-error` endpoint |
| `LivePreview.tsx` | +20 lines | Add console capture integration |

---

### 3.3 Code Export & Zero Lock-in

#### 3.3.1 Problem Statement

Generated projects must be self-contained, standard, and deployable without VBuilder. The `_finalize_node` is modified to always produce deployment-ready output.

#### 3.3.2 Standard Output Files

Every project includes (generated automatically if not present):

| File | Purpose |
|------|---------|
| `package.json` | Standard deps from tech stack + auth template |
| `tsconfig.json` | Standard TypeScript configuration |
| `.env.example` | All required env vars with descriptions |
| `Dockerfile` | Multi-stage production build |
| `docker-compose.yml` | App + DB + Redis (if applicable) |
| `.dockerignore` / `.gitignore` | Standard ignore patterns |
| `README.md` | Auto-generated setup/run/deploy instructions |

**Zero Lock-in Rule**: No exported file may import from `@vbuilder/*`, `vbuilder-*`, or reference VBuilder infrastructure.

#### 3.3.3 Export Endpoints

```python
# backend/api/export_routes.py

POST /api/v1/projects/{id}/export/zip      # Download project as ZIP
POST /api/v1/projects/{id}/export/github    # Push to GitHub (OAuth token from Clerk)
POST /api/v1/projects/{id}/deploy/vercel    # One-click Vercel deploy
POST /api/v1/projects/{id}/deploy/railway   # One-click Railway deploy
```

- **ZIP**: Collect files from Convex → add standard files → stream ZIP response
- **GitHub**: Uses GitHub Contents API (<20 files) or Git Data API (>20 files)
- **Vercel/Railway**: Deploy via platform APIs, return deployment URL

#### 3.3.4 CI/CD Autopilot Agent

**Problem**: Exported projects ship without CI/CD. Users must manually set up GitHub Actions and configure secrets. Naive workflows pull actions by mutable tag (e.g., `actions/checkout@v4`), which are vulnerable to supply chain attacks (cf. tj-actions/changed-files incident, March 2025).

**Solution**: Generate a **hardened `.github/workflows/deploy.yml`** automatically during export.

##### SEC-EXP-01: Full Commit SHA Pinning

All third-party GitHub Actions MUST be pinned to a full 40-character commit SHA:

```yaml
# ✅ CORRECT — immutable, auditable
- uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11  # v4.1.1

# ❌ WRONG — mutable tag, supply chain risk
- uses: actions/checkout@v4
```

`export_service.py` maintains a `PINNED_ACTIONS` registry. Any action not in the registry raises `ValueError` — no unpinned actions can slip through.

##### Default Permissions: Least Privilege

Every generated workflow sets:

```yaml
permissions:
  contents: read
```

Job-level overrides added only when required (e.g., `packages: write` for Docker push).

##### Environment Variable Rehydration

The CI/CD agent scans `.env.example` and classifies variables:

| Pattern | Classification | GitHub Mapping |
|---------|---------------|----------------|
| `NEXT_PUBLIC_*` | Non-secret (visible in logs) | `${{ vars.KEY }}` (GitHub Variable) |
| `*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD` | Sensitive | `${{ secrets.KEY }}` (GitHub Secret) |
| Everything else | Sensitive (safe default) | `${{ secrets.KEY }}` (GitHub Secret) |

Output:
- `.github/workflows/deploy.yml` — SHA-pinned, least-privilege, env-rehydrated
- `SECRETS_SETUP.md` — step-by-step guide for configuring GitHub Secrets/Variables

##### Workflow Adaptation

The generated workflow adapts based on `architecture.deploy_target`:
- **Vercel**: Build + Vercel CLI deploy (no Docker)
- **Railway**: Docker build + Railway CLI
- **Docker**: Docker build + push to GHCR
- **Fly.io**: Docker build + `flyctl deploy`

#### 3.3.5 New Files

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `backend/api/export_routes.py` | ~350 | Export/deploy endpoints |
| `backend/services/export_service.py` | ~400 | Standard output + CI/CD Autopilot + SEC-EXP-01 + env rehydration |
| `backend/templates/deployment/` | ~250 | Dockerfile, compose, deploy.yml, secrets_setup, README templates |
| `frontend/components/export/ExportPanel.tsx` | ~250 | Export/deploy modal UI |
| `multi_agent.py` | +40 lines | `_finalize_node` adds standard files |

---

### 3.4 Real-Time Collaboration

#### 3.4.1 Problem Statement

AI builders are solo tools. VBuilder has basic invite/collab infrastructure but no real-time sync. Phase 3.4 adds Yjs CRDT + Convex Presence for cursor sharing, concurrent editing, and team roles.

#### 3.4.2 Architecture: Yjs + Convex

```
User A (Monaco + y-monaco)  ←→  Convex (yjsUpdates/yjsDocuments)  ←→  User B (Monaco + y-monaco)
                                       ↓
                              Convex presence table
                             (cursor position, user color)
```

**Why Yjs + Convex**:
1. Convex already deployed with `useQuery` reactivity — no WebSocket server needed
2. Yjs CRDTs guarantee eventual consistency without custom merge logic
3. Yjs state stored in Convex, survives page reloads and reconnects
4. Offline support via Yjs local queue

#### 3.4.3 Convex Schema Extension

Three new tables:

```typescript
yjsDocuments: defineTable({
  projectId: v.id("projects"),
  filePath: v.string(),
  yjsState: v.bytes(),
  lastModifiedBy: v.string(),
  updatedAt: v.number(),
}).index("by_project_file", ["projectId", "filePath"]),

yjsUpdates: defineTable({
  projectId: v.id("projects"),
  filePath: v.string(),
  update: v.bytes(),
  userId: v.string(),
  createdAt: v.number(),
}).index("by_project_file_time", ["projectId", "filePath", "createdAt"]),

presence: defineTable({
  projectId: v.id("projects"),
  userId: v.string(),
  userName: v.string(),
  userColor: v.string(),
  currentFile: v.optional(v.string()),
  cursorPosition: v.optional(v.object({ line: v.number(), column: v.number() })),
  selection: v.optional(v.object({
    startLine: v.number(), startColumn: v.number(),
    endLine: v.number(), endColumn: v.number(),
  })),
  lastSeen: v.number(),
}).index("by_project", ["projectId"]),
```

#### 3.4.4 Sync Flow

1. User types → y-monaco captures as Yjs update (binary)
2. Hook sends update to Convex mutation `yjsUpdates:push`
3. Convex broadcasts to all subscribers via `useQuery`
4. Other users' Y.Doc apply update → Monaco reflects change
5. Every 100 updates: compact into single `yjsDocuments` entry

#### 3.4.5 Team Roles

| Role | Permissions |
|------|------------|
| Owner | Full access + billing + delete + manage team |
| Editor | Edit code, trigger builds, export, chat |
| Viewer | View code, preview, comment — no edit/build |

Enforced via `backend/middleware/team_auth.py` with role hierarchy check.

#### 3.4.6 New Files

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `frontend/hooks/useCollaborativeEditor.ts` | ~200 | Yjs + y-monaco integration |
| `frontend/hooks/usePresence.ts` | ~120 | Convex Presence cursor sharing |
| `frontend/components/collab/PeerCursors.tsx` | ~150 | Colored cursor decorations |
| `frontend/convex/yjsDocuments.ts` | ~100 | Convex mutations/queries for Yjs state |
| `frontend/convex/yjsUpdates.ts` | ~80 | Convex mutations/queries for updates |
| `frontend/convex/presence.ts` | ~80 | Convex mutations/queries for cursors |
| `frontend/convex/schema.ts` | +40 lines | 3 new tables |
| `backend/middleware/team_auth.py` | ~100 | Role-based project access |
| `backend/api/team_routes.py` | +50 lines | Role enforcement |
| `backend/services/collab_service.py` | +80 lines | Yjs compaction logic |

**Frontend dependencies**: `yjs@^13.6.0`, `y-monaco@^0.1.6`, `y-protocols@^1.0.6`

---

### 3.0.5 Test Strategy Summary

| Category | File | Tests (est.) |
|----------|------|-------------|
| Unit: Auth Templates | `test_auth_templates.py` | 13 |
| Unit: Debug Tools | `test_debug_tools.py` | 8 |
| Unit: Export | `test_export.py` | 22 |
| Unit: Collaboration | `test_collaboration.py` | 12 |
| Integration: Auth Pipeline | `test_auth_integration.py` | 5 |
| Integration: SOS→Resolve | `test_sos_to_resolve.py` | 5 |
| Integration: Debug Loop | `test_debug_integration.py` | 3 |
| Integration: Export Pipeline | `test_export_integration.py` | 4 |
| E2E: Build→Export→Run | `test_e2e_export.py` | 6 |
| Concurrency: Multi-User | `test_concurrency_collab.py` | 10 |
| **Total** | **10 files** | **~89** |

Full plan with detailed test cases: `docs/PHASE_3_PLAN.md`

---

### 3.0.6 Acceptance Criteria Summary

| AC Group | Subsystem | BLOCKERs | HIGHs |
|----------|-----------|----------|-------|
| AC-11 | Auth Module | 5 | 2 |
| AC-12 | Debugging | 5 | 2 |
| AC-13 | Export (incl. CI/CD Autopilot, SEC-EXP-01, Env Rehydration) | 7 | 3 |
| AC-14 | Collaboration | 5 | 3 |
| **Total** | | **20** | **10** |

Full acceptance criteria with IDs: `docs/PHASE_3_PLAN.md`

---

### 3.0.7 Implementation Order

```
1. CODE EXPORT (3.3)    — No pipeline changes, standalone endpoints
2. AUTH MODULE (3.1)    — Modifies _architect_node, adds template injection
3. DEBUGGING (3.2)      — Frontend-heavy, standalone backend endpoint
4. COLLABORATION (3.4)  — Highest complexity, requires Convex schema + Yjs
```

### 3.0.8 Dependency Graph

```
┌────────────────────────────────────────────────────────┐
│                                                        │
│  Export (3.3)        Auth (3.1)       Debug (3.2)       │
│  ─ No deps          ─ No deps       ─ Sandpack ✅      │
│  ─ Standalone        ─ Standalone    ─ Standalone       │
│                                                        │
│         ↓ (tests auth export)                          │
│                                                        │
│                  Collaboration (3.4)                    │
│                  ─ Depends on: team_routes (exists)    │
│                  ─ Depends on: Convex schema changes   │
│                  ─ Risk: CRDT + real-time sync          │
│                                                        │
└────────────────────────────────────────────────────────┘
```

### 3.0.9 File Manifest (Phase 3.0)

| File | Action | Subsystem |
|------|--------|-----------|
| `backend/templates/auth/clerk/` | NEW dir | Auth |
| `backend/templates/auth/nextauth/` | NEW dir | Auth |
| `backend/templates/auth/custom_jwt/` | NEW dir | Auth |
| `backend/templates/auth/manifest.json` | NEW | Auth |
| `backend/ai/agents/auth_injector.py` | NEW | Auth |
| `backend/api/debug_routes.py` | NEW | Debug |
| `backend/api/export_routes.py` | NEW | Export |
| `backend/services/export_service.py` | NEW (incl. CI/CD Autopilot + SEC-EXP-01 + env rehydration) | Export |
| `backend/templates/deployment/` | NEW dir (Dockerfile, compose, deploy.yml, secrets_setup, README) | Export |
| `backend/middleware/team_auth.py` | NEW | Collab |
| `frontend/hooks/useConsoleCapture.ts` | NEW | Debug |
| `frontend/hooks/useCollaborativeEditor.ts` | NEW | Collab |
| `frontend/hooks/usePresence.ts` | NEW | Collab |
| `frontend/components/debug/ConsolePanel.tsx` | NEW | Debug |
| `frontend/components/debug/ErrorOverlay.tsx` | NEW | Debug |
| `frontend/components/debug/AIDebugPanel.tsx` | NEW | Debug |
| `frontend/components/export/ExportPanel.tsx` | NEW | Export |
| `frontend/components/collab/PeerCursors.tsx` | NEW | Collab |
| `frontend/convex/yjsDocuments.ts` | NEW | Collab |
| `frontend/convex/yjsUpdates.ts` | NEW | Collab |
| `frontend/convex/presence.ts` | NEW | Collab |
| `frontend/convex/schema.ts` | MODIFY | Collab |
| `backend/ai/agents/multi_agent.py` | MODIFY | Auth + Export |
| `backend/api/team_routes.py` | MODIFY | Collab |
| `backend/services/collab_service.py` | MODIFY | Collab |
| `frontend/components/preview/LivePreview.tsx` | MODIFY | Debug |

**Total**: 21 new files + 5 modified files

---

## Phase 3.5: Creative Power — "Upload a Screenshot, Get an App"

> **Document Status**: Phase 3.5 DETAILED
> **Last Updated**: 2026-03-30
> **Prerequisite**: Phase 3.0 COMPLETE (2633/2633 tests PASS)
> **Approval Workflow**: Requires explicit "PHASE 3.5 APPROVED" before implementation begins.

### 3.5.0 Scope Overview

Phase 3.5 adds multimodal input to VBuilder. Users upload a screenshot, wireframe, Figma export, or describe a visual style — and the pipeline generates code that matches the visual intent. This addresses a universal pain point: **describing complex UI in text is hard**.

| # | Subsystem | Problem Solved | Key Deliverables |
|---|-----------|---------------|-----------------|
| 3.5.1 | **Vision Agent** | Describing complex UI in text is imprecise | Multimodal LLM analysis → structured layout, component list, color palette, typography |
| 3.5.2 | **Theme Engine** | Generated code has inconsistent styling | Tailwind config + CSS variables from visual analysis or style descriptors |
| 3.5.3 | **Vision Node (Pipeline)** | No way to feed visual context into the build | Alternative entry point: vision_node → architect with enriched state |

**Pipeline Change**: Phase 3.5 adds a **conditional entry path** — the Vision Node runs BEFORE Architect when image data is present in the initial state. When no image is provided, the pipeline is unchanged.

```
WITHOUT IMAGE (unchanged — 10-node pipeline from Phase 2.5):
  START → architect → [frontend || backend] → aggregator → guardrails_gate
    → db_migrations → tester → visual_qa → reviewer → finalize

WITH IMAGE (new — 11-node pipeline):
  START → vision_node → architect → [frontend || backend] → aggregator
    → guardrails_gate → db_migrations → tester → visual_qa → reviewer → finalize
```

The Vision Node is a **pre-processing step**, not a replacement for Architect. It extracts structured information from the image and injects it as additional context for downstream agents. The Architect still designs the architecture; the Frontend agent receives both the architecture AND the vision analysis to generate visually accurate code.

### 3.5.1 Existing Infrastructure Leveraged

| Component | File | Status | Phase 3.5 Usage |
|-----------|------|--------|-----------------|
| Visual QA multimodal prompts | `backend/ai/agents/visual_qa.py` | PRODUCTION | Reuse prompt engineering patterns for image analysis |
| `@monitor_node` decorator | `backend/ai/agents/multi_agent.py:53-76` | PRODUCTION | Applied to new vision_node |
| `assign_model()` | `backend/src/efficiency.py` | PRODUCTION | Route vision analysis to Claude Opus 4.6 (reviewer role, multimodal) |
| `ProjectState` TypedDict | `backend/ai/agents/multi_agent.py:226-279` | PRODUCTION | Extended with 3 new fields |
| Chat API (existing) | `backend/api/chat_routes.py` | PRODUCTION | Extended to accept image upload via multipart |
| `_build_graph()` | `backend/ai/agents/multi_agent.py:640-727` | PRODUCTION | Modified: conditional START → vision_node edge |

---

### 3.5.2 Vision Agent — `backend/ai/agents/vision_agent.py`

#### 3.5.2.1 Purpose

The Vision Agent receives a base64-encoded image (screenshot, wireframe, sketch, or Figma export) and produces a structured `VisionAnalysis` that downstream agents consume. It does NOT generate code — it generates a **structured description** that the Frontend agent uses alongside the architecture spec.

#### 3.5.2.2 Data Structures

```python
@dataclass
class LayoutRegion:
    """A rectangular region detected in the image."""
    role: str           # "header", "sidebar", "main", "footer", "card", "hero", "nav", "form"
    bounds: Dict[str, float]  # {"x": 0.0, "y": 0.0, "width": 1.0, "height": 0.08} — normalized 0-1
    children: List[str]       # IDs of nested regions
    css_hint: str             # e.g. "sticky top-0", "grid grid-cols-3 gap-4"

@dataclass
class DetectedComponent:
    """A UI component detected in the image."""
    type: str           # "button", "input", "card", "table", "image", "icon", "nav-link",
                        # "dropdown", "modal", "tabs", "avatar", "badge", "chart"
    label: str          # Visible text or inferred purpose: "Submit", "Search bar", "User avatar"
    region_id: str      # Which LayoutRegion contains this component
    props_hint: Dict[str, str]  # e.g. {"variant": "primary", "size": "lg", "icon": "search"}
    interactive: bool   # True for buttons, inputs, links; False for static text, images

@dataclass
class ColorPalette:
    """Extracted color scheme from the image."""
    primary: str        # Hex: "#3B82F6"
    secondary: str      # Hex: "#10B981"
    accent: str         # Hex: "#F59E0B"
    background: str     # Hex: "#FFFFFF"
    surface: str        # Hex: "#F3F4F6"
    text_primary: str   # Hex: "#111827"
    text_secondary: str # Hex: "#6B7280"
    border: str         # Hex: "#E5E7EB"
    error: str          # Hex: "#EF4444"
    success: str        # Hex: "#10B981"

@dataclass
class Typography:
    """Detected typography system."""
    heading_font: str     # "Inter", "SF Pro", "system-ui" — inferred from visual style
    body_font: str        # "Inter", "Georgia", "system-ui"
    heading_weight: str   # "bold", "semibold", "extrabold"
    base_size_px: int     # 14, 16, 18 — inferred from relative text size
    scale_ratio: float    # 1.25 (Major Third), 1.333 (Perfect Fourth), etc.
    line_height: float    # 1.5, 1.6, 1.75

@dataclass
class VisionAnalysis:
    """Complete structured analysis of an uploaded design image."""
    layout_regions: List[LayoutRegion]
    components: List[DetectedComponent]
    palette: ColorPalette
    typography: Typography
    overall_style: str          # "minimal", "corporate", "playful", "dark-mode", "glassmorphism",
                                 # "brutalist", "apple-like", "material-design", "cyberpunk"
    responsive_hints: Dict[str, str]  # {"mobile": "stack vertically, hide sidebar",
                                       #  "tablet": "2-col grid, collapsible nav"}
    page_type: str              # "landing", "dashboard", "form", "settings", "profile", "list"
    confidence: float           # 0.0 - 1.0, overall analysis confidence
```

#### 3.5.2.3 Core Methods

```python
class VisionAgent:
    """Multimodal analysis of design screenshots/wireframes."""

    MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB limit
    SUPPORTED_MIME = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}

    def __init__(self, llm_provider: str = "auto"):
        self.llm_provider = llm_provider

    def validate_image(self, image_b64: str) -> Tuple[bytes, str]:
        """Decode base64, validate size and MIME type. Returns (raw_bytes, mime_type)."""

    async def analyze(self, image_b64: str, requirements: str = "") -> VisionAnalysis:
        """
        Main entry point. Sends image to Claude Opus 4.6 (multimodal)
        with a structured extraction prompt. Returns VisionAnalysis.

        Steps:
        1. validate_image() — size, MIME check
        2. Build multimodal prompt with image + VISION_ANALYSIS_PROMPT
        3. Call LLM via assign_model("reviewer", complexity=8) → Claude Opus 4.6
        4. Parse JSON response into VisionAnalysis dataclass
        5. If parsing fails, retry once with VISION_REPAIR_PROMPT
        """

    def to_architect_context(self, analysis: VisionAnalysis) -> str:
        """
        Format VisionAnalysis as a human-readable SystemMessage for the Architect.
        Includes: layout description, component inventory, color tokens, typography spec.
        """

    def to_frontend_context(self, analysis: VisionAnalysis, theme: Dict) -> str:
        """
        Format VisionAnalysis + theme as a detailed SystemMessage for the Frontend agent.
        Includes: CSS variable definitions, Tailwind class suggestions per component,
        responsive breakpoint hints.
        """
```

#### 3.5.2.4 Multimodal Prompt Strategy

The Vision Agent uses a **single-shot structured extraction prompt** sent to Claude Opus 4.6 with the image as a multimodal content block. The prompt requests JSON output matching the `VisionAnalysis` schema.

```
VISION_ANALYSIS_PROMPT = """
You are a design analysis expert. Analyze this UI screenshot/wireframe and extract
a structured description in JSON format.

{requirements_context}

Return a JSON object with these exact keys:
{
  "layout_regions": [...],     // Rectangular regions with roles and CSS hints
  "components": [...],         // UI components detected (buttons, inputs, cards, etc.)
  "palette": {...},            // Color palette extracted from the image (hex values)
  "typography": {...},         // Font family, weight, size, scale inferences
  "overall_style": "...",      // One of: minimal, corporate, playful, dark-mode, etc.
  "responsive_hints": {...},   // How layout should adapt for mobile/tablet
  "page_type": "...",          // landing, dashboard, form, settings, profile, list
  "confidence": 0.85           // Your confidence in this analysis (0-1)
}

Rules:
- Use normalized coordinates (0-1) for layout_regions bounds
- Infer CSS hints as Tailwind utility classes where possible
- For colors, always return 6-digit hex (#RRGGBB)
- For typography, infer from visual appearance (you cannot read font metadata)
- If the image is a wireframe/sketch (grayscale, hand-drawn), set confidence < 0.6
  and use placeholder colors (#3B82F6 for primary, etc.)
- If the user provided requirements, let them override ambiguous visual elements
"""
```

If the LLM returns malformed JSON, a **repair prompt** is sent:

```
VISION_REPAIR_PROMPT = """
Your previous response was not valid JSON. Here is the raw text:
{raw_response}

Please fix the JSON syntax and return ONLY the corrected JSON object.
Do not include any explanation or markdown formatting — just the raw JSON.
"""
```

#### 3.5.2.5 LLM Model Selection

| Step | Model | Role | Rationale |
|------|-------|------|-----------|
| Image analysis | Claude Opus 4.6 | `reviewer` | Best multimodal reasoning, structured extraction |
| JSON repair (fallback) | Claude Opus 4.6 | `reviewer` | Same model, same context |

Only ONE LLM call per image (two if JSON repair needed). No dual-model strategy — the Vision Agent is a pre-processing step, not a quality gate.

---

### 3.5.3 Theme Engine — `backend/services/theme_engine.py`

#### 3.5.3.1 Purpose

The Theme Engine converts a `ColorPalette` + `Typography` (from VisionAnalysis) OR a style descriptor string (e.g., "Apple-like", "Cyberpunk") into a concrete `ThemeConfig` containing:
1. A `tailwind.config.js` `extend.colors` + `extend.fontFamily` block
2. A CSS variables block (`:root { --color-primary: ... }`)
3. Component-level Tailwind class maps (button styles, card styles, input styles)

This ensures every generated component uses the same design tokens — no hardcoded hex values scattered across files.

#### 3.5.3.2 Data Structures

```python
@dataclass
class ThemeConfig:
    """Complete theme configuration for code generation."""
    tailwind_extend: Dict[str, Any]     # Goes into tailwind.config.js extend block
    css_variables: Dict[str, str]       # CSS custom properties for :root
    component_classes: Dict[str, str]   # Tailwind class strings per component type
    globals_css: str                    # Complete globals.css content with @tailwind directives
    tailwind_config_js: str             # Complete tailwind.config.js content
    dark_mode: bool                     # Whether dark mode variant was detected/requested
    style_name: str                     # "apple-like", "cyberpunk", etc.
```

#### 3.5.3.3 Core Methods

```python
class ThemeEngine:
    """Produces consistent Tailwind config and CSS variables from design analysis."""

    # Pre-defined style presets
    STYLE_PRESETS: Dict[str, Dict] = {
        "apple-like": {
            "palette": ColorPalette(
                primary="#007AFF", secondary="#5856D6", accent="#FF9500",
                background="#FFFFFF", surface="#F2F2F7", text_primary="#000000",
                text_secondary="#8E8E93", border="#C6C6C8", error="#FF3B30",
                success="#34C759",
            ),
            "typography": Typography(
                heading_font="SF Pro Display, system-ui, sans-serif",
                body_font="SF Pro Text, system-ui, sans-serif",
                heading_weight="semibold", base_size_px=17,
                scale_ratio=1.25, line_height=1.47,
            ),
            "component_classes": {
                "button_primary": "bg-primary text-white rounded-xl px-6 py-3 font-semibold shadow-sm hover:bg-primary/90 transition-colors",
                "button_secondary": "bg-surface text-primary rounded-xl px-6 py-3 font-semibold hover:bg-surface/80 transition-colors",
                "card": "bg-white rounded-2xl shadow-sm border border-border p-6",
                "input": "bg-surface border border-border rounded-xl px-4 py-3 text-base focus:ring-2 focus:ring-primary/30 focus:border-primary outline-none",
                "nav": "bg-white/80 backdrop-blur-xl border-b border-border sticky top-0 z-50",
            },
        },
        "cyberpunk": { ... },
        "minimal": { ... },
        "corporate": { ... },
        "glassmorphism": { ... },
        "brutalist": { ... },
        "material-design": { ... },
        "dark-mode": { ... },
    }

    def from_analysis(self, analysis: VisionAnalysis) -> ThemeConfig:
        """
        Build ThemeConfig from VisionAnalysis.
        Uses the extracted palette and typography directly.
        Falls back to closest STYLE_PRESET if analysis.confidence < 0.5.
        """

    def from_style_descriptor(self, style: str) -> ThemeConfig:
        """
        Build ThemeConfig from a style name string.
        Exact match against STYLE_PRESETS keys.
        Fuzzy match: "apple", "iOS", "Apple-like" all resolve to "apple-like".
        Unknown styles: use "minimal" as default.
        """

    def generate_tailwind_config(self, palette: ColorPalette, typography: Typography) -> str:
        """
        Generate a complete tailwind.config.js file as a string.
        Extends default theme with extracted colors and fonts.
        """

    def generate_globals_css(self, palette: ColorPalette, typography: Typography, dark_mode: bool) -> str:
        """
        Generate globals.css with:
        - @tailwind base/components/utilities directives
        - :root CSS custom properties for all palette colors
        - @media (prefers-color-scheme: dark) block if dark_mode=True
        - Base typography styles (html { font-family, font-size, line-height })
        """

    def generate_component_classes(self, style: str, palette: ColorPalette) -> Dict[str, str]:
        """
        Return Tailwind class strings for common component types.
        Used by Frontend agent as concrete styling reference.
        Keys: button_primary, button_secondary, card, input, nav,
              heading, badge, avatar, table_row, modal.
        """

    def _resolve_style(self, style: str) -> str:
        """Fuzzy-match a style descriptor to a STYLE_PRESETS key."""
```

#### 3.5.3.4 Style Preset Lookup

The `_resolve_style()` method uses a simple alias map:

```python
STYLE_ALIASES = {
    "apple": "apple-like", "ios": "apple-like", "macos": "apple-like",
    "cyber": "cyberpunk", "neon": "cyberpunk", "sci-fi": "cyberpunk",
    "clean": "minimal", "simple": "minimal", "modern": "minimal",
    "business": "corporate", "enterprise": "corporate", "professional": "corporate",
    "glass": "glassmorphism", "frosted": "glassmorphism", "blur": "glassmorphism",
    "brutal": "brutalist", "raw": "brutalist",
    "google": "material-design", "material": "material-design", "md": "material-design",
    "dark": "dark-mode", "night": "dark-mode",
}
```

If the style descriptor doesn't match any alias, default to `"minimal"`.

#### 3.5.3.5 Generated File Content

The Theme Engine produces TWO files that are injected into `frontend_code`:

1. **`tailwind.config.js`** — extends the default Tailwind theme:
```javascript
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx}", "./components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: "var(--color-primary)",
        secondary: "var(--color-secondary)",
        accent: "var(--color-accent)",
        surface: "var(--color-surface)",
        border: "var(--color-border)",
        error: "var(--color-error)",
        success: "var(--color-success)",
      },
      fontFamily: {
        heading: ["var(--font-heading)", "system-ui", "sans-serif"],
        body: ["var(--font-body)", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
```

2. **`app/globals.css`** — CSS variables + base styles:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --color-primary: #3B82F6;
  --color-secondary: #10B981;
  /* ... all palette colors ... */
  --font-heading: 'Inter', system-ui, sans-serif;
  --font-body: 'Inter', system-ui, sans-serif;
}

html {
  font-family: var(--font-body);
  font-size: 16px;
  line-height: 1.5;
}
```

---

### 3.5.4 Vision Node — Pipeline Integration

#### 3.5.4.1 ProjectState Extensions

Three new fields added to `ProjectState` in `multi_agent.py`:

```python
class ProjectState(TypedDict):
    # ... existing fields ...
    # Phase 3.5 — Vision-to-Vibe (IE-6)
    vision_image: Optional[str]              # base64-encoded input image
    vision_analysis: Optional[Dict[str, Any]]  # VisionAnalysis.to_dict()
    vision_theme: Optional[Dict[str, Any]]     # ThemeConfig (tailwind + CSS vars + component classes)
```

#### 3.5.4.2 Vision Node Implementation

```python
@monitor_node("vision")
def _vision_node(self, state: ProjectState) -> Dict[str, Any]:
    """
    Vision node — analyzes uploaded image and generates theme.

    Position: before architect (conditional).
    Non-blocking: failures result in empty analysis, pipeline continues
    with text-only requirements.
    """
    image_b64 = state.get("vision_image")
    if not image_b64:
        return {"vision_analysis": None, "vision_theme": None}

    try:
        from agents.vision_agent import VisionAgent
        from services.theme_engine import ThemeEngine

        agent = VisionAgent(llm_provider="auto")
        requirements = state.get("requirements", "")

        # Step 1: Analyze image
        analysis = asyncio.run(agent.analyze(image_b64, requirements))

        # Step 2: Generate theme from analysis
        engine = ThemeEngine()
        theme = engine.from_analysis(analysis)

        return {
            "vision_analysis": asdict(analysis),
            "vision_theme": asdict(theme),
        }
    except Exception as e:
        lg_logger.warning("[VISION] Failed: %s — continuing without vision context", e)
        return {"vision_analysis": None, "vision_theme": None}
```

#### 3.5.4.3 Graph Modification

The `_build_graph()` method is modified to add the Vision Node as a conditional entry point:

```python
def _build_graph(self) -> StateGraph:
    workflow = StateGraph(ProjectState)

    # Add nodes (existing)
    workflow.add_node("architect", self._architect_node)
    # ... all existing nodes ...

    # Phase 3.5: Vision Node (conditional entry)
    workflow.add_node("vision", self._vision_node)

    # Entry: START → vision or architect
    workflow.add_conditional_edges(
        START,
        self._route_entry,   # NEW: checks if vision_image exists
        ["vision", "architect"]
    )

    # vision always flows to architect
    workflow.add_edge("vision", "architect")

    # Architect onwards: unchanged
    # (remove the old: workflow.add_edge(START, "architect"))
    ...
```

#### 3.5.4.4 Entry Router

```python
def _route_entry(self, state: ProjectState) -> str:
    """Route entry: vision_node if image provided, else straight to architect."""
    if state.get("vision_image"):
        return "vision"
    return "architect"
```

#### 3.5.4.5 Architect Node Modification

The `_architect_node` is extended to include vision context when available:

```python
@monitor_node("architect")
def _architect_node(self, state: ProjectState) -> Dict[str, Any]:
    # Inject vision context into messages if available
    vision_analysis = state.get("vision_analysis")
    if vision_analysis:
        from agents.vision_agent import VisionAgent
        agent = VisionAgent()
        analysis_obj = VisionAgent.from_dict(vision_analysis)
        context_msg = agent.to_architect_context(analysis_obj)
        state["messages"].append(SystemMessage(content=context_msg))

    result = self.agents["architect"].invoke(state)
    # ... existing auth detection logic ...
    return result
```

#### 3.5.4.6 Frontend Node Modification

The `_frontend_node` is extended to inject theme files and component-level styling hints:

```python
@monitor_node("frontend")
def _frontend_node(self, state: ProjectState) -> Dict[str, Any]:
    # Inject vision + theme context into messages if available
    vision_analysis = state.get("vision_analysis")
    vision_theme = state.get("vision_theme")
    if vision_analysis and vision_theme:
        from agents.vision_agent import VisionAgent
        from services.theme_engine import ThemeEngine
        agent = VisionAgent()
        analysis_obj = VisionAgent.from_dict(vision_analysis)
        theme_obj = ThemeEngine.from_dict(vision_theme)
        context_msg = agent.to_frontend_context(analysis_obj, theme_obj)
        state["messages"].append(SystemMessage(content=context_msg))

    result = self.agents["frontend"].invoke(state)

    # Inject theme files into frontend_code
    if vision_theme:
        frontend_code = result.get("frontend_code", {})
        frontend_code["tailwind.config.js"] = vision_theme["tailwind_config_js"]
        frontend_code["app/globals.css"] = vision_theme["globals_css"]
        result["frontend_code"] = frontend_code

    # ... existing auth injection logic ...
    return result
```

#### 3.5.4.7 API Entry Point — Image Upload

The build API (or chat API) is extended to accept multipart/form-data with an image attachment:

```python
# In backend/api/server.py or chat_routes.py:

@router.post("/api/v1/build")
async def start_build(
    requirements: str = Form(...),
    image: Optional[UploadFile] = File(None),
    style: Optional[str] = Form(None),  # "apple-like", "cyberpunk", etc.
):
    initial_state = {
        "requirements": requirements,
        "vision_image": None,
        "vision_theme": None,
    }

    if image:
        raw = await image.read()
        initial_state["vision_image"] = base64.b64encode(raw).decode("utf-8")
    elif style:
        # No image, but style descriptor provided
        from services.theme_engine import ThemeEngine
        engine = ThemeEngine()
        theme = engine.from_style_descriptor(style)
        initial_state["vision_theme"] = asdict(theme)

    # Start pipeline with vision-enriched state
    result = await pipeline.ainvoke(initial_state, config)
    ...
```

---

### 3.5.5 Visual QA Feedback Loop

When both Vision-to-Vibe (IE-6) and Visual QA (IE-4) are active, a natural feedback loop emerges:

```
User uploads screenshot → vision_node extracts analysis
    → architect designs architecture
    → frontend generates code (with theme + component hints)
    → tester runs tests
    → visual_qa captures screenshots of GENERATED app
    → visual_qa compares generated screenshots against ORIGINAL uploaded image
    → reviewer receives: "Generated app matches 85% of original design"
```

To enable this, the Visual QA agent receives the original `vision_image` as an additional comparison reference:

```python
# In _visual_qa_node:
vision_image = state.get("vision_image")
if vision_image:
    # Pass original design as comparison reference
    report = await agent.run(
        frontend_files=frontend_code,
        architecture=state.get("architecture"),
        reference_image=vision_image,  # NEW parameter
    )
```

The Visual QA prompt is extended with a comparison section when `reference_image` is provided:

```
## 6. Design Fidelity (when reference image provided)
- How closely does this screenshot match the reference design?
- Score 0-100: color match, layout match, component match, spacing match
- List specific differences: missing components, wrong colors, layout shifts
```

This creates a **closed-loop quality gate**: the user's intent (uploaded image) is measurably compared against the output.

---

### 3.5.6 Acceptance Criteria — AC-15: Vision-to-Vibe (IE-6)

#### AC-15: Vision Agent + Theme Engine

| # | Criterion | Test Procedure | Expected Outcome | Severity |
|---|-----------|----------------|-------------------|----------|
| AC-15.1 | Vision Agent parses image into VisionAnalysis | Provide base64-encoded PNG screenshot of a dashboard UI (mock LLM response) | `analyze()` returns `VisionAnalysis` with ≥3 `layout_regions`, ≥5 `components`, valid `palette` (all hex), valid `typography`. | **BLOCKER** |
| AC-15.2 | Vision Agent rejects oversized images | Provide 5 MB base64 image | `validate_image()` raises `ValueError` with message containing "exceeds". No LLM call made. | **BLOCKER** |
| AC-15.3 | Vision Agent handles LLM failure gracefully | Mock LLM to raise `APIError` | `analyze()` raises `VisionAnalysisError`. `_vision_node` catches it, returns `vision_analysis=None`. Pipeline continues to architect. | **BLOCKER** |
| AC-15.4 | Vision Agent repairs malformed JSON | Mock LLM to return JSON with trailing comma on first call, valid JSON on second | `analyze()` detects invalid JSON, sends `VISION_REPAIR_PROMPT`, returns valid `VisionAnalysis` from second call. Total LLM calls = 2. | HIGH |
| AC-15.5 | Theme Engine produces valid ThemeConfig from analysis | Provide `VisionAnalysis` with palette `primary="#3B82F6"`, typography `body_font="Inter"` | `from_analysis()` returns `ThemeConfig`. `tailwind_config_js` contains `"primary"`. `globals_css` contains `--color-primary: #3B82F6`. `component_classes` has ≥5 keys. | **BLOCKER** |
| AC-15.6 | Theme Engine resolves style descriptors | Call `from_style_descriptor("Apple-like")`, `from_style_descriptor("iOS")`, `from_style_descriptor("apple")` | All three return identical `ThemeConfig` with `style_name="apple-like"`. `palette.primary` is `"#007AFF"`. | **BLOCKER** |
| AC-15.7 | Theme Engine defaults unknown styles to minimal | Call `from_style_descriptor("unknown_style_xyz")` | Returns `ThemeConfig` with `style_name="minimal"`. No exception raised. | HIGH |
| AC-15.8 | Pipeline entry routes correctly | Start build with `vision_image` set → `_route_entry()` returns `"vision"`. Start build without image → returns `"architect"`. | Conditional routing works. Graph edges: START → vision → architect exists. START → architect exists (direct). | **BLOCKER** |
| AC-15.9 | Theme files injected into frontend_code | Complete build with vision image (mock LLM) | `state["frontend_code"]` contains `"tailwind.config.js"` with `var(--color-primary)` and `"app/globals.css"` with `:root` CSS variables block. | **BLOCKER** |
| AC-15.10 | Responsive hints influence Frontend agent context | `VisionAnalysis` with `responsive_hints={"mobile": "stack vertically, hide sidebar"}` | `to_frontend_context()` output contains `"stack vertically"` and `"hide sidebar"`. Frontend agent's system message includes responsive instructions. | HIGH |

**Severity totals**: 6 BLOCKER + 4 HIGH = 10 criteria

---

### 3.5.7 Phase 3.5 — File Manifest

| File | Action | Subsystem |
|------|--------|-----------|
| `backend/ai/agents/vision_agent.py` | NEW | 3.5.1 Vision Agent |
| `backend/services/theme_engine.py` | NEW | 3.5.2 Theme Engine |
| `backend/ai/agents/multi_agent.py` | MODIFY | 3.5.3 Vision Node (add node, conditional entry, extend architect + frontend nodes) |
| `backend/api/chat_routes.py` | MODIFY | 3.5.3 Image upload (multipart/form-data) |
| `backend/tests/test_vision_agent.py` | NEW | Unit tests for VisionAgent |
| `backend/tests/test_theme_engine.py` | NEW | Unit tests for ThemeEngine |
| `backend/tests/test_vision_pipeline.py` | NEW | Integration tests for Vision Node pipeline routing + state flow |

**Total**: 4 new files + 2 modified files + 3 test files

---

### 3.5.8 Phase 3.5 — Dependency Map

```
┌──────────────────────────┐
│  Vision Agent            │◄─── Depends on: Claude Opus 4.6 (multimodal, reviewer role)
│  vision_agent.py         │◄─── Depends on: assign_model() ✅ exists in efficiency.py
│                          │◄─── Depends on: @monitor_node decorator ✅ exists
│                          │───► Produces: VisionAnalysis (structured JSON)
│                          │───► Consumed by: _architect_node, _frontend_node
└──────────┬───────────────┘
           │ VisionAnalysis
           ▼
┌──────────────────────────┐
│  Theme Engine            │◄─── Depends on: VisionAnalysis.palette + .typography
│  theme_engine.py         │◄─── OR: style descriptor string ("apple-like")
│                          │───► Produces: ThemeConfig (tailwind.config.js + globals.css)
│                          │───► Injected into: state["frontend_code"] by _frontend_node
└──────────┬───────────────┘
           │ ThemeConfig
           ▼
┌──────────────────────────┐
│  Pipeline (_build_graph) │◄─── Modified: conditional START → vision | architect
│  multi_agent.py          │◄─── New node: "vision" → _vision_node
│                          │◄─── Modified: _architect_node (vision context injection)
│                          │◄─── Modified: _frontend_node (theme file injection)
│                          │───► Extended: _visual_qa_node (reference_image comparison)
└──────────────────────────┘
```

### 3.5.9 Phase 3.5 — Implementation Order

| Step | Task | Dependencies | Estimated Tests |
|------|------|-------------|-----------------|
| 1 | `backend/services/theme_engine.py` | None (standalone) | ~30 |
| 2 | `backend/ai/agents/vision_agent.py` | Theme Engine (for `from_analysis`) | ~25 |
| 3 | Pipeline integration (`multi_agent.py` + `chat_routes.py`) | Vision Agent + Theme Engine | ~20 |

**Total estimated tests**: ~75

---

## Phase 4.0: VOS3 Native Integration

> Dual-mode architecture, VOS3 app distribution, self-hosted enterprise mode.
> **Status**: Skeleton only.

---

## Phase 4.5: Enterprise Ready

> IE-7 (Performance Green-lining), VOS3 Sandbox Hardening (IE-3).
> **Status**: Skeleton only.

---

## Phase 5.0: The Living App

> IE-8 (Autonomous Maintenance & Self-Healing), Learning System, Agent Marketplace.
> **Status**: Skeleton only.

---

## Phase 5.5: Content & Scale

> IE-9 (V-CMS — AI-Native Content Management), Community Gallery.
> **Status**: Skeleton only.

---

## Phase 6.0: Full Transparency

> IE-10 (Documentation & Reverse Engineering — zero black boxes).
> **Status**: Skeleton only.

---

## Phase 6.5: Connectivity

> IE-11 (Synthetic Data Forge), IE-12 (Enterprise Bridge Agent).
> **Status**: Skeleton only.

---

## Phase 7.0: Go-to-Market

> IE-13 (SEO Autopilot), IE-14 (Universal Native Vibration), IE-15 (Global Compliance & Privacy).
> **Status**: Skeleton only.
