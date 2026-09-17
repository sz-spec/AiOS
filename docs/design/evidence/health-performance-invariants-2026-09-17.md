# Health performance: bounded invariants review, 2026-09-17

Reviewer: math_build_review. Production authors: coordinator (root synchronization) and mcp_upgrade (deferred empty path). This review inspected the final two C changes; it did not execute a build or VM. Combined final runtime qualification was still pending when this note was written.

## Deferred pending observation

In `kernel/src/sched/scheduler.c`, vos3_sched_process_deferred keeps its IF-enabled guard and CPU index bound. An acquire load observing pending=0 returns without changing either pending or active. A producer release-store racing after that observation therefore remains available to a subsequent safe point. A nonzero observation enters the original active exchange and pending-consuming exchange; concurrent/reentrant drains remain excluded. A stale positive observation followed by another completed drain reaches pending-exchange=0 and releases the active guard without draining twice.

This preserves the publication protocol, not an unconditional progress theorem. Progress requires a later IF-enabled safe point on the target CPU and completion of any active drain. Async cancellation of an active continuation remains a separately documented blocking lifetime/liveness gap. Acquire/release operations do not establish correctness of all subsystem destructors or remote task stopping.

The focused actual-C test uses deterministic atomic-boundary hooks and exercises publication after a zero observation, publication during an active drain plus reentry, remote CPU selection, IRQ-disabled deferral and CPU bounds in both UP and NATIVE_SMP_TEST branches. It is not hardware SMP stress or a full memory-model proof.

## Root synchronization equivalence

In `kernel/src/arch/x86_64/kpti_roots.c`, the destination partition is:

- indices 0 through 256 inclusive: copy source (257 entries);
- indices 257 through 510 inclusive: zero (254 entries);
- index 511: copy source (one entry).

The ranges are disjoint and their union is exactly all 512 entries. Thus the C-level operations retain exactly 512 destination assignments and 258 source reads, with the same resulting values as the previous conditional upper-half loop. These are abstract source operations, not a claim about emitted machine-store instruction count. NULL and overlap rejection remain unchanged. The change removes per-index policy branching; it does not skip synchronization or introduce compare-before-store.

The caller's AS locking, IRQ discipline, root binding and CR3/TLB transition behavior are unchanged by this diff. Retained entries 256/511 still represent the existing partial KPTI boundary; this optimization does not establish complete kernel isolation, remote revocation or atomic page-table snapshots against hardware updates.

## Performance reasoning and acceptance

The health producer polls GETTIME per attempt and yields on a full 1024-slot ring. Its consumer spins on empty until scheduling permits producer progress. For a nominal 10ms tick, a fill time near one tick can create a nonlinear throughput transition: finishing before the tick permits a full-ring yield, whereas preemption before filling can give the empty consumer another service interval. This is a qualitative model, not the established sole cause. Illustrative microsecond costs assume calibrated units; recorded emulated TSC deltas have not independently established such a frequency conversion.

The initial compare-before-store candidate achieved only 34,970 messages/second and was rejected/reverted. Instrumented health altered code layout and warmup and cannot serve as acceptance evidence. Unchanged-ISO variation also prevents attributing every rate change to production code.

The current candidate leaves the original health source, 80,000 messages/second threshold, duration, ring workload and flush behavior unchanged. Root reported 107 host tests passing and 27 KPTI tests passing for this candidate; these are coordinator-observed results, not reruns by this reviewer. Final combined BIOS/UEFI full-suite results must be recorded separately before claiming the performance gate passed. No full physical-hardware, fairness, async-cancellation or general SMP qualification is implied.

## Coordinator outcome after this source review

All four final full-suite runs passed, with 57 zero exits each; see the [qualification report](health-performance-2026-09-17.md). This later runtime result does not expand the safety claims or assumptions in the review above.
