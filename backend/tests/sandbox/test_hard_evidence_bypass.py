"""
P1.2-follow-up-C · Hard-Evidence bypass tests.

vOS·Adaptive·SHA=aeb3736·Phase=P1.2-follow-up-C

Verifies that services/hard_evidence.py + services/app_sandbox.py:

  1. Treat an UNKNOWN-mode manifest as PROTECTED for rlimit-tier
     purposes when a valid GCP_EVIDENCE_AAA.summary.txt exists.
  2. NEVER upgrade RESTRICTED_LEGACY (kernel-asserted) regardless of
     evidence file — security > availability.
  3. Reject touched-empty / anchor-less / no-PASS-line files.
  4. Leave the manifest's `.mode` field unchanged (audit-honesty —
     the bypass alters only the resource budget, not the truth-claim).
"""

from __future__ import annotations

import textwrap
from pathlib import Path


from services import hard_evidence
from services.hardware_manifest import HardwareManifest, build_empty

_VALID_EVIDENCE = textwrap.dedent("""
    # vOS·Adaptive·SHA=aeb3736·Phase=P1.2-follow-up-C
    # GCP_EVIDENCE_AAA — closing artifact for engagement task #48
    # Captured: 20260517T120000Z UTC
    # ----- script output below -----
    vOS·Adaptive·SHA=aeb3736·Phase=P1.2-follow-up-C
    KVM PROTECTED_FULL verification
    Captured: 20260517T120000Z
    Log: /tmp/vos_kvm.log
    ----------------------------------------
    PASS  Kernel did not refuse to boot (CPU has long mode)
    PASS  [KPTI] summary line emitted
    PASS  Mitigation tier = PROTECTED_FULL
    PASS  All four PROTECTED_FULL prerequisites detected (pcid+invpcid+smep+smap)
    PASS  Perf budget <=2% (AAA plan §3.2)
    PASS  kpti_init() reached the ready state
    PASS  No [SECURITY] microcode warning (host is at or above baseline)
    PASS  PCI-ECAM available (MCFG present)
""").strip()


def _unknown_manifest() -> HardwareManifest:
    return build_empty()


def _restricted_legacy_manifest() -> HardwareManifest:
    """Synthesize a kernel-asserted RESTRICTED_LEGACY manifest — the
    kernel told us it's not protected. The bypass MUST NOT touch this."""
    return HardwareManifest(
        mode="RESTRICTED_LEGACY",
        kpti_mode="LEGACY_KAISER",
        pcid=False,
        invpcid=False,
        smep=False,
        smap=False,
        sha_ni=False,
        kpti_budget="5-30%",
        kpti_init_ready=True,
        pml4_kept_indices=(),
        pml4_stripped_present_count=None,
        pci_transport="PORT_IO",
        pci_ecam_mmio_base=None,
        pci_ecam_bus_range=None,
        pci_ecam_pcd=None,
        pci_ecam_nx=None,
        py_platform="Linux",
        py_machine="x86_64",
        py_processor="",
        microcode_revision=None,
        microcode_baseline=None,
        microcode_below_baseline=False,
        cve_exposure_count=0,
        risk_score=55,
    )


# ---------------------------------------------------------------------------
# is_present_and_valid — file-shape validation
# ---------------------------------------------------------------------------


def test_no_evidence_file_returns_false(tmp_path, monkeypatch):
    monkeypatch.setenv("VOS3_HARD_EVIDENCE_PATH", str(tmp_path / "missing.txt"))
    assert hard_evidence.is_present_and_valid() is False


def test_empty_file_rejected(tmp_path, monkeypatch):
    p = tmp_path / "evidence.txt"
    p.write_text("", encoding="utf-8")
    monkeypatch.setenv("VOS3_HARD_EVIDENCE_PATH", str(p))
    assert (
        hard_evidence.is_present_and_valid() is False
    ), "Empty file must NOT count as valid evidence"


def test_file_without_anchor_rejected(tmp_path, monkeypatch):
    """A summary from a different engagement (wrong anchor SHA) must
    be refused."""
    p = tmp_path / "evidence.txt"
    p.write_text("PASS  Mitigation tier = PROTECTED_FULL\n", encoding="utf-8")
    monkeypatch.setenv("VOS3_HARD_EVIDENCE_PATH", str(p))
    assert (
        hard_evidence.is_present_and_valid() is False
    ), "File without anchor SHA must NOT count as valid evidence"


def test_file_without_protected_pass_rejected(tmp_path, monkeypatch):
    """Anchor + structure present but no PROTECTED tier confirmed —
    must refuse (e.g., a FAIL or only WARN run)."""
    p = tmp_path / "evidence.txt"
    p.write_text(
        "vOS·Adaptive·SHA=aeb3736·Phase=P1.2-follow-up-C\n"
        "FAIL  Mitigation tier not PROTECTED_*\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOS3_HARD_EVIDENCE_PATH", str(p))
    assert hard_evidence.is_present_and_valid() is False


def test_valid_protected_full_accepted(tmp_path, monkeypatch):
    p = tmp_path / "evidence.txt"
    p.write_text(_VALID_EVIDENCE, encoding="utf-8")
    monkeypatch.setenv("VOS3_HARD_EVIDENCE_PATH", str(p))
    assert hard_evidence.is_present_and_valid() is True


def test_valid_protected_pcid_only_accepted(tmp_path, monkeypatch):
    """PROTECTED_PCID_ONLY is the kernel's correct decision on a CPU
    without SMEP+SMAP and is acceptable evidence."""
    p = tmp_path / "evidence.txt"
    p.write_text(
        _VALID_EVIDENCE.replace(
            "Mitigation tier = PROTECTED_FULL",
            "Mitigation tier = PROTECTED_PCID_ONLY",
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOS3_HARD_EVIDENCE_PATH", str(p))
    assert hard_evidence.is_present_and_valid() is True


# ---------------------------------------------------------------------------
# app_sandbox._adaptive_rlimits — the load-bearing wire
# ---------------------------------------------------------------------------


def test_unknown_with_valid_evidence_yields_protected_rlimits(
    tmp_path,
    monkeypatch,
):
    """The headline behaviour: an UNKNOWN manifest with valid evidence
    produces the PROTECTED-tier rlimit dict."""
    from services import app_sandbox

    # Place valid evidence + point the module at it.
    p = tmp_path / "evidence.txt"
    p.write_text(_VALID_EVIDENCE, encoding="utf-8")
    monkeypatch.setenv("VOS3_HARD_EVIDENCE_PATH", str(p))

    # Make _active_manifest return an UNKNOWN manifest (dev macOS host).
    monkeypatch.setattr(
        app_sandbox,
        "_active_manifest",
        lambda: _unknown_manifest(),
    )

    rlimits = app_sandbox._adaptive_rlimits(base_memory_mb=256)
    # PROTECTED tier: full 256 MB, NPROC=(4,4), CPU=(60,120) — NOT halved.
    assert rlimits["RLIMIT_AS"] == (256 * 1024 * 1024, 256 * 1024 * 1024)
    assert rlimits["RLIMIT_CPU"] == (60, 120)
    assert rlimits["RLIMIT_NPROC"] == (4, 4)


def test_unknown_without_evidence_still_restricted(monkeypatch, tmp_path):
    """Sanity-check the negative: no evidence → restricted tier
    (preserves the existing P4.3 behaviour for UNKNOWN)."""
    from services import app_sandbox

    monkeypatch.setenv("VOS3_HARD_EVIDENCE_PATH", str(tmp_path / "no_such_file"))
    monkeypatch.setattr(
        app_sandbox,
        "_active_manifest",
        lambda: _unknown_manifest(),
    )

    rlimits = app_sandbox._adaptive_rlimits(base_memory_mb=256)
    # Restricted tier: 128 MB, NPROC=(2,2), CPU=(30,60).
    assert rlimits["RLIMIT_AS"] == (128 * 1024 * 1024, 128 * 1024 * 1024)
    assert rlimits["RLIMIT_CPU"] == (30, 60)
    assert rlimits["RLIMIT_NPROC"] == (2, 2)


def test_restricted_legacy_with_evidence_stays_restricted(
    tmp_path,
    monkeypatch,
):
    """Load-bearing safety property: even with valid evidence, a
    kernel-asserted RESTRICTED_LEGACY manifest MUST keep the restricted
    rlimit tier. Security > Availability; we believe the kernel."""
    from services import app_sandbox

    p = tmp_path / "evidence.txt"
    p.write_text(_VALID_EVIDENCE, encoding="utf-8")
    monkeypatch.setenv("VOS3_HARD_EVIDENCE_PATH", str(p))

    monkeypatch.setattr(
        app_sandbox,
        "_active_manifest",
        lambda: _restricted_legacy_manifest(),
    )

    rlimits = app_sandbox._adaptive_rlimits(base_memory_mb=256)
    # Still restricted: 128 MB, NPROC=(2,2). Bypass MUST NOT fire.
    assert rlimits["RLIMIT_AS"] == (128 * 1024 * 1024, 128 * 1024 * 1024)
    assert rlimits["RLIMIT_NPROC"] == (2, 2), (
        "Hard-evidence bypass MUST NOT override RESTRICTED_LEGACY — "
        "the kernel told us it's not protected, we believe it"
    )


def test_bypass_announces_warning(tmp_path, monkeypatch, caplog):
    """The bypass must be audit-loud: every activation emits a
    WARNING-level log line the operator will see."""
    from services import app_sandbox

    p = tmp_path / "evidence.txt"
    p.write_text(_VALID_EVIDENCE, encoding="utf-8")
    monkeypatch.setenv("VOS3_HARD_EVIDENCE_PATH", str(p))
    monkeypatch.setattr(
        app_sandbox,
        "_active_manifest",
        lambda: _unknown_manifest(),
    )

    with caplog.at_level("WARNING", logger="services.hard_evidence"):
        app_sandbox._adaptive_rlimits(base_memory_mb=256)

    bypass_logs = [
        r for r in caplog.records if "hard-evidence bypass" in r.message.lower()
    ]
    assert bypass_logs, "Hard-evidence bypass must emit a WARNING log line"
    assert bypass_logs[0].levelname == "WARNING"


def test_manifest_mode_unchanged_after_bypass(tmp_path, monkeypatch):
    """Audit-honesty: the manifest's `.mode` MUST stay UNKNOWN even
    when the bypass fires. Only the rlimit budget is altered, the
    truth-claim about what the kernel told us is preserved."""
    from services import app_sandbox

    p = tmp_path / "evidence.txt"
    p.write_text(_VALID_EVIDENCE, encoding="utf-8")
    monkeypatch.setenv("VOS3_HARD_EVIDENCE_PATH", str(p))
    fixed = _unknown_manifest()
    monkeypatch.setattr(app_sandbox, "_active_manifest", lambda: fixed)

    app_sandbox._adaptive_rlimits(base_memory_mb=256)  # triggers bypass

    # The manifest we passed in is frozen — verify the returned
    # accessor still gives UNKNOWN.
    assert (
        app_sandbox._active_manifest().mode == "UNKNOWN"
    ), "Bypass MUST NOT mutate the manifest's truth-claim about kernel state"


# ---------------------------------------------------------------------------
# Engagement-marker pin
# ---------------------------------------------------------------------------


def test_engagement_marker_present_in_hard_evidence_module():
    src = Path(__file__).parent.parent.parent / "services" / "hard_evidence.py"
    assert "vOS·Adaptive·SHA=aeb3736·Phase=P1.2-follow-up-C" in src.read_text(
        encoding="utf-8"
    )
