"""
P5.2 — Cryptographic manifest signatures.

Coverage groups:

  1. Crypto primitives — generate / sign / verify round-trip;
     canonicalization is deterministic; bad signatures / wrong key
     return False without raising.

  2. Install pipeline — `install()` verifies the signature against
     the canonical form, persists both fields, rejects mismatches
     before writing the row, and allows partner-signed re-install
     to rotate the trust anchor.

  3. Hydration pen-test — a raw SQLite UPDATE that tampers with
     `manifest_json` triggers `ManifestTampered` on the very next
     gate check, auto-isolates the row, and lands a high-severity
     `manifest_tampered` audit event.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from sqlalchemy import update

from core.database.sqlite_setup import (
    App,
    SecurityAuditLog,
    _reset_for_tests,
    get_session,
    init_db,
)
from services.app_crypto import (
    canonical_manifest_bytes,
    generate_keypair_hex,
    is_available,
    manifest_digest_sha256,
    sign_manifest,
    to_signable_form,
    verify_manifest_dict,
    verify_manifest_signature,
)
from services.app_sandbox import (
    PERMISSION_GATE,
    SANDBOX_MANAGER,
    AppIsolated,
    InvalidManifest,
    ManifestTampered,
    _reset_gate_for_tests,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env):
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()


# ---------------------------------------------------------------------------
# (1) Crypto primitives
# ---------------------------------------------------------------------------


def test_cryptography_backend_available():
    assert is_available() is True


def test_canonical_bytes_are_stable():
    """Different key orderings produce the SAME byte stream."""
    a = canonical_manifest_bytes({"name": "x", "version": "1.0", "scopes": ["a"]})
    b = canonical_manifest_bytes({"scopes": ["a"], "version": "1.0", "name": "x"})
    assert a == b
    # Tight separators — no whitespace.
    assert b" " not in a


def test_keypair_format():
    priv_hex, pub_hex = generate_keypair_hex()
    assert len(priv_hex) == 64 and len(pub_hex) == 64
    assert all(c in "0123456789abcdef" for c in priv_hex)
    assert all(c in "0123456789abcdef" for c in pub_hex)


def test_sign_then_verify_roundtrip():
    priv, pub = generate_keypair_hex()
    manifest = {"name": "Notes", "version": "1.0", "scopes": ["filesystem.read"]}
    signable = to_signable_form(manifest)
    sig = sign_manifest(signable, private_key_hex=priv)
    assert (
        verify_manifest_signature(
            canonical_manifest_bytes(signable),
            sig,
            pub,
        )
        is True
    )
    # Convenience wrapper agrees.
    assert (
        verify_manifest_dict(
            signable,
            signature_hex=sig,
            public_key_hex=pub,
        )
        is True
    )


def test_verify_rejects_wrong_key():
    priv, _ = generate_keypair_hex()
    _, attacker_pub = generate_keypair_hex()
    manifest = {"name": "x", "version": "1.0", "scopes": []}
    sig = sign_manifest(to_signable_form(manifest), private_key_hex=priv)
    assert (
        verify_manifest_signature(
            canonical_manifest_bytes(to_signable_form(manifest)),
            sig,
            attacker_pub,
        )
        is False
    )


def test_verify_rejects_bad_signature_shape():
    """Garbage hex, wrong length, non-string — all return False
    rather than raising."""
    _, pub = generate_keypair_hex()
    canon = canonical_manifest_bytes({"name": "x", "version": "1.0"})
    assert verify_manifest_signature(canon, "zz", pub) is False
    assert verify_manifest_signature(canon, "deadbeef", pub) is False  # too short
    assert verify_manifest_signature(canon, "", pub) is False


def test_verify_returns_false_when_either_input_missing():
    assert (
        verify_manifest_dict(
            {"name": "x"},
            signature_hex=None,
            public_key_hex="aa" * 32,
        )
        is False
    )
    assert (
        verify_manifest_dict(
            {"name": "x"},
            signature_hex="bb" * 64,
            public_key_hex=None,
        )
        is False
    )


def test_manifest_digest_helper():
    d1 = manifest_digest_sha256({"a": 1, "b": 2})
    d2 = manifest_digest_sha256({"b": 2, "a": 1})
    assert d1 == d2 and len(d1) == 64


# ---------------------------------------------------------------------------
# (2) Install pipeline
# ---------------------------------------------------------------------------


def test_install_with_valid_signature_persists_columns(local_db):
    priv, pub = generate_keypair_hex()
    manifest = {
        "name": "Signed Notes",
        "version": "1.0.0",
        "scopes": ["filesystem.read"],
        "restrictions": ["network.blocked"],
    }
    signable = to_signable_form(manifest)
    sig = sign_manifest(signable, private_key_hex=priv)

    result = SANDBOX_MANAGER.install(
        manifest,
        signature_hex=sig,
        developer_public_key_hex=pub,
    )
    app_id = result["app_id"]
    record = SANDBOX_MANAGER.get(app_id)
    assert record["status"] == "active"
    with get_session() as session:
        row = session.query(App).filter_by(id=app_id).one()
        assert row.signature == sig
        assert row.developer_public_key == pub


def test_install_rejects_bad_signature(local_db):
    """A manifest that doesn't verify must NOT land in the apps table."""
    _, pub = generate_keypair_hex()
    manifest = {"name": "Bad", "version": "1.0", "scopes": []}
    bad_sig = "ab" * 64  # syntactically valid, semantically wrong

    with pytest.raises(InvalidManifest, match="did not verify"):
        SANDBOX_MANAGER.install(
            manifest,
            signature_hex=bad_sig,
            developer_public_key_hex=pub,
        )
    # No row written.
    with get_session() as session:
        assert session.query(App).count() == 0
    # Audit trail logged the install-time tamper attempt.
    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="manifest_tampered",
            )
            .all()
        )
        assert len(rows) == 1


def test_install_requires_both_or_neither_signature_fields(local_db):
    _, pub = generate_keypair_hex()
    manifest = {"name": "x", "version": "1.0"}
    with pytest.raises(InvalidManifest, match="together"):
        SANDBOX_MANAGER.install(
            manifest,
            signature_hex="ab" * 64,
            developer_public_key_hex=None,
        )
    with pytest.raises(InvalidManifest, match="together"):
        SANDBOX_MANAGER.install(
            manifest,
            signature_hex=None,
            developer_public_key_hex=pub,
        )


def test_install_unsigned_still_works_for_dev(local_db):
    """A dev / first-party install with NO signature is allowed —
    the hydration path short-circuits to 'unsigned mode' when both
    columns are NULL."""
    result = SANDBOX_MANAGER.install(
        {"name": "Dev", "version": "0.0.1", "scopes": ["llm.local"]},
    )
    app_id = result["app_id"]
    with get_session() as session:
        row = session.query(App).filter_by(id=app_id).one()
        assert row.signature is None
        assert row.developer_public_key is None
    # Gate still works.
    assert PERMISSION_GATE.check(app_id, "llm.local") is True


def test_reinstall_can_rotate_signature(local_db):
    """Re-installing with a fresh signature replaces the trust anchor."""
    priv1, pub1 = generate_keypair_hex()
    priv2, pub2 = generate_keypair_hex()
    manifest = {"name": "rotate", "version": "1.0", "scopes": []}
    signable = to_signable_form(manifest)

    sig1 = sign_manifest(signable, private_key_hex=priv1)
    res1 = SANDBOX_MANAGER.install(
        manifest,
        signature_hex=sig1,
        developer_public_key_hex=pub1,
    )
    app_id = res1["app_id"]

    # Re-install with a new keypair.
    sig2 = sign_manifest(signable, private_key_hex=priv2)
    SANDBOX_MANAGER.install(
        manifest,
        app_id=app_id,
        signature_hex=sig2,
        developer_public_key_hex=pub2,
    )
    with get_session() as session:
        row = session.query(App).filter_by(id=app_id).one()
        assert row.signature == sig2
        assert row.developer_public_key == pub2


# ---------------------------------------------------------------------------
# (3) Hydration pen-test — DATABASE TAMPERING auto-isolates
# ---------------------------------------------------------------------------


def test_database_tampering_auto_isolates_and_logs(local_db):
    """Directive's load-bearing scenario.

    1. Install a signed app with restrictive scopes.
    2. Simulate a raw SQL UPDATE that swaps `manifest_json` for a
       scope-elevated version (bypassing the install path entirely).
    3. Evict the gate cache so the next check re-hydrates.
    4. Call PERMISSION_GATE.check() — the verifier inside
       _hydrate_from_db catches the tamper:
         * row.status flips to 'isolated' immediately
         * a `manifest_tampered` audit row is appended
         * ManifestTampered (subclass of AppIsolated) is raised."""
    priv, pub = generate_keypair_hex()
    legitimate = {
        "name": "Notes",
        "version": "1.0",
        "scopes": ["filesystem.read"],  # legit: read-only
        "restrictions": ["network.blocked"],
    }
    sig = sign_manifest(to_signable_form(legitimate), private_key_hex=priv)
    result = SANDBOX_MANAGER.install(
        legitimate,
        signature_hex=sig,
        developer_public_key_hex=pub,
    )
    app_id = result["app_id"]

    # Sanity — the legit scope check passes.
    assert PERMISSION_GATE.check(app_id, "filesystem.read") is True

    # Penetration step: raw SQL UPDATE escalates scopes to include
    # something we explicitly didn't sign for.
    tampered_manifest = {
        "name": "Notes",
        "version": "1.0",
        "scopes": [
            "filesystem.read",
            "llm.cloud",  # NEW — not signed
            "network.outbound",  # NEW — not signed
        ],
        "restrictions": ["network.blocked"],
        "config": None,
    }
    tampered_json = json.dumps(tampered_manifest, separators=(",", ":"))
    with get_session() as session:
        session.execute(
            update(App).where(App.id == app_id).values(manifest_json=tampered_json)
        )
        session.commit()

    # Force the gate to re-hydrate by clearing the in-memory cache.
    # In production this would happen organically (process restart,
    # explicit toggle_scope eviction, or LRU drop).
    PERMISSION_GATE._clear(app_id)

    # The very next check MUST raise ManifestTampered.
    with pytest.raises(ManifestTampered) as exc:
        PERMISSION_GATE.check(app_id, "filesystem.read")
    assert exc.value.app_id == app_id
    # ManifestTampered IS an AppIsolated subclass — existing
    # exception handlers / 403 surfaces transparently work.
    assert isinstance(exc.value, AppIsolated)

    # The row is now isolated on disk.
    with get_session() as session:
        row = session.query(App).filter_by(id=app_id).one()
        assert row.status == "isolated"

    # A `manifest_tampered` audit row was recorded.
    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="manifest_tampered",
                appId=app_id,
            )
            .all()
        )
        assert len(rows) >= 1
        # Stage = hydrate (distinguishes from the install-time
        # tamper rows test_install_rejects_bad_signature produces).
        assert any(
            json.loads(r.details_json or "{}").get("stage") == "hydrate" for r in rows
        )


def test_unsigned_app_skips_hydration_verification(local_db):
    """An unsigned (dev-mode) row hydrates without invoking the
    verifier — there's no signature to check against."""
    res = SANDBOX_MANAGER.install(
        {"name": "DevApp", "version": "1.0", "scopes": ["llm.local"]}
    )
    app_id = res["app_id"]
    PERMISSION_GATE._clear(app_id)
    # No ManifestTampered raised — the hydration path skips the
    # verify branch when both columns are NULL.
    assert PERMISSION_GATE.check(app_id, "llm.local") is True


def test_tampered_app_emits_403_through_route_layer(local_db, network_guard):
    """End-to-end: a tampered app, when reached through the LLM
    dispatcher, surfaces as HTTPException(403) — the existing
    `_enforce_app_scope` already catches AppIsolated, and
    ManifestTampered is a subclass so no code change is needed."""
    from services.llm_dispatcher import LLMRequestContext, LocalFirstRouter

    priv, pub = generate_keypair_hex()
    manifest = {"name": "x", "version": "1.0", "scopes": ["llm.local"]}
    sig = sign_manifest(to_signable_form(manifest), private_key_hex=priv)
    result = SANDBOX_MANAGER.install(
        manifest,
        signature_hex=sig,
        developer_public_key_hex=pub,
    )
    app_id = result["app_id"]

    # Tamper.
    bad = {
        "name": "x",
        "version": "1.0",
        "scopes": ["llm.local", "llm.cloud"],
        "restrictions": [],
        "config": None,
    }
    with get_session() as session:
        session.execute(
            update(App)
            .where(App.id == app_id)
            .values(manifest_json=json.dumps(bad, separators=(",", ":")))
        )
        session.commit()
    PERMISSION_GATE._clear(app_id)

    router = LocalFirstRouter()
    with pytest.raises(HTTPException) as exc:
        router.resolve(LLMRequestContext(role="coding", app_id=app_id))
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] in (
        "app_unauthorized",
        "scope_violation",
    )
