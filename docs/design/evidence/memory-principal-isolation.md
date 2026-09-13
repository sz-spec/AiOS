# Memory API principal isolation — 2026-09-14

The authenticated `/api/memory` router previously used one global DevMemory store. Every data operation now obtains a store through the server-verified `AuthenticatedUser.id`. Missing/non-string/blank identity fails with HTTP 401 before creating a store; the router-level guard also covers non-data helper endpoints.

`get_user_dev_memory(user_id)` hashes the complete principal with SHA-256. The hash determines both a dedicated persistence directory and a dedicated Chroma collection name. User text, arbitrary metadata (including forged owner IDs), insight IDs and imported IDs cannot select another principal's namespace. Default storage is `data/memory-tenants/v1/<sha256>/`; trusted deployment configuration may change its parent with `VOS_MEMORY_TENANT_ROOT`. New directories use restrictive permissions and a namespace symlink is rejected.

All 16 existing data-handler call sites were switched: statistics, store/add, similar/duplicate detection, consolidation, query, recent, clear, quick remember/recall, get, update, delete, bulk delete, export and import. The same namespace isolates real Chroma and JSON fallback. JSON fallback now implements its previously missing get/update/delete/export/bulk-delete/clear operations; writes replace a restrictive temporary file atomically so readers do not see partial JSON. Chroma delete/count responses now reflect actual existing IDs rather than treating unknown IDs as deleted.

## Legacy data and remaining trust boundaries

Legacy `data/memory` is untouched and never assigned to the first HTTP caller. It is quarantined from the new API namespace by separation, not physically moved/deleted. Migration requires a trusted ownership manifest and explicit review; user-supplied ownership metadata is not evidence. Trusted internal `get_dev_memory()`/`remember()` callers remain API-compatible with their legacy shared store. Other agent/tool call paths still using that singleton must propagate principal context before claiming end-to-end tenant isolation. This fix is scoped to the memory HTTP router.

The application wrapper cache is an LRU capped at 16 principals; shared embedding model weights/configuration are cached separately (up to four models), without caching user prompts or embedding results. Eviction preserves disk data and re-opening the same principal recovers it. **Chroma's upstream global System registry retains path-specific systems beyond wrapper eviction**, so this is not a total native-memory/resource bound. A coordinated connection lifecycle/pool design remains needed; stopping a shared system while requests hold references would be unsafe, and silently falling back for an existing vector user could hide data.

JSON mutation locking protects a single instance and atomic replacement prevents torn files. It does not provide cross-process transactions or a global lock across simultaneously retained wrappers after eviction. Multi-worker concurrent write durability needs a transactional backing store or cross-process locking. This limitation does not combine separate principal paths, but can cause lost updates within one principal. Cache directories and deployment environment remain trusted local inputs; arbitrary service-account filesystem access is outside the HTTP isolation boundary.

## Validation

Tests execute actual FastAPI memory handlers with a dependency stub only for authenticated identity:

- Alice/Bob isolation for get/similar/query/recent/stats/consolidation/export/update/delete/bulk-delete/clear.
- Forged owner metadata/imported IDs cannot select another store; duplicate search cannot disclose another principal's content.
- JSON persistence survives LRU eviction/re-opening; path-like principal IDs hash to contained directories; old global records remain unchanged/unavailable through the new API.
- Missing identity rejects before store allocation.
- Real installed Chroma clients/collections pass a two-principal read/query/delete/clear test with deterministic test embeddings, no remote model download, and an assertion preventing silent JSON fallback.

The existing test harness previously rewrote *all* explicit persistence paths to one worker directory. Its fixture now overrides only omitted paths, preserving explicit namespace tests. Existing memory API mock wiring targets the new principal-aware factory; its assertions are unchanged. Final combined results are recorded in `/private/tmp/vos5-memory-isolation-final.log`.

## Independent-review follow-up: metadata trust and log privacy

HTTP callers can no longer claim `trust_level=system` or `verified`. A common metadata sanitizer assigns the existing untrusted enum value `external`, the authenticated `actor_id`, `source=memory_api`, and `model_origin=unknown`. It applies to store, add, update, import and quick remember; benign metadata remains available. Imported non-object metadata fails with 422 before importing any entries. Trusted internal DevMemory callers retain their original explicit provenance interface.

Removed memory-add content previews from logs; successful adds log only the generated ID. Memory exception logs now include the exception type instead of backend error payloads that may contain documents. Invalid memory-type warnings no longer echo caller input.

Final combined run passed **73 tests** (68 existing API tests plus 5 isolation/trust tests), logged in `/private/tmp/vos5-memory-isolation-trust-tests.log`. Actual HTTP tests try forged system/verified trust and foreign actors across creation/update/import and assert server-assigned untrusted provenance. The real Chroma test captures logs and verifies the private document canary is absent. This is scoped to DevMemory logging, not a repository-wide logging audit.

## Container path configuration

Legacy/internal DevMemory now resolves storage in this order: explicit `persist_dir`, trusted `VOS_DEV_MEMORY_DIR`, then the unchanged source-tree legacy default. The container must explicitly configure writable persistent storage; with the backend installed directly at `/app`, relying on source-tree-relative parent traversal would select `/data`. Root deployment configuration uses `VOS_DEV_MEMORY_DIR=/app/data/memory` and `VOS_MEMORY_TENANT_ROOT=/app/data/memory-tenants/v1`; a read-only image requires a writable owner-controlled mount at the configured data location. No data is migrated by setting these paths.

The additional resolution test verifies environment precedence, explicit-path precedence and the unchanged fallback, without downloading an embedding model.
