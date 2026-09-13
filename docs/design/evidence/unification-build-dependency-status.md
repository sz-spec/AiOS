# Unification, build and dependency status — 2026-09-14

The canonical project is `vos 5`. This report records the current consolidation
and upgrade work, not a declaration that the independent OS is complete.
The [security review matrix](security-team-coverage.md) identifies the authors,
independent reviewers, evidence and remaining boundaries for each track.

## Implemented

- The reconciliation ledger compares eleven source trees and records component
  decisions, retained variants and unresolved differences. Content correspondence
  is evidence of preservation, not proof of semantic feature equivalence.
- The native build shares one user-program inventory, isolates build flavors,
  tracks flags/tool identities and C/assembly dependencies, invalidates musl CRT
  consumers, and publishes validated ISO output through atomic replacement.
- musl 1.2.6 and Limine 12.9.0 are integrated with recorded upstream provenance
  and preserved VOS port files. GCC 16.2.0 and binutils 2.47 built a separate
  qualified ISO. The previous host-toolchain ISO is retained.
- Python requirements come from one catalog with five hashed platform/profile
  locks. Eight Node applications retain independently checked manifest/lock
  pairs. Latest compatible versions and explicit upstream constraints replace
  unsupported blanket claims that every dependency is the newest release.
- API/package migrations include direct bcrypt, newer MCP SDK/authentication,
  Rust random/cryptographic APIs and the current frontend build/test tools.
- Security review produced tenant-specific memory storage, server-controlled
  provenance, secret-log removal, production JWT configuration refusal, explicit
  container storage paths and deployment dependency fixes.

## Recorded validation

| Scope | Evidence and result |
| --- | --- |
| Native build graph | Configuration, dependency, embedder and ISO publication regression checks passed |
| Host-toolchain ISO | BIOS with 4 CPUs, UEFI with 2, keyboard wizard with 2; no-op artifact hashes/mtimes preserved |
| New compiler ISO | BIOS/4 CPUs and UEFI/2 CPUs passed; repeated build preserved ISO hash |
| Frontend | 216 tests passed; current-source Webpack production build and TypeScript passed, 30 static pages |
| MCP | 18 tests and build/type checks recorded; independent authentication/cache reviews linked in review matrix |
| Rust desktop | 108 tests and cargo check recorded with explicit test-only Tauri asset overrides |
| Python routing | 47 tests passed |
| Python WASM/crypto/keyring | 80 passed, 1 skipped |
| Memory isolation/provenance | 74 tests passed under independent review |
| Production JWT/passwords | 10 focused tests passed; independent cross-process token verification and wrong-key rejection passed |
| HF model configuration scanner | 12 tests passed |
| Dependency coherence | Five lock fingerprints and eight Node pairs passed; Python shared versions match |
| Source secret scan | 5,315 nonignored source files; 24 findings all matched previously reviewed unchanged source spans |

The new compiler ISO SHA-256 is
`929643fae8662cb9d794ad7028bca0ede27c1457b4e3ab35ea88cbd0c151e412`.
See [toolchain evidence](dependency-upgrade-toolchain.md) and the
[independent vendor review](security-native-vendors-independent.md).

Resolved Python package-name counts are full Linux 278, root Linux 273,
minimal backend 178, Windows 254 and macOS ARM64 253. The final minimal-profile
fix adds Socket.IO/Strawberry and required transitives without changing any
previously resolved version. Actual final backend runtime qualification is
tracked in the [container review](security-backend-container-final.md).

The final backend image includes the corrected minimal profile and fail-closed
Chroma write handling. Image `4c9b1fe4be4ea44e9c86fe9b062dd67286a4bb137e8d01e4fa6d915972b91982`
passed actual Uvicorn startup, authentication refusal, strict route discovery
and SQLite/Chroma persistence across two different containers in 84.35 seconds.
Independent reviewers accepted this bounded gate; 75 memory regressions and
three runner cleanup tests passed. See [runtime qualification](backend-runtime-qualification.md)
for exact inputs, evidence and cloud/model limitations.

## Remaining gates

- Six unique Python advisories remain open; compatible pins do not resolve
  them. Exposure analysis is bounded to reviewed application paths.
- The macOS z3 wheel-tag compatibility issue remains despite a successful
  import and solver smoke. Solver models are not proofs of compiled kernel code.
- Default Turbopack revalidation is blocked by local port permissions; the
  supported Webpack build passed with documented Edge-runtime warnings.
- 270 browser cases were discovered, not executed. Rust production packaging
  still needs real sidecar/icon assets; test overrides are not release assets.
- MCP retains historical lint findings and quota/concurrency limits. Container
  startup, health and persistence are separate from successful image builds.
- Native Secure Boot, full process isolation, musl ABI edge cases, physical
  hardware coverage and broad driver support remain separate qualification
  work. BIOS/UEFI emulator smoke tests do not establish universal PC support.
- The full staged whitespace check reports unchanged upstream formatting in
  the newly imported Limine sources, including a PicoEFI Markdown underline
  mistaken for a conflict marker. Vendor bytes are retained to preserve the
  verified archive correspondence; this is not an unresolved merge conflict.

No report here certifies VOS as the most secure or most efficient OS. Security
and mathematical review continue by track, with independent checks after new
changes and explicit open findings rather than implied certification.
