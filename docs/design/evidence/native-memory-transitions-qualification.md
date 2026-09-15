# Native memory transitions — 2026-09-15

**PASS within the sequential, anonymous 4 KiB scope: 48/48 cases across
BIOS/UEFI × 1/4 CPUs.** The previous four-case isolation suite also passed
16/16 cases on the changed kernel, and normal boot passed all four configurations.
The changes fix actual permission-handling defects; they do not establish
concurrent shared-address-space or remote TLB safety.

## Defects and changes

An isolated baseline VM using the previous memory implementation accepted
`mprotect` against a kernel address from CPL3. Its test stopped with
`kernel_mprotect_accepted`, preserving the failed result. This demonstrates
the missing syscall boundary check; it is not a demonstration of arbitrary
kernel code execution. The corrected syscall rejects the request with EINVAL
before touching page tables. Range rounding, overflow, alignment, unsupported
protection bits and W+X requests are checked for the relevant mapping calls.

Demand paging now consults VMA permissions and the hardware read/write/fetch
operation instead of mapping every accepted fault writable and non-executable.
`mprotect` updates VMA permissions as well as resident PTEs. It preflights page
coverage, VMA split capacity and duplicate backing descriptors before committing
changes. Resident PROT_NONE retains its frame with USER cleared, allowing data
to survive restoration; a lazy PROT_NONE page remains unallocated on denial.

Fork no longer marks genuinely read-only pages as writable-on-demand COW.
Restoring write permission on a shared frame keeps it read-only/COW until a
private copy is obtained. Removing write permission removes that COW route,
and the fault handler additionally checks current VMA write permission.
The COW marker moved from bit9, which conflicted with AI_MONITORED, to software
bit52. The IA-32e leaf format identifies bits58:52 as ignored in the
[Intel SDM, Volume 3A](https://cdrdv2-public.intel.com/812386/253668-sdm-vol-3a.pdf).
The current address mask excludes this bit; the native COW runs exercised it.

`munmap` now covers all intersecting VMAs and reserves middle-split metadata
and file references before destructive changes. A preflight rejects unsupported
huge leaves instead of clearing an entire huge mapping for a 4 KiB request.
This is rejection of unsupported huge-page operations, not huge-page splitting.

## Reproduce and identify

With the prepared compiler image, Docker, QEMU and firmware installed:

```sh
python3 scripts/native_clean_qualification.py --memory --output /tmp/vos5-memory-run --seconds 40
```

The output directory must be new. The recorded run archived committed source
`56ed649889f7200b7ab49504c3f2db6f75e23c75`, verified absent prior project outputs,
used a pinned prebuilt compiler environment without network access, and ran one
`make native -j4` command with the isolation and memory diagnostic flags. All
5,385 original source files remained unchanged. Build exit0 and build-container
removal were verified; elapsed time was 115.58 seconds.

| Artifact | SHA-256 |
| --- | --- |
| Memory-test ISO (12,947,456 bytes) | `225bdead22328826569c8d1cb6eaffa214cd6f1a2822ff9548e6bd259daf9e1b` |
| Memory-test kernel | `13928ca6adc0748bee2c275d9fe0fc1b7e809d954dd579bb796c1d510e8cffaa` |
| User test ELF | `d25fd8e5c0c47c69fb163074ad27d2016ac6660e16705936cf9596d9c3263f45` |
| Updated normal ISO | `5816fb05d111a2c0fda956dfcc9a471342fd26bc913a0f0876ca1df1f36c8538` |
| Updated normal kernel | `9d0bfc6065797c5c6f2a2d66155719f356ac653c285a9a806832a75ac7b8457f` |

Local copies: `dist/vos5-memory-qualified.iso` directly launches diagnostics;
`dist/vos5-current-qualified.iso` is the normal image with the corrected kernel.
Neither is a final OS release. ISO binaries remain untracked; code and evidence
are committed. The clean workspace is `/private/tmp/vos5-memory-clean-20260915`.

## Runtime oracle and results

Each observation used QEMU10.2.2, q35/TCG, qemu64, 1,024 MiB RAM, no network or
host disk, and a private UEFI variables file where applicable. Every matrix
run used the same identified ISO and verified its pre/post hash.

The native coordinator and children check CPL3. A full-page parent witness
remains intact after every child, and affected parent pages are checked as well.
Successful COW children mutate their private page and exit0. Negative cases
require the exact child PID, address and hardware page-fault bits, followed by
wait status `139 << 8` under the current vOS ABI and parent progress. Setup
failures, missing functionality, kernel-uaccess faults, hangs and unexpected
faults are failures rather than successful denials.

| Case | Expected observation |
| --- | --- |
| COW private write | child data changes; parent page unchanged |
| COW followed by mprotect(RW) | private write remains isolated |
| Read-only before fork | child write faults, error7 |
| Read-only middle page after fork | child write faults, error7; both neighboring pages stay writable |
| Lazy read-only write | denied before allocation, error6 |
| Lazy PROT_NONE read | denied before allocation, error4 |
| Populated PROT_NONE read | supervisor-protection fault, error5 |
| NX execution | instruction-fetch protection fault, error21 |
| Lazy RW changed to read-only | child write denied, error6 |
| Single-page unmap | subsequent read faults, error4 |
| Middle-page unmap | hole faults, error4; child and parent survivors retain data |
| Unmap across three VMAs | final formerly mapped page faults, error4 |

All twelve cases passed on BIOS1, BIOS4, UEFI1 and UEFI4: eight positive COW
observations and forty expected faults. Additional mandatory controls verify
exact EINVAL for kernel/overflow/W+X requests, PROT_NONE→RW data restoration,
and successful RW→RX execution returning42. Final parent progress is required.

The 22 host classifier test methods passed. A final host-only hardening also
rejects failures from the shared kernel-canary diagnostic hook. Its six memory
test methods passed, and all four original serial logs passed reclassification.
The original aggregate retains its original classifier hash; the later
reclassification has its own hash. This was not an additional build or VM run.

## Regressions and evidence

Normal boot and the preceding isolation suite each passed BIOS/UEFI ×1/4 CPUs.
Their kernel/user source files were compared against the clean source manifest.
These builds used separate output directories but reused the development
workspace's bootloader, so they are regression runs, not additional clean-source
qualifications. The normal ELF lacks diagnostic symbols and target environment
strings; both diagnostic user programs and embedding entries are absent.

- [Clean aggregate](native-memory-2026-09-15/result.json),
  [source manifest](native-memory-2026-09-15/source-manifest.json),
  [build output](native-memory-2026-09-15/build.txt),
  [tool versions](native-memory-2026-09-15/tool-versions.txt)
- Raw memory logs: [BIOS1](native-memory-2026-09-15/bios1/serial.txt),
  [BIOS4](native-memory-2026-09-15/bios4/serial.txt),
  [UEFI1](native-memory-2026-09-15/uefi1/serial.txt),
  [UEFI4](native-memory-2026-09-15/uefi4/serial.txt)
- [Baseline failure](native-memory-2026-09-15/baseline-bios1/result.json),
  [baseline serial](native-memory-2026-09-15/baseline-bios1/serial.txt),
  [baseline source identities](native-memory-2026-09-15/baseline-source-hashes.json)
- [Regression aggregate](native-memory-2026-09-15/regressions/result.json),
  [source/exclusion checks](native-memory-2026-09-15/regressions/source-and-exclusion.json),
  [recorded runner](native-memory-2026-09-15/regressions/runner.py)
- [Final reclassification](native-memory-2026-09-15/final-reclassification.json),
  [final harness hashes](native-memory-2026-09-15/final-harness-manifest.json),
  [evidence hashes](native-memory-2026-09-15/evidence-sha256.json)
- [Security review](native-memory-transitions-security.md) and
  [invariant review](native-memory-transitions-invariants.md)
- [Secret-scan triage](native-memory-2026-09-15/secret-scan-review.json): 133
  source-manifest digest findings independently recomputed as false positives;
  no unresolved findings in this scan and no new suppressions.

## Explicit compatibility and qualification limits

MAP_SHARED and unsupported mmap flags now fail with EINVAL instead of being
silently treated as private mappings. Fixed-address overlap replacement returns
EEXIST until transactional replacement exists; nonfixed collision searching is
also incomplete. Huge-page protection/unmap requests fail rather than splitting
huge leaves. Complete file-backed semantics and allocation-failure injection
are not qualified by this anonymous-page matrix. Existing compiler/linker and
directory clock-skew warnings remain visible in the full logs.

Concurrent shared-VM operations remain open: CLONE_VM does not maintain reliable
address-space ownership counts, validation and mutation are not one synchronized
transaction across all paths, COW changes and unmaps use local invalidation,
and the old range-flush path is not a qualified acknowledged 4 KiB remote shootdown.
Non-VMA resident pages do not gain a persistent protection descriptor in this
change; their future eviction/demand-remapping behavior remains unqualified.

Four online CPUs do not demonstrate AP user execution. The
[scheduler investigation](native-ap-workload-gap.md) identifies the missing
guaranteed wake/reschedule path. The next gate must first prove actual user
workload progress on multiple CPUs, then qualify concurrent revocation with
acknowledged TLB invalidation and safe page lifetime handling. Physical hardware,
DMA/speculative isolation and bit-for-bit ISO reproducibility remain open.
