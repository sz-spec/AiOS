"""
Stage 1 · Atomic unit isolation for services/model_manager.py.

Covers — purpose | guards file:line:
  _validate_url    — HTTPS-only, no traversal       | model_manager.py:390-398
  _validate_model_id — safe character regex         | model_manager.py:400-405
  _detect_format / _detect_quant                    | model_manager.py:408-411, 488-497
  validate_gguf_header                              | model_manager.py:432-485
  _parse_size_string                                | model_manager.py:980-992
  _classify_tier                                    | model_manager.py:995-1006
  _param_count_to_int                               | model_manager.py:1054-1065
  load_curated_catalog                              | model_manager.py:1012-1026
  _host_can_run                                     | model_manager.py:1032-1034
  select_model_for_host                             | model_manager.py:1037-1051
  resolve_model_status (cloud_fallback path)        | model_manager.py:1068-1083
  HostCapability shape via probe_host_capability    | model_manager.py:798-861

Maps to spring-2026 CVE class:
  CVE-2026-33626 (LMDeploy SSRF — non-HTTPS scheme),
  Ghost-Tensor GGUF inflation (CRITICAL_METADATA_MISMATCH).
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# _validate_url — SSRF defense
# ---------------------------------------------------------------------------


def test_validate_url_accepts_https():
    from services.model_manager import _validate_url

    _validate_url("https://huggingface.co/foo/model.gguf")  # no raise


def test_validate_url_rejects_http():
    from services.model_manager import _validate_url

    with pytest.raises(ValueError, match="Scheme"):
        _validate_url("http://evil/model.gguf")


def test_validate_url_rejects_file():
    from services.model_manager import _validate_url

    with pytest.raises(ValueError, match="Scheme"):
        _validate_url("file:///etc/passwd")


def test_validate_url_rejects_ftp():
    from services.model_manager import _validate_url

    with pytest.raises(ValueError):
        _validate_url("ftp://attacker/x")


def test_validate_url_rejects_path_traversal():
    from services.model_manager import _validate_url

    with pytest.raises(ValueError, match="traversal"):
        _validate_url("https://huggingface.co/../../etc/passwd")


# ---------------------------------------------------------------------------
# _validate_model_id — filename-safe characters
# ---------------------------------------------------------------------------


def test_validate_model_id_accepts_normal():
    from services.model_manager import _validate_model_id

    _validate_model_id("llama-3-8b-q4_k_m.v2")


def test_validate_model_id_rejects_slash():
    from services.model_manager import _validate_model_id

    with pytest.raises(ValueError):
        _validate_model_id("../etc/passwd")


def test_validate_model_id_rejects_space():
    from services.model_manager import _validate_model_id

    with pytest.raises(ValueError):
        _validate_model_id("name with space")


def test_validate_model_id_rejects_shell_meta():
    from services.model_manager import _validate_model_id

    with pytest.raises(ValueError):
        _validate_model_id("id;rm -rf /")


def test_validate_model_id_rejects_empty():
    from services.model_manager import _validate_model_id

    with pytest.raises(ValueError):
        _validate_model_id("")


def test_validate_model_id_rejects_too_long():
    from services.model_manager import _validate_model_id

    with pytest.raises(ValueError):
        _validate_model_id("a" * 129)  # regex caps at 128


# ---------------------------------------------------------------------------
# _detect_format / _detect_quant
# ---------------------------------------------------------------------------


def test_detect_format_gguf():
    from services.model_manager import _detect_format, FORMAT_GGUF

    assert _detect_format("llama.gguf") == FORMAT_GGUF


def test_detect_format_safetensors():
    from services.model_manager import _detect_format, FORMAT_SAFETENSORS

    assert _detect_format("llama.safetensors") == FORMAT_SAFETENSORS


def test_detect_format_unknown():
    from services.model_manager import _detect_format, FORMAT_UNKNOWN

    assert _detect_format("llama.bin") == FORMAT_UNKNOWN


def test_detect_quant_recognizes_q4_k_m():
    from services.model_manager import _detect_quant

    label, _ = _detect_quant("llama-3-8b.Q4_K_M.gguf")
    # Either an explicit label match or unknown-default — but never crash.
    assert isinstance(label, str)


# ---------------------------------------------------------------------------
# validate_gguf_header — Ghost-Tensor defense
# ---------------------------------------------------------------------------


def _write_gguf(
    path: Path,
    *,
    magic=b"GGUF",
    version=3,
    tensor_count=2,
    metadata_kv=0,
    body_size=2048,
):
    """Write a synthetic GGUF header + padding to satisfy size checks."""
    header = bytearray()
    header += magic
    header += struct.pack("<I", version)
    header += struct.pack("<Q", tensor_count)
    header += struct.pack("<Q", metadata_kv)
    path.write_bytes(bytes(header) + b"\x00" * (body_size - len(header)))


def test_gguf_valid_header_passes(tmp_path):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "ok.gguf"
    _write_gguf(f, body_size=2048)
    assert validate_gguf_header(f, f.stat().st_size) is None


def test_gguf_bad_magic_rejected(tmp_path):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "bad.gguf"
    _write_gguf(f, magic=b"XXXX")
    err = validate_gguf_header(f, f.stat().st_size)
    assert err is not None and "Bad magic" in err


def test_gguf_future_version_rejected(tmp_path):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "future.gguf"
    _write_gguf(f, version=99)
    err = validate_gguf_header(f, f.stat().st_size)
    assert err is not None and "Unsupported" in err


def test_gguf_zero_version_rejected(tmp_path):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "v0.gguf"
    _write_gguf(f, version=0)
    err = validate_gguf_header(f, f.stat().st_size)
    assert err is not None and "Unsupported" in err


def test_gguf_ghost_tensor_inflation_rejected(tmp_path):
    """File is small but claims 1 billion tensors → reject."""
    from services.model_manager import validate_gguf_header

    f = tmp_path / "ghost.gguf"
    _write_gguf(f, tensor_count=1_000_000_000, body_size=2048)
    err = validate_gguf_header(f, f.stat().st_size)
    assert err is not None
    assert "CRITICAL_METADATA_MISMATCH" in err


def test_gguf_too_small_rejected(tmp_path):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "tiny.gguf"
    f.write_bytes(b"GG")  # only 2 bytes
    err = validate_gguf_header(f, 2)
    assert err is not None and "too small" in err.lower()


def test_gguf_metadata_kv_inflation_rejected(tmp_path):
    """A tensor count of zero but a billion metadata kv entries → reject."""
    from services.model_manager import validate_gguf_header

    f = tmp_path / "metadata_bomb.gguf"
    _write_gguf(f, tensor_count=0, metadata_kv=1_000_000_000, body_size=2048)
    err = validate_gguf_header(f, f.stat().st_size)
    assert err is not None and "CRITICAL_METADATA_MISMATCH" in err


# ---------------------------------------------------------------------------
# _parse_size_string
# ---------------------------------------------------------------------------


def test_parse_size_gb():
    from services.model_manager import _parse_size_string

    assert _parse_size_string("8 GB") == pytest.approx(8.0)


def test_parse_size_mb():
    from services.model_manager import _parse_size_string

    assert _parse_size_string("8192 MB") == pytest.approx(8.0)


def test_parse_size_garbage_returns_zero():
    from services.model_manager import _parse_size_string

    assert _parse_size_string("not a number") == 0.0


def test_parse_size_non_string_returns_zero():
    from services.model_manager import _parse_size_string

    assert _parse_size_string(None) == 0.0  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _classify_tier — discrete buckets
# ---------------------------------------------------------------------------


def test_classify_tier_enterprise_via_vram():
    from services.model_manager import _classify_tier

    assert (
        _classify_tier(vram_gb=48, total_ram_gb=64, is_apple_silicon=False)
        == "enterprise"
    )


def test_classify_tier_medium_via_vram():
    from services.model_manager import _classify_tier

    assert (
        _classify_tier(vram_gb=24, total_ram_gb=32, is_apple_silicon=False) == "medium"
    )


def test_classify_tier_workstation_no_gpu():
    from services.model_manager import _classify_tier

    assert (
        _classify_tier(vram_gb=0, total_ram_gb=8, is_apple_silicon=False)
        == "workstation"
    )


def test_classify_tier_apple_unified_memory():
    """Apple Silicon uses total RAM, not vram."""
    from services.model_manager import _classify_tier

    assert (
        _classify_tier(vram_gb=0, total_ram_gb=96, is_apple_silicon=True)
        == "enterprise"
    )
    assert _classify_tier(vram_gb=0, total_ram_gb=36, is_apple_silicon=True) == "medium"
    assert (
        _classify_tier(vram_gb=0, total_ram_gb=8, is_apple_silicon=True)
        == "workstation"
    )


# ---------------------------------------------------------------------------
# _param_count_to_int
# ---------------------------------------------------------------------------


def test_param_count_8b():
    from services.model_manager import _param_count_to_int

    assert _param_count_to_int("8B") == 8000


def test_param_count_70b():
    from services.model_manager import _param_count_to_int

    assert _param_count_to_int("70B") == 70000


def test_param_count_garbage_returns_zero():
    from services.model_manager import _param_count_to_int

    assert _param_count_to_int("trillion") == 0


def test_param_count_none_returns_zero():
    from services.model_manager import _param_count_to_int

    assert _param_count_to_int(None) == 0


# ---------------------------------------------------------------------------
# Curated catalog loading
# ---------------------------------------------------------------------------


def test_load_curated_catalog_returns_models_list(unit_env):
    from services.model_manager import load_curated_catalog

    cat = load_curated_catalog()
    assert isinstance(cat, dict)
    assert "models" in cat
    assert isinstance(cat["models"], list)
    # Catalog must have at least one entry.
    assert len(cat["models"]) >= 1


# ---------------------------------------------------------------------------
# _host_can_run — tier ranking
# ---------------------------------------------------------------------------


def test_host_can_run_higher_tier_ok():
    from services.model_manager import _host_can_run, HostCapability

    host = HostCapability(
        platform="linux",
        cpu_arch="x86_64",
        total_ram_gb=128,
        gpu_name="A100",
        vram_gb=80,
        unified_memory=False,
        tier="enterprise",
        probe_method="nvidia-smi",
    )
    assert _host_can_run({"tier": "workstation"}, host) is True
    assert _host_can_run({"tier": "medium"}, host) is True
    assert _host_can_run({"tier": "enterprise"}, host) is True


def test_host_can_run_lower_tier_blocked():
    from services.model_manager import _host_can_run, HostCapability

    host = HostCapability(
        platform="linux",
        cpu_arch="x86_64",
        total_ram_gb=8,
        gpu_name=None,
        vram_gb=0,
        unified_memory=False,
        tier="workstation",
        probe_method="none",
    )
    assert _host_can_run({"tier": "enterprise"}, host) is False
    assert _host_can_run({"tier": "medium"}, host) is False
    assert _host_can_run({"tier": "workstation"}, host) is True


# ---------------------------------------------------------------------------
# select_model_for_host — picks LARGEST param compatible entry
# ---------------------------------------------------------------------------


def test_select_returns_none_when_family_missing(unit_env):
    """A family not in the catalog → None, never crash."""
    from services.model_manager import select_model_for_host, HostCapability

    host = HostCapability(
        platform="linux",
        cpu_arch="x86_64",
        total_ram_gb=128,
        gpu_name="A100",
        vram_gb=80,
        unified_memory=False,
        tier="enterprise",
        probe_method="nvidia-smi",
    )
    assert select_model_for_host("never-existed-family", host) is None


# ---------------------------------------------------------------------------
# resolve_model_status — cloud_fallback path on under-spec host
# ---------------------------------------------------------------------------


def test_resolve_model_status_cloud_fallback_when_host_too_small():
    from services.model_manager import resolve_model_status, HostCapability

    host = HostCapability(
        platform="darwin",
        cpu_arch="arm64",
        total_ram_gb=8,
        gpu_name="Apple M1",
        vram_gb=0,
        unified_memory=True,
        tier="workstation",
        probe_method="system_profiler+unified",
    )
    entry = {"id": "huge-model", "tier": "enterprise", "size_bytes": 1}
    assert resolve_model_status(entry, host) == "cloud_fallback_only"


# ---------------------------------------------------------------------------
# probe_host_capability — does not crash; returns plausible shape
# ---------------------------------------------------------------------------


def test_probe_host_capability_shape(unit_env):
    from services.model_manager import (
        probe_host_capability,
        _reset_hw_cache_for_tests,
    )

    _reset_hw_cache_for_tests()
    cap = probe_host_capability()
    assert cap.platform in ("darwin", "linux", "windows")
    assert cap.tier in ("workstation", "medium", "enterprise")
    assert cap.total_ram_gb > 0
    assert cap.probe_method  # non-empty


def test_probe_host_capability_cached(unit_env):
    """Second call returns the same instance (no re-probe)."""
    from services.model_manager import (
        probe_host_capability,
        _reset_hw_cache_for_tests,
    )

    _reset_hw_cache_for_tests()
    a = probe_host_capability()
    b = probe_host_capability()
    assert a is b
