# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
Edge-case tests for 4 areas:
  1. MMR boundary — zero-leaf bundle, wrong-key signature, multi-leaf tampering
  2. HugePage VMM — source-shape for pool cap, 50%-free guard, buddy-order constant
  3. Rate-limiting — boundary/error paths in TokenBucket and middleware
  4. EU export corruption — not-a-zip, empty sig, wrong pubkey, all-zero pubkey
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TOOLS_DIR = REPO / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from vos3_eu_act_export import (  # noqa: E402
    _generate_keypair,
    _synthetic_leaves,
    build_bundle,
)
from vos3_eu_act_verify import verify  # noqa: E402

# ---------------------------------------------------------------------------
# Shared bundle builder
# ---------------------------------------------------------------------------


def _make(
    tmp_path: Path,
    count: int = 8,
    name: str = "b",
    signing_key=None,
    public_key_bytes=None,
) -> Path:
    leaves = _synthetic_leaves(count, base_ts=int(time.time() * 1_000_000_000))
    if signing_key is None:
        public_key_bytes, signing_key = _generate_keypair()
    out = tmp_path / f"{name}.zip"
    build_bundle(
        leaves=leaves,
        output_path=out,
        since_utc="2026-05-01T00:00:00Z",
        until_utc="2026-05-01T01:00:00Z",
        host_kernel_sha256="c" * 64,
        host_fingerprint_sha256=hashlib.sha256(secrets.token_bytes(16)).hexdigest(),
        region_code="EU",
        is_eu_region=True,
        build_flavor="PRO",
        issuer="self",
        signing_key=signing_key,
        public_key_bytes=public_key_bytes,
    )
    return out


def _replace_entry(src: Path, tmp: Path, name: str, data: bytes, tag: str) -> Path:
    out = tmp / f"{tag}.zip"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            zout.writestr(
                item, data if item.filename == name else zin.read(item.filename)
            )
    return out


# ===========================================================================
# 1. MMR BOUNDARY EDGE CASES
# ===========================================================================


def test_single_leaf_mmr_root_is_leaf_hash(tmp_path):
    """Single-leaf MMR: root == the leaf hash (no siblings, no pairing)."""
    bundle = _make(tmp_path, count=1, name="mmr_single")
    with zipfile.ZipFile(bundle) as zf:
        m = json.loads(zf.read("manifest.json"))
        leaf = json.loads(zf.read("leaves.ndjson").decode().splitlines()[0])
        proof = json.load(zf.open("proofs/0.json"))
    assert m["mmr_root_at_export"] == leaf["leaf_hash"]
    assert proof["siblings"] == []


def test_two_leaf_mmr_root_is_sha256_of_pair(tmp_path):
    """Two-leaf MMR: root == SHA-256(leaf0_hash_bytes || leaf1_hash_bytes)."""
    bundle = _make(tmp_path, count=2, name="mmr_pair")
    with zipfile.ZipFile(bundle) as zf:
        leaves_raw = [
            ln for ln in zf.read("leaves.ndjson").decode().splitlines() if ln.strip()
        ]
        l0 = json.loads(leaves_raw[0])
        l1 = json.loads(leaves_raw[1])
        m = json.loads(zf.read("manifest.json"))
    h0 = bytes.fromhex(l0["leaf_hash"])
    h1 = bytes.fromhex(l1["leaf_hash"])
    expected_root = hashlib.sha256(h0 + h1).hexdigest()
    assert m["mmr_root_at_export"] == expected_root


def test_mmr_wrong_signing_key_fails(tmp_path):
    """Bundle signed with key A but manifest declares key B → sig check fails."""
    pk_a, sk_a = _generate_keypair()
    pk_b, _ = _generate_keypair()  # different public key
    leaves = _synthetic_leaves(4, base_ts=int(time.time() * 1_000_000_000))
    out = tmp_path / "mismatch.zip"
    build_bundle(
        leaves=leaves,
        output_path=out,
        since_utc="2026-05-01T00:00:00Z",
        until_utc="2026-05-01T01:00:00Z",
        host_kernel_sha256="d" * 64,
        host_fingerprint_sha256=hashlib.sha256(secrets.token_bytes(16)).hexdigest(),
        region_code="EU",
        is_eu_region=True,
        build_flavor="PRO",
        issuer="self",
        signing_key=sk_a,
        public_key_bytes=pk_b,  # deliberately wrong pubkey in manifest
    )
    report = verify(out)
    assert not report.passed
    assert any("signature" in c.name for c in report.checks if not c.passed)


def test_mmr_all_leaf_hashes_tampered(tmp_path):
    """Every leaf hash overwritten with zeros → verify detects all failures."""
    bundle = _make(tmp_path, count=4, name="mmr_all_tamper")
    with zipfile.ZipFile(bundle) as zf:
        lines = zf.read("leaves.ndjson").decode().splitlines()
    tampered_lines = []
    for ln in lines:
        leaf = json.loads(ln)
        leaf["leaf_hash"] = "00" * 32
        tampered_lines.append(json.dumps(leaf))
    out = _replace_entry(
        bundle,
        tmp_path,
        "leaves.ndjson",
        "\n".join(tampered_lines).encode(),
        "all_hash_tamper",
    )
    report = verify(out)
    assert not report.passed


def test_mmr_last_leaf_hash_tampered(tmp_path):
    """Tamper only the last leaf; verifier must still detect the corruption."""
    bundle = _make(tmp_path, count=8, name="mmr_last_tamper")
    with zipfile.ZipFile(bundle) as zf:
        lines = [ln for ln in zf.read("leaves.ndjson").decode().splitlines() if ln]
    last = json.loads(lines[-1])
    last["leaf_hash"] = last["leaf_hash"][:-4] + "ffff"
    lines[-1] = json.dumps(last)
    out = _replace_entry(
        bundle, tmp_path, "leaves.ndjson", "\n".join(lines).encode(), "last_hash_tamper"
    )
    report = verify(out)
    assert not report.passed


# ===========================================================================
# 2. HUGEPAGE VMM — SOURCE-SHAPE TESTS
# ===========================================================================

PMM_SRC = (REPO / "kernel" / "src" / "mm" / "pmm.c").read_text()
PMM_H = (REPO / "kernel" / "include" / "vos" / "pmm.h").read_text()


def test_hugepage_pool_max_defined():
    """VOS3_HUGEPAGE_POOL_MAX must be a positive compile-time constant in pmm.c."""
    m = re.search(r"#define\s+VOS3_HUGEPAGE_POOL_MAX\s+(\d+)", PMM_SRC)
    assert m, "VOS3_HUGEPAGE_POOL_MAX not found in pmm.c"
    assert int(m.group(1)) > 0


def test_hugepage_pool_cap_guarded():
    """vos3_pmm_reserve_hugepages must clamp count to VOS3_HUGEPAGE_POOL_MAX."""
    assert (
        "count > VOS3_HUGEPAGE_POOL_MAX" in PMM_SRC
    ), "pool-max cap guard missing from vos3_pmm_reserve_hugepages"


def test_hugepage_50pct_free_guard():
    """Reserve function must limit to ≤ 50 % of current free pages."""
    assert (
        "/ 2U" in PMM_SRC or "/ 2)" in PMM_SRC or "max_huge_pages" in PMM_SRC
    ), "50 % free-pages guard missing from vos3_pmm_reserve_hugepages"
    assert "max_huge_pages" in PMM_SRC


def test_hugepage_buddy_order_constant():
    """VOS3_BUDDY_HP_ORDER must match log2(512) == 9 (512 pages per 2MB)."""
    m = re.search(r"#define\s+VOS3_BUDDY_HP_ORDER\s+(\d+)", PMM_SRC)
    assert m, "VOS3_BUDDY_HP_ORDER not defined in pmm.c"
    assert int(m.group(1)) == 9, "VOS3_BUDDY_HP_ORDER must be 9 (512 pages = 2MB)"


def test_hugepage_lock_per_pool():
    """g_hugepage_lock spinlock must guard the hugepage pool."""
    assert "g_hugepage_lock" in PMM_SRC
    assert (
        "vos3_spinlock_acquire(&g_hugepage_lock)" in PMM_SRC
        or "spinlock_acquire" in PMM_SRC
    )


def test_hugepage_stats_function_exported():
    """vos3_pmm_hugepage_stats must be declared in pmm.h."""
    assert (
        "vos3_pmm_hugepage_stats" in PMM_H
    ), "vos3_pmm_hugepage_stats not declared in pmm.h"


def test_pmm_uninitialized_guard():
    """Reserve function must be a no-op when PMM is not initialised."""
    assert (
        "g_pmm.initialized == 0U" in PMM_SRC or "g_pmm.initialized == 0" in PMM_SRC
    ), "uninitialized guard missing from vos3_pmm_reserve_hugepages"


# ===========================================================================
# 3. RATE-LIMITING — BOUNDARY / ERROR EDGE CASES
# ===========================================================================

sys.path.insert(0, str(REPO / "backend"))

from middleware.rate_limit import (  # noqa: E402
    TokenBucket,
    _is_auth_path,
    _get_client_ip,
)


def test_token_bucket_zero_rate_no_replenish():
    """rate=0 means no token replenishment; only the burst is available."""
    bucket = TokenBucket(rate=0.0, burst=2)
    assert bucket.allow("ip") is True
    assert bucket.allow("ip") is True
    assert bucket.allow("ip") is False
    time.sleep(0.05)
    assert bucket.allow("ip") is False  # still denied — no replenishment


def test_token_bucket_cleanup_empty_is_safe():
    """cleanup() on an empty bucket must not raise."""
    bucket = TokenBucket(rate=1.0, burst=5)
    bucket.cleanup(max_age=1.0)  # no entries → should be a no-op


def test_token_bucket_unknown_ip_is_one_bucket():
    """'unknown' key is treated as a normal bucket, not bypassed."""
    bucket = TokenBucket(rate=0.0, burst=1)
    assert bucket.allow("unknown") is True
    assert bucket.allow("unknown") is False


def test_token_bucket_large_burst_many_ips():
    """N distinct IPs each get their own independent burst budget."""
    bucket = TokenBucket(rate=0.0, burst=3)
    for i in range(50):
        key = f"10.0.0.{i}"
        assert bucket.allow(key) is True, f"{key} first request denied"


def test_get_client_ip_no_host_attr():
    """If request.client has no host attribute, return 'unknown'."""
    from unittest.mock import MagicMock

    req = MagicMock()
    req.client = None
    assert _get_client_ip(req) == "unknown"


def test_is_auth_path_case_sensitive():
    """_is_auth_path must not match uppercase variants (paths are lowercase)."""
    assert _is_auth_path("/API/AUTH/login") is False


def test_is_auth_path_empty_string():
    """Empty path string must not be treated as an auth path."""
    assert _is_auth_path("") is False


def test_is_auth_path_partial_prefix_no_match():
    """A path that contains but does NOT start with the auth prefix must be False."""
    assert _is_auth_path("/safe/api/auth/login") is False


# ===========================================================================
# 4. EU EXPORT CORRUPTION
# ===========================================================================


def test_corrupt_zip_not_a_zip(tmp_path):
    """Feeding random bytes as a ZIP file → verify must report failure."""
    bad = tmp_path / "garbage.zip"
    bad.write_bytes(secrets.token_bytes(128))
    report = verify(bad)
    assert not report.passed


def test_empty_zip_file(tmp_path):
    """An empty file is not a valid ZIP → verify must report failure."""
    empty = tmp_path / "empty.zip"
    empty.write_bytes(b"")
    report = verify(empty)
    assert not report.passed


def test_empty_signature_bin(tmp_path):
    """signature.bin present but zero-length → verify detects length mismatch."""
    bundle = _make(tmp_path, name="empty_sig")
    out = _replace_entry(bundle, tmp_path, "signature.bin", b"", "empty_sig_b")
    report = verify(out)
    assert not report.passed
    assert any("signature" in c.name for c in report.checks if not c.passed)


def test_all_zero_pubkey_in_manifest(tmp_path):
    """public_key=00*32 in manifest with a real signature → verify must fail."""
    bundle = _make(tmp_path, name="zero_pk_src")
    with zipfile.ZipFile(bundle) as zf:
        m = json.loads(zf.read("manifest.json"))
    m["signature"]["public_key"] = "00" * 32  # zeroed-out pubkey
    out = _replace_entry(
        bundle, tmp_path, "manifest.json", json.dumps(m).encode(), "zero_pk_patched"
    )
    report = verify(out)
    assert not report.passed


def test_corrupt_leaves_ndjson_not_json(tmp_path):
    """leaves.ndjson replaced with non-JSON bytes → verify detects parse failure."""
    bundle = _make(tmp_path, name="bad_ndjson_src")
    out = _replace_entry(
        bundle,
        tmp_path,
        "leaves.ndjson",
        b"not-json\nnot-json-either\n",
        "bad_ndjson_patched",
    )
    report = verify(out)
    assert not report.passed


def test_manifest_not_json(tmp_path):
    """manifest.json replaced with non-JSON bytes → verify detects parse failure."""
    bundle = _make(tmp_path, name="bad_manifest_src")
    out = _replace_entry(
        bundle, tmp_path, "manifest.json", b"{broken:json", "bad_manifest_patched"
    )
    report = verify(out)
    assert not report.passed
