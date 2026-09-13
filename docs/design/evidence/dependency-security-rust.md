# Independent Rust dependency security review — 2026-09-13

Reviewer did not author the Rust migration and made no Rust source changes. Scope: `desktop/src-tauri`, rand 0.10.2 / HMAC 0.13 / SHA-2 0.11 migration, selected session and packaging boundaries. This is a targeted review, not certification or a full advisory scan.

## Migration assessment

No cryptographic downgrade was identified in the reviewed API replacements. `rand::thread_rng().fill_bytes` becomes `rand::rng().fill_bytes` at the three secret-generation sites (`sidecar.rs`, `vbus/client.rs`, `vbus/hmac.rs`). The locked rand source seeds its ChaCha12 generator using SysRng and reseeds after 64 KiB. Initialization and reseeding failure panic; no zero/predictable fallback was introduced. The sidecar comment describing direct OS randomness is imprecise: bytes come from the OS-seeded PRNG. Fork reseeding and memory zeroization are not guaranteed by ThreadRng. No manual post-fork key generation was found in the reviewed path. [Upstream ThreadRng security documentation](https://docs.rs/rand/0.10.2/rand/rngs/struct.ThreadRng.html).

The `KeyInit` import matches the HMAC 0.13 API. `HmacAuth::verify` still computes HMAC over header bytes 0..16 and payload, and compares fixed 32-byte tags using `subtle::ConstantTimeEq`. Rejecting the public missing-HMAC flag before computation is not a secret-dependent comparison. This source review is not a measured constant-time proof for emitted machine code. [Upstream HMAC API](https://docs.rs/crate/hmac/0.13.0).

HKDF remains HMAC-Extract followed by one Expand block `HMAC(PRK, info || 0x01)` for 32 output bytes. New tests contain correct RFC 4231 HMAC and RFC 5869 first-block known answers. Reviewer independently recomputed both with Python stdlib HMAC/SHA-256: **2 passed**. Added header-prefix tamper tests cover all 16 authenticated prefix bytes and a wrong key. They do not claim every header byte is authenticated: bytes 48..64 are outside the MAC input.

A targeted primary-source advisory check found **RUSTSEC-2026-0097** fixes include rand >=0.10.1; locked 0.10.2 satisfies that patched range. This does not establish the absence of other advisories. No `cargo-audit` executable was available to this reviewer and no full Cargo vulnerability audit was run. [RustSec advisory](https://rustsec.org/advisories/RUSTSEC-2026-0097).

## Preexisting deployment findings requiring follow-up

1. **Authentication negotiation is optional** (`vbus/client.rs`, connect around lines 246–257): a handshake without an HMAC marker leaves the session unauthenticated. Once HMAC is enabled, unsigned frames are correctly rejected around lines 509–525. A production policy requiring authentication must reject negotiation downgrade rather than only post-negotiation downgrade.
2. **Transport trust is essential**: the client sends raw IKM in its initial Unix-stream handshake. HKDF provides key derivation, not peer authentication or confidentiality. A party able to observe or replace that handshake can derive the session key. Socket ownership/access controls and trusted peer establishment were not proven by this dependency review.
3. **Release fallback is not fail-closed at runtime** (`sidecar.rs`, `build_command`, around lines 218–273): if the sibling backend is absent, the function chooses `python3` through PATH and runs a module from a configurable/current directory. Despite the “Dev fallback” comment, there is no debug-build gate in that path. This is preexisting and must be separated from production packaged operation.
4. **Secrets remain in memory**: session keys use ordinary byte arrays and sidecar secrets ordinary strings, with no explicit zeroization in the reviewed migration. The change neither adds nor proves protection against memory disclosure.

## Validation boundary

The existing standard Cargo check log `/private/tmp/vos5-rust-check.log` stops because the real external backend binary is missing. `build.rs` still invokes `tauri_build::build()` unconditionally and the externalBin requirement remains in `tauri.conf.json`; this build-time gate was not bypassed in source. A test-only Tauri configuration override cannot be considered a packaged release pass.

The initial run failed a stale memmap2 checksum fixture. The migration author subsequently reported 108 tests passing (76 + 4 + 6 + 22) in `/private/tmp/vos5-rust-tests.log`, after verifying the new pin against the official index and downloaded archive. These runs use command-only `TAURI_CONFIG` overrides removing missing externalBin and icon requirements; they are source/test validation, not a production packaging pass. No hardware entropy-failure injection, release installer, Windows/Linux desktop qualification or end-to-end trusted-kernel authentication was performed.
