# Shared address-space lifetime and PTE metadata — 2026-09-15

This stage separates COW from cognitive PTE metadata and introduces explicit
ownership of address spaces, their file backings and SHM mappings. It tests
sequential shared-VM clone/exit/exec behavior. Concurrent shared-VM mutation,
remote permission revocation and full process isolation remain separate gates.

## Implementation and ownership contract

COW now uses software PTE bit 53; cognitive metadata retains bit 52. Static
assertions check disjointness from each other, AI flags, physical address bits,
NX and protection-key bits. Permission reconstruction preserves cognitive
metadata. The architectural basis for ignored leaf bits is the
[Intel SDM, Volume 3A](https://cdrdv2-public.intel.com/835754/253668-sdm-vol-3a.pdf);
this does not qualify arbitrary future paging formats or physical processors.

Every creator/task owns an address-space reference, including CLONE_VM children.
Each CPU also pins its currently bound roots. Root switching retains the incoming
space, replaces CR3 and the entry roots, then releases the outgoing CPU pin.
Same-space switches do not change the count. Checked atomic increments reject
zero-reference resurrection and overflow. The unique last release queues the
space; a process-context drain reclaims it outside the queue lock. CPU switching
does not run filesystem close callbacks. Early boot tests explicitly drain only
safe test resources. The API requires callers to own or serialize the pointer;
it does not make a stale pointer safe to retain.

Clone acquires references before making the child runnable and unwinds failures.
CLONE_VM without CLONE_THREAD produces a waitable child; shared-VM children retain
the parent's fixed CPU owner in the experimental SMP scheduler. CLONE_FILES
shares a checked table reference; otherwise descriptor entries are copied under
the table lock with owned file references. Fork registration failures release
address-space/file resources and do not leave child-list pointers to freed tasks.
Exec retains the old owner through loading: failure restores its roots and mmap
cursor; success releases that owner after binding the new space.

VMAs own file objects rather than integers in an unrelated task's descriptor
table. Splits and clones acquire backing references before publication; removal
and final reclamation release them. Demand faults use the owned object, check
file offsets and reject failed I/O. The current seek/read/restore sequence is
**not** qualified for concurrent file-position users; positional I/O and VMA
synchronization remain necessary.

SHM mapping records belong to the address space. Tracking capacity is checked
before mapping, and explicit unmap must match an owned record. Unmap/rollback
remove borrowed mappings without freeing region-owned physical pages. Final
address-space reclamation skips those physical pages and releases mapping/VA
ownership after removing page tables. SHM's kernel alias is NX, preserving W^X.
Creator close, explicit unmap, failed-map rollback and final address-space cleanup
now use one last-reference finalizer. A per-slot creator-close token prevents a
second creator close from consuming a live mapping reference; zero/overflow map
pins are rejected. Finalization reserves the registry slot until backing cleanup
finishes. This token is reference accounting, not caller authorization: the
syscall currently permits a foreign caller to close a creator reference by ID.
That separate IPC authorization defect remains a P1 release blocker.

Two compatibility restrictions are explicit: independent fork of an address
space with SHM mappings returns ENOTSUP until a safe SHM/COW ownership protocol
exists; fork/clone with an attached AI-guard context also returns ENOTSUP until
policy-preserving context inheritance exists. CLONE_VM can share SHM ownership.
Neither restriction silently removes a policy or copies unowned resources.

## Observed failure and correction

Development BIOS1 first stopped because ordinary SHM creation requested a writable
executable kernel alias. Adding NX corrected that call. A subsequent development
BIOS1 run passed the lifetime workload; these mutable-tree observations are not
substitutes for the committed-source qualification.

The first clean source, `32187957757ab57d7820d0a63fa62c409003b126`, passed
BIOS1/UEFI1 but both four-CPU runs stopped after the survivor exited. All twelve
original cases and the failed-exec case had passed, but coordinator wait
completion was absent. The aggregate remains recorded as failed.

QMP captures showed advancing scheduler ticks and repeated one-tick sleeps by
user init. The child had been adopted by an AP idle task: idle tasks can also
have numeric PID 1, and the old reparenting scan selected the first PID match.
The correction in `3ce56da73271db2e30ac4b226db88bb72a979d50` selects only a
live, non-idle user init other than the exiting task. General task/PID namespace
ambiguity and full multithreaded parent-list synchronization remain open.

After the four matrices passed on `3ce56da`, council review identified a second
ownership defect: creator-first SHM close could leave a zero-reference region
allocated. `3daf340141b90690a47bad84c51523c44c243e20` unifies final release and
adds both creator-first regressions. The observer requires their complete new
marker and rejects the old prefix. The `pre-shm-*` records preserve the earlier
passing results; they are not qualification of this last correction.

## Evidence and bounded tests

The memory image requires four exact kernel evidence records:

- PROCESS-ROOTS: allocation rollback, checked reference limits, multiple owners,
  CPU-only pin survival and exact physical/page-table accounting.
- VM-BACKING: real SHM capacity, foreign/NULL unmap refusal, explicit detach,
  unsupported independent SHM fork, surviving owner and final mapping cleanup; creator-first explicit detach and
  address-space reap, duplicate creator-close rejection and registry/physical-page
  release. Guarded PMM deltas prove a lower bound for backing/table reclamation,
  not exact total allocation balance for these two new SHM cases.
- VM-METADATA: real page-table COW copy, distinct physical pages, intact parent
  content, preserved cognitive metadata through permission changes, final PMM balance.
- VM-FILE-REFS: checked retain, clone overflow rollback, partial release and
  exactly-once final close. Page tables and ownership code are real; only the
  close callback/file object is synthetic.

The CPL3 workload adds malformed-ELF exec rollback, a survivor after owner exit,
and a survivor after successful owner self-exec. Pipe challenges establish
ordering and complete-page canaries verify access. Waiting alone is not proof of
physical reclamation; separate kernel counters test final reclamation. File-backed
VMAs are exercised after closing and reusing the original descriptor number,
through mprotect/munmap splits and fork, with content and unrelated-FD checks.

All 43 user records and all four kernel records are mandatory. The twelve
original memory cases remain separately counted. Host verification includes
36 native-observer methods, one PTE-metadata C regression and the production TLB
C harness's fourteen scenarios. Host stubs are not hardware evidence.

Final source `3daf340141b90690a47bad84c51523c44c243e20` is the build input.
The corrected memory matrix passes all four BIOS/UEFI × one/four CPU runs,
including 48 original memory cases, twelve lifecycle scenarios and four backing
workloads. All four kernel evidence groups pass in each configuration. Sources
are fresh Git archives containing 5,594 files without prior outputs; one ISO per
build flavor is reused unchanged across BIOS/UEFI × one/four CPUs. These are
QEMU q35/TCG results, not a physical-hardware or byte-identical-rebuild claim.

The qualified diagnostic ISO is `dist/vos5-lifetime-qualified.iso`, SHA-256
`ffd15c1c4f55497b2a9c4ea2229fcdc907461f202b3fce4091006ec95f34689e`.
The kernel hash is `80cdc36f39d04b842091732ed54cbddc07a288a95652864f5b6d2b9bbfb5227c`;
the user workload hash is `dbc15c1d4b6c49165d34187d2871fa47e444ef76397346a11c90a5a1be82b337`.
The 105.64-second clean matrix records unchanged inputs and builder cleanup.
The normal image also passes all four configurations with diagnostic defines
absent. Its local copy is `dist/vos5-lifetime-normal-qualified.iso`, SHA-256
`2efd21e6fc2ca77b62d0feacabb4afa2df3f464ee73371f74483c28ba53d076b`.
[Portable evidence](native-vm-lifetime-2026-09-15/) retains raw serial logs,
failures, build output, source hashes and diagnostic captures.

## Other final regressions

The final TLB matrix also passes BIOS/UEFI × one/four CPUs on the same source.
Its ISO SHA-256 is `a5970c99628e0f7cc8187799bf21018d2d1fe0b6a8a098511c4b5e5883531f88`. This retains the bounded
kernel-translation probe and experimental AP user workload, not concurrent
shared-user-VM revocation proof.

The final prior-isolation regression passes all four configurations, covering
sixteen observed cross-process/kernel read/write attempts in total. Its ISO
SHA-256 is `5c16228a619c29d9cb16e87f4c3c6446dc65804ca54783c2534fbc6e52a4fdb2`. Passing these direct
attempts does not establish complete process isolation or SHM authorization.

| Final matrix | Successful configurations | Elapsed seconds |
|---|---:|---:|
| memory | 4/4 | 105.64 |
| normal | 4/4 | 108.4 |
| tlb | 4/4 | 106.32 |
| isolation | 4/4 | 106.04 |

All sixteen final boots use fresh archived source from `3daf340`, with unchanged
source inputs and recorded compiler, firmware, classifier and artifact identities.
The independent review packet reclassifies raw logs rather than trusting only
aggregate pass fields. Original failed and intermediate matrices are retained.

## Reproduce

```sh
python3 scripts/native_clean_qualification.py --revision 3daf340141b90690a47bad84c51523c44c243e20 --memory --output /private/tmp/vos5-lifetime-new
```

Use a new output directory. The memory option enables PROCESS_ROOTS_TEST and the
existing headless isolation/memory diagnostics. The normal kernel contains the
ownership fixes but does not activate the diagnostic workloads or AP scheduler.

[Security review](native-vm-lifetime-security.md),
[mathematical review](native-vm-lifetime-invariants.md), and the
[twenty-task review council](expert-council-2026-09-15/README.md) distinguish
implemented behavior, measured evidence and remaining release requirements.


## Build diagnostics and remaining limits

Build logs retain clock-skew and objdump debug-information warnings. Fresh output
checks, unchanged input hashes and runtime observations support the stated gate;
these warnings are not silently recast as a warning-free build. The legacy
`bench_2026_frontier.c` also emits a signed-overflow warning in its agent-pattern
initialization. That benchmark is not executed by this qualification and cannot
supply performance or isolation evidence until separately corrected and tested.

Other council findings remain open: general PID/TID namespace consistency,
parent-list concurrency, positional backing I/O, SHM mutation synchronization,
policy-preserving AI-context inheritance, physical devices and firmware,
persistent installation recovery, authenticated updates and byte-identical ISOs.
