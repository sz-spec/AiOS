# Independent SYSINFO ABI review — 2026-09-18

Reviewer: math_build_review. Reviewed scope: versioned telemetry wire layout, kernel validation/copyout and dispatch; source frozen at `/private/tmp/vos-sysinfo-20260918/source`. No production files changed by this reviewer.

## Defect and migration

The legacy one-pointer syscall 99 could not distinguish a 32-byte native buffer from a 40-byte extended native buffer or a Linux/musl sysinfo buffer. Pointer validity establishes accessible address ranges, not the caller's allocated object capacity or intended ABI. Stack contents and unspecified argument registers cannot safely supply the missing contract. Appending eight bytes without an explicit size therefore was not backward-compatible.

The replacement uses private syscall 483 with pointer, size and version. Linux syscall 99 now returns ENOSYS without copyout; this is explicit unsupported functionality, not Linux sysinfo implementation. All useful native counters, including huge-page pool fields, remain in v1.

## Wire and arithmetic invariants

The actual shared UAPI header is compiled by the host regression. It fixes size at 40 bytes and checks every field offset: free_pages 0, total_pages 8, nr_tasks 16, nr_zombies 20, uptime_ms 24, hugepage_total 32, hugepage_used 36. Fixed-width fields occupy the full layout without uninitialized padding; the kernel also zero-initializes its local value.

The handler compares full uint64_t size/version arguments against sizeof(vos3_sysinfo_t) and version 1 before collection or copyout. It does not truncate high bits, multiply an attacker-controlled count, or infer a compatible prefix. Only exactly 40/version1 is accepted. Dispatch passes rdi/rsi/rdx unchanged; the syscall enum derives its number from the shared UAPI definition, and both legacy/new entries are registered.

Invalid size/version yields EINVAL without writes. Valid metadata with an invalid destination yields EFAULT through fault-safe copy_to_user. The latter may already have copied a prefix: no all-or-nothing guarantee is claimed. Native page-boundary, RO, unmapped and PROT_NONE tests are required to verify actual fault behavior. A host copy mock does not establish it.

Counters are collected through separate subsystem calls. This is not one globally atomic snapshot: allocations/tasks/time may change between reads. Per-field representation and subsystem locking do not imply cross-field transactional consistency.

## Validation and limits

The independent actual-C handler plus full POSIX dispatcher harness passed; log `/private/tmp/vos-sysinfo-abi-review-20260918.log`. It checks all values/offsets, exact copy extent and surrounding canaries, invalid sizes including legacy32 and UINT64_MAX, invalid versions, failed/NULL/short copy destinations and legacy99 no-write behavior. Mocked copy failures in this harness leave bytes unchanged; that is a test scenario, not a stronger ABI promise.

The additional real-musl-wrapper fixture required correction of a data-symbol inclusion problem. That correction is not native runtime evidence. At report time clean native build/full-suite qualification was pending. No claim is made that this ABI repair alone fixes the separate 80K health-performance failure, or that telemetry now supplies full Linux compatibility.

## Coordinator validation update

Final source snapshot: `/private/tmp/vos-sysinfo-qualified-20260918/source`. The kernel/UAPI/musl contract reviewed above is unchanged; the final caller audit also rejects stale telemetry in throughput and soak/sustained tests. The final tree passed 110 host tests. BIOS and UEFI both passed the new native SYSINFO and actual musl tests; the full suite remained FAIL at 56/57 due to the health performance threshold (41,931 and 42,850 messages/s). An unchanged old ISO control also failed at 41,144. These are coordinator observations, not a retroactive independent runtime claim by the author. See [final results and raw evidence](sysinfo-abi-2026-09-18.md).
