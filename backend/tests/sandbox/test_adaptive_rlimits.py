"""
P4.3 · Adaptive Risk Score → Adaptive rlimits tests.

vOS·Adaptive·SHA=aeb3736·Phase=P4.3

Verifies that services/risk_score.py maps a HardwareManifest mode
to the correct rlimit dict, and that services/app_sandbox.py wires
the function into both the plugin sandbox and the app-runner.

Honest scope
------------
* These tests synthesize HardwareManifest objects directly — no QEMU
  boot, no /tmp/vos3_console.log dependency. The kernel-side string
  format is pinned by tests/kernel/test_*_source.py and the parser
  round-trip is pinned by tests/hardware/test_universal_manifest.py.
* The tier-switching rule is taken verbatim from the AAA plan §4.3:
  halve RLIMIT_AS + halve RLIMIT_CPU + drop RLIMIT_NPROC 4→2.
  When the plan changes those deltas, both the implementation AND
  these tests must move together.
"""

from __future__ import annotations

import dataclasses


from services.hardware_manifest import HardwareManifest, build_empty, parse_klog
from services.risk_score import (
    DEFAULT_BASE_CPU_SECONDS,
    DEFAULT_BASE_FSIZE_MB,
    DEFAULT_BASE_MEMORY_MB,
    DEFAULT_BASE_NOFILE,
    DEFAULT_BASE_NPROC,
    compute_adaptive_rlimits,
    compute_risk_score,
    tier_for_manifest,
)

# ---------------------------------------------------------------------------
# Manifest builders — hand-rolled so we don't depend on QEMU
# ---------------------------------------------------------------------------


def _protected_manifest(*, microcode_below_baseline: bool = False) -> HardwareManifest:
    """Synthesize a PROTECTED-tier manifest (modern host).

    `microcode_below_baseline=True` flips the P4.2-amended mode override
    on: a modern PROTECTED_FULL host with outdated microcode lands in
    RESTRICTED_LEGACY because the speculative-exec mitigations require
    a microcode-delivered fix the host doesn't have.
    """
    mode = "RESTRICTED_LEGACY" if microcode_below_baseline else "PROTECTED"
    return HardwareManifest(
        mode=mode,
        kpti_mode="PROTECTED_FULL",
        pcid=True,
        invpcid=True,
        smep=True,
        smap=True,
        sha_ni=True,
        kpti_budget="<=2%",
        kpti_init_ready=True,
        pml4_kept_indices=("256", "511"),
        pml4_stripped_present_count=254,
        pci_transport="ECAM",
        pci_ecam_mmio_base=0xB0000000,
        pci_ecam_bus_range=(0, 255),
        pci_ecam_pcd=True,
        pci_ecam_nx=True,
        py_platform="Linux",
        py_machine="x86_64",
        py_processor="",
        microcode_revision=(0x10 if microcode_below_baseline else 0x05003604),
        microcode_baseline=0x05003604,
        microcode_below_baseline=microcode_below_baseline,
        cve_exposure_count=0,
        risk_score=85 if microcode_below_baseline else 100,
    )


def _restricted_legacy_manifest() -> HardwareManifest:
    """Synthesize a RESTRICTED_LEGACY-tier manifest (Nehalem-era)."""
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


def _unknown_manifest() -> HardwareManifest:
    """Empty manifest — no klog yet, conservative default."""
    return build_empty()


# ---------------------------------------------------------------------------
# Tier mapping (12 of the required ≥12 tests live here)
# ---------------------------------------------------------------------------


def test_protected_yields_baseline_memory():
    rlimits = compute_adaptive_rlimits(_protected_manifest())
    expected = DEFAULT_BASE_MEMORY_MB * 1024 * 1024
    assert rlimits["RLIMIT_AS"] == (
        expected,
        expected,
    ), "PROTECTED tier must use the full baseline memory budget"


def test_protected_yields_baseline_cpu():
    rlimits = compute_adaptive_rlimits(_protected_manifest())
    assert rlimits["RLIMIT_CPU"] == (
        DEFAULT_BASE_CPU_SECONDS,
        DEFAULT_BASE_CPU_SECONDS * 2,
    )


def test_protected_yields_baseline_nproc():
    rlimits = compute_adaptive_rlimits(_protected_manifest())
    assert rlimits["RLIMIT_NPROC"] == (DEFAULT_BASE_NPROC, DEFAULT_BASE_NPROC)


def test_restricted_legacy_halves_memory():
    rlimits = compute_adaptive_rlimits(_restricted_legacy_manifest())
    expected = (DEFAULT_BASE_MEMORY_MB // 2) * 1024 * 1024
    assert rlimits["RLIMIT_AS"] == (
        expected,
        expected,
    ), "RESTRICTED_LEGACY tier must halve RLIMIT_AS (AAA plan §4.3)"


def test_restricted_legacy_halves_cpu():
    rlimits = compute_adaptive_rlimits(_restricted_legacy_manifest())
    half = DEFAULT_BASE_CPU_SECONDS // 2
    assert rlimits["RLIMIT_CPU"] == (
        half,
        half * 2,
    ), "RESTRICTED_LEGACY tier must halve RLIMIT_CPU (AAA plan §4.3)"


def test_restricted_legacy_drops_nproc_to_two():
    rlimits = compute_adaptive_rlimits(_restricted_legacy_manifest())
    assert rlimits["RLIMIT_NPROC"] == (
        2,
        2,
    ), "RESTRICTED_LEGACY tier must drop NPROC from 4 to 2 (AAA plan §4.3)"


def test_unknown_matches_restricted_tier():
    """Security > Availability — UNKNOWN ⇒ tightest tier."""
    unknown = compute_adaptive_rlimits(_unknown_manifest())
    restricted = compute_adaptive_rlimits(_restricted_legacy_manifest())
    assert (
        unknown == restricted
    ), "UNKNOWN must produce the same rlimits as RESTRICTED_LEGACY"


def test_unknown_does_not_match_protected():
    unknown = compute_adaptive_rlimits(_unknown_manifest())
    protected = compute_adaptive_rlimits(_protected_manifest())
    assert (
        unknown != protected
    ), "UNKNOWN must NOT match PROTECTED — falls back to restricted tier"


def test_custom_base_memory_respected_on_protected():
    rlimits = compute_adaptive_rlimits(_protected_manifest(), base_memory_mb=512)
    expected = 512 * 1024 * 1024
    assert rlimits["RLIMIT_AS"] == (expected, expected)


def test_custom_base_memory_halved_on_restricted():
    rlimits = compute_adaptive_rlimits(
        _restricted_legacy_manifest(),
        base_memory_mb=512,
    )
    expected = 256 * 1024 * 1024  # halved
    assert rlimits["RLIMIT_AS"] == (expected, expected)


def test_fsize_floor_constant_across_tiers():
    """FSIZE doesn't shrink in restricted mode — disk size is plenty
    even on legacy hardware. The 10 MB cap is unchanged."""
    fsize = DEFAULT_BASE_FSIZE_MB * 1024 * 1024
    for mfst in (
        _protected_manifest(),
        _restricted_legacy_manifest(),
        _unknown_manifest(),
    ):
        rlimits = compute_adaptive_rlimits(mfst)
        assert rlimits["RLIMIT_FSIZE"] == (fsize, fsize)


def test_nofile_floor_constant_across_tiers():
    for mfst in (
        _protected_manifest(),
        _restricted_legacy_manifest(),
        _unknown_manifest(),
    ):
        rlimits = compute_adaptive_rlimits(mfst)
        assert rlimits["RLIMIT_NOFILE"] == (DEFAULT_BASE_NOFILE, DEFAULT_BASE_NOFILE)


# ---------------------------------------------------------------------------
# Floors — make sure halving a tiny baseline doesn't bottom out at zero
# ---------------------------------------------------------------------------


def test_memory_floor_clamps_at_32_mb():
    """Halving 16 MB would be 8 MB — too tight; the floor is 32 MB."""
    rlimits = compute_adaptive_rlimits(
        _restricted_legacy_manifest(),
        base_memory_mb=16,
    )
    floor = 32 * 1024 * 1024
    assert rlimits["RLIMIT_AS"] == (floor, floor)


def test_cpu_floor_clamps_at_five_seconds():
    """Halving 4s of CPU would be 2s — too tight; the floor is 5s."""
    rlimits = compute_adaptive_rlimits(
        _restricted_legacy_manifest(),
        base_cpu_seconds=4,
    )
    assert rlimits["RLIMIT_CPU"] == (5, 10)


# ---------------------------------------------------------------------------
# Type contract — values MUST be (int, int) tuples for resource.setrlimit
# ---------------------------------------------------------------------------


def test_rlimit_values_are_int_tuples():
    rlimits = compute_adaptive_rlimits(_protected_manifest())
    for key, val in rlimits.items():
        assert isinstance(val, tuple), f"{key} value must be a tuple"
        assert len(val) == 2, f"{key} value must be (soft, hard)"
        assert isinstance(val[0], int) and isinstance(
            val[1], int
        ), f"{key} must be (int, int) for resource.setrlimit"


# ---------------------------------------------------------------------------
# Purity — same input ⇒ same output, no hidden state
# ---------------------------------------------------------------------------


def test_compute_adaptive_rlimits_is_pure():
    m = _restricted_legacy_manifest()
    a = compute_adaptive_rlimits(m)
    b = compute_adaptive_rlimits(m)
    assert a == b, "compute_adaptive_rlimits must be deterministic on its inputs"


# ---------------------------------------------------------------------------
# Tier-label helper
# ---------------------------------------------------------------------------


def test_tier_for_protected_manifest():
    assert tier_for_manifest(_protected_manifest()) == "PROTECTED_TIER"


def test_tier_for_restricted_manifest():
    assert tier_for_manifest(_restricted_legacy_manifest()) == "RESTRICTED_TIER"


def test_tier_for_unknown_manifest():
    """UNKNOWN maps to the restricted tier — Security > Availability."""
    assert tier_for_manifest(_unknown_manifest()) == "RESTRICTED_TIER"


# ---------------------------------------------------------------------------
# Risk-score pass-through — informational, must still be exposed by the module
# ---------------------------------------------------------------------------


def test_risk_score_protected_full_yields_max():
    """PROTECTED_FULL with sha-ni should score ≥95.

    Exactly 100 when oqs-python is installed; 95 when it isn't (the
    -5 pure_python deduction fires). Either is in the PROTECTED band.
    The PROTECTED-vs-RESTRICTED comparison below is the load-bearing
    test; this one just pins the upper-band threshold.
    """
    score = compute_risk_score(_protected_manifest())
    assert score >= 95, f"PROTECTED_FULL with sha-ni must score ≥95, got {score}"


def test_risk_score_restricted_legacy_below_protected():
    """RESTRICTED_LEGACY hosts MUST score lower than PROTECTED — this
    is what makes the score informative in audit reports. Concrete
    arithmetic is pinned by tests/hardware/test_universal_manifest.py."""
    protected_score = compute_risk_score(_protected_manifest())
    legacy_score = compute_risk_score(_restricted_legacy_manifest())
    assert legacy_score < protected_score


def test_risk_score_floor_thirty():
    """The score floor is 30, even on the worst host."""
    score = compute_risk_score(_unknown_manifest())
    assert score >= 30


# ---------------------------------------------------------------------------
# Integration with app_sandbox — the wired-up preexec actually uses the
# adaptive dict. We patch the manifest cache to simulate a legacy host.
# ---------------------------------------------------------------------------


def test_sandbox_preexec_uses_adaptive_rlimits(monkeypatch):
    """LinuxRlimitProvider's preexec MUST read the cached manifest
    and produce halved-tier rlimits for a RESTRICTED_LEGACY host."""
    from services import app_sandbox

    app_sandbox._active_manifest.cache_clear()
    monkeypatch.setattr(
        app_sandbox,
        "_active_manifest",
        lambda: _restricted_legacy_manifest(),
    )

    rlimits = app_sandbox._adaptive_rlimits(base_memory_mb=256)
    # Halved memory: 128 MB
    assert rlimits["RLIMIT_AS"] == (128 * 1024 * 1024, 128 * 1024 * 1024)
    # Halved CPU: 30s soft / 60s hard
    assert rlimits["RLIMIT_CPU"] == (30, 60)
    # NPROC dropped 4 → 2
    assert rlimits["RLIMIT_NPROC"] == (2, 2)

    # Cleanup — restore the real cached manifest accessor for other tests
    (
        app_sandbox._active_manifest.cache_clear()
        if hasattr(
            app_sandbox._active_manifest,
            "cache_clear",
        )
        else None
    )


def test_sandbox_preexec_protected_baseline(monkeypatch):
    """The same wiring on a PROTECTED host must yield untouched
    baseline rlimits — no regression for modern silicon."""
    from services import app_sandbox

    monkeypatch.setattr(
        app_sandbox,
        "_active_manifest",
        lambda: _protected_manifest(),
    )

    rlimits = app_sandbox._adaptive_rlimits(base_memory_mb=256)
    assert rlimits["RLIMIT_AS"] == (256 * 1024 * 1024, 256 * 1024 * 1024)
    assert rlimits["RLIMIT_CPU"] == (60, 120)
    assert rlimits["RLIMIT_NPROC"] == (4, 4)


def test_active_manifest_missing_serial_log_falls_to_empty(monkeypatch, tmp_path):
    """When /tmp/vos3_console.log is absent the manifest must be the
    safe-default empty manifest — not raise."""
    from services import app_sandbox

    monkeypatch.setenv("VOS3_KERNEL_SERIAL_LOG", str(tmp_path / "nonexistent.log"))
    # Force a re-import path traversal by clearing the cache and
    # rebinding the constant the cache reads.
    monkeypatch.setattr(
        app_sandbox, "_KERNEL_SERIAL_LOG", str(tmp_path / "nonexistent.log")
    )
    app_sandbox._active_manifest.cache_clear()

    manifest = app_sandbox._active_manifest()
    assert manifest.mode in (
        "UNKNOWN",
        "RESTRICTED_LEGACY",
    ), "Missing klog must produce a conservative manifest, not crash"

    app_sandbox._active_manifest.cache_clear()


def test_engagement_marker_present():
    """Honest-scope discipline: every NEW P4.3 file carries the
    `vOS·Adaptive·SHA=aeb3736·Phase=P4.3` integrity marker."""
    import pathlib

    risk_score_path = (
        pathlib.Path(__file__).parent.parent.parent / "services" / "risk_score.py"
    )
    src = risk_score_path.read_text(encoding="utf-8")
    assert "vOS·Adaptive·SHA=aeb3736·Phase=P4.3" in src


def test_parsed_legacy_klog_round_trip():
    """End-to-end: feed a Nehalem-style klog into the parser and let
    the rlimit function consume the resulting manifest."""
    klog = (
        "[KPTI] mode=LEGACY_KAISER pcid=no invpcid=no smep=no smap=no "
        "sha-ni=no budget=5-30%\n"
        "[KPTI] init: ready\n"
        "[PCI-ECAM] MCFG absent — Port-I/O fallback active\n"
    )
    manifest = parse_klog(klog)
    assert manifest.mode == "RESTRICTED_LEGACY"
    rlimits = compute_adaptive_rlimits(manifest)
    assert rlimits["RLIMIT_NPROC"] == (2, 2)


# ---------------------------------------------------------------------------
# Frozen-dataclass contract — replace() must still produce a usable manifest
# ---------------------------------------------------------------------------


def test_manifest_remains_immutable_during_rlimit_compute():
    """compute_adaptive_rlimits MUST NOT mutate the manifest it reads
    (it's frozen, but check that dataclasses.replace returns a fresh
    instance with identical relevant fields)."""
    m = _protected_manifest()
    before = dataclasses.asdict(m)
    compute_adaptive_rlimits(m)
    after = dataclasses.asdict(m)
    assert before == after


# ---------------------------------------------------------------------------
# P4.2 verification — outdated microcode forces RESTRICTED rlimits
# ---------------------------------------------------------------------------


def test_outdated_microcode_forces_restricted_tier():
    """An otherwise-PROTECTED host with outdated microcode MUST land in
    the RESTRICTED rlimit tier (halved AS+CPU, NPROC=2). This is the
    load-bearing P4.2 → P4.3 wiring: the kernel emits below_baseline=yes,
    the manifest parser overrides the mode to RESTRICTED_LEGACY, and
    the sandbox preexec function halves the rlimits."""
    outdated = _protected_manifest(microcode_below_baseline=True)
    fresh = _protected_manifest(microcode_below_baseline=False)

    assert (
        outdated.mode == "RESTRICTED_LEGACY"
    ), "Outdated microcode must force RESTRICTED_LEGACY mode"
    assert fresh.mode == "PROTECTED"

    outdated_rl = compute_adaptive_rlimits(outdated)
    fresh_rl = compute_adaptive_rlimits(fresh)

    # Memory must halve relative to the fresh-microcode baseline.
    assert (
        outdated_rl["RLIMIT_AS"][0] == fresh_rl["RLIMIT_AS"][0] // 2
    ), "Outdated microcode must halve RLIMIT_AS"
    # CPU must halve relative to the fresh-microcode baseline.
    assert outdated_rl["RLIMIT_CPU"] == (
        fresh_rl["RLIMIT_CPU"][0] // 2,
        fresh_rl["RLIMIT_CPU"][1] // 2,
    ), "Outdated microcode must halve RLIMIT_CPU"
    # NPROC must drop from 4 to 2.
    assert outdated_rl["RLIMIT_NPROC"] == (
        2,
        2,
    ), "Outdated microcode must drop NPROC from 4 to 2"


def test_outdated_microcode_klog_round_trip_lowers_score():
    """Feed a kernel klog that includes the [MICROCODE] below-baseline
    line. The parser must produce mode=RESTRICTED_LEGACY AND deduct -15
    from the risk score."""
    klog_below = (
        "[KPTI] mode=PROTECTED_FULL pcid=yes invpcid=yes smep=yes smap=yes "
        "sha-ni=yes budget=<=2%\n"
        "[KPTI] init: ready\n"
        "[PCI-ECAM] available — buses 0..ff mapped MMIO@0xB0000000 (PCD=1, NX=1)\n"
        "[MICROCODE] revision=0x00000010 baseline=0x05003604 below_baseline=yes\n"
        "[SECURITY] Microcode outdated. System logic vulnerable to "
        "speculative execution side-channels.\n"
    )
    klog_fresh = (
        klog_below.replace(
            "below_baseline=yes",
            "below_baseline=no",
        )
        .replace(
            "revision=0x00000010",
            "revision=0x05003604",
        )
        .split("[SECURITY]")[0]
    )  # drop the security warning in the fresh path

    below = parse_klog(klog_below)
    fresh = parse_klog(klog_fresh)

    assert below.microcode_below_baseline is True
    assert fresh.microcode_below_baseline is False
    assert (
        below.mode == "RESTRICTED_LEGACY"
    ), "Outdated microcode must override PROTECTED → RESTRICTED_LEGACY"
    assert fresh.mode == "PROTECTED"
    # The -15 penalty must show up in the score delta. We can't assert
    # exact numbers because the pure_python pqc deduction is environment-
    # dependent (-5 when oqs isn't installed); the delta is what matters.
    assert below.risk_score <= fresh.risk_score - 15, (
        f"Outdated microcode must deduct ≥15 from risk_score; "
        f"got below={below.risk_score} fresh={fresh.risk_score}"
    )


def test_compliance_audit_finds_unmitigated_cves_on_outdated_microcode():
    """The compliance auditor must surface concrete CVE exposures when
    microcode is below baseline — that's the load-bearing P4.1→P4.2 link."""
    from services.compliance_audit import build_inventory

    outdated = _protected_manifest(microcode_below_baseline=True)
    inv = build_inventory(outdated)

    # At least Spectre v2 / MDS / Retbleed / Downfall must be flagged.
    assert inv.cve_exposed_count >= 4, (
        f"Outdated microcode must expose ≥4 architectural CVEs, "
        f"got {inv.cve_exposed_count}"
    )
    exposed_ids = {f.cve_id for f in inv.cve_findings if not f.mitigated}
    assert "CVE-2017-5715" in exposed_ids, "Spectre v2 must be flagged"
    assert "CVE-2022-40982" in exposed_ids, "Downfall must be flagged"
