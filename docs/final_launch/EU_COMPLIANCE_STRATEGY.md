<!--
SCAFFOLD: Reconstructed on 2026-05-01 due to data loss. Integrity vs
original v20.6 ELF not guaranteed. Sections 1–2 (lines 1..32) are
byte-faithful from a transcript Read. Sections 3+ are scaffolded
from the matching enforcement code in backend/services/regional_policy.py
and the sister file docs/final_launch/EU_COMPLIANCE_LOCKDOWN.md.
-->

# VOS3 EU AI Act Article 12 — Compliance Strategy
## Mandatory Local-First Inference for EU/EEA Users
**v20.3.0 | April 2026 | FOR: Legal, Compliance, SRE, Auditors**

---

## 1. Statement of Compliance

VOS3 v20.3 ships **regional routing enforcement** that satisfies the data-residency
and automatic-logging requirements of the EU AI Act, in particular:

- **Article 12** — automatic event logging without operator intervention, tamper-evident
- **Annex III** — high-risk AI systems must maintain compliant audit trails
- **Enforcement deadline** — 2026-08-02
- **Penalty for non-compliance** — up to €15M or 3% of worldwide annual turnover

Every authenticated request originating from an EU or EEA member state is now
**routed exclusively** to a sovereign inference path. There is no possibility
of silent fallback to a US-based cloud LLM. When no compliant route is
available, the request is **denied with HTTP 403** rather than served by a
non-compliant provider.

The policy lives in `backend/services/regional_policy.py` — a generalized
**Sovereign Zone** module. EU/EEA is the only zone activated in v20.3. The
file is structured so that adding future zones (IL, CH, UK post-DPDI, JP
under APPI) is a config-only change. Today, **users from IL, US, GB, JP, CH
and all other non-EU regions route via the standard EWMA PID controller** —
the EU lockdown does not affect them. This exclusion invariant is guarded
by `tests/test_load_security.py::test_israel_user_unaffected`.

---

## 2. Enforcement Hierarchy

```
                  ┌─────────────────────────────────────────┐
                  │  Authenticated Request                  │
                  └──────────────────┬──────────────────────┘
                                     │
                  ┌──────────────────▼──────────────────────┐
                  │  middleware/auth.py  resolves           │
                  │   - country_code (geo-IP / Clerk JWT)   │
                  │   - consent_to_global (user profile)    │
                  └──────────────────┬──────────────────────┘
                                     │
                  ┌──────────────────▼──────────────────────┐
                  │  regional_policy.classify_zone(...)     │
                  └──────────────────┬──────────────────────┘
                                     │
       ┌─────────────────────────────┼─────────────────────────────┐
       │ zone = NONE                 │ zone = EU_EEA               │
       │ (or consent_to_global=True) │ (no consent)                │
       │                             │                             │
       ▼                             ▼                             │
  Standard PID                regional_policy.enforce_routing(...) │
  (EWMA, hedge)                       │                            │
                                      │                            │
                ┌─────────────────────┼─────────────────────┐      │
                │ Probe local         │ Probe EU sovereign  │      │
                │ Ollama / NPU        │ cloud URL           │      │
                ▼                     ▼                     ▼      │
              "local"          "eu_sovereign"            "denied"  │
              dispatch         dispatch                  HTTP 403  │
                                                                   │
                                                                   ▼
                                                        ComplianceDenied
                                                        (event_label hash
                                                         in 403 response)
```

The control plane never proceeds past the third probe without a sovereign
route. **Failing closed is the design** — silent global-cloud fallback is
the specific behavior Article 12 forbids.

---

## 3. Configuration Surface

| Env var | Effect when set | Effect when unset |
|---|---|---|
| `VOS3_OLLAMA_BASE_URL` | Local Ollama daemon used as the primary sovereign path | Skipped |
| `VOS3_NPU_DEVICE` | NPU bound to this backend; same priority as Ollama | Skipped |
| `VOS3_EU_SOVEREIGN_CLOUD_URL` | Fallback EU-hosted cloud LLM (must contractually keep all data + logs in-region) | Skipped |
| _all three unset_ | EU users → HTTP 403 with `X-VOS3-Compliance-Reason` header | — |

There is **no** way to weaken the policy via configuration. Removing the
EU country codes from `regional_policy.EU_COUNTRY_CODES` is the only way
to disable the lockdown for a specific country, and that change must
clear code review + the `test_israel_user_unaffected` regression suite.

---

## 4. Audit Trail

Every enforcement decision emits a structured log record:

```jsonc
{
  "event":          "regional_policy.enforcement",
  "event_label":    "<SHA-256 of OP_LOCAL_ENFORCEMENT_EU>",
  "zone":           "eu_eea",
  "country_code":   "DE",
  "action":         "local" | "eu_sovereign" | "denied",
  "user_id":        "<Clerk sub — never PII>",
  "consent_to_global": false
}
```

`event_label` is the canonical cryptographic identifier the kernel MMR
ledger will use once the v20.4 `MMR_RECORD_EVENT` VBus command lands.
Until then, the structured backend log is the audit record of record.

**Retention.** EU AI Act Art. 12(3) requires the logs be retained "for an
appropriate period in accordance with the intended purpose of the
high-risk AI system". VOS3 retains for **12 months** by default; the
retention window is configurable per deployment via standard log-shipper
policy. Deletion is by automated rotation only — no operator-initiated
deletion path exists in the backend.

**Tamper evidence.** Backend logs ship to the customer's chosen log sink
(default: stdout structured JSON). The cryptographic anchoring will be
provided by the v20.4 MMR ledger via `MMR_RECORD_EVENT`. Until then,
operators relying on log integrity should ship to an append-only sink
(CloudWatch Logs Insights, Azure Monitor Logs, GCP Logging) and enable
log-bucket immutability.

---

## 5. User-Visible Behavior

### 5.1 EU user, sovereign route available
Request succeeds, response carries `X-VOS3-Region: eu_eea` and
`X-VOS3-Sovereign-Path: local|eu_sovereign`. No user-visible difference
in latency or quality versus the non-sovereign path under typical load.

### 5.2 EU user, no sovereign route
HTTP 403 with body:
```json
{
  "error": "compliance_denied",
  "reason": "EU AI Act Art. 12 enforcement: ...",
  "event_label_sha256": "<hex>"
}
```
The frontend treats this as a hard block (no retry) and surfaces a
compliance message in the chat panel referencing the deployment owner.

### 5.3 EU user with explicit `consent_to_global=true`
Standard PID router applies. The consent flag must be set affirmatively
via the user's profile UI (`/settings/privacy`); it is never inferred
from request headers or query parameters. Consent is auditable —
toggling the flag emits its own structured log event.

### 5.4 Non-EU user (IL, US, JP, CH, GB, etc.)
Untouched. Standard PID router applies. The exclusion is guarded by
`tests/test_load_security.py::test_israel_user_unaffected` and the
companion suites; any change that accidentally extends the EU set will
break those tests.

---

## 6. Operational Runbook

| Scenario | Action |
|---|---|
| EU user reports HTTP 403 | Check the deployment env: at least one of `VOS3_OLLAMA_BASE_URL` / `VOS3_NPU_DEVICE` / `VOS3_EU_SOVEREIGN_CLOUD_URL` must be set. Restart backend after change — `regional_policy.py` re-reads env on every request, but logging the change explicitly helps the audit trail. |
| Audit request from a notified body | Ship the last N days of `regional_policy.enforcement` log records, filtered by `event_label`. Pair with `docs/compliance/AI_ACT_INVENTORY_2026.md` for the system-level inventory. |
| New EU member state joins | Add the country code to `EU_COUNTRY_CODES` in `regional_policy.py` AND `_EU_COUNTRY_CODES` in `middleware/auth.py`. Both must change together — the auth middleware uses its set to set the user's `country_code` attribute, the policy uses its set to classify. |
| Country exits the EU/EEA | Remove from both sets; update `test_israel_user_unaffected`-style regression tests to cover the now-excluded country. |

---

## 7. Out-of-Scope (Documented Non-Coverage)

This compliance strategy covers:
- ✅ Inference routing for EU/EEA users
- ✅ Automatic structured logging of enforcement decisions
- ✅ Deny-on-failure (no silent fallback)

It does **NOT** cover (each is a separate document or a deferred work item):

- ❌ Data-protection rights under GDPR Art. 15–22 (data subject access,
  erasure, etc.). See `docs/compliance/DSAR_PROCESS.md` (separate file).
- ❌ Conformity assessment under AI Act Art. 43. See
  `docs/compliance/AI_ACT_INVENTORY_2026.md` for the component inventory
  that feeds the assessment.
- ❌ Tamper-evident anchoring of the audit log. Today the backend log
  is structurally append-only via the customer's log sink; cryptographic
  anchoring lands in v20.4 with `MMR_RECORD_EVENT`.
- ❌ Compliance for non-EU jurisdictions with their own AI laws (UK
  AI Bill, JP APPI extensions, IL Bill 4881). These are tracked as
  reserved sovereign zones in `regional_policy.py` and will be
  activated in subsequent point releases as enforcement deadlines
  approach.
