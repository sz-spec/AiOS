"""
backend/tests/security/test_kernel_write_block_live.py

Sprint 18 / Wave 3.B / Cluster C-2 — LIVE-mode tests for the
userspace bpf() binding in `backend/security/kernel_gate_connector.py`.

These tests exercise the REAL bpf() syscall path. They are SKIPPED on:
  - Non-Linux hosts (macOS dev, Windows runners)
  - Linux hosts without /sys/fs/bpf mounted
  - Linux hosts without the eBPF LSM program loaded (no pinned map
    at /sys/fs/bpf/vos3/taint_colors)

To run them:
  1. Build the eBPF program: `cd kernel/bpf && make`
  2. Load it: `cd kernel/bpf && sudo make load`
  3. Verify pinned map: `sudo bpftool map show pinned /sys/fs/bpf/vos3/taint_colors`
  4. Run: `sudo backend/.venv_p312/bin/python -m pytest backend/tests/security/test_kernel_write_block_live.py -v`
     (sudo because bpf() syscall needs CAP_BPF or root)

Honest scope ceiling:
  - The MOCK test suite in test_kernel_write_block.py covers the
    semantic contract; this file ONLY exercises the bpf() syscall
    binding correctness on real Linux.
  - Per the Sprint 18 plan: scaffolded from macOS dev, full
    validation deferred to a Linux CI runner.
  - The struct.pack format strings in kernel_gate_connector.py MUST
    match kernel/include/vos/taint_maps.h byte-for-byte. A mismatch
    would be caught by these tests (the kernel-side verifier
    rejects malformed map entries with -E2BIG / -EINVAL).
"""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_KGW_PATH = _REPO_ROOT / "backend" / "security" / "kernel_gate_connector.py"
_TE_PATH = _REPO_ROOT / "backend" / "security" / "taint_engine_v2.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


te = _load("vos3_tev2_live", _TE_PATH)
kgw = _load("vos3_kernel_gate_live", _KGW_PATH)


# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _has_bpffs() -> bool:
    return os.path.isdir("/sys/fs/bpf")


def _has_pinned_taint_map() -> bool:
    return os.path.exists(kgw.VOS3_TAINT_MAP_PIN_PATH)


def _has_cap_bpf() -> bool:
    """Best-effort check — only root reliably has CAP_BPF. Returns True
    if EUID == 0, otherwise False (the test SKIPS in that case)."""
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


REQUIRES_LINUX = pytest.mark.skipif(not _is_linux(), reason="LIVE tests require Linux")
REQUIRES_BPFFS = pytest.mark.skipif(
    not (_is_linux() and _has_bpffs()),
    reason="LIVE tests require /sys/fs/bpf mounted (bpffs)",
)
REQUIRES_TAINT_MAP = pytest.mark.skipif(
    not (_is_linux() and _has_bpffs() and _has_pinned_taint_map()),
    reason=(
        "LIVE tests require the eBPF LSM program loaded "
        "(run `cd kernel/bpf && sudo make load`)"
    ),
)
REQUIRES_CAP_BPF = pytest.mark.skipif(
    not (_is_linux() and _has_cap_bpf()),
    reason="LIVE tests require CAP_BPF (run with sudo)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_buffer(content: bytes, label: int) -> "te.TaintedBuffer":
    engine = te.ByteTaintEngine()
    return engine.label_source(
        source_id="test:live_kernel_gate",
        content=content,
        label=te.TaintLabel(label),
    )


# ---------------------------------------------------------------------------
# Skeleton-mode sanity (these run on ALL hosts including macOS — they
# verify the connector reports the correct mode + struct packing)
# ---------------------------------------------------------------------------


def test_value_packing_matches_kernel_header_size():
    """The packed map value MUST match
    sizeof(struct vos3_taint_color_entry) from taint_maps.h.

    Layout (packed):
      u8 colors[65536]; u32 length; u8 max_color; u8 sink_kind;
      u8 sink_max_label; u8 mode; u64 sha_low; u64 sha_high; u64 pushed_ns
    Total: 65536 + 4 + 4 + 24 = 65568 bytes."""
    assert kgw._VALUE_SIZE == 65568


def test_key_packing_matches_kernel_header_size():
    """struct vos3_taint_map_key { u32 pid; u32 fd; } = 8 bytes."""
    assert kgw._KEY_SIZE == 8


def test_value_packing_round_trip():
    """Pack a value with known fields + unpack — verify the values
    survive the round trip + the colors region is right-padded."""
    import struct as _struct

    sample = kgw._pack_map_value(
        colors=b"AB",
        max_color=1,
        sink_kind=0,
        sink_max_label=2,
        mode=kgw.VOS3_TAINT_MODE_PER_FD,
        sha256_low=0xDEADBEEF,
        sha256_high=0xFEEDFACE,
        pushed_ns=1234567890,
    )
    assert len(sample) == 65568
    (
        colors,
        length,
        max_color,
        sink_kind,
        sink_max_label,
        mode,
        sha_low,
        sha_high,
        pushed_ns,
    ) = _struct.unpack(kgw._VALUE_FMT, sample)
    # Right-pad with zeros past length=2
    assert colors[:2] == b"AB"
    assert colors[2:].count(b"\x00") == 65534
    assert length == 2
    assert max_color == 1
    assert sink_kind == 0
    assert sink_max_label == 2
    assert mode == kgw.VOS3_TAINT_MODE_PER_FD
    assert sha_low == 0xDEADBEEF
    assert sha_high == 0xFEEDFACE
    assert pushed_ns == 1234567890


def test_sha256_pack_high_low_split():
    """vos3_taint_sha_pack packs first 16 bytes of SHA-256 into two
    u64s, big-endian per-half. Verify our Python version matches."""
    sha_hex = "0102030405060708090a0b0c0d0e0f10" + "ff" * 16
    high, low = kgw._sha256_pack(sha_hex)
    assert high == 0x0102030405060708
    assert low == 0x090A0B0C0D0E0F10


def test_value_packing_records_mode_field():
    """Sprint 18 / Wave 3.C — the packed value's `mode` byte sits at a
    specific offset and the kernel reads it to branch on
    PER_BYTE vs PER_FD. Verify Python writes that byte correctly."""
    import struct as _struct

    sample = kgw._pack_map_value(
        colors=b"AB",
        max_color=1,
        sink_kind=0,
        sink_max_label=2,
        mode=kgw.VOS3_TAINT_MODE_PER_BYTE,
        sha256_low=0,
        sha256_high=0,
        pushed_ns=0,
    )
    unpacked = _struct.unpack(kgw._VALUE_FMT, sample)
    # Layout: (colors, length, max_color, sink_kind, sink_max_label,
    #          mode, sha_low, sha_high, pushed_ns) — mode is index 5
    assert unpacked[5] == kgw.VOS3_TAINT_MODE_PER_BYTE

    sample_fd = kgw._pack_map_value(
        colors=b"AB",
        max_color=1,
        sink_kind=0,
        sink_max_label=2,
        mode=kgw.VOS3_TAINT_MODE_PER_FD,
        sha256_low=0,
        sha256_high=0,
        pushed_ns=0,
    )
    assert _struct.unpack(kgw._VALUE_FMT, sample_fd)[5] == kgw.VOS3_TAINT_MODE_PER_FD


def test_bpf_binding_refuses_on_macos():
    """Hard guard: on macOS dev hosts, _BpfSyscall MUST refuse to
    construct so we never accidentally invoke a Darwin syscall by
    a Linux NR."""
    if platform.system() == "Linux":
        pytest.skip("macOS-only guard test")
    with pytest.raises(kgw._BpfBindingUnavailable):
        kgw._BpfSyscall()


# ---------------------------------------------------------------------------
# LIVE-mode tests — only run on Linux with the kernel program loaded
# ---------------------------------------------------------------------------


@REQUIRES_LINUX
@REQUIRES_BPFFS
def test_libc_binding_succeeds_on_linux():
    """On any Linux host, _BpfSyscall() MUST construct successfully —
    even if bpffs isn't mounted (that's a separate check)."""
    binding = kgw._BpfSyscall()
    assert binding is not None
    assert binding._libc is not None


@REQUIRES_LINUX
def test_connector_falls_back_to_mock_when_no_pinned_map(monkeypatch):
    """If the kernel-side program is NOT loaded, the connector must
    fall back to MOCK with a WARNING log (safe-fail). Test by
    pointing the pin path at a definitely-nonexistent file."""
    monkeypatch.setattr(
        kgw, "VOS3_TAINT_MAP_PIN_PATH", "/sys/fs/bpf/vos3/__nonexistent_taint_map__"
    )
    monkeypatch.setenv("VOS3_TAINT_GATE_FORCE_LIVE", "1")
    c = kgw.KernelGateConnector()
    assert c.mode == kgw.GateMode.MOCK


@REQUIRES_TAINT_MAP
@REQUIRES_CAP_BPF
def test_live_mode_opens_pinned_map():
    """With the kernel program loaded + CAP_BPF, the connector
    successfully opens the pinned map and reports mode == LIVE."""
    os.environ["VOS3_TAINT_GATE_FORCE_LIVE"] = "1"
    try:
        c = kgw.KernelGateConnector()
        assert c.mode == kgw.GateMode.LIVE
        assert c._map_fd is not None
        assert c._map_fd >= 0
    finally:
        os.environ.pop("VOS3_TAINT_GATE_FORCE_LIVE", None)


@REQUIRES_TAINT_MAP
@REQUIRES_CAP_BPF
def test_live_push_succeeds():
    """Push a TaintedBuffer to the LIVE kernel map and verify the
    receipt reports pushed=True with mode=LIVE."""
    os.environ["VOS3_TAINT_GATE_FORCE_LIVE"] = "1"
    try:
        c = kgw.KernelGateConnector()
        assert c.mode == kgw.GateMode.LIVE
        buf = _make_buffer(b"untrusted body", int(kgw.TaintLabel.UNTRUSTED))
        receipt = c.push_tainted_buffer(
            fd=42,
            pid=os.getpid(),
            buffer=buf,
            sink_kind=kgw.SinkKind.NETWORK_EGRESS,
        )
        assert receipt.pushed is True
        assert receipt.mode == kgw.GateMode.LIVE
        # Cleanup
        c.evict(fd=42, pid=os.getpid())
    finally:
        os.environ.pop("VOS3_TAINT_GATE_FORCE_LIVE", None)


@REQUIRES_TAINT_MAP
@REQUIRES_CAP_BPF
def test_live_push_then_evict_cycle():
    """Push entry → evict entry → re-evict returns False."""
    os.environ["VOS3_TAINT_GATE_FORCE_LIVE"] = "1"
    try:
        c = kgw.KernelGateConnector()
        buf = _make_buffer(b"x", int(kgw.TaintLabel.TOXIC))
        receipt = c.push_tainted_buffer(
            fd=43,
            pid=os.getpid(),
            buffer=buf,
            sink_kind=kgw.SinkKind.NETWORK_EGRESS,
        )
        assert receipt.pushed is True
        assert c.evict(fd=43, pid=os.getpid()) is True
        # Second evict: ENOENT → False
        assert c.evict(fd=43, pid=os.getpid()) is False
    finally:
        os.environ.pop("VOS3_TAINT_GATE_FORCE_LIVE", None)


@REQUIRES_TAINT_MAP
@REQUIRES_CAP_BPF
def test_live_per_byte_push_marks_mode_field():
    """Sprint 18 / Wave 3.C — push with taint_mode=PER_BYTE and verify
    the entry's mode byte is set correctly in the kernel map.

    We can't directly read the kernel-side map from Python (no
    BPF_MAP_LOOKUP_ELEM in the binding yet — Sprint 19 work for the
    audit ring consumer). But we CAN verify the push succeeded and
    that downstream writes get the right kernel decision based on
    the mode byte. Here we just confirm the push doesn't error out
    when the new mode field is set."""
    os.environ["VOS3_TAINT_GATE_FORCE_LIVE"] = "1"
    try:
        c = kgw.KernelGateConnector()
        engine = te.ByteTaintEngine()
        base = engine.label_source(
            source_id="test:live_per_byte",
            content=b"A" * 100 + b"B" * 30,
            label=te.TaintLabel.UNTRUSTED,
        )
        elevated = engine.label_range(
            base, start=100, end=130, label=te.TaintLabel.TOXIC
        )
        receipt = c.push_tainted_buffer(
            fd=85,
            pid=os.getpid(),
            buffer=elevated,
            sink_kind=kgw.SinkKind.NETWORK_EGRESS,
            taint_mode=kgw.TaintMode.PER_BYTE,
        )
        assert receipt.pushed is True
        c.evict(fd=85, pid=os.getpid())
    finally:
        os.environ.pop("VOS3_TAINT_GATE_FORCE_LIVE", None)


@REQUIRES_TAINT_MAP
@REQUIRES_CAP_BPF
def test_live_chunked_push_for_large_buffer():
    """Buffer > 64 KB gets chunked across multiple map entries (per-fd
    inside the kernel sees only the LAST entry, since the key is
    (pid,fd) — this is per the per-fd MVP). Verify stats counter."""
    os.environ["VOS3_TAINT_GATE_FORCE_LIVE"] = "1"
    try:
        c = kgw.KernelGateConnector()
        big = b"X" * (kgw.VOS3_TAINT_MAX_COLOR_BYTES + 1)
        buf = _make_buffer(big, int(kgw.TaintLabel.UNTRUSTED))
        receipt = c.push_tainted_buffer(
            fd=44,
            pid=os.getpid(),
            buffer=buf,
            sink_kind=kgw.SinkKind.FILE_WRITE,
        )
        assert receipt.pushed is True
        assert c.stats.chunks_emitted >= 2
        c.evict(fd=44, pid=os.getpid())
    finally:
        os.environ.pop("VOS3_TAINT_GATE_FORCE_LIVE", None)
