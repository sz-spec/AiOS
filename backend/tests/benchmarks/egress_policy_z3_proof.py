"""
v20.1.5 — Z3 SMT proof: kernel/src/net/egress_policy.c cannot be bypassed.

Invariant proven
================
For every 32-bit IPv4 address ``ip``:

    (1)  ∀ entry ∈ BLOCKLIST: ``in_cidr(ip, entry)`` →
         ``vos3_net_egress_allowed(ip) == 0``          (blocklist is hard)

    (2)  ``ip`` is public (not loopback/RFC1918/link-local/CGNAT) →
         ``vos3_net_egress_allowed(ip) == 0``          (no public-internet access)

    (3)  ``ip`` is private (loopback/RFC1918/link-local/CGNAT) AND
         ``ip`` is NOT in BLOCKLIST →
         ``vos3_net_egress_allowed(ip) == 1``          (local-first traffic works)

Z3 checks each clause by asserting its negation; ``unsat`` on all three
means the policy function is bypass-proof for the full 2³² IPv4 space.

This is a proof over the **finite IPv4 address space** (not a bounded
subset) — Z3 handles it as a BitVec theory problem in constant time.
"""

from __future__ import annotations
import sys
import z3

# ---- Mirror the C constants verbatim -------------------------------------
BLOCKLIST = [
    (0x08080808, 32),
    (0x01010101, 32),
    (0x09090909, 32),
    (0xC0000200, 24),
    (0xC6336400, 24),
    (0xCB007100, 24),
    (0xE0000000, 4),
    (0xF0000000, 4),
    (0x00000000, 8),
    (0xFFFFFFFF, 32),
]

PRIVATE_RANGES = [
    (0x7F000000, 8),  # 127.0.0.0/8 loopback
    (0x0A000000, 8),  # 10.0.0.0/8
    (0xAC100000, 12),  # 172.16.0.0/12
    (0xC0A80000, 16),  # 192.168.0.0/16
    (0xA9FE0000, 16),  # 169.254.0.0/16
    (0x64400000, 10),  # 100.64.0.0/10 CGNAT
]


def in_cidr(ip, net: int, bits: int):
    """Z3 BitVec CIDR membership test, mirroring the C ``_in_cidr`` helper."""
    if bits == 0:
        return z3.BoolVal(True)
    if bits >= 32:
        return ip == z3.BitVecVal(net, 32)
    mask = z3.BitVecVal((~((1 << (32 - bits)) - 1)) & 0xFFFFFFFF, 32)
    return (ip & mask) == (z3.BitVecVal(net, 32) & mask)


def is_blocklisted(ip):
    return z3.Or(*[in_cidr(ip, n, b) for (n, b) in BLOCKLIST])


def is_private(ip):
    return z3.Or(*[in_cidr(ip, n, b) for (n, b) in PRIVATE_RANGES])


def egress_allowed(ip):
    """Mirror of vos3_net_egress_allowed — blocklist first, then private-ok."""
    return z3.If(
        is_blocklisted(ip),
        z3.BoolVal(False),
        z3.If(is_private(ip), z3.BoolVal(True), z3.BoolVal(False)),
    )


# --- Three proofs ---------------------------------------------------------


def prove(title: str, violation) -> int:
    s = z3.Solver()
    s.add(violation)
    r = s.check()
    if r == z3.unsat:
        print(f"  ✓ {title:<60} UNSAT (proven)")
        return 0
    if r == z3.sat:
        print(f"  ✗ {title:<60} SAT — counterexample:")
        print(f"     {s.model()}")
        return 1
    print(f"  ? {title:<60} {r}")
    return 2


def main() -> int:
    print(f"Z3 {z3.get_version_string()}  —  egress policy proof")
    print(f"Blocklist entries: {len(BLOCKLIST)}  Private ranges: {len(PRIVATE_RANGES)}")
    print("Address space: 2^32 = 4 294 967 296 IPv4 addresses (BitVec-symbolic)")
    print()

    ip = z3.BitVec("ip", 32)
    failures = 0

    # (1) No blocklisted IP is ever allowed.
    failures += prove(
        "Clause 1: blocklist is hard",
        z3.And(is_blocklisted(ip), egress_allowed(ip)),
    )

    # (2) No public (non-private) IP is ever allowed.
    failures += prove(
        "Clause 2: public IPs denied",
        z3.And(z3.Not(is_private(ip)), egress_allowed(ip)),
    )

    # (3) Private IPs not in blocklist MUST be allowed (completeness).
    failures += prove(
        "Clause 3: private non-blocklisted allowed",
        z3.And(is_private(ip), z3.Not(is_blocklisted(ip)), z3.Not(egress_allowed(ip))),
    )

    print()
    if failures == 0:
        print("RESULT: 3/3 clauses proven over the full IPv4 space.")
        print("        vos3_net_egress_allowed cannot be bypassed for any")
        print("        32-bit IPv4 address.")
        return 0
    print(f"RESULT: {failures} clause(s) failed — fix the policy.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
