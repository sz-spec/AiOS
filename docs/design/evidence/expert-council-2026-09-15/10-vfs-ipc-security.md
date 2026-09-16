# VFS and IPC security

Reviewer: `/root/mcp_upgrade`. Source: `32187957757ab57d7820d0a63fa62c409003b126`. Date: 2026-09-15. Bounded source and evidence review; no production edits.

1. **P1 — positional file access is not atomic.** [kernel/src/arch/x86_64/interrupts.c](../../../../kernel/src/arch/x86_64/interrupts.c) uses a VMA-owned file reference and checked seek/read/restore. This fixes descriptor reuse and wrong-reaper-table ownership, but [kernel/src/fs/ramfs.c](../../../../kernel/src/fs/ramfs.c) locks each operation separately. Concurrent access can change the shared offset between operations. Require a genuine position-independent interface before qualifying concurrent file-backed faults.
2. **P1 — authorization needs a dedicated audit.** `kernel/src/fs/file.c:vos3_chown` and `vos3_fchmod` delegate to inode setattr without a visible credential check in those wrappers. This is a review lead, not a proven reachable privilege escalation: syscall callers, mount policy and filesystem implementations were not exhaustively traced. Require cross-user negative authorization tests before multi-user deployment.
3. **P1 compatibility — SHM fork is rejected.** `kernel/src/mm/vmm.c:vos3_vmm_clone_cow` rejects tracked SHM; [kernel/src/ipc/shm.c](../../../../kernel/src/ipc/shm.c) associates mappings with the address space and detaches borrowed pages without freeing them. Preserve this restriction until independent fork ownership is implemented.

Validation: reviewed the SHM detach correction and real-PMM test, plus authored file backing tests independently cross-reviewed by the mathematics agent. Synthetic close callbacks prove reference conservation only; actual user file tests establish a separate bounded data path. Not reviewed: filesystem corruption recovery, all IPC authorization, concurrent namespace mutation or storage-device failures.

2026-09-15 follow-up: source `3ce56da` fixes idle-task orphan adoption. All four corrected memory/lifetime logs and 5,594 source hashes independently verify; original failure remains preserved. This bounded sequential gate passes, while general PID ambiguity, concurrency and release findings remain open. Original review scope above is unchanged.

**P1 follow-up:** creator-first SHM finalization passes all four independently verified native configurations at `3daf340`. Separate authorization gap: `ipc.c:sys_shm_destroy` forwards caller-supplied IDs without checking creator ownership; the new creator-close flag does not authenticate callers. Full IPC security remains unqualified.

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
