# Sigstore v3 / Rekor v2 — Honest Scope & Migration Path

**Stage:** 11
**Date:** 2026-05-09
**Status:** v3-shaped pipeline is live and end-to-end verified against the deterministic kernel SHA-256. Byte-format compatibility with the upstream April-2026 v3 spec is **pending**.

This document is the operational source-of-truth for what Stage 11 actually shipped vs. what the upstream Sigstore v3 / Rekor v2 specification requires when the spec is in hand. A diligence-grade reviewer should read this **first** before reading any Stage-11 commit message.

---

## 1. Why this gap exists

The implementer's training data ends 2026-01. The upstream Sigstore v3 / Rekor v2 specifications are dated April 2026 (per the user's brief that drove this stage). I do not have independent access to:

- The exact JSON-Schema of a v3 bundle.
- The HTTP path / OpenAPI shape of the Rekor v2 endpoint.
- The new media-type string the upstream community has standardised.
- The `cosign verify-blob --new-bundle-format` flag combinations the v3 release ships.

Rather than fabricate a wire format I cannot verify against the spec, Stage 11 ships a **v3-shaped** pipeline whose cryptographic guarantees are real and whose serialisation has well-documented swap points.

---

## 2. What Stage 11 actually shipped

### 2.1 Cryptographic substrate (verified)

| Property | Implementation | Verified? |
|----------|----------------|-----------|
| Signature algorithm | ECDSA P-256 over SHA-256 | ✅ via `cryptography==48.0.0` |
| DSSE Pre-Authentication Encoding | RFC-pending DSSEv1 (`DSSEv1 <len> <pt> <len> <p>`) | ✅ |
| Statement payload schema | in-toto Statement v0.1 with subject + predicate | ✅ |
| Rekor inclusion-proof primitive | RFC-6962 binary Merkle tree, `0x00`/`0x01` domain separation | ✅ all 75 (size,idx) pairs round-trip |
| Append-only log persistence | JSON-Lines with crash-safe truncation rules | ✅ smoke-tested reload |
| Tamper detection | DSSE signature check + payload-subject SHA cross-check + inclusion-proof verify | ✅ corrupted bundle rejected with clear error |

These guarantees are **independent of the v3 wire format** and remain valid no matter how the upstream spec restructures its JSON envelope.

### 2.2 End-to-end ceremony evidence

The release ceremony produced three persistent artifacts on disk:

```
kernel/build/vos3.elf.bundle.json                              (2,637 bytes)
infra/security/vos3_sbom_v11_stage11.json.bundle.json          (2,771 bytes)
infra/security/rekor_v2.jsonl                                  (  960 bytes)
```

The kernel bundle binds to:
```
sha256(kernel/build/vos3.elf) = bd0ce974e8431c740b0689c85f5ffdf1bd3bc2c1a8128b84f74d25fa239b2fed
```

— which is the **deterministic** SHA-256 invariant from Stage 7 (re-verified at Stage 10.3 and Stage 11). The signature, when verified against the public key in `infra/security/keys/vos3_dev_signing.pub`, confirms an ECDSA-P256 attestation over that exact hash.

### 2.3 Rekor v2 log

`infra/security/rekor_v2.jsonl` is the canonical transparency log for the Stage-11 release. After the ceremony:

- `tree_size = 2` (kernel ELF + SBOM)
- `root_hash = 03be06d9840bb473b7be3cf8a2481b424b80d017397bce1f902986fafc666e46`

Every future signing ceremony appends to this log. The `latest_root` is publishable so an external observer can detect any retroactive log rewrite.

---

## 3. What needs to change when the v3 spec lands

The migration is **single-layer** and touches only serialisation. Crypto math stays.

### 3.1 Bundle JSON keys

`infra/security/sigstore_v3_bundle.py::Bundle.to_v3_dict()` currently emits the v2-era key names (`dsseEnvelope`, `verificationMaterial`, `tlogEntries`). The v3 spec may rename or restructure these. Every reader that consumes the bundle is in the same module — so the rename is a single function-body change. **No call-site impact.**

### 3.2 Media type

```python
BUNDLE_MEDIA_TYPE = "application/vnd.dev.sigstore.bundle.v3+json"
```

— is the placeholder. The exact upstream media type is the swap.

### 3.3 Rekor v2 endpoint

`rekor_v2_log.py` is local-only today (no HTTP endpoint). When CI wants to push to a real Rekor v2 service:

| Component | Stage 11 placeholder | Upstream v3 reality |
|-----------|----------------------|---------------------|
| Endpoint URL | (none) | `https://rekor.sigstore.dev/api/v2/log/entries` (probable; spec confirms) |
| Auth | (none, dev-tier local) | OIDC-bound short-lived Fulcio cert |
| Wire format | local LogEntry JSON | upstream-defined Rekor entry envelope |

The local `rekor_v2_log.py` stays as the **on-disk transparency log** for offline-verifiable releases (the same role Sigstore's local-bundle mode plays). The push-to-public-Rekor is a separate CI step.

### 3.4 `cosign` integration

We do NOT shell out to `cosign` from Stage 11 code — the `cryptography`-only path is fully self-contained, which avoids depending on a `cosign` binary we cannot verify supports v3 yet. When `cosign >= 2.6.2` ships v3 support, the pipeline can OPTIONALLY add a `cosign verify-blob --new-bundle-format` step as a redundant cross-check; this would not replace the in-tree verifier.

### 3.5 VEX live-feed integration

`infra/security/vex_baseline.json` is hand-curated. Production replaces it with the merged output of:

- `pip-audit -r backend/requirements.txt --format=cyclonedx-json`
- `npm audit --json` (transformed to CycloneDX VEX)
- `cargo audit --json` (transformed to CycloneDX VEX)

The CI step responsible for this lives in `.github/workflows/release.yml` (yet-to-port from VOS3-Cyber as part of Stage 11.1's nginx/deploy bring-up batch).

---

## 4. Audit-ready artefact inventory

For a third-party auditor verifying Stage 11:

```
infra/security/
├── build_sbom.py                # CycloneDX 1.5 generator
├── build_sbom_v20_1.py          # historical v20.1 reference
├── dev_sign.py                  # dev-tier ECDSA pipeline (carried from VOS3-Cyber)
├── sbom_verify.py               # SBOM verification (carried)
├── sigstore_mock.py             # CI mock from VOS3-Cyber pre-2026-05-06
├── sigstore_verify.py           # production verifier (carried)
├── sigstore_v3_bundle.py        # NEW: v3-shaped Signer/Verifier
├── rekor_v2_log.py              # NEW: RFC-6962 Merkle log
├── vex_baseline.json            # NEW: hand-curated VEX seed
├── vos3_sbom_v11_stage11.json   # NEW: Stage-11 SBOM
├── vos3_sbom_v11_stage11.json.bundle.json  # NEW: signed bundle for SBOM
├── rekor_v2.jsonl               # NEW: append-only transparency log
└── keys/
    ├── vos3_dev_signing.key     # gitignored — dev private key
    └── vos3_dev_signing.pub     # public key (committed)

kernel/build/
└── vos3.elf.bundle.json         # NEW: signed bundle for the kernel ELF
```

### 4.1 Verifying the kernel ELF independently

```bash
# As any third party, on a fresh checkout:
cd kernel && make clean && make
shasum -a 256 build/vos3.elf
# → bd0ce974e8431c740b0689c85f5ffdf1bd3bc2c1a8128b84f74d25fa239b2fed

cd .. && python3 -c "
from infra.security.sigstore_v3_bundle import Verifier
v = Verifier.from_dev_key('infra/security/keys/vos3_dev_signing.pub')
r = v.verify_artifact(
    'kernel/build/vos3.elf', 'kernel/build/vos3.elf.bundle.json',
    expected_sha256_hex='bd0ce974e8431c740b0689c85f5ffdf1bd3bc2c1a8128b84f74d25fa239b2fed',
)
assert r['verified'] and r['rekor_inclusion'] == 'passed'
print('verified:', r)
"
```

If the ELF hash mismatches, the determinism invariants are broken (would point to a toolchain or `SOURCE_DATE_EPOCH` regression — see `docs/PROVENANCE.md` §5). If the signature mismatches against an unchanged ELF, a fresh sign + log entry must be issued.

---

## 5. Honest scope summary table

| Promise | Cryptographically real? | Spec byte-compatible? |
|---------|-------------------------|-----------------------|
| ECDSA-P256 signature over `sha256(vos3.elf)` | ✅ yes | n/a (algorithm) |
| DSSE envelope | ✅ yes | partial — DSSEv1 PAE; v3 may carry additional fields |
| Rekor v2 inclusion proof | ✅ yes — RFC-6962 | partial — JSON field names may differ |
| Local transparency log | ✅ yes | n/a (local artefact) |
| Public Rekor v2 publication | ❌ not yet | CI step is the swap point (§3.3) |
| Fulcio short-lived cert | ❌ not yet — dev-tier self-signed | bundle carries `CN=VOS3-DEV-NOT-FULCIO` |
| `cosign verify-blob` cross-check | ❌ not yet | OPTIONAL once cosign v3 ships |

**The pipeline is auditor-ready for the cryptographic substrate today.** The wire-format / public-CI swap is the planned-and-documented forward path. A reviewer who insists on byte-for-byte v3 spec conformance must wait for the upstream spec; one who only requires "the kernel image's hash is signed by a key we can verify, with a tamper-evident inclusion proof" is satisfied now.
