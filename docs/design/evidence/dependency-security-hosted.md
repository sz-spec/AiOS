# Hosted dependency security review — 2026-09-13

Independent scope: Python/frontend dependency upgrade, lock consumption,
backend Dockerfile and frontend configuration diffs. No production source
changes were made by this reviewer. These checks do not certify OS security.

## Executed checks

- Fresh frontend `npm audit --json`: success, **0 reported vulnerabilities**,
  739 dependencies. Raw result: `/private/tmp/vos5-hosted-security-audit.json`.
  Network access was explicitly escalated after sandbox DNS failure.
- Frontend `npm ls --all --json`: **fails ELSPROBLEMS**. ESLint 10.10.0 is
  outside peer ranges of eslint-plugin-import, eslint-plugin-jsx-a11y and
  eslint-plugin-react carried by eslint-config-next. Two optional packages
  also appear extraneous: @emnapi/runtime and @img/sharp-wasm32. Raw result:
  `/private/tmp/vos5-hosted-security-tree.json`.
- `uv pip check --cache-dir /private/tmp/vos5-uv-cache --python
  .venv-upgrade/bin/python`: **fails one compatibility check** across 252
  packages. z3-solver 5.1.0.0 wheel has tag
  `py3-none-macosx_13_3_arm64`, rejected on this macOS 26.3.1 ARM64 host.
- Independent `packaging.tags.sys_tags()` with packaging 24.2 also excludes
  that tag. Inspection of its local `mac_platforms` implementation shows
  macOS >= 11 tags generated with minor version zero. Therefore this cannot
  be described as an isolated uv failure. uv version is 0.11.7.
- Importing z3 succeeds and reports 5.1.0. Parent integration agent separately
  verified an unsatisfiable solver query. Runtime smoke success does not make
  the wheel tag accepted by packaging tools. pip is absent in this environment,
  so no successful pip check is claimed.

## Required follow-up

1. Resolve ESLint peer incompatibility through supported versions or compatible
   upstream plugins; do not turn off peer checks or present npm audit as a
   compatibility proof. Re-run npm ls and actual lint after correction.
2. Track the z3 wheel packaging exception explicitly. Obtain an appropriately
   tagged upstream artifact or validate a local source build before claiming a
   fully portable install. No downgrade is justified solely by successful
   runtime smoke or solely by this metadata finding; preserve the distinction.
3. A Python advisory audit was not run by this reviewer. Dependency metadata
   consistency checks are not vulnerability scans. Docker image build/runtime
   and image advisory checks remain separate release evidence.

## Source review

The backend Dockerfile now installs a hashed lock into /opt/venv and copies that
environment to the final image, running as appuser. This is materially better
than copying root-private user-site packages. `--require-hashes` authenticates
downloaded bytes against the reviewed lock; it does not establish that those
bytes are benign. Base image tags and apt package versions remain mutable,
therefore whole-image reproducibility is not established. Backend .dockerignore
excludes .env variants except the example template.

Frontend changes move jest-dom to its Vitest integration entrypoint, move Convex
dependency inlining to the supported server configuration shape, and target
ES2022. No new disablement of checking appears in these inspected diffs.
Existing skipLibCheck and convex:dev --typecheck=disable predate this patch;
they are not evidence of a complete dependency type audit. The existing Next
postcss override is a range; the exact resolved artifact remains in the lock.

The native mathematical review separately covers lock drift and trusted input
boundaries. A clean advisory result here does not cover application authorization,
secrets, network policy, runtime isolation, or the native kernel.
