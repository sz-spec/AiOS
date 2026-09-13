# Frontend dependency integration — 2026-09-14

Updated test wiring and corrected two kernel status/error propagation bugs
exposed by the restored tests. Authorization logic remains unchanged.

## Changes

Vitest now has explicit unit and Convex projects. Unit tests use jsdom and
unit fixtures; Convex tests use real backend modules under Node without Clerk,
React or generated-API mocks. `npm test` runs both projects, including failures.
`npm run test:unit` and `npm run test:convex` run the individual groups.
Playwright suites remain available through `npm run test:e2e`; they are no
longer incorrectly imported by Vitest. `npm run test:e2e:list` validates discovery.

The missing Clerk unit fixture now supplies a stable test token function.
It is not loaded by Convex authorization tests or Playwright. Chat fixtures now
account for both model-list HTTP requests before chat completion. Kernel hook
tests reset the shared Zustand store before each test to prevent stale errors
from earlier cases. Existing kernel assertions were retained and strengthened. The obsolete wizard
maximum assertion was updated to the visible six-step flow, as explained below.

## Observed results

| Check | Result |
| --- | --- |
| All unit + Convex cases | 216 passed, 216 total; all 18 files passed |
| Dedicated real Convex group | 2 passed, including outsider authorization denials |
| Playwright discovery | 270 browser cases across 14 files and 3 browser projects |
| TypeScript check after configuration/test edits | Passed |
| Production Next build | Passed before this final hook correction; final production rebuild belongs to integration validation |

The earlier mixed-runner baseline had 147 failing and 69 passing cases, plus
14 incorrectly discovered Playwright suites. Runner separation does not count
browser cases as passed. Browser execution still requires its app/services,
authentication configuration and installed browser dependencies.

## Contract reconciliation and regression evidence

1. backend/api/kernel_routes.py /status explicitly returns ping and data.
   The browser store discarded them; the hook substituted desktop vbus state
   and an empty string. Added separate statusPing/statusData fields preserving
   the actual HTTP response. Desktop status updates those fields from its own
   vbus result. Regression asserts successful HTTP ping does not manufacture
   vbusConnected, preserving the separation from desktop transport state.
2. app/kernel renders the hook error, but fetchProcesses swallowed failures.
   It now exposes a local process error and clears that error when retrying.
   Existing HTTP failure assertion passes; a successful retry assertion verifies
   error recovery and returned processes. Authentication calls are unchanged.
3. app/create visibly renders Review at step5 and Deploy at step6. The old
   four-step test predates that flow. Replaced it with review5 -> deploy6 ->
   still6 assertions, retaining the boundary-clamping intent and also verifying
   the final visible transition. Production wizard behavior was not changed.

Final validation: all216 assertions-bearing cases pass; TypeScript passes.
Logs: `/private/tmp/vos5-frontend-contract-tests.log` and
`/private/tmp/vos5-frontend-contract-typecheck.log`.

Evidence logs: `/private/tmp/vos5-frontend-separated-tests.log`,
`/private/tmp/vos5-frontend-convex-tests.log`,
`/private/tmp/vos5-frontend-e2e-list.log`,
`/private/tmp/vos5-frontend-config-typecheck.log`, and the integration owner's
`/private/tmp/vos5-latest-frontend-build.log`.
Dependency versions and compatibility exceptions are recorded separately in
dependencies/upgrade-exceptions.json. No new advisory scan was performed by
this test cleanup task, and no overall security certification is implied.

Independent security review follow-up: clear statusPing/statusData on failed
status refresh and successful kernel stop. A regression seeds qemuAlive, fails
the refresh, and verifies stale successful ping/data cannot survive. All216
tests and TypeScript pass after this correction.

## Final production build using Webpack

After the last status-clearing fix, the default Turbopack build failed while
its CSS/PostCSS worker attempted to bind a local port (EPERM). The integration
owner observed the same environment failure after escalation. This is recorded
as a limitation of the default build in this execution environment, not a pass.

The supported alternative `npm run build -- --webpack` completed successfully
with the same current source and Node runtime, including compilation,
TypeScript checking, generation of30 static pages and build traces. No source,
assertion, type-check or bundler configuration was changed to obtain that result.
Log: `/private/tmp/vos5-frontend-final-webpack-build.log`.
Webpack emitted dependency Edge Runtime warnings; these remain visible in the
log. Successful bundling is not evidence that all middleware runtime paths or
the separately listed browser scenarios have been exercised.
