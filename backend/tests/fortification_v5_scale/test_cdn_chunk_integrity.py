"""
Phase 1 · CDN chunk integrity — SHA-256 cascade verification.

Honest scope
------------
We don't actually fetch from 50 global CDN regions. We simulate the
**verification logic**: a multi-chunk artifact is downloaded; if ANY
chunk's SHA-256 doesn't match the manifest, the cascade rejects and
the installer refuses to execute. The "50 regions" framing maps to 50
parametrized corruption scenarios at different chunk offsets.

The cascade is a hash-of-hashes: `final_hash = SHA256(h1 || h2 || ...
|| hN)`. Tampering with any chunk invalidates the final hash.
"""

from __future__ import annotations

import hashlib

import pytest


def _build_chunks(
    n_chunks: int, chunk_size: int, seed: int = 0xDEADBEEF
) -> list[bytes]:
    """Deterministic chunk generation — same seed → same chunks."""
    import random

    rng = random.Random(seed)
    return [
        bytes(rng.getrandbits(8) for _ in range(chunk_size)) for _ in range(n_chunks)
    ]


def _manifest_hashes(chunks: list[bytes]) -> list[str]:
    return [hashlib.sha256(c).hexdigest() for c in chunks]


def _cascade_hash(chunk_hashes: list[str]) -> str:
    return hashlib.sha256("".join(chunk_hashes).encode("ascii")).hexdigest()


def _verify_cascade(chunks: list[bytes], expected_cascade: str) -> bool:
    return _cascade_hash(_manifest_hashes(chunks)) == expected_cascade


# ---------------------------------------------------------------------------
# Positive control — clean chunks verify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n_chunks,chunk_size",
    [
        (1, 1024),
        (4, 1024),
        (16, 1024),
        (64, 1024),
        (4, 4096),
        (4, 16384),
        (4, 65536),
    ],
)
def test_intact_cascade_verifies(n_chunks, chunk_size):
    chunks = _build_chunks(n_chunks, chunk_size)
    cascade = _cascade_hash(_manifest_hashes(chunks))
    assert _verify_cascade(chunks, cascade) is True


# ---------------------------------------------------------------------------
# 50 corruption scenarios — one byte flipped at varying offsets in
# varying chunks. Each must be rejected.
# ---------------------------------------------------------------------------

_CHUNKS = _build_chunks(50, 1024)
_CASCADE = _cascade_hash(_manifest_hashes(_CHUNKS))


@pytest.mark.parametrize("region_idx", list(range(50)))
def test_50_corrupted_regions_rejected(region_idx):
    """Each parametrize 'region' represents a different CDN edge that
    returned a corrupted chunk. Cascade must reject."""
    chunks = [bytearray(c) for c in _CHUNKS]
    # Flip a single byte in the chunk corresponding to this region.
    chunks[region_idx][region_idx % 1024] ^= 0x01
    chunks_final = [bytes(c) for c in chunks]
    assert _verify_cascade(chunks_final, _CASCADE) is False


# ---------------------------------------------------------------------------
# Multi-byte corruptions across one chunk
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_flips", [1, 2, 4, 8, 16, 32, 64, 128, 256, 512])
def test_multi_byte_corruption_rejected(n_flips):
    chunks = [bytearray(c) for c in _CHUNKS]
    import random

    rng = random.Random(n_flips)
    for _ in range(n_flips):
        offset = rng.randint(0, 1023)
        chunks[0][offset] ^= 0xFF
    chunks_final = [bytes(c) for c in chunks]
    assert _verify_cascade(chunks_final, _CASCADE) is False


# ---------------------------------------------------------------------------
# Chunk swap — two chunks exchanged. Each chunk individually has the
# right hash, but the cascade ordering changes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        (0, 1),
        (0, 49),
        (1, 2),
        (10, 20),
        (5, 45),
        (3, 30),
        (24, 25),
        (0, 25),
        (0, 49),
        (15, 35),
    ],
)
def test_chunk_swap_detected_by_cascade(a, b):
    chunks = list(_CHUNKS)
    chunks[a], chunks[b] = chunks[b], chunks[a]
    # The individual chunks are the SAME bytes, but the cascade depends
    # on ORDER. Swapping detected.
    if chunks == list(_CHUNKS):
        pytest.skip("a==b — degenerate")
    assert _verify_cascade(chunks, _CASCADE) is False


# ---------------------------------------------------------------------------
# Truncation — chunk dropped entirely
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("drop_idx", [0, 1, 5, 10, 25, 49])
def test_truncated_chunks_rejected(drop_idx):
    chunks = [c for i, c in enumerate(_CHUNKS) if i != drop_idx]
    assert _verify_cascade(chunks, _CASCADE) is False


# ---------------------------------------------------------------------------
# Empty chunk substitution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nul_idx", [0, 5, 10, 25, 49])
def test_empty_chunk_substitution_rejected(nul_idx):
    chunks = list(_CHUNKS)
    chunks[nul_idx] = b""
    assert _verify_cascade(chunks, _CASCADE) is False


# ---------------------------------------------------------------------------
# Cascade is order-sensitive
# ---------------------------------------------------------------------------


def test_cascade_is_order_dependent():
    chunks = [b"alpha", b"beta", b"gamma"]
    h1 = _cascade_hash(_manifest_hashes(chunks))
    chunks_reordered = [b"beta", b"alpha", b"gamma"]
    h2 = _cascade_hash(_manifest_hashes(chunks_reordered))
    assert h1 != h2


def test_cascade_is_deterministic():
    chunks = [b"a", b"b", b"c"]
    for _ in range(10):
        h = _cascade_hash(_manifest_hashes(chunks))
    expected = _cascade_hash(_manifest_hashes(chunks))
    assert h == expected


# ---------------------------------------------------------------------------
# Self-healing — if the cascade rejects, the installer refuses execution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("region_idx", list(range(10)))
def test_self_healing_refuses_corrupted_artifact(region_idx):
    """Synthetic 'install' function — refuses to execute when cascade fails."""

    def install_if_clean(chunks, expected_cascade):
        if not _verify_cascade(chunks, expected_cascade):
            return "REFUSED_CORRUPTED"
        return "INSTALLED"

    chunks = [bytearray(c) for c in _CHUNKS]
    chunks[region_idx][0] ^= 0xFF
    chunks_final = [bytes(c) for c in chunks]
    assert install_if_clean(chunks_final, _CASCADE) == "REFUSED_CORRUPTED"


def test_self_healing_accepts_clean_artifact():
    def install_if_clean(chunks, expected_cascade):
        if not _verify_cascade(chunks, expected_cascade):
            return "REFUSED_CORRUPTED"
        return "INSTALLED"

    assert install_if_clean(_CHUNKS, _CASCADE) == "INSTALLED"
