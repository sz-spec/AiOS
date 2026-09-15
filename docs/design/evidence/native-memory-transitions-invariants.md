# Native memory-transition acceptance invariants

Read-only review of active `kernel/src/mm/vmm.c`, `kernel/src/exec/exec_syscall.c`, `kernel/src/arch/x86_64/interrupts.c` and `kernel/include/vos/vmm.h`, 2026-09-14. No VM execution or production edits by this reviewer. These are proposed acceptance conditions, not passing runtime evidence.

## Concrete source findings

- `mprotect_range` changes only resident PTEs, does not update VMA protections, ignores individual update failures, and skips missing mappings. Its PROT_NONE conversion retains USER without representing denied reads. Lazy pages therefore cannot inherit the requested transition correctly.
- Demand paging checks VMA membership without access type or `vm_prot` and installs present/user/writable/NX mappings unconditionally. Lazy PROT_NONE and read-only requests must be rejected or mapped with their actual permissions before the instruction retries.
- Clone logic marks every present leaf COW, including originally read-only pages. The COW handler grants writable access without checking current VMA protection. Read-only-before-fork and read-only-after-fork writes are essential independent regressions.
- COW and AI_MONITORED both occupy software bit 9. Preserving AI_MASK in `update_flags` preserves COW as well. Their semantics must be disambiguated before claiming permission revocation cannot be bypassed by COW handling.
- `munmap` processes only the VMA containing its initial address and returns success after clamping to that VMA. A range spanning adjacent VMAs is not fully revoked. Middle splitting with no VMA slot mutates mappings before returning ENOMEM; transactional failure behavior needs explicit design.
- Range arithmetic needs overflow checks before alignment/addition in mprotect and munmap. Validate the complete user range and supported protection bits before mutation.

## Native acceptance cases

Use a dedicated gated workload with explicit CPL3, exact syscall results, case/PID/address markers and a live parent witness. Correlate expected failures with actual kernel fault records and wait status 35584. Each surviving witness must respond after the fault; missing cases or unexpected process exits fail the run.

1. **Inherited COW divergence:** parent populates a private page with pattern A before fork. Child confirms A; both roots initially reference the same physical page with writable cleared and COW set. Child writes pattern B and reports its value. Independent mapping evidence then shows different physical pages and correct COW/refcount transition. Parent still reads A, writes C, and child still reads B via a new challenge. Both exit/progress successfully. Checking values alone demonstrates behavioral divergence, not that COW rather than eager copying implemented it.
2. **Read-only enforcement:** populate RW, transition to READ, verify reads still work, then child write must fault with user/write/protection error 7. Repeat with protection applied before fork and after fork so clone and stale-COW permission bypasses are covered. Parent canary remains unchanged after each fault.
3. **Lazy read-only:** mmap READ without touching the page; read yields zero while subsequent write faults. A separate untouched READ mapping must reject a first access that is a write; it must not temporarily grant write permission through demand allocation.
4. **PROT_NONE:** test both untouched and already populated pages, and both read and write attempts in distinct children. Require denied access, unchanged parent witness and denied effective permissions. For nonpresent representations expect user read/write errors 4/6; for supervisor-present representations expect 5/7. Fix the representation contract before implementing the classifier, rather than accepting any error. Restore READ/RW and check preserved populated contents to distinguish protection changes from destructive unmap.
5. **NX:** populate a page with a tiny reviewed return function while RW/NX; direct CPL3 instruction fetch must fault at the exact page with instruction-fetch/user/protection error 21. A controlled RW-to-RX transition must permit the function to return a known constant; simultaneous WRITE+EXEC must fail. Check NX support/enabling explicitly; absence is failure for this gate, not a skip.
6. **Unmap:** populate then unmap; subsequent read/write faults must report nonpresent user errors 4/6 and VMA absence, with no demand-page resurrection. Test untouched mappings, whole removal, front/back trimming, middle split and a range crossing adjacent VMAs. Neighbor pages outside the range retain their patterns. Repeat mapping at the address explicitly before permitting later access. Invalid/overflow ranges must fail without altering valid neighboring mappings.

For all cases, bind the committed source, actual image/kernel/user-test hashes and classifier hash; require unchanged ISO before/after each BIOS/UEFI 1/4-CPU run and strict completion/progress. Preserve failed first runs as evidence when correcting a source defect.

## Concurrency and proof boundaries

Local `invlpg` in permission/COW paths does not establish remote revocation. The visible range-flush implementation advances in 2 MiB increments, which does not flush every independently cached 4 KiB translation; timeout-warning completion is not a successful acknowledgement barrier. COW read/copy/update/refcount operations also need a coherent address-space synchronization argument for concurrent writers.

Four CPUs online does not mean these processes executed on different CPUs or shared one address space concurrently. A future SMP revocation test needs independent CPU-ID evidence, a worker that first warms the target translation on another CPU, explicit synchronization around revocation, per-generation shootdown acknowledgements and no stale access after the completion boundary. Memory must not be freed/reused until all relevant CPUs have acknowledged. The sequential native cases above make no scheduler-fairness, concurrent-refcount, remote-TLB or universal memory-isolation claim.

## Follow-up source review and test authorship

The mathematical reviewer now authors `user/src/test_native_memory.c` and the memory observer/tests; the separate MCP reviewer reviews these. The root authors VMM/demand changes and the MCP peer authors syscall changes. Independent source review of those production deltas found coherent preflight/split ordering for the sequential anonymous 4KiB cases: mprotect plans VMA splits before commitment, preserves populated PROT_NONE data with USER cleared, separates COW bit 52 from AI bit 9, and avoids making originally read-only clone leaves COW. Demand permission checks now consult VMA intent. Multi-VMA unmap processes all overlaps and reserves split metadata/backing references before mutation.

Two further findings were sent to their owners: unsupported mmap sharing/unknown flag modes should fail explicitly, and huge-leaf unmap currently clears a whole huge mapping for a 4KiB request while freeing only its first physical page. These require explicit rejection or a complete implementation; the 4KiB workload does not qualify them. Shared-address-space synchronization and remote TLB completion remain outside the sequential gate.

The implemented workload has 12 cases, full-page parent canaries, exact kernel-address mprotect rejection (-22), and populated PROT_NONE restoration. Its middle-hole case initializes three pages, checks both surviving neighbors in the child before the expected fault, and checks all three parent pages afterward. Six observer test methods pass. NX denial is exercised; successful RX execution is not yet a control in this workload. Positive private writes demonstrate behavior, not physical sharing/copy instrumentation. No actual native-memory runtime result has yet been reviewed here.

## Final workload controls and baseline failure (2026-09-15)

The final workload now includes an allowed-execution control: a fresh RW page receives `mov eax,42; ret`, transitions to RX, returns 42 and is unmapped. The observer requires the corresponding marker. API controls require exact EINVAL for kernel-address mmap/mprotect/munmap, overflowing lengths and W+X. The after-fork read-only case now protects the middle of three initialized pages and verifies both surviving writable neighbors in the child and all three original parent pages. The observer still requires twelve cases, with the new controls mandatory before them.

Independently read the baseline serial at `/private/tmp/vos5-memory-dev-20260914/baseline-bios1/`: it explicitly reports `NATIVE_MEMORY FAIL reason=kernel_mprotect_accepted pid=1` after CPL3 entry. The baseline result is failed with unchanged ISO digest `a693fd22d1430caf80e4c225cfd75c03697d9aa528945eacda4f58cf92041362`. This is an actual observed rejected regression baseline, not a failure inferred from absent new markers.

Read the final syscall empty-range boundary: protection bits/W+X/alignment/user-address bounds are validated before zero-length success; nonzero ranges additionally undergo checked rounding and end validation. Final clean-matrix review is pending completion. Initial BIOS1 serial already contains the mandatory API/RX controls and twelve completed cases, but the complete matrix and artifact identity must finish before qualification.

## Final clean memory gate accepted (2026-09-15)

Independently reclassified all four raw serial logs under `/private/tmp/vos5-memory-clean-20260915/`: BIOS/1 CPU, BIOS/4 CPUs, UEFI/1 CPU and UEFI/4 CPUs each pass all twelve cases, including mandatory API guards, restoration and permitted RX control. This includes forty correlated expected faults across the matrix, successful private-write controls and post-case parent progress. Independently recomputed the actual ISO, kernel ELF, user ELF, classifier and qualification-harness digests; all match the recorded result.

- Source commit: `56ed649889f7200b7ab49504c3f2db6f75e23c75`.
- ISO: `225bdead22328826569c8d1cb6eaffa214cd6f1a2822ff9548e6bd259daf9e1b`.
- Kernel: `13928ca6adc0748bee2c275d9fe0fc1b7e809d954dd579bb796c1d510e8cffaa`.
- User workload: `d25fd8e5c0c47c69fb163074ad27d2016ac6660e16705936cf9596d9c3263f45`.
- Classifier: `c7b575288275c07a1404e30a07d13a04e035a770a7fe7d3b14f6b2f6539eab5b`.
- Qualification harness: `f8ca238cd7375552e37c42a61bd81eacd2a6be8bc436e89d811ff4c8c123aeb5`.

The recorded clean build has 5,385 archived source files, no initial generated outputs, unchanged source inputs, network disabled, a pinned builder image, successful compilation and build-container removal. All four VM observations retain the same ISO digest before and after. Total elapsed time is 115.58 seconds.

Accept the bounded sequential anonymous-4KiB memory-transition gate for these emulator configurations. This approval concerns the root/peer-authored implementation and actual runtime evidence; the reviewer authored the workload/classifier, whose independent security review is separately assigned. It is not a physical-page-sharing proof of COW, a remote TLB/concurrent-address-space proof, a huge-page qualification, or evidence of workload execution on APs. Normal-boot and earlier isolation regression matrices are being qualified separately and are not inferred from this result.
