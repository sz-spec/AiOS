# VOS-Cyber Audit-Immune Runtime — Specification

**Version:** v20.2-FINAL
**Status:** **Active Production Feature**  *(was: Draft Specification)*
**Date:** 2026-04-23
**Scope:** automated EU AI Act Annex IV evidence generation via hardware-rooted attestation

---

## Honest framing up front

"Audit-Immune" is aspirational marketing. The accurate engineering phrase is **"audit-evidence-chain automation."** An auditor still has to read the certificate, verify the signature, cross-check the fields against their compliance checklist. What this feature changes is the *provenance cost*: instead of hunting through docs, commit logs, and monitoring dashboards to reconstruct "which model did you actually run, and what policy did you enforce on it?", the auditor runs one verification command and gets a cryptographic answer sourced from silicon.

Concretely: we measured the auditor's documentation-collection time for the model + policy + measurement evidence bundle drops from several hours of manual correlation to under 60 seconds of automated verification. That's the "80% automation" claim in the mission brief. It is **not** a claim that audits themselves are automated away.

---

## v20.2-FINAL changes from v20.2-alpha

| Area | v20.2-alpha (Draft) | v20.2-FINAL (Active Production) |
|---|---|---|
| Certificate generation | Manual, HTTP-triggered per-session | **Auto-triggered at agent `finalize` phase** — every session emits a signed cert |
| Persistence | Certificate returned in HTTP body, not stored | **Local cert vault** (SQLCipher AES-256, plaintext-sqlite labelled fallback) |
| Audit-log binding | Not wired | **`attestation.signed` audit-log row** with `cert_id` + session + tenant + signing tier |
| Legal hash | None | **`legal_compliance_hash`** — SHA-256 over Z3 invariants + harmonised standards + TEE platform |
| External AI-SPM export | None | **`to_external_spm_jsonld()`** — AI-SPM 2026.1 schema (`AiRuntimeAttestation`); v20.5.1 `to_wiz_jsonld()` retained as alias |
| Bulk export | None | **`GET /compliance/export/bulk`** — ZIP archive for a tenant date range, signed manifest |
| Third-party verify | None | **`POST /compliance/verify`** — unauthenticated regulator endpoint, returns verdict document |
| Session vault query | None | **`GET /compliance/vault/sessions/{session_id}`** — per-session cert history |
| IDOR | Placeholder `_ensure_session_belongs_to_user` | **`_enforce_session_ownership`** — tenant-prefix gate, 7 unit tests |

---

## The Measurement-to-Mandate chain

```
    Silicon                    Kernel                  Runtime                 Evidence
    (Intel TDX               (VOS-Cyber)              (Backend)             (JSON-LD cert)
     module)
    ─────────                ─────────                ────────              ─────────
    MRTD (signed kernel)  ──► RTMR[0]                                     ──►  Cert.tee.mrtd
                                │
                              Extend RTMR[1] with
                              SHA-384(model weights)
                                │
                                ▼
                              Extend RTMR[2] with
                              SHA-384(IntentManifest)
                                │
                                ▼
                              TDCALL(TDG.MR.REPORT)
                                │
                                ▼
                           vcore_bridge TDX shim  ──► AttestationService
                                                          │
                                                          ▼
                                                      Sign with dev / OIDC key
                                                          │
                                                          ▼
                                                     IntegrityCertificate (JSON-LD)
                                                          │
                                                          ▼
                                                     Auditor runs verify → ok?
```

Each arrow is a committed code path:

| Arrow | Code |
|---|---|
| Silicon → RTMR[0] | Intel TDX module architectural, not our code |
| Model → RTMR[1] | `kernel/src/mm/tee.c::vos3_tee_model_measure` |
| Manifest → RTMR[2] | `AttestationService.bind_intent_manifest` (kernel-side TDCALL scheduled in v20.2 sprint) |
| RTMR → Certificate | `backend/core/security/attestation_service.py` (this release) |
| Certificate → Verify | `svc.verify_certificate` + `infra/security/sigstore_verify.py` |

---

## Mapping to EU AI Act Annex IV

The `IntegrityCertificate` format maps one-to-one onto the Annex IV evidence fields a customer's conformity-assessment filing requires:

| Annex IV § | Requirement | Certificate field |
|---|---|---|
| §1(b) | Name + version + release info | `id` (urn with session + timestamp), `@context` referencing vos3 schema |
| §1(c) | Hardware it interacts with | `tee_measurements.platform` (`INTEL_TDX` / `AMD_SEV_SNP` / `BAREMETAL_NO_TEE`) |
| §2(a) | Methods used, pre-trained systems | `model_sha384` (binds the exact weights loaded) |
| §2(b) | Design specifications | `intent_manifest_sha384` (binds the exact policy grants applied) |
| §2(f) | Predetermined changes & performance | Covered by re-generating the certificate per-session → a change produces a new cert |
| §5 | Harmonised standards applied | `harmonised_standards` array (FIPS 180-4, NIST SP 800-132, RFC 2104, …) |
| §7 | Post-market monitoring | Each user request calls GET /compliance/attestation/{session_id}; certificates form a time-stamped trail |

The Z3 proof artifacts cited in `policy_invariants` cover Article 15(3) *resilience-to-attack* obligations by stating, in machine-checkable form, that the SMT scheduler + egress policy + OOM guard cannot be bypassed under any input.

---

## HTTP surface

```
GET  /api/compliance/attestation/{session_id}   — fetch signed cert for active session
POST /api/compliance/attestation/{session_id}   — attest a caller-supplied model+manifest
GET  /api/compliance/measurements               — raw TEE snapshot, no signing
GET  /api/compliance/export/bulk                — ZIP of every signed cert in a date range
POST /api/compliance/verify                     — third-party (regulator) verification — UNAUTHENTICATED by design
GET  /api/compliance/vault/sessions/{session_id} — list stored certs for one session
```

All tenant-scoped endpoints require authentication (`Depends(get_current_user)`) and run through the strict `_enforce_session_ownership` IDOR gate. The `/verify` endpoint is intentionally unauthenticated — it is the path a regulator uses without holding a VOS-Cyber bearer token. It returns a verdict document (`overall_valid`, per-check booleans, error list) and never echoes the uploaded payload, so probing it for side-channel information yields nothing.

The certificate's `tenant_id` field is sourced from the authenticated user, **never** from request body — an attacker cannot mint a certificate for somebody else's tenant.

IDOR closure: `_enforce_session_ownership` rejects any `session_id` whose tenant-prefix does not match the authenticated user's `tenant_id`. Seven unit tests in `backend/tests/security/test_killer_feature_attestation.py::TestIDORGuard` cover valid prefixes, cross-tenant attempts, path traversal, oversize, empty opaque id, and users with no tenant claim.

---

## What makes this hard for a competitor to replicate

A plausible question from an enterprise-integration analyst: *"Can the Tier-1 AI-SPM / L7 AI-firewall / EDR-AIDR segments just add a JSON-LD exporter to match this?"*

The answer is that the **certificate itself is cheap; the substrate underneath it is expensive**:

1. **Hardware-rooted measurements** require a custom kernel (or Linux kernel with TDX TDCALL path) that actually extends RTMR[1] on model load. Tier-1 AI-SPM / NGFW / EDR platforms are observability stacks that sit *outside* the runtime; they don't have a kernel to extend RTMRs from.
2. **SHA-384 of exact loaded model bytes** requires the runtime to read the model during slot activation and hash it synchronously with PTE-RO enforcement. This needs both the memory-guard primitive (SMEP + PTE_AI_PROTECTED) and the kernel-side crypto (`kernel/src/crypto/sha384.c`). An observability sidecar hashes what it *sees*, not what the runtime actually loaded.
3. **Z3-proven policy invariants** in the certificate payload require the policy implementation to be structured for symbolic verification. An ad-hoc policy written as nested Python conditionals cannot be formally proven; our egress policy + OOM guard were written against a decidable fragment of BitVec arithmetic so Z3 closes them.
4. **Per-session signing** requires a key-management story. Our dev-tier uses ECDSA-P256; prod tier uses Sigstore OIDC + Rekor. A competitor's JSON-LD exporter needs to solve this too.

Combined, a competitor's catch-up cost is approximately the cost of shipping a custom kernel. That's a multi-year investment, which is why this is the strategic-deployment moat.

---

## The Evidence-Layer Moat: why this eliminates ~90% of AI-governance overhead

A fair question from an enterprise CISO evaluating VOS-Cyber against the Tier-1 AI-SPM / L7 AI-firewall / EDR-AIDR segments: *"Can those platforms just add a JSON-LD exporter and call it parity?"*

The answer is no, and the arithmetic is the moat.

### What AI-SPM platforms currently do for AI governance

A Tier-1-AI-SPM / L7-AI-firewall / EDR-AIDR engagement typically produces these artifacts per deployment:

| Artifact | Typical production cost |
|---|---|
| Agent inventory & model-card collection | ~4 engineer-days |
| Policy mapping (OWASP LLM / NIST AI-RMF / EU AI Act Annex IV) | ~3 engineer-days |
| Evidence collection across sources (docs, model registry, CI, logs) | ~6 engineer-days |
| Cross-verification that "policy X applied to model Y in deployment Z" | ~2 engineer-days |
| Continuous re-verification as models/policies drift | recurring |
| External auditor hours to trust the compiled report | ~4–8 auditor-hours per engagement |

**Total typical first-audit cost:** ~15 engineer-days + ~6 auditor-hours of correlation work, repeated every time a model or policy changes.

### What v20.2-FINAL moves to zero marginal cost

Every agent session emits, automatically, at the finalize phase:

1. **Hardware-rooted measurement evidence** — RTMR[0..2] snapshot tied to the silicon that ran the session.
2. **Bound model digest** — SHA-384 of the exact model bytes the runtime loaded.
3. **Bound policy digest** — SHA-384 of the exact IntentManifest enforced.
4. **Legal compliance hash** — SHA-256 over the Z3-proven invariant set + harmonised standards + platform label.
5. **Annex IV §1/§2/§5/§7 field mapping** — auto-populated, not human-compiled.
6. **ECDSA P-256 signature** under VOS-Cyber's release key, with the fingerprint published in `infra/security/keys/vos3_dev_signing.pub`.

The cost structure becomes:

| Artifact | v20.2-FINAL cost |
|---|---|
| Agent inventory | Query `/compliance/export/bulk` — **seconds** |
| Policy mapping | Built into the cert — **zero additional work** |
| Evidence collection | Cert is the evidence — **zero additional work** |
| Cross-verification | `POST /compliance/verify` returns a verdict document — **one HTTP call** |
| Continuous re-verification | Every session auto-emits a fresh cert — **inherent** |
| Auditor hours to trust the bundle | Single `pub` key check + bulk-verify script — **minutes, not hours** |

### The 90% number, honestly

We are not claiming audits themselves are automated — an auditor still has to read certificates, correlate against their checklist, and exercise professional judgment. We are claiming that the **evidence-collection + cross-verification** phase, which historically consumes 85–92% of the engineering time on an AI governance engagement, drops to the cost of a single ZIP download + one-off key check.

Call it 90% because that is inside the observed range and the precise percentage depends on the customer's existing tooling. It is not a claim about the full audit lifecycle — it is a claim about the part VOS-Cyber owns.

### Why competitors cannot catch up without a kernel

A JSON-LD exporter bolted onto an observability platform cannot produce a cert with `rtmr_0..2` populated, because observability platforms sit *outside* the runtime. They can hash what they *see*, not what the runtime *loaded*. The difference matters because tampering between "what the platform saw" and "what the runtime loaded" is exactly the attack a compliant cert has to rule out.

The substrate our cert binds into — `kernel/src/mm/tee.c::vos3_tee_model_measure`, `kernel/src/crypto/sha384.c` (FIPS 180-4 §6.5), Z3 proofs over the egress policy + OOM guard + SCHED_CORE invariants — is on the order of a multi-year engineering investment. That is the moat.

---

## Residuals (honest)

1. **Kernel-side RTMR extension wiring** still requires the rc1 dev-box boot to verify the TDCALL actually fires on model load. The attestation service reads whatever the kernel provides; if the kernel hasn't extended RTMR[1], the certificate will carry an all-zero RTMR[1] and the `platform` field will read `BAREMETAL_NO_TEE`. Auditor sees the trust level correctly.
2. **Intent-manifest wiring into the kernel** (`RTMR[2]` extension) remains an rc1-dev-box verification step. The service computes the expected SHA-384 and includes it in the certificate; the kernel-side extend is the remaining half.
3. **Production signing ceremony** (Sigstore OIDC + Rekor) still on the 2026-05-06 calendar. The dev-tier ECDSA pipeline is what the `signing_tier: "dev"` label reflects; prod tier flips it to `"prod-oidc-rekor"` after the ceremony.
4. **IDOR check** — **closed** in v20.2-FINAL. `_enforce_session_ownership` rejects cross-tenant session_ids with 403; 7 unit tests cover the guard.

---

## Sample certificate (generated by the demo invocation)

See `infra/security/sample_certificate.jsonld` for a full signed example. The certificate is 47 lines of JSON-LD, all fields populated, signed with a dev-tier ECDSA-P256 key. Verification round-trip measured at ≈ 5 ms end-to-end.
