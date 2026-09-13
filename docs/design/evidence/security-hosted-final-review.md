# Final hosted security evidence review — 2026-09-14

Reviewed applicable instruction locations (canonical repository and ancestor `AGENTS.md` paths; none present), final hosted/frontend/authentication/deployment diffs, and existing validation reports. This pass made no production edits and did not repeat full suites. It is an evidence and security-boundary review, not a release certification.

## Independence

This reviewer independently examined frontend configuration/state changes, the other agent's bcrypt migration, root's CSRF logging change, Python advisory exposure, and ancillary webhook authentication. This reviewer **authored the Rust migration, memory remediation and MCP authentication implementation earlier**; therefore conclusions about those changes below cite other agents' independent reviews and distinguish their tests from subsequent author validation. A consolidated report does not create independent review of one's own code.

## Authentication and principal boundaries

- The independent [auth migration review](independent-auth-migration-security.md) records **19 passing tests**: seven bcrypt migration cases, three secret-resolution/logging cases, and nine existing CSRF cases. Direct bcrypt keeps cost 12, random salts and checked verification; invalid passwords/hashes fail closed. Passwords formerly truncated beyond 72 UTF-8 bytes now reject, requiring a verified reset policy for affected legacy users. The model HTTP 422 test is not blanket qualification of all authentication endpoints.
- CSRF's constant-time handshake comparison and production missing-secret refusal remain unchanged. Removing the generated secret from logs closes the observed disclosure. The Dockerfile now explicitly sets `ENVIRONMENT=production`; production launchers must provide an explicit handshake secret and appropriate other authentication configuration. Default development behavior must not be described as equivalent to a production guard.
- For MCP, the [independent reviewer](security-mcp-independent.md) inspected actual FastMCP request authentication and principal/admin checks. Their original six-test run was independently executed; the versioned cache fix and seventh cache test were subsequently inspected. Later **18-test** and actual non-root/read-only container evidence are author validation recorded in [MCP validation](../../../backend/mcp-server/docs/DEPENDENCY_VALIDATION.md), not a new independently executed 18-test run. No claim of hard concurrent quota reservations is supported: usage checking/accounting remains non-atomic and cache-hit accounting is incomplete. Static bearer rotation requires restart; stdio is an explicitly trusted local process boundary.
- The peer's [memory review](independent-memory-security.md) covers namespace isolation and the follow-up HTTP trust/logging remediation, with **74 tests independently passed** in its final run. Internal legacy singleton callers, upstream Chroma system retention and same-principal multi-process JSON write durability remain limitations. This reviewer does not independently re-certify their own memory code.
- Ancillary webhook source now requires a signing secret before service initialization, validates a fixed SHA-256 signature format and compares HMAC against the exact raw request bytes. Bad-length/malformed input no longer reaches an unsafe unequal-buffer timing comparison. The [ancillary report](dependency-upgrade-node-ancillary.md) distinguishes signing checks/container fail-closed evidence from full hosted webhook/Redis/provider behavior. Dashboard and monitoring exposure must retain required credentials and loopback publishing; monitoring scrape configuration is not fully qualified.

## Frontend state and test boundaries

The [independent frontend review](security-frontend-independent.md) preserves the distinction between signed-in unit fixtures and real application authorization logic in the dedicated Convex harness. The Clerk fixture is loaded only by the unit project. Real Convex schema/modules and outsider/owner-impersonation checks remain separate and are part of default `npm test`.

**216 tests passed**, with unchanged test-case count; the dedicated two Convex cases are included in that total. **270 Playwright cases were discovered**, not executed. No browser authentication/security runtime pass follows from discovery. Kernel status fields preserve HTTP ping/data without inventing desktop VBus connectivity; stale status is cleared after failure/stop and regression assertions retain the distinction. Process errors surface and clear on successful retry. The six-step wizard assertion matches production steps while retaining clamping. Inspected final build log ends in generated route output; build success does not establish authenticated browser behavior or offline operation.

## Dependency and packaging exceptions remain visible

- Python audit remains **nine entries, six unique advisories, three affected packages**, with no fixed versions listed in the scan. The [triage](python-dependency-security-triage.md) and [independent exposure review](independent-python-security.md) narrow observed entrypoints but do not suppress advisories or call the installed packages safe. Chroma is used through embedded clients (including an older `Client` call, not exclusively `PersistentClient`); Ragas uses fixed text metrics; traced cache factories default to no DiskCache backend. Alternate deployments, extensions, untrusted cache storage or future multimodal evaluation can change reachability.
- [Version exceptions](../../../dependencies/upgrade-exceptions.json) explicitly retain compatible earlier versions required by upstream constraints, including CrewAI's Chroma/OpenAI/Pydantic dependencies and frontend ESLint/TypeScript peer compatibility. “All latest” is not literally true for every dependency. No legacy-peer-deps bypass is justified by audit success.
- The macOS ARM z3 5.1.0.0 wheel tag remains rejected by packaging compatibility checks despite successful import/basic solver smoke. Runtime success does not establish portable installation. The exception file's lock-count snapshot predates the final bcrypt/email-validator sync and was flagged to the root for refresh; counts are not assumed current merely because package equality was previously checked.
- MCP reports zero known npm advisories and a valid peer tree, but lint still exposes **38 historical errors** rather than suppressing them. This prevents describing all engineering gates as clean.
- Hash-locked Python installation into `/opt/venv` fixes root-private package access and validates artifact hashes, not package benignness. Docker Node versions are explicit where project-owned; base image tags/OS package installation are not fully digest/reproducibility pinned. Positive MCP container checks do not substitute for final backend image startup/import, writable-volume/secret checks or full image vulnerability scans. Preserve the operator-configured memory directories and writable mount requirement for read-only backend images.

## Rust boundary: peer evidence only

The [independent Rust security review](dependency-security-rust.md) found no downgrade in the rand/HMAC/HKDF API migration and independently checked two known-answer computations. It explicitly retains optional initial HMAC negotiation, handshake transport trust, non-gated Python sidecar fallback and ordinary in-memory secrets as deployment concerns. The author's **108 tests** used test-only Tauri overrides for missing real sidecar/icon assets; these do not constitute a packaged release pass. No clean full Cargo advisory audit is inferred from a targeted rand advisory check or an attempted audit-tool installation.

## Disposition

Coordinator follow-up: the final Python lock counts were refreshed and checked
on 2026-09-14: full Linux 278, root Linux 273, minimal backend 178, Windows 254,
macOS 253; shared versions match and offline fingerprints pass. A later Next
build attempt, including an escalated retry, failed because Turbopack could not
bind a local port (EPERM). The earlier successful build remains historical
evidence; final production-build revalidation is not claimed complete.

Subsequently, the math/build agent validated the current source with the
supported alternative `npm run build -- --webpack`: exit 0, TypeScript checks
and 30 static pages passed. The default Turbopack EPERM remains unresolved;
Webpack emits existing Edge-runtime compatibility warnings, so this result
does not certify execution of every middleware path.

Strict container startup found missing Socket.IO and Strawberry GraphQL in
the minimal deployment profile. Their existing reviewed versions and required
transitives were added (170 to 178 packages); no existing resolved version
changed. The independent container report records subsequent runtime status.

Inspected hosted changes improve authentication, principal separation and explicit deployment assumptions without evidence that the frontend cleanup weakened existing security assertions. Open advisories, browser runtime qualification, packaging/installation exceptions, quota concurrency, internal principal propagation and the peer's Rust deployment findings remain release limitations. The user-facing status should report tested outcomes and these concrete remaining gates, not a claim that VOS is certified secure or runs on all PC hardware.
