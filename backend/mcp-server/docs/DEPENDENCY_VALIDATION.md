# Dependency integration validation — 2026-09-13

Validation used Node **26.8.2**, with dependencies locked in `package-lock.json`.

| Check | Result |
| --- | --- |
| `npm run build` | passed |
| `npm run typecheck` | passed |
| `npm test -- --run` | **18 passed**, 3 test files |
| `npm ls --all` | passed; no invalid peer dependency tree |
| `npm audit --json` | **0 known advisories** at check time |
| Manifest/lock direct dependencies, dev dependencies, engines, binary entry | coherent |
| Emitted main/types/CLI files and executable shebang | present |
| `npm run lint` | **fails: 38 pre-existing findings in 4 historical files** |

The manifest's old `dist/index.js` / `dist/cli.js` entries did not exist. Main/types/bin now reference the built server entrypoint, with a Node shebang. HTTP and stdio startup require explicit identity configuration described in README.

The new ESLint 10 flat configuration uses TypeScript ESLint recommended rules plus equality, unreachable-code, debugger, duplicate-case and constant-condition checks. It includes **all 16 source TypeScript files**, including files excluded by the older TypeScript build. No blanket rule suppression or production-code lint exclusion was added. Active server/auth/router modules are lint-clean after unused imports/variables and the Python response type were corrected.

Remaining baseline:

| File | Findings |
| --- | ---: |
| `src/framework/caching/tool-registry-cache.ts` | 17 |
| `src/server-base.ts` | 9 |
| `src/server-v1.ts` | 9 |
| `src/orchestration/tests/test-context-escalation.ts` | 3 |

These findings concern explicit `any`, unused declarations, and old `@ts-ignore` comments. They remain errors so the lint gate honestly exposes the migration backlog. Historical alternative server/cache implementations require a dedicated typed migration and runtime qualification before re-enabling them in the build; this integration did not replace or silently delete them.

Logs: `/private/tmp/vos5-mcp-auth-full-tests.log`, `vos5-mcp-auth-build.log`, `vos5-mcp-auth-typecheck.log`, `vos5-mcp-peer-tree.log`, `vos5-mcp-final-audit.json`, and `vos5-mcp-lint.json` in the same temporary directory. Test sockets bind loopback only and required sandbox permission; no external provider calls were made by the auth tests.


## Container integration

All Docker stages now use `node:26.8.2-alpine`; the upstream tag resolved and the image built successfully. Production `npm ci --omit=dev` and high-severity audit failures are fatal (removed the old shell fallback that could mask installation failures). The image explicitly binds all container interfaces while ordinary process startup defaults to loopback; the same mandatory bearer configuration applies. Cache storage is configurable through trusted `MCP_CACHE_DIR` and defaults to a writable `/tmp/vos-mcp-cache` in Docker.

Actual image `vos5-mcp:node26-review` smoke passed: no-credential startup exits nonzero; configured server runs as non-root with read-only root and tmpfs; published loopback host port responds to `/health` with only `ok`; missing bearer gets 401; correct bearer resolves the configured principal; temporary cache is writable. The owned container was stopped after testing. Evidence: `/private/tmp/vos5-mcp-container-smoke.json` and `/private/tmp/vos5-mcp-docker-build.log`. This does not test hosted model inference or full image vulnerability scanning beyond the npm advisory gate.
