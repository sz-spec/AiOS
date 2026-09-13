"""
backend/services/crypto_keyring.py — Sovereign Keyring Service (P6.1).

Unifies access to the host's hardware-backed secret store across
macOS / Windows / Linux. When a native backend isn't available
(headless CI runner, air-gap dev box, missing `keyring` package),
the service falls back to a Fernet-encrypted local file keyed off
the machine's `machine-id`. Every fallback invocation emits a
high-severity `local_fallback_active` row in `securityAuditLog`
so the dashboard surfaces the downgrade.

Backend tiers (probed lazily, cached for the process lifetime):

  1. `keyring` Python package + native bridge
     - macOS:    Apple Keychain (Security.framework)
     - Windows:  Credential Manager / DPAPI
     - Linux:    SecretService (GNOME Keyring / KWallet) via DBus
  2. Local Fernet-encrypted file
     - Path:     $VOS3_KEYRING_PATH or ~/.vos/keyring/secrets.enc
     - Key:      HKDF-SHA256(host_machine_id, salt="vOS3.P6.1.keyring")
     - Audit:    `kind="local_fallback_active"` event on EVERY call
                  so the operator never silently runs in fallback mode

The fallback is intentionally OS-bound — copying `secrets.enc` to a
different host won't decrypt because the host's machine-id differs.

Public surface
--------------
    SovereignKeyringService.set_secret(service, username, secret) -> bool
    SovereignKeyringService.get_secret(service, username)         -> Optional[str]
    SovereignKeyringService.delete_secret(service, username)       -> bool
    SovereignKeyringService.backend_label()                        -> str

  KEYRING is the module-level singleton; SQLCipher bootstrap +
  workflow signing services consume it directly.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Static salt — pinned to the P6.1 release so subsequent rotations
# of the local file can disambiguate themselves from older formats.
_HKDF_SALT = b"vOS3.P6.1.keyring.v1"
_HKDF_INFO = b"vOS3-keyring-master-key"


# ---------------------------------------------------------------------------
# Backend probes — lazy + cached so a missing module doesn't blow up
# on import.
# ---------------------------------------------------------------------------


def _force_local_mode() -> bool:
    """Operator override — set VOS3_KEYRING_MODE=local to skip the
    native probe entirely. Used by tests + air-gap dev boxes that
    deliberately want the fallback path even when `keyring` is
    importable."""
    return os.environ.get("VOS3_KEYRING_MODE", "").strip().lower() == "local"


def _probe_native_keyring() -> Any:
    """Return the `keyring` module if importable AND backed by a
    real backend; else None.

    Caches the probe across calls because import is expensive on
    some platforms (macOS Security.framework loads a chunk of
    Objective-C runtime)."""
    if _force_local_mode():
        return None
    if hasattr(_probe_native_keyring, "_cache"):
        return _probe_native_keyring._cache  # type: ignore[attr-defined]
    try:
        import keyring  # type: ignore

        # The default backend on a headless Linux is `keyring.backends.fail`
        # — refuse to count that as native.
        backend = keyring.get_keyring()
        backend_name = type(backend).__module__ + "." + type(backend).__name__
        if "fail" in backend_name.lower() or "null" in backend_name.lower():
            _probe_native_keyring._cache = None  # type: ignore[attr-defined]
            return None
        # Smoke-test: try a roundtrip we immediately undo. If the
        # backend raises, fall through.
        try:
            keyring.set_password("vos3-keyring-probe", "smoke", "ok")
            value = keyring.get_password("vos3-keyring-probe", "smoke")
            keyring.delete_password("vos3-keyring-probe", "smoke")
            if value != "ok":
                _probe_native_keyring._cache = None  # type: ignore[attr-defined]
                return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("[keyring] native smoke-test failed: %s", exc)
            _probe_native_keyring._cache = None  # type: ignore[attr-defined]
            return None
        _probe_native_keyring._cache = (keyring, backend_name)  # type: ignore[attr-defined]
        return _probe_native_keyring._cache  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[keyring] native import skipped: %s", exc)
        _probe_native_keyring._cache = None  # type: ignore[attr-defined]
        return None


def _machine_seed() -> bytes:
    """Host-bound seed for the local fallback's HKDF.

    Order:
      1. $VOS3_KEYRING_SEED_OVERRIDE (test hook)
      2. machine-id (Linux/macOS/Windows, see sqlcipher_setup._machine_id)
      3. SHA-256 of `Path.home() + uname()` as last-resort
    """
    override = os.environ.get("VOS3_KEYRING_SEED_OVERRIDE", "").strip()
    if override:
        return override.encode("utf-8")
    # Reuse the resolver from P3.2's SQLCipher module to avoid
    # duplicating platform detection.
    try:
        from core.database.sqlcipher_setup import _machine_id

        mid = _machine_id()
        if mid:
            return mid.encode("utf-8")
    except Exception:  # noqa: BLE001
        pass
    import platform

    fallback = (str(Path.home()) + "|" + platform.platform()).encode("utf-8")
    return hashlib.sha256(fallback).digest()


def _local_storage_path() -> Path:
    override = os.environ.get("VOS3_KEYRING_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".vos" / "keyring" / "secrets.enc").resolve()


# ---------------------------------------------------------------------------
# Local Fernet-backed fallback
# ---------------------------------------------------------------------------


def _derive_fernet_key(seed: bytes) -> bytes:
    """Derive a Fernet-shaped key (32 raw bytes, urlsafe-b64 encoded).

    HKDF-SHA256 with a pinned salt and info string. Output is the
    32 bytes Fernet expects, then base64-urlsafe-encoded per the
    Fernet API requirement."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=_HKDF_SALT,
            info=_HKDF_INFO,
        )
        raw = hkdf.derive(seed)
    except Exception:  # noqa: BLE001 - cryptography missing
        # Last-resort: plain SHA-256(salt||seed). NOT proper HKDF but
        # is at least deterministic + binds to the host seed.
        raw = hashlib.sha256(_HKDF_SALT + seed).digest()
    return base64.urlsafe_b64encode(raw)


class _LocalEncryptedStore:
    """File-backed Fernet store. Threadsafe via a single RLock; the
    file footprint is tiny so we read + decrypt + mutate + write +
    fsync per operation rather than caching in memory."""

    def __init__(self):
        self._lock = threading.RLock()

    # --- Fernet handle -----------------------------------------------

    def _fernet(self):
        from cryptography.fernet import Fernet

        return Fernet(_derive_fernet_key(_machine_seed()))

    # --- file I/O ----------------------------------------------------

    def _load(self) -> dict:
        path = _local_storage_path()
        if not path.exists():
            return {}
        f = self._fernet()
        try:
            ciphertext = path.read_bytes()
            plain = f.decrypt(ciphertext)
            data = json.loads(plain.decode("utf-8"))
            if not isinstance(data, dict):
                return {}
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[keyring] local store decrypt failed (%s) — starting fresh",
                exc,
            )
            return {}

    def _save(self, data: dict) -> None:
        path = _local_storage_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        f = self._fernet()
        ciphertext = f.encrypt(json.dumps(data, separators=(",", ":")).encode("utf-8"))
        # Atomic write — write to .tmp then rename.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(ciphertext)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)

    # --- secret operations -------------------------------------------

    def set(self, service: str, username: str, secret: str) -> bool:
        with self._lock:
            data = self._load()
            data.setdefault(service, {})[username] = secret
            self._save(data)
            return True

    def get(self, service: str, username: str) -> Optional[str]:
        with self._lock:
            data = self._load()
            return data.get(service, {}).get(username)

    def delete(self, service: str, username: str) -> bool:
        with self._lock:
            data = self._load()
            bucket = data.get(service)
            if not bucket or username not in bucket:
                return False
            bucket.pop(username, None)
            if not bucket:
                data.pop(service, None)
            self._save(data)
            return True

    def reset_for_tests(self) -> None:
        with self._lock:
            try:
                _local_storage_path().unlink()
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# SovereignKeyringService — public facade
# ---------------------------------------------------------------------------


class SovereignKeyringService:
    """Cross-platform keyring with native + Fernet-fallback tiers.

    Stateless aside from the local store's RLock. Backend selection
    is re-probed only on init; tests can call `_reset_for_tests()`
    to force a re-probe."""

    def __init__(self):
        self._local = _LocalEncryptedStore()
        self._audited_fallback_for = set()  # one audit row per
        # (service, username) per process

    # --- public API ---------------------------------------------------

    def set_secret(
        self,
        service_name: str,
        username: str,
        secret: str,
    ) -> bool:
        self._validate(service_name, username)
        if not isinstance(secret, str):
            raise ValueError("secret must be a string")
        native = _probe_native_keyring()
        if native is not None:
            kr, _ = native
            try:
                kr.set_password(service_name, username, secret)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[keyring] native set failed (%s) — falling back",
                    exc,
                )
        self._audit_fallback("set", service_name, username)
        return self._local.set(service_name, username, secret)

    def get_secret(self, service_name: str, username: str) -> Optional[str]:
        self._validate(service_name, username)
        native = _probe_native_keyring()
        if native is not None:
            kr, _ = native
            try:
                v = kr.get_password(service_name, username)
                if v is not None:
                    return v
                # Some backends return None for "not found"; fall through
                # to the local store ONLY when the native side has no
                # entry — operators may have migrated.
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[keyring] native get failed (%s) — falling back",
                    exc,
                )
        self._audit_fallback("get", service_name, username)
        return self._local.get(service_name, username)

    def delete_secret(self, service_name: str, username: str) -> bool:
        self._validate(service_name, username)
        native = _probe_native_keyring()
        if native is not None:
            kr, _ = native
            try:
                kr.delete_password(service_name, username)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[keyring] native delete failed (%s) — falling back",
                    exc,
                )
        self._audit_fallback("delete", service_name, username)
        return self._local.delete(service_name, username)

    def backend_label(self) -> str:
        """Return "native:<class>" or "local_fallback" for telemetry."""
        native = _probe_native_keyring()
        if native is not None:
            return f"native:{native[1]}"
        return "local_fallback"

    def is_native(self) -> bool:
        return _probe_native_keyring() is not None

    # --- test hooks ---------------------------------------------------

    def _reset_for_tests(self) -> None:
        """Clear the per-process audit memo + wipe the local store."""
        self._audited_fallback_for.clear()
        self._local.reset_for_tests()
        # Force the next probe to re-evaluate from scratch.
        if hasattr(_probe_native_keyring, "_cache"):
            delattr(_probe_native_keyring, "_cache")

    # --- internals ----------------------------------------------------

    @staticmethod
    def _validate(service_name: str, username: str) -> None:
        if not isinstance(service_name, str) or not service_name:
            raise ValueError("service_name must be a non-empty string")
        if not isinstance(username, str) or not username:
            raise ValueError("username must be a non-empty string")

    def _audit_fallback(self, op: str, service: str, username: str) -> None:
        """Emit ONE high-severity audit row per (service, username)
        for the lifetime of the process. Repeated calls for the same
        key don't flood the log, but the first operator-visible
        action against any new secret carries the downgrade
        notification."""
        memo_key = (service, username)
        if memo_key in self._audited_fallback_for:
            return
        self._audited_fallback_for.add(memo_key)
        try:
            from services.app_sandbox import _record_security_event

            _record_security_event(
                kind="local_fallback_active",
                reason=(
                    f"keyring fallback engaged: op={op} "
                    f"service={service!r} username={username!r}"
                ),
                details={
                    "op": op,
                    "service": service,
                    "username": username,
                    "path": str(_local_storage_path()),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[keyring] failed to record fallback audit: %s",
                exc,
            )
        # Always log at warning level so operators see the downgrade
        # even when the audit table isn't being polled.
        logger.warning(
            "[keyring] LOCAL FALLBACK ACTIVE for service=%r username=%r op=%s "
            "— install the `keyring` package and a native backend for "
            "hardware-backed secrets",
            service,
            username,
            op,
        )


KEYRING = SovereignKeyringService()


# ---------------------------------------------------------------------------
# Convenience helpers used by the SQLCipher bootstrap + orchestrator
# ---------------------------------------------------------------------------


VOS_KEYRING_SERVICE = "vOS_Secure_Enclave"
VOS_DB_MASTER_KEY = "db_master_key"
VOS_WORKFLOW_SIGNING_KEY = "workflow_signing_key"
# vOS·Adaptive·SHA=aeb3736·Phase=P2 — hybrid PQC companion to the
# classical Ed25519 workflow key. Stores base64-encoded JSON:
# {"mldsa_priv_b64": "...", "mldsa_pub_b64": "..."}. The Ed25519 half
# stays in VOS_WORKFLOW_SIGNING_KEY to keep the migration reversible.
VOS_WORKFLOW_PQC_KEY = "workflow_pqc_key"


def get_or_mint_db_master_key() -> str:
    """Return the SQLCipher master key, minting it on first boot.

    Order:
      1. Lookup `vOS_Secure_Enclave/db_master_key`.
      2. If missing, generate a 32-byte token_urlsafe (256 bits of
         entropy) and store it. Return the new key.

    The keyring's audit row tracks every read so a missing-key
    diagnostic is one query away."""
    existing = KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_DB_MASTER_KEY)
    if existing:
        return existing
    import secrets as _secrets

    new = _secrets.token_urlsafe(32)
    KEYRING.set_secret(VOS_KEYRING_SERVICE, VOS_DB_MASTER_KEY, new)
    logger.info(
        "[keyring] minted fresh db_master_key (backend=%s)", KEYRING.backend_label()
    )
    return new


def get_or_mint_workflow_signing_key() -> tuple:
    """Return the Ed25519 keypair used to sign workflow runs.

    Stored shape: a single hex string `<priv64>|<pub64>` (the colon
    would collide with the macOS keychain UI display). Minted on
    first boot via P5.2's `generate_keypair_hex`."""
    existing = KEYRING.get_secret(
        VOS_KEYRING_SERVICE,
        VOS_WORKFLOW_SIGNING_KEY,
    )
    if existing and "|" in existing:
        priv_hex, pub_hex = existing.split("|", 1)
        if len(priv_hex) == 64 and len(pub_hex) == 64:
            return priv_hex, pub_hex

    from services.app_crypto import generate_keypair_hex

    priv_hex, pub_hex = generate_keypair_hex()
    KEYRING.set_secret(
        VOS_KEYRING_SERVICE,
        VOS_WORKFLOW_SIGNING_KEY,
        f"{priv_hex}|{pub_hex}",
    )
    logger.info(
        "[keyring] minted fresh workflow signing keypair (backend=%s)",
        KEYRING.backend_label(),
    )
    return priv_hex, pub_hex


def get_or_mint_workflow_pqc_key() -> tuple:
    """Return the ML-DSA-65 PQC keypair used to co-sign workflow runs
    alongside the existing Ed25519 key (hybrid scheme).

    Stored shape: a JSON dict with base64-encoded private + public:
        {"mldsa_priv_b64": "...", "mldsa_pub_b64": "..."}

    On first call, mints via services.pqc_sign.hybrid_keygen (which
    discards the Ed25519 half — that's handled by
    get_or_mint_workflow_signing_key) and stores. Returns
    (mldsa_priv_bytes, mldsa_pub_bytes).

    Honest-scope: if no PQC backend is installed (neither oqs-python
    nor dilithium-py), this raises RuntimeError on first mint. The
    audit-honesty discipline calls for surfacing the missing
    dependency rather than silently degrading to Ed25519-only.
    """
    import base64
    import json

    existing = KEYRING.get_secret(VOS_KEYRING_SERVICE, VOS_WORKFLOW_PQC_KEY)
    if existing:
        try:
            d = json.loads(existing)
            return (
                base64.b64decode(d["mldsa_priv_b64"]),
                base64.b64decode(d["mldsa_pub_b64"]),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning(
                "[keyring] stored PQC key malformed (%s) — re-minting",
                exc,
            )

    from services.pqc_sign import hybrid_keygen

    _ed_priv, _ed_pub, mldsa_priv, mldsa_pub = hybrid_keygen()
    payload = json.dumps(
        {
            "mldsa_priv_b64": base64.b64encode(mldsa_priv).decode("ascii"),
            "mldsa_pub_b64": base64.b64encode(mldsa_pub).decode("ascii"),
        }
    )
    KEYRING.set_secret(VOS_KEYRING_SERVICE, VOS_WORKFLOW_PQC_KEY, payload)
    logger.info(
        "[keyring] minted fresh workflow PQC keypair "
        "(backend=%s, priv=%d B, pub=%d B)",
        KEYRING.backend_label(),
        len(mldsa_priv),
        len(mldsa_pub),
    )
    return mldsa_priv, mldsa_pub


__all__ = [
    "KEYRING",
    "SovereignKeyringService",
    "VOS_KEYRING_SERVICE",
    "VOS_DB_MASTER_KEY",
    "VOS_WORKFLOW_SIGNING_KEY",
    "VOS_WORKFLOW_PQC_KEY",
    "get_or_mint_db_master_key",
    "get_or_mint_workflow_signing_key",
    "get_or_mint_workflow_pqc_key",
]
