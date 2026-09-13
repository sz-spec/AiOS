"""
backend/tests/audit/test_eu_act_export.py

30 tests for the vos3_eu_act_export tool (Layer 2, EU Act sub-suite).
Uses the tool's real Python API: _synthetic_leaves, _generate_keypair,
build_bundle, _canonical_leaf_bytes, _leaf_hash.
All tests are deterministic and offline.
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
    SCHEMA_ID,
    Leaf,
    _synthetic_leaves,
    _generate_keypair,
    _canonical_leaf_bytes,
    _leaf_hash,
    build_bundle,
)

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


def _make_bundle(tmp_path: Path, count: int = 8) -> tuple[Path, list[Leaf]]:
    leaves = _synthetic_leaves(count, base_ts=int(time.time() * 1_000_000_000))
    pk, sk = _generate_keypair()
    out = tmp_path / f"bundle_{count}.zip"
    build_bundle(
        leaves=leaves,
        output_path=out,
        since_utc="2026-05-01T00:00:00Z",
        until_utc="2026-05-01T01:00:00Z",
        host_kernel_sha256="a" * 64,
        host_fingerprint_sha256=hashlib.sha256(secrets.token_bytes(16)).hexdigest(),
        region_code="EU",
        is_eu_region=True,
        build_flavor="PRO",
        issuer="self",
        signing_key=sk,
        public_key_bytes=pk,
    )
    return out, leaves


@pytest.fixture
def bundle8(tmp_path):
    return _make_bundle(tmp_path, 8)


@pytest.fixture
def bundle1(tmp_path):
    return _make_bundle(tmp_path, 1)


@pytest.fixture
def bundle16(tmp_path):
    return _make_bundle(tmp_path, 16)


# ---------------------------------------------------------------------------
# 1. Manifest structure
# ---------------------------------------------------------------------------


def test_manifest_schema_id(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        m = json.loads(zf.read("manifest.json"))
    assert m["schema"] == SCHEMA_ID


def test_manifest_generated_at_utc(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        m = json.loads(zf.read("manifest.json"))
    assert "T" in m["generated_at_utc"]
    assert "Z" in m["generated_at_utc"]


def test_manifest_generator_fields(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        m = json.loads(zf.read("manifest.json"))
    gen = m["generator"]
    for field in ("tool", "version", "host_kernel_sha256", "host_build_flavor"):
        assert field in gen, f"missing generator.{field}"


def test_manifest_host_fields(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        m = json.loads(zf.read("manifest.json"))
    host = m["host"]
    for field in ("fingerprint_sha256", "region_code", "is_eu_region"):
        assert field in host


def test_manifest_range_consistency(bundle8):
    path, leaves = bundle8
    with zipfile.ZipFile(path) as zf:
        m = json.loads(zf.read("manifest.json"))
    rng = m["range"]
    assert rng["leaf_count"] == len(leaves)
    assert rng["last_leaf_index"] - rng["first_leaf_index"] + 1 == len(leaves)


def test_manifest_mmr_root_64hex(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        m = json.loads(zf.read("manifest.json"))
    root = m["mmr_root_at_export"]
    assert isinstance(root, str) and len(root) == 64
    bytes.fromhex(root)  # must be valid hex


def test_manifest_sig_alg_ed25519(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        m = json.loads(zf.read("manifest.json"))
    assert m["signature"]["alg"] == "Ed25519"


def test_manifest_public_key_32_bytes(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        m = json.loads(zf.read("manifest.json"))
    pk = bytes.fromhex(m["signature"]["public_key"])
    assert len(pk) == 32


# ---------------------------------------------------------------------------
# 2. Leaves
# ---------------------------------------------------------------------------


def test_leaves_count_matches_manifest(bundle8):
    path, leaves = bundle8
    with zipfile.ZipFile(path) as zf:
        lines = [
            ln for ln in zf.read("leaves.ndjson").decode().splitlines() if ln.strip()
        ]
    assert len(lines) == len(leaves)


def test_leaf_json_parseable(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        for ln in zf.read("leaves.ndjson").decode().splitlines():
            if ln.strip():
                json.loads(ln)


def test_leaf_has_leaf_hash(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        for ln in zf.read("leaves.ndjson").decode().splitlines():
            if ln.strip():
                obj = json.loads(ln)
                assert "leaf_hash" in obj
                assert len(obj["leaf_hash"]) == 64


def test_leaf_canonical_excludes_leaf_hash():
    leaf = {"index": 0, "foo": "bar", "leaf_hash": "should-be-excluded"}
    canon = _canonical_leaf_bytes(leaf)
    assert b"leaf_hash" not in canon


def test_leaf_hash_is_sha256_of_canonical(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        for ln in zf.read("leaves.ndjson").decode().splitlines():
            if ln.strip():
                obj = json.loads(ln)
                expected = hashlib.sha256(_canonical_leaf_bytes(obj)).hexdigest()
                assert obj["leaf_hash"] == expected


# ---------------------------------------------------------------------------
# 3. Proofs
# ---------------------------------------------------------------------------


def test_proofs_count_matches_leaves(bundle8):
    path, leaves = bundle8
    with zipfile.ZipFile(path) as zf:
        proofs = [
            n for n in zf.namelist() if n.startswith("proofs/") and n.endswith(".json")
        ]
    assert len(proofs) == len(leaves)


def test_proof_has_required_fields(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        p = json.load(zf.open("proofs/0.json"))
    for field in ("leaf_index", "leaf_hash", "mmr_root", "siblings"):
        assert field in p


def test_proof_mmr_root_matches_manifest(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        root = json.loads(zf.read("manifest.json"))["mmr_root_at_export"]
        for name in zf.namelist():
            if name.startswith("proofs/") and name.endswith(".json"):
                p = json.load(zf.open(name))
                assert p["mmr_root"] == root


def test_single_leaf_proof_no_siblings(bundle1):
    path, _ = bundle1
    with zipfile.ZipFile(path) as zf:
        p = json.load(zf.open("proofs/0.json"))
    assert p["siblings"] == []


def test_direction_tagged_siblings(bundle8):
    """v21.4.3+: siblings are {"h": hex, "side": "L"|"R"} objects."""
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.startswith("proofs/") and name.endswith(".json"):
                p = json.load(zf.open(name))
                for sib in p["siblings"]:
                    assert isinstance(sib, dict), "siblings must be dicts"
                    assert "h" in sib
                    assert sib.get("side") in ("L", "R")


# ---------------------------------------------------------------------------
# 4. Signature
# ---------------------------------------------------------------------------


def test_signature_bin_64_bytes(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        sig = zf.read("signature.bin")
    assert len(sig) == 64


def test_signature_bin_nonzero(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        sig = zf.read("signature.bin")
    assert any(b != 0 for b in sig)


# ---------------------------------------------------------------------------
# 5. Layout
# ---------------------------------------------------------------------------


def test_required_files_present(bundle8):
    path, _ = bundle8
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    assert {"manifest.json", "leaves.ndjson", "signature.bin"}.issubset(names)


def test_output_is_valid_zip(tmp_path):
    path, _ = _make_bundle(tmp_path, 4)
    assert zipfile.is_zipfile(path)


# ---------------------------------------------------------------------------
# 6. Round-trip with verifier
# ---------------------------------------------------------------------------


def test_round_trip_8_leaves(bundle8):
    from vos3_eu_act_verify import verify

    path, _ = bundle8
    report = verify(path)
    assert report.passed, [c for c in report.checks if not c.passed]


def test_round_trip_single_leaf(bundle1):
    from vos3_eu_act_verify import verify

    path, _ = bundle1
    report = verify(path)
    assert report.passed


def test_round_trip_16_leaves(bundle16):
    from vos3_eu_act_verify import verify

    path, _ = bundle16
    report = verify(path)
    assert report.passed


# ---------------------------------------------------------------------------
# 7. Determinism
# ---------------------------------------------------------------------------


def test_leaf_hash_deterministic():
    leaf_dict = {"index": 0, "val": "hello", "num": 42}
    h1 = _leaf_hash(leaf_dict)
    h2 = _leaf_hash(leaf_dict)
    assert h1 == h2


def test_canonical_bytes_sorted_keys():
    leaf = {"b": 2, "a": 1}
    canon = _canonical_leaf_bytes(leaf)
    assert canon == b'{"a":1,"b":2}'


def test_different_leaves_different_hashes():
    h1 = _leaf_hash({"index": 0, "val": "A"})
    h2 = _leaf_hash({"index": 0, "val": "B"})
    assert h1 != h2


def test_synthetic_leaves_count(tmp_path):
    leaves = _synthetic_leaves(12, base_ts=0)
    assert len(leaves) == 12


def test_synthetic_leaves_indices_sequential(tmp_path):
    leaves = _synthetic_leaves(6, base_ts=0)
    for i, leaf in enumerate(leaves):
        assert leaf.index == i
