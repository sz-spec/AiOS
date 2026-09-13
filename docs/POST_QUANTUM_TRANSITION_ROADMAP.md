# VOS-Cyber — Post-Quantum Cryptography Transition Roadmap

**Date:** 2026-04-26
**Audience:** acquirer technical due-diligence team; CISO; auditor evaluating quantum-readiness per the 2026 M&A diligence frameworks (Freshfields' four-pillar PQ-DD model: cryptographic inventory, dependency mapping, PQC migration plan, implementation monitoring).
**Honesty contract:** every claim resolves to a file path, a real measurement, or an explicit "planned" item with a calendar date. Where v20.x prior commits established a PQ wire format but not full PQ security, this document repeats the distinction.

## 1. Cryptographic inventory (Pillar 1)

| Primitive | Use site | Tier | Source-of-truth |
|---|---|---|---|
| **ECDSA-P256 + SHA-256** | Primary attestation signature | Production (dev-tier; Sigstore prod 2026-05-06) | `backend/core/security/attestation_service.py::sign_certificate` |
| **ECDSA-P521 + SHA-512** | v20.4 strengthened companion signature | Production (dev-tier) | `attestation_service.py` (P-521 keypair auto-materialised) |
| **ML-DSA-65** (FIPS 204) | Optional PQ signature when `oqs` lib + `VOS3_PQ_SIGNING=1` | **Wire-format ready; activation gated on liboqs vendoring** | `attestation_service.py::_try_mldsa_sign` |
| **PQ commitment trapdoor** | Forward-compat scaffolding when ML-DSA absent | Production (NOT crypto strength; explicitly labelled `-PENDING`) | `pq_commitment_alg`, `pq_commitment_sha384` fields |
| **SHA-256 / SHA-384** | Hash everywhere (cert payload, Merkle, RTMR) | Production (FIPS 180-4) | `kernel/src/crypto/sha384.c` (GPR-only freestanding) |
| **HMAC-SHA-256** | VBus frame auth, session-context bind | Production (RFC 2104) | `kernel/src/drivers/vbus_transport.c` |
| **PBKDF2-HMAC-SHA512 @ 600 k** | SQLCipher entity vault KDF | Production | `backend/core/repositories/local_vault.py` (W ≈ 83 bits proven; see `CRYPTO_HARDENING_PROOF.md`) |
| **AES-256** | SQLCipher payload encryption | Production | SQLCipher 4 default |

Hash primitives (SHA-2 family) are **not at quantum risk in the same sense as signatures**. Grover gives at most a square-root speedup, so SHA-256 retains ~128-bit and SHA-384 retains ~192-bit security against quantum attack — both remain inside the NIST PQC "category 3 / 5" bands. The transition concern is **signatures and key-encapsulation**, not hashes.

## 2. Dependency mapping (Pillar 2)

| Dependency | Origin | Quantum exposure | Mitigation status |
|---|---|---|---|
| `cryptography` 46.0.7 (PyCA) | Python pip (hash-pinned) | ECDSA, RSA, X.509 — **classical only as of 2026-04-26 release** | Awaiting upstream ML-DSA; tracked weekly |
| `liboqs` (Open Quantum Safe) | C library — system dep, NOT in `uv.lock` | ML-DSA, ML-KEM, SLH-DSA available | **Not yet vendored**; flag `VOS3_PQ_SIGNING=1` activates when present |
| `sqlcipher3-wheels` 0.5.7 | Python pip | AES-256 (quantum-resistant) + PBKDF2 (HMAC, quantum-resistant) | No PQ migration needed |
| Sigstore (`cosign`) | external CLI | ECDSA P-256 today; Sigstore community has stated PQ migration intent but no concrete timeline as of 2026-04 | Tracked; monitor `sigstore/community` releases |
| TDX attestation-quote signing | Intel TDX Module | ECDSA P-384 (Intel-controlled) | Out of our control; Intel TDX Module roadmap monitored via INTEL-SA-* advisories |

## 3. Migration plan (Pillar 3) — phased, calendar-anchored

| Phase | Milestone | Trigger | Calendar | Owner |
|---|---|---|---|---|
| 0 | **Wire format ready (DONE)** | v20.4-TITAN ships | 2026-04-23 (commit `ec6fe7d`) | shipped |
| 1 | **Sigstore production tier** | Production keypair ceremony (Sigstore OIDC + Rekor v2) | **2026-05-06** | release engineer |
| 2 | **liboqs vendoring** | Vendor liboqs into the release pipeline; flip `VOS3_PQ_SIGNING=1` default; ML-DSA becomes mandatory | **2026-Q3 (target)** | infra team |
| 3 | **Hybrid certificates by default** | Every emitted cert carries P-256 + P-521 + ML-DSA-65 (real, not commitment) | follows phase 2 | shipped on flip |
| 4 | **PQ-only attestation tier** | Drop ECDSA fallback once ML-DSA verifiers are universal | **2027-Q4 (target — gated on industry verifier coverage)** | TBD |
| 5 | **PBKDF2 → Argon2id** | SQLCipher upstream lands Argon2 KDF support | **2027 (target — gated on SQLCipher upstream)** | infra team |

**Migration pattern:** hybrid-then-phase-out. Every cert from v20.4 onwards already carries the P-256 + P-521 dual ECDSA signature; adding ML-DSA-65 is a third concurrent signature, not a replacement. Verifiers gain ML-DSA acceptance without losing classical-cert verification. Phase 4 (drop ECDSA) is conditional on ecosystem readiness, not calendar-bound.

## 4. Implementation monitoring (Pillar 4) — KPIs

| KPI | Definition | Today | Target |
|---|---|---|---|
| `pq_commitment_share` | Fraction of v20.4+ certs carrying `pq_commitment_alg = "ML-DSA-65-PENDING"` | 100 % (default when oqs absent) | → 0 % once liboqs vendored (Phase 2) |
| `mldsa_signature_share` | Fraction carrying real `signature_mldsa_b64` | 0 % (Phase 0–1); → 100 % at Phase 3 | 100 % by 2026-Q3 |
| `signing_tier` distribution | `dev` vs `prod-oidc-rekor` per cert | 100 % dev | 100 % `prod-oidc-rekor` after 2026-05-06 |
| `liboqs_version_floor` | Minimum liboqs version asserted by verifier | n/a (not yet vendored) | ≥ 0.14.0 (last release shipping both Round-3 Dilithium and ML-DSA per agent research) |

## 5. Honest residuals (the diligence team should ask about these)

1. **PQ commitment trapdoor is NOT post-quantum security.** It is a hash over `(payload || alg_label || issued_at)` and gives no protection against a quantum adversary. The `-PENDING` label is on every commitment field and a regression test (`test_commitment_alg_label_indicates_not_real_pq`) enforces it. Anyone treating the commitment as a PQ signature is reading the cert wrong.
2. **The Sigstore production ceremony has not happened.** Calendar date 2026-05-06 is the trigger; if it slips, B18 falls back to MEASURED-MOCK + Z3-PROVEN tier (which is what v20.7-APEX already documents).
3. **TDX Module signature primitive (P-384) is Intel-controlled.** No roadmap action available to us; we monitor Intel TDX Module advisories.
4. **No SLH-DSA or stateful-hash-signature alternative is currently planned.** ML-DSA-65 is the FIPS 204 Category 3 lattice signature; SLH-DSA (FIPS 205) is the hash-based fallback for organisations who distrust lattice security. We do not plan SLH-DSA support unless an acquirer specifically requires it.
5. **Hashes are not migrating.** SHA-256 / SHA-384 retain Grover-bounded post-quantum security; no migration is planned. PBKDF2 → Argon2id (Phase 5) is a separate concern (memory-hard KDF, not quantum-driven).

## 6. Reproducible verification

```bash
# 1. Confirm hybrid signing is wired:
cd backend && .venv/bin/python -m pytest tests/security/ -k "Hybrid or PQCommitment" -v

# 2. Confirm liboqs absence is correctly handled:
.venv/bin/python -c "import oqs" 2>&1 | grep ImportError   # expected: ImportError

# 3. Confirm a generated cert carries the expected fields:
.venv/bin/python -c "
import sys, tempfile; from pathlib import Path
sys.path.insert(0, '.')
from core.security.attestation_service import AttestationService
with tempfile.TemporaryDirectory() as td:
    svc = AttestationService(measurement_mode='mock', key_dir=Path(td))
    c = svc.sign_certificate(svc.generate_certificate('s', 't', b'm', b'i'))
    print('algorithms:', c.signature_algorithms)
    print('pq_commitment_alg:', c.pq_commitment_alg)
"
```

Expected output of (3):

```
algorithms: ['ECDSA-P256-SHA256', 'ECDSA-P521-SHA512', 'PQ-COMMITMENT-ONLY']
pq_commitment_alg: ML-DSA-65-PENDING
```

## Sources

- [NIST FIPS 204 — Module-Lattice-Based Digital Signature Standard (ML-DSA)](https://csrc.nist.gov/pubs/fips/204/final)
- [NIST FIPS 205 — Stateless Hash-Based Digital Signature (SLH-DSA)](https://csrc.nist.gov/pubs/fips/205/final)
- [Open Quantum Safe — liboqs algorithm coverage](https://openquantumsafe.org/liboqs/algorithms/sig/ml-dsa.html)
- [Freshfields — Quantum disentangled #2: Quantum and M&A (2026 in-window)](https://technologyquotient.freshfields.com/post/102mkds/quantum-disentangled-2-quantum-and-ma-risks-and-opportunities)
- VOS-Cyber `docs/CRYPTO_HARDENING_PROOF.md` — PBKDF2 work-factor derivation
- VOS-Cyber commit `ec6fe7d` (v20.4-TITAN) — hybrid signature wire format
