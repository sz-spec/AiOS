#!/usr/bin/env node
/**
 * W4.4 — Static input-validation fuzzer for Convex mutations & queries.
 *
 * Companion to idor_fuzz.mjs (W3.1). Where idor_fuzz catches "any
 * authenticated user can read someone else's data", this harness
 * catches "any authenticated user can WRITE garbage data" by detecting:
 *
 *   - Enum-shaped args declared as plain v.string() instead of
 *     v.union(v.literal("a"), v.literal("b"), ...). A field like
 *     `status` accepting "pwned" is a downstream-state-corruption vector.
 *
 *   - Numeric args that look like bounded quantities (rating, price,
 *     percent) declared as bare v.number() with no handler-body range
 *     check.
 *
 * Heuristic: arg-name matching against ENUM_ARG_NAMES and BOUNDED_NUMBER_NAMES.
 *
 * For each match, the fuzzer accepts the call as VALIDATED if EITHER:
 *
 *   (a) the arg is declared as v.union(v.literal(...)) — type-level enum
 *   (b) the handler body contains an explicit validation pattern keyed
 *       on the arg name: `if (args.X` / `if (X` followed by `throw` or
 *       ConvexError, OR a known validator-call regex
 *
 * Run:
 *   node frontend/convex/_test/input_validation_fuzz.mjs
 *
 * Exit codes:
 *   0 — every flagged arg is either type-level enum or handler-validated
 *   1 — at least one arg has neither
 *
 * Allow-lists (intentional decisions):
 *   APPROVED_PLAIN_STRINGS — args we've reviewed and confirmed must be
 *     free-form strings (chat session IDs, Stripe IDs, user-supplied
 *     URLs, etc.). Adding here requires a security-team comment.
 */

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CONVEX_DIR = resolve(__dirname, "..");

// ---------------------------------------------------------------------------
// Files exempt from this scan (test scaffolding, schema, helpers).
// ---------------------------------------------------------------------------
const EXEMPT_MODULES = new Set([
  "schema.ts",
  "auth.config.ts",
  "authHelpers.ts",
  "webhook_seen.ts",
]);

// ---------------------------------------------------------------------------
// Arg-name patterns that strongly imply an enum value.
// (Match is case-insensitive on the exact arg name only — no substring match.)
// ---------------------------------------------------------------------------
const ENUM_ARG_NAMES = new Set([
  "status",
  "role",
  "type",
  "category",
  "pricing",
  "plan",
  "interval",
  "kind",
  "state",
  "level",
  "visibility",
]);

// ---------------------------------------------------------------------------
// Arg-name patterns that imply a bounded numeric.
// ---------------------------------------------------------------------------
const BOUNDED_NUMBER_NAMES = new Set([
  "rating",
  "score",
  "percent",
  "revenueSharePercent",
  "priority",
]);

// ---------------------------------------------------------------------------
// (file, function-name::arg-name) tuples explicitly allow-listed as
// free-form. EVERY entry requires a justification comment.
// ---------------------------------------------------------------------------
const APPROVED_PLAIN_STRINGS = new Set([
  // -------------------------------------------------------------------------
  // W4.4 — approved free-form decisions (security-team reviewed).
  // -------------------------------------------------------------------------
  // `apps.publish::category` — marketplace categories are operator-
  //   defined and grow over time (productivity, design, finance, ...).
  //   The set is too dynamic for a v.union, and a bad-value publish
  //   only mis-shelves the app (no downstream state corruption).
  "apps.ts::publish::category",
  // `apps.list::category` / `apps.list::status` — these are FILTER args
  //   on a read query. A bad value just returns an empty page; there is
  //   no write side-effect. Keeping them free-form lets the UI evolve
  //   without lockstep enum updates.
  "apps.ts::list::category",
  "apps.ts::list::status",
]);

// ---------------------------------------------------------------------------
// (file, function-name) pairs still under triage — flagged but not failing
// the build. Use this set for findings the security team has not yet
// resolved. Empty by W4.4 release goal.
// ---------------------------------------------------------------------------
const TODO_TRIAGE = new Set([]);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Strip TS line and block comments from source.
 *
 * Comments inside arg blocks would otherwise confuse iterArgs — a
 * trailing comma inside a comment line ("categories are dynamic and
 * operator-defined.") gets read as a top-level separator and the next
 * arg silently drops out of inspection. Strip comments up-front so the
 * brace/paren walker only sees code.
 *
 * The simple regex stripper is safe here because Convex TS files don't
 * embed comment-delimiter sequences inside string literals at the
 * top level (no `"// ..."` constants in this codebase as of W4.4).
 * If a future case lands such a string, replace with a proper TS
 * tokenizer.
 */
function stripComments(src) {
  // Block comments first (multiline), then line comments.
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/[^\n]*/g, "");
}

/** Extract every `export const NAME = (query|mutation)({ ... });` block. */
function extractBlocks(src) {
  const results = [];
  const re = /export\s+const\s+([A-Za-z_][\w]*)\s*=\s*(query|mutation)\s*\(\s*\{/g;
  let match;
  while ((match = re.exec(src)) !== null) {
    const startIdx = match.index;
    const openIdx = src.indexOf("{", match.index + match[0].length - 1);
    if (openIdx < 0) continue;
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
    results.push({
      name: match[1],
      kind: match[2],
      block,
      startLine: src.slice(0, startIdx).split("\n").length,
    });
  }
  return results;
}

/** Pull the `args: { ... },` sub-block. */
function extractArgsBlock(block) {
  // Match args: { ... } followed by either handler: or returns: (Convex 1.x)
  const m = block.match(/args\s*:\s*(\{[\s\S]*?\})\s*,\s*(handler|returns)\s*[:(]/);
  return m ? m[1] : null;
}

/**
 * Walk the args block and yield (argName, typeExpr) tuples.
 *
 * Brace-balanced split: an arg's type expression is everything from the
 * `:` to the next comma at depth 0.
 */
function* iterArgs(argsBlock) {
  // Strip the outermost braces.
  const inner = argsBlock.slice(1, -1);
  let i = 0;
  while (i < inner.length) {
    // Skip whitespace + leading comma.
    while (i < inner.length && /[\s,]/.test(inner[i])) i++;
    if (i >= inner.length) break;
    // Read identifier (or quoted name).
    const nameMatch = /^([A-Za-z_][\w]*)\s*:/.exec(inner.slice(i));
    if (!nameMatch) {
      // Couldn't parse — skip to next comma at depth 0 and resume.
      i = advanceToTopLevelComma(inner, i);
      continue;
    }
    const argName = nameMatch[1];
    i += nameMatch[0].length;
    // Read type expression to next top-level comma.
    const exprStart = i;
    i = advanceToTopLevelComma(inner, i);
    const typeExpr = inner.slice(exprStart, i).trim();
    yield { argName, typeExpr };
  }
}

function advanceToTopLevelComma(src, start) {
  let depth = 0;
  let i = start;
  while (i < src.length) {
    const ch = src[i];
    if (ch === "(" || ch === "{" || ch === "[") depth++;
    else if (ch === ")" || ch === "}" || ch === "]") depth--;
    else if (ch === "," && depth === 0) return i;
    i++;
  }
  return src.length;
}

/** Is `typeExpr` a v.union(v.literal(...), ...) type — i.e. an enum? */
function isEnum(typeExpr) {
  return /v\.union\s*\(\s*v\.literal/.test(typeExpr);
}

/** Is `typeExpr` exactly v.string() or v.optional(v.string())? */
function isPlainString(typeExpr) {
  return /^v\.(optional\(\s*)?v\.string\(\)\)?$/.test(typeExpr.replace(/\s+/g, ""))
    || /^v\.string\(\)$/.test(typeExpr.trim())
    || /^v\.optional\(v\.string\(\)\)$/.test(typeExpr.replace(/\s+/g, ""));
}

/** Is `typeExpr` v.number() or v.optional(v.number())? */
function isPlainNumber(typeExpr) {
  const norm = typeExpr.replace(/\s+/g, "");
  return norm === "v.number()" || norm === "v.optional(v.number())";
}

/** True if the handler body contains a validation check that mentions argName. */
function handlerValidates(block, argName) {
  // Patterns we accept as evidence of validation:
  //  1. `throw new ... Error(...args.X...)`
  //  2. `if (args.X ...) throw`
  //  3. `if (X ...) throw`   (after destructuring `{ X } = args`)
  //  4. `[a, b, c].includes(args.X)`
  //  5. `["a", "b"].includes(X)`
  const patterns = [
    new RegExp(`throw\\s+new\\s+(?:Error|ConvexError)\\([^)]*\\b${argName}\\b`, "s"),
    new RegExp(`if\\s*\\([^)]*\\bargs\\.${argName}\\b[^)]*\\)[^;]*throw`, "s"),
    new RegExp(`if\\s*\\([^)]*\\b${argName}\\b[^)]*\\)[^;]*throw`, "s"),
    new RegExp(`\\.includes\\(\\s*args\\.${argName}\\s*\\)`),
    new RegExp(`\\.includes\\(\\s*${argName}\\s*\\)`),
    // Common "not in set" pattern: !VALID_X.has(args.X)
    new RegExp(`\\.has\\(\\s*args\\.${argName}\\s*\\)`),
    new RegExp(`\\.has\\(\\s*${argName}\\s*\\)`),
  ];
  return patterns.some((re) => re.test(block));
}

// ---------------------------------------------------------------------------
// Scan
// ---------------------------------------------------------------------------

const files = readdirSync(CONVEX_DIR)
  .filter((f) => f.endsWith(".ts") && !EXEMPT_MODULES.has(f));

let totalBlocks = 0;
let inspectedArgs = 0;
let violations = [];
let todoSkipped = [];
let approvedSkipped = [];

for (const file of files) {
  const path = join(CONVEX_DIR, file);
  const src = stripComments(readFileSync(path, "utf-8"));
  const blocks = extractBlocks(src);
  for (const { name, kind, block, startLine } of blocks) {
    totalBlocks++;
    const argsBlock = extractArgsBlock(block);
    if (!argsBlock) continue;
    for (const { argName, typeExpr } of iterArgs(argsBlock)) {
      const isEnumName = ENUM_ARG_NAMES.has(argName);
      const isBoundedNumName = BOUNDED_NUMBER_NAMES.has(argName);
      if (!isEnumName && !isBoundedNumName) continue;
      inspectedArgs++;
      const approvalKey = `${file}::${name}::${argName}`;
      if (APPROVED_PLAIN_STRINGS.has(approvalKey)) {
        approvedSkipped.push({ file, name, argName, line: startLine });
        continue;
      }
      const triageKey = `${file}::${name}`;
      if (TODO_TRIAGE.has(triageKey)) {
        todoSkipped.push({ file, name, argName, line: startLine });
        continue;
      }
      // Enum-named arg: enum-typed is the strongest validation; handler
      // validation is the secondary acceptable path.
      if (isEnumName) {
        if (isEnum(typeExpr)) continue; // type-level enum — pass
        if (!isPlainString(typeExpr)) continue; // some other custom validator type
        if (handlerValidates(block, argName)) continue;
        violations.push({
          file, name, kind, argName, line: startLine,
          reason: "enum-shaped arg declared as v.string() with no handler validation",
        });
        continue;
      }
      // Bounded-number-named arg: needs explicit handler validation,
      // since Convex has no built-in numeric-range validator.
      if (isBoundedNumName) {
        if (!isPlainNumber(typeExpr)) continue; // some custom validator
        if (handlerValidates(block, argName)) continue;
        violations.push({
          file, name, kind, argName, line: startLine,
          reason: "bounded-number arg declared as v.number() with no handler range check",
        });
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

console.log("==========================================");
console.log("W4.4 — Static input-validation fuzz");
console.log("==========================================");
console.log(`Scanned    : ${files.length} modules (${EXEMPT_MODULES.size} exempt)`);
console.log(`Blocks     : ${totalBlocks} (query + mutation)`);
console.log(`Inspected  : ${inspectedArgs} enum/bounded-num candidate args`);
console.log(`Approved   : ${approvedSkipped.length} reviewed-and-approved free-form`);
console.log(`TODO triage: ${todoSkipped.length} pending security decision`);
console.log("");

if (todoSkipped.length > 0) {
  console.log("⚠ Pending triage (not failing the build):");
  for (const v of todoSkipped) {
    console.log(`   ${v.file}:${v.line}  ${v.name}  arg=${v.argName}`);
  }
  console.log("");
}

if (violations.length === 0) {
  console.log("✓ PASS — every enum-shaped / bounded-number arg is validated.");
  process.exit(0);
}

console.error(`✗ FAIL — ${violations.length} input-validation gap(s) found:`);
console.error("");
for (const v of violations) {
  console.error(`   ${v.file}:${v.line}  export const ${v.name} = ${v.kind}({...})`);
  console.error(`      arg "${v.argName}" — ${v.reason}`);
  console.error("");
}
console.error(
  "Fixes:\n" +
  "  - Replace v.string() with v.union(v.literal(...), v.literal(...))\n" +
  "  - OR add an explicit handler-body check (throw on invalid)\n" +
  "  - OR add (file::function::arg) to APPROVED_PLAIN_STRINGS with a\n" +
  "    security-team comment if the field is truly free-form."
);
process.exit(1);
