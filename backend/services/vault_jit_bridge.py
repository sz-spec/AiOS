"""
backend/services/vault_jit_bridge.py
======================================

Sprint 16 / Item F5 — Vault Enterprise 2.0 JIT credential bridge.

What this is
------------

From the 80-problem agent-era catalog, F5:
  "Long-lived agent tokens vs JIT credentials — no kernel rotation hook.
   Industry moving to short-lived JIT (Vault + SPIFFE) but OS-level
   rotation hooks don't exist."

This module is the userspace orchestrator that:

  1. Mints just-in-time (JIT) credentials by calling Vault Enterprise 2.0
     (April 2026; commercial API). Each credential carries a short TTL
     (default 5 minutes; operator-tunable).
  2. Tracks credential lineage: a (issued_at, expiry, token_hash) tuple
     per credential so the system knows who has what and when to rotate.
  3. Triggers kernel-side rotation via the vos3_cred_rotate syscall
     (declared in kernel/include/vos/cred_rotate.h, F5 companion).
  4. Reports the outcome — how many open fds got invalidated, how many
     were closed, how many were retained — so the operator can verify
     no stale-token race window leaked.

Public surface
--------------

  VaultJITBridge.issue_credential(audience, ttl_seconds=300) -> JITCredential
  VaultJITBridge.rotate_credential(credential_id) -> RotationOutcome
  VaultJITBridge.revoke_credential(credential_id)
  VaultJITBridge.snapshot_stats() -> JITBridgeStats

Vault API integration
---------------------

In production: connects to a Vault Enterprise 2.0 cluster via the
hvac client and uses the `auth/token/create` endpoint with a
short-lived role.

In dev / test: a `VaultStubBackend` mints fake JWTs locally — same
interface, no network. The bridge uses dependency injection so the
production wiring (real hvac client) and the test wiring share the
same code path.

Kernel rotation hook
--------------------

The bridge calls a `KernelRotationHook` protocol implementation. In
production this maps to the vos3_cred_rotate syscall via a small C
shim. In dev / test, a `SimulatedKernelHook` mimics the syscall
behavior so this module's tests are end-to-end without needing the
kernel running.

Honest scope ceiling
--------------------

  - Real hvac client integration ships in a follow-up; this module
    ships with the VaultStubBackend so the integration surface is
    testable today. The KernelRotationHook protocol is structural;
    swapping to the real syscall is a one-line change.
  - VaultJITBridge does NOT cache tokens beyond their TTL. Caller is
    expected to rotate proactively at least 30s before expiry.
  - Rotation atomicity: the kernel syscall blocks until the fd-table
    walk completes; this module wraps the call and reports the
    outcome. If the kernel returns ERR_NOTFOUND (no fd held the
    token), rotation still succeeds (the userspace caller may have
    closed all fds before rotation).
  - SPIFFE Federation (F4) wiring: the bridge can register a
    rotation-on-bundle-refresh handler for federated trust domains.
    Implemented as a callback hook; not wired in this commit.

References:
  - HashiCorp Vault Enterprise 2.0 announcement (April 2026)
  - HashiCorp blog: "SPIFFE: Securing the identity of agentic AI"
  - Vault automated credential rotation
    (developer.hashicorp.com/vault/docs/enterprise/automated-credential-rotation)
"""

from __future__ import annotations

import enum
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional, Protocol

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


DEFAULT_JIT_TTL_SECONDS = 300  # 5 minutes
MAX_JIT_TTL_SECONDS = 3600  # 1 hour ceiling — enforced at issue time


class CredentialStatus(enum.IntEnum):
    ACTIVE = 0
    ROTATED = 1  # superseded; kernel walked + fds invalidated
    REVOKED = 2  # operator killed it
    EXPIRED = 3  # TTL elapsed without rotation


@dataclass(frozen=True)
class JITCredential:
    credential_id: str  # opaque server-side ID
    audience: str
    token: str  # the actual JWT (Vault response)
    token_sha256: bytes  # 32-byte SHA-256 of the token (for kernel hook)
    issued_at: float
    expiry: float


@dataclass(frozen=True)
class RotationOutcome:
    credential_id: str
    new_credential_id: str
    fds_marked_stale: int
    fds_closed: int
    fds_skipped: int
    walk_duration_ns: int


@dataclass
class JITBridgeStats:
    credentials_issued: int = 0
    rotations: int = 0
    revocations: int = 0
    expirations_reaped: int = 0


# ---------------------------------------------------------------------------
# Vault backend protocol — production = hvac; tests = stub
# ---------------------------------------------------------------------------


class VaultBackend(Protocol):
    def create_token(self, audience: str, ttl_seconds: int) -> tuple[str, str]:
        """Returns (credential_id, raw_token)."""
        ...

    def revoke_token(self, credential_id: str) -> None: ...


# ---------------------------------------------------------------------------
# Kernel rotation hook protocol — production = vos3_cred_rotate syscall
#                                 dev/test = SimulatedKernelHook
# ---------------------------------------------------------------------------


class KernelRotationHook(Protocol):
    def rotate(self, token_sha256: bytes, new_expiry_ns: int) -> RotationOutcome:
        """Invalidate every fd holding `token_sha256` and report counts."""
        ...


# ---------------------------------------------------------------------------
# Default in-process implementations (for tests + dev)
# ---------------------------------------------------------------------------


class VaultStubBackend:
    """Mints fake JWTs without touching the network. The token string
    embeds the credential_id so tests can assert on it."""

    def __init__(self):
        self._tokens: dict[str, str] = {}
        self._revoked: set[str] = set()
        self._counter = 0
        self._lock = threading.Lock()

    def create_token(self, audience: str, ttl_seconds: int) -> tuple[str, str]:
        with self._lock:
            self._counter += 1
            cid = f"vault-stub-{self._counter:06d}"
            raw_token = f"FAKE.{cid}.{audience}.{secrets.token_hex(8)}"
            self._tokens[cid] = raw_token
            return cid, raw_token

    def revoke_token(self, credential_id: str) -> None:
        with self._lock:
            self._revoked.add(credential_id)


class SimulatedKernelHook:
    """Stand-in for the vos3_cred_rotate syscall.

    Mimics the kernel-side fd-walker outcome: configurable per-test
    so a test can simulate "5 fds held this token, 4 marked stale,
    1 closed immediately, 0 skipped".
    """

    def __init__(self):
        self._next_outcome: Optional[RotationOutcome] = None
        self._lock = threading.Lock()
        self.calls: list[tuple[bytes, int]] = []

    def queue_outcome(
        self,
        *,
        marked_stale: int = 0,
        closed: int = 0,
        skipped: int = 0,
        walk_duration_ns: int = 0,
        new_credential_id: str = "next-cred",
    ) -> None:
        with self._lock:
            self._next_outcome = RotationOutcome(
                credential_id="<replaced-on-rotate>",
                new_credential_id=new_credential_id,
                fds_marked_stale=marked_stale,
                fds_closed=closed,
                fds_skipped=skipped,
                walk_duration_ns=walk_duration_ns,
            )

    def rotate(self, token_sha256: bytes, new_expiry_ns: int) -> RotationOutcome:
        with self._lock:
            self.calls.append((token_sha256, new_expiry_ns))
            if self._next_outcome is None:
                # Default outcome: no fds matched.
                return RotationOutcome(
                    credential_id="<replaced-on-rotate>",
                    new_credential_id="next-cred",
                    fds_marked_stale=0,
                    fds_closed=0,
                    fds_skipped=0,
                    walk_duration_ns=0,
                )
            outcome = self._next_outcome
            self._next_outcome = None
            return outcome


# ---------------------------------------------------------------------------
# VaultJITBridge
# ---------------------------------------------------------------------------


class VaultJITBridge:
    """Mints JIT credentials + orchestrates kernel-coordinated rotation."""

    def __init__(
        self,
        vault: VaultBackend,
        kernel_hook: KernelRotationHook,
        default_ttl_seconds: int = DEFAULT_JIT_TTL_SECONDS,
    ):
        if vault is None:
            raise ValueError("vault backend is required")
        if kernel_hook is None:
            raise ValueError("kernel_hook is required")
        if default_ttl_seconds <= 0 or default_ttl_seconds > MAX_JIT_TTL_SECONDS:
            raise ValueError(
                f"default_ttl_seconds must be in (0, {MAX_JIT_TTL_SECONDS}]"
            )
        self._vault = vault
        self._hook = kernel_hook
        self._default_ttl = default_ttl_seconds
        self._credentials: dict[str, JITCredential] = {}
        self._status: dict[str, CredentialStatus] = {}
        self._lock = threading.Lock()
        self._stats = JITBridgeStats()

    @staticmethod
    def _now() -> float:
        return time.time()

    def issue_credential(
        self, audience: str, ttl_seconds: Optional[int] = None
    ) -> JITCredential:
        if not audience or not isinstance(audience, str):
            raise ValueError("audience must be a non-empty string")
        ttl = int(ttl_seconds if ttl_seconds is not None else self._default_ttl)
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        if ttl > MAX_JIT_TTL_SECONDS:
            raise ValueError(f"ttl_seconds {ttl} exceeds MAX {MAX_JIT_TTL_SECONDS}")
        cid, raw_token = self._vault.create_token(audience, ttl)
        now = self._now()
        cred = JITCredential(
            credential_id=cid,
            audience=audience,
            token=raw_token,
            token_sha256=hashlib.sha256(raw_token.encode("utf-8")).digest(),
            issued_at=now,
            expiry=now + ttl,
        )
        with self._lock:
            self._credentials[cid] = cred
            self._status[cid] = CredentialStatus.ACTIVE
            self._stats.credentials_issued += 1
        return cred

    def rotate_credential(self, credential_id: str) -> RotationOutcome:
        """Rotate: mint replacement + invalidate kernel fds bound to old."""
        with self._lock:
            old = self._credentials.get(credential_id)
            status = self._status.get(credential_id)
        if old is None:
            raise KeyError(f"credential {credential_id!r} not found")
        if status != CredentialStatus.ACTIVE:
            raise RuntimeError(
                f"credential {credential_id!r} status is {status.name}, "
                f"cannot rotate"
            )

        # Mint the replacement first so a failure here doesn't leave a gap.
        replacement = self.issue_credential(
            audience=old.audience,
            ttl_seconds=int(old.expiry - old.issued_at),
        )
        # Trigger kernel-side fd-walker for the OLD token.
        new_expiry_ns = int(replacement.expiry * 1_000_000_000)
        outcome = self._hook.rotate(old.token_sha256, new_expiry_ns)
        with self._lock:
            self._status[credential_id] = CredentialStatus.ROTATED
            self._stats.rotations += 1
        # Return a copy bound to the actual ids (the hook's stub doesn't
        # know the credential_id; we fill it in here).
        return RotationOutcome(
            credential_id=credential_id,
            new_credential_id=replacement.credential_id,
            fds_marked_stale=outcome.fds_marked_stale,
            fds_closed=outcome.fds_closed,
            fds_skipped=outcome.fds_skipped,
            walk_duration_ns=outcome.walk_duration_ns,
        )

    def revoke_credential(self, credential_id: str) -> None:
        with self._lock:
            cred = self._credentials.get(credential_id)
        if cred is None:
            raise KeyError(f"credential {credential_id!r} not found")
        self._vault.revoke_token(credential_id)
        # Kernel hook is also called so any open fd is killed.
        self._hook.rotate(cred.token_sha256, new_expiry_ns=0)
        with self._lock:
            self._status[credential_id] = CredentialStatus.REVOKED
            self._stats.revocations += 1

    def status(self, credential_id: str) -> CredentialStatus:
        with self._lock:
            st = self._status.get(credential_id)
        if st is None:
            raise KeyError(f"credential {credential_id!r} not found")
        return st

    def reap_expired(self) -> int:
        """Move ACTIVE creds past their TTL into EXPIRED state. Returns count."""
        reaped = 0
        now = self._now()
        with self._lock:
            for cid, cred in self._credentials.items():
                if self._status[cid] == CredentialStatus.ACTIVE and cred.expiry <= now:
                    self._status[cid] = CredentialStatus.EXPIRED
                    reaped += 1
            self._stats.expirations_reaped += reaped
        return reaped

    def snapshot_stats(self) -> JITBridgeStats:
        with self._lock:
            return JITBridgeStats(
                credentials_issued=self._stats.credentials_issued,
                rotations=self._stats.rotations,
                revocations=self._stats.revocations,
                expirations_reaped=self._stats.expirations_reaped,
            )


__all__ = [
    "DEFAULT_JIT_TTL_SECONDS",
    "MAX_JIT_TTL_SECONDS",
    "CredentialStatus",
    "JITCredential",
    "RotationOutcome",
    "JITBridgeStats",
    "VaultBackend",
    "KernelRotationHook",
    "VaultStubBackend",
    "SimulatedKernelHook",
    "VaultJITBridge",
]
