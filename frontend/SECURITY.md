# VOS4 Frontend — Security Manifest

## Reporting a Vulnerability

Email security findings to the maintainers privately. Do not open public GitHub issues for unpatched vulnerabilities.

## Known Accepted Risks

The following advisories are tracked, evaluated as **not exploitable** in VOS4's threat model, and intentionally suppressed via `.audit-resolve.json`. Each entry includes the technical justification and the upstream condition that will close it.

### CVE-2026-41305 / GHSA-qx2v-qp2m-jg93 — `postcss <8.5.10` XSS

- **Affected path**: `next/dist/compiled/postcss` (bundled — `npm overrides` cannot replace it)
- **Direct dep status**: VOS4's top-level `postcss@8.5.13` is already patched. Only the **bundled** copy inside `next@16.2.4` carries the vulnerable `8.4.31`.
- **Vulnerability mechanics**: The advisory requires CSS that originates from an attacker to be parsed and re-stringified by PostCSS into HTML `<style>` output. A `</style>` substring inside an attacker-controlled CSS value escapes the style context and triggers XSS.
- **Why it is not exploitable in VOS4**:
  1. PostCSS in this project runs **build-time only** (Next.js compile step).
  2. The only CSS input to that pipeline is the project's own Tailwind output (`postcss.config.js` → `@tailwindcss/postcss`). No request handler, no API endpoint, and no Sandpack/preview surface feeds user-supplied CSS into PostCSS.
  3. The vulnerable `<style>` re-stringification path is not reachable by any data path that crosses a trust boundary.
- **Closure plan**: Upgrade to `next@16.3.0` once GA is published (canary.9 already contains `chore: bump postcss to 8.5.10`, ETA ~2 weeks based on Vercel's release cadence). Tracked in `migration_plan.md` §2.3.
- **Suppression**: `frontend/.audit-resolve.json` (expires 2025-07-04 to force a recheck even if upstream slips).

## Resolved Vulnerabilities (Historical Reference)

| Date | Advisory | Action |
|------|----------|--------|
| 2026-05-04 | GHSA-crv5-9vww-q3g8, GHSA-v9jr-rg53-9pgp (`dompurify` XSS, bundled in `monaco-editor`) | Removed `@monaco-editor/react` and `y-monaco` — both were dead code with zero call sites. Closes the entire dompurify dependency chain. |

## Audit Workflow

```bash
cd /Users/sz/Desktop/vos/vos4/frontend
npm audit                                      # all current findings
npx npm-audit-resolver check-resolutions       # apply .audit-resolve.json suppressions
```

When a new advisory appears that is genuinely exploitable, fix it — do not add it to `.audit-resolve.json`. This file is reserved for vulnerabilities whose **threat-model analysis** shows they are unreachable in VOS4's actual data paths.
