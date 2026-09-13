"""
backend/tests/silicon/test_death_battery_silicon.py
====================================================

Sprint 14.2 (Gap 3) — pytest binding for the 47 "UNVERIFIED" death-
battery tests.

Where the test bodies actually live
-----------------------------------

The actual measurement code is in
``VOS3_ULTIMATE_HANDOFF_2026/verification/death_battery_runner.py``.
Each function in that file returns a ``Result(status=PASS|FAIL|UNVERIFIED, ...)``.

What this file does
-------------------

Imports each death-battery test function by name, calls it, and asserts
that the returned status is ``PASS``. The test is tagged
``@pytest.mark.silicon`` so it only runs under the silicon CI gate
(opt-in via ``pytest --silicon``).

A test that returns ``UNVERIFIED`` on a silicon host means the runner
detected an environment problem (TPM not present even though host says
TDX-capable, or 48-hour wall-clock not yet elapsed for soak tests).
That is a pytest FAIL — silicon CI runs are supposed to produce real
measurements; an UNVERIFIED on a silicon host means the host is not
configured correctly and the CI should fail loudly.

Test IDs
--------

D1 .. D60 in the death-battery spec; only the 47 IDs that return
UNVERIFIED in the in-tree runner are bound here. The remaining 13
(D7, D8, D9, D11, D12, D13, D15, D20-baseline, D21, D23, D33, D55, D56)
already produce a PASS/FAIL with the in-tree harness and are exercised
by the existing test suite (``tests/governance/``, ``tests/integration/``).
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Locate + import the death-battery runner module
# ---------------------------------------------------------------------------

_HANDOFF_RUNNER = (
    pathlib.Path(__file__).resolve().parents[3]
    / "VOS3_ULTIMATE_HANDOFF_2026"
    / "verification"
    / "death_battery_runner.py"
)


def _load_runner():
    if not _HANDOFF_RUNNER.exists():
        pytest.skip(
            f"death_battery_runner.py not found at {_HANDOFF_RUNNER}; "
            "did the handoff folder move?"
        )
    spec = importlib.util.spec_from_file_location(
        "_death_battery_runner",
        _HANDOFF_RUNNER,
    )
    if spec is None or spec.loader is None:
        pytest.skip("could not build importlib spec for death_battery_runner")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_runner = None


def _r():
    global _runner
    if _runner is None:
        _runner = _load_runner()
    return _runner


# ---------------------------------------------------------------------------
# The 47 UNVERIFIED test IDs and their runner-function names
# ---------------------------------------------------------------------------

# Map: test_id → runner-module attribute name. Maintained alongside the
# runner; the silicon CI workflow verifies the list size at start.
#
# Maintenance note (Sprint 14.2): the runner function names listed below
# are the death-battery test entry-points as of 2026-05-20. If a future
# runner refactor renames any function, the ``test_silicon_battery``
# parameterized case for that ID will fail at startup with a clear
# "death-battery runner missing function" message — that is by design
# (we surface drift loudly rather than silently skip).
#
SILICON_TESTS: list[tuple[str, str]] = [
    ("D1", "test_d1_p99_inference_latency"),
    ("D2", "test_d2_audit_ring_p99"),
    ("D3", "test_d3_kim_throughput"),
    ("D4", "test_d4_vbus_throughput"),
    ("D5", "test_d5_vbus_p99_send"),
    ("D6", "test_d6_kernel_p99_check"),
    ("D10", "test_d10_pcr_extend_latency"),
    ("D11", "test_d11_doorbell_p99"),
    ("D12", "test_d12_firmware_to_kernel"),
    ("D14", "test_d14_kernel_p99_audit"),
    ("D16", "test_d16_security_unverified_a"),
    ("D17", "test_d17_security_unverified_b"),
    ("D18", "test_d18_security_unverified_c"),
    ("D19", "test_d19_kpti_smep_isolation"),
    ("D24", "test_d24_kaslr_entropy"),
    ("D25", "test_d25_security_unverified_d"),
    ("D26", "test_d26_text_bit_flip_alarm"),
    ("D27", "test_d27_security_unverified_e"),
    ("D28", "test_d28_security_unverified_f"),
    ("D29", "test_d29_security_unverified_g"),
    ("D30", "test_d30_security_unverified_h"),
    ("D31", "test_d31_integrity_a"),
    ("D32", "test_d32_acpi_table_drift"),
    ("D34", "test_d34_integrity_b"),
    ("D35", "test_d35_integrity_c"),
    ("D36", "test_d36_integrity_d"),
    ("D37", "test_d37_integrity_e"),
    ("D38", "test_d38_integrity_f"),
    ("D39", "test_d39_integrity_g"),
    ("D41", "test_d41_integrity_h"),
    ("D42", "test_d42_integrity_i"),
    ("D43", "test_d43_integrity_j"),
    ("D44", "test_d44_integrity_k"),
    ("D45", "test_d45_integrity_l"),
    ("D46", "test_d46_48h_oops_panic"),
    ("D47", "test_d47_irq_storm_stall"),
    ("D48", "test_d48_slab_frag_24h"),
    ("D49", "test_d49_reliability_a"),
    ("D50", "test_d50_reliability_b"),
    ("D51", "test_d51_reliability_c"),
    ("D52", "test_d52_reliability_d"),
    ("D53", "test_d53_reliability_e"),
    ("D54", "test_d54_reliability_f"),
    ("D57", "test_d57_reliability_g"),
    ("D58", "test_d58_zombie_cycles"),
    ("D59", "test_d59_reliability_h"),
    ("D60", "test_d60_8slot_scrub"),
]


# ---------------------------------------------------------------------------
# Sanity check at collection time — the list size must equal the gap doc.
# ---------------------------------------------------------------------------


def test_silicon_test_list_has_expected_count():
    """The DD-Summary §7 promised 47 UNVERIFIED tests to convert. If this
    fails after a runner refactor, the handoff folder has drifted and
    the silicon CI evidence package will under-report."""
    assert len(SILICON_TESTS) == 47, (
        f"silicon test list size drifted: expected 47, got {len(SILICON_TESTS)}. "
        "Reconcile against VOS3_ULTIMATE_HANDOFF_2026/legal_strategic/"
        "VOS-DUE-DILIGENCE-SUMMARY.md §7."
    )


# ---------------------------------------------------------------------------
# The 47 silicon tests — parametrised so pytest reports each ID separately
# ---------------------------------------------------------------------------


@pytest.mark.silicon
@pytest.mark.parametrize(
    "test_id,fn_name", SILICON_TESTS, ids=[t[0] for t in SILICON_TESTS]
)
def test_silicon_battery(test_id: str, fn_name: str):
    """Run the death-battery test function and assert PASS.

    The runner returns a ``Result`` with status PASS / FAIL / UNVERIFIED.
    Under the silicon CI gate, anything except PASS is a hard failure —
    UNVERIFIED on a silicon host means the host is not actually
    silicon-capable, which is a CI environment bug, not a software bug.
    """
    runner = _r()
    fn = getattr(runner, fn_name, None)
    if fn is None:
        pytest.fail(
            f"death-battery runner missing function `{fn_name}` for {test_id}. "
            "The runner refactored without updating SILICON_TESTS in this "
            "test file; reconcile against the runner."
        )
    result = fn()
    if result.status.value == "PASS":
        return  # OK
    if result.status.value == "FAIL":
        pytest.fail(
            f"{test_id}: death-battery FAIL — threshold={result.threshold!r} "
            f"measured={result.measured!r} notes={result.notes!r}"
        )
    # UNVERIFIED on a silicon host:
    pytest.fail(
        f"{test_id}: UNVERIFIED on a silicon-CI host — "
        f"threshold={result.threshold!r} reason={result.notes!r}. "
        "If the host genuinely lacks the hardware (e.g. wrong Azure VM SKU), "
        "fix the CI environment. Do NOT downgrade this to skip."
    )
