"""
backend/security/taint_engine_v2.py
=====================================

Sprint 17 / Cluster C-1 — Byte-level information-flow control engine.

What this is
------------

Cluster C upgrade of Sprint 16 / C7 (`ifc_engine.py`). C7 ships
**blob-level** taint: one `TaintLabel` per `TaintedBlob`. The known
limitation per the Fides paper (arxiv 2505.23643) + C7's own docstring:

  > "High-watermark over a whole blob falsely contaminates clean
  >  fields with a single UNTRUSTED byte. Byte-level tracking solves
  >  the false-positive class."

Cluster C-1 ships **per-byte** color: each byte of a `TaintedBuffer`
has its own label. Slicing preserves per-byte color. Concat merges
per-byte color via max. Egress checks consider the MAX color in the
relevant range.

This module COEXISTS with C7 — it does not replace it. C7 stays as
the fast path for callers that don't need per-byte fidelity. C-1 is
opt-in for callers (web-fetch intake, document-parser output, MAIF
artifact streams) that need to avoid the high-watermark over-contamination.

Public surface
--------------

  ByteTaintEngine
    .label_source(source_id, content, label) -> TaintedBuffer
    .label_range(buffer, start, end, label) -> TaintedBuffer
    .concat(buffers) -> TaintedBuffer
    .slice(buffer, start, end) -> TaintedBuffer
    .max_color_in_range(buffer, start, end) -> TaintLabel
    .check_egress(buffer, sink) -> EgressDecision
    .declassify_range(buffer, start, end, target_label, *, evidence)
       -> TaintedBuffer
    .from_blob(c7_blob) -> TaintedBuffer            # interop with C7
    .to_blob(buffer) -> dict suitable for C7

Honest scope ceiling (carried from CLUSTER_C_BYTE_LEVEL_IFC_SPEC.md)
--------------------------------------------------------------------

  - Python-side ONLY in this hello-world. Kernel-side enforcement
    (C-2 eBPF LSM write-gate) is Sprint 18 work.
  - Declassification interface is Ed25519-base MVP (this module's
    DeclassEvidence + _verify_ed25519). BBS+ selective disclosure
    and Groth16 DV-SNARK are Sprint 18/19 extensions.
  - 100% memory overhead (1 color byte per content byte). Operators
    can exclude large model-weight buffers via
    VOS3_IFC_NO_COLOR_GLOBS env (default skips .gguf/.safetensors/.onnx).
  - Buffers are immutable; mutating the underlying bytes outside the
    engine breaks the invariant. Caller code that needs to mutate
    must call engine.label_range() to get a new buffer.
  - Byte-level coloring has FALSE-NEGATIVE risk on covert channels
    (timing, cache). Pair with Sprint 16 C2 dual-LLM router + H1
    combined egress + C7 declassification-on-review.
"""

from __future__ import annotations

import enum
import fnmatch
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Label / sink enums — mirror C7 exactly so cross-engine conversion is trivial
# ---------------------------------------------------------------------------


class TaintLabel(enum.IntEnum):
    PUBLIC = 0
    UNTRUSTED = 1
    SECRET = 2
    TOXIC = 3


class SinkKind(enum.IntEnum):
    NETWORK_EGRESS = 0
    FILE_WRITE = 1
    USER_STDOUT = 2
    AUDIT_LOG = 3  # always accepts, per the C7 contract


_DEFAULT_SINK_POLICY: dict[SinkKind, TaintLabel] = {
    SinkKind.NETWORK_EGRESS: TaintLabel.UNTRUSTED,
    SinkKind.FILE_WRITE: TaintLabel.SECRET,
    SinkKind.USER_STDOUT: TaintLabel.SECRET,
    SinkKind.AUDIT_LOG: TaintLabel.TOXIC,
}


_DEFAULT_NO_COLOR_GLOBS = (
    "*.gguf",
    "*.safetensors",
    "*.onnx",
    "*.pt",
    "*.pth",
    "*.bin",
)


def _no_color_globs() -> tuple[str, ...]:
    env = os.environ.get("VOS3_IFC_NO_COLOR_GLOBS", "").strip()
    if env:
        return tuple(g.strip() for g in env.split(",") if g.strip())
    return _DEFAULT_NO_COLOR_GLOBS


# ---------------------------------------------------------------------------
# TaintedBuffer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaintedBuffer:
    """Bytes + per-byte color array. Immutable.

    Invariant: len(content) == len(colors). color bytes are in {0,1,2,3}.
    """

    content: bytes
    colors: bytes  # parallel array; one TaintLabel int per byte
    provenance_chain: tuple[str, ...] = field(default_factory=tuple)
    sha256: str = ""
    creation_ts: float = 0.0
    no_color_reason: Optional[str] = None  # set when buffer is excluded from
    # coloring (model-weight opt-out)

    @staticmethod
    def _hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @classmethod
    def make(
        cls,
        content: bytes,
        colors: bytes,
        source_id: str,
        provenance_chain: Iterable[str] = (),
        no_color_reason: Optional[str] = None,
    ) -> "TaintedBuffer":
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError("content must be bytes-like")
        content_bytes = bytes(content)
        if no_color_reason is None:
            if not isinstance(colors, (bytes, bytearray)):
                raise TypeError("colors must be bytes-like")
            color_bytes = bytes(colors)
            if len(color_bytes) != len(content_bytes):
                raise ValueError(
                    f"colors length {len(color_bytes)} != content "
                    f"length {len(content_bytes)}"
                )
            for c in color_bytes:
                if c not in (0, 1, 2, 3):
                    raise ValueError(
                        f"invalid color byte {c}; must be 0..3 (TaintLabel)"
                    )
        else:
            # Skipped-coloring buffer carries empty colors array.
            color_bytes = b""
        if not source_id or not isinstance(source_id, str):
            raise ValueError("source_id must be non-empty string")
        chain = tuple(provenance_chain) if provenance_chain else (source_id,)
        if source_id not in chain:
            chain = chain + (source_id,)
        return cls(
            content=content_bytes,
            colors=color_bytes,
            provenance_chain=chain,
            sha256=cls._hash(content_bytes),
            creation_ts=time.time(),
            no_color_reason=no_color_reason,
        )

    def max_color(self) -> TaintLabel:
        """Maximum color over the entire buffer.

        Buffers excluded from coloring (no_color_reason set) return
        TOXIC as a SAFE-FAIL — operators relied on opt-out for model
        weights; egress of those bytes should default to "deny" so
        the operator notices if the wrong buffer gets opted out.
        """
        if self.no_color_reason is not None:
            return TaintLabel.TOXIC
        if not self.colors:
            return TaintLabel.PUBLIC
        return TaintLabel(max(self.colors))


# ---------------------------------------------------------------------------
# Egress decision
# ---------------------------------------------------------------------------


class EgressDecisionKind(enum.IntEnum):
    ALLOW = 0
    DENY = 1


@dataclass(frozen=True)
class EgressDecision:
    kind: EgressDecisionKind
    sink: SinkKind
    max_color_in_buffer: TaintLabel
    sink_max_label: TaintLabel
    reason: str
    buffer_sha256: str


# ---------------------------------------------------------------------------
# Declassification — Ed25519-base MVP
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeclassEvidence:
    """Cryptographic evidence that a byte range was reviewed before
    declassification."""

    reviewer_id: str  # SPIFFE-ID or operator email
    reviewed_bytes_sha256: str  # SHA-256 of the bytes being downgraded
    reviewed_at: float
    target_label: int  # TaintLabel int
    reason: str
    signature: bytes  # Ed25519 over canonical JSON of the rest
    public_key: bytes  # Ed25519 pubkey of reviewer

    def canonical_payload(self) -> bytes:
        """The exact bytes signature must cover. Excludes signature + pubkey
        so the same evidence can be re-verified with the pubkey ascribed
        at verification time."""
        d = {
            "reviewer_id": self.reviewer_id,
            "reviewed_bytes_sha256": self.reviewed_bytes_sha256,
            "reviewed_at": self.reviewed_at,
            "target_label": self.target_label,
            "reason": self.reason,
        }
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode("utf-8")


class DeclassError(Exception):
    pass


def _verify_ed25519(evidence: DeclassEvidence) -> bool:
    """Verify the Ed25519 signature on a DeclassEvidence.

    Uses the in-tree `cryptography` library. Returns True on success;
    raises DeclassError on any failure (bad signature, wrong key, etc.)
    so callers can capture the cause.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:
        raise DeclassError(
            f"cryptography library not available for Ed25519 verify: {exc}"
        )
    try:
        pk = Ed25519PublicKey.from_public_bytes(evidence.public_key)
    except Exception as exc:
        raise DeclassError(f"invalid public_key bytes: {exc}")
    try:
        pk.verify(evidence.signature, evidence.canonical_payload())
    except InvalidSignature:
        raise DeclassError("signature verification failed")
    except Exception as exc:
        raise DeclassError(f"signature verify error: {exc}")
    return True


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class ByteTaintStats:
    sources_labeled: int = 0
    ranges_relabeled: int = 0
    concats: int = 0
    slices: int = 0
    egress_checks: int = 0
    egress_allowed: int = 0
    egress_denied: int = 0
    declassifications: int = 0
    declass_evidence_failures: int = 0
    no_color_buffers: int = 0


# ---------------------------------------------------------------------------
# ByteTaintEngine
# ---------------------------------------------------------------------------


class ByteTaintEngine:
    """Byte-level taint engine. Coexists with C7 TaintEngine."""

    def __init__(
        self,
        policy: Optional[dict[SinkKind, TaintLabel]] = None,
        no_color_globs: Optional[Iterable[str]] = None,
    ):
        self._policy: dict[SinkKind, TaintLabel] = dict(policy or _DEFAULT_SINK_POLICY)
        self._no_color_globs = (
            tuple(no_color_globs) if no_color_globs is not None else _no_color_globs()
        )
        self._lock = threading.Lock()
        self._stats = ByteTaintStats()

    # -- Source labeling -----------------------------------------------------

    def label_source(
        self,
        source_id: str,
        content: bytes,
        label: TaintLabel = TaintLabel.UNTRUSTED,
        filename_hint: Optional[str] = None,
    ) -> TaintedBuffer:
        """Tag bytes from a new source. Returns a TaintedBuffer with
        uniform per-byte color = `label`.

        If filename_hint matches any VOS3_IFC_NO_COLOR_GLOBS pattern,
        the buffer is created with no_color_reason set (memory-saver
        for large model-weight files)."""
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError("content must be bytes-like")
        if not isinstance(label, TaintLabel):
            raise TypeError("label must be TaintLabel")
        no_color_reason = None
        if filename_hint is not None:
            for g in self._no_color_globs:
                if fnmatch.fnmatchcase(filename_hint, g):
                    no_color_reason = f"matches_no_color_glob:{g}"
                    break
        colors = b"" if no_color_reason else bytes([int(label)] * len(content))
        buf = TaintedBuffer.make(
            content=content,
            colors=colors,
            source_id=source_id,
            no_color_reason=no_color_reason,
        )
        with self._lock:
            self._stats.sources_labeled += 1
            if no_color_reason is not None:
                self._stats.no_color_buffers += 1
        return buf

    # -- Range relabel -------------------------------------------------------

    def label_range(
        self, buffer: TaintedBuffer, start: int, end: int, label: TaintLabel
    ) -> TaintedBuffer:
        """Return a NEW buffer with [start:end) re-labeled to `label`.

        Lifts the per-byte color at those positions to `label` ONLY IF
        the new label is GREATER than the existing color (high-watermark
        within the range — never downgrade via this method; use
        declassify_range for downgrades)."""
        if buffer.no_color_reason is not None:
            raise ValueError(
                f"cannot label_range on no-color buffer "
                f"(reason: {buffer.no_color_reason})"
            )
        if not isinstance(label, TaintLabel):
            raise TypeError("label must be TaintLabel")
        if start < 0 or end > len(buffer.content) or start >= end:
            raise ValueError(
                f"invalid range [{start}, {end}) for buffer of "
                f"length {len(buffer.content)}"
            )
        new_colors = bytearray(buffer.colors)
        target = int(label)
        for i in range(start, end):
            if new_colors[i] < target:
                new_colors[i] = target
        new_buf = TaintedBuffer.make(
            content=buffer.content,
            colors=bytes(new_colors),
            source_id=buffer.provenance_chain[0],
            provenance_chain=buffer.provenance_chain
            + (f"label_range[{start}:{end}]={label.name}",),
        )
        with self._lock:
            self._stats.ranges_relabeled += 1
        return new_buf

    # -- Slice / concat ------------------------------------------------------

    def slice(self, buffer: TaintedBuffer, start: int, end: int) -> TaintedBuffer:
        if buffer.no_color_reason is not None:
            raise ValueError("cannot slice a no-color buffer")
        if start < 0 or end > len(buffer.content) or start > end:
            raise ValueError(f"invalid slice [{start}, {end})")
        new_content = buffer.content[start:end]
        new_colors = buffer.colors[start:end]
        new_buf = TaintedBuffer.make(
            content=new_content,
            colors=new_colors,
            source_id=buffer.provenance_chain[0],
            provenance_chain=buffer.provenance_chain + (f"slice[{start}:{end}]",),
        )
        with self._lock:
            self._stats.slices += 1
        return new_buf

    def concat(self, buffers: Iterable[TaintedBuffer]) -> TaintedBuffer:
        """Concat buffers; per-byte colors are preserved (one byte
        from each constituent buffer contributes its own color)."""
        bufs = list(buffers)
        if not bufs:
            raise ValueError("concat requires at least one buffer")
        for b in bufs:
            if b.no_color_reason is not None:
                raise ValueError(
                    f"cannot concat a no-color buffer (id={b.provenance_chain[0]})"
                )
        new_content = b"".join(b.content for b in bufs)
        new_colors = b"".join(b.colors for b in bufs)
        chain: list[str] = []
        for b in bufs:
            for p in b.provenance_chain:
                if p not in chain:
                    chain.append(p)
        chain.append(f"concat({len(bufs)})")
        new_buf = TaintedBuffer.make(
            content=new_content,
            colors=new_colors,
            source_id=bufs[0].provenance_chain[0],
            provenance_chain=tuple(chain),
        )
        with self._lock:
            self._stats.concats += 1
        return new_buf

    # -- Max-color in range --------------------------------------------------

    def max_color_in_range(
        self, buffer: TaintedBuffer, start: int, end: int
    ) -> TaintLabel:
        if buffer.no_color_reason is not None:
            return TaintLabel.TOXIC
        if start < 0 or end > len(buffer.content) or start >= end:
            raise ValueError(f"invalid range [{start}, {end})")
        return TaintLabel(max(buffer.colors[start:end]))

    # -- Egress check --------------------------------------------------------

    def check_egress(self, buffer: TaintedBuffer, sink: SinkKind) -> EgressDecision:
        if not isinstance(sink, SinkKind):
            raise TypeError("sink must be SinkKind")
        max_color = buffer.max_color()
        sink_max = self._policy.get(sink, TaintLabel.PUBLIC)
        with self._lock:
            self._stats.egress_checks += 1
            allowed = max_color <= sink_max
            if allowed:
                self._stats.egress_allowed += 1
            else:
                self._stats.egress_denied += 1
        return EgressDecision(
            kind=(EgressDecisionKind.ALLOW if allowed else EgressDecisionKind.DENY),
            sink=sink,
            max_color_in_buffer=max_color,
            sink_max_label=sink_max,
            reason=("label_within_policy" if allowed else "label_exceeds_sink_policy"),
            buffer_sha256=buffer.sha256,
        )

    # -- Declassification ----------------------------------------------------

    def declassify_range(
        self,
        buffer: TaintedBuffer,
        start: int,
        end: int,
        target_label: TaintLabel,
        *,
        evidence: DeclassEvidence,
    ) -> TaintedBuffer:
        """Downgrade [start:end) to `target_label` after verifying the
        Ed25519-signed evidence + the SHA-256-binding to the actual bytes."""
        if buffer.no_color_reason is not None:
            raise ValueError("cannot declassify a no-color buffer")
        if not isinstance(target_label, TaintLabel):
            raise TypeError("target_label must be TaintLabel")
        if start < 0 or end > len(buffer.content) or start >= end:
            raise ValueError(f"invalid range [{start}, {end})")

        current_max = self.max_color_in_range(buffer, start, end)
        if int(target_label) > int(current_max):
            raise ValueError(
                f"declassify cannot RAISE label "
                f"({current_max.name} → {target_label.name}); use label_range"
            )

        # SHA-binding check.
        actual_sha = hashlib.sha256(buffer.content[start:end]).hexdigest()
        if actual_sha != evidence.reviewed_bytes_sha256:
            with self._lock:
                self._stats.declass_evidence_failures += 1
            raise DeclassError(
                f"evidence SHA-256 does not match actual bytes: "
                f"evidence={evidence.reviewed_bytes_sha256[:16]}... "
                f"actual={actual_sha[:16]}..."
            )

        # Time-window check (5-minute default).
        if abs(time.time() - evidence.reviewed_at) > 300:
            with self._lock:
                self._stats.declass_evidence_failures += 1
            raise DeclassError(
                f"evidence reviewed_at {evidence.reviewed_at} is outside "
                f"the 5-minute time window from current time {time.time()}"
            )

        # Target-label check.
        if int(target_label) != evidence.target_label:
            with self._lock:
                self._stats.declass_evidence_failures += 1
            raise DeclassError(
                f"evidence target_label {evidence.target_label} != "
                f"declassify target {int(target_label)}"
            )

        # Ed25519 verify.
        try:
            _verify_ed25519(evidence)
        except DeclassError:
            with self._lock:
                self._stats.declass_evidence_failures += 1
            raise

        # All checks pass — emit downgraded buffer.
        new_colors = bytearray(buffer.colors)
        target = int(target_label)
        for i in range(start, end):
            new_colors[i] = target
        new_buf = TaintedBuffer.make(
            content=buffer.content,
            colors=bytes(new_colors),
            source_id=buffer.provenance_chain[0],
            provenance_chain=buffer.provenance_chain
            + (
                f"declassify[{start}:{end}]→{target_label.name}"
                f"_by:{evidence.reviewer_id}",
            ),
        )
        with self._lock:
            self._stats.declassifications += 1
        return new_buf

    # -- C7 interop ----------------------------------------------------------

    def from_blob_label(
        self, content: bytes, source_id: str, label: TaintLabel
    ) -> TaintedBuffer:
        """Lift a C7-style (content, label) pair into a byte-level
        buffer with uniform per-byte color = label."""
        return self.label_source(source_id, content, label)

    def to_blob_max(self, buffer: TaintedBuffer) -> tuple[bytes, TaintLabel]:
        """Lower a byte-level buffer to a C7-style (content, max_label)
        pair. Lossy: loses per-byte fidelity."""
        return buffer.content, buffer.max_color()

    # -- Stats ---------------------------------------------------------------

    def snapshot_stats(self) -> ByteTaintStats:
        with self._lock:
            return ByteTaintStats(
                sources_labeled=self._stats.sources_labeled,
                ranges_relabeled=self._stats.ranges_relabeled,
                concats=self._stats.concats,
                slices=self._stats.slices,
                egress_checks=self._stats.egress_checks,
                egress_allowed=self._stats.egress_allowed,
                egress_denied=self._stats.egress_denied,
                declassifications=self._stats.declassifications,
                declass_evidence_failures=self._stats.declass_evidence_failures,
                no_color_buffers=self._stats.no_color_buffers,
            )


__all__ = [
    "TaintLabel",
    "SinkKind",
    "EgressDecisionKind",
    "TaintedBuffer",
    "EgressDecision",
    "DeclassEvidence",
    "DeclassError",
    "ByteTaintStats",
    "ByteTaintEngine",
]
