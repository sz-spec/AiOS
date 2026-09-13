"""
backend/services/m3_signature_gate.py — Phase 30 (Gap G9)
==========================================================

Userspace M3 model-signature gate: verifies the cryptographic signature of every
model weight-set at the load ingress, behind an abstract Hardware Security Module
(HSM) interface, BEFORE the weights are handed to a kernel slot.

Honest scope (read before citing G9 as closed)
===============================================

The KERNEL read-gate already exists (`kernel/src/fs/vvfs_transport.c` +
`vvfs_model_verify.c`, the A6 host-twin tests) but ships **OFF**
(`VOS3_VVFS_REQUIRE_MODEL_SIG=0`) and its only key is an *ephemeral test vector*
(`a6_vectors.h`). Per the remediation playbook (G9 / INV-6), truly CLOSING G9 is
NOT code-only: it needs (1) a real signing key provisioned via a real HSM /
sealed build secret, (2) the shipped `.gguf`/`.safetensors` actually signed, and
(3) a Phase-6 external crypto audit. Flipping the kernel gate on with the test
key would brick real model loads.

So this module lands the **userspace enforcement layer + HSM seam**, default
**OFF** (`VOS3_ENABLE_M3_HSM_GATE`), so dev/CI are unaffected:
  - OFF (default): an unsigned/invalid payload logs a `[SECURITY_WARNING]` and is
    ALLOWED (legacy pipelines keep working).
  - ON: an invalid/missing signature is **fail-closed** — the model is quarantined
    with a TOXIC taint color via the existing `KernelGateConnector` taint_colors
    mechanism (NOT by editing the LSM hook — the egress hook is the wrong layer
    for a load-time gate), any in-flight buffer is discarded, an audit record is
    emitted, and `M3GateRejected` is raised (→ HTTP 403 at the route layer).

Audit sink: there is **no ClickHouse subsystem** in this repo — the structured
`[SECURITY][m3-gate]` log line (or an injected sink) IS the audit record, matching
the project-wide convention. Markers: `M3_GATE_VERIFIED` / `M3_GATE_REJECTED` /
`M3_GATE_WARN_ALLOWED`.

This advances no moat tally: INV-6 gates the G9 moat row on the external audit.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

logger = logging.getLogger("vos3.security.m3_gate")

# Taint label for quarantine (mirrors enum vos3_taint_label: TOXIC == 3).
_TOXIC = 3


def m3_gate_enabled() -> bool:
    """True iff the operator opted the live M3 HSM gate ON. Default OFF so dev/CI
    and the existing model-load path are unaffected."""
    return os.environ.get("VOS3_ENABLE_M3_HSM_GATE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# ---------------------------------------------------------------------------
# HSM seam
# ---------------------------------------------------------------------------


class HardwareSecurityModule(Protocol):
    """Abstract HSM: verifies a signature over a digest against a hardware-held
    trust-root key. Production swaps in a PKCS#11 / CloudHSM-backed implementation
    via the same interface; the gate never sees private key material."""

    @property
    def key_id(self) -> str: ...

    def verify(self, *, digest: bytes, signature: bytes) -> bool: ...


class LocalEd25519HSM:
    """Dev/test HSM stand-in: a software Ed25519 verifier over the trust-root
    PUBLIC key. Real crypto (``cryptography``), not a mock verdict — but the key
    lives in process memory, not hardware, so it is INSECURE for production. The
    swap point is this class; the gate is unchanged."""

    def __init__(self, public_key, *, key_id: str = "vos3-m3-dev"):
        self._pub = public_key
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def verify(self, *, digest: bytes, signature: bytes) -> bool:
        from cryptography.exceptions import InvalidSignature

        if not signature:
            return False
        try:
            self._pub.verify(signature, digest)
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False


# ---------------------------------------------------------------------------
# Decision + failure types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class M3GateDecision:
    verdict: str  # "VERIFIED" | "WARN_ALLOWED"
    reason: str
    audit_marker: str  # M3_GATE_VERIFIED | M3_GATE_WARN_ALLOWED
    key_id: str


class M3GateRejected(Exception):
    """Raised when the M3 HSM gate is ON and a model's signature is missing or
    invalid. The route layer maps this to HTTP 403 (see app.py
    ``m3_gate_rejected_handler``). FAIL-CLOSED: never swallowed, never downgraded.
    ``.reason`` carries the audit cause; ``.model_id`` the offending model."""

    def __init__(self, reason: str, *, model_id: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.model_id = model_id


def _default_audit_sink(record: dict) -> None:
    """Default audit-registry export: a structured ``[SECURITY][m3-gate]`` log
    (the structured log IS the audit record; no ClickHouse exists)."""
    level = (
        logging.WARNING if record.get("marker") != "M3_GATE_VERIFIED" else logging.INFO
    )
    logger.log(
        level,
        "[SECURITY][m3-gate] %s model=%s key=%s reason=%s",
        record.get("marker"),
        record.get("model_id"),
        record.get("key_id"),
        record.get("reason"),
    )


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class M3ModelSignatureGate:
    """Verifies a model payload's signature via the HSM before load.

    Atomic + fail-closed when ON: on an invalid/missing signature it (1) assigns a
    TOXIC taint color to the model's (pid, fd) slot through the kernel gate
    connector — so any egress of the toxic weights is kernel-blocked — (2) emits
    an ``M3_GATE_REJECTED`` audit record, and (3) raises ``M3GateRejected``. The
    caller MUST treat the raise as a complete discard of the model buffers."""

    def __init__(
        self,
        hsm: HardwareSecurityModule,
        *,
        gate_connector=None,
        audit_sink: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._hsm = hsm
        self._conn = gate_connector
        self._sink = audit_sink or _default_audit_sink

    def _audit(self, marker: str, model_id: str, reason: str) -> None:
        try:
            self._sink(
                {
                    "marker": marker,
                    "model_id": model_id,
                    "key_id": self._hsm.key_id,
                    "reason": reason,
                }
            )
        except Exception as exc:  # noqa: BLE001 — audit export must not break the gate
            logger.warning("[m3-gate] audit sink raised (dropped): %s", exc)

    def _quarantine_toxic(
        self, model_id: str, *, pid: Optional[int], fd: Optional[int]
    ) -> bool:
        """Assign a TOXIC taint color to the model's (pid, fd) slot via the EXISTING
        taint_colors connector mechanism, so the kernel egress gate blocks the
        toxic weights. Best-effort: returns True iff a TOXIC color was pushed.
        Never raises (quarantine failure must not mask the rejection)."""
        if pid is None or fd is None:
            return False
        try:
            conn = self._conn
            if conn is None:
                from security.kernel_gate_connector import get_kernel_gate

                conn = get_kernel_gate()
            from security.kernel_gate_connector import SinkKind

            toxic = _ToxicBuffer(64)  # all-TOXIC color mask over a small slice
            conn.push_tainted_buffer(
                fd=fd, pid=pid, buffer=toxic, sink_kind=SinkKind.NETWORK_EGRESS
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[m3-gate] TOXIC quarantine push failed for %s: %s", model_id, exc
            )
            return False

    def verify_payload(
        self,
        *,
        model_id: str,
        digest: bytes,
        signature: Optional[bytes],
        pid: Optional[int] = None,
        fd: Optional[int] = None,
    ) -> M3GateDecision:
        """Verify a signature over ``digest`` (typically SHA-256 of the weights).
        Returns a decision on success/OFF; raises ``M3GateRejected`` on ON+invalid."""
        ok = bool(signature) and self._hsm.verify(digest=digest, signature=signature)
        if ok:
            self._audit("M3_GATE_VERIFIED", model_id, "signature verified by HSM")
            return M3GateDecision(
                "VERIFIED", "signature verified", "M3_GATE_VERIFIED", self._hsm.key_id
            )

        reason = (
            "missing signature" if not signature else "signature verification failed"
        )
        if m3_gate_enabled():
            # Fail-closed + atomic: quarantine TOXIC, audit, discard (raise).
            quarantined = self._quarantine_toxic(model_id, pid=pid, fd=fd)
            self._audit(
                "M3_GATE_REJECTED",
                model_id,
                f"{reason}; toxic_quarantined={quarantined}",
            )
            raise M3GateRejected(
                f"M3 HSM gate rejected model {model_id!r}: {reason}", model_id=model_id
            )

        # OFF: warn but allow (legacy dev/CI pipelines keep working).
        self._audit("M3_GATE_WARN_ALLOWED", model_id, f"{reason} (gate OFF — allowed)")
        return M3GateDecision(
            "WARN_ALLOWED",
            f"{reason} (gate OFF)",
            "M3_GATE_WARN_ALLOWED",
            self._hsm.key_id,
        )

    def verify_model_file(
        self,
        *,
        model_id: str,
        sha256_hex: str,
        signature: Optional[bytes],
        pid: Optional[int] = None,
        fd: Optional[int] = None,
    ) -> M3GateDecision:
        """Convenience: verify against a hex SHA-256 the registry already computed
        (no re-read of the weight file)."""
        try:
            digest = bytes.fromhex(sha256_hex)
        except (ValueError, TypeError):
            digest = b""
        return self.verify_payload(
            model_id=model_id, digest=digest, signature=signature, pid=pid, fd=fd
        )


class _ToxicBuffer:
    """A TaintedBuffer-shaped all-TOXIC mask used to quarantine a rejected model."""

    def __init__(self, n: int):
        import hashlib

        self.content = b"\x00" * n
        self.colors = bytes([_TOXIC] * n)
        self._sha = hashlib.sha256(self.content).hexdigest()

    @property
    def sha256(self) -> str:
        return self._sha

    def max_color(self) -> int:
        return _TOXIC


# ---------------------------------------------------------------------------
# Singleton + dev key resolution
# ---------------------------------------------------------------------------

_GATE_SINGLETON: Optional[M3ModelSignatureGate] = None
_SINGLETON_LOCK = None


def _resolve_dev_hsm() -> "HardwareSecurityModule":
    """Load the trust-root PUBLIC key for the dev HSM from
    ``VOS3_M3_TRUST_ROOT_PEM`` (a PEM Ed25519 public key) if set; else generate an
    ephemeral verify-only key (every signature then fails -> OFF warns / ON
    rejects, which is the safe default when no anchor is provisioned)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    pem = os.environ.get("VOS3_M3_TRUST_ROOT_PEM", "").strip()
    if pem and os.path.isfile(pem):
        try:
            pub = serialization.load_pem_public_key(open(pem, "rb").read())
            return LocalEd25519HSM(pub, key_id=f"file:{os.path.basename(pem)}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[m3-gate] trust-root %s unloadable (%s); ephemeral", pem, exc
            )
    # No anchor provisioned -> ephemeral verify-only key (nothing verifies).
    eph = Ed25519PrivateKey.generate().public_key()
    return LocalEd25519HSM(eph, key_id="ephemeral-no-anchor")


def get_m3_gate() -> M3ModelSignatureGate:
    global _GATE_SINGLETON, _SINGLETON_LOCK
    if _SINGLETON_LOCK is None:
        import threading

        _SINGLETON_LOCK = threading.Lock()
    if _GATE_SINGLETON is None:
        with _SINGLETON_LOCK:
            if _GATE_SINGLETON is None:
                _GATE_SINGLETON = M3ModelSignatureGate(_resolve_dev_hsm())
    return _GATE_SINGLETON


def _reset_singleton_for_tests() -> None:
    global _GATE_SINGLETON
    _GATE_SINGLETON = None


__all__ = [
    "m3_gate_enabled",
    "HardwareSecurityModule",
    "LocalEd25519HSM",
    "M3GateDecision",
    "M3GateRejected",
    "M3ModelSignatureGate",
    "get_m3_gate",
]
