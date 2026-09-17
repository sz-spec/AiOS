# Native health performance repair — 2026-09-17

The original health gate now passes in two BIOS and two UEFI complete-suite runs of the same final ISO. Each run executes all 57 programs with zero exit status. This is bounded single-vCPU QEMU TCG qualification, not a physical-hardware or complete-SMP performance claim.

[Machine-readable results](health-performance-2026-09-17/summary.json) and [independent runtime audit](health-performance-2026-09-17/final/runtime-review.json).

## Changes and preserved checks

- `vos3_sched_process_deferred()` observes the pending bit before taking the active guard. An empty observation does not consume concurrent publication. The nonempty drain, exclusion guard, deferred work, signals and reclamation remain.
- `vos3_kpti_sync_root()` partitions the existing mapping policy into contiguous loops. It still assigns every one of the 512 entries, reads the same 258 source entries, propagates revocations and clears forbidden slots. Root binding, locks and CR3/TLB transitions remain.
- The health source and ELF, one-second duration, 1024-slot ring and 80,000 messages/s threshold are unchanged. No MMIO permission or isolation check was relaxed.

## Results

| Run | Programs exiting zero | Messages | Elapsed guest ms | Messages/s | Full suite |
|---|---:|---:|---:|---:|---|
| Rejected compare-before-store | 56/57 | 34,970 | 1000 | 34,970 | FAIL |
| Instrumented current; diagnostic only | 57/57 | 86,577 | 1000 | 86,577 | PASS |
| Instrumented old; diagnostic only | 57/57 | 90,611 | 1010 | 89,713 | PASS |
| Unmodified current ISO replay | 56/57 | 74,227 | 1000 | 74,227 | FAIL |
| Pending early load only | 57/57 | 91,225 | 1000 | 91,225 | PASS |
| Final bios-1 | 57/57 | 86,575 | 1000 | 86,575 | PASS |
| Final uefi-1 | 57/57 | 90,635 | 1000 | 90,635 | PASS |
| Final bios-2 | 57/57 | 89,345 | 1010 | 88,460 | PASS |
| Final uefi-2 | 57/57 | 87,786 | 1000 | 87,786 | PASS |

The instrumented programs are diagnostic evidence only. The rejected compare-before-store patch and its failing run are retained. The pending-only sample establishes a passing narrow candidate; one sample does not quantify its effect independently of host/layout variation. The final repeated runs qualify the combined production changes.

The same unmodified ISO previously measured 37,923 messages/s and measured 74,227 in the adjacent replay. Earlier GETPID/context-switch metrics also varied, including before the diagnostic health code executed. Consequently, prior attribution solely to Frontier was not justified, and these data do not establish one complete cause for the historical 98K-to-38K gap. The identified fixed syscall costs are reduced; observed throughput also depends on host execution and guest scheduling phase.

A 1024-slot ring can cross a scheduling boundary when producer fill time approaches the 10ms tick. Filling before the tick permits an early yield; otherwise the empty consumer can spend the next service interval spinning. This explains sensitivity qualitatively, not as a calibrated causal proof. No emulated TSC value is presented as physical CPU cycles or independently calibrated microseconds.

## Validation and reproducibility

- 107 host regression tests passed (`final/host-tests.log`). The new actual-source drain test exercises deterministic publication/reentry cases in both UP and `NATIVE_SMP_TEST` branches with UBSan.
- 27 actual-source KPTI tests passed (`final/kpti-tests.log`), including randomized roots, mapping revocation and rejected storage aliasing.
- Pinned-builder assembly confirms the empty pending path has no locked read-modify-write and the nonempty path retains both exchanges. Root sync retains 512 writes/258 reads with 384 loop-branch executions; see the independent assembly review.
- Two fresh manifest-only builds produced identical ISO, kernel and six bootloader artifacts. All 6,321 frozen source files remained unchanged in each build. The second input tree had normalized timestamps; neither build reused project outputs.
- Reconstructing the frozen tree from the base commit, saved patch and additions reproduced all 6,321 manifest hashes (`final/reconstruction-review.json`).
- Compiler warnings remain recorded, including existing upstream/vendor diagnostics and clock-skew warnings. Passing checks do not mean warning-free builds.

ISO SHA-256: `a473dfabbbc0bb10973327a256febd809d1399cd8efc6766d25086987827ba05`
Kernel SHA-256: `cc91274a6a07cb7c05031d5a503ca010bc3d5fbcf9550b938c169bf4b112f30b`
Original/final health ELF SHA-256: `28cac8eb9131f7f9c9bcac756542f5eb3d6af79d51de51a200085b239aa7b56a`

Immutable builder: `sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6`; `SOURCE_DATE_EPOCH=1700000000`; container path `/vos3`; four build jobs; offline Docker with dropped capabilities and no-new-privileges. Both builds use:

```sh
make native -j4 BENCH_MODE=1 HEADLESS_AUDIT=1 BUILD_DIR=build/native-full-final INSTALLER_ISO=../dist/final-full.iso
```

## Evidence and review

The evidence directory contains manifests, the source patch, frozen untracked additions, build logs, raw serial logs, full workload classifiers, exact VM commands, firmware hashes, diagnostic/repeat drivers, static assembly inspection and `SHA256SUMS`. Reconstruct the final frozen tree from the recorded base commit, `final/dirty.patch`, and `frozen-additions/`; the manifest is the authoritative file set. Review documents were completed after freezing; the compiled native sources match the workspace. Drivers retain absolute experiment paths as provenance and require path adaptation on another host.

The BIOS observer uses 100ms host-monotonic milestone polling; these timestamps are not per-byte arrival times. VM runs were sequential. Final qualification runs had no concurrent project builds or test suites. Exploratory runs were not a fully controlled host A/B study; other host activity and CPU/core/power allocation were not controlled or certified. Failed samples have not been discarded.

[Research limited to 2026-07-19 through 2026-09-17](health-performance-research-2026-09-17.md), [mathematical review](health-performance-invariants-2026-09-17.md), and [security review](health-performance-security-2026-09-17.md).

## Remaining limits

The existing partial KPTI boundary, remote TLB/cancellation safety, physical PC compatibility and Secure Boot remain outside this qualification. The separate SYSINFO ABI defect (kernel copies 40 bytes into legacy 32-byte caller structures) existed in both compared baselines; inspected health frames placed the excess bytes in padding, which does not make the ABI safe. It is documented for follow-up, not presented as repaired or as the proven throughput cause. Unsupported NPU/MMIO hardware remains unavailable and denied. This ISO is a benchmark qualification artifact, not a production installation release.
