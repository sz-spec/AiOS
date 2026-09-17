# Native saturation cleanup — 2026-09-17

Author/collector: `/root/rust_upgrade`. This report records a bounded benchmark lifecycle repair, not whole-suite qualification. Production kernel behavior was not changed by this repair.

The earlier saturation benchmark deliberately waited for only 20 children. Its remaining 1,000 zombies were adopted by init and persisted into subsequent benchmarks. `test_sustained` already started with 1,000 zombies; its own fork/wait loop was not their source. The repair preserves the saturation/FPU/performance workload and waits for every owned PID, including entries after a failed spawn. Every successful wait must return that exact PID and raw exit status zero. Sweeps are bounded by 30 seconds and 100,000 iterations. Unexpected extra-child cleanup is checked too.

The first repaired run, revision `ddaea9ccd9d7533f4647dbd562ef3110d527e775`, reaped all 1,020 children without child errors and returned to four tasks/zero zombies, but immediately measured a 35,301-page deficit. It correctly failed the strengthened memory check. Task-table removal precedes deferred physical reclamation; later serial telemetry showed those pages returning. The initial cleanup predicate incorrectly treated logical removal alone as completion.

Final revision `b66ea5f2daa24fc13b748b0be94eba3f7d184438` waits for both baseline task/zombie counts and physical memory recovery. It retains the existing strict deficit below 2,000 pages, with a five-second/100,000-sweep bound, yielding while cleanup is pending. Failed telemetry fails closed; baseline and final syscall results are checked. The final native run reports 1,020 children spawned/reaped, zero child errors, four tasks, zero zombies and a **1,639-page deficit**. Saturation exits zero. This tolerance is not proof of exact zero leakage or allocator conservation.

## Complete-run result remains failing

Both preserved runs executed **56 benchmark programs** and reached suite completion; both classifier results are `passed: false`, with **13 nonzero exits each**. Final nonzero programs:

- `bench_fuzz_test` (139)
- `stress_thread`, `test_shm_dispatch`, `diag_shm_stress`, `test_sustained`, `bench_ai_throughput`, `bench_2026_frontier`, `test_agent_chaos`, `test_advanced_chaos`, `test_health_check`, `test_agent_cluster`, `bench_ai_scale`, `test_stress_mt` (1 each)

`test_stress_mt` passed in the intermediate run and failed in the final run; that variability is preserved, not classified as fixed. The final count remains equal to the prior signal-final failure count despite the saturation repair. Full-suite success is not claimed.

Final ISO SHA256: `f418af6631bec2b149b91de69912a8a6844065e3db5258dbc2bd2a71675f9ce9`.
Intermediate ISO SHA256: `0eb7c4fe643ab630f3ccb35159d217dfcae72c9914242dbd00fe02fdaf4a92dd`.
These are observed build artifacts; this BENCH_MODE execution does not itself establish a clean archived-source build or physical hardware qualification.

## Validation and portable evidence

The actual C helper regression compiles extracted production-test functions with deterministic wait/telemetry faults. Final host log records **3 tests passed**, covering full owned-child reaping, malformed/error wait results, physical versus logical readiness, timeout and failed telemetry. This validates the test's control logic, not native scheduler timing. x86_64 freestanding syntax checks also passed during development.

The preserved discovery log records **77 tests passed** before the final helper revision. The later three-test final host log is separate validation; the earlier 77-test run must not be described as a complete discovery rerun against the final revision.

Directory [native-saturation-cleanup-2026-09-17](native-saturation-cleanup-2026-09-17/) contains intermediate and final `serial.txt`/`result.json`, both corresponding build logs, `scripts-discovery.txt`, and `final-host.txt`. Serial logs are copied as raw bytes (including any control characters), not rewritten or sanitized. [manifest.json](native-saturation-cleanup-2026-09-17/manifest.json) records exact byte lengths and SHA256 for all eight evidence files. Intermediate failures are retained. The manifest does not hash itself or this narrative.
