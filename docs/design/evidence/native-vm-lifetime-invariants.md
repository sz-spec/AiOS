# Shared address-space lifetime and PTE metadata invariants

Independent read-only review of VMM, clone/fork/exec and task destruction/reaping. Root owns implementation. This document proposes invariants and tests; it does not certify concurrency or memory safety.

## Concrete initial findings

CLONE_VM copies the address-space pointer without acquiring a reference. Destruction/reaping skips `is_thread`, while leader destruction and successful exec unconditionally destroy the old address space. A surviving thread can therefore retain freed page tables. The initialized address-space `ref_count` does not currently govern destruction.

Address-space destruction closes VMA backing descriptors using `vos3_close(fd)`, which resolves through the current task's descriptor table. Reaping another task can close an unrelated descriptor or fail to release the intended backing object. Shared VMA lifetime cannot be represented safely by a bare descriptor number without a retained owning table/object reference.

Clone links the child into `parent->children` before task registration. Registration failure frees the child without undoing that link and uses `kfree` for a vmap stack. New reference ownership must include transactional rollback of list publication, retained file/address-space references and the correct guarded-stack allocator.

COW and cognitive metadata both occupy bit 52: `VOS3_PTE_COW` in vmm.h, `VOS3_PTE_COGNITIVE`/TOMBSTONE in ai_guard.h and a local cognitive bit in vmm.c. Moving a definition without auditing all aliases/operations is insufficient. Centralize distinct metadata masks and compile-time disjointness checks against address, protection-key, NX and other software fields.

## Ownership model

A newly created nonkernel address space starts with one owned reference. Each published task pointer owns exactly one reference, including every CLONE_VM thread. Private fork obtains ownership of its new space. Acquire must reject zero (no resurrection) and overflow; release must reject underflow and destroy exactly once at the unique final transition.

CLONE_VM acquires before child publication; every failure releases exactly the acquired references. Successful exec transfers the task's ownership to the new space and releases the old reference only after the CPU has switched roots. Failed exec switches back and preserves the old ownership while releasing the failed new space. Decide and document the semantics of exec with surviving sibling threads: retaining their old space is memory-lifetime safety, not full process-group exec semantics.

Task references alone are insufficient while a CPU actively uses full/user roots. Each active CPU binding needs a pin or equivalent synchronization. Acquire the incoming binding before publishing/loading it; do not release the outgoing pin until CR3 and entry-trampoline root metadata can no longer reference it. Local IRQ exclusion protects local switching; it does not replace inter-CPU acquire/release ordering. A current-pointer snapshot alone cannot prove that in-flight hardware/root transitions have finished. Same-space task switches must preserve one CPU pin without leaking duplicates.

The kernel address space is immortal under an explicit rule. Boot/AP initialization must install bindings consistently. Final destruction must require no task owners, active CPU bindings or pending protocol users, and must release each distinct page-table root/backing object exactly once. Inactive PCID/TLB translations and page reuse remain a separate barrier/lifetime obligation; a CPU-pin scheme must explain their invalidation policy.

## Necessary implementation tests

- Compile actual lifetime helper C with allocator/destructor hooks: create/acquire/release, no resurrection, overflow/underflow, exactly-once destruction, multiple task references and two active CPU pins. Enumerate release orderings, including task references reaching zero before CPU pins.
- Fault-inject clone allocation/registration stages; assert no child-list dangling pointer, no leaked reference and correct guarded-stack release. Check CLONE_VM without CLONE_FILES as a distinct backing/descriptor case.
- Native leader-exits-first control: survivor shares a canary, repeatedly reads/writes after leader exit/reap, then exits; release evidence shows destruction only afterward. Reverse exit order must also work.
- Native exec-success and exec-failure controls with a surviving shared-space thread: old canary remains valid for the sibling; failed exec preserves caller mappings and releases its temporary space. Bind destruction observations to unique space identities, not recycled pointer values alone.
- Exercise actual CPU binding transitions A→B→A, same-space switches and final release on separate proven CPU identities. Missing pin or early destroy must fail a host negative control and, where safely gated, a native lifecycle probe.
- Compile actual PTE operations against cognitive-only, COW-only and combined flags. Cognitive-only pages must never enter the COW write-enable path; resolving COW preserves cognitive/protection metadata and physical address. Assert all masks are disjoint where required.

These tests establish only their stated ownership/protocol observations. Shared page-table mutation locking, simultaneous COW faults, remote TLB revocation, scheduler fairness and POSIX thread-group exec semantics remain independent obligations.

## Implemented design review and pending qualification

Reviewed root's checked AS retain/release and deferred queue: zero cannot be resurrected, saturation is rejected, only the unique 1→0 transition enqueues retirement, and final destruction occurs outside the short queue lock. CPU switching retains the incoming space before entry-root/CR3 updates and releases the outgoing pin afterward; same-space switches do not add pins. Clone retains AS and either retains or snapshots its FD table before publication, with failure rollback. Exec holds the old task ownership across its temporary new-root binding and either commits or restores it. All tasks release AS ownership irrespective of thread status.

Owned VMA backing pointers replace current-task descriptor lookup. The checked FD snapshot helper holds the source table lock, retains each file before copying its slot and unwinds only acquired entries. CLONE_FILES rejects zero/saturated table counters. Remaining explicit limits include concurrent VMA mutation/positional-I/O serialization and the existing unchecked decrement in `vos3_fd_put`; no reviewed valid ownership path was shown to underflow it. Scheduler reclamation currently rejects IF-clear entry; the explicit safe process-context contract remains necessary, since IF alone is not a universal interrupt-depth proof.

Reviewed the separate file-reference conservation test: saturated clone retains nothing, successful clone adds one backing owner, extra AS owners do not duplicate backing ownership, partial release preserves it, active CPU pin delays its last release, and final detach closes exactly once. The close callback is synthetic; PMM/VMM/refcount paths are real. Exact free-page accounting returns to baseline.

The memory observer now requires four complete kernel PASS markers (PROCESS-ROOTS, VM-BACKING, VM-METADATA and VM-FILE-REFS), plus 43 user records including malformed-ELF rollback, successful self-exec with a surviving shared process, owner-exit survivor and closed/reused backing FD controls. Six observer test methods pass. The earlier development BIOS1 run predates the fourth kernel marker and subsequent source corrections; fresh final native qualification remains pending and must not be inferred from that historical pass.

## Corrected lifetime matrix disposition — 2026-09-15

The original `3218795` matrix failure remains valid historical evidence: BIOS4 lacked the coordinator's final wait confirmation after its survivor exited. Root's diagnostic inspection identified reparent selection accepting an AP idle task with PID 1 rather than the live user init. The narrow fix in `3ce56da73271db2e30ac4b226db88bb72a979d50` requires PID 1, USER, non-IDLE, alive and not the exiting task. The reported advancing retry wake times and missing coordinator child-list entry support wrong reparenting; they do not support a lost-wakeup diagnosis. Repeated BSP HMP register captures are not treated as independent AP observations.

Independently reclassified the corrected raw logs at `/private/tmp/vos5-lifetime-memory-final-20260915/`: BIOS/UEFI × 1/4 CPUs all pass. Every run contains exactly 43 user memory/lifecycle records and all four required kernel checks, including complete survivor waits, successful exec detachment and owned-backing verification. The twelve original fault/COW cases remain required, yielding 48 such cases across this matrix.

Independently recomputed actual artifacts and observer/harness hashes:

- ISO: `4f5906cb3b10a80375712ef85488f7f491f5033102c9e9cc5d48606ee1079c38`.
- Kernel: `601832043baec7aa94e5b8ed2a699a74c2ff8865d512cfff2dd64d6c87524c50`.
- User workload: `dbc15c1d4b6c49165d34187d2871fa47e444ef76397346a11c90a5a1be82b337`.
- Observer: `a91aecafe18dc930f70944c3eaceecb6fcebdd75be1d4574b462aea692eaf944`.
- Harness: `7d7672a5dbd457c6e58a65ac42d59ee6bd6e6a362ed4a736cf08b43c272687f7`.

All match the result, which binds tree `cdc74c6a291516b427cb517f678aed22a2d20d5b`, unchanged source inputs and 106.71 seconds elapsed. Accept the bounded sequential shared-address-space lifecycle/metadata/backing gate for these configurations. This does not prove simultaneous shared-VM mutation, general thread-group semantics or hardware-wide safety. Normal/TLB/isolation regressions remain separate pending gates.

## Creator-first SHM extension review

Reviewed the new unified SHM release path: creator, mapping and temporary-pin releases use one final transition, serialized with map pinning. A separate creator-release token rejects duplicate creator close while mappings survive. The finalizer invalidates registry lookup before teardown and reserves the slot until cleanup. The tests preserve a mapping reference after creator release, verify backing is still allocated, and exercise both explicit final unmap and final address-space reap. After last release they inspect only registry/PMM metadata, never dereference the freed SHM object or backing pointer.

Reported and requested a guard before unsigned page-table counter subtraction and its subsequent free-page arithmetic. The reclamation inequality verifies at least the backing page plus released page-table pages; it is not an exact total-allocation conservation proof for this extension. That limitation must remain explicit unless a separate full baseline comparison is added.

The memory observer now requires the full VM-BACKING marker including `creator-first explicit/reap, duplicate creator close`. An explicit regression rejects the historical shorter marker, so a pre-extension image cannot satisfy the new gate. Seven memory observer methods pass. Previous `3ce56da` runtime results remain historical intermediate evidence; the extended creator-first code requires a fresh final source/artifact matrix.

## Final creator-first release qualification — approved within scope

Independently reclassified all four completed raw logs under `/private/tmp/vos5-lifetime-memory-release-20260915/`. BIOS/UEFI × 1/4 CPUs pass the final observer: exactly 43 user records per run, twelve original memory cases, and all four kernel checks. The required full VM-BACKING marker includes creator-first explicit-unmap/reap and duplicate-creator-close checks, so an earlier shorter-marker image cannot pass. The creator-first arithmetic now checks monotonic table/free-page counts before subtraction/addition, preventing unsigned wrap from manufacturing success.

Verified committed source `3daf340141b90690a47bad84c51523c44c243e20`, tree `95db2afaf893a1ee873e1020b561cce7bf59b0d5`, unchanged source inputs, and stable before/after ISO for each run. Independently recomputed actual artifact and script hashes:

- ISO: `ffd15c1c4f55497b2a9c4ea2229fcdc907461f202b3fce4091006ec95f34689e`.
- Kernel: `80cdc36f39d04b842091732ed54cbddc07a288a95652864f5b6d2b9bbfb5227c`.
- User workload: `dbc15c1d4b6c49165d34187d2871fa47e444ef76397346a11c90a5a1be82b337`.
- Observer: `fdc6cfcc10e671085bf2a482b509800dadcb7f0618a38b3a0cad243fb221b65d`.
- Harness: `7d7672a5dbd457c6e58a65ac42d59ee6bd6e6a362ed4a736cf08b43c272687f7`.

All match the recorded result; elapsed time is 105.64 seconds. Also reran all 36 native observer test methods and both actual-C test methods successfully. Portable output is retained in `native-vm-lifetime-2026-09-15/observer-tests.txt` and `c-tests.txt`; the TLB C method contains fourteen independent-process scenarios.

Approve the final sequential lifetime, metadata and creator-first backing gate for these QEMU configurations. This is evidence-based invariant review, not a formal proof of all machine-code executions. Authorship boundaries remain disclosed: this reviewer authored portions of workload/oracle tests; separate security reviewers assess those. Creator-first free-page checks establish a guarded reclamation lower bound, while separate process-root/metadata tests establish their exact accounting baselines. No universal concurrency, shared-page-table mutation, fairness or physical-PC qualification follows. Other final regression matrices are independently reported and not implied by this approval.
