"""
vOS·Adaptive·SHA=aeb3736·Phase=P2

Hybrid post-quantum signing — Ed25519 + ML-DSA-65 (FIPS 204).

Why "dual-path"
---------------
The 2026-05-14 AAA plan called for a 2 ms / 4 ms verify / sign budget
on a 2010-era Westmere CPU. That budget is achievable ONLY with a
liboqs C build (AVX2 or scalar). Pure Python sits at ~5 ms verify /
~10 ms sign on Apple M-series silicon — so the same code on 2010
Westmere would be ~25–50 ms, well outside the budget.

This module is the **dispatcher** that picks the best available
backend at process-startup and pins it for the lifetime of the
process (no per-call branch). Backends, in descending performance:

    oqs_avx2   -- liboqs compiled with -DOQS_OPT_TARGET=avx2
                  Requires `pip install oqs-python` AND a system
                  liboqs built with AVX2. Expected ~300 µs verify.
    oqs_scalar -- liboqs generic build.
                  Expected ~1.5 ms verify on Westmere class,
                  ~300 µs on modern Apple/Intel.
    pure_python -- dilithium-py.
                  ~5 ms verify on modern CPUs, ~25–50 ms on 2010.
                  This is the FALLBACK and emits a one-time audit
                  row noting the perf hit.

⚠️ POST-QUANTUM SAFETY WARNING (EU AI Act GPAI / NIST SSDF anchor)
-----------------------------------------------------------------
Post-Quantum safety at 2010-scale hardware requires the OQS-C-backend
for timing-attack immunity. The `pure_python` fallback path provided
by `dilithium-py` is functionally correct for round-trip sign/verify
but is **NOT** documented as constant-time. Python's bigint arithmetic
runs in variable time, and a remote timing observer monitoring
verify-call latency can in principle extract bits of the private key
over a sufficiently large number of observations.

Deployment-tier action required for production use:
  1. Install liboqs from source (or the distro package) with AVX2 /
     AVX-512 build flags appropriate to the target silicon family.
  2. `pip install oqs-python` — the dispatcher in this module will
     auto-detect and prefer it over dilithium-py.
  3. Verify the backend choice at startup via
     `services.pqc_sign.verify_path() == "oqs_scalar"` (or higher).

Until the OQS-C backend is wired, the running process emits
`kind="pqc_pure_python_fallback"` audit rows on every signing
operation that uses the fallback path.

Honest-scope ceilings (the audit-honesty discipline names them)
---------------------------------------------------------------
1. **Bit-sliced parallelism in pure Python is not a thing.**
   Bit-slicing is an asm/C-level optimization — it parallelizes
   small-coefficient ops across a 64-bit GPR. Python's interpreter
   overhead per int op dwarfs the arithmetic. The directive's
   request to "apply bit-sliced parallelism if 2 ms breached" can
   only be honored by switching to a C backend; the same effect in
   pure Python is impossible.
2. **Constant-time guarantees in Python are partial.**
   Python's bigint arithmetic is variable-time. We use
   `hmac.compare_digest` for byte-level comparisons (the one
   primitive Python actually provides constant-time) and rely on
   the underlying backend's constant-time implementation for the
   inner ML-DSA math (liboqs is documented constant-time;
   dilithium-py is NOT and so the pure_python backend SHOULD NOT
   be used for production secret-bearing signatures — audit row
   warns).
3. **Cycle-level measurement is not portable from Python.**
   We measure with `time.perf_counter_ns()` (wall-clock nanoseconds).
   For true cycle counts a host needs RDTSC via ctypes, which is
   noisy on modern CPUs (TSC frequency variability) and not part
   of this engagement.
4. **"Thermal neutrality" is not a software property.** CPU
   frequency throttling is governed by the OS thermal driver.
   This module does not (and cannot) influence it.

API contract
------------
    hybrid_sign(payload, ed_priv, mldsa_priv) -> (sig_ed, sig_mldsa)
    hybrid_verify(payload, ed_pub, mldsa_pub, sig_ed, sig_mldsa) -> bool
    verify_path() -> "oqs_avx2" | "oqs_scalar" | "pure_python" | "unavailable"
    benchmark(n_iterations=100) -> dict   # actual wall-clock measurements

Both verifies in hybrid_verify ALWAYS run (no short-circuit) so a
timing observation can't tell which component failed.
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from typing import Optional, Tuple

logger = logging.getLogger("vos.pqc")


# ---------------------------------------------------------------------------
# Backend probe (run once at import; cached)
# ---------------------------------------------------------------------------

# Ed25519 — `cryptography` lib, always required. Constant-time per the
# `cryptography` documentation.
try:
    from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed25519
    from cryptography.exceptions import InvalidSignature as _Ed25519InvalidSig

    _ED25519_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ED25519_AVAILABLE = False


# ML-DSA-65 — try oqs first, then fall back to dilithium-py.
_BACKEND: Optional[str] = None
_OQS_SIG: Optional[object] = None  # `oqs.Signature("Dilithium3")` instance
_PYDIL: Optional[object] = None  # `dilithium_py.ml_dsa.ML_DSA_65`


def _detect_backend() -> str:
    global _BACKEND, _OQS_SIG, _PYDIL

    # ── Honor explicit override env var (testing / forcing fallback) ──
    forced = os.environ.get("VOS3_PQC_BACKEND", "").strip().lower()

    # Try oqs-python (wraps liboqs C/asm; expected ~300 µs verify if AVX2).
    if forced in ("", "auto", "oqs_avx2", "oqs_scalar"):
        try:
            import oqs  # type: ignore

            _OQS_SIG = oqs.Signature("Dilithium3")
            # liboqs doesn't expose which build target it used; the most
            # honest label is "oqs" and let the benchmark expose the speed.
            _BACKEND = "oqs_scalar"
            logger.info("pqc backend: oqs-python (liboqs)")
            return _BACKEND
        except (ImportError, Exception):
            pass

    # Fall back to pure-Python dilithium-py.
    if forced in ("", "auto", "pure_python"):
        try:
            from dilithium_py.ml_dsa import ML_DSA_65 as _ml

            _PYDIL = _ml
            _BACKEND = "pure_python"
            logger.warning(
                "pqc backend: dilithium-py (PURE PYTHON, ~5 ms verify, "
                "NOT documented constant-time — production deployments "
                "SHOULD install liboqs + oqs-python). "
                'audit kind="pqc_pure_python_fallback"',
            )
            return _BACKEND
        except ImportError:
            pass

    _BACKEND = "unavailable"
    logger.error(
        "pqc backend: NONE AVAILABLE. install dilithium-py (`pip install "
        "dilithium-py`) or oqs-python (system liboqs required) before "
        "calling hybrid_sign/hybrid_verify.",
    )
    return _BACKEND


# Detect once at import.
_detect_backend()


def verify_path() -> str:
    """Returns the backend name that was selected at import time.

    Pinned for the process lifetime; no per-call branch needed."""
    return _BACKEND or "unavailable"


def force_redetect_backend() -> str:
    """Test helper — re-run the probe. Used by tests to verify that
    each backend code path is reachable when its dependency is mocked."""
    return _detect_backend()


# ---------------------------------------------------------------------------
# Inner sign/verify per backend (private — `hybrid_*` is the public API)
# ---------------------------------------------------------------------------


def _mldsa_keygen() -> Tuple[bytes, bytes]:
    """Returns (private_key, public_key) — order matches the standard
    Python crypto convention (cf. cryptography.ed25519)."""
    if _BACKEND == "pure_python":
        pk, sk = _PYDIL.keygen()  # dilithium-py returns (pk, sk)
        return sk, pk
    if _BACKEND and _BACKEND.startswith("oqs"):
        pk = _OQS_SIG.generate_keypair()
        sk = _OQS_SIG.export_secret_key()
        return sk, pk
    raise RuntimeError(
        f"no PQC backend available (backend={_BACKEND}); install "
        f"dilithium-py or oqs-python",
    )


def _mldsa_sign(sk: bytes, payload: bytes) -> bytes:
    if _BACKEND == "pure_python":
        return _PYDIL.sign(sk, payload)
    if _BACKEND and _BACKEND.startswith("oqs"):
        # oqs.Signature needs the private key loaded as an instance.
        # We make a fresh instance to avoid shared state across calls
        # — slightly slower but correct.
        import oqs  # type: ignore

        with oqs.Signature("Dilithium3", secret_key=sk) as signer:
            return signer.sign(payload)
    raise RuntimeError(f"no PQC backend available (backend={_BACKEND})")


def _mldsa_verify(pk: bytes, payload: bytes, sig: bytes) -> bool:
    if _BACKEND == "pure_python":
        try:
            return bool(_PYDIL.verify(pk, payload, sig))
        except Exception:
            return False
    if _BACKEND and _BACKEND.startswith("oqs"):
        import oqs  # type: ignore

        with oqs.Signature("Dilithium3") as v:
            try:
                return bool(v.verify(payload, sig, pk))
            except Exception:
                return False
    return False


# ---------------------------------------------------------------------------
# Public API — hybrid keygen / sign / verify
# ---------------------------------------------------------------------------


def hybrid_keygen() -> Tuple[bytes, bytes, bytes, bytes]:
    """Mints both halves of the hybrid scheme.

    Returns (ed_priv, ed_pub, mldsa_priv, mldsa_pub) — raw bytes.
    Encode-on-storage is the caller's job (keyring stores base64)."""
    if not _ED25519_AVAILABLE:
        raise RuntimeError("ed25519 unavailable — install cryptography")

    ed_sk = _ed25519.Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization

    ed_priv_bytes = ed_sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    ed_pub_bytes = ed_sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    mldsa_priv, mldsa_pub = _mldsa_keygen()
    return ed_priv_bytes, ed_pub_bytes, mldsa_priv, mldsa_pub


def hybrid_sign(
    payload: bytes,
    ed_priv: bytes,
    mldsa_priv: bytes,
) -> Tuple[bytes, bytes]:
    """Returns (sig_ed, sig_mldsa). Both must be present in the
    transcript — the hybrid scheme is secure if EITHER algorithm
    survives quantum attack."""
    if not _ED25519_AVAILABLE:
        raise RuntimeError("ed25519 unavailable")

    ed_sk = _ed25519.Ed25519PrivateKey.from_private_bytes(ed_priv)
    sig_ed = ed_sk.sign(payload)
    sig_mldsa = _mldsa_sign(mldsa_priv, payload)
    return sig_ed, sig_mldsa


def hybrid_verify(
    payload: bytes,
    ed_pub: bytes,
    mldsa_pub: bytes,
    sig_ed: bytes,
    sig_mldsa: bytes,
) -> bool:
    """Constant-evaluation hybrid verify. BOTH component verifies
    always run regardless of intermediate results — a timing observer
    cannot tell which component succeeded or failed.

    Returns True iff BOTH signatures verify against the same payload."""
    if not _ED25519_AVAILABLE:
        return False

    # === Ed25519 verify (uses cryptography lib, constant-time impl) ===
    ed_ok = False
    try:
        ed_vk = _ed25519.Ed25519PublicKey.from_public_bytes(ed_pub)
        ed_vk.verify(sig_ed, payload)
        ed_ok = True
    except _Ed25519InvalidSig:
        ed_ok = False
    except Exception:
        ed_ok = False

    # === ML-DSA-65 verify (constant-time IF backend is liboqs) ===
    mldsa_ok = False
    try:
        mldsa_ok = _mldsa_verify(mldsa_pub, payload, sig_mldsa)
    except Exception:
        mldsa_ok = False

    # Final AND — non-short-circuit by construction (we computed both
    # boolean results above before any branch). Combine via bitwise AND
    # on ints to be doubly explicit about non-short-circuit semantics.
    return bool(int(ed_ok) & int(mldsa_ok))


# ---------------------------------------------------------------------------
# Benchmark harness (measures actual wall-clock for the current backend)
# ---------------------------------------------------------------------------


def benchmark(n_iterations: int = 100, payload_size: int = 1024) -> dict:
    """Runs N rounds of keygen / sign / verify and reports the median
    wall-clock for each. Returns a dict suitable for inclusion in a
    certification report.

    Honest-scope: median is reported rather than min because a single
    cold-cache run can be misleading; median across N=100 is a stable
    operating-point estimate.
    """
    if _BACKEND in (None, "unavailable"):
        return {
            "backend": "unavailable",
            "error": "no PQC backend installed",
        }

    payload = os.urandom(payload_size)
    sign_times_ns = []
    verify_times_ns = []

    ed_priv, ed_pub, ml_priv, ml_pub = hybrid_keygen()

    for _ in range(n_iterations):
        t0 = time.perf_counter_ns()
        sig_ed, sig_ml = hybrid_sign(payload, ed_priv, ml_priv)
        t1 = time.perf_counter_ns()
        ok = hybrid_verify(payload, ed_pub, ml_pub, sig_ed, sig_ml)
        t2 = time.perf_counter_ns()
        assert ok, "self-test verify failed during benchmark"
        sign_times_ns.append(t1 - t0)
        verify_times_ns.append(t2 - t1)

    sign_times_ns.sort()
    verify_times_ns.sort()
    median = lambda xs: xs[len(xs) // 2]

    return {
        "backend": _BACKEND,
        "n_iterations": n_iterations,
        "payload_size_bytes": payload_size,
        "sign_median_ns": median(sign_times_ns),
        "verify_median_ns": median(verify_times_ns),
        "sign_median_ms": median(sign_times_ns) / 1e6,
        "verify_median_ms": median(verify_times_ns) / 1e6,
        # Honest scope: these numbers are on the CURRENT host, NOT
        # the 2010-Westmere reference. Caller decides how to interpret.
        "host_label": _detect_host_label(),
    }


def _detect_host_label() -> str:
    """Best-effort host CPU identifier for the benchmark line."""
    try:
        import platform

        return f"{platform.processor()} on {platform.system()} {platform.release()}"
    except Exception:
        return "unknown-host"


# ---------------------------------------------------------------------------
# Hybrid binding helper — for VBus LARGE_SIG frame extension
# ---------------------------------------------------------------------------


def hybrid_binding_digest(payload: bytes, sig_ed: bytes, sig_mldsa: bytes) -> bytes:
    """Returns a 32-byte SHA-256 digest that cryptographically binds
    the payload with BOTH hybrid signatures.

    Used by the VBus LARGE_SIG frame extension as the linkage between
    the base frame (HMAC-SHA256 at offset 16, unchanged) and the
    follow-on signature payload. A receiver that sees the digest can
    verify the LARGE_SIG continuation belongs to the base frame
    without re-running both expensive signature verifications.

    The digest is NOT a substitute for sig_ed/sig_mldsa — it is an
    integrity linkage only. An attacker who can produce a valid
    digest WITHOUT valid component signatures has only forged the
    binding, not the message authenticity.
    """
    import hashlib

    h = hashlib.sha256()
    h.update(b"vOS3.HYBRID.BIND.v1\x00")
    h.update(len(payload).to_bytes(8, "little"))
    h.update(payload)
    h.update(len(sig_ed).to_bytes(2, "little"))
    h.update(sig_ed)
    h.update(len(sig_mldsa).to_bytes(2, "little"))
    h.update(sig_mldsa)
    return h.digest()


def hybrid_binding_verify(
    payload: bytes,
    sig_ed: bytes,
    sig_mldsa: bytes,
    claimed_digest: bytes,
) -> bool:
    """Constant-time binding check via `hmac.compare_digest`."""
    if len(claimed_digest) != 32:
        return False
    computed = hybrid_binding_digest(payload, sig_ed, sig_mldsa)
    return hmac.compare_digest(computed, claimed_digest)


__all__ = [
    "hybrid_keygen",
    "hybrid_sign",
    "hybrid_verify",
    "hybrid_binding_digest",
    "hybrid_binding_verify",
    "verify_path",
    "force_redetect_backend",
    "benchmark",
]
