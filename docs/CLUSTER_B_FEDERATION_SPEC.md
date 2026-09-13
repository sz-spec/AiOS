# Cluster B — Cross-Organization Identity Federation Technical Specification

**Date:** 2026-05-25
**Status:** DRAFT (Wave B.1 prototype lands in same commit; CEO sign-off needed for B.2 + B.3 timelines)
**Owner:** Sprint 17 / Wave 2 / Cluster B
**Predecessors:**
  - Sprint 15 / F1 — `backend/core/security/spiffe_workload_identity.py` (single-domain JWT-SVID verifier)
  - Sprint 16 / F4 — `backend/core/security/spiffe_federation.py` (multi-domain routing verifier)
  - Sprint 16 / F5 — Vault JIT (referenced for bundle-refresh integration)
  - Sprint 17 W1 P2 — `backend/services/aims_envelope.py` (AIMS attestation envelope builder/verifier)
**Sources (May 25, 2026 horizon):**
  - [IETF WIMSE WG charter](https://datatracker.ietf.org/wg/wimse/about/) — Workload Identity in Multi-System Environments
  - [draft-ietf-wimse-arch-07](https://datatracker.ietf.org/doc/draft-ietf-wimse-arch/) — workload-identity context conveyance
  - [draft-ietf-wimse-workload-identity-bcp-02](https://datatracker.ietf.org/doc/draft-ietf-wimse-workload-identity-bcp/) — OAuth 2.0 Client Assertion in Workload Environments
  - [draft-ietf-wimse-workload-identity-practices-04](https://datatracker.ietf.org/doc/html/draft-ietf-wimse-workload-identity-practices) — industry practices
  - [draft-ietf-wimse-identifier-02](https://www.ietf.org/archive/id/draft-ietf-wimse-identifier-02.txt) — canonical Workload Identifier URI
  - [SPIFFE Federation specification](https://spiffe.io/docs/latest/spiffe-specs/spiffe_federation/) — bundle endpoint protocol
  - [SPIFFE Federation standards (GitHub)](https://github.com/spiffe/spiffe/blob/main/standards/SPIFFE_Federation.md)
  - [draft-irtf-cfrg-bbs-signatures-10](https://datatracker.ietf.org/doc/html/draft-irtf-cfrg-bbs-signatures) — IRTF CFRG BBS canonical
  - [W3C Data Integrity BBS Cryptosuites v1.0](https://www.w3.org/TR/vc-di-bbs/) — W3C VC integration
  - [CUBE: Partially Blind BBS — IACR 2026/920](https://eprint.iacr.org/2026/920.pdf)
  - [Compact and Selective Disclosure for VC — arxiv 2506.00262](https://arxiv.org/pdf/2506.00262)
  - [On Cryptographic Mechanisms for Selective Disclosure — arxiv 2401.08196](https://arxiv.org/abs/2401.08196)
  - [NIST SP 800-204A — Service-Mesh Microservices](https://csrc.nist.gov/pubs/sp/800/204/a/final) (the real "multi-domain" reference)
  - [NIST SP 800-204D — DevSecOps CI/CD SCM security](https://csrc.nist.gov/pubs/sp/800/204/d/final) (scope honest-flag: covers CI/CD supply chain, NOT multi-domain AI environments as informally cited; included here for completeness)

---

## 0. Why Cluster B

From the 80-problem agent-era catalog:

| Item | What |
|---|---|
| **F1** | No agreed identity model for "agent acting on behalf of user" — partial today via SPIFFE WIT-SVID |
| **F2** | OAuth2/OIDC scopes don't model agent-autonomy degree |
| **F3** | Audit logs lack human-vs-agent provenance |
| **F4** | Cross-tenant agent identity — partial today via `SPIFFEFederationVerifier` |
| **F5** | Long-lived agent tokens vs JIT — partial today via Vault JIT |
| **F6** | Invocation-Bound Capability Tokens (IBCT) — proposed but not adopted |

Sprint 16 closed F4 at the **routing layer only**: an incoming JWT-SVID from a registered partner domain is verified by that domain's pre-imported verifier. The remaining gaps:

1. **Trust-bundle synchronization is manual.** The operator hand-imports the partner's JWKS/bundle. No protocol-level pull, no polling, no rotation handling.
2. **AIMS envelopes are validated only within a single trust domain.** A cross-org request that carries an AIMS envelope from Trust Domain B must be re-verifiable by Trust Domain A.
3. **Identity disclosure is all-or-nothing.** The full SPIFFE-ID + the full AIMS context are exposed to every relying party. For PII-sensitive flows (consulting handovers, medical-data agents) a verifier should see *only* the claims it needs.

Cluster B closes these three gaps in three waves, mapped to the three identified pain points.

---

## 1. The cross-domain identity model

### 1.1 Trust Domain definition

A **Trust Domain** in vOS is the unit of administrative control over a SPIFFE issuer + AIMS envelope authority. Examples:
- `spiffe://aidg.vos3.dev` — the AIDG production trust domain (issuing org)
- `spiffe://partner-corp.example.com` — a sub-contractor's trust domain
- `spiffe://staging.aidg.vos3.dev` — a staging environment of the same org

Each trust domain runs:
- A SPIFFE SVID issuer (Sprint 15 F1 single-domain verifier as a *relying-party* peer)
- A SPIFFE Federation bundle endpoint at a well-known URL
- An AIMS envelope builder (Sprint 17 W1 P2)
- An audit ring (Sprint 16 G2/G4)

### 1.2 How an agent in Trust Domain A proves hardware integrity to Trust Domain B

The canonical flow:

```
                Trust Domain A                  ║                Trust Domain B
                                                ║
  Agent process (vOS slot)                      ║   Verifier (vOS receiving side)
   │                                            ║    ▲
   │  1. Obtain JWT-SVID (Sprint 15 F1)         ║    │
   │     subject = spiffe://A/agent/slot-42     ║    │
   │     issuer  = spiffe://A                   ║    │
   │  2. Build AIMS envelope (Sprint 17 W1 P2)  ║    │
   │     identity_source = SPIFFE_WIT_SVID      ║    │
   │     attestation_links → RTMR[0..3] (D4)    ║    │
   │     intent_manifest → declared scope       ║    │
   │  3. Sign envelope with A's signing key     ║    │
   ├──────────────► HTTP POST ──────────────────╫────►  4. Look up bundle for trust_domain=A
   │                                            ║    │     (B's local copy, refreshed from
   │                                            ║    │      A's bundle endpoint)
   │                                            ║    │  5. Verify JWT-SVID with A's bundle
   │                                            ║    │  6. Verify AIMS envelope signature
   │                                            ║    │     against A's signing key in bundle
   │                                            ║    │  7. Verify attestation_links → RTMRs
   │                                            ║    │     are recent + match A's policy
   │                                            ║    │  8. Apply B's authorization policy
   │                                            ║    │     (which agents from A may invoke
   │                                            ║    │      which capabilities in B)
```

Hardware integrity flows via the **AttestationLink** field of the AIMS envelope: it carries the RTMR[0..3] measurements (M1, M2, D4 from the catalog) signed by A's TPM/TDX quote. B verifies the quote against the platform vendor's PKI (Intel, AMD, etc.) and then checks the measurements against B's hardware-policy table.

The CRITICAL invariant: the AIMS envelope's signature MUST cover the AttestationLink, so B cannot be tricked into accepting a fresh envelope tied to a stale RTMR.

### 1.3 Trust Bundle exchange — the heart of Cluster B

The SPIFFE Federation Bundle Endpoint protocol (`spiffe.io/docs/latest/spiffe-specs/spiffe_federation/`):

- Each domain exposes `https://<domain>/.well-known/spiffe-bundle` returning the **JWKS** (RFC 7517) + metadata (refresh hint, sequence number).
- Remote validators poll periodically; on detected change, rotate keys in their local store.
- The bundle endpoint itself MUST be authenticated — either by a separate TLS PKI ("https_web" profile) or by a pre-shared "https_spiffe" anchor.

In Cluster B's `IdentityFederationBridge`:

1. Operators register a **FederationRelationship** per partner: trust_domain, bundle_endpoint_url, profile (web/spiffe), refresh interval, optional pinned-cert.
2. The bridge has a background poller that pulls the bundle, validates the response (signature on the JWKS via the profile's anchor, sequence-number monotonicity), and forwards the parsed bundle to the underlying `SPIFFEFederationVerifier`.
3. On rotation: the bridge issues `verifier.unregister_federated_domain()` + `register_federated_domain()` atomically, so no verification request sees a partial state.

---

## 2. The `IdentityFederationBridge` API (Wave B.1 — this commit)

`backend/services/identity_federation_bridge.py` exposes:

```python
class IdentityFederationBridge:
    def __init__(self, *, local_trust_domain: str,
                 federation_verifier: SPIFFEFederationVerifier,
                 transport: BundleTransport = HttpBundleTransport(),
                 clock: ClockProtocol = SystemClock()): ...

    def register_partner(self, *, trust_domain: str,
                          bundle_endpoint_url: str,
                          profile: BundleProfile = HTTPS_WEB,
                          refresh_seconds: int = 300,
                          pinned_sha256: Optional[str] = None) -> PartnerRecord: ...

    def list_partners(self) -> tuple[PartnerRecord, ...]: ...

    def refresh_partner(self, trust_domain: str) -> RefreshOutcome: ...
        # Force an immediate poll. Used by tests + by the operator's
        # "rotate now" CLI.

    def refresh_all(self) -> tuple[RefreshOutcome, ...]: ...

    def revoke_partner(self, trust_domain: str) -> bool: ...
        # Drops both the bridge's partner record AND the underlying
        # federation_verifier's domain registration. Atomic — held under
        # the bridge's lock.

    def verify_cross_domain_jwt(self, jwt_token: str
                                ) -> VerifiedFederatedIdentity:
        # Convenience pass-through to the underlying verifier; emits
        # an audit event with the trust_domain and verification outcome.

    def stats(self) -> BridgeStats: ...
```

### 2.1 Transport abstraction

`BundleTransport` is a Protocol with one method:
```python
def fetch(self, url: str, *, profile: BundleProfile,
           pinned_sha256: Optional[str] = None) -> bytes: ...
```

The default `HttpBundleTransport` uses `urllib.request` (Python stdlib, no new dependency). For testing, `InMemoryBundleTransport` lets the suite seed canned responses and assert the bridge handles them correctly without a real network.

### 2.2 Bundle profile

```python
class BundleProfile(enum.IntEnum):
    HTTPS_WEB    = 0   # bundle endpoint authenticated by Web PKI
    HTTPS_SPIFFE = 1   # bundle endpoint authenticated by a pre-shared
                       # SPIFFE bundle (chicken-and-egg solved by an
                       # operator-pinned anchor)
```

Wave B.1 ships HTTPS_WEB only. HTTPS_SPIFFE requires the chicken-and-egg
anchor flow which is Wave B.2 work.

### 2.3 PartnerRecord

```python
@dataclass(frozen=True)
class PartnerRecord:
    trust_domain: str
    bundle_endpoint_url: str
    profile: BundleProfile
    refresh_seconds: int
    pinned_sha256: Optional[str]
    last_refresh_ts: Optional[float]
    last_refresh_outcome: Optional[RefreshOutcomeKind]
    sequence_number: int
    keys_seen: int
```

### 2.4 RefreshOutcome

```python
class RefreshOutcomeKind(enum.IntEnum):
    SUCCESS_NEW       = 0  # bundle changed → verifier reregistered
    SUCCESS_UNCHANGED = 1  # sequence_number same → no-op
    TRANSPORT_ERROR   = 2  # fetch failed
    PARSE_ERROR       = 3  # bundle malformed
    PIN_MISMATCH      = 4  # pinned_sha256 set, doesn't match
    REGRESSION        = 5  # sequence_number went backwards → rejected
```

REGRESSION is the most subtle: a partner that publishes a bundle with a *lower* sequence number is either rolling back keys (acceptable for operators) or being impersonated (must reject). Default behavior: REJECT + audit; operator can force-accept via `refresh_partner(force_rollback=True)`.

---

## 3. Wave plan

| Wave | Sub-feature | Status target |
|---|---|---|
| **B.1** | SPIFFE Trust Bundle synchronization — bridge + transport + RefreshOutcome + atomic verifier re-register | **prototype this commit** |
| **B.2** | Cross-domain AIMS envelope validation — verify an AIMS envelope's signature against a federated trust domain's bundle | Sprint 18 |
| **B.3** | Selective Disclosure (BBS+) prototype for PII-safe identity — re-derive a `partial` AIMS envelope disclosing only specific claims | Sprint 19 |
| **B.4** | HTTPS_SPIFFE profile + chicken-and-egg anchor | Sprint 19 |
| **B.5** | Vault JIT-backed bundle storage (links to F5) | Sprint 20 |

---

## 4. Dependencies

### 4.1 Existing (Sprint 16 / Wave 1)

- `backend/core/security/spiffe_workload_identity.py` (F1) — single-domain verifier
- `backend/core/security/spiffe_federation.py` (F4) — multi-domain routing
- `backend/services/aims_envelope.py` (Sprint 17 W1 P2) — envelope builder + verifier
- `backend/services/vault_jit.py` (F5) — JIT-credential rotation hook

### 4.2 New for Wave B.1 (this commit)

- `backend/services/identity_federation_bridge.py` — the bridge itself
- `backend/tests/services/test_identity_federation_bridge.py` — smoke tests (transport mock + refresh outcomes)

### 4.3 New for Wave B.2 (Sprint 18)

- Cross-domain AIMS envelope verification path in `aims_envelope.py` — feed the partner's bundle in instead of the local signing key
- SPIRE server config templates for cross-org deployment (`infra/spire/`)
- New trust-bundle persistence under `infra/persistence/active_context/federation_bundles/`

### 4.4 New for Wave B.3 (Sprint 19)

- New crypto dep: `bbs-py` or equivalent IRTF CFRG-compliant BBS implementation (NO pure-Python implementations from random hubs; use the W3C-VC ecosystem's blessed lib once published)
- Selective-disclosure envelope variant in `aims_envelope.py`: `AIMSPartialEnvelope` that carries only the disclosed claims + the BBS+ proof
- Verifier method `verify_partial_envelope(partial, full_bundle)`

---

## 5. Honest scope ceilings (publish at v1.2 release)

1. **Wave B.1 ships SYNC only.** Trust-bundle exchange works; cross-domain AIMS envelope verification is Wave B.2. A B.1-only deployment lets you federate SPIFFE-IDs but still requires single-domain AIMS envelopes.
2. **HTTPS_WEB profile only.** Pinned-cert is supported (`pinned_sha256`); HTTPS_SPIFFE is Wave B.4. Operators using cross-cloud federation with mutual SPIFFE need to wait or implement the anchor flow manually.
3. **No background poller.** Wave B.1 exposes `refresh_partner` and `refresh_all` for callers (CLI cron, scheduled task) to invoke. The async poller is intentionally deferred so the threading model + cancellation are settled before background tasks land — Sprint 18.
4. **Sequence-number REGRESSION default = REJECT.** Operators with key-rotation rollback procedures must explicitly force-accept. This is conservative; the Fides paper + SPIFFE spec both recommend it for cross-org flows where you don't trust the partner to be benignly buggy.
5. **No BBS+ in Wave B.1.** BBS+ is Wave B.3 / Sprint 19 because:
   - The IRTF CFRG draft is at -10 (not yet RFC).
   - Pure-Python BBS implementations are not constant-time + have known side-channel exposure; production needs a Rust-bindings lib that doesn't yet exist in the W3C VC ecosystem.
   - W3C `vc-di-bbs` is v1.0 but adoption is uneven; we ship our own bridge with a documented interface so we can plug in the production lib when it's ready.
6. **NIST SP 800-204D scope correction.** The directive informally cited "NIST SP 800-204D for multi-domain AI environments" — the actual SP 800-204D covers Software Supply Chain Security in DevSecOps CI/CD, NOT multi-domain AI. The closest match is SP 800-204A (service-mesh microservices). Both are cited above for completeness; we do not use either as the security argument for Cluster B — that comes from the WIMSE drafts + SPIFFE Federation spec directly.

---

## 6. Threat model (Wave B.1)

| Threat | Mitigation in B.1 | Sprint-18 work |
|---|---|---|
| Partner publishes a malicious bundle | `pinned_sha256` pin + REGRESSION reject | TUF-style key history for stronger rollback resistance |
| Partner's bundle endpoint TLS-MITM'd | HTTPS_WEB cert validation (default urllib) | `pinned_cert_sha256` for cert pinning |
| Partner trust domain impersonates a different domain | The verifier registered for `trust_domain=X` is rejected if it reports a different domain | Partner-declared domain re-cross-checked against bundle metadata |
| Operator misconfigures `bundle_endpoint_url` | `register_partner` requires HTTPS scheme | Configuration file format with signing |
| Race during bundle rotation | Atomic unregister+register under bridge lock | None — already MVP-acceptable |

---

## 7. Open questions for CEO

| # | Question | Default |
|---|---|---|
| 1 | Wave B.2 (Cross-domain AIMS verify) in Sprint 18 — accept? | yes |
| 2 | Wave B.3 (BBS+) wait for production-grade BBS lib, or ship Wave 1.x with a Python BBS lib and accept the side-channel ceiling for non-EU-AI-Act-critical paths? | wait — EU AI Act 73 enforcement is too close to ship a known-not-constant-time crypto primitive |
| 3 | HTTPS_SPIFFE profile priority — Sprint 18 or Sprint 19? | Sprint 19; cross-cloud customers can use HTTPS_WEB + pinned_sha256 in the interim |
| 4 | Background poller threading model — `asyncio` (matches FastAPI) or `threading.Timer` (matches existing Sprint 16 G2 OTel)? | `asyncio` — FastAPI is the dominant call site |

---

**Status:** spec drafted; Wave B.1 prototype lands in the same commit.
