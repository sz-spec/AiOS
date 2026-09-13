# vOS Sovereign Shield — Activation Blueprint (5 components)

**Branch:** `feat/shield-integration` · **Date:** 2026-06-08 · **Core gate:** 931/931
**Type:** implementation blueprint (analysis + design; no code committed by this doc)

> ## Corrections to the brief (grounded in the actual code — read first)
> Three premises in the request are factually off; this blueprint uses the real
> architecture instead:
> 1. **App entry is `backend/app.py`** (factory: `FastAPI(...)` @81, `add_middleware`
>    @247, router registry @271–321) + `backend/router_registry.py` — **not**
>    `backend/src/main.py`.
> 2. **`runtime_firewall_adapter.py` and `external_spm_connector.py` are NOT
>    HTTP request interceptors.** `RuntimeFirewallAdapter.to_firewall(VOS3KernelEvent)`
>    translates **kernel events** into an external-firewall/SIEM event schema;
>    `external_spm_connector` generates an **AIBOM** (AI software-bill-of-materials
>    JSON) from a set of `IntegrityCertificate`s. Routing live agent HTTP traffic
>    "through them before LLM execution" would be an architectural mismatch.
>    Egress/request control already exists elsewhere (`middleware/`, `regional_policy.py`,
>    `outbound_pii_shield.py`).
> 3. **`external_spm_connector.py` has NO code stubs.** The "4 stubs" were
>    docstring ellipses in a JSON-LD example; the functions are fully
>    implemented. Phase 3 below is therefore a *parity verification* (passed),
>    not a stub-closure.

---

## Phase 1 — Wiring the 4 ready components (correct integration points)

### 1.1 `runtime_firewall_adapter` + `external_spm_connector` — what they actually wire into
- **`runtime_firewall_adapter`** consumes `VOS3KernelEvent` (kernel security events arriving over VBus) and emits a firewall/SIEM event dict (`to_firewall` / `to_firewall_batch`). **Correct integration:** a consumer of the kernel VBus event stream (e.g. inside the kernel-event relay alongside `edr_event_relay.py`), feeding an external firewall/SIEM — **not** a FastAPI request middleware.
  ```python
  # in the VBus kernel-event consumer (NOT app.py middleware)
  from core.security.connectors.runtime_firewall_adapter import RuntimeFirewallAdapter, VOS3KernelEvent
  _fw = RuntimeFirewallAdapter()
  siem_payload = _fw.to_firewall_batch(kernel_events)   # ship to external firewall/SIEM
  ```
- **`external_spm_connector`** produces an AIBOM on demand. **Correct integration:** an authenticated, admin-scoped reporting endpoint (or the SBOM-generation pipeline), invoked when an operator/auditor requests the AI-SPM bill of materials — not in the request hot path.
  ```python
  # a new admin route (mounted via router_registry behind admin auth)
  from core.security.connectors.external_spm_connector import ExternalSpmConnector
  @router.get("/admin/aibom", dependencies=[Depends(require_admin)])
  async def aibom(user=Depends(get_current_user)):
      certs = attestation_store.all_active_certificates()
      return ExternalSpmConnector().aibom(certs)
  ```
  Mount via `backend/router_registry.py` (mirror the existing `system_router`/`app_router` registration @281–321), behind the existing admin/auth dependency.

### 1.2 `leak_detector` — authenticated admin endpoint + CLI
- It is already a standalone `tracemalloc` CLI (`if __name__ == "__main__"` + argparse). Two clean exposures:
  - **CLI:** already usable — `python -m core.observability.leak_detector ...` (no change).
  - **Admin endpoint:** wrap its snapshot function in an admin route. Keep it **on-demand** (never start `tracemalloc` in the global lifespan — per-boot overhead).
  ```python
  @router.post("/admin/diag/leak-snapshot", dependencies=[Depends(require_admin)])
  async def leak_snapshot(top_n: int = 25):
      from core.observability.leak_detector import take_snapshot   # on-demand
      return take_snapshot(top_n=top_n)   # starts/stops tracemalloc within the call
  ```
- **Risk note:** `tracemalloc` has real overhead; gate behind admin auth + rate-limit; never auto-enable.

### 1.3 `merkle_log` — logging hook for critical state transitions
- **There is no single `backend/core/observability/logger.py`** (the dir has only `__init__.py` + `leak_detector.py`); structured observability lives in `backend/src/observability.py` + per-module loggers. **Correct hook point:** the audit/event sinks that already record critical transitions — e.g. `regional_policy._log_enforcement`, the compliance store, and agent-tool-invocation audit paths — should *additionally* append to a process-wide `MerkleLog` instance.
  ```python
  # a single shared sink (e.g. backend/audit/__init__.py)
  from audit.merkle_log import MerkleLog
  AUDIT_MERKLE = MerkleLog(path=os.getenv("VOS3_MERKLE_LOG_PATH", "<data-dir>/audit/merkle.log"))
  # at each critical transition / tool invocation:
  AUDIT_MERKLE.append(canonical_json_of(event))   # returns leaf hash; root is tamper-evident
  ```
- **Design constraints:** `MerkleLog` is file+thread-backed — confirm the on-disk path follows vOS data-dir conventions; appends must be non-blocking on the request path (offload or accept the small hash cost); never log raw PII (hash/redact first, consistent with `outbound_pii_shield`).

---

## Phase 2 — WebAuthn ceremony (the honest skeleton → real)

**Current state:** `backend/api/webauthn_routes.py` is a *deliberate* skeleton — all 4 handlers call `_not_yet_implemented()` (HTTP 501), and the file's own docstring says "Do NOT mount in production until verification is complete," pointing to `docs/WEBAUTHN_DESIGN_v21.7.md`. **This is real implementation work, not a wiring task.**

### 2.1 Dependency (live June-2026 sweep)
- **`py_webauthn`** (Duo Labs), PyPI package **`webauthn` v2.2.0**, tested **Python 3.9–3.13** (covers `.venv_p312`/CPython 3.12). Provides `generate_registration_options`, `verify_registration_response`, `generate_authentication_options`, `verify_authentication_response` + helper structs. **Do not hand-roll FIDO2 crypto** — use this audited library.
- Add to `requirements.txt`/`constraints.txt`: `webauthn==2.2.0` (pin to CI), install into `.venv_p312`.

### 2.2 Credential store schema
vOS uses **Convex** (`frontend/convex/schema.ts`) as the system of record; model credentials there (mirrors how users/orgs are stored), or via the SQLite local-vault for air-gapped tier. Required fields per credential:
| field | type | purpose |
|------|------|---------|
| `userId` | id→users | owner |
| `credentialId` | bytes (b64url) | unique authenticator credential |
| `publicKey` | bytes (COSE) | verification key |
| `signCount` | number | clone-detection counter |
| `aaguid` | string | authenticator model id |
| `transports` | string[] | usb/nfc/ble/internal |
| `createdAt` / `lastUsedAt` | number | audit |
Plus a short-lived **challenge** store (per-user, TTL ≤ 5 min) for the begin→finish handshake.

### 2.3 Ceremony handlers (structure — fill the 4 handlers)
- `register/begin` → `generate_registration_options(rp_id, rp_name, user, exclude_credentials=...)`; persist `challenge`; return options JSON.
- `register/finish` → `verify_registration_response(credential, expected_challenge, expected_rp_id, expected_origin)`; on success store the credential row; clear challenge.
- `authenticate/begin` → `generate_authentication_options(rp_id, allow_credentials=user's creds)`; persist challenge.
- `authenticate/finish` → `verify_authentication_response(credential, expected_challenge, expected_rp_id, expected_origin, credential_public_key, credential_current_sign_count)`; on success bump `signCount` (reject if counter regresses → clone).
- `rp_id`/`origin` from config (replace the `vos-shield.example.com` example with the real deployment origin).
- Wire `require_fresh_webauthn` (the step-up dependency, line 136) into sensitive routes only **after** the verifier passes its tests.

**Effort:** real (~1–2 days + tests). Do **not** mount the router (`router_registry`) until 2.4 is green.

---

## Phase 3 — external_spm parity audit (✅ passed; no stub closure)

**Stub status:** the 4 "stub markers" were docstring ellipses; `build_external_spm_aibom` and `ExternalSpmConnector.aibom/aibom_json` are **fully implemented**. Nothing to close.

**Interface parity proof (verified):** the connector reads exactly 3 members of `IntegrityCertificate`, all present in `backend/core/security/attestation_service.py`:
| connector uses | vos.v1 `IntegrityCertificate` | status |
|---|---|---|
| `cert.to_wiz_jsonld()` | method, line 284 (alias of `to_external_spm_jsonld`) | ✅ resolves (back-compat alias) |
| `cert.tee_measurements` | field, line 160 (`Dict[str,Any]`) | ✅ |
| `cert.signing_tier` | field, line 165 (`str`) | ✅ |
Import + module load succeed under `.venv_p312`. **Zero runtime field drift.** Action: simply consume it (Phase 1.1 admin endpoint); no code change required.

---

## Phase 4 — Risk & gate protection

| Component | Activation risk | Keep-931-green strategy |
|---|---|---|
| external_spm admin route | low (read-only AIBOM) | mount behind admin auth; add an isolated route test; the 931 gate is unaffected until mounted |
| leak_detector admin route | med (tracemalloc overhead) | on-demand only, admin+rate-limit; never in lifespan; isolated test |
| runtime_firewall_adapter | low (event translation) | consume in the VBus relay; unit-test `to_firewall` mapping |
| merkle_log hook | med (per-event hash + file IO on hot path) | async/offloaded append; PII-hashed; soak-test latency |
| webauthn router | **do not mount until verified** | finish 2.x; full ceremony tests with a virtual authenticator; mount last |

**Loopback policy:** all five are **local** (no external egress) — the suite-wide loopback-only test policy is unaffected. WebAuthn's `verify_*` is local crypto; the only "network" is the browser↔server handshake (loopback in tests). **Testing order:** per-component isolated tests first (`-p no:xdist`), then the core gate (`security/ + services/` must stay 931), then a broad sequential sweep to confirm no contamination — wire one component at a time.

---

## Sources (June 2026)
- [py_webauthn (Duo Labs) — GitHub](https://github.com/duo-labs/py_webauthn) · [docs v2.2.0](https://duo-labs.github.io/py_webauthn/index.html) · [PyPI `webauthn`](https://pypi.org/project/webauthn/) · [registration example](https://github.com/duo-labs/py_webauthn/blob/master/README.md)

*Code facts verified against `backend/app.py`, `router_registry.py`, the 5 ported
modules, and `attestation_service.py` at HEAD on `feat/shield-integration`.*
