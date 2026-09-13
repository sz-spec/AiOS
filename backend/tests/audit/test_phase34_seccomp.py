"""
Phase 34 — hardened seccomp-BPF syscall filter (Gap G11).
=========================================================

Exercises the REAL ``SeccompFilterEngine`` + ``ProfileSwitcher`` + the Phase-26
``PolicyTransparencyLedger`` provenance:

  TC1  ptrace (denied)            -> SeccompKill (SIGSYS / process termination) +
       SECCOMP_VIOLATION recorded.
  TC2  read (whitelisted)         -> ALLOW, no kill.
  TC3  TOXIC profile is stricter  -> mmap ALLOWED under CLEAN but KILLED under
       TOXIC.

Anti-gaming (charter §II): fixed-value asserts on the exact action / raised
signal / strictness ordering; decisions are number-based (the LD_PRELOAD-proof
property is asserted directly), and the violation is verified through the real
ledger inclusion proof — no mocked verdicts.

Run: .venv_p312/bin/python -m pytest tests/audit/test_phase34_seccomp.py -n 0
"""

from __future__ import annotations

import pytest

from security.kernel_gate_connector import TaintLabel
from services.policy_transparency import PolicyTransparencyLedger, verify_proof
from services.seccomp_filter import (
    HARD_DENY,
    SYSCALL_NR,
    MVP_PROFILE,
    TOXIC_PROFILE,
    ProfileSwitcher,
    SeccompAction,
    SeccompFilterEngine,
    SeccompKill,
)

PUBLIC = int(TaintLabel.PUBLIC)
TOXIC = int(TaintLabel.TOXIC)


# ---------------------------------------------------------------------------
# TC1 — denied syscall (ptrace) -> SIGSYS / process termination + provenance.
# ---------------------------------------------------------------------------


def test_tc1_ptrace_denied_sigsys_and_logged(tmp_path):
    ledger = PolicyTransparencyLedger(path=str(tmp_path / "seccomp.jsonl"))
    eng = SeccompFilterEngine(ledger=ledger)

    before = ledger.tree_size()
    with pytest.raises(SeccompKill) as ei:
        eng.enforce("ptrace", pid=4242, taint_color=PUBLIC)
    assert ei.value.signal == "SIGSYS"
    assert ei.value.syscall == "ptrace"

    # A SECCOMP_VIOLATION provenance event was recorded + is provable.
    assert ledger.tree_size() == before + 1
    last_id = ledger._records[-1]["event_id"]  # noqa: SLF001
    proof = ledger.generate_proof(last_id)
    assert verify_proof(proof) is True
    assert proof["event_type"] == "seccomp_violation"


def test_tc1b_other_dangerous_syscalls_are_hard_denied():
    eng = SeccompFilterEngine()
    for name in ("unshare", "mount", "reboot", "kexec_load"):
        with pytest.raises(SeccompKill):
            eng.enforce(name, pid=1, taint_color=PUBLIC)
        # Even under no profile, these are in HARD_DENY.
        assert SYSCALL_NR[name] in HARD_DENY


# ---------------------------------------------------------------------------
# TC2 — whitelisted syscall (read) -> ALLOW.
# ---------------------------------------------------------------------------


def test_tc2_read_whitelisted_allows():
    eng = SeccompFilterEngine()
    assert eng.enforce("read", pid=7, taint_color=PUBLIC) == SeccompAction.ALLOW
    assert eng.enforce("write", pid=7, taint_color=PUBLIC) == SeccompAction.ALLOW
    assert eng.enforce("futex", pid=7, taint_color=PUBLIC) == SeccompAction.ALLOW
    assert eng.enforce("exit", pid=7, taint_color=PUBLIC) == SeccompAction.ALLOW


# ---------------------------------------------------------------------------
# TC3 — TOXIC profile strictly tighter than CLEAN (memory-map calls).
# ---------------------------------------------------------------------------


def test_tc3_toxic_profile_stricter_than_clean_for_mmap():
    eng = SeccompFilterEngine()
    # CLEAN: mmap is allowed.
    assert eng.enforce("mmap", pid=9, taint_color=PUBLIC) == SeccompAction.ALLOW
    # TOXIC: the SAME mmap is killed (no memory-map calls for toxic processes).
    with pytest.raises(SeccompKill):
        eng.enforce("mmap", pid=9, taint_color=TOXIC)
    # And mprotect/brk too.
    for name in ("mprotect", "brk", "munmap"):
        assert eng.evaluate(name, taint_color=PUBLIC) == SeccompAction.ALLOW
        assert eng.evaluate(name, taint_color=TOXIC) == SeccompAction.KILL_PROCESS


def test_tc3b_toxic_allow_set_is_a_strict_subset_of_clean():
    # Structural: the toxic whitelist must be strictly smaller than the clean one.
    assert TOXIC_PROFILE.allow < MVP_PROFILE.allow  # proper subset
    # read/write/exit/futex survive in both (an agent can still run + return).
    for name in ("read", "write", "exit", "futex"):
        assert SYSCALL_NR[name] in TOXIC_PROFILE.allow


# ---------------------------------------------------------------------------
# No-bypass: decisions are syscall-NUMBER based (LD_PRELOAD cannot change them).
# ---------------------------------------------------------------------------


def test_decision_is_number_based_not_name_based():
    sw = ProfileSwitcher()
    prof = sw.profile_for(PUBLIC)
    # Passing the raw ptrace NUMBER (101) is killed exactly like the name — a
    # userspace shim that renames the symbol cannot change the kernel-seen number.
    assert prof.evaluate(SYSCALL_NR["ptrace"]) == SeccompAction.KILL_PROCESS
    assert prof.evaluate("ptrace") == SeccompAction.KILL_PROCESS
    # An unknown (userspace-invented) syscall name is default-denied, never allowed.
    assert prof.evaluate("totally_made_up_syscall") == SeccompAction.KILL_PROCESS
    # The whitelist default is deny (a number not in the allow set is killed).
    assert prof.evaluate(99999) == SeccompAction.KILL_PROCESS


def test_install_filter_is_honest_noop_on_dev():
    from services.seccomp_filter import install_filter

    # On a non-Linux dev host we NEVER fake a kernel-enforced filter.
    assert install_filter(MVP_PROFILE) is False
