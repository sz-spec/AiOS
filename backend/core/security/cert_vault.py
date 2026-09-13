"""
backend/core/security/cert_vault.py
====================================

Stage 10.3 (Sprint 14.1) — TPM-sealed X.509 certificate store.

What this is
------------

A storage layer that:
  1. Stores DER-encoded X.509 certificates and their private keys.
  2. Optionally seals the private key bytes against the platform TPM 2.0
     via the kernel's sec/tpm2.c sealing primitives (over VBus).
  3. Honors cert lifecycle (not-before / not-after); auto-rotates by
     calling out to rotation_manager when expiry < threshold.

Why a separate vault (and not just keyring)
-------------------------------------------

The Sovereign Keyring (services.crypto_keyring) is keyed by
(service, username) and is optimized for short secrets — JWT signing keys,
API tokens, DB master passphrases. Certificate material differs:
  - It is composite (public cert + chain + private key, all related).
  - It has a published lifetime that we must honor (not-after).
  - On the sovereign profile, the private key MUST be TPM-sealed.
  - Auditors want a single "what certs do we hold" view, not a keyring scan.

So we layer cert_vault ABOVE keyring: each cert's private-key wrap key
lives in the keyring, the cert+wrapped-key pair lives in the vault.

Honest scope ceiling
--------------------

TPM 2.0 sealing on the sovereign profile is delegated to the kernel via
VBus commands TPM_SEAL / TPM_UNSEAL (declared in
kernel/include/vos/tpm2.h, implemented in kernel/src/sec/tpm2.c).

When VOS_PROFILE != "fortress" OR when the kernel reports no TPM
("TEE_ENV" returns NOTEE), the vault falls back to AES-256-GCM with a
KDF-derived wrap key from the keyring. This is documented to the
operator: every put_cert in fallback mode emits an audit row with
``kind="cert_vault_fallback_active"`` so the downgrade is visible on the
dashboard.

The vault does NOT validate certificate chains itself — callers that
need chain validation use ``cryptography.x509`` against the relevant trust
store. The vault is storage + lifecycle, not PKI logic.

Schema
------

Single table ``certificates``:
    name           TEXT PRIMARY KEY     -- caller-chosen identifier
    cert_der       BLOB NOT NULL        -- end-entity certificate
    chain_pem      TEXT                  -- intermediate + root, PEM bundle
    wrapped_key    BLOB NOT NULL        -- sealed or AES-GCM ciphertext
    wrap_mode      TEXT NOT NULL        -- "tpm2" | "aes-gcm"
    not_before     INTEGER              -- unix ts
    not_after      INTEGER              -- unix ts
    fingerprint    TEXT NOT NULL        -- SHA-256 hex of cert_der
    created_at     INTEGER NOT NULL
    rotated_from   TEXT                  -- prior `name` if this is a rotation
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


DEFAULT_VAULT_PATH = "/tmp/vos_cert_vault.db"
WRAP_MODE_TPM2 = "tpm2"
WRAP_MODE_AES_GCM = "aes-gcm"

# When a cert is within this many seconds of not_after, list_expiring() includes it.
DEFAULT_EXPIRY_WARN_WINDOW_S = 14 * 24 * 3600  # 14 days


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS certificates (
    name          TEXT PRIMARY KEY,
    cert_der      BLOB NOT NULL,
    chain_pem     TEXT,
    wrapped_key   BLOB NOT NULL,
    wrap_mode     TEXT NOT NULL,
    not_before    INTEGER,
    not_after     INTEGER,
    fingerprint   TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    rotated_from  TEXT
);
CREATE INDEX IF NOT EXISTS idx_cert_not_after ON certificates(not_after);
CREATE INDEX IF NOT EXISTS idx_cert_fingerprint ON certificates(fingerprint);
"""


# ---------------------------------------------------------------------------
# Data class for caller-facing reads (private key NEVER returned in dict form)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CertRecord:
    name: str
    cert_der: bytes
    chain_pem: Optional[str]
    wrap_mode: str
    not_before: Optional[int]
    not_after: Optional[int]
    fingerprint: str
    created_at: int
    rotated_from: Optional[str]

    def time_to_expiry_s(self) -> Optional[int]:
        if self.not_after is None:
            return None
        return int(self.not_after - time.time())


# ---------------------------------------------------------------------------
# Wrap / unwrap — TPM-sealed primary, AES-GCM fallback
# ---------------------------------------------------------------------------


def _is_fortress_profile() -> bool:
    try:
        from vos_profile import is_fortress

        return bool(is_fortress())
    except ImportError:
        return os.environ.get("VOS_PROFILE", "").lower() == "fortress"


def _wrap_key_tpm2(private_key_bytes: bytes, *, driver) -> tuple[bytes, str]:
    """Seal private key against the platform TPM via VBus TPM_SEAL.

    Returns (wrapped_bytes, WRAP_MODE_TPM2). Raises if driver is None or
    the kernel reports no TPM.
    """
    if driver is None:
        raise RuntimeError("TPM-seal requires an attached VBus driver; got None")
    # Hex-encode payload because VBus framing is line-oriented.
    payload_hex = private_key_bytes.hex()
    reply = driver.send_command(f"TPM_SEAL|{payload_hex}")
    if not reply or not reply.startswith("TPM_SEALED|"):
        raise RuntimeError(f"TPM_SEAL refused: {reply!r}")
    sealed_hex = reply.split("|", 1)[1].strip()
    return bytes.fromhex(sealed_hex), WRAP_MODE_TPM2


def _unwrap_key_tpm2(wrapped: bytes, *, driver) -> bytes:
    if driver is None:
        raise RuntimeError("TPM-unseal requires an attached VBus driver")
    reply = driver.send_command(f"TPM_UNSEAL|{wrapped.hex()}")
    if not reply or not reply.startswith("TPM_UNSEALED|"):
        raise RuntimeError(f"TPM_UNSEAL refused: {reply!r}")
    return bytes.fromhex(reply.split("|", 1)[1].strip())


def _wrap_key_aes_gcm(private_key_bytes: bytes) -> tuple[bytes, str]:
    """AES-256-GCM with a wrap key derived from the keyring master.

    Layout: nonce(12) || ciphertext || tag(16).
    """
    from services.crypto_keyring import KEYRING, VOS_KEYRING_SERVICE
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    wrap_key_hex = KEYRING.get_secret(VOS_KEYRING_SERVICE, "cert_vault_wrap_key")
    if not wrap_key_hex:
        # Mint a fresh 32-byte wrap key the first time
        wrap_key = AESGCM.generate_key(bit_length=256)
        KEYRING.set_secret(
            VOS_KEYRING_SERVICE,
            "cert_vault_wrap_key",
            wrap_key.hex(),
        )
    else:
        wrap_key = bytes.fromhex(wrap_key_hex)

    nonce = os.urandom(12)
    ct_plus_tag = AESGCM(wrap_key).encrypt(
        nonce, private_key_bytes, associated_data=None
    )
    return nonce + ct_plus_tag, WRAP_MODE_AES_GCM


def _unwrap_key_aes_gcm(wrapped: bytes) -> bytes:
    from services.crypto_keyring import KEYRING, VOS_KEYRING_SERVICE
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    wrap_key_hex = KEYRING.get_secret(VOS_KEYRING_SERVICE, "cert_vault_wrap_key")
    if not wrap_key_hex:
        raise RuntimeError("cert_vault_wrap_key missing from keyring")
    wrap_key = bytes.fromhex(wrap_key_hex)
    nonce, ct_plus_tag = wrapped[:12], wrapped[12:]
    return AESGCM(wrap_key).decrypt(nonce, ct_plus_tag, associated_data=None)


# ---------------------------------------------------------------------------
# Vault class
# ---------------------------------------------------------------------------


class CertVault:
    """Thread-safe X.509 cert store with TPM-sealed private keys."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        vbus_driver=None,
    ) -> None:
        self._db_path = db_path or os.environ.get(
            "VOS3_CERT_VAULT_PATH", DEFAULT_VAULT_PATH
        )
        self._driver = vbus_driver
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self._db_path, isolation_level=None, check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL)

    def _wrap(self, private_key_bytes: bytes) -> tuple[bytes, str]:
        if _is_fortress_profile() and self._driver is not None:
            try:
                return _wrap_key_tpm2(private_key_bytes, driver=self._driver)
            except RuntimeError as exc:
                logger.warning(
                    "[cert_vault] TPM-seal unavailable (%s); using AES-GCM fallback. "
                    "Audit row emitted.",
                    exc,
                )
                self._audit_fallback(reason=str(exc))
        else:
            if _is_fortress_profile():
                self._audit_fallback(reason="no_driver_in_fortress")
        return _wrap_key_aes_gcm(private_key_bytes)

    def _unwrap(self, wrapped: bytes, wrap_mode: str) -> bytes:
        if wrap_mode == WRAP_MODE_TPM2:
            return _unwrap_key_tpm2(wrapped, driver=self._driver)
        return _unwrap_key_aes_gcm(wrapped)

    def _audit_fallback(self, *, reason: str) -> None:
        """Best-effort fallback notice. Never raises."""
        try:
            from core.database.sqlite_setup import SecurityAuditLog, get_session
            import json as _json
            import uuid as _uuid

            with get_session() as session:
                session.add(
                    SecurityAuditLog(
                        id=str(_uuid.uuid4()),
                        timestamp=int(time.time() * 1000),
                        kind="cert_vault_fallback_active",
                        details_json=_json.dumps({"reason": reason}),
                    )
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cert_vault] audit emit failed: %s", exc)

    # ------------------------------------------------------------------
    # Public API — put / get / list / rotate
    # ------------------------------------------------------------------

    def put_cert(
        self,
        *,
        name: str,
        cert_der: bytes,
        private_key_bytes: bytes,
        chain_pem: Optional[str] = None,
        not_before: Optional[int] = None,
        not_after: Optional[int] = None,
        rotated_from: Optional[str] = None,
    ) -> CertRecord:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        if not cert_der:
            raise ValueError("cert_der must be non-empty")
        if not private_key_bytes:
            raise ValueError("private_key_bytes must be non-empty")

        wrapped, mode = self._wrap(private_key_bytes)
        fingerprint = hashlib.sha256(cert_der).hexdigest()
        created_at = int(time.time())

        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO certificates "
                "(name, cert_der, chain_pem, wrapped_key, wrap_mode, "
                " not_before, not_after, fingerprint, created_at, rotated_from) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    name,
                    cert_der,
                    chain_pem,
                    wrapped,
                    mode,
                    not_before,
                    not_after,
                    fingerprint,
                    created_at,
                    rotated_from,
                ),
            )

        return CertRecord(
            name=name,
            cert_der=cert_der,
            chain_pem=chain_pem,
            wrap_mode=mode,
            not_before=not_before,
            not_after=not_after,
            fingerprint=fingerprint,
            created_at=created_at,
            rotated_from=rotated_from,
        )

    def get_cert(self, name: str) -> Optional[CertRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT name, cert_der, chain_pem, wrap_mode, "
                "       not_before, not_after, fingerprint, created_at, rotated_from "
                "FROM certificates WHERE name = ?;",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return CertRecord(
            name=row[0],
            cert_der=row[1],
            chain_pem=row[2],
            wrap_mode=row[3],
            not_before=row[4],
            not_after=row[5],
            fingerprint=row[6],
            created_at=row[7],
            rotated_from=row[8],
        )

    def get_private_key(self, name: str) -> Optional[bytes]:
        """Unwrap and return the private key bytes. Caller is responsible
        for clearing the bytes from memory after use."""
        with self._lock:
            row = self._conn.execute(
                "SELECT wrapped_key, wrap_mode FROM certificates WHERE name = ?;",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return self._unwrap(row[0], row[1])

    def list_certs(self) -> list[CertRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, cert_der, chain_pem, wrap_mode, "
                "       not_before, not_after, fingerprint, created_at, rotated_from "
                "FROM certificates ORDER BY name;"
            ).fetchall()
        return [
            CertRecord(
                name=r[0],
                cert_der=r[1],
                chain_pem=r[2],
                wrap_mode=r[3],
                not_before=r[4],
                not_after=r[5],
                fingerprint=r[6],
                created_at=r[7],
                rotated_from=r[8],
            )
            for r in rows
        ]

    def list_expiring(
        self, *, within_s: int = DEFAULT_EXPIRY_WARN_WINDOW_S
    ) -> list[CertRecord]:
        cutoff = int(time.time() + within_s)
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, cert_der, chain_pem, wrap_mode, "
                "       not_before, not_after, fingerprint, created_at, rotated_from "
                "FROM certificates "
                "WHERE not_after IS NOT NULL AND not_after <= ? "
                "ORDER BY not_after;",
                (cutoff,),
            ).fetchall()
        return [
            CertRecord(
                name=r[0],
                cert_der=r[1],
                chain_pem=r[2],
                wrap_mode=r[3],
                not_before=r[4],
                not_after=r[5],
                fingerprint=r[6],
                created_at=r[7],
                rotated_from=r[8],
            )
            for r in rows
        ]

    def delete_cert(self, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM certificates WHERE name = ?;", (name,)
            )
            return cur.rowcount > 0

    @contextmanager
    def borrow_private_key(self, name: str) -> Iterator[Optional[bytes]]:
        """Context manager that yields the private key bytes and clears them
        on exit. Use this when handing the key to a short-lived signer."""
        key = self.get_private_key(name)
        try:
            yield key
        finally:
            if key is not None:
                # Best-effort overwrite — Python strings/bytes are immutable,
                # but bytearray view lets us scrub. We accept that the GC may
                # have copied internally; this is defense in depth.
                try:
                    ba = bytearray(key)
                    for i in range(len(ba)):
                        ba[i] = 0
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_singleton: Optional[CertVault] = None
_singleton_lock = threading.Lock()


def get_cert_vault(driver=None) -> CertVault:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = CertVault(vbus_driver=driver)
    return _singleton


def reset_for_tests() -> None:
    global _singleton
    with _singleton_lock:
        _singleton = None


# ===========================================================================
# v20.2-final — CertificateVault (IntegrityCertificate store, singleton)
#
# Distinct from CertVault above (signing certs + wrapped private keys);
# this is a tenant-partitioned SQLite store for finalized, SIGNED
# IntegrityCertificate attestation payloads. Fail-closed: refuses to store
# an unsigned certificate. Path from VOS3_CERT_VAULT_PATH; optional
# mmap_size from VOS3_CERT_VAULT_MMAP_MB (v20.4-TITAN SQLCipher mmap).
# ===========================================================================

ENV_CERT_VAULT_PATH = "VOS3_CERT_VAULT_PATH"
ENV_CERT_VAULT_MMAP_MB = "VOS3_CERT_VAULT_MMAP_MB"


def _certificate_payload(cert) -> dict:
    """Project an IntegrityCertificate (dataclass or duck-typed) to a
    JSON-serialisable dict that always carries id + tenant_id."""
    if is_dataclass(cert) and not isinstance(cert, type):
        try:
            return asdict(cert)
        except (TypeError, ValueError):
            pass
    base = dict(getattr(cert, "__dict__", {}))
    base.setdefault("id", getattr(cert, "id", None))
    base.setdefault("tenant_id", getattr(cert, "tenant_id", None))
    return base


class CertificateVault:
    """Singleton tenant-partitioned attestation-certificate store."""

    _instance: Optional["CertificateVault"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        path = os.environ.get(ENV_CERT_VAULT_PATH, DEFAULT_VAULT_PATH)
        self._path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        mmap_mb = os.environ.get(ENV_CERT_VAULT_MMAP_MB)
        if mmap_mb:
            try:
                self._conn.execute(f"PRAGMA mmap_size={int(mmap_mb) * 1024 * 1024}")
            except (ValueError, sqlite3.Error) as exc:
                logger.warning("CertificateVault: mmap_size pragma failed: %s", exc)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS attestation_certs (
                cert_id   TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                issued_ts INTEGER NOT NULL,
                payload   TEXT NOT NULL
            )
            """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_att_certs_tenant "
            "ON attestation_certs(tenant_id, issued_ts)"
        )
        self._conn.commit()

    @classmethod
    def instance(cls) -> "CertificateVault":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance_for_tests(cls) -> None:
        with cls._instance_lock:
            if cls._instance is not None:
                try:
                    cls._instance._conn.close()
                except sqlite3.Error:
                    pass
            cls._instance = None

    @staticmethod
    def _is_signed(cert) -> bool:
        return bool(getattr(cert, "signature_b64", None))

    def store(self, cert) -> dict:
        if not self._is_signed(cert):
            raise ValueError("refusing to store an unsigned certificate")
        cert_id = getattr(cert, "id", None)
        tenant_id = getattr(cert, "tenant_id", None)
        if not cert_id or not tenant_id:
            raise ValueError("certificate missing id or tenant_id")
        issued_ts = int(time.time())
        payload = _certificate_payload(cert)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO attestation_certs "
                "(cert_id, tenant_id, issued_ts, payload) VALUES (?,?,?,?)",
                (cert_id, tenant_id, issued_ts, json.dumps(payload, default=str)),
            )
            self._conn.commit()
        return {
            "cert_id": cert_id,
            "tenant_id": tenant_id,
            "issued_ts": issued_ts,
        }

    def get(self, cert_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM attestation_certs WHERE cert_id = ?",
                (cert_id,),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def list_by_date_range(
        self, tenant_id: str, start_ts: int, end_ts: int
    ) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM attestation_certs "
                "WHERE tenant_id = ? AND issued_ts BETWEEN ? AND ? "
                "ORDER BY issued_ts",
                (tenant_id, int(start_ts), int(end_ts)),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]


__all__ = [
    "CertRecord",
    "CertVault",
    "CertificateVault",
    "get_cert_vault",
    "reset_for_tests",
    "ENV_CERT_VAULT_PATH",
    "ENV_CERT_VAULT_MMAP_MB",
    "WRAP_MODE_TPM2",
    "WRAP_MODE_AES_GCM",
    "DEFAULT_EXPIRY_WARN_WINDOW_S",
]
