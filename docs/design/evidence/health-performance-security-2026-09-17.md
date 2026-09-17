# Health performance: scoped security review

Reviewer: `/root/mcp_upgrade`, 2026-09-17. Read-only source review of the final two C deltas; this report is the only file written in this review. No hashing, tests, build or VM execution was performed while the coordinator's measurement was running.

**Authorship boundary:** this reviewer authored the scheduler empty-path change and its focused test, so the scheduler discussion below is an implementation self-check, not independent approval of that author's code. The coordinator and mathematical reviewer provide its independent review. The root-loop change was authored separately and is independently reviewed here. This distinction prevents treating one author as two independent reviewers.

## Accepted bounded semantics

`kernel/src/sched/scheduler.c:915–930` adds an acquire-load of the selected CPU's pending bit before the active-guard exchange. CPU bounds and the existing IRQ-enabled precondition remain. Reading zero consumes no publication: a producer that sets pending after that observation leaves the bit for a subsequent safe point. Reading nonzero proceeds through the unchanged active guard and pending exchange. A recursive invocation cannot clear pending while another drain owns the guard. Work published after the consuming exchange remains pending for a later drain. The change does not skip signal delivery, subsystem cleanup with consumed pending work, or owner-local scheduling policy.

This argument assumes the existing fixed-owner/no-live-migration contract and recurring safe points. It is not a bounded-latency or arbitrary SMP progress proof. Remote publication still requires the owning CPU to reach a safe point. Release/acquire operations do not themselves wake a CPU or repair abandoned cleanup ownership.

`kernel/src/arch/x86_64/kpti_roots.c:13–20` splits the existing synchronization into copy 0–256, zero 257–510, and copy 511. The partitions contain 257 + 254 + 1 entries: every destination entry is still written, and the same 258 full-root entries are read. Desired values and increasing-index order are equivalent in abstract C. Null and overlapping-storage rejection is unchanged. Forbidden upper entries remain zero; copied permissions, mapping removals and Accessed bits are not masked or skipped. The caller's AS lock, lifetime checks, root binding and CR3/flush behavior are unchanged. No generation shortcut, dirty-root assumption or new mapping permission is introduced. Compiler lowering may differ; this review does not assert machine-level instruction-order identity.

No blocker specific to the root-loop delta was identified. The scheduler self-check likewise identifies no new publication-loss path under its stated contract; independent review of that authored change must remain separately attributed.

## Validation status at this review

The coordinator reports 107 host tests passed and 27 KPTI tests passed. The pending-load-only native full suite reportedly completed 57 programs successfully, with health at 91,225 messages/s; the adjacent original sample measured 74,227/s. Those are individual observations, not a distribution or a universal speedup estimate. The original health workload and 80,000 messages/s threshold remain unchanged. The combined final BIOS measurement is still running at the time of this report; combined performance approval is therefore pending. This reviewer did not independently execute or rehash these runs in this turn.

The previously attempted compare-before-store candidate failed its full-suite health gate at 34,970/s and was reverted. It is not part of these accepted source deltas. Root-loop splitting still performs all original stores.

## Remaining safety and interpretation limits

- Asynchronous cancellation can orphan transient context pins, detached retirement batches or the existing per-CPU deferred-active guard. The early pending read does not close that blocker.
- The preexisting SYSINFO 32-byte user/40-byte kernel layout mismatch is present in old and current versions. The coordinator reports the health program's extra written bytes land in padding; that observation is not general ABI safety or a fix. Track the mismatch separately rather than explaining this optimization with it.
- KPTI remains explicitly partial: the retained kernel/direct-map entries are unchanged. Full isolation, concurrent region mutation, remote task stopping and physical hardware are not qualified here.
- Keep MMIO denied, retain all ownership and permission checks, and preserve CR3/TLB invalidation. Neither source change authorizes weakening these boundaries.
- A passed sample does not identify the entire historical slowdown mechanism. Final performance conclusions require the coordinator's frozen-source evidence and the original benchmark, with failed samples retained.

## Coordinator outcome after this source review

All four final full-suite runs passed, with 57 zero exits each; see the [qualification report](health-performance-2026-09-17.md). This later runtime result does not expand the safety claims or assumptions in the review above.
