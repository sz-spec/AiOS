"""
backend/security/outbound_pii_shield.py
=======================================

Sprint 19 / Wave 2 / Item O3 — Context-Aware Outbound-PII Shield
(fail-closed).

What this is
------------

From the 80-problem agent-era catalog, O3 is the noisy-neighbour /
data-exfiltration sibling of the H1/H3 egress story:

  "An agent assembles a tool result or a model answer that contains a
   user's PII (email, phone, SSN, credit-card, national ID) and then
   sends it *outbound* — to a webhook, a third-party API, a log sink, a
   co-tenant channel — without the data ever having been cleared for
   that destination."

The blob-level IFC engine (C7) and byte-level taint engine (C-1) gate
*labelled* bytes; the combined egress gate (H1) gates *destinations*.
Neither inspects the *content* of an otherwise-unlabelled buffer for
raw PII at the moment it crosses an outbound boundary. O3 closes that
gap with a **content-aware, destination-aware, fail-closed** gate.

The "context-aware" part is the whole point — and what makes this a
genuine NEW closure rather than a relabelled regex:

  - The SAME payload containing a customer email is **allowed** to an
    operator-declared trusted/internal destination, or when an explicit
    ``ReleaseContext`` says a human/policy reviewed and cleared exactly
    those PII kinds for egress.
  - The SAME payload is **refused** (fail-closed) to any other
    destination. A gate that refuses the unsafe send is enforcement; a
    redaction-by-default filter that silently drops data is not — and a
    doc that asks nicely is certainly not.

Enforcement contract
--------------------

    shield = OutboundPiiShield()
    shield.require_clean_egress(payload, destination="https://hooks.example.com/x")
        # raises OutboundPiiBlocked if payload carries PII and the
        # destination/context does not permit it

    # Inspection without raising (for callers that want to redact):
    decision = shield.inspect(payload, destination=..., context=...)
    if decision.allowed:
        send(decision.effective_payload)   # original, or redacted in REDACT mode

Modes
-----

  - BLOCK  (default) — fail-closed: refuse the send, raise on
    require_clean_egress(). The agent's outbound op is denied.
  - REDACT — allow the send but with PII masked in
    ``decision.redacted_payload`` / ``effective_payload``. Use only on
    paths where a redacted body is still useful (e.g. log sinks).
  - AUDIT  — allow + record, never block. For shadow/observe rollouts
    before flipping a path to BLOCK.

Detection reuses the canonical, CI-backing patterns from
``tools/log_pii_scan.py`` (the same scanner that guards the Annex IV
"Zero PII in logs" claim) so the shield and the log gate can never
drift apart.

Honest scope ceilings
---------------------

  - Detection is regex + checksum (Luhn / Israeli-ID) based: it catches
    structured PII (email, E.164 phone, US SSN, credit card, national
    ID). It does NOT catch free-text names/addresses — that needs an
    NER model and is explicitly out of scope (tracked as O3-follow-up).
    The shield is therefore a *precision* gate for structured
    identifiers, not a completeness oracle for all PII.
  - Trusted destinations are operator-declared
    (``VOS3_PII_TRUSTED_DESTINATIONS``), default EMPTY → every
    destination is untrusted → fail-closed. We never infer trust.
  - ``ReleaseContext`` here is an in-process policy assertion. Binding a
    release to a cryptographic, auditor-verifiable signature is the
    C-3 Ed25519 ``DeclassEvidence`` path in ``taint_engine_v2.py``;
    wiring O3 releases through C-3 is a Sprint 20 follow-up.
  - The shield reads + refuses; it never mutates host state. The dev
    escape hatch ``VOS3_PII_SHIELD_DEV_OVERRIDE=1`` downgrades a refusal
    to an allow WITH a loud WARNING — never set it in production.
"""

from __future__ import annotations

import enum
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

ENV_TRUSTED_DESTINATIONS = "VOS3_PII_TRUSTED_DESTINATIONS"
ENV_DEV_OVERRIDE = "VOS3_PII_SHIELD_DEV_OVERRIDE"
ENV_DEFAULT_MODE = "VOS3_PII_SHIELD_MODE"


# --------------------------------------------------------------------------
# Detection — reuse the canonical scanner patterns (single source of truth).
# --------------------------------------------------------------------------


def _load_canonical_patterns():
    """Pull the regexes + checksum helpers from the canonical
    ``tools/log_pii_scan.py``. Falls back to a local copy if the module
    can't be imported (e.g. an unusual sys.path), so the shield is never
    dependent on import-resolution luck."""
    try:
        from tools.log_pii_scan import (  # type: ignore
            EMAIL_RE,
            PHONE_RE,
            SSN_RE,
            CC_RE,
            luhn_check,
            israeli_id_check,
        )

        return EMAIL_RE, PHONE_RE, SSN_RE, CC_RE, luhn_check, israeli_id_check
    except Exception:  # noqa: BLE001 - resilience over purity here
        email_re = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
        phone_re = re.compile(r"\+\d{7,15}\b")
        ssn_re = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
        cc_re = re.compile(r"\b\d{13,19}\b")

        def _luhn(number: str) -> bool:
            digits = [int(d) for d in number if d.isdigit()]
            if len(digits) < 13:
                return False
            checksum = 0
            parity = len(digits) % 2
            for i, d in enumerate(digits):
                if i % 2 == parity:
                    d *= 2
                    if d > 9:
                        d -= 9
                checksum += d
            return checksum % 10 == 0

        def _israeli(number: str) -> bool:
            if len(number) != 9 or not number.isdigit():
                return False
            total = 0
            for i, ch in enumerate(number):
                d = int(ch) * (1 if i % 2 == 0 else 2)
                if d > 9:
                    d -= 9
                total += d
            return total % 10 == 0

        return email_re, phone_re, ssn_re, cc_re, _luhn, _israeli


_EMAIL_RE, _PHONE_RE, _SSN_RE, _CC_RE, _luhn_check, _israeli_id_check = (
    _load_canonical_patterns()
)


class PiiKind(str, enum.Enum):
    EMAIL = "email"
    PHONE = "phone"
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    ISRAELI_ID = "israeli_id"


@dataclass(frozen=True)
class PiiFinding:
    kind: PiiKind
    value: str
    start: int
    end: int

    @property
    def redacted(self) -> str:
        """A non-reversible preview safe to put in logs (keep first 3)."""
        keep = min(3, len(self.value))
        return self.value[:keep] + "*" * (len(self.value) - keep)


# --------------------------------------------------------------------------
# Policy inputs
# --------------------------------------------------------------------------


class EgressMode(str, enum.Enum):
    BLOCK = "block"  # fail-closed (default)
    REDACT = "redact"  # allow with PII masked
    AUDIT = "audit"  # allow + record only


@dataclass(frozen=True)
class ReleaseContext:
    """A context-aware authorization to release PII for THIS send.

    ``cleared_kinds`` lists the PiiKind values a reviewer/policy has
    cleared for egress to ``destination``. ``reviewed`` must be True for
    the release to apply. ``justification`` is recorded for audit. A
    release only covers the kinds it names — PII of an un-cleared kind
    still fail-closes."""

    reviewed: bool = False
    cleared_kinds: frozenset[PiiKind] = field(default_factory=frozenset)
    justification: str = ""
    reviewer: str = ""

    def clears(self, found_kinds: Iterable[PiiKind]) -> bool:
        if not self.reviewed:
            return False
        return set(found_kinds).issubset(set(self.cleared_kinds))


@dataclass(frozen=True)
class ShieldDecision:
    allowed: bool
    mode: EgressMode
    findings: tuple[PiiFinding, ...]
    reason: str
    destination: str
    redacted_payload: Optional[str] = None
    trusted_destination: bool = False
    released_by_context: bool = False
    released_by_bbs: bool = False
    dev_override_used: bool = False

    @property
    def has_pii(self) -> bool:
        return bool(self.findings)

    @property
    def kinds(self) -> frozenset[PiiKind]:
        return frozenset(f.kind for f in self.findings)

    def effective_payload(self, original: str) -> str:
        """What the caller should actually send: the redacted body in
        REDACT mode, otherwise the original (only reachable when
        allowed)."""
        if self.mode is EgressMode.REDACT and self.redacted_payload is not None:
            return self.redacted_payload
        return original


class OutboundPiiBlocked(Exception):
    """Raised by require_clean_egress() when a payload carries PII that
    the destination + context do not permit. Fail-closed."""

    def __init__(self, decision: ShieldDecision):
        self.decision = decision
        super().__init__(decision.reason)


@dataclass
class ShieldStats:
    inspections: int = 0
    clean: int = 0
    allowed_trusted: int = 0
    allowed_released: int = 0
    allowed_bbs_released: int = 0
    allowed_redacted: int = 0
    allowed_audit: int = 0
    blocked: int = 0
    dev_overrides_used: int = 0


@dataclass
class OutboundPiiShield:
    """Fail-closed, context-aware PII egress gate."""

    trusted_destinations: Optional[frozenset[str]] = None
    default_mode: Optional[EgressMode] = None
    allowlist: frozenset[str] = field(default_factory=frozenset)
    stats: ShieldStats = field(default_factory=ShieldStats)

    def __post_init__(self) -> None:
        if self.trusted_destinations is None:
            self.trusted_destinations = self._load_trusted_from_env()
        if self.default_mode is None:
            self.default_mode = self._load_mode_from_env()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inspect(
        self,
        payload,
        destination: str,
        *,
        context: Optional[ReleaseContext] = None,
        mode: Optional[EgressMode] = None,
    ) -> ShieldDecision:
        """Inspect an outbound payload. Never raises — returns a
        ShieldDecision describing whether the send is permitted and
        why."""
        self.stats.inspections += 1
        mode = mode or self.default_mode or EgressMode.BLOCK
        text = self._as_text(payload)
        findings = tuple(self._detect(text))

        if not findings:
            self.stats.clean += 1
            return ShieldDecision(
                allowed=True,
                mode=mode,
                findings=(),
                reason="no PII detected",
                destination=destination,
            )

        trusted = self._is_trusted(destination)
        kinds = frozenset(f.kind for f in findings)
        redacted_preview = ", ".join(
            sorted({f"{f.kind.value}:{f.redacted}" for f in findings})
        )

        # 1. Trusted destination — operator declared this sink internal.
        if trusted:
            self.stats.allowed_trusted += 1
            logger.info(
                "[pii_shield] egress ALLOWED to trusted destination %r "
                "despite %d PII finding(s): %s",
                destination,
                len(findings),
                redacted_preview,
            )
            return ShieldDecision(
                allowed=True,
                mode=mode,
                findings=findings,
                reason=f"trusted destination {destination!r}",
                destination=destination,
                trusted_destination=True,
            )

        # 2. Explicit, kind-scoped release context.
        if context is not None and context.clears(kinds):
            self.stats.allowed_released += 1
            logger.info(
                "[pii_shield] egress ALLOWED to %r by ReleaseContext "
                "(reviewer=%r kinds=%s): %s",
                destination,
                context.reviewer,
                sorted(k.value for k in kinds),
                context.justification,
            )
            return ShieldDecision(
                allowed=True,
                mode=mode,
                findings=findings,
                reason=(
                    f"released by context (reviewer={context.reviewer!r}, "
                    f"kinds={sorted(k.value for k in kinds)})"
                ),
                destination=destination,
                released_by_context=True,
            )

        # 3. Not permitted. Behaviour depends on mode.
        if mode is EgressMode.REDACT:
            self.stats.allowed_redacted += 1
            redacted = self._redact(text, findings)
            logger.warning(
                "[pii_shield] egress to %r REDACTED — %d PII finding(s) " "masked: %s",
                destination,
                len(findings),
                redacted_preview,
            )
            return ShieldDecision(
                allowed=True,
                mode=mode,
                findings=findings,
                reason=f"{len(findings)} PII finding(s) masked (REDACT mode)",
                destination=destination,
                redacted_payload=redacted,
            )

        if mode is EgressMode.AUDIT:
            self.stats.allowed_audit += 1
            logger.warning(
                "[pii_shield] AUDIT-only: egress to %r carries %d PII "
                "finding(s) (NOT blocked): %s",
                destination,
                len(findings),
                redacted_preview,
            )
            return ShieldDecision(
                allowed=True,
                mode=mode,
                findings=findings,
                reason=f"{len(findings)} PII finding(s) (AUDIT mode — not blocked)",
                destination=destination,
            )

        # BLOCK (default) — fail-closed.
        if self._env_true(ENV_DEV_OVERRIDE):
            self.stats.dev_overrides_used += 1
            self.stats.allowed_audit += 1
            logger.warning(
                "[pii_shield] egress to %r allowed by DEV OVERRIDE despite "
                "%d PII finding(s): %s. NEVER set %s in production.",
                destination,
                len(findings),
                redacted_preview,
                ENV_DEV_OVERRIDE,
            )
            return ShieldDecision(
                allowed=True,
                mode=mode,
                findings=findings,
                reason="allowed by DEV OVERRIDE (PII present)",
                destination=destination,
                dev_override_used=True,
            )

        self.stats.blocked += 1
        logger.error(
            "[pii_shield] egress to %r REFUSED (fail-closed) — %d PII "
            "finding(s): %s",
            destination,
            len(findings),
            redacted_preview,
        )
        return ShieldDecision(
            allowed=False,
            mode=mode,
            findings=findings,
            reason=(
                f"{len(findings)} unreleased PII finding(s) "
                f"({sorted(k.value for k in kinds)}) bound for untrusted "
                f"destination {destination!r}"
            ),
            destination=destination,
        )

    def require_clean_egress(
        self,
        payload,
        destination: str,
        *,
        context: Optional[ReleaseContext] = None,
        mode: Optional[EgressMode] = None,
    ) -> ShieldDecision:
        """Inspect + enforce. Returns the ShieldDecision when the send is
        permitted; raises OutboundPiiBlocked when it is refused."""
        decision = self.inspect(payload, destination, context=context, mode=mode)
        if not decision.allowed:
            raise OutboundPiiBlocked(decision)
        return decision

    def require_bbs_release(
        self,
        payload,
        destination: str,
        *,
        derived_proof,
        issuer_public_bytes: bytes,
        destination_trust_domain: str,
        verifier=None,
    ) -> ShieldDecision:
        """Cryptographic upgrade of ``require_clean_egress`` (Sprint 20,
        Primitive (a) / C-3 BBS+ ceiling).

        Where ``ReleaseContext`` is a Python boolean a bug could forge,
        this gate permits an outbound PII send iff the payload's detected
        PII kinds are a subset of the kinds proven by a **valid,
        unexpired BBS derived proof** issued by ``issuer_public_bytes``
        and bound to ``destination_trust_domain``. On a
        missing/invalid/expired/over-broad/wrong-domain proof it
        fail-closes by raising ``OutboundPiiBlocked`` — the same
        exception as the boolean path, so callers need no new handling.

        ``derived_proof`` is a
        ``bbs_selective_disclosure.DerivedProof``; ``verifier`` defaults
        to a fresh ``BbsVerifier`` (pass one with a pinned clock in
        tests). A clean payload is allowed without requiring a proof.
        """
        from services.bbs_selective_disclosure import (  # lazy: keep crypto optional
            BbsVerifier,
            revealed_pii_kinds,
            revealed_trust_domain,
        )

        self.stats.inspections += 1
        text = self._as_text(payload)
        findings = tuple(self._detect(text))

        if not findings:
            self.stats.clean += 1
            return ShieldDecision(
                allowed=True,
                mode=EgressMode.BLOCK,
                findings=(),
                reason="no PII detected",
                destination=destination,
            )

        kinds = frozenset(f.kind for f in findings)
        redacted_preview = ", ".join(
            sorted({f"{f.kind.value}:{f.redacted}" for f in findings})
        )

        def _block(reason: str) -> ShieldDecision:
            self.stats.blocked += 1
            logger.error(
                "[pii_shield] BBS-gated egress to %r REFUSED (fail-closed) "
                "— %s; %d PII finding(s): %s",
                destination,
                reason,
                len(findings),
                redacted_preview,
            )
            decision = ShieldDecision(
                allowed=False,
                mode=EgressMode.BLOCK,
                findings=findings,
                reason=reason,
                destination=destination,
            )
            raise OutboundPiiBlocked(decision)

        if derived_proof is None:
            return _block("no BBS derived proof presented for PII egress")

        verifier = verifier or BbsVerifier()
        result = verifier.verify(
            derived_proof,
            issuer_public_bytes,
            expected_verifier_id=destination_trust_domain,
        )
        if not result.ok:
            return _block(f"BBS proof failed verification: {result.reason}")

        proven_td = revealed_trust_domain(result.revealed_statements)
        if proven_td != destination_trust_domain:
            return _block(
                f"BBS proof trust-domain {proven_td!r} != destination "
                f"trust-domain {destination_trust_domain!r}"
            )

        proven_kinds = revealed_pii_kinds(result.revealed_statements)
        payload_kinds = {k.value for k in kinds}
        if not payload_kinds.issubset(proven_kinds):
            missing = sorted(payload_kinds - proven_kinds)
            return _block(
                f"BBS proof does not clear PII kinds {missing} for "
                f"destination {destination!r}"
            )

        self.stats.allowed_bbs_released += 1
        logger.info(
            "[pii_shield] egress to %r ALLOWED by BBS derived proof "
            "(verifier=%s, cleared kinds=%s, pseudonym=%s…)",
            destination,
            destination_trust_domain,
            sorted(proven_kinds),
            result.pseudonym[:12],
        )
        return ShieldDecision(
            allowed=True,
            mode=EgressMode.BLOCK,
            findings=findings,
            reason=(
                f"released by BBS derived proof "
                f"(kinds={sorted(payload_kinds)}, td={destination_trust_domain})"
            ),
            destination=destination,
            released_by_bbs=True,
        )

    # ------------------------------------------------------------------
    # Detection / redaction
    # ------------------------------------------------------------------

    def _detect(self, text: str) -> list[PiiFinding]:
        allow = self.allowlist
        findings: list[PiiFinding] = []

        for m in _EMAIL_RE.finditer(text):
            s = m.group(0)
            if s in allow or s.endswith("@example.com"):
                continue
            findings.append(PiiFinding(PiiKind.EMAIL, s, m.start(), m.end()))

        for m in _PHONE_RE.finditer(text):
            s = m.group(0)
            if s in allow:
                continue
            findings.append(PiiFinding(PiiKind.PHONE, s, m.start(), m.end()))

        for m in _SSN_RE.finditer(text):
            s = m.group(0)
            if s in allow:
                continue
            findings.append(PiiFinding(PiiKind.SSN, s, m.start(), m.end()))

        for m in _CC_RE.finditer(text):
            s = m.group(0)
            if s in allow:
                continue
            if _luhn_check(s):
                findings.append(PiiFinding(PiiKind.CREDIT_CARD, s, m.start(), m.end()))
            elif len(s) == 9 and _israeli_id_check(s):
                findings.append(PiiFinding(PiiKind.ISRAELI_ID, s, m.start(), m.end()))

        return findings

    @staticmethod
    def _redact(text: str, findings: Iterable[PiiFinding]) -> str:
        # Replace from the end so earlier offsets stay valid.
        out = text
        for f in sorted(findings, key=lambda x: x.start, reverse=True):
            out = out[: f.start] + f"[REDACTED:{f.kind.value}]" + out[f.end :]
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _as_text(payload) -> str:
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace")
        if isinstance(payload, str):
            return payload
        return str(payload)

    def _is_trusted(self, destination: str) -> bool:
        if not destination:
            return False
        dest = destination.strip().lower()
        host = self._host_of(dest)
        for t in self.trusted_destinations or frozenset():
            t = t.strip().lower()
            if not t:
                continue
            # match full destination, host, or suffix (e.g. ".internal")
            if dest == t or host == t:
                return True
            if t.startswith(".") and host.endswith(t):
                return True
            if host == t.lstrip("*."):
                return True
        return False

    @staticmethod
    def _host_of(destination: str) -> str:
        d = destination
        if "://" in d:
            d = d.split("://", 1)[1]
        d = d.split("/", 1)[0]
        d = d.split("@")[-1]  # strip userinfo
        d = d.split(":", 1)[0]  # strip port
        return d

    @staticmethod
    def _load_trusted_from_env() -> frozenset[str]:
        raw = os.environ.get(ENV_TRUSTED_DESTINATIONS, "")
        return frozenset(tok.strip() for tok in raw.split(",") if tok.strip())

    @staticmethod
    def _load_mode_from_env() -> EgressMode:
        raw = os.environ.get(ENV_DEFAULT_MODE, "").strip().lower()
        for m in EgressMode:
            if raw == m.value:
                return m
        return EgressMode.BLOCK

    @staticmethod
    def _env_true(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


__all__ = [
    "EgressMode",
    "OutboundPiiBlocked",
    "OutboundPiiShield",
    "PiiFinding",
    "PiiKind",
    "ReleaseContext",
    "ShieldDecision",
    "ShieldStats",
    "ENV_TRUSTED_DESTINATIONS",
    "ENV_DEV_OVERRIDE",
    "ENV_DEFAULT_MODE",
]
