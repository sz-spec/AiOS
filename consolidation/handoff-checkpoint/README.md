# Scheduler handoff candidate — not approved for integration

Base: `b2b4b5dc4ac58950cf4baa8b8d05ec46e1a45b15`.
Working branch: `integration/scheduler-handoff`.
Canonical `main` is unchanged. This is an implementation checkpoint, not a
release or a declaration that cancellation/task lifetime is safe.

## Implemented in this candidate

* Reserve incoming execution under the scheduler lock. Outgoing ownership
  survives publication of `current=next` until assembly acknowledges the
  actual stack transfer. All builds enforce reservation in task selection;
  the test-only immutable CPU assignment remains a separate restriction.
* Acknowledge from the incoming stack using the per-CPU outgoing token;
  release the outgoing reservation as the final access to that task.
* Cover BSP first dispatch and AP bootstrap idle identity. Remove the unused
  public `set_current` bypass and reset new fields on fork/clone copies.
* Use a separate reaper link, prevent duplicate retirement publication,
  check scheduler/FPU ownership before claiming a retired task, and prevent
  direct destruction from bypassing the reaper.
* Save/detach lazy FPU state on the outgoing CPU. Remote reclamation cannot
  clear another CPU's FPU-owner slot as a substitute for a handoff.
* New ordinary task entries enable IF after acknowledgement and use the
  SysV call alignment. `task_destroy` reports invalid live-task destruction
  instead of silently succeeding; construction failure cleanup is separate.

## Evidence and limits

The production kernel builds, and the repository's `make native` entry
builds its own vendored Limine and produces an ISO in this worktree. An
earlier direct `make -C kernel ... iso` failed because this fresh worktree had
no generated Limine; that failure is preserved rather than attributed to a
kernel bug. These are incremental builds, not independent reproducibility
acceptance. The ISO is a diagnostic candidate, not the canonical release.

The host gate and eleven focused tests passed. Tests execute actual extracted
selection/reservation/ack/FPU helpers with ASan/UBSan, and preserve the existing
wait/deferred/queue contracts. Hardware FPU instructions are mocked there;
these tests are not a native SIMD correctness or concurrent-SMP proof.

The freestanding assembly guest links the actual `context.S`, switches real
stacks, and observes acknowledgement on the incoming stack. A control that
moves acknowledgement before `mov rsp` fails. The callback body is a stack
oracle; it does not substitute for the scheduler's ownership tests. Initial
assembly evidence predates the later ordinary-entry IF/alignment change;
later runs must be identified by their own context hash.

Initial fixture failures and subsequent corrections are preserved. The
initial-placement fixture now also rejects ZOMBIE/DEAD/reaper-claimed tasks;
the destruction checks follow `destroy -> defer -> claimed reaper` instead
of incorrectly requiring synchronous cleanup in the wrapper.

## Review blockers — still open

1. **Terminal transition versus selection.** Existing kill/quota/fault/VBus
   writers can publish a terminal state without the scheduler lock. A target
   can change state between eligibility and reservation, or after reservation.
   A common lifecycle protocol and safe target-side checkpoints are required.
2. **Abandoned continuation.** An off-CPU kernel continuation may still own a
   transaction. CPU reservation zero does not authorize discarding its stack.
   The old kill path can also abandon the caller during retirement publication
   after `retirement_started=1`. The new once marker is not a recoverable
   publication protocol until those cancellation semantics are implemented.
3. **Borrowed references.** A sleeper removed under `g_sleep_lock` is still
   used after unlocking; removing it from the list does not end that borrow.
   Task lookups and parent/child relationships also need explicit pin/ref or
   quiescence protection. Reaper SENTINEL alone does not protect those readers.
4. **Optional XSAVE path.** `VOS3_HW_XSAVE` still has a second state buffer and
   TS ordering inconsistent with the lazy-FPU path. It must use one coherent
   xstate model; a default build pass does not qualify this option.
5. **End-to-end native acceptance.** Full vOS boot/user workloads, real SMP
   interleavings, kill during blocked syscalls, wake/retire races and SIMD
   state migration need dedicated tests. The freestanding ISA guest does not
   provide those results.

The security review blocks merging this candidate. The narrower mathematical
review accepts the reservation/assembly handoff protocol under its stated
assumptions; it does not override the lifetime, terminal-state or XSAVE
blockers above. No source disposition is closed by this checkpoint.

## Continue here

Use `consolidation/cancellation-integration-plan.md` for the full required
unit. Build on this worktree; do not restart the reservation/assembly work.
Resolve terminal publication/checkpoints and borrower lifetime together,
then unify xstate and repeat both host and native tests. Only after those
reviews and acceptance evidence should this branch be integrated into main.

## Trampoline checkpoint — 2026-09-19

The updated real-stack guest passes with production context.S hash
`0527bbd938963c052477e460aa48f8dc771d973804627bde4b2aa007ff326cf2`.
It also enters the actual ordinary task trampoline and checks IF=1, raw
SysV entry RSP mod 16 = 8, argument preservation, incoming-stack ack and
return through task_exit(0). Single mutations restoring the old sub-8 or
removing STI both fail (QEMU debug exit 165); the positive guest exits 33.
The PIC is masked by the fixture before enabling IF. These results qualify
only the fixture paths; they are not interrupt-delivery or full-OS acceptance.
Logs including negative controls are archived under trampoline-* here.

This checkpoint does not close any review blocker or source disposition.
