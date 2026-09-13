# VOS3 Kernel Reasoning Spec — Model Tier Requirements

Defines minimum model capabilities for serving as VOS3 "CPU" across different task classes.

## Model Tiers

| Tier | Context | Params | Tool Use | Parallel Tools | JSON | Streaming | Examples |
|------|---------|--------|----------|----------------|------|-----------|----------|
| 1 (Chat) | 8K+ | Any | No | No | Yes | Yes | Codestral 22B, Phi-3 |
| 2a (Fast Agent) | **32K+** | **>=27B** | Yes | Yes | **Structured** | Yes | **Gemma 4 27B (baseline)** |
| 2b (Full Agent) | **32K+** | **>=70B** | Yes | Yes | **Structured** | Yes | Llama 3.3 70B, Qwen2.5-72B |
| 3 (Premium) | 128K+ | Any | Yes | Yes | Yes | Yes | Claude Opus, GPT-5, Gemini 3 Pro |

### Tier Descriptions

**Tier 1 (Chat)** — Chat-only completions. No terminal tools, no agentic workflows. Used for simple Q&A, completions, and non-interactive tasks. Routed via `local-light` slot (e.g., `codestral:22b`).

**Tier 2a (Fast Agent)** — Gemma 4 27B is the baseline. Supports tool use, parallel tool calls, and Structured JSON Blocks. Designated for ultra-low-latency tasks: system monitoring, VBus status, telemetry, simple terminal commands (complexity <= 3). The 27B exception to the 70B rule is explicitly granted only to Gemma 4 due to its verified instruction-following and JSON compliance at 27B. Routed via `local-snappy` slot.

**Tier 2b (Full Agent)** — 70B+ models for complex multi-step agentic workflows. Handles code generation, architecture, multi-file reasoning. Routed via `local-default` or `local-code` slots. Minimum `min_params_b: 70` enforced.

**Tier 3 (Premium)** — Cloud models with massive context, vision, extended thinking. All features enabled. Routed via cloud providers (Anthropic, OpenAI, Google).

## Mandatory Protocol Rules

1. **AAAK compression EXPLICITLY FORBIDDEN** in ALL Tier 2a and Tier 2b reasoning paths. Compressed instruction tokens cause instruction-drift on sub-200K-context models. Every system prompt, tool definition, and tool result MUST use full uncompressed text.

2. **Structured JSON Blocks** mandatory for all tool interactions. No free-form text tool calls. Tool definitions use `json_schema` format. Every tool call returns `{"tool_name": str, "arguments": dict}`. Every tool result returns `{"result": str, "error": str | null}`.

3. **`processing_locality` metadata** on every response: `"local"` or `"cloud"`.

4. **`model_origin` + `global_timestamp`** on every memory write (Atomic Writes). Ensures cross-model consistency — when `local-snappy` writes a memory, `local-default` can verify provenance.

5. **Bandwidth-aware routing**: Heavy tasks (complexity >= 7) prefer `local-first` when `VOS3_LOCALITY_PREFERENCE=local-first`. Light queries (complexity <= 3) may burst to cloud for speed.

6. **Spatial Scoping** enforced:
   - `local-snappy` (Tier 2a) → Infrastructure Wing only (`wing="infra"`)
   - `local-default` / `local-code` (Tier 2b) → Logic Wings (`kernel`, `backend`, `frontend`)
   - Cloud (Tier 3) → All wings (no restriction)

## Degraded Mode Behavior

| Detected Tier | Terminal Tools | Agent Dispatch | Vision | Extended Thinking |
|---------------|---------------|----------------|--------|-------------------|
| 1 | Disabled | Simple prompt chains | No | No |
| 2a | Full (monitoring/telemetry) | Escalation to 2b for complex | No | No |
| 2b | Full | Full agentic | No | No |
| 3 | Full | Full agentic | Yes | Yes |

## Escalation Triggers

When a Tier 2a model (`local-snappy`) encounters these conditions, it auto-escalates to Tier 2b (`local-default`):

1. **>3 consecutive malformed tool calls** — indicates model capacity exceeded
2. **Input context exceeds 28K tokens** (90% of 32K window) — context overflow risk
3. **User explicitly requests higher quality** (`/upgrade` command)

Escalation preserves full conversation state via `ContextSnapshot` (messages + tool state + task metadata).

## Router Configuration

Local model slots defined in `backend/config/router.yaml`:

```yaml
local-snappy:   # Tier 2a — Gemma 4 27B, Priority 1, ultra-low latency
local-default:  # Tier 2b — Llama 3.3 70B, Priority 3, full agentic
local-code:     # Tier 2b — Qwen2.5-Coder 72B, Priority 4, code specialist
local-light:    # Tier 1  — Codestral 22B, Priority 5, chat-only
```

## Environment Variables

```env
OLLAMA_BASE_URL=http://localhost:11434
VOS3_PREFERRED_PROVIDER=auto          # auto|anthropic|openai|local
VOS3_LOCALITY_PREFERENCE=auto         # local-first|cloud-first|auto
VOS3_LOCAL_MIN_PARAMS_B=70            # Minimum model size for agentic tasks
```

## Implementation References

| Component | File |
|-----------|------|
| ToolUseProvider abstraction | `backend/ai/llm/tool_provider.py` |
| SmartRouter config | `backend/config/router.yaml` |
| Factory mappings | `backend/src/efficiency/factory.py` |
| Memory spatial scoping | `backend/memory/dev_memory.py` |
| Provider health check | `backend/api/chat_routes.py` (`/providers/status`) |
| Frontend model selector | `frontend/components/settings/ModelSelector.tsx` |
