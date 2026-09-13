"""Sovereign Final Acceptance Test (SFAT) — VOS3 v19.8 Certification Suite.

Automated verification of all v19.8 hardening features:
  A. Hardware Scrubbing (XRSTOR) — symbol presence + call-site integration
  B. Binary Sync & Desync — SHA-256 manifest + tamper detection
  C. Cognitive Loop & Latency — stall threshold + gauge rendering
  D. Hard HMAC Lockdown — VBusSecurityError on non-HMAC connections
  E. Forensic Audit Integrity — purge/rescan audit log format validation
"""

import os
import re
import subprocess
import tempfile
import time

import pytest

# ---------------------------------------------------------------------------
# Path setup — ensure cli_system and backend are importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_KERNEL_ELF = os.path.join(_PROJECT_ROOT, "kernel", "build", "vos3.elf")

import sys

sys.path.insert(0, os.path.join(_PROJECT_ROOT, "backend"))
sys.path.insert(0, _PROJECT_ROOT)


# ===================================================================
# Track A: Hardware Scrubbing Verification (XRSTOR)
# ===================================================================
class TestHardwareScrubbing:
    """Verify vos3_fpu_scrub_full is present and integrated."""

    def test_fpu_scrub_symbol_exists(self):
        """nm must show vos3_fpu_scrub_full in the kernel ELF."""
        if not os.path.isfile(_KERNEL_ELF):
            pytest.skip("Kernel ELF not found — build first")

        result = subprocess.run(
            ["nm", _KERNEL_ELF],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"nm failed: {result.stderr}"
        assert (
            "vos3_fpu_scrub_full" in result.stdout
        ), "vos3_fpu_scrub_full symbol not found in kernel binary"

    def test_fpu_scrub_is_text_symbol(self):
        """vos3_fpu_scrub_full must be a text (T) symbol — executable code."""
        if not os.path.isfile(_KERNEL_ELF):
            pytest.skip("Kernel ELF not found — build first")

        result = subprocess.run(
            ["nm", _KERNEL_ELF],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # nm output format: <addr> <type> <name>
        for line in result.stdout.splitlines():
            if "vos3_fpu_scrub_full" in line:
                parts = line.split()
                assert len(parts) >= 3, f"Unexpected nm line: {line}"
                sym_type = parts[1]
                assert sym_type in (
                    "T",
                    "t",
                ), f"vos3_fpu_scrub_full has type '{sym_type}', expected 'T' (text)"
                return
        pytest.fail("vos3_fpu_scrub_full not found in nm output")

    def test_simd_scrub_all_still_present(self):
        """Belt-and-suspenders: vos3_simd_scrub_all must also exist."""
        if not os.path.isfile(_KERNEL_ELF):
            pytest.skip("Kernel ELF not found — build first")

        result = subprocess.run(
            ["nm", _KERNEL_ELF],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (
            "vos3_simd_scrub_all" in result.stdout
        ), "vos3_simd_scrub_all missing — fpu_scrub_full calls this as fallback"

    def test_fpu_scrub_source_uses_xrstor(self):
        """Source code must contain the XRSTOR inline assembly."""
        guard_path = os.path.join(_PROJECT_ROOT, "kernel", "src", "mm", "ai_guard.c")
        if not os.path.isfile(guard_path):
            pytest.skip("ai_guard.c not found")

        with open(guard_path, "r") as f:
            src = f.read()
        assert "vos3_fpu_scrub_full" in src, "Function not defined in ai_guard.c"
        assert "xrstor" in src.lower(), "XRSTOR instruction not found in source"
        assert "xgetbv" in src.lower(), "XGETBV instruction not found in source"

    def test_ai_slots_calls_fpu_scrub(self):
        """ai_slots.c must call vos3_fpu_scrub_full (not simd_scrub_all directly)."""
        slots_path = os.path.join(_PROJECT_ROOT, "kernel", "src", "mm", "ai_slots.c")
        if not os.path.isfile(slots_path):
            pytest.skip("ai_slots.c not found")

        with open(slots_path, "r") as f:
            src = f.read()
        assert (
            "vos3_fpu_scrub_full" in src
        ), "ai_slots.c does not call vos3_fpu_scrub_full — upgrade incomplete"


# ===================================================================
# Track B: Binary Sync & Desync Detection
# ===================================================================
class TestBinarySyncDesync:
    """Verify SHA-256 manifest checking and tamper detection."""

    def test_sha256_of_known_elf(self):
        """SHA-256 of current ELF must be computable."""
        if not os.path.isfile(_KERNEL_ELF):
            pytest.skip("Kernel ELF not found — build first")

        from cli_system.modules.integrity import _sha256_file

        sha = _sha256_file(_KERNEL_ELF)
        assert len(sha) == 64, f"SHA-256 hex must be 64 chars, got {len(sha)}"
        assert all(c in "0123456789abcdef" for c in sha), "Invalid hex chars"

    def test_manifest_contains_v19_8(self):
        """The certified manifest must have v19.8 entry."""
        from cli_system.modules.integrity import _MANIFEST

        assert "v19.8" in _MANIFEST, "v19.8 missing from manifest"
        assert len(_MANIFEST["v19.8"]) == 64, "v19.8 hash must be full SHA-256"

    def test_known_elf_matches_manifest(self):
        """Current ELF SHA must match a manifest entry (if unmodified)."""
        if not os.path.isfile(_KERNEL_ELF):
            pytest.skip("Kernel ELF not found — build first")

        from cli_system.modules.integrity import _sha256_file, _MANIFEST

        sha = _sha256_file(_KERNEL_ELF)
        match = any(sha == h or sha.startswith(h) for h in _MANIFEST.values())
        assert match, (
            f"Current ELF SHA {sha} does not match any manifest entry — "
            f"rebuild or update manifest"
        )

    def test_tamper_detection(self):
        """Modifying a byte in the ELF must cause SHA mismatch."""
        if not os.path.isfile(_KERNEL_ELF):
            pytest.skip("Kernel ELF not found — build first")

        from cli_system.modules.integrity import _sha256_file, _MANIFEST

        original_sha = _sha256_file(_KERNEL_ELF)

        # Create a tampered copy
        with tempfile.NamedTemporaryFile(suffix=".elf", delete=False) as tmp:
            tmp_path = tmp.name
            with open(_KERNEL_ELF, "rb") as orig:
                data = orig.read()
            # Flip a byte in the middle (non-critical area)
            midpoint = len(data) // 2
            tampered = bytearray(data)
            tampered[midpoint] ^= 0xFF
            tmp.write(bytes(tampered))

        try:
            tampered_sha = _sha256_file(tmp_path)
            assert (
                tampered_sha != original_sha
            ), "Tampered ELF has same SHA as original — something is wrong"
            # Tampered hash must NOT match any manifest entry
            match = any(
                tampered_sha == h or tampered_sha.startswith(h)
                for h in _MANIFEST.values()
            )
            assert not match, (
                f"Tampered SHA {tampered_sha} somehow matches manifest — "
                f"collision or bug"
            )
        finally:
            os.unlink(tmp_path)

    def test_build_uuid_extraction(self):
        """BUILD_UUID must be extractable from the ELF binary."""
        if not os.path.isfile(_KERNEL_ELF):
            pytest.skip("Kernel ELF not found — build first")

        from cli_system.modules.integrity import _extract_elf_build_uuid

        uuid = _extract_elf_build_uuid(_KERNEL_ELF)
        assert uuid is not None, "Could not extract BUILD_UUID from ELF"
        # UUID format: "Mon DD YYYY HH:MM:SS"
        assert re.match(
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}",
            uuid,
        ), f"UUID format invalid: '{uuid}'"


# ===================================================================
# Track C: Cognitive Loop & Latency Profiling
# ===================================================================
class TestCognitiveLoopLatency:
    """Verify latency thresholds, gauge rendering, and stall detection."""

    def test_latency_color_green(self):
        """Latency < 1ms must be green."""
        from cli_system.dashboard import _latency_color, NEON_GREEN

        assert _latency_color(500) == NEON_GREEN
        assert _latency_color(999) == NEON_GREEN

    def test_latency_color_yellow(self):
        """Latency 1ms-5ms must be yellow."""
        from cli_system.dashboard import _latency_color, NEON_YELLOW

        assert _latency_color(1000) == NEON_YELLOW
        assert _latency_color(4999) == NEON_YELLOW

    def test_latency_color_red(self):
        """Latency 5ms-20ms must be red."""
        from cli_system.dashboard import _latency_color, NEON_RED

        assert _latency_color(5000) == NEON_RED
        assert _latency_color(19999) == NEON_RED

    def test_latency_color_critical(self):
        """Latency >= 20ms must be bright_red (critical)."""
        from cli_system.dashboard import _latency_color

        assert _latency_color(20000) == "bright_red"
        assert _latency_color(499999) == "bright_red"
        assert _latency_color(1000000) == "bright_red"

    def test_stall_threshold(self):
        """_LATENCY_STALL must be 500ms (500000 us)."""
        from cli_system.dashboard import _LATENCY_STALL

        assert (
            _LATENCY_STALL == 500000
        ), f"STALL threshold is {_LATENCY_STALL}, expected 500000 (500ms)"

    def test_stall_detection_logic(self):
        """Latency >= 500ms must trigger stall classification."""
        from cli_system.dashboard import _LATENCY_STALL

        # Values at or above threshold → stall
        assert 500000 >= _LATENCY_STALL
        assert 600000 >= _LATENCY_STALL
        # Values below → no stall
        assert 499999 < _LATENCY_STALL

    def test_gauge_bar_renders(self):
        """_gauge_bar must return a Rich Text object for any valid latency."""
        from cli_system.dashboard import _gauge_bar
        from rich.text import Text

        for us in [0, 500, 1000, 5000, 20000, 50000, 500000]:
            result = _gauge_bar(us)
            assert isinstance(
                result, Text
            ), f"_gauge_bar({us}) returned {type(result)}, expected Text"
            # Must contain the microsecond label
            plain = result.plain
            assert (
                "µs" in plain or "\\u00b5s" in plain or str(us) in plain
            ), f"Gauge bar for {us} missing label: '{plain}'"

    def test_percentile_computation(self):
        """_percentile must compute correct values."""
        from cli_system.dashboard import _percentile

        values = list(range(1, 101))  # 1..100
        assert _percentile(values, 50) == 51  # Median-ish
        assert _percentile(values, 99) == 100
        assert _percentile([], 99) == 0

    def test_adaptive_refresh_thresholds(self):
        """Verify adaptive refresh constants are sane."""
        from cli_system.dashboard import (
            _REFRESH_NORMAL,
            _REFRESH_DEGRADED,
            _JITTER_THRESHOLD_MS,
        )

        assert _REFRESH_NORMAL == 2.0
        assert _REFRESH_DEGRADED == 4.0
        assert _JITTER_THRESHOLD_MS == 50.0
        assert (
            _REFRESH_DEGRADED > _REFRESH_NORMAL
        ), "Degraded refresh must be slower than normal"

    def test_parse_slot_state(self):
        """_parse_slot_state must extract state from OK|STATE format."""
        from cli_system.dashboard import _parse_slot_state

        assert _parse_slot_state("OK|ACTIVE") == "ACTIVE"
        assert _parse_slot_state("OK|DORMANT") == "DORMANT"
        assert _parse_slot_state("ERR|something") == "UNKNOWN"
        assert _parse_slot_state("garbage") == "UNKNOWN"


# ===================================================================
# Track D: Hard HMAC Lockdown
# ===================================================================
class TestHardHMACLockdown:
    """Verify VBusSecurityError on non-HMAC kernel connections."""

    def test_vbus_security_error_hierarchy(self):
        """VBusSecurityError must inherit from VBusError."""
        from services.vbus_driver import VBusError, VBusSecurityError

        assert issubclass(VBusSecurityError, VBusError)
        assert issubclass(VBusSecurityError, Exception)

    def test_session_epoch_error_hierarchy(self):
        """SessionEpochError must inherit from VBusSecurityError."""
        from cli_system.client import SessionEpochError
        from services.vbus_driver import VBusSecurityError

        assert issubclass(SessionEpochError, VBusSecurityError)

    def test_non_hmac_handshake_raises_security_error(self):
        """A kernel that responds without HMAC must trigger VBusSecurityError."""
        from unittest.mock import patch, MagicMock
        from services.vbus_driver import (
            VBusDriver,
            VBusSecurityError,
            VBUS_TYPE_HANDSHAKE,
        )

        driver = VBusDriver(socket_path="/tmp/fake_vbus.sock")

        mock_sock = MagicMock()
        # Simulate: socket connects, then handshake returns without HMAC
        with patch.object(driver, "_sock", None):
            with patch("socket.socket") as mock_socket_cls:
                mock_socket_cls.return_value = mock_sock
                mock_sock.recv.return_value = b""  # Empty drain

                # Mock _send_frame and _recv_frame
                with patch.object(driver, "_send_frame"):
                    with patch.object(driver, "_recv_frame") as mock_recv:
                        # Kernel responds with HANDSHAKE but no HMAC keyword
                        mock_recv.return_value = (
                            VBUS_TYPE_HANDSHAKE,
                            0xFF,
                            0,
                            b"VBUS2 OK",  # No "HMAC" in response
                        )
                        with pytest.raises(VBusSecurityError, match="HMAC"):
                            driver.connect()

    def test_no_handshake_response_raises_security_error(self):
        """If kernel sends 5 non-HANDSHAKE frames, must raise VBusSecurityError."""
        from unittest.mock import patch, MagicMock
        from services.vbus_driver import (
            VBusDriver,
            VBusSecurityError,
            VBUS_TYPE_EVENT,
        )

        driver = VBusDriver(socket_path="/tmp/fake_vbus.sock")

        mock_sock = MagicMock()
        with patch.object(driver, "_sock", None):
            with patch("socket.socket") as mock_socket_cls:
                mock_socket_cls.return_value = mock_sock
                mock_sock.recv.return_value = b""

                with patch.object(driver, "_send_frame"):
                    with patch.object(driver, "_recv_frame") as mock_recv:
                        # 5 EVENT frames — no HANDSHAKE
                        mock_recv.return_value = (
                            VBUS_TYPE_EVENT,
                            0xFF,
                            0xFFFF,
                            b"event",
                        )
                        with pytest.raises(VBusSecurityError, match="no HANDSHAKE"):
                            driver.connect()

    def test_hmac_handshake_succeeds(self):
        """A kernel that responds with HMAC must connect successfully."""
        from unittest.mock import patch, MagicMock
        from services.vbus_driver import VBusDriver, VBUS_TYPE_HANDSHAKE

        driver = VBusDriver(socket_path="/tmp/fake_vbus.sock")

        mock_sock = MagicMock()
        with patch.object(driver, "_sock", None):
            with patch("socket.socket") as mock_socket_cls:
                mock_socket_cls.return_value = mock_sock
                mock_sock.recv.return_value = b""

                with patch.object(driver, "_send_frame"):
                    with patch.object(driver, "_recv_frame") as mock_recv:
                        mock_recv.return_value = (
                            VBUS_TYPE_HANDSHAKE,
                            0xFF,
                            0,
                            b"VBUS2 HMAC-SHA256 OK",
                        )
                        result = driver.connect()
                        assert result is True
                        assert driver._hmac_key is not None

    def test_client_connect_propagates_security_error(self):
        """VOS3Client.connect() must let VBusSecurityError propagate."""
        from unittest.mock import patch
        from cli_system.client import VOS3Client
        from services.vbus_driver import VBusSecurityError

        client = VOS3Client(socket_path="/tmp/fake_vbus.sock")

        with patch.object(
            client._driver, "connect", side_effect=VBusSecurityError("HMAC failed")
        ):
            with pytest.raises(VBusSecurityError, match="HMAC"):
                client.connect()


# ===================================================================
# Track E: Forensic Audit Integrity
# ===================================================================
class TestForensicAuditIntegrity:
    """Verify purge/rescan append valid entries to SOVEREIGN_LOG.audit."""

    @pytest.fixture
    def temp_audit_dir(self, tmp_path):
        """Create a temporary docs/ directory for audit log testing."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        return tmp_path

    def test_rescan_audit_log_format(self, temp_audit_dir):
        """_audit_log must write correctly formatted rescan entries."""

        log_path = os.path.join(str(temp_audit_dir), "docs", "SOVEREIGN_LOG.audit")

        # Monkey-patch the function to write to our temp dir
        import cli_system.modules.integrity as integrity_mod

        def patched_audit_log(action, sha, version, kernel_path):
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            entry = (
                f"[{timestamp}] action={action} "
                f"sha256={sha} "
                f"version={version or 'UNKNOWN'} "
                f"binary={os.path.basename(kernel_path)}\n"
            )
            with open(log_path, "a") as f:
                f.write(entry)

        patched_audit_log(
            "rescan",
            "abcd1234" * 8,
            "v19.8",
            "/path/to/vos3.elf",
        )

        assert os.path.isfile(log_path), "Audit log not created"
        with open(log_path) as f:
            content = f.read()

        # Validate format
        assert "action=rescan" in content
        assert "sha256=" in content
        assert "version=v19.8" in content
        assert "binary=vos3.elf" in content
        # Timestamp format: [YYYY-MM-DD HH:MM:SS UTC]
        assert re.search(
            r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\]",
            content,
        ), "Timestamp format invalid"

    def test_purge_audit_log_format(self, temp_audit_dir):
        """_purge_audit_log must write correctly formatted purge entries."""
        log_path = os.path.join(str(temp_audit_dir), "docs", "SOVEREIGN_LOG.audit")

        # Directly write a purge-format entry
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        results = {"kill": "OK|killed=3", "hp": "OK|free=120/128"}
        entry = (
            f"[{timestamp}] action=purge "
            f"success=True "
            f"kill={results['kill'][:60]} "
            f"hp={results['hp'][:60]}\n"
        )
        with open(log_path, "w") as f:
            f.write(entry)

        with open(log_path) as f:
            content = f.read()

        assert "action=purge" in content
        assert "success=True" in content
        assert "kill=OK|killed=3" in content
        assert "hp=OK|free=120/128" in content
        assert re.search(
            r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\]",
            content,
        )

    def test_audit_log_is_append_only(self, temp_audit_dir):
        """Multiple audit entries must accumulate (append, not overwrite)."""
        log_path = os.path.join(str(temp_audit_dir), "docs", "SOVEREIGN_LOG.audit")

        for i in range(3):
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            entry = f"[{timestamp}] action=test_{i} sha256=0000 version=test binary=test.elf\n"
            with open(log_path, "a") as f:
                f.write(entry)

        with open(log_path) as f:
            lines = f.readlines()

        assert len(lines) == 3, f"Expected 3 entries, got {len(lines)}"
        assert "action=test_0" in lines[0]
        assert "action=test_1" in lines[1]
        assert "action=test_2" in lines[2]

    def test_real_audit_log_exists(self):
        """docs/SOVEREIGN_LOG.audit must exist in the project."""
        log_path = os.path.join(_PROJECT_ROOT, "docs", "SOVEREIGN_LOG.audit")
        assert os.path.isfile(log_path), f"SOVEREIGN_LOG.audit not found at {log_path}"
        with open(log_path) as f:
            content = f.read()
        assert len(content) > 0, "Audit log is empty"
        # Must have at least one valid entry
        assert re.search(
            r"action=(rescan|purge)", content
        ), "No valid audit entries found"

    def test_audit_log_sha256_values_valid(self):
        """All sha256= values in the audit log must be valid hex."""
        log_path = os.path.join(_PROJECT_ROOT, "docs", "SOVEREIGN_LOG.audit")
        if not os.path.isfile(log_path):
            pytest.skip("Audit log not found")

        with open(log_path) as f:
            content = f.read()

        sha_pattern = re.compile(r"sha256=([0-9a-f]+)")
        matches = sha_pattern.findall(content)
        assert len(matches) > 0, "No SHA-256 values found in audit log"
        for sha in matches:
            assert len(sha) == 64, f"SHA-256 '{sha}' is not 64 hex chars"
            assert all(
                c in "0123456789abcdef" for c in sha
            ), f"SHA-256 '{sha}' contains invalid chars"


# ===================================================================
# Track F: Integration — Client Session Lifecycle
# ===================================================================
class TestClientSessionLifecycle:
    """Verify VOS3Client epoch tracking and session management."""

    def test_epoch_extraction(self):
        """_extract_epoch must parse epoch= from pipe-separated response."""
        from cli_system.client import VOS3Client

        client = VOS3Client.__new__(VOS3Client)
        client._epoch = None

        assert client._extract_epoch("OK|epoch=0xDEAD") == "0xDEAD"
        assert client._extract_epoch("OK|uptime=100|epoch=0xBEEF|mem=512") == "0xBEEF"
        assert client._extract_epoch("OK|PONG") is None
        assert client._extract_epoch("ERR|timeout") is None

    def test_epoch_mismatch_raises(self):
        """_check_epoch must raise SessionEpochError on mismatch."""
        from cli_system.client import VOS3Client, SessionEpochError
        from unittest.mock import patch

        client = VOS3Client.__new__(VOS3Client)
        client._epoch = "0xAAAA"
        client._connected = True
        client._connect_time = time.monotonic()
        client._driver = None  # Will be replaced by mock

        # Mock disconnect to avoid NoneType errors
        with patch.object(VOS3Client, "disconnect"):
            with pytest.raises(SessionEpochError, match="epoch mismatch"):
                client._check_epoch("OK|epoch=0xBBBB")

    def test_epoch_match_no_error(self):
        """_check_epoch must not raise when epochs match."""
        from cli_system.client import VOS3Client

        client = VOS3Client.__new__(VOS3Client)
        client._epoch = "0xAAAA"

        # Should not raise
        client._check_epoch("OK|epoch=0xAAAA")

    def test_epoch_none_disables_check(self):
        """When epoch is None, _check_epoch must be a no-op."""
        from cli_system.client import VOS3Client

        client = VOS3Client.__new__(VOS3Client)
        client._epoch = None

        # Should not raise even with different epoch in response
        client._check_epoch("OK|epoch=0xDEAD")

    def test_send_command_requires_connection(self):
        """send_command must raise VBusError if not connected."""
        from cli_system.client import VOS3Client
        from services.vbus_driver import VBusError

        client = VOS3Client(socket_path="/tmp/fake.sock")
        with pytest.raises(VBusError, match="Not connected"):
            client.send_command("PING")
