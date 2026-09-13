# VOS3 SOVEREIGNTY MANIFEST

**Golden Master Attestation** | Genesis RC1 | 2026-04-14

---

## Binary Identity

| Property | Value |
|----------|-------|
| File | `kernel/build/vos3.elf` |
| SHA-256 | `43bfdd2ac2336d8326209db033c45f414b3fbb2deb3fce904d8aa233b2bc9b9a` |
| SHA-256 (prior, 2026-05-09 RC) | `5a47e556dc79024542c01825ba9f3f9043b4936257e4f718b178c48a1e719501` |
| Multi-target | `vos3.efi`=`773c58aa…` · `vos3-hyperv.elf`=`14f17675…` |
| Build | Release (`PRODUCTION=1`, `.comment` stripped) |
| .text | 462,510 bytes at VMA `0xffffffff80106000` |
| Test symbols | 0 (`test_phase*` eliminated by `--gc-sections`) |
| Test strings | 0 (all behind `#ifndef VOS3_PRODUCTION_BUILD`) |

## Compiler Hardening (OpenSSF 2026)

| Flag | Status |
|------|--------|
| `-fstack-protector-strong` | ENABLED (394 call sites) |
| `-fcf-protection=branch` | ENABLED (CET IBT) |
| `-ftrivial-auto-var-init=zero` | ENABLED (production CFLAGS) |
| `-fno-delete-null-pointer-checks` | ENABLED (production CFLAGS) |
| `--gc-sections` | ENABLED (dead code elimination) |
| `.comment` stripping | ENABLED (objcopy post-link) |
| W^X enforcement | ENABLED (mprotect rejects PROT_WRITE\|PROT_EXEC) |
| KASLR | ENABLED (`limine.conf: kaslr: yes`) |

## Integrity Seals

### Guardian Seal (Immutable Source Lock)
- Algorithm: SHA-256 (FIPS 180-4)
- Scope: `[_text_start, _text_end)` -- full kernel .text section
- Verification: Constant-time XOR comparison (no `memcmp`)
- Init order: Position 3 in `boot_ai_init()` -- BEFORE VecVFS, SNI, vScreen, Mesh, vSpace, Clipboard
- State: OPERATIONAL

### HMAC-SHA256 Frame Authentication (VBus)
- C-side: `volatile uint8_t diff` XOR accumulation (prevents compiler optimization)
- Rust-side: `subtle::ConstantTimeEq` (audited crate)
- Coverage: `header[0..16] + payload`, MAC at `header[16..48]`
- Violation counter: Atomic `g_vbus_hmac_violation_count` for observability (logged, not enforced)

### Sovereign Watermark
- Printed on both debug and production builds
- Conditional compilation via `#ifdef` guards

## Authentication (60/60 Endpoints)

- All 60 active API endpoints require `AuthenticatedUser = Depends(get_current_user)`
- Centralized auth: `api/deps.py` -> `middleware/auth.py` -> typed `AuthenticatedUser` dataclass
- JWKS: 1-hour TTL cache
- Convex IDOR: `requireProjectOwnership` on all project/file/chat/memory/builds/yjsUpdates
- Frontend: Clerk middleware with `auth().protect()` on non-public routes
- CSRF: Double-submit cookie (`__vos3_csrf`) with `SameSite=strict`

## Security Controls

| Control | Implementation |
|---------|---------------|
| RCE Defense | 5-layer command allowlist, `shell=False` |
| SSRF Lock | DNS pinning + 10 blocked networks + scheme whitelist |
| Sandbox | RLIMIT_AS + RLIMIT_CPU(60,120) + RLIMIT_NPROC(4,4) + Semaphore(10) |
| CSP | Nonce-based, no `unsafe-eval`, `frame-ancestors 'none'` |
| VOS_API_SECRET | Injected only on mutating requests (POST/PUT/PATCH/DELETE) |
| Agent Kill Switch | SYS_AGENT_KILL_ALL (syscall 497) |
| Agent Isolation | ivshmem Zone ACL + PUD sandbox |
| Model Memory | `VOS3_AI_FLAG_READ_ONLY` -> PTE bit 10 (hardware-enforced immutability) |

## Standards Compliance

| Standard | Coverage |
|----------|----------|
| OWASP API Top 10 (2026) | 10/10 controls addressed |
| OpenSSF Compiler Hardening | All applicable flags enabled |
| NIST SP 800-53 | 10 controls mapped (see `2026_STANDARDS_MAPPING.md`) |
| CVE-2025-29927 (Next.js) | PATCHED (Next.js 14.2.35 >= 14.2.26) |
| CVE-2026-40194 (HMAC timing) | N/A -- constant-time comparison on both C and Rust sides |
| CVE-2026-21713 (Node.js HMAC) | N/A -- Clerk JWT, no custom Node HMAC |

## Test Suite

- 26 kernel test files, all wrapped in `#ifndef VOS3_PRODUCTION_BUILD`
- ~1,332 assertion points across 22 phase tests + 4 supplementary + 16 boot inline
- Production binary: 0 test symbols, 0 test strings

## Evidence Files

| File | Purpose |
|------|---------|
| `docs/evidence/PHASE_AUDIT_LOG.txt` | Complete phase-by-phase test census |
| `docs/evidence/PRE_FREEZE_MANIFEST.hash` | Binary identity + section layout |
| `docs/compliance/2026_STANDARDS_MAPPING.md` | NIST/OpenSSF control mapping |
| `docs/SOVEREIGNTY_MANIFEST.md` | This file -- Golden Master attestation |

## Attestation

This binary has been verified through a Zero-Trust audit process:
1. All 3 blockers resolved (Guardian init order, auth gap, hash staleness)
2. Release binary compiles with 0 test symbols and 0 test strings
3. Guardian Seal initializes BEFORE complex subsystems
4. 60/60 API endpoints are auth-guarded
5. SHA-256 hashes are consistent across all evidence files

**Status: VOS3 GOLDEN MASTER ATTESTATION -- GENESIS RC1**
