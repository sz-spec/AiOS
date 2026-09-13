"""
backend/tests/security/test_gvisor_magi_loader.py

Sprint 15 / Item C6 — gVisor MAGI config loader tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_ROOT))

from sandbox.gvisor_loader import (  # noqa: E402
    CONFIG_PATH,
    GVisorMAGIConfig,
    GVisorConfigError,
    SCHEMA_VERSION,
)


def test_canonical_config_loads_and_validates():
    cfg = GVisorMAGIConfig.load()
    assert cfg.version == SCHEMA_VERSION
    assert cfg.magi.enabled is True
    assert cfg.magi.isolation_mode == "per-agent"
    assert cfg.platform.preferred in {"systrap", "kvm"}
    assert cfg.network.type == "sandbox"
    # Critical syscall denies are in place
    for must_deny in ("ptrace", "init_module", "bpf", "kexec_load"):
        assert (
            must_deny in cfg.deny_syscalls
        ), f"safety regression: {must_deny!r} missing from deny_syscalls"


def test_runsc_args_produced():
    cfg = GVisorMAGIConfig.load()
    args = cfg.to_runsc_args()
    assert any(a.startswith("--platform=") for a in args)
    assert "--magi" in args
    assert any(a.startswith("--magi-isolation=per-agent") for a in args)
    assert any(a.startswith("--network=") for a in args)
    # Sentry watchdog wires panic-on-fault per the canonical config.
    assert "--watchdog-action=panic" in args


def test_bad_isolation_mode_rejected(tmp_path):
    """Operator typos in isolation_mode must fail validation, not silently
    fall through to a wrong sandbox config."""
    doc = json.loads(CONFIG_PATH.read_text("utf-8"))
    doc["magi"]["isolation_mode"] = "yolo"
    bad_path = tmp_path / "bad_isolation.json"
    bad_path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(GVisorConfigError, match="isolation_mode"):
        GVisorMAGIConfig.load(bad_path)


def test_bad_platform_rejected(tmp_path):
    doc = json.loads(CONFIG_PATH.read_text("utf-8"))
    doc["platform"]["preferred"] = "wasm"
    bad = tmp_path / "bad_platform.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(GVisorConfigError, match="platform.preferred"):
        GVisorMAGIConfig.load(bad)


def test_schema_version_mismatch_rejected(tmp_path):
    doc = json.loads(CONFIG_PATH.read_text("utf-8"))
    doc["version"] = "2.0"
    bad = tmp_path / "future.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(GVisorConfigError, match="schema version"):
        GVisorMAGIConfig.load(bad)


def test_invalid_json_rejected(tmp_path):
    bad = tmp_path / "not_json.json"
    bad.write_text("{not valid", encoding="utf-8")
    with pytest.raises(GVisorConfigError, match="not valid JSON"):
        GVisorMAGIConfig.load(bad)


def test_missing_file_rejected(tmp_path):
    with pytest.raises(GVisorConfigError, match="not found"):
        GVisorMAGIConfig.load(tmp_path / "does-not-exist.json")
