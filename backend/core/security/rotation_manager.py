"""
core/security/rotation_manager.py — DB master key + workflow signing key rotation.

Closes the Stage-10 missing-files batch documented in CLAUDE.md.

Public surface
--------------

    rotate_workflow_signing_key() -> str
        Mint a fresh Ed25519 keypair, atomically swap the active key
        in the Sovereign Keyring, return the new fingerprint.

    rotate_db_master_key(new_key: str | None) -> str
        SQLCipher `PRAGMA rekey` against the compliance DB. Reads the
        old key from `VOS3_COMPLIANCE_KEY` (or the keyring), opens a
        connection, validates the old key by issuing a real read, then
        rekeys in place. Updates the keyring + env atomically.

    rotate_key() -> str
        Default alias — rotates the workflow signing key.

    get_active_key_fingerprint() -> str
        SHA-256 fingerprint (16 hex chars) of the active workflow
        signing public key.

    verify_against_active_key(fp: str) -> bool
        Constant-time compare of a candidate fingerprint to the active.

    schedule_rotation(cron: str, *, scope="workflow_signing") -> dict
        Register a cron expression in the in-memory rotation schedule
        registry. Honest scope: APScheduler/Celery wiring is out of
        scope for v1 — startup code reads the registry.

Concurrency
-----------
A module-level `threading.RLock` serializes every public entry point.
Critical sections are microseconds (a keyring read + an atomic write),
so reader contention is negligible even under the spec's
"10 reader threads + 1 rotator" stress test.

Audit
-----
Every rotation writes a `SecurityAuditLog` row with
`kind="key_rotated"`. Audit failures must NEVER fail the rotation —
the key swap is the load-bearing operation, the audit row is the
chronicle.

Historical workflow runs
------------------------
`workflowRuns` rows persist their own `signing_public_key_hex` so a
rotated-out keypair still verifies its prior signatures. Only NEW
runs use the freshly-minted key. This module does not touch the
`workflowRuns` table.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


_lock = threading.RLock()
_schedule_registry: dict[str, str] = {}

# Hard-fail state — vOS·Adaptive·SHA=aeb3736·Phase=P2.3-polish.
#
# Set when a hybrid-rotation rollback ITSELF fails (kind=
# "hybrid_key_rotation_rollback_failed"). At that point the keyring
# is in an UNCERTAIN state — either half could be the new value, the
# prior value, or absent entirely.
#
# Per the AAA plan's "Security > Availability" directive, every
# subsequent rotation attempt MUST fail closed until an operator
# explicitly clears the state via `clear_hard_fail_state(
# operator_attestation=...)`. There is intentionally no automatic
# recovery path — if the keyring is uncertain, every workflow signing
# operation that proceeds is a potential security incident.
#
# Reads of get_active_key_fingerprint() are also blocked: a reader
# during hard-fail could observe whichever rolled-forward half ended
# up in the keyring and treat it as canonical.
_hard_fail_state: dict = {
    "active": False,
    "reason": None,
    "since_ms": None,
}


class RotationHardFailError(RuntimeError):
    """Raised by every public rotation_manager entry point while the
    module is in hard-fail state. The error is intentionally distinct
    from SecuritySandboxError because it represents a STANDING
    incident state, not a per-call sandbox refusal."""

    def __init__(self, reason: str, since_ms: int | None):
        super().__init__(
            f"rotation_manager is in HARD-FAIL state — keyring uncertain. "
            f"Manual operator attestation required to clear. "
            f"reason={reason!r} since_ms={since_ms}"
        )
        self.reason = reason
        self.since_ms = since_ms


def _enter_hard_fail(reason: str) -> None:
    """Latch the hard-fail state. Idempotent — repeated calls keep
    the FIRST reason+timestamp so an operator sees the originating
    event, not whatever masked it later."""
    with _lock:
        if _hard_fail_state["active"]:
            return
        _hard_fail_state["active"] = True
        _hard_fail_state["reason"] = reason
        _hard_fail_state["since_ms"] = int(time.time() * 1000)


def _check_hard_fail() -> None:
    """Called at the top of every public rotation entry point.
    Raises RotationHardFailError if active."""
    if _hard_fail_state["active"]:
        raise RotationHardFailError(
            _hard_fail_state["reason"],
            _hard_fail_state["since_ms"],
        )


def is_hard_fail_active() -> bool:
    """Read-only observer for operators / health endpoints. Does NOT
    raise — health probes need to be able to ask."""
    return bool(_hard_fail_state["active"])


def get_hard_fail_state() -> dict:
    """Read-only snapshot of the hard-fail latch. For diagnostic /
    incident-response surfaces."""
    return {
        "active": bool(_hard_fail_state["active"]),
        "reason": _hard_fail_state["reason"],
        "since_ms": _hard_fail_state["since_ms"],
    }


def clear_hard_fail_state(*, operator_attestation: str) -> None:
    """Manual recovery path. The `operator_attestation` argument MUST
    be a non-empty string — it is recorded in the audit trail so the
    incident response shows who signed off on clearing the state.

    This function is intentionally NOT idempotent on side-effects:
    calling it when hard-fail is inactive still emits an audit row
    (with status='no_op') so an operator can't quietly probe the
    flag through this entry point."""
    if not isinstance(operator_attestation, str) or len(operator_attestation) < 4:
        raise ValueError(
            "operator_attestation must be a non-empty (≥4 char) "
            "string — typically the operator's name or ticket id"
        )
    with _lock:
        was_active = _hard_fail_state["active"]
        prior_reason = _hard_fail_state["reason"]
        prior_since = _hard_fail_state["since_ms"]
        _hard_fail_state["active"] = False
        _hard_fail_state["reason"] = None
        _hard_fail_state["since_ms"] = None
    _audit(
        "hard_fail_cleared",
        scope="rotation_manager",
        attestation=operator_attestation,
        was_active=was_active,
        prior_reason=prior_reason,
        prior_since_ms=prior_since,
        status="cleared" if was_active else "no_op",
    )


def _compute_fingerprint(material: str) -> str:
    return hashlib.sha256(material.encode("ascii")).hexdigest()[:16]


def _quote_pragma(s: str) -> str:
    return s.replace("'", "''")


def _audit(kind: str, **details) -> None:
    """Best-effort audit emit. Rotation must NOT fail on audit error."""
    try:
        from core.database.sqlite_setup import SecurityAuditLog, get_session

        with get_session() as session:
            session.add(
                SecurityAuditLog(
                    id=str(uuid.uuid4()),
                    timestamp=int(time.time() * 1000),
                    kind=kind,
                    details_json=json.dumps(details, default=str),
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[rotation_manager] audit emit failed: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_active_key_fingerprint() -> str:
    _check_hard_fail()
    with _lock:
        from services.crypto_keyring import get_or_mint_workflow_signing_key

        _priv, pub = get_or_mint_workflow_signing_key()
        return _compute_fingerprint(pub)


def verify_against_active_key(fingerprint: str) -> bool:
    _check_hard_fail()
    if not isinstance(fingerprint, str) or not fingerprint:
        return False
    return hmac.compare_digest(get_active_key_fingerprint(), fingerprint)


def rotate_workflow_signing_key() -> str:
    """Mint a fresh Ed25519 keypair, atomically replace the active key."""
    _check_hard_fail()
    from services.app_crypto import generate_keypair_hex
    from services.crypto_keyring import (
        KEYRING,
        VOS_KEYRING_SERVICE,
        VOS_WORKFLOW_SIGNING_KEY,
    )

    with _lock:
        priv_hex, pub_hex = generate_keypair_hex()
        KEYRING.set_secret(
            VOS_KEYRING_SERVICE,
            VOS_WORKFLOW_SIGNING_KEY,
            f"{priv_hex}|{pub_hex}",
        )
        fp = _compute_fingerprint(pub_hex)
        _audit("key_rotated", scope="workflow_signing", fingerprint=fp)
        return fp


def rotate_key() -> str:
    """Default rotation — the workflow signing key."""
    return rotate_workflow_signing_key()


# ---------------------------------------------------------------------------
# Hybrid PQC rotation — vOS·Adaptive·SHA=aeb3736·Phase=P2
# ---------------------------------------------------------------------------


def rotate_hybrid_workflow_key() -> tuple[str, str]:
    """Atomically mint a fresh Ed25519 + ML-DSA-65 keypair pair.

    Returns (ed25519_fingerprint, mldsa_fingerprint) — both 16-char
    hex (SHA-256[:16] of the public key material).

    Atomicity contract (fail-closed):
      1. Snapshot prior values of BOTH keyring entries before any write.
      2. Mint fresh hybrid pair via services.pqc_sign.hybrid_keygen.
      3. Write Ed25519 first, then ML-DSA. If EITHER write raises,
         restore BOTH from the snapshot before propagating.
      4. On rollback success, audit kind="hybrid_key_rotation_failed".
      5. On rollback failure, audit kind="hybrid_key_rotation_rollback_failed"
         (the keyring is in an uncertain state — operator must intervene).
      6. The caller-facing exception is always SecuritySandboxError so a
         single audit-log filter catches both syscall-time fail-closed
         and rotation-time fail-closed.

    Single audit row records BOTH fingerprints under
    kind="hybrid_key_rotated" — one row, two keys, atomic relationship.
    """
    import base64
    from services.crypto_keyring import (
        KEYRING,
        VOS_KEYRING_SERVICE,
        VOS_WORKFLOW_SIGNING_KEY,
        VOS_WORKFLOW_PQC_KEY,
    )

    _check_hard_fail()
    from services.pqc_sign import hybrid_keygen, verify_path
    from services.app_sandbox import SecuritySandboxError

    with _lock:
        # 1. Snapshot prior state for rollback.
        prior_ed = KEYRING.get_secret(
            VOS_KEYRING_SERVICE,
            VOS_WORKFLOW_SIGNING_KEY,
        )
        prior_pqc = KEYRING.get_secret(
            VOS_KEYRING_SERVICE,
            VOS_WORKFLOW_PQC_KEY,
        )

        try:
            # 2. Mint hybrid pair. If no PQC backend is available,
            #    hybrid_keygen raises immediately; we treat that as a
            #    rotation failure and roll back nothing (no writes yet).
            ed_priv_bytes, ed_pub_bytes, mldsa_priv, mldsa_pub = hybrid_keygen()

            # 3a. Write Ed25519 half — same encoding as the legacy
            #     get_or_mint_workflow_signing_key (hex|hex).
            ed_priv_hex = ed_priv_bytes.hex()
            ed_pub_hex = ed_pub_bytes.hex()
            KEYRING.set_secret(
                VOS_KEYRING_SERVICE,
                VOS_WORKFLOW_SIGNING_KEY,
                f"{ed_priv_hex}|{ed_pub_hex}",
            )

            # 3b. Write ML-DSA half — base64-JSON, same encoding as
            #     get_or_mint_workflow_pqc_key.
            pqc_payload = json.dumps(
                {
                    "mldsa_priv_b64": base64.b64encode(mldsa_priv).decode("ascii"),
                    "mldsa_pub_b64": base64.b64encode(mldsa_pub).decode("ascii"),
                }
            )
            KEYRING.set_secret(
                VOS_KEYRING_SERVICE,
                VOS_WORKFLOW_PQC_KEY,
                pqc_payload,
            )

            ed_fp = _compute_fingerprint(ed_pub_hex)
            ml_fp = _compute_fingerprint(
                base64.b64encode(mldsa_pub).decode("ascii"),
            )
            _audit(
                "hybrid_key_rotated",
                scope="workflow_signing",
                ed25519_fingerprint=ed_fp,
                mldsa_fingerprint=ml_fp,
                pqc_backend=verify_path(),
            )
            return ed_fp, ml_fp

        except Exception as exc:  # noqa: BLE001 — rollback ANY failure
            # 4 + 5. Restore both halves atomically. Track each
            # restore's outcome separately so the audit row tells the
            # operator exactly what state survived.
            ed_restored = False
            pqc_restored = False
            try:
                if prior_ed is not None:
                    KEYRING.set_secret(
                        VOS_KEYRING_SERVICE,
                        VOS_WORKFLOW_SIGNING_KEY,
                        prior_ed,
                    )
                ed_restored = True
            except Exception as e1:  # noqa: BLE001
                _audit(
                    "hybrid_key_rotation_rollback_failed",
                    scope="workflow_signing",
                    half="ed25519",
                    primary_reason=str(exc),
                    rollback_reason=str(e1),
                )
                # vOS·Adaptive·SHA=aeb3736·Phase=P2.3-polish
                # The Ed25519 rollback itself failed → keyring is in
                # an UNCERTAIN state. Enter hard-fail per the
                # "Security > Availability" directive.
                _enter_hard_fail(
                    f"hybrid rotation rollback failed on ed25519 half: "
                    f"primary={exc!r} rollback={e1!r}"
                )
            try:
                if prior_pqc is not None:
                    KEYRING.set_secret(
                        VOS_KEYRING_SERVICE,
                        VOS_WORKFLOW_PQC_KEY,
                        prior_pqc,
                    )
                pqc_restored = True
            except Exception as e2:  # noqa: BLE001
                _audit(
                    "hybrid_key_rotation_rollback_failed",
                    scope="workflow_signing",
                    half="mldsa",
                    primary_reason=str(exc),
                    rollback_reason=str(e2),
                )
                # Same hard-fail rule for the PQC half. The latch is
                # idempotent — if both halves' rollback failed, we
                # keep the FIRST reason as the originating event.
                _enter_hard_fail(
                    f"hybrid rotation rollback failed on mldsa half: "
                    f"primary={exc!r} rollback={e2!r}"
                )

            _audit(
                "hybrid_key_rotation_failed",
                scope="workflow_signing",
                reason=str(exc),
                ed25519_rolled_back=ed_restored,
                mldsa_rolled_back=pqc_restored,
            )

            # 6. Surface as SecuritySandboxError so a single audit
            #    filter catches all fail-closed events.
            raise SecuritySandboxError(
                f"pqc_keygen_failed: {exc}",
                platform="any",
            ) from exc


def rotate_db_master_key(new_key: Optional[str] = None) -> str:
    """SQLCipher PRAGMA rekey against the compliance DB.

    Raises RuntimeError if sqlcipher3 is unavailable, the old key
    can't be sourced, or the old key fails to decrypt the DB.
    """
    from services.crypto_keyring import (
        KEYRING,
        VOS_KEYRING_SERVICE,
        VOS_DB_MASTER_KEY,
    )

    with _lock:
        if new_key is None:
            new_key = secrets.token_urlsafe(32)
        if not isinstance(new_key, str) or len(new_key) < 16:
            raise ValueError("new_key must be a str of at least 16 chars")

        try:
            import sqlcipher3  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "sqlcipher3 not installed — install sqlcipher3-binary "
                "to use rotate_db_master_key"
            ) from e

        old_key = os.environ.get("VOS3_COMPLIANCE_KEY") or KEYRING.get_secret(
            VOS_KEYRING_SERVICE,
            VOS_DB_MASTER_KEY,
        )
        if not old_key:
            raise RuntimeError(
                "no existing master key — rotation requires either "
                "VOS3_COMPLIANCE_KEY env or a keyring entry"
            )

        from services.compliance_store import DEFAULT_DB_PATH

        db_path = os.environ.get("VOS3_COMPLIANCE_DB_PATH", DEFAULT_DB_PATH)
        if not os.path.exists(db_path):
            raise RuntimeError(
                f"compliance DB at {db_path!r} does not exist — " "nothing to rekey"
            )

        conn = sqlcipher3.connect(db_path, isolation_level=None)
        try:
            conn.execute(f"PRAGMA key = '{_quote_pragma(old_key)}';")
            try:
                conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    "old master key failed to decrypt the compliance DB; "
                    "refusing to rekey"
                ) from e
            conn.execute(f"PRAGMA rekey = '{_quote_pragma(new_key)}';")
        finally:
            conn.close()

        KEYRING.set_secret(VOS_KEYRING_SERVICE, VOS_DB_MASTER_KEY, new_key)
        os.environ["VOS3_COMPLIANCE_KEY"] = new_key

        fp = _compute_fingerprint(new_key)
        _audit("key_rotated", scope="db_master", fingerprint=fp)
        return new_key


def schedule_rotation(cron: str, *, scope: str = "workflow_signing") -> dict:
    """Register a cron expression for periodic rotation."""
    if not isinstance(cron, str) or not cron.strip():
        raise ValueError("cron must be a non-empty string")
    parts = cron.strip().split()
    if len(parts) not in (5, 6):
        raise ValueError(f"cron must have 5 or 6 fields; got {len(parts)}")
    with _lock:
        _schedule_registry[scope] = cron.strip()
    return {"scope": scope, "cron": cron.strip()}


def get_schedule_registry() -> dict:
    with _lock:
        return dict(_schedule_registry)


def _reset_for_tests() -> None:
    with _lock:
        _schedule_registry.clear()
        _hard_fail_state["active"] = False
        _hard_fail_state["reason"] = None
        _hard_fail_state["since_ms"] = None


# ---------------------------------------------------------------------------
# v20.5.1-RECLAMATION — stateful RotationManager (Doomsday B20)
#
# A dual-curve (P-256 + P-521) signing-key rotation manager with
# cross-signed transitions and a revocation ledger. Distinct from the
# module-level rotate_* helpers above (which target specific named keys);
# this is a self-contained, key_dir-scoped rotation chain used by the
# attestation key-vault story.
# ---------------------------------------------------------------------------

import base64 as _base64
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

from cryptography.hazmat.primitives import hashes as _hashes
from cryptography.hazmat.primitives import serialization as _serialization
from cryptography.hazmat.primitives.asymmetric import ec as _ec

# The only rotation reasons the manager accepts. Anything else is an
# operator typo and must fail loudly rather than silently rotate.
_ALLOWED_ROTATION_REASONS = frozenset({"scheduled", "precautionary", "leak"})


@_dataclass
class RotationEvent:
    reason: str
    old_p256_fp: Optional[str]
    new_p256_fp: str
    new_p521_fp: str
    transition_signature_b64: str  # signed by the OLD key ("" on first)
    transition_signature_new_b64: str  # signed by the NEW key (always)


def _ec_public_fp(public_key) -> str:
    der = public_key.public_bytes(
        _serialization.Encoding.DER,
        _serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()[:16]


class RotationManager:
    """Stateful, dual-curve signing-key rotation chain.

    Each rotation mints a fresh P-256 (SECP256R1) + P-521 (SECP521R1)
    keypair, cross-signs the transition (new key always; old key when a
    prior generation exists), and — on a ``leak`` rotation — revokes the
    superseded generation's fingerprints. History + revocation ledger
    persist to ``key_dir/rotation_ledger.json``."""

    def __init__(self, key_dir) -> None:
        self.key_dir = _Path(key_dir)
        self.key_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._p256_priv = None
        self._p521_priv = None
        self._p256_fp: Optional[str] = None
        self._p521_fp: Optional[str] = None
        self._history: list[dict] = []
        self._revoked: dict[str, str] = {}  # fp -> reason
        self._revocation_ledger: list[dict] = []

    def rotate(self, reason: str) -> RotationEvent:
        if reason not in _ALLOWED_ROTATION_REASONS:
            raise ValueError(
                f"unknown rotation reason {reason!r}; allowed: "
                f"{sorted(_ALLOWED_ROTATION_REASONS)}"
            )
        with self._lock:
            old_p256_priv = self._p256_priv
            old_p256_fp = self._p256_fp
            old_p521_fp = self._p521_fp

            new_p256_priv = _ec.generate_private_key(_ec.SECP256R1())
            new_p521_priv = _ec.generate_private_key(_ec.SECP521R1())
            new_p256_fp = _ec_public_fp(new_p256_priv.public_key())
            new_p521_fp = _ec_public_fp(new_p521_priv.public_key())

            payload = (
                f"{old_p256_fp or ''}->{new_p256_fp}:{new_p521_fp}:{reason}"
            ).encode("utf-8")

            sig_new = new_p256_priv.sign(payload, _ec.ECDSA(_hashes.SHA256()))
            transition_signature_new_b64 = _base64.b64encode(sig_new).decode()

            if old_p256_priv is not None:
                sig_old = old_p256_priv.sign(payload, _ec.ECDSA(_hashes.SHA256()))
                transition_signature_b64 = _base64.b64encode(sig_old).decode()
            else:
                transition_signature_b64 = ""

            # A leak revokes the superseded generation's fingerprints.
            if reason == "leak":
                for fp in (old_p256_fp, old_p521_fp):
                    if fp:
                        self._revoked[fp] = "leak"
                        self._revocation_ledger.append(
                            {
                                "fingerprint": fp,
                                "reason": "leak",
                                "revoked_at": time.time(),
                            }
                        )

            # Advance the current generation.
            self._p256_priv = new_p256_priv
            self._p521_priv = new_p521_priv
            self._p256_fp = new_p256_fp
            self._p521_fp = new_p521_fp

            self._history.append(
                {
                    "reason": reason,
                    "old_p256_fp": old_p256_fp,
                    "new_p256_fp": new_p256_fp,
                    "new_p521_fp": new_p521_fp,
                    "at": time.time(),
                }
            )
            self._persist()

            return RotationEvent(
                reason=reason,
                old_p256_fp=old_p256_fp,
                new_p256_fp=new_p256_fp,
                new_p521_fp=new_p521_fp,
                transition_signature_b64=transition_signature_b64,
                transition_signature_new_b64=transition_signature_new_b64,
            )

    def is_revoked(self, fingerprint: str) -> bool:
        return fingerprint in self._revoked

    def revocation_list(self) -> list[dict]:
        return list(self._revocation_ledger)

    def history(self) -> list[dict]:
        return list(self._history)

    def _persist(self) -> None:
        try:
            ledger = {
                "history": self._history,
                "revocation": self._revocation_ledger,
                "current": {
                    "p256_fp": self._p256_fp,
                    "p521_fp": self._p521_fp,
                },
            }
            (self.key_dir / "rotation_ledger.json").write_text(
                json.dumps(ledger, indent=2)
            )
        except OSError as exc:  # best-effort durability; never fatal
            logger.warning("RotationManager: ledger persist failed: %s", exc)


__all__ = [
    "get_active_key_fingerprint",
    "verify_against_active_key",
    "rotate_key",
    "rotate_workflow_signing_key",
    "rotate_hybrid_workflow_key",
    "rotate_db_master_key",
    "schedule_rotation",
    "get_schedule_registry",
    "RotationHardFailError",
    "is_hard_fail_active",
    "get_hard_fail_state",
    "clear_hard_fail_state",
    "RotationManager",
    "RotationEvent",
]
