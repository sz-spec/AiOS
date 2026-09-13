# Ancillary Node dependency migration — 2026-09-14

Scope: `sdk/typescript`, `backend/code_review`, OmniStock frontend/backend, backend Convex CLI profile, desktop Tauri CLI profile. MCP server source and primary frontend are owned by other agents. No external reviews/messages were sent and no cloud deployment was performed.

## Package integration and validation

All six profiles passed `npm ls --depth=0`. Direct dependency/devDependency/engine declarations match their lockfile root metadata. Missing backend, OmniStock backend and desktop installations were completed with locked `npm ci --ignore-scripts`; sandbox DNS denial required approved network retries. OmniStock installation reports two extraneous optional platform helper packages; direct requested versions are present. This is not a claim that all transitive packages equal their independent latest release.

- **SDK:** TypeScript 7.0.2 builds successfully. Changed module emit/resolution to NodeNext, consistent with the package's existing CommonJS metadata, so ordinary Node consumption works instead of emitting unresolved extensionless ESM imports. Replaced placeholder test script with **2 real tests** loading the emitted package: authenticated request serialization/denied response and asynchronous hooks/scope declarations. No live network call.
- **Code-review webhook:** TypeScript 7.0.2 build passes. **4 real tests** pass: exact-byte HMAC validation and tamper rejection, malformed signatures/missing secret, actual server startup refusing absent secret before service initialization, and Bull timer UUID generation/cleanup. Server now requires `GITHUB_WEBHOOK_SECRET`; absence can no longer disable verification. Comparison accepts only a correctly shaped SHA-256 tag and compares equal-length bytes with `timingSafeEqual`. Signed malformed JSON returns 400. Existing external GitHub/Anthropic/Redis flows were not exercised.
- **Webhook advisory remediation:** initial npm audit found two moderate entries through Bull's UUID dependency. Scoped override selects registry-latest UUID **14.0.2** only under Bull. Source inspection found Bull uses `uuid.v4()`; the timer test exercises that actual call. Fresh `npm audit --package-lock-only --json` reports **0 advisories**, `/private/tmp/vos5-code-review-final-audit.json`. This does not substitute for a live Redis job-processing test.
- **OmniStock frontend:** Next 16.3.5 / React 19.3.0 production build passes and prerenders 11 pages. Explicit `turbopack.root` removes accidental parent workspace inference. The sandbox prevented a build worker binding a port; approved retry passed. Log: `/private/tmp/vos5-omnistock-verified-build.log`. Latest lint rules exposed a component defined during render; moved the sort indicator to a stable top-level component and removed unused imports. Final lint, TypeScript no-emit and rebuilt production output pass. TypeScript 6.0.3 and ESLint 9.39.5 are explicit framework compatibility exceptions to latest major versions.
- **Convex profiles:** both CLIs launch and report 1.45.0. OmniStock's checked-in Convex functions reference an absent `_generated/server`; no fabricated generated bindings or cloud codegen/deploy was performed. Backend root profile has no local Convex function directory. These are install/CLI checks, not backend application build proofs.
- **Desktop:** installed CLI launches and reports 2.11.4. Native Rust and missing release assets are covered by the separate Rust migration evidence, not this npm check.

The webhook's previous lint command has no usable ESLint configuration; it remains an explicit unresolved lint setup. No empty test/placeholder command is counted as validation. Added the missing `Dockerfile.webhook`, using Node 26.8.2 multi-stage locked installation and the non-root node user; the image build passed (`/private/tmp/vos5-code-review-docker.log`, local manifest `sha256:58b8e6a67bd688534853b11378e1ca7f619f7ded62cd0af9b2d832f92e201ffd`). Live Redis/job execution remains a separate gate.

## Compose dependency pins and security boundary

Verified current stable release tags against official release APIs/pages:

| Component | Selected image/version | Primary source |
|---|---|---|
| Redis | `redis:8.10.1-alpine` | [Redis 8.10.1](https://github.com/redis/redis/releases/tag/8.10.1) |
| Jaeger | `cr.jaegertracing.io/jaegertracing/jaeger:2.20.0` | [Jaeger 2.20 setup](https://www.jaegertracing.io/docs/2.20/getting-started/) |
| Prometheus | `prom/prometheus:v3.14.0` | [Prometheus 3.14.0](https://github.com/prometheus/prometheus/releases/tag/v3.14.0) |
| Grafana | `grafana/grafana:13.2.1` | [Grafana 13.2.1](https://github.com/grafana/grafana/releases/tag/v13.2.1) |
| Ollama | `ollama/ollama:0.34.0` | [Ollama 0.34.0](https://github.com/ollama/ollama/releases/tag/v0.34.0) |

Jaeger 2 replaces the archived 1.x all-in-one image using its documented default all-in-one mode and the same query/OTLP ports; the obsolete `COLLECTOR_OTLP_ENABLED` setting was removed. Its trace data remains transient. Metrics integration and persisted Redis/Prometheus/Grafana data migration have not been qualified. Redis licensing changes across major versions remain subject to the project's license review.

**Bull Board:** migrated the optional profile to maintained official `ghcr.io/felixmosh/bull-board:9.10.1`, replacing the legacy wrapper. The official CLI discovers both Bull and BullMQ queues under the `bull` prefix. Redis URL, bind address, browser suppression and required credentials use its supported `BULL_BOARD_*` variables. Existing loopback port 3002 and optional monitoring profile are retained. [Pinned official Docker guide](https://github.com/felixmosh/bull-board/blob/v9.10.1/website/docs/guide/docker.md), [release](https://github.com/felixmosh/bull-board/releases/tag/v9.10.1).

MCP Compose now requires externally supplied `MCP_AUTH_TOKENS`, sets the supported cache directory to `/tmp/vos-mcp-cache` under its tmpfs, and sets container binding explicitly while published ports remain loopback-only. Grafana's hardcoded admin password was replaced with a required environment value. Webhook Compose requires its signing secret; ancillary published ports are loopback-only. The webhook healthcheck uses built-in Node fetch instead of depending on missing curl. Optional Bull Board credentials are validated by Compose interpolation even when that profile is disabled.

Both Compose files pass `docker compose ... config --quiet` with deliberately dummy values. Real secrets were neither generated nor logged, no project stack was deployed, and these syntax checks do not demonstrate image runtime compatibility, advisory cleanliness or production authentication throughout monitoring services. Image tags are explicit but not digest-pinned. The existing monitoring configuration also points Redis scraping at its protocol port and expects MCP `/metrics`; this migration does not claim either scrape path is implemented.

The official Redis image manifest was independently resolved to `sha256:becdda6c7f4b3fb42e42fd7f120bbf5c54c4caaaf16f26da24e4563d2c1f0576`; other listed versions were verified against their upstream releases/docs, not fully pulled and exercised. Final root `scripts/check_node_locks.py` passed all 8 tracked Node profiles.

A disposable webhook container with `--network none --read-only`, no ports and no secrets exited 1 with the expected missing signing-secret error. This confirms the image entry point fails closed before starting its external services; it is not a positive webhook/Redis integration test.


## Maintained Bull Board image follow-up

Pulled official image 9.10.1 with digest `sha256:4f520bd06e4dcee9aa6d500983ed1fb570715318d97f1d17a702f79dddc43a4f`. Its upstream Dockerfile uses the non-root node user and Node 22 Alpine, whereas the locally maintained webhook image uses Node 26.8.2. No claim is made that the upstream image's transitive runtime packages independently equal latest versions.

`backend/code_review/tests/bull_board_smoke.py` is the repeatable manual integration check. It creates only uniquely named temporary Redis/dashboard containers, uses Redis's network-none namespace for both, publishes no host ports, seeds an actual Bull code-reviews job and removes both containers in a finally block. Checks cover unauthenticated/wrong-password denial, authenticated HTML/API access, discovery of the existing Bull queue, and Compose rejection of missing dashboard credentials. Execution passed: unauthenticated HTML 401, wrong password 401, authenticated HTML 200, Bull code-reviews queue visible through the authenticated API, unauthenticated queue API 401, and missing-credential Compose validation failed as expected. Log: `/private/tmp/vos5-bull-board-smoke.log`. Both temporary containers were removed.
