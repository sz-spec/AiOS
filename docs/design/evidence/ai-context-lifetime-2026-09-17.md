# AI context retained-reader checkpoint

Date: 2026-09-17. Coordinator: `/root`; independent security and mathematical
reviewers: `/root/mcp_upgrade` and `/root/math_build_review`; diagnostic integration
and initial host validation: `/root/rust_upgrade`; native execution: `/root`.
This is a bounded development checkpoint, not release
approval or full process-isolation qualification.

Authorship boundary: `/root/mcp_upgrade` authored the external-caller conversions
and reviewed the coordinator-authored core independently; its caller tests and
implementation notes are not an independent audit of its own code. The coordinator
executed native validation; the mathematical reviewer independently reclassified
its logs and verified artifacts, as recorded below.

## Change and ownership contract

Context lookup and retain now share an IRQ-safe registry lock with publication
and detach. A creator or app slot owns one reference; the global pointer borrows
that owner. A successful lookup adds a reader reference before releasing the
lock. Closing removes all discoverable aliases before consuming the owner.
The last put enqueues retirement; region/page destruction occurs outside the
registry lock at an IRQ-enabled process continuation. No-current-task and
IRQ-disabled drains leave retirement pending. IF alone is not a general ISR
detector; callers must satisfy the process-context contract.

For normally completing readers, `references = owner + outstanding readers`.
Close sets the owner contribution to zero and prevents new acquisitions; a
held reader therefore prevents premature reclamation. The publication loser
of a competing create never becomes discoverable and retires once. Overflow
rejects acquisition; cross-app access checking denies when a present context
cannot be retained. This argument assumes valid reference ownership and eventual
completion. It is not a proof of region-list synchronization or cancellation.

Internal scans, telemetry, APPLIST and status/scrub paths use retained references.
Task teardown drops its persistent reference rather than closing the registry's
owner. The syscall wrappers reject app IDs outside 0–7 before narrowing them.
Those range checks do not grant or verify authority over a valid app ID.

APPLOAD explicitly returns ENOTSUP before any launch side effect. Its previous
stack request, early task publication and missing enqueue could not provide a
safe successful launch. APPSTAT, APPKILL, APPLOGS, APPLIST and AGENT_KILL_ALL remain
present; a frozen function fixture guards their intended preservation. Reopening
APPLOAD requires safe task construction/startup/reaping and administrator
authorization. See the [external-caller review](ai-context-external-callers-2026-09-17.md).

The same-lock lookup/retain discipline is consistent with the official
[Linux kref guidance](https://cdn.kernel.org/doc/html/latest/core-api/kref.html).
That is established design guidance, not a newly published 60-day forum result
or evidence that vOS implements Linux semantics. The recent engineering
discussions remain in the [council research report](expert-council-failures-60d-2026-09-17.md).

## Validation scope and frozen inputs

The frozen source contains 6,255 files from base commit `f8c0b82` plus the recorded
working-tree changes. Manifest SHA-256:
`ccf91c8de0c8bcc5e722ca82debec5beba4285e9ca5d33d9aa8e04728023f532`.
The [evidence directory](ai-context-lifetime-2026-09-17/) preserves the manifest,
tracked patch, test logs and cancellation diagnostic. Later documentation edits
are outside that build snapshot. Independent reconstruction from the base Git
archive, recorded patch and six preserved additions reproduced all 6,255 files,
with no missing/extra files or hash differences, without using the frozen tree.

The actual-source host regression exercises held readers across detach and slot
replacement, two racing creators, concurrent final puts, overflow, deferred
reclamation, two region metadata frees and exact counters. A mutation removing
retain compiles and then fails as required. IRQ/allocator/scheduler/region
fixtures are mocked; this is not production SMP or populated page-table teardown
qualification. External-caller tests additionally cover pin balance, unsupported
launch and full-width IDs 8, 263 and UINT64_MAX. The observer rejects missing,
malformed, duplicate or prematurely reported native completion.

Frozen host discovery passed **106 tests in 53.588 seconds** without a `.git`
directory. All 6,255 hashes still match after the run. `native-build-check` passed
its three groups (3 + 1 + 1 tests). Cross-compiler syntax checking passed all nine
changed production/diagnostic C translation units. Native build and boot results
are recorded below.

| Diagnostic firmware | CPUs requested / observed online | Context marker + subsequent userspace |
|---|---:|---|
| BIOS | 1 / 1 | PASS |
| BIOS | 4 / 4 | PASS |
| UEFI | 1 / 1 | PASS |
| UEFI | 4 / 4 | PASS |

Each guest ran alone for 45 seconds under QEMU 10.2.2 TCG; every ISO hash was
unchanged before/after. Diagnostic ISO SHA-256:
`ace20c9bd21ca44ac4733f3a3736f62470eea16ce5854a87e9f49776a27960c5`;
kernel SHA-256:
`2fb34f154c53c4e21a6cc0a1c37d91424520be89ab4f00b98b26557895249a6d`.
The test is gated by `AI_CONTEXT_LIFETIME_TEST=1 HEADLESS_AUDIT=1 BENCH_MODE=0`.
It performs controlled empty-context interleavings in the init task; four CPUs
online do not establish concurrent lifecycle execution or populated-region teardown.

The diagnostic build retains 108 warning-containing lines versus 110 in the
previous final3 BENCH build. Normalized compiler diagnostics introduced no new
warning; two unused init functions differ by mode. Existing ML-KEM buffer-size,
musl return-local-address, GNU-stack and ignored-linker-option diagnostics remain
open. The new build directory also triggered an 8.9-microsecond future-mtime and
clock-skew warning. These observations do not establish harmlessness or cause.
See the preserved [warning comparison](ai-context-lifetime-2026-09-17/warning-review.json).

Two fresh offline BENCH builds using image
`sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6`,
`SOURCE_DATE_EPOCH=1700000000`, `BENCH_MODE=1 HEADLESS_AUDIT=1` and identical
internal paths produced byte-identical ISO, kernel and six Limine artifacts.
All manifest-listed source files remained unchanged in both fresh trees.

- Full-suite ISO SHA-256: `154892f8d7f583462f6e5fba8e407fa88bf1e869653570a6d1090cd217100668`.
- Kernel SHA-256: `d8f697840e1dba640a46090dab31170d12167b8b62a1fb690d056809ebd36cfa`.

This repeats one pinned compiler environment on one host; it does not establish
independent compiler provenance.

| Full suite, one CPU | Completed / zero exits | Health messages / guest ms | Integer messages/s | Gate |
|---|---:|---:|---:|---|
| BIOS | 57 / 56 | 37,923 / 1,000 | 37,923 | FAIL |
| UEFI | 57 / 56 | 40,611 / 1,010 | 40,208 | FAIL |

`test_health_check` alone returned nonzero in each run; both completed without
timeout. The 80,000 messages/s threshold is unchanged. Both health probes report
four tasks, zero zombies and equal pushed/consumed counts. The UEFI rate is
integer division, not its raw message count. These two fresh single samples do
not establish variance, a causal performance fix or a regression magnitude.
The existing performance gate remains open. Guests ran sequentially after builds
completed, with no concurrent builds/VMs launched by this checkpoint; background
host activity is not proven absent. Preserve the [full logs and invocations](ai-context-lifetime-2026-09-17/full-suite/).

The independent [final artifact and oracle review](ai-context-lifetime-2026-09-17/final-independent-review.json)
rehashes all sixteen build artifacts, verifies 6,255 source hashes in each build
tree and reproduces both failed suite classifications. It also confirms the
health ELF is byte-identical to final3, SHA-256
`28cac8eb9131f7f9c9bcac756542f5eb3d6af79d51de51a200085b239aa7b56a`.

Copies of both tested ISOs and their hash manifest are in local
`dist/qualification-ai-context-20260917/`. They are diagnostic/full-suite images,
not a final release or an installation-qualified ISO. Their binaries are excluded
from the source commit; [artifact identities](ai-context-lifetime-2026-09-17/local-artifacts.json)
are preserved in the evidence packet.

## Blocking cancellation finding and remaining gates

The [independent cancellation review](ai-context-cancellation-review-2026-09-17.md)
reproduces one undiscoverable context with one abandoned reader reference and no
free. Its harness models abandonment rather than executing a real task kill;
the source audit establishes that kernel preemption and terminal task removal
can abandon that continuation even on a single CPU. Exit zero from that diagnostic
means the defect was reproduced, not that cancellation passed.

The broader cancellation defect predates this change. Reference tracking exposes
an additional leak if a reader never puts. A per-task pin ledger alone would not
recover an abandoned retirement batch, held locks or the per-CPU deferred-active
flag. Cancellation needs a complete safe-boundary or ownership-transfer policy
with proven stop/acknowledgement before cleanup. This remains the next blocking
safety stage; it is not an established cause of the independent health slowdown.

Also open: concurrent region removal/mutation; huge/shared backing finalization;
valid-ID authorization and per-principal active context; populated-context native
teardown; full SMP task stopping and remote memory revocation; physical hardware,
signed boot, installation and Windows coexistence. No MMIO or NPU permission is
re-enabled. The health acceptance threshold and workload remain unchanged.
