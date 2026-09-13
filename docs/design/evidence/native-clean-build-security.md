# Independent native clean-build security review

Scope: clean native build provenance, BIOS/UEFI boot observations and subsequent process-isolation evidence. Root owns builds and VM execution; this reviewer performs bounded read-only inspection and edits only this report. No universal hardware or kernel-security claim follows from this qualification.

## Required clean-build evidence

Record the source commit plus the exact tracked-source snapshot manifest, including any deliberate uncommitted overlay. The isolated snapshot must contain no copied object files, archives, generated headers, bootloader binaries or ISO outputs. Record immutable builder image identity, compiler/linker/tool versions, network-disabled execution, the single build command and exit status, and output hashes. Reusing a pinned toolchain image is compatible with a clean source build; reusing project output artifacts is not.

All BIOS/UEFI and single/multiple-CPU runs must identify the same resulting ISO hash, firmware identity, requested CPU model/count, RAM and VM acceleration. Require actual user-space output, no panic or unexplained reset, and observed online CPU count for SMP. A timeout is merely an observation window; it passes only with the required positive evidence. CPU enumeration does not prove that multiple processors scheduled independent workloads.

## Source review and false-positive risks

`vmm.c` contains W^X enforcement and user-mapping sanitization. `mm/user_copy.c` checks process-specific address bounds and rejects kernel/noncanonical addresses; copy helpers manage SMAP access around the transfer, with capability-aware STAC/CLAC. These source mechanisms require runtime corroboration, including CPUs without optional SMEP/SMAP capabilities.

A separate legacy `vos3_copy_from_user` / `vos3_copy_to_user` implementation in `arch/x86_64/user.c` uses unchecked end-address addition and retains a page-fault handling TODO. This is a helper reachability/audit concern, not an established reachable exploit in the active syscall path. The main copy implementation validates before a raw copy, so unmapped/cross-page faults and concurrent mapping changes cannot be assumed safe from range checks alone.

Do not use these existing summaries as security proof:

- `bench_app_isolation_test.c` explicitly runs its sandbox check as a non-app/kernel task and accepts syscall reachability; it does not prove denial for an untrusted app. Its quota check starts by observing getpid, not actual quota enforcement.
- `bench_isolate.c` counts unavailable mmap or fork as PASS. Those cases must be failures or explicit unqualified skips for a test claiming process isolation/COW.
- Some unrelated benchmarks contain constant assertions, such as a DMA-isolation variable initialized to one. Such statements cannot establish hardware isolation.
- `native_boot_smoke.py` requires real wizard text and requested online CPU counts, which supports boot qualification but does not prove process separation or fairness.

## Adversarial runtime expectations

Use actual user-mode execution, not a host model or a kernel task labeled as an application. Confirm required syscall availability before the adversarial action. Exercise kernel/noncanonical pointers, overflowing lengths, unmapped and page-boundary buffers, and read-only destinations through real syscalls; require the specified error and subsequent task/kernel liveness rather than accepting any failure.

A child attempting supervisor-memory access or execution from a non-executable mapping should fault as expected while the parent and another sentinel workload continue. Assert the fault belongs to the offending process, not a global panic. A successful fork/COW case must confirm distinct process identities and unchanged parent data after child mutation. SMP evidence should include actual workload progress on more than one CPU before claiming concurrent isolation. Missing features or test setup failures must never produce a security PASS marker.

## Status

Clean-build and boot/isolation evidence pending. Findings above define qualification boundaries; no production files were modified and no Docker/VM execution was launched by this reviewer.

## Independent review of boot classifier correction

The mathematics reviewer authored the classifier correction; this reviewer independently inspected its diff and reran `python3 -m unittest discover -s scripts -p test_native_boot_smoke.py -v`: **9 tests passed**. Classification now requires PMM, VMM, scheduler and actual wizard-output evidence, exact reported CPU count including single-CPU runs, and no recognized fatal markers after ANSI normalization. Unexpected VM exit is rejected even after a banner. Pre/post ISO hashes invalidate an observation whose artifact changed.

The tests cover each missing stage, CPU count mismatch, ANSI-obscured panic/count markers, faults before/after otherwise complete boot, and ISO hash mismatch. These are classifier regressions, not new VM execution results. Pre/post hashing establishes endpoint consistency under the isolated/immutable artifact assumption; it does not detect a hostile change-and-restore race. The classifier must remain separate from future adversarial tests that intentionally fault a user child.

## Final clean-build and boot assessment

Independently reviewed `/private/tmp/vos5-native-clean-20260914-verified/result.json` and recalculated the resulting ISO hash. The bounded clean-build/boot gate **passes**: commit `4082424c3f61b837c7052494566f385f4a0df745`, 5,328 archived source files, unchanged source manifest, no preexisting compiled/output paths, builder `sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6`, network disabled, and the single `make native -j4` command exited zero. The reviewed boot-source manifest contained no copied `.bin`, `.efi` or `.sys` artifacts; relevant extensionless inputs were source/build scripts and license files.

ISO `4b85acf0a51583aff2361408125fda4800827d0c13b92e4d1c596b19656009d8` passed BIOS/UEFI with both 1 and 4 requested CPUs under QEMU 10.2.2 TCG. Every observation reported PMM/VMM/scheduler and actual wizard output, exact online CPU count, no classified fatal markers, and identical pre/post ISO hashes. The complete verified run took 105.38 seconds and confirmed its build container absent after cleanup.

The first run's overall failure was an absence-message parsing issue in cleanup despite successful build/boots. Root corrected the harness to require successful exact-name container enumeration, verify its ownership label before removal if present, and require a successful empty requery. This reviewer inspected that correction; the fresh verified rerun passed without suppressing cleanup errors.

Warnings remain recorded: a small future timestamp on a Docker bind-mounted directory, GNU-stack/linker warnings, and ignored link flags. The fresh source/output separation rules out warm project artifacts for this run, but does not justify hiding those warnings. The two clean runs produced the same kernel hash `137a383ef839840e1192b3d7f55b4944360597968b61ff76d5ef93e7c25ac610` and different ISO hashes. The separate investigator attributed differences to Rock Ridge timestamps and the Limine BIOS build ID. **Bit-for-bit ISO reproducibility is not established.**

Approval is limited to clean native build and the four observed emulator boot configurations. Process isolation remains the next separate gate; these results do not prove SMP workload execution, COW/fault containment, physical-PC compatibility, DMA isolation, or complete kernel security.

## Independent secret-scan triage

Reviewed all 131 findings in `/private/tmp/vos5-native-clean-secret-review.json`. Every finding uses `generic-api-key` and points to a single line in the generated source manifest. For each flagged line, independently parsed its source path and 64-character hexadecimal value, checked that the resolved path stays inside the isolated source snapshot, and recomputed SHA-256 from that file. **All 131 values exactly match source-file digests.** The scanner's captured Secret fields were redacted, so classification relies on the actual flagged lines and independent recomputation, not guessing from redacted values.

These 131 findings are manifest-hash false positives, not credentials. No potential secret values were printed, and no scanner suppression was added. This conclusion is scoped to the reported findings; it is not a claim that every repository file or historical commit is free of secrets.
