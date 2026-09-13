# VOS Hidden System Agent — Implementation Spec

## Overview

VOS is the central intelligence layer of VOS3 — a hidden agent that acts as the system's brain. Users interact with visible agents, but when they request system-level actions (e.g., "create another agent", "check metrics", "store this as a memory"), VOS intercepts and executes those actions via internal API tools. VOS is invisible to users but observable by admins.

## Architecture: Three-Layer Design

1. **Intent Classifier (Fast Path)** — Lightweight gpt-4o-mini call on every message. Returns intent category or null. Rule-based pre-filter skips obvious non-system messages. ~100-200ms added latency.
2. **VOS Engine (Full Path)** — When intent detected: builds relevant tool set, calls capable LLM (gpt-4o) with tools, executes tool calls, returns results.
3. **Tool Registry** — Declarative mapping of internal API endpoints as OpenAI function-calling tools, grouped by category.

## Routing Strategy

**Hybrid routing**: fast rule-based classifier + LLM classification when intent not obvious. Messages that match keyword patterns get classified instantly; ambiguous messages get a gpt-4o-mini classification call.

## Intent Categories

| Category | Description | Example Triggers |
|----------|-------------|-----------------|
| `agent_management` | Create, list, delete, update, execute agents | "Create an agent named Analyzer" |
| `memory_management` | Store, recall, browse memories | "Remember this for later" |
| `vcore_management` | Business entities, records, workflows | "List all entities" |
| `system_query` | Health, metrics, costs | "How much have we spent?" |
| `settings_management` | API keys, configuration | "Are API keys configured?" |
| `codegen` | Generate code | "Generate a React component for..." |

## Tool Registry

Tools call services directly (not HTTP self-calls) for performance. Each tool has:
- `name`: Unique identifier
- `category`: Intent category it belongs to
- `description`: For LLM function-calling
- `parameters`: JSON Schema
- `handler`: Async callable
- `requires_confirmation`: Boolean for destructive actions

### Registered Tools

| Tool | Category | Maps To |
|------|----------|---------|
| `create_agent` | agent_management | agents_routes.py internals |
| `list_agents` | agent_management | agents_routes.py internals |
| `delete_agent` | agent_management | agents_routes.py internals |
| `update_agent` | agent_management | agents_routes.py internals |
| `execute_agent_task` | agent_management | agents_routes.py internals |
| `store_memory` | memory_management | memory/dev_memory.py |
| `recall_memory` | memory_management | memory/dev_memory.py |
| `get_recent_memories` | memory_management | memory/dev_memory.py |
| `list_entities` | vcore_management | core/business_core.py |
| `create_record` | vcore_management | core/business_core.py |
| `list_workflows` | vcore_management | core/workflow_engine.py |
| `trigger_workflow` | vcore_management | core/workflow_engine.py |
| `get_system_health` | system_query | src/observability.py |
| `get_metrics` | system_query | src/observability.py |
| `get_cost_breakdown` | system_query | src/observability.py |
| `get_settings_status` | settings_management | Environment variables |
| `generate_code` | codegen | ai/codegen/generator.py |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/vos/classify` | Classify message intent (fast path only) |
| `POST` | `/api/vos/execute` | Execute VOS action for pre-classified intent |
| `POST` | `/api/vos/process` | Combined: classify + execute if intent found |
| `GET` | `/api/vos/actions` | Recent VOS actions (admin observability) |
| `GET` | `/api/vos/actions/stats` | VOS action stats by intent/source/tool |
| `GET` | `/api/vos/tools` | List available tools (admin) |

### Key Response: `/api/vos/process`

```json
{
  "had_intent": true,
  "intent": "agent_management",
  "tool_calls": [{"name": "create_agent", "arguments": {"name": "Analyzer", "role": "researcher"}}],
  "results": [{"tool": "create_agent", "success": true, "data": {"agent_id": "abc123", "name": "Analyzer"}}],
  "summary": "Created agent 'Analyzer' (role: researcher, model: gpt-4o-mini, id: abc123)",
  "pass_through": false
}
```

- `pass_through: true` means message should continue to regular chat LLM
- `pass_through: false` means VOS handled it completely

## Frontend Integration

### Chat (`useChat.ts`)
Before calling `/api/chat/completions`, VOS intercepts:
1. Call `/api/vos/process` with message + last 4 messages as context
2. If `had_intent && !pass_through`: insert VOS summary as assistant message, skip regular LLM
3. If `pass_through`: continue normal chat flow

### Agent Collaboration (`CollaborateChat.tsx`)
After receiving agent responses, parse for `[VOS:...]` directives:
- Strip directive from displayed message
- Fire-and-forget call to `/api/vos/process` with `source: "agent"`, `source_id: agentId`

### Admin Panel (`VosActivityPanel.tsx`)
Available on `/metrics` page:
- Timeline of recent VOS actions
- Intent distribution breakdown (bar charts)
- Source breakdown (chat vs agent)
- Tool usage frequency

## Dev Mode Fallback

When no API keys are configured:
- Intent classification falls back to rule-based only (keyword matching)
- Tool execution uses rule-based tool selection (first matching tool by name)
- No LLM calls made — fully functional without external APIs

## Files Created/Modified

| File | Action | Description |
|------|--------|-------------|
| `backend/vos/__init__.py` | New | Module exports + singleton |
| `backend/vos/tool_registry.py` | New | Tool definitions + registry |
| `backend/vos/intent_classifier.py` | New | Fast intent classification |
| `backend/vos/engine.py` | New | Core VOS engine |
| `backend/api/vos_routes.py` | New | FastAPI routes |
| `backend/main.py` | Modify | Mount VOS router, init engine |
| `backend/api/agents_routes.py` | Modify | VOS-aware default prompts |
| `frontend/hooks/useVos.ts` | New | VOS React hook |
| `frontend/hooks/useChat.ts` | Modify | Intercept messages through VOS |
| `frontend/components/agents/CollaborateChat.tsx` | Modify | Parse VOS directives |
| `frontend/components/admin/VosActivityPanel.tsx` | New | Admin observability UI |
| `frontend/app/metrics/page.tsx` | Modify | Add VOS activity section |

## VOS-Aware Agent Prompts

All default agent system prompts now include a VOS directive instruction:

```
You have access to VOS, the system's hidden intelligence layer.
When you need to perform system-level actions (create agents, store memories,
check metrics, trigger workflows), emit a directive on its own line:
[VOS:action description]
Examples: [VOS:Create an agent named DataBot with role researcher],
[VOS:Store a memory about this architecture decision],
[VOS:Check system health]
```

This follows the existing directive pattern (`[DECISION]`, `[FACT]`, `[CONFIG:*]`).

## Verification Checklist

1. Send "Create an agent named Analyzer with role researcher" in chat → VOS intercepts, creates agent, shows summary (no regular LLM call)
2. Send "What is the weather today?" → VOS classifies as null, passes through to regular chat
3. In agent collaboration, agent responds with `[VOS:Store a memory about this discussion]` → VOS processes, stores memory
4. Visit `/metrics` → VOS Activity section shows logged actions
5. Remove API keys → VOS falls back to dev mode rule-based behavior
6. `npm run build` passes
