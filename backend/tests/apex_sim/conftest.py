"""
backend/tests/apex_sim/conftest.py — pytest fixtures wiring the sim.

Usage in a test file inside this directory:

    def test_my_thing(apex_driver, compliance_store_clean):
        apex_driver.inject_audit_event(category=0x10, rc=2, slot_id=0,
                                        digest="ab" * 8)
        ring = VBusRingBuffer(apex_driver, compliance_store_clean)
        assert ring.drain_once()["new"] == 1
"""

from __future__ import annotations


import pytest

from tests.apex_sim import ApexSimDriver


@pytest.fixture
def apex_driver() -> ApexSimDriver:
    """A fresh in-memory sim driver per test."""
    return ApexSimDriver()


@pytest.fixture
def compliance_store_clean(tmp_path, monkeypatch):
    """A ComplianceStore pointed at a per-test SQLite file."""
    from services.compliance_store import ComplianceStore

    db_path = str(tmp_path / "compliance.db")
    monkeypatch.setenv("VOS3_COMPLIANCE_DB_PATH", db_path)
    return ComplianceStore(db_path=db_path)


@pytest.fixture
def policy_override_apex(monkeypatch, apex_driver):
    """Bind the policy_override service to the apex sim driver for the test."""
    # Best-effort: many service modules look up the driver lazily via a
    # ``get_vbus_driver()`` factory; we monkeypatch that so the sim is used.
    try:
        from services import vbus_driver as vd_mod

        monkeypatch.setattr(vd_mod, "get_vbus_driver", lambda: apex_driver)
    except ImportError:
        pass
    return apex_driver
