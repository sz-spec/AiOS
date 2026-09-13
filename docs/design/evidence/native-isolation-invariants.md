# Native isolation observation invariants

The mathematical reviewer authored `scripts/native_isolation_smoke.py` and its synthetic negative-control tests. This is classifier implementation, not independent approval of that implementation; the separate MCP/security reviewer reviews it. The root agent authors the kernel instrumentation and user workload and runs the VMs.

Acceptance requires one observed PMM/VMM/scheduler initialization, the requested online CPU count, CPL3 parent and victim, and a victim mapping with aligned nonzero physical page and page-table roots. Four distinct child PIDs attempt foreign read/write and supervisor-kernel read/write, in that order. Each attempt is followed by a kernel diagnostic with the matching address and separate victim/child roots, the actual SIGSEGV with matching PID/address and exact page-fault error (4, 6, 5, 7), a matching wait status of 35584, and a subsequent victim canary/challenge acknowledgement. Kernel targets require a canonical higher-half supervisor mapping; foreign targets require absence in the child's user root. Completion requires successful victim exit and subsequent parent progress.

Missing, duplicate, malformed, unexpected or reordered evidence fails. So do additional SIGSEGV records, explicit failures, recognized fatal conditions, repeated boot stages, premature VM exit, CPU-count mismatch and an ISO digest change during observation. QEMU uses a bounded timeout and a private UEFI variable copy. `subprocess.run` kills and reaps its owned process on timeout. No inherited external QEMU is terminated.

Seven unit-test methods exercise successful 1/4-CPU and ANSI traces, deletion of every evidence line, adversarial changes to identities/permissions/fault errors/canaries/wait status/challenge/order, extra failures, early exits and digest mutation. These are tests of the observer, not observations of a running kernel. Actual VM qualification must be recorded separately before claiming the bounded runtime gate passed.

The result establishes only four observed user-mode fault-containment cases and survivor progress for the tested artifact/VM configurations. It does not prove universal process isolation, multicore workload execution, side-channel resistance, correctness of every page-table transition, absence of memory corruption, or compatibility with all PC hardware. CPL and canary markers rely on reviewed test code; the independent kernel fault records provide a separate correlation, not a cryptographic attestation.

## Development observation reviewed (2026-09-14)

Read the actual serial log and result at `/private/tmp/vos5-isolation-dev-20260914/bios1-env-fixed/`. The BIOS/one-CPU development run contains parent PID 1, victim PID 100, distinct faulting children 101–104, exact page-fault errors 4/6/5/7, separate victim/child page-table roots, four ordered wait/ACK pairs, victim exit zero and parent progress. The before/after ISO SHA-256 is `197c08069375fc356cdd8fdc7a41678f80b3c9e540cdd3c2b000000bb2bedd77`. This is development evidence; the clean committed-source matrix must be reviewed separately.

Inspection of the root-authored diagnostic walker confirms effective U/S is accumulated across all traversed page-table levels, including large pages. The victim maps its fixed page after forking from the parent, while the faulting children are later forked from the parent, so this workload deliberately exercises an absent foreign mapping rather than shared COW data. Neither COW divergence, scheduling fairness nor execution of the workload on application processors is established by these observations.

## Final clean matrix: bounded gate accepted

Reviewed `/private/tmp/vos5-isolation-clean-20260914/result.json` and independently reclassified all four saved serial logs: BIOS/1 CPU, BIOS/4 CPUs, UEFI/1 CPU and UEFI/4 CPUs each contain all four required cases, with no classifier failures. Independently recomputed the actual ISO and kernel digests and checked the classifier digest against the run record. The run reports a fresh archive of commit `113975f3fc4718f60bec412e07127bc8dca639a0`, 5,355 source files, no initial generated outputs, unchanged source inputs, successful build and build-container removal. Elapsed time was 115.59 seconds.

Artifact identities:

- ISO: `549e8d49235cca5956faa7ae0b3879af7f663639d81a9d44a57f20412d928883` (same before/after every VM).
- Diagnostic kernel: `b5ee527f574bfcf9b08dd7f2f4c53b6808312beb1bbe35b12fd39dc2f0f85d63`.
- User test: `c84417c80a5905882f4d77d8181cf1ee7841c97a7656ed6f6acbb5ada38ec394`.
- Classifier: `cf074095d8b8adae6058bdc863742603982db8589465fc3934d0ba3067a2bd4f`.

Also reviewed the separate `production-exclusion.json` and normal BIOS/1-CPU result: diagnostic symbols, target environment string and embedded test entry are reported absent, and normal user setup boots. Its production kernel digest `137a383ef839840e1192b3d7f55b4944360597968b61ff76d5ef93e7c25ac610` equals the previously qualified production kernel. This separate exclusion build reused bootloader output and is not represented as another clean-source build.

Accept the narrowly defined clean-build and four-case containment gate for these four QEMU configurations. This remains a diagnostic artifact qualification; it establishes neither COW behavior, scheduler fairness nor actual workload execution on APs. Review of runtime evidence is independent of the root-authored workload and kernel hooks; classifier authorship remains disclosed above.
