# Independent backend container configuration review — 2026-09-14

Scope: final backend image and root Compose configuration, production JWT initialization, and integration of previously reviewed memory namespaces. Reviewer did not author the Docker/Compose, JWT production guard, or memory implementation. The earlier direct bcrypt migration was authored by this reviewer and is **not** independently audited by this document; its separate reviewer supplied that assessment. No production files were changed during this review. No applicable AGENTS.md was found in the workspace search.

## Configuration assessment

The reviewed image installs the hashed Linux dependency lock in `/opt/venv`, runs as uid 1000, and creates owned `/app/data` and `/app/.cache` directories. Compose mounts a named volume at `/app/data`, provides an owned cache tmpfs, makes the remaining root filesystem read-only, drops all capabilities, and enables no-new-privileges. Published API port 8000 is restricted to host loopback. The backend also joins `frontend_net`, so the internal second network does not eliminate outbound connectivity.

The previous runtime failure attempting to write `/data` is addressed by explicit `VOS_DEV_MEMORY_DIR` and `VOS_MEMORY_TENANT_ROOT` below `/app/data`. SQLite, the separate local Chroma resolver, application data, and model storage now have explicit paths under that volume. `XDG_CACHE_HOME` uses the writable cache tmpfs. Both image and Compose specify `ENVIRONMENT=production`; the Compose entry prevents an accidental development value from `.env` overriding the image default.

The final command uses one Uvicorn worker and `exec`. This removes the default four-process JSON fallback write race; it does not make JSON storage transactional or remove the retained-wrapper concurrency limit documented in the memory review. New named-volume initialization should preserve the image directory ownership. Existing volumes must already permit uid 1000 access; do not indiscriminately change ownership of unrelated operator data.

## Authentication and memory integration

The new JWT production guard rejects absent, empty and whitespace-only signing keys before creating the service. Configured keys remain unchanged, ensuring workers or restarts with the same external configuration can verify the same token. This does not measure operator-provided key entropy. Production must also supply its required Clerk and Tauri IPC handshake configuration. The CSRF handshake development fallback no longer logs its generated secret.

Independent execution of `backend/tests/test_auth_production_secret.py` passed all three tests in 12.05 seconds (`/private/tmp/vos5-jwt-independent-tests.log`). An additional fresh-interpreter experiment created a real access token in one process, verified its principal in another process with the same configured key, and confirmed rejection with a different key. Only synthetic credentials were used; no signing key or token was printed.

The separate [memory review](independent-memory-security.md) independently ran 74 passing tests, including real HTTP principal separation, JSON persistence, real Chroma collections, provenance forgery rejection, content-log canaries and configured-path precedence. The deployment paths preserve separate principal namespaces and quarantine the legacy shared store. Those tests override the authentication dependency and therefore do not establish end-to-end Clerk token validation. Existing internal global memory callers remain a documented boundary.

## Runtime qualification pending

The earlier `/private/tmp/vos5-backend-app-import.log` reports seven omitted routers from missing passlib and a read-only `/data` failure. That development-mode import result is obsolete evidence, not a passing production qualification. The final image must be rebuilt after these source changes and exercised with production configuration, uid 1000, a read-only root, and the actual writable mounts.

Pending final evidence from the root agent: successful strict production router discovery with the critical billing/team/GitHub/template/terminal/version-control/theme routers present; writable SQLite, per-principal memory and cache paths; persistence across restart using an isolated test volume; a working health endpoint; and missing-secret refusal. Health alone is insufficient because a development factory can swallow router import failures. No production services or external accounts were deployed by this reviewer.

## Final image runtime result: production startup blocked

The final image built successfully with manifest-list digest `sha256:d049f4ab13f0463b46823b31f4c59107d64bc3a492883dde2687d945fdeb26c6`. The root agent also validated final Compose syntax using a temporary copy with a synthetic env file; no real `.env` or live deployment was required.

Executed the final image with `--network none --read-only --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges`, ephemeral owned `/app/data` and `/app/.cache` tmpfs mounts, and `/tmp` tmpfs. Random transient JWT/IPC secrets and synthetic Clerk configuration were set inside the process. The uid, all configured storage/cache directory writes, and an actual SQLite table/write passed. This proves writable path configuration under the tested mounts; it does not test named-volume restart persistence.

**Strict production router discovery then failed**, correctly refusing partial startup:

`api.billing_routes` → `tools.stripe_service` → eager `tools/__init__.py` → `tools/realtime.py` → `ModuleNotFoundError: No module named 'socketio'`.

The final minimal image dependency profile therefore still lacks a dependency required by its mounted production router graph. Resolve the `python-socketio` dependency/profile or eager package initialization coherently, rebuild, and repeat strict discovery before describing the backend as production-ready. Do not disable strict discovery to make the health endpoint pass. No authentication or memory isolation failure was observed in this attempt; execution stopped before the application factory and HTTP checks.

Evidence: `/private/tmp/vos5-backend-independent-container.log`, command exit 1. The Docker daemon was initially slow, but the command ultimately completed with the concrete application failure above. The disposable container used `--rm` and left no intentionally running workload. Full application health/lifespan, named-volume restart persistence and end-to-end external authentication remain unverified. No real credentials, external network requests or user data were used.
