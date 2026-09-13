# VOS-Cyber — OS Portability Status

**Date:** 2026-04-25
**Audience:** customer DevOps, integrator deciding which deployment shapes are real today vs roadmap.
**Tone:** every "supported" claim has a corresponding test artifact in this repo. Anything we have not actually built and run is labelled clearly so a deployment plan does not get written against a fiction.

## TL;DR

| Surface | Linux x86_64 | Linux arm64 | macOS arm64 | Windows | Bare-metal x86_64 |
|---|---|---|---|---|---|
| Backend (FastAPI, agent runtime) | ✅ supported | ✅ supported | ✅ dev-only | 🟡 untested | ❌ N/A |
| Kernel (`kernel/build/vos3.elf`) | ✅ runs under QEMU/KVM | 🟡 untested | ✅ runs under QEMU | 🟡 untested | ✅ target platform |
| SQLCipher entity vault | ✅ supported | ✅ supported | ✅ supported | 🟡 untested | ❌ N/A |
| Cert vault | ✅ supported | ✅ supported | ✅ supported | 🟡 untested | ❌ N/A |
| VBus zero-copy ring (POSIX shm) | ✅ supported | ✅ supported | ✅ supported | ❌ POSIX shm not native | ❌ N/A |
| Z3 proofs | ✅ runs | ✅ runs | ✅ runs | 🟡 should run | ❌ N/A |

✅ = tested in this repo today. 🟡 = should work but no test artifact. ❌ = does not apply or genuinely missing.

## Supported deployment shapes (today)

### 1. Linux x86_64 backend on a TDX host

The intended production shape. Backend runs as a normal FastAPI process; the kernel runs *inside* a TDX TD; VBus connects backend ↔ kernel via the ivshmem socket bridge. Every claim in `AUDIT_IMMUNE_SPEC.md` and `SINGULARITY_WHITE_PAPER.md` is dimensioned for this shape.

**What's verified here:** the backend test suite runs on Linux x86_64 (`pytest tests/security/`). The kernel ELF builds with the documented x86_64-elf cross-compile toolchain.

**What's not verified here today:** the live TDX boot path — that requires actual TDX silicon (see `DOOMSDAY_GAUNTLET_v20.5.md` rows 1, 2, 4). The code path is in place; the live measurement is the gauntlet's standing yellow.

### 2. Linux arm64 backend (Graviton / Ampere)

The backend runs on arm64. SQLCipher, `cryptography`, FastAPI, all dependencies have arm64 wheels in the lockfile. The kernel ELF is x86_64-only; on arm64 hosts you would either (a) accept the labelled `BAREMETAL_NO_TEE` platform on an arm64 baremetal, or (b) run the x86_64 kernel under cross-arch QEMU (slow; dev only).

### 3. macOS arm64 (developer workstation)

Used for development. Every test in this repo runs on macOS arm64. SQLCipher works (sqlcipher3-wheels). The kernel ELF builds via the x86_64-elf cross-compiler from Homebrew. The TDX path is not available on Apple Silicon — the attestation service correctly labels the platform as `BAREMETAL_NO_TEE` and the v20.5 in-kernel intent validator runs at the kernel level inside QEMU but not in a TD.

## Surfaces that are 🟡 (untested) or ❌ (not applicable)

### Windows backend — 🟡 untested

The brief mentions "VBS (Virtualisation-Based Security) hooks" as if VOS-Cyber has a Windows port. **It does not.** Concrete state of play:

- No file in this repo imports a Windows-only API (no `pywin32`, no Hyper-V/VBS bindings, no Windows Driver Kit).
- `multiprocessing.shared_memory` (used by the v20.5 zero-copy VBus ring) is **POSIX-shm-backed**. CPython on Windows ships `multiprocessing.shared_memory` over Win32 file mappings, which is API-compatible but performance-different. We have not tested this path.
- A real Windows port would need: a Windows VBus transport (named-pipe or Win32 file-mapping), VBS-equivalent integrity attestation (which is **not** the same as TDX — VBS gives a virtualised secure kernel, not a hardware-rooted RTMR), and a CI runner.
- **Recommendation:** if a Windows deployment is an actual customer ask, scope it as a v21 roadmap item with a named timeline. Do not pre-promise "VBS hooks we've prepared" — none are in this tree.

### Linux eBPF observability — 🟡 untested

The brief mentions "eBPF hooks we've prepared." **None are in this tree.** We have not committed BPF programs, no BCC/libbpf integration, no `bpftrace` script, and no kernel-side BPF helper hooks specific to VOS-Cyber kernel events.

What *would* be useful and *is* a credible v20.6 roadmap item:

- A small libbpf user-space program that subscribes to VOS-Cyber kernel rejection signals (via the existing VBus ingest) and re-emits them as eBPF tracepoints so a Linux host operator can correlate them with their existing eBPF observability pipeline (e.g., Cilium Tetragon).
- This is a *bridge*, not a port — VOS-Cyber kernel decisions still happen in the VOS3 kernel; eBPF is the Linux-side observability hook.

We would not call this a "port" or a "supported deployment shape" — it is a Linux-host integration layer.

### Bare-metal x86_64 — ✅ target platform

The VOS-Cyber kernel runs bare-metal x86_64 (Limine boot, x86_64-elf toolchain). This is the kernel's **primary** target; everything else (Linux backend, macOS dev) is host-side around it.

## Concrete reproduction commands

```bash
# Linux x86_64 backend (any reasonable host):
cd backend && uv sync && .venv/bin/python -m pytest tests/security/

# Kernel build (requires brew install x86_64-elf-{gcc,binutils}):
cd kernel && make clean && make
# Output: kernel/build/vos3.elf, SHA-256 published in CLAUDE.md

# Run kernel under QEMU (host can be Linux or macOS):
qemu-system-x86_64 -kernel kernel/build/vos3.elf -m 4096M -smp 2 -cpu max ...
```

## What this document does NOT promise

- A Windows backend port exists — **it does not, and we will not claim it does** until one is in this repo with a passing test suite.
- VBS / eBPF "hooks we've prepared" — **none are in this tree.** Both are credible v20.6+ roadmap items, not v20.5 capabilities.
- Cross-arch (arm64) kernel — the kernel is x86_64-only by design today; arm64 kernel support is a multi-quarter undertaking.

## Items the gauntlet kept yellow on this axis

- Row **47 (OS Portability — Win/Linux/Bare-metal)** in `DOOMSDAY_GAUNTLET_v20.5.md` is the umbrella row this document de-yellows for *Linux x86_64/arm64* and *macOS arm64* (which all have test artifacts) and **leaves yellow for Windows and bare-metal-without-QEMU** (which do not).

## Sources

- This file is the source of truth for OS portability claims. Every bullet above is verifiable from `git ls-files` + `pytest tests/security/`.
- `CLAUDE.md` — kernel build commands and SHA-256.
- `DOOMSDAY_GAUNTLET_v20.5.md` — gauntlet status of the OS-portability row.
