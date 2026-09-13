"""
backend/services/weight_overlay.py
==================================

Sprint 22 / Item K2 (software sliver) — sparse weight-overlay format
with base-hash verification (fail-closed) (NEW row).

What this is
------------

From the 80-problem agent-era catalog, K2:
  "Copy-on-write breaks on fine-tuned diffs — when an operator runs
   many fine-tunes of one base model, the OS page cache / filesystem CoW
   can't share storage because a fine-tune rewrites weights throughout
   the file, so each variant costs a full copy. There is no
   diff-against-base format that the loader understands and can verify."

vOS's software sliver (the CXL-tiering half stays in Partition 2): a
**sparse weight-overlay format**. A fine-tune is stored as the set of
*changed blocks* over a named base, plus the base's SHA-256. Many
fine-tunes share one base on disk; each overlay is small. Critically,
the loader **verifies the base hash before applying an overlay** and
refuses to apply an overlay to a base it wasn't computed against —
applying a diff to the wrong base would silently corrupt the model.
Each block is content-hashed so a tampered overlay is also caught.

Enforcement contract
--------------------

    overlay = build_overlay(base_bytes, finetuned_bytes)   # sparse diff
    # ... store overlay (small) + share base_bytes across variants ...
    restored = apply_overlay(base_bytes, overlay)          # == finetuned_bytes
    apply_overlay(WRONG_base_bytes, overlay)               # raises
                                                           # OverlayBaseMismatch

Honest scope ceilings
--------------------

  - The diff is BYTE/BLOCK level over the serialized weight image, not
    tensor-aware. It captures the dominant "same architecture, weights
    changed" fine-tune case (full FT or merged LoRA) where base and
    variant have the same serialized length. Shape-changing edits
    (added layers) fall back to a full copy — build_overlay raises so the
    caller stores the variant whole rather than producing a wrong diff.
  - This is the format + verification layer. Wiring it into the model
    loader's mmap path (so the page cache actually shares the base) is
    the integration follow-up; the verified format is the primitive.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_BLOCK_SIZE = 4096


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class OverlayError(Exception):
    """Base class."""


class OverlayBaseMismatch(OverlayError):
    """Raised when an overlay is applied to a base whose hash doesn't match
    the overlay's declared base_sha256. Fail-closed."""


class OverlayIntegrityError(OverlayError):
    """Raised when an overlay's own integrity check fails (a block hash or
    the overlay digest doesn't match). Fail-closed."""


class OverlayShapeError(OverlayError):
    """Raised when base and variant differ in serialized length — a
    shape-changing edit that the sparse format can't represent."""


@dataclass(frozen=True)
class OverlayBlock:
    byte_offset: int
    data: bytes
    block_sha256: str

    @staticmethod
    def of(byte_offset: int, data: bytes) -> "OverlayBlock":
        return OverlayBlock(
            byte_offset=byte_offset, data=data, block_sha256=_sha256_hex(data)
        )


@dataclass(frozen=True)
class WeightOverlay:
    base_sha256: str
    total_length: int  # serialized length of the (identical-length) variant
    block_size: int
    blocks: tuple  # tuple[OverlayBlock, ...] — only CHANGED blocks
    overlay_sha256: str  # digest over (base_sha256, total_length, block hashes)

    @property
    def changed_blocks(self) -> int:
        return len(self.blocks)

    @property
    def sparsity(self) -> float:
        """Fraction of blocks left UNchanged (shared with the base)."""
        total = max(1, (self.total_length + self.block_size - 1) // self.block_size)
        return 1.0 - (len(self.blocks) / total)


def _overlay_digest(
    base_sha256: str, total_length: int, block_size: int, blocks
) -> str:
    h = hashlib.sha256()
    h.update(b"vos3-weight-overlay-v1")
    h.update(base_sha256.encode("utf-8"))
    h.update(total_length.to_bytes(8, "big"))
    h.update(block_size.to_bytes(4, "big"))
    for blk in blocks:
        h.update(blk.byte_offset.to_bytes(8, "big"))
        h.update(bytes.fromhex(blk.block_sha256))
    return h.hexdigest()


def build_overlay(
    base: bytes,
    variant: bytes,
    *,
    block_size: int = DEFAULT_BLOCK_SIZE,
) -> WeightOverlay:
    """Compute a sparse overlay of ``variant`` over ``base`` — only the
    blocks that differ are stored. Raises OverlayShapeError if the two
    differ in length (a shape-changing edit the format can't represent)."""
    if len(base) != len(variant):
        raise OverlayShapeError(
            f"base length {len(base)} != variant length {len(variant)} — "
            f"shape-changing edit cannot be a sparse overlay; store whole"
        )
    if block_size <= 0:
        raise OverlayError("block_size must be > 0")

    blocks = []
    for off in range(0, len(variant), block_size):
        b_blk = base[off : off + block_size]
        v_blk = variant[off : off + block_size]
        if b_blk != v_blk:
            blocks.append(OverlayBlock.of(off, v_blk))
    blocks = tuple(blocks)
    base_hash = _sha256_hex(base)
    return WeightOverlay(
        base_sha256=base_hash,
        total_length=len(variant),
        block_size=block_size,
        blocks=blocks,
        overlay_sha256=_overlay_digest(base_hash, len(variant), block_size, blocks),
    )


def verify_overlay_integrity(overlay: WeightOverlay) -> None:
    """Raise OverlayIntegrityError if the overlay's own digest or any block
    hash doesn't match. Does NOT touch the base."""
    for blk in overlay.blocks:
        if _sha256_hex(blk.data) != blk.block_sha256:
            raise OverlayIntegrityError(
                f"block at offset {blk.byte_offset} hash mismatch (tampered)"
            )
    expected = _overlay_digest(
        overlay.base_sha256,
        overlay.total_length,
        overlay.block_size,
        overlay.blocks,
    )
    if not hmac.compare_digest(expected, overlay.overlay_sha256):
        raise OverlayIntegrityError("overlay digest mismatch (tampered)")


def apply_overlay(
    base: bytes,
    overlay: WeightOverlay,
    *,
    expected_base_sha256: Optional[str] = None,
) -> bytes:
    """Apply ``overlay`` onto ``base`` and return the reconstructed variant.

    Fail-closed checks BEFORE applying:
      1. The overlay's own integrity (block hashes + digest).
      2. base's actual SHA-256 == overlay.base_sha256 (refuse a wrong base).
      3. (optional) overlay.base_sha256 == expected_base_sha256 (caller pin).
    """
    verify_overlay_integrity(overlay)

    actual_base = _sha256_hex(base)
    if not hmac.compare_digest(actual_base, overlay.base_sha256):
        raise OverlayBaseMismatch(
            f"overlay was computed against base {overlay.base_sha256[:16]}… "
            f"but the supplied base hashes to {actual_base[:16]}… — refusing "
            f"to apply (would corrupt the model)"
        )
    if expected_base_sha256 is not None and not hmac.compare_digest(
        overlay.base_sha256, expected_base_sha256
    ):
        raise OverlayBaseMismatch(
            f"overlay base {overlay.base_sha256[:16]}… != caller-pinned base "
            f"{expected_base_sha256[:16]}…"
        )
    if len(base) != overlay.total_length:
        raise OverlayBaseMismatch(
            f"base length {len(base)} != overlay total_length "
            f"{overlay.total_length}"
        )

    out = bytearray(base)
    for blk in overlay.blocks:
        out[blk.byte_offset : blk.byte_offset + len(blk.data)] = blk.data
    return bytes(out)


__all__ = [
    "OverlayError",
    "OverlayBaseMismatch",
    "OverlayIntegrityError",
    "OverlayShapeError",
    "OverlayBlock",
    "WeightOverlay",
    "build_overlay",
    "verify_overlay_integrity",
    "apply_overlay",
    "DEFAULT_BLOCK_SIZE",
]
