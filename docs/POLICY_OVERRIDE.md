# vOS Policy Override — Management Control & Logic Flexibility

**Stage:** 10.2.2
**Date:** 2026-05-08
**Status:** kernel surface live; backend service module live; HTTP routes pending Stage 10.3

This document covers the four management contracts shipped in Stage 10.2.2 to make the hallucination guardrail (Stage 10.2) **fully reversible and adjustable by management without a kernel rebuild**, while preserving the full audit trail of every decision.

The four contracts are:

| # | Contract | Lever | Persistence |
|---|----------|-------|-------------|
| 1 | Per-agent / global override | `POLICY_OVERRIDE` VBus, `PolicyOverrideService.set_agent_threshold()` | Backend JSON; not kernel-persistent |
| 2 | Dynamic prompt revision | `register_system_context_reload_hook()`, `reload_system_context_now()` | None (live re-read) |
| 3 | Safe-Rollout kill switch | `VOS_FORCE_PERMIT` env, `POLICY_FORCE_PERMIT` VBus | Env-driven boot default; runtime mutable |
| 4 | Availability guarantee | `evaluate_confidence()` returns `REVIEW_REQUIRED` on bad input | Stateless; per-call decision |

---

## 1. Per-agent / Global Override

The CEO can dial any individual agent's `min_confidence_score` up or down without re-issuing an `IntentManifest`. This is the **light path** — it changes the gate value without touching the TEE measurement chain (RTMR[1] is not extended), so a quick threshold tweak does not invalidate the platform attestation.

### VBus

```
POLICY_OVERRIDE|<slot_id>|<score 0..1000>     →  POLICY_OK|slot=N|gate=G
```

### Backend

```python
from backend.services.policy_override import get_policy_override_service

svc = get_policy_override_service()
svc.set_agent_threshold(slot_id=2, score=850)    # one agent
svc.set_global_floor(score=600)                  # all agents
svc.persist_overrides()                          # JSON snapshot for restart
```

### Why it exists separately from `INTENT_SUBMIT`

`INTENT_SUBMIT` performs a `vos3_tee_slot_activate_bound` extend on RTMR[1]. That is the right path for **a real policy change** (the manifest's bound semantics changed; the auditor must see a new commitment). It is the **wrong path** for "the CEO wants agent #3 to be 5% more cautious for the next hour" — that's a threshold tweak, not a policy change. `POLICY_OVERRIDE` is the light path.

The audit trail still captures every subsequent block / permit decision under the new threshold, so the policy change remains visible downstream.

---

## 2. Dynamic Prompt Revision (Hot-Reload)

The `vOS_System_Context.md` document is the authoritative source-of-truth for what the LLM is allowed to know about the kernel surface (real VBus commands, profile awareness, anti-hallucination capability list). It ships in **Stage 13**; this stage establishes the **hot-reload contract** so the file-watcher can be wired the moment the doc lands.

### Backend

```python
def my_prompt_builder_callback(new_body: str) -> None:
    rebuild_system_prompt(new_body)
    invalidate_in_flight_completions()

svc.register_system_context_reload_hook(my_prompt_builder_callback)

# Trigger:
#  - external file-watcher (inotify / fswatch) detects edit to docs/vOS_System_Context.md
#  - or the management dashboard's "Reload Now" button hits this method
svc.reload_system_context_now()
```

### Operational use case

If the audit ring shows **a spike in hallucination blocks for category #3 (`HALLUCINATION_BLOCK`)** — say, the LLM started inventing a fictitious `SLOT_GUARDIAN` command — the on-call engineer can edit `vOS_System_Context.md` to add an explicit "you do NOT have a SLOT_GUARDIAN command" line, save, and the file-watcher will fan-out the revised body to every active completion stream. **No backend restart, no kernel rebuild, no model re-load.**

### Failure semantics

`reload_system_context_now()` returns `bool` and **never raises**. If the file is missing, unreadable, or malformed, the agent fleet keeps running with the previous in-memory body. A WARNING is logged so ops sees the issue.

---

## 3. Safe-Rollout Kill Switch — `VOS_FORCE_PERMIT`

The kill switch lets management deploy the guardrail in **observe-only mode** before flipping enforcement on. While `VOS_FORCE_PERMIT=1`:

- Every action that **would** be blocked is still recorded in the kernel audit ring — but under the distinct category `VOS3_AUDIT_CAT_FORCE_PERMIT_OVERRIDE` (not `HALLUCINATION_BLOCK`), so a downstream filter can split "real blocks" from "would-have-blocked".
- The action **proceeds** (`vos3_action_bridge_check_confidence` returns 0).
- All other guardrails — IntentManifest validation, TEE bind, allowlist, capability check, trust tier, consensus — remain fully enforced. The kill switch ONLY governs the confidence gate.

### Boot default

The kill switch reads from the `VOS_FORCE_PERMIT` env var at backend startup. Truthy spellings: `1`, `true`, `yes`, `on`, `enable`, `enabled`. Anything else is OFF.

```bash
# Safe-Rollout deploy:
export VOS_FORCE_PERMIT=1
# ...wait two days, study the audit ring...
unset VOS_FORCE_PERMIT
# restart backend: enforcement now on
```

### Runtime override

Either lever flips it without a restart:

```
POLICY_FORCE_PERMIT|0       →  POLICY_OK|force_permit=0    # enforce
POLICY_FORCE_PERMIT|1       →  POLICY_OK|force_permit=1    # observe-only
```

```python
svc.set_force_permit(False)    # enforce
svc.set_force_permit(True)     # observe-only
```

### Reversibility

The kernel never persists the toggle across reboots. After a kernel restart, `vos3_action_bridge_get_force_permit()` returns 0 by default; the backend re-asserts the env value via `apply_env_at_startup()` during FastAPI lifespan boot. **No "stuck observe-only mode after a crash" failure case** — the worst-case after a clean reboot is enforcement-on, which is the safe default.

---

## 4. Availability Guarantee — `REVIEW_REQUIRED` Fallback

The fundamental design constraint: **the org's 50 AI agents must remain operational even when one agent's confidence-emission contract is malformed.** If the LLM stops reporting confidence, fails to report a numeric value, or reports a value outside the documented range, the system must NOT crash and must NOT silently default to "permit". Instead, the action is routed to a human-in-the-loop review queue.

This is implemented in `PolicyOverrideService.evaluate_confidence(slot_id, reported_score)`, which returns one of three states:

| Decision | When | What the caller does |
|----------|------|----------------------|
| `PERMIT` | numeric score, kernel returned `CONF_OK` | execute the action |
| `BLOCK` | numeric score, kernel returned `CONF_BLOCK` | drop the action, surface the block reason |
| `REVIEW_REQUIRED` | score is `None`, NaN, infinity, or outside `[0.0, 1.0]`, OR the kernel was unreachable | enqueue for human review; do not execute, do not crash |

### Trigger conditions for `REVIEW_REQUIRED`

| Reason code (logged) | Trigger |
|----------------------|---------|
| `no_score` | `reported_score is None` (LLM did not produce the field) |
| `non_numeric_score` | the value is not coercible to `float` |
| `score_out_of_range` | NaN, ±inf, or outside `[0.0, 1.0]` |
| `kernel_unreachable` | VBus driver raised an exception |
| `unexpected_reply` | kernel returned neither `CONF_OK` nor `CONF_BLOCK` |

The reason is logged at INFO; the kernel audit ring is **intentionally not touched** for these events because they are backend-detected (no kernel decision was made).

### Why a separate `REVIEW_REQUIRED` instead of "default to BLOCK"

Defaulting to BLOCK would give the right SECURITY answer (no execution on bad input) but the wrong AVAILABILITY answer — every agent that briefly stops emitting confidence values would silently halt, and the operator would not know which actions were lost. `REVIEW_REQUIRED` is **explicit telemetry** that the input was malformed; the human reviewer sees the action with full context (what the agent wanted to do, why the confidence was missing) and can either approve, refine, or reject.

This pattern matches the **Article 22 GDPR** principle of "right to human intervention in automated decision-making" — when the automated system cannot make a high-confidence decision, it routes to a human, it does not silently fail.

---

## 5. Interaction Matrix

The four contracts compose cleanly. The full decision flow on a single agent action:

```
LLM emits action + reported_score
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ Backend: PolicyOverrideService.evaluate_confidence()        │
│                                                             │
│   reported_score is None / NaN / out-of-range               │
│       → REVIEW_REQUIRED   ← contract (4) availability       │
│                                                             │
│   reported_score numeric → forward to kernel                │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ Kernel: cmd_action_check_confidence                         │
│                                                             │
│   gate = g_slot_min_confidence[slot_id]    ← contract (1)   │
│                                              per-agent      │
│                                              override       │
│                                                             │
│   if score >= gate:           → CONF_OK                     │
│   else if force_permit:       → CONF_OK + audit             │
│                                  ← contract (3) Safe-Rollout│
│   else:                       → CONF_BLOCK + audit          │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
   Backend dispatches the action OR drops it OR enqueues
   for human review. Audit ring carries the kernel decision
   in either case. The system-context prompt              ← contract (2)
   (revisable hot via reload_system_context_now)            hot-reload
   continues serving the next request without restart.
```

### Worst-case audit completeness

In every code path above, **at least one of the following is recorded**:

- Kernel audit ring entry under one of the categories `HALLUCINATION_BLOCK` / `FORCE_PERMIT_OVERRIDE` (when the kernel made a decision).
- Backend INFO log with reason code (when the backend made a `REVIEW_REQUIRED` decision before reaching the kernel).

There is no path where an action is silently dropped or silently executed. This is the auditability guarantee a regulator looking at EU AI Act Article 73 (incident reporting) cares about.

---

## 6. Operational Playbook

### "Hallucination spike on agent #2 — what do I do?"

```
1. Read the audit ring:
     POLICY_STATUS                            (current gates)
     AUDIT_FAIL_QUOTE                          (recent rejections)

2. Identify the agent's slot. Tighten its threshold:
     svc.set_agent_threshold(slot_id=2, score=950)

3. If a hallucination pattern matches a missing capability fact in the
   system-context, edit docs/vOS_System_Context.md and:
     svc.reload_system_context_now()

4. Persist so a backend restart re-applies your decision:
     svc.persist_overrides()
```

### "I am rolling this out for the first time and want to observe before enforcing"

```
1. Set env var: VOS_FORCE_PERMIT=1
2. Restart backend.
3. After 24-48 hours, look at audit-ring entries with category
   FORCE_PERMIT_OVERRIDE — these are the actions you WOULD have blocked.
   Review them with the model owner.
4. If the would-block rate is acceptable: unset VOS_FORCE_PERMIT, restart.
5. If the would-block rate is too high: tune the per-agent thresholds
   downward (set_agent_threshold), then unset and restart.
```

### "My LLM stopped emitting confidence and 17 actions are stuck"

```
1. The 17 actions are in REVIEW_REQUIRED, not crashed. The fleet is up.
2. Surface the human-review queue to your on-call.
3. Fix the model's confidence-emission contract.
4. Once fixed: replay the queue (each action arrives with its original
   slot_id + reported_score) — the system processes them through the
   normal evaluate_confidence path, no special handling required.
```

---

## 7. What's NOT yet built

For full transparency about Stage 10.2.2's scope:

| Deliverable | Status |
|-------------|--------|
| Kernel `POLICY_OVERRIDE` / `POLICY_FORCE_PERMIT` / `POLICY_STATUS` VBus commands | ✅ Live |
| Kernel `g_force_permit` toggle + `FORCE_PERMIT_OVERRIDE` audit category | ✅ Live |
| Backend `PolicyOverrideService` module (this doc's reference impl) | ✅ Live |
| `vOS_System_Context.md` document itself | ❌ Stage 13 |
| HTTP routes (`/api/policy/override`, `/api/policy/force-permit`, `/api/policy/status`) | ❌ Stage 10.3 (depends on the broader backend port) |
| Management dashboard UI | ❌ Stage 13/14 (frontend overlay) |
| File-watcher daemon for `docs/vOS_System_Context.md` | ❌ Stage 13 (depends on the doc landing) |
| `pytest` integration tests for the override service | ❌ Stage 12 (depends on the test-suite port) |

The kernel surface is complete and frozen for Stage 10.2.2 — every backend / frontend follow-on calls these primitives without requiring further kernel changes.
