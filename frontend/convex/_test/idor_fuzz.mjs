#!/usr/bin/env node
/**
 * W3.1 — Static IDOR fuzzer for Convex query handlers.
 *
 * Scans every `frontend/convex/*.ts` module for `export const X = query({...})`
 * blocks. Any query whose `args` schema includes a user-scoped identifier
 * (`userId`, `projectId`, `developerId`, `organizationId`, `appId`, etc.) MUST
 * call one of the authHelpers (`requireMatchingUser`, `requireOwnership`,
 * `requireProjectOwnership`, `requireOrgMember`) inside its handler. Plain
 * `requireAuth(ctx)` alone is NOT sufficient — that proves the caller is
 * authenticated, not that they own the requested resource.
 *
 * Why static fuzz rather than runtime?
 *   A real runtime fuzz needs `npx convex dev` running plus seeded data;
 *   it can't run in the CI pre-merge gate without a live deployment. This
 *   static check runs in millisecond range, catches new IDOR holes the
 *   moment they're added, and is auto-discovering (no allow-list to
 *   maintain).
 *
 * Run:
 *   node frontend/convex/_test/idor_fuzz.mjs
 *
 * Exit codes:
 *   0 — every user-scoped query is gated by an ownership helper
 *   1 — at least one query reads scoped data with no ownership check
 */

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CONVEX_DIR = resolve(__dirname, "..");

// ---------------------------------------------------------------------------
// Files that are exempt by design (no user-scoped data, or test scaffolding).
// ---------------------------------------------------------------------------
const EXEMPT_MODULES = new Set([
  "schema.ts",
  "auth.config.ts",
  "authHelpers.ts",
  "webhook_seen.ts",   // server-side idempotency table; no user surface
]);

// ---------------------------------------------------------------------------
// Specific (file, query-name) pairs that are intentionally public.
// EVERY entry here is a deliberate product decision — additions require a
// security-team comment justifying the public access.
// ---------------------------------------------------------------------------
const PUBLIC_QUERIES = new Set([
  // App marketplace reviews are public-by-design (Amazon-style storefront).
  "appReviews.ts::list",
  "appReviews.ts::averageRating",

  // -------------------------------------------------------------------------
  // W4.3 — TODO_TRIAGE resolution (security-team approved).
  // -------------------------------------------------------------------------
  // `developers.getProfile`
  //   The marketplace UI ("about this developer" cards) needs a public
  //   profile lookup. W4.3 refactored the handler to enforce a strict
  //   projection: non-owners receive a hardcoded allow-list of
  //   { displayName, bio, website, verified, totalApps, _id, _creationTime }.
  //   The sensitive columns — email (PII), stripeConnectId (financial
  //   routing), totalEarnings (financial PII), and the internal userId
  //   linkage — are NEVER returned to anyone except the owner themselves.
  //   The projection is built from a literal-keyed object, so adding a
  //   new sensitive column to the developers table cannot accidentally
  //   leak through this API. See developers.ts::getProfile body.
  "developers.ts::getProfile",

  // `users.getByClerkId`
  //   Used by @-mentions, collab presence panels, and any UI that
  //   resolves a user reference into a display card. W4.3 refactored
  //   the handler to enforce a strict projection: non-owners receive a
  //   hardcoded allow-list of { clerkId, displayName (from fullName),
  //   avatarUrl, lastSignInAt, _id, _creationTime }. The sensitive
  //   columns — email (PII) and metadata (admin/internal flags such as
  //   `deleted: true` markers and webhook-attached billing references)
  //   — are NEVER returned to non-owners. The projection is literal-
  //   keyed; adding a new sensitive column to the users table cannot
  //   accidentally leak. See users.ts::getByClerkId body.
  "users.ts::getByClerkId",
]);

// ---------------------------------------------------------------------------
// (file, query-name) pairs still under triage. Tracked so the fuzz reports
// them separately from real violations, but does not fail the build until
// the security team makes a call. Empty as of W4.3 — every previously-
// pending query has either been moved into PUBLIC_QUERIES with a security
// justification, or has had an ownership helper added inside its handler.
// ---------------------------------------------------------------------------
const TODO_TRIAGE = new Set([]);

// ---------------------------------------------------------------------------
// Argument names that indicate user-scoped data.
// ---------------------------------------------------------------------------
const SCOPED_ARG_PATTERNS = [
  // arg-name + Convex type, matching the v.id("...") declarations
  { re: /userId:\s*v\.(?:id\("users"\)|string\(\))/, scope: "userId" },
  { re: /clerkId:\s*v\.string\(\)/,                   scope: "clerkId" },
  { re: /projectId:\s*v\.id\("projects"\)/,            scope: "projectId" },
  { re: /developerId:\s*v\.id\("developers"\)/,        scope: "developerId" },
  { re: /appId:\s*v\.id\("apps"\)/,                    scope: "appId" },
  { re: /organizationId:\s*v\.id\("organizations"\)/,  scope: "organizationId" },
  { re: /ownerId:\s*v\.id\("users"\)/,                  scope: "ownerId" },
  // builds, sessions and other docs where we fetch and then must verify
  { re: /buildId:\s*v\.id\("builds"\)/,                 scope: "buildId" },
  { re: /sessionId:\s*v\.string\(\)/,                    scope: "sessionId" },
];

// Any of these calls is considered a valid ownership gate for a scoped query.
const OWNERSHIP_HELPERS = [
  "requireMatchingUser",
  "requireOwnership",
  "requireProjectOwnership",
  "requireOrgMember",
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isQueryDecl(src, start) {
  // Look back a few characters to confirm this is `= query({`
  const before = src.slice(Math.max(0, start - 40), start);
  return /=\s*query\(\s*\{?\s*$/.test(before);
}

/** Extract every `export const NAME = query({ ... });` block, returning
 *  `{ name, args, handlerBody }` for each. Brace-balanced scan. */
function extractQueryBlocks(src) {
  const results = [];
  const re = /export\s+const\s+([A-Za-z_][\w]*)\s*=\s*query\s*\(\s*\{/g;
  let match;
  while ((match = re.exec(src)) !== null) {
    const startIdx = match.index;
    const openIdx = src.indexOf("{", match.index + match[0].length - 1);
    if (openIdx < 0) continue;
    // Brace-balance from openIdx
    let depth = 1;
    let i = openIdx + 1;
    while (i < src.length && depth > 0) {
      const ch = src[i];
      if (ch === "{") depth++;
      else if (ch === "}") depth--;
      i++;
    }
    if (depth !== 0) continue;
    const block = src.slice(openIdx, i);
    results.push({ name: match[1], block, startLine: lineOf(src, startIdx) });
  }
  return results;
}

function lineOf(src, idx) {
  return src.slice(0, idx).split("\n").length;
}

/** Detect which scoped args (if any) the query declares. */
function detectScopedArgs(block) {
  // Restrict to the `args: { ... }` sub-block to avoid handler body false-positives.
  const argsMatch = block.match(/args\s*:\s*\{([\s\S]*?)\},\s*handler/);
  if (!argsMatch) return [];
  const argsBody = argsMatch[1];
  const hits = [];
  for (const { re, scope } of SCOPED_ARG_PATTERNS) {
    if (re.test(argsBody)) hits.push(scope);
  }
  return hits;
}

/** True if the handler body invokes at least one ownership helper. */
function hasOwnershipGate(block) {
  return OWNERSHIP_HELPERS.some((fn) => block.includes(`${fn}(`));
}

// ---------------------------------------------------------------------------
// Scan
// ---------------------------------------------------------------------------

const files = readdirSync(CONVEX_DIR)
  .filter((f) => f.endsWith(".ts") && !EXEMPT_MODULES.has(f));

let totalQueries = 0;
let scopedQueries = 0;
let violations = [];
let publicSkipped = [];
let todoSkipped = [];

for (const file of files) {
  const path = join(CONVEX_DIR, file);
  const src = readFileSync(path, "utf-8");
  const blocks = extractQueryBlocks(src);
  for (const { name, block, startLine } of blocks) {
    totalQueries++;
    const scoped = detectScopedArgs(block);
    if (scoped.length === 0) continue;
    scopedQueries++;
    const fqn = `${file}::${name}`;
    if (PUBLIC_QUERIES.has(fqn)) {
      publicSkipped.push({ file, name, line: startLine });
      continue;
    }
    if (TODO_TRIAGE.has(fqn)) {
      todoSkipped.push({ file, name, line: startLine, scopedArgs: scoped });
      continue;
    }
    if (!hasOwnershipGate(block)) {
      violations.push({
        file,
        name,
        line: startLine,
        scopedArgs: scoped,
      });
    }
  }
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

console.log("==========================================");
console.log("W3.1 — Static IDOR fuzz on Convex queries");
console.log("==========================================");
console.log(`Scanned     : ${files.length} modules (${EXEMPT_MODULES.size} exempt)`);
console.log(`Queries     : ${totalQueries} total, ${scopedQueries} take a user-scoped argument`);
console.log(`Public      : ${publicSkipped.length} intentionally-public (allow-list)`);
console.log(`TODO triage : ${todoSkipped.length} pending security decision`);
console.log("");

if (todoSkipped.length > 0) {
  console.log("⚠ Pending triage (not failing the build):");
  for (const v of todoSkipped) {
    console.log(`   ${v.file}:${v.line}  ${v.name}  [args: ${v.scopedArgs.join(", ")}]`);
  }
  console.log("");
}

if (violations.length === 0) {
  console.log("✓ PASS — every user-scoped query is gated by an ownership helper.");
  process.exit(0);
}

console.error(`✗ FAIL — ${violations.length} IDOR violation(s) found:`);
console.error("");
for (const v of violations) {
  console.error(
    `   ${v.file}:${v.line}  export const ${v.name} = query({...})`,
  );
  console.error(`      scoped args: ${v.scopedArgs.join(", ")}`);
  console.error(
    `      missing one of: ${OWNERSHIP_HELPERS.map((h) => `${h}(ctx, ...)`).join(", ")}`,
  );
  console.error("");
}
process.exit(1);
