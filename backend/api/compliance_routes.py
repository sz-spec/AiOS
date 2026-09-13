"""
backend/api/compliance_routes.py
=================================

Sprint 14.1 — EU AI Act Article 73 compliance surface.

Mounted under ``/api/compliance`` by router_registry.mount_routers().

Endpoints
---------

  GET   /api/compliance/audit/failures        — drain + return audit ring events
  GET   /api/compliance/audit/status          — drain stats (no event payload)
  POST  /api/compliance/incident/report       — operator-filed serious incident
  GET   /api/compliance/incidents             — list filed incidents
  GET   /api/compliance/annex-iv/export       — Annex IV technical-doc bundle
  POST  /api/compliance/edr/relay              — EDR webhook receiver
  GET   /api/compliance/posture               — current AI-SPM posture snapshot
  POST  /api/compliance/posture/publish        — push posture to external SPM

Article 73 contract (EU AI Act, in force 2026-08-02)
----------------------------------------------------

Providers of high-risk AI systems must notify the relevant market-
surveillance authority of a "serious incident" within **15 days** of
awareness, escalated to **2 days** if the incident involves death or
serious harm. This module collects the incident record at the moment
of awareness; the actual filing to the authority's submission portal
is operator-driven (the regulator does not yet expose a stable HTTPS
API; the European Commission publishes a reporting template, not an
endpoint).

The endpoint returns the structured record an operator can paste / file.
We persist it locally so the operator can prove (a) we detected, (b)
when we detected, (c) what we reported, all timestamped.

Auth
----

All endpoints require an authenticated user (Bearer JWT via Clerk),
matching the convention in every other route module. Auditor-only
endpoints (annex-iv/export, incidents/list) additionally require the
``audit:read`` permission.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user, AuthenticatedUser

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Lazy service accessors — let import succeed even if Stage-10 services are
# not yet on path (dev workstation w/ partial install).
# ---------------------------------------------------------------------------


def _get_compliance_store():
    try:
        from services.compliance_store import get_compliance_store

        return get_compliance_store()
    except ImportError as exc:
        raise HTTPException(
            status_code=503, detail=f"compliance_store unavailable: {exc}"
        )


def _get_vbus_ring_buffer():
    """Construct an on-demand ring buffer bound to the global VBus driver +
    compliance store. Cached at module level via lru-cache pattern."""
    global _ring_buffer_singleton
    if _ring_buffer_singleton is not None:
        return _ring_buffer_singleton
    from services.vbus_driver import get_vbus_driver  # type: ignore
    from services.vbus_ring_buffer import VBusRingBuffer

    driver = get_vbus_driver()
    store = _get_compliance_store()
    _ring_buffer_singleton = VBusRingBuffer(driver, store)
    return _ring_buffer_singleton


_ring_buffer_singleton = None


def _get_edr_relay():
    from services.compliance_store import get_compliance_store
    from core.security.connectors.edr_event_relay import get_edr_relay

    return get_edr_relay(store=get_compliance_store())


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class IncidentReportIn(BaseModel):
    """Operator-filed serious incident, Article 73 §1."""

    severity: str = Field(
        ..., description="low | medium | high | critical | death-harm"
    )
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=5000)
    affected_systems: list[str] = Field(default_factory=list)
    detected_at: Optional[int] = Field(
        None, description="Unix ts of detection; default=now"
    )
    related_slot_ids: list[int] = Field(default_factory=list)
    related_model_ids: list[str] = Field(default_factory=list)
    related_intent_grants: list[str] = Field(default_factory=list)
    operator_notes: Optional[str] = None


class IncidentReportOut(BaseModel):
    incident_id: str
    severity: str
    detected_at: int
    reported_at: int
    article_73_deadline: int  # unix ts by which authority filing must complete
    persisted: bool


class AuditDrainOut(BaseModel):
    new: int
    gap: int
    total: int
    fill: int
    highest_seq_seen: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_audit_permission(user: AuthenticatedUser) -> None:
    if not user.has_permission("audit:read") and not user.has_permission("admin:full"):
        raise HTTPException(status_code=403, detail="audit:read permission required")


# Opaque session ids are "<tenant>:<opaque>". Cap the whole thing so a
# pathological id can't blow out a query or a log line.
_MAX_OPAQUE_SESSION_ID_LEN = 260
# Characters that have no business in a tenant-prefixed opaque id and that
# would enable path-traversal / null-injection if they reached a sink.
_FORBIDDEN_SESSION_ID_TOKENS = ("..", "/", "\\", "\x00")


def _enforce_session_ownership(opaque_id: str, user) -> str:
    """IDOR gate for /api/compliance/attestation.

    Admits only sessions whose ``<tenant>:`` prefix matches the caller's
    authenticated ``tenant_id``. Returns the validated tenant prefix.
    Raises HTTPException: 403 when the caller has no tenant claim or the
    prefix mismatches (cross-tenant), 400 on a malformed / oversize /
    traversal-bearing id. Fail-closed."""
    caller_tenant = getattr(user, "tenant_id", None)
    if not caller_tenant:
        raise HTTPException(status_code=403, detail="caller has no tenant claim")
    if not opaque_id or len(opaque_id) > _MAX_OPAQUE_SESSION_ID_LEN:
        raise HTTPException(status_code=400, detail="invalid session id length")
    if any(tok in opaque_id for tok in _FORBIDDEN_SESSION_ID_TOKENS):
        raise HTTPException(status_code=400, detail="invalid characters in session id")
    if ":" not in opaque_id:
        raise HTTPException(status_code=400, detail="session id missing tenant prefix")
    tenant_prefix, _, rest = opaque_id.partition(":")
    if not tenant_prefix or not rest:
        raise HTTPException(status_code=400, detail="empty tenant or opaque segment")
    if tenant_prefix != caller_tenant:
        raise HTTPException(
            status_code=403, detail="cross-tenant session access denied"
        )
    return tenant_prefix


def _article_73_deadline(severity: str, detected_at: int) -> int:
    """Return the Unix timestamp by which the operator must file with the
    authority. 2 days for death-harm, 15 days otherwise."""
    if severity.strip().lower() in {"death-harm", "critical"}:
        return detected_at + 2 * 24 * 3600
    return detected_at + 15 * 24 * 3600


# ---------------------------------------------------------------------------
# Endpoints — audit ring
# ---------------------------------------------------------------------------


@router.get("/compliance/audit/failures", summary="Drain kernel audit ring")
async def audit_drain(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuditDrainOut:
    """Pull the latest events from the kernel audit ring and persist them.

    Returns the drain summary. To page through historical events use
    GET /api/compliance/incidents (this endpoint is for the rolling
    refresh that the Sovereign control panel hits every 2s)."""
    ring = _get_vbus_ring_buffer()
    summary = ring.drain_once()
    return AuditDrainOut(
        new=int(summary.get("new", 0)),
        gap=int(summary.get("gap", 0)),
        total=int(summary.get("total", 0)),
        fill=int(summary.get("fill", 0)),
        highest_seq_seen=int(summary.get("highest_seq_seen", 0)),
    )


@router.get("/compliance/audit/status", summary="Audit ring status (no drain)")
async def audit_status(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    ring = _get_vbus_ring_buffer()
    return ring.status()


# ---------------------------------------------------------------------------
# Endpoints — incident reporting (Article 73)
# ---------------------------------------------------------------------------


@router.post(
    "/compliance/incident/report", summary="File serious incident (Article 73)"
)
async def incident_report(
    body: IncidentReportIn,
    user: AuthenticatedUser = Depends(get_current_user),
) -> IncidentReportOut:
    detected_at = body.detected_at or int(time.time())
    reported_at = int(time.time())
    incident_id = f"vos-incident-{uuid.uuid4().hex[:12]}"
    deadline = _article_73_deadline(body.severity, detected_at)

    record = {
        "incident_id": incident_id,
        "severity": body.severity,
        "title": body.title,
        "description": body.description,
        "affected_systems": body.affected_systems,
        "detected_at": detected_at,
        "reported_at": reported_at,
        "operator_user_id": user.id,
        "operator_org_id": user.org_id,
        "related_slot_ids": body.related_slot_ids,
        "related_model_ids": body.related_model_ids,
        "related_intent_grants": body.related_intent_grants,
        "operator_notes": body.operator_notes,
        "article_73_deadline": deadline,
    }

    persisted = _persist_incident(record)

    logger.warning(
        "[compliance] Article 73 incident filed: id=%s severity=%s operator=%s deadline_ts=%d",
        incident_id,
        body.severity,
        user.id,
        deadline,
    )

    return IncidentReportOut(
        incident_id=incident_id,
        severity=body.severity,
        detected_at=detected_at,
        reported_at=reported_at,
        article_73_deadline=deadline,
        persisted=persisted,
    )


@router.get("/compliance/incidents", summary="List filed incidents")
async def incidents_list(
    user: AuthenticatedUser = Depends(get_current_user),
    limit: int = 100,
) -> dict:
    _require_audit_permission(user)
    return {"incidents": _read_incidents(limit=limit)}


def _incidents_path() -> str:
    return os.environ.get("VOS3_INCIDENTS_PATH", "/tmp/vos_incidents.jsonl")


def _persist_incident(record: dict) -> bool:
    """Append the incident as one JSONL line. Compliance store is the
    bigger sibling for kernel-emitted events; incidents are operator-
    filed and stored separately so an auditor can pull them as one file
    without grepping the kernel event stream."""
    try:
        path = _incidents_path()
        os.makedirs(os.path.dirname(path) or "/tmp", exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[compliance] incident persist failed: %s", exc)
        return False


def _read_incidents(*, limit: int = 100) -> list[dict]:
    path = _incidents_path()
    if not os.path.exists(path):
        return []
    records: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("[compliance] incident read failed: %s", exc)
        return []
    # Return newest first
    records.sort(key=lambda r: r.get("reported_at", 0), reverse=True)
    return records[:limit]


# ---------------------------------------------------------------------------
# Endpoints — Annex IV export
# ---------------------------------------------------------------------------


@router.get("/compliance/annex-iv/export", summary="Export Annex IV technical file")
async def annex_iv_export(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Return the EU AI Act Annex IV bundle as a single JSON document.

    Nine mandatory sections per EU AI Act Annex IV Article 11(1):
      1. General description
      2. Detailed design (system, components, integration)
      3. Monitoring + functioning + control
      4. Risk-management system
      5. Changes through lifecycle
      6. Standards applied
      7. EU declaration of conformity
      8. Post-market monitoring plan
      9. Logs (system + serious-incident; here we link to the audit ring)
    """
    _require_audit_permission(user)

    return {
        "@context": "https://vos3.dev/eu-ai-act/annex-iv/v1",
        "exported_at": int(time.time()),
        "exported_by": user.id,
        "sections": {
            "1_general_description": _section_general(),
            "2_detailed_design": _section_design(),
            "3_monitoring": _section_monitoring(),
            "4_risk_management": _section_risk(),
            "5_lifecycle_changes": _section_lifecycle(),
            "6_standards": _section_standards(),
            "7_declaration_of_conformity": _section_doc(),
            "8_post_market_plan": _section_post_market(),
            "9_logs": _section_logs(),
        },
    }


def _section_general() -> dict:
    return {
        "system_name": "vOS.v1",
        "version": os.environ.get("VOS3_VERSION", "v1.1.0-rc"),
        "intended_purpose": (
            "AI-infrastructure runtime: hosts third-party AI models in "
            "hardware-isolated slots with kernel-enforced intent gating, "
            "audit ring, and post-quantum-ready cryptography."
        ),
        "provider": os.environ.get("VOS3_PROVIDER_NAME", "vOS Project"),
        "ce_marked": False,
        "developer_preview": True,
    }


def _section_design() -> dict:
    return {
        "architecture_doc": "ARCHITECTURE.md",
        "cyber_overlay_doc": "docs/CYBER_OVERLAY_INTEGRATION.md",
        "kernel_provenance_doc": "docs/PROVENANCE.md",
        "kernel_sha256": os.environ.get("VOS3_KERNEL_SHA256", "unknown"),
        "z3_proofs_path": "backend/tests/benchmarks/*_z3_proof.py",
    }


def _section_monitoring() -> dict:
    try:
        ring = _get_vbus_ring_buffer()
        status = ring.status()
    except Exception:  # noqa: BLE001
        status = {"unavailable": True}
    return {
        "audit_ring_status": status,
        "policy_override_doc": "docs/POLICY_OVERRIDE.md",
        "streaming_fidelity_doc": "docs/STREAMING_FIDELITY.md",
    }


def _section_risk() -> dict:
    return {
        "hardening_plan_doc": "VOS3_Hardening_Plan.md",
        "doomsday_gauntlet_doc": "docs/DOOMSDAY_GAUNTLET_v20.5.md",
        "hardware_cve_inventory_doc": "docs/HARDWARE_CVE_INVENTORY_2010_2026.md",
    }


def _section_lifecycle() -> dict:
    return {
        "milestones_doc": "VOS3_PROJECT_MILESTONES_AND_EXECUTIVE_SUMMARY.md",
        "errata_doc": "vOS_Product_Specification_v1.1_errata.md",
    }


def _section_standards() -> dict:
    return {
        "post_quantum_inventory_doc": "docs/POST_QUANTUM_INVENTORY.md",
        "nist_ai_600_mapping_doc": "docs/NIST_AI_600-1_MAPPING.md",
        "standards_mapping_doc": "docs/compliance/2026_STANDARDS_MAPPING.md",
    }


def _section_doc() -> dict:
    return {
        "status": "draft",
        "note": "EU declaration of conformity is filed by the deployer, not the project.",
    }


def _section_post_market() -> dict:
    try:
        incidents = _read_incidents(limit=50)
    except Exception:  # noqa: BLE001
        incidents = []
    return {
        "incidents_count": len(incidents),
        "article_73_endpoint": "/api/compliance/incident/report",
        "incident_log_path": _incidents_path(),
    }


def _section_logs() -> dict:
    try:
        store = _get_compliance_store()
        highest = store.highest_seq() if hasattr(store, "highest_seq") else 0
    except Exception:  # noqa: BLE001
        highest = -1
    return {
        "compliance_db_path": "/tmp/vos_compliance.db",
        "highest_seq_persisted": highest,
        "audit_ring_kernel_source": "kernel/src/mm/audit_ring.c",
    }


# ---------------------------------------------------------------------------
# Endpoints — EDR webhook
# ---------------------------------------------------------------------------


@router.post("/compliance/edr/relay", summary="EDR webhook receiver")
async def edr_relay(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Receive an EDR event, validate signature, persist enriched record."""
    from core.security.connectors.edr_event_relay import verify_signature

    raw = await request.body()
    sig = request.headers.get("X-Vos3-Signature")
    if not verify_signature(raw_body=raw, header_signature=sig):
        raise HTTPException(status_code=401, detail="bad EDR HMAC signature")

    try:
        body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"bad JSON: {exc}")

    relay = _get_edr_relay()
    return relay.relay(body)


# ---------------------------------------------------------------------------
# Endpoints — SPM posture
# ---------------------------------------------------------------------------


@router.get("/compliance/posture", summary="AI-SPM posture snapshot")
async def posture_snapshot(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    from core.security.connectors.external_spm import PostureDocument

    try:
        ring = _get_vbus_ring_buffer()
        ring_status = ring.status()
    except Exception:  # noqa: BLE001
        ring_status = {}

    doc = PostureDocument(
        asset_id=f"vos3://kernel/{os.environ.get('VOS3_KERNEL_SHA256', 'unknown')[:16]}",
        kernel_hash_sha256=os.environ.get("VOS3_KERNEL_SHA256"),
        audit_ring_health={
            "highest_seq_seen": ring_status.get("highest_seq_seen"),
            "last_total_from_kernel": ring_status.get("last_total_from_kernel"),
            "last_drain_at": ring_status.get("last_drain_at"),
        },
        policy_state=_safe_policy_status(),
    )
    return doc.to_json_ld()


@router.post("/compliance/posture/publish", summary="Push posture to external SPM")
async def posture_publish(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    from core.security.connectors.external_spm import PostureDocument, publish_posture

    doc_dict = await posture_snapshot(user)
    # Reconstruct PostureDocument from dict shape we just produced.
    doc = PostureDocument(
        asset_id=doc_dict["asset_id"],
        kernel_hash_sha256=doc_dict.get("kernel_hash_sha256"),
        audit_ring_health=doc_dict.get("audit_ring_health", {}),
        policy_state=doc_dict.get("policy_state", {}),
    )
    return publish_posture(doc)


def _safe_policy_status() -> dict:
    try:
        from services.policy_override import get_policy_service

        svc = get_policy_service()
        snap = svc.status()
        return {
            "force_permit": getattr(snap, "force_permit", None),
            "global_floor": getattr(snap, "global_floor", None),
            "per_slot_thresholds": getattr(snap, "per_slot_thresholds", {}),
        }
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# Sovereign Shield — External-SPM AI-BOM export (Phase 1.1, read-only)
# ---------------------------------------------------------------------------


def _collect_integrity_certificates():
    """Return the currently-registered Sovereign ``IntegrityCertificate``s.

    HONEST STATE (2026-06): there is no persistent ``IntegrityCertificate``
    corpus yet — ``attestation_service`` generates/verifies them on demand and
    ``cert_vault`` stores X.509 (a different type), not these. So this returns
    an empty tuple today, and the AI-BOM below reflects that truthfully
    (``componentCount == 0``). When a cert store is wired, return its active
    set here; the endpoint shape does not change.
    """
    return ()


@router.get(
    "/compliance/aibom",
    summary="External-SPM AI-BOM export (read-only; reflects registered integrity certs)",
)
async def export_external_spm_aibom(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Build the external-SPM-ingestible AI-BOM over the registered Sovereign
    Integrity Certificates. Read-only, admin/audit-gated, no side effects, no
    blocking of agent execution. Returns a well-formed AI-BOM (currently empty
    until a cert corpus is wired — see ``_collect_integrity_certificates``)."""
    _require_audit_permission(user)
    from core.security.connectors.external_spm_connector import (
        build_external_spm_aibom,
    )

    certs = _collect_integrity_certificates()
    vendor_tenant_id = user.org_id or "vos3-default-tenant"
    return build_external_spm_aibom(certs, vendor_tenant_id=vendor_tenant_id)


# ---------------------------------------------------------------------------
# Sovereign Shield — on-demand leak-detection snapshot (Phase 1.2)
# ---------------------------------------------------------------------------

# Serialize snapshots: tracemalloc is process-global, so concurrent runs would
# corrupt each other's start/stop state. Non-blocking acquire -> 409 if busy.
_LEAK_SNAPSHOT_LOCK = threading.Lock()


class LeakSnapshotRequest(BaseModel):
    """Bounded parameters for an on-demand leak-detection run. Maxima clamp the
    cost so an admin cannot trigger a runaway profiling job on the live process."""

    agents: int = Field(50, ge=1, le=200)
    cycles: int = Field(25, ge=1, le=100)
    threshold_kb: float = Field(512.0, gt=0, le=1_000_000)
    top_n: int = Field(10, ge=1, le=50)


@router.post(
    "/compliance/leaks/snapshot",
    summary="On-demand bounded leak-detection snapshot (admin; synthetic-load self-test)",
)
async def leak_detection_snapshot(
    req: Optional[LeakSnapshotRequest] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Run the leak detector on demand and return its LeakReport as JSON.

    HONEST SCOPE: this is a *synthetic-agent-load* leak self-test — it allocates
    a simulated workload between two tracemalloc snapshots and reports the drift.
    It is NOT a snapshot of the live request-serving process's working set.
    tracemalloc is started AND stopped strictly inside the call (never at global
    startup / in the lifespan), the params are clamped, the run is offloaded off
    the event loop, and concurrent runs are rejected (409) — so core runtime
    performance is protected.
    """
    _require_audit_permission(user)
    params = req or LeakSnapshotRequest()

    if not _LEAK_SNAPSHOT_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail="a leak snapshot is already running"
        )
    try:
        from dataclasses import asdict
        from core.observability.leak_detector import run as _run_leak_detector

        report = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _run_leak_detector(
                agents=params.agents,
                cycles=params.cycles,
                threshold_kb=params.threshold_kb,
                top_n=params.top_n,
            ),
        )
    finally:
        _LEAK_SNAPSHOT_LOCK.release()

    out = asdict(report)
    out["pass"] = out.pop("pass_")  # clean public key
    out["mode"] = "synthetic-agent-load self-test (tracemalloc bounded within call)"
    return out


# ---------------------------------------------------------------------------
# Phase 22 — B3-1 eBPF-LSM compliance evidence bundle.
#
# HONEST SCOPE: serves SELF-COLLECTED runtime evidence as INPUT for an
# independent external auditor. It is NOT an external audit and does NOT
# advance the moat tally (the bundle records external_audit_status=PENDING /
# moat_impact=none). Read-only: regeneration is the out-of-band
# infra/audit/generate_compliance_proof.sh (privileged Docker) — NOT triggered
# from this web handler (docker-exec-from-route is an RCE-shaped anti-pattern).
# ---------------------------------------------------------------------------
@router.get(
    "/audit/compliance-proof",
    summary="B3-1 eBPF-LSM runtime compliance evidence bundle (auditor input)",
)
async def get_compliance_proof(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    import json
    from pathlib import Path

    payload = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "audit"
        / "compliance_payload.json"
    )
    if not payload.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "compliance bundle not generated; run "
                "infra/audit/generate_compliance_proof.sh on a Linux/Docker host"
            ),
        )
    try:
        return json.loads(payload.read_text())
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover
        raise HTTPException(
            status_code=500, detail=f"compliance bundle unreadable: {exc}"
        ) from exc
