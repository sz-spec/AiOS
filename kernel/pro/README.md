# VOS3 Pro — Sovereign Enterprise Components

This directory is a **logical marker** for the open-core split.

## Why this directory exists vs. where the code actually lives

The kernel's existing build system (`kernel/Makefile`) requires source
files to live under `$(SRC_DIR)` (= `kernel/src/`). Physically moving
already-certified PRO sources out of `kernel/src/` would:

- Break tightly-coupled internals — `ai_pte.c` shares an internal
  header (`ai_guard_internal.h`) with `ai_guard.c`, which is a CORE
  file. Splitting them physically would require extracting a stable
  ABI between them, a multi-week refactor.
- Break the boot path — `tpm2.c`'s `tpm2_boot_measurement()` is
  called from `kernel/src/boot/boot_drivers.c:123`. Moving the source
  while preserving the boot symbol resolution requires updating every
  Makefile pattern rule.
- Add risk at T-48h before a launch — the certified 145/145 test
  baseline took weeks to establish.

The pragmatic pattern below preserves the open-core *intent* without
the structural risk:

## Logical Open-Core Classification

The split is enforced **at compile time** via the `VOS3_PRO` define and
**at boot time** via `vos3_pro_license_check()`, not by physical file
location.

### Files classified as PRO (Sovereign Enterprise)

| Path | What it contains | Why it's PRO |
|------|------------------|--------------|
| `kernel/src/sec/tpm2.c` + `.h` | TPM 2.0 PCR sealing, Arrow of Time | Hardware-rooted attestation |
| `kernel/src/mm/ai_pte.c` | PTE inversion, CRC32C, XXH3 | Slot-isolation primitive |
| `kernel/src/sec/slot_state.c` | ZOMBIE quarantine + PUD scrub | Sovereign isolation |
| `kernel/src/drivers/gpu/vfio_core.c` | PCI BAR slot mapping | Hardware passthrough |
| `kernel/src/ai/npu_ops.c` | NPU gradient offload skeleton | Hardware AI acceleration |
| `kernel/src/pro/license_check.c` | Boot-time license posture | The gate itself |
| `kernel/include/ai/kv_cache.h` | KV-cache constants (10 GiB ceiling) | The gated capability |
| Hugepage pool sizing in `kernel/src/mm/pmm.c` | 5,120 entries vs 256 | Compile-time `#ifdef` |
| `backend/pro/finetune_engine.py` | QLoRA fine-tuning + MMR audit | Training pipeline |

### Files explicitly classified as CORE (always present, MIT)

| Path | What it contains | Why it's CORE |
|------|------------------|---------------|
| `kernel/src/mm/vmm.c` (incl. `vos3_vmm_cas_pte`) | Atomic CAS, W^X enforcement | **Infrastructure** — disabling bricks the kernel |
| `kernel/src/sec/mmr_audit.c` + `.h` | MMR audit chain | Trust primitive |
| `kernel/src/drivers/virtio_bridge.c` | VBus dispatcher | The "DirectX of AI" — must be open |
| `kernel/include/ipc/slots.h` | Slot capability flags | API surface |
| `kernel/include/vos/vmm.h` | VMM API + `VOS3_PUD_SIZE` | API surface |
| `kernel/src/boot/*` | Boot path | Must work for everyone |

## Build commands

```bash
# Open-core build (CORE) — 512 MB hugepage ceiling
make VOS3_BUILD_TYPE=CORE

# Sovereign Enterprise build (PRO) — 10 GiB hugepage ceiling
make VOS3_BUILD_TYPE=PRO

# Default (no flag): currently PRO for v20.5.x continuity.
# Will become CORE-default in v20.6 once licensing infra (signed
# vos3.lic + CA) ships.
```

## Honest scope of `kernel/src/pro/license_check.c`

What it DOES today:
- Compile-time gate via `VOS3_PRO` define
- Logs the build label at boot via `vos3_pro_build_label()`
- Provides single source-of-truth `vos3_pro_license_check()` for the
  rest of the kernel to query

What it does NOT yet do (deferred to v20.6):
- Read a `vos3.lic` file from the boot partition (early-boot VFS
  is not yet stabilized)
- Verify Ed25519 signature on the license blob (the signing CA is a
  legal/operational task, not engineering)
- Validate license expiry against TPM-rooted boot timestamp

## What gating absolutely does NOT do

Per the open-core charter, the following are **always available** in
both CORE and PRO:

- `vos3_vmm_cas_pte` (the ISR-safe atomic CAS — used by 20 files)
- W^X PTE enforcement
- Slot ZOMBIE state machine
- MMR audit chain (kernel + Python attestation header)
- VBus protocol (the universal substrate API)

Disabling any of these for unlicensed users would be a denial-of-
service primitive, not an open-core boundary.

## Legal note

The "VOS3 Sovereign Enterprise — Proprietary" headers added to PRO
files in v20.5.2 are a **proposed licensing scheme**. The repo-wide
`LICENSE` file remains MIT pending legal review of the dual-license
split. Until that review completes, all VOS3 code is governed by the
existing MIT license. **This is a notice of intent, not a binding
license change.**

See also:
- [`docs/strategy/OPEN_CORE_LICENSING.md`](../../docs/strategy/OPEN_CORE_LICENSING.md) — full charter, three-rule discipline, file classification
- [`LICENSE_PRO`](../../LICENSE_PRO) — proposed BSL 1.1-style proprietary text (template, awaiting legal sign-off)
- [`CONTRIBUTING.md`](../../CONTRIBUTING.md) — DCO + CLA framework for upstream contributions
