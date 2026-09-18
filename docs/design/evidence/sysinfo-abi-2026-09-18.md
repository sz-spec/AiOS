# Versioned SYSINFO repair and recurring health failure — 2026-09-18

The new SYSINFO memory-boundary tests and actual musl errno tests pass in BIOS and UEFI. **The full suite fails in both runs: 56/57 programs exit zero because the unchanged 80,000 messages/s health gate fails.** The previously qualified, byte-identical ISO also fails today. The performance cause remains unresolved; this is not release qualification.

[Failure report, Hebrew PDF](../../reports/vos-performance-failure-2026-09-18.pdf), [machine-readable evidence](sysinfo-abi-2026-09-18/summary.json), [mathematical review](sysinfo-abi-math-2026-09-18.md), and [security review](sysinfo-abi-security-2026-09-18.md).

## Fixed contract

The kernel formerly copied a 40-byte private telemetry object into fourteen native declarations of 32 bytes; only the VMM soak declaration included the extra fields. It also used Linux syscall 99 for a structure incompatible with musl, and copied directly after a range helper that ignored length.

- Native telemetry now uses syscall **483(pointer, size, version)**. The shared UAPI fixes version 1 at exactly 40 bytes, with compile-time assertions for all seven offsets. Unknown sizes or versions return `-EINVAL` before any output write. All fifteen native declarations now use the common header, including huge-page counters.
- The kernel zero-initializes its snapshot and uses the existing fault-recovering `copy_to_user`. Failure returns `-EFAULT`; a prefix may already have been copied. This is not atomic copyout or a globally atomic cross-subsystem snapshot.
- Syscall **99 returns `-ENOSYS` without writing**. Old native binaries must be rebuilt; Linux sysinfo remains unsupported. The kernel does not infer an ABI from buffer contents or unspecified registers.
- Native callers check failed telemetry before reading statistics; existing SHM/child cleanup remains reachable. The sustained test now uses the common syscall wrappers, replacing its private wrappers while adopting the shared telemetry interface.
- Musl `sysconf` memory queries and `getloadavg` propagate failure without consuming uninitialized output. The port had excluded the real wrapper and defined `__lsysinfo` as a data object; that object and a conflicting weak stub were removed, and `src/linux/sysinfo.c` is explicitly built. ELF inspection confirms `sysinfo` and `__lsysinfo` are functions at the same address. Distinct auxiliary-vector `__sysinfo` storage remains.

## Verification

A clean build used 6,392 frozen files with no previous project outputs. The pinned offline builder was `sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6`, mounted at `/vos3`, with all capabilities dropped, no-new-privileges, `SOURCE_DATE_EPOCH=1700000000` and four build jobs:

```sh
make native -j4 BENCH_MODE=1 HEADLESS_AUDIT=1 \
  BUILD_DIR=build/native-full-final INSTALLER_ISO=../dist/final-full.iso
```

All frozen input hashes remained unchanged and matched the workspace. Reconstructing the base commit with the recorded patch and additions reproduced all 6,392 manifest hashes. The final frozen tree passed **110 host tests**. New host tests compile actual kernel handler/dispatcher and musl implementations with controlled copy/syscall boundaries; they are not substitutes for guest protection tests.

Both guest runs passed `[PASS] sysinfo_versioned_abi` and `[PASS] test_env: sysinfo_errno_no_write`, and their containing programs exited zero. Cases cover surrounding canaries; size 0/32/39/41/UINT64_MAX; unknown version; syscall 99 no-write; NULL, kernel, canonical-hole and wrapping pointers; untouched lazy mappings; writable, read-only, PROT_NONE and unmapped second-page crossings; cleanup and a final valid call. Linked musl checks cover public sysinfo, both memory sysconf queries, their legacy helpers and getloadavg, requiring `-1/ENOSYS` and untouched output.

Existing compiler/linker and clock-skew warnings remain in the logs. The first compile attempt failed because `test_sustained` still had private syscall wrappers and lacked the shared header; it was corrected before the clean final build. That failure and the intermediate validation are retained.

## Performance remains open

| Sample | Zero exits | Pushed = consumed | Guest ms | Messages/s |
|---|---:|---:|---:|---:|
| Prior ISO, BIOS control today | 56/57 | 41,556 | 1,010 | 41,144 |
| ABI intermediate, BIOS | 56/57 | 41,465 | 1,010 | 41,054 |
| Final sources, BIOS | 56/57 | 41,931 | 1,000 | 41,931 |
| Final sources, UEFI | 56/57 | 43,279 | 1,010 | 42,850 |

The old ISO is exactly `a473dfabbbc0bb10973327a256febd809d1399cd8efc6766d25086987827ba05`, which passed four complete runs on September 17 at 86,575–90,635 messages/s. Today's old/new comparison does not establish a new telemetry-induced performance regression, nor does it identify a complete cause. Both the historical passing runs and today's failed runs remain evidence.

The health threshold, 1,000 ms workload, 1,024-slot ring and ordered 57-program workload remain unchanged. Its ELF changed for the explicit ABI migration and error checks; it is not claimed byte-identical to the historical health ELF. Independent disassembly found equivalent timed producer instructions/register allocation after address normalization, unchanged ring/global addresses, and the previous scheduler/KPTI optimizations intact. Earlier syscall/context-switch metrics also slowed before the new SYSINFO tests. TSC values here are emulated measurements, not independently calibrated physical CPU cycles.

The empty consumer spins while the producer yields only on a full ring. Crossing a 10 ms scheduling boundary can qualitatively explain large rate changes; it is not a demonstrated sole cause. Runs were sequential, without concurrent project builds, test suites or PDF rendering. Other host activity/core/power allocation was not controlled. Future performance closure needs interleaved control/candidate repeats and separately marked instrumentation of syscall and scheduling costs, followed by the original uninstrumented gate.

Final ISO SHA-256: `90cc28eeef42290b10fa0a4f4bc443bed40dae54358bcb7a9127fbe499378a64`.
Local testing artifact: `dist/qualification-sysinfo-20260918/vos5-sysinfo-validation.iso`.

## Evidence and remaining scope

The packet contains source manifests, the base-commit patch, new source additions, build/test logs, raw serials, classifiers' results and commands, firmware hashes, linked symbols, independent review and `SHA256SUMS`. The final input tree can be reconstructed from commit `cfd23f6a8595b13da005c59f1efacb6744e53832`, `final/dirty.patch`, and `frozen-additions/`; the manifest defines the exact file set. The drivers contain local absolute paths and require adaptation elsewhere. This final ISO has one clean build; the prior ISO's byte-reproducibility result is not a new reproducibility claim for it.

Scope is QEMU TCG x86-64, one vCPU, 3,072 MiB, BIOS and UEFI. Other POSIX handlers still use unsafe direct user-memory copies; partial KPTI, cancellation, remote-TLB/shared-AS concurrency, physical hardware and full SMP remain outside this repair. No claim of complete isolation or production readiness is made.
