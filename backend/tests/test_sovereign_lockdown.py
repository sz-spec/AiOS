"""Sovereign Lockdown Tests — v19.4 Unbreakable Alignment Patch.

Offline-only tests (no QEMU needed):
  1. VBusSecurityError on legacy (non-HMAC) handshake
  2. VBusSecurityError on no HANDSHAKE response after 5 frames
  3. Entropy filter effectiveness (CRC offline)
  4. VBusSecurityError is subclass of VBusError
"""

from unittest.mock import patch, MagicMock

import pytest

from services.vbus_driver import (
    VBusDriver,
    VBusError,
    VBusSecurityError,
    VBUS_TYPE_HANDSHAKE,
    VBUS_TYPE_EVENT,
)


# ---------------------------------------------------------------------------
# Test 1: Legacy handshake (no HMAC) → VBusSecurityError
# ---------------------------------------------------------------------------
def test_legacy_handshake_raises_security_error():
    """A HANDSHAKE response without 'HMAC' must raise VBusSecurityError."""
    driver = VBusDriver(socket_path="/tmp/fake.sock")

    mock_sock = MagicMock()
    # recv returns empty on drain, then setblocking/settimeout are no-ops
    mock_sock.recv.side_effect = BlockingIOError

    # Patch socket.socket() to return our mock (connect() creates a new socket)
    with patch(
        "services.vbus_driver.socket.socket", return_value=mock_sock
    ), patch.object(driver, "_send_frame"), patch.object(
        driver,
        "_recv_frame",
        return_value=(VBUS_TYPE_HANDSHAKE, 0xFF, 0, b"VOS3-VBUS2-OK"),
    ):
        with pytest.raises(VBusSecurityError, match="unauthenticated"):
            driver.connect()

    # Socket should be closed after security error
    mock_sock.close.assert_called()


# ---------------------------------------------------------------------------
# Test 2: No HANDSHAKE in 5 frames → VBusSecurityError
# ---------------------------------------------------------------------------
def test_no_handshake_raises_security_error():
    """5 consecutive EVENT frames and no HANDSHAKE → VBusSecurityError."""
    driver = VBusDriver(socket_path="/tmp/fake.sock")

    mock_sock = MagicMock()
    mock_sock.recv.side_effect = BlockingIOError

    event_frame = (VBUS_TYPE_EVENT, 0, 0, b"event-payload-xx")

    with patch(
        "services.vbus_driver.socket.socket", return_value=mock_sock
    ), patch.object(driver, "_send_frame"), patch.object(
        driver, "_recv_frame", return_value=event_frame
    ):
        with pytest.raises(VBusSecurityError, match="no HANDSHAKE response"):
            driver.connect()

    mock_sock.close.assert_called()


# ---------------------------------------------------------------------------
# Test 3: Entropy filter effectiveness (offline Python simulation)
# ---------------------------------------------------------------------------
def test_entropy_filter_effectiveness():
    """Simulate the high-pass entropy filter and verify noise reduction.

    Creates 128 pages: 100 filled with repetitive bytes (<=4 distinct),
    28 with random-ish data (>4 distinct). After filtering, effective bytes
    from repetitive pages should drop to 0.
    """
    PAGE_SIZE = 4096
    BLOCK_SIZE = 64

    # Build 128 pages
    pages = []
    # 100 repetitive pages (e.g. 0xAA repeated, or 2-3 distinct values)
    for i in range(100):
        # Use 1-4 distinct byte values in a repeating pattern
        vals = bytes([i % 256, (i + 1) % 256, (i + 2) % 256])
        page = (vals * ((PAGE_SIZE // len(vals)) + 1))[:PAGE_SIZE]
        pages.append(bytearray(page))

    # 28 high-entropy pages (many distinct values)
    for i in range(28):
        page = bytearray((b * 7 + i) % 256 for b in range(PAGE_SIZE))
        pages.append(page)

    # Apply entropy filter (same logic as kernel Phase A.5 v2)
    total_zeroed_blocks = 0
    for page in pages:
        for blk_start in range(0, PAGE_SIZE, BLOCK_SIZE):
            block = page[blk_start : blk_start + BLOCK_SIZE]
            distinct = len(set(block))
            if distinct <= 4:
                page[blk_start : blk_start + BLOCK_SIZE] = b"\x00" * BLOCK_SIZE
                total_zeroed_blocks += 1

    # Count effective (non-zero) bytes per page
    effective_repetitive = sum(sum(1 for b in pages[i] if b != 0) for i in range(100))
    effective_random = sum(sum(1 for b in pages[i] if b != 0) for i in range(100, 128))

    # Repetitive pages should be fully zeroed (0 effective bytes)
    assert (
        effective_repetitive == 0
    ), f"Expected 0 effective bytes from repetitive pages, got {effective_repetitive}"
    # Random pages should retain most of their data
    assert (
        effective_random > 28 * PAGE_SIZE * 0.5
    ), f"Random pages lost too much data: {effective_random}"
    # Sanity: at least 100 * 64 blocks zeroed (100 pages × 64 blocks each)
    assert total_zeroed_blocks >= 100 * (PAGE_SIZE // BLOCK_SIZE), (
        f"Expected >= {100 * (PAGE_SIZE // BLOCK_SIZE)} zeroed blocks, "
        f"got {total_zeroed_blocks}"
    )


# ---------------------------------------------------------------------------
# Test 4: VBusSecurityError is a subclass of VBusError
# ---------------------------------------------------------------------------
def test_security_error_is_vbus_error_subclass():
    """VBusSecurityError must be a subclass of VBusError for handler compat."""
    assert issubclass(VBusSecurityError, VBusError)
    assert issubclass(VBusSecurityError, Exception)
    # An instance should be catchable as VBusError
    err = VBusSecurityError("test")
    assert isinstance(err, VBusError)
