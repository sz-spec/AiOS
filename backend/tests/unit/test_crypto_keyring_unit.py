"""
Stage 1 · Atomic unit isolation for services/crypto_keyring.py.

Covers:
  test_force_local_mode_env_override          | crypto_keyring.py:65-70
  test_probe_native_keyring_caches_result     | crypto_keyring.py:80-83
  test_probe_returns_none_under_local_mode    | crypto_keyring.py:80-81
  test_machine_seed_override_takes_precedence | crypto_keyring.py:122-124
  test_machine_seed_fallback_when_no_override | crypto_keyring.py:126-136
  test_local_storage_path_env_override        | crypto_keyring.py:139-143
  test_derive_fernet_key_deterministic        | crypto_keyring.py:151-169
  test_derive_fernet_key_seed_isolation       | crypto_keyring.py:151-169
  test_local_store_roundtrip                  | crypto_keyring.py:172-253
  test_local_store_corrupt_ciphertext_starts_fresh | crypto_keyring.py:200-205
  test_service_set_get_delete_roundtrip       | crypto_keyring.py:274-328
  test_service_overwrite_replaces_value       | crypto_keyring.py:274-292
  test_service_delete_nonexistent_false       | crypto_keyring.py:235-245
  test_service_validate_rejects_empty_inputs  | crypto_keyring.py:352-357
  test_service_validate_rejects_non_string_secret | crypto_keyring.py:278-279
  test_audit_fallback_dedupes_per_key         | crypto_keyring.py:359-395
  test_backend_label_local_fallback           | crypto_keyring.py:330-335
  test_is_native_returns_false_in_local_mode  | crypto_keyring.py:337-338
  test_get_or_mint_db_master_key_stable       | crypto_keyring.py:411-429
  test_get_or_mint_workflow_signing_key_shape | crypto_keyring.py:432-456
"""

from __future__ import annotations


import pytest


def _fresh_service(tmp_path, monkeypatch):
    """Build a SovereignKeyringService pointed at tmp_path with the
    native probe forced off and a deterministic seed."""
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "secrets.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "fresh-test-seed")
    from services.crypto_keyring import SovereignKeyringService

    s = SovereignKeyringService()
    s._reset_for_tests()
    return s


def test_force_local_mode_env_override(unit_env):
    from services.crypto_keyring import _force_local_mode

    assert _force_local_mode() is True


def test_probe_native_keyring_returns_none_under_local_mode(unit_env):
    from services.crypto_keyring import _probe_native_keyring

    assert _probe_native_keyring() is None


def test_probe_native_keyring_short_circuits_in_local_mode(unit_env):
    """`VOS3_KEYRING_MODE=local` is the first guard — returns None
    BEFORE the cache lookup or smoke test. Verifies the operator
    escape hatch works."""
    from services.crypto_keyring import _probe_native_keyring

    # Two calls — both must return None and not raise.
    assert _probe_native_keyring() is None
    assert _probe_native_keyring() is None


def test_machine_seed_override_takes_precedence(unit_env, monkeypatch):
    from services.crypto_keyring import _machine_seed

    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "explicit-seed-abc")
    assert _machine_seed() == b"explicit-seed-abc"


def test_machine_seed_falls_back_when_override_missing(unit_env, monkeypatch):
    """No VOS3_KEYRING_SEED_OVERRIDE → falls back to machine_id or
    last-resort SHA256 of home+platform. Both paths produce non-empty
    bytes; we just assert that."""
    from services.crypto_keyring import _machine_seed

    monkeypatch.delenv("VOS3_KEYRING_SEED_OVERRIDE", raising=False)
    seed = _machine_seed()
    assert isinstance(seed, (bytes, bytearray))
    assert len(seed) > 0


def test_local_storage_path_env_override(unit_env, monkeypatch, tmp_path):
    from services.crypto_keyring import _local_storage_path

    target = tmp_path / "custom" / "store.enc"
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(target))
    assert _local_storage_path() == target.resolve()


def test_derive_fernet_key_deterministic(unit_env):
    from services.crypto_keyring import _derive_fernet_key

    a = _derive_fernet_key(b"seed-X")
    b = _derive_fernet_key(b"seed-X")
    assert a == b
    # Fernet keys are 44 bytes (32 raw → urlsafe-b64).
    assert len(a) == 44


def test_derive_fernet_key_seed_isolation(unit_env):
    from services.crypto_keyring import _derive_fernet_key

    a = _derive_fernet_key(b"seed-A")
    b = _derive_fernet_key(b"seed-B")
    assert a != b


def test_local_store_roundtrip(unit_env, tmp_path):
    from services.crypto_keyring import _LocalEncryptedStore

    store = _LocalEncryptedStore()
    assert store.set("svc.x", "user", "secret-value") is True
    assert store.get("svc.x", "user") == "secret-value"
    assert store.delete("svc.x", "user") is True
    assert store.get("svc.x", "user") is None


def test_local_store_corrupt_ciphertext_starts_fresh(unit_env, tmp_path):
    """When the .enc file is garbage, _load returns {} and the next
    set/get works — i.e. we don't crash on corrupt state."""
    from services.crypto_keyring import _LocalEncryptedStore, _local_storage_path

    path = _local_storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this-is-not-fernet-ciphertext")
    store = _LocalEncryptedStore()
    # _load returns {} silently → set then get works.
    assert store.set("svc.recover", "u", "v") is True
    assert store.get("svc.recover", "u") == "v"


def test_service_set_get_delete_roundtrip(unit_env, tmp_path, monkeypatch):
    s = _fresh_service(tmp_path, monkeypatch)
    assert s.set_secret("svc", "u", "v") is True
    assert s.get_secret("svc", "u") == "v"
    assert s.delete_secret("svc", "u") is True
    assert s.get_secret("svc", "u") is None


def test_service_overwrite_replaces_value(unit_env, tmp_path, monkeypatch):
    s = _fresh_service(tmp_path, monkeypatch)
    s.set_secret("svc", "u", "v1")
    s.set_secret("svc", "u", "v2")
    assert s.get_secret("svc", "u") == "v2"


def test_service_delete_nonexistent_returns_false(unit_env, tmp_path, monkeypatch):
    s = _fresh_service(tmp_path, monkeypatch)
    assert s.delete_secret("nope", "nobody") is False


def test_service_validate_rejects_empty_inputs(unit_env, tmp_path, monkeypatch):
    s = _fresh_service(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        s.set_secret("", "u", "v")
    with pytest.raises(ValueError):
        s.set_secret("svc", "", "v")


def test_service_validate_rejects_non_string_secret(unit_env, tmp_path, monkeypatch):
    s = _fresh_service(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        s.set_secret("svc", "u", 123)  # type: ignore[arg-type]


def test_audit_fallback_dedupes_per_key(unit_env, tmp_path, monkeypatch):
    """Multiple ops against the same (service, user) write ONE audit
    row in this process — the dedupe memo holds the (svc, user) tuple."""
    s = _fresh_service(tmp_path, monkeypatch)
    s.set_secret("svc.dedupe", "u", "v1")
    s.set_secret("svc.dedupe", "u", "v2")
    s.get_secret("svc.dedupe", "u")
    s.delete_secret("svc.dedupe", "u")
    # The memo holds exactly one entry — irrespective of operation count.
    assert ("svc.dedupe", "u") in s._audited_fallback_for
    assert len(s._audited_fallback_for) == 1


def test_backend_label_reflects_local_mode(unit_env, tmp_path, monkeypatch):
    s = _fresh_service(tmp_path, monkeypatch)
    assert s.backend_label() == "local_fallback"


def test_is_native_returns_false_in_local_mode(unit_env, tmp_path, monkeypatch):
    s = _fresh_service(tmp_path, monkeypatch)
    assert s.is_native() is False


def test_get_or_mint_db_master_key_is_stable(unit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "k.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "stable-seed")
    from services.crypto_keyring import KEYRING, get_or_mint_db_master_key

    KEYRING._reset_for_tests()
    first = get_or_mint_db_master_key()
    again = get_or_mint_db_master_key()
    assert first == again
    assert len(first) >= 32


def test_get_or_mint_workflow_signing_key_shape(unit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "k.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "wf-seed")
    from services.crypto_keyring import KEYRING, get_or_mint_workflow_signing_key

    KEYRING._reset_for_tests()
    priv, pub = get_or_mint_workflow_signing_key()
    assert len(priv) == 64
    assert len(pub) == 64
    # Stable across calls.
    priv2, pub2 = get_or_mint_workflow_signing_key()
    assert (priv, pub) == (priv2, pub2)


def test_local_store_atomic_write_creates_parent_dir(unit_env, tmp_path, monkeypatch):
    """_save creates the parent dir if missing."""
    nested = tmp_path / "deep" / "nested" / "secrets.enc"
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(nested))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "nested-seed")
    from services.crypto_keyring import SovereignKeyringService

    s = SovereignKeyringService()
    s._reset_for_tests()
    assert s.set_secret("svc", "u", "v") is True
    assert nested.exists()


def test_audit_fallback_distinct_keys_each_record(unit_env, tmp_path, monkeypatch):
    """Operations against DIFFERENT (service, user) tuples each
    write their own audit memo entry."""
    s = _fresh_service(tmp_path, monkeypatch)
    s.set_secret("svc.a", "u", "v")
    s.set_secret("svc.b", "u", "v")
    s.set_secret("svc.a", "u2", "v")
    assert ("svc.a", "u") in s._audited_fallback_for
    assert ("svc.b", "u") in s._audited_fallback_for
    assert ("svc.a", "u2") in s._audited_fallback_for
    assert len(s._audited_fallback_for) == 3
