"""
Tests for Sprint 22 / Item I2 — MAIF v2 provenance chain + SLOT_START gate
(backend/services/maif_v2.py).

Pins the fail-closed contract: a slot starts only under a verified,
policy-satisfying provenance chain that terminates at the model being
loaded.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.maif_v2 import (  # noqa: E402
    ModelProvenance,
    ProvenanceGate,
    ProvenanceStep,
    SlotStartPolicy,
    SlotStartRefused,
    build_provenance,
)


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


DATA = _h("clean-corpus")
MODEL_SHA = _h("final-weights")


def _chain(model_sha=MODEL_SHA, data=DATA, builder_priv=None, builder_pub=b""):
    return build_provenance(
        "llama-4-ft",
        [
            (
                ProvenanceStep.PRETRAIN,
                _h("base-weights"),
                _h("pretrain-corpus"),
                "base",
            ),
            (ProvenanceStep.FINETUNE, model_sha, data, "corp ft"),
        ],
        builder_private=builder_priv,
        builder_public=builder_pub,
    )


def _keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return priv, pub


def test_valid_chain_allows_slot_start():
    gate = ProvenanceGate(policy=SlotStartPolicy(require_provenance=True))
    prov = _chain()
    out = gate.require_slot_start(model_id="m", model_sha256=MODEL_SHA, provenance=prov)
    assert out.head_artifact_sha256() == MODEL_SHA
    assert gate.stats.allowed == 1


def test_missing_provenance_refused_when_required():
    gate = ProvenanceGate(policy=SlotStartPolicy(require_provenance=True))
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(model_id="m", model_sha256=MODEL_SHA, provenance=None)


def test_missing_provenance_allowed_when_not_required():
    gate = ProvenanceGate(policy=SlotStartPolicy(require_provenance=False))
    out = gate.require_slot_start(model_id="m", model_sha256=MODEL_SHA, provenance=None)
    assert out.links == ()


def test_broken_chain_refused():
    gate = ProvenanceGate()
    prov = _chain()
    # Tamper a link's parent binding.
    bad_links = list(prov.links)
    bad = bad_links[1]
    bad_links[1] = type(bad)(
        step=bad.step,
        artifact_sha256=bad.artifact_sha256,
        training_data_sha256=bad.training_data_sha256,
        parent_link_hash="deadbeef",
        description=bad.description,
    )
    broken = ModelProvenance(model_id=prov.model_id, links=tuple(bad_links))
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(model_id="m", model_sha256=MODEL_SHA, provenance=broken)


def test_head_artifact_must_match_loaded_model():
    gate = ProvenanceGate()
    prov = _chain(model_sha=_h("some-other-model"))
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(model_id="m", model_sha256=MODEL_SHA, provenance=prov)


def test_signed_provenance_with_trusted_key_allows():
    priv, pub = _keypair()
    gate = ProvenanceGate(
        policy=SlotStartPolicy(
            require_signed=True, trusted_builder_keys=frozenset({pub})
        )
    )
    prov = _chain(builder_priv=priv, builder_pub=pub)
    out = gate.require_slot_start(model_id="m", model_sha256=MODEL_SHA, provenance=prov)
    assert out.head_signature


def test_unsigned_refused_when_signing_required():
    gate = ProvenanceGate(policy=SlotStartPolicy(require_signed=True))
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(
            model_id="m", model_sha256=MODEL_SHA, provenance=_chain()
        )


def test_untrusted_builder_key_refused():
    priv, pub = _keypair()
    _, other_pub = _keypair()
    gate = ProvenanceGate(
        policy=SlotStartPolicy(
            require_signed=True, trusted_builder_keys=frozenset({other_pub})
        )
    )
    prov = _chain(builder_priv=priv, builder_pub=pub)
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(model_id="m", model_sha256=MODEL_SHA, provenance=prov)


def test_blocklisted_dataset_refused():
    gate = ProvenanceGate(policy=SlotStartPolicy(blocked_data_hashes=frozenset({DATA})))
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(
            model_id="m", model_sha256=MODEL_SHA, provenance=_chain()
        )


def test_dataset_outside_allowlist_refused():
    gate = ProvenanceGate(
        policy=SlotStartPolicy(allowed_data_hashes=frozenset({_h("only-this-dataset")}))
    )
    with pytest.raises(SlotStartRefused):
        gate.require_slot_start(
            model_id="m", model_sha256=MODEL_SHA, provenance=_chain()
        )
