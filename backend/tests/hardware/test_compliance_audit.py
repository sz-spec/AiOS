"""
P4.1 · Compliance Audit (CVE inventory) tests.

vOS·Adaptive·SHA=aeb3736·Phase=P4.1

Verifies that services/compliance_audit.py:
  * Cross-references HardwareManifest against the architectural CVE
    baseline (Meltdown, Spectre v2, MDS, L1TF, Retbleed, Downfall, …).
  * Produces a deterministic compliance_inventory_v1.json bound to the
    aeb3736 anchor.
  * Wires `cve_exposure_count` back into the manifest so risk_score
    deducts proportionally (capped at -15).

The CVE table is sampled and pinned to silicon this engagement targets.
Adding a new CVE class = one row in `_BASELINE` + one row here.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path


from services.compliance_audit import (
    CveBaselineEntry,
    build_inventory,
    derive_cve_exposure,
    evaluate_cve,
    scan_requirements,
    write_inventory_json,
)
from services.hardware_manifest import build_empty, parse_klog

# ---------------------------------------------------------------------------
# Hand-crafted klog fragments
# ---------------------------------------------------------------------------

_KLOG_FRESH_MICROCODE = textwrap.dedent("""
    [KPTI] mode=PROTECTED_FULL pcid=yes invpcid=yes smep=yes smap=yes sha-ni=yes budget=<=2%
    [KPTI] init: ready
    [PCI-ECAM] available — buses 00..FF mapped MMIO@0x00000000B0000000 (PCD=1, NX=1)
    [MICROCODE] revision=0x05003604 baseline=0x05003604 below_baseline=no
""").strip()

_KLOG_OUTDATED_MICROCODE = textwrap.dedent("""
    [KPTI] mode=PROTECTED_FULL pcid=yes invpcid=yes smep=yes smap=yes sha-ni=yes budget=<=2%
    [KPTI] init: ready
    [PCI-ECAM] available — buses 00..FF mapped MMIO@0x00000000B0000000 (PCD=1, NX=1)
    [MICROCODE] revision=0x00000010 baseline=0x05003604 below_baseline=yes
    [SECURITY] Microcode outdated. System logic vulnerable to speculative execution side-channels.
""").strip()


# ---------------------------------------------------------------------------
# evaluate_cve — the single-entry decision function
# ---------------------------------------------------------------------------


def test_meltdown_mitigated_when_kpti_active():
    """CVE-2017-5754 needs KPTI; an active-KPTI host mitigates it."""
    m = parse_klog(_KLOG_FRESH_MICROCODE)
    entry = CveBaselineEntry(
        cve_id="CVE-2017-5754",
        family="Meltdown",
        affected_vendor="intel",
        requires_kpti=True,
        requires_microcode=False,
    )
    f = evaluate_cve(m, entry)
    assert f.mitigated is True
    assert f.mitigation_source == "kpti"


def test_spectre_v2_mitigated_when_microcode_current():
    m = parse_klog(_KLOG_FRESH_MICROCODE)
    entry = CveBaselineEntry(
        cve_id="CVE-2017-5715",
        family="Spectre v2",
        affected_vendor="both",
        requires_kpti=False,
        requires_microcode=True,
    )
    assert evaluate_cve(m, entry).mitigated is True


def test_spectre_v2_exposed_when_microcode_outdated():
    m = parse_klog(_KLOG_OUTDATED_MICROCODE)
    entry = CveBaselineEntry(
        cve_id="CVE-2017-5715",
        family="Spectre v2",
        affected_vendor="both",
        requires_kpti=False,
        requires_microcode=True,
    )
    f = evaluate_cve(m, entry)
    assert f.mitigated is False
    assert f.mitigation_source == "exposed"
    assert "microcode" in f.reason.lower()


def test_l1tf_requires_both_kpti_and_microcode():
    """L1TF (Foreshadow) needs KPTI AND microcode. Missing either → exposed."""
    fresh = parse_klog(_KLOG_FRESH_MICROCODE)
    outdated = parse_klog(_KLOG_OUTDATED_MICROCODE)
    entry = CveBaselineEntry(
        cve_id="CVE-2018-3615",
        family="L1TF",
        affected_vendor="intel",
        requires_kpti=True,
        requires_microcode=True,
    )
    assert evaluate_cve(fresh, entry).mitigated is True
    f = evaluate_cve(outdated, entry)
    assert f.mitigated is False
    assert "microcode" in f.reason.lower()


def test_compiler_level_cve_is_reported_mitigated():
    """Spectre v1 sits at the compiler level (retpoline / array_index_nospec).
    The audit returns mitigated=True with source='compiler' — we cannot
    verify the compiler invariant from a manifest, so we don't claim to."""
    m = build_empty()
    entry = CveBaselineEntry(
        cve_id="CVE-2017-5753",
        family="Spectre v1",
        affected_vendor="both",
        requires_kpti=False,
        requires_microcode=False,
    )
    f = evaluate_cve(m, entry)
    assert f.mitigated is True
    assert f.mitigation_source == "compiler"


# ---------------------------------------------------------------------------
# Inventory builder
# ---------------------------------------------------------------------------


def test_build_inventory_on_fresh_host_has_zero_exposure():
    """A PROTECTED + KPTI-active + fresh-microcode host should report
    zero CVE exposure."""
    m = parse_klog(_KLOG_FRESH_MICROCODE)
    inv = build_inventory(m)
    assert inv.cve_exposed_count == 0


def test_build_inventory_on_outdated_microcode_flags_multiple_cves():
    m = parse_klog(_KLOG_OUTDATED_MICROCODE)
    inv = build_inventory(m)
    assert (
        inv.cve_exposed_count >= 4
    ), f"Outdated microcode must surface ≥4 CVE exposures, got {inv.cve_exposed_count}"
    exposed_ids = {f.cve_id for f in inv.cve_findings if not f.mitigated}
    assert "CVE-2017-5715" in exposed_ids  # Spectre v2
    assert "CVE-2018-12126" in exposed_ids  # MDS / Fallout
    assert "CVE-2022-29900" in exposed_ids  # Retbleed
    assert "CVE-2022-40982" in exposed_ids  # Downfall


def test_inventory_carries_engagement_anchor():
    inv = build_inventory(build_empty(), anchor_sha="aeb3736")
    assert inv.anchor_sha == "aeb3736"


def test_inventory_json_is_valid_and_deterministic(tmp_path):
    """write_inventory_json must produce parseable JSON and be
    deterministic across two runs with the same inputs."""
    m = parse_klog(_KLOG_FRESH_MICROCODE)
    out = tmp_path / "compliance_inventory_v1.json"
    write_inventory_json(m, out_path=str(out))
    j1 = out.read_text(encoding="utf-8")
    write_inventory_json(m, out_path=str(out))
    j2 = out.read_text(encoding="utf-8")
    assert j1 == j2, "Inventory JSON must be deterministic on the same inputs"
    parsed = json.loads(j1)
    assert parsed["anchor_sha"] == "aeb3736"
    assert parsed["host_mode"] == "PROTECTED"
    assert "cve_findings" in parsed


# ---------------------------------------------------------------------------
# Risk-score wiring (P4.1 deduction)
# ---------------------------------------------------------------------------


def test_cve_exposure_lowers_risk_score():
    """Outdated microcode triggers both the flat -15 deduction AND the
    per-CVE deductions. The result must be strictly lower than the
    fresh-microcode score."""
    fresh = parse_klog(_KLOG_FRESH_MICROCODE)
    outdated = parse_klog(_KLOG_OUTDATED_MICROCODE)
    assert outdated.risk_score < fresh.risk_score - 15, (
        f"Outdated host must lose >15 points vs fresh; "
        f"got fresh={fresh.risk_score} outdated={outdated.risk_score}"
    )


def test_derive_cve_exposure_returns_nonzero_on_outdated_microcode():
    m_below_dict = parse_klog(_KLOG_OUTDATED_MICROCODE)
    assert derive_cve_exposure(m_below_dict) >= 4


def test_derive_cve_exposure_zero_on_clean_host():
    m = parse_klog(_KLOG_FRESH_MICROCODE)
    assert derive_cve_exposure(m) == 0


# ---------------------------------------------------------------------------
# Dependency scanner
# ---------------------------------------------------------------------------


def test_scan_requirements_flags_known_unsafe_pin(tmp_path):
    """A requirements file with a too-low cryptography pin must produce
    a DependencyFinding."""
    req = tmp_path / "requirements.txt"
    req.write_text("cryptography==41.0.0\nfastapi>=0.136.0\n", encoding="utf-8")
    findings = scan_requirements(str(req))
    names = {f.name for f in findings}
    assert "cryptography" in names


def test_scan_requirements_missing_file_returns_empty():
    findings = scan_requirements("/tmp/this/path/does/not/exist/req.txt")
    assert findings == []


# ---------------------------------------------------------------------------
# Engagement-marker pin
# ---------------------------------------------------------------------------


def test_engagement_marker_present_in_compliance_audit():
    src = Path(__file__).parent.parent.parent / "services" / "compliance_audit.py"
    assert "vOS·Adaptive·SHA=aeb3736·Phase=P4.1" in src.read_text(encoding="utf-8")
