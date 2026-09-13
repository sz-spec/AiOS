# Dependency sources, updates and validation

`python-dependencies.json` is the direct Python package catalog and profile
membership source. Generated requirements files retain flat deployment contexts.
`latest-registry-versions.json` records registry observations, not a promise that
each observed newest version is compatible with every other package.
`upgrade-exceptions.json` lists the concrete compatibility exceptions and the
remaining z3 macOS wheel-tag issue. No peer-check bypass is the update policy.

## Python update process

1. Refresh official registry metadata and review direct requirement changes in
   the catalog, including upstream dependency constraints.
2. Run `python3 scripts/sync_python_requirements.py`.
3. Run `python3 scripts/lock_python_dependencies.py` (`make python-lock`). It
   uses `uv pip compile --upgrade --generate-hashes`; upgrade is necessary to
   refresh transitive versions rather than preserve prior lock preferences.
4. Run `python3 scripts/lock_python_dependencies.py --check`. This checks
   generated profile text and recorded input/output fingerprints offline.
5. Install the matching hashed lock in an isolated target environment and run
   dependency compatibility checks, application tests and advisory scans.

The full Linux profile resolves first. All subsequent solves constrain shared
packages to that lock. Python is 3.12; deployment targets are Linux x86_64
(full, root and minimal backend profiles), Windows x86_64 and macOS ARM64 with
MACOSX_DEPLOYMENT_TARGET=14.0. These are hosted application environments;
the independent native OS does not require Python or Linux to boot.

After the latest explicit upgrade, independent inspection found 278 full,
273 root, 178 minimal backend, 254 Windows and 253 macOS resolved package names,
with **zero shared-version mismatches**. Fingerprint verification passed.
This finite check does not prove marker/wheel compatibility on every platform.
The known z3 wheel-tag mismatch must not be hidden behind successful import.

## Other ecosystems and native libraries

Each Node application retains its own package.json/package-lock.json pair.
Use its supported Node version, clean lock installation, full peer inspection,
type/build tests and a fresh advisory audit. Frontend ESLint and TypeScript
exceptions are explicit because newest releases exceed plugin peer ranges.
Do not use --legacy-peer-deps or disable checks to claim compatibility.

Rust dependency definitions remain in desktop/src-tauri; no Go module was found in this canonical tree.
Native musl and Limine provenance is recorded in musl-upgrade.json and
limine-upgrade.json. Upstream source identity, preserved VOS port changes,
native compilation and native runtime validation are separate obligations.

## Evidence boundaries

Hashes establish agreement with reviewed artifacts; they do not establish
that an artifact is benign. An offline fingerprint check is neither a
vulnerability scan nor a successful installation. Latest compatible versions
can differ from latest registry versions for documented upstream constraints.
Docker base images and operating system packages require separate review.
Reports in docs/design/evidence record concrete tests and remaining limitations;
none establishes overall OS security or support for all PC hardware.
