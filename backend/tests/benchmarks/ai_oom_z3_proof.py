"""SMT contract checks for the canonical AI quota state machine.

This model uses mathematical integers bounded to uint64_t, so conservation is
proved without modular wraparound. It is a contract proof, not a refinement
proof of compiled C; host, fault-injection and SMP tests remain mandatory.
"""

from __future__ import annotations

import sys
import z3

MAX = (1 << 64) - 1
FREE, RESERVED, COMMITTED = 0, 1, 2


def prove(title: str, domain, violation) -> int:
    solver = z3.Solver()
    solver.add(*domain, violation)
    result = solver.check()
    if result == z3.unsat:
        print(f"  ✓ {title:<70} UNSAT (proven)")
        return 0
    if result == z3.sat:
        print(f"  ✗ {title:<70} SAT — {solver.model()}")
        return 1
    print(f"  ? {title:<70} {result}")
    return 2


def sat_increment(value):
    return z3.If(value == MAX, MAX, value + 1)


def main() -> int:
    used, reserved, requested, limit = z3.Ints(
        "used reserved requested limit"
    )
    enforce = z3.Bool("enforce")
    domain = [
        used >= 0,
        used <= MAX,
        reserved >= 0,
        reserved <= MAX,
        requested >= 0,
        requested <= MAX,
        limit >= 0,
        limit <= MAX,
    ]
    ledger_valid = used + reserved <= MAX
    arithmetic_valid = z3.And(requested > 0, used + reserved + requested <= MAX)
    over_limit = z3.And(limit != 0, used + reserved + requested > limit)
    reserve_ok = z3.And(
        ledger_valid,
        arithmetic_valid,
        z3.Not(z3.And(enforce, over_limit)),
    )
    new_used = used
    new_reserved = z3.If(reserve_ok, reserved + requested, reserved)

    failures = 0
    print(f"Z3 {z3.get_version_string()} — AI quota contract (uint64_t domain)")
    failures += prove(
        "Q1 successful reserve cannot overflow charged accounting",
        domain,
        z3.And(reserve_ok, new_used + new_reserved > MAX),
    )
    failures += prove(
        "Q2 successful hard reserve cannot exceed a nonzero limit",
        domain,
        z3.And(reserve_ok, enforce, limit != 0,
               new_used + new_reserved > limit),
    )
    failures += prove(
        "Q3 rejected reserve leaves used/reserved unchanged",
        domain,
        z3.And(z3.Not(reserve_ok),
               z3.Or(new_used != used, new_reserved != reserved)),
    )
    failures += prove(
        "Q4 soft reserve admits every arithmetically valid request",
        domain,
        z3.And(ledger_valid, arithmetic_valid, z3.Not(enforce),
               z3.Not(reserve_ok)),
    )

    bytes_, generation, handle_generation, state = z3.Ints(
        "bytes generation handle_generation state"
    )
    transition_domain = domain + [
        bytes_ > 0,
        bytes_ <= MAX,
        generation > 0,
        generation <= MAX,
        handle_generation > 0,
        handle_generation <= MAX,
        state >= FREE,
        state <= COMMITTED,
    ]
    handle_matches = handle_generation == generation

    commit_ok = z3.And(
        ledger_valid,
        state == RESERVED,
        handle_matches,
        reserved >= bytes_,
        used + bytes_ <= MAX,
    )
    commit_used = z3.If(commit_ok, used + bytes_, used)
    commit_reserved = z3.If(commit_ok, reserved - bytes_, reserved)
    commit_state = z3.If(commit_ok, COMMITTED, state)
    failures += prove(
        "Q5 commit preserves exact charged total",
        transition_domain,
        z3.And(commit_ok,
               commit_used + commit_reserved != used + reserved),
    )
    failures += prove(
        "Q6 copied committed handle cannot commit a second time",
        transition_domain,
        z3.And(commit_ok, commit_state == RESERVED,
               handle_generation == generation),
    )

    cancel_ok = z3.And(
        ledger_valid,
        state == RESERVED,
        handle_matches,
        reserved >= bytes_,
    )
    cancel_used = used
    cancel_reserved = z3.If(cancel_ok, reserved - bytes_, reserved)
    cancel_state = z3.If(cancel_ok, FREE, state)
    failures += prove(
        "Q7 cancel decreases charged total by exactly token bytes",
        transition_domain,
        z3.And(cancel_ok,
               cancel_used + cancel_reserved != used + reserved - bytes_),
    )
    failures += prove(
        "Q8 copied cancelled handle cannot cancel a second time",
        transition_domain,
        z3.And(cancel_ok, cancel_state == RESERVED,
               handle_generation == generation),
    )

    release_ok = z3.And(
        ledger_valid,
        state == COMMITTED,
        handle_matches,
        used >= bytes_,
    )
    release_used = z3.If(release_ok, used - bytes_, used)
    release_reserved = reserved
    release_state = z3.If(release_ok, FREE, state)
    failures += prove(
        "Q9 release decreases charged total by exactly token bytes",
        transition_domain,
        z3.And(release_ok,
               release_used + release_reserved != used + reserved - bytes_),
    )
    failures += prove(
        "Q10 copied released handle cannot release a second time",
        transition_domain,
        z3.And(release_ok, release_state == COMMITTED,
               handle_generation == generation),
    )
    failures += prove(
        "Q11 corrupt charged total permits no terminal transition",
        transition_domain,
        z3.And(z3.Not(ledger_valid),
               z3.Or(commit_ok, cancel_ok, release_ok)),
    )

    counter = z3.Int("counter")
    counter_domain = [counter >= 0, counter <= MAX]
    failures += prove(
        "Q12 saturating increment is monotonic over uint64_t",
        counter_domain,
        sat_increment(counter) < counter,
    )
    failures += prove(
        "Q13 saturating increment fixes UINT64_MAX",
        counter_domain,
        z3.And(counter == MAX, sat_increment(counter) != MAX),
    )
    failures += prove(
        "Q14 nonmax saturating increment advances exactly once",
        counter_domain,
        z3.And(counter < MAX, sat_increment(counter) != counter + 1),
    )

    print()
    if failures == 0:
        print("RESULT: 14/14 quota-contract obligations proven.")
        print("This is not a compiled-C refinement or runtime concurrency proof.")
        return 0
    print(f"RESULT: {failures} obligation(s) failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
