"""
backend/tests/security/test_hf_config_scanner.py

Sprint 15 / Item I6 — coverage for backend/security/hf_config_scanner.py.

Tests cover EVERY finding kind documented in the scanner:
  - pickle_import (pickle protocol bytes in a config file)
  - auto_map_remote (auto_map entry pointing at a remote URL)
  - dangerous_loader_signature (auto_map entry matching a known-evil pattern)
  - trust_remote_code (the explicit RCE-opt-in flag)
  - py_shim_in_tokenizer (.py reference in tokenizer fields)
  - benign config (no findings, is_safe=True)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make `from security import hf_config_scanner` importable.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_ROOT))

from security.hf_config_scanner import (  # noqa: E402
    scan_config_file,
    scan_directory,
)

# ---------------------------------------------------------------------------
# Per-class finding tests
# ---------------------------------------------------------------------------


def test_benign_config_returns_no_findings(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "model_type": "llama",
                "vocab_size": 32000,
                "hidden_size": 4096,
            }
        ),
        encoding="utf-8",
    )

    result = scan_directory(tmp_path)
    assert result.is_safe is True
    assert result.severity == "none"
    assert len(result.findings) == 0


def test_pickle_bytes_in_config_flagged_critical(tmp_path):
    config_path = tmp_path / "config.json"
    # Pickle protocol 4 header + dangerous opcode embedded.
    config_path.write_bytes(b"\x80\x04cposix\nsystem\n... rest of pickle ...")

    findings = scan_config_file(config_path)
    assert any(f.kind == "pickle_import" for f in findings)
    assert any(f.severity == "critical" for f in findings)


def test_auto_map_remote_url_flagged_critical(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "model_type": "custom",
                "auto_map": {
                    "AutoModel": "https://attacker.example.com/evil_model.py",
                },
            }
        ),
        encoding="utf-8",
    )

    result = scan_directory(tmp_path)
    assert result.is_safe is False
    assert result.severity == "critical"
    assert any(f.kind == "auto_map_remote" for f in result.findings)
    finding = next(f for f in result.findings if f.kind == "auto_map_remote")
    assert "AutoModel" in (finding.field_path or "")


def test_auto_map_dangerous_loader_signature_flagged(tmp_path):
    """auto_map → 'os.system' is a known-dangerous pattern (matches the
    DANGEROUS_LOADER_PATTERNS regex), not a remote URL — different code path."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "model_type": "custom",
                "auto_map": {"AutoModel": "os.system"},
            }
        ),
        encoding="utf-8",
    )

    findings = scan_config_file(config_path)
    assert any(f.kind == "dangerous_loader_signature" for f in findings)
    assert any(f.severity == "critical" for f in findings)


def test_trust_remote_code_flag_flagged_high(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "model_type": "llama",
                "trust_remote_code": True,
            }
        ),
        encoding="utf-8",
    )

    result = scan_directory(tmp_path)
    assert result.is_safe is False
    assert result.severity == "high"
    finding = next(f for f in result.findings if f.kind == "trust_remote_code")
    assert "trust_remote_code=true" in finding.detail


def test_trust_remote_code_as_string_true_also_flagged(tmp_path):
    """Some configs store booleans as strings ('true'). Catch that too."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "model_type": "llama",
                "trust_remote_code": "true",
            }
        ),
        encoding="utf-8",
    )

    result = scan_directory(tmp_path)
    assert result.is_safe is False


def test_py_shim_in_tokenizer_config_flagged(tmp_path):
    config_path = tmp_path / "tokenizer_config.json"
    config_path.write_text(
        json.dumps(
            {
                "tokenizer_class": "evil_tokenizer.py",
            }
        ),
        encoding="utf-8",
    )

    findings = scan_config_file(config_path)
    assert any(f.kind == "py_shim_in_tokenizer" for f in findings)


def test_non_json_config_file_flagged_medium(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("THIS IS NOT JSON {[\n", encoding="utf-8")

    findings = scan_config_file(config_path)
    assert any(f.severity == "medium" for f in findings)


def test_missing_directory_returns_unsafe_result(tmp_path):
    """Calling scan_directory on a non-existent path returns a structured
    unsafe result (not an exception)."""
    result = scan_directory(tmp_path / "does-not-exist")
    assert result.is_safe is False
    assert result.severity == "medium"
    assert len(result.findings) == 0


def test_multiple_findings_aggregate_to_max_severity(tmp_path):
    """A directory with both a high (trust_remote_code) and a critical
    (pickle_import) finding reports severity=critical."""
    (tmp_path / "config.json").write_bytes(
        b"\x80\x05cbuiltins\neval\n"
        + json.dumps({"trust_remote_code": True}).encode("utf-8")
    )
    # Note: the file isn't valid JSON because of the pickle prefix; the
    # scanner emits a pickle_import finding AND a non-JSON finding. The
    # max severity should still be critical (from the pickle).
    result = scan_directory(tmp_path)
    assert result.is_safe is False
    assert result.severity == "critical"
    kinds = {f.kind for f in result.findings}
    assert "pickle_import" in kinds


def test_known_filename_filter_skips_unrelated_files(tmp_path):
    """Files NOT in _KNOWN_CONFIG_FILENAMES (e.g., README.md) are skipped
    even if they contain dangerous bytes."""
    (tmp_path / "README.md").write_bytes(b"\x80\x04cposix\nsystem\n")
    result = scan_directory(tmp_path)
    assert result.is_safe is True
    assert result.severity == "none"


def test_field_path_populated_for_nested_findings(tmp_path):
    """Findings under nested keys (auto_map.AutoTokenizer) should have a
    dotted field_path for triage."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "auto_map": {
                    "AutoTokenizer": "https://attacker.example.com/x.py",
                    "AutoModel": "safe_class",
                }
            }
        ),
        encoding="utf-8",
    )
    findings = scan_config_file(config_path)
    remote_findings = [f for f in findings if f.kind == "auto_map_remote"]
    assert len(remote_findings) == 1
    assert remote_findings[0].field_path is not None
    assert "AutoTokenizer" in remote_findings[0].field_path
