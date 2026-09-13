<!--
SPDX-License-Identifier: MIT
SPDX-FileCopyrightText: 2026 VOS3 Project
-->

# Phase 6.1 — EU AI Act Audit Export (the Killer Feature)

**Decision date:** 2026-04-30
**Driver document:** `docs/strategy/MARKET_GAP_ANALYSIS_APRIL_2026.md`
**Hard deadline:** 2026-08-02 (EU AI Act Article 19/26 enforcement)
**Days remaining:** 94

---

## What & why (one screen)

**Ship `tools/vos3_eu_act_export.py` plus the `vos3.eu_act.v1` bundle
schema.** The kernel already produces tamper-evident MMR audit leaves
(`kernel/src/sec/mmr_audit.c`); the gap is a regulator-facing export
format that an enterprise CISO can hand to a Member State auditor on
August 2, 2026.

This is the single feature most likely to convert procurement intent
into procurement signature in the next 94 days. Reasoning + sources
in `MARKET_GAP_ANALYSIS_APRIL_2026.md` §4.

---

## Scope (5 working days, single PR)

### Files to create

| Path | Purpose | LOC est. |
|---|---|---|
| `tools/vos3_eu_act_export.py` | CLI: enumerate MMR leaves over [since,until], emit bundle | ~250 |
| `tools/vos3_eu_act_verify.py` | Companion verifier (regulator-side); replays Merkle proof | ~150 |
| `docs/compliance/EU_AI_ACT_BUNDLE_SCHEMA.md` | Schema spec (`vos3.eu_act.v1`) | ~120 |
| `backend/tests/test_eu_act_export.py` | Source-shape + round-trip tests | ~140 |

### Files to extend (additive only)

| Path | Change |
|---|---|
| `backend/services/vbus_driver.py` | Add `enumerate_mmr_leaves(since, until)` helper that issues paged `MMR_RANGE` ASCII commands |
| `kernel/src/drivers/virtio_bridge.c` | Add `MMR_RANGE` ASCII handler (emit leaves between two TSC bounds) |
| `docs/strategy/OPEN_CORE_LICENSING.md` | Add bullet: "EU-Act-Export bundle in CORE; third-party notarisation in PRO" |

### NOT in scope

- v3.0 binary VBus opcode wiring (that's Phase 6.1.e)
- Trust Registry interop (Phase 6.1.c)
- WASM-in-slot for `secure_compute` (Phase 7 / v20.6)

---

## Bundle schema (`vos3.eu_act.v1`)

Restated from the gap analysis for self-containment:

```
vos3_audit_bundle.zip
├── manifest.json
│   {
│     "schema":   "vos3.eu_act.v1",
│     "generated_at_utc": "2026-04-30T12:34:56Z",
│     "kernel": { "sha256": "<hex>", "build_flavor": "PRO" | "CORE" },
│     "host":   { "fingerprint_sha256": "<hex>" },     // from license_check.c
│     "range":  { "since_utc": "...", "until_utc": "...", "leaf_count": N },
│     "mmr_root_at_export": "<hex>"
│   }
├── leaves.ndjson           // one MMR leaf per line
├── proofs/<leaf_index>.json   // Merkle inclusion proof to mmr_root_at_export
└── signature.bin           // Ed25519 over (manifest.json || leaves.ndjson || sorted proofs/)
                            //   PRO: signed with VOS3 CA + customer .lic key
                            //   CORE: self-signed with host fingerprint key
```

Each `leaves.ndjson` line:

```json
{
  "leaf_index": 12345,
  "tsc": "0xabcdef...",
  "syscall_nr": 1024,
  "operation": "VOS3_OP_LOCAL_EXECUTION",
  "rdseed_nonce": "<hex>",
  "payload_sha256": "<hex>",
  "payload_b64": "..."          // present only with --include-payloads
}
```

---

## Honest scoping

- **Today** the kernel exposes `MMR_ROOT` via `virtio_bridge.c`. A
  ranged enumeration (`MMR_RANGE since=<tsc> until=<tsc> page=<n>`)
  needs to be added — small, self-contained, ~30 LOC of dispatcher
  code + a paged response format. No new MMR semantics.
- **Today** `vos3_pro_activate.py` produces an Ed25519 fingerprint
  request; the *signing* still uses a stub (real Ed25519 verify is
  v20.6). For the bundle, the same honest stub applies: the v1
  signature is *Ed25519-shaped* and verifiable in CORE-self-signed
  mode; PRO countersigning is wired but waits on the CA key
  ceremony (also v20.6).
- The bundle is therefore "regulator-grade in structure today,
  hardware-anchored in v20.6." We say so out loud in the
  `EU_AI_ACT_BUNDLE_SCHEMA.md` honest-caveats section.

---

## Acceptance gates

```
1. tools/vos3_eu_act_export.py --print               (smoke)
2. round-trip test: export → verify → root matches kernel's live root
3. test_eu_act_export.py: 8+ source-shape + round-trip tests pass
4. test_agent_swarm_efficiency.py + open-core invariant check still green
5. Kernel certified ELF SHA-256 unchanged (or new build doc'd)
6. tools/check_open_core_split.sh: 7/7 PASS preserved
```

---

## Day-by-day execution sketch

| Day | Deliverable |
|---|---|
| 1 | Schema doc + Python skeleton + `MMR_RANGE` kernel handler (compile clean) |
| 2 | `enumerate_mmr_leaves` Python helper + leaves.ndjson emission |
| 3 | Merkle inclusion proof generator + per-leaf `proofs/*.json` |
| 4 | Signature path (CORE self-signed; PRO wired to license key) + `vos3_eu_act_verify.py` |
| 5 | Test suite + open-core invariant updates + gap-analysis cross-link + commit |

---

## Marketing one-liner (for procurement decks)

> "VOS3 is the only AI substrate that hands you the regulator-grade
> tamper-evident audit bundle on August 2 — not a SIEM integration,
> not a SaaS dashboard, an actual signed `.zip` your auditor can
> verify offline."

That sentence becomes defensible the day this Phase ships.
