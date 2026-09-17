# AI context cancellation: unresolved conservation gate

Independent reviewer: math_build_review, 2026-09-17. **Not closed.** No production changes and no QEMU execution were made for this review.

## Reproducible diagnostic

The preserved [diagnostic source, logs and hashes](ai-context-lifetime-2026-09-17/cancellation/) are also available in `/private/tmp/vos-ai-cancellation-frozen-review-20260917/`, alongside the local executable. Compile with `cc -std=c11 -Wall -Wextra -Werror -pthread diagnostic.c -o diagnostic`, then execute `./diagnostic`. These final inputs match frozen `ai_guard.c` SHA-256 `62a000d26ddb49344a25e85b789787a29dc45b18430a796c9bef382052e996a4`. The earlier pre-freeze run is retained separately under `cancellation-pre-freeze/`.

The diagnostic extracts the current production context lifecycle functions using the existing host harness and replaces only its scenario main. It creates app context 7, acquires a reader, destroys the app owner, omits the abandoned reader's put, then executes the real context reaper. Output:

```
BLOCKING_GAP owner_refs=0 abandoned_reader_refs=1 allocations=1 frees=0 live_contexts=1 discoverable=0
```

Exit zero means the diagnostic reproduced the blocking gap, **not** that cancellation is safe. This is deliberately outside the passing regression suite. IRQ, scheduler-current, allocation and task abandonment are mocked. In particular, no actual `vos3_task_kill` or task reaper runs in this harness; the source-level kill-path audit supplies that connection.

## Conservation argument and source linkage

For a context, refs = owner refs + outstanding reader refs. Creation gives 1; acquire gives 2; owner detach gives 1. Final reclamation requires zero. An abandoned continuation never executes its put, so the remaining reference is inaccessible to normal discovery yet prevents retirement indefinitely. Refcounting correctly avoids premature free but cannot manufacture cancellation cleanup.

`kernel/src/sched/task.c`, `vos3_task_kill`, publishes terminal state and removes the target from scheduling without guaranteeing its interrupted kernel continuation resumes. `vos3_task_reap` invokes the wait cleanup hook, handles the persistent `task->ai_guard_ctx` reference and releases its stack; it has no ledger for transient reader locals. `kernel/src/mm/ai_guard.c`, acquire_global/app_ctx and ctx_put, account those locals. Examples are telemetry reads in `kernel/src/mm/ai_telemetry.c` and reprotect/reclaim in ai_guard.c.

The broader async-kill kernel-continuation problem predates this patch. Retained readers introduce a new visible conservation requirement: all acquisitions must eventually release, including cancellation. Neither the host retained-reader regression nor this diagnostic proves a complete task cancellation mechanism.

## Required policy before closure

Either defer terminal cancellation until a defined safe kernel boundary while preventing new work, or provide explicit ownership/cleanup for every resource an abandoned continuation can hold. A separate bounded task pin ledger would address transient AI refs, but does not cover held context locks, detached reclamation batches or the per-CPU deferred-active guard in `kernel/src/sched/scheduler.c`. Those require their own completion/transfer protocol.

There is an existing starting point: `vos3_signal_send` in `kernel/src/ipc/signal.c` queues a pending signal, and syscall dispatch invokes `vos3_signal_deliver` before returning to userspace. Merely routing APPKILL through that path is insufficient: CPU-bound userspace also needs a safe interrupt-return boundary, blocked kernel waits must unwind on cancellation, and direct destruction/dispatcher kill paths must honor the same policy. These are implementation requirements for the next stage, not behavior demonstrated by this checkpoint.

Cleanup must follow a proven stop of the target continuation. IRQ-off on the killing CPU is insufficient for remote SMP; owner CPU acknowledgement and exclusion of resumption are required. Ordinary completion and cancellation must claim cleanup exactly once, with overflow/capacity failures before acquiring unrecorded resources. Final destructors remain outside locks in safe process context. Region-list lifetime/mutation and authorization remain separate gates.

The present checkpoint is limited to retained readers whose continuations complete. Async cancellation remains an explicit blocking gate; do not present this diagnostic as closure or as the established cause of the separate health throughput regression.
