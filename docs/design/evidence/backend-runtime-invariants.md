# Backend runtime acceptance invariants — 2026-09-14

Independent mathematical/test design review for the integration owner's Docker
harness. No Docker runs or production edits were performed for this review.
These are acceptance obligations; this document alone does not assert a pass.

Let I be an immutable image ID, V a uniquely owned persistent volume, C1 and C2
distinct containers using I and V, and A/B distinct authenticated principals.
Let m be a fresh unpredictable marker and id its server-returned memory ID.

## Required observable chain

1. **Identity:** record image ID/digest, runtime UID, Python version and resolved
   package checks. Both C1 and C2 must use the same immutable image identity;
   reusing a mutable tag without recording IDs is insufficient.
2. **Strict discovery:** execute discover_routers() with production environment
   active (the current implementation derives strict mode from environment);
   require success and record named routers, not only a count. Verify actual
   mounted HTTP method/path pairs for the exercised endpoints. Default app
   discovery is not automatically strict; an import smoke can miss optional
   routers and does not run lifespan.
3. **Startup:** start real Uvicorn, observe Application startup complete and a
   bounded successful HTTP response. /health returns status=healthy even when
   startup has caught optional service failures. Record its service booleans
   and required profile explicitly. Never equate the string with full readiness.
4. **Write:** use the real production repository/API path to commit SQLite data
   and an authenticated memory write. Assert expected status, returned identity
   and exact payload. No direct fixture injection may replace the write under
   test. Record the actual memory backend (Chroma or JSON fallback).
5. **Process boundary:** stop C1 normally, wait for exit, start a genuinely new C2
   with the same V and I. Record different container IDs. A same-process cache
   read, Docker restart alone, or an unchanged Python singleton is weaker proof.
6. **Read:** after C2 startup, recover committed SQLite data and memory id/content
   exactly. Prefer direct ID/recent lookup rather than semantic ranking. Assert
   one expected persisted record rather than accepting any nonempty response.
7. **Negative control:** a new owned empty volume must not contain m. This
   distinguishes persistence in V from a baked fixture, external service or
   accidental reuse of the original container/cache.

## Principal noninterference

For each supported observer O in get-by-ID, recent, query, export and statistics,
O(B) must not reveal A's unique marker or ID before or after replacement of C1.
B's update/delete of id must be denied or reported absent, and a subsequent
read by A must recover the unchanged value. Explicitly test absent/invalid
credentials: they must not silently enter the default/global memory store.

HTTP metadata claiming another actor, internal trust or model origin must be
overwritten to actor=A, trust_level=external, source=memory_api and
model_origin=unknown. Successful principal directory hashing is not itself
authorization; the principal must originate in real verified credentials.
Dependency overrides, monkeypatched get_current_user or development auth
bypasses must be disclosed and cannot count as production authentication proof.

## Code-specific pitfalls

- SQLite's local repository selects VOS3_LOCAL_DB_PATH, not DATABASE_URL.
  HTTP tenant memory selects VOS_MEMORY_TENANT_ROOT, not VOS_DEV_MEMORY_DIR.
  Mounting /app/data covers Docker's configured local paths; verify effective
  paths and readable on-disk artifacts instead of assuming the mount is used.
- DevMemory initialization may fall back from Chroma/embeddings to JSON.
  Record this honestly; a fallback persistence pass is not a vector-search pass.
  Pre-provisioned model-cache availability and network policy affect startup.
- The JSON fallback uses atomic file replacement plus an in-process RLock.
  Atomic replacement prevents partial files but does not serialize read-modify-
  write across four workers. Sequential restart testing does not prove no lost
  updates under concurrent multi-process writes. A separate barrier-synchronized
  write test would require all committed unique IDs survive, with no duplicates.
- SQLite sessions require explicit commit. Read in another process only after
  commit and graceful stop; account for WAL/journal files in the same volume.
  This is graceful-restart durability, not power-loss/fsync qualification.
- Every production worker must share the same configured JWT key. Token
  acceptance after replacement must use stable external identity/key material,
  not a newly minted fallback identity that accidentally changes the namespace.
- Internal agents/chat routes still include explicit global get_dev_memory
  callers. A pass for /api/memory alone must not be generalized to every memory
  surface; those distinct authorization paths need separate coverage.

## Evidence format and proof boundary

Record each stage with timestamps, container/image identity, observed status,
nonsecret principal labels, marker hashes, backend mode and failure details.
Do not log bearer tokens, signing secrets or unrelated volume contents. Poll
with a finite deadline, stop only owned containers, and retain failing-stage
logs. A refused router import or swallowed startup failure is a failed/partial
gate even if another endpoint responds.

The mathematical property tested is finite trace consistency: one known commit
survives process replacement while B cannot observe/mutate it through specified
API operations. It does not establish universal noninterference, all possible
interleavings, power-loss durability, every router's authorization or overall
OS security. Image build, import, lifespan, persistence and security assertions
must remain separately labeled so one cannot substitute for another.

## Harness inspection before execution

Inspected scripts/backend_runtime_smoke.py. It pins docker image inspect's
immutable ID, creates two different containers from that ID, uses a uniquely
owned shared data volume, runs the actual image CMD and observes both startup
and graceful shutdown. HTTP health additionally requires explicit service
booleans and protected-route credential denials. This is materially stronger
than interpreting healthy alone as success.

The storage probe uses the actual SQLAlchemy User model with explicit commit
and opens a new session/process after replacement. DevMemory uses its real
principal namespaces and persistent Chroma collection with a deterministic
embedding adapter only in the probe process. Requiring initialized collections
prevents JSON fallback from masquerading as a Chroma pass. The adapter means
this does not qualify downloaded models or embedding quality.

Findings sent to integration owner before results:

- Cleanup must attempt removal even if log capture fails or detects leakage;
  capture and rm originally shared one try block, permitting an owned container
  to survive on the very error path the harness should clean up.
- Positive memory writes and principal noninterference are direct store probes,
  not authenticated HTTP writes. Name this store-level isolation; successful
  HTTP credential verification and metadata sanitization remain separate tests.
- Compare persisted marker hash against the actual marker in the read phase,
  rather than only echoing it as evidence. Exact content comparison already
  protects the main read assertion, but the report hash should be verified too.
- A second independent empty-volume negative control is not present. Initial
  empty-store assertions give useful prewrite control, with a narrower scope.

The current image CMD uses one worker, consistent with the documented JSON
process-local lock boundary. This supersedes the earlier four-worker scenario;
it does not establish multi-worker concurrent storage safety. No Docker runs
were performed by this reviewer during this inspection. Runtime pass/fail
results must be appended only after examining the actual final evidence.

## First execution and strengthened harness review

Inspected /private/tmp/vos5-runtime-qualification-20260914-a/result.json. This
first run passed against image
sha256:0bab617c63145ed5eedf11368f250f055d25dee63f446ef2aeae86cde1e3dd50.
It records40 router groups,457 OpenAPI paths, UID1000, disabled development
authentication, required service booleans true, three protected denials per
container, and four missing-secret startup refusals. SQLite and real Chroma
recovered the same memory ID and marker hash after replacement by a distinct
container. The owned volume was removed. Redis checkpointer remained false,
consistent with the explicitly offline profile rather than full cloud readiness.

Subsequent harness inspection confirms cleanup log capture and removal are
separated, ownership is checked, marker hash is asserted, and the isolation
result is now accurately named store_principal_noninterference. Host/image
fingerprint equality and probe hashing strengthen association with tested code.
Suggested including api/memory_routes.py and core/database/sqlite_setup.py in
the fingerprint set because those files are directly exercised.

The first result predates the next memory fail-closed source change. It cannot
serve as final-image qualification for that change. Awaiting the strengthened
harness result against the rebuilt image before closing the final runtime gate.

## Final reviewed result: bounded runtime gate approved

Independently examined the final result and both server logs under
/private/tmp/vos5-runtime-qualification-20260914-final. The84.35-second run
passed against immutable image
`sha256:4c9b1fe4be4ea44e9c86fe9b062dd67286a4bb137e8d01e4fa6d915972b91982`.
Recomputed all13 selected host source hashes and the probe hash; they match
the recorded evidence. Probe SHA256:
`2451bccfa09747be27cedb13ddb98ed38fabe5abc15b534909d7b3bfb11087d2`.
The inventory assertion compares those selected source hashes inside the image.
This is a selected-file match, not an exhaustive repository attestation.

Two different container IDs each show one application startup completion and
one graceful shutdown completion. Each HTTP probe observed403/401/401 for its
three invalid credential cases. The committed SQLite row and real persistent
Chroma memory survived replacement, with identical marker SHA256
`130bc573d850afda9167c5e094730db054437cdeaf2a71a8bca3f662a8ffb6b0`.
Store-level foreign-principal read/update/delete checks passed. Both containers
and their owned volume were removed. Durable evidence is copied by integration
into docs/design/evidence/backend-runtime-2026-09-14.

**Approval scope:** offline production process startup, selected strict router
availability, required local service readiness, negative HTTP authentication,
graceful-replacement SQLite/Chroma persistence and tested store-level principal
separation. This closes that bounded runtime acceptance gate for the recorded
image and probe, not general production readiness or successful live HTTP login.

Both server logs retain ASGI exception tracebacks from the deliberately missing
or malformed credentials even though observed responses were401. They also
contain offline embedding load/Chroma operation OSError messages from ordinary
server background paths. These are not hidden: the runtime is not claimed
error-free, and the deterministic-adapter storage probe does not establish
production embedding availability. Expected-error logging and online/model
readiness remain separate follow-up work. Positive authenticated HTTP mutation,
all other global-memory callers, multi-worker contention, fresh empty-volume
control and power-loss durability remain outside this run's approval.
