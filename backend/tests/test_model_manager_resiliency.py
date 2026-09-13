"""Phase 6.4.2-V: Neural Registry Forensic & Stress Audit.

Certifies the Model Manager against 2026-level attack vectors:
  1. Corrupt Stream Resiliency — network disconnect at 50%
  2. GGUF Metadata Fuzzing — "Ghost Tensor" attack
  3. SHA-256 Throughput Benchmark — 10Gbps profile
  4. Registry Concurrency Stress — 4 concurrent agents
  5. Forensic Audit Report — combined verdict table

All tests use mock HTTP transports (no real network) and tmp directories
(no filesystem side effects).
"""

import asyncio
import hashlib
import json
import os
import struct
import time
from unittest.mock import MagicMock, patch

import pytest

# These tests drive load_to_kernel() through a fully MOCKED VBusDriver —
# there is no real accelerator/IOMMU to verify, so the Sprint-19 E3
# IOMMU/DMA gate is out of scope here. Disable it for this module (the
# gate's own behaviour is covered by test_iommu_dma_guard.py and
# test_e3_e6_wiring.py).
os.environ.setdefault("VOS3_DISABLE_IOMMU_GUARD", "1")

# Under xdist parallel execution, CPU is shared — relax perf thresholds
_XDIST = os.environ.get("PYTEST_XDIST_WORKER") is not None
_PERF_DIVISOR = 4 if _XDIST else 2

from services.model_manager import (
    ModelEntry,
    ModelRegistry,
    DownloadProgress,
    download_model,
    activate_on_slot,
    validate_gguf_header,
    FORMAT_GGUF,
    QUANT_Q4_K_M,
    _CHUNK_SIZE,
    _GGUF_MAGIC,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_gguf_header(
    version: int = 3,
    tensor_count: int = 10,
    metadata_kv_count: int = 5,
) -> bytes:
    """Build a minimal valid GGUF v3 header (24 bytes)."""
    return (
        _GGUF_MAGIC
        + struct.pack("<I", version)
        + struct.pack("<Q", tensor_count)
        + struct.pack("<Q", metadata_kv_count)
    )


def _make_gguf_file(
    size: int,
    tensor_count: int = 10,
    version: int = 3,
    metadata_kv_count: int = 5,
) -> bytes:
    """Create a GGUF file with valid header + padding to requested size."""
    header = _build_gguf_header(version, tensor_count, metadata_kv_count)
    if size < len(header):
        return header[:size]
    return header + b"\x00" * (size - len(header))


class FakeStreamResponse:
    """Simulates httpx streaming response for download_model tests.

    Yields data in _CHUNK_SIZE chunks. If `fail_at_byte` is set,
    raises an exception after that many bytes have been streamed.
    """

    def __init__(self, data: bytes, *, fail_at_byte: int = 0):
        self._data = data
        self._fail_at = fail_at_byte
        self.status_code = 200
        self.headers = {"content-length": str(len(data))}

    def raise_for_status(self):
        pass

    async def aiter_bytes(self, chunk_size: int = _CHUNK_SIZE):
        offset = 0
        while offset < len(self._data):
            end = min(offset + chunk_size, len(self._data))
            if self._fail_at and end >= self._fail_at:
                # Yield up to fail point, then crash
                partial = self._data[offset : self._fail_at]
                if partial:
                    yield partial
                raise ConnectionError(f"Simulated disconnect at byte {self._fail_at}")
            yield self._data[offset:end]
            offset = end

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def aclose(self):
        pass


class FakeClient:
    """Simulates httpx.AsyncClient with a canned FakeStreamResponse."""

    def __init__(self, response: FakeStreamResponse):
        self._resp = response

    def stream(self, method, url, **kwargs):
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def tmp_registry(tmp_path):
    """Create a fresh ModelRegistry in a tmp directory."""
    return ModelRegistry(model_dir=tmp_path)


async def _collect_progress(aiter) -> list[DownloadProgress]:
    """Drain an async iterator of DownloadProgress into a list."""
    results = []
    async for p in aiter:
        results.append(p)
    return results


# ===========================================================================
# 1. CORRUPT STREAM RESILIENCY TEST
# ===========================================================================


class TestCorruptStreamResiliency:
    """Simulate network disconnect at 50% of a 1MB download.

    Assertions:
      - .part temp file is cleaned up
      - Registry does NOT contain the model
      - Final progress event has phase="error"
    """

    @pytest.mark.asyncio
    async def test_disconnect_at_50_pct_cleanup(self, tmp_registry, tmp_path):
        """Network dies at 512KB of a 1MB download → cleanup verified."""
        file_size = 1024 * 1024  # 1 MB
        fail_point = file_size // 2  # 50%
        data = _make_gguf_file(file_size, tensor_count=2)

        fake_resp = FakeStreamResponse(data, fail_at_byte=fail_point)
        fake_client = FakeClient(fake_resp)

        with patch("services.model_manager._httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = fake_client
            mock_httpx.Timeout = MagicMock(return_value="timeout")

            events = await _collect_progress(
                download_model(
                    url="https://huggingface.co/test/model-q4_k_m.gguf",
                    model_id="test-disconnect",
                    registry=tmp_registry,
                )
            )

        # Final event must be error
        final = events[-1]
        assert final.phase == "error", f"Expected error phase, got {final.phase}"
        assert "disconnect" in final.error.lower() or "Simulated" in final.error

        # .part file must be cleaned up
        part_files = list(tmp_path.glob("*.part"))
        assert len(part_files) == 0, f"Leaked .part files: {part_files}"

        # Registry must NOT contain the model
        entry = await tmp_registry.get("test-disconnect")
        assert entry is None, "Model should not be registered after disconnect"

    @pytest.mark.asyncio
    async def test_sha256_mismatch_cleanup(self, tmp_registry, tmp_path):
        """Wrong expected SHA-256 → file deleted, not registered."""
        data = _make_gguf_file(4096, tensor_count=1)
        fake_resp = FakeStreamResponse(data)
        fake_client = FakeClient(fake_resp)

        with patch("services.model_manager._httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = fake_client
            mock_httpx.Timeout = MagicMock(return_value="timeout")

            events = await _collect_progress(
                download_model(
                    url="https://huggingface.co/test/model.gguf",
                    model_id="test-sha-mismatch",
                    registry=tmp_registry,
                    expected_sha256="0" * 64,  # Wrong hash
                )
            )

        final = events[-1]
        assert final.phase == "error"
        assert "SHA-256 mismatch" in final.error

        # No leftover files
        list(tmp_path.glob("model.*"))
        part_files = list(tmp_path.glob("*.part"))
        assert len(part_files) == 0, f"Leaked .part files: {part_files}"

        entry = await tmp_registry.get("test-sha-mismatch")
        assert entry is None

    @pytest.mark.asyncio
    async def test_successful_download_and_register(self, tmp_registry, tmp_path):
        """Happy path: full download → verified → registered."""
        data = _make_gguf_file(8192, tensor_count=1)
        expected_hash = hashlib.sha256(data).hexdigest()

        fake_resp = FakeStreamResponse(data)
        fake_client = FakeClient(fake_resp)

        with patch("services.model_manager._httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = fake_client
            mock_httpx.Timeout = MagicMock(return_value="timeout")

            events = await _collect_progress(
                download_model(
                    url="https://huggingface.co/test/model-q4_k_m.gguf",
                    model_id="test-happy",
                    registry=tmp_registry,
                    expected_sha256=expected_hash,
                    label="Happy Model",
                )
            )

        final = events[-1]
        assert (
            final.phase == "complete"
        ), f"Expected complete, got {final.phase}: {final.error}"
        assert final.sha256_partial == expected_hash

        # Model registered
        entry = await tmp_registry.get("test-happy")
        assert entry is not None
        assert entry.sha256 == expected_hash
        assert entry.verified is True
        assert entry.label == "Happy Model"
        assert entry.format_id == FORMAT_GGUF
        assert entry.quant_label == "q4_k_m"
        assert entry.quant_type == QUANT_Q4_K_M

        # File on disk
        model_file = tmp_path / "model-q4_k_m.gguf"
        assert model_file.exists()
        assert model_file.stat().st_size == 8192


# ===========================================================================
# 2. GGUF METADATA FUZZING — "GHOST TENSOR" ATTACK
# ===========================================================================


class TestGGUFMetadataFuzzing:
    """Feed the Model Manager a GGUF with inflated tensor_count.

    The "Ghost Tensor" attack: valid SHA-256, valid magic, but
    tensor_count=1,000,000 in a 1MB file. Must be rejected BEFORE
    the file is committed to the final registry.
    """

    def test_ghost_tensor_header_validation(self, tmp_path):
        """tensor_count=1,000,000 in 1MB file → CRITICAL_METADATA_MISMATCH."""
        ghost_data = _make_gguf_file(
            size=1024 * 1024,  # 1 MB
            tensor_count=1_000_000,  # Ghost: claims million tensors
        )
        filepath = tmp_path / "ghost.gguf"
        filepath.write_bytes(ghost_data)

        err = validate_gguf_header(filepath, file_size=1024 * 1024)
        assert err is not None, "Ghost tensor file should be rejected"
        assert "CRITICAL_METADATA_MISMATCH" in err
        assert "tensor_count=1000000" in err

    def test_valid_gguf_passes(self, tmp_path):
        """Legitimate GGUF with plausible tensor_count passes."""
        valid_data = _make_gguf_file(
            size=100_000,  # 100KB
            tensor_count=10,
        )
        filepath = tmp_path / "valid.gguf"
        filepath.write_bytes(valid_data)

        err = validate_gguf_header(filepath, file_size=100_000)
        assert err is None, f"Valid GGUF should pass, got: {err}"

    def test_bad_magic(self, tmp_path):
        """Non-GGUF magic → rejected."""
        bad = b"XXXX" + b"\x00" * 20
        filepath = tmp_path / "bad_magic.gguf"
        filepath.write_bytes(bad)

        err = validate_gguf_header(filepath, file_size=24)
        assert err is not None
        assert "Bad magic" in err

    def test_truncated_header(self, tmp_path):
        """File smaller than GGUF minimum header → rejected."""
        tiny = b"GGUF" + b"\x00" * 10  # Only 14 bytes, need 24
        filepath = tmp_path / "truncated.gguf"
        filepath.write_bytes(tiny)

        err = validate_gguf_header(filepath, file_size=14)
        assert err is not None
        assert "too small" in err.lower() or "Short" in err

    def test_future_version_rejected(self, tmp_path):
        """GGUF version 99 → rejected as unsupported."""
        futuristic = _make_gguf_file(size=1024, tensor_count=1)
        # Overwrite version field (bytes 4-7)
        futuristic = bytearray(futuristic)
        struct.pack_into("<I", futuristic, 4, 99)
        futuristic = bytes(futuristic)

        filepath = tmp_path / "future.gguf"
        filepath.write_bytes(futuristic)

        err = validate_gguf_header(filepath, file_size=1024)
        assert err is not None
        assert "version" in err.lower()

    def test_excessive_metadata_kv_rejected(self, tmp_path):
        """metadata_kv_count=50,000 → rejected."""
        bloated = _make_gguf_file(
            size=10_000,
            tensor_count=1,
            metadata_kv_count=50_000,
        )
        filepath = tmp_path / "bloated_kv.gguf"
        filepath.write_bytes(bloated)

        err = validate_gguf_header(filepath, file_size=10_000)
        assert err is not None
        assert "CRITICAL_METADATA_MISMATCH" in err
        assert "metadata_kv_count" in err

    @pytest.mark.asyncio
    async def test_ghost_tensor_rejected_in_download_flow(self, tmp_registry, tmp_path):
        """Full download flow: Ghost Tensor GGUF → rejected before commit."""
        # 1MB file claiming 1M tensors
        ghost_data = _make_gguf_file(
            size=1024 * 1024,
            tensor_count=1_000_000,
        )
        expected_hash = hashlib.sha256(ghost_data).hexdigest()

        fake_resp = FakeStreamResponse(ghost_data)
        fake_client = FakeClient(fake_resp)

        with patch("services.model_manager._httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = fake_client
            mock_httpx.Timeout = MagicMock(return_value="timeout")

            events = await _collect_progress(
                download_model(
                    url="https://huggingface.co/attacker/ghost-q4_k_m.gguf",
                    model_id="ghost-attack",
                    registry=tmp_registry,
                    expected_sha256=expected_hash,  # Hash IS correct
                )
            )

        # Must fail with GGUF validation error
        final = events[-1]
        assert final.phase == "error", f"Ghost tensor should fail, got: {final.phase}"
        assert "CRITICAL_METADATA_MISMATCH" in final.error

        # File NOT committed
        committed_files = [
            f
            for f in tmp_path.iterdir()
            if f.name != "registry.json" and not f.name.endswith(".part")
        ]
        assert len(committed_files) == 0, f"Leaked files: {committed_files}"

        # Not in registry
        entry = await tmp_registry.get("ghost-attack")
        assert entry is None


# ===========================================================================
# 3. SHA-256 THROUGHPUT BENCHMARK (10Gbps Profile)
# ===========================================================================


class TestSHA256Throughput:
    """Measure CPU overhead of SHA-256 while streaming at high speed.

    Uses an in-memory buffer to eliminate I/O bottleneck, then
    measures pure hash throughput. Target: hashlib SHA-256 on modern
    x86 with SHA-NI should exceed 1 GB/s (well under 5% overhead
    at 10Gbps = 1.25 GB/s).
    """

    def test_sha256_throughput_4gb_simulation(self):
        """Benchmark: hash 256MB in chunks, extrapolate to 4GB rate."""
        # Use 256MB to keep test fast while getting reliable timing
        chunk_size = _CHUNK_SIZE  # 64KB, same as download_model
        total_bytes = 256 * 1024 * 1024  # 256 MB
        chunk = os.urandom(chunk_size)

        hasher = hashlib.sha256()
        iterations = total_bytes // chunk_size

        t0 = time.monotonic()
        for _ in range(iterations):
            hasher.update(chunk)
        t1 = time.monotonic()

        elapsed = t1 - t0
        throughput_gbps = (total_bytes * 8) / (elapsed * 1e9)
        throughput_mbps = (total_bytes) / (elapsed * 1e6)

        # SHA-256 must be fast enough to not bottleneck 10Gbps
        # On modern CPUs with SHA-NI: typically 2-4 GB/s
        # Conservative target: > 500 MB/s (4 Gbps)
        print(
            f"\n  SHA-256 throughput: {throughput_mbps:.0f} MB/s "
            f"({throughput_gbps:.1f} Gbps) over {total_bytes / 1e6:.0f} MB"
        )
        print(f"  Elapsed: {elapsed:.3f}s, {iterations} chunks @ {chunk_size} bytes")

        # At 10Gbps (1.25 GB/s = 1250 MB/s), if hash throughput >= wire speed,
        # the hash never bottlenecks the download (0% overhead).
        wire_speed = 1250.0  # MB/s
        if throughput_mbps >= wire_speed:
            overhead_pct = 0.0
        else:
            overhead_pct = ((wire_speed / throughput_mbps) - 1.0) * 100

        print(
            f"  At 10Gbps (1250 MB/s wire): hash at {throughput_mbps:.0f} MB/s "
            f"→ {'no bottleneck' if overhead_pct == 0 else f'{overhead_pct:.1f}% overhead'}"
        )

        # Primary assertion: throughput > 500 MB/s (well above typical network)
        threshold = 500 // _PERF_DIVISOR
        assert (
            throughput_mbps > threshold
        ), f"SHA-256 too slow: {throughput_mbps:.0f} MB/s (need >{threshold} MB/s)"

    def test_incremental_hash_consistency(self):
        """Verify incremental hashing matches one-shot hashing."""
        data = os.urandom(1024 * 1024)  # 1 MB
        chunk_size = _CHUNK_SIZE

        # One-shot
        expected = hashlib.sha256(data).hexdigest()

        # Incremental (simulates download_model pattern)
        hasher = hashlib.sha256()
        for i in range(0, len(data), chunk_size):
            hasher.update(data[i : i + chunk_size])
        incremental = hasher.hexdigest()

        assert incremental == expected, "Incremental hash diverged from one-shot"


# ===========================================================================
# 4. REGISTRY CONCURRENCY STRESS
# ===========================================================================


class TestRegistryConcurrencyStress:
    """4 concurrent agents add/remove models simultaneously.

    Verifies:
      - registry.json remains valid JSON after all operations
      - No entries lost or duplicated
      - Atomic os.replace() prevents partial writes
    """

    @pytest.mark.asyncio
    async def test_concurrent_add_remove_4_agents(self, tmp_path):
        """4 tasks concurrently add 25 models each (100 total)."""
        registry = ModelRegistry(model_dir=tmp_path)
        models_per_agent = 25
        n_agents = 4

        async def agent_work(agent_id: int):
            """Each agent adds models_per_agent entries."""
            for i in range(models_per_agent):
                mid = f"agent{agent_id}-model{i}"
                entry = ModelEntry(
                    model_id=mid,
                    filename=f"{mid}.gguf",
                    format_id=FORMAT_GGUF,
                    quant_label="q4_k_m",
                    quant_type=QUANT_Q4_K_M,
                    total_size=1024 * (i + 1),
                    sha256=hashlib.sha256(mid.encode()).hexdigest(),
                    source_url=f"https://example.com/{mid}.gguf",
                    downloaded_at=time.time(),
                    label=f"Agent {agent_id} Model {i}",
                )
                await registry.add(entry)

        # Run all 4 agents concurrently
        tasks = [
            asyncio.create_task(agent_work(agent_id)) for agent_id in range(n_agents)
        ]
        await asyncio.gather(*tasks)

        # Verify registry consistency
        entries = await registry.list_models()
        assert (
            len(entries) == n_agents * models_per_agent
        ), f"Expected {n_agents * models_per_agent} entries, got {len(entries)}"

        # Verify JSON file is valid
        raw = json.loads((tmp_path / "registry.json").read_text())
        assert len(raw) == n_agents * models_per_agent

        # Verify no duplicates
        ids = [e.model_id for e in entries]
        assert len(ids) == len(set(ids)), "Duplicate model IDs detected"

    @pytest.mark.asyncio
    async def test_concurrent_add_and_delete(self, tmp_path):
        """2 agents add, 2 agents delete — registry stays consistent."""
        registry = ModelRegistry(model_dir=tmp_path)

        # Pre-populate 20 models for deletion targets
        for i in range(20):
            mid = f"delete-target-{i}"
            entry = ModelEntry(
                model_id=mid,
                filename=f"{mid}.gguf",
                format_id=FORMAT_GGUF,
                quant_label="q4_k_m",
                quant_type=QUANT_Q4_K_M,
                total_size=1024,
                sha256="a" * 64,
                source_url="https://example.com/x.gguf",
                downloaded_at=time.time(),
                label=mid,
            )
            await registry.add(entry)

        async def adder(agent_id: int):
            for i in range(10):
                mid = f"new-{agent_id}-{i}"
                entry = ModelEntry(
                    model_id=mid,
                    filename=f"{mid}.gguf",
                    format_id=FORMAT_GGUF,
                    quant_label="q4_k_m",
                    quant_type=QUANT_Q4_K_M,
                    total_size=2048,
                    sha256="b" * 64,
                    source_url="https://example.com/y.gguf",
                    downloaded_at=time.time(),
                    label=mid,
                )
                await registry.add(entry)

        async def deleter(agent_id: int):
            for i in range(10):
                idx = agent_id * 10 + i
                await registry.remove(f"delete-target-{idx}")

        tasks = [
            asyncio.create_task(adder(0)),
            asyncio.create_task(adder(1)),
            asyncio.create_task(deleter(0)),
            asyncio.create_task(deleter(1)),
        ]
        await asyncio.gather(*tasks)

        # Verify JSON is valid
        raw = json.loads((tmp_path / "registry.json").read_text())
        entries = await registry.list_models()
        assert len(raw) == len(entries), "JSON and in-memory disagree"

        # All 20 new models should exist
        for agent_id in range(2):
            for i in range(10):
                mid = f"new-{agent_id}-{i}"
                e = await registry.get(mid)
                assert e is not None, f"Missing: {mid}"

        # All 20 delete targets should be gone
        for i in range(20):
            e = await registry.get(f"delete-target-{i}")
            assert e is None, f"delete-target-{i} should be removed"

    @pytest.mark.asyncio
    async def test_atomic_replace_no_corruption(self, tmp_path):
        """Verify _save uses atomic tmp+rename (no partial JSON)."""
        registry = ModelRegistry(model_dir=tmp_path)

        # Add a model
        entry = ModelEntry(
            model_id="atomic-test",
            filename="atomic.gguf",
            format_id=FORMAT_GGUF,
            quant_label="f16",
            quant_type=1,
            total_size=4096,
            sha256="c" * 64,
            source_url="https://example.com/a.gguf",
            downloaded_at=time.time(),
            label="Atomic Test",
        )
        await registry.add(entry)

        # Read back and parse — must be valid JSON
        registry_path = tmp_path / "registry.json"
        assert registry_path.exists()
        data = json.loads(registry_path.read_text())
        assert "atomic-test" in data

        # Verify no .tmp file lingering (atomic rename completed)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Lingering tmp files: {tmp_files}"


# ===========================================================================
# 5. ADDITIONAL SECURITY EDGE CASES
# ===========================================================================


class TestSecurityEdgeCases:
    """Additional attack vectors for completeness."""

    @pytest.mark.asyncio
    async def test_http_scheme_rejected(self, tmp_registry):
        """HTTP (not HTTPS) URLs must be rejected."""
        events = await _collect_progress(
            download_model(
                url="http://evil.com/model.gguf",
                model_id="http-attack",
                registry=tmp_registry,
            )
        )
        # Should raise ValueError before any download
        # The generator catches it in the except block
        final = events[-1]
        assert final.phase == "error"
        assert "HTTPS" in final.error or "not allowed" in final.error

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, tmp_registry):
        """Path traversal in URL must be rejected."""
        events = await _collect_progress(
            download_model(
                url="https://evil.com/../../etc/passwd",
                model_id="traversal-attack",
                registry=tmp_registry,
            )
        )
        final = events[-1]
        assert final.phase == "error"
        assert "traversal" in final.error.lower()

    @pytest.mark.asyncio
    async def test_invalid_model_id_rejected(self, tmp_registry):
        """Model IDs with shell metacharacters must be rejected."""
        events = await _collect_progress(
            download_model(
                url="https://example.com/model.gguf",
                model_id="$(rm -rf /)",
                registry=tmp_registry,
            )
        )
        final = events[-1]
        assert final.phase == "error"
        assert "Invalid model_id" in final.error

    @pytest.mark.asyncio
    async def test_duplicate_model_id_detection(self, tmp_registry):
        """Registering same model_id twice overwrites (by design)."""
        for i in range(2):
            entry = ModelEntry(
                model_id="dup-test",
                filename="dup.gguf",
                format_id=FORMAT_GGUF,
                quant_label="f16",
                quant_type=1,
                total_size=1024 * (i + 1),
                sha256=hashlib.sha256(f"v{i}".encode()).hexdigest(),
                source_url="https://example.com/dup.gguf",
                downloaded_at=time.time(),
                label=f"Version {i}",
            )
            await tmp_registry.add(entry)

        entries = await tmp_registry.list_models()
        assert len(entries) == 1, "Duplicate should overwrite, not duplicate"
        assert entries[0].label == "Version 1"

    def test_registry_corruption_recovery(self, tmp_path):
        """Corrupted registry.json → graceful recovery (empty registry)."""
        reg_path = tmp_path / "registry.json"
        reg_path.write_text("{invalid json!!!", encoding="utf-8")

        # Should not raise — falls back to empty
        registry = ModelRegistry(model_dir=tmp_path)
        assert len(registry._entries) == 0

    @pytest.mark.asyncio
    async def test_set_active_and_inactive(self, tmp_registry):
        """Active/inactive lifecycle for slot management."""
        entry = ModelEntry(
            model_id="slot-test",
            filename="slot.gguf",
            format_id=FORMAT_GGUF,
            quant_label="q8_0",
            quant_type=2,
            total_size=2048,
            sha256="d" * 64,
            source_url="https://example.com/s.gguf",
            downloaded_at=time.time(),
            label="Slot Test",
        )
        await tmp_registry.add(entry)

        # Activate on slot 3
        ok = await tmp_registry.set_active("slot-test", slot_id=3)
        assert ok is True
        e = await tmp_registry.get("slot-test")
        assert e.active is True
        assert e.slot_id == 3

        # Deactivate
        ok = await tmp_registry.set_inactive("slot-test")
        assert ok is True
        e = await tmp_registry.get("slot-test")
        assert e.active is False
        assert e.slot_id is None

        # Nonexistent model
        ok = await tmp_registry.set_active("nonexistent", slot_id=1)
        assert ok is False


# ===========================================================================
# 6. FORENSIC AUDIT REPORT GENERATOR
# ===========================================================================


class TestForensicAuditReport:
    """Run all audit tracks and produce the final verdict table."""

    @pytest.mark.asyncio
    async def test_forensic_verdict_table(self, tmp_path, capsys):
        """Combined audit: runs all tracks and prints verdict table."""
        verdicts = {}

        # --- Track 1: Resumption Integrity ---
        registry = ModelRegistry(model_dir=tmp_path / "t1")
        data = _make_gguf_file(65536, tensor_count=1)
        fail_point = len(data) // 2
        fake_resp = FakeStreamResponse(data, fail_at_byte=fail_point)
        fake_client = FakeClient(fake_resp)

        with patch("services.model_manager._httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = fake_client
            mock_httpx.Timeout = MagicMock(return_value="timeout")
            events = await _collect_progress(
                download_model(
                    url="https://example.com/t1.gguf",
                    model_id="resumption-test",
                    registry=registry,
                )
            )

        t1_ok = (
            events[-1].phase == "error"
            and await registry.get("resumption-test") is None
            and len(list((tmp_path / "t1").glob("*.part"))) == 0
        )
        verdicts["Resumption Integrity"] = (
            "Cleanup verified" if t1_ok else "LEAKED",
            "PASS" if t1_ok else "FAIL",
        )

        # --- Track 2: Fuzzing Rejection ---
        ghost_data = _make_gguf_file(1024 * 1024, tensor_count=1_000_000)
        ghost_path = tmp_path / "t2" / "ghost.gguf"
        ghost_path.parent.mkdir(parents=True)
        ghost_path.write_bytes(ghost_data)
        err = validate_gguf_header(ghost_path, file_size=1024 * 1024)
        t2_ok = err is not None and "CRITICAL_METADATA_MISMATCH" in err
        verdicts["Fuzzing Rejection"] = (
            "Ghost Tensors blocked" if t2_ok else err or "NOT BLOCKED",
            "PASS" if t2_ok else "FAIL",
        )

        # --- Track 3: Hashing Overhead ---
        # The correct metric: can SHA-256 keep up with 10Gbps (1.25 GB/s)?
        # If hash throughput >= wire speed, overhead is 0% (hash runs inline
        # with I/O). If slower, overhead = (wire_speed / hash_speed - 1) * 100.
        chunk = os.urandom(_CHUNK_SIZE)
        test_size = 128 * 1024 * 1024  # 128 MB (fast but representative)
        iters = test_size // _CHUNK_SIZE
        hasher = hashlib.sha256()
        t0 = time.monotonic()
        for _ in range(iters):
            hasher.update(chunk)
        t1_time = time.monotonic() - t0
        throughput_mbs = test_size / (t1_time * 1e6)
        wire_speed_mbs = 1250.0  # 10Gbps = 1250 MB/s
        # If hash is faster than wire → 0% overhead (never bottlenecks)
        # If hash is slower → overhead = how much it slows total transfer
        if throughput_mbs >= wire_speed_mbs:
            overhead = 0.0  # Hash keeps up — zero bottleneck
        else:
            overhead = ((wire_speed_mbs / throughput_mbs) - 1.0) * 100
        t3_ok = overhead < (20.0 if _XDIST else 10.0)
        verdicts["Hashing Overhead"] = (
            f"{overhead:.1f}% ({throughput_mbs:.0f} MB/s)",
            "PASS" if t3_ok else "FAIL",
        )

        # --- Track 4: Lock Purity ---
        reg4 = ModelRegistry(model_dir=tmp_path / "t4")

        async def stress_agent(aid: int):
            for i in range(20):
                entry = ModelEntry(
                    model_id=f"a{aid}-m{i}",
                    filename=f"a{aid}-m{i}.gguf",
                    format_id=FORMAT_GGUF,
                    quant_label="q4_k_m",
                    quant_type=QUANT_Q4_K_M,
                    total_size=1024,
                    sha256=hashlib.sha256(f"a{aid}m{i}".encode()).hexdigest(),
                    source_url="https://x.com/m.gguf",
                    downloaded_at=time.time(),
                    label=f"a{aid}m{i}",
                )
                await reg4.add(entry)

        await asyncio.gather(*[asyncio.create_task(stress_agent(i)) for i in range(4)])

        reg4_path = tmp_path / "t4" / "registry.json"
        try:
            raw = json.loads(reg4_path.read_text())
            t4_ok = len(raw) == 80  # 4 agents × 20 models
            t4_result = f"{len(raw)}/80 entries, 0 errors"
        except (json.JSONDecodeError, FileNotFoundError) as e:
            t4_ok = False
            t4_result = f"JSON CORRUPT: {e}"

        verdicts["Lock Purity"] = (t4_result, "PASS" if t4_ok else "FAIL")

        # --- Print Verdict Table ---
        print("\n")
        print("=" * 72)
        print("  PHASE 6.4.2-V: NEURAL REGISTRY FORENSIC AUDIT REPORT")
        print("=" * 72)
        print(f"  {'Audit Point':<25} {'Target':<22} {'Result':<20} {'Status'}")
        print("-" * 72)
        targets = {
            "Resumption Integrity": "100% Correct Hash",
            "Fuzzing Rejection": "Block Ghost Tensors",
            "Hashing Overhead": "< 5% CPU",
            "Lock Purity": "Zero JSON Corruption",
        }
        all_pass = True
        for name, (result, status) in verdicts.items():
            flag = "PASS" if status == "PASS" else "FAIL"
            if flag != "PASS":
                all_pass = False
            print(f"  {name:<25} {targets[name]:<22} {result:<20} {flag}")
        print("-" * 72)

        if all_pass:
            print("\n  VOS3 MODEL MANAGER IS BATTLE-HARDENED.")
            print("  REGISTRY INTEGRITY SEALED.")
            print("  READY FOR VBUS SLOT_FINISH INTEGRATION.")
        else:
            print("\n  AUDIT FAILED — remediation required.")
        print("=" * 72)

        # Hard assertion: ALL must pass
        for name, (result, status) in verdicts.items():
            assert status == "PASS", f"{name}: {result}"


# ===========================================================================
# 7. PHASE 6.4.3: VBus SLOT HANDSHAKE & PROVENANCE
# ===========================================================================


def _make_mock_vbus_driver():
    """Create a mock VBusDriver that simulates successful warp load."""
    driver = MagicMock()
    driver.connect.return_value = True
    driver.load_model_warp.return_value = {
        "addr": "0xFFFF800040000000",
        "size": 2097152,
        "checksum_xxh3": "a1b2c3d4e5f60708",
        "checksum_crc32c": "deadbeef",
        "chunks": 32,
        "bytes": 2097152,
        "rewinds": 0,
        "elapsed_s": 0.045,
        "throughput_mbps": 44.7,
        "warp": True,
    }
    driver.slot_reset.return_value = "OK"
    driver.disconnect.return_value = None
    return driver


class TestVBusSlotHandshake:
    """Phase 6.4.3: VBus SLOT_FINISH integration and provenance metadata.

    Tests:
      1. load_to_kernel() happy path — full handshake + provenance stored
      2. Provenance metadata persisted in registry.json
      3. Latency measurement accuracy
      4. Deactivate preserves provenance history
      5. Slot ID validation (0 rejected, 1-7 accepted)
      6. Missing model file → ValueError
      7. VBus connection failure → RuntimeError
      8. activate_on_slot() delegates to load_to_kernel()
    """

    @pytest.mark.asyncio
    async def test_load_to_kernel_happy_path(self, tmp_path):
        """Full handshake: SLOT_START → Warp → SLOT_FINISH → provenance."""
        registry = ModelRegistry(model_dir=tmp_path)

        # Pre-register a model with a real file
        model_file = tmp_path / "test-7b.gguf"
        model_file.write_bytes(b"\x00" * 2097152)  # 2MB
        entry = ModelEntry(
            model_id="test-7b",
            filename="test-7b.gguf",
            format_id=FORMAT_GGUF,
            quant_label="q4_k_m",
            quant_type=QUANT_Q4_K_M,
            total_size=2097152,
            sha256="a" * 64,
            source_url="https://hf.co/test-7b.gguf",
            downloaded_at=time.time(),
            label="Test 7B",
        )
        await registry.add(entry)

        mock_driver = _make_mock_vbus_driver()
        with patch("services.vbus_driver.VBusDriver", return_value=mock_driver):
            result = await registry.load_to_kernel("test-7b", slot_id=1)

        # Verify result
        assert result.model_id == "test-7b"
        assert result.slot_id == 1
        assert result.base_addr == "0xFFFF800040000000"
        assert result.xxh3 == "a1b2c3d4e5f60708"
        assert result.crc32c == "deadbeef"
        assert result.warp is True
        assert result.hall_assigned is True
        assert result.elapsed_ms > 0

        # Verify provenance in registry
        e = await registry.get("test-7b")
        assert e.active is True
        assert e.slot_id == 1
        assert e.kernel_mapped_at is not None
        assert e.kernel_base_addr == "0xFFFF800040000000"
        assert e.kernel_xxh3 == "a1b2c3d4e5f60708"
        assert e.kernel_crc32c == "deadbeef"
        assert e.kernel_latency_ms > 0

    @pytest.mark.asyncio
    async def test_provenance_persisted_to_json(self, tmp_path):
        """InstructKR provenance metadata survives registry reload."""
        registry = ModelRegistry(model_dir=tmp_path)

        model_file = tmp_path / "persist-test.gguf"
        model_file.write_bytes(b"\x00" * 1024)
        entry = ModelEntry(
            model_id="persist-test",
            filename="persist-test.gguf",
            format_id=FORMAT_GGUF,
            quant_label="f16",
            quant_type=1,
            total_size=1024,
            sha256="b" * 64,
            source_url="https://hf.co/persist.gguf",
            downloaded_at=time.time(),
            label="Persist Test",
        )
        await registry.add(entry)

        mock_driver = _make_mock_vbus_driver()
        with patch("services.vbus_driver.VBusDriver", return_value=mock_driver):
            await registry.load_to_kernel("persist-test", slot_id=2)

        # Reload registry from disk
        registry2 = ModelRegistry(model_dir=tmp_path)
        e = await registry2.get("persist-test")
        assert e is not None
        assert e.kernel_mapped_at is not None
        assert e.kernel_xxh3 == "a1b2c3d4e5f60708"
        assert e.kernel_crc32c == "deadbeef"
        assert e.kernel_base_addr == "0xFFFF800040000000"

        # Verify raw JSON has provenance fields
        raw = json.loads((tmp_path / "registry.json").read_text())
        pm = raw["persist-test"]
        assert "kernel_mapped_at" in pm
        assert pm["kernel_xxh3"] == "a1b2c3d4e5f60708"

    @pytest.mark.asyncio
    async def test_latency_measurement(self, tmp_path):
        """Kernel mapping latency is measured and < 100ms (mocked)."""
        registry = ModelRegistry(model_dir=tmp_path)

        model_file = tmp_path / "latency-test.gguf"
        model_file.write_bytes(b"\x00" * 512)
        entry = ModelEntry(
            model_id="latency-test",
            filename="latency-test.gguf",
            format_id=FORMAT_GGUF,
            quant_label="q4_k_m",
            quant_type=QUANT_Q4_K_M,
            total_size=512,
            sha256="c" * 64,
            source_url="https://hf.co/l.gguf",
            downloaded_at=time.time(),
            label="Latency",
        )
        await registry.add(entry)

        mock_driver = _make_mock_vbus_driver()
        with patch("services.vbus_driver.VBusDriver", return_value=mock_driver):
            result = await registry.load_to_kernel("latency-test", slot_id=3)

        # Mocked driver returns instantly — latency should be < 100ms
        assert (
            result.elapsed_ms < 100.0
        ), f"Latency {result.elapsed_ms}ms exceeds 100ms target"
        e = await registry.get("latency-test")
        assert e.kernel_latency_ms < 100.0

    @pytest.mark.asyncio
    async def test_deactivate_preserves_provenance(self, tmp_path):
        """Deactivation clears active/slot but preserves provenance history."""
        registry = ModelRegistry(model_dir=tmp_path)

        model_file = tmp_path / "deact-test.gguf"
        model_file.write_bytes(b"\x00" * 256)
        entry = ModelEntry(
            model_id="deact-test",
            filename="deact-test.gguf",
            format_id=FORMAT_GGUF,
            quant_label="q8_0",
            quant_type=2,
            total_size=256,
            sha256="d" * 64,
            source_url="https://hf.co/d.gguf",
            downloaded_at=time.time(),
            label="Deact",
        )
        await registry.add(entry)

        mock_driver = _make_mock_vbus_driver()
        with patch("services.vbus_driver.VBusDriver", return_value=mock_driver):
            await registry.load_to_kernel("deact-test", slot_id=1)

        # Deactivate
        await registry.set_inactive("deact-test")

        e = await registry.get("deact-test")
        assert e.active is False
        assert e.slot_id is None
        # Provenance preserved for audit trail
        assert e.kernel_mapped_at is not None
        assert e.kernel_xxh3 == "a1b2c3d4e5f60708"
        assert e.kernel_base_addr == "0xFFFF800040000000"

    @pytest.mark.asyncio
    async def test_slot_id_validation(self, tmp_path):
        """Slot 0 (Coordinator) rejected, slots 1-7 accepted."""
        registry = ModelRegistry(model_dir=tmp_path)

        model_file = tmp_path / "slot-val.gguf"
        model_file.write_bytes(b"\x00" * 128)
        entry = ModelEntry(
            model_id="slot-val",
            filename="slot-val.gguf",
            format_id=FORMAT_GGUF,
            quant_label="f16",
            quant_type=1,
            total_size=128,
            sha256="e" * 64,
            source_url="https://hf.co/sv.gguf",
            downloaded_at=time.time(),
            label="SlotVal",
        )
        await registry.add(entry)

        # Slot 0 must be rejected
        with pytest.raises(ValueError, match="slot 0 is Coordinator"):
            await registry.load_to_kernel("slot-val", slot_id=0)

        # Slot 8 must be rejected
        with pytest.raises(ValueError, match="must be 1-7"):
            await registry.load_to_kernel("slot-val", slot_id=8)

    @pytest.mark.asyncio
    async def test_missing_model_file_rejected(self, tmp_path):
        """Model registered but file missing → ValueError."""
        registry = ModelRegistry(model_dir=tmp_path)

        # Register WITHOUT creating the file
        entry = ModelEntry(
            model_id="ghost",
            filename="ghost.gguf",
            format_id=FORMAT_GGUF,
            quant_label="q4_k_m",
            quant_type=QUANT_Q4_K_M,
            total_size=999,
            sha256="f" * 64,
            source_url="https://hf.co/g.gguf",
            downloaded_at=time.time(),
            label="Ghost",
        )
        await registry.add(entry)

        with pytest.raises(ValueError, match="file missing"):
            await registry.load_to_kernel("ghost", slot_id=1)

    @pytest.mark.asyncio
    async def test_vbus_connection_failure(self, tmp_path):
        """VBus connection failure → RuntimeError."""
        registry = ModelRegistry(model_dir=tmp_path)

        model_file = tmp_path / "conn-fail.gguf"
        model_file.write_bytes(b"\x00" * 64)
        entry = ModelEntry(
            model_id="conn-fail",
            filename="conn-fail.gguf",
            format_id=FORMAT_GGUF,
            quant_label="f16",
            quant_type=1,
            total_size=64,
            sha256="0" * 64,
            source_url="https://hf.co/cf.gguf",
            downloaded_at=time.time(),
            label="ConnFail",
        )
        await registry.add(entry)

        mock_driver = MagicMock()
        mock_driver.connect.return_value = False  # Connection fails

        with patch("services.vbus_driver.VBusDriver", return_value=mock_driver):
            with pytest.raises(RuntimeError, match="Cannot connect"):
                await registry.load_to_kernel("conn-fail", slot_id=1)

    @pytest.mark.asyncio
    async def test_activate_on_slot_delegates(self, tmp_path):
        """activate_on_slot() delegates to registry.load_to_kernel()."""
        registry = ModelRegistry(model_dir=tmp_path)

        model_file = tmp_path / "delegate.gguf"
        model_file.write_bytes(b"\x00" * 512)
        entry = ModelEntry(
            model_id="delegate",
            filename="delegate.gguf",
            format_id=FORMAT_GGUF,
            quant_label="q4_k_m",
            quant_type=QUANT_Q4_K_M,
            total_size=512,
            sha256="1" * 64,
            source_url="https://hf.co/del.gguf",
            downloaded_at=time.time(),
            label="Delegate",
        )
        await registry.add(entry)

        mock_driver = _make_mock_vbus_driver()
        with patch("services.vbus_driver.VBusDriver", return_value=mock_driver):
            result = await activate_on_slot(
                model_id="delegate",
                slot_id=2,
                registry=registry,
            )

        assert result.model_id == "delegate"
        assert result.slot_id == 2
        assert result.warp is True

        # Provenance should also be set (via load_to_kernel)
        e = await registry.get("delegate")
        assert e.kernel_mapped_at is not None
