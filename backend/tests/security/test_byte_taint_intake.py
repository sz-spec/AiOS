"""
backend/tests/security/test_byte_taint_intake.py

Sprint 17 / Cluster C-1 operationalization — integration tests for
ByteTaintEngine wired into DualLLMRouter.route_tool_result().

Covers:
- Default-on behavior: every tool result returns a TaintedBuffer
  attached to the ToolResultPacket.
- Buffer content matches the raw tool output byte-for-byte.
- env-flag (VOS3_DISABLE_BYTE_TAINT_INTAKE) disables the engine cleanly
  (buffer is None; existing semantics preserved).
- bypass path (VOS3_DISABLE_DUAL_LLM) still attaches the buffer — byte
  taint runs even when the dual-LLM defense is off.
- EchoLeak-class scenario: a web-search result with attacker-injected
  TOXIC bytes between clean public bytes. Verifies:
    * full-blob max_color is TOXIC (matches C7 baseline)
    * but a slice of the buffer over only the clean bytes has max_color
      <= UNTRUSTED — which is what makes byte-level worth the 100%
      memory overhead.
- Safe-fail: if the engine raises (we monkeypatch it to do so), the
  router still produces a valid packet with tainted_buffer=None and
  logs a warning. Tool flow does NOT break.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load the modules under test directly — pytest's sys.path includes
# backend/ via tests/conftest.py, but loading by file path keeps these
# tests independent of any package-init state.
ql = _load_module(
    "vos3_quarantined_llm_under_test_bti",
    _REPO_ROOT / "backend" / "services" / "quarantined_llm.py",
)
dr = _load_module(
    "vos3_dual_llm_router_under_test_bti",
    _REPO_ROOT / "backend" / "ai" / "agents" / "dual_llm_router.py",
)
te = _load_module(
    "vos3_tev2_under_test_bti",
    _REPO_ROOT / "backend" / "security" / "taint_engine_v2.py",
)


# ---------------------------------------------------------------------------
# Stub LLM — copied from test_dual_llm_defense.py shape so we don't need to
# rely on any real provider.
# ---------------------------------------------------------------------------


class StubLLM:
    def __init__(self, default: str = "summary of the tool output"):
        self.default = default
        self.calls: list[tuple[str, int]] = []

    def generate(self, prompt: str, *, max_tokens: int) -> str:
        self.calls.append((prompt, max_tokens))
        return self.default


def _make_router() -> "dr.DualLLMRouter":
    stub = StubLLM()
    qllm = ql.QuarantinedLLM(llm=stub)
    return dr.DualLLMRouter(quarantined_llm=qllm)


# ---------------------------------------------------------------------------
# Default-on behavior
# ---------------------------------------------------------------------------


def test_default_route_attaches_tainted_buffer(monkeypatch):
    monkeypatch.delenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, raising=False)
    monkeypatch.delenv(dr.ENV_DISABLE_DUAL_LLM, raising=False)
    router = _make_router()
    raw = "search result: vOS is a sovereign agent OS"
    packet = router.route_tool_result("web_search", raw)
    assert packet.tainted_buffer is not None
    assert packet.tainted_buffer_max_color is not None
    # Default label for tool outputs is UNTRUSTED (= 1).
    assert packet.tainted_buffer_max_color == int(te.TaintLabel.UNTRUSTED)


def test_buffer_content_matches_raw_output(monkeypatch):
    monkeypatch.delenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, raising=False)
    monkeypatch.delenv(dr.ENV_DISABLE_DUAL_LLM, raising=False)
    router = _make_router()
    raw = "row1\nrow2\nrow3 with unicode: שלום עולם"
    packet = router.route_tool_result("web_search", raw)
    expected_bytes = raw.encode("utf-8", errors="replace")
    assert packet.tainted_buffer.content == expected_bytes


def test_buffer_attached_for_every_tool_kind(monkeypatch):
    """Router is the single chokepoint — coloring runs for ANY tool, not
    just web_search."""
    monkeypatch.delenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, raising=False)
    monkeypatch.delenv(dr.ENV_DISABLE_DUAL_LLM, raising=False)
    router = _make_router()
    for tool in ["web_search", "read_file", "fetch_url", "calendar_query"]:
        packet = router.route_tool_result(tool, "stub output")
        assert packet.tainted_buffer is not None, f"missing buffer for {tool}"


# ---------------------------------------------------------------------------
# Env-flag disable path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag_value", ["1", "true", "yes", "ON"])
def test_env_flag_disables_byte_taint_intake(monkeypatch, flag_value):
    monkeypatch.setenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, flag_value)
    monkeypatch.delenv(dr.ENV_DISABLE_DUAL_LLM, raising=False)
    router = _make_router()
    packet = router.route_tool_result("web_search", "some output")
    assert packet.tainted_buffer is None
    assert packet.tainted_buffer_max_color is None
    # Existing semantics still produce a valid summary.
    assert isinstance(packet.safe_summary, str)


def test_env_flag_off_means_engine_active(monkeypatch):
    """Explicit '0' means engine ON, matching the env-flag convention."""
    monkeypatch.setenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, "0")
    monkeypatch.delenv(dr.ENV_DISABLE_DUAL_LLM, raising=False)
    router = _make_router()
    packet = router.route_tool_result("web_search", "some output")
    assert packet.tainted_buffer is not None


# ---------------------------------------------------------------------------
# Bypass interaction (defense disabled but taint still on)
# ---------------------------------------------------------------------------


def test_bypass_via_env_still_attaches_taint_buffer(monkeypatch):
    """If the operator turns OFF the dual-LLM defense but leaves byte
    taint enabled, packets still carry the buffer so downstream egress
    checks can use it as a safety net."""
    monkeypatch.delenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, raising=False)
    monkeypatch.setenv(dr.ENV_DISABLE_DUAL_LLM, "1")
    router = _make_router()
    packet = router.route_tool_result("web_search", "raw output")
    assert packet.bypassed is True
    assert packet.tainted_buffer is not None
    assert packet.tainted_buffer_max_color == int(te.TaintLabel.UNTRUSTED)


def test_bypass_with_taint_disabled_yields_no_buffer(monkeypatch):
    monkeypatch.setenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, "1")
    monkeypatch.setenv(dr.ENV_DISABLE_DUAL_LLM, "1")
    router = _make_router()
    packet = router.route_tool_result("web_search", "raw output")
    assert packet.bypassed is True
    assert packet.tainted_buffer is None


# ---------------------------------------------------------------------------
# EchoLeak-class — the motivating real-world case for C-1 vs C7
# ---------------------------------------------------------------------------


def test_echoleak_scenario_byte_level_avoids_over_contamination(monkeypatch):
    """A web search returns 100 bytes of clean public document body
    plus 30 bytes of attacker-injected exfiltration payload between
    offsets [100, 130). Verifies that:

      - The full blob's max color is TOXIC (matches what C7 blob-level
        would also conclude — i.e. the safety floor is preserved).
      - But a slice over [0:100) (the clean body) has max color
        UNTRUSTED, NOT TOXIC. This is the byte-level win: privileged
        downstream code consuming only those 100 bytes does NOT inherit
        the attacker's TOXIC label, so egress on the clean summary is
        permitted instead of refused.

    This test is the C-1 raison d'etre. It is the same logic as the
    end-to-end test in test_taint_engine_v2.py, but executed through
    the production DualLLMRouter chokepoint — not against the engine
    in isolation.
    """
    monkeypatch.delenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, raising=False)
    monkeypatch.delenv(dr.ENV_DISABLE_DUAL_LLM, raising=False)
    router = _make_router()

    clean_body = b"A" * 100
    attacker_payload = b"B" * 30
    raw = (clean_body + attacker_payload).decode("latin-1")

    packet = router.route_tool_result("web_search", raw)
    assert packet.tainted_buffer is not None
    buf = packet.tainted_buffer

    # The router labels uniformly at intake (UNTRUSTED, since the source
    # is untrusted). The attacker bytes don't get an automatic upgrade —
    # that's the downstream pattern-detector / spotlighter's job. Here
    # we simulate that by lifting [100:130) to TOXIC via label_range.
    engine = te.ByteTaintEngine()
    upgraded = engine.label_range(buf, start=100, end=130, label=te.TaintLabel.TOXIC)

    # Whole-blob view (what C7 would see).
    assert upgraded.max_color() == te.TaintLabel.TOXIC

    # Byte-level view of the clean slice — the C-1 win.
    clean_view = engine.slice(upgraded, 0, 100)
    assert clean_view.max_color() == te.TaintLabel.UNTRUSTED
    # And the egress decision over only that slice is ALLOW for NETWORK.
    decision = engine.check_egress(clean_view, te.SinkKind.NETWORK_EGRESS)
    assert decision.kind == te.EgressDecisionKind.ALLOW

    # Egress over the upgraded full blob is DENY — both engines agree
    # on the conservative case.
    full_decision = engine.check_egress(upgraded, te.SinkKind.NETWORK_EGRESS)
    assert full_decision.kind == te.EgressDecisionKind.DENY


# ---------------------------------------------------------------------------
# Safe-fail when the engine misbehaves
# ---------------------------------------------------------------------------


def test_safe_fail_when_byte_taint_engine_raises(monkeypatch):
    """If the byte-taint engine raises mid-call, the router MUST still
    produce a valid packet with tainted_buffer=None and a logged
    warning, never propagating the exception to the tool caller."""
    monkeypatch.delenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, raising=False)
    monkeypatch.delenv(dr.ENV_DISABLE_DUAL_LLM, raising=False)

    def _broken_lazy_import():
        class _BrokenEngine:
            def label_source(self, *a, **kw):
                raise RuntimeError("simulated engine failure")

        return _BrokenEngine, te.TaintLabel

    monkeypatch.setattr(dr, "_lazy_import_byte_taint", _broken_lazy_import)
    router = _make_router()
    packet = router.route_tool_result("web_search", "raw output")
    assert packet.tainted_buffer is None
    assert packet.tainted_buffer_max_color is None
    # Tool flow still produces a usable summary.
    assert isinstance(packet.safe_summary, str)


def test_lazy_import_returns_none_yields_no_buffer(monkeypatch):
    """If backend.security.taint_engine_v2 isn't importable, the router
    degrades to C7-blob-level downstream without crashing."""
    monkeypatch.delenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, raising=False)
    monkeypatch.delenv(dr.ENV_DISABLE_DUAL_LLM, raising=False)
    monkeypatch.setattr(dr, "_lazy_import_byte_taint", lambda: (None, None))
    router = _make_router()
    packet = router.route_tool_result("web_search", "raw output")
    assert packet.tainted_buffer is None
    assert packet.tainted_buffer_max_color is None


# ---------------------------------------------------------------------------
# Source provenance + buffer SHA matches packet raw_hash
# ---------------------------------------------------------------------------


def test_buffer_sha_matches_packet_raw_hash(monkeypatch):
    """The packet's raw_hash and the buffer's sha256 are both SHA-256
    over the same raw_bytes — they MUST agree, since downstream
    auditors need to cross-reference them."""
    monkeypatch.delenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, raising=False)
    monkeypatch.delenv(dr.ENV_DISABLE_DUAL_LLM, raising=False)
    router = _make_router()
    raw = "hello world from the tool"
    packet = router.route_tool_result("web_search", raw)
    assert packet.tainted_buffer.sha256 == packet.raw_hash


def test_source_id_records_tool_name(monkeypatch):
    monkeypatch.delenv(dr.ENV_DISABLE_BYTE_TAINT_INTAKE, raising=False)
    monkeypatch.delenv(dr.ENV_DISABLE_DUAL_LLM, raising=False)
    router = _make_router()
    packet = router.route_tool_result("fetch_url", "x")
    chain = packet.tainted_buffer.provenance_chain
    assert any("tool:fetch_url" in entry for entry in chain)
