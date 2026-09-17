# Backend test repair evidence — 2026-09-17

Reviewer/implementer: `/root/mcp_upgrade`. Starting repository revision: `53b9a44`; final reviewed test-repair snapshot: `cd0e2362f43fff2f7cb42aba437d0391f2d98cff`. This is not a clean release qualification. Scope is backend Python tests. Root owns billing; `/root/math_build_review` owns the native source-contract reviews and multi-agent provenance fixtures.

## Baseline and environment

The documented backend runner invokes `pytest tests/` with coverage for `api`, `core`, `middleware`, `services`, and `db`. The investigation used `.venv-upgrade/bin/python` (Python 3.12.13, pytest 9.1.1), four xdist workers, the existing load-file distribution, and an explicit 90-second test timeout. No test selection was excluded from the baseline.

The first attempt was interrupted after 52.61 seconds during buffered collection. This was a premature investigator interruption, not an established hang or application failure. The second attempt was allowed to finish: **180 failed, 9,802 passed, 277 skipped, 2 xfailed, 15 errors, and 9 subtests passed in 377.03 seconds**, exit 1. Log: `/private/tmp/vos-goal-backend-20260917-suite-r2.log`; summary: `/private/tmp/vos-goal-backend-20260917-failures.txt`. These counts precede the repairs and do not describe a passing suite.

The suite has a loopback-only network guard. Local POSIX shared memory, subprocess Seatbelt setup, and loopback server binding require permission unavailable in the execution sandbox. Elevated focused verification obtained **85 passing attestation tests** and **152 passing subprocess/P2P tests**. The latter run also had 13 fixture lookup errors. The same local-LLM fixtures passed separately (14) and with P2P (36); the broader collection interaction remains unresolved. These were local synthetic tests, not cloud authentication or physical hardware qualification.

## Repairs and their purpose

- DevMemory test factories now call the real constructor while mocking external model initialization, retaining locks and fallback state. The router contract asserts the existing documented kernel/local-first fallback order.
- Test startup directs keyring, SQLite and application-data defaults into a disposable process-local temporary directory before importing application singletons. It forces a local test keyring and an explicit test seed. PQC rotation tests reset the deliberate hard-failure latch between cases. Real developer keys must not be rotated by test runs.
- Rate-limit tests pass real HTTP responses to middleware. DNS pinning cases receive isolated firewall environment settings. Chat boundary tests mock the current dispatcher rather than an obsolete factory, preventing unintended cloud calls.
- Authenticated multi-user clients supply the current valid CSRF token; production enforcement remains intact. The missing-developer test supplies an explicitly absent repository result and asserts the queried identity.
- The JWT confusion test constructs an attacker HMAC token directly because current PyJWT correctly refuses to generate it. It invokes the actual production-mode verifier and requires HTTP 401. Firewall response tests require the generic client refusal and server-side reason/confidence logs, preserving the anti-oracle boundary.
- The unconditional 19-name CI hang/performance skip list was removed. Existing hardware/dependency skips and xfails remain visible and are not counted as passes.

All changes authored in this track at this point are test-side. Billing production changes and PRO wrapper corrections have separate authors/reviews. Multi-agent fixture repairs use real constructor/provenance signing, with negative unsigned/tampered cases; they do not replace verification with a mock.

## Focused evidence

| Run | Observed result |
| --- | --- |
| DevMemory/router fixtures | 128 passed, 1 terminal escalation failure, 12.54s |
| Isolated PQC rotation | 14 passed, 7.27s |
| DNS/rate-limit/terminal/chat fixtures | 94 passed, 9.10s |
| BOLA/developer fixtures | 72 passed, 8.46s |
| Root billing verification | 61 passed, reported by root |
| Provenance fixtures plus dedicated security tests | 125 passed, 11.36s, reported by math reviewer |
| Restored previously gated groups | 187 passed, 1 skipped, 1 timed-out worker/test; interrupted harness, 387.55s |
| Final combined focused run | 486 passed, 1 terminal escalation failure, 3 xfailed, 21.32s; `/private/tmp/vos-goal-backend-20260917-merged-focused-final.log` |
| Dedicated memory stress | PQC memory test passed; health-memory test failed; 749.81s, exit 1; `/private/tmp/vos-goal-backend-20260917-memory-stress.log` |

The restored run used session 73369. After a 90-second timeout in PQC memory stress, xdist reported a dead worker and replacement, then made no progress for over four minutes. Process inspection showed an original defunct worker and idle parent/replacement. Only then was it interrupted. The dedicated sequential rerun completed normally in 749.81 seconds with a 900-second per-test timeout, without reducing the existing workload or allocation threshold. The PQC memory test passed. The restored health-memory test failed: 5,558,827 retained bytes (5.30 MB) after 1,000 requests exceeded its 5 MiB limit. This newly exposed failure is outside the original 195-node inventory and remains open. The health-only diagnostic rerun reproduced 5,559,723 retained bytes in 15.08 seconds. Its largest sites are Pydantic fields/schema validators and FastAPI routing/dependency structures. A temporary pytest parametrizing plugin then ran the identical test twice in one process: the cold case failed, the warm case passed (22.54 seconds). No warm-up or threshold change was introduced in the repository test. This supports a one-time initialization cost but does not turn the original cold-case failure into a pass. Logs: `health-allocation.log` and `health-cold-warm-r2.log` under the same temporary evidence prefix. No live test process from this track remains. The legacy test name says 1,000 cycles, but its implementation actually performs two windows of 100; this discrepancy is retained explicitly rather than represented as 1,000 completed cycles.

## Exact-node failure inventory

[`test-repair-backend-2026-09-17-inventory.json`](test-repair-backend-2026-09-17-inventory.json) tracks all 195 initial failed/error node IDs against completed verbose reruns. **174 have an explicit PASS for the same node ID as their latest completed result; 21 remain open.** These are distinct node IDs, not sums of overlapping run counts. Interrupted or worker-crash runs do not resolve entries. Two original open-core tests were replaced/renamed when their contracts were corrected; the strict inventory keeps their original node IDs open despite the replacement suite passing. Baseline skips/xfails and newly exposed failures remain separate from this inventory.

## Remaining gates and limits

A full corrected-tree suite has not yet passed. The terminal streaming route still lacks tested escalation integration. Existing provider code also leaves its malformed counter unused and does not preserve the agent role in snapshot handoff; blindly adding imports or enabling escalation would not establish locality/tool-permission safety. This requires a bounded production contract fix.

Native source-contract failures include missing fine-tuning/VMM/dispatcher capabilities; they remain failures pending implementation, not weakened assertions. Root repaired the CORS test against the real app factory: the configured Tauri origin receives HTTP 200 with exact origin/credentials headers; an untrusted origin receives HTTP 400 without an allow-origin header. Its completed 10-test run passed in 8.03 seconds (`/private/tmp/vos-goal-backend-20260917-cors-final.log`). Remaining environment-dependent groups require appropriate local execution permissions. Existing xfails/skips need explicit disposition; the broad goal is not complete.

An untracked `backend/:memory:.ses` artifact was quarantined to `/private/tmp/vos-goal-backend-20260917-memory-artifact.ses`, mode 0600. Its payload was not printed. The exact producer remains unidentified; it recurred while multiple test processes were running, so the underlying path issue is not fixed. An audit-hook rerun of the combined focused set observed no Python `.ses` opens and did not change its modification time; concurrent or C-level creation remains possible. The recurrence was quarantined separately as `memory-artifact-r2.ses`. It must remain outside commits.

No universal operating-system security, physical device support, cloud service integration, or all-tests-green claim follows from these results.
