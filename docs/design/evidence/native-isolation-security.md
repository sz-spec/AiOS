# Independent native process-isolation security review

Scope: the test-only kernel launch hook, actual user-space adversarial program, and VM-result classifier. Root owns kernel hook/VM runs, the Rust reviewer authors the user test, and the mathematics reviewer authors the classifier. This reviewer changes only this report and launches no Docker/VM workload.

## Security oracle required before a pass

- Prove executing user privilege with `CS & 3 == 3`; a kernel task or syscall-registration check cannot qualify user isolation.
- Supervisor attack target must be a real present canonical mapping with effective supervisor-only permissions. An unmapped address or merely a leaf USER-bit assertion is insufficient to prove supervisor protection.
- A foreign victim page must not have been inherited by the attacker. Establish process creation/order and mapping ownership explicitly; fork COW of an inherited page is a different test.
- Bind expected fault evidence to offending child PID, target CR2 and page-fault error bits. Exit status 139 alone may be voluntarily returned and does not prove a hardware fault.
- Require parent and sentinel progress after the fault and unchanged protected data. Global panic, unexpected reset, unsupported fork/mmap, missing evidence or task-setup failure cannot pass.
- Test-only hook must require an explicit build flag, be represented in build identity, and be absent from the normal release path. Test environment addresses must contain only synthetic data and must not create a production capability.

## Existing fault-path distinction

`interrupts.c` logs actual user faults with task PID, CR2, RIP and error code before terminating the child. Its separate `SIGSEGV (kernel uaccess)` path kills a task after a kernel-mode user-copy fault. The latter must not count as the required user-mode supervisor/NX fault. Bad-pointer syscalls must separately assert their specified return value and liveness; kernel-uaccess termination is not equivalent to `EFAULT` recovery.

## Status

Awaiting settled implementation diffs and actual VM evidence. Prior clean-build/boot qualification does not establish this isolation gate.

## Implementation and classifier review

Authorship correction: root implemented the kernel hook and user ELF after the originally assigned user-test agent was unavailable. The mathematics reviewer authored the classifier. This reviewer independently inspected all three implementations.

The user program checks CPL3 in parent, victim and attackers. The victim alone maps its page after the initial fork; attackers are subsequently forked from the coordinator, which never acquires that VMA. Four volatile CPU accesses exercise foreign read/write and supervisor read/write. After each child termination, a pipe challenge requires the living victim to verify every byte of its page. The coordinator verifies victim exit and demonstrates post-completion progress. Missing fork/mmap/pipe/wait functionality fails rather than skipping.

The diagnostic kernel hook checks effective USER permissions across every present page-table level, including large pages. It records victim mapping ownership and terminal-fault roots without changing permission or fault-handling behavior. The supervisor target is an aligned synthetic kernel canary, checked initially and after each terminal user fault. Diagnostic hooks and direct test init launch require `NATIVE_ISOLATION_TEST=1` and `HEADLESS_AUDIT=1`. An ambient intermediate program-list variable was flagged as build hygiene; it cannot enable the gated kernel capability but should not undermine a claim that test ELF packaging is impossible in normal builds.

Independently reviewed `native_isolation_smoke.py` and ran its unit suite: **7 tests passed**. The oracle requires four correlated actual user SIGSEGV records, exact child PID/address/error codes (foreign read/write 4/6, supervisor read/write 5/7), effective present/USER evidence in the attacker user root, roots distinct from the victim, ordered attempts/faults/waits/acknowledgements, and final parent progress. Kernel-uaccess SIGSEGV, unsupported features, status139 without hardware fault, duplicate/missing evidence, corrupted identity/permissions and changed artifact hashes are rejected. These tests validate classification logic; they are not VM execution evidence.

## Final independent assessment: four-case diagnostic gate passes

Reviewed the clean qualification at `/private/tmp/vos5-isolation-clean-20260914/result.json`, built from committed source `113975f3fc4718f60bec412e07127bc8dca639a0`. Independently recomputed ISO, kernel and user-test ELF hashes, and reran the reviewed classifier against all four raw serial logs. BIOS and UEFI with 1 and 4 CPUs each passed all four cases, for 16 observed attack executions. The run completed in 115.59 seconds with build-container cleanup confirmed.

Qualified ISO: `549e8d49235cca5956faa7ae0b3879af7f663639d81a9d44a57f20412d928883`. Kernel: `b5ee527f574bfcf9b08dd7f2f4c53b6808312beb1bbe35b12fd39dc2f0f85d63`. User test: `c84417c80a5905882f4d77d8181cf1ee7841c97a7656ed6f6acbb5ada38ec394`. Every matrix entry retained the same ISO before and after observation.

The actual traces establish CPL3, a victim-created page never mapped by the coordinator, distinct victim/attacker address-space roots, foreign read/write faults with errors 4/6, and present supervisor-only target faults with errors 5/7. Each attack is followed by the correct child wait result, living victim acknowledgement after full-page canary verification, and eventual parent progress. These are correlated hardware faults rather than inferred signals from exit139. No kernel-uaccess fault was accepted as a user-isolation success.

The native test's poll operation uses the real syscall because the small libc lacks a wrapper; its bounded environment lookup reads the CRT-provided `envp` because that libc does not initialize `getenv`. Both corrections remain inside the diagnostic program. The bound is checked before indexing. The program manifest now directly includes the test only under the explicit flag, closing the ambient-variable issue.

Reviewed the separate normal-build exclusion result and symbol listing: diagnostic symbols, synthetic target environment string, test ELF and embedded test entry are absent. Its kernel hash matches the preceding normal clean-build kernel exactly (`137a383ef839840e1192b3d7f55b4944360597968b61ff76d5ef93e7c25ac610`), and the reported normal BIOS boot reaches the wizard. This exclusion run reused bootloader outputs and is not represented as another clean-source qualification.

The gate is accepted for these four direct access/fault-containment cases in the four emulator configurations. It does not certify all syscalls, NX execution, COW semantics, concurrent multi-CPU workload isolation, side channels, DMA, physical hardware, or universal process security. No kernel permissions were weakened to obtain the result; the hook only supplies synthetic setup and diagnostic evidence. No production edits or VM executions were performed by this reviewer.

Final publication scan triage: independently reviewed all 132 `generic-api-key` findings in `/private/tmp/vos5-isolation-secret-review.json`. Every finding points to a source-manifest line whose 64-character hexadecimal value exactly equals the independently recomputed SHA-256 of its corresponding file inside the isolated clean source snapshot. All 132 are manifest-hash false positives. No potential secret values were printed and no suppressions were added. This conclusion covers these findings, not an unrestricted repository-history secret audit.
