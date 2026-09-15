# Core principles and release decision

Reviewer: `/root` (coordinator). Source: `32187957757ab57d7820d0a63fa62c409003b126`. This is the twentieth review task, not a twentieth independent agent. The council uses three reviewing agents and one coordinator.

The user-supplied ten-page `vOS_Technical_Brief.pdf` was read locally. Its SHA-256 is `c4a209477383e00f660699707fa83de2ef19294bba8284da3be6d7483644207e`. Its architecture establishes a custom native kernel alongside separately hosted components, local AI, signed actions, permission enforcement, resource containment and auditable approval. These are requirements to preserve; the brief's historical implementation and performance assertions are not current qualification evidence.

- **P1 — retain the distinction between requirements and measured behavior.** The README still marks integration incomplete. The native TLB report demonstrates bounded kernel translation replacement; it does not establish the brief's complete AI-slot isolation or every-PC compatibility. Require a requirement-to-test matrix before approving those claims.
- **P1 — fix observed failures before expansion.** The first fresh lifetime matrix passes one-CPU BIOS/UEFI but stops after a shared survivor exits in four-CPU runs. Preserve those failures and diagnose the missing coordinator progress before accepting the lifetime gate.
- **P1 — preserve policy when compatibility is unavailable.** The lifetime changes reject cloning an attached AI-guard context instead of copying an unowned context or removing its policy. Independent SHM fork is explicitly unsupported until mapping ownership can be retained correctly. These restrictions need documented capability tracking and replacement tests.
- **P2 — sequence release work by evidence.** After bounded lifecycle qualification, address shared-VM mutation and remote revocation, then persistent installation recovery, physical hardware coverage and reproducible release artifacts. Hosted-provider, cryptographic-update and performance claims each require their own evidence.

Reviewed: the brief, current status/build reports, lifetime code/evidence and council findings. Not reviewed exhaustively: all eleven historical trees, every hosted endpoint, cryptographic implementations, all drivers or physical machines. No production release is approved by this council packet.

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
