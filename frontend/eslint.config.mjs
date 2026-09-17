// Use the native flat preset exported by the installed Next.js 16 config.
import nextVitals from "eslint-config-next/core-web-vitals";

export default [
  // Re-use the existing Next.js preset.
  ...nextVitals,
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
