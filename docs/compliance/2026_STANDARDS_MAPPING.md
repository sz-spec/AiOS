# VOS3 Compliance Standards Mapping (2026)

Generated: 2026-04-14
Binary: kernel/build/vos3.elf
Branch: feat/10-10-all-capabilities

## NIST SP 800-53 Rev 5 Controls

| Control | Requirement | VOS3 Implementation | Evidence |
|---------|-------------|---------------------|----------|
| CM-7 | Least Functionality | Phase 9.1 `#ifdef VOS3_PRODUCTION_BUILD` guards strip 26 test files + boot inline tests from production | `make release` produces test-free binary; `nm` shows 0 `test_phase` symbols |
| SC-12 | Cryptographic Key Establishment | X25519 + HKDF-SHA256 (RFC 7748/5869) for key exchange; entropy-derived sovereign keys | `crypto/x25519.c`, `crypto/tls13.c`, `boot_ai.c:294-300` |
| SC-13 | Cryptographic Protection | SHA-256 (FIPS 180-4), HMAC-SHA256 (RFC 2104) for frame auth and integrity | `crypto/sha256.c`, `drivers/vbus_transport.c` |
| SC-28 | Protection of Information at Rest | Guardian Seal runtime SHA-256 verification of `.text` section | `sec/immutable_lock.c` |
| SI-7 | Software/Firmware Integrity | Boot-time SHA-256 hash of `[_text_start, _text_end)`; constant-time XOR comparison | `guardian_init()` + `guardian_verify_text()` in `sec/immutable_lock.c` |
| SA-15 | Development Process | 1,332 assertions across 22 phase audits + 4 supplementary + 16 boot inline | `kernel/src/tests/test_phase*.c` (26 files) |
| AC-3 | Access Enforcement | ivshmem Zone ACL with per-agent `owner_tid` tracking; Slot 0 kernel-only guard | `drivers/ivshmem.c`, `mm/ai_slots.c` |
| AC-6 | Least Privilege | W^X hard enforcement (mprotect rejects PROT_WRITE|PROT_EXEC) | `mm/vmm.c` |
| AU-12 | Audit Generation | Per-slot telemetry (CTX_STATS), AI access monitoring | `mm/ai_telemetry.c`, `mm/ai_monitor.c` |
| SC-7 | Boundary Protection | VMM boundary at `0xFFFF800000000000` enforced in all map operations | `mm/vmm.c` |

## OpenSSF Compiler Hardening Guide Compliance

| Requirement | Flag/Implementation | Status | Evidence |
|-------------|---------------------|--------|----------|
| Stack protection | `-fstack-protector-strong` | ACTIVE | 394 `__stack_chk_fail` call sites in binary |
| Control-flow integrity | `-fcf-protection=branch` | ACTIVE | CET IBT via Makefile CFLAGS |
| W^X enforcement | mprotect/mmap reject `PROT_WRITE|PROT_EXEC` | ACTIVE | `mm/vmm.c` returns `-EINVAL` |
| Auto-var init | `-ftrivial-auto-var-init=zero` | PRODUCTION | Added in Phase 9.1 `PRODUCTION` CFLAGS |
| Null pointer safety | `-fno-delete-null-pointer-checks` | PRODUCTION | Added in Phase 9.1 `PRODUCTION` CFLAGS |
| Section GC | `-ffunction-sections -fdata-sections` + `--gc-sections` | ACTIVE | Makefile CFLAGS/LDFLAGS |
| Metadata stripping | `objcopy --remove-section=.comment` post-link | PRODUCTION | `make release` post-link step |

## Cryptographic Inventory

| Algorithm | Standard | Implementation | Binary Symbols |
|-----------|----------|----------------|----------------|
| SHA-256 | FIPS 180-4 | GPR-only freestanding | 8 symbols (sha256.o: 30,248 bytes) |
| HMAC-SHA256 | RFC 2104 | Per-session random key | 7 symbols |
| X25519 | RFC 7748 | Key agreement | `crypto/x25519.c` |
| AES-128-GCM | NIST SP 800-38D | Authenticated encryption | `crypto/aes_gcm.c` |
| HKDF-SHA256 | RFC 5869 | Key derivation | `crypto/tls13.c` |
| CRC32C | Castagnoli | Frame integrity (non-crypto) | `core/crc64.c`, `drivers/vbus_transport.c` |

## Security Hardening Summary

| Feature | Implementation | Symbol Count |
|---------|----------------|--------------|
| KASLR | `limine.conf: kaslr: yes` | N/A (bootloader) |
| SMAP | CR4 SMAP bit + stac/clac | 11 instructions (4 stac + 7 clac) |
| Serialization | mfence/sfence/lfence + cpuid | 73 fence instructions |
| Stack canaries | `__stack_chk_fail` | 394 call sites |
| Agent Kill Switch | SYS_AGENT_KILL_ALL (497) | Privileged syscall |
| Zone ACL | `owner_tid` + `zone_base()` gating | 3 ownership + 2 zone_base |

## Pentest Results

| Test | Target | Result |
|------|--------|--------|
| HMAC Tampering | VBus frame body modification | PASS (frame rejected) |
| HMAC Forgery | Random MAC insertion | PASS (constant-time reject) |
| Key Downgrade | Empty HMAC key | PASS (rejected at handshake) |
| Key Mismatch | Wrong session key | PASS (frame rejected) |
| Empty Payload | Zero-length VBus frame | PASS (handled gracefully) |
| Large Payload | Oversized VBus frame | PASS (truncated to max) |
| Replay Attack | Duplicate sequence number | PASS (sequence check reject) |
| Timing | MAC comparison timing variance | PASS (constant-time verified) |
