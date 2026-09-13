"""
backend/security/fscrypt_policy.py
====================================

Sprint 16 / Item K5 — fscrypt-required policy enforcement (twin).

What this is
------------

From the 80-problem agent-era catalog, K5:
  "Encrypted-at-rest gap for model weights — even on LUKS/FileVault,
   model files are plaintext when read into memory; no per-file
   enforcement."

Operators mark subtrees (e.g. /opt/vos3/models/) as fscrypt-required.
The model_loader (and any other caller that opens files under those
subtrees) consults this twin BEFORE invoking the kernel-side fscrypt-
check, fails fast on plaintext files, and emits an audit event so
operators see the violation.

Public surface
--------------

  FscryptPolicyStore.mark_dir(prefix, kind)
  FscryptPolicyStore.unmark_dir(prefix)
  FscryptPolicyStore.policy_for(file_path) -> FscryptPolicyKind
  FscryptPolicyStore.check_required(file_path, is_encrypted_fn) -> FscryptCheckResult
  FscryptPolicyStore.snapshot_stats() -> FscryptStats

Honest scope ceiling
--------------------

  - This is the policy LAYER. Real-on-disk fscrypt verification is the
    kernel's job (fs/crypto/, fscrypt_file_open). The twin lets backend
    services + tests exercise the policy decision without needing
    real fscrypt-enabled storage.
  - Detection of "is the file encrypted?" is delegated to a caller-
    supplied `is_encrypted_fn(path) -> bool`. Production wires this to
    the FS_IOC_GET_ENCRYPTION_POLICY_EX ioctl; tests inject a stub.
  - Path matching is prefix-based (subtree). A more sophisticated
    glob or regex matcher could be added later; the current model
    matches the kernel's fscrypt_policy_v2 directory-inheritance.
  - Marking is APPEND-then-MATCH; the most-recently-marked matching
    prefix wins (so operators can override a broad RESTRICTIVE mark
    with a narrower PREFER mark).
"""

from __future__ import annotations

import enum
import os
import threading
from dataclasses import dataclass
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Enums + dataclasses
# ---------------------------------------------------------------------------


class FscryptPolicyKind(str, enum.Enum):
    NONE = "none"
    REQUIRED = "required"
    PREFER = "prefer"  # warn but don't block — useful during rollout


class FscryptCheckOutcome(str, enum.Enum):
    PASSED = "passed"
    BLOCKED = "blocked"
    WARNED = "warned"  # PREFER policy + file not encrypted
    NO_POLICY = "no_policy"


@dataclass(frozen=True)
class FscryptCheckResult:
    outcome: FscryptCheckOutcome
    file_path: str
    matched_prefix: Optional[str]
    matched_policy: FscryptPolicyKind
    reason: str


@dataclass(frozen=True)
class _PolicyEntry:
    prefix: str  # absolute, terminated with "/" for safe matching
    kind: FscryptPolicyKind
    marker_id: int


@dataclass
class FscryptStats:
    policies_marked: int = 0
    checks_run: int = 0
    blocked: int = 0
    warned: int = 0
    passed: int = 0
    no_policy: int = 0


# ---------------------------------------------------------------------------
# Path normalization helpers
# ---------------------------------------------------------------------------


def _normalize_prefix(prefix: str) -> str:
    """Absolute + trailing slash, so /a matches /a/b/c but not /ab/."""
    if not isinstance(prefix, str) or not prefix:
        raise ValueError("prefix must be a non-empty string")
    abs_path = os.path.abspath(prefix)
    if not abs_path.endswith(os.sep):
        abs_path = abs_path + os.sep
    return abs_path


def _normalize_file_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("file_path must be a non-empty string")
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class FscryptPolicyStore:
    def __init__(self, max_entries: int = 64):
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max = max_entries
        self._entries: list[_PolicyEntry] = []
        self._next_marker_id = 1
        self._lock = threading.Lock()
        self._stats = FscryptStats()

    # -- Mutation -----------------------------------------------------------

    def mark_dir(self, prefix: str, kind: FscryptPolicyKind) -> int:
        if not isinstance(kind, FscryptPolicyKind):
            raise TypeError("kind must be FscryptPolicyKind")
        if kind == FscryptPolicyKind.NONE:
            raise ValueError("use unmark_dir to clear a policy")
        normalized = _normalize_prefix(prefix)
        with self._lock:
            if len(self._entries) >= self._max:
                raise RuntimeError(
                    f"policy table full ({self._max} entries); unmark first"
                )
            marker = self._next_marker_id
            self._next_marker_id += 1
            self._entries.append(
                _PolicyEntry(
                    prefix=normalized,
                    kind=kind,
                    marker_id=marker,
                )
            )
            self._stats.policies_marked += 1
            return marker

    def unmark_dir(self, prefix: str) -> int:
        normalized = _normalize_prefix(prefix)
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.prefix != normalized]
            removed = before - len(self._entries)
        return removed

    def list_entries(self) -> list[_PolicyEntry]:
        with self._lock:
            return list(self._entries)

    # -- Lookup -------------------------------------------------------------

    def policy_for(self, file_path: str) -> FscryptPolicyKind:
        normalized = _normalize_file_path(file_path)
        if not normalized.endswith(os.sep):
            normalized += os.sep  # treat as dir-prefix for inclusive match
        with self._lock:
            # Most-recently-marked matching prefix wins → iterate reversed.
            for entry in reversed(self._entries):
                if normalized.startswith(entry.prefix):
                    return entry.kind
        return FscryptPolicyKind.NONE

    def matching_prefix(self, file_path: str) -> Optional[str]:
        normalized = _normalize_file_path(file_path)
        if not normalized.endswith(os.sep):
            normalized += os.sep
        with self._lock:
            for entry in reversed(self._entries):
                if normalized.startswith(entry.prefix):
                    return entry.prefix
        return None

    # -- Enforcement --------------------------------------------------------

    def check_required(
        self, file_path: str, is_encrypted_fn: Callable[[str], bool]
    ) -> FscryptCheckResult:
        if not callable(is_encrypted_fn):
            raise TypeError("is_encrypted_fn must be callable(path)->bool")
        normalized = _normalize_file_path(file_path)
        kind = self.policy_for(file_path)
        prefix = self.matching_prefix(file_path)
        with self._lock:
            self._stats.checks_run += 1

        if kind == FscryptPolicyKind.NONE:
            with self._lock:
                self._stats.no_policy += 1
            return FscryptCheckResult(
                outcome=FscryptCheckOutcome.NO_POLICY,
                file_path=normalized,
                matched_prefix=None,
                matched_policy=FscryptPolicyKind.NONE,
                reason="no_policy_for_path",
            )

        is_enc = bool(is_encrypted_fn(normalized))
        if is_enc:
            with self._lock:
                self._stats.passed += 1
            return FscryptCheckResult(
                outcome=FscryptCheckOutcome.PASSED,
                file_path=normalized,
                matched_prefix=prefix,
                matched_policy=kind,
                reason="file_is_fscrypt_protected",
            )

        # Not encrypted; outcome depends on policy kind.
        if kind == FscryptPolicyKind.REQUIRED:
            with self._lock:
                self._stats.blocked += 1
            return FscryptCheckResult(
                outcome=FscryptCheckOutcome.BLOCKED,
                file_path=normalized,
                matched_prefix=prefix,
                matched_policy=kind,
                reason="policy_requires_fscrypt_but_file_plaintext",
            )
        # PREFER → warn.
        with self._lock:
            self._stats.warned += 1
        return FscryptCheckResult(
            outcome=FscryptCheckOutcome.WARNED,
            file_path=normalized,
            matched_prefix=prefix,
            matched_policy=kind,
            reason="policy_prefers_fscrypt_but_file_plaintext",
        )

    # -- Stats --------------------------------------------------------------

    def snapshot_stats(self) -> FscryptStats:
        with self._lock:
            return FscryptStats(
                policies_marked=self._stats.policies_marked,
                checks_run=self._stats.checks_run,
                blocked=self._stats.blocked,
                warned=self._stats.warned,
                passed=self._stats.passed,
                no_policy=self._stats.no_policy,
            )


__all__ = [
    "FscryptPolicyKind",
    "FscryptCheckOutcome",
    "FscryptCheckResult",
    "FscryptStats",
    "FscryptPolicyStore",
]
