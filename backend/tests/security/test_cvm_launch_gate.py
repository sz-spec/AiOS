"""
Tests for Sprint 22 / Item D2 (+D6) — CVM interrupt-filtering launch gate
(backend/security/cvm_launch_gate.py).

Pins the fail-closed contract: a confidential VM launches only when the
platform attests restricted interrupt injection (anti-HECKLER), and — in
the FORTRESS profile — Confidential Compute is enabled (D6).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from security.cvm_launch_gate import (  # noqa: E402
    CvmAttestation,
    CvmLaunchGate,
    CvmLaunchRefused,
    CvmProfile,
    CvmTech,
    ENV_DEV_OVERRIDE,
)


def _att(tech=CvmTech.SEV_SNP, ri=True, cc=True, verified=True):
    return CvmAttestation(
        cvm_tech=tech, restricted_injection=ri, cc_enabled=cc, verified=verified
    )


def test_good_attestation_allows_standard():
    gate = CvmLaunchGate(profile=CvmProfile.STANDARD)
    gate.require_cvm_launch(_att())
    assert gate.stats.allowed == 1


def test_no_attestation_refused():
    gate = CvmLaunchGate()
    with pytest.raises(CvmLaunchRefused):
        gate.require_cvm_launch(None)


def test_unverified_attestation_refused():
    gate = CvmLaunchGate()
    with pytest.raises(CvmLaunchRefused):
        gate.require_cvm_launch(_att(verified=False))


def test_non_cvm_refused():
    gate = CvmLaunchGate()
    with pytest.raises(CvmLaunchRefused):
        gate.require_cvm_launch(_att(tech=CvmTech.NONE))


def test_no_restricted_injection_refused_heckler():
    gate = CvmLaunchGate()
    with pytest.raises(CvmLaunchRefused):
        gate.require_cvm_launch(_att(ri=False))


def test_fortress_refuses_cc_off():
    gate = CvmLaunchGate(profile=CvmProfile.FORTRESS)
    with pytest.raises(CvmLaunchRefused):
        gate.require_cvm_launch(_att(cc=False))


def test_standard_allows_cc_off_if_interrupts_restricted():
    gate = CvmLaunchGate(profile=CvmProfile.STANDARD)
    gate.require_cvm_launch(_att(cc=False))  # CC off ok in STANDARD
    assert gate.stats.allowed == 1


def test_dev_override_allows_unattested(monkeypatch):
    monkeypatch.setenv(ENV_DEV_OVERRIDE, "1")
    gate = CvmLaunchGate()
    gate.require_cvm_launch(None)
    assert gate.stats.dev_overrides_used == 1


def test_probe_cvm_tech_from_cpuinfo(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "cpuinfo").write_text("flags : fpu vme sev_snp\n")
    gate = CvmLaunchGate(proc_root=str(proc))
    assert gate.probe_cvm_tech() == CvmTech.SEV_SNP
