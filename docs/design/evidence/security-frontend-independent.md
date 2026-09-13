# Independent frontend security review — 2026-09-14

Reviewed the dependency-migration test cleanup and subsequent kernel status/error fixes authored by another agent. This reviewer made no frontend source/test edits.

## Findings and disposition

- Clerk's signed-in fixture exists only in `test/setup.ts`, loaded solely by the unit Vitest project. The Convex project has no unit setup or mock aliases and imports the real Convex schema, generated API, and backend modules through `convex-test`. This preserves real application authorization logic in that harness, not live Clerk token verification or a deployed Convex service.
- Unit includes cover all current hook, store, component tests; Convex tests remain in the default root Vitest projects. Playwright files have a separate actual execution command (`npm run test:e2e`) and discovery command (`npm run test:e2e:list`). They were not silently marked skipped to make Vitest pass.
- Existing hook assertions were preserved. Additional model/catalog fetch fixtures match the two actual initialization calls. Kernel store reset isolates test cases. The six-step wizard test now checks Review -> Deploy -> final clamp, consistent with the production wizard's visible six steps; it does not remove the clamp assertion.
- The production kernel API already returns `ping` and `data`; the frontend previously discarded these. Dedicated status fields now preserve them without manufacturing desktop VBus connectivity. A regression assertion explicitly verifies HTTP ping does not set `vbusConnected`.
- Process-list failures now surface to the user and successful retries clear the local error. Existing failure assertions remain and successful recovery is additionally checked.
- **Found and resolved during independent review:** new status fields initially survived failed refresh/stop, allowing a stale positive ping while QEMU remained alive. The author now clears `statusPing`/`statusData` on status-fetch failure and successful stop. A failed-refresh regression seeds a live QEMU and verifies false/empty status; inspected final source closes this finding.

## Evidence and limits

- Default `npm test`: **216 tests passed**, 18 files, unchanged case count. Log: `/private/tmp/vos5-frontend-contract-tests.log`.
- Independent `npm run test:convex`: **2 tests passed**. Log: `/private/tmp/vos5-frontend-convex-independent.log`. These two tests are included in the 216 total, not additional tests. They verify owner membership creation, outsider denial, owner impersonation rejection, and no partial organization/member writes on rejection.
- Typecheck passed in `/private/tmp/vos5-frontend-contract-typecheck.log`. Root separately reported a successful Next production build; this reviewer did not rerun it.
- Playwright discovery: **270 browser cases in 14 files**, across three browser projects, recorded in `/private/tmp/vos5-frontend-e2e-list.log`. **Discovery is not execution and provides no browser security runtime evidence.** Authenticated browser journeys remain unqualified by this review.

No production identity bypass or reduced security assertion was introduced in the inspected change. Unit signed-in mocks do not establish anonymous-user behavior, live token validation, or production authentication correctness. The review is bounded to the changed test configuration and kernel hook/store contracts, not a complete frontend audit.
