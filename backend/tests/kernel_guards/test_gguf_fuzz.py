"""
Stage 3 · GGUF header validation fuzz.

Pen-tests `validate_gguf_header` — the "Ghost Tensor" defense.
Every test crafts a malformed GGUF (per the on-disk layout) and
asserts the validator rejects it BEFORE the kernel/inference path
would see corrupt metadata.

GGUF v3 header layout (little-endian):
  bytes 0-3:    magic ("GGUF")
  bytes 4-7:    version (uint32)
  bytes 8-15:   tensor_count (uint64)
  bytes 16-23:  metadata_kv_count (uint64)
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest


def _gguf(
    path: Path,
    *,
    magic=b"GGUF",
    version=3,
    tensor_count=2,
    metadata_kv=0,
    body_size=2048,
):
    header = bytearray()
    header += magic
    header += struct.pack("<I", version)
    header += struct.pack("<Q", tensor_count)
    header += struct.pack("<Q", metadata_kv)
    path.write_bytes(bytes(header) + b"\x00" * (body_size - len(header)))


# ---------------------------------------------------------------------------
# Magic byte fuzz — 12 variants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_magic",
    [
        b"XXXX",
        b"AAAA",
        b"\x00\x00\x00\x00",
        b"\xff\xff\xff\xff",
        b"GGUf",  # case
        b"FUGG",  # reversed
        b"GGU\x00",  # almost
        b"GG\x00F",
        b"\xefGG\x00",
        b"PNG\x00",
        b"\x7fELF",  # ELF
        b"MZAP",
    ],
)
def test_bad_magic_rejected(tmp_path, bad_magic):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "bad.gguf"
    _gguf(f, magic=bad_magic)
    err = validate_gguf_header(f, f.stat().st_size)
    assert err is not None and "Bad magic" in err


# ---------------------------------------------------------------------------
# Version fuzz — invalid versions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", [0, 5, 99, 1000, 2**31 - 1, 2**32 - 1])
def test_invalid_version_rejected(tmp_path, version):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "v.gguf"
    _gguf(f, version=version)
    err = validate_gguf_header(f, f.stat().st_size)
    assert err is not None and "Unsupported" in err


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_valid_version_accepted(tmp_path, version):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "v.gguf"
    _gguf(f, version=version)
    assert validate_gguf_header(f, f.stat().st_size) is None


# ---------------------------------------------------------------------------
# Tensor count inflation — Ghost Tensor defense
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "count",
    [
        100_001,  # one over the cap
        1_000_000,  # 1M
        100_000_000,  # 100M
        1_000_000_000,  # 1B
        2**63 - 1,  # max int64
    ],
)
def test_tensor_count_inflation_rejected(tmp_path, count):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "ghost.gguf"
    _gguf(f, tensor_count=count, body_size=2048)
    err = validate_gguf_header(f, f.stat().st_size)
    assert err is not None and "CRITICAL_METADATA_MISMATCH" in err


# ---------------------------------------------------------------------------
# Size-vs-tensor plausibility — file too small for claimed tensors
# ---------------------------------------------------------------------------


def test_tensor_count_inconsistent_with_file_size(tmp_path):
    """Claim 100 tensors but file is only 2 KB. Each tensor needs >= 64
    bytes, so 100 × 64 + 24 = 6424 > 2048 → reject."""
    from services.model_manager import validate_gguf_header

    f = tmp_path / "lying.gguf"
    _gguf(f, tensor_count=100, body_size=2048)
    err = validate_gguf_header(f, f.stat().st_size)
    assert err is not None and "CRITICAL_METADATA_MISMATCH" in err


def test_tensor_count_consistent_with_file_size(tmp_path):
    """Claim 10 tensors with a 4 KB body — 10 × 64 + 24 = 664, fits."""
    from services.model_manager import validate_gguf_header

    f = tmp_path / "ok.gguf"
    _gguf(f, tensor_count=10, body_size=4096)
    assert validate_gguf_header(f, f.stat().st_size) is None


# ---------------------------------------------------------------------------
# File-too-small at threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size_bytes", [0, 1, 5, 23])
def test_file_smaller_than_header_rejected(tmp_path, size_bytes):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "tiny.gguf"
    f.write_bytes(b"A" * size_bytes)
    err = validate_gguf_header(f, size_bytes)
    assert err is not None and "too small" in err.lower()


def test_file_exactly_header_size_passes(tmp_path):
    """Exactly 24 bytes with tensor_count=0 → passes."""
    from services.model_manager import validate_gguf_header

    f = tmp_path / "exact.gguf"
    _gguf(f, tensor_count=0, body_size=24)
    assert validate_gguf_header(f, f.stat().st_size) is None


# ---------------------------------------------------------------------------
# Metadata kv inflation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kv", [10_001, 100_000, 1_000_000, 2**60])
def test_metadata_kv_inflation_rejected(tmp_path, kv):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "metabomb.gguf"
    _gguf(f, tensor_count=0, metadata_kv=kv, body_size=2048)
    err = validate_gguf_header(f, f.stat().st_size)
    assert err is not None and "CRITICAL_METADATA_MISMATCH" in err


@pytest.mark.parametrize("kv", [0, 1, 10, 100, 1000, 9999])
def test_metadata_kv_under_cap_accepted(tmp_path, kv):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "ok-meta.gguf"
    _gguf(f, tensor_count=0, metadata_kv=kv, body_size=2048)
    assert validate_gguf_header(f, f.stat().st_size) is None


# ---------------------------------------------------------------------------
# Short read of header
# ---------------------------------------------------------------------------


def test_partial_header_truncation(tmp_path):
    """Write only the first 12 bytes — header read returns short → reject."""
    from services.model_manager import validate_gguf_header

    f = tmp_path / "trunc.gguf"
    # Magic + version, but missing tensor_count + metadata_kv.
    body = b"GGUF" + struct.pack("<I", 3) + struct.pack("<I", 0)
    # But we'll claim file_size = 100 to trick the early check.
    f.write_bytes(body)
    err = validate_gguf_header(f, 100)
    # The header read returns only 12 bytes → "Short read" path.
    assert err is not None and ("Short read" in err or "too small" in err.lower())


# ---------------------------------------------------------------------------
# Permission / unreadable file
# ---------------------------------------------------------------------------


def test_nonexistent_path_handled_gracefully(tmp_path):
    """A path that doesn't exist returns an error string, not a crash."""
    from services.model_manager import validate_gguf_header

    f = tmp_path / "missing.gguf"
    err = validate_gguf_header(f, 2048)
    assert err is not None  # either "Cannot read header" or "too small"


# ---------------------------------------------------------------------------
# Edge: tensor_count == max cap
# ---------------------------------------------------------------------------


def test_tensor_count_at_cap_with_enough_body(tmp_path):
    """tensor_count = 100_000 (the cap) → accepted IF body fits."""
    from services.model_manager import validate_gguf_header

    f = tmp_path / "cap.gguf"
    # 100_000 × 64 + 24 = 6_400_024 bytes minimum.
    _gguf(f, tensor_count=100_000, body_size=6_500_000)
    assert validate_gguf_header(f, f.stat().st_size) is None


# ---------------------------------------------------------------------------
# Sanity check on combined attack — both inflations simultaneously
# ---------------------------------------------------------------------------


def test_both_tensor_and_metadata_inflation_rejected(tmp_path):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "doublebomb.gguf"
    _gguf(f, tensor_count=999_999_999, metadata_kv=999_999, body_size=2048)
    err = validate_gguf_header(f, f.stat().st_size)
    assert err is not None
    # Either inflation catches it — tensor_count is checked first.
    assert "CRITICAL_METADATA_MISMATCH" in err


# ---------------------------------------------------------------------------
# Empty / single-byte / 23-byte files (boundary)
# ---------------------------------------------------------------------------


def test_single_byte_file_rejected(tmp_path):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "1.gguf"
    f.write_bytes(b"G")
    err = validate_gguf_header(f, 1)
    assert err is not None


def test_23_byte_file_rejected(tmp_path):
    from services.model_manager import validate_gguf_header

    f = tmp_path / "23.gguf"
    f.write_bytes(b"GGUF" + b"\x03" + b"\x00" * 18)
    err = validate_gguf_header(f, 23)
    assert err is not None and "too small" in err.lower()
