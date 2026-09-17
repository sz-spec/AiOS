# Independent arithmetic, wait-status and eleven-source review — 2026-09-17

Reviewer: /root/math_build_review. Source review plus host checks, not a full OS proof.

## Approved bounded fixes

`bench_2026_frontier.c` now multiplies unsigned 32-bit operands before widening. On the native x86-64 ABI the operation is defined modulo 2^32; the fixed upper 32-bit tags remain unchanged. The odd low multiplier has period 2^32 and the high multiplier, with exactly one factor of two, has period 2^31. Thus both produce distinct payloads for the 32 agent indices. The actual extracted assignments passed UBSan and an independent 64-bit-product/mask oracle; this does not validate the rest of the benchmark or its performance measurements. Evidence: `vos-goal-native-20260917-frontier-review.log`.

`init.c` initializes wait status, verifies waitpid returns exactly the launched PID, and emits `Init: ERROR` before breaking on failure. The host benchmark oracle rejects this diagnostic and the missing child result even if the outer loop later prints its completion banner. This closes uninitialized-status false success; it does not repair waitpid implementation behavior.

## Fresh reconciliation results

Executed the actual reconciliation with only module OUT overridden to `/private/tmp/vos-goal-native-20260917-reconciliation`. The committed ledger was not regenerated. All eleven sources were scanned: 6,239 distinct donor paths comprise 4,571 selected-content matches, 422 canonical divergences, 92 exact matches at another path, and 1,154 paths without retained bytes. The candidate variant ledger contains 2,655 entries. Of absent native paths, 616 are bootloader paths and 33 are other native paths. These counts describe bytes and paths, not missing runtime capabilities. The complete fresh-ledger/source-accounting plus importer safety suite passed 10 tests.

Provenance limits: VOS-Cyber-Standard has no resolvable HEAD (2,089 included files are working-tree-only). VOS3 has 882 local changes, VOS3-Cyber 226, vos/vos4 226, and vos.v1 10; six other tracked donor trees report zero filtered changes. Generated/runtime exclusions and unfollowed symlinks remain explicit; zero filtered changes is not an unrestricted clean-tree guarantee.

## Prioritized integration findings

1. Security policy candidates remain open: donor `kernel/src/net/egress_policy.c` and `kernel/src/mm/ai_oom.c` are absent as retained content. Their predicates need current socket/allocation call-path integration, authority design, and adversarial runtime tests. Donor egress permits private/link-local ranges and its header acknowledges enforcement integration; copying it is not proof of safe network isolation. OOM arithmetic alone does not prove concurrent reservation/accounting.
2. Do not regress current isolation to preserve donor scaffolding. Donor `pcid.c` explicitly describes a shared-address-space design with no production ASID callers. It cannot replace current per-process roots, active CPU lifetime pins or acknowledged TLB invalidation. Hyper-V vsock explicitly falls back to loopback; portable Windows/Linux files are stubs. None qualifies host hardware isolation or actual Windows coexistence.
3. Capability relocation must be resolved semantically: donor `drivers/pci_ecam.c` is absent by hash, but canonical `arch/x86_64/pci_ecam.c` is called by `drivers/pci.c`. Similarly canonical `sec/mmr_audit.c` and CPU mitigation implementations exist despite missing donor `crypto/mmr.c`/core paths. Compare contracts and hardware tests before marking these capabilities lost or merging duplicate implementations.
4. Documentation decisions lag implementation: component-decisions still lists actual AP workload qualification as open, while the later bounded gated-SMP evidence exists. That evidence does not qualify the normal scheduler generally. Bootloader and musl upstream upgrades account for large byte differences; missing old vendor files must be reconciled against recorded upgrade provenance instead of blindly restored.

## Evidence limits

Reconciliation is a live working-tree scan, not an atomic snapshot; other agents were editing hosted files during this audit. Exact hashes preserve each observed version but do not establish one simultaneous repository state. Symbol extraction is a heuristic review index; empty/matching symbol sets do not prove equivalence. The ledger tests check accounting and import/exclusion safety, not preservation of every historical behavior. Full BENCH_MODE runtime qualification is separately pending and no all-hardware or complete-unification claim follows from these results.
