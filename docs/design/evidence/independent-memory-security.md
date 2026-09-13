# Independent memory namespace security review — 2026-09-14

Reviewer did not author the memory implementation and made no memory production changes. Scope: the memory HTTP router, principal store factory, JSON/Chroma persistence selection, and application cache changes. No real user data was read, assigned ownership, migrated or deleted.

## Assessment

No cross-principal fallback was found in the reviewed HTTP path. All 18 routed handlers inherit the authenticated-principal guard; static AST enumeration confirms that all 16 data handlers obtain their store through `_principal_memory(user)`. The other two handlers only return keyword tags/type definitions. Store selection uses the verified dependency's user ID, never request content, metadata, imported IDs or path IDs.

The complete principal ID is SHA-256 hashed for both the persistence directory and Chroma collection. The cache key additionally includes the resolved trusted parent directory. File-like IDs therefore do not become path components, and different configured storage roots do not accidentally share a cached instance. A blank/non-string principal fails before namespace creation. This assumes the authentication layer supplies stable, globally unambiguous principal IDs; it is not an independent audit of token verification or development-mode identity fallback.

JSON reads, queries, statistics and mutations resolve under the instance's selected persistence directory. Chroma uses a distinct persistent path and collection for each principal. Import regenerates entry IDs through `add`, so an imported identifier cannot select an entry in another namespace. No caller-supplied owner metadata changes this selection. The legacy global singleton remains separate and is not adopted for missing or new user stores.

## Independent validation

Executed `.venv-upgrade/bin/python -m pytest backend/tests/security/test_memory_principal_isolation.py -q`: **4 passed in 21.30 seconds**, `/private/tmp/vos5-memory-independent-tests.log`.

These tests use actual HTTP handlers and real JSON persistence. Only the identity dependency is replaced with a test principal. They cover cross-owner ID reads/updates/deletes, query/export/stats/consolidation, forged owner metadata, import, duplicate detection, clear, persistence after LRU eviction, path-like principal input and legacy-store quarantine. The Chroma case uses real installed Chroma clients/collections with deterministic embeddings and explicitly asserts Chroma initialized; it cannot silently count JSON fallback as a Chroma pass.

Reviewed the author's 72-test combined result: 68 existing mocked API tests plus the four integration cases. The mock factory now accepts the principal argument; existing assertions were not removed. The shared fixture change preserves explicit persistence directories and only redirects omitted/default directories into the test area. This corrects a fixture that previously collapsed distinct stores; it does not manufacture namespace isolation in production.

## Cache and persistence limits

- Application LRU is bounded to 16 store wrappers, but upstream Chroma can retain path-specific systems beyond wrapper eviction. It is not a proven bound on total native resources.
- Shared embedding cache contains up to four model objects keyed by model name. Reviewed application code caches no prompts, embeddings or retrieval results globally. A full audit of upstream model-library internals/machine memory is outside this review; model reuse alone is not a formal noninterference proof.
- JSON temporary-file replacement prevents partially written reads. The RLock only serializes mutations through one instance; another process or a retained evicted wrapper can still race and lose same-principal updates. No transaction/durability claim is made.
- Namespace symlinks are rejected, but hostile concurrent modification by an actor with service-account filesystem access is outside the reviewed HTTP boundary. Trusted environment root selection, ownership/permission provisioning and filesystem integrity remain deployment assumptions.
- Legacy internal `get_dev_memory()` / `remember()` callers retain a shared store. They must propagate principal identity before asserting system-wide tenant isolation. User IDs are not organization partitions; organization-specific sharing/separation needs an explicit policy.

## Follow-up: both scoped findings corrected and independently reviewed

The memory author corrected the two findings without changing the trusted internal memory interface:

1. Successful Chroma insertion logs only the generated entry ID, not a content preview. Memory module error logs now report exception types instead of exception messages that may contain payloads. The real Chroma test captures logs and asserts its private-content canary is absent.
2. `_http_memory_metadata` copies supplied metadata and unconditionally sets `trust_level=external`, `actor_id` from the authenticated user, `source=memory_api`, and `model_origin=unknown`. Store, add, update, import and quick-remember all use the helper. Import sanitizes the complete input list before invoking persistence; malformed metadata objects receive 422. Ordinary custom metadata remains supported.

Independent review confirmed helper ordering and that neither import nor update can restore caller-provided values for those four provenance fields. Strengthened the actual HTTP regression to inspect store/add responses **before** the update, require a non-empty imported result, assert all four fields after import, and exercise quick-remember. The existing tests continue to check update forgery, owner isolation and invalid metadata rejection. No production code was changed by this reviewer.

A final author change adds trusted operator configuration for the legacy default persistence directory. `_resolve_dev_memory_dir` selects an explicit path first, then `VOS_DEV_MEMORY_DIR`, then the legacy default. Principal factory paths remain explicit, so this does not redirect users to the shared store. The test calls the actual resolver for omitted-path behavior because the existing global fixture intentionally redirects omitted constructor paths; it also tests an explicit constructor path. No additional fixture weakening was introduced.

The earlier independent five-case run passed in 6.70 seconds. The final combined rerun including strengthened assertions and the new path-resolution test is logged at `/private/tmp/vos5-memory-trust-independent-tests.log`; **all 74 tests passed in 7.44 seconds** (6 integration/path checks and 68 existing API tests). This closes the two identified code paths, not the unrelated resource/concurrency/authentication-layer limits above. Internal trusted callers may still assign provenance, so that privilege must not be exposed directly to untrusted input.
