# Oracle strictness and evidence

Reviewer: `/root/math_build_review`. Source reviewed: `32187957757ab57d7820d0a63fa62c409003b126`. This is one specialized review task performed by an existing agent, not an additional independent agent or comprehensive audit.

- **P1 — fresh results override historical passes.** The memory observer requires four kernel lifecycle records plus 43 user records. Fresh BIOS1 passes, but fresh BIOS4 lacks the final survivor wait record and correctly fails. It must not be described as twelve passing memory cases completing the enlarged lifetime gate.
- **P2 — preserve authorship boundaries.** This agent authored the memory workload and observer, while other agents reviewed them. Its source review of root/peer implementation is independent of implementation authorship; its observer assessment is not a second independent author review.
- **P2 — artifacts bind claims.** `native_clean_qualification.py` records committed source, manifest, actual artifact hashes and unchanged inputs. Raw logs and failed aggregates remain necessary: a summary boolean alone cannot distinguish expected negative controls from positive qualification.

Validation reviewed: six memory observer methods, including deletion of every required line, identity/value corruption and ordering checks; earlier SMP interleaving/truncation regressions reject malformed records. These tests assess observers, not kernel safety. Final lifetime matrix, normal boot and regression outcomes must each be reviewed separately. No evidence was repaired into a pass or inferred from elapsed time.

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
