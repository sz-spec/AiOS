"""
backend/tests/security/test_oms_model_signer.py

Sprint 15 / Item I3 — coverage for infra/security/model_signer.py

Tests:
  - Sign + verify roundtrip produces matching SHA-256 + valid Ed25519 sig
  - verify_model_signature() short-form succeeds when bundle present
  - Tamper detection — modifying the model bytes invalidates the signature
  - Missing .sig file → verify returns False (not raise)
  - Missing .bundle.json → verify_oms_bundle returns ok=False with reason
  - OMS bundle JSON structure matches v1.0 spec keys
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# Load infra/security/model_signer.py via explicit file path. We avoid
# `from security import model_signer` because both `backend/security/`
# (Sprint 15 / I6) and `infra/security/` exist; sys.path manipulation
# would cause a package-name collision during pytest collection.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODEL_SIGNER_PATH = _REPO_ROOT / "infra" / "security" / "model_signer.py"
_MOD_NAME = "vos3_model_signer_under_test"
_spec = importlib.util.spec_from_file_location(_MOD_NAME, _MODEL_SIGNER_PATH)
ms = importlib.util.module_from_spec(_spec)
# Register before exec_module so @dataclass can walk sys.modules to find this
# module by its (synthetic) name during the class-decorator pass.
import sys as _sys  # noqa: E402

_sys.modules[_MOD_NAME] = ms
_spec.loader.exec_module(ms)


@pytest.fixture
def isolated_keys(monkeypatch, tmp_path):
    """Point the model-signer to a per-test dev key so tests don't share state."""
    monkeypatch.setenv("VOS3_MODEL_SIGNING_KEY", str(tmp_path / "test_signing.key"))
    monkeypatch.setenv("VOS3_MODEL_TRUST_ROOT", str(tmp_path / "test_signing.pub"))
    yield tmp_path


@pytest.fixture
def test_model(tmp_path):
    """Create a small fake model file."""
    p = tmp_path / "tiny.gguf"
    p.write_bytes(b"fake-model-weights" * 1000)  # 18 KB
    return p


def test_sign_model_produces_sig_and_bundle(isolated_keys, test_model):
    bundle = ms.sign_model(test_model)
    sig_path = test_model.with_suffix(test_model.suffix + ms.OMS_SIG_SUFFIX)
    bundle_path = test_model.with_suffix(test_model.suffix + ms.OMS_BUNDLE_SUFFIX)

    assert sig_path.is_file(), "sig sidecar missing"
    assert sig_path.stat().st_size == 64, "Ed25519 sig must be 64 bytes"
    assert bundle_path.is_file(), "bundle sidecar missing"
    assert bundle.subject.size == test_model.stat().st_size


def test_verify_model_signature_passes_on_clean_file(isolated_keys, test_model):
    ms.sign_model(test_model)
    assert ms.verify_model_signature(test_model) is True


def test_verify_oms_bundle_passes_on_clean_file(isolated_keys, test_model):
    ms.sign_model(test_model)
    result = ms.verify_oms_bundle(test_model)
    assert result.ok is True
    assert result.bundle is not None
    assert result.bundle.subject.digest_sha256 != ""


def test_tamper_detection(isolated_keys, test_model):
    ms.sign_model(test_model)
    # Append bytes to the model after signing.
    with open(test_model, "ab") as f:
        f.write(b"malicious-payload")

    # Short verify
    assert ms.verify_model_signature(test_model) is False

    # Full bundle verify with structured reason
    result = ms.verify_oms_bundle(test_model)
    assert result.ok is False
    assert "digest mismatch" in (
        result.reason or ""
    ), f"expected digest-mismatch reason, got: {result.reason!r}"


def test_missing_sig_returns_false(isolated_keys, test_model):
    # Model file exists but never signed.
    assert ms.verify_model_signature(test_model) is False


def test_missing_bundle_returns_structured_failure(isolated_keys, test_model):
    # Sign produces both sidecars, then delete just the bundle.
    ms.sign_model(test_model)
    bundle_path = test_model.with_suffix(test_model.suffix + ms.OMS_BUNDLE_SUFFIX)
    bundle_path.unlink()

    result = ms.verify_oms_bundle(test_model)
    assert result.ok is False
    assert "OMS bundle missing" in (result.reason or "")


def test_bundle_json_has_oms_v1_0_keys(isolated_keys, test_model):
    ms.sign_model(test_model)
    bundle_path = test_model.with_suffix(test_model.suffix + ms.OMS_BUNDLE_SUFFIX)
    doc = json.loads(bundle_path.read_text("utf-8"))

    assert doc["specVersion"] == "1.0"
    assert doc["mediaType"] == ms.OMS_MEDIA_TYPE
    assert "subject" in doc and "digest" in doc["subject"]
    assert doc["subject"]["digest"]["sha256"]
    assert "signature" in doc
    assert doc["signature"]["alg"] == "ed25519"
    assert (
        "predicate" in doc
        and doc["predicate"]["predicateType"] == ms.OMS_PREDICATE_TYPE
    )


def test_model_card_digest_populated_when_present(isolated_keys, test_model):
    card_path = Path(str(test_model) + ms.OMS_MODELCARD_SUFFIX)
    card_path.write_text("# Test Model Card\n\nLicense: MIT\n", encoding="utf-8")

    bundle = ms.sign_model(test_model)
    assert bundle.predicate.model_card_digest is not None
    assert len(bundle.predicate.model_card_digest) == 64  # SHA-256 hex


def test_bundle_to_dict_roundtrip(isolated_keys, test_model):
    bundle = ms.sign_model(test_model)
    doc = bundle.to_dict()
    restored = ms.ModelSigningBundle.from_dict(doc)
    assert restored.subject == bundle.subject
    assert restored.signature == bundle.signature
    assert restored.predicate == bundle.predicate
