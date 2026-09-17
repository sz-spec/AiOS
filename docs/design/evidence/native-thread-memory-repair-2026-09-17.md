# Thread memory failures — 2026-09-17

The preserved full-run log `/private/tmp/vos-goal-usercopy-20260917-run/serial.log` shows two distinct failures. `stress_thread` completed all five 100,000-increment rounds with correct TLS, but its 512 KiB memory bound failed with a 3,204 KiB decrease. Fifty retired clone stacks alone account for 3,200 KiB. Its completion counter is published before thread exit, so it is not proof that deferred resources were reclaimed. `test_stress_mt` passed its file operations and races but reported ten malloc failures and forty successful allocation operations; an earlier cleanup run reported fifty successful operations. The thresholds and workload counts are unchanged.

## Shared mmap reservation

Implementation author: `/root/mcp_upgrade`; independent reviewer: `/root/math_build_review`. `sys_mmap` previously chose and advanced a cursor in each task descriptor, although `CLONE_VM` siblings share VMA state. A sibling could therefore request an address already reserved by another sibling and receive EEXIST instead of a fresh allocation.

The authoritative cursor now belongs to the address-space descriptor. Fresh address spaces initialize it, private forks snapshot it under the source address-space lock, and shared-VM clones use the same descriptor. The old task field remains solely to preserve task layout and is no longer consulted by mmap. The syscall acquires and validates any backing-file reference before taking the address-space lock. IRQ exclusion and that lock cover free-slot selection, overlap checks, VMA publication, and successful cursor advancement. Failure unlocks and restores IRQ state before releasing backing references. Fixed mappings retain the prior overlap-rejection policy and do not advance the cursor.

`scripts/test_shared_mmap_cursor.py` compiles the actual syscall and range helper. Eight simultaneous host threads make eight allocations each into one simulated address space; the test verifies all 64 VMAs are nonoverlapping and the final cursor is exact. Additional controls cover slot exhaustion, fixed overlap, invalid backing, failure reference conservation, IRQ/lock restoration, and independent address-space cursor mutation. Source assertions check fresh initialization and the locked private-fork snapshot. This harness uses mocked page-table lookup and backing files; it is not an SMP kernel test. Syntax checks passed for both modified production translation units.

A subsequent security re-review found that the initial per-page overlap scan could keep interrupts disabled for millions of iterations on a large valid reservation. The replacement `vos3_vmm_check_unmapped_locked` skips missing page-table subtrees and performs at most 4,096 four-level walks. Exhaustion returns EAGAIN before publication, cursor advancement, or backing ownership transfer. One TiB of empty address space takes two walks in the actual-walker host test. Dense table layouts may now return EAGAIN even when no physical allocation is requested; this is an explicit bounded-work compatibility limit, not an assertion that every large request succeeds.

The authorized follow-up also moves munmap into a VMM transaction. It allocates a bounded PTE journal before disabling interrupts, verifies the complete range and split capacity, acquires split backing ownership, then commits PTE removal and VMA edits under the same address-space lock used by mmap/mprotect. Physical-page and file releases occur after unlocking. No recursive call to the separately locking unmap API occurs. Private fork now snapshots VMA descriptors, retained backing files, cursor, brk, and flags in its existing source-lock interval, with failure cleanup outside the lock. The page-table clone implementation remains the existing COW mechanism.

Mprotect now applies the same 4,096-walk preflight bound, verifies continuous VMA coverage of every skipped absent subtree, and skips absent subtrees during commit. Budget exhaustion happens before permission or VMA changes. Existing W^X, COW, PROT_NONE, and unsupported huge-page handling remain. Large densely populated unmap/protect ranges may return EAGAIN before mutation; callers must not interpret that as a successful release.

`scripts/test_mmap_sparse_scan.py` compiles the actual scanner and four-level walker with synthetic tables. It covers 1 TiB sparsity, 1 GiB/2 MiB/4 KiB collisions, exact budget success/exhaustion, and invalid ranges. `scripts/test_vma_transactions.py` compiles the actual mmap, munmap, mprotect, clone and VMA lookup routines. Four pthread callers each run 100 allocate/middle-split/clone/release transactions, checking file-reference conservation and coherent nonoverlapping snapshots. Failure injections cover allocation, backing retain, page-table clone, budget exhaustion and huge pages; controls cover sparse protection, VMA gaps, COW permission preservation and page release after unlocking. Allocators, CPU/TLB operations and backing objects are mocks, so this is not a guest concurrency or physical-memory proof.

At this intermediate checkpoint, the four current host tests passed in `/private/tmp/vos-goal-native-20260917-mmap-bounded.log` and `/private/tmp/vos-goal-native-20260917-vma-transactions.log`. Production syntax checks passed with the existing unsupported GCC-warning pragma warning retained in `/private/tmp/vos-goal-native-20260917-vma-transaction-syntax.log`. Independent reviewer `/root/math_build_review` approved the bounded follow-up scope and reran four VMA test methods successfully. The later frozen BIOS qualification described below includes these VMA changes; it predates the subsequent MMIO and reproducible-packaging changes, which require their own final guest run. The unused `vos3_vmm_validate_user_unmap` API was removed: the bounded transaction replaces its separate, potentially unbounded preflight. Page-fault/uaccess, brk, SHM mutation, remote TLB coherence, and other shared-address-space concurrency are not comprehensively qualified by these transactions.

## Deferred reclamation review

Implementation author: `/root/math_build_review`; independent reviewer: `/root/mcp_upgrade`. The syscall dispatcher now invokes deferred work after the handler returns and before signal delivery. This adds an IRQ-enabled cleanup opportunity for workloads whose exits/waits and timer rescheduling otherwise run with interrupts disabled. The existing IF guard, tick-pending gates, current-task exclusion, and gated CPU-owner rules remain. A per-CPU active guard prevents recursive draining if a destructor blocks and another continuation reaches a safe point. Its validity relies on the existing no-live-migration scheduling constraint.

The reviewer inspected the dispatcher/guard changes and reran `scripts/test_deferred_syscall.py`, which compiles actual dispatcher and drain code with only the architecture flag read and subsystem operations mocked. Together with the mmap tests, three host tests passed in `/private/tmp/vos-goal-native-20260917-thread-repair-host.log`. The 512 KiB threshold was not relaxed and no warm-up or sleep workaround was added to the stress workload. At that checkpoint guest results were pending; the later frozen BIOS run below covers the repair snapshot, while host tests alone do not establish that all retired resources meet the native memory bound.

Package-mode focused invocation passes five tests after correcting conditional imports. Full `python3 -m unittest discover -s scripts -p 'test_*.py'` completed with **89 passing tests in 43.793 seconds**, exit 0; evidence: `/private/tmp/vos-goal-native-20260917-scripts-full-discovery.log`. This result includes the current merged host tests and does not substitute for native guest validation.

## Huge-page allocation and release

Implementation author: `/root/math_build_review`; independent reviewer: `/root/rust_upgrade`. The preserved guest log above reported `massive_context` failing at its first huge-page region and `hugepage_pool_alloc` obtaining only one region against its unchanged minimum of twenty. These observations establish the failing workload, not that a single allocator defect explains every failure. Source review found three concrete defects: `free_huge` decremented the reserved-pool active count even when the supplied address was not in its active partition; reserve and buddy range claims omitted initial page reference counts; and huge SHM creation selected an arbitrary prefix of free huge pages, rejecting it when sorting failed to produce physical contiguity even if another free run existed.

The original actual-function regression reproduced the release defect: a foreign physical address changed a pool with two active entries. It failed at `g_hugepage_pool_used == 2` in `/private/tmp/vos-goal-native-20260917-huge-provenance-before.log`. That failure is retained, not reclassified as a successful test. The expanded corrected regression passed in 0.506 seconds in `/private/tmp/vos-goal-native-20260917-huge-provenance-after.log`.

The corrected reserved-pool release changes ownership only for a known active entry. Foreign and duplicate returns do not decrement the pool or free unrelated pages. Buddy fallback allocations have separate provenance records. Their metadata is allocated and released outside the huge-pool lock. A request for one through sixteen huge pages explicitly requests a power-of-two-rounded buddy extent; only the requested pages are issued, and the whole original extent is released exactly once after every issued page is returned. A misaligned emergency bitmap fallback is rejected and its exact requested page count returned through the ordinary page-free API. This preserves fallback capacity without silently inserting foreign extents into the reserved pool.

The new contiguous-huge allocator sorts only the pool's free partition using heapsort, keeping physical addresses paired with cooldown timestamps. It selects an entire consecutive physical run before publishing ownership; the active partition is preserved. If no such run exists, the tracked buddy fallback is attempted. SHM uses this allocator instead of arbitrary-prefix allocation, retaining its sixteen-huge-page limit and existing physical-contiguity requirement. Neither benchmark workloads nor thresholds changed. Free-partition sorting is bounded by the configured pool size and O(n log n); physical contiguity is still not guaranteed merely by a high total free-memory count.

Reserved and buddy bitmap claims now initialize page references only after the complete claim succeeds. If a lock-free allocator wins a page during claiming, rollback affects only pages already claimed by this attempt and does not overwrite the competitor's reference count. Exclusive buddy release clears reference metadata before publishing free bitmap bits. The exclusivity requirement remains part of that allocator's caller contract.

[`test_huge_pool_provenance.py`](../../../scripts/test_huge_pool_provenance.py) compiles the actual reserve, claim/free, pool, fallback and contiguous-selection functions. Its deterministic controls cover foreign/duplicate release, active-entry preservation, scrambled free runs, one hundred shuffled pool orders, partial fallback returns, padding/duplicate returns, exactly-once rounded release, metadata/allocator/alignment failure, and competing bitmap-claim rollback/reference initialization. Allocator, IRQ, bitmap and lock boundaries are mocked: this is not execution of the real buddy allocator, hardware locking, or a guest SMP test. Cross-compiler syntax checks passed for the modified PMM and SHM translation units. The independent reviewer inspected these paths and reran the host regression successfully, identifying no blocker within this bounded scope.

The later frozen BIOS run validated this correction in the complete guest
sequence: `bench_hugepage_tlb`, `massive_context`, `hugepage_hard_wall` and
`hugepage_pool_alloc` all passed. Remaining boundaries include real physical
hardware/SMP behavior and stale-address ABA: a physical-address-only API
cannot distinguish an old stale return from a newly allocated object at the
same address. Pool reservation currently mutates the pool under the PMM lock,
not the huge-pool lock; its observed sole call site is the boot memory
initializer, before concurrent pool use. Runtime pool-reservation concurrency
is not qualified.

After removal of the obsolete unmap-preflight API, final full discovery again passed **89 tests in 41.805 seconds**, exit 0 (`/private/tmp/vos-goal-native-20260917-scripts-final-discovery.log`). Final syntax validation and `git diff --check` also passed; the existing ignored GCC diagnostic pragma warning remains recorded. Production changes are frozen for native validation.

## Synchronization, IPC lifetime and interrupt input

The follow-up repairs close lost-wakeup windows by publishing each embedded
wait entry under a global membership lock and the primitive queue lock before
yielding. Cancellation detaches the exact embedded entry. Mutex, semaphore,
condition, reader/writer and barrier paths use that membership protocol.
Condition timed wait now returns an explicit unsupported error instead of
blocking forever. Reader/writer state changes are serialized, foreign writer
unlock is rejected, and wake-all avoids stranding readers when a queued writer
is cancelled. The documented policy does not promise writer fairness.

Semaphore close wakes blocked users and reports closure. Initialization,
posting and reader acquisition reject or stop at `INT32_MAX`, avoiding signed
overflow. Barrier generation and count use unsigned operations, including the
wrap boundary. `scripts/test_sync_waitqueue_protocol.py` compiles actual
primitive functions and exercises publication races, cancellation, repeated
barrier generations, spurious wake, generation wrap and saturation boundaries.

Message queues now use generation-bearing opaque handles, immutable creator
identity for destroy authorization and a registry reference plus operation
pins. Destroy removes the handle, closes both wait channels and drops the
registry reference without a yield loop. The final reference owns object
destruction. A task registers a single cleanup claim while blocked on a queue
semaphore; normal wake and final task reaping race to claim it exactly once.
The message list uses a short IRQ-save spinlock, so no later sleeping mutex can
strand a pin or allocated entry. Fork and clone clear copied cleanup state.
`vos3_msgq_find` copies the ID before releasing the registry lock.

`scripts/test_msgqueue_lifetime.py` runs the actual queue routines under
ASan/UBSan. It covers blocked receive versus destroy, concurrent send/receive/
count/find versus destroy, stale handles, foreign destroy, quiesced killed
wait cleanup and 10,000 disarm/reaper ownership races. This proves the host
model and the UP task-reaper contract; remote kill while another CPU is still
executing the target remains outside the evidence.

TTY input no longer takes a sleeping mutex in the keyboard interrupt path.
The producer updates the buffer under an IRQ-save spinlock and posts the
semaphore after releasing it; readers wait first and then take the same short
spinlock.

## Deferred-work liveness and chaos workload

Deferred-work requests are per CPU. Task retirement targets the recorded
owner in native SMP builds, every CPU tick re-arms its pending bit while the
shared reaper list is nonempty, and all lockless reads and locked publications
of the reaper head use matching atomic operations. Empty syscall safe points
avoid subsystem scans. The focused deferred test covers IRQ-disabled refusal,
recursion, CPU targeting and dispatcher order.

`test_agent_chaos` now initializes and detaches public SHM before fork, lets
the child attach independently, and reattaches only after exact child PID and
successful exit status are established. The 20-object phase maps in windows of
16 to respect the address-space tracking bound while preserving all 1,115
operations. Unmap, destroy and telemetry failures participate in the verdict;
the soak path also rejects cleanup and sysinfo failure. A failed wait attempts
to terminate and reap the child before the suite continues.

## Final host and native qualification

Final host discovery passed **96 tests in 54.517 seconds**. `make
native-build-check` passed every invoked group. The complete evidence is in
[`native-thread-memory-repair-2026-09-17/`](native-thread-memory-repair-2026-09-17/README.md).

The frozen-source container build produced ISO SHA-256
`ce55108cb59e15af215d6861bc2d2a357ea99403bd32fe5f54f8fac750d7212c`.
The source verifier found no mutation of the 6,137 manifest entries. A q35/TCG
single-CPU BIOS run then started the complete BENCH_MODE list: **57/57 programs
returned zero**. `test_agent_chaos` passed 4/4 with 1,115 fragmentation
operations, zero failures and a one-page delta. `test_health_check` passed 4/4
at 97,389 messages/second. The ISO hash was unchanged after QEMU.

This closes the current single-CPU BIOS guest regression. Full-suite UEFI/multi-CPU and
physical-hardware execution, hostile DMA, remote SMP kill/reclamation and a
second byte-identical ISO build remain unqualified. The expert council's
evidence-backed priorities and recent-source research are recorded in
[`expert-council-review-2026-09-17.md`](expert-council-review-2026-09-17.md).

An additional run of that frozen ISO under OVMF executed all 57 programs but
returned 55 successes. `test_npu_direct` failed because it treated the
unbacked constant `0xFD000000` as emulated NPU memory; QEMU's post-firmware
memory tree shows no mapped region there. `test_health_check` transferred the
same message count at both ends but measured 77,882 messages/second, below its
unchanged 80,000 threshold. The performance observation ran while other TCG
and container qualifications were active, so it remains a recorded failure
pending an isolated rerun rather than a threshold change.

Review of the NPU failure exposed a more serious policy flaw: an AI context
alone authorized every non-PMM physical address because the hardware check
ignored its address and size. The current follow-up denies all user MMIO until
an enumerated BAR registry, immutable ownership and an enforcing IOMMU domain
exist. The guest probes now report hardware qualification as `UNAVAILABLE`,
prove the denial and award no NPU hardware score. See
[`mmio-fail-closed-2026-09-17.md`](mmio-fail-closed-2026-09-17.md). This
production change requires a new frozen BIOS/UEFI full-suite run; the earlier
57/57 BIOS result remains evidence for the preceding snapshot only.

## Separate diagnostic SMP qualification

A fresh dirty-source snapshot at the same base commit and dirty patch hash
was built with `HEADLESS_AUDIT=1 NATIVE_SMP_WORKLOAD=1 NATIVE_SMP_TEST=1`.
BIOS/UEFI with one/four vCPUs all passed `native_smp_smoke.py`, each over
45 seconds. Both four-vCPU runs correlated deterministic CPL3 worker
checkpoints with kernel scheduling on APIC IDs 0 and 1. Build exit was zero,
source inputs were unchanged and ISO SHA-256 was
`28acdee910cab6adcad1169ec182e2429d9aeebca5c179d09931a6900d6be5f3`.
The [raw SMP evidence and hashes](native-thread-memory-repair-2026-09-17/smp/README.md)
preserve the clock-skew warning and all four results. This is diagnostic-only
coverage, not proof of simultaneous execution, fairness, work on every online
CPU, production SMP enablement or the full 57-program suite under UEFI/SMP.

## Reproducibility investigation

An independent rebuild of the first frozen input initially failed the
byte-identical gate: the ISO had the same size but 2,555 differing bytes.
Kernel and init were identical; the differences reduced to Limine BIOS
artifacts whose three embedded GNU build-id descriptors included private
scratch-path debug input. The ISO packager also had no fixed filesystem and
volume dates.

The build-only correction retains GNU build IDs, removes unpublished debug
sections from Limine's intermediate target ELF before their IDs are derived,
maps private source paths to a stable prefix, and sets all image dates from
`SOURCE_DATE_EPOCH`. Its cache fingerprint includes every consumed host and
target compiler, linker and assembler flag. A reusable two-clean-tree checker
normalizes source mtimes, verifies source hashes, uses the pinned offline
builder and compares the ISO, kernel and six Limine artifacts. Its pre-MMIO
validation produced two identical ISOs with SHA-256
`afac3b3a2c1e0d567baaa2a9b982c5a0a67400895f4df7d70292893c0b617935`.
Final reproducibility and boot qualification must be repeated after the MMIO
policy change; this intermediate success does not certify the final snapshot.
