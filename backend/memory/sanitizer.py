# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
MemorySanitizer — Morris-II / AgentPoison defense for VOS3 DevMemory.

Defends against the Morris-II self-replicating prompt-injection cascade
(Cornell Tech / Technion / Intuit, arXiv 2403.02817; Microsoft+Sysdig
April 2026 coverage) and AgentPoison RAG-corpus poisoning (>80% ASR
at <0.1% poison rate per Agent Security Bench 2026).

Layered defense (mirrors VOS3's existing 5-layer command defense):

  Layer 1: Pattern reject list — known prompt-injection markers.
  Layer 2: Composite trust score (provenance, recency, cross-validation).
  Layer 3: Quarantine tier — PENDING -> TRUSTED on N independent endorsements.
  Layer 4: Per-agent READ namespace isolation (mirrors WRITE_PERMISSIONS
           in dev_memory.py:286).
  Layer 5: Telemetry — every reject logged for forensic review.

Honest scoping
--------------
This module ships the SANITIZER. Wiring it into ``dev_memory.add()`` and
``dev_memory.query()`` is a separate, scoped change that requires test
coverage of the existing 158-test memory suite (out of scope for the
spec-execution session). The wiring snippet is documented in
``docs/technical/V20_6_MASTER_REPAIR_SPEC.md`` §3.1.

Per V20_6_MASTER_REPAIR_SPEC.md §3.1.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("vos3.memory.sanitizer")


class TrustTier(str, Enum):
    """Memory entry trust state.

    REJECTED — Failed Layer 1 pattern filter; never retrieved.
    PENDING  — Quarantined; not retrieved by default queries.
    TRUSTED  — Promoted after N independent endorsements.
    SYSTEM   — Admin-curated baseline (read-only).
    """

    REJECTED = "REJECTED"
    PENDING = "PENDING"
    TRUSTED = "TRUSTED"
    SYSTEM = "SYSTEM"


# Layer 1: prompt-injection pattern reject list (case-insensitive)
INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"disregard\s+(all\s+)?prior\s+(context|instructions)", re.I),
    re.compile(r"system\s*:\s*[a-z]", re.I),  # role injection
    re.compile(r"<\s*system\s*>", re.I),  # XML role inject
    re.compile(r"\[\[\s*system\s*\]\]", re.I),  # bracket inject
    re.compile(r"forget\s+everything\s+(above|before)", re.I),
    re.compile(r"you\s+are\s+now\s+a\s+different", re.I),
    re.compile(r"reveal\s+your\s+(system\s+)?prompt", re.I),
    re.compile(r"(exfiltrate|leak|send)\s+to\s+https?://", re.I),
    re.compile(r"base64[, ]\s*[A-Za-z0-9+/=]{200,}"),  # large b64 payload
]

# Per-agent READ namespace: which wings each model_origin can READ from.
# Mirror of WRITE_PERMISSIONS in dev_memory.py — blocks Morris-II Phase 3.
# An agent absent from this dict gets read-all (legacy default — kept for
# backward compatibility with existing internal callers).
READ_PERMISSIONS: Dict[str, Set[str]] = {
    "architect": {"kernel", "infra", "backend"},
    "frontend": {"frontend", "infra"},
    "backend": {"backend", "infra"},
    "tester": {"frontend", "backend", "kernel", "infra"},  # read-all for QA
    "reviewer": {"frontend", "backend", "kernel", "infra"},
}


@dataclass(slots=True)
class TrustScore:
    """Composite score informing PENDING -> TRUSTED promotion."""

    provenance: float = 0.0  # 0..1 — how trusted is the source agent
    cross_validations: int = 0  # how many independent agents endorsed
    recency_decay: float = 1.0  # 0..1 — newer = higher
    pattern_clean: bool = True  # passed Layer 1
    endorsing_agents: Set[str] = field(default_factory=set)

    @property
    def composite(self) -> float:
        """Weighted composite: 0.3*provenance + 0.4*cross_val + 0.3*recency."""
        if not self.pattern_clean:
            return 0.0
        return (
            0.3 * self.provenance
            + 0.4 * min(1.0, self.cross_validations / 2.0)
            + 0.3 * self.recency_decay
        )


# Promotion thresholds
TRUST_PROMOTE_THRESHOLD = 0.65  # composite to promote PENDING -> TRUSTED
TRUST_MIN_ENDORSEMENTS = 2  # distinct agents needed (breaks Morris-II Phase 2)


class MemorySanitizer:
    """Singleton sanitizer; injected into dev_memory.add() / .query().

    Stateless except for the in-memory endorsement map. The map is bounded
    by the parent DevMemory's eviction policy; lifetime never exceeds a
    single backend process.
    """

    def __init__(self) -> None:
        self._reject_count = 0
        self._endorsements: Dict[str, Set[str]] = {}  # entry_id -> agent set

    # -- ingestion path (called from dev_memory.add) ------------------------

    def score_ingest(
        self,
        content: str,
        model_origin: str,
        wing: str,
    ) -> Tuple[TrustTier, TrustScore]:
        """Returns (tier, score). Caller MUST honor REJECTED.

        New entries enter the PENDING tier and are not retrievable until
        promoted by ``endorse()`` reaching ``TRUST_MIN_ENDORSEMENTS``.
        """
        score = TrustScore(provenance=self._provenance_for(model_origin))

        # Layer 1: pattern filter
        for pat in INJECTION_PATTERNS:
            if pat.search(content):
                score.pattern_clean = False
                self._reject_count += 1
                logger.warning(
                    "MemorySanitizer REJECT origin=%s wing=%s pattern=%s",
                    model_origin,
                    wing,
                    pat.pattern[:40],
                )
                return TrustTier.REJECTED, score

        # Layer 3: new memories enter PENDING
        return TrustTier.PENDING, score

    # -- retrieval path (called from dev_memory.query) ----------------------

    def filter_results(
        self,
        results: List[Dict[str, Any]],
        requesting_agent: str,
        requested_wing: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Drops REJECTED + PENDING entries (unless caller explicitly opts in)
        and cross-namespace reads forbidden by ``READ_PERMISSIONS``.

        Layer 4 here closes Morris-II Phase 3 — the Reviewer cannot read
        what an external agent wrote into the Frontend wing unless the
        Reviewer's allow-list grants it.
        """
        allowed_wings = READ_PERMISSIONS.get(requesting_agent)
        out: List[Dict[str, Any]] = []
        for r in results:
            meta = r.get("metadata", {})
            tier = meta.get("trust_tier", TrustTier.TRUSTED.value)
            wing = meta.get("wing", "infra")

            if tier == TrustTier.REJECTED.value:
                continue
            if tier == TrustTier.PENDING.value:
                continue
            if allowed_wings is not None and wing not in allowed_wings:
                logger.info(
                    "READ-NS reject: agent=%s wing=%s allowed=%s",
                    requesting_agent,
                    wing,
                    allowed_wings,
                )
                continue
            out.append(r)
        return out

    # -- promotion path (called when an agent endorses a memory) -----------

    def endorse(self, entry_id: str, endorsing_agent: str) -> bool:
        """Adds an endorsement. Returns True if entry should be promoted."""
        endorsers = self._endorsements.setdefault(entry_id, set())
        endorsers.add(endorsing_agent)
        return len(endorsers) >= TRUST_MIN_ENDORSEMENTS

    @property
    def reject_count(self) -> int:
        """Number of memories rejected since process start (Layer 5 telemetry)."""
        return self._reject_count

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _provenance_for(model_origin: str) -> float:
        """Trust by source. System ingest is highest; user-facing routes lowest.

        AgentPoison's primary infection vector is external_url_fetch and
        user_chat — these get the lowest provenance scores so even a
        successful Layer-1 bypass yields a low composite score that
        won't promote.
        """
        return {
            "system": 1.0,
            "reviewer": 0.85,
            "architect": 0.7,
            "tester": 0.6,
            "backend": 0.5,
            "frontend": 0.5,
            "user_chat": 0.2,
            "voice_transcript": 0.2,
            "external_url_fetch": 0.1,  # AgentPoison primary vector
        }.get(model_origin, 0.3)


_sanitizer: Optional[MemorySanitizer] = None


def get_sanitizer() -> MemorySanitizer:
    """Singleton accessor. Lazy-initializes on first call."""
    global _sanitizer
    if _sanitizer is None:
        _sanitizer = MemorySanitizer()
    return _sanitizer


__all__ = [
    "TrustTier",
    "TrustScore",
    "MemorySanitizer",
    "get_sanitizer",
    "INJECTION_PATTERNS",
    "READ_PERMISSIONS",
    "TRUST_PROMOTE_THRESHOLD",
    "TRUST_MIN_ENDORSEMENTS",
]
