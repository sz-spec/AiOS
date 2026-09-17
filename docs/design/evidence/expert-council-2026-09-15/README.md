# Expert review council — 2026-09-15

Twenty specialized review tasks were performed in parallel by **three reviewing agents and one coordinator**. The environment permits four agent threads; this packet does not represent twenty separate agents, independent people, external certifications or an exhaustive audit. The user accepted this execution arrangement.

The reviews cover the standalone operating system first, plus consolidation, hosted boundaries, installation and supply chain. Each opinion records the reviewed source, findings, required validation and unreviewed areas. A finding is closed only by a linked code change and evidence; documentation or consensus alone is insufficient.

| Task | Review | Actual agent |
|---|---|---|
| 01 | [BIOS boot and native execution](01-bios-boot.md) | `/root/rust_upgrade` |
| 02 | [UEFI and address-space transitions](02-uefi-kpti.md) | `/root/rust_upgrade` |
| 03 | [Scheduler and SMP execution](03-scheduler-smp.md) | `/root/rust_upgrade` |
| 04 | [Drivers and hardware coverage](04-drivers-hardware.md) | `/root/rust_upgrade` |
| 05 | [Build graph and reproducibility](05-build-reproducibility.md) | `/root/rust_upgrade` |
| 06 | [Eleven-source consolidation coverage](06-consolidation-coverage.md) | `/root/rust_upgrade` |
| 07 | [Persistent installation and Windows coexistence](07-install-windows-coexistence.md) | `/root/rust_upgrade` |
| 08 | [Kernel isolation security](08-kernel-isolation-security.md) | `/root/mcp_upgrade` |
| 09 | [AI policy and capabilities](09-ai-policy-capabilities.md) | `/root/mcp_upgrade` |
| 10 | [VFS and IPC security](10-vfs-ipc-security.md) | `/root/mcp_upgrade` |
| 11 | [Network and hosted integration boundaries](11-network-hosted-boundaries.md) | `/root/mcp_upgrade` |
| 12 | [Supply chain and updates](12-supply-chain-updates.md) | `/root/mcp_upgrade` |
| 13 | [Threat model and release conditions](13-threat-model-release.md) | `/root/mcp_upgrade` |
| 14 | [Reference ownership](14-refcount-invariants.md) | `/root/math_build_review` |
| 15 | [Memory ordering and concurrency](15-memory-model-concurrency.md) | `/root/math_build_review` |
| 16 | [Address and counter arithmetic](16-address-arithmetic.md) | `/root/math_build_review` |
| 17 | [Scheduler and lifecycle liveness](17-scheduler-liveness.md) | `/root/math_build_review` |
| 18 | [Oracle strictness and evidence](18-test-oracles-evidence.md) | `/root/math_build_review` |
| 19 | [Performance and resource efficiency](19-performance-efficiency.md) | `/root/math_build_review` |
| 20 | [Core principles and release decision](20-core-principles-release-plan.md) | `/root` |

See [consolidated dispositions and next gates](ACTIONS.md).

## Joint decision

Release qualification remains incomplete. Preserve local operation, permission enforcement, isolation, signed/audited actions and recoverability as requirements. Hardware universality, complete process/AI-slot isolation and byte-identical ISO reconstruction must not be advertised as verified.

At the initial review baseline, the immediate gate was the four-CPU lifecycle-progress failure. Its subsequent resolution is recorded below. Synchronized shared-VM mutation and remote permission revocation remain prerequisites for wider concurrent execution. The individual reports preserve the source/evidence available at review time; subsequent resolutions belong in an explicitly dated disposition below.


## Disposition after corrected lifetime verification

The four-CPU orphan-adoption failure is closed by `3ce56da73271db2e30ac4b226db88bb72a979d50`:
all four corrected memory configurations pass, independently reclassified by the
security and mathematics reviewers. The original failure remains preserved in
[the lifetime evidence](../native-vm-lifetime-qualification.md). This closes a
bounded sequential lifecycle gate; it does not close the council's shared-VM
concurrency, PID-namespace, hardware, deployment or release findings.

## Final SHM ownership disposition

Source `3daf340141b90690a47bad84c51523c44c243e20` also closes the creator-first
SHM resource leak. Independent security and mathematics reviews accepted the
new four-configuration memory matrix, including final explicit detach, final
address-space reap and duplicate creator-close rejection. The full new marker
is mandatory; historical shorter markers fail the observer. This closes the
sequential ownership defect only. Unauthorized creator destruction by a foreign
caller remains an explicit P1 release blocker in the action table.

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

## Current review — 2026-09-17

The [current technical council review](../expert-council-review-2026-09-17.md)
records the new kernel, security, build, BIOS/UEFI and recent-source findings.
The [product and hardware gap review](../expert-council-product-gaps-2026-09-17.md)
turns those findings into measurable release gates. These reports preserve the
same execution model: twenty specialized review roles rotated across the three
available reviewing agents and the coordinator, not twenty independent people
or an external certification.

## Sixty-day failure review and experiment results — 2026-09-17

The [consolidated failure council report](../expert-council-failures-60d-2026-09-17.md)
extends recent-source research to July 19–September 17 and updates all known
finding classes. It includes the full factorial performance matrix, the
correction to the Frontier-only hypothesis, the bounded AI-context detach
repair and independently reviewed recommendations. The
[preserved raw evidence](../council-failure-research-2026-09-17/experiment-matrix.md)
contains failed runs as well as passes. Byte-identical final2 builds are now
verified for their frozen inputs and pinned builder; that supersedes the
earlier absence of ISO reproducibility evidence, but not the open runtime,
clock-skew, lifetime, hardware or release gates.
