# Independent TLB and shared-address-space security review

Scope: current VMM flush APIs/call sites and shared-address-space lifetime, before the next corrective stage. Read-only source review; root owns implementation and runtime work. No universal memory-safety claim follows from prior AP execution.

## Concrete blockers

- Both flush-range APIs and their IPI handler iterate in 2 MiB steps. This may suit their original huge-page callers, but does not invalidate every ordinary 4 KiB mapping in an arbitrary range. Arithmetic and alignment validation are absent.
- Request base/size and one acknowledgement counter are global. There is no per-CPU acknowledgement identity or generation. After timeout the descriptor may be reused while old IPIs/handlers remain outstanding; late/duplicate acknowledgements can be attributed to a later operation. Counting acknowledgements alone cannot prove every target invalidated the current request.
- Global flush-lock acquisition has no documented local interrupt/preemption contract. Same-CPU interrupt reentry can deadlock; multiple CPUs waiting with interrupts disabled can prevent each other's required acknowledgements. The IPI handler must not acquire a lock held by its requester.
- Handler INVLPG operates on the currently active address context. No address-space/PCID identity, inactive-context epoch, or context-switch catch-up establishes revocation for other translations. KPTI/user-versus-kernel roots and global mappings must be included in the selected contract rather than assumed equivalent.
- The void API logs a timeout and returns; `dma_warp.c` and `ai_slots.c` call that API. This is not fail-closed completion. The checked API returns failure, but callers need an explicit rule forbidding physical reuse or successful revocation after a missing acknowledgement.
- mprotect, ordinary unmap and COW continue to use local INVLPG. A correct standalone shootdown primitive would not automatically secure these mutation paths.
- `CLONE_VM` copies the address-space pointer without retaining it. Reaping a non-thread parent destroys its address space even if sharing children remain alive; skipping destruction for thread tasks does not solve ownership. Fixed CPU affinity prevents migration, not parent-before-child lifetime failure or shared metadata races.

## Smallest independently testable corrective stage

First qualify a narrowly defined kernel mapping shared across the participating CPUs, using ordinary 4 KiB pages and the existing gated AP scheduler. Specify checked range arithmetic, exact online/started target set, serialized request publication, per-CPU generation-bound completion, and a timeout rule that keeps affected pages allocated/quarantined and reports failure. Ensure the responder can execute without waiting on requester-held locks; a missing response must never be counted through a stale acknowledgement. A poisoned/non-reusable request after timeout is safer than silently recycling a live descriptor without a retirement protocol.

Use actual hardware CPU identity: warm distinct 4 KiB translations on a remote CPU, coordinate the mutation and acknowledgement, then require the remote observer to see the new mapping/permission. Include two adjacent pages so a 2 MiB-stride bug cannot pass. Add an intentionally withheld acknowledgement and require failure/no frame reuse, plus a subsequent-request test for stale acknowledgement contamination. The oracle must distinguish transport acknowledgement from actual translation change. Do not claim shared user-address-space safety from a kernel-mapping probe.

Before shared-VM user revocation is enabled, separately establish balanced address-space retain/release across clone, exit, exec and failure unwinding; serialize VMA/PTE/COW mutations; define CPU-context tracking and inactive-root invalidation; and prevent physical reuse until the applicable acknowledgements complete. A parent-exits-first/live-child regression is a concrete first lifetime test.

## Status

Findings and bounded stage recommendation sent to root. No new implementation or runtime qualification reviewed yet. Prior sequential memory tests and AP CPUID checkpoints do not close these blockers.

## Review of proposed bounded replacement

Root proposes validated canonical ranges, full local flushes, per-CPU generation acknowledgements, started-target snapshots, nonblocking requester locking, permanent poison after timeout and a panic-on-error legacy void wrapper. This is a suitable bounded primitive design, provided responders acquire the published generation, complete their flush before release-acknowledging that same generation, and timeout poison prevents descriptor reuse. Busy failures must not mutate another active request. Target topology must remain stable during completion.

Independently checked the supplied [Intel SDM Volume 3A](https://cdrdv2-public.intel.com/835754/253668-sdm-vol-3a.pdf), section 5.10.4.1 in this edition, page 156: changing CR4.PGE invalidates TLB/global and paging-structure cached translations across PCIDs. The sequence must actually toggle the bit in either initial state and restore original CR4; clearing an already-clear bit is insufficient. Preserve interrupt state and require architectural PGE support. This hardware contract avoids the old range stride and inactive-PCID holes at the cost of broader invalidation.

A native init test that receives AP acknowledgements establishes transport/handler completion, not direct observation of stale translation eviction. Initial host fault-injection tests can qualify failure handling but must not be described as native timeout or remote memory-reuse evidence. Shared-VM lifetime and mutation integration remain separate gates.

## Initial implementation/probe review

Independently inspected the actual protocol, architecture adapter, VMM wrappers and native two-page probe. Per-CPU generation acquire/release acknowledgement, nonblocking requester acquisition and sticky poison address stale-count/reuse hazards. The architecture adapter actually toggles/restores PGE with a compiler memory barrier; handlers complete invalidation before acknowledging. The legacy void wrapper panics on error instead of silently continuing.

The native probe warms two adjacent global 4 KiB aliases on every participating CPU, atomically replaces only the second PTE without local INVLPG, and requires old 17/34 values immediately before the flush and new 17/51 afterward. It retains every physical frame through acknowledged cleanup. This is stronger than acknowledgement-only evidence. The skip-flush control still depends on observing stale translations: hardware is permitted to evict spontaneously, so a non-stale baseline would be inconclusive rather than proof that invalidation was unnecessary.

Independent host runs passed: the actual-C protocol harness (one Python test executing the supplied twelve scenarios) and six observer tests. No VM result reviewed yet. One concrete remaining bound issue was reported: the architecture send delegates to two unbounded LAPIC ICR-pending waits. A bounded acknowledgement loop does not bound the complete call. Recommended checked send with failure propagation/poison after publication before claiming bounded failure handling. Stable CPU topology and early-boot local-only operation remain explicit assumptions.

## Delivery-bound follow-up and adjacent PTE finding

Independently reviewed checked LAPIC delivery: destination/vector/nonzero budget are validated, both pre-send and post-send ICR-pending waits are bounded, status propagates through the architecture adapter, and any delivery failure permanently poisons the protocol before releasing requester ownership. The actual-C host harness passes again. This closes the unbounded ICR-wait finding for this primitive; budgets count iterations per target rather than providing a hard wall-clock deadline. NMI interaction with ICR programming remains outside the ordinary interrupt-exclusion assumption.

Confirmed an adjacent existing PTE-bit collision: `VMM_PTE_COGNITIVE_BIT` in vmm.c and `VOS3_PTE_COW` in vmm.h both use bit 52. Setting a cognitive marker therefore also sets the COW marker; querying cognitive state can classify ordinary COW pages as cognitive, and COW/mprotect clearing can erase cognitive metadata. This requires a separate bit-allocation/policy correction and regression. The isolated kernel-alias TLB probe does not exercise that combination, so its result cannot qualify general COW/cognitive compatibility. No adjacent production changes were made by this reviewer.
