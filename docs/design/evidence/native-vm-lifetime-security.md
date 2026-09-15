# Independent shared-VM lifetime and PTE-policy review

Scope: COW/cognitive flag allocation and address-space retain/release across clone, fork, exec and task reclamation. Root owns implementation; this reviewer reads source and edits only this document. No VM execution or production edits by this reviewer.

## Early blockers

`CLONE_VM` shares an address-space pointer without retaining it. Reaper/direct-destroy paths free address spaces based on `!is_thread`, while successful exec destroys the old space directly. Both can free a space still referenced by another task. Every task reference needs balanced ownership, including threads, with kernel space explicitly immortal. Acquire before publishing a child and unwind on every later failure; release only after the executing CPU's CR3/entry-root references have switched away.

Address-space destruction closes VMA backing descriptors through `vos3_close(fd)`, which resolves the current task's descriptor table. The reaper may run as idle or another task, and shared-VM clone need not share descriptors. Bare descriptor numbers are not an independent address-space backing reference. Use retained file objects or a deliberately retained owning table and an explicit close operation; otherwise a final release can close the wrong file or lose backing lifetime.

SHM mappings/refcounts are stored per task, with cleanup skipped for threads. A parent may release SHM state while sharing children still use the address space. AS-owned mapping cleanup must occur at final ownership release, or unsupported sharing combinations must fail before publication. A reference count alone does not repair metadata ownership.

Thread relationships also retain raw `parent` and `thread_group_leader` pointers. A parent-exits-first test can expose task-structure lifetime issues separately from page-table lifetime; do not infer those pointers are safe after adding AS references.

Fork's post-COW task-registration failure currently frees the child stack/task without releasing its newly cloned address space and inherited file references. This unwind belongs in the ownership audit. Exec temporarily installs a new address space during loading; rollback must restore the old active root before releasing the failed new space. Success must keep the old reference until the new root is active. A bounded implementation may reject shared-AS exec before mutation until complete thread-group/FD/SHM semantics are designed; such a restriction is not full POSIX multi-thread exec.

## PTE-policy independence

COW and cognitive metadata must occupy distinct software bits that do not intersect physical address, hardware permission or other policy masks. Test both directions: marking cognitive does not create COW, and clearing/resolving COW does not erase cognitive state. Validate clone, mprotect and COW fault transformations, not only the constant definitions. Existing simple COW tests did not exercise the combined cognitive policy.

## Required bounded evidence

Test parent-before-child and child-before-parent lifetimes, multiple sharing children, final release exactly once, failed clone/fork unwind, and successful/failed exec reference balance. A native child must continue accessing known shared pages after the parent's exit/reap, with actual process/root identities and final cleanup evidence; a count-only host model is supporting evidence, not the native lifetime proof. Include file-backed/SHM ownership cases or explicitly reject and document unsupported combinations. Preserve the existing COW, protection-transition and normal-boot regressions.

This stage does not establish concurrent shared-VMA mutation or remote revocation safety. The accepted TLB primitive is not automatically integrated into every teardown or address-space switch.

## Status

Findings sent to root before implementation. Awaiting settled diff and independently reviewable host/native evidence.


## Backing ownership implementation boundary

The security reviewer implemented the bounded VMA backing prerequisite at the coordinator's request. This portion therefore requires another reviewer's independent assessment. VMAs now own file references rather than descriptor numbers: mmap obtains a retained file, splitting and COW cloning retain additional references, and removal or final destruction releases the exact backing object. Reaping no longer closes a descriptor in an unrelated current task's table. Demand paging uses the retained file's operations directly and rejects offset overflow, failed seeks, negative reads and oversized read results instead of mapping zero-filled success after I/O errors.

`git diff --check` passed after these edits. Compilation and native validation are pending integration by the coordinator; no runtime success is asserted here. The mathematics reviewer was asked to cross-review this implementation.

Positional file I/O remains a concrete limitation: the filesystem interface exposes seek/read operations, and ramfs takes its file lock inside each operation. An outer lock would recurse; seek/read/restore is not atomic against other users of the same open file. This migration resolves object ownership and descriptor reuse, but does not qualify concurrent file-backed faults or a general pread implementation. Concurrent VMA mutation and blocking filesystem operations in fault context also remain outside the bounded proof. The independent reviewer identified file-reference overflow; backing splits and clones now use the coordinator's checked retain helper, with pre-mutation failure or partial-clone release.


## Integrated source review and added native control

Reviewed the coordinator's deferred address-space release queue and CPU pins: incoming roots retain before binding/CR3 load, outgoing roots release after replacement, and queued zero-reference objects reclaim from deferred scheduler work with interrupts enabled. This covers reference ownership; it does not make shared page-table edits atomic or integrate remote TLB revocation everywhere. Reported a gated scheduler regression where shared-VM children reset their CPU owner; the coordinator restored inheritance to preserve the previous concurrency boundary.

Reported a concrete remaining SHM detach defect: ordinary explicit detach called the physical-freeing unmap-range helper, despite SHM retaining ownership of the backing. The user huge-page detach path also called a kernel-address-only helper. These findings were sent to the coordinator and the independent SHM-test author; fixes and runtime evidence remain pending at this review point.

The reviewer's added `user/src/test_native_backing.inc` exercises seven lazy file-backed pages after closing the original descriptor and reusing its number for an unrelated sentinel file. It splits VMAs with both mprotect and middle munmap, forks before faulting any backing page, verifies every byte of five surviving pages in child and parent, removes all remaining mappings, and verifies that the reused descriptor remains usable. This is an authored test requiring independent cross-review, not an independent certification of its own implementation. The mathematics reviewer integrated its mandatory marker and reviewed the control sequence.

Independently inspected the mathematics reviewer's clone assembly, malformed-ELF failure, and successful self-exec detach controls. The new-stack assembly avoids resuming a C frame on the replacement stack. Pipe challenges require the surviving child to access shared data after the owner has exited or successfully replaced its image. Wait completion alone still does not prove physical reclamation; the separate real-PMM kernel tests cover final release accounting. An initial review statement that a missing file fails before address-space allocation was incorrect: the actual exec path switches to the new space before loading the file. This was corrected with the coordinator.

The integrated user workload passed freestanding x86-64 Clang syntax checking. The six memory-oracle host tests passed through `python3 scripts/test_native_memory_smoke.py -q`; a first module-style invocation failed because the script import directory was absent, then the supported direct invocation passed. No native run has yet been reviewed for this new lifetime stage.


## Development evidence and exact backing-release control

Reviewed `/private/tmp/vos5-lifetime-dev-bios1-r2/serial.log`: the earlier development image emits process-root, SHM ownership and metadata/COW kernel PASS records, followed by malformed-ELF restore, owner-exit survivor, successful exec-detach survivor, owned-file backing and complete twelve-case user records. This is a development BIOS/one-CPU observation, not a clean final matrix or evidence for tests added afterward.

The SHM author replaced user rollback/detach calls with non-freeing per-mapping unmap operations, avoiding both borrowed-frame release and the kernel-only huge-unmap assertion. Source review confirms unexpected detach errors retain the lifetime record for retry/final cleanup. The real SHM test now checks the backing PMM reference immediately after explicit detach, before reading the remaining aliases.

Added `test_file_reference_ownership()` to the kernel backing test at the coordinator's request. It uses real production address-space clone, retain, release, CPU pin and deferred-reclamation operations with real PMM allocations. Only the filesystem object and its close callback are synthetic; the callback does not free the stack-owned test object. Assertions cover zero/saturated retain rejection, saturated clone unwind, exact clone backing-reference increments, partial/final AS releases, active-CPU retention, exactly one close after the last unpin, repeated reaping and restored PMM availability. It requires a new `[VM-FILE-REFS] PASS` marker. Syntax validation passed; independent cross-review and native execution were requested. No filesystem-concurrency or allocator-destruction claim follows from this synthetic callback test.


## Final source review before clean runtime

Reviewed committed source `32187957757ab57d7820d0a63fa62c409003b126`. Fork and shared-VM clone now reject a parent with an AI guard context using `-95` until policy-preserving inheritance exists. The inspected interface exposes context creation and destruction, without a clone/retain contract; explicit refusal preserves the security policy better than clearing a copied context. This is a compatibility restriction, not newly supported guarded-process cloning.

Shared-VM clone logs and parent-TID handling occur before scheduler publication, and its return value is captured before publication, avoiding later child dereferences in that path. The final synthetic backing test excludes local interrupts only while borrowing a CPU root without changing the task pointer; it restores the original root and interrupt state before the final close callback. The test runs at the boot qualification point with no competing test-owned reclaim work. The mathematics reviewer independently confirmed its reference-count sequence and mandatory observer marker.

Clean matrix execution is pending review. Earlier development output is not substituted for the committed-source result.


## Clean matrix result: gate remains open

The first clean committed-source run at `/private/tmp/vos5-lifetime-memory-20260915/result.json` reports `passed: false`, `AssertionError: bios4`, build exit zero and successful builder-container removal. BIOS1 and UEFI1 passed; BIOS4 and UEFI4 stop after the shared survivor PID 301 logs exit zero, without its parent's required verification/progress. The raw BIOS4 trace reaches all twelve transition cases and failed-exec verification before this point. This is a real failed qualification, not approval based on partial markers.

The recorded ISO is `8731a8fc6d092e39f78e167b7e9a6917a6e9302446d05a436af06e52a7746a11`; source remains `32187957757ab57d7820d0a63fa62c409003b126`. Artifact values here are transcribed from the failed aggregate; complete independent hash requalification awaits the corrected run. Root is collecting CPU state. Read-only triage distinguishes exit scheduler locking, sleeping-parent wakeup, and resumed deferred cleanup; no single cause is asserted from the final log line alone. The clean lifetime gate is not closed, and later regression matrices are not assumed to have run.


## Four-CPU failure diagnosis

Independent source inspection confirms the coordinator's PID-collision explanation. `vos3_task_create` assigns kernel task PID equal to its allocated TID, per-CPU idle tasks are registered in that table, and `init.c` separately sets the user init PID to one. The old exit reparent scan selected the first table entry with PID one without checking IDLE or USER flags. Therefore an AP idle task can be selected as the orphan adopter before the real user init. The coordinator's task-state capture identifies that wrong adopter; this reviewer independently confirmed the responsible source paths, not a separate guest-memory decoder.

The intended correction selects the non-idle user init and excludes the exiting task. It directly addresses the observed multi-CPU-only failure without weakening the observer. Generic `vos3_task_find_by_pid` has the same first-match ambiguity and should prefer a real user process where appropriate while preserving any required kernel lookup semantics. Corrected runtime evidence is still required; the failed aggregate remains valid historical evidence.


## Corrected clean gate: bounded approval, 2026-09-15

Independently verified `/private/tmp/vos5-lifetime-memory-final-20260915/result.json` for source `3ce56da73271db2e30ac4b226db88bb72a979d50`: all BIOS/UEFI one/four-CPU raw serial logs reclassify successfully with the archived classifier, including all 48 transition observations, failed-exec restoration, both survivor sequences, actual file backing and the exact-once synthetic callback control. Each log contains exactly one required VM-FILE-REFS marker. The formerly missing parent verification now appears in both four-CPU configurations; the original failed aggregate is preserved.

Recomputed hashes for all 5,594 recorded source files, the source archive, classifier, actual ISO, kernel and user test. All match their recorded identities. The manifest identity uses the harness's sorted canonical JSON serialization, not the pretty-printed file bytes. ISO: `4f5906cb3b10a80375712ef85488f7f491f5033102c9e9cc5d48606ee1079c38`; kernel: `601832043baec7aa94e5b8ed2a699a74c2ff8865d512cfff2dd64d6c87524c50`. Aggregate reports build exit zero, unchanged inputs, builder cleanup and 106.71 seconds. Independent recomputation is saved at `/private/tmp/vos5-lifetime-security-independent.json`.

This closes the bounded sequential address-space lifetime/backing/metadata qualification and the observed idle-adopter regression. It does not qualify remote shared-VMA mutation, concurrent file faults, all PID lookup semantics, full guarded-process inheritance, physical hardware or general-purpose release security. This reviewer authored portions of backing ownership/tests; their independent mathematics review is separately identified above. Normal/default-off, TLB and prior-isolation regressions are still pending in this section and must not be assumed complete.


## Normal configuration and evidence scan follow-up

Independently reclassified all four normal BIOS/UEFI one/four-CPU logs from `/private/tmp/vos5-lifetime-normal-final-20260915` with its archived boot observer. Recomputed all 5,594 source hashes and the actual observer, kernel and ISO identities. Normal ISO `9c850ceea84bf44a27eb41f147ae51606ba30dfb8ce7ccc6a50f1b28932c6579` and kernel `8b3962a1f3a206263cfd8d8ccebb68eb6d3460df4d0f6d046e60d95701126a36` match source `3ce56da`. The recorded normal build configuration contains none of the isolation, memory-transition, process-root, SMP-workload/activation or TLB-test/skip-flush diagnostic defines. This verifies default build configuration, not absence of the intended production lifetime fixes.

Independently recomputed all 136 flagged source-manifest line values against the corresponding clean memory-run source files, verifying both each line's path and its SHA-256. Every finding is a source-file digest false positive; no secret values were printed and no suppressions added. The retained scan review reports two source commits with zero source findings. TLB and previous-isolation regression matrices remain pending; normal and memory/lifetime are now verified.


## TLB regression and outstanding SHM finalizer gap

Independently reclassified the four raw TLB regression logs at `/private/tmp/vos5-lifetime-tlb-final-20260915`, verifying all recorded source hashes and actual classifier/kernel/ISO identities for `3ce56da`. All pass. ISO `9a56c12140e6ea0584a406b20b557f3539c1f8dd3692965c5dfe1bf49c9529bf`; kernel `68cdb92e945946b5f35b852e7a4ae618b37e7e5515395e0f0f0d2aa433f94b3c`. Aggregate duration is 106.03 seconds. Prior-isolation regression is still pending.

**P1 resource-lifetime gap, independently confirmed:** `vos3_shm_destroy` only finalizes physical/kernel mappings, table slot and descriptor when its own decrement reaches zero. If the creator releases first while mappings remain, it returns without finalizing. A later final mapping decrement in `vos3_shm_unmap` or `vos3_shm_dec_refcount` can reach zero without invoking that finalizer. This leaks region resources and can exhaust memory/SHM slots; it is not fixed by moving mapping records to the address space. The current native SHM test deliberately releases the creator last and therefore does not cover this order. No full SHM lifetime approval is given.

Release condition: one coordinated last-reference finalization protocol used by every release path, with creator-first/final-explicit-unmap and creator-first/final-AS-reap native tests proving exactly-once slot removal and PMM/kernel-mapping recovery. Concurrency and failed detach controls must remain fail-closed. This finding is retained as a separate open boundary; it does not erase the narrower creator-last evidence.


## Creator-first correction: independent source review

Reviewed the SHM author's settled working diff before the new commit. A private owned-reference release helper serializes every decrement with registry lookup and map pinning, refuses zero, and gives the unique final transition the invalidation/finalization work. The slot remains reserved through physical cleanup. Public creator close is recorded separately and a duplicate creator close is rejected before decrementing mapping ownership. Map pinning rejects zero and saturation; rollback and explicit-unmap paths release the region mutex before a potentially final put and do not dereference the region afterward. No blocker was found in the bounded sequential owned-reference paths.

The added native controls cover creator-first explicit detach and creator-first final-AS reap, assert that duplicate creator close preserves the live mapping, then require absent registry/name lookup, zero backing PMM reference and backing/table PMM recovery after last release. They avoid reading freed backing. Independent freestanding Clang syntax validation passed with one existing unsupported-GCC-warning pragma warning; the six observer tests passed. Native rerun remains required; previous `3ce56da` matrices do not validate these new paths.

**Separate IPC authorization boundary:** `kernel/src/ipc/ipc.c:sys_shm_destroy` forwards a user-provided region ID directly to public destruction. The new creator-released flag distinguishes reference type but does not authenticate the caller against the creator. A foreign caller can request creator release by ID under the inspected code. This was reported to the coordinator. Likewise, concurrent unmap/raw lookup lifetime and stale ID reuse are not proved by the sequential finalizer tests. Full IPC authorization and concurrency approval remain excluded.

The reachable unauthorized creator-destruction path is recorded as a **P1 release-blocking IPC authorization backlog item** in council `ACTIONS.md`. Closing the sequential reference lifecycle does not close this authorization gate. Required validation is a foreign-principal native destroy attempt rejected without changing the victim region, backing references or owner access, with stale-ID reuse controls. No production authorization change was made by this review.


## Creator-first release qualification, source 3daf340

Independently rehashed the new clean memory source/archive/classifier/kernel/user/ISO and reclassified all four raw logs for `3daf340141b90690a47bad84c51523c44c243e20`. All pass, including the strengthened creator-first explicit-detach and final-AS-reap assertions and duplicate-creator-close negative control. Portable `native-vm-lifetime-2026-09-15/security-independent.json` records artifact identities and raw-log hashes. This closes the identified sequential creator-first resource leak; the separately recorded unauthorized foreign creator-destruction path remains a release blocker. Normal/TLB/isolation results for this final source are still pending.


## Final security disposition — all release-source matrices verified

For final source `3daf340141b90690a47bad84c51523c44c243e20`, independently verified all four matrices: memory/lifetime, normal boot, TLB and prior process isolation, each across BIOS/UEFI with one/four CPUs. All sixteen raw logs pass their archived observers. Recomputed each matrix's 5,594 source-file hashes, canonical manifest, source archive, actual classifier, kernel, ISO and applicable user workload. Before/after ISO identity matches every case. Normal configuration excludes the diagnostic and experimental activation defines. All aggregates report builder cleanup. Portable `native-vm-lifetime-2026-09-15/security-independent.json` now records `complete: true` with actual artifact and raw-log identities.

The bounded result is 48 memory-transition observations plus required lifecycle/backing/SHM creator-first controls, four normal boots, four TLB configurations and sixteen prior-isolation access cases. This supersedes earlier pending regression statuses, without removing either failed initial run or intermediate-source evidence. The independent verification script used the harness's actual generated user-bin paths and the TLB profile's SMP workload ELF; corrected verifier path assumptions were not runtime failures.

Approve these sequential ownership, cleanup, metadata and regression gates within their recorded emulator configurations. Do not infer general IPC authorization, concurrent shared-VMA mutation, positional backing-I/O safety, universal PC support or release readiness. Unauthorized SHM destruction remains explicitly release-blocking in council ACTIONS; the creator-first resource leak is closed by source and native evidence. Authorship remains disclosed: this reviewer authored file-backing portions and their tests, which received the separately identified mathematics cross-review; SHM finalizer source was independently reviewed here.
