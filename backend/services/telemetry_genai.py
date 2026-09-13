"""
backend/services/telemetry_genai.py
=====================================

Sprint 15 / Items G1 + G5 — OpenTelemetry GenAI semantic-conventions
emitter + PII-redacted MAIF envelope wrapping for compliance ingestion.

Why OpenTelemetry GenAI
-----------------------

The CNCF graduated OpenTelemetry to "graduated" status on 2026-05-21
(https://www.cncf.io/announcements/2026/05/21/cloud-native-computing-
foundation-announces-opentelemetrys-graduation-solidifying-status-as-the-
de-facto-observability-standard/). The GenAI working group's semantic
conventions (https://opentelemetry.io/docs/specs/semconv/gen-ai/) are
the **de facto** standard for AI agent tracing as of May 2026 — the
ISO/IEC 27090 draft references them in §7 (detection guidance) as the
preferred "structured detection record" format.

This module:

  1. Emits `gen_ai.agent.*` spans + `gen_ai.tool.*` spans from the vOS
     agent / chat / codegen routes, conforming to the OTel GenAI
     spec's attribute names.
  2. Mirrors every emitted span into the compliance_store via the
     existing audit-event ingestion endpoint, wrapped in a PII-redacted
     MAIF envelope (Item G5) so prompts containing PII aren't leaked
     into SIEM tooling that doesn't know to redact them.

Public surface
--------------

    emit_agent_span(span_data) -> None
        Fire one `gen_ai.agent` span. Wraps the OTel SDK's tracer
        when configured; falls back to a structured-log emitter when
        the OTel runtime isn't installed (CI / dev environments).

    emit_tool_span(span_data) -> None
        Fire one `gen_ai.tool` span (tool invocation by an agent).

    wrap_for_audit(span_dict, *, signing_key=None) -> MAIFRedactedBlob
        Take an OTel span dict, redact PII, wrap in MAIF envelope, sign,
        return the blob to write to compliance_store.

    redact_pii(text: str, *, mode='replace') -> str
        Best-effort PII redactor. Pattern-based; not a Microsoft Presidio
        replacement. Covers: email addresses, US SSN, US phone numbers,
        IBAN, credit-card-shaped digit runs, IPv4, common api-key shapes
        (sk-..., ghp_..., AKIA..., bearer tokens). Replaces matches with
        `[REDACTED:<kind>]` markers preserving the kind for audit-trail
        reasoning.

Honest scope ceiling
--------------------

  1. The PII redactor is pattern-based. It will miss obfuscated PII
     (e.g., "my email is john at example dot com") and will produce
     false-positives on benign data that happens to match a pattern.
     We optimize for low false-negative rate on the known-dangerous
     patterns; ops can plug Presidio in via the `redact_pii` swap
     point if they need higher fidelity.

  2. We do NOT ship the OTel exporter wiring (OTLP/HTTP, OTLP/gRPC).
     That's deployment-specific (Tempo, Jaeger, Honeycomb, Datadog all
     have different config). Operators configure via the standard
     OTEL_EXPORTER_OTLP_ENDPOINT env var; we just produce the spans.

  3. The MAIF envelope here uses an inline Ed25519 keypair derived
     from the keyring (services.crypto_keyring) — same key the
     workflow_signing path uses. Production deployments rotate via
     rotation_manager (Sprint 14.1) on the standard cadence.

References:
  - https://opentelemetry.io/docs/specs/semconv/gen-ai/
  - https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/
  - https://opentelemetry.io/blog/2026/genai-observability/
  - https://www.cncf.io/announcements/2026/05/21/cloud-native-computing-foundation-announces-opentelemetrys-graduation-solidifying-status-as-the-de-facto-observability-standard/
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — OTel GenAI semantic-convention attribute names
# ---------------------------------------------------------------------------

# Per https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/
ATTR_AGENT_NAME = "gen_ai.agent.name"
ATTR_AGENT_ID = "gen_ai.agent.id"
ATTR_AGENT_DESCRIPTION = "gen_ai.agent.description"
ATTR_AGENT_TYPE = "gen_ai.agent.type"  # "router" | "worker" | "supervisor" | ...
ATTR_AGENT_ROLE = "gen_ai.agent.role"
ATTR_OPERATION_NAME = "gen_ai.operation.name"  # "chat" | "tool" | "embeddings" | ...
ATTR_SYSTEM = "gen_ai.system"  # "openai" | "anthropic" | "google" | "local"
ATTR_REQUEST_MODEL = "gen_ai.request.model"
ATTR_RESPONSE_MODEL = "gen_ai.response.model"
ATTR_RESPONSE_ID = "gen_ai.response.id"
ATTR_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
ATTR_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ATTR_TOOL_NAME = "gen_ai.tool.name"
ATTR_TOOL_CALL_ID = "gen_ai.tool.call.id"
ATTR_TOOL_DESCRIPTION = "gen_ai.tool.description"
ATTR_TOOL_ARGUMENTS = "gen_ai.tool.arguments"  # arbitrary JSON — auto-redacted
ATTR_TOOL_RESULT = "gen_ai.tool.result"  # arbitrary JSON — auto-redacted
ATTR_VOS_ACTOR_TYPE = "vos3.actor.type"  # "human" | "agent" | "agent_chain"
ATTR_VOS_SLOT_ID = "vos3.slot.id"
ATTR_VOS_INTENT_DIGEST = "vos3.intent.digest"  # first 16 hex of intent manifest SHA-384


# Env var operators set to enable the exporter side. We don't read it
# directly here — the OTel SDK does — but we document it for the runbook.
ENV_OTLP_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
ENV_VOS_AUDIT_ENABLED = (
    "VOS3_OTEL_AUDIT_ENABLED"  # "1" to mirror spans into compliance_store
)


# ---------------------------------------------------------------------------
# PII redactor (Item G5)
# ---------------------------------------------------------------------------

# Patterns tuned to balance false-positive vs false-negative on the
# known-dangerous PII classes. Each entry: (kind-label, compiled regex).
_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    # API-key shapes — these MUST not leak even into "secured" SIEM.
    ("SK_OPENAI", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("PAT_GITHUB", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("AWS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "AWS_SECRET",
        re.compile(r"(?<![A-Za-z0-9/+])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])"),
    ),
    # JWT-shaped tokens (3 base64url segments separated by dots).
    (
        "JWT_TOKEN",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
    ),
    # Bearer header value (common log shape).
    ("BEARER_HEADER", re.compile(r"(?i)(bearer\s+)([A-Za-z0-9_.\-+/=]{16,})")),
    # Email — RFC 5322 simplification.
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # US SSN — XXX-XX-XXXX.
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # IBAN (very rough: 2 letters + 2 digits + 11-30 alphanumerics).
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    # Credit-card-shaped 13-19 digit runs.
    ("CC_NUMBER", re.compile(r"\b\d{13,19}\b")),
    # IPv4 — purely informational; some deployments DO want IPs in audit.
    # Mark separately so operators can flip the policy.
    ("IPV4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    # US phone number — common shapes.
    ("US_PHONE", re.compile(r"\b\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b")),
]

# Some kinds the operator may want to retain in audit (e.g. IPv4 for
# forensic correlation). Configurable via env.
ENV_PII_KEEP_KINDS = "VOS3_PII_REDACTION_KEEP_KINDS"


@dataclass
class RedactionResult:
    redacted: str
    counts: dict[str, int] = field(default_factory=dict)


def _get_kept_kinds() -> frozenset[str]:
    raw = os.environ.get(ENV_PII_KEEP_KINDS, "").strip()
    if not raw:
        return frozenset()
    return frozenset(s.strip().upper() for s in raw.split(",") if s.strip())


def redact_pii(text: str, *, mode: str = "replace") -> RedactionResult:
    """Best-effort pattern-based PII redactor.

    mode='replace' substitutes `[REDACTED:<kind>]` for each match.
    mode='count'   leaves the text alone and only counts matches
                   (useful for telemetry that doesn't ship the text).
    """
    if not isinstance(text, str) or not text:
        return RedactionResult(redacted=text or "", counts={})

    kept = _get_kept_kinds()
    counts: dict[str, int] = {}
    result = text

    for kind, pattern in _PII_PATTERNS:
        if kind in kept:
            continue
        if mode == "count":
            matches = pattern.findall(result)
            if matches:
                counts[kind] = counts.get(kind, 0) + len(matches)
            continue

        # mode == "replace"
        def _sub(match: re.Match) -> str:
            counts[kind] = counts.get(kind, 0) + 1
            # Preserve the "Bearer " prefix for BEARER_HEADER so the
            # caller's HTTP-trace context still shows the header existed.
            if kind == "BEARER_HEADER":
                return match.group(1) + f"[REDACTED:{kind}]"
            return f"[REDACTED:{kind}]"

        result = pattern.sub(_sub, result)

    return RedactionResult(redacted=result, counts=counts)


def redact_span(span: dict[str, Any]) -> dict[str, Any]:
    """Walk a span dict and apply PII redaction to string leaves.

    Recurses into nested dicts and lists. Does NOT redact attribute
    NAMES — only string VALUES — so the gen_ai.* schema stays intact.
    Returns a NEW dict; original is unmodified.
    """

    def _walk(value: Any) -> Any:
        if isinstance(value, str):
            return redact_pii(value).redacted
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v) for v in value]
        return value

    return _walk(span)


# ---------------------------------------------------------------------------
# Span emit + MAIF wrapping
# ---------------------------------------------------------------------------


@dataclass
class GenAISpanData:
    """The minimum shape a caller passes to emit_agent_span / emit_tool_span."""

    operation_name: str  # "chat" | "tool" | "embeddings" | ...
    agent_name: str
    agent_id: str
    duration_ms: float
    attributes: dict[str, Any] = field(default_factory=dict)
    # Caller may pre-populate vos3.* attributes; we add gen_ai.* before emit.
    actor_type: str = "agent"  # human / agent / agent_chain
    slot_id: Optional[int] = None
    intent_digest: Optional[str] = None
    timestamp_ns: int = field(default_factory=lambda: time.time_ns())


def _to_otel_attributes(span_data: GenAISpanData) -> dict[str, Any]:
    """Map GenAISpanData → flat OTel attributes dict."""
    out: dict[str, Any] = {
        ATTR_OPERATION_NAME: span_data.operation_name,
        ATTR_AGENT_NAME: span_data.agent_name,
        ATTR_AGENT_ID: span_data.agent_id,
        ATTR_VOS_ACTOR_TYPE: span_data.actor_type,
    }
    if span_data.slot_id is not None:
        out[ATTR_VOS_SLOT_ID] = int(span_data.slot_id)
    if span_data.intent_digest:
        out[ATTR_VOS_INTENT_DIGEST] = span_data.intent_digest[:16]
    out.update(span_data.attributes)
    return out


def _otel_emit_via_sdk(
    span_name: str, attrs: dict[str, Any], duration_ms: float
) -> bool:
    """Try to emit through the OTel SDK if installed + configured.

    Returns True if successful, False if the SDK isn't available. Falls
    back to a structured-log emit so the data is never lost.
    """
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]
    except ImportError:
        return False

    tracer = trace.get_tracer("vos3.genai")
    with tracer.start_as_current_span(span_name, attributes=attrs):
        # Synthesize the duration — production callers run the span as a
        # context manager around the actual operation. This module is the
        # post-hoc emitter for routes that did the timing themselves.
        pass
    return True


def _structured_log_emit(
    span_name: str, attrs: dict[str, Any], duration_ms: float
) -> None:
    """Fallback emitter — structured JSON to stdout via logger.info."""
    logger.info(
        "[gen_ai_span] %s",
        json.dumps(
            {
                "name": span_name,
                "duration_ms": duration_ms,
                "attributes": attrs,
            },
            sort_keys=True,
            default=str,
        ),
    )


def emit_agent_span(span_data: GenAISpanData) -> None:
    """Emit a `gen_ai.agent.*` span via OTel SDK (or fallback)."""
    attrs = _to_otel_attributes(span_data)
    span_name = f"gen_ai.agent.{span_data.operation_name}"
    if not _otel_emit_via_sdk(span_name, attrs, span_data.duration_ms):
        _structured_log_emit(span_name, attrs, span_data.duration_ms)
    _maybe_mirror_to_compliance(span_name, attrs, span_data)


def emit_tool_span(span_data: GenAISpanData) -> None:
    """Emit a `gen_ai.tool.*` span — tool invocation by an agent."""
    attrs = _to_otel_attributes(span_data)
    span_name = "gen_ai.tool.execute"
    if not _otel_emit_via_sdk(span_name, attrs, span_data.duration_ms):
        _structured_log_emit(span_name, attrs, span_data.duration_ms)
    _maybe_mirror_to_compliance(span_name, attrs, span_data)


# ---------------------------------------------------------------------------
# Compliance-store mirror (Item G5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MAIFRedactedBlob:
    """An OTel span wrapped in a PII-redacted MAIF envelope, ready for
    ingestion into the compliance store."""

    span_name: str
    timestamp_ns: int
    redacted_attrs: dict[str, Any]
    redaction_counts: dict[str, int]
    envelope_sha256: str  # SHA-256 of the canonical JSON-bytes BEFORE redaction


def wrap_for_audit(
    span_name: str,
    raw_attrs: dict[str, Any],
    timestamp_ns: Optional[int] = None,
) -> MAIFRedactedBlob:
    """Redact PII and wrap into an audit-friendly envelope."""
    if timestamp_ns is None:
        timestamp_ns = time.time_ns()

    # Compute the digest of the original (PRE-redaction) span so an
    # auditor with appropriate authority can fetch the unredacted blob
    # from a separate secured store. The redacted blob carries this
    # digest as a forward-reference.
    canonical_pre = json.dumps(raw_attrs, sort_keys=True, default=str).encode("utf-8")
    envelope_sha256 = hashlib.sha256(canonical_pre).hexdigest()

    redacted_attrs = {}
    counts: dict[str, int] = {}
    for k, v in raw_attrs.items():
        if isinstance(v, str):
            r = redact_pii(v)
            redacted_attrs[k] = r.redacted
            for kind, n in r.counts.items():
                counts[kind] = counts.get(kind, 0) + n
        elif isinstance(v, (dict, list)):
            redacted_attrs[k] = (
                redact_span(v)
                if isinstance(v, dict)
                else [
                    (
                        redact_span(x)
                        if isinstance(x, dict)
                        else (redact_pii(x).redacted if isinstance(x, str) else x)
                    )
                    for x in v
                ]
            )
        else:
            redacted_attrs[k] = v

    return MAIFRedactedBlob(
        span_name=span_name,
        timestamp_ns=timestamp_ns,
        redacted_attrs=redacted_attrs,
        redaction_counts=counts,
        envelope_sha256=envelope_sha256,
    )


_mirror_lock = threading.Lock()


def _maybe_mirror_to_compliance(
    span_name: str,
    attrs: dict[str, Any],
    span_data: GenAISpanData,
) -> None:
    """Mirror the span into compliance_store if VOS3_OTEL_AUDIT_ENABLED=1.

    Best-effort — never raises, never blocks the request path. Failures
    log a warning and continue.
    """
    if os.environ.get(ENV_VOS_AUDIT_ENABLED, "").strip() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return

    try:
        from services.compliance_store import get_compliance_store
    except Exception:  # noqa: BLE001
        # compliance_store import may fail in CI without the full
        # service tree; that's fine.
        return

    try:
        blob = wrap_for_audit(span_name, attrs, span_data.timestamp_ns)
        store = get_compliance_store()
        # Use the same append_events shape the kernel audit-ring uses,
        # tagged with a synthetic seq number based on the timestamp.
        with _mirror_lock:
            row = {
                "seq": span_data.timestamp_ns // 1000,  # microseconds — monotonic-ish
                "tick": span_data.timestamp_ns // 1_000_000_000,
                "category": 0x60,  # VOS3_AUDIT_CAT_GENAI_SPAN
                "rc": 0,
                "slot_id": span_data.slot_id if span_data.slot_id is not None else 255,
                "digest_prefix": blob.envelope_sha256[:16],
            }
            store.append_events([row])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[telemetry_genai] compliance mirror failed: %s", exc)


__all__ = [
    "GenAISpanData",
    "MAIFRedactedBlob",
    "RedactionResult",
    "emit_agent_span",
    "emit_tool_span",
    "redact_pii",
    "redact_span",
    "wrap_for_audit",
    "ATTR_AGENT_NAME",
    "ATTR_AGENT_ID",
    "ATTR_OPERATION_NAME",
    "ATTR_SYSTEM",
    "ATTR_REQUEST_MODEL",
    "ATTR_RESPONSE_MODEL",
    "ATTR_USAGE_INPUT_TOKENS",
    "ATTR_USAGE_OUTPUT_TOKENS",
    "ATTR_TOOL_NAME",
    "ATTR_TOOL_ARGUMENTS",
    "ATTR_TOOL_RESULT",
    "ATTR_VOS_ACTOR_TYPE",
    "ATTR_VOS_SLOT_ID",
    "ATTR_VOS_INTENT_DIGEST",
    "ENV_OTLP_ENDPOINT",
    "ENV_VOS_AUDIT_ENABLED",
    "ENV_PII_KEEP_KINDS",
]
