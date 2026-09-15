# Native AP workload qualification gap

Status: bounded source review, 2026-09-15. No scheduler changes, builds or runtime experiments were performed for this review. The current memory qualification matrix is sequential; AP workload execution remains a separate next gate.

## Observed production paths

- `kernel/src/arch/x86_64/smp.c`, `vos3_ap_entry`: initializes each AP's CPU state, syscall MSRs, LAPIC and scheduler, then waits for BSP release before calling `vos3_sched_loop_ap`. The online/release handshake establishes successful initialization, not user workload execution.
- `kernel/src/arch/x86_64/smp.c`, `vos3_lapic_init`: configures LAPIC enablement, spurious vector, task priority, error status and EOI. This function does not configure an AP periodic scheduling timer.
- `kernel/src/sched/scheduler.c`, `vos3_sched_init_ap` and `vos3_sched_loop_ap`: initializes the AP's current task to its idle task and `need_reschedule` to zero. The idle loop processes deferred work, executes `sti; hlt`, and only invokes rescheduling when that CPU's flag is set.
- `kernel/src/sched/scheduler.c`, task enqueue: chooses a nominal least-loaded CPU and updates task bookkeeping, but inserts the task into shared priority/interactive queues. This path does not set the target AP's reschedule flag. `pick_next_task` dequeues from shared queues without enforcing the nominal assignment as affinity. The context-switch path updates the task's CPU identifier to the actual scheduling CPU; an enqueue-time identifier alone is not execution evidence.
- `kernel/src/sched/scheduler.c`, `vos3_sched_ipi_preempt_hook`: the interactive preemption hook is inactive unless `VOS3_LATENCY_IPI` is enabled; its source comment describes the architecture integration as deferred. Its presence does not establish a working default AP wakeup mechanism.
- `kernel/src/arch/x86_64/interrupts.c`, `handle_timer_irq`: handles IRQ0, acknowledges the PIC and checks the current CPU's reschedule flag. `vos3_sched_tick` advances global ticks and wakes sleepers on the BSP. This does not by itself establish an AP-local timer source.

Searches of native syscall implementations and declarations found no implemented `getcpu`, `sched_setaffinity` or `sched_getaffinity` interface. AI slot affinity metadata is a different interface and must not be treated as process scheduling affinity. The scheduler's nominal CPU assignments and online CPU counts are therefore unsuitable as a workload oracle.

These observations identify the absence of a demonstrated normal wake/reschedule route for newly runnable AP user workloads. They are not a runtime proof that an AP can never execute a task: other interrupts, configurations or diagnostic paths may change behavior. Trace and qualify the concrete scheduling route before claiming AP application execution.

## Smallest next native test

Run a normal native ring-3 ELF coordinator with multiple CPU-bound child workers on the qualified QEMU image. Use independent processes first; thread/address-space sharing adds a separate synchronization surface. Workers perform deterministic computation, check their results, and emit repeated numbered checkpoints containing PID, actual `CS & 3`, and CPU identity sampled with CPUID. Map the hardware APIC identity to the kernel's logical CPU topology; CPUID's legacy initial APIC-ID field is sufficient only for the small, explicitly checked topology, and larger topologies require the appropriate supported topology leaf.

Correlate worker observations with bounded kernel scheduler observations of actual context switches or syscall execution on those CPUs. Require CPL 3, correct computation, continued coordinator/BSP progress, successful worker completion, and multiple computation checkpoints on at least two distinct CPUs. Unsupported identification, missing AP progress, deadline expiry, panic or missing completion must fail qualification. Merely increasing QEMU's configured CPU count must not satisfy the oracle. Preserve the kernel/ELF/ISO hashes, configuration and complete serial evidence.

First determine whether the current production scheduler can pass this test unchanged. If it cannot, implement and review the smallest missing timer/wakeup/reschedule mechanism separately, then rerun the same oracle; do not bypass it by manually placing diagnostic code on APs and labeling that user scheduling.

## Claim boundaries

Update, 2026-09-15: the proposed actual-user-execution gate is now implemented and
[qualified in an explicitly gated diagnostic image](native-smp-qualification.md).
The unchanged scheduler fails its multi-CPU criterion. The analysis above remains
the baseline diagnosis; the normal production build still does not enable the new
AP scheduler. The stronger concurrency and memory-revocation claims below remain open.

1. **Online CPU:** initialization and BSP release succeeded.
2. **Actual user execution on multiple CPUs:** genuine ring-3 computation checkpoints were observed on distinct hardware CPUs.
3. **Concurrent progress:** independent workloads made progress during a coordinated overlapping interval; migration of a single worker between CPUs is insufficient.

The proposed first test establishes the second claim only. A subsequent synchronized multi-worker interval, with continued per-worker progress, is needed for concurrency. Preemption fairness, cross-CPU memory coherence, migration safety and sustained SMP stability require further targeted tests. Neither the sequential memory matrix nor AP online counts establish those properties.
