"""
Tests for Sprint 22 / Item K2 — sparse weight-overlay format
(backend/services/weight_overlay.py).

Pins the fail-closed contract: an overlay reconstructs the variant only
when applied to the exact base it was built against; a wrong base or a
tampered overlay is refused.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.weight_overlay import (  # noqa: E402
    OverlayBaseMismatch,
    OverlayIntegrityError,
    OverlayShapeError,
    WeightOverlay,
    apply_overlay,
    build_overlay,
)

BS = 4096


def _base(n_blocks=8):
    return bytes(((i % 251) for i in range(n_blocks * BS)))


def test_build_apply_roundtrip():
    base = _base()
    variant = bytearray(base)
    variant[2 * BS : 2 * BS + 4] = b"\xde\xad\xbe\xef"  # change one block
    variant = bytes(variant)
    overlay = build_overlay(base, variant, block_size=BS)
    assert apply_overlay(base, overlay) == variant


def test_overlay_is_sparse():
    base = _base(n_blocks=10)
    variant = bytearray(base)
    variant[0] ^= 0xFF  # change only block 0
    overlay = build_overlay(base, bytes(variant), block_size=BS)
    assert overlay.changed_blocks == 1
    assert overlay.sparsity > 0.8  # 9/10 blocks shared


def test_apply_to_wrong_base_refused():
    base = _base()
    variant = bytearray(base)
    variant[100] ^= 0xFF
    overlay = build_overlay(base, bytes(variant), block_size=BS)
    wrong_base = bytearray(base)
    wrong_base[5000] ^= 0xFF  # different base
    with pytest.raises(OverlayBaseMismatch):
        apply_overlay(bytes(wrong_base), overlay)


def test_tampered_block_refused():
    base = _base()
    variant = bytearray(base)
    variant[200] ^= 0xFF
    overlay = build_overlay(base, bytes(variant), block_size=BS)
    blk = overlay.blocks[0]
    tampered = type(blk)(
        byte_offset=blk.byte_offset,
        data=b"x" + blk.data[1:],
        block_sha256=blk.block_sha256,
    )  # data changed, hash stale
    bad = WeightOverlay(
        base_sha256=overlay.base_sha256,
        total_length=overlay.total_length,
        block_size=overlay.block_size,
        blocks=(tampered,),
        overlay_sha256=overlay.overlay_sha256,
    )
    with pytest.raises(OverlayIntegrityError):
        apply_overlay(base, bad)


def test_tampered_digest_refused():
    base = _base()
    variant = bytearray(base)
    variant[300] ^= 0xFF
    overlay = build_overlay(base, bytes(variant), block_size=BS)
    bad = WeightOverlay(
        base_sha256=overlay.base_sha256,
        total_length=overlay.total_length,
        block_size=overlay.block_size,
        blocks=overlay.blocks,
        overlay_sha256="0" * 64,
    )
    with pytest.raises(OverlayIntegrityError):
        apply_overlay(base, bad)


def test_shape_change_refused():
    base = _base()
    longer = base + b"\x00" * BS
    with pytest.raises(OverlayShapeError):
        build_overlay(base, longer, block_size=BS)


def test_expected_base_pin_mismatch_refused():
    base = _base()
    variant = bytearray(base)
    variant[10] ^= 0xFF
    overlay = build_overlay(base, bytes(variant), block_size=BS)
    with pytest.raises(OverlayBaseMismatch):
        apply_overlay(base, overlay, expected_base_sha256="f" * 64)
