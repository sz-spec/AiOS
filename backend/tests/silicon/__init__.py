"""
backend/tests/silicon
=====================

Sprint 14.2 (Gap 3) — pytest bridge to the death-battery runner.

The death-battery suite lives in
``VOS3_ULTIMATE_HANDOFF_2026/verification/death_battery_runner.py`` as a
standalone CLI. The 47 tests that return ``UNVERIFIED`` are the ones
that need real silicon to produce a measurement.

This package wraps each of those 47 tests as a ``pytest.mark.silicon``
test function. The pytest test passes if and only if the death-battery
result for that ID is ``PASS`` — ``UNVERIFIED`` becomes a pytest FAIL
when running under the silicon CI gate (and is automatically skipped
in normal CI via conftest.py's ``pytest_collection_modifyitems``).

What "silicon CI" looks like
----------------------------

The silicon CI workflow (``.github/workflows/silicon_battery.yml``)
provisions a self-hosted Azure DCedsv6 runner (Intel TDX-capable),
attaches the Intel Trust Authority for RTMR quotes, and invokes::

    pytest backend/tests/silicon/ --silicon -v

The pytest_collection_modifyitems hook in conftest.py lets these tests
through. The same suite, when invoked WITHOUT ``--silicon``, is silently
skipped — keeping the everyday ``pytest tests/`` developer loop fast.
"""
