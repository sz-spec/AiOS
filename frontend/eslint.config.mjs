// frontend/eslint.config.mjs
//
// ESLint 9 flat config. Replaces the legacy .eslintrc.json setup that
// was driven by `next lint` (deprecated in Next.js 15 + removed in
// Next.js 16). The .eslintrc.json is preserved for IDE plugins that
// still read it.
//
// Migration notes (Sprint 16 / CI debt cleanup):
//   - Next.js 16.x: `next lint` parses subsequent positional args as a
//     project-directory, which broke our CI invocation `next lint`
//     (interpreted as `next lint=$cwd/lint` → directory-not-found).
//   - Fix per Next.js 16 docs: invoke ESLint directly via `eslint .`
//     and provide a flat-config file that re-uses next/core-web-vitals.
//   - FlatCompat shim allows reusing the eslintrc preset until the
//     next-eslint package publishes a native flat-config export.

import { FlatCompat } from "@eslint/eslintrc";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

export default [
  // Re-use the existing Next.js preset.
  ...compat.extends("next/core-web-vitals"),
  {
    // Ignore generated + vendored directories.
    ignores: [
      ".next/**",
      "node_modules/**",
      "convex/_generated/**",
      "out/**",
      "build/**",
      "dist/**",
    ],
  },
  // Project-wide rule overrides.
  //
  // react/no-unescaped-entities: turn OFF. Modern browsers handle
  //   literal apostrophes and quotes inside JSX text content fine;
  //   the rule produces noise without functional benefit.
  //
  // react-hooks/exhaustive-deps: keep as WARN (the default) — there
  //   are ~20 legitimate places in the codebase where the callback
  //   intentionally omits a dep to break a render cycle. Tracked in
  //   the Sprint 17 frontend-cleanup ticket; not blocking the build.
  {
    rules: {
      "react/no-unescaped-entities": "off",
    },
  },
];
