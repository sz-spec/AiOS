# Native test repair investigation — 2026-09-17

This is a failing-suite investigation, not final OS qualification. Source commit
`374ccaa3615d7e93ecd0055c92d403e9a2843cee` contains the contiguous PMM ownership fix,
benchmark arithmetic/wait-status fixes, and strict test runners. Hosted changes
are tracked in the sibling backend and Node reports.

## Reproduced defects and repairs

The multi-page path in `vos3_pmm_alloc_pages` claimed bitmap entries and updated
allocation statistics but left their reference counts at zero. `vos3_pmm_free`
delegates to reference decrement; zero references prevented bitmap release.
The original full guest run lost exactly 132,992 pages over 2,078 allocations of
64 pages each. This equality supports the diagnosis; it does not explain every
other memory or task-lifetime failure in the suite.

The repair initializes each claimed page's reference to one after the entire
contiguous claim succeeds, before unlocking and returning the address. Failed
partial claims leave competing owners' reference counts untouched. The extracted
production-function regression failed before the repair and passes afterward. It
checks allocations of 2 through 64 pages, zero-fill, exact reference/bitmap/free
accounting, a competing claim followed by retry, and exhaustion. Atomic/lock
operations are deterministic host hooks; this is not a weak-memory SMP proof.
Independent review was provided by `/root/math_build_review`.

A gated native boot control allocates and frees 64 real pages, verifies every
initial/final reference and exact free-page accounting, and emits the required
unique `[PMM-CONTIGUOUS]` marker. It runs before SMP/scheduler startup with local
interrupts excluded. The memory observer retains all previous required records.

The frontier benchmark also multiplied signed integers before widening them.
UBSan reproduced overflow at agent index 4. Unsigned 32-bit multiplication now
preserves the fixed upper tags. A regression extracts the actual assignments,
runs UBSan, compares independently calculated values, and checks uniqueness for
all 32 agents. The odd low multiplier and even high multiplier have respective
periods 2^32 and 2^31, both exceeding the tested agent count.

The init benchmark launcher now rejects unsuccessful `waitpid` results before
reading status. The native runner builds through `make native`, boots the Limine
ISO with `-cdrom`, requires the full ordered program list and successful exit
records, rejects failure diagnostics/timeouts, and cleans up only its owned
QEMU child. Missing tools are failures. Supplied `--iso` bytes are hashed, but
that option alone does not establish build provenance. The aggregate runs each
layer once and labels explicit partial selections. Independent review is in the
Node report.

## Observed evidence

* The baseline full BENCH_MODE run failed, reaching its 500-second timeout in
  `bench_ai_scale`. Its result and serial log are preserved in
  `test-repair-native-2026-09-17/`. ISO SHA-256:
  `a29a5984d320c983ae74490325502d32ed64a524b283c27e7813c54c2b6f0559`.
* The post-PMM guest reached 2,814 soak iterations with **zero lost pages**.
  Other failures remain, including SHM/fork contracts, task/zombie cleanup,
  signals, agent quotas and huge-page workloads. This is not a full-suite pass.
* The swarm benchmark waited on all 32 slots even when only 20 children were
  created. The wait now covers successfully created children only; all 32 clone
  attempts and the final requirement for 32 successes are preserved. Its actual
  function regression fails before the fix and passes afterward for 0/20/32
  created children; 0 and 20 still produce a failed benchmark.
* With this wait repair, the complete **55-program** workload reaches its final
  halt. **15 programs return nonzero** and the runner correctly exits 1; there
  are also explicit failure diagnostics, including signal cases whose enclosing
  program exits zero. Therefore even the other 40 zero exits are not a claim of
  40 semantically passing programs. The complete result/serial log is preserved.
  Final full-suite ISO SHA-256:
  `88dfc7b8a34db45e2059524ad15cfdd08d4d6f23c4885641b4d2554e485d4660`.
* All four clean-source matrices for commit `374ccaa` passed: memory, normal
  boot, TLB, and isolation, each BIOS/UEFI × 1/4 CPU, **16 boots total**.
  All 5,887 archived source files remained
  unchanged; generated outputs were absent initially. ISO SHA-256:
  `854fb4bfeb473c71f04480aeefb25bcb363c365a0be36b3e8980660be3a0d1f9`.
  Other image hashes and exact configurations are in each preserved result.
* Final script discovery passed **66 tests**. The earlier
  focused PMM/frontier/memory-observer/aggregate run passed 19 methods using
  `PYTHONPATH=scripts python3 -m unittest test_unified_runner test_native_memory_smoke test_pmm_contiguous test_frontier_patterns`.
  An earlier module-style invocation omitted that path and failed import; it is
  retained as a harness invocation error, not a passing check.

The final benchmark image was incrementally rebuilt in the isolated post-PMM
build directory; it is not represented as a clean-source qualification. Its
native source changes are contained in `374ccaa` and `4e84ddf`. The memory matrix
qualifies `374ccaa` specifically, before the later benchmark wait-only change.

Full benchmark images use the pinned builder
`sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6`,
`SOURCE_DATE_EPOCH=1700000000`, isolated build directories, `BENCH_MODE=1`, and
`HEADLESS_AUDIT=1`. QEMU uses q35/TCG, one CPU and 3 GiB for this legacy suite.
The clean diagnostic matrix uses its recorded separate configuration. Performance
thresholds under concurrent emulation/build load are not physical-PC benchmarks.

## Open integration boundaries

The refreshed eleven-source audit is preserved separately in this evidence
directory. Retained/missing byte counts are not proof of semantic preservation.
Missing native capability assertions remain enabled; the implementation plan
identifies absent APIs without adding unused stubs to satisfy string tests.

Default desktop `cargo test --locked --offline` still fails during Tauri build:
`binaries/vos-backend-aarch64-apple-darwin` is absent. No placeholder executable,
asset suppression, or `TAURI_CONFIG` bundle override was used. Shipping a real
sidecar and complete application assets remains required.

The full corrected project suite, physical hardware support, unrestricted SMP
isolation, and byte-identical ISO reproduction remain unqualified.

Related evidence: [backend repairs](test-repair-backend-2026-09-17.md),
[exact-node backend inventory](test-repair-backend-2026-09-17-inventory.json),
and [Node repairs/review](test-repair-node-2026-09-17.md).
All native artifacts have a SHA-256 manifest. Secret scanning of the initial
portable evidence reported 142 candidates, all manifest hash values; each was
independently recomputed from the archived source or corresponding evidence
file. No scanner suppression was added.
