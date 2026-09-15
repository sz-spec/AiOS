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
