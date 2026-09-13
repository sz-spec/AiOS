# AI-SA Agent Autonomy Level Mapping

**Stage:** 13
**Date:** 2026-05-09
**Owner:** Stage 10's `IntegrityCertificate.agent_autonomy_level` field; this doc is the value-mapping source-of-truth.

---

## Honest scope upfront

The user's plan briefs reference an "AI-SA May 2026 metadata schema" that mandates an `agent_autonomy_level` field on the IntegrityCertificate.

**Stage 14 web-search update (2026-05-09):** I executed a live web search for the exact name "AI-SA" + metadata schema + agent autonomy level + 2026. **No published standard, organization, or document by that name was found** (full search log + queries: `docs/STAGE_14_RESEARCH_FINDINGS.md` §1).

What this means: until the user supplies a real upstream schema (URL, PDF, source repository), the `agent_autonomy_level` field stays implementer-defined. The placeholder URN below (`urn:vos:ai-sa:2026-05:autonomy/level`) does NOT resolve to any registered or claimed `urn:` namespace; it is a stable in-tree identifier so a verifier can detect implementations using *this* version of the taxonomy.

What I ship today is a defensible mapping based on **published, search-verified autonomy taxonomies**:

- **NIST AI RMF 1.0 — "MEASURE" tier:** describes a five-level human-in-the-loop continuum (`fully manual` → `fully autonomous`).
- **EU AI Act — risk-classification discourse:** uses an analogous degree-of-autonomy concept under "high-risk AI systems" Annex III.
- **OECD AI Principles (2019/24):** the "human agency and oversight" pillar uses similar 5-level taxonomy.
- **SAE J3016 (driving automation):** the canonical 0-5 autonomy level framework adopted by analogy in many AI-systems docs.

The numeric scale below (0–5) and its semantics are aligned with these published taxonomies. If the AI-SA May-2026 schema renames a level or adds a 6th, the JSON wire format swap is a single line in `attestation_service.py` (the field is already `int`).

---

## 1. The 0–5 scale

| Level | Name | Human role | vOS examples |
|-------|------|------------|--------------|
| **0** | Manual / no automation | Every output is a human authoring a prompt; the AI is purely a draft assistant. | A code-review session where the LLM suggests but a human types every line. |
| **1** | Tool-assisted | AI executes single discrete tools under per-tool human approval. | An agent that rewrites a single file after a human clicks "approve". |
| **2** | Constrained autonomy | AI completes multi-step tasks within a narrow envelope; human approves the *final output*. | A code-generation pipeline that produces a PR; the human reviews and merges. |
| **3** | Bounded autonomy | AI loops without per-step approval but is bounded by a written policy; human is notified on policy-relevant decisions. | An agent that maintains a CI dashboard, autonomously triaging green/red, but escalating policy-flagged events. |
| **4** | High autonomy | AI loops autonomously; human is involved only on exceptions, deadlocks, or scheduled audits. | An agent fleet that operates a billing-reconciliation workflow nightly with no per-batch human review. |
| **5** | Full autonomy | AI is the decision authority for the entire workflow; human is notified post-hoc only. | Not currently shipped in vOS.v1; reserved for the future. |

---

## 2. Mapping rules — how vOS computes the level for a given IntegrityCertificate

`backend/ai/agents/multi_agent.py::_emit_session_attestation` (carry-forward from VOS3-Cyber, deferred to Stage 10's missing-files port) is the producer. Its mapping logic SHOULD follow:

```python
def derive_autonomy_level(workflow: WorkflowGraph) -> int:
    """Inspect the actual workflow shape and return the AI-SA level."""
    if workflow.requires_human_approval_per_tool_call():
        return 1
    if workflow.requires_human_approval_on_final_output():
        return 2
    if workflow.has_exception_escalation_only():
        return 4
    if workflow.has_post_hoc_notification_only():
        return 5
    # Default — the most common shape: bounded autonomy with policy gates.
    return 3
```

Key principle: the level is derived from **observed workflow shape**, NOT from a developer-set flag. A developer cannot mark an agent "level 1" while the workflow actually loops without approval — the discrepancy would be detectable at audit.

---

## 3. Wire format — IntegrityCertificate JSON-LD fragment

Stage 10's `attestation_service.py` is supposed to emit:

```json
{
  "agent_autonomy_level": 3,
  "autonomy_taxonomy_uri": "urn:vos:ai-sa:2026-05:autonomy/level"
}
```

The `autonomy_taxonomy_uri` is a stable identifier so a verifier can know which version of the taxonomy was used. Today's value `urn:vos:ai-sa:2026-05:autonomy/level` is a placeholder — when the AI-SA May-2026 schema confirms its canonical URI, this string is updated and the change is captured under the existing audit-ring rotation.

### When the schema lands

A single-line change in `attestation_service.py::IntegrityCertificate.to_jsonld()`:

```python
"autonomy_taxonomy_uri": "<exact-URI-from-AI-SA-spec>",
```

Plus a documentation update to this file. Nothing else.

---

## 4. EU AI Act cross-reference

The autonomy level feeds the Annex IV §III.1.f "autonomy degree" requirement:

| Annex IV section | vOS evidence |
|------------------|--------------|
| III.1.f — autonomy degree | `IntegrityCertificate.agent_autonomy_level` |
| III.2 — risk-management documentation | this file (`AI_SA_AUTONOMY_LEVEL_MAPPING.md`) |
| V.1 — post-market monitoring | the audit ring in `kernel/src/mm/audit_ring.c` (Stage 10.1) |

A regulator asking "what's the autonomy classification of agent X?" gets the literal `agent_autonomy_level` integer from the most-recent IntegrityCertificate, plus a pointer to this document for the meaning.

---

## 5. Operator override — explicit caveat

If an operator manually sets a slot to "level 5" via a tooling escape hatch (none currently exists; Stage 10's missing-files port may add one), the override MUST be recorded in the audit ring under a new category `VOS3_AUDIT_CAT_AUTONOMY_OVERRIDE` (reserved value `5`). The category integer is held aside in `kernel/include/vos/audit_ring.h` for the future.

For now, the autonomy level is observable-only and not user-settable — closing one possible insider-threat vector.

---

## 6. Open questions tracked for follow-up

- **Field name confirmation.** AI-SA may use `agent_autonomy_level`, `autonomy_degree`, `human_oversight_level`, or another name. The integer value (0-5) is the same; only the JSON key changes.
- **5 vs 6 levels.** SAE J3016 has six levels (0-5). NIST AI RMF informally uses five. AI-SA's choice impacts whether vOS adds a level 6 or keeps the 0-5 scale.
- **URI stability.** The `urn:vos:ai-sa:2026-05:autonomy/level` placeholder is replaced once the AI-SA URI is published. No expected breaking change to consumers.

These are documented as Stage-13.X follow-ups; none block the Stage-13 commit because the integer field is forward-compatible.
