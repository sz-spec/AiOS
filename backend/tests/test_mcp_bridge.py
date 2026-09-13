"""
VOS3 — MCP Bridge Protocol Conformance Tests
==============================================
Kill-List P0-A verification: tools/vos3_mcp_bridge.py speaks valid
JSON-RPC 2.0 over stdio and exposes 12 sovereignty tools per MCP spec
2025-06-18.

Subprocess-driven tests — spawn the bridge and exchange real JSON-RPC
messages. Validates:
  - initialize handshake returns correct protocolVersion + serverInfo
  - tools/list returns exactly 12 tools, each with name + description +
    inputSchema, and NO leaked private `_handler` callable
  - tools/call dispatches to the right handler
  - eu_compliance_check returns the canonical EU label hash for DE
  - optimal_model returns a valid model slot for non-local input
  - Unknown tool returns JSON-RPC error -32601
  - Malformed JSON returns -32700 parse error
  - Output stream is pure JSON (no stdout pollution from imports)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BRIDGE = os.path.join(REPO_ROOT, "tools", "vos3_mcp_bridge.py")


def _spawn_with_request(payload: str | list[str]) -> tuple[str, str]:
    """Spawn the bridge, write `payload` to stdin, return (stdout, stderr)."""
    if isinstance(payload, list):
        payload = "\n".join(payload) + "\n"
    elif not payload.endswith("\n"):
        payload += "\n"
    proc = subprocess.run(
        [sys.executable, "-u", BRIDGE],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout, proc.stderr


def _parse_one_line(stdout: str) -> dict:
    """Read the first non-empty line of stdout as JSON. Asserts it parses."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        return json.loads(line)
    pytest.fail(f"No JSON response on stdout. stdout={stdout!r}")


# ---------------------------------------------------------------------------
# Test 1: initialize handshake
# ---------------------------------------------------------------------------


def test_initialize_returns_correct_protocol_version():
    req = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
    stdout, _ = _spawn_with_request(req)
    resp = _parse_one_line(stdout)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    result = resp["result"]
    assert "protocolVersion" in result
    assert result["protocolVersion"].startswith("2025-")
    assert result["serverInfo"]["name"] == "vos3-sovereignty"
    assert "capabilities" in result


# ---------------------------------------------------------------------------
# Test 2: tools/list returns 12 tools, public-only fields
# ---------------------------------------------------------------------------


def test_tools_list_returns_twelve_tools():
    req = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
    stdout, _ = _spawn_with_request(req)
    resp = _parse_one_line(stdout)
    tools = resp["result"]["tools"]
    assert len(tools) == 12, f"Expected 12 tools, got {len(tools)}"
    # Required tool names — sovereignty primitives
    names = {t["name"] for t in tools}
    expected = {
        "vos3_mmr_root",
        "vos3_kernel_status",
        "vos3_npu_topology",
        "vos3_driver_pressure",
        "vos3_ktext_hash",
        "vos3_attest_request",
        "vos3_optimal_model",
        "vos3_eu_compliance_check",
        "vos3_isolate_zone",
        "vos3_seal_adapter",
        "vos3_memory_status",
        "vos3_transparency_proof",
    }
    assert names == expected
    # No private _handler should leak to clients
    for t in tools:
        assert "_handler" not in t, f"Tool {t['name']} leaks private _handler"
        assert "name" in t and "description" in t and "inputSchema" in t


# ---------------------------------------------------------------------------
# Test 3: vos3_eu_compliance_check returns the canonical EU label hash
# ---------------------------------------------------------------------------


def test_eu_compliance_check_for_germany():
    req = (
        '{"jsonrpc":"2.0","id":3,"method":"tools/call",'
        '"params":{"name":"vos3_eu_compliance_check",'
        '"arguments":{"region_code":"DE"}}}'
    )
    stdout, _ = _spawn_with_request(req)
    resp = _parse_one_line(stdout)
    text = resp["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["region_code"] == "DE"
    assert payload["is_eu_eea"] is True
    assert payload["would_enforce_local"] is True
    # Canonical SHA-256 of "OP_LOCAL_ENFORCEMENT_EU"
    assert payload["label_hash"] == (
        "ca095456b27e291ff80d778a5aa6fc167ec9d59134b7e0e580d5045c6c131524"
    )


def test_eu_compliance_check_for_us():
    req = (
        '{"jsonrpc":"2.0","id":4,"method":"tools/call",'
        '"params":{"name":"vos3_eu_compliance_check",'
        '"arguments":{"region_code":"US"}}}'
    )
    stdout, _ = _spawn_with_request(req)
    resp = _parse_one_line(stdout)
    text = resp["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["is_eu_eea"] is False
    assert payload["would_enforce_local"] is False
    assert payload["label_hash"] is None


# ---------------------------------------------------------------------------
# Test 4: vos3_optimal_model — pluggable router contract
# ---------------------------------------------------------------------------


def test_optimal_model_for_non_local_request():
    req = (
        '{"jsonrpc":"2.0","id":5,"method":"tools/call",'
        '"params":{"name":"vos3_optimal_model",'
        '"arguments":{"role":"frontend","complexity":5}}}'
    )
    stdout, _ = _spawn_with_request(req)
    resp = _parse_one_line(stdout)
    text = resp["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["role"] == "frontend"
    # Without VOS3_DEFAULT_LOCAL_FIRST set, frontend complexity 5 → cloud
    assert payload["chosen_model"] != "local-titan"


def test_optimal_model_local_only_returns_titan():
    req = (
        '{"jsonrpc":"2.0","id":6,"method":"tools/call",'
        '"params":{"name":"vos3_optimal_model",'
        '"arguments":{"role":"frontend","complexity":5,"local_only":true}}}'
    )
    stdout, _ = _spawn_with_request(req)
    resp = _parse_one_line(stdout)
    text = resp["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert payload["chosen_model"] == "local-titan"


# ---------------------------------------------------------------------------
# Test 5: Unknown tool returns JSON-RPC error -32601
# ---------------------------------------------------------------------------


def test_unknown_tool_returns_method_not_found():
    req = (
        '{"jsonrpc":"2.0","id":7,"method":"tools/call",'
        '"params":{"name":"vos3_does_not_exist","arguments":{}}}'
    )
    stdout, _ = _spawn_with_request(req)
    resp = _parse_one_line(stdout)
    assert "error" in resp
    assert resp["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# Test 6: Malformed JSON returns -32700 parse error
# ---------------------------------------------------------------------------


def test_malformed_json_returns_parse_error():
    req = "this is not json"
    stdout, _ = _spawn_with_request(req)
    resp = _parse_one_line(stdout)
    assert "error" in resp
    assert resp["error"]["code"] == -32700


# ---------------------------------------------------------------------------
# Test 7: stdout is pure JSON — no library output leaks (the bug we fixed)
# ---------------------------------------------------------------------------


def test_stdout_is_pure_json_no_library_leaks():
    """Tools that import services.* (which pulls middleware/auth) must not
    leak the AUTH startup warning to stdout. Bridge sets VOS3_ALLOW_DEV_MODE
    + wraps tool calls in stdout→stderr redirect for belt-and-suspenders."""
    req = (
        '{"jsonrpc":"2.0","id":8,"method":"tools/call",'
        '"params":{"name":"vos3_eu_compliance_check",'
        '"arguments":{"region_code":"FR"}}}'
    )
    stdout, _ = _spawn_with_request(req)
    # Every line on stdout must parse as JSON
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"Non-JSON output corrupting protocol stream: {line!r}")


# ---------------------------------------------------------------------------
# Test 8: mcp.json manifest is valid + matches tool list
# ---------------------------------------------------------------------------


def test_mcp_json_manifest_matches_bridge_tools():
    manifest_path = os.path.join(REPO_ROOT, "tools", "mcp.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    assert manifest["name"] == "vos3-sovereignty"
    assert manifest["transport"] == "stdio"
    assert manifest["command"] == "python3"
    declared_tools = set(manifest["tools"])
    # Live-list from the bridge
    req = '{"jsonrpc":"2.0","id":9,"method":"tools/list","params":{}}'
    stdout, _ = _spawn_with_request(req)
    resp = _parse_one_line(stdout)
    live_tools = {t["name"] for t in resp["result"]["tools"]}
    assert declared_tools == live_tools, (
        f"manifest declares {declared_tools - live_tools} not in bridge; "
        f"bridge has {live_tools - declared_tools} not in manifest"
    )
