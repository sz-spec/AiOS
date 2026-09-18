# Security and validator council — existing evidence only

Reviewer: `/root/mcp_upgrade`, 2026-09-18. No tests, builds or VM runs. Read-only parsing of eight preserved canonical serial logs; only this report was written. Line numbers below count LF-delimited original records after removing CR/ANSI controls, with no dropped records. Raw-byte hashes bind the inputs. HEAD observed: `0324521a2494b25c29a01b055ff8b38029819fb9`; local commit presence is not remote or release approval. Experiment times are planning estimates, not work already executed.

## Validator

[V1] תשובה: All four September18 runs have exactly one nonzero top-level program, `test_health_check`, exit1. No other top-level program failure was found. ראיה: eight-log inventory below and `sysinfo-abi-2026-09-18/runs/bios/result.json:2`. ביטחון: גבוה.

[V2] תשובה: The failing classifier condition is the nonzero health exit, not missing, duplicate or reordered starts/exits. All eight have the identical ordered57 program list. September18 result files additionally reject explicit [FAIL] and nonzero FAIL summaries. ראיה: `scripts/native_bench.py:43–61` and preserved result failure arrays. ביטחון: גבוה.

[V3] תשובה: Independently scanned all eight complete raw logs, attributing [FAIL] to enclosing start/exit records: zero [FAIL]-with-zero-exit programs. September17 logs contain no [FAIL]; September18 contain one each, all health exit1. Historical57/57 is not based only on exits: classifier also rejects PANIC/[FAIL]/[MISS], malformed ordering, incomplete suite and filters. ראיה: `scripts/native_bench.py:39–68`, raw inventory. ביטחון: גבוה for these exact logs; absence of diagnostics is not proof every assertion is implemented.

[V4] תשובה: All eight start sequences match; immediate predecessor is `test_model_load`. The health point-in-time telemetry reports four tasks/zero zombies, not continuous identities during the timed window. ראיה: `user/src/init.c:387–388`, health sections in inventory. ביטחון: גבוה order, בינוני runtime population. Unknown timed-window identities -> E6/E7, estimated30–60min instrumentation plus five runs/config at observed suite duration; no new run here.

[V5] תשובה: NPU hardware is unavailable, while denial assertions may pass and program exits0. That does not count as NPU hardware coverage. Both test_npu_direct and Frontier print UNAVAILABLE; no top-level program is skipped by the full-suite validator. ראיה: `user/src/test_npu_direct.c:36`, `user/src/bench_2026_frontier.c:801,965`, raw markers below. ביטחון: גבוה. CAP classification is a recommendation, not an assigned issue ID.

[V6] תשובה: All four September17 raw logs are retained and independently hashed below. September18 control uses the prior ISO identity recorded in its result; raw evidence remains separate from source changes. ראיה: inventory SHA-256 and result files. ביטחון: גבוה.

## ABI and safety

[A1] תשובה: SYSINFO483 repair is locally committed at0324521; report records110 host checks and successful new BIOS/UEFI ABI tests, but full suites remain56/57. This is bounded verification, not release approval or confirmed remote merge. ראיה: `sysinfo-abi-2026-09-18.md:3,26–39`; local HEAD above. ביטחון: גבוה local status. Release/remote approval -> להחלטת CTO; no experiment substitutes for authorization.

[A2] תשובה: Fifteen native source declarations now share40-byte v1 and call483(pointer,size,version); old binaries calling99 receive ENOSYS without writes and require rebuild. Musl public sysinfo uses the real wrapper; sysconf memory queries/get_phys_pages/get_avphys_pages/getloadavg propagate failure instead of reading uninitialized output. ראיה: `kernel/include/uapi/vos_sysinfo.h:6`, `user/include/vos_sysinfo.h:8`, `kernel/src/fs/posix_syscall.c:351–358`, `user/musl/src/conf/sysconf.c:215`, `sysinfo-abi-2026-09-18/final/musl-symbols.txt`. ביטחון: גבוה bounded compatibility; Linux sysinfo is unsupported.

[A3] תשובה: Size/version rejection writes nothing. EFAULT may leave a copied prefix; no all-or-nothing guarantee. Native page-crossing tests permit that prefix and assert protected/outside bytes unchanged. ראיה: `kernel/include/uapi/vos_sysinfo.h:6–9`, `kernel/src/fs/posix_syscall.c:307–318`, `user/src/test_posix_core.c` test_sysinfo_abi. ביטחון: גבוה. Concurrent unmap race qualification remains unknown -> separate safety experiment with coordinated mutation/fault ownership, estimated1–2days; not a performance E1–E8 result.

[A4] תשובה: No full analogous-syscall audit completed. Concrete adjacent raw copies remain in getrusage/getrlimit/setrlimit/getitimer/setitimer; posix_access_ok ignores len. ראיה: `kernel/src/fs/posix_syscall.c:114–120,171,188,202,248,254,297`. ביטחון: גבוה finding, no broad safety certification. Extend exact-size/copyout adversarial tests perhandler as separate safety work, estimated1–2days, prioritized by CTO.

[A5] תשובה: Existing host tests compile wrappers and existing native test checks behavior/errno/canaries. The stored ELF symbol evidence records the same function address, but no durable test was found that explicitly fails merely when public sysinfo and __lsysinfo addresses differ. Distinct correct forwarding functions would not necessarily be a bug. ראיה: `scripts/test_musl_sysinfo_failure.py:14–27`, `user/src/test_env_musl.c` test_sysinfo_unavailable, stored musl-symbols.txt. ביטחון: גבוה currenttestscope. If alias identity is a required build invariant, add automated readelf type/address assertion, estimated30min; behavior remains independently required.

[A6] תשובה: This repair qualifies one-vCPU x86 QEMU ABI behavior only. Shared-AS copyout TOCTOU, remote stop/revocation, cancellation, hardware and fullSMP remain open; four-CPU results from other diagnostic images do not extend this gate. ראיה: `sysinfo-abi-2026-09-18.md:54`, `sysinfo-abi-security-2026-09-18.md`. ביטחון: גבוה scope. Required hardware/SMP mutation matrix and stop-ack tests -> CTO-defined separate gate, executiontime unknown until hardware/coverage is specified.

## Governance

[G1] תשובה: Coordinator owns present diagnosis; no repository-backed named issue owner with committed due date was established. ראיה: current checkpoint reports identify `/root` as coordinator, not an organizational SLA. ביטחון: גבוה distinction. Named accountable owner/due date -> להחלטת CTO, not inventable from tests.

[G2] תשובה: Existing canonical acceptance retains80K,1000ms,1024slots and full57programs. New council instruction requires five repetitions/config for conclusions; it does not prove a formally approved number of consecutive release passes. ראיה: `questions.md` finalparagraph; `sysinfo-abi-2026-09-18.md:43–45`. ביטחון: גבוה. Exact closure rule/host envelope -> להחלטת CTO before E2; estimated five fullruns perfirmware at measured duration, plus host capture.

[G3] תשובה: Host/TCG sensitivity is an open hypothesis. Icount is diagnostic, not replacement for the canonical wall/guest-time gate; dedicated hardware may require a new declared product gate. ראיה: `questions.md:E3` and finalparagraph; historical sameISO pass/fail. ביטחון: גבוה limitation. E2/E3 five repetitions/config, time proportional to fullsuite runtime; authority to change product test -> להחלטת CTO. No security/threshold relaxation recommended.

[G4] תשובה: Base+patch+preserved additions binds dirty frozen build inputs while reviewers finish work; final local0324521 records source after qualification. No evidence every ISO is built directly from a unique committed/tagged tree. ראיה: `sysinfo-abi-2026-09-18.md:52`, final/source-manifest.json and final/reconstruction-review.json. ביטחון: גבוה. One commit/tag perISO and release-signing policy -> להחלטת CTO; reconstruction is evidence, not a signature.

[G5] תשובה: A stable REL/CAP issue-ID to J0–J12 journey mapping was not established by the reviewed source/report evidence. Existing council product-gaps records installation/Windows/hardware capability gaps, not an authoritative issue register. Recommend health repeatability as REL and unavailableNPU/Linuxsysinfo/APPLOAD as CAP, but these are proposed classifications only. ראיה: `expert-council-product-gaps-2026-09-17.md:9–20`, `sysinfo-abi-2026-09-18.md:14,54`. ביטחון: גבוה lackofverifiedmapping, נמוך anyinferredjourney. IDs/journeys/accountableowners -> להחלטת CTO; no invented mapping.

[G6] תשובה: Fullsuite performance/release gate is blocked; SYSINFO bounded repair, source review and separately labeled research can continue. Cancellation/fullisolation/hardware remain separate blockers, not waived by ABI success. During performance runs all agents must idle, including reportwriting. ראיה: `sysinfo-abi-2026-09-18.md:3,54`, `questions.md` finalparagraph. ביטחון: גבוה. Any release exception -> explicit CTO decision; none recorded here.

## Independent eight-log inventory

These are existing-log parses, not new test runs. Paths below are repository-relative; hashes cover original bytes including CR/NUL. Each record has57ordered starts/exits. No zero-exit [FAIL] was observed.

### docs/design/evidence/health-performance-2026-09-17/runs/bios-1

- Raw SHA-256: `bd83bf4db683e63a9b2f5d817b3226a1cfdadf923245f188db0d6e96c966e0ec`.
- Zero exits: 57/57; preserved classifier passed=True.
- Health exit anchor: 11028; predecessor: test_model_load.
- [FAIL] records: none.
- UNAVAILABLE markers: test_npu_direct at line 10006, bench_2026_frontier at line 10157.

### docs/design/evidence/health-performance-2026-09-17/runs/uefi-1

- Raw SHA-256: `4f38493e5d862685288ecc38ae43b18ac226715c07767f6617d6ee65e4a81319`.
- Zero exits: 57/57; preserved classifier passed=True.
- Health exit anchor: 11107; predecessor: test_model_load.
- [FAIL] records: none.
- UNAVAILABLE markers: test_npu_direct at line 10082, bench_2026_frontier at line 10233.

### docs/design/evidence/health-performance-2026-09-17/runs/bios-2

- Raw SHA-256: `68f08d28ce0ed4e4ed1dabf80d5871cff985a4a9166bd4d51db60c62afe93f5f`.
- Zero exits: 57/57; preserved classifier passed=True.
- Health exit anchor: 10972; predecessor: test_model_load.
- [FAIL] records: none.
- UNAVAILABLE markers: test_npu_direct at line 9947, bench_2026_frontier at line 10098.

### docs/design/evidence/health-performance-2026-09-17/runs/uefi-2

- Raw SHA-256: `bdd0c358c28728f89a07118c49f2ec72265d5dea388b3286f9329d60e7125e79`.
- Zero exits: 57/57; preserved classifier passed=True.
- Health exit anchor: 11097; predecessor: test_model_load.
- [FAIL] records: none.
- UNAVAILABLE markers: test_npu_direct at line 10072, bench_2026_frontier at line 10223.

### docs/design/evidence/sysinfo-abi-2026-09-18/runs/old-iso-control

- Raw SHA-256: `a6aa740bb7b6887e438d51410ec6206f7df39d3b511a9beb13fcbc094bb9839e`.
- Zero exits: 56/57; preserved classifier passed=False.
- Health exit anchor: 9621; predecessor: test_model_load.
- [FAIL] records: line 9614, test_health_check: `[FAIL] spsc_responsive: 41144 msgs/sec (need >=80K)`.
- UNAVAILABLE markers: test_npu_direct at line 8597, bench_2026_frontier at line 8748.

### docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/before-caller-review/bios

- Raw SHA-256: `6ef43b08e61d630333550ebe4e22d056fdde6d4f7cf2c20bb68b6cee5e7d96b1`.
- Zero exits: 56/57; preserved classifier passed=False.
- Health exit anchor: 9636; predecessor: test_model_load.
- [FAIL] records: line 9629, test_health_check: `[FAIL] spsc_responsive: 41054 msgs/sec (need >=80K)`.
- UNAVAILABLE markers: test_npu_direct at line 8612, bench_2026_frontier at line 8763.

### docs/design/evidence/sysinfo-abi-2026-09-18/runs/bios

- Raw SHA-256: `803bb68c6759fc3002e121516891e4e7a381216ea18e86d7d28a025387da7cf1`.
- Zero exits: 56/57; preserved classifier passed=False.
- Health exit anchor: 9636; predecessor: test_model_load.
- [FAIL] records: line 9629, test_health_check: `[FAIL] spsc_responsive: 41931 msgs/sec (need >=80K)`.
- UNAVAILABLE markers: test_npu_direct at line 8612, bench_2026_frontier at line 8763.

### docs/design/evidence/sysinfo-abi-2026-09-18/runs/uefi

- Raw SHA-256: `4ebfa48652967172dc1611e3ca6eae7584bde2e521a8b9b95ee520269ff2b0b7`.
- Zero exits: 56/57; preserved classifier passed=False.
- Health exit anchor: 9722; predecessor: test_model_load.
- [FAIL] records: line 9715, test_health_check: `[FAIL] spsc_responsive: 42850 msgs/sec (need >=80K)`.
- UNAVAILABLE markers: test_npu_direct at line 8698, bench_2026_frontier at line 8849.

