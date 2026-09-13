# MCP dependency migration — 2026-09-13

Scope: `backend/mcp-server`. Registry-selected exact versions and npm lock were updated by the coordinator. This pass validated migration using Node 26.8.2 and the installed lock. TypeScript 6.0.3 is an explicit compatibility exception to latest, because the selected TypeScript ESLint packages do not support TypeScript 7.

Changes:
- Retained Mistral routing and added its existing provider to the model types and runtime Zod schema. Previously the registry contradicted its type and schema.
- Added `.js` to relative imports backed by TypeScript files, enabling emitted ESM modules to load in Node without bundler-only extension resolution.
- Raised declared Node support to the selected Vitest engine requirement: `^22.12.0 || ^24.0.0 || >=26.0.0`, synchronized in package lock root metadata.
- Added three real regression tests exercising routing/validation and quota limits. Existing six placeholder tests remain; they do not prove server behavior.

Validation:
- `npm run build`: passed.
- `npm run typecheck`: passed.
- `npm test -- --run`: 2 files, 9 tests passed (including 3 new behavioral tests).
- Node import of emitted `dist/orchestration/cost-aware-router.js` and actual routing: passed, `complexity_simple`.
- `npm audit --json`: 0 total advisories in this installed dependency graph. Query required network approval after sandbox DNS denial. This is an advisory snapshot, not a security proof.
- `npm run lint`: FAILED because no `eslint.config.*` exists. No suppression configuration was added to manufacture a pass.

Security observations and limitations:
- Zod 4 continues to reject unrecognized provider values, string costs and NaN in routing decisions. Existing `z.object` schemas strip extra fields; strict rejection of unknown keys was not previously provided and is not claimed here.
- `src/server.ts` has no authenticate callback. Its primary tool uses `default-user` around line 160. Quota lookup accepts userId around line 497 and quota mutation around line 541 has no enforced admin check. An “admin only” description is not authorization. This preexisting deployment blocker is escalated to the coordinator for dedicated security work.
- No public server was started and no model/provider API calls were issued. HTTP transport, auth, live provider SDK behavior, Redis integration, cloud credentials and Docker image execution remain unverified. Build success alone does not validate these.
- Model names/prices in the static registries remain legacy data; this dependency update does not assert their currency or availability.
- Existing relaxed TypeScript settings and excluded source files remain visible in tsconfig; passing tsc covers only that configured source set.
