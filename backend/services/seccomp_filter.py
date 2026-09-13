"""
backend/services/seccomp_filter.py — Phase 34 (Gap G11)
========================================================

Hardened seccomp-BPF syscall filter with dynamic, taint-color-driven profile
switching. Applies a default-deny "Minimal Viable Syscall" whitelist to agent
processes; a TOXIC-tainted process is switched to a strictly tighter profile.
A denied syscall is fail-closed: SECCOMP_RET_KILL_PROCESS (SIGSYS termination) +
a cryptographic provenance record in the Phase-26 transparency ledger.

Honest scope (read before citing G11 as closed)
================================================

A real seccomp filter is a cBPF program the kernel evaluates at the syscall
boundary, installed via ``prctl(PR_SET_NO_NEW_PRIVS)`` + ``prctl(PR_SET_SECCOMP,
SECCOMP_MODE_FILTER, ...)`` (or libseccomp). Once installed under
NO_NEW_PRIVS it is **irrevocable and inherited across exec** — which is exactly
why an ``LD_PRELOAD`` / userspace shim CANNOT bypass it: the filter matches the
raw syscall NUMBER the kernel sees, not any userspace symbol. macOS has no
seccomp, so this module ships:
  - the **profile engine** (default-deny whitelist by real x86_64 syscall number),
  - the **ProfileSwitcher** (taint-color → profile), and
  - the **decision + fail-closed SIGSYS model + provenance** layer,
with the kernel install (``prctl``) behind an abstract seam (``install_filter``)
that is the documented production hook — NOT executed on dev (same boundary as the
Phase-29 perf_event_open / Phase-32 bpf_timer / Phase-33 kprobe producers).

This advances no moat tally.
"""

from __future__ import annotations

import enum
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional, Union

from security.kernel_gate_connector import TaintLabel

logger = logging.getLogger("vos3.security.seccomp")

# ---------------------------------------------------------------------------
# x86_64 syscall numbers (the values the kernel actually matches on). Keeping
# real numbers makes the filter number-based — the property that makes it
# LD_PRELOAD-proof (a userspace wrapper cannot change the number the kernel sees).
# ---------------------------------------------------------------------------

SYSCALL_NR: Dict[str, int] = {
    "read": 0,
    "write": 1,
    "mmap": 9,
    "mprotect": 10,
    "munmap": 11,
    "brk": 12,
    "rt_sigreturn": 15,
    "exit": 60,
    "ptrace": 101,
    "mount": 165,
    "reboot": 169,
    "futex": 202,
    "exit_group": 231,
    "kexec_load": 246,
    "unshare": 272,
}
_NR_TO_NAME = {v: k for k, v in SYSCALL_NR.items()}

# Always-fatal syscalls (defense in depth): KILL regardless of profile, even if a
# future edit accidentally adds one to an allow set.
HARD_DENY: FrozenSet[int] = frozenset(
    SYSCALL_NR[n] for n in ("ptrace", "unshare", "mount", "reboot", "kexec_load")
)


class SeccompAction(enum.IntEnum):
    """Mirrors the seccomp return actions we use."""

    ALLOW = 0  # SECCOMP_RET_ALLOW
    KILL_PROCESS = 1  # SECCOMP_RET_KILL_PROCESS -> SIGSYS


def _resolve(syscall: Union[str, int]) -> int:
    if isinstance(syscall, int):
        return syscall
    n = SYSCALL_NR.get(str(syscall))
    if n is None:
        # Unknown name -> a number that is in no allow set -> default KILL.
        return -1
    return n


@dataclass(frozen=True)
class SeccompProfile:
    """A default-deny syscall whitelist (allow set of syscall NUMBERS)."""

    name: str
    allow: FrozenSet[int]
    default_action: SeccompAction = SeccompAction.KILL_PROCESS

    def evaluate(self, syscall: Union[str, int]) -> SeccompAction:
        num = _resolve(syscall)
        if num in HARD_DENY:
            return SeccompAction.KILL_PROCESS
        if num in self.allow:
            return SeccompAction.ALLOW
        return self.default_action


# Minimal Viable Syscall whitelist (CLEAN agents): just enough to run + return.
_MVP_NAMES = (
    "read",
    "write",
    "exit",
    "exit_group",
    "rt_sigreturn",
    "futex",
    "mmap",
    "munmap",
    "mprotect",
    "brk",
)
MVP_PROFILE = SeccompProfile(
    name="mvp-clean",
    allow=frozenset(SYSCALL_NR[n] for n in _MVP_NAMES),
)

# TOXIC profile: strictly tighter — drop ALL memory-map calls (mmap/mprotect/brk/
# munmap), so a toxic process cannot grow/relabel address space. read/write/exit/
# futex only.
_TOXIC_NAMES = ("read", "write", "exit", "exit_group", "rt_sigreturn", "futex")
TOXIC_PROFILE = SeccompProfile(
    name="toxic-strict",
    allow=frozenset(SYSCALL_NR[n] for n in _TOXIC_NAMES),
)


class ProfileSwitcher:
    """Selects the seccomp profile for a process by its taint color. A TOXIC
    process gets the strict profile; everything else gets the MVP whitelist."""

    def profile_for(self, taint_color: Union[int, TaintLabel]) -> SeccompProfile:
        if int(taint_color) >= int(TaintLabel.TOXIC):
            return TOXIC_PROFILE
        return MVP_PROFILE


class SeccompKill(Exception):
    """Models SECCOMP_RET_KILL_PROCESS: the kernel delivers SIGSYS and terminates
    the process on a denied syscall. ``.syscall`` / ``.pid`` / ``.signal`` carry
    the audit cause."""

    def __init__(self, syscall: Union[str, int], *, pid: int) -> None:
        name = (
            syscall
            if isinstance(syscall, str)
            else _NR_TO_NAME.get(syscall, str(syscall))
        )
        super().__init__(f"SIGSYS: syscall {name!r} denied by seccomp filter")
        self.syscall = name
        self.pid = pid
        self.signal = "SIGSYS"


@dataclass
class SeccompFilterEngine:
    """Evaluates syscalls against the taint-selected profile and fails closed."""

    switcher: ProfileSwitcher = field(default_factory=ProfileSwitcher)
    ledger: Optional[object] = None

    def evaluate(
        self, syscall: Union[str, int], *, taint_color: Union[int, TaintLabel]
    ) -> SeccompAction:
        """Pure decision (no side effects) — the number-based verdict the kernel
        would reach. Identical whether given a name or the raw number, so a
        userspace LD_PRELOAD shim cannot change it."""
        return self.switcher.profile_for(taint_color).evaluate(syscall)

    def enforce(
        self,
        syscall: Union[str, int],
        *,
        pid: int,
        taint_color: Union[int, TaintLabel],
    ) -> SeccompAction:
        """Evaluate + fail-closed: on KILL, record a provenance violation and
        raise ``SeccompKill`` (the kernel would SIGSYS-terminate the process).
        Returns ``ALLOW`` for whitelisted syscalls."""
        profile = self.switcher.profile_for(taint_color)
        action = profile.evaluate(syscall)
        if action == SeccompAction.KILL_PROCESS:
            self._log_violation(syscall, pid=pid, profile=profile.name)
            raise SeccompKill(syscall, pid=pid)
        return action

    def _log_violation(
        self, syscall: Union[str, int], *, pid: int, profile: str
    ) -> None:
        name = (
            syscall
            if isinstance(syscall, str)
            else _NR_TO_NAME.get(syscall, str(syscall))
        )
        meta = {"pid": pid, "syscall": name, "profile": profile, "action": "SIGSYS"}
        logger.critical(
            "[SECURITY_CRITICAL][seccomp] SECCOMP_VIOLATION pid=%d syscall=%s profile=%s -> SIGSYS",
            pid,
            name,
            profile,
        )
        try:
            canonical = "|".join(f"{k}={meta[k]}" for k in sorted(meta)).encode("utf-8")
            schema_hash = hashlib.sha256(canonical).hexdigest()
            if self.ledger is not None:
                self.ledger.record(
                    event_type="seccomp_violation",
                    schema_hash=schema_hash,
                    metadata=meta,
                )
            else:
                from services.policy_transparency import record_policy_event

                record_policy_event(
                    event_type="seccomp_violation",
                    schema_hash=schema_hash,
                    metadata=meta,
                )
        except Exception as exc:  # noqa: BLE001 — provenance must not break enforcement
            logger.warning("[seccomp] provenance log failed: %s", exc)


# ---------------------------------------------------------------------------
# Production install seam (NOT executed on dev).
# ---------------------------------------------------------------------------


def install_filter(profile: SeccompProfile) -> bool:
    """Install the profile as a real kernel seccomp filter under NO_NEW_PRIVS
    (irrevocable, inherited across exec — the LD_PRELOAD-proof property). This is
    the production hook; on a non-Linux/dev host it is a no-op returning False
    (we never fake a kernel-enforced filter). Returns True iff a real filter was
    installed."""
    import os
    import platform

    if platform.system() != "Linux":
        logger.info("[seccomp] install_filter no-op on %s (dev)", platform.system())
        return False
    # Real install requires libseccomp / raw prctl wiring (production); we do NOT
    # attempt a partial install here, to avoid a false sense of enforcement.
    logger.warning(
        "[seccomp] install_filter(%s): real prctl(PR_SET_SECCOMP) install is the "
        "production hook; not wired in this build (pid=%d)",
        profile.name,
        os.getpid(),
    )
    return False


__all__ = [
    "SYSCALL_NR",
    "HARD_DENY",
    "SeccompAction",
    "SeccompProfile",
    "MVP_PROFILE",
    "TOXIC_PROFILE",
    "ProfileSwitcher",
    "SeccompKill",
    "SeccompFilterEngine",
    "install_filter",
]
