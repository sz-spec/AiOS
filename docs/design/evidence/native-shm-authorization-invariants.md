# SHM authorization and stale-handle invariants

Reviewer: `/root/math_build_review`. Independent production-source arithmetic/identity review; this reviewer authors the host helper test and native observer changes. Root authors production identity/authorization changes; another agent authors the native user fixture. Final runtime qualification is pending.

## Identity and handle design

SHM handles use six low slot bits and twenty-five generation bits, remaining positive signed-32-bit values. Slots 1–63 are valid; generation one is first issued. Allocation increments under the registry lock, never wraps, and permanently retires an exhausted slot. Lookup requires the complete issued handle and live magic, so the same slot with a new generation cannot authorize an old handle.

Task identity cookies are centrally allocated under `g_task_lock` at creation/registration, including newly registered fork/clone children after structure copying. Zero is invalid; UINT64_MAX is the last issued cookie and subsequent allocation fails. This separates authority from overlapping numeric PID/TID allocators. Authorization compares the live caller's cookie to the recorded creator cookie before changing the creator-close token or count, within the same SHM registry lock. Public mapping permission does not imply creator-destruction authority.

Syscall wrappers reject values wider than UINT32_MAX before casting. Full lookup rejects the signed high-bit range. The Linux shmctl alias accepts only its explicitly supported removal command. Trusted mapping/temporary-pin releases remain distinct from creator-authorized closure. This is lifetime-safe identity within a boot, not a cryptographic capability or cross-boot token.

## Actual C helper validation

`scripts/test_shm_identity.py` extracts the exact production `shm_slot`, `shm_get`, `shm_alloc_id`, handle macros and `alloc_identity_cookie`; it compiles them with minimal table/region mocks under C11 with warnings as errors. No algorithm is rewritten in Python. Tests pass first generation, occupied-slot skipping, stale lookup after reuse, zero/invalid/sign-bit handles, maximum slot/handle, all-slot exhaustion, no generation wrap and cookie exhaustion without reuse. The extracted helper SHA-256 is printed for provenance. These tests do not execute lock concurrency or the complete release finalizer.

## Native oracle conditions

Three additional ordered records are mandatory after backing checks and before final completion: foreign private denial, foreign public destruction denial with surviving mapping after owner closure, and same-slot stale-handle rejection. Child identities must be distinct and cannot reuse parent/earlier fixture PIDs. The stale record must show valid positive handles with equal nonzero slot and strictly increasing generation. Every denial/control flag, CPL3 and zero wait status is mandatory; unexpected fields or missing records fail. Eight memory observer methods pass, including denial/alias/slot/wide/PID mutations and deletion of every required record.

## Remaining limits

Native collision and syscall tests are required before closing the release blocker. Source review alone does not prove all kernel callers use the correct API, authorization remains valid under arbitrary concurrent task teardown, or global numeric PID/TID ambiguity is fixed. Generation exhaustion is intentionally resource exhaustion, not handle reuse. Full shared-VMA mutation, remote TLB revocation and physical-machine isolation remain separate gates.

## Final native authorization gate — verified

Independently reclassified all four raw logs at `/private/tmp/vos5-shm-auth-memory-release-20260915/`: BIOS/UEFI × 1/4 CPUs pass. Every run contains exactly 46 user records and all five mandatory kernel checks, including the zero-cookie/same-TID-different-cookie identity test and native shared-clone denial. Private/public foreign creator-destruction denials, command-alias controls and owner/survivor checks satisfy the strict observer.

Every stale-handle observation records old handle 449 and new handle 513 at slot 1: generations 7 and 8, respectively. The same slot is actually reused with a new generation; canonical, alias, unsupported-command, wide-value and canary controls all pass. This is not merely rejection of a currently empty slot.

Verified source commit `9e58227056eeb0ec0f6a5b14330406488a26a542`, tree `3452e74d5da3a5b806ff42505ac529a2a1a4e509`, unchanged source inputs and unchanged ISO before/after every run. Independently recomputed actual artifacts and scripts:

- ISO: `3bfbb2771ae8d605d70c273c8d67445c03cec9408c4a6622e11e4f95a3e12a94`.
- Kernel: `9c1381880962cbae7f3a91a756fb83ef72b50cb621dc4030f71ca6ded248953c`.
- User workload: `203e2cd5afcfe4705397ca9b0312e5c688576f30dc7f3ccbdd40d162b34f4355`.
- Observer: `4e0c89b6df074e1c2491a22fea96a8606f92ce67a92f614a7bb90667a70f9ba6`.
- Harness: `7d7672a5dbd457c6e58a65ac42d59ee6bd6e6a362ed4a736cf08b43c272687f7`.

All match the recorded result, elapsed 107.35 seconds. All 37 native observer methods and three actual-C test methods also pass; portable outputs reside in `native-shm-authorization-2026-09-15/observer-tests.txt` and `c-tests.txt`.

Approve the bounded creator-authorization/stale-handle gate for these interfaces and QEMU configurations. This resolves the demonstrated foreign creator-destruction defect and immediate slot-reuse alias within the tested policy. It does not certify every IPC operation, cryptographic capability delegation, global PID correctness, concurrent VMA mutation or physical-machine security. This reviewer authored the observer/helper tests; separate agents review those, while this review independently assesses root-authored implementation and actual runtime evidence.
