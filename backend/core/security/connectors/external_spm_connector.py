"""
v20.7.1-NEUTRAL — External AI-SPM (Security Posture Management) connector.

Builds the **AI-BOM** ("AI Bill of Materials") payload an external
AI-SPM platform's graph ingests. The single-cert form is provided by
``IntegrityCertificate.to_wiz_jsonld()`` (method name retained for
back-compat with v20.4-TITAN); this module adds the **bulk /
aggregate** payload shape SPM tools use for inventory-level ingestion.

External SPM AI-BOM envelope (industry-standard JSON-LD)
========================================================

Per the public AI-SPM ingest patterns published by Tier-1 security
platforms in 2026, the AI-BOM ingest endpoint accepts a JSON-LD
object with:

    {
      "@context": [...credential v2 + AI-SPM schema URLs...],
      "@type":    "AiBillOfMaterials",
      "schemaVersion": "ai-spm-2026.1",
      "tenant":   { "id": "<vendor-side-tenant>" },
      "issuedAt": "<ISO-8601>",
      "components": [
        { "@type": "AiRuntimeAttestation", ... },   # one per cert
        ...
      ],
      "summary": {
        "componentCount": N,
        "platformDistribution": { "INTEL_TDX": k1, "BAREMETAL_NO_TEE": k2, ... },
        "signingTiers": { "dev": k1, "prod-oidc-rekor": k2 }
      },
      "integrityProof": {
        "alg": "SHA-384",
        "value": "<hash of canonical JSON of components[]>"
      }
    }

The ``integrityProof`` lets a consuming SPM-side reader detect AI-BOM
tampering without re-verifying every individual cert signature. The
SPM still re-verifies signatures for any component it chooses to
surface in its graph — this is the bulk-shape's tamper detector for
the *envelope*, not a substitute for cert-level verification.

Honest scope
============

We have not committed to any specific external SPM partner agreement.
This module implements *our side* of the handshake to the publicly
documented AI-SPM JSON-LD pattern. If a specific vendor's schema
evolves we update this file; the rest of the codebase is unaffected.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from ..attestation_service import IntegrityCertificate

__all__ = ["SpmConnector", "build_external_spm_aibom"]


_AIBOM_CONTEXT = [
    "https://www.w3.org/ns/credentials/v2",
    "https://schema.ai-spm.example/2026/v1",
    {
        "vos3":    "https://vos-shield.example/schema/",
        "euaiact": "https://artificialintelligenceact.eu/annex/",
    },
]


def build_external_spm_aibom(
    certs:         Iterable[IntegrityCertificate],
    *,
    vendor_tenant_id: str,
    issued_at:        Optional[str] = None,
) -> Dict[str, Any]:
    """Build an external-SPM-ingestible AI-BOM payload from a stream of certs.

    Parameters
    ----------
    certs : iterable of ``IntegrityCertificate``
        Stream is consumed once. Each cert is expanded via its
        ``to_wiz_jsonld()`` view (back-compat name retained from
        v20.4) — we do not re-implement the component shape here, we
        delegate to the canonical view.
    vendor_tenant_id : str
        The tenant id the SPM platform's side uses (NOT necessarily
        the same as VOS-Cyber's internal tenant_id; some operators
        map these).
    issued_at : ISO-8601 UTC; defaults to ``now()``.

    Returns
    -------
    dict
        JSON-serialisable AI-BOM payload. The integrity proof is a
        SHA-384 over the canonical-JSON of the ``components`` array,
        matching the documented AI-SPM integrity-proof algorithm.
    """
    components: List[Dict[str, Any]] = []
    platform_counts: Counter = Counter()
    tier_counts: Counter = Counter()

    for cert in certs:
        component = cert.to_wiz_jsonld()    # back-compat method name
        components.append(component)
        platform = (cert.tee_measurements or {}).get("platform", "UNKNOWN")
        platform_counts[platform] += 1
        tier_counts[cert.signing_tier or "unspecified"] += 1

    components_canonical = json.dumps(
        components, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    integrity_proof = hashlib.sha384(components_canonical).hexdigest()

    issued = issued_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    return {
        "@context":       list(_AIBOM_CONTEXT),
        "@type":          "AiBillOfMaterials",
        "schemaVersion":  "ai-spm-2026.1",
        "tenant":         {"id": vendor_tenant_id},
        "issuedAt":       issued,
        "components":     components,
        "summary": {
            "componentCount":       len(components),
            "platformDistribution": dict(platform_counts),
            "signingTiers":         dict(tier_counts),
        },
        "integrityProof": {
            "alg":   "SHA-384",
            "value": integrity_proof,
        },
    }


@dataclass
class SpmConnector:
    """Stateful wrapper for emitting AI-BOMs against a single tenant.

    The connector keeps no live network connection — it is a pure
    transformation. Callers handle transport (HTTPS POST to the
    AI-SPM ingest URL, signed with the tenant's API key).
    """
    vendor_tenant_id: str

    def aibom(self, certs: Iterable[IntegrityCertificate]) -> Dict[str, Any]:
        return build_external_spm_aibom(
            certs, vendor_tenant_id=self.vendor_tenant_id
        )

    def aibom_json(self, certs: Iterable[IntegrityCertificate]) -> str:
        return json.dumps(self.aibom(certs), indent=2, sort_keys=False)
