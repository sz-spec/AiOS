"""
backend/tests/security/test_v13_production_lockdown.py
=====================================================

V1.3 production lockdown — 100-test mathematical stress matrix across the
Sprint 19–23 security core. Pure logic, deterministic, 100% on macOS via
injected seams (no Linux/eBPF/GPU). Exactly 100 collected test items in 5
sections of 20:

  S1  PII / secret-leakage permutations         (O3 + C7)
  S2  cryptographic nonce & replay stressors    (D5 nonce gate + BBS + F4 bridge)
  S3  model-memory integrity bit-flips          (Sprint 23 watchdog)
  S4  history multi-step policy violations       (Sprint 23 history validator)
  S5  driver-gate fail-closed permutations       (E1 + E2 + O4)

Honesty notes:
  - This is QA/stabilization only. Moat stays 49/80 — no new rows.
  - O3's `israeli_id` branch is unreachable behind CC_RE (a latent O3
    detail, out of scope here), so no test asserts israeli-ID detection.
  - Every assertion targets REAL module behaviour; nothing is mocked
    beyond the documented injection seams (memory_source, clocks,
    sysfs/proc roots, in-memory bundle transport).
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# -- Sprint 19 / O3 + C7 ----------------------------------------------------
from security.outbound_pii_shield import (  # noqa: E402
    EgressMode,
    OutboundPiiBlocked,
    OutboundPiiShield,
    PiiKind,
    ReleaseContext,
)
from security.taint_engine_v2 import (  # noqa: E402
    ByteTaintEngine,
    EgressDecisionKind,
    SinkKind,
    TaintLabel,
)

# -- Sprint 20 / BBS + D5 nonce gate + F4 bridge ----------------------------
from services.bbs_selective_disclosure import (  # noqa: E402
    BbsHolder,
    BbsIssuer,
    BbsVerifier,
    DerivedProof,
    build_pii_statements,
    generate_keypair,
)
from services.attestation_nonce_gate import (  # noqa: E402
    NonceAudienceMismatchError,
    NonceBindingMismatchError,
    NonceExpiredError,
    NonceGate,
    NonceMissingError,
)
from services.identity_federation_bridge import (  # noqa: E402
    BundleProfile,
    IdentityFederationBridge,
    InMemoryBundleTransport,
    RefreshOutcomeKind,
)

# -- Sprint 21 / driver gates -----------------------------------------------
from security.gpu_driver_gate import (  # noqa: E402
    DriverState,
    ENV_PINNED_VERSION,
    GpuDriverGate,
    UntrustedGpuDriver,
)
from security.accel_ioctl_filter import (  # noqa: E402
    AccelDevice,
    AccelIoctlFilter,
    IoctlBlocked,
)
from security.gpu_alloc_validator import (  # noqa: E402
    AttestationQuote,
    GpuAllocRefused,
    GpuAllocRequest,
    GpuAllocValidator,
)

# -- Sprint 23 / DEPTH ------------------------------------------------------
from security.model_integrity_watchdog import (  # noqa: E402
    ModelIntegrityCompromised,
    ModelIntegrityWatchdog,
)
from core.security.history_validator import (  # noqa: E402
    AgentExecutionHistoryValidator,
    ContextViolation,
    Operation,
    PredicateRule,
    RateCeilingRule,
    ToolLoopRule,
)

# ===========================================================================
# Shared helpers
# ===========================================================================


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def time(self):
        return self.now


def _quote_for(nonce: str) -> bytes:
    """A stand-in attestation quote whose last 32 bytes = SHA-256(nonce)."""
    return b"vos3-reportdata-prefix" + hashlib.sha256(nonce.encode()).digest()


def _bundle_json(td: str, seq: int) -> bytes:
    return json.dumps(
        {
            "trust_domain": td,
            "spiffe_sequence": seq,
            "spiffe_refresh_hint": 300,
            "keys": [{"kty": "OKP", "crv": "Ed25519", "kid": f"k{seq}", "x": "dGVzdA"}],
        }
    ).encode("utf-8")


class _FakeFedVerifier:
    def __init__(self):
        self.registrations = []
        self.unregistrations = []

    def register_federated_domain(self, trust_domain, *a, **k):
        self.registrations.append(trust_domain)

    def unregister_federated_domain(self, trust_domain):
        self.unregistrations.append(trust_domain)

    def list_federated_domains(self):
        return list({d for d in self.registrations})

    def verify(self, jwt_token):
        return "ok"


# ===========================================================================
# SECTION 1 — PII / secret-leakage permutations (20)
# ===========================================================================

EXTERNAL = "https://partner.example/ingest"
TRUSTED = "internal.corp"

_PII_PAYLOADS = {
    "email": "record: alice@aidg.com placed an order",
    "phone": "callback +12025550123 requested",
    "ssn": "applicant ssn 123-45-6789 pending",
    "cc": "charge card 4242424242424242 settled",
    "multi": "alice@aidg.com ssn 123-45-6789 combined",
}


@pytest.mark.parametrize("key", list(_PII_PAYLOADS.keys()))
def test_s1_structured_pii_blocked_to_untrusted(key):
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    with pytest.raises(OutboundPiiBlocked):
        shield.require_clean_egress(_PII_PAYLOADS[key], EXTERNAL)


@pytest.mark.parametrize("key", list(_PII_PAYLOADS.keys()))
def test_s1_pii_allowed_to_trusted_dest(key):
    shield = OutboundPiiShield(trusted_destinations=frozenset({TRUSTED}))
    decision = shield.require_clean_egress(
        _PII_PAYLOADS[key], f"https://{TRUSTED}/sink"
    )
    assert decision.allowed and decision.trusted_destination


_TAINT_CASES = [
    (TaintLabel.SECRET, SinkKind.NETWORK_EGRESS, EgressDecisionKind.DENY),
    (TaintLabel.UNTRUSTED, SinkKind.NETWORK_EGRESS, EgressDecisionKind.ALLOW),
    (TaintLabel.TOXIC, SinkKind.NETWORK_EGRESS, EgressDecisionKind.DENY),
    (TaintLabel.SECRET, SinkKind.FILE_WRITE, EgressDecisionKind.ALLOW),
    (TaintLabel.TOXIC, SinkKind.AUDIT_LOG, EgressDecisionKind.ALLOW),
]


@pytest.mark.parametrize("label,sink,expect", _TAINT_CASES)
def test_s1_taint_color_egress_decision(label, sink, expect):
    eng = ByteTaintEngine()
    buf = eng.label_source(source_id="s1", content=b"x" * 64, label=label)
    decision = eng.check_egress(buf, sink)
    assert decision.kind == expect


def test_s1_release_context_clears_matching_kind():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    ctx = ReleaseContext(
        reviewed=True,
        cleared_kinds=frozenset({PiiKind.EMAIL}),
        reviewer="sec@corp",
        justification="approved",
    )
    d = shield.require_clean_egress(_PII_PAYLOADS["email"], EXTERNAL, context=ctx)
    assert d.allowed and d.released_by_context


def test_s1_release_context_wrong_kind_still_blocks():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    ctx = ReleaseContext(
        reviewed=True, cleared_kinds=frozenset({PiiKind.PHONE}), reviewer="sec@corp"
    )
    with pytest.raises(OutboundPiiBlocked):
        shield.require_clean_egress(_PII_PAYLOADS["email"], EXTERNAL, context=ctx)


def test_s1_audit_mode_allows_but_flags():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    d = shield.inspect(_PII_PAYLOADS["ssn"], EXTERNAL, mode=EgressMode.AUDIT)
    assert d.allowed and d.has_pii


def test_s1_redact_mode_masks_pii():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    d = shield.inspect(_PII_PAYLOADS["email"], EXTERNAL, mode=EgressMode.REDACT)
    assert d.allowed and "alice@aidg.com" not in d.effective_payload(
        _PII_PAYLOADS["email"]
    )


def test_s1_clean_payload_allowed_to_untrusted():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    d = shield.require_clean_egress("build complete; 0 warnings", EXTERNAL)
    assert d.allowed and not d.has_pii


# ===========================================================================
# SECTION 2 — cryptographic nonce & replay stressors (20)
# ===========================================================================


def test_s2_nonce_valid_quote_verifies():
    gate = NonceGate()
    n = gate.issue_nonce("aud-1")
    vq = gate.verify_quote(_quote_for(n), n, "aud-1")
    assert vq.nonce == n


def test_s2_nonce_replay_blocked():
    gate = NonceGate()
    n = gate.issue_nonce("aud-1")
    gate.verify_quote(_quote_for(n), n, "aud-1")
    with pytest.raises(NonceMissingError):
        gate.verify_quote(_quote_for(n), n, "aud-1")


def test_s2_nonce_expired_blocked():
    gate = NonceGate()
    gate._now = lambda: 1_000.0
    n = gate.issue_nonce("aud-1", ttl_seconds=30)
    gate._now = lambda: 1_031.0  # past TTL
    with pytest.raises(NonceExpiredError):
        gate.verify_quote(_quote_for(n), n, "aud-1")


def test_s2_nonce_audience_mismatch_blocked():
    gate = NonceGate()
    n = gate.issue_nonce("aud-1")
    with pytest.raises(NonceAudienceMismatchError):
        gate.verify_quote(_quote_for(n), n, "aud-2")


def test_s2_nonce_binding_mismatch_blocked():
    gate = NonceGate()
    n = gate.issue_nonce("aud-1")
    bad = b"x" * 16 + hashlib.sha256(b"different").digest()
    with pytest.raises(NonceBindingMismatchError):
        gate.verify_quote(bad, n, "aud-1")


def test_s2_nonce_unknown_blocked():
    gate = NonceGate()
    with pytest.raises(NonceMissingError):
        gate.verify_quote(_quote_for("deadbeef"), "deadbeef", "aud-1")


@pytest.mark.parametrize("i", range(4))
def test_s2_nonce_flood_each_single_use(i):
    """Flood: issue many, verify each once OK, replay each → blocked."""
    gate = NonceGate()
    nonces = [gate.issue_nonce(f"aud-{i}") for _ in range(8)]
    for n in nonces:
        gate.verify_quote(_quote_for(n), n, f"aud-{i}")
    for n in nonces:
        with pytest.raises(NonceMissingError):
            gate.verify_quote(_quote_for(n), n, f"aud-{i}")


def _bbs_setup(clock=None, kinds=("email",), td="spiffe://verifier"):
    issuer = BbsIssuer.generate("spiffe://issuer")
    holder = BbsHolder.generate(clock=clock)
    statements, mandatory = build_pii_statements(
        trust_domain=td, epoch=1, cleared_kinds=kinds
    )
    base = issuer.sign(
        statements, holder.public_bytes, mandatory_indices=mandatory, nonce_seed=b"seed"
    )
    return issuer, holder, base, td


def test_s2_bbs_valid_proof_verifies():
    clock = FakeClock()
    issuer, holder, base, td = _bbs_setup(clock)
    idx = base.statements.index("pii-cleared:email")
    derived = holder.prove(base, reveal=[idx], verifier_id=td)
    assert BbsVerifier(clock=clock).verify(derived, issuer.public_bytes).ok


def test_s2_bbs_expired_proof_rejected():
    clock = FakeClock()
    issuer, holder, base, td = _bbs_setup(clock)
    derived = holder.prove(base, reveal=[], verifier_id=td, ttl_seconds=30)
    clock.advance = None
    clock.now += 31
    assert not BbsVerifier(clock=clock).verify(derived, issuer.public_bytes).ok


def test_s2_bbs_holder_forgery_rejected():
    issuer, holder, base, td = _bbs_setup()
    derived = holder.prove(base, reveal=[], verifier_id=td)
    forged = DerivedProof(**{**derived.__dict__, "holder_signature": b"\x00" * 64})
    assert not BbsVerifier().verify(forged, issuer.public_bytes).ok


def test_s2_bbs_wrong_issuer_rejected():
    issuer, holder, base, td = _bbs_setup()
    derived = holder.prove(base, reveal=[], verifier_id=td)
    _, other_pub = generate_keypair()
    assert not BbsVerifier().verify(derived, other_pub).ok


def test_s2_bbs_pseudonyms_unlinkable_across_verifiers():
    _, holder, base, _ = _bbs_setup()
    a = holder.prove(base, reveal=[], verifier_id="verifier-A")
    b = holder.prove(base, reveal=[], verifier_id="verifier-B")
    assert a.pseudonym != b.pseudonym


def _fed():
    transport = InMemoryBundleTransport()
    verifier = _FakeFedVerifier()
    bridge = IdentityFederationBridge(
        local_trust_domain="spiffe://local",
        federation_verifier=verifier,
        transport=transport,
    )
    return bridge, transport, verifier


_FED_TD = "spiffe://partner.example.com"
_FED_URL = "https://partner.example.com/bundle"


def test_s2_fed_success_new():
    bridge, transport, _ = _fed()
    transport.seed(_FED_URL, _bundle_json(_FED_TD, 1))
    bridge.register_partner(trust_domain=_FED_TD, bundle_endpoint_url=_FED_URL)
    out = bridge.refresh_partner(_FED_TD)
    assert out.kind == RefreshOutcomeKind.SUCCESS_NEW


def test_s2_fed_unchanged_on_same_sequence():
    bridge, transport, _ = _fed()
    transport.seed(_FED_URL, _bundle_json(_FED_TD, 1))
    bridge.register_partner(trust_domain=_FED_TD, bundle_endpoint_url=_FED_URL)
    bridge.refresh_partner(_FED_TD)
    out = bridge.refresh_partner(_FED_TD)
    assert out.kind == RefreshOutcomeKind.SUCCESS_UNCHANGED


def test_s2_fed_sequence_regression_rejected():
    bridge, transport, _ = _fed()
    transport.seed_sequence(
        _FED_URL, [_bundle_json(_FED_TD, 2), _bundle_json(_FED_TD, 1)]
    )
    bridge.register_partner(trust_domain=_FED_TD, bundle_endpoint_url=_FED_URL)
    bridge.refresh_partner(_FED_TD)
    out = bridge.refresh_partner(_FED_TD)
    assert out.kind == RefreshOutcomeKind.REGRESSION


def test_s2_fed_force_rollback_accepts_regression():
    bridge, transport, _ = _fed()
    transport.seed_sequence(
        _FED_URL, [_bundle_json(_FED_TD, 2), _bundle_json(_FED_TD, 1)]
    )
    bridge.register_partner(trust_domain=_FED_TD, bundle_endpoint_url=_FED_URL)
    bridge.refresh_partner(_FED_TD)
    out = bridge.refresh_partner(_FED_TD, force_rollback=True)
    assert out.kind == RefreshOutcomeKind.SUCCESS_NEW


def test_s2_fed_spiffe_without_anchor_refused():
    bridge, _, _ = _fed()
    with pytest.raises(ValueError):
        bridge.register_partner(
            trust_domain=_FED_TD,
            bundle_endpoint_url=_FED_URL,
            profile=BundleProfile.HTTPS_SPIFFE,
        )


# ===========================================================================
# SECTION 3 — model-memory integrity bit-flips (20)
# ===========================================================================


@pytest.mark.parametrize("offset", [0, 1, 100, 1000, 2047, 4095, 8191, 9999])
def test_s3_single_bit_flip_evicts_and_raises(offset):
    data = bytearray(b"\x00" * 10000)
    src = {("m", "blk"): bytes(data)}
    wd = ModelIntegrityWatchdog(memory_source=src)
    wd.register_region(model_id="m", layer_id="blk", baseline=src[("m", "blk")])
    data[offset] ^= 0x01
    src[("m", "blk")] = bytes(data)
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")
    assert not wd.is_registered("m", "blk")


@pytest.mark.parametrize("victim", ["a", "b", "c", "d", "e"])
def test_s3_multi_region_only_victim_evicted(victim):
    src = {("m", k): bytes([ord(k)]) * 512 for k in "abcde"}
    wd = ModelIntegrityWatchdog(memory_source=src)
    for k in "abcde":
        wd.register_region(model_id="m", layer_id=k, baseline=src[("m", k)])
    src[("m", victim)] = b"\xff" * 512
    with pytest.raises(ModelIntegrityCompromised) as exc:
        wd.scan("m")
    assert {r.layer_id for r in exc.value.reports} == {victim}
    assert not wd.is_registered("m", victim)
    for k in "abcde":
        if k != victim:
            assert wd.is_registered("m", k)


def test_s3_concurrent_scans_clean_no_false_drift():
    src = {("m", f"l{i}"): bytes([i]) * 256 for i in range(8)}
    wd = ModelIntegrityWatchdog(memory_source=src)
    for i in range(8):
        wd.register_region(model_id="m", layer_id=f"l{i}", baseline=src[("m", f"l{i}")])
    errors = []

    def run():
        try:
            wd.scan("m")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert wd.stats.scans >= 12


def test_s3_concurrent_scan_with_drift_evicts_under_contention():
    src = {("m", "l0"): b"\x00" * 256, ("m", "l1"): b"\x11" * 256}
    wd = ModelIntegrityWatchdog(memory_source=src)
    wd.register_region(model_id="m", layer_id="l0", baseline=src[("m", "l0")])
    wd.register_region(model_id="m", layer_id="l1", baseline=src[("m", "l1")])
    src[("m", "l1")] = b"\x22" * 256
    raised = []

    def run():
        try:
            wd.scan("m")
        except ModelIntegrityCompromised:
            raised.append(1)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert raised  # at least one scan caught the drift
    assert not wd.is_registered("m", "l1")
    assert wd.is_registered("m", "l0")


def test_s3_dev_override_downgrades(monkeypatch):
    monkeypatch.setenv("VOS3_MODEL_INTEGRITY_DEV_OVERRIDE", "1")
    data = bytearray(b"\x00" * 128)
    src = {("m", "l"): bytes(data)}
    wd = ModelIntegrityWatchdog(memory_source=src)
    wd.register_region(model_id="m", layer_id="l", baseline=src[("m", "l")])
    data[0] ^= 0x01
    src[("m", "l")] = bytes(data)
    wd.scan("m")  # does not raise under override
    assert wd.is_registered("m", "l")


def test_s3_unavailable_region_failclosed():
    src = {("m", "l"): b"abc"}
    wd = ModelIntegrityWatchdog(memory_source=src)
    wd.register_region(model_id="m", layer_id="l", baseline=b"abc")
    del src[("m", "l")]
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")


def test_s3_require_integrity_unregistered_errors():
    wd = ModelIntegrityWatchdog(memory_source={})
    with pytest.raises(ValueError):
        wd.require_integrity("ghost")


def test_s3_tick_scans_all_models():
    src = {("m1", "a"): b"1", ("m2", "a"): b"2"}
    wd = ModelIntegrityWatchdog(memory_source=src)
    wd.register_region(model_id="m1", layer_id="a", baseline=b"1")
    wd.register_region(model_id="m2", layer_id="a", baseline=b"2")
    reports = wd.tick()
    assert {r.model_id for r in reports} == {"m1", "m2"}


def test_s3_callable_source_drift_detected():
    store = {("m", "a"): b"hello"}
    wd = ModelIntegrityWatchdog(memory_source=lambda mid, lid: store.get((mid, lid)))
    wd.register_region(model_id="m", layer_id="a", baseline=b"hello")
    store[("m", "a")] = b"world"
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")


# ===========================================================================
# SECTION 4 — history multi-step policy violations (20)
# ===========================================================================

_FORBIDDEN = [
    ("SECRET", "net.send"),
    ("SECRET", "http.post"),
    ("SECRET", "http.put"),
    ("TOXIC", "net.send"),
    ("TOXIC", "webhook"),
    ("SECRET", "email.send"),
]


@pytest.mark.parametrize("label,tool", _FORBIDDEN)
def test_s4_secret_then_egress_blocked(label, tool):
    v = AgentExecutionHistoryValidator()
    v.require_operation("a", Operation(tool="fs.read", labels={label}))
    with pytest.raises(ContextViolation):
        v.require_operation("a", Operation(tool=tool, destination="https://evil/"))


_BENIGN = [
    ("PUBLIC", "net.send"),
    ("UNTRUSTED", "compute"),
    ("PUBLIC", "fs.read"),
    ("UNTRUSTED", "http.post"),
]


@pytest.mark.parametrize("label,tool", _BENIGN)
def test_s4_benign_sequence_allowed(label, tool):
    v = AgentExecutionHistoryValidator()
    v.require_operation("a", Operation(tool="fs.read", labels={label}))
    out = v.require_operation("a", Operation(tool=tool, destination="https://x/"))
    assert out.tool == tool


@pytest.mark.parametrize("dest", ["internal.corp", "vault.internal"])
def test_s4_secret_to_trusted_dest_allowed(dest):
    v = AgentExecutionHistoryValidator(trusted_destinations=frozenset({dest}))
    v.require_operation("a", Operation(tool="fs.read", labels={"SECRET"}))
    out = v.require_operation(
        "a", Operation(tool="net.send", destination=f"https://{dest}/x")
    )
    assert out.tool == "net.send"


def test_s4_ring_buffer_eviction_frees_gate():
    v = AgentExecutionHistoryValidator(ring_size=3)
    v.require_operation("a", Operation(tool="fs.read", labels={"SECRET"}))
    for _ in range(3):
        v.require_operation("a", Operation(tool="compute"))
    out = v.require_operation(
        "a", Operation(tool="net.send", destination="https://evil/")
    )
    assert out.tool == "net.send"


def test_s4_per_agent_isolation():
    v = AgentExecutionHistoryValidator()
    v.require_operation("a", Operation(tool="fs.read", labels={"SECRET"}))
    out = v.require_operation("b", Operation(tool="net.send", destination="https://x/"))
    assert out.tool == "net.send"


def test_s4_loop_rule_fires():
    v = AgentExecutionHistoryValidator(policy=(ToolLoopRule(max_repeats=3),))
    for _ in range(3):
        v.require_operation("a", Operation(tool="poll", destination="d"))
    with pytest.raises(ContextViolation):
        v.require_operation("a", Operation(tool="poll", destination="d"))


def test_s4_rate_ceiling_fires():
    v = AgentExecutionHistoryValidator(policy=(RateCeilingRule(max_ops=5),))
    for _ in range(5):
        v.require_operation("a", Operation(tool="op"))
    with pytest.raises(ContextViolation):
        v.require_operation("a", Operation(tool="op"))


def test_s4_window_expiry_lets_egress_through():
    clock = FakeClock()
    v = AgentExecutionHistoryValidator(window_seconds=60.0, clock=clock)
    v.require_operation("a", Operation(tool="fs.read", labels={"SECRET"}))
    clock.now += 61
    out = v.require_operation(
        "a", Operation(tool="net.send", destination="https://evil/")
    )
    assert out.tool == "net.send"


def test_s4_window_within_still_blocks():
    clock = FakeClock()
    v = AgentExecutionHistoryValidator(window_seconds=60.0, clock=clock)
    v.require_operation("a", Operation(tool="fs.read", labels={"SECRET"}))
    clock.now += 30
    with pytest.raises(ContextViolation):
        v.require_operation(
            "a", Operation(tool="net.send", destination="https://evil/")
        )


def test_s4_custom_predicate_rule_blocks():
    def no_delete(ctx):
        return "no deletes" if ctx.pending.tool == "fs.delete" else None

    v = AgentExecutionHistoryValidator(policy=(PredicateRule("no-delete", no_delete),))
    with pytest.raises(ContextViolation):
        v.require_operation("a", Operation(tool="fs.delete"))


def test_s4_input_validation():
    v = AgentExecutionHistoryValidator()
    with pytest.raises(ValueError):
        v.require_operation("", Operation(tool="x"))
    with pytest.raises(TypeError):
        v.require_operation("a", "not-an-operation")


# ===========================================================================
# SECTION 5 — driver-gate fail-closed permutations (20)
# ===========================================================================


def _module_tree(tmp_path, *, taint="", version="550.90.07"):
    root = tmp_path / "nvidia"
    root.mkdir()
    (root / "taint").write_text(taint + "\n")
    (root / "version").write_text(version + "\n")
    return str(root)


@pytest.mark.parametrize(
    "taint,state,allowed",
    [
        ("O", DriverState.OPEN_SIGNED, True),
        ("PO", DriverState.PROPRIETARY, False),
        ("E", DriverState.UNSIGNED, False),
    ],
)
def test_s5_driver_taint_states(tmp_path, taint, state, allowed):
    gate = GpuDriverGate(module_root=_module_tree(tmp_path, taint=taint))
    assert gate.probe().state == state
    if allowed:
        assert gate.require_trusted_driver_for_gpu_bind().state == state
    else:
        with pytest.raises(UntrustedGpuDriver):
            gate.require_trusted_driver_for_gpu_bind()


def test_s5_driver_absent_refused(tmp_path):
    gate = GpuDriverGate(module_root=str(tmp_path / "missing"))
    with pytest.raises(UntrustedGpuDriver):
        gate.require_trusted_driver_for_gpu_bind()


def test_s5_driver_version_mismatch_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_PINNED_VERSION, "999.0.0")
    gate = GpuDriverGate(module_root=_module_tree(tmp_path, taint="O"))
    with pytest.raises(UntrustedGpuDriver):
        gate.require_trusted_driver_for_gpu_bind()


def test_s5_driver_version_match_allows(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_PINNED_VERSION, "550.90.07")
    gate = GpuDriverGate(module_root=_module_tree(tmp_path, taint="O"))
    assert gate.require_trusted_driver_for_gpu_bind().state == DriverState.OPEN_SIGNED


def test_s5_driver_dev_override(tmp_path, monkeypatch):
    monkeypatch.setenv("VOS3_GPU_DRIVER_DEV_OVERRIDE", "1")
    gate = GpuDriverGate(module_root=_module_tree(tmp_path, taint="P"))
    assert gate.require_trusted_driver_for_gpu_bind().state == DriverState.PROPRIETARY
    assert gate.stats.dev_overrides_used == 1


def _ioc(type_: int, nr: int, size: int = 0) -> int:
    return (size << 16) | (type_ << 8) | nr


_ALLOWED_IOCTLS = [
    (AccelDevice.NVIDIA_CTL, ord("F"), 0x2A),
    (AccelDevice.NVIDIA_GPU, ord("F"), 0x21),
    (AccelDevice.NVIDIA_UVM, 0x0, 0x1),
    (AccelDevice.AMD_KFD, ord("K"), 0x01),
]


@pytest.mark.parametrize("device,t,nr", _ALLOWED_IOCTLS)
def test_s5_ioctl_allowlisted_passes(device, t, nr):
    filt = AccelIoctlFilter(device=device)
    assert filt.require_ioctl(_ioc(t, nr, size=64)).key == (t, nr)


def test_s5_ioctl_non_allowlisted_refused():
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    with pytest.raises(IoctlBlocked):
        filt.require_ioctl(_ioc(ord("F"), 0xDE))


def test_s5_ioctl_dev_override(monkeypatch):
    monkeypatch.setenv("VOS3_ACCEL_IOCTL_DEV_OVERRIDE", "1")
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    assert filt.require_ioctl(_ioc(ord("F"), 0xDE)).nr == 0xDE


def test_s5_ioctl_size_independent_match():
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL)
    assert filt.is_allowed(_ioc(ord("F"), 0x2A, size=4096))


def test_s5_ioctl_empty_allowlist_denies():
    filt = AccelIoctlFilter(device=AccelDevice.NVIDIA_CTL, allowlist=frozenset())
    with pytest.raises(IoctlBlocked):
        filt.require_ioctl(_ioc(ord("F"), 0x2A))


_MIG_UUID = "MIG-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _alloc_host(tmp_path, mig="1", cc="on"):
    proc = tmp_path / "proc"
    (proc / "driver" / "nvidia" / "gpus" / "0000:01:00.0").mkdir(parents=True)
    (proc / "driver" / "nvidia" / "gpus" / "0000:01:00.0" / "mig_mode").write_text(mig)
    sysfs = tmp_path / "sys"
    (sysfs / "class" / "nvidia" / "nvidia0").mkdir(parents=True)
    (sysfs / "class" / "nvidia" / "nvidia0" / "cc_mode").write_text(cc)
    return str(proc), str(sysfs)


def _alloc_provider(clock, age=10.0, uuid=_MIG_UUID, verified=True):
    return lambda u: (
        AttestationQuote(mig_uuid=uuid, issued_at=clock.now - age, verified=verified)
        if u == uuid
        else None
    )


def _good_alloc(shared=True):
    return GpuAllocRequest(
        gpu_index=0,
        mig_uuid=_MIG_UUID,
        shared_host=shared,
        cgroup_mem_limit_bytes=8 << 30,
        cgroup_sm_limit=14,
    )


def test_s5_alloc_all_conditions_met_allows(tmp_path):
    clock = FakeClock()
    proc, sysfs = _alloc_host(tmp_path)
    v = GpuAllocValidator(
        attestation_provider=_alloc_provider(clock),
        proc_root=proc,
        sysfs_root=sysfs,
        clock=clock,
    )
    assert v.require_isolated_gpu_alloc(_good_alloc()).safe_for_alloc


def test_s5_alloc_mig_off_refused(tmp_path):
    clock = FakeClock()
    proc, sysfs = _alloc_host(tmp_path, mig="0")
    v = GpuAllocValidator(
        attestation_provider=_alloc_provider(clock),
        proc_root=proc,
        sysfs_root=sysfs,
        clock=clock,
    )
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(_good_alloc())


def test_s5_alloc_cc_off_refused(tmp_path):
    clock = FakeClock()
    proc, sysfs = _alloc_host(tmp_path, cc="off")
    v = GpuAllocValidator(
        attestation_provider=_alloc_provider(clock),
        proc_root=proc,
        sysfs_root=sysfs,
        clock=clock,
    )
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(_good_alloc())


def test_s5_alloc_stale_attestation_refused(tmp_path):
    clock = FakeClock()
    proc, sysfs = _alloc_host(tmp_path)
    v = GpuAllocValidator(
        attestation_provider=_alloc_provider(clock, age=10_000.0),
        proc_root=proc,
        sysfs_root=sysfs,
        clock=clock,
    )
    with pytest.raises(GpuAllocRefused):
        v.require_isolated_gpu_alloc(_good_alloc())


def test_s5_alloc_dedicated_host_allowed(tmp_path):
    v = GpuAllocValidator()
    assert v.require_isolated_gpu_alloc(_good_alloc(shared=False)).safe_for_alloc
