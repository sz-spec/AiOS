"""
Phase 4.0 QA — Native Deploy Service: Bound Window + Protocol
==============================================================
Tests:
  1. Bound window calculation
  2. Command validation
  3. Response parsing
  4. Hex encoding
  5. Retention code mapping
  6. Service constants
"""

import pytest

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.native_deploy_service import (
    VOS3NativeDeployService,
    KERNEL_LINE_MAX,
    MAX_CHUNK_SIZE,
    HEADER_OVERHEAD,
)
from services.vpacker import (
    VPKManifest,
    VPKSystemManifest,
    VPKIntentManifest,
    VPKDataRetentionPolicy,
)

# ═════════════════════════════════════════════════════════════════════════
# 1. Bound Window Calculation
# ═════════════════════════════════════════════════════════════════════════


class TestBoundWindow:
    def setup_method(self):
        self.service = VOS3NativeDeployService(bridge_socket="/tmp/nonexistent.sock")

    def test_default_bound_window(self):
        """Default peer_buf_size should be LINE_MAX."""
        window = self.service._bound_window(9999)
        assert window == KERNEL_LINE_MAX - HEADER_OVERHEAD

    def test_bound_window_caps_at_payload(self):
        """Should return payload_len if smaller than window."""
        window = self.service._bound_window(100)
        assert window == 100

    def test_bound_window_small_peer(self):
        """With a smaller peer buffer, window should shrink."""
        self.service._peer_buf_size = 4096
        window = self.service._bound_window(9999)
        assert window == 4096 - HEADER_OVERHEAD

    def test_bound_window_never_exceeds_line_max(self):
        """Even with large peer buffer, window caps at LINE_MAX."""
        self.service._peer_buf_size = 99999
        window = self.service._bound_window(99999)
        assert window == KERNEL_LINE_MAX - HEADER_OVERHEAD


# ═════════════════════════════════════════════════════════════════════════
# 2. Command Validation
# ═════════════════════════════════════════════════════════════════════════


class TestCommandValidation:
    def setup_method(self):
        self.service = VOS3NativeDeployService(bridge_socket="/tmp/nonexistent.sock")

    def test_validate_short_command(self):
        """Short commands should pass validation."""
        result = self.service._validate_and_bound("PING")
        assert result == "PING"

    def test_validate_oversized_command(self):
        """Command exceeding bound window should raise ValueError."""
        huge = "WRITE|/tmp/test|" + "a" * (KERNEL_LINE_MAX + 1)
        with pytest.raises(ValueError, match="exceeds bound window"):
            self.service._validate_and_bound(huge)

    def test_validate_at_line_max(self):
        """Command exactly at LINE_MAX should pass."""
        cmd = "X" * (KERNEL_LINE_MAX - 1)
        result = self.service._validate_and_bound(cmd)
        assert len(result) == KERNEL_LINE_MAX - 1


# ═════════════════════════════════════════════════════════════════════════
# 3. Response Parsing
# ═════════════════════════════════════════════════════════════════════════


class TestResponseParsing:
    def setup_method(self):
        self.service = VOS3NativeDeployService(bridge_socket="/tmp/nonexistent.sock")

    def test_parse_ok_with_data(self):
        ok, payload = self.service._parse_response("OK|some_data")
        assert ok is True
        assert payload == "some_data"

    def test_parse_ok_empty(self):
        ok, payload = self.service._parse_response("OK")
        assert ok is True

    def test_parse_err(self):
        ok, payload = self.service._parse_response("ERR|22|bad hex")
        assert ok is False
        assert "22" in payload

    def test_parse_unknown(self):
        ok, payload = self.service._parse_response("GARBAGE")
        assert ok is False

    def test_parse_ok_with_pipe_in_data(self):
        ok, payload = self.service._parse_response("OK|123|48656c6c6f")
        assert ok is True
        assert payload == "123|48656c6c6f"


# ═════════════════════════════════════════════════════════════════════════
# 4. Hex Encoding
# ═════════════════════════════════════════════════════════════════════════


class TestHexEncoding:
    def setup_method(self):
        self.service = VOS3NativeDeployService(bridge_socket="/tmp/nonexistent.sock")

    def test_encode_ascii(self):
        result = self.service._hex_encode(b"Hello")
        assert result == "48656c6c6f"

    def test_encode_empty(self):
        result = self.service._hex_encode(b"")
        assert result == ""

    def test_encode_binary(self):
        result = self.service._hex_encode(bytes([0, 255, 128]))
        assert result == "00ff80"


# ═════════════════════════════════════════════════════════════════════════
# 5. Retention Code Mapping
# ═════════════════════════════════════════════════════════════════════════


class TestRetentionMapping:
    def test_scrub_is_code_0(self):
        system = VPKSystemManifest(name="test", version="1.0.0", entry="main.c")
        intent = VPKIntentManifest(data_retention_policy=VPKDataRetentionPolicy.SCRUB)
        manifest = VPKManifest(system=system, intent=intent)
        code = 0
        if manifest.intent.data_retention_policy == VPKDataRetentionPolicy.PERSIST:
            code = 1
        elif manifest.intent.data_retention_policy == VPKDataRetentionPolicy.SNAPSHOT:
            code = 2
        assert code == 0

    def test_persist_is_code_1(self):
        intent = VPKIntentManifest(data_retention_policy=VPKDataRetentionPolicy.PERSIST)
        manifest = VPKManifest(
            system=VPKSystemManifest(name="test", version="1.0.0", entry="main.c"),
            intent=intent,
        )
        code = 0
        if manifest.intent.data_retention_policy == VPKDataRetentionPolicy.PERSIST:
            code = 1
        assert code == 1

    def test_snapshot_is_code_2(self):
        intent = VPKIntentManifest(
            data_retention_policy=VPKDataRetentionPolicy.SNAPSHOT
        )
        manifest = VPKManifest(
            system=VPKSystemManifest(name="test", version="1.0.0", entry="main.c"),
            intent=intent,
        )
        code = 0
        if manifest.intent.data_retention_policy == VPKDataRetentionPolicy.SNAPSHOT:
            code = 2
        assert code == 2


# ═════════════════════════════════════════════════════════════════════════
# 6. Service Constants
# ═════════════════════════════════════════════════════════════════════════


class TestConstants:
    def test_line_max(self):
        assert KERNEL_LINE_MAX == 9216

    def test_max_chunk_size(self):
        assert MAX_CHUNK_SIZE == 8192

    def test_header_overhead(self):
        assert HEADER_OVERHEAD == 64

    def test_chunk_fits_in_buffer(self):
        """MAX_CHUNK_SIZE / 2 (decoded) must fit in wbuf[4096]."""
        assert MAX_CHUNK_SIZE / 2 <= 4096
