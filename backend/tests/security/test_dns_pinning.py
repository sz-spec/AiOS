"""
backend/tests/security/test_dns_pinning.py

Sprint 15 / Item H4 — DNS pinning regression tests for runtime_firewall.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RF_PATH = (
    _REPO_ROOT / "backend" / "core" / "security" / "connectors" / "runtime_firewall.py"
)
_spec = importlib.util.spec_from_file_location(
    "vos3_runtime_firewall_under_test", _RF_PATH
)
rf = importlib.util.module_from_spec(_spec)
sys.modules["vos3_runtime_firewall_under_test"] = rf
_spec.loader.exec_module(rf)


@pytest.fixture
def fresh_firewall(monkeypatch):
    """Fresh firewall instance with deterministic policy (no env carryover)."""
    monkeypatch.delenv("VOS3_EGRESS_ALLOWLIST", raising=False)
    monkeypatch.delenv("VOS3_EGRESS_BLOCKLIST", raising=False)
    monkeypatch.delenv("VOS3_LOCALITY_PREFERENCE", raising=False)
    monkeypatch.delenv("VOS3_EGRESS_DENY_DEFAULT", raising=False)
    monkeypatch.delenv("VOS3_DNS_PIN_DISABLED", raising=False)
    return rf.RuntimeFirewall()


def _stub_resolver(monkeypatch, fw, host_to_ips: dict):
    """Replace _resolve_ips with a controllable lookup table.

    IP literals always short-circuit to themselves so callers that pass
    an IP directly don't need a stub entry.
    """
    import ipaddress

    def fake(host: str):
        try:
            ip = ipaddress.ip_address(host)
            return frozenset({str(ip)}), False
        except ValueError:
            pass
        ips = host_to_ips.get(host)
        if ips is None:
            return frozenset(), True  # gaierror
        return frozenset(ips), False

    monkeypatch.setattr(fw, "_resolve_ips", fake)


def test_pin_installs_on_first_resolve(monkeypatch, fresh_firewall):
    fw = fresh_firewall
    _stub_resolver(monkeypatch, fw, {"api.example.com": ["203.0.113.10"]})
    decision = fw.check_egress("https://api.example.com/v1/x")
    assert decision.allowed is True
    assert decision.outcome == rf.EgressOutcome.ALLOW
    # Pin was installed.
    pinned, _exp = fw._dns_pin_cache["api.example.com"]
    assert pinned == frozenset({"203.0.113.10"})


def test_pin_same_ips_subsequent_request_passes(monkeypatch, fresh_firewall):
    fw = fresh_firewall
    _stub_resolver(monkeypatch, fw, {"api.example.com": ["203.0.113.10"]})
    fw.check_egress("https://api.example.com/")  # install pin
    decision = fw.check_egress("https://api.example.com/another")
    assert decision.allowed is True


def test_pin_rebind_to_new_public_ip_rejected(monkeypatch, fresh_firewall):
    """Classic DNS rebinding: attacker controls api.example.com's A
    record; after we passed the SSRF gate they flip it to a different IP.
    We refuse to follow the new resolution."""
    fw = fresh_firewall
    state = {"ips": ["203.0.113.10"]}

    def fake(host):
        if host == "api.example.com":
            return frozenset(state["ips"]), False
        return frozenset(), True

    monkeypatch.setattr(fw, "_resolve_ips", fake)

    # First request — installs pin.
    fw.check_egress("https://api.example.com/")
    # Attacker swaps DNS.
    state["ips"] = ["198.51.100.99"]
    decision = fw.check_egress("https://api.example.com/")
    assert decision.allowed is False
    assert decision.outcome == rf.EgressOutcome.DENY_DNS_REBIND
    assert "rebinding" in decision.reason.lower()


def test_pin_rebind_to_private_ip_rejected(monkeypatch, fresh_firewall):
    """The classic exploit: rebind to 169.254.169.254 (AWS metadata) or
    127.0.0.1. Even though private-IP guard catches it on its own, the
    DNS-rebind path also fires; either outcome is acceptable security-wise."""
    fw = fresh_firewall
    state = {"ips": ["203.0.113.10"]}

    def fake(host):
        return frozenset(state["ips"]), False

    monkeypatch.setattr(fw, "_resolve_ips", fake)

    fw.check_egress("https://api.example.com/")
    state["ips"] = ["169.254.169.254"]  # AWS metadata
    decision = fw.check_egress("https://api.example.com/")
    assert decision.allowed is False
    assert decision.outcome in {
        rf.EgressOutcome.DENY_PRIVATE_IP,
        rf.EgressOutcome.DENY_DNS_REBIND,
    }


def test_pin_subset_of_pinned_set_passes(monkeypatch, fresh_firewall):
    """If the first resolution returned 3 IPs and a later resolution
    returns only 2 (a SUBSET), that's a normal load-balancer rotation,
    NOT a rebind. Allow it."""
    fw = fresh_firewall
    state = {"ips": ["203.0.113.10", "203.0.113.11", "203.0.113.12"]}

    def fake(host):
        return frozenset(state["ips"]), False

    monkeypatch.setattr(fw, "_resolve_ips", fake)

    fw.check_egress("https://api.example.com/")
    state["ips"] = ["203.0.113.11"]  # subset of original 3
    decision = fw.check_egress("https://api.example.com/")
    assert decision.allowed is True


def test_pin_disabled_via_env(monkeypatch):
    """Operator can opt out of pinning via VOS3_DNS_PIN_DISABLED=1."""
    monkeypatch.setenv("VOS3_DNS_PIN_DISABLED", "1")
    monkeypatch.delenv("VOS3_EGRESS_ALLOWLIST", raising=False)
    fw = rf.RuntimeFirewall()
    state = {"ips": ["203.0.113.10"]}

    def fake(host):
        return frozenset(state["ips"]), False

    monkeypatch.setattr(fw, "_resolve_ips", fake)

    fw.check_egress("https://api.example.com/")
    state["ips"] = ["198.51.100.99"]
    # With pinning disabled, the new public IP is allowed.
    decision = fw.check_egress("https://api.example.com/")
    assert decision.allowed is True


def test_reset_dns_pin_cache(monkeypatch, fresh_firewall):
    """Operator-callable reset for planned CDN rotation."""
    fw = fresh_firewall
    state = {"ips": ["203.0.113.10"]}

    def fake(host):
        return frozenset(state["ips"]), False

    monkeypatch.setattr(fw, "_resolve_ips", fake)

    fw.check_egress("https://api.example.com/")
    assert "api.example.com" in fw._dns_pin_cache
    fw.reset_dns_pin_cache()
    assert "api.example.com" not in fw._dns_pin_cache
    # After reset, new IP can install fresh pin.
    state["ips"] = ["198.51.100.99"]
    decision = fw.check_egress("https://api.example.com/")
    assert decision.allowed is True


def test_ip_literal_not_pinned(monkeypatch, fresh_firewall):
    """IP literals don't go through DNS; they ARE the pin. No cache entry."""
    fw = fresh_firewall
    _stub_resolver(monkeypatch, fw, {})  # no DNS needed
    decision = fw.check_egress("http://203.0.113.10/")
    # Public IP literal — passes the private check, doesn't install a pin.
    assert decision.allowed is True
    assert "203.0.113.10" not in fw._dns_pin_cache


def test_pin_ttl_expiry_allows_new_pin(monkeypatch):
    """After the pin TTL expires, a new resolution installs a fresh pin
    (it's a refresh, not a rebind alert)."""
    monkeypatch.setenv("VOS3_DNS_PIN_TTL_SECONDS", "1")
    monkeypatch.delenv("VOS3_DNS_PIN_DISABLED", raising=False)
    monkeypatch.delenv("VOS3_EGRESS_ALLOWLIST", raising=False)
    fw = rf.RuntimeFirewall()
    state = {"ips": ["203.0.113.10"]}

    def fake(host):
        return frozenset(state["ips"]), False

    monkeypatch.setattr(fw, "_resolve_ips", fake)

    fw.check_egress("https://api.example.com/")  # installs pin at T0

    # Manually expire the pin in the cache.
    with fw._lock:
        pinned, _exp = fw._dns_pin_cache["api.example.com"]
        fw._dns_pin_cache["api.example.com"] = (pinned, 0.0)  # expired in the past

    state["ips"] = ["198.51.100.99"]
    decision = fw.check_egress("https://api.example.com/")
    # Pin expired → fresh pin installed → not a rebind.
    assert decision.allowed is True
    pinned_new, _ = fw._dns_pin_cache["api.example.com"]
    assert "198.51.100.99" in pinned_new
