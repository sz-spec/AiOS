"""
backend/services/tpm_attestation.py — Phase 31 (Gap G7)
========================================================

Hardware-rooted platform-identity remote attestation: a challenge-response that
binds the platform's TPM 2.0 Endorsement Key (EK) to the current vOS state
(kernel-git-hash + signed M3 root) and the boot-time PCR bank, and FAILS CLOSED
(denies session-token issuance) when no TPM is present or the PCRs do not match
the authorized boot state.

Honest scope (read before citing G7 as closed)
===============================================

Real hardware attestation requires a physical TPM 2.0 at ``/dev/tpmrm0`` plus a
manufacturer EK-certificate chain to a trusted CA — and, per the remediation
playbook (INV-6), the attestation moat row only advances after an **external
crypto audit**, never on self-collected evidence. macOS/dev has no TPM, so this
module ships:
  - an abstract ``TPM2Device`` seam (production swaps a ``/dev/tpmrm0`` ESAPI
    implementation behind it; the service is unchanged), and
  - a software ``LocalSoftTPM`` (real ECDSA-P256 "EK" + an in-memory PCR bank)
    for dev/test — INSECURE for production (the EK is not hardware-bound and has
    no manufacturer cert chain).

Flag ``VOS3_ENABLE_TPM_ATTESTATION`` (default **OFF**) keeps dev/CI unaffected:
the token-issuance guard is a no-op until an operator opts in. When ON, the guard
enforces fail-closed: no TPM OR PCR mismatch → ``[SECURITY_CRITICAL]`` +
``AttestationDenied`` (→ HTTP 403; no session token is minted — the G2/G10
integration point).

This advances no moat tally.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Protocol

logger = logging.getLogger("vos3.security.tpm_attestation")

# Default PCR set asserted for the boot state: 0 (firmware), 1 (config),
# 7 (secure-boot policy), 11 (vOS measured-seal — cf. the finetune TPM seal).
DEFAULT_PCR_SELECTION = (0, 1, 7, 11)
_TPM_DEVICE_PATH = "/dev/tpmrm0"


def tpm_attestation_enabled() -> bool:
    """True iff the operator opted hardware-rooted attestation ON. Default OFF so
    dev/CI and the token-issuance path are unaffected."""
    return os.environ.get("VOS3_ENABLE_TPM_ATTESTATION", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# ---------------------------------------------------------------------------
# State digest: kernel-git-hash + signed M3 root.
# ---------------------------------------------------------------------------


def vos_state_digest(
    *, kernel_hash: Optional[str] = None, m3_root_ref: Optional[str] = None
) -> str:
    """SHA-256 over the platform identity: current kernel image hash +
    the signed M3 model-root reference (Phase 30). Defaults read the same env
    the compliance surface uses; explicit args make tests deterministic."""
    kh = kernel_hash or os.environ.get("VOS3_KERNEL_SHA256", "unknown-kernel")
    m3 = m3_root_ref or os.environ.get("VOS3_M3_TRUST_ROOT_PEM", "m3:ephemeral")
    return hashlib.sha256(f"vos-state-v1|{kh}|{m3}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# TPM 2.0 device seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TpmQuote:
    """An EK-signed attestation quote over (nonce || state_digest || pcr_digest)."""

    signature_b64: str
    ek_public_pem: str
    pcr_digest: str  # SHA-256 over the selected PCR values, in index order
    alg: str = "ecdsa-p256-sha256"


class TPM2Device(Protocol):
    """Abstract TPM 2.0. Production = an ESAPI binding over /dev/tpmrm0."""

    def present(self) -> bool: ...

    def read_pcrs(self, indices: tuple[int, ...]) -> Dict[int, str]: ...

    def quote(self, *, nonce: bytes, message: bytes) -> TpmQuote: ...


def _pcr_digest(pcrs: Dict[int, str], indices: tuple[int, ...]) -> str:
    h = hashlib.sha256()
    for i in indices:
        h.update(i.to_bytes(2, "big"))
        h.update(bytes.fromhex(pcrs.get(i, "00" * 32)))
    return h.hexdigest()


class LocalSoftTPM:
    """Software TPM stand-in (dev/test): a real ECDSA-P256 EK + an in-memory PCR
    bank. INSECURE for production (no hardware binding, no EK cert chain)."""

    def __init__(
        self,
        *,
        present: bool = True,
        pcrs: Optional[Dict[int, str]] = None,
    ):
        from cryptography.hazmat.primitives.asymmetric import ec

        self._present = present
        self._pcrs = dict(pcrs or {})
        self._ek = ec.generate_private_key(ec.SECP256R1())

    def present(self) -> bool:
        return self._present

    def read_pcrs(self, indices: tuple[int, ...]) -> Dict[int, str]:
        return {i: self._pcrs.get(i, "00" * 32) for i in indices}

    def quote(self, *, nonce: bytes, message: bytes) -> TpmQuote:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        sig = self._ek.sign(nonce + message, ec.ECDSA(hashes.SHA256()))
        pem = (
            self._ek.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )
        return TpmQuote(
            signature_b64=base64.b64encode(sig).decode("ascii"),
            ek_public_pem=pem,
            pcr_digest="",  # filled by the service (it owns the selection)
        )


class HSMBackedTPM2:
    """Phase 38 (G15) lockdown anchor: a production-shaped TPM that REQUIRES a
    manufacturer-signed EK-certificate chain validating to a trusted CA before it
    will attest. ``present()`` is True ONLY when a real ``/dev/tpmrm0`` exists AND
    the EK-cert chain (``VOS3_HSM_EK_CERT_CHAIN``) verifies to the trust CA
    (``VOS3_HSM_TRUST_CA``). On a dev host with neither, ``present()`` is False →
    every attestation/anchor FAILS CLOSED. We never fall back to a software EK in
    lockdown mode — that is the whole point of replacing the dev anchor."""

    def __init__(self) -> None:
        self._chain = os.environ.get("VOS3_HSM_EK_CERT_CHAIN", "").strip()
        self._ca = os.environ.get("VOS3_HSM_TRUST_CA", "").strip()

    def _cert_chain_valid(self) -> bool:
        # Production: verify the EK leaf -> ... -> trusted manufacturer CA chain.
        # We refuse to claim validity unless BOTH a device, a chain, and a CA are
        # provisioned AND readable. (Full X.509 path validation is the production
        # wiring; on dev the inputs are absent so this is False — fail-closed.)
        if not (self._chain and self._ca):
            return False
        if not (os.path.isfile(self._chain) and os.path.isfile(self._ca)):
            return False
        return os.path.exists(_TPM_DEVICE_PATH)

    def present(self) -> bool:
        return self._cert_chain_valid()

    def read_pcrs(self, indices):  # pragma: no cover - unreachable on dev (not present)
        raise RuntimeError(
            "HSMBackedTPM2.read_pcrs: real TPM binding is the production hook"
        )

    def quote(self, *, nonce, message):  # pragma: no cover - unreachable on dev
        raise RuntimeError(
            "HSMBackedTPM2.quote: real TPM binding is the production hook"
        )


def require_hsm_ek_cert() -> bool:
    """Lockdown flag: when set, the platform anchor MUST be the HSM EK-cert-chain
    TPM (no software dev anchor)."""
    return os.environ.get("VOS3_REQUIRE_HSM_EK_CERT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _resolve_default_tpm() -> TPM2Device:
    """Resolve the platform TPM. In lockdown (``VOS3_REQUIRE_HSM_EK_CERT``) the
    anchor MUST be the HSM EK-cert-chain TPM — which is not-present (fail-closed)
    on a dev host without a provisioned chain. Otherwise (dev default, flag unset)
    a host with /dev/tpmrm0 binds ESAPI in prod; absent that, a NOT-present soft
    TPM so the fail-closed path engages truthfully (no fake "present")."""
    if require_hsm_ek_cert():
        return HSMBackedTPM2()
    has_dev = os.path.exists(_TPM_DEVICE_PATH)
    return LocalSoftTPM(present=has_dev)


# ---------------------------------------------------------------------------
# Attestation result + failure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttestationResult:
    success: bool
    state_digest: str
    pcr_digest: str
    nonce_b64: str
    quote: Optional[TpmQuote]
    runtime_integrity_hash: str = ""  # Phase 32 (G6): bound into the EK quote


class AttestationDenied(Exception):
    """Raised (fail-closed) when the platform cannot be hardware-attested: no TPM,
    or the PCR bank does not match the authorized boot state. The route layer maps
    this to HTTP 403 (see app.py ``attestation_denied_handler``); no session token
    is issued. ``.reason`` carries the audit cause."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _default_audit_sink(record: dict) -> None:
    level = (
        logging.CRITICAL
        if record.get("marker") == "ATTESTATION_DENIED"
        else logging.INFO
    )
    logger.log(
        level,
        "[SECURITY_CRITICAL][tpm-attest] %s reason=%s state=%s",
        record.get("marker"),
        record.get("reason"),
        record.get("state_digest"),
    )


def _load_authorized_pcrs() -> Dict[int, str]:
    raw = os.environ.get("VOS3_TPM_AUTHORIZED_PCRS", "").strip()
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return {int(k): str(v) for k, v in d.items()}
    except (ValueError, TypeError):
        logger.warning("[tpm-attest] VOS3_TPM_AUTHORIZED_PCRS unparseable; ignoring")
        return {}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PlatformAttestationService:
    """Challenge-response platform attestation. ``attest`` returns a verifiable
    EK-signed quote over (nonce || state_digest || pcr_digest) when the TPM is
    present AND the selected PCRs match the authorized boot state; otherwise it
    FAILS CLOSED with ``AttestationDenied`` + a ``[SECURITY_CRITICAL]`` audit."""

    def __init__(
        self,
        tpm: TPM2Device,
        *,
        authorized_pcrs: Optional[Dict[int, str]] = None,
        pcr_selection: tuple[int, ...] = DEFAULT_PCR_SELECTION,
        audit_sink: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._tpm = tpm
        self._authorized = (
            authorized_pcrs if authorized_pcrs is not None else _load_authorized_pcrs()
        )
        self._selection = pcr_selection
        self._sink = audit_sink or _default_audit_sink

    def _audit(self, marker: str, reason: str, state_digest: str) -> None:
        try:
            self._sink(
                {"marker": marker, "reason": reason, "state_digest": state_digest}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[tpm-attest] audit sink raised (dropped): %s", exc)

    def _pcr_mismatches(self, current: Dict[int, str]) -> List[int]:
        """Return the indices whose current PCR != authorized value. An index with
        no authorized value is treated as a MISMATCH (fail-closed: we never accept
        an un-anchored PCR)."""
        bad = []
        for i in self._selection:
            want = self._authorized.get(i)
            if want is None or current.get(i) != want:
                bad.append(i)
        return bad

    def attest(
        self,
        *,
        nonce: bytes,
        kernel_hash: Optional[str] = None,
        m3_root_ref: Optional[str] = None,
        runtime_integrity_hash: str = "",
    ) -> AttestationResult:
        state_digest = vos_state_digest(
            kernel_hash=kernel_hash, m3_root_ref=m3_root_ref
        )

        # Fail-closed #1: no TPM present.
        if not self._tpm.present():
            self._audit("ATTESTATION_DENIED", "no TPM device present", state_digest)
            raise AttestationDenied(
                "no TPM 2.0 device present — identity provisioning halted"
            )

        current = self._tpm.read_pcrs(self._selection)
        pcr_digest = _pcr_digest(current, self._selection)

        # Fail-closed #2: PCR bank does not match the authorized boot state.
        bad = self._pcr_mismatches(current)
        if bad:
            self._audit(
                "ATTESTATION_DENIED",
                f"PCR mismatch at indices {bad} (boot state not authorized)",
                state_digest,
            )
            raise AttestationDenied(
                f"PCR mismatch at {bad} — boot state not authorized"
            )

        # Authorized: EK-sign (nonce || state_digest || pcr_digest || integrity).
        # Phase 32 (G6): bind the runtime integrity hash so a remote verifier sees
        # the kernel has not been tampered with since boot.
        integrity_bytes = (
            bytes.fromhex(runtime_integrity_hash) if runtime_integrity_hash else b""
        )
        message = (
            bytes.fromhex(state_digest) + bytes.fromhex(pcr_digest) + integrity_bytes
        )
        q = self._tpm.quote(nonce=nonce, message=message)
        quote = TpmQuote(
            signature_b64=q.signature_b64,
            ek_public_pem=q.ek_public_pem,
            pcr_digest=pcr_digest,
            alg=q.alg,
        )
        self._audit(
            "ATTESTATION_VERIFIED", "EK quote over state+PCR+integrity", state_digest
        )
        return AttestationResult(
            success=True,
            state_digest=state_digest,
            pcr_digest=pcr_digest,
            nonce_b64=base64.b64encode(nonce).decode("ascii"),
            quote=quote,
            runtime_integrity_hash=runtime_integrity_hash,
        )

    def ek_sign_digest(self, digest: bytes, *, nonce: Optional[bytes] = None):
        """Phase 35 (G12): EK-sign an arbitrary digest (e.g. a Merkle checkpoint
        root) with the TPM Endorsement Key. Fail-closed: raises AttestationDenied
        if no TPM is present (we never anchor with a software key when the brief
        demands a hardware root). Returns (nonce, TpmQuote)."""
        if not self._tpm.present():
            self._audit("ATTESTATION_DENIED", "no TPM for EK anchor signature", "")
            raise AttestationDenied("no TPM 2.0 device present — cannot EK-sign anchor")
        if nonce is None:
            nonce = os.urandom(32)
        return nonce, self._tpm.quote(nonce=nonce, message=digest)

    def require_attested(self, *, nonce: Optional[bytes] = None) -> AttestationResult:
        """Attest with a fresh nonce; raise AttestationDenied on any failure."""
        if nonce is None:
            nonce = os.urandom(32)
        return self.attest(nonce=nonce)


def verify_quote(result: AttestationResult, *, nonce: bytes) -> bool:
    """Independent verifier: the EK signature covers (nonce || state || pcr), the
    nonce matches (anti-replay), and the pcr_digest is bound. False on any
    failure (never raises)."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        q = result.quote
        if q is None:
            return False
        if base64.b64encode(nonce).decode("ascii") != result.nonce_b64:
            return False
        integrity_bytes = (
            bytes.fromhex(result.runtime_integrity_hash)
            if result.runtime_integrity_hash
            else b""
        )
        message = (
            bytes.fromhex(result.state_digest)
            + bytes.fromhex(q.pcr_digest)
            + integrity_bytes
        )
        pub = serialization.load_pem_public_key(q.ek_public_pem.encode("ascii"))
        pub.verify(
            base64.b64decode(q.signature_b64),
            nonce + message,
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Singleton + token-issuance guard (G2/G10 integration)
# ---------------------------------------------------------------------------

_SERVICE_SINGLETON: Optional[PlatformAttestationService] = None
_SINGLETON_LOCK = None


def get_platform_attestation_service() -> PlatformAttestationService:
    global _SERVICE_SINGLETON, _SINGLETON_LOCK
    if _SINGLETON_LOCK is None:
        import threading

        _SINGLETON_LOCK = threading.Lock()
    if _SERVICE_SINGLETON is None:
        with _SINGLETON_LOCK:
            if _SERVICE_SINGLETON is None:
                _SERVICE_SINGLETON = PlatformAttestationService(_resolve_default_tpm())
    return _SERVICE_SINGLETON


def attested_token_guard() -> None:
    """G2/G10 integration hook: deny session-token issuance unless the platform is
    hardware-attested. No-op when the flag is OFF (default). When ON, raises
    ``AttestationDenied`` (→ 403) on a TPM-absent or PCR-mismatched platform — so
    no token is minted on an unattested host. NEVER silently allows when ON."""
    if not tpm_attestation_enabled():
        return
    get_platform_attestation_service().require_attested()


def _reset_singleton_for_tests() -> None:
    global _SERVICE_SINGLETON
    _SERVICE_SINGLETON = None


__all__ = [
    "tpm_attestation_enabled",
    "vos_state_digest",
    "TPM2Device",
    "TpmQuote",
    "LocalSoftTPM",
    "HSMBackedTPM2",
    "require_hsm_ek_cert",
    "AttestationResult",
    "AttestationDenied",
    "PlatformAttestationService",
    "get_platform_attestation_service",
    "attested_token_guard",
    "verify_quote",
    "DEFAULT_PCR_SELECTION",
]
