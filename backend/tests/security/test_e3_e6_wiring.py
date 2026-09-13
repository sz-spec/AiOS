"""
Sprint 19 Wave 2 — E3 / E6 live-path wiring tests.

These prove the moat guards actually FIRE through their production call
paths (not just in isolation):

  - E3: model_manager.load_to_kernel() calls
    IommuDmaGuard.require_iommu_for_gpu_bind() before the DMA warp
    transfer. On this unverifiable (non-Linux) host the gate fail-closes
    → DmaBypassRefused, and the model is NOT bound.
  - E6: kernel_provider.KernelProvider.astream() calls
    PerfCounterLockdown.require_lockdown_for_multitenant_inference()
    before launching inference. On this host it fail-closes →
    PerfCounterExposed.

Each gate's escape hatches (dev-override / single-tenant / hard-disable)
are exercised so the gate is provably skippable in the environments that
need it — without weakening the default fail-closed posture.
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.model_manager import (  # noqa: E402
    ModelEntry,
    ModelRegistry,
    FORMAT_GGUF,
    QUANT_Q4_K_M,
    _enforce_iommu_for_gpu_bind,
)
from security.iommu_dma_guard import DmaBypassRefused  # noqa: E402
from security.perf_counter_lockdown import PerfCounterExposed  # noqa: E402
import ai.llm.kernel_provider as kp  # noqa: E402

HUGEPAGE = 2 * 1024 * 1024


def _make_model_entry(model_id: str) -> ModelEntry:
    return ModelEntry(
        model_id=model_id,
        filename=f"{model_id}.gguf",
        format_id=FORMAT_GGUF,
        quant_label="q4_k_m",
        quant_type=QUANT_Q4_K_M,
        total_size=HUGEPAGE,
        sha256=hashlib.sha256(model_id.encode()).hexdigest(),
        source_url=f"https://hf.co/{model_id}.gguf",
        downloaded_at=time.time(),
        label=model_id[:32],
    )


def _mock_driver():
    d = MagicMock()
    d.connect.return_value = True
    d.disconnect.return_value = None
    d.load_model_warp.return_value = {
        "addr": "0xFFFF800040000000",
        "size": HUGEPAGE,
        "checksum_xxh3": "a1b2c3d4e5f60708",
        "checksum_crc32c": "deadbeef",
        "warp": True,
    }
    return d


@pytest.fixture(autouse=True)
def _clean_guard_env(monkeypatch):
    """Start each test with the guards ENABLED and no overrides set."""
    for var in (
        "VOS3_DISABLE_IOMMU_GUARD",
        "VOS3_IOMMU_DEV_OVERRIDE",
        "VOS3_DISABLE_PERF_GUARD",
        "VOS3_PERF_DEV_OVERRIDE",
        "VOS3_PERF_SINGLE_TENANT",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


# ==========================================================================
# E3 — GPU slot-bind path
# ==========================================================================


class TestE3Wiring:
    @pytest.mark.asyncio
    async def test_load_to_kernel_fail_closes_on_unverifiable_host(self, tmp_path):
        registry = ModelRegistry(model_dir=tmp_path)
        entry = _make_model_entry("e3-model")
        (tmp_path / "e3-model.gguf").write_bytes(b"\x00" * HUGEPAGE)
        await registry.add(entry)
        driver = _mock_driver()
        with patch("services.vbus_driver.VBusDriver", return_value=driver):
            with pytest.raises(DmaBypassRefused):
                await registry.load_to_kernel("e3-model", slot_id=1)
        # The DMA warp transfer must NEVER have been reached.
        driver.load_model_warp.assert_not_called()
        # And the model must NOT be marked active.
        assert (await registry.get("e3-model")).active is False

    @pytest.mark.asyncio
    async def test_dev_override_allows_bind(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VOS3_IOMMU_DEV_OVERRIDE", "1")
        registry = ModelRegistry(model_dir=tmp_path)
        entry = _make_model_entry("e3-ovr")
        (tmp_path / "e3-ovr.gguf").write_bytes(b"\x00" * HUGEPAGE)
        await registry.add(entry)
        driver = _mock_driver()
        with patch("services.vbus_driver.VBusDriver", return_value=driver):
            result = await registry.load_to_kernel("e3-ovr", slot_id=1)
        assert result is not None
        driver.load_model_warp.assert_called_once()

    @pytest.mark.asyncio
    async def test_hard_disable_skips_gate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VOS3_DISABLE_IOMMU_GUARD", "1")
        registry = ModelRegistry(model_dir=tmp_path)
        entry = _make_model_entry("e3-dis")
        (tmp_path / "e3-dis.gguf").write_bytes(b"\x00" * HUGEPAGE)
        await registry.add(entry)
        driver = _mock_driver()
        with patch("services.vbus_driver.VBusDriver", return_value=driver):
            await registry.load_to_kernel("e3-dis", slot_id=1)
        driver.load_model_warp.assert_called_once()

    def test_helper_raises_without_env(self):
        with pytest.raises(DmaBypassRefused):
            _enforce_iommu_for_gpu_bind("m", 1)

    def test_helper_disabled_returns(self, monkeypatch):
        monkeypatch.setenv("VOS3_DISABLE_IOMMU_GUARD", "1")
        assert _enforce_iommu_for_gpu_bind("m", 1) is None


# ==========================================================================
# E6 — inference-launch path
# ==========================================================================


class TestE6Wiring:
    def _provider(self):
        kp.register_kernel_slot("kernel-default", 1, model_id="e6-model")
        return kp.KernelProvider(alias="kernel-default")

    def teardown_method(self):
        kp.unregister_kernel_slot("kernel-default")

    def test_astream_fail_closes_on_unverifiable_host(self):
        provider = self._provider()
        with pytest.raises(PerfCounterExposed):
            list(provider.astream("hello", max_tokens=4))

    def test_single_tenant_skips_gate(self, monkeypatch):
        monkeypatch.setenv("VOS3_PERF_SINGLE_TENANT", "1")
        provider = self._provider()
        # Gate skipped → execution proceeds to the VBus connect, which
        # fails on this host (no bridge) → KernelProviderUnavailable, NOT
        # PerfCounterExposed. Reaching it proves the gate was passed.
        with patch.object(kp, "VBusDriver", create=True):
            with pytest.raises(kp.KernelProviderUnavailable):
                driver = MagicMock()
                driver.connect.return_value = False
                with patch("services.vbus_driver.VBusDriver", return_value=driver):
                    list(provider.astream("hello", max_tokens=4))

    def test_dev_override_skips_gate(self, monkeypatch):
        monkeypatch.setenv("VOS3_PERF_DEV_OVERRIDE", "1")
        provider = self._provider()
        driver = MagicMock()
        driver.connect.return_value = False
        with patch("services.vbus_driver.VBusDriver", return_value=driver):
            with pytest.raises(kp.KernelProviderUnavailable):
                list(provider.astream("hello", max_tokens=4))

    def test_hard_disable_skips_gate(self, monkeypatch):
        monkeypatch.setenv("VOS3_DISABLE_PERF_GUARD", "1")
        provider = self._provider()
        driver = MagicMock()
        driver.connect.return_value = False
        with patch("services.vbus_driver.VBusDriver", return_value=driver):
            with pytest.raises(kp.KernelProviderUnavailable):
                list(provider.astream("hello", max_tokens=4))
