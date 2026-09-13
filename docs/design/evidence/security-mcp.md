# MCP transport authorization remediation — 2026-09-13

Independent follow-up to the MCP dependency migration found that the running server did not register authentication, billed every caller as `default-user`, and exposed quota changes/global usage to any caller. Descriptions saying “admin only” did not enforce a permission.

Implemented in `backend/mcp-server/src/security/mcp-auth.ts` and `src/server.ts`:

- HTTP startup requires a nonempty `MCP_AUTH_TOKENS` JSON array of `{ "token": ..., "userId": ..., "role": "user" | "admin" }`. Tokens must be unique and at least 32 characters. Use independently generated random secrets (length validation is not an entropy proof). Unknown transport values and missing/invalid configuration fail startup before services initialize.
- FastMCP authenticates bearer tokens against fixed-length SHA-256 digests with `timingSafeEqual`. Missing/incorrect credentials receive HTTP 401. Tokens/user IDs/roles are never accepted from tool arguments as identity.
- HTTP defaults to `127.0.0.1` (an explicit validated `MCP_HOST` literal address can override it), disables CORS, and uses stateless requests, authenticating each request. Remote exposure requires a correctly configured TLS proxy; this change does not provide a public HTTPS service, token issuance, expiry, or live revocation. Rotate credentials and restart the service to revoke them.
- Chat accounting uses the authenticated principal. A user may read their own quota; selecting another user requires admin. Setting quota, reading global cost/health metrics, and reading global cache statistics require admin. All current tool handlers validate a principal before performing their operation.
- The shared semantic cache is disabled for HTTP until it supports verified tenant namespaces. HTTP retains exact-match caching keyed by authenticated user, exact prompt, requested model, and agent. A new `simple-cache-v2-principals.json` file separates the old unscoped keyspace entirely, preventing legacy prompt strings from colliding with encoded scoped keys.
- `TRANSPORT=stdio` now selects the stdio transport and requires `MCP_STDIO_USER_ID`. Local role defaults to `user`; `MCP_STDIO_ROLE=admin` is explicit authority granted by the process owner. Console status logging is redirected to stderr to preserve stdout JSON-RPC framing. This is a trusted local process boundary, not authentication of arbitrary users sharing that process.

## Checks

`npm run typecheck` passed. The full suite before the added cache test passed 15 tests; the final focused authorization suite passed all 7 tests. Seven integration/configuration tests in `tests/unit/mcp-auth.test.ts` passed, using the actual server and FastMCP HTTP/stdio transports:

1. Missing/wrong/forged bearer rejection.
2. Authenticated principal determines quota identity.
3. Cross-user quota read and five admin operation paths reject ordinary users.
4. Explicit admin may change another principal's quota.
5. Missing HTTP configuration, unknown transport, and missing stdio identity fail closed.
6. Real stdio SDK client sees its explicit identity and cannot self-promote quota.
7. Actual seeded HTTP cache responses remain separate for Alice/Bob and for agent/model contexts, using an owned temporary cache directory.

Log: `/private/tmp/vos5-mcp-auth-tests.log`. Local socket binding required sandbox escalation. Tests generate transient random credentials, make no provider requests, and terminate only their own server child processes.

## Remaining boundaries

This is not a complete service security certification. Quota accounting is in-memory and does not reserve budgets atomically across concurrent requests; cache hits/accounting need further billing review. Model/provider authorization, prompt privacy, cache persistence permissions, rate limiting, distributed tenancy, logging/secret handling beyond this boundary, and upstream dependency advisories remain separate review subjects. Global semantic caching remains available only in explicitly trusted stdio mode. Tests establish functional authorization outcomes, not a cryptographic timing or entropy proof.


Container follow-up (2026-09-14): Docker explicitly uses `MCP_HOST=0.0.0.0` with authentication still mandatory and `MCP_CACHE_DIR=/tmp/vos-mcp-cache` for read-only root operation. Added bind-address/default/fail-closed and minimal-health tests; final full MCP suite **18 passed**. Node 26.8.2 image built, and actual non-root/read-only container passed published-port bearer rejection/identity, missing-credential startup failure, liveness, and writable-tmp-cache checks. See `backend/mcp-server/docs/DEPENDENCY_VALIDATION.md` for bounded evidence.
