# Backend runtime qualification — 2026-09-14

The pending offline backend runtime gate is closed for image
`sha256:4c9b1fe4be4ea44e9c86fe9b062dd67286a4bb137e8d01e4fa6d915972b91982`.
The final run passed in 84.35 seconds. Independent
[security review](backend-runtime-security-review.md) and
[mathematical/invariant review](backend-runtime-invariants.md) accepted the
evidence within the scope below. No additional OS feature work was started.

## What was verified

- The image's real Uvicorn command completed startup and graceful shutdown in
  two different containers, under uid 1000 with a read-only root, dropped
  capabilities, no-new-privileges, no external network and no published ports.
- Strict production discovery loaded all 40 registered router groups. The
  generated OpenAPI document contained 457 paths. Thirteen selected source,
  configuration and lock-file hashes matched the host files exactly; the
  harness hash and immutable image ID are recorded.
- Both servers returned HTTP 200 from `/health` with the required core-service
  presence flags. Separately, a protected memory request returned 403 without
  an API key, 401 with the correct key but no Bearer token, and 401 with an
  invalid token. Missing JWT, IPC, Clerk and API secrets each caused refusal.
- A real SQLite `User` row and a real Chroma-backed DevMemory entry survived
  graceful shutdown and replacement of the container. The second container
  read the same content, memory ID and marker hash from the same owned volume.
- A separate principal's store could not read, enumerate, update or delete
  the first principal's entry. This is storage-level isolation, not a positive
  end-to-end Clerk authentication test.
- Both containers and the temporary volume were removed. A separate Docker
  label query confirmed zero remaining resources belonging to this run.

## Persistence correction and regression checks

Review found that `DevMemory.add` could return an entry after a failed Chroma
write even though it had not persisted the entry. It now returns `None` and the
HTTP storage handler returns 500. A failed write does not silently switch to
JSON and hide an existing vector store. The regression injects a real Chroma
write failure, verifies preservation of the previous record, absence of a JSON
fallback or content in logs, and successful recovery after the fault clears.

All **75 affected memory tests** passed independently. Three additional host
runner tests cover server-side resource creation followed by client timeout,
log-canary detection during cleanup, and preservation of resources with a
different ownership label. Cleanup does not depend on successful log capture.

## Reproduction and retained evidence

Build the backend image from the reviewed tree, then run:

```sh
docker build --platform linux/amd64 -t vos5-backend:runtime-qualified backend
python3 scripts/backend_runtime_smoke.py \
  --image vos5-backend:runtime-qualified \
  --output /private/tmp/vos5-runtime-new-run
python3 -m unittest scripts/test_backend_runtime_smoke.py -v
.venv-upgrade/bin/python -m pytest \
  backend/tests/test_api_memory.py \
  backend/tests/security/test_memory_principal_isolation.py -q
```

The output directory must be new. Docker access is required. Each command and
startup/probe phase has a timeout; the runner does not claim one global
120-second deadline. It uses generated synthetic credentials and only removes
resources created for the run. The application must remain in production mode.

The durable [result](backend-runtime-2026-09-14/result.json),
[cleanup confirmation](backend-runtime-2026-09-14/cleanup.json), server logs and
independent test transcripts are in `backend-runtime-2026-09-14/`.
The staged secret scan reported six source-file hashes as generic API keys;
each was independently recomputed from the named source file and classified
as non-credential material. The [review record](backend-runtime-2026-09-14/secret-scan-review.json)
retains all findings without suppressing them.

## Scope limits

Clerk/Convex configuration is synthetic and external networking is disabled.
Live identity verification, cloud database operations and deployment were not
tested. Chroma persistence uses a deterministic embedding adapter only in the
probe process; no downloaded model is qualified. The application logs show
offline embedding/Chroma errors and unavailable Ollama, as expected without
model assets. Redis checkpointing is not enabled in this profile.

The missing/invalid Bearer requests receive 401 but also produce ASGI exception
tracebacks. These are retained in the logs; this is not an error-free runtime
claim. No full secret/content canary appeared in the captured output. Successful
serial replacement does not prove multiworker JSON transaction safety, global
tenant isolation, or full production readiness. Existing dependency advisories
and independent-kernel/hardware qualification gaps remain separate work.
