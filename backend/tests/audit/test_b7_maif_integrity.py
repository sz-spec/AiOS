"""
B7 — MAIF provenance + runtime model-integrity watchdog (TEST_PLAN_300 §B7)
===========================================================================

Adversarial sweep over the two B7 integrity layers:

  * ``services/maif_v2.py`` — MAIF v2 provenance hash-chain + SLOT_START gate
    (load-time provenance / anti-swap, Sprint 22 / I2).
  * ``security/model_integrity_watchdog.py`` — runtime re-checksum watchdog
    that evicts + raises on resident-weight bit-drift (Sprint 23 DEPTH).

Adversarial scenarios:
  * runtime weight tampering → watchdog evicts + raises instantly;
  * mismatched checksum / unavailable bytes → fail-closed (treated as drift);
  * model hot-swap (same length, different weights) → detected;
  * broken / unsigned / untrusted-builder / poisoned-dataset provenance →
    SLOT_START refused;
  * head-artifact swap → refused;
  * FINDING B7-1: an empty model_sha256 skips the anti-swap binding.

Run:
    .venv_p312/bin/python -m pytest tests/audit/test_b7_maif_integrity.py -v
"""
from __future__ import annotations

import dataclasses
import hashlib
import threading

import pytest

from security.model_integrity_watchdog import (
    ModelIntegrityCompromised,
    ModelIntegrityWatchdog,
    RegionState,
)
from services.maif_v2 import (
    ModelProvenance,
    ProvenanceGate,
    ProvenanceStep,
    SlotStartPolicy,
    SlotStartRefused,
    build_provenance,
)


def _h(s: bytes) -> str:
    return hashlib.sha256(s).hexdigest()


def _keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv, pub


def _chain(*, builder_private=None, builder_public=b""):
    """A clean 2-link chain: pretrain(base/data1) → finetune(ft/data2)."""
    return build_provenance(
        "llama-4-ft-corp",
        [
            (ProvenanceStep.PRETRAIN, _h(b"base"), _h(b"data1"), "base"),
            (ProvenanceStep.FINETUNE, _h(b"ft"), _h(b"data2"), "ft"),
        ],
        builder_private=builder_private,
        builder_public=builder_public,
    )


# ===========================================================================
# B7a — MAIF v2 SLOT_START gate
# ===========================================================================


def test_b7_valid_chain_matching_model_allowed():
    gate = ProvenanceGate(policy=SlotStartPolicy(require_provenance=True))
    prov = _chain()
    out = gate.require_slot_start(
        model_id="llama-4-ft-corp", model_sha256=_h(b"ft"), provenance=prov)
    assert out is prov


def test_b7_missing_provenance_refused_when_required():
    gate = ProvenanceGate(policy=SlotStartPolicy(require_provenance=True))
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(model_id="m", model_sha256=_h(b"ft"), provenance=None)


def test_b7_missing_provenance_allowed_when_not_required():
    gate = ProvenanceGate(policy=SlotStartPolicy(require_provenance=False))
    out = gate.require_slot_start(model_id="m", model_sha256=_h(b"ft"), provenance=None)
    assert out.links == ()


def test_b7_head_artifact_swap_is_refused():
    """The chain's head artifact must equal the model being loaded."""
    gate = ProvenanceGate(policy=SlotStartPolicy(require_provenance=True))
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(
            model_id="llama-4-ft-corp",
            model_sha256=_h(b"A-DIFFERENT-MODEL"),
            provenance=_chain())


def test_b7_broken_parent_binding_is_refused():
    """Tampering a link breaks the parent hash binding of the next link."""
    prov = _chain()
    tampered_first = dataclasses.replace(prov.links[0], artifact_sha256=_h(b"EVIL"))
    broken = ModelProvenance(
        model_id=prov.model_id,
        links=(tampered_first, prov.links[1]),  # link[1].parent still points at old
    )
    gate = ProvenanceGate(policy=SlotStartPolicy(require_provenance=True))
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(
            model_id=prov.model_id, model_sha256=_h(b"ft"), provenance=broken)


def test_b7_require_signed_but_unsigned_is_refused():
    gate = ProvenanceGate(policy=SlotStartPolicy(require_provenance=True,
                                                 require_signed=True))
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(
            model_id="llama-4-ft-corp", model_sha256=_h(b"ft"), provenance=_chain())


def test_b7_signed_by_trusted_key_allowed():
    priv, pub = _keypair()
    prov = _chain(builder_private=priv, builder_public=pub)
    gate = ProvenanceGate(policy=SlotStartPolicy(
        require_provenance=True, require_signed=True,
        trusted_builder_keys=frozenset({pub})))
    out = gate.require_slot_start(
        model_id="llama-4-ft-corp", model_sha256=_h(b"ft"), provenance=prov)
    assert out is prov


def test_b7_signed_by_untrusted_key_refused():
    priv, pub = _keypair()
    _, other_pub = _keypair()
    prov = _chain(builder_private=priv, builder_public=pub)
    gate = ProvenanceGate(policy=SlotStartPolicy(
        require_provenance=True,
        trusted_builder_keys=frozenset({other_pub})))  # pub not trusted
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(
            model_id="llama-4-ft-corp", model_sha256=_h(b"ft"), provenance=prov)


def test_b7_tampered_signature_refused():
    priv, pub = _keypair()
    prov = _chain(builder_private=priv, builder_public=pub)
    forged = dataclasses.replace(prov, head_signature=b"\x00" * 64)
    gate = ProvenanceGate(policy=SlotStartPolicy(
        require_provenance=True, require_signed=True,
        trusted_builder_keys=frozenset({pub})))
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(
            model_id="llama-4-ft-corp", model_sha256=_h(b"ft"), provenance=forged)


def test_b7_blocklisted_dataset_refused():
    gate = ProvenanceGate(policy=SlotStartPolicy(
        require_provenance=True,
        blocked_data_hashes=frozenset({_h(b"data2")})))  # poisoned finetune data
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(
            model_id="llama-4-ft-corp", model_sha256=_h(b"ft"), provenance=_chain())


def test_b7_dataset_outside_allowlist_refused():
    gate = ProvenanceGate(policy=SlotStartPolicy(
        require_provenance=True,
        allowed_data_hashes=frozenset({_h(b"data1")})))  # data2 not allowed
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(
            model_id="llama-4-ft-corp", model_sha256=_h(b"ft"), provenance=_chain())


@pytest.mark.parametrize("bad_sha", ["", "   ", None])
def test_b7_empty_model_sha_now_rejected(bad_sha):
    """FINDING B7-1 — NOW PATCHED (was S3 sharp-edge).

    The anti-swap binding used to fire only when BOTH head_artifact AND
    model_sha256 were non-empty, so an empty/whitespace/None model_sha256
    silently bypassed the 'chain is for THIS model' guarantee. The fix
    (maif_v2.py) rejects an empty model_sha256 under require_provenance with
    a loud ValueError (caller contract violation) BEFORE any chain is
    accepted. This test now asserts active enforcement across all three
    empty forms."""
    gate = ProvenanceGate(policy=SlotStartPolicy(require_provenance=True))
    with pytest.raises(ValueError):
        gate.require_slot_start(
            model_id="llama-4-ft-corp", model_sha256=bad_sha, provenance=_chain())


# ===========================================================================
# B7b — runtime model-integrity watchdog
# ===========================================================================


def _wd(source: dict) -> ModelIntegrityWatchdog:
    return ModelIntegrityWatchdog(memory_source=source)


def test_b7_watchdog_intact_region_passes():
    src = {("m", "blk0"): b"WEIGHTS-V1"}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="blk0", baseline=b"WEIGHTS-V1")
    reports = wd.require_integrity("m")
    assert all(r.intact for r in reports)


def test_b7_watchdog_runtime_tampering_evicts_and_raises():
    src = {("m", "blk0"): b"WEIGHTS-V1"}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="blk0", baseline=b"WEIGHTS-V1")
    src[("m", "blk0")] = b"WEIGHTS-XX"  # runtime bit-drift
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")
    assert wd.is_registered("m", "blk0") is False  # evicted
    assert wd.stats.evictions == 1


def test_b7_watchdog_hot_swap_same_length_detected():
    """A hot-swap to different weights of the SAME length is still caught."""
    src = {("m", "blk0"): b"AAAAAAAA"}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="blk0", baseline=b"AAAAAAAA")
    src[("m", "blk0")] = b"BBBBBBBB"  # same length, different content
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")


def test_b7_watchdog_unavailable_bytes_fail_closed():
    """A source that can't produce the bytes is treated as drift, not pass."""
    src = {("m", "blk0"): b"WEIGHTS-V1"}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="blk0", baseline=b"WEIGHTS-V1")
    del src[("m", "blk0")]  # bytes gone → source.get returns None
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")


def test_b7_watchdog_source_raises_fail_closed():
    def boom(model_id, layer_id):
        raise RuntimeError("mmap view gone")

    wd = ModelIntegrityWatchdog(memory_source=boom)
    wd.register_region(model_id="m", layer_id="blk0", baseline=b"WEIGHTS-V1")
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")


def test_b7_watchdog_require_integrity_without_region_raises():
    """Gating an unregistered model must error — never a silent false-safe."""
    wd = _wd({})
    with pytest.raises(ValueError):
        wd.require_integrity("never-registered")


def test_b7_watchdog_multiregion_only_drifted_evicted():
    src = {("m", "a"): b"AAA", ("m", "b"): b"BBB"}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="a", baseline=b"AAA")
    wd.register_region(model_id="m", layer_id="b", baseline=b"BBB")
    src[("m", "a")] = b"ZZZ"  # only 'a' drifts
    with pytest.raises(ModelIntegrityCompromised):
        wd.scan("m")
    assert wd.is_registered("m", "a") is False  # evicted
    assert wd.is_registered("m", "b") is True   # intact region survives


def test_b7_watchdog_dev_override_does_not_evict(monkeypatch):
    src = {("m", "blk0"): b"WEIGHTS-V1"}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="blk0", baseline=b"WEIGHTS-V1")
    src[("m", "blk0")] = b"TAMPERED!!"
    monkeypatch.setenv("VOS3_MODEL_INTEGRITY_DEV_OVERRIDE", "1")
    reports = wd.scan("m")  # no raise under override
    assert wd.stats.dev_overrides_used == 1
    assert wd.is_registered("m", "blk0") is True  # NOT evicted (dev mode)
    assert any(r.state == RegionState.EVICTED for r in reports)  # still reported


def test_b7_watchdog_register_validation():
    wd = _wd({})
    with pytest.raises(ValueError):
        wd.register_region(model_id="", layer_id="x", baseline=b"d")
    with pytest.raises(TypeError):
        wd.register_region(model_id="m", layer_id="x", baseline="not-bytes")


def test_b7_watchdog_no_false_positive_under_concurrent_scans():
    """B7.02: concurrent scans of an INTACT region must not false-positive."""
    src = {("m", "blk0"): b"STABLE-WEIGHTS"}
    wd = _wd(src)
    wd.register_region(model_id="m", layer_id="blk0", baseline=b"STABLE-WEIGHTS")
    errors: list = []

    def worker():
        try:
            for _ in range(50):
                wd.scan("m")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []  # zero false-positive evictions on a stable region
    assert wd.is_registered("m", "blk0") is True
