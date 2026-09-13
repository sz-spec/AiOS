"""
backend/vos_profile.py — VOS_PROFILE dispatcher (Stage 14 / Zero-Gap Task 1).

Named `vos_profile` (not `profile`) to avoid collision with the Python
stdlib `profile` deterministic profiler module.

Single source of truth for which capabilities are gated to which deployment
profile. Every call site that conditionalises behaviour on profile (PQ
signing, Sigstore-verify mandate, SQLCipher mandate, TDX RTMR mandate,
Hyper-V build) goes through this module so the gating rules live in
exactly one place.

Profiles
========

community  — default. Engine + V-Core + smart router. No shield gating.
             Suitable for self-hosted dev, low-stakes deployments. Plain
             SQLite is fine; ECDSA P-256 attestation is fine; Sigstore
             verification is advisory.

enterprise — adds: Convex audit, EDR/SPM/firewall connectors, hybrid
             P-256 + P-521 attestation, Sigstore-verify is *recommended*.
             Plain SQLite still acceptable.

fortress   — adds: Intel TDX RTMR ladder mandatory, ML-DSA-65 PQ
             signature mandatory (alongside P-256+P-521), Sigstore-verify
             is REQUIRED for every artifact load, SQLCipher encrypted
             compliance store is REQUIRED. Hyper-V kernel build (-DVOS3_TARGET_HYPERV)
             is the canonical fortress kernel image. Refusing any of these
             gates fails closed.

How to use
==========

    from profile import get_profile, is_fortress

    if is_fortress():
        # require ML-DSA-65 + Sigstore + SQLCipher
        ...

Validation
==========

Reading `VOS_PROFILE=anything-else` raises `RuntimeError` at import time
of this module — fail-fast on misconfiguration rather than silently
falling back to community. Empty / unset → community.
"""

from __future__ import annotations

import enum
import os


class Profile(enum.Enum):
    COMMUNITY = "community"
    ENTERPRISE = "enterprise"
    FORTRESS = "fortress"

    def requires_pq_sig(self) -> bool:
        """Whether ML-DSA-65 must be in the signing chain."""
        return self is Profile.FORTRESS

    def requires_hybrid_classical(self) -> bool:
        """Whether P-521 must be co-signed alongside P-256."""
        return self in (Profile.ENTERPRISE, Profile.FORTRESS)

    def requires_sigstore_verify(self) -> bool:
        """Whether artifact loads MUST verify a Sigstore v3 bundle."""
        return self is Profile.FORTRESS

    def requires_encrypted_compliance_store(self) -> bool:
        """Whether the compliance event store MUST be SQLCipher (not plain SQLite)."""
        return self is Profile.FORTRESS

    def requires_tdx_attestation(self) -> bool:
        """Whether RTMR ladder must be live (no BAREMETAL_NO_TEE fallback)."""
        return self is Profile.FORTRESS

    def expects_hyperv_kernel(self) -> bool:
        """Whether the canonical kernel for this profile is vos3-hyperv.elf."""
        return self is Profile.FORTRESS


def _read_env() -> Profile:
    raw = os.environ.get("VOS_PROFILE", "").strip().lower()
    if not raw:
        return Profile.COMMUNITY
    try:
        return Profile(raw)
    except ValueError:
        valid = ", ".join(p.value for p in Profile)
        raise RuntimeError(
            f"VOS_PROFILE={raw!r} is not a valid profile. Expected one of: {valid}."
        )


_PROFILE: Profile = _read_env()


def get_profile() -> Profile:
    """Return the active profile. Cached at import time."""
    return _PROFILE


def is_community() -> bool:
    return _PROFILE is Profile.COMMUNITY


def is_enterprise() -> bool:
    return _PROFILE is Profile.ENTERPRISE


def is_fortress() -> bool:
    return _PROFILE is Profile.FORTRESS


def reload_for_test() -> Profile:
    """Re-read VOS_PROFILE from env. ONLY for tests that mutate os.environ.

    Production code must not call this. The profile is meant to be a
    boot-time constant.
    """
    global _PROFILE
    _PROFILE = _read_env()
    return _PROFILE
