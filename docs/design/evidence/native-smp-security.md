# Independent AP user-execution security review

Scope: gated scheduler/AP wakeup changes and actual user-mode CPU-execution oracle. Scheduler implementation is assigned to the Rust agent; user workload/oracle to the mathematics agent; root owns integration and VM execution. This reviewer edits only this report and performs no Docker/VM runs.

## Initial prioritized findings

The scheduler stores current and idle tasks per CPU, but runnable queues are global. `vos3_sched_reschedule` currently requeues the executing previous task under `g_sched_lock`, selects a successor, releases the lock, then calls `do_context_switch`. Enabling another CPU to dequeue that previous task before its context/stack is relinquished creates a concurrent task-ownership hazard. Disabling local interrupts does not prevent another CPU from acquiring the queue lock. A safe switch handoff, deferred enqueue, or equivalent ownership protocol is required before ordinary multi-CPU dispatch can be considered safe.

The AP idle loop checks its reschedule flag after `sti; hlt`. Work already pending before the halt can miss its wakeup. The decision to sleep needs an interrupt-disabled pending check followed by the atomic enable-and-halt sequence; producers must publish work before the wake IPI. IPI handling must acknowledge the interrupt and defer scheduling until lock/interrupt state permits it, rather than switching while holding unrelated locks or before EOI.

## Required evidence and activation boundary

The baseline must fail the AP execution oracle despite online APs. The patched test must execute ordinary user work, read CPL3 and hardware CPUID/APIC identity from that user code, and correlate it with the kernel's logical-CPU/APIC map. Require a distinct non-BSP hardware identity and actual workload progress; neither CPU enumeration nor a kernel-assigned label proves AP user execution.

Keep activation behind the explicit diagnostic flag until the ownership and wakeup paths are qualified. Prior memory-transition tests were sequential and left shared-address-space COW/VMA mutation and remote TLB safety open. Activating AP scheduling increases exposure to these paths; an AP execution pass does not close them. Separate address spaces also share global queues, allocator state and kernel structures, so process separation alone cannot justify ignoring scheduler handoff races.

## Status

Initial findings sent to root and implementation agents. Awaiting settled diffs, baseline failure and patched runtime evidence; no AP execution or SMP memory-safety pass claimed.

## Independent workload/oracle review

Reviewed `test_native_smp.c` and `native_smp_smoke.py`; independently reran the five host tests successfully. Two forked workers produce eight deterministic 200,000-step 32-bit LCG checkpoints each, with actual CPL3 checks and CPUID leaf-1 APIC identity. The classifier verifies arithmetic, distinct child identity, completion and parent progress, topology consistency and preceding kernel scheduling evidence. For multiple configured CPUs it requires checkpoints from at least two distinct hardware APIC IDs; a BSP-only trace fails.

A CPUID result proves the user instruction executed on that hardware identity, even if migration occurs before its subsequent output. The classifier's preceding schedule marker does not establish exact ownership at every instruction, and the trace does not prove concurrent overlap, fairness or all APs executing work. The legacy 8-bit APIC oracle is deliberately restricted to the small 1–4 CPU test topology.

Host oracle tests do not substitute for the real baseline and patched-image runs. Scheduler ownership handoff and activation risks remain pending implementation review.

## Proposed ownership approach and lifetime review

Root took over scheduler implementation after the assigned implementation agent could not complete it. An immutable CPU owner established on first dispatch can prevent another CPU from dequeuing a task whose previous context has not yet been saved, provided every queue-selection path filters ownership, enqueue never overwrites it, fork starts unbound and shared-VM clone remains on the parent's owner. This deliberately restricts migration and is not general load balancing.

Additional activation-critical lifetime finding: the existing reaper excludes only the task current on the reaping CPU. A two-tick delay is not proof that another CPU has stopped using a dead task's stack/context. Owner-local reaping after context handoff or a proven quiescence protocol is needed. Lazy FPU ownership is per CPU, but the existing release helper traverses every CPU's owner pointer without synchronization; remote freeing can race #NM state access. Fixed ownership and owner-local, interrupt-safe release can bound that risk. The integer-only LCG test does not qualify FPU isolation.

## Activation-diff review

Reviewed root's gated fixed-owner picker, preserved enqueue ownership, fork reset, inherited clone owner, owner-local reaper and interrupt-protected local FPU-owner release. These address the identified remote context-handoff and remote-reclamation hazards within the fixed-ownership model. The appended owner field preserves offsets of existing task members. Idle pending checks now occur with interrupts disabled before STI/HLT. Periodic BSP wake IPIs use the reserved installed vector and started AP topology; immediate enqueue wakeups are not implemented.

Before trial execution, flagged that the new IPI handler unconditionally reschedules, and reschedule invokes deferred reclamation while still in the interrupt call chain. Calling such work “process context” does not make it safe when an interrupt stopped kernel code holding another lock. Requested bounded gating to interrupted user mode or idle, respect for pending scheduling, and deferral of reclamation when entered with interrupts disabled. The existing BSP path has related assumptions; AP activation must not be presented as a full kernel-preemption audit.

## Gated trial readiness follow-up

Independently inspected the follow-up correction: the AP IPI acknowledges first, requests scheduling, and switches only when the interrupted frame is CPL3. Interrupted kernel/idle code returns to its own continuation. Deferred reclamation rejects entry with IF clear; the AP idle loop explicitly enables interrupts before its own deferred-work phase, then disables them for the pending check and STI/HLT decision. Initial scheduler selection disables interrupts before publishing scheduler-running state.

These changes resolve the identified AP interrupt/reclamation blocker sufficiently for the bounded diagnostic VM trial. They do not authorize production-wide SMP activation. IF alone is not a universal interrupt-context detector if future handlers enable nested interrupts; the reviewed handler behavior is an explicit assumption. Setting pending before each user-mode IPI permits preemption irrespective of the previous time slice, so fairness/overhead remain separate policy questions. Actual baseline and patched observations are still required.

## Diagnostic record-lock review

Reviewed root's gated `NATIVE_SMP_WORKLOAD` record lock shared by `vos3_log` and the TTY console write path. The syscall copies user data to stack/heap bounce storage before acquiring this lock. Within the reviewed locked paths, formatting/ring updates and serial/VGA output do not allocate or call the logger recursively; raw console helpers do not reacquire the record lock. Interrupt disable/restore protects against ordinary same-CPU interrupt reentry. This is acceptable for the next bounded diagnostic trial and prevents interleaved kernel/user checkpoint records without loosening the classifier.

Limits remain: direct raw-console and panic output are outside this serialization; NMI or a fault while formatting invalid kernel data can still reenter logging. Large writes split into bounce chunks are not guaranteed atomic as a whole. The tested checkpoint records fit the small bounded-write path. This is not a general crash-safe console or arbitrary untrusted logging guarantee. The earlier interleaved multi-CPU trace remains a failed observation, not a retroactive pass.

## Gated clean runtime evidence

Independently reviewed the clean result for commit `c4df93d468585542147ca96f5aba04b6cd2e5843` at `/private/tmp/vos5-smp-records-20260915`, recomputed ISO/kernel/user-ELF hashes, and reclassified all four original serial logs. All pass: BIOS/UEFI single-CPU controls report worker APIC 0; four-CPU BIOS reports worker APIC IDs 1 and 2, and four-CPU UEFI IDs 0 and 1. These are actual CPL3 CPUID observations with matching kernel topology/schedule evidence and verified checkpoint arithmetic, child exits and parent progress.

ISO `3bebb24ff547d9ea69b27f535e9cd8df89e27dbc49e84d0ba6b05a1c5fd8d75d`; kernel `f239f485af2ce7a4ca8e5764b72c26f714eb79fea28140b47ec6d726cee28af3`; user ELF `cb65e27bc559bb36b6a7c16421b25663a8f31cd7d8ae1efa566bd2408295d32b`. Every boot retains the same ISO hash. The run took 106.61 seconds and recorded successful build-container cleanup.

The recorded build explicitly enables `HEADLESS_AUDIT=1`, `NATIVE_SMP_WORKLOAD=1` and `NATIVE_SMP_TEST=1`. Source inspection confirms activation requires the workload flag and remains absent by default; the workload alone does not enable AP scheduling. The supplied four-commit source secret scan reports zero findings. This is a scoped scan result, not a repository-history guarantee.

The gated result establishes user execution on non-BSP processors. The serialized old-scheduler baseline and normal-build regression remain pending at this writing, so comparative/default-build qualification is not yet closed. No remote TLB, shared-VM concurrency, FPU isolation, fairness, simultaneous execution or physical-hardware guarantee is inferred.

## Final comparative/default-build review and verdict

Independently reclassified the persisted serialized baseline: both single-CPU controls pass; BIOS/UEFI four-CPU cases fail specifically because workers did not demonstrate multiple hardware CPUs. This supplies the required controlled negative baseline without treating malformed/interleaved output as the evidence of absent AP execution.

Independently reclassified all four normal-regression raw traces successfully. Its kernel hash `9d0bfc6065797c5c6f2a2d66155719f356ac653c285a9a806832a75ac7b8457f` matches the previously qualified normal kernel from the memory-transition regression. Together with the explicit build gates, this supports the default-off boundary: the AP scheduler remains diagnostic and is not activated in ordinary builds. Comparative baseline and normal-regression checks are now complete, superseding earlier pending statements.

Reviewed the final qualification report's scope: it correctly excludes simultaneous execution, fairness, sustained stability, FPU isolation, shared-VM synchronization, remote TLB safety and physical hardware. The gated AP user-execution stage is accepted within those limits; production activation is not approved by this evidence.

Independently recomputed all 134 source-manifest values identified by the publication secret-scan review against the isolated clean source files. Every value is its corresponding file's SHA-256 digest: all are hash false positives. No potential secret values were printed and no suppressions were added. This triage is limited to the reported findings.
