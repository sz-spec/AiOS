# Hardware-CVE Inventory (2010–2026)

**Anchor:** `aeb3736` · **Phase:** P4.1 · **Source-of-truth:** `backend/services/compliance_audit.py`

## Scope and intent

This document is the human-readable companion to the machine-checkable
CVE table in `backend/services/compliance_audit.py::_BASELINE`. A
HardwareManifest is cross-referenced against the table at boot, and the
resulting `compliance_inventory_v1.json` is bound into the AAA cert
package as **Annex IV §V.1 evidence** for the EU AI Act.

**What this is**: an architectural-class CVE inventory. We do NOT
duplicate `pip-audit`/OSV/GHSA's dependency-CVE coverage — that's a
separate scan run by CI.

**What this is not**: exhaustive. Each entry is pinned to the silicon
this engagement actually targets (2010+ x86_64 floor per AAA plan).
Adding a new CVE class means one row in `_BASELINE` + one row in this
doc + a regenerated `compliance_inventory_v1.json`.

## Entries (sampled)

| Year | CVE | Family | Vendor | KPTI? | µcode? | Notes |
|---|---|---|---|---|---|---|
| 2018 | CVE-2017-5754 | Meltdown | intel | yes | no | Bounds-check bypass closed by KPTI page-table isolation. |
| 2018 | CVE-2017-5753 | Spectre v1 | both | no | no | Compiler-level (retpoline + `array_index_nospec`). |
| 2018 | CVE-2017-5715 | Spectre v2 | both | no | yes | IBPB + IBRS + retpoline. |
| 2018 | CVE-2018-3615 | L1TF / Foreshadow | intel | yes | yes | Page-table inversion + L1D flush on VMENTER. |
| 2019 | CVE-2018-12126 | MDS / Fallout | intel | no | yes | MDS_CLEAR on context switch. |
| 2019 | CVE-2018-12127 | MDS / RIDL | intel | no | yes | Microcode VERW. |
| 2019 | CVE-2018-12130 | MDS / ZombieLoad | intel | no | yes | Microcode + MDS_CLEAR. |
| 2022 | CVE-2022-29900 | Retbleed | both | no | yes | IBPB on context switch. |
| 2022 | CVE-2022-40982 | Downfall | intel | no | yes | Skylake–Ice Lake; mitigation IS the microcode update. |
| 2023 | CVE-2023-20569 | Inception | amd | no | yes | AMD Zen; microcode-delivered. |

The full picture (including VMScape, TEE.Fail, FP-DSS, and out-of-scope
items like SGX-only attacks) is in `docs/POST_QUANTUM_INVENTORY.md`'s
neighbour entries; this file pins only the items the audit engine
actually evaluates.

## How an entry is evaluated

```
finding = evaluate_cve(manifest, entry):
    needs_kpti = entry.requires_kpti
    needs_uc   = entry.requires_microcode
    have_kpti  = manifest.kpti_init_ready and manifest.kpti_mode
    have_uc    = not manifest.microcode_below_baseline

    if needs_kpti & needs_uc:   mitigated = have_kpti & have_uc
    elif needs_kpti:            mitigated = have_kpti
    elif needs_uc:              mitigated = have_uc
    else:                       mitigated = True   (compiler-level)
```

A row with `mitigated=False` is counted in
`ComplianceInventory.cve_exposed_count` and contributes a `-3` deduction
per row (capped at `-15`) to the risk score via
`compute_risk_score`. The cap prevents double-counting against the
flat `-15` microcode penalty.

## JSON inventory

```bash
python -c "
from services.compliance_audit import write_inventory_json
from services.hardware_manifest import build_empty
write_inventory_json(build_empty(), requirements_path='requirements.txt')
"
```

Produces `compliance_inventory_v1.json` with:

```json
{
  "anchor_sha": "aeb3736",
  "version": "v1",
  "host_mode": "UNKNOWN",
  "host_risk_score": 55,
  "microcode_revision": null,
  "microcode_baseline": null,
  "microcode_below_baseline": false,
  "cve_exposed_count": 4,
  "cve_findings": [ ... ],
  "dependency_findings": [ ... ],
  "requirements_sha256": "..."
}
```

The write is atomic (write-then-rename) so a concurrent reader either
sees the previous file or the new one, never a partial one.

## What's NOT in scope

- **Exploit POCs.** The audit cross-references claims to mitigation
  requirements; it does not run side-channel POCs.
- **Branded-platform-specific CVEs** (SGX-only enclaves, SEV-SNP, TDX)
  — vOS doesn't depend on these enclaves, so the relevant CVEs are
  documented as out-of-scope in `docs/POST_QUANTUM_INVENTORY.md`.
- **Live microcode-update path.** Loading a fresh microcode blob is a
  separate kernel feature (P4.2 only checks the revision at boot).
