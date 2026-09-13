"""
Tests for Sprint 23 (DEPTH) — Runtime Model-Integrity Watchdog
(backend/security/model_integrity_watchdog.py).

Pins the fail-closed contract: a registered model-memory region that drifts
from its baseline is evicted and raises ModelIntegrityCompromised. Uses an
injected in-memory MemorySource (no Linux/GPU/shared-memory) so the gate is
100% exercised on macOS.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from security.model_integrity_watchdog import (  # noqa: E402
    ENV_DEV_OVERRIDE,
    ModelIntegrityCompromised,
    ModelIntegrityWatchdog,
    RegionState,
)


def _wd(source: dict):
    """Watchdog backed by a mutable dict source the test can corrupt."""
    return ModelIntegrityWatchdog(memory_source=source)


def test_register_and_clean_scan_passes():
    src = {("m", "blk.0"): b"\x01" * 4096}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="blk.0", baseline=src[("m", "blk.0")])
    reports = wd.scan("m")
    assert len(reports) == 1
    assert reports[0].intact
    assert wd.stats.verified == 1
    assert wd.stats.drift_detected == 0


def test_single_bit_drift_evicts_and_raises():
    data = bytearray(b"\x00" * 8192)
    src = {("m", "blk.0"): bytes(data)}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="blk.0", baseline=src[("m", "blk.0")])
    # Flip a single bit deep in the region.
    data[5000] ^= 0x01
    src[("m", "blk.0")] = bytes(data)
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")
    # Region evicted (no longer active).
    assert not wd.is_registered("m", "blk.0")
    assert wd.stats.evictions == 1
    assert wd.stats.drift_detected == 1


def test_multi_region_only_drifted_one_evicted():
    src = {
        ("m", "a"): b"A" * 1024,
        ("m", "b"): b"B" * 1024,
        ("m", "c"): b"C" * 1024,
    }
    wd = _wd(src)
    for layer in ("a", "b", "c"):
        wd.register_region(model_id="m", layer_id=layer, baseline=src[("m", layer)])
    src[("m", "b")] = b"B" * 1023 + b"X"  # corrupt only region b
    with pytest.raises(ModelIntegrityCompromised) as exc:
        wd.scan("m")
    drifted = {r.layer_id for r in exc.value.reports}
    assert drifted == {"b"}
    # a and c remain active; b evicted.
    assert wd.is_registered("m", "a")
    assert wd.is_registered("m", "c")
    assert not wd.is_registered("m", "b")


def test_evicted_region_not_rescanned():
    data = bytearray(b"\x00" * 256)
    src = {("m", "x"): bytes(data)}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="x", baseline=src[("m", "x")])
    data[0] ^= 0xFF
    src[("m", "x")] = bytes(data)
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")
    # After eviction the model has no active regions → require_integrity errors.
    assert wd.active_regions() == ()


def test_require_integrity_allows_when_intact():
    src = {("m", "x"): b"z" * 512}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="x", baseline=src[("m", "x")])
    reports = wd.require_integrity("m")
    assert all(r.intact for r in reports)


def test_require_integrity_raises_on_drift():
    src = {("m", "x"): b"z" * 512}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="x", baseline=src[("m", "x")])
    src[("m", "x")] = b"z" * 511 + b"q"
    with pytest.raises(ModelIntegrityCompromised):
        wd.require_integrity("m")


def test_require_integrity_unregistered_model_errors():
    wd = _wd({})
    with pytest.raises(ValueError):
        wd.require_integrity("ghost")


def test_unavailable_region_is_failclosed():
    """A source that can't produce the bytes is treated as drift."""
    src = {("m", "x"): b"abc"}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="x", baseline=b"abc")
    del src[("m", "x")]  # source can no longer return the region
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")


def test_dev_override_downgrades_drift(monkeypatch):
    monkeypatch.setenv(ENV_DEV_OVERRIDE, "1")
    data = bytearray(b"\x00" * 128)
    src = {("m", "x"): bytes(data)}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="x", baseline=src[("m", "x")])
    data[0] ^= 0x01
    src[("m", "x")] = bytes(data)
    reports = wd.scan("m")  # does NOT raise under override
    assert wd.stats.dev_overrides_used == 1
    assert wd.is_registered("m", "x")  # not evicted
    assert any(r.state == RegionState.EVICTED for r in reports)  # state still honest


def test_tick_scans_all_models():
    src = {("m1", "a"): b"1", ("m2", "a"): b"2"}
    wd = _wd(src)
    wd.register_region(model_id="m1", layer_id="a", baseline=b"1")
    wd.register_region(model_id="m2", layer_id="a", baseline=b"2")
    reports = wd.tick()
    assert {r.model_id for r in reports} == {"m1", "m2"}
    assert wd.stats.scans == 1


def test_callable_memory_source():
    store = {("m", "a"): b"hello"}
    wd = ModelIntegrityWatchdog(memory_source=lambda mid, lid: store.get((mid, lid)))
    wd.register_region(model_id="m", layer_id="a", baseline=b"hello")
    assert wd.scan("m")[0].intact
    store[("m", "a")] = b"world"
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")


def test_register_rejects_bad_input():
    wd = _wd({})
    with pytest.raises(ValueError):
        wd.register_region(model_id="", layer_id="a", baseline=b"x")
    with pytest.raises(TypeError):
        wd.register_region(model_id="m", layer_id="a", baseline="not-bytes")
