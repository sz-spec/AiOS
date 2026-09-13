"""
v20.2-alpha — IntegrityCertificate end-to-end test.

Verifies the "hardware proof of intent" chain:
    1. happy path: cert round-trips (sign → verify) with valid model hash
    2. tamper detection: if the on-disk model bytes change, verification
       against the cert raises CertificateVerificationError
    3. tenant binding: certs for different tenants produce different
       payload hashes (no cross-tenant confusion)
    4. mock TEE mode produces deterministic RTMR values; signed payload
       differs on tamper as expected
    5. unsigned certificates are rejected by the verifier (fail-closed)
    6. baremetal mode produces all-zero RTMRs with clear platform label
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

# Make sure the backend root is importable.
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.security.attestation_service import (
    AttestationService,
    CertificateVerificationError,
)

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def tmp_key_dir(tmp_path):
    kd = tmp_path / "keys"
    kd.mkdir()
    return kd


@pytest.fixture
def svc(tmp_key_dir):
    """AttestationService in mock TEE mode with an ephemeral keypair."""
    return AttestationService(measurement_mode="mock", key_dir=tmp_key_dir)


@pytest.fixture
def sample_model(tmp_path):
    """Create a deterministic 'model' file on disk."""
    p = tmp_path / "model.safetensors"
    p.write_bytes(b"VOS3-MODEL-WEIGHTS-V1-" + b"A" * 4096)
    return p


def _sha384(path: Path) -> str:
    h = hashlib.sha384()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestIntegrityCertificate:

    def test_happy_path_sign_and_verify(self, svc, sample_model):
        cert = svc.generate_certificate(
            session_id="session-abc",
            tenant_id="tenant-acme",
            model_path_or_bytes=sample_model,
            intent_manifest_bytes=b"role: coding\nscopes: [read]\n",
        )
        assert cert.signature_b64 is None, "unsigned before sign_certificate"
        cert = svc.sign_certificate(cert)
        assert cert.signature_b64 is not None
        assert cert.signer_public_key_fp is not None
        # Verifier must accept with matching expected-hash
        svc.verify_certificate(cert, expected_model_sha384=_sha384(sample_model))

    def test_tampered_model_detected(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="session-abc",
                tenant_id="tenant-acme",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"manifest-v1",
            )
        )
        # Attacker flips a byte in the model on disk. The cert's
        # recorded model_sha384 no longer matches the actual bytes.
        tampered = sample_model.read_bytes() + b"\x00"
        sample_model.write_bytes(tampered)
        with pytest.raises(CertificateVerificationError, match="model hash mismatch"):
            svc.verify_certificate(cert, expected_model_sha384=_sha384(sample_model))

    def test_tampered_payload_detected(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="session-abc",
                tenant_id="tenant-acme",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"manifest-v1",
            )
        )
        # Attacker mutates a field post-signing without re-signing.
        cert.tenant_id = "tenant-victim"
        with pytest.raises(CertificateVerificationError):
            svc.verify_certificate(cert, expected_model_sha384=_sha384(sample_model))

    def test_tenant_binding(self, svc, sample_model):
        cert_a = svc.sign_certificate(
            svc.generate_certificate(
                session_id="session-shared",
                tenant_id="tenant-A",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"manifest-v1",
            )
        )
        cert_b = svc.sign_certificate(
            svc.generate_certificate(
                session_id="session-shared",
                tenant_id="tenant-B",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"manifest-v1",
            )
        )
        # Same session_id + same model + same manifest, different tenant →
        # different certificate_sha256 (tenant_id is part of the payload).
        assert cert_a.certificate_sha256 != cert_b.certificate_sha256
        # A cert_a signature won't verify on cert_b's payload.
        cert_b.signature_b64 = cert_a.signature_b64
        with pytest.raises(CertificateVerificationError):
            svc.verify_certificate(cert_b, expected_model_sha384=_sha384(sample_model))

    def test_unsigned_certificate_rejected(self, svc, sample_model):
        cert = svc.generate_certificate(
            session_id="s1",
            tenant_id="t1",
            model_path_or_bytes=sample_model,
            intent_manifest_bytes=b"m",
        )
        assert cert.signature_b64 is None
        with pytest.raises(CertificateVerificationError, match="unsigned"):
            svc.verify_certificate(cert, expected_model_sha384=_sha384(sample_model))

    def test_intent_manifest_tamper_detected(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"original-manifest",
            )
        )
        # Pass a DIFFERENT manifest to the verifier — must reject.
        fake_manifest_hash = svc.bind_intent_manifest(b"attacker-manifest", "s1")
        with pytest.raises(CertificateVerificationError, match="intent manifest"):
            svc.verify_certificate(
                cert,
                expected_model_sha384=_sha384(sample_model),
                expected_manifest_sha384=fake_manifest_hash,
            )


class TestTeeMeasurementSource:

    def test_baremetal_produces_all_zero(self, tmp_key_dir):
        svc = AttestationService(measurement_mode="baremetal", key_dir=tmp_key_dir)
        tee = svc.read_measurements()
        assert tee.platform == "BAREMETAL_NO_TEE"
        assert tee.mrtd == "00" * 48
        assert tee.rtmr_0 == "00" * 48
        assert tee.rtmr_1 == "00" * 48
        assert tee.rtmr_2 == "00" * 48
        assert tee.rtmr_3 == "00" * 48
        assert tee.tee_quote_available is False

    def test_mock_is_deterministic(self, tmp_key_dir):
        a = AttestationService(measurement_mode="mock", key_dir=tmp_key_dir)
        b = AttestationService(measurement_mode="mock", key_dir=tmp_key_dir)
        ta = a.read_measurements()
        tb = b.read_measurements()
        assert ta.rtmr_0 == tb.rtmr_0
        assert ta.rtmr_1 == tb.rtmr_1
        assert ta.platform == "MOCK"


class TestIntentManifestBinding:

    def test_same_manifest_different_session_different_hash(self, svc):
        m = b"role: coding\nscope: [read]\n"
        h1 = svc.bind_intent_manifest(m, "session-1")
        h2 = svc.bind_intent_manifest(m, "session-2")
        assert h1 != h2, (
            "session_id must be part of the manifest binding — "
            "otherwise the same manifest grants work across sessions"
        )
        # SHA-384 produces 96 hex chars
        assert len(h1) == 96 and len(h2) == 96


class TestIDORGuard:
    """v20.2-final IDOR close on /api/compliance/attestation.

    Validates the _enforce_session_ownership gate admits only sessions
    whose tenant-prefix matches the caller's authenticated tenant_id.
    """

    def _make_user(self, tenant_id):
        """Minimal AuthenticatedUser-like object for the gate test."""
        from types import SimpleNamespace

        return SimpleNamespace(
            tenant_id=tenant_id, org_id=None, user_id="u1", email="u@example.com"
        )

    def test_valid_tenant_prefix_accepted(self):
        from api.compliance_routes import _enforce_session_ownership

        tid = _enforce_session_ownership(
            "tenant-acme:sess-abc-123", self._make_user("tenant-acme")
        )
        assert tid == "tenant-acme"

    def test_cross_tenant_session_rejected(self):
        """User from tenant-A cannot attest a tenant-B session."""
        from fastapi import HTTPException
        from api.compliance_routes import _enforce_session_ownership

        with pytest.raises(HTTPException) as exc:
            _enforce_session_ownership(
                "tenant-victim:sess-secret", self._make_user("tenant-attacker")
            )
        assert exc.value.status_code == 403

    def test_missing_tenant_prefix_rejected(self):
        from fastapi import HTTPException
        from api.compliance_routes import _enforce_session_ownership

        with pytest.raises(HTTPException) as exc:
            _enforce_session_ownership("no-colon-here", self._make_user("tenant-acme"))
        assert exc.value.status_code == 400

    def test_path_traversal_rejected(self):
        from fastapi import HTTPException
        from api.compliance_routes import _enforce_session_ownership

        for nasty in ["../etc/passwd", "t:..\\b", "t:\x00inject", "t:a/b", "t:a\\b"]:
            with pytest.raises(HTTPException) as exc:
                _enforce_session_ownership(nasty, self._make_user("t"))
            assert exc.value.status_code in (400, 403)

    def test_oversize_session_id_rejected(self):
        from fastapi import HTTPException
        from api.compliance_routes import _enforce_session_ownership

        big = "tenant-acme:" + ("A" * 300)  # 312 chars > 260
        with pytest.raises(HTTPException) as exc:
            _enforce_session_ownership(big, self._make_user("tenant-acme"))
        assert exc.value.status_code == 400

    def test_empty_opaque_id_rejected(self):
        from fastapi import HTTPException
        from api.compliance_routes import _enforce_session_ownership

        with pytest.raises(HTTPException) as exc:
            _enforce_session_ownership("tenant-acme:", self._make_user("tenant-acme"))
        assert exc.value.status_code == 400

    def test_user_without_tenant_rejected(self):
        """Authenticated user with no tenant claim → 403."""
        from fastapi import HTTPException
        from api.compliance_routes import _enforce_session_ownership
        from types import SimpleNamespace

        u = SimpleNamespace(tenant_id=None, org_id=None, user_id="u1")
        with pytest.raises(HTTPException) as exc:
            _enforce_session_ownership("t:sess", u)
        assert exc.value.status_code == 403


# --------------------------------------------------------------------------
# v20.2-FINAL additions: legal-hash, Wiz export, cert vault, bulk, verify
# --------------------------------------------------------------------------


class TestLegalComplianceHash:

    def test_hash_populated_on_generation(self, svc, sample_model):
        cert = svc.generate_certificate(
            session_id="s1",
            tenant_id="t1",
            model_path_or_bytes=sample_model,
            intent_manifest_bytes=b"m",
        )
        assert cert.legal_compliance_hash
        assert len(cert.legal_compliance_hash) == 64  # SHA-256 hex

    def test_hash_changes_when_invariants_change(self, svc, sample_model):
        cert = svc.generate_certificate(
            session_id="s1",
            tenant_id="t1",
            model_path_or_bytes=sample_model,
            intent_manifest_bytes=b"m",
        )
        baseline = cert.legal_compliance_hash
        cert.policy_invariants = dict(cert.policy_invariants)
        cert.policy_invariants["new_rule"] = "proof/path.py"
        recomputed = svc._compute_legal_compliance_hash(cert)
        assert recomputed != baseline

    def test_hash_stable_across_reruns(self, svc, sample_model):
        a = svc.generate_certificate(
            session_id="s1",
            tenant_id="t1",
            model_path_or_bytes=sample_model,
            intent_manifest_bytes=b"m",
        )
        b = svc.generate_certificate(
            session_id="s2",
            tenant_id="t1",
            model_path_or_bytes=sample_model,
            intent_manifest_bytes=b"m",
        )
        # Invariant set + standards + platform don't depend on session →
        # same legal hash across sessions.
        assert a.legal_compliance_hash == b.legal_compliance_hash


class TestWizJsonLdExport:

    def test_external_spm_view_carries_core_fields(self, svc, sample_model):
        """v20.7.1: renamed export method; v20.5.1 alias preserved."""
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        view = cert.to_external_spm_jsonld()
        assert view["@type"] == "AiRuntimeAttestation"
        assert view["schemaVersion"].startswith("ai-spm-2026")
        assert view["attestationId"] == cert.id
        assert view["ownerTenantId"] == cert.tenant_id
        assert view["modelBinaryDigest"]["alg"] == "SHA-384"
        assert view["modelBinaryDigest"]["value"] == cert.model_sha384
        assert view["invariantSetDigest"]["value"] == cert.legal_compliance_hash
        assert view["signature"]["value"] == cert.signature_b64
        # Back-compat alias must return the same object shape.
        assert cert.to_wiz_jsonld() == view


class TestCertificateVault:

    def _fresh_vault(self, tmp_path, monkeypatch):
        from core.security.cert_vault import CertificateVault

        CertificateVault.reset_instance_for_tests()
        monkeypatch.setenv("VOS3_CERT_VAULT_PATH", str(tmp_path / "certs.db"))
        return CertificateVault.instance()

    def test_store_and_retrieve(self, tmp_path, monkeypatch, svc, sample_model):
        vault = self._fresh_vault(tmp_path, monkeypatch)
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        record = vault.store(cert)
        assert record["cert_id"] == cert.id
        round_trip = vault.get(cert.id)
        assert round_trip is not None
        assert round_trip["id"] == cert.id

    def test_refuse_to_store_unsigned(self, tmp_path, monkeypatch, svc, sample_model):
        vault = self._fresh_vault(tmp_path, monkeypatch)
        unsigned = svc.generate_certificate(
            session_id="s1",
            tenant_id="t1",
            model_path_or_bytes=sample_model,
            intent_manifest_bytes=b"m",
        )
        with pytest.raises(ValueError):
            vault.store(unsigned)

    def test_list_by_date_range_filters_tenant(
        self, tmp_path, monkeypatch, svc, sample_model
    ):
        vault = self._fresh_vault(tmp_path, monkeypatch)
        cert_a = svc.sign_certificate(
            svc.generate_certificate(
                session_id="sA",
                tenant_id="tenant-A",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        cert_b = svc.sign_certificate(
            svc.generate_certificate(
                session_id="sB",
                tenant_id="tenant-B",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        vault.store(cert_a)
        vault.store(cert_b)
        rows_a = vault.list_by_date_range("tenant-A", 0, 2**31 - 1)
        assert len(rows_a) == 1
        assert rows_a[0]["tenant_id"] == "tenant-A"


class TestVerifySignedPayload:

    def test_happy_path_verdict(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        verdict = svc.verify_signed_payload(cert)
        assert verdict["overall_valid"] is True
        assert verdict["signature_valid"] is True
        assert verdict["payload_hash_valid"] is True
        assert verdict["legal_compliance_valid"] is True
        assert verdict["errors"] == []

    def test_tampered_legal_hash_detected(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        # Flip the legal hash — invalidates both signature and legal check.
        cert.legal_compliance_hash = "0" * 64
        verdict = svc.verify_signed_payload(cert)
        assert verdict["overall_valid"] is False
        # Either signature or legal-hash or payload-hash must surface the drift.
        assert not (
            verdict["signature_valid"]
            and verdict["payload_hash_valid"]
            and verdict["legal_compliance_valid"]
        )

    def test_unsigned_rejected(self, svc, sample_model):
        cert = svc.generate_certificate(
            session_id="s1",
            tenant_id="t1",
            model_path_or_bytes=sample_model,
            intent_manifest_bytes=b"m",
        )
        verdict = svc.verify_signed_payload(cert)
        assert verdict["overall_valid"] is False
        assert any("unsigned" in e for e in verdict["errors"])


# --------------------------------------------------------------------------
# v20.3-PRODIGY — composed-commitment cross-check
# --------------------------------------------------------------------------


class TestComposedCommitment:
    """Verifier must catch tampering in h_M / h_I / h_P without the model."""

    def test_commitment_populated(self, svc, sample_model):
        cert = svc.generate_certificate(
            session_id="s1",
            tenant_id="t1",
            model_path_or_bytes=sample_model,
            intent_manifest_bytes=b"m",
        )
        assert len(cert.composed_commitment_sha384) == 96  # SHA-384 hex

    def test_commitment_happy_path(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        v = svc.verify_signed_payload(cert)
        assert v["composed_commitment_valid"] is True

    def test_commitment_tamper_detected(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        # Attacker flips a bit in the commitment; legal-hash + payload-hash
        # checks also surface it, but we want the commitment-specific bool.
        tampered_hex = "ff" + cert.composed_commitment_sha384[2:]
        cert.composed_commitment_sha384 = tampered_hex
        v = svc.verify_signed_payload(cert)
        assert v["composed_commitment_valid"] is False

    def test_back_compat_empty_commitment_accepted(self, svc, sample_model):
        """v20.2 certs lacking the field must still verify cleanly."""
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        cert.composed_commitment_sha384 = ""  # simulate v20.2 cert shape
        # Payload hash drift is expected after field edit; rehash.
        cert.certificate_sha256 = svc._payload_sha256(cert)
        # Signature no longer valid post-edit — that's fine; we only
        # assert the commitment check itself is "not applicable" = valid.
        v = svc.verify_signed_payload(cert)
        assert v["composed_commitment_valid"] is True


# --------------------------------------------------------------------------
# v20.3-PRODIGY — V-AAAK zero-grow decoder parity
# --------------------------------------------------------------------------


class TestVAaakDecoderParity:
    """The v20.3 pre-scan + single-allocation decoder must produce
    byte-identical output to the v20.2 extend-based decoder."""

    def test_literal_passthrough(self):
        from services.vbus_driver import v_aaak_decode

        assert v_aaak_decode(b"hello world") == b"hello world"

    def test_escape_escape_literal(self):
        from services.vbus_driver import v_aaak_decode, V_AAAK_ESCAPE

        out = v_aaak_decode(bytes([V_AAAK_ESCAPE, V_AAAK_ESCAPE]))
        assert out == bytes([V_AAAK_ESCAPE])

    def test_encode_decode_roundtrip(self):
        from services.vbus_driver import v_aaak_encode, v_aaak_decode

        sample = b'{"hello": "world", "count": 42}'
        assert v_aaak_decode(v_aaak_encode(sample)) == sample

    def test_truncated_raises(self):
        import pytest as _pt
        from services.vbus_driver import v_aaak_decode, V_AAAK_ESCAPE

        with _pt.raises(ValueError, match="Truncated"):
            v_aaak_decode(bytes([V_AAAK_ESCAPE]))

    def test_invalid_code_raises(self):
        import pytest as _pt
        from services.vbus_driver import (
            v_aaak_decode,
            V_AAAK_ESCAPE,
            _V_AAAK_DICT_BYTES,
        )

        # Code above dict bound → ValueError in pre-scan.
        bad = bytes([V_AAAK_ESCAPE, (len(_V_AAAK_DICT_BYTES) + 50) % 256])
        if bad[1] == V_AAAK_ESCAPE:
            bad = bytes([V_AAAK_ESCAPE, (bad[1] + 1) % 256])
        if bad[1] >= len(_V_AAAK_DICT_BYTES) and bad[1] != V_AAAK_ESCAPE:
            with _pt.raises(ValueError):
                v_aaak_decode(bad)


# --------------------------------------------------------------------------
# v20.4-TITAN — hybrid signatures (P-256 + P-521 [+ optional ML-DSA])
# --------------------------------------------------------------------------


class TestHybridSignatures:
    """Every v20.4 cert carries P-256 + P-521; verifier must pass both."""

    def test_p521_populated_on_sign(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        assert cert.signature_p521_b64 is not None
        assert cert.signer_p521_public_key_fp is not None
        assert cert.signature_algorithms is not None
        assert "ECDSA-P256-SHA256" in cert.signature_algorithms
        assert "ECDSA-P521-SHA512" in cert.signature_algorithms

    def test_p521_verify_happy_path(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        v = svc.verify_signed_payload(cert)
        assert v["signature_valid"] is True
        assert v["signature_p521_valid"] is True
        assert v["overall_valid"] is True

    def test_p521_tampered_detected(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        # Flip the P-521 signature; verdict must surface the P-521 bit
        # as False even though the P-256 bit is still True.
        import base64

        raw = base64.b64decode(cert.signature_p521_b64)
        tampered = bytearray(raw)
        tampered[-1] ^= 0xFF
        cert.signature_p521_b64 = base64.b64encode(bytes(tampered)).decode("ascii")
        v = svc.verify_signed_payload(cert)
        assert v["signature_p521_valid"] is False
        assert v["overall_valid"] is False

    def test_back_compat_no_p521(self, svc, sample_model):
        """v20.2/v20.3 certs lacking P-521 must still verify."""
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        # Simulate a v20.3 cert by stripping P-521 fields then rehashing.
        cert.signature_p521_b64 = None
        cert.signer_p521_public_key_fp = None
        cert.certificate_sha256 = svc._payload_sha256(cert)
        v = svc.verify_signed_payload(cert)
        # P-521 absence = N/A = valid
        assert v["signature_p521_valid"] is True

    def test_mldsa_absent_is_valid(self, svc, sample_model):
        """With VOS3_PQ_SIGNING unset, ML-DSA sig is absent → verdict N/A."""
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        assert cert.signature_mldsa_b64 is None
        v = svc.verify_signed_payload(cert)
        assert v["signature_mldsa_valid"] is True  # N/A


# --------------------------------------------------------------------------
# v20.4-TITAN — prefetch path-traversal guard (state-collapse hardening)
# --------------------------------------------------------------------------


class TestPrefetchPathGuard:
    """Prefetcher must reject model refs outside the allowed-root set."""

    def test_etc_passwd_rejected(self):
        from services.prefetch import _warm_one

        status = _warm_one("/etc/passwd")
        assert status["warmed"] is False
        assert status["error"] in ("path_outside_allowlist", "not a filesystem path")

    def test_allowed_root_accepted(self, tmp_path, monkeypatch):
        from services.prefetch import _warm_one

        # Point the allow-root env at tmp_path, write a small file under it,
        # confirm the warm succeeds.
        monkeypatch.setenv("VOS3_PREFETCH_ALLOW_ROOTS", str(tmp_path))
        model = tmp_path / "m.safetensors"
        model.write_bytes(b"A" * 1024)
        status = _warm_one(str(model))
        assert status["warmed"] is True
        assert status["bytes"] == 1024

    def test_traversal_rejected(self, tmp_path, monkeypatch):
        from services.prefetch import _warm_one

        # Allow a subdirectory; try to traverse upward with "..".
        allowed = tmp_path / "ok"
        allowed.mkdir()
        outside = tmp_path / "secret.bin"
        outside.write_bytes(b"SECRET" * 64)
        monkeypatch.setenv("VOS3_PREFETCH_ALLOW_ROOTS", str(allowed))
        status = _warm_one(str(allowed / ".." / "secret.bin"))
        # Must not warm; Path.resolve() normalises the ``..`` away so the
        # final resolved path lands outside the allowlist.
        assert status["warmed"] is False


# --------------------------------------------------------------------------
# v20.4-TITAN — SQLCipher mmap PRAGMA (cert vault backend)
# --------------------------------------------------------------------------


class TestCertVaultMmap:

    def test_mmap_pragma_accepted(self, tmp_path, monkeypatch):
        """Cert vault must open cleanly with mmap_size set."""
        from core.security.cert_vault import CertificateVault

        CertificateVault.reset_instance_for_tests()
        monkeypatch.setenv("VOS3_CERT_VAULT_PATH", str(tmp_path / "certs.db"))
        monkeypatch.setenv("VOS3_CERT_VAULT_MMAP_MB", "4")
        v = CertificateVault.instance()
        # Query the pragma back to confirm it actually applied.
        row = v._conn.execute("PRAGMA mmap_size").fetchone()
        assert row is not None
        assert int(row[0]) == 4 * 1024 * 1024


# --------------------------------------------------------------------------
# v20.5-SINGULARITY — Tenant-partitioned KV prefix cache
# --------------------------------------------------------------------------


class TestKVPrefixCache:
    """Hard tenant partition + RadixAttention longest-prefix lookup."""

    def test_basic_lookup_and_hit_rate(self):
        from ai.llm.kv_prefix_cache import KVPrefixCache

        c = KVPrefixCache(max_entries_per_tenant=128)
        c.insert("t1", [1, 2, 3, 4], handle=42)
        hit = c.lookup("t1", [1, 2, 3, 4, 5, 6])
        assert hit is not None
        assert hit.handle == 42
        assert hit.trust_domain == "t1"
        assert tuple(hit.tokens) == (1, 2, 3, 4)
        # ρ = hits/(hits+misses) = 1/1 after one successful lookup
        assert c.hit_rate("t1") == 1.0

    def test_no_cross_tenant_leak_identical_prefix(self):
        """The whole point of this module: identical prefixes across
        tenants must NOT collide."""
        from ai.llm.kv_prefix_cache import KVPrefixCache

        c = KVPrefixCache()
        c.insert("alice", [1, 2, 3], handle=100)
        c.insert("bob", [1, 2, 3], handle=200)
        # Bob's lookup must yield Bob's handle, never Alice's.
        hit = c.lookup("bob", [1, 2, 3, 4])
        assert hit is not None
        assert hit.handle == 200
        assert hit.trust_domain == "bob"

    def test_third_party_lookup_returns_none(self):
        from ai.llm.kv_prefix_cache import KVPrefixCache

        c = KVPrefixCache()
        c.insert("alice", [1, 2, 3], handle=100)
        # Carol has no entries — must miss.
        assert c.lookup("carol", [1, 2, 3]) is None

    def test_longest_prefix_wins(self):
        from ai.llm.kv_prefix_cache import KVPrefixCache

        c = KVPrefixCache()
        c.insert("t", [1, 2], handle=10)
        c.insert("t", [1, 2, 3, 4], handle=20)
        # Probe [1,2,3,4,5,6] should match the longer entry.
        hit = c.lookup("t", [1, 2, 3, 4, 5, 6])
        assert hit.handle == 20
        assert tuple(hit.tokens) == (1, 2, 3, 4)

    def test_lru_eviction_caps_per_tenant(self):
        from ai.llm.kv_prefix_cache import KVPrefixCache

        c = KVPrefixCache(max_entries_per_tenant=3)
        for k in range(5):
            c.insert("t", [k, k + 1, k + 2], handle=k * 10)
        # Cap is 3, so older entries should have been evicted.
        s = c.stats()
        assert s["t"]["entries"] <= 3

    def test_empty_tenant_id_rejected(self):
        from ai.llm.kv_prefix_cache import KVPrefixCache
        import pytest as _pt

        c = KVPrefixCache()
        with _pt.raises(ValueError):
            c.insert("", [1, 2, 3], handle=1)
        with _pt.raises(ValueError):
            c.lookup("", [1, 2, 3])


# --------------------------------------------------------------------------
# v20.5-SINGULARITY — Zero-copy VBus ring buffer
# --------------------------------------------------------------------------


class TestZeroCopyRingBuffer:

    def _shm_name(self, tag: str) -> str:
        """macOS shm names are limited to ~31 chars; keep it tight."""
        import os, hashlib

        h = hashlib.sha1(f"{os.getpid()}-{id(self)}-{tag}".encode()).hexdigest()
        return f"v5-{tag}-{h[:8]}"  # ≤ ~22 chars

    def test_create_attach_emit_consume(self):
        from services.vbus_ring_buffer import ZeroCopyRingBuffer

        name = self._shm_name("ec")
        ring = ZeroCopyRingBuffer.create(
            name=name,
            slot_count=8,
            slot_size=256,
        )
        try:
            payloads = [b"frame-" + str(i).encode() for i in range(5)]
            for p in payloads:
                ring.emit(p)
            drained = list(ring.consume_iter())
            assert drained == payloads
        finally:
            ring.close()
            ring.unlink()

    def test_zero_copy_producer_writes_in_place(self):
        """producer_slot yields a memoryview; bytes written through it
        must appear at the consumer side without an intermediate copy."""
        from services.vbus_ring_buffer import ZeroCopyRingBuffer

        name = self._shm_name("zc")
        ring = ZeroCopyRingBuffer.create(name=name, slot_count=4, slot_size=256)
        try:
            with ring.producer_slot() as view:
                # In-place write — no intermediate bytes object.
                payload = b"VOS3-zero-copy"
                view[: len(payload)] = payload
            ring.commit_emit(len(payload))

            # Consumer should see the same bytes via memoryview.
            with ring.consumer_slot() as view:
                assert bytes(view) == b"VOS3-zero-copy"
        finally:
            ring.close()
            ring.unlink()

    def test_ring_full_raises(self):
        from services.vbus_ring_buffer import ZeroCopyRingBuffer, RingFull
        import pytest as _pt

        name = self._shm_name("rf")
        ring = ZeroCopyRingBuffer.create(name=name, slot_count=2, slot_size=64)
        try:
            ring.emit(b"a")
            ring.emit(b"b")
            with _pt.raises(RingFull):
                ring.emit(b"c")
        finally:
            ring.close()
            ring.unlink()

    def test_oversize_payload_rejected(self):
        from services.vbus_ring_buffer import ZeroCopyRingBuffer
        import pytest as _pt

        name = self._shm_name("ov")
        ring = ZeroCopyRingBuffer.create(name=name, slot_count=2, slot_size=32)
        try:
            with _pt.raises(ValueError):
                ring.emit(b"x" * 100)  # > slot_size - 8 (len prefix)
        finally:
            ring.close()
            ring.unlink()

    def test_attach_reads_layout(self):
        """A separate attach() handle must observe the producer's writes."""
        from services.vbus_ring_buffer import ZeroCopyRingBuffer

        name = self._shm_name("at")
        producer = ZeroCopyRingBuffer.create(
            name=name,
            slot_count=4,
            slot_size=128,
        )
        try:
            producer.emit(b"hello-from-producer")
            # Second handle, same segment.
            consumer = ZeroCopyRingBuffer.attach(name=name)
            try:
                drained = list(consumer.consume_iter())
                assert drained == [b"hello-from-producer"]
            finally:
                consumer.close()
        finally:
            producer.close()
            producer.unlink()


# --------------------------------------------------------------------------
# v20.5-SINGULARITY — PQ commitment trapdoor (forward-compat scaffolding)
# --------------------------------------------------------------------------


class TestPQCommitment:
    """When ML-DSA isn't available, the commitment field must be populated
    AND must not be confused for real PQ security."""

    def test_commitment_populated_when_mldsa_absent(self, svc, sample_model):
        # Default env: VOS3_PQ_SIGNING unset → commitment trapdoor active.
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        assert cert.signature_mldsa_b64 is None
        assert cert.pq_commitment_alg == "ML-DSA-65-PENDING"
        assert cert.pq_commitment_sha384 is not None
        assert len(cert.pq_commitment_sha384) == 96  # SHA-384 hex
        assert "PQ-COMMITMENT-ONLY" in (cert.signature_algorithms or [])

    def test_commitment_verify_happy_path(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        v = svc.verify_signed_payload(cert)
        assert v["pq_commitment_valid"] is True
        assert v["pq_commitment_alg"] == "ML-DSA-65-PENDING"
        assert v["overall_valid"] is True

    def test_commitment_tamper_detected(self, svc, sample_model):
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        # Flip the commitment label — recomputed hash will differ.
        cert.pq_commitment_alg = "ML-KEM-1024-PENDING"
        v = svc.verify_signed_payload(cert)
        assert v["pq_commitment_valid"] is False
        assert v["overall_valid"] is False

    def test_commitment_alg_label_indicates_not_real_pq(self, svc, sample_model):
        """The label MUST end with -PENDING so no consumer mistakes the
        commitment for a real PQ signature."""
        cert = svc.sign_certificate(
            svc.generate_certificate(
                session_id="s1",
                tenant_id="t1",
                model_path_or_bytes=sample_model,
                intent_manifest_bytes=b"m",
            )
        )
        assert cert.pq_commitment_alg.endswith("-PENDING")


# --------------------------------------------------------------------------
# v20.7.1-NEUTRAL — third-party AI-security platform connectors
# (renamed from v20.5.1: external_spm / runtime_firewall / edr)
# --------------------------------------------------------------------------


class TestExternalSpmConnector:

    def test_aibom_round_trip(self, svc, sample_model):
        from core.security.connectors import (
            build_external_spm_aibom,
        )

        c1 = svc.sign_certificate(
            svc.generate_certificate("s:1", "t:a", sample_model, b"m")
        )
        c2 = svc.sign_certificate(
            svc.generate_certificate("s:2", "t:b", sample_model, b"m")
        )
        bom = build_external_spm_aibom(
            [c1, c2],
            vendor_tenant_id="spm-tenant-1",
        )
        assert bom["@type"] == "AiBillOfMaterials"
        assert bom["schemaVersion"].startswith("ai-spm-2026")
        assert bom["summary"]["componentCount"] == 2
        assert bom["integrityProof"]["alg"] == "SHA-384"
        assert len(bom["integrityProof"]["value"]) == 96  # SHA-384 hex

    def test_aibom_integrity_changes_on_component_tamper(self, svc, sample_model):
        from core.security.connectors import build_external_spm_aibom

        certs = [
            svc.sign_certificate(
                svc.generate_certificate("s:1", "t:a", sample_model, b"m")
            )
        ]
        baseline = build_external_spm_aibom(certs, vendor_tenant_id="t1")
        certs[0].tenant_id = "tampered"
        tampered_bom = build_external_spm_aibom(certs, vendor_tenant_id="t1")
        assert (
            baseline["integrityProof"]["value"]
            != tampered_bom["integrityProof"]["value"]
        )

    def test_back_compat_alias_still_resolves(self, svc, sample_model):
        """v20.5.1 names must still import for any external caller."""
        from core.security.connectors import WizConnector, build_wiz_aibom
        from core.security.connectors import SpmConnector

        assert WizConnector is SpmConnector
        certs = [
            svc.sign_certificate(
                svc.generate_certificate("s:1", "t:a", sample_model, b"m")
            )
        ]
        bom_via_alias = build_wiz_aibom(certs, vendor_tenant_id="t1")
        assert bom_via_alias["@type"] == "AiBillOfMaterials"


class TestRuntimeFirewallAdapter:

    def test_egress_deny_maps_to_high_severity(self):
        from core.security.connectors import (
            RuntimeFirewallAdapter,
            VOS3KernelEvent,
        )

        a = RuntimeFirewallAdapter(firewall_tenant_id="rtaf-1")
        ev = a.to_firewall(
            VOS3KernelEvent(
                code="EGRESS_DENY_PUBLIC_IP",
                subject_kind="session",
                subject_id="t:s",
                tenant_id="t",
                evidence={"dst": "1.2.3.4"},
            )
        )
        assert ev["errorId"] == "RTAF.NETWORK.EGRESS.PUBLIC_DENY"
        assert ev["severity"] == "high"
        assert ev["context"]["vos3KernelCode"] == "EGRESS_DENY_PUBLIC_IP"

    def test_unknown_code_maps_to_generic(self):
        from core.security.connectors import (
            RuntimeFirewallAdapter,
            VOS3KernelEvent,
        )

        a = RuntimeFirewallAdapter()
        ev = a.to_firewall(
            VOS3KernelEvent(
                code="VOS3_NEVER_SEEN",
                subject_kind="agent",
                subject_id="x",
                tenant_id="t",
            )
        )
        assert ev["errorId"].startswith("RTAF.GENERIC")

    def test_back_compat_alias_still_resolves(self):
        from core.security.connectors import PrismaAdapter
        from core.security.connectors import RuntimeFirewallAdapter

        assert PrismaAdapter is RuntimeFirewallAdapter


class TestEdrEventRelay:

    def test_kernel_event_mapped_to_aidetection(self):
        from core.security.connectors import EdrEventRelay, VOS3KernelEvent

        r = EdrEventRelay(customer_id="cs-1")
        out = r.relay_kernel(
            VOS3KernelEvent(
                code="SCHED_CORE_SIBLING_INCOMPATIBLE",
                subject_kind="session",
                subject_id="t:s",
                tenant_id="t",
            )
        )
        assert out["metadata"]["eventType"] == "AIDetection"
        assert out["event"]["DetectName"].startswith("VOS3.SchedCore")
        assert out["event"]["Severity"] == 4  # high

    def test_attestation_mapped_to_aiattested(self, svc, sample_model):
        from core.security.connectors import EdrEventRelay

        cert = svc.sign_certificate(
            svc.generate_certificate("s:1", "t:1", sample_model, b"m")
        )
        r = EdrEventRelay(customer_id="cs-1")
        out = r.relay_attestation(cert)
        assert out["metadata"]["eventType"] == "AIAttested"
        assert out["event"]["DetectName"] == "VOS3.Attestation.SessionFinalized"
        assert out["event"]["Severity"] == 1  # informational
        assert out["event"]["Evidence"]["attestationId"] == cert.id

    def test_offset_monotonic(self, svc, sample_model):
        from core.security.connectors import EdrEventRelay, VOS3KernelEvent

        r = EdrEventRelay(customer_id="cs-1")
        a = r.relay_kernel(
            VOS3KernelEvent(
                code="EGRESS_DENY_PUBLIC_IP",
                subject_kind="s",
                subject_id="x",
                tenant_id="t",
            )
        )
        b = r.relay_kernel(
            VOS3KernelEvent(
                code="EGRESS_DENY_PUBLIC_IP",
                subject_kind="s",
                subject_id="y",
                tenant_id="t",
            )
        )
        assert b["metadata"]["offset"] == a["metadata"]["offset"] + 1

    def test_back_compat_alias_still_resolves(self):
        from core.security.connectors import FalconRelay, EdrEventRelay

        assert FalconRelay is EdrEventRelay


# --------------------------------------------------------------------------
# v20.5.1-RECLAMATION — rotation manager (Doomsday B20)
# --------------------------------------------------------------------------


class TestRotationManager:

    def test_first_rotation_no_old_keys(self, tmp_path):
        from core.security.rotation_manager import RotationManager

        m = RotationManager(key_dir=tmp_path)
        ev = m.rotate(reason="scheduled")
        assert ev.old_p256_fp is None
        assert ev.new_p256_fp
        assert len(ev.new_p256_fp) == 16
        # No OLD-key signature on first rotation.
        assert ev.transition_signature_b64 == ""
        assert ev.transition_signature_new_b64

    def test_subsequent_rotation_signs_with_old_and_new(self, tmp_path):
        from core.security.rotation_manager import RotationManager

        m = RotationManager(key_dir=tmp_path)
        m.rotate(reason="scheduled")
        ev2 = m.rotate(reason="leak")
        assert ev2.old_p256_fp is not None
        assert ev2.new_p256_fp != ev2.old_p256_fp
        assert ev2.transition_signature_b64  # signed by OLD
        assert ev2.transition_signature_new_b64  # signed by NEW

    def test_revocation_recorded(self, tmp_path):
        from core.security.rotation_manager import RotationManager

        m = RotationManager(key_dir=tmp_path)
        first = m.rotate(reason="scheduled")
        m.rotate(reason="leak")
        # The first rotation's keys should be in the revocation list.
        assert m.is_revoked(first.new_p256_fp)
        assert m.is_revoked(first.new_p521_fp)
        revoked = m.revocation_list()
        assert any(r["reason"] == "leak" for r in revoked)

    def test_rejects_unknown_reason(self, tmp_path):
        from core.security.rotation_manager import RotationManager
        import pytest as _pt

        m = RotationManager(key_dir=tmp_path)
        with _pt.raises(ValueError):
            m.rotate(reason="oops-typo")

    def test_history_grows_with_each_rotation(self, tmp_path):
        from core.security.rotation_manager import RotationManager

        m = RotationManager(key_dir=tmp_path)
        m.rotate(reason="scheduled")
        m.rotate(reason="precautionary")
        m.rotate(reason="leak")
        h = m.history()
        assert len(h) == 3
        assert [e["reason"] for e in h] == ["scheduled", "precautionary", "leak"]


# --------------------------------------------------------------------------
# v20.6-OMNIPRESENCE — vault pool, sigstore mock
# --------------------------------------------------------------------------


class TestVaultPool:

    def test_store_and_get(self, tmp_path):
        from core.repositories.vault_pool import VaultPool

        p = VaultPool(tmp_path / "p.db", pool_size=4)
        try:
            p.store(cert_id="c-1", tenant_id="t1", issued_ts=1, payload={"x": 42})
            got = p.get("c-1")
            assert got == {"x": 42}
        finally:
            p.close_all()

    def test_batched_insert_count(self, tmp_path):
        from core.repositories.vault_pool import VaultPool

        p = VaultPool(tmp_path / "p.db", pool_size=4)
        try:
            rows = [
                {
                    "cert_id": f"c-{i}",
                    "tenant_id": f"t-{i % 4}",
                    "issued_ts": 1000 + i,
                    "payload": {"i": i},
                }
                for i in range(100)
            ]
            n = p.store_many(rows)
            assert n == 100
            stats = p.stats()
            assert stats.batches == 1
            assert stats.inserts == 100
        finally:
            p.close_all()

    def test_list_by_tenant_filters(self, tmp_path):
        from core.repositories.vault_pool import VaultPool

        p = VaultPool(tmp_path / "p.db", pool_size=4)
        try:
            for i in range(10):
                p.store(cert_id=f"a-{i}", tenant_id="A", issued_ts=i, payload={"i": i})
            for i in range(5):
                p.store(cert_id=f"b-{i}", tenant_id="B", issued_ts=i, payload={"j": i})
            assert len(p.list_by_tenant("A")) == 10
            assert len(p.list_by_tenant("B")) == 5
            assert p.list_by_tenant("missing") == []
        finally:
            p.close_all()


class TestSigstoreMock:

    def _import(self):
        import sys

        sys.path.insert(0, "..")
        from infra.security.sigstore_mock import (
            MockBundleBuilder,
            MockBundleVerifier,
            MOCK_TRUST_TIER,
        )

        return MockBundleBuilder, MockBundleVerifier, MOCK_TRUST_TIER

    def test_round_trip_verifies(self):
        Builder, Verifier, tier = self._import()
        artifact = b"VOS3-test-bytes-" + b"A" * 256
        bundle = Builder().build(
            artifact, log_index=2, other_entries=[b"x", b"y", b"z", b"w"]
        )
        result = Verifier().verify(artifact, bundle)
        assert result.overall_valid is True
        assert result.trust_tier == tier == "mock"
        assert result.payload_digest_valid
        assert result.signature_valid
        assert result.inclusion_proof_valid
        assert result.timestamp_valid

    def test_artifact_tamper_rejected(self):
        Builder, Verifier, _ = self._import()
        artifact = b"VOS3-test-bytes"
        bundle = Builder().build(artifact)
        result = Verifier().verify(artifact + b"!", bundle)
        assert result.overall_valid is False
        assert not result.payload_digest_valid
        # The bundle's sig is over the original bytes; flipped bytes
        # also fail the signature check → both flags are False.
        assert not result.signature_valid

    def test_inclusion_path_corruption_rejected(self):
        Builder, Verifier, _ = self._import()
        artifact = b"foo"
        bundle = Builder().build(artifact)
        # Corrupt one sibling hash in the merkle path.
        bundle["inclusionProof"]["merklePath"][0]["hash"] = (
            "ZZZZ" + bundle["inclusionProof"]["merklePath"][0]["hash"][4:]
        )
        result = Verifier().verify(artifact, bundle)
        assert result.inclusion_proof_valid is False
        assert result.overall_valid is False

    def test_trust_tier_is_explicitly_mock(self):
        """Anyone reading verdict.trust_tier must see 'mock' so they
        cannot accidentally promote a CI-mock-pass to a Sigstore-pass."""
        Builder, Verifier, _ = self._import()
        result = Verifier().verify(b"x", Builder().build(b"x"))
        assert result.trust_tier == "mock"


# --------------------------------------------------------------------------
# v20.7-APEX — Z3 Merkle inclusion-proof structural equivalence
# --------------------------------------------------------------------------


class TestMerkleInclusionZ3:
    """The Z3 proof in tests/benchmarks/merkle_inclusion_z3_proof.py
    must remain UNSAT — i.e., the verifier's path-walk is structurally
    equivalent to the RFC 6962 honest-builder under uninterpreted hash."""

    def test_merkle_inclusion_z3_unsat(self):
        import subprocess
        import sys
        from pathlib import Path

        proof = (
            Path(__file__).resolve().parents[1]
            / "benchmarks"
            / "merkle_inclusion_z3_proof.py"
        )
        assert proof.is_file(), f"missing proof script at {proof}"
        result = subprocess.run(
            [sys.executable, str(proof)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"Z3 Merkle proof did NOT return UNSAT (exit={result.returncode}). "
            f"stdout tail:\n{result.stdout[-500:]}"
        )
        assert "UNSAT" in result.stdout
        assert "STRUCTURAL EQUIVALENCE PROVEN" in result.stdout


# --------------------------------------------------------------------------
# v20.7-APEX — apex_sim.py reproducibility + target-met assertions
# --------------------------------------------------------------------------


class TestApexSim:
    """apex_sim must produce reproducible numbers under a fixed seed,
    and the modeled targets that PASS in the dashboard must keep passing
    so a future regression surfaces in CI."""

    def test_seed_reproducibility(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from apex_sim import build_tpc  # noqa: E402

        a = build_tpc(seed=42)
        b = build_tpc(seed=42)
        # Same seed → same percentile output. Compare a few stable keys.
        assert (
            a["simulations"]["row_36_ipc"]["mean_cycles"]
            == b["simulations"]["row_36_ipc"]["mean_cycles"]
        )
        assert (
            a["simulations"]["row_40_recovery"]["p99_ms"]
            == b["simulations"]["row_40_recovery"]["p99_ms"]
        )

    def test_modeled_targets_met(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from apex_sim import build_tpc  # noqa: E402

        tpc = build_tpc(seed=42)
        s = tpc["summary"]
        # The targets we DO expect to pass under the modeled assumptions:
        assert s["row_6_p99_under_10us_target_met"]
        assert s["row_36_p99_under_2us_target_met"]
        assert s["row_40_p99_under_5ms_target_met"]
        # Row 39 p99.99 includes preemption-event tail and is honestly
        # documented as "OVER" in the dashboard. The model says so;
        # we assert the model's value is what the dashboard claims.
        assert not s["row_39_p99_99_under_500ns_target_met"]

    def test_tpc_carries_honesty_label(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from apex_sim import build_tpc  # noqa: E402

        tpc = build_tpc(seed=0)
        # The certificate must self-identify as MODELED, not measured.
        assert "MODELED" in tpc["honestyLabel"]
        assert "NOT on-silicon" in tpc["honestyLabel"]
