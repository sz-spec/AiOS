# vOS Sovereign Shield — Porting Feasibility Analysis (5 assets)

**Date:** 2026-06-08 · **Type:** read-only dependency/structure audit (no copies, no code changes)
**Source:** `VOS3-Cyber` · **Target:** `vos.v1` @ `72ebe6a` (moat 49/80, core gate 931/931)

> **Scope of confidence:** this audit verifies **size, import-level dependency
> resolution, filename collisions, and target-dir existence** — i.e. whether each
> file would *import cleanly* into vos.v1. It does **not** verify semantic /
> interface compatibility (e.g. whether `external_spm` uses
> `IntegrityCertificate`'s fields exactly as vos.v1 defines them) or whether the
> stubbed logic is functionally acceptable. "Clean import" ≠ "functionally
> correct." Each port still needs code review before wiring.

## Summary index

| # | Asset | Lines | def/class | Stub markers | Internal deps | Target dir | Collision | **Risk** |
|---|-------|------:|-----------|-------------:|---------------|------------|-----------|----------|
| 1 | `runtime_firewall_adapter.py` | 172 | 3 / 2 | 2 | **none (stdlib only)** | `core/security/connectors/` ✅ | none | **LOW** |
| 2 | `leak_detector.py` | 190 | 5 / 1 | 0 | **none (stdlib only)** | `core/observability/` ✗ (create) | none | **LOW** |
| 3 | `merkle_log.py` | 238 | 7 / 1 | 0 | **none (stdlib only)** | `backend/audit/` ✗ (create) | none | **LOW** |
| 4 | `external_spm_connector.py` | 157 | 3 / 1 | 4 | 1: `..attestation_service.IntegrityCertificate` → **present in vos.v1** | `core/security/connectors/` ✅ | none | **LOW–MED** |
| 5 | `webauthn_routes.py` | 146 | 6 / 6 | **8** | none (fastapi/pydantic only) | `backend/api/` ✅ | none | **MEDIUM** |

**Universal property:** all 5 are **inert until imported/mounted**. A pure
file-copy that does not wire them into a collected test, the security pipeline,
or `main.py` **cannot corrupt the 931-test gate** — the gate risk is entirely in
the *wiring*, not the *copy*.

---

## 1. `runtime_firewall_adapter.py` — LOW
- **Completeness:** real (172 lines, 2 classes + 3 funcs); 2 stub markers (minor).
- **Deps:** `time, uuid, dataclasses, typing` — **100% stdlib, zero internal deps.**
- **Modernization delta:** none — `from __future__ import annotations` + dataclasses are 3.12-native. No v20-legacy coupling.
- **Risk:** **LOW.** Self-contained; copies into the existing `connectors/` dir beside `edr_event_relay.py`; inert until imported. Wiring it into the runtime-firewall path is the only review surface.

## 2. `leak_detector.py` — LOW
- **Completeness:** real (190 lines, 5 funcs, 1 class); **0 stub markers.** A standalone observability/diagnostic tool (uses `tracemalloc`, `gc`, `resource`).
- **Deps:** `argparse, gc, json, sys, time, tracemalloc, dataclasses, typing, resource` — **100% stdlib, zero internal deps.**
- **Modernization delta:** none. Requires creating `backend/core/observability/` (+`__init__.py`).
- **Risk:** **LOW.** Pure diagnostic; no app coupling; inert until invoked.

## 3. `merkle_log.py` — LOW
- **Completeness:** real (238 lines, 7 funcs, 1 class); **0 stub markers.** A Merkle append-only audit log (`hashlib`, `threading`, file-backed via `pathlib`).
- **Deps:** `hashlib, logging, os, threading, pathlib, typing` — **100% stdlib, zero internal deps.**
- **Modernization delta:** none. Requires creating `backend/audit/` (+`__init__.py`).
- **Risk:** **LOW.** Self-contained; complements the existing `compliance_store`/attestation surface. Review: confirm its on-disk path policy fits vos.v1's data-dir conventions.

## 4. `external_spm_connector.py` — LOW–MEDIUM
- **Completeness:** real (157 lines, 1 class + 3 funcs); **4 stub markers** (some placeholder logic).
- **Deps:** stdlib (`hashlib, json, time, collections, dataclasses, typing`) + **one internal:** `from ..attestation_service import IntegrityCertificate`. **Verified present** in vos.v1 at `backend/core/security/attestation_service.py:94`.
- **Modernization delta:** import resolves; the **open question is interface compatibility** — confirm `IntegrityCertificate`'s fields/constructor in vos.v1 match what this connector consumes (the two repos' attestation_service may have diverged). This is the one real review item.
- **Risk:** **LOW–MEDIUM.** Imports cleanly; risk is the `IntegrityCertificate` interface drift + the 4 stubs. Inert until imported into the SPM path. This is one of the 2 Stage-10 connectors vos.v1's own CLAUDE.md lists as "not yet ported."

## 5. `webauthn_routes.py` — MEDIUM
- **Completeness:** **partial.** 146 lines, 6 Pydantic models + 6 handlers, but **8 stub markers** — the handler bodies are largely placeholders (no real WebAuthn ceremony / credential store).
- **Deps:** `fastapi, pydantic` only — **no internal deps, and notably no `webauthn`/`fido2` library import** (so the actual crypto ceremony isn't implemented here).
- **Modernization delta:** (a) it is **not auto-mounted** — `main.py` uses explicit `include_router`, so it stays inert unless manually registered; (b) to be functional it needs auth integration (`api.deps.get_current_user`), a credential repository, and a real WebAuthn/FIDO2 backend.
- **Risk:** **MEDIUM.** Copy is zero-risk (inert, no collision), but it is **functionally a skeleton** — porting it "green" would be importing a stub, not a feature. Treat as scaffold-to-finish, not a ready capability.

---

## Recommended porting order (if approved later)
1. **merkle_log.py + leak_detector.py** (LOW, zero-dep, 0 stubs) — cleanest, highest-confidence; create `backend/audit/` + `core/observability/`.
2. **runtime_firewall_adapter.py** (LOW, zero-dep) — lands beside `edr_event_relay.py`.
3. **external_spm_connector.py** (LOW–MED) — after verifying `IntegrityCertificate` interface parity; completes 2/3 of the Stage-10 connector batch.
4. **webauthn_routes.py** (MEDIUM) — only if WebAuthn is a wanted feature; budget for finishing the stubbed ceremony + mounting + auth wiring. Do **not** port-and-claim-done.

## Gate-safety statement
A file-copy of items 1–4 (without wiring) is **931-gate-safe** (inert, no
collisions, deps resolve). Item 5 is copy-safe but functionally incomplete.
**Activation/wiring** of any item (mounting routes, importing into pipelines) is
where regression risk lives and must be tested per-item.

## Verification (reproduce)
- Sizes/stubs: `wc -l` + `grep -cE 'def|class|TODO|NotImplemented|stub' <file>`
- Deps: `grep -nE '^\s*(from|import)' <file>`
- Dep presence: `grep -n 'class IntegrityCertificate' backend/core/security/attestation_service.py`
- Collisions: `find backend -name '<n>.py' | grep -v .venv`
