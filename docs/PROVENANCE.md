# vOS.v1 — Build Provenance

**Repository:** `git@github.com:sz-spec/vOS.v1.git`
**Branch:** `unified-master-v1`
**Provenance manifest version:** 1.2 (Universal-Model Upgrade)
**Last updated:** 2026-05-11

---

## 0a. Active Manifest (post 2026-05-11 Universal-Model Upgrade)

```
43bfdd2ac2336d8326209db033c45f414b3fbb2deb3fce904d8aa233b2bc9b9a  build/vos3.elf
773c58aaedc222277cf8b43d435ac03519251288c847f14cef25d3c6c1a1f623  build/vos3.efi
14f176756028a64d42f50955c1ad8cf8601817302a7ca3010b66f3ffc756ddec  build/vos3-hyperv.elf
```

These SHAs supersede the 2026-05-09 RC manifest (`5a47e556…`). The hash change
is **expected**: `kernel/include/vos/ai_kim.h` had its
`VOS3_KIM_MAX_VOCAB` (65 536→262 144), `VOS3_KIM_MAX_DIM` (8 192→16 384),
`VOS3_KIM_MAX_SEQ_LEN` (4 096→8 192), and `VOS3_KIM_KV_PAGES` (4→8) constants
lifted to admit the May-2026 universal-model family (Gemma 3, Llama 4 Scout/
Maverick, DeepSeek R1, Qwen3, Phi-4). No other kernel source file was modified
by the upgrade. Reproducibility flags unchanged (`SOURCE_DATE_EPOCH=1700000000`,
`--build-id=none`, `-ffile-prefix-map`).

---

## 0. Resilience-Repair Status (2026-05-09)

After the 200-vector Resilience Matrix audit identified 16 FAIL rows
deduplicating to 11 distinct fixes, the Resilience-Repair pass landed
all 11 fixes in tree (F1–F11). A subsequent Final Micro-Pass
addressed 3 residual single-line items (F12–F14) that surfaced during
post-repair re-grading. Final scoreboard:

| Round | Tests | PASS | PARTIAL | FAIL |
|-------|------:|-----:|--------:|-----:|
| 1 — Speed & Performance         | 40 | 40 | 0 | 0 |
| 2 — Hardened Security           | 50 | 50 | 0 | 0 |
| 3 — Connectivity Chaos          | 40 | 40 | 0 | 0 |
| 4 — Functional Bugs & Boundaries| 70 | 70 | 0 | 0 |
| 5 — Doomsday Gauntlet           | (synthesis) | — | — | — |
| **Total** | **200** | **200 (100%)** | **0** | **0** |

**Caveat (engineering honesty):** the 200/200 reflects code-path
coverage in tree — every named stress vector now has a verifiable
in-tree defense or regression-asserted gate at a literal file:line.
**Live-load verification** (10k-user Poisson burst, 72-hour soak,
3-platform synthesis) is the next CI step — see §6 below.

The fixes themselves, by Resilience-Matrix ID:

| ID | Fix | Files touched |
|----|-----|---------------|
| F1 | Per-slot binding spinlock | `kernel/src/drivers/vbus_ai_cmds.c` |
| F2 | Out-of-band `VBUS_TYPE_BUSY` frame | `kernel/include/vos/virtio_vbus.h`, `kernel/src/drivers/virtio_vbus.c` |
| F3 | Hex-decode 1 MiB cap + parity | `backend/kernel_bridge/protocol.py` |
| F4 | Native klog ring buffer | `kernel/include/vos/klog.h`, `kernel/src/diag/klog.c`, `kernel/Makefile` |
| F5 | Wall-clock 2000 ms VBus deadline | `kernel/src/drivers/virtio_vbus.c` |
| F6 | Heap-shrink trigger on PMM low-watermark | `kernel/src/mm/pmm.c` |
| F7 | CSP + security-header middleware | `backend/middleware/security_headers.py`, `backend/app.py` |
| F8 | JWT `jti` replay-protection cache | `backend/middleware/auth.py` |
| F9 | 10-fail account lockout (24 h) | `backend/middleware/rate_limit.py` |
| F10 | Action-bridge idempotency ring + Convex `webhook_seen` | `kernel/src/exec/action_bridge.c`, `frontend/convex/schema.ts`, `frontend/convex/webhook_seen.ts`, `backend/api/clerk_webhook.py` |
| F11 | HTTPS-only outbound (gated by `VOS_PROFILE`) | `backend/kernel_bridge/service.py` |
| F12 | Timezone-aware UTC stamp (replaces last `utcnow()` site) | `backend/api/version_control_routes.py` |
| F13 | `wi` 32-bit overflow guard on V-AAAK encoder | `kernel/src/drivers/vbus_transport.c` |
| F14 | Audit-ring overspeed gap detector + WARN | `kernel/src/mm/audit_ring.c` |

### Post-Repair build SHAs (2026-05-09)

The Resilience-Repair pass touched 6 kernel C files (F1, F2, F4-as-aborted-duplicate, F5, F6, F10, F13, F14), so the deterministic build SHAs roll forward from the pre-repair manifest. Verified bit-identical across two consecutive `make multi-target` invocations:

```
vos3.elf         SHA-256: facf0183316de344349c0f268bdbb5e9f30b8e43278d692ebd93d39160ff86b9
vos3.efi         SHA-256: 773c58aaedc222277cf8b43d435ac03519251288c847f14cef25d3c6c1a1f623
vos3-hyperv.elf  SHA-256: 8d61d5387fc583f21a3019a8826f639f55af858fd01ad042bb3a12b6c31eea5f
```

The `vos3.efi` SHA is unchanged from pre-repair because the EFI stub (`src/boot/efi_stub.c`) is its own translation unit untouched by F1/F2/F5/F6/F10/F13/F14. The vos3.elf and vos3-hyperv.elf SHAs are the new canonical identifiers for v1.0.0-RC1.

> **F4 honest scope note:** the audit's "klog ring" addition was reverted because `kernel/src/drivers/console.c:603-621` already provides the same overwrite-oldest, lock-free, drop-counter ring under the same `vos3_klog_init` / `vos3_klog_stats` symbols. The pre-existing implementation satisfies the F4 requirement; my new `kernel/src/diag/klog.c` was a redundant duplicate that broke the link with a multiple-definition error. The matrix's R1#28 ("klog drop policy under throttle") was effectively PASS pre-repair — my prior matrix miscategorised it as ABSENT because I missed the existing console.c implementation. The post-repair PASS row now correctly cites `kernel/src/drivers/console.c:603-621`.

---

## 1. Repository Genesis

vOS.v1 is the unified successor to three previously-parallel forks. Stage 0 of the merger initialised this repository as a fresh git tree seeded from `vos4` (the Engine), with subsequent stages porting the Cyber security overlay (the Shield) and preserving the VOS3 canonical artifacts (the Product).

The three source trees were verified from their local-filesystem locations only — **no network clones, no upstream fetches** were performed during the merge:

| Source | Local path | Used in stages |
|--------|-----------|----------------|
| vos4 (Engine) | `/Users/sz/Desktop/vos/vos4` | Stage 0 (full clone) |
| VOS3-Cyber (Shield) | `/Users/sz/Desktop/95%ֿ/vos7220206/VOS3-Cyber` | Stages 1–8 (selective port) |
| VOS3 (Product) | `/Users/sz/Desktop/95%ֿ/vos7220206/VOS3` | Stage 9 (handoff + disk.img) |

---

## 2. Forge Log — 10 commits, Stages 0–9

| Stage | Commit | One-line description |
|-------|--------|----------------------|
| 0 | `48fb084` | initial baseline — clone of vos4 as vOS unified engine |
| 1 | `d5bfdb0` | inject cyber security layers (intent_validator, tee, core_cookie, attestation) |
| 2 | `6f182e9` | cyber security layer ported and compiled cleanly. Symbols stripped by GC due to no call-sites |
| 3 | `acbaa13` | awaken cyber security layer. Wired SCHED_CORE cookie init and boot-time IntentManifest self-test |
| 4 | `a237997` | userspace bridge active. Added VBus INTENT_SUBMIT command and hex-decoder |
| 5 | `f1f2ad8` | hardware-rooted enforcement. Integrated TEE slot-binding and model measurement into VBus flow |
| 6 | `180711d` | full cyber surface live. All 18 ported Cyber primitives now have call sites; 23/23 symbols active |
| 7 | `36bebf0` | stability + determinism. Fixed RDRAND #UD on qemu64; deterministic vos3.elf SHA-256 across rebuilds |
| 8 | `1f0bbcf` | neutralized ai_guard #UD on qemu64. Default-CPU boot reaches AI Guard subsystem ready with zero panics |
| 9 | *(this commit)* | VOS3 preservation overlay — handoff dir, disk.img, AUDIT_STAGE_A.md, WASM v44.0.2 pin |

Full commit messages contain the file-level provenance for each stage's port and integration.

---

## 3. Deterministic Artifacts

### 3.1 — `kernel/build/vos3.elf`

The kernel ELF is bit-for-bit reproducible since Stage 7 (commit `36bebf0`), governed by:

- `SOURCE_DATE_EPOCH=1700000000` (2023-11-14T22:13:20Z) pinned in `kernel/Makefile`
- `--build-id=none` in `LDFLAGS`
- `-ffile-prefix-map=$(CURDIR)=.` and `-ffile-prefix-map=$(abspath src)=src` in `CFLAGS`

**Canonical SHA-256 (verified across two consecutive rebuilds with `sleep 2` between them):**

```
SHA-256:  710a82b0d33107ca14c791b35a0945f1a86f910f912bc89bf2e4193fef669b2b
Size:     12,797,816 bytes
.text:    660,723 bytes
.data:    8,430,236 bytes
.bss:     59,751,552 bytes
Banner:   "Built: Nov 14 2023 22:13:20"  (deterministic, from SOURCE_DATE_EPOCH)
```

Verifiable runtime evidence — boot log `[CYBER]` lines on `-cpu max -smp 1`:

```
[DEBUG] [CYBER] IntentManifest validator self-test PASS (rc=-1)
[DEBUG] [CYBER] SHA-384 streaming-vs-one-shot self-test PASS
[INFO]  [SCHED_CORE] topology table populated: 7 cpus
[INFO]  [CYBER] SCHED_CORE sibling topology initialized
[INFO]  [AI-GUARD] AI-KASLR v2: base=<...>  (offset=<...>, entropy-subsystem)
```

### 3.2 — Symbol audit (Stage 6 baseline, valid for all subsequent commits)

23 of 23 ported Cyber kernel symbols LIVE in the final ELF:

```
vos3_intent_validate           @ T  (intent_validator.o)
vos3_sched_set_cookie          @ T  (core_cookie.o)
vos3_sched_get_cookie          @ T
vos3_sched_sibling_compatible  @ T
vos3_sched_core_init_topology  @ T
vos3_sched_cookie_rejections   @ T
vos3_tee_model_measure         @ T  (tee.o)
vos3_tee_rtmr_extend           @ T
vos3_tee_env                   @ T
vos3_tee_slot_activate_bound   @ T
vos3_tee_measurements_snapshot @ T
vos3_hcs_smt_siblings          @ T  (hcs.o)
vos3_hcs_flush                 @ T
vos3_sha384                    @ T  (sha384.o)
vos3_sha384_init               @ T
vos3_sha384_update             @ T
vos3_sha384_final              @ T
cmd_intent_submit              @ T  (vbus_ai_cmds.o)
cmd_tee_env                    @ T
cmd_tee_quote                  @ T
cmd_sched_cookie_stats         @ T
cmd_hcs_flush                  @ T
cmd_sched_sibling_check        @ T
```

### 3.3 — `disk.img` (Bare-metal QEMU testbed, gitignored)

The 64 MB raw disk image preserved from VOS3 is **not committed to git** (covered by the existing `.gitignore` rule at line 27: `disk.img`). It is checked into the working tree on Stage-9 prepared workstations as a known-good QEMU testbed for boot-and-attest validation.

```
File:     vos.v1/disk.img
Size:     67,108,864 bytes
SHA-256:  f282f376c6a1eb2abb13a2ab69cf9a0a58a30ea00fe46dea40643b9b4f9981e3
Source:   /Users/sz/Desktop/95%ֿ/vos7220206/VOS3/disk.img (Apr-5-2026)
```

To reproduce on a fresh checkout, copy the file from the canonical VOS3 source location with `cp -p` and verify the SHA-256 matches.

### 3.4 — Reproducibility — three-target manifest *(Stage 14, PRODUCED 2026-05-09)*

The product manifesto promises three reproducible build targets. Stage 14's `kernel/Makefile` `multi-target` rule produces all three from a clean tree, deterministically. SHAs verified across two consecutive rebuilds.

**Authoritative SHA-256 manifest (Stage 14.D.3 — three distinct hashes):**

| # | Target | Artifact | SHA-256 | Status |
|---|--------|----------|---------|--------|
| 1 | Linux ELF | `kernel/build/vos3.elf` | `1bdab44c304003ebf1702dd2417b52a1aa63b1234f8af603e3e50e0634f37029` | ✅ deterministic; full functional kernel; no Hyper-V code paths |
| 2 | Bare-metal UEFI | `kernel/build/vos3.efi` | `773c58aaedc222277cf8b43d435ac03519251288c847f14cef25d3c6c1a1f623` | ✅ deterministic; PE32+ EFI app via `efi_stub.c`, 3.9 KiB |
| 3 | Windows Hyper-V | `kernel/build/vos3-hyperv.elf` | `d8deae7c24058b4b007bfc32170f7616eeaf85699cf3554fdb26af6ad7f834b3` | ✅ **GENUINELY DIFFERENT** from Linux ELF — Stage 14.D.3 ships real Hyper-V init compiled in via `-DVOS3_TARGET_HYPERV` |

**Stage 14.D.3 Hyper-V divergence — what makes the binary different:**

| Layer | Linux build | Hyper-V build |
|-------|-------------|---------------|
| Compile flags | (baseline) | `-DVOS3_TARGET_HYPERV -DHYPERV_EXTENDED_ISOLATION` |
| `kernel/src/hyperv/hyperv_init.c` | excluded by `#ifdef` (empty .o) | compiled in (~250 lines of init code) |
| `boot_drivers.c` Hyper-V branch | excluded by `#ifdef` | calls `vos3_hyperv_init()` after detect |
| Symbols `vos3_hyperv_init`, `hyperv_get_state` | not present in ELF | LIVE in ELF (verified via `nm`) |
| Boot log lines | 4 `[CYBER]` self-tests | same + `[HYPERV]` branch (active when running under real Hyper-V; no-op on QEMU TCG / bare metal) |
| `.text` size | 667,379 B | 667,699 B (+320 B for the init code) |
| Build directory | `build/` | `build-hyperv/` (separate object dir prevents cross-contamination) |

**Spec citations in the Hyper-V code** (verified against published Microsoft docs — see `docs/STAGE_14_RESEARCH_FINDINGS.md` §5):

- TLFS v6.0b §3.6 — `HV_X64_MSR_GUEST_OS_ID` (0x40000000)
- TLFS v6.0b §3.13 — `HV_X64_MSR_HYPERCALL` (0x40000001)
- TLFS v6.0b §10.3 — SynIC enable (`HV_X64_MSR_SCONTROL`, 0x40000080)

**The implementation cites TLFS v6.0b explicitly, NOT v7.0b** — Stage 14 web-search confirmed v7.0b does not exist as a published Microsoft document. The MSR numbers and CPUID semantics are stable across the v4 → v6.0b TLFS lineage; identical to those in Linux's `include/asm-generic/hyperv-tlfs.h`.

**Note on the Linux SHA change since Stage 11:**

| Stage | `vos3.elf` SHA-256 | Why |
|-------|---------------------|-----|
| 11    | `bd0ce974…666e46`  | (baseline; pre-SHAKE) |
| 14    | `9bbaddb4…0a8e955c` | added SHAKE-128/256 module + boot KAT (+~1.6 KiB .text) |
| 14.D.3 | `1bdab44c…34f37029` | added (gated-out) hyperv_init.c TU + `#ifdef VOS3_TARGET_HYPERV` block in boot_drivers.c — both produce empty/excluded code in the Linux build but the new compilation unit affects the linker's debug-section ordering. Functionally bit-equivalent to 14; deterministic across rebuilds. |

**Sigstore v3-shaped bundles** for all three artefacts (Stage 14.D.3 set):

```
infra/security/release_artifacts/vos3_elf_stage14_d3.bundle.json
infra/security/release_artifacts/vos3_efi_stage14_d3.bundle.json
infra/security/release_artifacts/vos3_hyperv_elf_stage14_d3.bundle.json
```

The Stage-14 baseline bundles (`vos3_elf_stage14.bundle.json` etc., signing the `9bbaddb4…` SHA) remain in tree as historical artefacts; the `_d3` bundles supersede them for the current Stage 14.D.3 hashes.

**Rekor v2 transparency log** (`infra/security/rekor_v2.jsonl`): tree size advanced from 2 (Stage 11) → 5 (Stage 14) → **8** (Stage 14.D.3).

```
03be06d9840bb473b7be3cf8a2481b424b80d017397bce1f902986fafc666e46  (Stage 11 root)
fcbd2ef785d5a1f799fdf92fcdba2248ac78da92bcf851d83c17178f2f220496  (Stage 14 root)
46a2e140d855496b1b11cd22b68bc854ee6763b42548a9ea98235d754796e025  (Stage 14.D.3 root)
```

Each entry's audit path can be verified independently via `infra/security/rekor_v2_log.py::verify_stored_entry`.

**Reproducing the manifest** (single command from a fresh checkout):

```bash
make -C kernel clean && make -C kernel multi-target
# Outputs the manifest above on stdout.
```

**Honest scope ceiling on the Hyper-V target (Stage 14.D.3 update):**

The Hyper-V variant now genuinely diverges from the Linux build via real CPUID detection + MSR setup compiled in only when `-DVOS3_TARGET_HYPERV` is set. The implementation cites TLFS v6.0b sections explicitly.

What's STILL stubbed in the Hyper-V build (Stage 14.D.3.X follow-ups):

| Sub-stage | Deliverable |
|-----------|-------------|
| 14.D.3.2 | STIMER-based timer source (replace PIT/HPET in `kernel/src/drivers/timer.c`); MSR addresses already documented in `hyperv_init.c` |
| 14.D.3.3 | Per-vCPU SynIC Event Log Page allocation + SINT0-15 enrolment (TLFS v6.0b §10.4) |
| 14.D.3.4 | `vos3_hyperv_hypercall()` real implementation (allocate hypercall page, R-X map, write MSR with GPA, exercise via `HvPostMessage`) |

Each deliverable is single-file scope; the public API surface is declared today so callers can be wired against `vos3_hyperv_init()` without forward-decl conflict.

---

## 4. Preserved VOS3 Handoff Package

`vos.v1/VOS3_ULTIMATE_HANDOFF_2026/` contains the canonical investor / auditor / acquirer package as it existed on the VOS3 v20.0 Genesis Master release. Verbatim copy via `rsync -a`; file count matches source (13 files); spot-check SHA-256s match.

Key entries:

| File | Purpose |
|------|---------|
| `README.md` | Top-level handoff narrative |
| `EXECUTIVE_VALUE_PROP.md` | Investor-facing value proposition |
| `FINAL_SHA_MANIFEST.json` | The VOS3 Genesis Master signed-hash manifest |
| `GLOBAL_VOS3_RELEASE_MANIFEST.json` | Release composition |
| `GLOBAL_SIGNATURE.txt` | Top-level signature over the manifest |
| `TITAN_VERIFICATION_REPORT.json` | Diligence-grade audit memo |
| `legal_strategic/` | Sub-tree of legal / strategic docs |
| `verification/` | Sub-tree of verification artefacts |

`vos.v1/AUDIT_STAGE_A.md` (11,653 bytes, SHA-256 `407a1f213c2d4e47…`) preserves the original VOS3 Stage-A audit baseline as a historical reference.

---

## 5. Reproducing the vos3.elf Hash

On a workstation with the cross-compiler installed (`x86_64-elf-gcc 13.2.0`, `binutils 2.42`):

```bash
git clone git@github.com:sz-spec/vOS.v1.git
cd vOS.v1
git checkout unified-master-v1
cd kernel
make clean && make
shasum -a 256 build/vos3.elf
# Expected: 710a82b0d33107ca14c791b35a0945f1a86f910f912bc89bf2e4193fef669b2b
```

If the hash does not match, check:

1. The cross-compiler version (`x86_64-elf-gcc --version` — must be 13.2.0).
2. The `SOURCE_DATE_EPOCH` value (`echo $SOURCE_DATE_EPOCH` — must be unset or `1700000000`).
3. The clone is on `unified-master-v1` at commit `1f0bbcf` (Stage 8) or later.

Reproducible builds are also available via the in-tree `Dockerfile.kernel` for hosts without a local cross-compiler:

```bash
docker build -f Dockerfile.kernel -t vos3-builder .
docker run --rm -v $(pwd):/vos3 vos3-builder make -C /vos3/kernel
```

---

## 6. Forward Compatibility — Future Stages

This document is the canonical provenance source for vOS.v1. It will be updated at the conclusion of each future stage:

| Stage | Provenance impact |
|-------|-------------------|
| Stage 10 | New compliance artifacts — audit_ring kernel module, signed JSON-LD failure stream |
| Stage 11 | Sigstore + SBOM + VEX manifests; `infra/security/build_sbom.py` outputs |
| Stage 12 | Test-suite results manifest (85 security + 4 Z3 UNSAT proofs) |
| Stage 13 | Compliance docs index + Post-Quantum inventory + System-Context prompt |
| Stage 14 | Three-target reproducible build hashes (Linux + Bare-metal UEFI + Hyper-V); Sovereign Boot shim hash |

---

**End of provenance manifest v1.0.**
