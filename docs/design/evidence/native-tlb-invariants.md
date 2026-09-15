# Native TLB range and acknowledgement invariants

Read-only review of `kernel/src/mm/vmm.c` and all C/header callers on 2026-09-15. Root owns implementation. These findings and tests are acceptance proposals, not a claim of successful remote revocation.

## Existing algorithm findings

Both public range functions and the IPI handler invalidate one address per 2 MiB. That does not cover independent 4 KiB translations. This is an actual caller mismatch: AI context dirty-bit clearing supplies `context_page_count * 4096`, whereas DMA paths generally request huge-page-sized ranges. An unaligned range crossing a 4 KiB boundary also needs both intersecting translations invalidated. `base + size` and repeated addition can overflow; a high final page can wrap the iterator.

The shared acknowledgement counter records neither CPU identity nor request generation. Duplicate or delayed IPIs after timeout can contribute to a later request. The handler reads mutable globals without acquiring a request snapshot; serializing senders does not serialize a handler already running after a timed-out sender releases the lock. A generation tag alone is insufficient unless the acknowledged generation is the same immutable request whose range was actually invalidated.

The checked variant currently has no C callers. The void function warns on timeout and returns; DMA permission restoration/rollback continues and AI context clearing clears its dirty bitmap. Thus timeout is not propagated into those consumers' success/failure contract. Failure must prevent reliance on revoked permissions or completed dirty tracking, and pages must not be freed/reused based on a failed barrier.

Caller context also matters: a CPU spinning for the sender lock with interrupts disabled cannot service another CPU's pending shootdown. A local interrupt caller can attempt recursive acquisition. Define allowable interrupt context and a bounded fail-closed contention protocol rather than assuming every lock holder can receive an IPI. CPU-count arithmetic is not a substitute for identifying actual started target CPUs.

## Exact range contract

For nonempty half-open input `[base, base+size)`, first validate that the last byte `base + size - 1` is representable and the range is canonical/supported. Let `first = floor(base/4096)*4096` and `last = floor((base+size-1)/4096)*4096`. Every page in `{first + 4096*k | 0 <= k <= (last-first)/4096}` must be invalidated. Iterate with a count or terminate before advancing beyond `last`, so the top page cannot wrap. Define zero size explicitly as a no-op. Rejected ranges must cause no partially published request or stale success flag.

## Request/acknowledgement contract

A request binds a nonreused generation, immutable normalized range, initiating CPU and target set. PTE writes precede release publication; each target acquires that request, invalidates its entire range, then release-publishes completion for that same generation and CPU. The sender acquires completions and succeeds only when every target has acknowledged once. Duplicate, unknown-CPU, non-target, stale and future acknowledgements cannot complete it. Generation rollover must either be impossible in the supported lifetime or fail closed. CPU hotplug, address-space identities, PCID/global mappings and remote inactive address spaces need explicit scope; this primitive alone does not solve those problems.

Timeout/lock contention leaves an explicit failure. Later requests cannot reinterpret outstanding earlier acknowledgements as their own. Handler completion cannot observe a torn or replaced range. The sender must not report success before its own invalidations and all target completions are ordered after the relevant page-table changes.

## Meaningful tests of actual C implementation

Compile the actual production range/request helper C into a host harness with narrow injected callbacks for local invalidation, topology, IPI delivery and bounded polling. Do not rewrite the algorithm in Python and call that an implementation test. Keep helper code shared with the native build, verify it is linked there, and separately exercise the interrupt integration in QEMU.

- Record exact invalidation addresses for one byte, aligned 4 KiB, unaligned two-page spans, 8 KiB, 2 MiB plus 4 KiB, zero length, the highest supported page and overflow/noncanonical inputs. Assert exact sequence/count, not merely a nonzero flush count.
- Feed a scripted two/four-CPU topology with sparse APIC IDs, self exclusion and offline CPUs. Deliver acknowledgements in different orders; assert success only after all actual targets.
- Inject duplicate delivery, stale request completion, wrong CPU, delayed handler after timeout and a later request. Verify none can create false success; verify each handler's invalidated range corresponds to its acknowledged generation.
- Exercise lock contention and caller interrupt-state rejection without unbounded host waits. Ensure request state remains reusable only under its defined failure recovery rules.
- Make every target fail individually and assert timeout propagates through actual checked callers or their explicit fail-closed wrapper; verify dirty bitmaps/success statuses are not committed after failed barriers.
- Use a native diagnostic to warm more than one 4 KiB translation on a proven AP, revoke/change translations on the initiator, wait for the actual barrier and challenge the AP afterward. Correlate CPU identity, generation and expected accesses/faults. This is a separate integration gate, not implied by host stubs or CPU online markers.

## Scope

Host helper tests establish arithmetic and protocol behavior under injected schedules; QEMU integration establishes only observed hardware/interrupt cases. Neither proves shared-address-space COW, arbitrary concurrent page-table mutation, general scheduler safety, PCID lifecycle correctness, fairness or universal SMP isolation.

## Actual C protocol host test result

Implemented `tests/native/tlb_shootdown_host.c` as architecture-hook stubs and `scripts/test_tlb_shootdown.py` as a compiler/process driver. The production `kernel/src/mm/tlb_shootdown.c` is compiled separately into the test executable with C11 and `-Wall -Wextra -Werror`; the protocol is not duplicated in Python. Twelve fresh-process scenarios pass: empty, unaligned cross-page, highest canonical byte, arithmetic overflow, canonical-hole crossing, sparse started targets, duplicate same-CPU delivery, timeout followed by late delivery and permanent poison, nested busy, pre-SMP local-only, duplicate APIC rejection and prior-generation acknowledgement rejection.

The revised protocol uses full context invalidation rather than range stepping, eliminating range-descriptor replacement during a handler. Per-CPU generation completions distinguish targets; timeout permanently poisons reuse; busy callers fail rather than spin with IRQs disabled. Review found no additional core protocol blocker under stable topology and correct architecture hooks. The test driver reports one unittest method containing twelve scenario subtests; this must not be described as twelve hardware tests.

This reviewer authored the host tests but not the production protocol. Architecture flushing, interrupt delivery/EOI and address-space lifetime remain outside these stubbed tests and require separate native and independent security review. Previous findings above describe the superseded algorithm and caller risks; they are not automatically resolved merely by these core protocol tests.

## Native probe observer authored

Added `scripts/native_tlb_smoke.py`, composing the existing strict SMP observer with exact native TLB probe checks. For every requested CPU it requires one warm and one remap record, hardware APIC matching kernel topology, before values 17/34, warm after values 17/34 and remap after values 17/51. All topology records precede all warm records, which precede all remap records, completion and the user SMP parent. Unchanged first-page values are mandatory; the second page must exhibit stale-before and updated-after behavior.

Six observer test methods pass, covering every missing probe record, skipped-flush stale results, corrupted values/APICs, phase reordering, duplicate/truncated/failure records and preservation of underlying SMP failure. The reviewer authored this observer; independent review and actual positive/skip-flush VM evidence remain separate. The proposed native gate concerns two observed kernel mappings on each CPU and does not establish general shared-address-space or memory-lifetime correctness.
