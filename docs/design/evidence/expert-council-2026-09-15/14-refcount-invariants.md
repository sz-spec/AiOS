# Reference ownership

Reviewer: `/root/math_build_review`. Source reviewed: `32187957757ab57d7820d0a63fa62c409003b126`. This is one specialized review task performed by an existing agent, not an additional independent agent or comprehensive audit.

- **P1 — runtime gate incomplete.** The final memory BIOS4 run stops after shared survivor 301 exits, without its coordinator wait confirmation. The reference design must not be declared fully qualified from the earlier development pass. Preserve this failure and determine whether exit, reparenting or deferred cleanup prevents progress.
- **P1 — checked ownership is materially improved.** `vmm.c` rejects retain from zero and saturation, decrements with CAS, and enqueues only the final owner transition. Root switching retains the incoming descriptor before changing roots and releases the outgoing CPU pin afterward. Task and CPU ownership are distinct obligations.
- **P2 — file release remains asymmetric.** `fd.c` has checked retain, while `vos3_fd_put` uses unchecked subtraction. Correct callers should prevent underflow, but the API itself cannot diagnose an erroneous final duplicate release before wraparound.

Validation reviewed: actual process-root and synthetic-file close conservation source; the observer now requires all four native kernel markers. BIOS1 fresh qualification passes. The complete four-configuration lifetime matrix does not. Concurrent retain/release stress and arbitrary file-close callbacks were not exercised by this review.

**2026-09-15 subsequent disposition:** the baseline findings above are retained.
The corrected `3daf340` sequential memory matrix now passes all four configurations;
see the [final council disposition](README.md#final-shm-ownership-disposition) and
[mathematical evidence](../native-vm-lifetime-invariants.md). Concurrent shared-VM
protection and the foreign-caller SHM destruction release blocker remain open.

## Subsequent SHM authorization disposition — 2026-09-15

The prior direct foreign-destruction blocker is closed within the task-local
contract by `9e58227056eeb0ec0f6a5b14330406488a26a542`; all four new memory
configurations pass. This supersedes earlier statements that this specific
check remained absent. See the [authorization qualification](../native-shm-authorization.md)
for immutable identities, stale-handle protection and explicit remaining IPC,
creator-orphan cleanup and concurrency limits. Historical review findings above
remain the record of their original source baseline.

## Creator-exit disposition — 2026-09-17

`8c23007eed4acd731b98bdd2998c9634f2ba3f75` adds deferred creator-reference
cleanup across task death paths. The four memory configurations pass normal/fault
retirement before parent collection, mapped-survivor and no-double-release tests.
See the [creator-exit qualification](../native-shm-exit.md) for physical-page
controls and the limits on zombie address spaces, concurrency and remote stopping.
This supersedes the earlier absence of automatic creator-reference release.
