# VOS3 Multi-Agent Pipeline Architecture

## Overview

The VOS3 pipeline implements a multi-model code generation system with parallel execution,
cross-model verification, automatic retry logic, and **security sandboxing**.

```
USER REQUEST → ARCHITECT → EXPANDER → DISPATCHER ─┬→ FRONTEND ─┐
                                                  └→ BACKEND  ─┴→ AGGREGATOR → TESTER → REVIEWER → FINALIZE
```

---

## Pipeline Stages

### 1. ARCHITECT (GPT-5.2-Pro)
**Purpose:** High-level system design and decomposition

```
Input:  Natural language requirements ("Build a todo app with auth")
Output: {
  tech_stack: ["Next.js", "Convex", "Clerk"],
  components: ["TodoList", "AuthForm", "Dashboard"],
  api_endpoints: ["/api/todos", "/api/auth"],
  data_models: ["Todo", "User"]
}
```

**Model:** `gpt-5.2-pro` (extended reasoning)

---

### 2. EXPANDER (Gemini 3 Pro)
**Purpose:** Expand architecture into detailed file specifications

```
Input:  Architecture blueprint from Architect
Output: execution_queue [20+ file specifications]
        - Each spec includes: path, purpose, dependencies, interfaces
```

**Model:** `gemini-3-pro-latest` (1M token context)

---

### 3. DISPATCHER (Liquid LFM 2.5)
**Purpose:** Fan-out to parallel execution branches

```python
def _dispatcher_node(state: ProjectState) -> List[Send]:
    branches = [Send("frontend", state)]

    if needs_backend:
        branches.append(Send("backend", state))
        # Both run CONCURRENTLY via LangGraph Send API

    return branches
```

**Model:** `liquid-lfm-2.5-1.2b` (359 tokens/sec, SOTA SLM)

---

### 4. FRONTEND (Claude Opus 4.6)
**Purpose:** Generate frontend code

**Outputs:**
- React/Next.js components
- Custom hooks
- TypeScript types
- Tailwind styling

**Model:** `claude-3-opus-20260210`

---

### 5. BACKEND (Claude Opus 4.6)
**Purpose:** Generate backend code

**Outputs:**
- API routes (FastAPI/Express)
- Database models
- Middleware
- Convex functions

**Model:** `claude-3-opus-20260210`

---

### 6. AGGREGATOR (Liquid LFM 2.5)
**Purpose:** Merge parallel execution results

```python
def _aggregator_node(state: ProjectState) -> Dict:
    frontend_files = state.get("frontend_code", {})
    backend_files = state.get("backend_code", {})

    return {"all_files": {**frontend_files, **backend_files}}
```

**Model:** `liquid-lfm-2.5-1.2b` (fast merging, 359 tok/s)

---

### 7. TESTER (GPT-5.2)
**Purpose:** Generate test suites

**Outputs:**
- Jest tests (React components)
- Pytest tests (API endpoints)
- Playwright tests (E2E)

**Model:** `gpt-5.2`

---

### 8. REVIEWER (GPT-5.2-Pro)
**Purpose:** Cross-model verification and quality assurance

**Checks:**
- Security audit
- Code quality
- Best practices
- Type safety

**Decision Logic:**
```
if critical_issues:
    if iteration < 3:
        return Send("dispatcher", state)  # Retry
    else:
        return {"approved": False, "issues": issues}
else:
    return {"approved": True}
```

**Model:** `gpt-5.2-pro` (cross-model: Claude writes → GPT reviews)

---

### 9. FINALIZE
**Purpose:** Assemble final output

```python
Output: GeneratedProject {
    files: Dict[str, str],      # path -> content
    summary: str,               # Human-readable summary
    metadata: {
        models_used: List[str],
        total_tokens: int,
        total_cost: float,
        iterations: int
    }
}
```

---

## State Management

```python
class ProjectState(TypedDict):
    messages: List[BaseMessage]      # Message history
    requirements: str                 # User input
    architecture: Dict[str, Any]     # Architect output
    execution_queue: List[FileSpec]  # Expander output
    frontend_code: Dict[str, str]    # Frontend output
    backend_code: Dict[str, str]     # Backend output
    tests: Dict[str, str]            # Tester output
    review_results: Dict[str, Any]   # Reviewer output
    current_phase: PipelinePhase     # Current stage
    iteration: int                    # Retry counter
    error_count: int                  # Error counter for escalation
```

---

## Model Routing Matrix

| Agent      | Default Model            | 32K Fallback | Low Confidence† | Reason                           |
|------------|--------------------------|--------------|-----------------|----------------------------------|
| Architect  | `gpt-5.2-pro`       | -            | -               | Extended reasoning for design    |
| Expander   | `gemini-3-pro-latest`    | -            | `claude-opus`   | 1M context for blueprints        |
| Dispatcher | `liquid-lfm-2.5-1.2b`    | `mistral-7b` | `claude-opus`   | 359 tok/s, fast routing          |
| Frontend   | `claude-3-opus-20260210` | -            | -               | High-quality React/TS code       |
| Backend    | `claude-3-opus-20260210` | -            | -               | High-quality API code            |
| Aggregator | `liquid-lfm-2.5-1.2b`    | `mistral-7b` | `claude-opus`   | Fast merging, lightweight        |
| Tester     | `gpt-5.2`                | -            | `claude-opus`   | Test analysis                    |
| Reviewer   | `gpt-5.2-pro`       | -            | -               | Cross-model verification         |
| Finalize   | `liquid-lfm-2.5-1.2b`    | `mistral-7b` | `claude-opus`   | Fast final assembly              |

\* **32K Fallback:** Applied when `calculateStateContext(state) > 32000` tokens.
   See "The 32K Rule" section below.

† **Low Confidence:** Applied when `confidence < 0.85`. Escalates to Claude Opus 4.6 with thinking mode.
   See "Confidence Escalation" section below.

---

## Cross-Model Verification Pattern

```
┌──────────────────────────────────────────────────────────┐
│  ANTI ECHO-CHAMBER PATTERN                               │
│                                                          │
│  Claude writes code ──────┬───────► GPT reviews         │
│  (Frontend/Backend)       │         (Reviewer)          │
│                           │                              │
│  GPT writes code ─────────┴───────► Claude reviews      │
│  (Architect)                        (if configured)      │
└──────────────────────────────────────────────────────────┘
```

**Rationale:** Different models have different biases and blind spots.
Cross-model review catches issues that same-model review would miss.

---

## Dynamic Escalation

The system uses multiple escalation mechanisms to ensure quality:

```python
# router_bridge.py - resolve_pipeline_routing()

# 1. ITERATION ESCALATION: Auto-escalate on retry iterations
if iteration > 1:
    return PREMIUM_MODEL  # gpt-5.2-pro or claude-opus

# 2. COMPLEXITY ESCALATION: Phase-specific complexity boost
complexity_boost = {
    "architect": +2,
    "reviewer": +1,
    "finalize": +1
}
effective_complexity = base_complexity + complexity_boost.get(phase, 0)

# 3. CONTEXT ESCALATION (32K Rule): Large context → Mistral 7B
if phase in ORCHESTRATION_PHASES and context_length > 32000:
    return MISTRAL_7B_V4

# 4. CONFIDENCE ESCALATION: Low confidence → Opus 4.6 + Thinking
confidence = _calculate_confidence(phase, complexity, iteration, errors, context)
if confidence < 0.85:
    return CLAUDE_OPUS_WITH_THINKING
```

### Escalation Priority

```
┌────────────────────────────────────────────────────┐
│  ESCALATION PRIORITY (highest to lowest)           │
├────────────────────────────────────────────────────┤
│  1. Context > 32K      → Mistral 7B v4            │
│  2. Iteration > 1      → Premium Model            │
│  3. Complexity >= 7    → Premium Model            │
│  4. Confidence < 85%   → Opus 4.6 + Thinking      │
│  5. Standard routing   → Default for phase        │
└────────────────────────────────────────────────────┘
```

---

## The 32K Rule (February 2026)

### Overview

The 32K Rule is an automatic context-aware model escalation mechanism for orchestration phases.
When the combined context (frontend code + backend code + requirements + tests) exceeds
**32,000 tokens**, the system automatically switches from Liquid LFM 2.5 to Mistral 7B v4.

### Why It's Needed

| Model | Context Limit | Speed | Cost/1M |
|-------|---------------|-------|---------|
| Liquid LFM 2.5 | 32K tokens | 359 tok/s | $0.05 |
| Mistral 7B v4 | 128K tokens | 180 tok/s | $0.10 |

For small-to-medium projects, Liquid LFM is optimal (fastest, cheapest).
For large projects with extensive codebases, Mistral 7B handles the larger context.

### Affected Phases

The 32K Rule applies to orchestration phases:
- `dispatcher` - Fan-out to parallel branches
- `aggregator` - Merge frontend + backend code
- `finalize` - Final assembly

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           THE 32K RULE                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ProjectState                                                              │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  frontend_code: { "App.tsx": "...", "components/*.tsx": "..." }     │   │
│   │  backend_code: { "api/*.py": "...", "models/*.py": "..." }          │   │
│   │  requirements: "Build a todo app with auth and real-time sync"      │   │
│   │  tests: { "*.test.tsx": "...", "test_*.py": "..." }                 │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                       │
│                                      ▼                                       │
│                        calculateStateContext(state)                         │
│                                      │                                       │
│                                      ▼                                       │
│                         ┌────────────────────────┐                          │
│                         │  Total tokens: 45,000  │                          │
│                         └────────────────────────┘                          │
│                                      │                                       │
│                                      ▼                                       │
│                    ┌─────────────────────────────────┐                      │
│                    │  tokens > 32,000?               │                      │
│                    │  phase in [dispatcher,          │                      │
│                    │            aggregator,          │                      │
│                    │            finalize]?           │                      │
│                    └─────────────────────────────────┘                      │
│                           │                │                                 │
│                      YES  │                │  NO                            │
│                           ▼                ▼                                 │
│              ┌──────────────────┐  ┌──────────────────┐                     │
│              │   Mistral 7B v4  │  │  Liquid LFM 2.5  │                     │
│              │  "The Runner Up" │  │   "SOTA SLM"     │                     │
│              │                  │  │                  │                     │
│              │  128K context    │  │  32K context     │                     │
│              │  180 tok/s       │  │  359 tok/s       │                     │
│              │  $0.10/1M        │  │  $0.05/1M        │                     │
│              └──────────────────┘  └──────────────────┘                     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Implementation

```typescript
// smart-router-bridge.ts

async getModelForPipelineStep(state: ProjectState): Promise<RoutingDecision> {
    const contextSize = this.calculateStateContext(state);
    const phase = state.current_phase;

    // The 32K Rule
    const LIQUID_LFM_CONTEXT_LIMIT = 32000;
    const ORCHESTRATION_PHASES = ["dispatcher", "aggregator", "finalize"];

    if (ORCHESTRATION_PHASES.includes(phase) && contextSize > LIQUID_LFM_CONTEXT_LIMIT) {
        console.warn(
            `[SmartRouter] Context (${contextSize}) exceeds limit. ` +
            `Switching to Mistral 7B v4.`
        );

        return {
            modelId: "mistral-7b-v4-instruct",
            provider: "mistral",
            source: "context_escalation_32k_rule"
        };
    }

    // Standard routing for smaller contexts
    return this.getModelForTaskAsync(phase, complexity, contextSize);
}
```

### Token Estimation

```typescript
// Accurate average for TypeScript/Python code: 3.8 chars per token
private estimateTokens(text: string): number {
    return Math.ceil(text.length / 3.8);
}

calculateStateContext(state: ProjectState): number {
    let totalLength = 0;
    if (state.frontend_code) totalLength += JSON.stringify(state.frontend_code).length;
    if (state.backend_code) totalLength += JSON.stringify(state.backend_code).length;
    if (state.requirements) totalLength += state.requirements.length;
    if (state.tests) totalLength += JSON.stringify(state.tests).length;
    return this.estimateTokens(totalLength);
}
```

### Usage Example

```typescript
import { getModelForPipelineStep } from "./smart-router-bridge";

// In LangGraph aggregator node
async function aggregatorNode(state: ProjectState) {
    // Automatically applies 32K Rule
    const model = await getModelForPipelineStep({
        current_phase: "aggregator",
        complexity: 5,
        frontend_code: state.frontend_code,
        backend_code: state.backend_code,
        requirements: state.requirements
    });

    // model.modelId will be:
    // - "liquid-lfm-2.5-1.2b" if context <= 32K
    // - "mistral-7b-v4-instruct" if context > 32K

    const llm = createLLM(model);
    return await llm.invoke(mergePrompt);
}
```

### Monitoring

The system logs context escalation events:

```
[SmartRouter] Pipeline step: phase=aggregator, complexity=5, context=45000 tokens
[SmartRouter] Context size (45000) exceeds Liquid LFM limit (32000). Auto-switching to Mistral 7B v4.
```

---

## File References

| File                              | Purpose                    | Lines |
|-----------------------------------|----------------------------|-------|
| `backend/ai/agents/multi_agent.py`   | Main pipeline              | ~1750 |
| `backend/ai/agents/router_agent.py`  | Query router               | ~656  |
| `backend/src/llm.py`              | SmartLLM factory           | ~830  |
| `scripts/router_bridge.py`        | Model routing (Convex)     | ~500  |
| `mcp-server/src/orchestration/`   | TypeScript bridge          | ~800  |

---

## Retry Logic

```
Max retries: 3

Iteration 0: Normal execution
Iteration 1: Re-run failed phase
Iteration 2: Escalate to premium model
Iteration 3: Final attempt with max resources

If still failing after 3 iterations:
  → Return partial results with error report
```

---

## Cost Optimization

1. **Smart Routing:** Use cheaper models for simple phases
2. **Liquid LFM 2.5:** Ultra-fast SLM (359 tok/s) for dispatcher/aggregator/finalize
3. **Caching:** Semantic cache for repeated queries (92% similarity threshold)
4. **Parallel Execution:** Frontend + Backend run concurrently
5. **Early Exit:** Skip phases if not needed (e.g., no backend for static sites)

**Model Cost Comparison (per 1M tokens):**
| Model | Input | Output | Context | Use Case |
|-------|-------|--------|---------|----------|
| Liquid LFM 2.5 | $0.05 | $0.15 | 32K | Orchestration (default) |
| Mistral 7B v4 | $0.10 | $0.30 | 128K | Orchestration (32K fallback) |
| Gemini 3 Flash | $0.50 | $3.00 | 1M | Testing |
| Gemini 3 Pro | $2.00 | $12.00 | 1M | Research/Expansion |
| GPT-5.2 | $15.00 | $60.00 | 128K | Code Review |
| Claude Opus 4.6 | $15.00 | $75.00 | 200K | Code Generation |
| GPT-5.2-Thinking | $20.00 | $80.00 | 128K | Architecture |

**Estimated savings:** 50-70% vs. always using premium models

**32K Rule Impact:**
- Small projects (< 32K context): Use Liquid LFM → Maximum savings
- Large projects (> 32K context): Auto-escalate to Mistral 7B → Still 99% cheaper than premium models

---

## JIT Schema Filtering (February 2026)

### Overview

Just-In-Time (JIT) Schema Filtering is a context optimization technique that extracts only
the relevant table definitions from the Convex schema before passing it to agents.

This reduces token usage by **80-95%** for projects with large schemas.

### Problem

A typical Convex schema with 50+ tables can be 5,000+ tokens. When the Backend agent only
needs to work with 2-3 tables, sending the full schema wastes context and money.

### Solution

```
┌─────────────────────────────────────────────────────────────────┐
│                    JIT SCHEMA FILTERING                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   Full Schema (schema.ts)                                       │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │  users: defineTable({ ... })        ← NEEDED            │   │
│   │  sessions: defineTable({ ... })     ← NEEDED            │   │
│   │  products: defineTable({ ... })                         │   │
│   │  orders: defineTable({ ... })                           │   │
│   │  payments: defineTable({ ... })                         │   │
│   │  ... 45 more tables ...                                 │   │
│   └─────────────────────────────────────────────────────────┘   │
│                           │                                      │
│                           ▼                                      │
│              filterRelevantSchema(schema, ["users", "sessions"]) │
│                           │                                      │
│                           ▼                                      │
│   Filtered Schema                                               │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │  // JIT Filtered Schema                                 │   │
│   │  // Only includes tables: users, sessions               │   │
│   │                                                         │   │
│   │  users: defineTable({ ... })                            │   │
│   │  sessions: defineTable({ ... })                         │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│   Result: 5,000 tokens → 400 tokens (92% reduction)             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Implementation

```typescript
// smart-router-bridge.ts

filterRelevantSchema(fullSchema: string, requiredModels: string[]): string {
    if (requiredModels.length === 0) return fullSchema;

    const lines = fullSchema.split("\n");
    let filteredSchema = "// JIT Filtered Schema\n";
    let isExtracting = false;
    let braceCount = 0;

    for (const line of lines) {
        // Detect table definition: tableName: defineTable({
        const tableMatch = line.match(/^\s*(\w+)\s*:\s*defineTable\s*\(/);

        if (tableMatch) {
            const tableName = tableMatch[1].toLowerCase();
            const isRelevant = requiredModels.some(m =>
                tableName.includes(m) || m.includes(tableName)
            );
            if (isRelevant) isExtracting = true;
        }

        if (isExtracting) {
            filteredSchema += line + "\n";
            braceCount += (line.match(/{/g) || []).length;
            braceCount -= (line.match(/}/g) || []).length;

            if (braceCount <= 0 && line.includes(")")) {
                isExtracting = false;
            }
        }
    }

    return filteredSchema;
}
```

### Auto-Detection

The system can automatically detect required models from requirements:

```typescript
// Automatically extract model names from requirements
const models = extractRequiredModels(
    "Create a user authentication system with sessions"
);
// Result: ["user", "authentication", "sessions"]

// Prepare optimized context
const { schema, detectedModels, tokensBefore, tokensAfter } =
    prepareBackendContext(fullSchema, requirements);

console.log(`Detected models: ${detectedModels}`);
console.log(`Tokens: ${tokensBefore} → ${tokensAfter}`);
```

### Usage Example

```typescript
import {
    filterRelevantSchema,
    extractRequiredModels,
    prepareBackendContext
} from "./smart-router-bridge";

// In Backend agent node
async function backendAgentNode(state: ProjectState) {
    const fullSchema = await readFile("convex/schema.ts");

    // Option 1: Manual model list
    const filtered = filterRelevantSchema(fullSchema, ["users", "todos"]);

    // Option 2: Auto-detect from requirements
    const { schema, detectedModels } = prepareBackendContext(
        fullSchema,
        state.requirements,
        state.backend_code
    );

    // Use filtered schema in prompt
    const prompt = `
        Generate Convex functions for the following schema:
        ${schema}

        Requirements: ${state.requirements}
    `;

    return await llm.invoke(prompt);
}
```

### Cost Savings

| Schema Size | Full Schema | Filtered (2 tables) | Savings |
|-------------|-------------|---------------------|---------|
| 10 tables | 1,000 tokens | 200 tokens | 80% |
| 25 tables | 2,500 tokens | 250 tokens | 90% |
| 50 tables | 5,000 tokens | 300 tokens | 94% |
| 100 tables | 10,000 tokens | 350 tokens | 96% |

**Estimated annual savings:** $500-2,000 for active projects with large schemas.

---

## Confidence Escalation (February 2026)

### Overview

Confidence Escalation is an automatic quality-assurance mechanism that upgrades to premium models
when the system detects low confidence in the current model's ability to handle the task.

When confidence drops below **85%**, the system automatically escalates to Claude Opus 4.6
with thinking mode enabled for improved reasoning quality.

### Self-Correction Logic

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     CONFIDENCE ESCALATION LOGIC                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  If the local confidence score calculated by the Smart Router falls         │
│  below 0.85, the pipeline automatically promotes the task to Claude         │
│  Opus 4.6.                                                                  │
│                                                                              │
│  This ensures that even if a lightweight model (Liquid LFM) was initially   │
│  selected, the system will SELF-CORRECT to the most capable model           │
│  available to prevent failures.                                              │
│                                                                              │
│  ┌─────────────────┐                      ┌─────────────────┐               │
│  │  Liquid LFM 2.5 │ ──── confidence ──── │  Claude Opus 4.6│               │
│  │  (selected)     │      < 0.85?         │  (promoted)     │               │
│  │                 │         │            │                 │               │
│  │  $0.05/1M       │        YES           │  $15.00/1M      │               │
│  │  359 tok/s      │         │            │  + Thinking Mode│               │
│  └─────────────────┘         ▼            └─────────────────┘               │
│                         SELF-CORRECT                                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key Behavior:**
- Confidence is calculated locally before each model invocation
- Threshold: **0.85** (85%)
- Below threshold → Automatic promotion to Opus 4.6
- Thinking mode enabled for complex reasoning
- No manual intervention required

### Why It's Needed

Low confidence scenarios indicate increased risk of:
- Incorrect code generation
- Missing edge cases
- Security vulnerabilities
- Integration failures

By escalating to a premium model with extended reasoning, we trade cost for quality
when the stakes are higher.

### Confidence Factors

The confidence score (0.0 - 1.0) is calculated based on multiple factors:

| Factor | Penalty | Reason |
|--------|---------|--------|
| Complexity ≥ 9 | -15% | Very complex tasks need premium models |
| Complexity ≥ 7 | -10% | Complex tasks may challenge cheaper models |
| Each retry iteration | -12% | Previous attempts failed |
| Each previous error | -8% | Error patterns suggest difficulty |
| Context > 24K tokens | -10% | Approaching model limits |
| Context > 16K tokens | -5% | Moderate context pressure |
| Architect/Reviewer phase | -5% | Complex phases need extra care |
| Orchestration phases | +5% | Well-understood, predictable tasks |

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      CONFIDENCE ESCALATION                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Input Factors                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │  complexity: 8                                                       │   │
│   │  iteration: 1                                                        │   │
│   │  error_count: 1                                                      │   │
│   │  context_length: 28000                                               │   │
│   │  phase: "backend"                                                    │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                       │
│                                      ▼                                       │
│                        _calculate_confidence()                              │
│                                      │                                       │
│   ┌──────────────────────────────────┼──────────────────────────────────┐   │
│   │  1.0  (base)                     │                                   │   │
│   │ -0.10 (complexity >= 7)          │                                   │   │
│   │ -0.12 (iteration = 1)            │                                   │   │
│   │ -0.08 (error_count = 1)          │                                   │   │
│   │ -0.05 (context > 16K)            │                                   │   │
│   │ ─────────────────────────────────│                                   │   │
│   │ = 0.65 (65% confidence)          │                                   │   │
│   └──────────────────────────────────┼──────────────────────────────────┘   │
│                                      │                                       │
│                                      ▼                                       │
│                    ┌─────────────────────────────────┐                      │
│                    │  confidence < 0.85?             │                      │
│                    │  model != claude-opus?          │                      │
│                    └─────────────────────────────────┘                      │
│                           │                │                                 │
│                      YES  │                │  NO                            │
│                           ▼                ▼                                 │
│              ┌──────────────────┐  ┌──────────────────┐                     │
│              │  Claude Opus 4.6 │  │  Selected Model  │                     │
│              │  + Thinking Mode │  │  (as planned)    │                     │
│              │                  │  │                  │                     │
│              │  "Premium + Safe"│  │  Cost optimized  │                     │
│              │  $15/$75 per 1M  │  │                  │                     │
│              └──────────────────┘  └──────────────────┘                     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Implementation

**Python (router_bridge.py):**

```python
def _calculate_confidence(
    self,
    phase: str,
    complexity: int,
    iteration: int,
    error_count: int,
    context_length: int
) -> float:
    """Calculate confidence score for model selection."""
    confidence = 1.0

    # Complexity penalty
    if complexity >= 9:
        confidence -= 0.15
    elif complexity >= 7:
        confidence -= 0.10

    # Iteration penalty (each retry reduces confidence)
    confidence -= iteration * 0.12

    # Error penalty
    confidence -= error_count * 0.08

    # Large context penalty
    if context_length > 24000:
        confidence -= 0.10
    elif context_length > 16000:
        confidence -= 0.05

    # Phase-specific adjustments
    if phase in ["architect", "reviewer"]:
        confidence -= 0.05  # Complex phases
    elif phase in ["dispatcher", "aggregator", "finalize"]:
        confidence += 0.05  # Well-understood phases

    return max(0.0, min(1.0, confidence))
```

**TypeScript (smart-router-bridge.ts):**

```typescript
async getModelForPipelineStep(state: ProjectState): Promise<RoutingDecision> {
    // ... 32K Rule check first ...

    const CONFIDENCE_THRESHOLD = 0.85;

    // Get routing from Python with confidence score
    const response = await this.sendCommand("get_pipeline_model", {
        ...params,
        enable_speculation: true
    });

    const confidence = response.data.confidence ?? 1.0;

    // Confidence Escalation
    if (confidence < CONFIDENCE_THRESHOLD &&
        response.data.model_id !== "claude-3-opus-20260210") {

        console.warn(
            `[SmartRouter] Low confidence (${(confidence * 100).toFixed(1)}%) ` +
            `Escalating to Opus 4.6 with thinking mode.`
        );

        return {
            modelId: "claude-3-opus-20260210",
            provider: "anthropic",
            thinking: true,  // Enable thinking mode
            source: "low_confidence_escalation",
            confidence: confidence
        };
    }

    return response.data;
}
```

### Escalation Triggers

Common scenarios that trigger confidence escalation:

| Scenario | Typical Confidence | Result |
|----------|-------------------|--------|
| First attempt, simple task | 95% | No escalation |
| Complex task (complexity=8) | 85% | Borderline |
| First retry with error | 72% | **Escalate to Opus** |
| Second retry, complex | 55% | **Escalate to Opus** |
| Large context + errors | 67% | **Escalate to Opus** |

### Monitoring

The system logs confidence escalation events:

```
[SmartRouter] Pipeline step: phase=backend, complexity=8, context=28000 tokens, iteration=1
[SmartRouter] Confidence calculated: 65.0%
[SmartRouter] Low confidence (65.0%) for liquid-lfm-2.5-1.2b. Escalating to Opus 4.6 with thinking mode.
```

### Combined Routing Rules

The three routing rules are applied in order:

```
┌─────────────────────────────────────────────────────────────────┐
│                    ROUTING DECISION ORDER                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   1. THE 32K RULE (Context Escalation)                          │
│      └─► Context > 32K? → Mistral 7B v4                         │
│                                                                  │
│   2. SPECULATIVE EXECUTION                                       │
│      └─► Query Python/Convex for model + confidence             │
│                                                                  │
│   3. CONFIDENCE ESCALATION                                       │
│      └─► Confidence < 85%? → Claude Opus 4.6 + Thinking         │
│                                                                  │
│   4. STANDARD ROUTING                                            │
│      └─► Use selected model as planned                          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Cost Impact

Confidence escalation increases cost but improves reliability:

| Scenario | Without Escalation | With Escalation | Trade-off |
|----------|-------------------|-----------------|-----------|
| Simple task | $0.05 (Liquid) | $0.05 (Liquid) | Same |
| Complex, first try | $0.05 (Liquid) | $15.00 (Opus) | 300x more, but correct |
| Failed retry | $0.05 + retry costs | $15.00 (Opus) | Saves retry cycles |

**Net effect:** Higher upfront cost for complex tasks, but fewer retries and failures.
Estimated overall savings: 20-30% when accounting for avoided retry cycles

### Testing with force_confidence

For testing and debugging, you can override the calculated confidence using the `force_confidence` parameter:

**CLI Testing:**

```bash
# Test low confidence escalation (should upgrade to Opus)
echo '{"command": "get_pipeline_model", "params": {
    "current_phase": "dispatcher",
    "complexity": 3,
    "force_confidence": 0.7
}}' | python3 scripts/router_bridge.py

# Test high confidence (should stay with Liquid LFM)
echo '{"command": "get_pipeline_model", "params": {
    "current_phase": "dispatcher",
    "complexity": 3,
    "force_confidence": 0.95
}}' | python3 scripts/router_bridge.py
```

**Expected Results:**

| Test Case | force_confidence | Expected Model | Escalated |
|-----------|------------------|----------------|-----------|
| Low confidence | 0.70 | `claude-3-opus-20260210` | Yes |
| Very low | 0.50 | `claude-3-opus-20260210` | Yes |
| Borderline | 0.84 | `gpt-5.2-pro` | Yes |
| Above threshold | 0.86 | `liquid-lfm-2.5-1.2b` | No |
| High confidence | 0.95 | `liquid-lfm-2.5-1.2b` | No |

**Log Output:**

When `force_confidence` is used, the system logs:

```
{"status": "info", "message": "[Test Mode] Using forced confidence: 0.7"}
{"status": "info", "message": "[Confidence Escalation] Phase dispatcher: 0.70 < 0.85 threshold. Upgrading to Opus 4.6."}
```

**TypeScript Usage:**

```typescript
// For testing via TypeScript bridge
const decision = await smartRouter.getModelForPipelineStep({
    current_phase: "aggregator",
    complexity: 5,
    force_confidence: 0.6  // Force low confidence for testing
});

console.log(decision.modelId);  // "claude-3-opus-20260210"
console.log(decision.escalated); // true
console.log(decision.confidence); // 0.6
```

**Important:** The `force_confidence` parameter is for **testing only**. In production, confidence
is always calculated dynamically based on complexity, iteration, errors, and context size.

---

## Security Sandboxing (February 2026)

### Overview

Agents in the VOS3 pipeline can generate and write files to disk. To prevent malicious or
misconfigured agents from writing to sensitive locations, all file operations are **sandboxed**
using a **defense-in-depth** approach with validation at **both TypeScript and Python layers**.

### Defense in Depth

Both layers validate independently - a bypass in one layer is caught by the other:

```
┌─────────────────────────────────────────────────────────────────┐
│                    DEFENSE IN DEPTH                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   Agent Request: write("../../../etc/passwd", "malicious")      │
│                                      │                           │
│                                      ▼                           │
│   ┌──────────────────────────────────────────────────────────┐  │
│   │  LAYER 1: TypeScript (smart-router-bridge.ts)            │  │
│   │  ────────────────────────────────────────────            │  │
│   │  ✓ Normalize path                                        │  │
│   │  ✓ Block ".." (path traversal)        ← BLOCKED HERE     │  │
│   │  ✓ Block absolute paths                                  │  │
│   │  ✓ Check ALLOWED_WRITE_PATHS allowlist                   │  │
│   └──────────────────────────────────────────────────────────┘  │
│                                      │                           │
│                              (if passed)                         │
│                                      ▼                           │
│   ┌──────────────────────────────────────────────────────────┐  │
│   │  LAYER 2: Python (router_bridge.py)                      │  │
│   │  ──────────────────────────────────                      │  │
│   │  ✓ Normalize path                                        │  │
│   │  ✓ Block ".." (path traversal)        ← BLOCKED AGAIN    │  │
│   │  ✓ Block absolute paths                                  │  │
│   │  ✓ Check ALLOWED_PREFIXES allowlist                      │  │
│   │  ✓ Verify resolved path stays in project root            │  │
│   └──────────────────────────────────────────────────────────┘  │
│                                      │                           │
│                              (if passed)                         │
│                                      ▼                           │
│                           Physical File Write                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Threat Model

| Threat | Mitigation |
|--------|------------|
| Path traversal (`../../../etc/passwd`) | Block any path containing `..` (both layers) |
| Absolute path injection (`/etc/passwd`) | Block all absolute paths (both layers) |
| Unauthorized directory access | Allowlist check (both layers) |
| Symlink escape | `os.path.realpath()` check in Python |
| Sensitive file overwrite | Only code directories are allowed |
| TypeScript bypass | Python validates independently |

### Allowed Write Paths

Agents can **only** write to these directories:

```typescript
const ALLOWED_WRITE_PATHS = [
  "backend/convex/",          // Convex functions
  "frontend/src/components/", // React components
  "frontend/src/hooks/",      // Custom hooks
  "frontend/src/app/",        // Next.js app router
  "frontend/src/types/",      // TypeScript types
  "frontend/src/lib/",        // Utility libraries
  "frontend/src/utils/",      // Helper utilities
  "backend/api/",             // API routes
  "backend/src/",             // Backend source
  "backend/tests/",           // Test files
];
```

### Implementation

**Layer 1: TypeScript (smart-router-bridge.ts)**

```typescript
const ALLOWED_WRITE_PATHS = [
    "backend/convex/",
    "frontend/src/components/",
    "frontend/src/hooks/",
    // ... more paths
];

private validatePath(targetPath: string): boolean {
    const normalizedPath = path.normalize(targetPath).replace(/\\/g, "/");

    // Block path traversal
    if (normalizedPath.includes("..")) {
        console.error(`[Security Alert] Blocked path traversal: ${targetPath}`);
        return false;
    }

    // Block absolute paths
    if (path.isAbsolute(targetPath)) {
        console.error(`[Security Alert] Blocked absolute path: ${targetPath}`);
        return false;
    }

    // Check allowlist
    return ALLOWED_WRITE_PATHS.some(allowed =>
        normalizedPath.startsWith(allowed)
    );
}
```

**Layer 2: Python (router_bridge.py)**

```python
ALLOWED_PREFIXES = [
    "backend/convex/",
    "backend/api/",
    "backend/src/",
    "backend/tests/",
    "frontend/src/",
]

def write_file(self, params: dict) -> dict:
    file_path = params.get("path", "")
    normalized_path = file_path.replace("\\", "/")

    # Block path traversal
    if ".." in normalized_path:
        return {"success": False, "error": "Security Violation: Path traversal blocked"}

    # Block absolute paths
    if os.path.isabs(file_path):
        return {"success": False, "error": "Security Violation: Absolute paths not allowed"}

    # Check allowlist
    if not any(normalized_path.startswith(p) for p in ALLOWED_PREFIXES):
        return {"success": False, "error": f"Security Violation: Path outside sandbox"}

    # Final check: resolved path must stay within project root
    real_path = os.path.realpath(absolute_path)
    real_root = os.path.realpath(vos3_root)
    if not real_path.startswith(real_root):
        return {"success": False, "error": "Security Violation: Path escapes root"}

    # Safe to write
    with open(absolute_path, 'w') as f:
        f.write(content)
```

### Usage Example

```typescript
import { safeWriteFile, safeWriteFiles, isPathAllowed } from "./smart-router-bridge";

// Single file write
await safeWriteFile("frontend/src/components/Button.tsx", buttonCode);

// Batch write (validates ALL paths before writing ANY)
const results = await safeWriteFiles({
    "frontend/src/components/Button.tsx": buttonCode,
    "frontend/src/hooks/useButton.ts": hookCode,
    "backend/api/buttons.py": apiCode
});

// Pre-validation (useful in planning phase)
if (isPathAllowed("frontend/src/components/NewComponent.tsx")) {
    // Path will be allowed
}
```

### Security Errors

When an agent tries to write to an unauthorized path:

```
[Security Violation] Agent tried to access unauthorized path: /etc/passwd
Allowed paths: backend/convex/, frontend/src/components/, ...

Error: Access Denied: Path "/etc/passwd" is not in the allowlist.
Agents can only write to: backend/convex/, frontend/src/components/, ...
```

### Batch Write Protection

For batch writes (`safeWriteFiles`), ALL paths are validated BEFORE any file is written:

```
┌─────────────────────────────────────────────────────────────────┐
│                    BATCH WRITE VALIDATION                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   Input Files:                                                  │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │  "frontend/src/components/A.tsx": "..."                 │   │
│   │  "frontend/src/components/B.tsx": "..."                 │   │
│   │  "../../../etc/passwd": "malicious"        ← BLOCKED    │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                      │                           │
│                                      ▼                           │
│                    ┌──────────────────────────┐                 │
│                    │  Validate ALL paths      │                 │
│                    │  BEFORE writing ANY      │                 │
│                    └──────────────────────────┘                 │
│                           │                │                     │
│               All Valid   │                │  Any Invalid        │
│                           ▼                ▼                     │
│              ┌──────────────────┐  ┌──────────────────┐         │
│              │  Write all files │  │  REJECT ENTIRE   │         │
│              │                  │  │  BATCH           │         │
│              └──────────────────┘  │  (no files       │         │
│                                    │   written)       │         │
│                                    └──────────────────┘         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Monitoring

Security events are logged to stderr:

```
[Security Alert] Blocked path traversal attempt: ../../config/secrets.json
[Security Alert] Blocked absolute path: /Users/admin/.ssh/id_rsa
[Security Violation] Agent tried to access unauthorized path: node_modules/package.json
```
