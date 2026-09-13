# VOS3 Open-Core Licensing Strategy
## v20.5.2 — Project ADAPTER Activation
**Date: April 29, 2026 | Status: Engineering scaffolded; legal review PENDING**

---

## Executive Summary

VOS3 is transitioning from a single-license MIT repository to an
**open-core dual-license model**:

- **VOS3 Core** (open, MIT) — kernel substrate, MMR audit, VBus protocol,
  the universal sovereignty primitives every developer can build on
- **VOS3 Sovereign Enterprise** (proprietary) — 10 GiB hugepage scaling,
  TPM hardware sealing, NPU passthrough, fine-tuning engine

This document codifies the split, the gating mechanism, and — most
importantly — the **boundaries the split must not cross**.

---

## 1. The Open-Core Charter (Engineering Constraints)

The split is governed by three rules. Every code change that touches
the open/proprietary boundary must satisfy all three.

### Rule 1: Infrastructure is always open

The following primitives are **never** gated, regardless of license
posture, because gating them would brick the kernel for unlicensed
users (the opposite of open-core):

- `vos3_vmm_cas_pte` — atomic ISR-safe CAS used by 20+ files
- W^X PTE enforcement — `vmm.c:889`
- Slot ZOMBIE state machine — `slot_state.c`
- MMR audit chain — `mmr_audit.c`
- VBus protocol & dispatcher — `virtio_bridge.c`
- Boot path — `boot/*.c`

### Rule 2: Premium scaling is gated

Capability ceilings that benefit from larger hardware budgets ARE
gated. The pattern: a CORE build gets a bounded-but-functional
ceiling; a PRO build unlocks the larger ceiling.

| Feature | CORE ceiling | PRO ceiling |
|---------|-------------|-------------|
| Hugepage pool | 256 entries (512 MB) | 5,120 entries (10 GiB) |
| KV-cache per slot | 512 MB working | 10 GiB w/ dynamic expansion |
| Long-context inference | up to 32k tokens | beyond 32k tokens |

Source of truth: `kernel/src/mm/pmm.c` `#ifdef VOS3_PRO`.

### Rule 3: Hardware-vendor-anchored features are gated

Features that depend on per-customer hardware provisioning (TPM
endorsement keys, NPU vendor SDK access, signed boot images) are PRO,
because they cannot be meaningfully democratized — every install of
them requires custom configuration.

- TPM PCR sealing (`tpm2.c`) — requires per-host TPM provisioning
- NPU gradient offload (`npu_ops.c`) — requires vendor binary driver
- VFIO PCI passthrough (`vfio_core.c`) — requires hardware affinity
- Fine-tuning engine (`pro/finetune_engine.py`) — requires Ollama-TITAN
  + ML stack (~4 GB CUDA runtime)

---

## 2. File Classification (v20.5.2 baseline)

### Core (always present, MIT)

```
kernel/src/mm/vmm.c                    — VMM, W^X, vos3_vmm_cas_pte
kernel/src/mm/pmm.c                    — PMM (with PRO-gated pool size)
kernel/src/sec/mmr_audit.{c,h}         — MMR audit chain
kernel/src/sec/slot_state.c            — slot state machine + scrub
kernel/src/drivers/virtio_bridge.c     — VBus dispatcher
kernel/src/drivers/virtio_*.c          — VBus transport
kernel/src/boot/*                      — boot path
kernel/include/vos/vmm.h               — VMM API
kernel/include/ipc/slots.h             — slot capability flags
kernel/include/vos/sha256.h            — crypto helpers
backend/api/*                          — FastAPI routes (Apache?)
backend/services/regional_policy.py    — EU AI Act enforcement
backend/services/request_manifest.py   — privacy mandate
backend/services/agent_orchestration.py — TITAN orchestration
backend/services/vbus_driver.py        — VBus client
tools/vos3_mcp_bridge.py               — MCP bridge (kill-list P0-A)
tools/vos3_verify.py                   — auditor verifier
```

### Pro (proposed proprietary, MIT today pending legal review)

```
kernel/src/sec/tpm2.{c,h}              — TPM PCR sealing
kernel/src/mm/ai_pte.c                 — PTE inversion + CRC32C + XXH3
kernel/src/drivers/gpu/vfio_core.c     — PCI BAR slot mapping
kernel/src/ai/npu_ops.c                — NPU gradient offload
kernel/src/pro/license_check.c         — boot-time license posture
kernel/include/ai/kv_cache.h           — KV-cache constants
backend/pro/finetune_engine.py         — QLoRA fine-tuning
```

---

## 3. The Gating Mechanism

### Compile-time: `VOS3_BUILD_TYPE`

```bash
# CORE build
make VOS3_BUILD_TYPE=CORE

# PRO build
make VOS3_BUILD_TYPE=PRO

# Default (no flag): currently PRO for v20.5.x continuity.
```

The Makefile wires this via the `VOS3_PRO` C define (set when
`VOS3_BUILD_TYPE=PRO` is passed, or when no flag is passed). Code
guards premium ceilings with `#ifdef VOS3_PRO`.

### Boot-time: `vos3_pro_license_check()`

`kernel/src/pro/license_check.c` exposes a single source-of-truth
boolean for "is this a PRO instance." The implementation today is the
compile-time `VOS3_PRO` flag; v20.6 will add real signature
verification of a `vos3.lic` blob in the boot partition.

### Verification

Both builds compile clean at the snapshot baseline:
- PRO: 59 warnings, 0 errors, BSS = 59,805,728 bytes
- CORE: 59 warnings, 0 errors, BSS = 59,727,904 bytes
- Delta = 77,824 bytes (4,864 fewer pool entries × 16 B = exactly the
  expected difference)

This proves the gate operates at the binary level, not just at logging.

---

## 4. What This Document Does NOT Do

Honest scoping:

- **Does not change the actual `LICENSE` file.** The repo-root LICENSE
  remains MIT. The "Proprietary" headers added to PRO files are
  PROPOSED markers. They take legal effect only after the legal team
  signs off on the dual-license split.
- **Does not implement signature verification.** The `vos3.lic`
  signing CA + verifier public key are not provisioned. v20.6 work.
- **Does not physically relocate certified kernel files.** `tpm2.c`
  and `ai_pte.c` are *classified* as PRO but live at their original
  paths under `kernel/src/` because moving them would break tightly
  coupled internals (`ai_guard_internal.h` shared header,
  `boot_drivers.c` tpm2 caller). The `kernel/pro/` directory is a
  documentation marker explaining this trade-off.
- **Does not gate base infrastructure.** `vos3_vmm_cas_pte` and friends
  remain in CORE per Rule 1.

---

## 5. Open Questions for Legal Review

1. **Dual-license compatibility** — can a single source file carry
   both an MIT (repo-wide) and a proprietary (intent-only) license
   header without conflict? Common pattern in commercial open-core
   projects (e.g., Elastic's old dual-license model).

2. **Contributor License Agreement (CLA)** — does VOS3 need a CLA for
   contributions to ensure the eventual proprietary license can be
   granted by the project? Most open-core projects say yes.

3. **GPL contagion** — if any Linux GPL header is transitively included
   by a PRO file, the proprietary classification breaks. Must audit
   the include graph. Initial pass: VOS3 is freestanding C with no GPL
   dependencies in production (per `docs/SPDX.md`).

4. **EU AI Act** — does classifying audit infrastructure as PRO create
   compliance risk for unlicensed CORE users? The MMR audit chain is
   CORE precisely to avoid this; documenting that explicitly here.

---

## 6. Roadmap to Real Open-Core (v20.6+)

| Milestone | Owner | Deliverable |
|-----------|-------|-------------|
| Legal review of dual-license | Legal | Updated LICENSE + LICENSE-PRO files |
| Signing CA provisioned | Eng + Legal | Ed25519 keypair, public key embedded in kernel |
| `vos3.lic` format spec | Eng | RFC-style spec doc + reference encoder |
| Signature verify in `license_check.c` | Eng | Read /boot/vos3.lic, verify Ed25519 |
| Default build → CORE | Eng | Flip Makefile default; PRO becomes opt-in |
| CLA in place for contributions | Legal | DCO or CLA bot wired to PR pipeline |

---

*VOS3 Open-Core Licensing Strategy — v20.5.2 — April 29, 2026*
*"Make the substrate free. Sell the unlock."*
*Repo license: MIT (until legal review completes)*
