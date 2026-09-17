# Cold health routing memory review — 2026-09-17

Reviewer: `/root/math_build_review`, who authored this routing correction and its focused regression; root separately reviewed the initial GET change. This report is not an independent security certification of the application.

The unchanged cold 1,000-GET health test initially retained 5,559,252 bytes (5.30 MiB), exceeding its 5 MiB limit. Included FastAPI router branches lazily materialized unrelated dependency/response schemas before matching the late-registered health route. Moving the unchanged GET handler before those branches reduced the diagnostic cold retained delta to 141,765 bytes. No request warmup or memory threshold change was introduced.

A broader run exposed the same issue for POST: a GET-only partial match continues through unrelated branches before returning HTTP 405. An exact-path ASGI rejection route now follows GET /health and precedes the included branches. Non-GET requests still use the normal structured HTTP exception handler and application middleware. The handler now preserves exception headers, including Allow; this also preserves authentication challenge headers supplied by other exceptions.

Validation: **30 passed in 43.76 seconds** — all 12 unchanged memory tests, eight actual-app method-routing cases, and ten middleware checks. The routing cases cover GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS, and an unknown method; they verify status, Allow on rejection, security headers, zero unrelated branch matches for health, and continued branch matching for other requests. A separate fresh-process run of the unchanged 500-POST/5-MiB test passed in 24.56 seconds, excluding dependence on a prior GET request. Initial GET profiling preceded the subsequent non-GET guard/header change; the expanded final run rechecks the unchanged cold GET requirement against the final source.

Scope: health-path retained Python allocations, not total RSS or memory behavior of every API. A real API request may still initialize required schemas. HTTP health remains liveness metadata, not proof of lifespan readiness. No authentication bypass, middleware removal, threshold increase, fixture priming, or request-count reduction was used.

## Reviewed source SHA-256

- [backend/app.py](../../../backend/app.py): `2df8a0be992ead995a518b2b173c61caa7e36520aa436048382f016ccf0a3fc8`
- [backend/tests/perf/test_health_routing.py](../../../backend/tests/perf/test_health_routing.py): `adb2d40dafec9424f25bbe3a926fed3fa08b84c0e1bae109dfd13916c360e58a`
- [backend/tests/perf/test_memory_leaks.py](../../../backend/tests/perf/test_memory_leaks.py): `f7dabad0bcf40b356af368926f09ce14b50fac7c72811ef9c077216c72d2596e`
- [backend/tests/integration/test_middleware_chain.py](../../../backend/tests/integration/test_middleware_chain.py): `0e732e196d45232f98950d461a33b0169e5d53d53e3db5cfd7766bd6259b2036`

## Preserved evidence SHA-256

- [cold-get-before.log](health-routing-memory-2026-09-17/cold-get-before.log): `11e44738717aa67e0a7ba497107224df41f21266b471bdfa06f4c4183d90b1a3`
- [cold-get-after.log](health-routing-memory-2026-09-17/cold-get-after.log): `020c1c16dadf4a17421a695509bba7e6b406963eb34273f72e248b1d0c625445`
- [cold-get-profile.json](health-routing-memory-2026-09-17/cold-get-profile.json): `52a4cf76df78add230d11e4f7380acf7cdb9f25762a619209e9cca83cb422602`
- [expanded-tests.log](health-routing-memory-2026-09-17/expanded-tests.log): `11ffc19cbabc872ea37b9dadfb2c02ccc59e54bb57323232eea451314eaa3e87`
- [cold-post.log](health-routing-memory-2026-09-17/cold-post.log): `ccf8c5462b71f587fc77d2e621ba773b918768eacf758b142f8cfe858bc09edf`
