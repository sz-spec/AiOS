"""
B3 — eBPF host↔guest write-gate bridge tests (TEST_PLAN_300 §B3)
================================================================

Adversarial sweep over ``security/kernel_gate_connector.py`` — the userspace
bridge that marshals per-(pid,fd) byte-color entries into the kernel eBPF
LSM map so the in-kernel hook can return -EPERM on a toxic egress.

On macOS dev the connector runs in MOCK mode (no bpffs); ``simulate_write``
mirrors the kernel LSM decision so the policy is exercised with zero kernel
dependency. These tests assert deterministic fail-closed marshaling:

  * malformed packet shapes (bad fd/pid, broken color/content invariant,
    missing buffer attributes) are rejected at the boundary;
  * the gate enforces on the RECOMPUTED max(colors), not the buffer's
    self-reported max_color() — a mis-pushing / lying buffer cannot
    under-report its way past the sink ceiling;
  * per-byte mode honours clean-prefix / toxic-region semantics (EchoLeak);
  * the map key/value packing matches the kernel struct byte-for-byte
    (CONTRACT — a drift here silently breaks live enforcement);
  * the documented "no entry → default ALLOW" behaviour is characterized
    as FINDING B3-1 (fail-open ceiling).

Run:
    .venv_p312/bin/python -m pytest tests/audit/test_b3_ebpf_bridge.py -v
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

import pytest

from security import kernel_gate_connector as kgc
from security.kernel_gate_connector import (
    DecisionKind,
    GateMode,
    KernelGateConnector,
    SinkKind,
    TaintLabel,
    TaintMode,
    VOS3_TAINT_MAX_COLOR_BYTES,
)


# ---------------------------------------------------------------------------
# A minimal TaintedBuffer-shaped object (duck-typed per the connector contract)
# ---------------------------------------------------------------------------


@dataclass
class FakeBuffer:
    content: bytes
    colors: bytes
    _claimed_max: int | None = None  # if set, max_color() LIES (adversarial)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def max_color(self) -> int:
        if self._claimed_max is not None:
            return self._claimed_max
        return max(self.colors) if self.colors else int(TaintLabel.PUBLIC)


def _buf(colors: bytes, *, claimed_max: int | None = None) -> FakeBuffer:
    return FakeBuffer(content=b"\x00" * len(colors), colors=colors,
                      _claimed_max=claimed_max)


def _conn() -> KernelGateConnector:
    return KernelGateConnector(force_mock=True)


# ===========================================================================
# Mode + basic push/decision
# ===========================================================================


def test_b3_force_mock_is_mock_mode():
    assert _conn().mode == GateMode.MOCK


def test_b3_public_buffer_to_egress_allows():
    c = _conn()
    c.push_tainted_buffer(fd=5, pid=10, buffer=_buf(bytes([0, 0, 0, 0])),
                          sink_kind=SinkKind.NETWORK_EGRESS)
    d = c.simulate_write(fd=5, pid=10, length=4)
    assert d.decision == DecisionKind.ALLOW and d.errno == 0


def test_b3_secret_buffer_to_egress_denies_with_eperm():
    c = _conn()
    # SECRET(2) > NETWORK_EGRESS ceiling UNTRUSTED(1) → DENY.
    c.push_tainted_buffer(fd=5, pid=10, buffer=_buf(bytes([0, 0, 2, 0])),
                          sink_kind=SinkKind.NETWORK_EGRESS)
    d = c.simulate_write(fd=5, pid=10, length=4)
    assert d.decision == DecisionKind.DENY
    assert d.errno == -13  # -EPERM


def test_b3_file_write_allows_secret_but_denies_toxic():
    c = _conn()
    c.push_tainted_buffer(fd=1, pid=10, buffer=_buf(bytes([2, 2])),
                          sink_kind=SinkKind.FILE_WRITE)  # ceiling SECRET(2)
    assert c.simulate_write(fd=1, pid=10).decision == DecisionKind.ALLOW
    c.push_tainted_buffer(fd=2, pid=10, buffer=_buf(bytes([3])),
                          sink_kind=SinkKind.FILE_WRITE)  # TOXIC(3) > SECRET
    assert c.simulate_write(fd=2, pid=10).decision == DecisionKind.DENY


def test_b3_audit_log_sink_accepts_toxic():
    c = _conn()
    # AUDIT_LOG ceiling is TOXIC(3) — the catch-all sink accepts everything.
    c.push_tainted_buffer(fd=9, pid=10, buffer=_buf(bytes([3, 3, 3])),
                          sink_kind=SinkKind.AUDIT_LOG)
    assert c.simulate_write(fd=9, pid=10).decision == DecisionKind.ALLOW


# ===========================================================================
# ADVERSARIAL: the gate must not trust the buffer's self-reported max_color
# ===========================================================================


def test_b3_lying_buffer_under_reporting_max_color_is_still_gated():
    """A compromised buffer claims max_color()=PUBLIC while its colors[]
    actually contain a SECRET byte. The gate must enforce on the REAL
    max(colors), not the lie — else mis-pushing defeats the gate."""
    c = _conn()
    c.push_tainted_buffer(
        fd=5, pid=10,
        buffer=_buf(bytes([0, 0, 2, 0]), claimed_max=int(TaintLabel.PUBLIC)),
        sink_kind=SinkKind.NETWORK_EGRESS,
    )
    d = c.simulate_write(fd=5, pid=10)
    assert d.decision == DecisionKind.DENY  # real max(colors)=SECRET wins


# ===========================================================================
# PER_BYTE mode — clean-prefix / toxic-region (EchoLeak)
# ===========================================================================


def test_b3_per_byte_clean_prefix_allowed_toxic_region_denied():
    c = _conn()
    # colors: 3 clean bytes then a toxic tail.
    colors = bytes([0, 0, 0, 3, 3])
    c.push_tainted_buffer(fd=7, pid=10, buffer=_buf(colors),
                          sink_kind=SinkKind.NETWORK_EGRESS,
                          taint_mode=TaintMode.PER_BYTE)
    # Writing only the clean prefix [0:3] → ALLOW.
    clean = c.simulate_write(fd=7, pid=10, length=3, write_start=0)
    assert clean.decision == DecisionKind.ALLOW
    # Writing a range that includes the toxic tail → DENY (deny_off set).
    toxic = c.simulate_write(fd=7, pid=10, length=5, write_start=0)
    assert toxic.decision == DecisionKind.DENY
    assert "deny_off" in toxic.reason


# ===========================================================================
# Malformed packet shapes → rejected at the boundary
# ===========================================================================


def test_b3_color_content_invariant_violation_rejected():
    c = _conn()
    bad = FakeBuffer(content=b"\x00\x00\x00", colors=b"\x00\x00")  # len mismatch
    with pytest.raises(ValueError):
        c.push_tainted_buffer(fd=5, pid=10, buffer=bad,
                              sink_kind=SinkKind.NETWORK_EGRESS)


@pytest.mark.parametrize("fd,pid", [(-1, 10), (10, -1)])
def test_b3_negative_fd_or_pid_rejected(fd, pid):
    c = _conn()
    with pytest.raises(ValueError):
        c.push_tainted_buffer(fd=fd, pid=pid, buffer=_buf(b"\x00"),
                              sink_kind=SinkKind.NETWORK_EGRESS)


def test_b3_none_buffer_rejected():
    c = _conn()
    with pytest.raises(ValueError):
        c.push_tainted_buffer(fd=5, pid=10, buffer=None,
                              sink_kind=SinkKind.NETWORK_EGRESS)


def test_b3_buffer_missing_attributes_rejected():
    c = _conn()
    with pytest.raises(TypeError):
        c.push_tainted_buffer(fd=5, pid=10, buffer=object(),
                              sink_kind=SinkKind.NETWORK_EGRESS)


# ===========================================================================
# Chunking, eviction
# ===========================================================================


def test_b3_oversized_buffer_is_chunked():
    c = _conn()
    n = VOS3_TAINT_MAX_COLOR_BYTES + 1  # forces 2 chunks
    receipt = c.push_tainted_buffer(fd=5, pid=10, buffer=_buf(b"\x00" * n),
                                    sink_kind=SinkKind.NETWORK_EGRESS)
    assert receipt.pushed is True
    assert c.stats.chunks_emitted == 2


def test_b3_evict_removes_entry():
    c = _conn()
    c.push_tainted_buffer(fd=5, pid=10, buffer=_buf(bytes([2])),
                          sink_kind=SinkKind.NETWORK_EGRESS)
    assert c.evict(fd=5, pid=10) is True
    # After eviction, the (pid,fd) is untracked → default ALLOW (see B3-1).
    assert c.simulate_write(fd=5, pid=10).decision == DecisionKind.ALLOW


def test_b3_FINDING_no_entry_defaults_to_allow():
    """FINDING B3-1 (fail-open ceiling, documented): a write to an fd with
    NO pushed color entry is ALLOWED ('no entry → default ALLOW'). This is
    the documented race ceiling — if a caller writes before pushing (or an
    attacker prevents the push), the gate does not fire. Characterized here
    so a regression that changes the default is caught, and so the ceiling
    stays visible. Hardening (atomic mark-and-push) is Sprint 19+ per the
    module docstring."""
    c = _conn()
    d = c.simulate_write(fd=999, pid=999, length=10)  # never pushed
    assert d.decision == DecisionKind.ALLOW
    assert "no entry" in d.reason.lower()


# ===========================================================================
# CONTRACT — map key/value packing byte-for-byte with the kernel header
# ===========================================================================


def test_b3_04_map_struct_sizes_match_kernel_header():
    """B3.04: the packed key/value MUST match kernel/include/vos/taint_maps.h
    byte-for-byte (8-byte key, 65568-byte value). A silent drift here makes
    LIVE-mode bpf() updates corrupt or rejected."""
    assert kgc._KEY_SIZE == 8
    assert kgc._VALUE_SIZE == 65568  # 65536 colors + 4 len + 4 (4×u8) + 24 (3×u64)


def test_b3_sha256_pack_high_low_split():
    """_sha256_pack mirrors the kernel vos3_taint_sha_pack: high = first 8
    bytes big-endian, low = next 8 bytes."""
    hexd = hashlib.sha256(b"contract").hexdigest()
    high, low = kgc._sha256_pack(hexd)
    raw = bytes.fromhex(hexd[:32])
    assert high == int.from_bytes(raw[:8], "big")
    assert low == int.from_bytes(raw[8:16], "big")
    assert kgc._sha256_pack("") == (0, 0)


# ===========================================================================
# §9 — Atomic Mark-and-Push: LIVE connector enforcement (Phase 19.0/19.1)
# (design: docs/design/vOS_B31_eBPF_Race_Closure_Spec.md, Option (a))
#
# These 12 tests originally pinned a test-local reference model; they are now
# REPOINTED to the real ``KernelGateConnector`` API
# (``mark_push`` / ``is_marked`` / ``gc_entry`` / ``peek_entry`` /
# ``simulate_write``) landed in kernel_gate_connector.py. They exercise the
# host-side (MOCK-mode) enforcement: the per-fd MARK bit, the atomic
# mark+push commit, the marked-no-entry fail-closed ladder, and the
# ``max(colors)`` re-derivation that overrides caller metadata (G4).
#
# HONEST SCOPE (unchanged): this host is macOS — there is NO Linux eBPF LSM,
# so MOCK mode is the only path here and these tests verify the BRIDGE policy,
# not live kernel enforcement. Live LSM load + race-under-load needs a
# Linux >= 5.17 BPF-LSM runner (spec §7). Finding B3-1 stays PARTIAL (not
# "verified-closed") and the moat is UNCHANGED until that runner + an external
# audit exist (spec §8, phases 19.2-19.4).
# ===========================================================================

# Wire-format helpers + ABI constant now live in the production connector —
# re-export so the assertions below run against real code, not a test stub.
_pack_mark_push_arg = kgc._pack_mark_push_arg
_MARK_PUSH_HDR_FMT = kgc._MARK_PUSH_HDR_FMT
_MARK_PUSH_HDR_SIZE = kgc._MARK_PUSH_HDR_SIZE
_MARK_PUSH_SIZE = kgc._MARK_PUSH_SIZE
VOS3_TAINT_MARK_PUSH_ABI = kgc.VOS3_TAINT_MARK_PUSH_ABI


# ---------------------------------------------------------------------------
# B3-ATOMIC.1 — the single-syscall window: no partial / split push state
# ---------------------------------------------------------------------------


def test_b3_atomic1_arg_is_header_plus_unchanged_entry_struct():
    """B3-ATOMIC.1: the mark_push arg is a 16-byte header prepended to the
    UNCHANGED color-entry struct. The embedded value half MUST be byte-identical
    to a standalone ``kgc._pack_map_value`` (spec §6.1, invariant G5) — proving
    the atomic carrier reuses the existing wire contract with zero drift."""
    colors = bytes([0, 0, 2, 0])
    arg = _pack_mark_push_arg(
        fd=5, colors=colors, sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=int(TaintLabel.SECRET),
    )
    assert _MARK_PUSH_HDR_SIZE == 16
    assert len(arg) == _MARK_PUSH_SIZE == 16 + kgc._VALUE_SIZE == 65584
    standalone = kgc._pack_map_value(
        colors=colors, max_color=int(TaintLabel.SECRET),
        sink_kind=int(SinkKind.NETWORK_EGRESS),
        sink_max_label=int(TaintLabel.UNTRUSTED), mode=int(TaintMode.PER_FD),
        sha256_low=0, sha256_high=0, pushed_ns=0,
    )
    assert arg[_MARK_PUSH_HDR_SIZE:] == standalone  # byte-for-byte, G5
    abi, fd_field, _flags, pad = struct.unpack(
        _MARK_PUSH_HDR_FMT, arg[:_MARK_PUSH_HDR_SIZE]
    )
    assert (abi, fd_field, pad) == (VOS3_TAINT_MARK_PUSH_ABI, 5, 0)


def test_b3_atomic1_single_commit_sets_mark_and_entry_together():
    """B3-ATOMIC.1: one mark_push installs BOTH the MARK bit and the color
    entry; there is no API that sets one without the other → no split state."""
    c = _conn()
    arg = _pack_mark_push_arg(
        fd=5, colors=bytes([2]), sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=int(TaintLabel.SECRET),
    )
    assert not c.is_marked(pid=10, fd=5)
    c.mark_push(arg, pid=10)
    assert c.is_marked(pid=10, fd=5)
    assert c.peek_entry(pid=10, fd=5) is not None


def test_b3_atomic1_truncated_arg_commits_nothing():
    """B3-ATOMIC.1: a partial / split push is structurally impossible — the
    commit decodes header+entry from ONE contiguous arg, so a truncated buffer
    is rejected BEFORE any mark/entry is written; no half-state can leak."""
    c = _conn()
    arg = _pack_mark_push_arg(
        fd=5, colors=bytes([2]), sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=int(TaintLabel.SECRET),
    )
    with pytest.raises(ValueError):
        c.mark_push(arg[:-1], pid=10)  # one byte short → cannot commit the entry
    assert not c.is_marked(pid=10, fd=5)
    assert c.peek_entry(pid=10, fd=5) is None


def test_b3_atomic1_bad_abi_rejected_with_no_state():
    """B3-ATOMIC.1: an arg with an unrecognised ABI version is rejected and
    commits nothing (forward-compat guard; spec §3.2 ``abi_version``)."""
    c = _conn()
    arg = _pack_mark_push_arg(
        fd=5, colors=bytes([2]), sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=int(TaintLabel.SECRET),
        abi=VOS3_TAINT_MARK_PUSH_ABI + 1,
    )
    with pytest.raises(ValueError):
        c.mark_push(arg, pid=10)
    assert not c.is_marked(pid=10, fd=5)


# ---------------------------------------------------------------------------
# B3-MARKED-NO-ENTRY — the fail-closed ladder (closes "no entry → ALLOW")
# ---------------------------------------------------------------------------


def test_b3_marked_no_entry_fails_closed_with_eperm():
    """B3-MARKED-NO-ENTRY: an fd carrying the per-fd MARK bit whose color entry
    is absent (GC / TTL / tamper) MUST DENY with -EPERM — the direct closure of
    the historical 'no entry → default ALLOW' bypass (spec §4 / §5.2)."""
    c = _conn()
    arg = _pack_mark_push_arg(
        fd=5, colors=bytes([2]), sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=int(TaintLabel.SECRET),
    )
    c.mark_push(arg, pid=10)
    c.gc_entry(pid=10, fd=5)  # entry gone, fd STILL marked
    d = c.simulate_write(pid=10, fd=5, length=1)
    assert d.decision == DecisionKind.DENY
    assert d.errno == -13
    assert "fail-closed" in d.reason


def test_b3_marked_no_entry_inverts_the_live_no_entry_allow_bug():
    """Contrast lock: the LIVE connector still ALLOWs a never-pushed fd
    (Finding B3-1, still OPEN), whereas the proposed MARKED ladder DENies the
    same no-entry condition. Pins both the current ceiling and its closure so a
    regression on either side is caught."""
    # Live connector: no entry → ALLOW (the open finding, unchanged today).
    live = _conn()
    assert (
        live.simulate_write(fd=999, pid=999, length=4).decision == DecisionKind.ALLOW
    )
    # Model: MARKED + no entry → DENY (what the Sprint-19 fix must do).
    c = _conn()
    arg = _pack_mark_push_arg(
        fd=999, colors=bytes([2]), sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=int(TaintLabel.SECRET),
    )
    c.mark_push(arg, pid=999)
    c.gc_entry(pid=999, fd=999)
    assert c.simulate_write(pid=999, fd=999, length=4).decision == DecisionKind.DENY


def test_b3_unmarked_fd_allows_so_untainted_io_is_unbroken():
    """G3: an unmarked fd is outside the taint domain → ALLOW, so ordinary
    untainted I/O is never gated. (This is NOT the B3-1 hole — such fds never
    carried a TaintedBuffer.)"""
    c = _conn()
    d = c.simulate_write(pid=10, fd=42, length=8)
    assert d.decision == DecisionKind.ALLOW
    assert d.errno == 0


def test_b3_marked_with_toxic_entry_still_denies():
    """Enforcement on the populated path is intact: marked + entry whose
    recomputed max exceeds the sink ceiling → DENY."""
    c = _conn()
    arg = _pack_mark_push_arg(
        fd=5, colors=bytes([0, 0, 2, 0]), sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=int(TaintLabel.SECRET),
    )
    c.mark_push(arg, pid=10)
    assert c.simulate_write(pid=10, fd=5).decision == DecisionKind.DENY


def test_b3_marked_with_clean_entry_allows():
    """Marked + within-ceiling entry → ALLOW (no false positive)."""
    c = _conn()
    arg = _pack_mark_push_arg(
        fd=5, colors=bytes([0, 0, 0]), sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=int(TaintLabel.PUBLIC),
    )
    c.mark_push(arg, pid=10)
    assert c.simulate_write(pid=10, fd=5).decision == DecisionKind.ALLOW


# ---------------------------------------------------------------------------
# B3-MAX-COLOR-OVERRIDE — re-derive max(colors), never trust caller metadata
# ---------------------------------------------------------------------------


def test_b3_max_color_override_re_derives_and_overrides_arg_lie():
    """B3-MAX-COLOR-OVERRIDE: a compromised caller packs claimed_max_color=PUBLIC
    while colors[] actually carry a SECRET byte. The commit re-derives
    max(colors[0:length]) and OVERRIDES the lie (spec §6.2; invariant G4 at
    kernel_gate_connector.py:673 / :702) → the egress is DENied."""
    c = _conn()
    arg = _pack_mark_push_arg(
        fd=5, colors=bytes([0, 0, 2, 0]),          # real max = SECRET(2)
        sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=int(TaintLabel.PUBLIC),  # the LIE
    )
    c.mark_push(arg, pid=10)
    entry = c.peek_entry(pid=10, fd=5)
    assert entry["claimed_max"] == int(TaintLabel.PUBLIC)  # what the caller said
    assert entry["max_color"] == int(TaintLabel.SECRET)    # what the kernel re-derived
    assert c.simulate_write(pid=10, fd=5).decision == DecisionKind.DENY


def test_b3_max_color_override_matches_live_connector_recompute():
    """The model's re-derived max MUST equal the LIVE connector's recompute for
    the same colors — anchoring the contract to the real _push_one path
    (kernel_gate_connector.py:673) rather than an invented number."""
    colors = bytes([0, 1, 2, 0])  # real max = SECRET(2)
    # Live: push a buffer that LIES (claimed_max=PUBLIC) → simulate_write DENies.
    live = _conn()
    live.push_tainted_buffer(
        fd=5, pid=10,
        buffer=_buf(colors, claimed_max=int(TaintLabel.PUBLIC)),
        sink_kind=SinkKind.NETWORK_EGRESS,
    )
    assert live.simulate_write(fd=5, pid=10).decision == DecisionKind.DENY
    # Model: same colors, same lie → re-derived max equals the real max(colors).
    c = _conn()
    arg = _pack_mark_push_arg(
        fd=5, colors=colors, sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=int(TaintLabel.PUBLIC),
    )
    c.mark_push(arg, pid=10)
    assert c.peek_entry(pid=10, fd=5)["max_color"] == max(colors)


def test_b3_max_color_override_honest_arg_is_unchanged():
    """When the caller is honest (claimed == real) the re-derivation is a no-op:
    the entry's max_color is unchanged and a within-ceiling write ALLOWs."""
    c = _conn()
    arg = _pack_mark_push_arg(
        fd=5, colors=bytes([0, 1, 1]),                # real max = UNTRUSTED(1)
        sink_kind=SinkKind.NETWORK_EGRESS,
        sink_max_label=TaintLabel.UNTRUSTED,
        claimed_max_color=int(TaintLabel.UNTRUSTED),  # honest
    )
    c.mark_push(arg, pid=10)
    assert c.peek_entry(pid=10, fd=5)["max_color"] == int(TaintLabel.UNTRUSTED)
    assert c.simulate_write(pid=10, fd=5).decision == DecisionKind.ALLOW
