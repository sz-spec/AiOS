# Independent security review of the backend runtime gate

Date: 2026-09-14. Scope is closure of the pending backend container runtime qualification, without deployment or external authentication. Root owns Docker execution and the harness; this reviewer inspects source and subsequent evidence, and edits only this report. No Docker commands are launched by this reviewer in this follow-up. The preceding image/profile history is in `security-backend-container-final.md`.

## Required assertions

1. Identify the tested image by immutable digest. Assert actual uid 1000, exact production mode, authentication development mode disabled, read-only root by a failed write outside writable mounts, and successful writes to the configured data/cache directories. Keep networking disabled and use synthetic transient secrets.
2. Exercise strict router discovery and the actual application factory. Require billing, team, GitHub, template, terminal, version-control, theme and memory routers to exist, report the full inventory, and reject swallowed import errors. A health response alone does not prove router completeness.
3. Independently remove each required credential while supplying the others. Missing JWT signing key, Clerk key, Tauri IPC handshake secret and production API secret must fail before usable service startup. Missing/placeholder API secret is guarded inside `create_app`, so import-only tests do not cover it.
4. Exercise HTTP denial both without an API key and with the correct synthetic API key but no Bearer identity. The first checks the API-key gate; the second must reach authentication and still deny the protected memory endpoint. Require 401/403 and no private payload; redirects, 200 or 500 are not passes. Public health is checked separately.
5. Scan captured output against the actual generated secret values and a private-memory canary without printing those values. No assertion may log the secret to explain its failure.
6. Use only a uniquely owned disposable persistence volume. Write distinct synthetic principal stores, reopen in a new process/container, verify persistence and cross-principal ID rejection. Namespace selection must derive from principal identity, not imported metadata. Direct store tests do not prove live Clerk authentication; report that boundary explicitly.
7. Bound process and Docker waits, retain exit codes and test output, and report cleanup of only owned containers/volumes. Interrupted execution, no returned container identity, or an empty log is unverified runtime, not success.

## Source findings relevant to the harness

`create_app` requires a nonempty `VOS_API_SECRET` other than `change_me` in production. Its API-key middleware runs independently of identity verification, so a protected endpoint returning 403 without the key is insufficient evidence that Bearer authentication remains enforced. The memory router inherits a verified nonblank-principal dependency and obtains per-principal storage through the centralized factory.

`/health` always reports `status=healthy` and separately includes service initialization booleans. An HTTP 200 therefore demonstrates routing/process responsiveness; full lifespan/service readiness needs additional evidence and must not be inferred from the status string. External services are deliberately unavailable in an offline test.

The deployment defaults remain one worker, uid 1000, read-only root, explicit production mode, owned writable data/cache mounts and environment-directed storage paths. Prior independent JWT and memory tests remain useful supporting evidence, but do not substitute for the corrected image runtime test. Direct bcrypt implementation was authored by this reviewer and is not represented as independently reviewed here.

## Outcome

Pending root harness review and bounded execution evidence. No new-image runtime pass is claimed at this stage.

## Harness and persistence failure review

Reviewed `scripts/backend_runtime_smoke.py` before execution. It resolves the image ID before launch, uses the image's actual CMD in two distinct containers, waits for lifespan startup, checks selected service flags, verifies protected HTTP denial with a correct API key and missing/malformed Bearer credentials, inventories strict routers, and reopens actual SQLite/Chroma data across replacement containers. Its embedding adapter exists only in the probe process. These checks establish local runtime/storage behavior, not external Clerk/Convex integration or embedding-model quality.

Flagged cleanup ordering: a failed log capture must not skip container removal. Root separated those operations before the first run. Also requested tracking the intended volume before creation returns, so a create-call timeout cannot silently orphan it; final harness/evidence will determine closure. Command/phase deadlines are bounded but do not constitute a single 120-second aggregate deadline.

Independently reviewed the author's small `DevMemory.add` correction: initialized Chroma write failure now returns `None`, and an inconsistent initialized state cannot return an unsaved entry. JSON remains restricted to a store that did not initialize its vector backend. The regression uses a real Chroma collection, injects a write exception, requires HTTP 500, preserves an existing record/count, rejects JSON fallback, asserts no private-content canary in logs, and verifies successful recovery afterward.

Independent rerun: **7 focused tests passed in 6.90 seconds**, `/private/tmp/vos5-memory-write-failure-independent.log`. No memory production code or test assertions were changed by this reviewer in this follow-up. Final image runtime evidence remains pending.

The first complete runtime run (`/private/tmp/vos5-runtime-qualification-20260914-a/result.json`) passed on image `sha256:0bab617c63145ed5eedf11368f250f055d25dee63f446ef2aeae86cde1e3dd50`: 40 strict router groups, 457 OpenAPI paths, two actual HTTP rounds returning 403/401/401 for protected denial cases, four missing-secret refusals, persisted SQLite/Chroma content across two distinct containers, and owned-volume removal. This predates the write-failure source correction and is supporting evidence, not final-image qualification.

Final harness review closes both cleanup findings: intended volume ownership is tracked before creation returns, label checks restrict deletion to this run, and failed log capture no longer prevents bounded container removal. Source fingerprints compare a selected list of deployment/auth/storage inputs, not the entire build context. Persistence result labels now explicitly say store-level principal isolation; marker fingerprints are asserted after reopen.

After the write-failure behavior change, independently ran the combined affected API and namespace suite: **75 passed in 7.50 seconds**, `/private/tmp/vos5-memory-final-independent.log` (68 API cases plus 7 integration/path/fault cases). Final rebuilt-image runtime result is still awaited.

## Final assessment: bounded runtime gate closed

Independently reviewed the final [runtime result](backend-runtime-2026-09-14/result.json) and both server logs. Image `sha256:4c9b1fe4be4ea44e9c86fe9b062dd67286a4bb137e8d01e4fa6d915972b91982` passed in **84.35 seconds**. Independently recalculated all 13 selected source hashes and the harness hash against the workspace; they match the recorded image/probe evidence, including the persistence failure correction.

The two distinct containers ran the real image command and completed startup and graceful shutdown. Actual HTTP probes returned healthy responses with the required local service flags, and protected requests returned 403 without the API key and 401 with the correct key but missing/malformed Bearer credentials. Strict discovery mounted 40 router groups and exposed 457 OpenAPI paths. Four separate missing-secret cases refused operation. Actual SQLite and Chroma records retained the exact synthetic marker and memory ID after container replacement; a second principal could not read, mutate, delete or retrieve that record. Both containers and the owned volume have recorded successful removal.

The reviewed harness rejects captured secret/content-canary leakage and redacts before writing logs; the successful result indicates those assertions passed. This is a tested-canary guarantee, not a universal proof that every possible logging path excludes secrets. Server logs contain noisy ASGI traces for deliberately rejected credentials despite correct 401 responses. They also report unavailable offline embedding models and failed internal Chroma additions; the storage probe explicitly supplies deterministic embeddings and verifies durable writes independently. These limitations remain visible and do not establish real-model or internal bug-tracker persistence readiness. Redis checkpointer remained false, as expected without a Redis service.

This closes the pending **offline production startup, authentication refusal, read-only nonroot filesystem and local persistence** gate for the identified image. It does not certify live Clerk/Convex integration, cloud deployment, downloaded model behavior, organization-wide isolation, multiworker JSON transactions, or universal OS/hardware security. The independent 75-test regression and injected Chroma failure review supplement this runtime evidence. No Docker execution or production edits were performed by this reviewer in this follow-up.
