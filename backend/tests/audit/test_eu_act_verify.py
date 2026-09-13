"""
backend/tests/audit/test_eu_act_verify.py

30 tests for the vos3_eu_act_verify tool (Layer 2, EU Act sub-suite).
Covers: happy path, layout failures, manifest shape failures, leaf hash
failures, Merkle proof failures, signature failures, output formats,
and issuer handling.  All tests are deterministic and offline.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
import time
import zipfile
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).parent.parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from vos3_eu_act_export import (  # noqa: E402
    _synthetic_leaves,
    _generate_keypair,
    build_bundle,
)
from vos3_eu_act_verify import (  # noqa: E402
    verify,
    _validate_manifest_shape,
    _check_leaf_hashes,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_bundle(tmp_path: Path, count: int = 8, name: str = "b") -> Path:
    leaves = _synthetic_leaves(count, base_ts=int(time.time() * 1_000_000_000))
    pk, sk = _generate_keypair()
    out = tmp_path / f"{name}.zip"
    build_bundle(
        leaves=leaves,
        output_path=out,
        since_utc="2026-05-01T00:00:00Z",
        until_utc="2026-05-01T01:00:00Z",
        host_kernel_sha256="b" * 64,
        host_fingerprint_sha256=hashlib.sha256(secrets.token_bytes(16)).hexdigest(),
        region_code="EU",
        is_eu_region=True,
        build_flavor="PRO",
        issuer="self",
        signing_key=sk,
        public_key_bytes=pk,
    )
    return out


@pytest.fixture
def valid(tmp_path):
    return _make_bundle(tmp_path)


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_valid_bundle_passes(valid):
    report = verify(valid)
    assert report.passed


def test_all_individual_checks_pass(valid):
    report = verify(valid)
    for c in report.checks:
        assert c.passed, f"'{c.name}' failed: {c.detail}"


def test_report_records_bundle_path(valid):
    report = verify(valid)
    assert str(valid) in report.bundle


# ---------------------------------------------------------------------------
# 2. Layout failures
# ---------------------------------------------------------------------------


def test_fail_missing_manifest(tmp_path):
    path = _make_bundle(tmp_path, name="src_noman")
    out = _remove_entry(path, "manifest.json", tmp_path)
    report = verify(out)
    assert not report.passed


def test_fail_missing_leaves(tmp_path):
    path = _make_bundle(tmp_path, name="src_noleaves")
    out = _remove_entry(path, "leaves.ndjson", tmp_path)
    report = verify(out)
    assert not report.passed


def test_fail_missing_signature(tmp_path):
    path = _make_bundle(tmp_path, name="src_nosig")
    out = _remove_entry(path, "signature.bin", tmp_path)
    report = verify(out)
    assert not report.passed


def test_fail_file_not_found(tmp_path):
    report = verify(tmp_path / "ghost.zip")
    assert not report.passed
    assert any("zip-open" in c.name for c in report.checks)


# ---------------------------------------------------------------------------
# 3. Manifest shape failures
# ---------------------------------------------------------------------------


def test_fail_wrong_schema_id(tmp_path, valid):
    out = _patch_manifest(valid, tmp_path, "schema", "wrong.v0")
    report = verify(out)
    assert not report.passed


def test_fail_wrong_sig_alg(tmp_path, valid):
    out = _patch_nested(valid, tmp_path, ["signature", "alg"], "RSA-PSS")
    report = verify(out)
    assert not report.passed


def test_fail_leaf_count_mismatch(tmp_path, valid):
    with zipfile.ZipFile(valid) as zf:
        m = json.loads(zf.read("manifest.json"))
    m["range"]["leaf_count"] = m["range"]["leaf_count"] + 99
    out = _write_back_manifest(valid, tmp_path, m, "lc_mismatch")
    report = verify(out)
    assert not report.passed


def test_validate_manifest_shape_ok(valid):
    with zipfile.ZipFile(valid) as zf:
        m = json.loads(zf.read("manifest.json"))
    ok, detail = _validate_manifest_shape(m)
    assert ok, detail


def test_validate_manifest_shape_missing_field():
    m = _minimal_manifest()
    del m["mmr_root_at_export"]
    ok, _ = _validate_manifest_shape(m)
    assert not ok


def test_validate_manifest_range_last_lt_first():
    m = _minimal_manifest()
    # last < first: set leaf_count to match arithmetic so the
    # direction guard fires before the leaf_count mismatch check.
    m["range"]["first_leaf_index"] = 5
    m["range"]["last_leaf_index"] = 2
    m["range"]["leaf_count"] = -2  # would pass arithmetic if direction not guarded
    ok, detail = _validate_manifest_shape(m)
    assert not ok
    assert "last_leaf_index" in detail and "first_leaf_index" in detail


# ---------------------------------------------------------------------------
# 4. Leaf hash failures
# ---------------------------------------------------------------------------


def test_fail_tampered_leaf_hash(tmp_path, valid):
    with zipfile.ZipFile(valid) as zf:
        lines = zf.read("leaves.ndjson").decode().splitlines()
    first = json.loads(lines[0])
    first["leaf_hash"] = first["leaf_hash"][:-4] + "0000"
    lines[0] = json.dumps(first)
    out = _replace_entry(
        valid, tmp_path, "leaves.ndjson", "\n".join(lines).encode(), "tampered_hash"
    )
    report = verify(out)
    assert not report.passed


def test_check_leaf_hashes_ok(valid):
    with zipfile.ZipFile(valid) as zf:
        leaves = [
            json.loads(ln)
            for ln in zf.read("leaves.ndjson").decode().splitlines()
            if ln.strip()
        ]
    ok, detail = _check_leaf_hashes(leaves)
    assert ok, detail


def test_check_leaf_hashes_detects_bad_hash():
    leaf = {"index": 0, "payload": "test"}
    leaf["leaf_hash"] = "00" * 32
    ok, _ = _check_leaf_hashes([leaf])
    assert not ok


def test_check_leaf_hashes_missing_leaf_hash():
    leaf = {"index": 0, "payload": "test"}
    ok, _ = _check_leaf_hashes([leaf])
    assert not ok


# ---------------------------------------------------------------------------
# 5. Merkle proof failures
# ---------------------------------------------------------------------------


def test_fail_proof_root_tampered(tmp_path, valid):
    with zipfile.ZipFile(valid) as zf:
        p = json.load(zf.open("proofs/0.json"))
    p["mmr_root"] = "00" * 32
    out = _replace_entry(
        valid, tmp_path, "proofs/0.json", json.dumps(p).encode(), "bad_root"
    )
    report = verify(out)
    assert not report.passed


def test_fail_sibling_hash_corrupted(tmp_path, valid):
    """Corrupt a sibling so the proof no longer replays to root."""
    with zipfile.ZipFile(valid) as zf:
        all_proofs = [
            (n, json.load(zf.open(n)))
            for n in zf.namelist()
            if n.startswith("proofs/") and n.endswith(".json")
        ]
    name, p = next((n, pp) for n, pp in all_proofs if pp["siblings"])
    p["siblings"][0]["h"] = "ff" * 32
    out = _replace_entry(valid, tmp_path, name, json.dumps(p).encode(), "bad_sib")
    report = verify(out)
    assert not report.passed


# ---------------------------------------------------------------------------
# 6. Signature failures
# ---------------------------------------------------------------------------


def test_fail_all_zero_signature(tmp_path, valid):
    out = _replace_entry(valid, tmp_path, "signature.bin", bytes(64), "zero_sig")
    report = verify(out)
    assert not report.passed


def test_fail_short_signature(tmp_path, valid):
    out = _replace_entry(valid, tmp_path, "signature.bin", bytes(32), "short_sig")
    report = verify(out)
    assert not report.passed
    assert any("signature-length" in c.name for c in report.checks if not c.passed)


def test_fail_truncated_public_key(tmp_path, valid):
    with zipfile.ZipFile(valid) as zf:
        m = json.loads(zf.read("manifest.json"))
    m["signature"]["public_key"] = "aa" * 16  # 16 bytes, not 32
    out = _write_back_manifest(valid, tmp_path, m, "short_pk")
    report = verify(out)
    assert not report.passed


# ---------------------------------------------------------------------------
# 7. Output formats
# ---------------------------------------------------------------------------


def test_json_output_structure(valid, capsys):
    from vos3_eu_act_verify import _emit_json

    report = verify(valid)
    _emit_json(report)
    data = json.loads(capsys.readouterr().out)
    assert "passed" in data
    assert "checks" in data
    assert isinstance(data["checks"], list)


def test_json_output_passed_true(valid, capsys):
    from vos3_eu_act_verify import _emit_json

    report = verify(valid)
    _emit_json(report)
    data = json.loads(capsys.readouterr().out)
    assert data["passed"] is True


def test_human_output_has_pass(valid, capsys):
    from vos3_eu_act_verify import _emit_human

    report = verify(valid)
    _emit_human(report)
    out = capsys.readouterr().out
    assert "PASS" in out


# ---------------------------------------------------------------------------
# 8. Issuer handling
# ---------------------------------------------------------------------------


def test_self_attested_issuer_check_present(valid):
    report = verify(valid)
    assert any("issuer-self-attested" in c.name for c in report.checks)


def test_vos3_ca_issuer_without_cert_fails(tmp_path, valid):
    out = _patch_nested(valid, tmp_path, ["signature", "issuer"], "vos3-ca")
    report = verify(out)
    assert any("vos3-ca-chain" in c.name and not c.passed for c in report.checks)


def test_report_passed_property_true(valid):
    """VerifyReport.passed is True iff all checks passed."""
    report = verify(valid)
    assert report.passed is True
    assert all(c.passed for c in report.checks)


def test_verify_single_leaf_bundle(tmp_path):
    path = _make_bundle(tmp_path, count=1, name="single")
    report = verify(path)
    assert report.passed


def test_verify_large_bundle(tmp_path):
    """32-leaf bundle must verify end-to-end cleanly."""
    path = _make_bundle(tmp_path, count=32, name="large")
    report = verify(path)
    assert report.passed, [c for c in report.checks if not c.passed]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _remove_entry(src: Path, name: str, tmp: Path) -> Path:
    out = tmp / f"rm_{name.replace('/', '_')}_{src.stem}.zip"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            if item.filename != name:
                zout.writestr(item, zin.read(item.filename))
    return out


def _replace_entry(src: Path, tmp: Path, name: str, data: bytes, tag: str) -> Path:
    out = tmp / f"{tag}.zip"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            zout.writestr(
                item, data if item.filename == name else zin.read(item.filename)
            )
    return out


def _patch_manifest(src: Path, tmp: Path, key: str, value) -> Path:
    with zipfile.ZipFile(src) as zf:
        m = json.loads(zf.read("manifest.json"))
    m[key] = value
    return _replace_entry(
        src, tmp, "manifest.json", json.dumps(m).encode(), f"patch_{key}"
    )


def _patch_nested(src: Path, tmp: Path, keys: list, value) -> Path:
    with zipfile.ZipFile(src) as zf:
        m = json.loads(zf.read("manifest.json"))
    cur = m
    for k in keys[:-1]:
        cur = cur[k]
    cur[keys[-1]] = value
    return _replace_entry(
        src, tmp, "manifest.json", json.dumps(m).encode(), "patch_nested"
    )


def _write_back_manifest(src: Path, tmp: Path, m: dict, tag: str) -> Path:
    return _replace_entry(src, tmp, "manifest.json", json.dumps(m).encode(), tag)


def _minimal_manifest() -> dict:
    return {
        "schema": "vos3.eu_act.v1",
        "generated_at_utc": "2026-01-01T00:00:00Z",
        "generator": {
            "tool": "test",
            "version": "0",
            "host_kernel_sha256": "00" * 32,
            "host_build_flavor": "test",
        },
        "host": {
            "fingerprint_sha256": "00" * 32,
            "region_code": "EU",
            "is_eu_region": True,
        },
        "range": {
            "since_utc": "2026-01-01T00:00:00Z",
            "until_utc": "2026-01-01T01:00:00Z",
            "leaf_count": 1,
            "first_leaf_index": 0,
            "last_leaf_index": 0,
        },
        "mmr_root_at_export": "00" * 32,
        "signature": {
            "alg": "Ed25519",
            "public_key": "00" * 32,
            "issuer": "self",
        },
    }
