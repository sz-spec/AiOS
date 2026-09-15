# Independent native memory-transition security review

Scope: active mprotect, munmap, demand paging and COW paths, before root's bounded fixes. Read-only source review; no exploit execution, production edits or VM launches by this reviewer. Prior direct-access isolation passes do not establish correctness of permission transitions.

## Prioritized findings

1. **Critical user/kernel range boundary:** `sys_mprotect` forwards almost any nonzero range to `vos3_vmm_mprotect_range`, which checks alignment/W^X but not user address bounds or arithmetic overflow. `vos3_vmm_update_flags` selects the global kernel address space for higher-half targets. An untrusted syscall can therefore reach kernel PTE mutation. Effective user access may still be constrained by upper-level permissions, but modifying supervisor mapping write/NX state is itself outside the authorized boundary. Reject noncanonical/kernel/overflowing ranges before any walk or mutation; test kernel target rejection and unchanged PTE/canary.

2. **Lazy protection bypass:** the demand-fault handler uses `vos3_vmm_is_valid_user_addr`, which accepts a VMA based on range alone, and installs every page as USER|WRITABLE|NX. A lazy PROT_NONE or read-only VMA therefore gains writable backing; an executable VMA cannot execute correctly. Validate the attempted access against VMA protection before allocating, then derive page permissions from that protection. Preserve intentional heap/stack defaults separately; do not infer permission from membership alone.

3. **mprotect metadata and COW:** mprotect changes only present PTEs and never the VMA protection. Unpopulated pages silently skip updates; later faults resurrect old/default permission. PROT_NONE converts to a user-present readable mapping. A writable transition on a COW page replaces flags on the shared physical frame and can bypass copy separation. Preserve sharing semantics (keep write-protected COW or materialize a private copy) and update/split VMA metadata consistently before later faults. The COW fault handler also needs to respect current logical write permission so a previously COW-marked page cannot bypass a later read-only transition.

4. **munmap range and failure atomicity:** `sys_munmap` processes only the first VMA containing the starting address, clamps to its end, and returns. Ranges spanning multiple VMAs or beginning in holes are not handled coherently. It unmaps physical pages before securing a split slot; on slot exhaustion it truncates the VMA and returns ENOMEM, losing the surviving tail's metadata. Failed file-descriptor duplication silently substitutes an anonymous tail. Validate arithmetic and plan/reserve splits/resources before destructive changes; preserve file offsets and backing references, and test cross-VMA/hole/middle-split cases and resource failure.

5. **TLB and SMP scope:** update_flags, unmap and COW use local INVLPG. Unmap can release the physical frame while another CPU retains a translation for the same address space. Existing flush_range iterates by 2 MiB, which is insufficient for arbitrary 4 KiB ranges; its unchecked timeout path logs and continues. Require a correct acknowledged shootdown before reuse/restriction or prove/enforce that the affected address space cannot execute concurrently elsewhere. Merely reporting four CPUs online is not that proof.

All range paths need checked page-rounding/end arithmetic and defined treatment of unsupported protection bits. Success must not conceal ignored PTE-update failures. Huge-page subrange operations require explicit split/reject policy rather than accidentally modifying or freeing an entire larger mapping.

## Bounded regression expectations

Use actual CPL3 probes for lazy/resident PROT_NONE, write to RO, execute NX, and a permitted execute control. Test permission changes before first touch and after warming a translation. Fork then apply RW/RO transitions while verifying parent/child private sentinels and logical permission enforcement. Unmap a middle page and require both surviving sides intact while the hole faults; extend to a range spanning VMAs and resource-exhaustion failure without lost survivors. Reject kernel/noncanonical/overflowing ranges before mutation. Bind expected faults to PID/address/error bits and verify unaffected process progress.

## Status

Findings sent to root for bounded implementation and tests. No fixes or runtime transition qualification are claimed yet. Reachability details and concurrency assumptions remain to be confirmed against the final implementation.

## Authorship boundary and syscall patch in progress

Root subsequently assigned this reviewer ownership of `kernel/src/exec/exec_syscall.c` fixes. Accordingly, the syscall implementation below is **not independently audited by its author**; independent mathematics review was requested. Root retains ownership of VMM/demand/COW changes and runtime tests.

The syscall patch checks page rounding, canonical user bounds, protection bits and file-offset arithmetic before mutation. mmap rejects overlapping fixed mappings with EEXIST instead of unsafe replacement and defers the allocation cursor update until success. This intentionally does not implement POSIX fixed replacement; a nonfixed bump collision also returns an error instead of searching a gap. munmap processes every intersecting VMA and holes, reserving a middle-split metadata slot and backing FD before unmapping so resource failure leaves mappings intact.

Integration remains pending for VMA synchronization, partial huge-page handling, and propagation of low-level unmap errors; the existing unmap count interface cannot distinguish an absent lazy page from all failure modes. No clean build/runtime pass is claimed for this draft.

## Review of root's VMM correction (before runtime)

Root's revised demand fault path checks access type against VMA protection before allocation and derives WRITE/NX from that protection. Resident PROT_NONE retains its frame with USER cleared, allowing restoration without losing data. mprotect plans VMA splits before committing, rejects unsupported huge-page permission operations, and retains private-write separation by keeping multiply referenced frames COW. Fork no longer marks genuinely read-only mappings writable through COW, and the COW handler checks VMA WRITE permission. Moving COW away from the AI flag alias removes an unrelated policy-bit collision.

These directions address the identified single-process/VMA permission bypasses, but final runtime evidence is still required. Residual review concerns include permission metadata for resident heap/stack mappings without VMAs, FD duplication while holding the address-space lock with interrupts disabled, unsynchronized shared-VMA mutation, partial huge-page unmap behavior, and remote TLB invalidation/frame reuse. Twelve sequential cases on a multi-CPU VM would not close concurrent address-space safety.

Independent test review found the two COW cases correctly verify private child mutation against unchanged parent data. Requested stronger middle-unmap coverage (both surviving child pages), exact kernel-range rejection errno, and a permitted-execution control or explicit missing coverage. Independent review of the syscall implementation was requested from the mathematics reviewer; its author does not self-certify that patch.

The mathematics reviewer independently checked the syscall multi-VMA/preflight loop and found no further sequential anonymous-4KiB blocker. It identified unsupported MAP_SHARED/unknown flags being accepted despite private COW semantics. The author corrected mmap to require MAP_PRIVATE and reject unsupported flag bits with EINVAL; the independent reviewer confirmed that guard. Shared mappings and fixed overlap replacement are therefore explicit compatibility limits, not silently emulated incorrectly.

The mathematics reviewer strengthened its user test to require exact EINVAL for the kernel-range mprotect rejection and verify initialized left/right child survivors plus all three parent pages in the middle-unmap case. This reviewer independently reran the strict memory-classifier suite: **6 tests passed**. These are host oracle tests; native workload execution remains pending.

## Independent unmap preflight review — 2026-09-15

Root added `vos3_vmm_validate_user_unmap` and wired it before syscall VMA/FD mutation. Independently inspected it against the actual page-table walker: missing levels 1/2/3/4 correspond to 512 GiB/1 GiB/2 MiB/4 KiB spans, so absent-subtree skipping is consistent. Present mappings above the 4 KiB leaf return ENOTSUP before mutation. Checked canonical/end bounds prevent iteration overflow. This closes the previously identified accidental huge-leaf unmap path for the sequential syscall operation by rejecting unsupported mappings, rather than implementing huge-page splitting.

Concurrency remains distinct: the validator releases the address-space lock before mutation, allowing a concurrent mapping change between validation and use. `CLONE_VM` copies the address-space pointer without maintaining `as->ref_count`; that field cannot currently establish exclusive ownership. An `is_thread`-only guard also misses the original parent sharing its space with children. A safe exclusivity guard requires maintained owner accounting or a task-lifecycle-synchronized ownership check; remote TLB acknowledgement is still needed for concurrently executing shared spaces. No shared-VM safety claim follows from this preflight.

## Final workload/oracle review before clean execution

Independently reran all three native classifier suites: **22 tests passed**. The final memory workload includes exact EINVAL guards for kernel ranges, maximum-length overflow and W+X requests. Its positive execution control writes `mov eax,42; ret` while RW, changes the page to RX and requires a return value of 42; an NX fault cannot pass merely because every execution attempt fails. Middle-page mprotect verifies both neighboring child pages remain writable, then faults on the protected middle; parent checks all three original canaries. Middle munmap checks both surviving child pages before faulting on the hole.

The root's final empty-range mprotect correction preserves the valid-user no-op while still checking alignment, kernel range, unsupported protection bits and W+X first. This does not reopen the unprivileged higher-half mutation path.

Clean committed-source matrix evidence remains pending. In particular, online AP counts do not guarantee that the user workload ran on an AP; no AP execution or cross-CPU TLB/COW claim is made by these sequential tests.

## Final clean memory-transition gate

Independently reviewed `/private/tmp/vos5-memory-clean-20260915/result.json`, recomputed its ISO/kernel/user-test hashes and reclassified all four raw serial logs with the reviewed oracle. Committed source `56ed649889f7200b7ab49504c3f2db6f75e23c75` passed BIOS/UEFI with 1/4 configured CPUs: **12 sequential cases per configuration, 48 observed cases total**. The clean build/matrix completed in 115.58 seconds and recorded successful build-container cleanup.

ISO: `225bdead22328826569c8d1cb6eaffa214cd6f1a2822ff9548e6bd259daf9e1b`; kernel: `13928ca6adc0748bee2c275d9fe0fc1b7e809d954dd579bb796c1d510e8cffaa`; user test: `d25fd8e5c0c47c69fb163074ad27d2016ac6660e16705936cf9596d9c3263f45`. Each observation retained the same ISO hash before/after execution. Actual logs satisfy exact guard errors, positive RX execution, correlated expected faults, COW private-write results, preserved parent canaries and final progress.

The bounded sequential memory-transition gate is accepted. Independent evidence review here covers others' VMM/test/classifier changes; the syscall patch authored by this reviewer received separate mathematics review. Additional normal-boot and original-isolation regression runs are tracked separately and were still running when this assessment was written.

Remaining limits are not closed by this pass: shared-address-space mutation/COW concurrency, remote TLB acknowledgement, AP user-workload execution, fault-injected split-resource exhaustion, complete file-backed mapping semantics and full POSIX mapping compatibility. Huge-leaf unmap is rejected rather than supported. No universal security or physical-hardware claim follows.

Post-run oracle hardening: root added rejection of `NATIVE_ISOLATION FAIL`, the failure marker emitted by the shared kernel-canary diagnostic hook, with a negative fixture. Independently reviewed the two-line change, reran all six memory-classifier tests successfully, and reclassified the four original raw serial logs: all retain 12 passing cases. This is stronger analysis of the same observations, not a new OS build or VM run. The original aggregate classifier hash remains historical provenance; final reclassification is recorded separately in `final-reclassification.json`.

The eight additional regression runs are now complete. Independently reclassified all persisted raw `serial.txt` traces under `native-memory-2026-09-15/regressions`: four normal BIOS/UEFI × 1/4 CPU boots and four original isolation runs all pass their respective strict classifiers. Both build exits are zero. The source/exclusion evidence reports 3,539 kernel/user source files matched against the clean manifest and absence of both diagnostic programs, diagnostic symbols and synthetic target environment from the normal build. These are regression builds with an existing bootloader reused, not additional clean-build or reproducibility claims. This supersedes the earlier pending-regression statements; the concurrency and hardware limitations remain unchanged.

Final publication scan: independently reviewed all 133 `generic-api-key` findings in `/private/tmp/vos5-memory-secret-review.json`. Every flagged source-manifest value exactly matches the independently recomputed SHA-256 of its corresponding file inside the clean source snapshot. These findings are hash false positives; no potential secret values were printed and no suppressions were added. This conclusion is limited to the reported findings, not repository history generally.
