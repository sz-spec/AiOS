"""
backend/security/ifc_engine.py
================================

Sprint 16 / Item C7 — OS-level information-flow control (taint engine).

What this is
------------

From the 80-problem agent-era catalog, C7:
  "No OS-level taint-tracking for 'data tainted by untrusted input' —
   information flow control (IFC) at OS level was researched in 1990s
   (HiStar, Asbestos) but never mainlined; agent era resurrects the need."

This engine implements the Fides / GAAP / SAMOS labeling model (May 2026
arxiv 2505.23643 + 2604.19657): every byte that originates from an
untrusted source (tool result, web fetch, file read, MCP-routed call)
carries a Taint label. Labels propagate through agent reasoning chains.
When tainted bytes reach an egress sink (HTTP POST, file write, stdout
to user), the engine enforces a configurable policy.

Public surface
--------------

  TaintEngine.label_source(source_id, content) -> TaintedBlob
  TaintEngine.propagate(input_blobs, output_content) -> TaintedBlob
  TaintEngine.check_egress(blob, sink) -> EgressDecision
  TaintEngine.policy_set(sink, max_label)
  TaintEngine.snapshot_stats() -> TaintStats

Label model
-----------

Taint levels (monotonic; higher = more sensitive):
  PUBLIC      — no taint (trusted inputs only)
  UNTRUSTED   — anything from any tool output
  SECRET      — explicitly marked by the privileged LLM as sensitive
  TOXIC       — flagged as containing an active injection attempt

Propagation rule: the OUTPUT label is the MAXIMUM of all INPUT labels
that the output derives from. This is the standard high-watermark IFC
discipline (Bell-LaPadula style) — it's conservative (sometimes labels
clean derivations as tainted) but provably sound against data
exfiltration via partial-leak attacks.

Egress policy
-------------

Per-sink maximum allowed label. Defaults:
  NETWORK_EGRESS  → UNTRUSTED   (deny SECRET + TOXIC)
  FILE_WRITE      → SECRET      (deny TOXIC)
  USER_STDOUT     → SECRET      (deny TOXIC)
  AUDIT_LOG       → TOXIC       (allow all — audit MUST see everything)

Operator can override per-sink via env or policy_set().

Honest scope ceiling
--------------------

  - High-watermark IFC has well-known false-positive issues: a single
    UNTRUSTED input contaminates the entire output even if that input
    wasn't actually used. Endorsement/declassification can relax this
    (per Asbestos), but Sprint 16 / Wave 2 ships the conservative
    discipline and leaves endorsement to a Wave 3 follow-up — the
    privileged LLM can explicitly mark a derivation as PUBLIC via the
    .declassify() helper, with audit-trail recording.

  - This module is the LOGICAL enforcement layer. The kernel-side
    enforcement (where the egress syscall is intercepted and the
    label is read from a per-fd metadata table) is the B3+B4 eBPF
    LSM hook work in Wave 2 — out of scope for C7 itself but cleanly
    integrable via the kernel-side capability_table.c (A3) lookup.

  - The propagation model assumes the LLM-output path is the only way
    bytes flow agent-to-agent. Direct shared-memory channels (planned
    in H3) need their own label-carrying wrapper; not C7's job.

References:
  - Fides: Securing AI Agents with Information-Flow Control (arxiv 2505.23643)
  - GAAP: An AI Agent Execution Environment to Safeguard User Data (arxiv 2604.19657)
  - SAMOS: Securing MCP-based Agent Workflows (ACM '26)
  - HiStar (2006) / Asbestos (2005) — OS-level IFC precedents
"""

from __future__ import annotations

import enum
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Label model + sink kinds
# ---------------------------------------------------------------------------


class TaintLabel(enum.IntEnum):
    """Monotonic taint level — higher = more sensitive.

    Propagation rule: output_label = max(input_labels) (high-watermark).
    """

    PUBLIC = 0  # trusted; no taint
    UNTRUSTED = 1  # any tool output / external content
    SECRET = 2  # privileged LLM marked as sensitive
    TOXIC = 3  # active injection attempt detected


class SinkKind(enum.IntEnum):
    """Egress sink classes. Each has a configurable max-allowed label."""

    NETWORK_EGRESS = 0
    FILE_WRITE = 1
    USER_STDOUT = 2
    AUDIT_LOG = 3  # special — must accept all labels for compliance


_DEFAULT_POLICY: dict[SinkKind, TaintLabel] = {
    SinkKind.NETWORK_EGRESS: TaintLabel.UNTRUSTED,
    SinkKind.FILE_WRITE: TaintLabel.SECRET,
    SinkKind.USER_STDOUT: TaintLabel.SECRET,
    SinkKind.AUDIT_LOG: TaintLabel.TOXIC,
}


# ---------------------------------------------------------------------------
# Tainted blob + egress decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaintedBlob:
    """Bytes + their taint label + provenance chain.

    Immutable — to mutate, create a new blob via propagate() or
    declassify() so the audit chain stays traceable.
    """

    content: bytes
    label: TaintLabel
    source_id: str  # original source identifier (URL, tool name, etc.)
    provenance_chain: tuple[str, ...] = field(default_factory=tuple)
    sha256: str = ""  # filled at construction

    @staticmethod
    def _hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @classmethod
    def make(
        cls,
        content: bytes,
        label: TaintLabel,
        source_id: str,
        provenance_chain: Iterable[str] = (),
    ) -> "TaintedBlob":
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError("content must be bytes-like")
        if not isinstance(label, TaintLabel):
            raise TypeError("label must be TaintLabel")
        if not source_id or not isinstance(source_id, str):
            raise ValueError("source_id must be non-empty string")
        content_bytes = bytes(content)
        return cls(
            content=content_bytes,
            label=label,
            source_id=source_id,
            provenance_chain=tuple(provenance_chain),
            sha256=cls._hash(content_bytes),
        )


class EgressDecisionKind(enum.IntEnum):
    ALLOW = 0
    DENY = 1


@dataclass(frozen=True)
class EgressDecision:
    kind: EgressDecisionKind
    sink: SinkKind
    blob_label: TaintLabel
    sink_max_label: TaintLabel
    reason: str
    blob_sha256: str


@dataclass
class TaintStats:
    sources_labeled: int = 0
    propagations: int = 0
    declassifications: int = 0
    egress_checks: int = 0
    egress_allowed: int = 0
    egress_denied: int = 0


# ---------------------------------------------------------------------------
# TaintEngine
# ---------------------------------------------------------------------------


class TaintEngine:
    """Central chokepoint for IFC labeling + enforcement.

    Thread-safe — holds a Lock around the per-sink policy table + stats
    (the label-source / propagate / check_egress operations are
    short-lived under the lock; expected throughput is per-tool-call,
    not per-byte).
    """

    def __init__(self, policy: Optional[dict[SinkKind, TaintLabel]] = None):
        self._policy: dict[SinkKind, TaintLabel] = dict(policy or _DEFAULT_POLICY)
        self._lock = threading.Lock()
        self._stats = TaintStats()

    # -- Source labeling -----------------------------------------------------

    def label_source(
        self, source_id: str, content: bytes, label: TaintLabel = TaintLabel.UNTRUSTED
    ) -> TaintedBlob:
        """Tag bytes from a NEW source with an initial label.

        Tool outputs default to UNTRUSTED. Callers can pass a higher label
        (e.g. TOXIC for content the dual-LLM router flagged as injection).
        """
        blob = TaintedBlob.make(
            content=content,
            label=label,
            source_id=source_id,
            provenance_chain=(source_id,),
        )
        with self._lock:
            self._stats.sources_labeled += 1
        return blob

    # -- Propagation ---------------------------------------------------------

    def propagate(
        self,
        input_blobs: Iterable[TaintedBlob],
        output_content: bytes,
        derived_source_id: str = "agent_output",
    ) -> TaintedBlob:
        """Compute the output label as max(input labels) — high-watermark.

        Provenance chain is the union of input chains + the derivation
        site, preserved as a tuple for audit traceability.
        """
        input_list = list(input_blobs)
        if not input_list:
            # No inputs ⇒ this is a synthetic/trusted output (e.g. the
            # privileged LLM's own reasoning). Label PUBLIC.
            return TaintedBlob.make(
                content=output_content,
                label=TaintLabel.PUBLIC,
                source_id=derived_source_id,
                provenance_chain=(derived_source_id,),
            )
        max_label = max(b.label for b in input_list)
        chain: list[str] = []
        for b in input_list:
            for p in b.provenance_chain:
                if p not in chain:
                    chain.append(p)
        if derived_source_id not in chain:
            chain.append(derived_source_id)
        result = TaintedBlob.make(
            content=output_content,
            label=max_label,
            source_id=derived_source_id,
            provenance_chain=tuple(chain),
        )
        with self._lock:
            self._stats.propagations += 1
        return result

    # -- Declassification (audit-trailed; rare) ------------------------------

    def declassify(
        self,
        blob: TaintedBlob,
        target_label: TaintLabel,
        reason: str,
        authorized_by: str,
    ) -> TaintedBlob:
        """Lower a blob's label — used only when the privileged LLM (or
        an operator) has REVIEWED the bytes and confirmed they're safe.

        Recorded in stats; the caller is expected to ALSO emit an audit
        event (G2 + G4 reasoning-audit spans) for the declassification.
        """
        if not isinstance(target_label, TaintLabel):
            raise TypeError("target_label must be TaintLabel")
        if target_label > blob.label:
            raise ValueError(
                f"declassify cannot RAISE label "
                f"({blob.label.name} → {target_label.name})"
            )
        if not reason or not isinstance(reason, str):
            raise ValueError("reason must be non-empty string")
        if not authorized_by or not isinstance(authorized_by, str):
            raise ValueError("authorized_by must be non-empty string")
        marker = f"declassify({blob.label.name}→{target_label.name},by={authorized_by})"
        new_chain = blob.provenance_chain + (marker,)
        with self._lock:
            self._stats.declassifications += 1
        return TaintedBlob.make(
            content=blob.content,
            label=target_label,
            source_id=blob.source_id,
            provenance_chain=new_chain,
        )

    # -- Egress enforcement --------------------------------------------------

    def check_egress(self, blob: TaintedBlob, sink: SinkKind) -> EgressDecision:
        """Decide whether a blob may flow to a sink.

        Allowed iff blob.label <= self._policy[sink].
        """
        if not isinstance(sink, SinkKind):
            raise TypeError("sink must be SinkKind")
        with self._lock:
            self._stats.egress_checks += 1
            max_label = self._policy.get(sink, TaintLabel.PUBLIC)
            if blob.label <= max_label:
                self._stats.egress_allowed += 1
                decision = EgressDecision(
                    kind=EgressDecisionKind.ALLOW,
                    sink=sink,
                    blob_label=blob.label,
                    sink_max_label=max_label,
                    reason="label_within_policy",
                    blob_sha256=blob.sha256,
                )
            else:
                self._stats.egress_denied += 1
                decision = EgressDecision(
                    kind=EgressDecisionKind.DENY,
                    sink=sink,
                    blob_label=blob.label,
                    sink_max_label=max_label,
                    reason="label_exceeds_sink_policy",
                    blob_sha256=blob.sha256,
                )
        return decision

    # -- Policy + stats ------------------------------------------------------

    def policy_set(self, sink: SinkKind, max_label: TaintLabel) -> None:
        if not isinstance(sink, SinkKind):
            raise TypeError("sink must be SinkKind")
        if not isinstance(max_label, TaintLabel):
            raise TypeError("max_label must be TaintLabel")
        with self._lock:
            self._policy[sink] = max_label

    def policy_get(self, sink: SinkKind) -> TaintLabel:
        with self._lock:
            return self._policy.get(sink, TaintLabel.PUBLIC)

    def snapshot_stats(self) -> TaintStats:
        with self._lock:
            return TaintStats(
                sources_labeled=self._stats.sources_labeled,
                propagations=self._stats.propagations,
                declassifications=self._stats.declassifications,
                egress_checks=self._stats.egress_checks,
                egress_allowed=self._stats.egress_allowed,
                egress_denied=self._stats.egress_denied,
            )


__all__ = [
    "TaintLabel",
    "SinkKind",
    "TaintedBlob",
    "EgressDecisionKind",
    "EgressDecision",
    "TaintStats",
    "TaintEngine",
]
