# vOS — True Architecture, Demystified (engineering ground truth)

**Branch/HEAD:** `feat-m3-ed25519-verify` @ `72ebe6a` · **Date:** 2026-06-08
**Scope:** fact-only architecture record from source inspection. No marketing,
no valuation, no roadmap-as-fact. Every claim is grep/file-verifiable.

---

## 1. System boundary reality check

**What vOS physically is:** a **freestanding x86-64 C microkernel**
(`kernel/build/vos3.elf`) **plus** a host-side **Python/TypeScript application +
agent layer** (`backend/`, `frontend/`, `backend/mcp-server/`). These are two
distinct programs, not one image.

**What it is NOT:**
- **Not a Windows/macOS/iOS application.** There is no host-OS app bundle for the
  kernel; `vos3.elf` is an ELF that boots on the x86-64 platform, not a process
  under a host OS. (The desktop shell in `desktop/` is a separate Tauri wrapper.)
- **Not a from-source bare-metal product *today*.** The Makefile is explicit:
  `kernel-linux → build/vos3.elf` is annotated **"Linux/QEMU multiboot2 — REAL"**,
  while `kernel-baremetal → build/vos3.efi` is annotated **"Bare-metal UEFI —
  STUB"**. So the *validated* execution path is **QEMU**, and the UEFI bare-metal
  path is an incomplete stub.

**Accurate one-line model:** vOS is a **genuine multiboot2 microkernel whose
exercised/validated runtime is a QEMU virtual machine**, paired with a
**host-resident Python/TS agent framework** that talks to it. It *could* boot
bare-metal in principle (it implements multiboot2/limine/EFI/PVH entry paths —
the boot serial shows `MB / PV64 / LMOK`, i.e. PVH long-mode), but the
production bare-metal UEFI target is not finished.

**Evidence:** `kernel/Makefile` boot stubs (`multiboot2_stub.c`, `limine_stub.c`,
`efi_stub.c`); `kernel/src/boot/kmain.c`; the `kernel-linux`/`kernel-baremetal`/
`kernel-hyperv` multi-target block.

---

## 2. Kernel layer anatomy (the C code)

**Tree:** `kernel/src/` has 21 subsystems —
`arch, boot, core, crypto, diag, drivers, exec, fs, hyperv, init, ipc, mm, net,
pro, sched, sec, time, ui, ai, bench, tests`.

**Boot (`boot/kmain.c::kernel_main`)** — observed init order:
`vos3_console_init` → `vos3_klog_init` → `vos3_gdt_init` → `percpu_init` →
`vos3_idt_init` → `vos3_pic_init` → `vos3_interrupts_init` → `vos3_kaslr_init` →
`vos3_pci_ecam_init` → `vos3_mitigation_factory_init` → `vos3_kpti_init` → …
(then scheduler, heap, VBus, AI subsystems). Under the assert-harness build,
`vos3_run_cert_harness()` emits `~~CERT~~` lines on COM1 for the QEMU runner.

**Memory (`mm/pmm.c`)** — physical frame allocator with a static huge-page pool.
The pool ceiling is now **open-core gated**:
```c
#ifdef VOS3_PRO
#define VOS3_HUGEPAGE_POOL_MAX  5120
#else
#define VOS3_HUGEPAGE_POOL_MAX  256
#endif
static uint64_t g_hugepage_pool[VOS3_HUGEPAGE_POOL_MAX];
```
Higher-half kernel mapping enforced at `0xFFFF800000000000`.

**Model verification (`fs/vvfs_transport.c` + `fs/vvfs_model_verify.c`)** — M3
SecureBoot. The real read path is the transport layer (`vvfs_read_block`); the
gate (`vvfs_model_sig_verify`, behind `VOS3_VVFS_REQUIRE_MODEL_SIG`,
**default-OFF**) admits only slots whose model signature was verified at
`SLOT_FINISH` by `vvfs_verify_model_slot()`: SHA-256(model) == OMS digest, signer
== build-time trust anchor (`vvfs_trusted_key.h`, not runtime-settable), and a
freestanding Ed25519 verify (`crypto/ed25519_verify.c`, RFC 8032, `S<L`
malleability-hardened) over the digest. SHA-512 lives in `crypto/sha512.c`.

**Compilation pipeline (`kernel/Makefile`)** — cross-compiles with
`x86_64-elf-gcc` (freestanding, `-ffreestanding`, GPR-only crypto). `C_SOURCES`
is an explicit list (not wildcard). Build-type gate: `VOS3_BUILD_TYPE=PRO`
defines `-DVOS3_PRO`; `=CORE` defines `-DVOS3_CORE`; `VOS3_ASSERT_HARNESS=1`
enables the cert harness; `BENCH_MODE=1` raises `VOS3_LOG_MIN_LEVEL` to silence
DEBUG. Reproducible flags: `SOURCE_DATE_EPOCH`, `--build-id=none`,
`-ffile-prefix-map`. Output: `build/vos3.elf`, run via the `qemu` targets.

---

## 3. Agent framework anatomy (Python / TypeScript)

**Backend (`backend/`)** — FastAPI app (`main.py`) + service layers. Routing IP
in **`backend/src/`**: `smart_routing.py`, `mcp_provider.py`, `code_generator.py`,
`observability.py`, and the **`efficiency/`** package (the router).

**Routing (`backend/src/efficiency/router.py`)** —
- `assign_model(role, complexity)` selects a model. With the optional
  `shared-ai-router` `SmartRouter` installed it uses the real role/complexity
  matrix; otherwise it uses `_FALLBACK_MODELS` and **raises `KeyError` on an
  unrecognised role** (loud-fail contract).
- **Local-first enforcement:** `get_optimal_model(role, complexity, local_only,
  …)` routes non-critical roles to `local-titan` (Ollama) under
  `VOS3_DEFAULT_LOCAL_FIRST` when reachable; `local_only` forces all roles local.
  `services/agent_orchestration.orchestrate_pre_flight` raises **HTTP 503 +
  `Retry-After: 30`** when local is required but TITAN is down — never silent
  cloud egress. `services/regional_policy.py` fail-closes (`ComplianceDenied`)
  when an EU/sovereign request has no compliant local/sovereign path.

**MCP bridge (`backend/mcp-server/`)** — the Model Context Protocol server
(config/, Dockerfile, k8s/, monitoring/) that bridges tools to the agent layer.

**Runtime requirements (verified on this host):**
- **Python `.venv_p312` → CPython 3.12.13** (FastAPI/pytest/langchain stack).
- **Node.js v24.14.0** (frontend / MCP-server TS).
- **Ollama** daemon for the local-titan lane (`OLLAMA_TITAN_ENDPOINT` →
  fallback `OLLAMA_BASE_URL`); absent ⇒ local lane reported down (fail-closed).
- The kernel↔host bridge is **VBus** (virtio-serial / unix-socket; CRC32C +
  HMAC-SHA256 frames) — see `desktop/src-tauri/src/vbus/` and the `qemu-vbus`
  Makefile target.

---

## 4. Realized invariants vs. roadmap (commodity HW today vs. future silicon)

**Shipped + functional on commodity developer hardware (no special silicon):**
- **Moat 49/80** — fail-closed enforcement primitives (auth/CSRF, SSRF/DNS
  pinning, IOMMU/perf gates as logic layers, taint engine, IBCT, outbound-PII
  shield, etc.).
- **Core release gate 931/931** (`backend/tests/security/ + services/`); broad
  consumer sweep 1,097 pass; documented residual debt (`handover_quality_debt.json`).
- **Local-first routing** (local-titan/Ollama, 503 fail-closed) — real.
- **M3 SecureBoot crypto** — host KAT 21/21, E2E 7/7, **booted-QEMU 67/67 cert
  points** (`kernel/tests/results/m3_phase5_qemu_certs.txt`). Enforcement
  **default-OFF**; row **NOT counted** (still 49/80) pending external audit.
- **Loopback test policy** — CI determinism control (blocks external connects
  during tests); a *test* control, not a production runtime guarantee.

**Requires future silicon / external delivery (roadmap, NOT shipped):**
- Intel **TDX 2.0**, NVIDIA **Blackwell Confidential-Compute**, **TPM 2.0**,
  **CXL 3.0** tiering — the ~31 open catalog rows that are hardware/research/
  upstream-bound (see `docs/release_notes/vOS_Cloud_Enclave_Sovereign_Spec.md`).
  No real TEE attestation exists in the current tree; the bare-metal UEFI target
  is a stub.

**One-paragraph truth:** today vOS is a QEMU-validated microkernel + a
Python/TS local-first agent layer that runs on an ordinary developer machine,
enforces a 49/80 fail-closed security posture, and has a verified-but-unaudited
in-kernel model-SecureBoot path that is shipped switched off. Everything
silicon-confidential (TDX/Blackwell/TPM/CXL) and bare-metal UEFI is roadmap, not
running.

---

*Verification: re-run the inspections behind each section — `ls kernel/src/`,
`grep kernel_main kernel/src/boot/kmain.c`, `sed -n '47,57p' kernel/src/mm/pmm.c`,
the `kernel/Makefile` boot/build-type blocks, `backend/src/efficiency/router.py`,
and `kernel/tests/results/m3_phase5_qemu_certs.txt`.*
