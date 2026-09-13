# Frontend Dependency Migration — April 2026 Security Sprint

This file documents the required follow-up work after the `package.json` version bumps in Phase 0.
The version bumps are security-forced (CVE-2026-23869 + CVE-2026-29057 + Next.js 14 EOL since 2025-10-26).

## Why

| Package | Old | New | Reason |
|---------|-----|-----|--------|
| `next` | `14.2.35` | `^16.2.3` | CVE-2026-23869 (React DoS, CVSS 7.5) + CVE-2026-29057 (HTTP Request Smuggling). 14.x EOL since 2025-10-26 with **no patch backport**. |
| `react` / `react-dom` | `18.3.1` | `^19.2.5` | CVE-2026-23869 patch landed in 19.0.5 / 19.1.6 / 19.2.5. |
| `@clerk/nextjs` | `^5.0.0` | `^7.2.3` | v7.2.1 fixed a `createPathMatcher` route-protection bypass. v7.1.0 bumped peer Next.js to 15.5.15 for the CVE-2026-23869 patch. |
| `vitest` | `^3.1.0` | `^4.1.4` | v4 is current; v3 no longer receiving security updates. |
| `@playwright/test` | `^1.49.0` | `^1.58.0` | v1.58 adds Trace Viewer search and sharded merge-reports. |
| `@types/react` / `@types/react-dom` | `18.x` | `^19.0.0` | Match runtime. |
| `eslint-config-next` | `14.2.35` | `^16.2.3` | Match Next major. |

## Required steps after `npm install`

### 1. Run the Next.js codemod (mandatory)

```bash
cd frontend
npx @next/codemod@canary upgrade latest
```

This rewrites:
- `cookies()`, `headers()`, `draftMode()` to be `await`ed in server components / route handlers.
- `params` and `searchParams` in page/layout props to be awaited.
- `unstable_noStore`, `unstable_cache` imports.

### 2. `middleware.ts` → `proxy.ts` (Next.js 16 rename)

```bash
git mv middleware.ts proxy.ts
```

The export signature and matcher config are unchanged. Next 16 still accepts
`middleware.ts` as a deprecated alias, but the linter will warn; rename before
CI turns red.

### 3. Audit caching semantics

Next 15 (and therefore 16) changed `fetch()` default cache from `force-cache`
to `no-store`. Any code relying on implicit caching must add
`fetch(url, { cache: "force-cache" })` or use `"use cache"` directives.

Grep for `await fetch(` across `app/` and `components/`; review each call.

### 4. Clerk v7 migration

- `ClerkProvider` and `clerkMiddleware` API are backwards-compatible at the
  call-site. No immediate rewrites required.
- Optional: adopt `<OAuthConsent />` (7.2.0) and `useAPIKeys()` (7.0.12) if
  wanted for the API-keys management UI. These are additive, not required.

### 5. React 19 breaking changes

Audit:
- `ref` is now a prop on function components (`forwardRef` is soft-deprecated).
- `useRef()` now requires an explicit initial argument — a pure `useRef()` call
  is a type error in React 19.
- `useActionState` replaces `useFormState`.

Run `npx react-codemod@latest update-to-react-19 .` for bulk rewrites.

### 6. Vitest v4 config changes

Check `vitest.config.ts`:
- `workspace` option renamed to `projects`.
- `coverage.provider: "c8"` removed — use `"v8"`.
- Snapshot format default changed.

### 7. Post-migration verification

```bash
npm run type-check        # Must pass cleanly on React 19 types
npm run lint              # Expect a few codemod-missed spots; fix inline
npm run build             # Must succeed; Turbopack is the default bundler
npm run test              # Vitest 4 suite must pass
npx playwright test       # E2E suite must pass
```

### 8. Tauri embedding verification

Because the desktop app bundles the Next.js output as `frontendDist`, run a
production build and open it in a Tauri dev window to confirm the WebView
handles Next 16 RSC payloads and streaming correctly.

## Rollback

If anything breaks in CI and the timeline is tight:

```bash
git revert <commit-with-bumps>
npm install
```

You lose the CVE-2026-23869 / -29057 mitigations. Only use as a last resort
during the migration window, and only if the exposed routes do not include
`/api/*` rewrites to external origins (check `next.config.js`).

## Vercel env rotation reminder (April 20, 2026 incident)

Independently of this migration: rotate all non-"sensitive"-flagged env vars
in the Vercel dashboard and re-classify them as sensitive. See
https://vercel.com/kb/bulletin/vercel-april-2026-security-incident.
