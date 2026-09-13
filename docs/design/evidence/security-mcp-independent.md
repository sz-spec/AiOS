# Independent MCP authorization review — 2026-09-13

Reviewer did not author the authentication implementation. Inspected
src/security/mcp-auth.ts, server.ts, quota manager, cache integration and the
installed FastMCP stateless request handling implementation. Scope is this MCP
entrypoint, not every server or service in VOS.

## Results

Independently executed `npm test -- --run tests/unit/mcp-auth.test.ts` with
the current Node runtime. All **6 tests passed**, 3.15 seconds. Initial sandbox
attempt could not bind loopback (EPERM); explicitly escalated execution passed.
Raw log: `/private/tmp/vos5-mcp-auth-independent.log`.

The tests exercise actual HTTP missing/incorrect bearer rejection, authenticated
quota identity, cross-user and admin denial, permitted administrator changes,
startup failure without configured identities, and actual SDK stdio transport
using an explicitly configured ordinary user.

## Security and mathematical invariants

- HTTP principal = server-configured mapping of presented bearer. Tool arguments
  do not select this identity. Strict schema accepts only user/admin roles.
- Matching compares fixed-size SHA256 digests with timingSafeEqual and examines
  every credential. This avoids an obvious early-exit token comparison, without
  constituting a formal side-channel proof of the complete request path.
- In inspected FastMCP stateless handling, createServer authenticates the current
  request and requires a non-null result. A supplied mcp-session-id is not used
  as a substitute for bearer authentication. No direct bypass was found here.
- Global metrics and quota mutation require admin; requested foreign quota
  identity requires admin. Ordinary users obtain their own quota by default.
- Exact HTTP cache keys encode the tuple (userId, agentId, forced model, message)
  as JSON. This is injective over the accepted tuple values; principal/context
  differences cannot produce the same fresh key through delimiter ambiguity.
- HTTP semantic cache is disabled. Stdio is a local process trust boundary with
  one explicit principal; its shared semantic cache must not be treated as a
  general multi-user or agent-isolated cache.

## Findings sent to implementation owner

1. **Legacy cache namespace migration:** the new tuple key initially shares
   cache/simple-cache.json with historical plain-message keys. An old prompt
   equal to a new JSON tuple can collide with the new namespace when loaded
   within TTL. Use a new versioned filename/envelope and reject legacy entries.
   This is a migration issue despite the injective new-key function.
2. **Quota limits are not hard concurrent reservations:** checkQuota reads usage,
   then provider work runs before recordUsage. Concurrent requests can all pass
   against the same remaining budget. Cache hits also return before request
   usage is incremented. These are pre-existing enforcement limitations, not
   evidence of principal forgery; do not describe this patch as strict budget
   enforcement. Atomic reservation/reconciliation needs a separate change.
3. **Coverage gap:** the six reviewed tests do not exercise cache tenant/context
   isolation or legacy migration via actual HTTP. Requested seeded-cache tests
   with two principals and differing agent/model inputs from the author.

Credentials are static environment configuration; rotation requires restart.
Loopback bind and disabled CORS reduce exposure but do not provide encryption
or protect against another local process holding the bearer. No TLS proxy or
multi-process quota persistence was validated. Operational environment and
cache directory are trusted. Findings refer to inspected state and may be
corrected concurrently; corrected tests/results must be recorded before closure.

## Follow-up observed in the same review

Author changed persistence to `simple-cache-v2-principals.json`, separating
legacy prompt keys, and added actual HTTP seeded-cache checks for Alice/Bob
and agent/model contexts in a disposable working directory. Reviewer inspected
the changed filename and test cases. Updated author-run log reports **7 tests
passed**, 3.24 seconds, `/private/tmp/vos5-mcp-auth-tests.log`. The earlier
six-test run above was independently executed by this reviewer; the seventh
was observed in the author-run log, not independently rerun. Quota concurrency
and cache-hit accounting remain open.

## Final container delta review

Inspected subsequent changes by the other implementation agent: MCP_HOST now
accepts a literal IPv4/IPv6 address, defaults to loopback in source, and is set
to0.0.0.0 in the Docker image to permit published-port access. Authentication
configuration is still required before listening; a broader bind does not
create an anonymous principal. This supersedes the earlier unconditional
loopback description. Container exposure requires an explicit network/TLS
deployment boundary; CORS=false does not encrypt bearer credentials.

MCP_CACHE_DIR redirects the versioned principal cache to writable temporary
storage. The runtime user is non-root and compiled application files are
read-only; that does not make the entire container filesystem immutable.
Application arguments do not choose the cache directory. The versioned cache
namespace, stateless per-request principal and admin guards remain present.
Tests now explicitly cover literal bind-address validation and the liveness
endpoint. This final delta was inspected, not redundantly rebuilt by this
reviewer. The author/container runner owns actual final runtime-test evidence.

Pre-existing Docker metadata such as security.hardened=true and production-grade
wording must not be interpreted as this review's certification. No fresh
container advisory scan or TLS validation was performed in this delta review.
