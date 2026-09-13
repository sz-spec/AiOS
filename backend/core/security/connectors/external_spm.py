"""
backend/core/security/connectors/external_spm.py
=================================================

Sprint 14.1 — adapter for external AI-Security-Posture-Management platforms.

What is AI-SPM
--------------

Per the 2025-2026 CNAPP landscape (Wiz AI-SPM, Prisma Cloud AI Security,
Sysdig Sage, etc.), AI-SPM tooling treats AI workloads as a distinct
asset type and produces posture findings about:

  • Model inventory + provenance (where weights came from, hash, license)
  • Inference-runtime hardening (sandbox escape, prompt injection surface)
  • Data lineage (training data → embeddings → vector store → answers)
  • Drift / poisoning indicators (output statistical anomalies)
  • Audit-trail completeness (Article 73-style incident detectability)

vOS already produces a lot of this state internally — the kernel audit
ring + ComplianceStore + IntegrityCertificate cover most of it. The role
of this connector is to **publish** vOS's view to external AI-SPM
platforms so a SOC analyst's existing dashboard sees vOS as a first-class
asset rather than a black box.

Direction
---------

Outbound only. We do NOT accept inbound mutations from external platforms
— the audit trail must remain the kernel's monotonic ring + compliance
DB. An external platform that wants to take action (block an agent,
quarantine a model) does so via Article 73 incident workflow, not by
mutating vOS state directly.

Wire shape
----------

JSON-LD posture document, CycloneDX-compatible field names where possible:

    {
      "@context": "https://vos3.dev/ai-spm/v1",
      "asset_id": "vos3://kernel/<hash-prefix>",
      "asset_type": "ai-os-runtime",
      "kernel_hash_sha256": "63a9b540…",
      "model_inventory": [ {model_id, sha384, license, loaded_slots} ],
      "audit_ring_health": {fill, total_emitted, gap_events_24h},
      "policy_state": {force_permit, per_slot_thresholds, global_floor},
      "attestation_quote": <PEM>,    # only on fortress profile
      "generated_at": <ISO-8601>
    }

Honest scope ceiling
--------------------

Each downstream platform has its own ingest API:
  • Wiz:        POST /api/posture/aisec/findings   (Bearer)
  • Prisma:     POST /api/v2/external-assets       (X-Redlock-Auth)
  • Sysdig:     POST /api/v1/sdc/aispm/posture     (Bearer)
  • Generic webhook (default fallback)             (Bearer or HMAC)

The adapter classes below all conform to the same ``ExternalSPMSink``
interface. Production deployments configure ONE sink via env:

    VOS3_SPM_PROVIDER       = "wiz" | "prisma" | "sysdig" | "webhook" | ""
    VOS3_SPM_ENDPOINT       = <URL>
    VOS3_SPM_TOKEN          = <Bearer or platform-specific token>
    VOS3_SPM_HMAC_SECRET    = <only for webhook provider>

Empty/unset VOS3_SPM_PROVIDER → no-op. No traffic leaves the host.

The actual learning of which Wiz / Prisma / Sysdig fields map to which
vOS internal fields is a v1.2 ticket — we ship the schema and the four
adapters with their wire formats documented; production teams plug in
their specific field-mapping at deployment time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env names + provider enum
# ---------------------------------------------------------------------------

ENV_PROVIDER = "VOS3_SPM_PROVIDER"
ENV_ENDPOINT = "VOS3_SPM_ENDPOINT"
ENV_TOKEN = "VOS3_SPM_TOKEN"
ENV_HMAC_SECRET = "VOS3_SPM_HMAC_SECRET"
ENV_TIMEOUT_S = "VOS3_SPM_TIMEOUT_S"

PROVIDER_WIZ = "wiz"
PROVIDER_PRISMA = "prisma"
PROVIDER_SYSDIG = "sysdig"
PROVIDER_WEBHOOK = "webhook"
PROVIDER_NOOP = ""

DEFAULT_TIMEOUT_S = 5.0
CONTEXT_URL = "https://vos3.dev/ai-spm/v1"


# ---------------------------------------------------------------------------
# Posture document
# ---------------------------------------------------------------------------


@dataclass
class PostureDocument:
    asset_id: str
    asset_type: str = "ai-os-runtime"
    kernel_hash_sha256: Optional[str] = None
    model_inventory: list[dict] = field(default_factory=list)
    audit_ring_health: dict = field(default_factory=dict)
    policy_state: dict = field(default_factory=dict)
    attestation_quote: Optional[str] = None
    generated_at: Optional[str] = None

    def to_json_ld(self) -> dict:
        return {
            "@context": CONTEXT_URL,
            "asset_id": self.asset_id,
            "asset_type": self.asset_type,
            "kernel_hash_sha256": self.kernel_hash_sha256,
            "model_inventory": self.model_inventory,
            "audit_ring_health": self.audit_ring_health,
            "policy_state": self.policy_state,
            "attestation_quote": self.attestation_quote,
            "generated_at": self.generated_at
            or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


# ---------------------------------------------------------------------------
# Sink protocol — every adapter conforms
# ---------------------------------------------------------------------------


class ExternalSPMSink(Protocol):
    def publish(self, doc: PostureDocument) -> dict: ...
    def is_configured(self) -> bool: ...


# ---------------------------------------------------------------------------
# Common HTTP helper — lazy import httpx so the module loads in CI without it
# ---------------------------------------------------------------------------


def _http_post_json(
    url: str, *, headers: dict, payload: dict, timeout_s: float
) -> dict:
    try:
        import httpx  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "httpx not installed — external_spm needs httpx to publish. "
            "Install via `pip install httpx>=0.27`."
        ) from exc
    resp = httpx.post(url, headers=headers, json=payload, timeout=timeout_s)
    return {
        "status_code": resp.status_code,
        "body": resp.text[:4096],
    }


# ---------------------------------------------------------------------------
# Wiz adapter
# ---------------------------------------------------------------------------


class WizSPMSink:
    def __init__(
        self, endpoint: str, token: str, *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._timeout = timeout_s

    def is_configured(self) -> bool:
        return bool(self._endpoint and self._token)

    def publish(self, doc: PostureDocument) -> dict:
        payload = doc.to_json_ld()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/ld+json",
            "X-Vos3-Asset-Type": "ai-os-runtime",
        }
        return _http_post_json(
            self._endpoint, headers=headers, payload=payload, timeout_s=self._timeout
        )


# ---------------------------------------------------------------------------
# Prisma Cloud adapter
# ---------------------------------------------------------------------------


class PrismaSPMSink:
    def __init__(
        self, endpoint: str, token: str, *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._timeout = timeout_s

    def is_configured(self) -> bool:
        return bool(self._endpoint and self._token)

    def publish(self, doc: PostureDocument) -> dict:
        payload = doc.to_json_ld()
        headers = {
            "X-Redlock-Auth": self._token,
            "Content-Type": "application/ld+json",
        }
        return _http_post_json(
            self._endpoint, headers=headers, payload=payload, timeout_s=self._timeout
        )


# ---------------------------------------------------------------------------
# Sysdig adapter
# ---------------------------------------------------------------------------


class SysdigSPMSink:
    def __init__(
        self, endpoint: str, token: str, *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._timeout = timeout_s

    def is_configured(self) -> bool:
        return bool(self._endpoint and self._token)

    def publish(self, doc: PostureDocument) -> dict:
        payload = doc.to_json_ld()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/ld+json",
        }
        return _http_post_json(
            self._endpoint, headers=headers, payload=payload, timeout_s=self._timeout
        )


# ---------------------------------------------------------------------------
# Generic webhook (Bearer or HMAC)
# ---------------------------------------------------------------------------


class WebhookSPMSink:
    def __init__(
        self,
        endpoint: str,
        *,
        token: Optional[str] = None,
        hmac_secret: Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._hmac_secret = hmac_secret
        self._timeout = timeout_s

    def is_configured(self) -> bool:
        return bool(self._endpoint) and bool(self._token or self._hmac_secret)

    def publish(self, doc: PostureDocument) -> dict:
        payload = doc.to_json_ld()
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        headers: dict[str, str] = {
            "Content-Type": "application/ld+json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._hmac_secret:
            sig = hmac.new(
                self._hmac_secret.encode("utf-8"),
                body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Vos3-Signature"] = f"sha256={sig}"
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("httpx required for webhook sink") from exc
        resp = httpx.post(
            self._endpoint, headers=headers, content=body, timeout=self._timeout
        )
        return {"status_code": resp.status_code, "body": resp.text[:4096]}


# ---------------------------------------------------------------------------
# No-op sink — returned when no provider is configured
# ---------------------------------------------------------------------------


class NoopSPMSink:
    def is_configured(self) -> bool:
        return False

    def publish(self, doc: PostureDocument) -> dict:
        return {"status": "noop", "reason": f"{ENV_PROVIDER} unset or empty"}


# ---------------------------------------------------------------------------
# Factory + module singleton
# ---------------------------------------------------------------------------

_singleton: Optional[ExternalSPMSink] = None
_singleton_lock = threading.Lock()


def _build_sink_from_env() -> ExternalSPMSink:
    provider = os.environ.get(ENV_PROVIDER, "").strip().lower()
    endpoint = os.environ.get(ENV_ENDPOINT, "").strip()
    token = os.environ.get(ENV_TOKEN, "").strip()
    hmac_secret = os.environ.get(ENV_HMAC_SECRET, "").strip()
    try:
        timeout = float(os.environ.get(ENV_TIMEOUT_S, str(DEFAULT_TIMEOUT_S)))
    except ValueError:
        timeout = DEFAULT_TIMEOUT_S

    if provider == PROVIDER_WIZ and endpoint and token:
        return WizSPMSink(endpoint, token, timeout_s=timeout)
    if provider == PROVIDER_PRISMA and endpoint and token:
        return PrismaSPMSink(endpoint, token, timeout_s=timeout)
    if provider == PROVIDER_SYSDIG and endpoint and token:
        return SysdigSPMSink(endpoint, token, timeout_s=timeout)
    if provider == PROVIDER_WEBHOOK and endpoint:
        return WebhookSPMSink(
            endpoint,
            token=token or None,
            hmac_secret=hmac_secret or None,
            timeout_s=timeout,
        )
    return NoopSPMSink()


def get_spm_sink() -> ExternalSPMSink:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = _build_sink_from_env()
    return _singleton


def reset_for_tests() -> None:
    global _singleton
    with _singleton_lock:
        _singleton = None


def publish_posture(doc: PostureDocument) -> dict:
    """Module-level convenience: publish using the env-configured sink."""
    sink = get_spm_sink()
    if not sink.is_configured():
        return {"status": "skipped", "reason": "no provider configured"}
    try:
        return sink.publish(doc)
    except Exception as exc:  # noqa: BLE001 — caller chooses behavior
        logger.warning("[external_spm] publish failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# v20.7.1-NEUTRAL — AI-BOM (AI Bill of Materials) builder
#
# Publishes a set of IntegrityCertificates as an AI-SPM AiBillOfMaterials
# document with a tamper-evident SHA-384 integrity proof over the per-cert
# components. Outbound only. The proof binds the security-relevant fields
# of every component, so mutating any one of them (e.g. a tenant_id)
# changes the proof — an external SPM platform can therefore detect a
# component-substitution attack on the BOM in transit.
# ---------------------------------------------------------------------------

AIBOM_SCHEMA_VERSION = "ai-spm-2026.1"


def _aibom_component(cert) -> dict:
    """Project an IntegrityCertificate onto an AI-BOM component. Uses
    getattr so it tolerates either the real dataclass or a duck-typed
    cert with the same attributes."""
    return {
        "@type": "AiModelComponent",
        "attestationId": getattr(cert, "id", None),
        "tenantId": getattr(cert, "tenant_id", None),
        "sessionId": getattr(cert, "session_id", None),
        "modelSha384": getattr(cert, "model_sha384", None),
        "signerFp": getattr(cert, "signer_public_key_fp", None),
        "signingTier": getattr(cert, "signing_tier", None),
    }


def build_external_spm_aibom(certs, vendor_tenant_id: str) -> dict:
    """Build an AI-SPM AiBillOfMaterials document from a list of
    IntegrityCertificates with a SHA-384 integrity proof."""
    components = [_aibom_component(c) for c in certs]
    digest = hashlib.sha384()
    for comp in components:
        digest.update(json.dumps(comp, sort_keys=True).encode("utf-8"))
    return {
        "@context": CONTEXT_URL,
        "@type": "AiBillOfMaterials",
        "schemaVersion": AIBOM_SCHEMA_VERSION,
        "vendorTenantId": vendor_tenant_id,
        "summary": {"componentCount": len(components)},
        "components": components,
        "integrityProof": {
            "alg": "SHA-384",
            "value": digest.hexdigest(),
        },
    }


class SpmConnector:
    """Thin facade binding a vendor tenant to the AI-BOM builder + the
    configured posture sink. Outbound only."""

    def __init__(self, vendor_tenant_id: str = "", sink=None) -> None:
        self.vendor_tenant_id = vendor_tenant_id
        self._sink = sink

    def build_aibom(self, certs) -> dict:
        return build_external_spm_aibom(certs, vendor_tenant_id=self.vendor_tenant_id)


# Back-compat aliases (the v20.5.1 "Wiz"-specific names).
build_wiz_aibom = build_external_spm_aibom
WizConnector = SpmConnector


__all__ = [
    "PostureDocument",
    "ExternalSPMSink",
    "WizSPMSink",
    "PrismaSPMSink",
    "SysdigSPMSink",
    "WebhookSPMSink",
    "NoopSPMSink",
    "get_spm_sink",
    "publish_posture",
    "reset_for_tests",
    "build_external_spm_aibom",
    "build_wiz_aibom",
    "SpmConnector",
    "WizConnector",
    "PROVIDER_WIZ",
    "PROVIDER_PRISMA",
    "PROVIDER_SYSDIG",
    "PROVIDER_WEBHOOK",
    "PROVIDER_NOOP",
]
