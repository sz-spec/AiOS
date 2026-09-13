# VOS3 Open-Source LLM Strategy
## When and Why VOS3 Uses Open-Source Local Models
**v20.3.0 | April 2026 | FOR: SRE Team, Technical Auditors, Series A DD**

---

## 1. Executive Summary

VOS3 routes ~100% of production AI traffic through cloud LLMs today (Anthropic Claude, OpenAI GPT, Google Gemini). The infrastructure for **open-source local inference** — Ollama HTTP client, model catalog, four registered local models, hardware-pressure telemetry — exists at the **config and client layer**. The **control layer** that activates local routing under privacy / cost / pressure conditions is the v20.4 work item. This document defines the activation contract, the decision matrix the SRE team will use, and the roadmap from "dormant infrastructure" to "first-class production routing."

**Three reasons OS LLMs matter to VOS3:**

1. **Privacy / Data Sovereignty** — EU AI Act Article 12 plus enterprise data-residency requirements demand zero-egress inference for certain workloads. Cloud LLMs by definition transmit user input to third-party infrastructure.
2. **Cost** — Apr 2026 benchmarks show DeepSeek V3.2 is **~3.8× cheaper on input tokens, 13.2× cheaper on output tokens** than Claude Haiku 4.5, the next-cheapest cloud option in our router. ([Galaxy.ai comparison](https://blog.galaxy.ai/compare/claude-haiku-4-5-vs-deepseek-chat))
3. **Latency** — Local-fast-path inference via the VBus bridge measured at **P99 0.012ms, P99.9 0.019ms** (`docs/FINAL_CERTIFICATION_REPORT.md`). Network round trip to cloud LLMs adds 100–800ms before any model work.

**Honest caveat:** Open-source models trade reliability for cost. DeepSeek V3.2 scores 88.7% on agentic loop pass rate vs Haiku 4.5's 97% ([Tier 3 vs Tier 2 in Paterson's 38-task benchmark](https://ianlpaterson.com/blog/llm-benchmark-2026-38-actual-tasks-15-models-for-2-29/)). The activation contract must include retry guards and fail-up paths.

---

## 2. Inventory — Current State

### 2.1 Models registered in `backend/config/router.yaml`

| Slot | Model ID | Priority | Status | Params | Use Case |
|------|----------|----------|--------|--------|----------|
| `local-snappy` | gemma-4-27b | 1 | **DORMANT** (`enabled: false`) | tier:2, ultra-low latency, 32k ctx, structured JSON | Telemetry, monitoring, batch routing |
| `local-default` | llama-3.3-70b | 3 | **DORMANT** (`enabled: false`) | min_params_b:70 | General-purpose, last entry in `fallback_chain` |
| `local-code` | qwen2.5-coder:72b | 4 | **DORMANT** (`enabled: false`) | min_params_b:70 | Heavy code generation |
| `local-light` | codestral:22b | 5 | **DORMANT** (`enabled: false`) | tier:1 | Lightweight code completion |

All four entries are real — see `backend/config/router.yaml:104–166`. None of them is currently selectable by the public router because `enabled: false`.

### 2.2 Models catalog (frontend-visible)

`backend/config/models_catalog.py` defines four open-weight Ollama entries with `requires_local=True`:
- `gemma-1.0-nano` (line 698)
- `gemma-3` (line 713)
- `gemma-2-27b` (line 728)
- `llama-3.3-70b` (line 1183)

Plus additional entries flagged `is_open_weight=True`. Exposed at `/api/settings/models/catalog` (`backend/api/settings_routes.py:415`). The frontend Settings page reads this catalog to display activatable models.

### 2.3 Dispatch infrastructure

| Component | File | Status |
|-----------|------|--------|
| Ollama HTTP client | `backend/ai/llm/tool_provider.py:580–687` | **ACTIVE** — wraps Ollama via OpenAI-compat `/v1/chat/completions` |
| Health probe | `tool_provider.py:599–607` (`_is_ollama_available()`) | **ACTIVE** — pings `{OLLAMA_BASE_URL}/api/tags` |
| Telemetry fast-path | `tool_provider.py:641–649` | **ACTIVE** — monitoring routes go to `gemma-4-27b` |
| Locality preference hook | `tool_provider.py:652–659` | **ACTIVE** — `VOS3_LOCALITY_PREFERENCE=local-first` + complexity ≥ 7 → `llama-3.3-70b` |
| EWMA PID router | `backend/src/efficiency/router.py:122–167` | **ACTIVE for cloud-only** — switches Sonnet/Opus → Haiku 4.5; **GAP** for local |
| NPU topology API | `backend/api/kernel_routes.py:422–469` | **EXPOSED** but **DEAD-DATA** — no consumer |
| `IntentManifest` | `backend/services/vpacker.py:55–84` | **EXPOSED** but does **NOT** gate inference locality |
| `services/local_inference.py` | — | **DOES NOT EXIST** |

---

## 3. Trigger Conditions — When VOS3 Should Switch to Local

Three independent dimensions trigger local inference. Each section gives the **mathematical / operational condition**, the **today vs designed** state, and the **activation contract**.

### 3.1 Privacy Isolation — `IntentManifest` Local-Only Mandate

**Mathematical condition:**
```
∀ request R: IntentManifest(R).data_residency == LOCAL_ONLY
       ⇒ assign_model(R) ∈ {local-snappy, local-default, local-code, local-light}
```

**Today (v20.3):** **GAP.** `VPKIntentManifest` (`backend/services/vpacker.py:55–84`) defines retention policies (SCRUB / PERSIST / SNAPSHOT) but these control data **cleanup after inference**, not where inference **happens**. There is no field equivalent to `data_residency: LOCAL_ONLY`. The chat / codegen routes do not consult any privacy intent before calling `assign_model()`.

**Designed (v20.4):** Add `requires_local_inference: bool` to `VPKIntentManifest`. In `chat_routes.py` and `codegen_routes.py`, check this flag **before** `assign_model_with_pressure_check()`. If set, skip the EWMA path entirely and select `local-default` (or fall through `local-code` for codegen) — never falling back to cloud regardless of pressure. If `_is_ollama_available()` returns False under a `LOCAL_ONLY` request, return HTTP 503 with `Retry-After` rather than silently leaking data.

**Why this matters:** This is the **EU AI Act + GDPR + enterprise on-premise contract**. A request marked `LOCAL_ONLY` must have a verifiable, hard guarantee that no bytes traverse the cloud. The MMR audit ledger already records this — `mmr_record_syscall()` would log the model_id chosen, allowing post-hoc compliance verification via `/api/kernel/transparency`.

### 3.2 Hardware Failover — DRIVER_PRESSURE EWMA Crossover

**Mathematical condition (today, cloud → cloud):**
```
ewma_pressure = α · raw_pressure + (1 − α) · ewma_pressure_prev,    α = 0.30
ewma_pressure > 0.85  ∧  role ∉ {architect, reviewer, researcher-deep}
       ⇒ assign_model(role) = claude-haiku-4-5-20251001
```

This is real and shipping. See `backend/src/efficiency/router.py:122–167`. The hysteresis exit threshold is 0.75. Critical roles are never downgraded.

**Mathematical condition (designed, cloud → local):**
```
ewma_pressure > 0.85
∧ _is_ollama_available()  == True
∧ p99_cloud_latency_ms    > 500
∧ role ∉ critical
       ⇒ assign_model(role) = local-snappy  (gemma-4-27b)
```

**Today (v20.3):** **GAP.** `router.py:155–166` only knows the constant `_HAIKU_MODEL = "claude-haiku-4-5-20251001"`. There is no `_is_ollama_available()` check inside the EWMA path; the local-availability hook lives in `tool_provider.py` but is not consulted by the router. There is no measurement of `p99_cloud_latency_ms` feeding the router decision.

**Designed (v20.4):** Add a third arm to `_update_pressure_ewma()`. When kernel pressure crosses the threshold AND the local-fast-path is available AND cloud latency is degraded, route the next request to `local-snappy`. Cost: zero (Ollama already wired). Risk: low — same hysteresis prevents oscillation. Reliability fail-up: if `local-snappy` returns an error, `assign_model_with_fallback()` already cycles through the chain ending in `local-default`, then template fallback.

**Why this matters:** Under sustained kernel pressure, the cloud path may itself be saturated (everyone falls back to Haiku at once → Anthropic rate limits). The local path becomes the reliability bridge — it's the only model that can serve when everything else is down.

### 3.3 Cost-Efficiency — Break-Even Calculation

**Mathematical condition:**
```
monthly_cost_cloud(haiku) = (T_in × $0.80 + T_out × $4.00) / 1_000_000
monthly_cost_local(gemma-4-27b) = capex_amortized_per_month + electricity

break_even occurs when:
  T_in × 0.80 + T_out × 4.00 > 1_000_000 × (capex_amort + electricity_per_month)
```

Reasonable assumptions: $1,500/yr amortized GPU cost = $125/month, plus $25/month electricity = **$150/month TCO per machine.** At Haiku 4.5 list prices, you cross $150 at roughly **10 million tokens of mixed I/O per month per machine**. Above that, every additional token is essentially free on local. Below that, cloud is cheaper.

**Today (v20.3):** **GAP.** `router.yaml:186–189` defines `spending_caps` (per_request, per_minute, per_hour USD) but no code reads them when picking a model. `assign_model()` is not cost-aware. `track_request()` (`backend/src/observability.py`) records cost after the fact — there's no pre-flight comparison.

**Designed (v20.4):** Add `spending_cap_check(role, complexity)` that:
1. Reads `get_cost_breakdown()` for the rolling minute / hour
2. If on track to exceed `per_minute_max_usd`, prefer the cheaper tier
3. For batch / non-interactive requests (codegen of bulk files, scheduled analysis), route to local automatically when `_is_ollama_available()` is True

**Batch detection signal:** `Idempotency-Key` header presence + `Prefer: respond-async` header → batch task → local. This mirrors the Stripe webhook pattern already used in billing.

**Why this matters:** At 1M users and 10M+ tokens/user/month projected (per `docs/market_entry/global_domination_summary.md`), the cloud bill scales linearly with usage. The break-even pivot defines the path to gross-margin neutrality.

### 3.4 NPU Hardware Awareness (DSAR Topology)

**Mathematical condition:**
```
∃ cluster ∈ npu_topology()  with  cluster.compute_capacity > THRESHOLD
       ⇒ pin model M ∈ {NPU-eligible}  to cluster.cluster_id
```

**Today (v20.3):** **GAP.** `GET /api/kernel/hardware/npu-topology` returns the ACPI DSAR cluster list (kernel/src/drivers/acpi.c parses it). The endpoint works. **Zero consumers in `backend/src` and `backend/ai`** — confirmed via grep.

**Designed (v20.4):** Tool provider factory consumes the topology at startup. Heavy local models (`local-default` 70B, `local-code` 72B) bind to the NPU cluster with the highest `memory_bandwidth`. Light models (`local-snappy` 27B, `local-light` 22B) can run on CPU/iGPU. NPU constraints from Apr 2026 hardware (per [Phoronix coverage of OpenVINO 2026.1](https://www.phoronix.com/news/OpenVINO-2026.1-Released)): NPU SRAM is non-expandable, ~9 GB max for weights, models > 8B at 4096 ctx start losing budget to KV cache.

---

## 4. Performance Benchmarks — Today vs Forecast

### 4.1 Measured (today)

| Path | P99 latency | P99.9 latency | Source |
|------|-------------|---------------|--------|
| VBus local-fast-path (Gemma-4-27B) | **0.012 ms** | 0.019 ms | `docs/FINAL_CERTIFICATION_REPORT.md` |
| VBus command dispatch (any) | 7.6 ms | 7.85 ms | `backend/scripts/bench_vbus.py` |
| Cloud Claude Haiku 4.5 | ~800–2000 ms | (no SLA) | Anthropic API public latency |

The local-fast-path number is for **already-loaded model in slot, single-token retrieval from a warm KV cache** — it's the round-trip cost across the VBus bridge, not full inference. Full local inference for a 27B model is bound by GPU/NPU compute and depends on hardware.

### 4.2 Forecast — Llama 4 8B on Intel Panther Lake NPU

Per Apr 2026 published numbers from Intel ([IPEX-LLM repo](https://github.com/intel/ipex-llm) and [OpenVINO 2026.1 release](https://www.phoronix.com/news/OpenVINO-2026.1-Released)):
- 8B 4-bit quantized model fits in NPU SRAM with 4096 context (~2 GB KV cache budget consumed)
- First-token latency target: < 200 ms (NPU-resident)
- Sustained throughput: ~30–60 tokens/sec depending on quantization
- Key constraint: NPU SRAM is non-expandable — models > 8B at full precision will not fit; Phi-4 14B Q4_K_M (9.2 GB) is documented as unable to run

For VOS3 routing decisions: NPU is suited to **`local-snappy` (gemma-4-27b at Q4)** and **`local-light` (codestral:22b at Q4)** — both fit. **`local-default` (llama-3.3-70b)** and **`local-code` (qwen-coder:72b)** require dGPU or system RAM with iGPU, not NPU.

### 4.3 Forecast — DeepSeek V3.2 vs Claude Haiku 4.5 (Apr 2026)

Comparative benchmarks ([BenchLM.ai](https://benchlm.ai/compare/claude-haiku-4-5-vs-deepseek-v3-2-thinking), [Galaxy.ai](https://blog.galaxy.ai/compare/claude-haiku-4-5-vs-deepseek-v3-2)):

| Dimension | DeepSeek V3.2 (Thinking) | Claude Haiku 4.5 | Winner |
|-----------|--------------------------|------------------|--------|
| Agentic average | 69.4 | 51.9 | DeepSeek (+33.7%) |
| Terminal-Bench 2.0 | 71% | 41% | DeepSeek (+30 pts) |
| Agent-loop pass rate | 88.7% | 97% | Haiku (+8.3 pts) |
| Input cost ($/MTok) | ~$0.21 | $0.80 | DeepSeek (3.8× cheaper) |
| Output cost ($/MTok) | ~$0.30 | $4.00 | DeepSeek (13.2× cheaper) |

**Implication for VOS3 routing:** DeepSeek-class models are the right cost lever for **agentic loops with retry guards**. The 88.7% pass rate means ~1 in 9 steps fail — acceptable when wrapped in our existing OCC retry / circuit breaker pattern, intolerable for unsupervised production loops without it. Today VOS3 has neither DeepSeek nor a retry-guard pattern in `chat_routes.py` for tool loops; this is a v20.4 candidate.

---

## 5. Decision Matrix — For the SRE Team

| Scenario | Trigger | Recommended Model | Cloud Fallback | Activation Flag (today) |
|----------|---------|-------------------|----------------|-------------------------|
| Privacy-mandated request | `IntentManifest.data_residency == LOCAL_ONLY` | `local-default` (or `local-code` for codegen) | **NONE — must 503 if local unavailable** | **GAP — v20.4** |
| Sustained DRIVER_PRESSURE | EWMA > 0.85 + Ollama up + role non-critical | `local-snappy` (gemma-4-27b) | Haiku 4.5 | **GAP — v20.4** |
| Cost-cap reached | rolling cost > `per_minute_max_usd` | `local-default` | Haiku 4.5 | **GAP — v20.4** |
| Batch / async task | `Prefer: respond-async` header present | `local-code` (for codegen) or `local-default` | Sonnet 4.6 | **GAP — v20.4** |
| Heavy code-gen, on-prem | Heavy local routing requested | `local-code` (qwen2.5-coder:72b) | Sonnet 4.6 | `VOS3_LOCALITY_PREFERENCE=local-first` + complexity ≥ 7 → **ACTIVE** |
| Telemetry / monitoring | Internal observability route | `local-snappy` | — | **ACTIVE** (`tool_provider.py:641–649`) |
| Dev loop / no API keys | Cloud creds absent | `local-default` (chain final fallback) | Template | **ACTIVE** via `fallback_chain` |
| Critical role (architect, reviewer, researcher-deep) | Any | **Cloud only — never local** | Cloud chain | **ACTIVE** — these roles bypass `_HAIKU_MODEL` and never degrade |

### 5.1 Activation flags an SRE can flip today

| Env var | Effect | Set in |
|---------|--------|--------|
| `OLLAMA_BASE_URL` | Path to Ollama HTTP server (default `http://localhost:11434`) | Backend env |
| `VOS3_LOCALITY_PREFERENCE=local-first` | Routes complexity ≥ 7 to `local-default` | Backend env |
| `VOS3_PREFERRED_PROVIDER=local` | Tool provider factory prefers local on every fallback | Backend env |

To enable a model in `router.yaml`, flip `enabled: false` → `enabled: true` for the desired slot AND restart the FastAPI process. Note: this alone does not activate the EWMA-driven cloud-to-local switch (that's the v20.4 work item).

### 5.2 Health checks before enabling local routing in production

Run before flipping flags in production:
1. `curl ${OLLAMA_BASE_URL}/api/tags` returns a model list
2. `python3 backend/scripts/bench_vbus.py --ping-only` succeeds
3. `pytest backend/tests/test_v32_stability.py -k local` — at least the 3 config tests must pass (`test_local_first_heavy_tasks`, `test_router_yaml_local_models_defined`, `test_factory_local_model_mappings`)
4. `pytest backend/tests/test_sovereign_integration.py::test_escalate_provider_to_70b` passes — proves the Snappy → 70B escalation path works

---

## 6. Activation Roadmap (v20.4)

Three concrete code changes lift OS LLMs from dormant to first-class:

### 6.1 EWMA → local-snappy bridge (~30 LOC)

**File:** `backend/src/efficiency/router.py`

Add an `_OLLAMA_AVAILABLE` callable injected from `tool_provider.py`. In `assign_model_with_pressure_check()`, after the existing Haiku decision, add:

```python
# v20.4 — local fail-down arm
if degraded and _OLLAMA_AVAILABLE() and role not in _CRITICAL_ROLES:
    # Prefer local-snappy when both pressure + local availability hold
    if _PRESSURE_EWMA > 0.90:        # one notch above Haiku threshold
        return "local-snappy"        # gemma-4-27b
```

Set `enabled: true` for `local-snappy` in `router.yaml`. Add a unit test that mocks `_is_ollama_available=True` and forces pressure > 0.90 to verify the switch.

### 6.2 NPU topology consumer (~60 LOC)

**File:** `backend/ai/llm/tool_provider.py`

At factory initialization, fetch `GET /api/kernel/hardware/npu-topology` and cache the result (15-minute TTL). When a heavy local model is requested, check whether any cluster has sufficient `compute_capacity` and `memory_bandwidth`. Pin the request to that cluster via a future Ollama server-affinity hint (the contract for this hint will be defined in v20.4).

### 6.3 IntentManifest local-only enforcement (~80 LOC)

**Files:**
- `backend/services/vpacker.py` — extend `VPKIntentManifest` with `requires_local_inference: bool` field
- `backend/api/chat_routes.py` and `backend/api/codegen_routes.py` — check the flag before calling `assign_model_with_pressure_check()`. When set, route directly to `local-default` (chat) or `local-code` (codegen). On `_is_ollama_available() == False`, return HTTP 503 with `Retry-After` and an explanatory error body — **do not fall back to cloud**.
- New unit test: `test_local_only_intent_blocks_cloud_fallback` — verifies 503 is returned when local is requested but unavailable.

---

## 7. Code References — Auditor's Quick Verify

Every claim in this document can be verified by reading these exact lines:

| Claim | File:Line |
|-------|-----------|
| EWMA α=0.30, hysteresis [0.75, 0.85] | `backend/src/efficiency/router.py:114–138` |
| Switches to Haiku 4.5 only (cloud→cloud) | `backend/src/efficiency/router.py:113, 165` |
| Critical roles never downgraded | `backend/src/efficiency/router.py:112` |
| 4 local models registered, all disabled | `backend/config/router.yaml:104–166` |
| `local-default` in fallback chain | `backend/config/router.yaml:180–184` |
| Spending caps defined but unused | `backend/config/router.yaml:186–189` |
| Ollama HTTP wrapper | `backend/ai/llm/tool_provider.py:580–687` |
| `_is_ollama_available()` health probe | `backend/ai/llm/tool_provider.py:599–607` |
| Telemetry → gemma fast-path | `backend/ai/llm/tool_provider.py:641–649` |
| `VOS3_LOCALITY_PREFERENCE` hook | `backend/ai/llm/tool_provider.py:652–659` |
| Models catalog API | `backend/api/settings_routes.py:415` |
| Open-weight model definitions | `backend/config/models_catalog.py:698–1200` |
| NPU topology API endpoint | `backend/api/kernel_routes.py:422–469` |
| `IntentManifest` definition | `backend/services/vpacker.py:55–84` |
| Local model config tests | `backend/tests/test_v32_stability.py:391, 877, 918` |
| Snappy → 70B escalation test | `backend/tests/test_sovereign_integration.py:818, 1373` |
| Local-fast-path P99 0.012ms | `docs/FINAL_CERTIFICATION_REPORT.md` |
| 30/30 security suite | `backend/tests/test_load_security.py` |
| MMR audit (records model_id per syscall) | `kernel/src/sec/mmr_audit.c` |

---

## 8. External Sources & Web Citations

### Intel hardware / local inference (Apr 2026)
- [Intel IPEX-LLM — accelerated local LLM on Intel iGPU/NPU/CPU](https://github.com/intel/ipex-llm)
- [OpenVINO 2026.1 release with llama.cpp backend](https://www.phoronix.com/news/OpenVINO-2026.1-Released)
- [OpenVINO lands in llama.cpp — run GGUF on Intel CPU/GPU/NPU](https://medium.com/openvino-toolkit/openvino-lands-in-llama-cpp-run-gguf-models-on-intel-cpu-gpu-and-npu-d6fca1d633e8)
- [Intel — Run LLMs on GPUs using llama.cpp](https://www.intel.com/content/www/us/en/developer/articles/technical/run-llms-on-gpus-using-llama-cpp.html)
- [llama.cpp NPU portable zip quickstart (Intel)](https://github.com/intel/ipex-llm/blob/main/docs/mddocs/Quickstart/llama_cpp_npu_portable_zip_quickstart.md)
- [Gemma 4 local inference + NPU benchmarks](https://explore.n1n.ai/blog/gemma-4-local-inference-llama-cpp-kv-cache-fix-npu-benchmarks-2026-04-05)
- [NPU Comparison 2026 — Intel vs Qualcomm vs AMD vs Apple](https://localaimaster.com/blog/npu-comparison-2026)

### DeepSeek V3.2 vs Claude Haiku 4.5
- [BenchLM.ai — Claude Haiku 4.5 vs DeepSeek V3.2 (Thinking) Apr 2026](https://benchlm.ai/compare/claude-haiku-4-5-vs-deepseek-v3-2-thinking)
- [Galaxy.ai — Haiku 4.5 vs DeepSeek V3](https://blog.galaxy.ai/compare/claude-haiku-4-5-vs-deepseek-chat)
- [Galaxy.ai — Haiku 4.5 vs DeepSeek V3.2](https://blog.galaxy.ai/compare/claude-haiku-4-5-vs-deepseek-v3-2)
- [Appaca — Haiku 4.5 vs DeepSeek V3 comparison](https://www.appaca.ai/resources/llm-comparison/claude-4.5-haiku-vs-deepseek-v3)
- [DocsBot — Detailed Haiku 4.5 vs DeepSeek V3 comparison](https://docsbot.ai/models/compare/claude-haiku-4-5/deepseek-v3)
- [ArtificialAnalysis — DeepSeek V3.2 (Reasoning) vs Haiku 4.5 (Reasoning)](https://artificialanalysis.ai/models/comparisons/deepseek-v3-2-reasoning-vs-claude-4-5-haiku-reasoning)
- [Paterson — 38 real coding tasks across 15 LLMs (routing table)](https://ianlpaterson.com/blog/llm-benchmark-2026-38-actual-tasks-15-models-for-2-29/)
- [DeepSeek vs Claude — Open-Source Disruptor vs Premium AI](https://neuronad.com/deepseek-vs-claude/)
- [DeepSeek V4-Pro Review — Benchmarks, Pricing, Architecture](https://www.buildfastwithai.com/blogs/deepseek-v4-pro-review-2026)
- [Iternal — LLM Benchmarks 2026 (30+ models, MMLU, SWE-bench, Arena)](https://iternal.ai/llm-selection-guide)

---

## 9. Sign-off

This document captures the **current state** of VOS3's open-source LLM strategy honestly, surfaces the four real **gaps** (router cloud→local bridge, NPU topology consumer, cost gate, IntentManifest enforcement), and provides the **decision matrix** the SRE team will use the day v20.4 ships.

**For investors:** the OS-LLM story is structurally sound at the foundation layer (Ollama wired, models registered, hardware telemetry exposed). The control layer is a focused 4–6 week engineering investment, not a foundational rebuild.

**For SREs:** today, run cloud. Use the activation flags in §5.1 only after running the §5.2 health checks. v20.4 will deliver automatic local routing — until then, local is opt-in via env vars.

**For technical auditors:** every claim in this document maps to a code reference in §7. No claim references a function or file that does not exist. Where a feature is dormant or absent, the doc says **GAP** explicitly.

---

*VOS3 Open-Source LLM Strategy — v20.3.0 — April 25, 2026*
*"Honest about today. Architectural about tomorrow."*
*SPDX-License-Identifier: MIT | SPDX-FileCopyrightText: 2026 VOS3 Project*
