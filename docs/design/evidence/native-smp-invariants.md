# Native SMP observation invariants

The mathematical reviewer authors the user workload and host classifier; a separate security reviewer reviews them. Root/peer authors implement kernel scheduling and metadata. No native execution has yet been observed by this author for this gate.

Two forked CPL3 workers each compute eight checkpoints of 200,000 deterministic unsigned-32-bit LCG iterations. The host independently calculates each expected value. Every checkpoint identifies its PID and the actual legacy APIC ID read by CPUID leaf 1 in user mode; the supported gate is restricted to 1–4 CPUs under qemu64. Parent completion requires both distinct children to exit zero, all sixteen checkpoints, and subsequent CPL3 parent progress.

Kernel topology must contain each requested logical CPU exactly once with unique APIC identity. Every worker/APIC observation requires a preceding kernel scheduling marker for that PID and APIC; all scheduling markers must agree with topology. Kernel scheduling logs alone cannot pass: actual user CPUID observations are mandatory. SMP configurations require worker observations on at least two hardware CPU identities. The one-CPU run is a deterministic workload control and makes no AP execution claim.

This establishes actual observed execution across CPU identities; it does not establish simultaneous execution. A migrating worker may account for some observations, and serialized workers can satisfy the gate. Neither scheduling fairness nor concurrent shared-memory/TLB/COW correctness follows. An overlap proof would require additional synchronized interval evidence, and is deliberately not inferred from checkpoint ordering or online counts.

Missing/corrupt/reordered checkpoints, incorrect calculations, absent schedule correlation, unknown/duplicate topology, BSP-only multiprocessor runs, unexpected faults or completion failures are rejected. The runner binds before/after ISO hashes, runs a finite QEMU observation and kills/reaps its owned QEMU on timeout. Five host test methods cover valid controls, BSP-only negative baseline, deletion of every evidence line, identity/value/topology corruption and fatal/early exits. These test the observer; clean native build, baseline and final VM evidence remain separate qualification steps.

## Proposed scheduler ownership review

Read-only review of the existing scheduler/task lifecycle before the new ownership implementation. Immutable owner assignment under the scheduler lock can prevent a different CPU from selecting the previous task after it is queued but before its stack/context handoff finishes. The proof requires every queue-selection path, including interactive priority and AP paths, to enforce the same owner predicate. Forked private address spaces may start unbound; CLONE_VM must inherit the already bound parent's owner. Wakes and affinity/balance operations must never overwrite ownership and must notify the owner so a sleeping AP resumes eligible work.

A concrete additional lifetime gap was reported: task destruction/reaping currently excludes only the local current task. A remote parent can observe an exiting task before its AP has completed switching away; a fixed tick delay does not prove quiescence. Owner-local reclamation with same-owner current exclusion is a bounded strategy compatible with immovable tasks. An all-CPU current-pointer snapshot alone is insufficient because context-switch code publishes `next` before leaving the previous kernel stack. Destruction paths must not free stack, address space or lazy-FPU state during that handoff. This is an implementation review condition, not a proof already satisfied.

The existing add-task path writes nominal `task->cpu_id`; that field must remain separate from immutable scheduling ownership and hardware CPUID evidence. Repeated wake/unblock and sleep-queue transitions also need serialized state/queue membership: immobility by itself does not repair stale READY transitions or duplicate wake races. Final source and observed execution must be reviewed after integration.

## Gated ownership implementation review

Reviewed the current gated scheduler/task/exec/interrupt diff. The owner-filtered queue scan preserves interactive-first ordering followed by priorities from highest to lowest, and scans beyond ineligible foreign-owner heads. Selection assigns ownership and removes the chosen task under the scheduler lock. Private fork clears ownership; the CLONE_VM copy retains the parent's bound owner. Idle tasks are bound to their own CPU. Wake routing retains the owner target rather than replacing it with load-balancing metadata.

Reaping now defers tasks owned by another CPU, and direct destruction rejects remote-owned tasks. Local FPU-owner release is consistent with immobility. Within that assumption, owner-local reaping plus local-current exclusion avoids freeing a different CPU's active or switching stack; it does not prove arbitrary migration or shared-address-space scheduling safe. Global deferred-list contention may delay reclamation and does not provide a fairness bound.

The AP scheduling IPI retires its LAPIC interrupt before invoking scheduling, and BSP timer broadcasts provide scheduling opportunities to started APs. A concrete startup exclusion question was sent to root: the first selection now takes the scheduler lock but must explicitly disable local IRQs before publishing scheduler-running, rather than relying on an undocumented boot caller IF=0 condition. Timer re-entry while holding that lock would otherwise deadlock. Final integration and baseline/patched VM results remain pending.

## Linked baseline diagnosis

Read actual baseline evidence under `/private/tmp/vos5-smp-baseline-linked-20260915/`. BIOS/4 CPUs contains all sixteen user checkpoints, both workers reporting hardware APIC 0 throughout, and successful worker waits. This is meaningful failure of the AP-execution criterion despite four online CPUs.

BIOS/1 CPU instead fails because serial records interleave mid-run, not because timeout truncates the final line. Exact line 1373 contains `... cpl=3 api[INFO]  NATIVE_SMP schedule pid=101 cpu=0 apic=0`, interrupting the worker record before its APIC field completes. The file tail consists of complete scheduling records. User `vfprintf` already formats into a buffer and issues one write; the TTY console write implementation emits each byte through separate `vos3_console_putc` calls, permitting kernel scheduling output between bytes. Reported this path to root and recommended whole-record console emission with serialization after confirming its buffer contract. Subsequent inspection found no existing console lock; root added a diagnostic-only spinlock with IRQ exclusion. Bounded once-per-task scheduling output reduces noise but does not by itself establish atomic records. The strict oracle remains unchanged and rejects the corrupted baseline.

## Serial corruption regressions

Added explicit observer tests reproducing the observed mid-record scheduling insertion inside a worker APIC token. Added three truncated schedule forms: partial key, empty value and missing fields, including trailing corruption after otherwise valid completion. All must fail; no normalization or record repair is used. Seven host test methods pass. Root owns the separate console serialization implementation; passing these tests establishes rejection of malformed evidence, not successful runtime serialization.

## Final gated AP-execution gate accepted

Independently reclassified all four raw logs under `/private/tmp/vos5-smp-records-20260915/`. Each has exactly sixteen valid deterministic checkpoints, correlated with kernel topology/scheduling and actual user CPUID identity. BIOS/1 and UEFI/1 pass as single-CPU controls with APIC 0. BIOS/4 workers execute on APICs 1 and 2; UEFI/4 workers execute on APICs 0 and 1. Thus both multiprocessor runs establish actual user execution on an AP and across at least two CPU identities, beyond the online-count-only baseline.

Independently recomputed actual ISO, kernel ELF, workload ELF, classifier and qualification-harness hashes; every digest matches the result:

- Source commit: `c4df93d468585542147ca96f5aba04b6cd2e5843`.
- ISO: `3bebb24ff547d9ea69b27f535e9cd8df89e27dbc49e84d0ba6b05a1c5fd8d75d`.
- Kernel: `f239f485af2ce7a4ca8e5764b72c26f714eb79fea28140b47ec6d726cee28af3`.
- User workload: `cb65e27bc559bb36b6a7c16421b25663a8f31cd7d8ae1efa566bd2408295d32b`.
- Classifier: `207b34fe145214e4e0223256333db51dd717a55085e449864d233abc3d5cd1a2`.
- Qualification harness: `902f72b8529d3c3223b1b52fcb31efbdd72fd75cc5ea395e645a01e00c685a60`.

The result records 5,436 fresh archived source files, no initial generated output, unchanged source inputs, network-disabled pinned builder, successful build, stable before/after ISO in all runs and build-container removal. Elapsed time: 106.61 seconds.

Accept this gated AP-execution gate for the tested QEMU configurations. The reviewer independently examined production implementation and runtime evidence but authored the workload/classifier; separate security review owns their independent assessment. The gate does not establish simultaneous execution, fairness, remote TLB correctness, shared-address-space concurrency, isolation or enabled-by-default production SMP behavior. The serialized unchanged-scheduler baseline and normal-production regression are separate pending comparisons and are not inferred from this result.

## Serialized unchanged-scheduler comparison confirmed

Independently reclassified all four raw logs from `/private/tmp/vos5-smp-baseline-serialized-20260915/`. Both one-CPU controls pass. Both four-CPU runs fail only `workers did not demonstrate multiple hardware CPUs`, after all sixteen correct checkpoints, successful waits and final parent progress were validated. Every worker checkpoint reports APIC 0. There is no malformed-record explanation for these two failures.

The baseline uses the same source commit `c4df93d468585542147ca96f5aba04b6cd2e5843`, workload ELF digest `cb65e27bc559bb36b6a7c16421b25663a8f31cd7d8ae1efa566bd2408295d32b` and classifier digest `207b34fe145214e4e0223256333db51dd717a55085e449864d233abc3d5cd1a2` as the passing gated scheduler matrix. Independently hashed the actual baseline workload ELF to confirm identity. Baseline ISO digest is `c8a9e7b45693f94ba6c83a350d6865895428068f147763474d55271fe6d9230f`.

This controlled comparison supports the narrow conclusion: the gated scheduler enables observed AP user execution that the unchanged scheduler does not demonstrate, while the same workload/control logic completes under both. It does not expand the previously stated concurrency, fairness or production-default boundaries. Normal-production regression remains separately pending.


## Normal-build regression confirmed

Read the completed normal qualification result at `/private/tmp/vos5-smp-normal-regression-20260915/result.json`: all four BIOS/UEFI × 1/4-CPU boot controls pass PMM, VMM, scheduler, user setup and exact CPU-count requirements. This uses the same committed source `c4df93d468585542147ca96f5aba04b6cd2e5843`, a clean source archive and ordinary `make native`, without enabling the diagnostic scheduler/workload flags. Source inputs remain unchanged and the build container is removed.

Normal kernel digest is `9d0bfc6065797c5c6f2a2d66155719f356ac653c285a9a806832a75ac7b8457f`, matching the prior normal kernel reported by the qualification owner. ISO digest is `678d6a73baf4d8533cd92a1751cddb7ac2ff103e1517e79485e23efc31ccde45`, stable throughout these runs. The ISO differs from the prior packaging output; byte-identical ISO reproducibility is not established. This normal-boot regression does not imply diagnostic AP scheduling is enabled in the default build. Together with the passing gated matrix and meaningful serialized baseline, the bounded AP-execution evidence is complete.
