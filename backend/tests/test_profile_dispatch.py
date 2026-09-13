"""
backend/tests/test_profile_dispatch.py — VOS_PROFILE dispatcher coverage.

Tests behaviour of the Profile enum's `requires_*` predicates across the
three valid values, plus fail-fast on invalid VOS_PROFILE strings, plus
the integrations in compliance_store and attestation_service.
"""

from __future__ import annotations

import os
import pytest

import sys
from pathlib import Path

# Make `backend/` importable when invoked from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vos_profile import Profile, reload_for_test  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_profile_env():
    """Save and restore VOS_PROFILE around every test."""
    original = os.environ.get("VOS_PROFILE")
    yield
    if original is None:
        os.environ.pop("VOS_PROFILE", None)
    else:
        os.environ["VOS_PROFILE"] = original
    reload_for_test()


def _set(profile: str | None) -> Profile:
    if profile is None:
        os.environ.pop("VOS_PROFILE", None)
    else:
        os.environ["VOS_PROFILE"] = profile
    return reload_for_test()


# ---------------------------------------------------------------------------
# Default + parsing
# ---------------------------------------------------------------------------


def test_default_is_community_when_unset():
    assert _set(None) is Profile.COMMUNITY


def test_empty_string_is_community():
    assert _set("") is Profile.COMMUNITY


def test_case_insensitive():
    assert _set("FORTRESS") is Profile.FORTRESS
    assert _set("Enterprise") is Profile.ENTERPRISE


def test_invalid_profile_fails_fast():
    os.environ["VOS_PROFILE"] = "platinum"
    with pytest.raises(RuntimeError, match="not a valid profile"):
        reload_for_test()


# ---------------------------------------------------------------------------
# Capability matrix per spec / docs/PROFILES.md
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "profile,expected",
    [
        (Profile.COMMUNITY, False),
        (Profile.ENTERPRISE, False),
        (Profile.FORTRESS, True),
    ],
)
def test_requires_pq_sig(profile, expected):
    assert profile.requires_pq_sig() is expected


@pytest.mark.parametrize(
    "profile,expected",
    [
        (Profile.COMMUNITY, False),
        (Profile.ENTERPRISE, True),
        (Profile.FORTRESS, True),
    ],
)
def test_requires_hybrid_classical(profile, expected):
    assert profile.requires_hybrid_classical() is expected


@pytest.mark.parametrize(
    "profile,expected",
    [
        (Profile.COMMUNITY, False),
        (Profile.ENTERPRISE, False),
        (Profile.FORTRESS, True),
    ],
)
def test_requires_sigstore_verify(profile, expected):
    assert profile.requires_sigstore_verify() is expected


@pytest.mark.parametrize(
    "profile,expected",
    [
        (Profile.COMMUNITY, False),
        (Profile.ENTERPRISE, False),
        (Profile.FORTRESS, True),
    ],
)
def test_requires_encrypted_compliance_store(profile, expected):
    assert profile.requires_encrypted_compliance_store() is expected


@pytest.mark.parametrize(
    "profile,expected",
    [
        (Profile.COMMUNITY, False),
        (Profile.ENTERPRISE, False),
        (Profile.FORTRESS, True),
    ],
)
def test_requires_tdx_attestation(profile, expected):
    assert profile.requires_tdx_attestation() is expected


@pytest.mark.parametrize(
    "profile,expected",
    [
        (Profile.COMMUNITY, False),
        (Profile.ENTERPRISE, False),
        (Profile.FORTRESS, True),
    ],
)
def test_expects_hyperv_kernel(profile, expected):
    assert profile.expects_hyperv_kernel() is expected


# ---------------------------------------------------------------------------
# Integration: compliance_store fortress gate
# ---------------------------------------------------------------------------


def test_compliance_store_under_fortress_without_key_refuses():
    """Under fortress, compliance_store must refuse to open without a key."""
    _set("fortress")
    os.environ.pop("VOS3_COMPLIANCE_KEY", None)
    from services.compliance_store import ComplianceStore  # noqa: WPS433

    with pytest.raises(RuntimeError, match="VOS3_COMPLIANCE_KEY"):
        ComplianceStore(db_path="/tmp/_test_fortress_no_key.db")


def test_compliance_store_under_community_works_without_key(tmp_path):
    """Under community, plain sqlite3 is fine; no key required."""
    _set("community")
    os.environ.pop("VOS3_COMPLIANCE_KEY", None)
    from services.compliance_store import ComplianceStore  # noqa: WPS433

    store = ComplianceStore(db_path=str(tmp_path / "community.db"))
    assert store.db_path.endswith("community.db")
