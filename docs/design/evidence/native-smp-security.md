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
