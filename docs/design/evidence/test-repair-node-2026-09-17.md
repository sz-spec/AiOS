# Node test repairs and independent runner review — 2026-09-17

Reviewer/implementer: `/root/rust_upgrade`, one actual agent. Node edits below are author validation; the native runner, aggregate runner and billing changes were authored by other agents and reviewed independently. This is a bounded review, not an exhaustive application or OS security audit. Commands used the installed Node 26.8.2 toolchain by prepending `/private/tmp/vos5-node-current/node_modules/.bin` to PATH.

## Repairs and measured results

| Component | Commands / outcome |
| --- | --- |
| Frontend | `npm test`: initially 216 tests/18 files; after selector regression tests 218 tests passed; after the bounded Marketplace follow-up **220 tests/20 files passed**. `npm run type-check` passed after the final Marketplace changes. |
| Frontend build | `npm run build -- --webpack`: passed, 30 pages, after WebCrypto migration and before the final selector extraction. A production build after that extraction is not claimed here. |
| MCP | `npm test -- --run`: **18 tests/3 files passed**; `npm run typecheck`, `npm run build`, and lint passed. Network-listening tests required the approved unsandboxed rerun after sandbox EPERM. |
| TypeScript SDK | `npm test`: TypeScript build and **2 tests passed**. |
| Code review service | `npm test`: TypeScript build and **4 tests passed**; lint and `npm ls --all` passed. |
| Dependency manifests | `python3 scripts/check_node_locks.py`: **8 manifest/lock pairs passed**. |
| Browser tests | `npm run test:e2e:list`: **270 tests discovered in 14 files**, not executed successfully. See blocker below. |

The frontend ESLint configuration now consumes Next's native flat configuration, fixing the configuration-loading failure without disabling compiler rules. Middleware uses Web Crypto UUIDs and `btoa` rather than Node crypto/Buffer in the Edge runtime; UUID ASCII encoding and cryptographic entropy are preserved. The successful build no longer reports that Node crypto Edge warning. This does not qualify an actual browser session.

`ModelSelector`'s status indicator and the studio view selector now have stable component identities. The extracted `components/bmad/ViewModeSelector.tsx` receives explicit mode/navigation callbacks. Two behavioral tests cover focus/DOM preservation through rerender, updated callbacks, back navigation and all three selectable modes. Existing routing behavior is retained.

MCP's **38 lint errors were reduced to zero** with explicit structural types, removal of unused bindings and obsolete duplicate exports, and descriptive expected-error annotation on the legacy adapter. No lint rules were weakened. The production tsconfig already excludes the legacy server variants and tool-registry cache; those exclusions were not introduced here. The cache was additionally checked directly:

```sh
./node_modules/.bin/tsc --ignoreConfig --types node --noEmit --target ES2022 --module ESNext --moduleResolution bundler --skipLibCheck src/framework/caching/tool-registry-cache.ts
```

This passed; it is not runtime qualification of excluded legacy server variants. Code review service now has a native flat TypeScript ESLint configuration; all 13 exposed findings were repaired. Parser/plugin 8.70.0 require TypeScript below 6.1, so that service uses 6.0.3 rather than 7.0.2. No force/legacy-peer installation bypass was used. The dependency exception is documented separately by root.

## Bounded Marketplace follow-up

`frontend/hooks/useMarketplace.ts` now declares `getInstalled` before its consuming `install` callback and includes it in that callback's dependency list. The existing authenticated request and refresh behavior remains intact; no lint rules were disabled. Two new behavior tests verify successful installation followed by refresh using the current authentication callback after rerender, and a rejected installation retaining the existing installed list without a refresh. Root independently reviewed and approved this callback ordering/dependency change. The full frontend suite passed **220 tests**, and type-check passed. No further frontend source cohort was attempted.

Final logs: `/private/tmp/vos-goal-node-20260917-marketplace-full-tests.log`, `marketplace-tests.log`, `marketplace-types.log` and `marketplace-lint.json` under the same prefix. The production build result above predates both the selector and Marketplace follow-ups; it is not presented as a rebuild of these final edits.

## Remaining frontend gates

Final lint reports **61 errors and 24 warnings**, down from 70 errors and 25 warnings. The seven static-component errors are resolved. The Marketplace follow-up also resolves one immutability error, one preserve-manual-memoization error and one exhaustive-deps warning. Remaining errors comprise 36 set-state-in-effect, 10 immutability, 11 refs and 4 preserve-manual-memoization findings. Lint remains a failing gate; passing unit tests do not override it.

An actual Chromium run was attempted with `npm run test:e2e -- --project=chromium --workers=1 --reporter=line --max-failures=1`. After the sandbox port restriction was resolved through escalation, the application failed for a missing Clerk publishable key. No browser case is claimed qualified. No Clerk authentication bypass, external account creation or invented key was introduced. Runtime-generated reports/instruction files were preserved outside the repository; the tracked Playwright HTML report was restored to its prior contents.

Logs are preserved under `/private/tmp/vos-goal-node-20260917-*`, notably `static-components-tests.log`, `static-components-types.log`, `frontend-lint-after-static.json`, `mcp-final-tests.log`, `mcp-lint-final.log`, `mcp-build-final.log` and `mcp-cache-types.log`. These absolute paths are local evidence, not portable repository artifacts.

## Independent native/aggregate runner review

Reviewed `scripts/native_bench.py`, `scripts/test_native_bench.py`, `run_all_tests.sh`, `kernel/run_tests.sh` and the benchmark launch/wait logic in `user/src/init.c`. Following the independent review, root authorized a bounded aggregate scope-reporting fix; that follow-up is author-validated below.

* Native classification requires the exact ordered expected benchmark starts and successful exits, one suite completion and later halt, and the owned QEMU process reaching the bounded observation condition. Missing/duplicate/malformed records, nonzero exits, unexpected full-suite skips and known failure diagnostics fail. Expected nested fault diagnostics cannot replace a successful top-level benchmark result.
* The observer holds its own `Popen` handle, checks early exit, bounds runtime, and terminates/waits/kills only that child. It does not use a shared PID file. Input disks use QEMU snapshot mode. Build failures and missing prerequisites do not become passes.
* The aggregate shell captures both command and `tee` pipeline exit statuses immediately, accumulates failures across layers, rejects legacy `[SKIP]` prerequisite messages, and labels `--no-kernel` as partial. Unknown arguments fail.

Validation: `python3 scripts/test_native_bench.py -v` passed **5 test methods**, including real ordinary child-process early-exit, timeout and owned cleanup tests. `bash -n run_all_tests.sh kernel/run_tests.sh` passed. An exact copy of the aggregate runner in a temporary fixture passed **7 orchestration scenarios**: success, independent backend/frontend/kernel failures, skip refusal, explicit partial selection and invalid argument. Fixture children are synthetic and prove shell orchestration only, not application or native kernel correctness. Logs: `native-runner-review.log` and `aggregate-runner-review.log` under the prefix above.

No concrete false-pass was found in those exercised controls. Remaining limits:

1. `--iso` hashes stable supplied bytes but does not establish a source-to-ISO manifest relationship. Clean-build provenance requires separate recorded build evidence.
2. The initially observed `TEST_ONLY` scope-reporting gap is fixed: the aggregate announces a filtered workload and its final success explicitly says partial. The requested kernel layer still executes. `python3 scripts/test_unified_runner.py -v` passed **7 test methods**, including an actual shell child asserting receipt of the filter and a filtered kernel failure that must still fail the aggregate. The fixture explicitly clears inherited filters by default. `bash -n run_all_tests.sh` passed. Log: `/private/tmp/vos-goal-node-20260917-unified-runner-final.log`. This follow-up was authored by this reviewer and awaits separate review.
3. QEMU user networking remains enabled; this is not an air-gapped test environment. Serial markers are evidence from the test guest, not cryptographic attestation against malicious guest code.
4. The two-second post-halt observation and finite workload are not sustained stress or exhaustive semantic validation of every benchmark. The build timeout directly owns `make`; descendant process-group cleanup was not separately tested.

## Independent billing guard review

Reviewed `backend/api/billing_routes.py`, `backend/tools/stripe_service.py` and `frontend/convex/billing.ts`. Rejecting a false `use_tokens` result prevents a success response when no debit occurred. The Convex mutation retains ownership checks, balance validation and debit/ledger updates within the mutation; the route did not introduce a pre-read debit race. An unavailable billing backend currently produces the same HTTP 402 rejection as an unsuccessful debit, which is an error-classification limitation, not an observed authorization bypass. This was a source review, not a live payment-system test.
