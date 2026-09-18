# Cancellation, deferred work and task lifetime — integration work

Review date: 2026-09-18. Code inspected: `fedcfc895b1cc499dcb183fa1978be25c996075d`.
Status: design review in progress, **not implemented or accepted**. This work
blocks the AI OOM integration; it is also required for production task safety.

## Established code facts

* `kernel/src/sched/task.c:1203`: remote `vos3_task_kill` directly publishes
  ZOMBIE and removes the target from scheduling. It has no handshake with an
  active kernel continuation on another CPU.
* `kernel/src/sched/scheduler.c:914`: deferred drain executes in a caller's
  continuation, consumes pending work and holds a per-CPU active flag while
  callbacks can block. Discarding that continuation can abandon the active
  flag and detached local reclamation lists.
* `scheduler.c:313`: only the `NATIVE_SMP_TEST` branch of task selection enforces
  `sched_owner_plus_one`. Production dequeues from shared queues without that
  owner check. The nearby no-migration comment is not a production guarantee.
* `scheduler.c:990`: the previous task is requeued and the scheduler lock is
  released before the actual context switch. `do_context_switch` publishes
  `g_current_task[cpu]=next` at line 415 before saving/leaving the previous
  stack at lines 520 onward. Neither absence from `current[]` nor queue removal
  by itself proves that a stack is no longer in use.
* `task.c:642,700`: retirement/reaping lacks a common terminal claim; the
  current-task exclusion is local and CPU-owner exclusion is test-only.
* `scheduler.c:1113`: sleeping tasks retain a pointer in a sleep queue. Setting
  `wake_time=0` in the kill path does not detach that pointer before retirement.
* Direct terminal writers also exist in `task_exit`, quota handling,
  `arch/x86_64/interrupts.c:63` and `drivers/vbus_fs_cmds.c:442`. Quota handling
  is currently unwired; this is not evidence of a timer-triggered quota kill.
* VBus EXEC passes stack-owned request state to a child and can return after a
  timeout/direct ZOMBIE publication. Target quiescence and request lifetime must
  be resolved together; a fixed number of yields is not an acknowledgement.

## Rejected shortcuts

1. Adding a cancellation-depth counter around the drain while leaving direct
   ZOMBIE/DEAD writers unchanged does not protect the continuation.
2. Preventing all CANCEL_PENDING tasks from being scheduled deadlocks cleanup
   when the pending target is suspended inside a callback.
3. `active[cpu] => current[cpu] owns the guard` is false when a callback blocks.
   Ownership belongs to a continuation token that survives context switches.
4. Scanning `current[]` under the scheduler lock is not sufficient for safe
   freeing: the actual handoff happens after that lock is released. A switch
   handoff/acknowledgement from the incoming stack is required as well.
5. Per-CPU workers alone do not fix remote termination, stack lifetime or
   global monitor-state races. They also require task/stack reservation and
   serialization of shared reclamation lists.

## Required implementation unit

The merge must address these coupled responsibilities, with an explicit
lock-order and stack-handoff protocol before code is accepted:

1. Separate cancellation request from terminal commit. Publish reason/status
   with the phase in one synchronized claim; external callers use a pinned
   lookup or request-by-ID under the task-table lock, not an unowned pointer.
2. Only a valid target/owner checkpoint may commit a running target's exit.
   Cover syscall returns, interrupt returns to user mode, blocked kernel
   continuations, natural exit and fatal fault. Pending cancellation cannot
   send execution back into user payload, but must allow kernel cleanup to
   finish. A pending task may need to remain blocked until a real resource
   event permits safe continuation; wakeup cannot bypass a lock/wait contract.
3. Make run/wait/sleep membership, handoff, terminal commit and retirement
   cooperate. Prevent a task from being selected on another CPU before its
   outgoing context is saved; do not free it until a published handoff ack
   proves it has left every CPU stack. Define lifetime of lazy FPU ownership
   and CPU-local entry state as part of migration.
4. Route all terminal writers through the protocol, make retirement single
   owner, detach queue memberships, and preserve parent/child-TID/SHM side
   effects exactly once. Internal destruction must distinguish unpublished
   construction failures from live or retired tasks.
5. Define ownership of deferred callbacks: either temporarily pin a guarded
   continuation, or use a reserved non-cancellable kernel worker with a
   race-free wakeup protocol. The single-worker option avoids running global
   housekeeping on user stacks but does not remove items 1–4. This choice is
   still under review; neither option is merged.
6. Replace timeout-based teardown in VBus/APPKILL/kill-all with actual target
   completion acknowledgement and owned request storage.

## Invariants for review

* A deferred owner token keeps its task and detached work alive even while
  another task is current. Release the token and return/finish local work
  before an outer cancellation checkpoint can abandon the continuation.
* A guarded CANCEL_PENDING continuation may resume to finish cleanup, on its
  authorized CPU. COMMITTING and later states cannot re-enter user execution.
* Pin establishment and scheduler eligibility must be synchronized; separate
  guard-depth/CPU writes cannot leave a preemption or migration window.
* No terminal storage reclamation precedes queue detachment and switch ack.
  At-most-once terminal side effects do not by themselves imply this property.
* No-return self-exit inside protected cleanup requires a defined unwind
  contract. A debug assertion alone is not production cancellation support.

## Acceptance evidence required

Use actual-code host tests and deterministic interleaving tests for
enter-before-kill/kill-before-enter, nested and blocked cleanup, two killers
versus natural exit, sleep/wake/cancel, duplicate retire/reap and stack handoff.
Test forced pauses after publication of `next` but before switching stacks;
another CPU must neither resume nor free the outgoing task. Preserve failed
runs and use mutation controls for early free, late guard release and direct
terminal bypasses. An abstract state-machine proof supplements those tests;
it does not prove equivalence to the compiled kernel.

Guest acceptance must exercise 1/2/4 vCPU, real simultaneous user workloads,
kill while a syscall/deferred callback is blocked, all user-return paths,
fault/clone/exit and VBus timeout cleanup. Preserve BIOS/UEFI boot regressions,
but do not count the existing four boot observations as this acceptance.

Next implementation decision: settle the scheduler handoff and termination
protocol jointly with the deferred-work owner model; then implement and test
the complete unit above. Do not reconnect AI quota/reclaim by bypassing it.
