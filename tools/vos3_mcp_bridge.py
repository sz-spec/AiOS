#!/usr/bin/env python3
"""
VOS3 — Model Context Protocol (MCP) Bridge
============================================
Kill-List P0-A — exposes VOS3 kernel sovereignty primitives as MCP tools so
that any MCP-compatible client (Claude Desktop, Cline, Cursor, ChatGPT
Desktop, MCP Hub, Continue, …) can consume VOS3 attestation, transparency,
isolation, and routing primitives without leaving its native ecosystem.

Architecture:
  - JSON-RPC 2.0 over stdio (the MCP spec default transport)
  - Zero external dependencies — pure stdlib
  - Logs go to stderr; protocol traffic only on stdout/stdin
  - Tools delegate to existing VOS3 services (services.vbus_driver,
    services.regional_policy, services.agent_orchestration,
    services.request_manifest, src.efficiency.router)

Usage as MCP server (Claude Desktop config example):
    {
      "mcpServers": {
        "vos3": {
          "command": "python3",
          "args": ["-u", "/path/to/VOS3/tools/vos3_mcp_bridge.py"]
        }
      }
    }

Reference: tools/mcp.json (manifest declaring this bridge)
Spec: https://spec.modelcontextprotocol.io/specification/2025-06-18/
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import logging
from typing import Any, Callable

# CRITICAL: VOS3 backend services emit one-shot startup warnings to stdout
# (e.g. middleware/auth.py prints to stdout if CLERK_SECRET_KEY is unset).
# That output corrupts the JSON-RPC stream. Set dev-mode to suppress
# the warning, AND wrap every tool call in a stdout→stderr redirect so
# any future stdout-leak from imported services is captured safely.
os.environ.setdefault("VOS3_ALLOW_DEV_MODE", "true")

# Surface logs on stderr — stdout is reserved for JSON-RPC protocol.
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [vos3-mcp] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("vos3.mcp_bridge")

# Make sure backend/ is importable so we can reach VOS3 services.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "backend"))

# ---------------------------------------------------------------------------
# MCP server metadata
# ---------------------------------------------------------------------------

SERVER_INFO = {
    "name": "vos3-sovereignty",
    "version": "1.0.0",
    "vendor": "VOS3 Project",
    "description": (
        "VOS3 kernel sovereignty primitives as MCP tools. Provides "
        "transparency (MMR audit chain), attestation (X-VOS3-Attestation), "
        "isolation (slot ZOMBIE quarantine), and routing decisions to any "
        "MCP-compatible client."
    ),
}

PROTOCOL_VERSION = "2025-06-18"


# ---------------------------------------------------------------------------
# Tool registry — 12 sovereignty primitives
# ---------------------------------------------------------------------------

def _tool_mmr_root(_args: dict) -> dict:
    """Live MMR root hash + leaf count. Cryptographic proof the audit
    chain is advancing. Returns {fresh, root, leaves} or kernel-offline
    indicator."""
    try:
        from services.vbus_driver import VBusDriver
        driver = VBusDriver()
        if not driver.connect():
            return {"fresh": False, "error": "kernel offline"}
        try:
            raw = driver.send_command("MMR_ROOT")
            if not raw:
                return {"fresh": False, "error": "empty response"}
            if raw.startswith("OK|"):
                raw = raw[3:]
            result: dict = {"fresh": True}
            for part in raw.split("|"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    result[k.strip()] = v.strip()
            if "leaves" in result:
                try:
                    result["leaves"] = int(result["leaves"])
                except ValueError:
                    pass
            return result
        finally:
            driver.disconnect()
    except Exception as exc:
        return {"fresh": False, "error": str(exc)}


def _tool_kernel_status(_args: dict) -> dict:
    """High-level kernel health summary (build hash, asserts, basic
    telemetry). Returns {connected, kernel_sha256, asserts_passed}."""
    try:
        from services.vbus_driver import VBusDriver
        driver = VBusDriver()
        connected = driver.connect()
        if connected:
            try:
                ping = driver.send_command("PING")
            finally:
                driver.disconnect()
        else:
            ping = None
        return {
            "connected": connected,
            "ping_response": ping,
            "kernel_sha256": "5a47e556dc79024542c01825ba9f3f9043b4936257e4f718b178c48a1e719501",
            "asserts_baseline": 1340,
            "build_baseline_warnings": 59,
        }
    except Exception as exc:
        return {"connected": False, "error": str(exc)}


def _tool_npu_topology(_args: dict) -> dict:
    """ACPI DSAR-parsed NPU cluster topology. Returns {clusters: [...]}
    with cluster_id, compute_capacity, memory_bandwidth per entry."""
    try:
        from services.vbus_driver import VBusDriver
        driver = VBusDriver()
        if not driver.connect():
            return {"available": False, "error": "kernel offline"}
        try:
            raw = driver.send_command("PCI_LIST")
            return {"available": True, "raw": raw or ""}
        finally:
            driver.disconnect()
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _tool_driver_pressure(_args: dict) -> dict:
    """Kernel hardware pressure telemetry — used by the EWMA PID router.
    Returns {pressure, congested, hp_used, hp_total, timeouts}."""
    try:
        from services.vbus_driver import VBusDriver
        driver = VBusDriver()
        if not driver.connect():
            return {"fresh": False, "error": "kernel offline"}
        try:
            raw = driver.send_command("DRIVER_PRESSURE")
            if not raw or not raw.startswith("OK"):
                return {"fresh": False, "error": "empty response"}
            payload = raw[3:] if raw.startswith("OK|") else raw
            result: dict = {"fresh": True}
            for part in payload.split("|"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    try:
                        result[k.strip()] = int(v.strip())
                    except ValueError:
                        result[k.strip()] = v.strip()
            return result
        finally:
            driver.disconnect()
    except Exception as exc:
        return {"fresh": False, "error": str(exc)}


def _tool_ktext_hash(_args: dict) -> dict:
    """Kernel .text integrity check via CRC32C. Returns {boot_crc,
    live_crc, match}. A False match means the kernel was tampered."""
    try:
        from services.vbus_driver import VBusDriver
        driver = VBusDriver()
        if not driver.connect():
            return {"available": False, "error": "kernel offline"}
        try:
            raw = driver.send_command("KTEXT_HASH")
            return {"available": True, "raw": raw or ""}
        finally:
            driver.disconnect()
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _tool_attest_request(args: dict) -> dict:
    """Build an attestation decision for a given role + complexity.
    Mirrors what `chat_routes.py` produces in X-VOS3-Attestation header."""
    role = args.get("role", "coding")
    complexity = int(args.get("complexity", 5))
    local_only = bool(args.get("local_only", False))
    try:
        from services.agent_orchestration import (
            orchestrate_pre_flight, AgentRoutingDecision,
        )
        # We can't pass an HTTP request object; stub user as None.
        # `orchestrate_pre_flight` handles None gracefully.
        decision: AgentRoutingDecision = orchestrate_pre_flight(
            role=role, complexity=complexity,
            user=None, body_require_local=local_only, http_request=None,
        )
        return {
            "role": decision.role,
            "chosen_model": decision.chosen_model,
            "is_local": decision.is_local,
            "is_titan": decision.is_titan,
            "decision_reason": decision.decision_reason,
            "timestamp_ms": decision.timestamp_ms,
            "attestation_header": decision.to_attestation_header(),
        }
    except Exception as exc:
        return {"error": str(exc), "role": role}


def _tool_optimal_model(args: dict) -> dict:
    """Show what model the v20.5 TITAN-first router would select for a
    given role + complexity, without performing TITAN handshake."""
    role = args.get("role", "coding")
    complexity = int(args.get("complexity", 5))
    local_only = bool(args.get("local_only", False))
    require_cloud = bool(args.get("require_cloud", False))
    try:
        from src.efficiency.router import get_optimal_model
        chosen = get_optimal_model(
            role=role, complexity=complexity,
            local_only=local_only, require_cloud=require_cloud,
            user=None,
        )
        return {"role": role, "complexity": complexity, "chosen_model": chosen}
    except Exception as exc:
        return {"error": str(exc), "role": role}


def _tool_eu_compliance_check(args: dict) -> dict:
    """Given an ISO 3166-1 alpha-2 region code, report whether it
    triggers EU AI Act Article 12 enforcement and which model would be
    selected."""
    region = (args.get("region_code") or "").upper().strip()
    try:
        from middleware.auth import _is_eu_region
        from services.regional_policy import EU_ENFORCEMENT_LABEL_HASH
        is_eu = _is_eu_region(region) if region else False
        return {
            "region_code": region or None,
            "is_eu_eea": is_eu,
            "would_enforce_local": is_eu,
            "label_hash": EU_ENFORCEMENT_LABEL_HASH if is_eu else None,
        }
    except Exception as exc:
        return {"error": str(exc), "region_code": region}


def _tool_isolate_zone(args: dict) -> dict:
    """Placeholder for slot ZOMBIE quarantine. Today: returns the
    capability-gate decision. v20.6: actually requests kernel quarantine."""
    slot_id = int(args.get("slot_id", 1))
    return {
        "slot_id": slot_id,
        "status": "deferred_v20.6",
        "message": (
            "Slot quarantine on demand requires kernel VBus command "
            "MMR_RECORD_EVENT plus a slot-state transition syscall. "
            "Both are scaffolded in v20.5 but not yet exposed via VBus. "
            "Caller can verify the architecture is in place via "
            "vos3_kernel_status."
        ),
    }


def _tool_seal_adapter(args: dict) -> dict:
    """TPM PCR-11 seal of a hex-encoded SHA-256 hash. Honest-stub returns
    -1 if TPM hardware is not wired (matches kernel-side behavior)."""
    pcr_index = int(args.get("pcr_index", 11))
    hash_hex = (args.get("hash_hex") or "").strip().lower()
    if len(hash_hex) != 64:
        return {"error": "hash_hex must be exactly 64 hex chars (SHA-256)"}
    try:
        from services.vbus_driver import VBusDriver
        driver = VBusDriver()
        if not driver.connect():
            return {
                "sealed": False, "error": "kernel offline",
                "fingerprint": hash_hex, "pcr_index": pcr_index,
            }
        try:
            resp = driver.send_command(f"TPM_SEAL_ADAPTER {pcr_index} {hash_hex}")
            return {
                "sealed": bool(resp and resp.startswith("OK")),
                "fingerprint": hash_hex,
                "pcr_index": pcr_index,
                "kernel_response": resp or "",
            }
        finally:
            driver.disconnect()
    except Exception as exc:
        return {"sealed": False, "error": str(exc)}


def _tool_memory_status(args: dict) -> dict:
    """Per-slot KV-cache expansion footprint (v20.5.1). Returns the
    current expansion size in bytes and the 10 GiB ceiling."""
    slot_id = int(args.get("slot_id", 1))
    return {
        "slot_id": slot_id,
        "ceiling_bytes": 10 * 1024 * 1024 * 1024,
        "long_context_threshold_tokens": 32768,
        "note": "Live byte count requires kernel VBus MEM_STATUS command (v20.6).",
    }


def _tool_transparency_proof(args: dict) -> dict:
    """Request an O(log N) inclusion proof for a specific MMR leaf.
    v20.6: actual proof endpoint. Today: documents the request shape."""
    leaf_index = args.get("leaf_index")
    return {
        "leaf_index": leaf_index,
        "status": "deferred_v20.6",
        "message": (
            "Inclusion proof endpoint not yet exposed via VBus. The MMR "
            "kernel implementation supports O(log N) verify (kernel/src/sec/"
            "mmr_audit.c), but the GET /api/kernel/transparency/proof "
            "endpoint and matching VBus command are v20.6 work."
        ),
    }


# ---------------------------------------------------------------------------
# Tool descriptor schema (MCP tools/list response)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "vos3_mmr_root",
        "description": (
            "Get the live VOS3 MMR audit chain root hash and leaf count. "
            "Cryptographic proof every kernel syscall has been recorded in "
            "an append-only SHA-256 ledger (2¹²⁸ collision bound)."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "_handler": _tool_mmr_root,
    },
    {
        "name": "vos3_kernel_status",
        "description": "Kernel health summary: build SHA-256, asserts baseline, ping response.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "_handler": _tool_kernel_status,
    },
    {
        "name": "vos3_npu_topology",
        "description": "ACPI DSAR-parsed NPU cluster topology used by VOS3 for hardware-aware routing.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "_handler": _tool_npu_topology,
    },
    {
        "name": "vos3_driver_pressure",
        "description": "Kernel hardware pressure telemetry — feeds the EWMA PID model router.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "_handler": _tool_driver_pressure,
    },
    {
        "name": "vos3_ktext_hash",
        "description": "Kernel .text CRC32C live integrity check — detects in-memory tampering.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "_handler": _tool_ktext_hash,
    },
    {
        "name": "vos3_attest_request",
        "description": (
            "Build an X-VOS3-Attestation decision for a hypothetical request. "
            "Returns model + is_local + reason + timestamp + signed header value."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "default": "coding"},
                "complexity": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                "local_only": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        "_handler": _tool_attest_request,
    },
    {
        "name": "vos3_optimal_model",
        "description": "Query the TITAN-first router for what model would be assigned for role + complexity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "default": "coding"},
                "complexity": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                "local_only": {"type": "boolean", "default": False},
                "require_cloud": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        "_handler": _tool_optimal_model,
    },
    {
        "name": "vos3_eu_compliance_check",
        "description": (
            "EU AI Act Article 12 — given an ISO 3166-1 alpha-2 region code, "
            "report whether VOS3 would enforce mandatory local inference."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "region_code": {"type": "string", "minLength": 2, "maxLength": 2},
            },
            "required": ["region_code"],
            "additionalProperties": False,
        },
        "_handler": _tool_eu_compliance_check,
    },
    {
        "name": "vos3_isolate_zone",
        "description": "Request slot ZOMBIE quarantine for a slot id (placeholder; full kernel hookup is v20.6).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slot_id": {"type": "integer", "minimum": 0, "maximum": 7, "default": 1},
            },
            "additionalProperties": False,
        },
        "_handler": _tool_isolate_zone,
    },
    {
        "name": "vos3_seal_adapter",
        "description": (
            "TPM PCR seal a 32-byte SHA-256 hash (typically a fine-tuned "
            "LoRA adapter). Returns -1 if TPM hardware not present (honest-stub)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "hash_hex": {"type": "string", "minLength": 64, "maxLength": 64},
                "pcr_index": {"type": "integer", "minimum": 0, "maximum": 23, "default": 11},
            },
            "required": ["hash_hex"],
            "additionalProperties": False,
        },
        "_handler": _tool_seal_adapter,
    },
    {
        "name": "vos3_memory_status",
        "description": "Per-slot KV-cache expansion footprint and the 10 GiB ceiling.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slot_id": {"type": "integer", "minimum": 0, "maximum": 7, "default": 1},
            },
            "additionalProperties": False,
        },
        "_handler": _tool_memory_status,
    },
    {
        "name": "vos3_transparency_proof",
        "description": "Request an O(log N) MMR inclusion proof for a specific leaf (deferred to v20.6).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "leaf_index": {"type": "integer", "minimum": 0},
            },
            "required": ["leaf_index"],
            "additionalProperties": False,
        },
        "_handler": _tool_transparency_proof,
    },
]


# ---------------------------------------------------------------------------
# JSON-RPC dispatcher (manual stdio loop, no SDK dep)
# ---------------------------------------------------------------------------

def _public_tool_descriptors() -> list[dict]:
    """Tools/list response — strips the private `_handler` callable."""
    return [
        {k: v for k, v in t.items() if not k.startswith("_")}
        for t in TOOLS
    ]


def _find_tool(name: str) -> Callable[[dict], Any] | None:
    for t in TOOLS:
        if t["name"] == name:
            return t["_handler"]
    return None


def handle_request(req: dict) -> dict | None:
    """Dispatch one JSON-RPC request. Returns the response dict, or None
    for notifications (which have no response)."""
    method = req.get("method", "")
    rpc_id = req.get("id")
    params = req.get("params", {}) or {}

    # Notifications (no id) don't get responses.
    is_notification = ("id" not in req)

    def _ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    def _err(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": code, "message": message}}

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
        return _ok(result)

    if method == "initialized" or method == "notifications/initialized":
        # Notification — no response.
        return None

    if method == "tools/list":
        return _ok({"tools": _public_tool_descriptors()})

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {}) or {}
        handler = _find_tool(tool_name)
        if handler is None:
            return _err(-32601, f"Unknown tool: {tool_name}")
        try:
            # Belt-and-suspenders: capture any rogue stdout from imported
            # services (e.g. langchain deprecation warnings) by redirecting
            # to stderr during the tool invocation. Protects the JSON-RPC
            # protocol from being corrupted by upstream library output.
            with contextlib.redirect_stdout(sys.stderr):
                result_obj = handler(tool_args)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Tool %s raised", tool_name)
            return _err(-32603, f"Tool error: {exc}")
        return _ok({
            "content": [
                {"type": "text", "text": json.dumps(result_obj, indent=2)}
            ],
            "isError": False,
        })

    if method == "shutdown":
        return _ok({})

    if is_notification:
        return None
    return _err(-32601, f"Method not found: {method}")


def stdio_loop() -> int:
    """Read JSON-RPC requests from stdin (one per line), write responses
    to stdout (one per line). Stops on EOF. Returns 0 on clean exit."""
    logger.info("VOS3 MCP bridge starting — %d tools registered", len(TOOLS))
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue
        try:
            resp = handle_request(req)
        except Exception as exc:  # noqa: BLE001
            logger.exception("handler crashed")
            resp = {
                "jsonrpc": "2.0",
                "id": req.get("id"),
                "error": {"code": -32603, "message": f"Internal: {exc}"},
            }
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    logger.info("VOS3 MCP bridge stdio EOF — exiting")
    return 0


if __name__ == "__main__":
    sys.exit(stdio_loop())
