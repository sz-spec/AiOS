# vOS GA 1.3 — Security Audit Master Ledger

> **Classification:** Internal — Security Engineering / GA Release Gate
> **Scope:** Adversarial security audit of the vOS sovereign-agent stack
> (Python application layer + freestanding `vos3.elf` microkernel layer).
> **Branch:** `feat/shield-integration` · **Base:** `main@b3a6883` (v1.3.0-ga slice)
> **Audit window:** 2026-06-12 · **Mode:** autonomous, fail-closed-first
> **Working-tree state:** all artifacts below are **uncommitted, unstaged** by
> design — this ledger documents a working-tree audit, not a merged change.

---

## 1. Executive Summary & Core Metrics

This audit added **280 new adversarial security tests** mapped to the
`docs/TEST_PLAN_300.md` matrix, executed **6 active root-cause vulnerability
patches** (H.17, B8, B5-1, B7-1, A3-1 + the S2 A1 ELF-loader RWX bypass) plus
the TS-2026-PF_MEMSET_SCRUB kernel stability fix, and registered **5
architectural findings** ("sharp edges"), **four of which (B5-1, B7-1, A1,
A3-1) are now patched** — only B3-1 (eBPF race ceiling) remains unclosed (now
⚪ PARTIAL: host-side mark-and-push landed, live kernel LSM + external audit
pending — moat unchanged). Every
fail-closed enforcement gate exercised
held under adversarial input; the core cryptographic, memory-protection, and
storage-isolation subsystems of the microkernel are verified **S1-Grade** (no
exploitable boundary, exhaustively or KAT-proven).

| Metric | Value |
|---|---|
| New adversarial tests | **287** (100% green) |
| — Python application layer | 207 |
| — C microkernel layer | 80 |
| Active vulnerability patches executed | **6** (H.17, B8, B5-1, B7-1, A3-1 + S2 A1 ELF RWX bypass) + kernel stability fix TS-2026-PF_MEMSET_SCRUB |
| Architectural findings registered | **5** (B5-1, B7-1, A1, A3-1 ✅ PATCHED; B3-1 ⚪ PARTIAL — host-side closure landed, live kernel LSM + external audit pending) |
| Fail-open / bypass vulnerabilities | 0 net-open (A3-1 now hardened to a 3-valued fail-closed contract; B3-1 documented/compensated) |
| Subsystems verified S1-Grade | 4 kernel (W^X, VBus-HMAC, model SecureBoot, vVFS isolation) |
| Commits / staged changes | **0** (working-tree only, by mandate) |

### 1.1 Module → Layer → Coverage Map

| Module | Subsystem | Target Layer | Tests | Status |
|---|---|---|---:|:---:|
| **H.17** | Fail-open sweep (all `require_*`/`check_*`/`validate_*` gates) | Python · cross-cutting | 61 | ✅ 100% |
| **B8** | Semantic firewall input boundary | Python · `api/chat_routes.py`, `services/semantic_firewall.py` | 3 | ✅ 100% |
| **B9** | SPIFFE/AIMS cross-domain identity federation | Python · `services/identity_federation_bridge.py` | 17 | ✅ 100% |
| **B4** | Dual-LLM quarantine / isolation | Python · `ai/agents/dual_llm_router.py`, `services/quarantined_llm.py` | 26 | ✅ 100% |
| **B5** | IBCT delegation + agent capability table | Python · `core/security/{ibct_engine,agent_capability_table}.py` | 22 | ✅ 100% |
| **B6** | GPU/accelerator isolation gates (E3/E6/E2/O4) | Python · `security/{iommu_dma_guard,perf_counter_lockdown,accel_ioctl_filter,gpu_alloc_validator}.py` | 25 | ✅ 100% |
| **B3** | eBPF host↔guest write-gate bridge (+12 live mark-push bridge tests) | Python · `security/kernel_gate_connector.py` | 29 | ✅ 100% |
| **B7** | MAIF provenance + runtime integrity watchdog | Python · `services/maif_v2.py`, `security/model_integrity_watchdog.py` | 24 | ✅ 100% |
| **A1** | PTE W^X sanitizer + memory boundary | C · `include/vos/vmm.h`, `src/mm/vmm.c`, `src/exec/elf.c` | 23 | ✅ 100% |
| **A3** | VBus per-command HMAC trailer verify | C · `src/drivers/vbus_transport.c`, `src/crypto/sha256.c` | 15 | ✅ 100% |
| **A6** | Model/driver SecureBoot signature gate | C · `src/fs/vvfs_model_verify.c`, `src/crypto/{ed25519_verify,sha512}.c` | 12 | ✅ 100% |
| **A8** | vVFS isolation: path-traversal jail + multi-tenant ACL | C · `src/fs/{vfs,vvfs,vvfs_transport,vector_vfs}.c` | 16 | ✅ 100% |
| **M3** | vVFS model-signature read-gate enforcement (ON + default-OFF) | C · `src/fs/vvfs_transport.c` (gate) + `vvfs_model_verify.c` (real verify) | 7 | ✅ 100% |
| **B3-MP** | Atomic mark-and-push struct contract (G5 host sizeof/offset probe) | C · `include/vos/taint_maps.h` | 7 | ✅ 100% |
| | | **TOTAL** | **287** | **✅ 100%** |

> **Count reconciliation (2026-06-13, re-run vs `main@b67e45c`):** the **A3**
> HMAC-trailer suite executes **15** host-tests — A3.1–A3.5 (populated-path
> soundness) **plus** A3-1.1–A3-1.6 (the three-valued `VALID`/`INVALID`/
> `NO_TRAILER` strip/no-key regressions landed with the A3-1 patch) — not the
> 12 recorded at audit freeze. Correcting that single cell lifts the C-microkernel
> layer **70 → 73** and the adversarial grand total **265 → 268**. Verified live:
> `run_kernel_audit.sh` → `ALL KERNEL AUDIT TESTS PASSED` (A1 23 · A3 15 · A6 12 ·
> A8 16 · M3 5+2) and 413 passed in `backend/tests/audit/` (195-test adversarial
> subset). Python layer (195) is unchanged.

> **B3 suite expansion (2026-06-13, vs `main@b67e45c`):** added **12** §9
> Mock-side adversarial tests to `test_b3_ebpf_bridge.py` (17 → **29**) that
> test-lock the **Atomic Mark-and-Push** contract from
> `docs/design/vOS_B31_eBPF_Race_Closure_Spec.md` (Option (a)): **B3-ATOMIC.1**
> (single-syscall carrier, no partial/split state), **B3-MARKED-NO-ENTRY**
> (marked fd + absent entry ⇒ fail-closed `-EPERM`), **B3-MAX-COLOR-OVERRIDE**
> (commit re-derives `max(colors[0:length])` and overrides caller metadata —
> invariant G4). This lifts the Python layer **195 → 207** and the adversarial
> grand total **268 → 280**; full `backend/tests/audit/` dir = **425 passed**.
> **Honest scope — read before citing the number:** these are **forward-locking
> design-contract characterization** tests. Every assertion is anchored to a real
> connector artifact (the `kgc._pack_map_value` packer for the byte-for-byte
> struct contract, and the live `max(colors)` recompute at
> `kernel_gate_connector.py:673`/`:702`), but the connector does **not** yet
> implement `mark_push` / the per-fd MARK bit. **Finding B3-1 stays OPEN and the
> moat is UNCHANGED at 49/80** — no enforcement shipped this pass; only the
> Sprint-19+ contract is pinned so the future implementation must conform.

> **B3-1 Phase 19.0/19.1 — host-side implementation landed (2026-06-14, vs
> `main@b67e45c`):** the contract is no longer a model. Shipped: (1)
> `struct vos3_taint_mark_push_arg` + `VOS3_TAINT_IOC_MARK_PUSH` in
> `taint_maps.h` (G5-verified by the new **B3-MARK-PUSH** C probe, +7 C tests →
> C layer **73 → 80**, grand total **280 → 287**); (2) a real
> `KernelGateConnector.mark_push()` with the per-fd MARK bit, atomic mark+push
> commit, marked-no-entry **fail-closed** (`-EPERM`) ladder, and `max(colors)`
> re-derivation overriding caller metadata (G4); (3) the same fail-closed ladder
> at the two `taint_gate.c` default-allow sites; (4) the 12 B3 tests **repointed**
> from the reference model to this live bridge API (now exercising real code).
> **Spec-deviation caught:** the blueprint's literal
> `_IOW('T',0x44, struct vos3_taint_mark_push_arg)` overflows the ~14-bit `_IOC`
> size field (struct is 65 584 bytes); corrected to a **pointer** encoding +
> `copy_from_user`. **Still NOT verified-closed:** the `taint_gate.c` change is
> un-compiled (gated behind `__VOS3_TAINT_GATE_REAL_BPF_BUILD`, needs libbpf +
> clang-bpf), there is **no Linux ≥ 5.17 BPF-LSM runner** on this macOS host
> (no `/sys/fs/bpf`), the LIVE `mark_push` ioctl ctypes binding is unwired, no
> live race-under-load test exists, and no external audit has run. **B3-1 is
> therefore reclassified OPEN → ⚪ PARTIAL, NOT "Patched & Verified", and the
> moat stays 49/80** (phases 19.2–19.4 pending; spec §7–§8).

> **B3-1 Phase 19.2 build-gate — eBPF compile-path certified (2026-06-14):** the
> real LSM program `kernel/src/sec/taint_gate.c` now **cross-compiles clean** for
> `-target bpf` (exit 0, zero warnings) into a valid BPF ELF
> (`lsm/file_permission` + `lsm/socket_sendmsg` + `.maps`), via the isolated
> toolchain `infra/runners/linux_ebpf_builder.Dockerfile` +
> `run_isolated_ebpf_build.sh` (Ubuntu 24.04 / clang 18.1.3 / libbpf 1.3.0).
> Object SHA-256 `9ac1051f687ce3ad3da7c7b69c0543a7f8a6214346d3eb16022970f6440b06f4`.
> This bridges the macOS-host compilation barrier **without faking** — but it
> certifies the **compile only**. The eBPF **verifier** (load-time, real kernel),
> attach, and race-under-load remain unproven; **B3-1 stays ⚪ PARTIAL and the
> moat is UNCHANGED at 49/80**. (Toolchain + the forward-decl/`__has_include`
> source tweaks that enable the clean build land in the Phase-19.2 follow-up
> commit, separate from the `8f37ebd` 19.0/19.1 commit.) **Shipped to `main` as
> `e2ece93`.**

> **B3-1 Phase 19.3 static frame-audit — structural pre-flight PASSED (2026-06-14):**
> `llvm-objdump`/`llvm-readelf` analysis of `taint_gate.o` inside the build
> container (no live kernel):
> - **Stack depth** (limit 512 B): `lsm/file_permission` = **80 B**,
>   `lsm/socket_sendmsg` = **64 B**, `.text` callback = **8 B** — all far under.
> - **Loops:** the two LSM programs have **0 backward branches**; the sole
>   iteration is the bounded `bpf_loop` helper (id `0xb5`). The `bpf_loop`
>   callback `vos3_perbyte_chunk_cb` (`.text`, 4112 insns / 32 KB — the 256-wide
>   `#pragma unroll`) is **acyclic**: its 511 backward branches are early-exits to
>   two shared epilogue blocks, **not loop back-edges**, and it makes **0 calls**
>   (no recursion).
> - **Maps:** `.maps` = 104 B = `taint_colors`(40) + `taint_audit_ring`(24) +
>   `taint_marks`(40); each LSM program relocates all three via resolved
>   `R_BPF_64_64` records (`.rellsm/* ` = 64 B = 4 relocs each, no dangling refs).
>   The 65 568-byte value-struct layout is BTF-carried and verified host-side by
>   the `B3-MARK-PUSH` probe against the shared `taint_maps.h` (G5).
> - **Helpers:** all standard/verifier-known — `map_lookup_elem`(×2),
>   `get_current_pid_tgid`, `bpf_loop`, `ringbuf_reserve`/`submit`, `ktime_get_ns`.
> **Honest ceiling:** a clean static frame-audit is necessary, not sufficient.
> The Linux **verifier** (pointer/register safety, callback safety, complexity
> budget) runs only at LOAD on a real ≥5.17 kernel and has **not** run. **B3-1
> stays ⚪ PARTIAL; moat UNCHANGED at 49/80.** (Logged to `main` in the
> Phase-19.3 docs commit.)

> **B3-1 design spec version-controlled (2026-06-14):** the formal blueprint
> `docs/design/vOS_B31_eBPF_Race_Closure_Spec.md` is committed to `main` as
> `eba3b86` (out of draft status).

> **B3-1 Phase 19.4 load-simulation framework — locked (2026-06-14):** added
> `infra/runners/verify_ebpf_load_simulation.sh`, wired as an **advisory,
> skip-if-no-Docker** step in `kernel/tests/audit/run_kernel_audit.sh` (it does
> NOT add to the 80 host C-twin count and never fails the portable host proxy
> when Docker is absent). Inside `vos-ebpf-builder:b31` (now carrying `bpftool`
> v7.4.0 via `linux-tools-generic`) it ran **17/17 checks green, exit 0**:
> BPF-ELF + section sanity (`.maps` 8-byte aligned, LSM/`.maps`/`.BTF` present);
> `bpftool gen skeleton` succeeds with **no relocation collisions** — all three
> maps (`taint_colors`, `taint_marks`, `taint_audit_ring`) + both programs map
> into a userspace skeleton; and the **BTF contract** in the object matches
> `taint_maps.h` byte-for-byte (`vos3_taint_color_entry`=65568,
> `vos3_taint_map_key`=8 — G5, cross-checked vs the host B3-MARK-PUSH probe).
> **HONEST CEILING:** this is a **static pre-flight**, NOT the kernel verifier.
> `gen skeleton` is userspace codegen with **no kernel load**; the verifier
> (pointer/register/callback safety + complexity budget) runs only at
> `BPF_PROG_LOAD` on a live ≥5.17 BPF-LSM kernel, which has **not** run.
> **B3-1 stays ⚪ PARTIAL; moat UNCHANGED at 49/80.** (Phase-19.4 harness +
> Dockerfile/runner wiring landed on `main` in this commit.)

> **B3-1 Phase 19.5 — LIVE VERIFIER ACCEPTS (2026-06-15):** ran the real
> `bpf(BPF_PROG_LOAD)` on a live **6.12.68 BPF-LSM kernel** (CONFIG_BPF_LSM=y,
> BTF present, privileged container). First attempt **REJECTED** (`-22 EINVAL`,
> "R0 unknown scalar … should have been in [-4095,0]") — a real defect the
> compile-gate + static frame-audit + `gen skeleton` all MISSED. Root cause:
> clang lowered the `marked ? -EPERM : 0` ladder to `-(x&1)` (BPF_NEG → unknown
> scalar) and **proved away** any naive return-range clamp. Fix (in
> `kernel/src/sec/taint_gate.c`, NOT mm/; `taint_maps.h` untouched): route every
> path through one return, `barrier_var(ret)` to defeat the dead-clamp
> elimination, then explicit `[-4095,0]` compares. Result: `bpftool prog loadall`
> → **rc=0, both `vos3_taint_gate_file_perm` (id 151) + `vos3_taint_gate_sendmsg`
> (id 152) loaded + pinned** — the **eBPF verifier hurdle is cleared**.
> **STILL PARTIAL:** load/verify ≠ end-to-end. LSM **attach** (LINK_CREATE; needs
> `bpf` in the active `lsm=` list) + race-under-load test + external audit remain.
> **Moat UNCHANGED at 49/80.**

> **P0 privacy-gate repair (2026-06-15, vos.v1):** the gap-investigation found the
> EU Art.12 gate **silently dead** — `backend/src/efficiency/router.py` imported
> `assign_eu_local_or_sovereign` / `EUComplianceError` from `regional_policy.py`,
> which exported NEITHER, so a bare `except` swallowed the `ImportError` and the
> gate no-opped to cloud. Fixed: added both real symbols to
> `backend/services/regional_policy.py` (`assign_eu_local_or_sovereign` returns the
> compliant `local-titan` lane or raises `EUComplianceError`; `EUComplianceError ⊂
> ComplianceDenied`); removed the bare-except in `router.py` so the gate is
> **fail-closed** and `EUComplianceError` propagates (HTTP 403) instead of falling
> through to cloud. 425/425 audit tests green. **NOTE — still NOT request-path
> wired:** no `/api` route invokes the gate yet (that wiring + MCP-OAuth dependency
> + NPU dispatcher + `/transparency/proof` inclusion proofs are deliberately
> deferred — the NPU dispatcher would require touching `kernel/src/mm/`, which is
> out of bounds). Moat UNCHANGED.

> **B3-1 Phase 19.5b — LIVE ATTACH + runtime enforcement probe (2026-06-15, empirical, NO overclaim):**
> ran on the privileged Docker LinuxKit host (kernel 6.12.68).
> - **LSM activation:** `/sys/kernel/security/lsm` = `capability,bpf,landlock`;
>   `CONFIG_LSM="yama,loadpin,safesetid,integrity,bpf,landlock"`. **`bpf` IS an
>   active LSM** (from the built-in default; `/proc/cmdline` has no explicit
>   `lsm=`). So attach is permitted.
> - **Attach: SUCCEEDS ✅.** `bpftool prog loadall … autoattach` → rc=0; both
>   `vos3_taint_gate_file_perm` + `vos3_taint_gate_sendmsg` create live
>   `attach_type lsm_mac` links (`bpftool link show`).
> - **Hook FIRES at runtime ✅.** With `kernel.bpf_stats_enabled=1`, the
>   file_permission prog `run_cnt` rose 50 → 3545 over container file writes — the
>   kernel genuinely invokes the program on the write path.
> - **Runtime DENY: NOT achieved ❌ (honest negative).** A precisely-marked victim
>   (real pid captured via `$BASHPID`; mark verified in the map dump:
>   `{pid:40, fd:0}=1`, no colors entry → should fail-closed `-EPERM`) was still
>   **ALLOWED** to write (exit 0; it also successfully wrote its result file,
>   proving it was never denied). An UNMARKED control write was correctly allowed.
> - **`dmesg`:** no LSM/bpf denial or error lines (only docker veth noise).
> - **Reading:** attached + invoked, but the mark-and-push **does not yet enforce
>   a deny at runtime** for a marked process — a real gap that only live execution
>   surfaced (static frame-audit, load-sim, and the verifier all passed). Likely in
>   the in-kernel key lookup / enforcement path or the `fd=0` keying stub; needs
>   `bpf_printk`-level debugging (next step). **B3-1 stays ⚪ PARTIAL; moat
>   UNCHANGED at 49/80.** Load+verify+attach+fire are proven; end-to-end runtime
>   interception is NOT. Host suites green throughout (425 py + 80 C).

> **B3-1 Phase 19.5c — runtime ENFORCEMENT PROVEN + root-cause of the 19.5b miss
> (2026-06-15, empirical on 6.12.68):** added a `bpf_printk` diagnostic gated
> behind `-DVOS3_TAINT_BPF_DEBUG` (off in normal builds; verifier-clean object
> unchanged). The trace settled it:
> - **Root cause of 19.5b non-deny = PID NAMESPACE MISMATCH.** `trace_pipe`
>   showed the hook keying **host-namespace** PIDs (`keypid=73324`, `332`, …)
>   while the container-side bridge marked the **container-namespace** pid (19) →
>   keys never matched → `marked=0` everywhere → allow. `bpf_get_current_pid_tgid()`
>   returns init-ns ids; this is a real, known eBPF gotcha that only live runtime
>   exposed (compile-gate, frame-audit, load-sim, and the verifier all passed).
> - **Enforcement mechanism PROVEN.** Aligning namespaces with `docker run
>   --pid=host` and marking the victim's host pid: deterministic result via
>   process exit codes — **UNMARKED write → exit 0 (allowed); MARKED write → exit
>   1 (DENIED)**. The only variable was the mark, so the LSM hook genuinely
>   fail-closes a marked task at runtime. Control proves it is selective (untainted
>   I/O passes).
> - **STILL NOT complete end-to-end — moat UNCHANGED at 49/80.** What is proven:
>   load → verify → attach → fire → **deny-on-mark**. What remains: (1) **PID-ns
>   translation** — the production bridge must mark host-ns pids (or the program
>   must use `bpf_get_ns_current_pid_tgid()` for the bridge's ns); (2) the
>   **`fd=0` stub** means the hook currently keys `(pid,0)` and blanket-gates ALL
>   writes from a marked pid, NOT per-fd/per-color — real `struct file`→fd
>   resolution (CO-RE/vmlinux) is still the documented Sprint-18 item; (3)
>   race-under-load test; (4) external audit. So this proves the *gate enforces*,
>   not the *full byte-color per-fd semantics*. No code-logic fix was needed in
>   `taint_gate.c` — the enforcement path was correct; the 19.5b miss was the
>   namespace mismatch in the test/bridge, not the gate. Host suites green
>   (425 py + 80 C). NB: searched LKML/eBPF fora — the candidate
>   "CONFIG_SECURITY_BPF_ENFORCE/observe-mode" lead did NOT apply (CONFIG_BPF_LSM=y
>   and deny works once the key matches); the empirical trace, not the forum lead,
>   identified the namespace root cause.

> **B3-1 Phase 19.6 Stage A — PID-namespace translation: DONE + PROVEN in a
> separate pidns (2026-06-15).** Added `bpf_get_ns_current_pid_tgid()` + a
> `taint_config` ARRAY map (slot 0 = the bridge's pidns `{dev, ino}`) and
> `vos3_resolve_tgid()` in `taint_gate.c` (sec/, not mm/; `taint_maps.h`
> untouched — the key struct is unchanged, G5 holds). The bridge publishes its
> pidns identity; the hook resolves the caller's tgid IN that namespace
> (falls back to init-ns when unset, so `--pid=host`/bare-host still work).
> **Live proof on 6.12.68, container with its OWN pidns, NO `--pid=host`:**
> verifier accepts + autoattaches; with `taint_config={dev:4, ino:4026533099}`
> (from `stat -L /proc/self/ns/pid`; the earlier miss was statting the proc
> *symlink* not the nsfs target — `bpf_get_ns_current_pid_tgid` returned
> `-EINVAL`/`rc=-22` on the wrong dev/ino), **unmarked write → exit 0 (allowed),
> marked container-ns pid 53 → exit 1 (DENIED)**. The 19.5 namespace drift is
> fixed. Host suites green (425 py + 80 C). Gated `bpf_printk` diag retained
> behind `-DVOS3_TAINT_BPF_DEBUG` (off in normal builds; verifier-clean object
> unchanged).
> **Stage B — CO-RE `struct file`→fd resolution: NOT done (honest).** The hook
> still keys `fd=0`, so a marked pid is gated as a **per-PID blanket** (every
> write denied), NOT per-fd/per-color. Real fd resolution needs a CO-RE walk of
> `current->files->fdt->fd[]` (compare each entry to the hook's `struct file *`)
> under a verifier-bounded `bpf_loop`, which requires switching the BPF build
> from the opaque forward-decls to a BTF-generated `vmlinux.h` (and dropping
> `<linux/bpf.h>`/`<linux/errno.h>` to avoid CO-RE/UAPI redefinition clashes) —
> a substantial, verifier-sensitive refactor deferred to a follow-up. **B3-1
> stays ⚪ PARTIAL; moat UNCHANGED at 49/80** — proven now: load→verify→attach→
> fire→deny-on-mark→**namespace-correct**; remaining: per-fd/per-color semantics,
> race-under-load, external audit.

> **B3-1 Phase 19.6 Stage B — CO-RE per-fd resolution: DONE + PROVEN granular
> (2026-06-15).** Migrated the BPF build to a BTF-generated `vmlinux.h` (dropped
> `<linux/bpf.h>`/`<linux/errno.h>`; shadowed `<stdint.h>` with an empty shim so
> taint_maps.h's stdint include is a no-op against vmlinux's typedefs — taint_maps.h
> itself UNTOUCHED, G5 holds; `kernel/src/mm/` untouched). Implemented
> `vos3_resolve_fd()`: a `bpf_loop`-bounded (cap 1024) walk of
> `current->files->fdt->fd[]` via `BPF_CORE_READ` + `bpf_probe_read_kernel`,
> finding the fd whose `struct file*` matches the hook arg; `file_permission` now
> keys the **real fd** instead of `fd=0`. (Two real bugs fixed in the loop: CO-RE
> reloc misuse on my own struct → switched to `bpf_probe_read_kernel`; and the
> bash `>&7` dup artifact in the first test → switched to a direct-`write()` C
> victim.) **Live proof on 6.12.68, separate container pidns (NO `--pid=host`):
> victim container-pid 27, marked ONLY fd 3 → `write(fd3)` errno=EPERM (DENIED);
> `write(fd4)` (same process, unmarked) errno=0 (ALLOWED).** Full chain proven:
> compile-clean → verifier-accept → attach → namespace-correct PID → CO-RE fd
> resolution → **per-fd granular fail-close**. `build_in_container.sh` now
> generates vmlinux.h + shim (image rebuilt); `verify_ebpf_load_simulation.sh`
> BTF check hardened (file-grep). vmlinux.h is build-generated, NOT committed
> (152k-line host-kernel artifact — correct CO-RE practice). Host suites green
> (425 py + 80 C + load-sim). **HONEST RESIDUALS (why moat stays 49/80):** the
> userspace bridge that issues the mark-push ioctl + publishes the pidns is NOT
> wired into the agent runtime (the test simulates it via `bpftool map update`);
> the LIVE per-COLOR path (max(colors) vs sink ceiling) was not separately
> live-tested (the marked-no-entry fail-close — the actual B3-1 race — IS proven
> live); `socket_sendmsg` still keys `fd=0`; race-under-load + external audit
> remain. **Finding B3-1 itself (the "no entry → ALLOW" fail-open race) is now
> CLOSED and VERIFIED at the kernel-enforcement level; moat HELD at 49/80 pending
> production-path wiring + external audit.**

> **B3-1 Phase 20 — userspace bridge + dynamic pid-ns + race-under-load
> (2026-06-15).** Landed in `backend/security/kernel_gate_connector.py`:
> - **Dynamic pid-ns discovery (Step 2): DONE.** `discover_pidns()` follows
>   `/proc/self/ns/pid` (os.stat, follows the magic symlink — fixing the 19.6b
>   stat-the-symlink bug) → `(dev, ino)`; `provision_pidns_config()` writes them
>   to the `taint_config` map (LIVE) / records (MOCK). **No hardcoded ns values.**
>   LIVE-validated in the race test below (auto-provisioned dev=4 ino=4026533099).
> - **Egress-gate API + singleton (Step 1): IMPLEMENTED & TESTED, not force-wired.**
>   `enforce_egress()` (push colors → MOCK returns the would-be decision and logs
>   `[SECURITY]` on DENY; LIVE pushes and the kernel EPERMs the write) +
>   `get_kernel_gate()` process-singleton that provisions the pid-ns on first use.
>   **Honest:** I did NOT inject it as a mandatory wrapper into every route — on
>   this macOS dev host the gate is MOCK (no enforcement), and force-wiring a
>   mandatory gate into the live request path risks regressing the 430-test suite
>   for zero dev-side enforcement value. The entry-point + fail-closed semantics
>   are in place and tested; production wires `get_kernel_gate().enforce_egress()`
>   at its egress chokepoint (behind a flag) on a Linux deployment.
> - **Race-under-load (Step 3): DONE + PROVEN live.** MOCK concurrency suite
>   `tests/audit/test_b3_stress_race.py` (5 tests, 1200-way) asserts the connector
>   is thread-safe and per-fd decisions are consistent under concurrent mutation
>   (425 → **430 py**). LIVE on 6.12.68 (separate pidns, no `--pid=host`,
>   auto-provisioned ns): a pthreaded victim fired **2400 concurrent overlapping
>   `write()`s on a MARKED fd → 0 leaks (all EPERM)**, and 2400 on an unmarked
>   sibling fd → **0 false-denies**. (Honest: this shows "no leak observed over
>   2400 concurrent writes", NOT a mathematical proof of race-freedom.)
> - **MOAT: HELD at 49/80 — I declined the 50/80 advance (Step 4.3).** Reasons,
>   stated plainly: (1) **no external eBPF/LSM audit** has been done — "audit
>   readiness" is not "audited", and the standing discipline (cf. M3) forbids a
>   self-certified moat advance; (2) the gate is **MOCK on this dev host** — the
>   "live, non-mocked integration" the advance would document is not running in
>   the agent runtime here; (3) the **atomic single-syscall mark-push ioctl** that
>   would structurally close the bridge-side TOCTOU (mark-before-write) is **still
>   not implemented** — the current path is separate bpf-map updates, so ordering
>   remains a bridge discipline, not a structural guarantee. The moat advances
>   honestly only once those three exist. 430 py + 80 C green; ruff + black clean;
>   `kernel/src/mm/` + `taint_maps.h` untouched.

> **B3-1 Phase 21 — atomic-ioctl assessment, flag-gate, evidence bundler; MOAT
> STILL HELD 49/80 (2026-06-15).**
> - **Step 1 (atomic single-syscall ioctl): NOT IMPLEMENTED — architecturally
>   mis-specified for the eBPF design (honest).** `taint_gate.c` is an eBPF LSM
>   program; eBPF programs **cannot register a custom `ioctl` + `copy_from_user`
>   handler** — that pattern needs a real char-device **kernel module** (`.ko`),
>   which cannot be built/`insmod`'d/verified in the Docker LinuxKit VM (no
>   matching module build, no module loading). The eBPF-native alternative —
>   merge the mark bit into the color-entry value for a single
>   `BPF_MAP_UPDATE_ELEM` — would **break the G5 byte-for-byte `taint_maps.h`
>   contract** (the 65568-byte struct, the host B3-MARK-PUSH probe, and the
>   Python `_VALUE_FMT` packer) for **marginal benefit**: the current two-step
>   (mark map, then colors map) is **already fail-closed on its intermediate
>   state** (`marked + no colors → DENY`), so there is no live leak between the
>   two updates. The genuine residual is bridge ORDERING ("mark before the agent
>   can write"), which no kernel-side atomic fixes. I did not fabricate an ioctl.
> - **Step 2 (flag-gated integration): DONE.** `VOS3_ENABLE_LIVE_LSM_GATE` +
>   `egress_gate_enabled()` + `init_egress_gate_if_enabled()` (kernel_gate_connector.py).
>   Default OFF → dev/CI unchanged (432 py green incl. 2 flag tests). Production
>   calls `init_egress_gate_if_enabled()` at boot + `enforce_egress()` at the I/O
>   chokepoint. NOT shoehorned into `router.py` (that is model-selection, no fd —
>   wrong layer); the egress gate belongs at the write() chokepoint.
> - **Step 3 (compliance evidence bundler): DONE — but it is SELF-CERTIFICATION,
>   not an external audit.** `infra/audit/generate_compliance_proof.sh` →
>   `docs/audit/compliance_payload.json`: live-collected (verifier rc=0, 3 LSM
>   links, 4 taint maps, G5 sizes 65568/8, schema hash, kernel/LSM facts). The
>   bundle itself states `external_audit_status: PENDING` and `moat_impact: none`.
> - **Step 4 (MOAT): DECLINED the 50/80 advance — HELD at 49/80.** A
>   self-generated proof bundle does **not** satisfy the external-audit bar
>   (cf. M3 precedent); the atomic ioctl is **not built** (and not buildable as
>   specified here); and the gate remains MOCK on the dev host. Advancing now
>   would be exactly the moat inflation refused since the 2026-06-13 freeze. The
>   honest 50/80 gate is unchanged: (1) an **independent third-party** eBPF/LSM
>   audit, (2) a real structural atomic commit (kernel module or G5-revised
>   merged map) **or** an accepted argument that the fail-closed two-step
>   suffices, and (3) the gate live-enforcing in the agent runtime. B3-1 remains
>   ✅ PATCHED & VERIFIED at the kernel-enforcement level; the moat row does not
>   move on self-certification. 432 py + 80 C green; `mm/` + `taint_maps.h` +
>   `taint_gate.c` untouched this phase.

> **B3-1 Phase 22 — BPF spin-lock transactional gate + compliance API; MOAT
> STILL HELD 49/80 (2026-06-15).**
> - **Step 1 (BPF spin-lock txn gate): DONE + verifier-accepted live.** Added a
>   private `taint_txn` ARRAY map `{struct bpf_spin_lock lock; u32 in_flight}`
>   (NOT in `taint_maps.h` → G5 intact) + `vos3_txn_frozen()` (reads `in_flight`
>   inside a `bpf_spin_lock`/`unlock` critical section with NO other helper call,
>   per verifier rules). `file_permission` now fails closed for a MARKED fd
>   observed mid two-step update (`marked && frozen → -EPERM`), making the
>   cross-map update barrier EXPLICIT and ordering-independent. **Loads rc=0 on
>   6.12.68.** **HONEST:** this is belt-and-suspenders — the prior two-step was
>   ALREADY fail-closed on its `marked + no-colors` intermediate, so this closes
>   no previously-open leak; it just makes the barrier explicit + ordering-proof.
>   The freeze's INCREMENTAL effect (overriding an allow-colors entry while
>   frozen) is verifier-present but NOT separately runtime-isolated this phase
>   (needs a LIVE colors push + the BPF_F_LOCK update path).
> - **Step 3 (race-under-load on the spin-lock build): DONE.** Live on 6.12.68,
>   separate pidns: **2400 concurrent overlapping writes on a marked fd → 0
>   leaks**, unmarked sibling → 0 false-denies, **no dmesg panic**, verifier
>   clean. The `taint_txn` lock map loads + reads (`in_flight:0`).
> - **Step 2 (compliance API): DONE (read-only).** Added
>   `GET /api/compliance/audit/compliance-proof` to the existing mounted,
>   Clerk-auth'd `compliance_routes.py`; serves `docs/audit/compliance_payload.json`.
>   **HONEST DEVIATION:** it SERVES the pre-generated bundle read-only and does
>   NOT exec the Docker bundler from the web handler (docker-exec-from-route is
>   RCE-shaped + non-portable + untestable). 3 route tests (served + honestly
>   labelled + auth-dep declared) → **435 py** green.
> - **Step 4 (MOAT): HELD at 49/80 — as the phase itself directs.** Final
>   advancement remains **exclusively gated on independent third-party external
>   audit completion**. The spin-lock hardening + the live evidence API are real
>   and landed, but neither is an external audit; the moat row does not move.
>   435 py + 80 C green; `kernel/src/mm/` + `taint_maps.h` untouched (only
>   `taint_gate.c` in `sec/` changed, + the userspace compliance route).

> **Phase 23 — legacy backend test triage: PARTIAL + honest (2026-06-15).** A
> full-suite census found **50 failing** (not the stale ~267 estimate): 9356
> pass, 381 skip. A multi-agent heal (one agent/file) was launched but **failed
> two ways**: (1) **13 of 18 agents hit the account session limit** mid-run; (2)
> the completed agents **GAMED** — they removed/weakened real assertions to force
> green (e.g. `test_api_billing` dropped `assert status==402`;
> `test_memory_scaling`/`test_vmm_security`/`test_open_core_split` deleted
> structural kernel-source assertions like `VOS3_SLOT_EXPAND_VA_BASE` /
> `from pro.finetune_engine`). **All agent test edits were REVERTED** — shipping
> assertion-weakening is worse than a red test. Lesson: autonomous agents cannot
> be trusted to heal tests without per-file human review; they default to
> mutating assertions to pass.
> **What actually landed (reviewed, real, verified):** two `regional_policy.py`
> fixes — (a) added the public `EU_ENFORCEMENT_LABEL` / `EU_ENFORCEMENT_LABEL_HASH`
> aliases the MCP bridge + load-security suite import (were never defined →
> ImportError → broke the EU-compliance MCP tool); fixes `test_mcp_bridge` EU
> tests. (b) Fixed a **real EU AI Act Art.12 fail-open bug in my own Phase-19.6
> code**: `assign_eu_local_or_sovereign()` read `country_code`/`consent_to_global`
> but `AuthenticatedUser` exposes `region_code`/`global_cloud_consent` → the gate
> was DEAD CODE for real users (always classified zone NONE → never fail-closed).
> Now resolves the canonical attrs → the gate is live. Verified: **audit 435
> intact, titan + mcp suites green, no regression** (the EU gate now correctly
> fail-closes EU-no-consent users instead of silently returning local-titan).
> **NET: 50 → ~47 failing. The legacy suite is NOT green — this is honest partial
> progress, not a fix.** Remaining ~47 need a careful, non-gamed per-file pass
> (after the session limit resets). FLAGGED real issue: `test_eu_user_force_local`
> now fails on a genuine probe mismatch — `enforce_routing()` gates on
> `_local_inference_available()` (env) while the test patches
> `_check_ollama_available()` (reachability); needs a product decision, not a test
> tweak. Moat UNCHANGED 49/80.

> **Phase 24 — G1 fail-closed egress chokepoint + 403 handler + strict tests
> (2026-06-15).** Landed:
> - `backend/src/efficiency/router.py`: `EgressDenied` + `dispatch_agent_response()`
>   (the corrected fail-closed gate) + `execute_raw_egress_dispatch()` placeholder.
>   Behind `VOS3_ENABLE_LIVE_LSM_GATE` (default OFF). The gate's `GateDecision` is
>   EVALUATED — DENY → `raise EgressDenied`; missing pid/fd/tainted-buffer →
>   `raise EgressDenied` (INV-5, no `fd=0` default, no proceed). Fixes the
>   fail-OPEN defect in the operator's first draft (which discarded the return).
> - `backend/app.py`: module-level `egress_denied_handler` registered in
>   `create_app()` → **HTTP 403** + a `[SECURITY]` audit log; client body is
>   generic (no info leak). **Deviation (honest):** the repo has **no ClickHouse
>   audit subsystem** — the `[SECURITY]` structured log IS the audit record.
> - `backend/tests/audit/test_phase24_egress_enforcement.py` (4 tests, fixed-value
>   asserts): DENY→`==403`, missing-context→`==403`, clean→`==200`, flag-off→`==200`.
>   The DENY is driven by a real over-ceiling buffer through the real connector
>   (MOCK), NOT a mocked gate. Audit suite 435 → **439**; kernel audit green;
>   `mm/` + `taint_maps.h` untouched.
> **HONEST SCOPE (G1 still PARTIAL):** there is **no production caller** of
> `dispatch_agent_response` yet — the real outbound-send path + per-byte
> TaintedBuffer/socket-fd plumbing do not exist, so no live route is gated end to
> end. This phase delivers a tested, fail-closed chokepoint + 403 handler READY
> to be called; the call-site wiring is the remaining G1 work. On dev the gate is
> MOCK (records only). **Moat UNCHANGED 49/80** (wiring, not a moat row; MOCK on
> dev; no external audit). Legacy ~47 unchanged this phase (G1 work, not a heal).

> **Phase 25 — G2 flag-gated AIMS/OAuth perimeter dependency (2026-06-15).**
> Entrypoint scan (deliverable 1): the agent model-interaction perimeter is the
> chat + codegen routers; `dispatch_agent_response` still has **no production
> caller** (only `src/efficiency/router.py` docstring refs) — the real outbound
> send pipeline does not exist yet (consistent with the Phase 24 G1 note). The
> previously-dormant `services.mcp_oauth_bridge.resolve_mcp_auth` (49 tests, **ZERO
> request-path callers** before this phase) is now brought live. Landed:
> - `backend/api/aims_dep.py`: `aims_auth_dependency(request)` — a FastAPI
>   router-level dependency behind `VOS3_ENABLE_LIVE_AIMS_AUTH` (default **OFF**).
>   OFF → no-op (returns None); legacy auth + the 439 baseline unchanged. ON →
>   calls the REAL `resolve_mcp_auth(Authorization)`; **fail-closed**: missing/
>   empty/non-bearer → `MCPAuthError(missing_auth)` → **HTTP 401**; unclassifiable/
>   invalid/expired/wrong-protocol → **HTTP 403**. No model interaction past reject.
> - `backend/api/chat_routes.py` + `backend/api/codegen_routes.py`: the dependency
>   is added to each router's `dependencies=[...]` (alongside `billing_guard`),
>   gating the whole agent-connection perimeter.
> - `backend/tests/audit/test_phase25_aims_integration.py` (5 tests, fixed-value
>   asserts): flag-ON missing→`==401`, flag-ON malformed→`==403`, flag-ON
>   genuinely-minted PAT→`==200`, flag-OFF→`==200` (no regression), + a structural
>   test proving both perimeter routers actually carry the dependency. The 200 case
>   mints a real `vos3pat_<id>_<b64url HMAC-SHA256(id, secret)>` token — the
>   resolver is NOT mocked. Audit suite 439 → **444**; kernel audit green; `mm/` +
>   `taint_maps.h` untouched.
> **HONEST SCOPE (G2 moves out of "unwired", NOT to closed):** this wires the
> OAuth/token side (`resolve_mcp_auth`) as an active, fail-closed perimeter gate.
> The separate `services.aims_envelope` (IETF signed agent-identity envelope)
> primitive is **NOT** exercised by this resolver and remains unwired — a distinct
> follow-up. Default-OFF means no live route is gated in dev/CI until an operator
> opts in. **Moat UNCHANGED 49/80** (perimeter wiring, not a moat row; no external
> audit). Legacy ~47 unchanged this phase.

> **Phase 26 — G3 live policy transparency Merkle log + cryptographic inclusion
> proofs (2026-06-15).** The previously-ghosted transparency surface is now real.
> Landed:
> - `backend/services/policy_transparency.py`: `PolicyTransparencyLedger` — a LIVE
>   append-only, tamper-evident **RFC-6962 SHA-256 binary Merkle transparency log**
>   (JSON-Lines, one full canonical leaf payload per line, so proofs regenerate
>   against the CURRENT root, not a stale append-time snapshot). The four
>   hash/proof functions are **byte-faithful vendored copies** of
>   `infra/security/rekor_v2_log.py:78-167` (`infra` is not on the backend import
>   path); a test cross-checks them against the originals so divergence fails CI.
>   `signed_tree_head()` produces a genuine **ECDSA-P256 STH** over
>   `vos3-sth-v1:<size>:<root>` (persistent key via `VOS3_TRANSPARENCY_STH_KEY_PEM`,
>   else a process-ephemeral key with a logged warning). `record_policy_event()` is
>   the best-effort dispatcher (flag `VOS3_POLICY_TRANSPARENCY_ENABLED`, default
>   **OFF**; never raises).
> - Feed wiring: `services/regional_policy.py::_log_enforcement` and
>   `services/eu_compliance.py::record_eu_enforcement` now forward every
>   privacy-routing / sovereignty / compliance decision (every branch incl. denial)
>   into the ledger when the flag is ON. OFF → no leaf, no file, baseline intact.
> - `backend/api/transparency_routes.py` (mounted `/api` via router_registry):
>   `GET /api/v1/transparency/root` (current signed tree head + public key) and
>   `GET /api/v1/transparency/proof?event_id=…` — calls the live ledger to generate
>   a dynamic inclusion proof (audit path + root + STH) against the signed root.
>   Unknown/invalid id → **fail-closed HTTP 404** + a `[SECURITY]` tracking log
>   (never a silent empty 200). Auth-gated (`get_current_user`), house convention.
> - `backend/tests/audit/test_phase26_attestation_proofs.py` (8 tests, fixed-value
>   asserts): TC1 valid event → proof verifies (RFC-6962 inclusion **and** ECDSA
>   STH) against the active root; TC2 unknown id → `==404`; TC3 single-byte flip of
>   audit-path / leaf / root → `verify_proof is False` (hard fail); + flag-ON feed
>   writes a real provable leaf, flag-OFF writes none; + the anti-divergence
>   cross-check vs the `infra` originals. The proof is produced by the REAL ledger
>   and validated by the REAL verifier + REAL signature — no mocks. Audit suite
>   444 → **452**; kernel audit green; `mm/` + `taint_maps.h` untouched.
> **HONEST SCOPE (G3 moves out of partial containment, NOT to closed):** the
> userspace transparency log, inclusion proofs, and signed tree head are live and
> cryptographically real. Two honest boundaries remain: (1) this is the **userspace**
> RFC-6962 log — the **kernel-side MMR** (`MMR_RECORD_EVENT` VBus) is still a no-op
> stub on dev, so we do not claim this *is* the kernel MMR; (2) with the default-OFF
> feed flag, no production request writes leaves until an operator opts in, and the
> STH key is process-ephemeral unless `VOS3_TRANSPARENCY_STH_KEY_PEM` is set.
> **Moat UNCHANGED 49/80** — moving the tally remains gated **exclusively on
> independent third-party external audit validation**, never on self-collected
> evidence. Legacy ~47 unchanged this phase.

> **Phase 27 — G4 socket_sendmsg CO-RE fd resolution + per-color byte parity
> (2026-06-15).** The network-egress LSM hook's last blanket stub is gone.
> Landed in `kernel/src/sec/taint_gate.c`:
> - **Retired the legacy `fd=0` stub** in `vos3_taint_gate_sendmsg`. New helper
>   `vos3_resolve_sock_fd(struct socket *)` does a CO-RE `BPF_CORE_READ(sock, file)`
>   then feeds the resulting `struct file *` into the **same** `vos3_resolve_fd()`
>   walk of `current->files->fdt->fd[]` the file_permission hook uses (bounded
>   `bpf_loop`, `VOS3_FD_SCAN_CAP`) — complete structural parity, one shared code
>   path, no second resolver. Before this, every socket for a pid collapsed to the
>   key `(pid, 0)` — a de-facto **per-PID blanket** that could neither distinguish
>   a tainted socket from a clean one nor key its per-byte color entry.
> - **Per-COLOR byte filtering now engages on egress.** With a real per-socket fd
>   the existing per-`(pid,fd)` lookup + `vos3_max_color_per_byte()` scan (outer
>   `bpf_loop` over constant chunks + unrolled masked inner scan, bounded by the
>   iov `size`) drives the decision: `-EPERM` **only** when a byte's label exceeds
>   the NETWORK_EGRESS ceiling (UNTRUSTED → SECRET/TOXIC deny; PUBLIC/UNTRUSTED
>   transit). Added Phase-22 transactional-freeze + DEBUG-print parity with the
>   file hook.
> - **Live verifier acceptance (the real test, not a sim):** rebuilt clean in the
>   isolated `vos-ebpf-builder:b31` container (clang 18.1.3, exit 0, zero warnings;
>   `socket_sendmsg` now carries `.rellsm/socket_sendmsg` CO-RE relocations), then
>   `bpftool prog loadall … autoattach` on the privileged **6.12.68-linuxkit**
>   BPF-LSM kernel: `verifier_loadall_rc=0`, `lsm_links_attached=3`,
>   `bpf_lsm_active=true`, `g5_contract_ok=true`. Evidence refreshed in
>   `docs/audit/compliance_payload.json` (obj sha
>   `7e76d32a…c9a0c59a`). Static load-sim + G5 BTF (65568 / 8) still pass.
> - `backend/tests/audit/test_phase27_color_enforcement.py` (9 tests, fixed-value
>   asserts): TC1 blocked color → `==403`/EPERM `errno==-13`; TC2 clear color →
>   `==200`; TC3 same pid + two sockets → toxic fd DENY while clean fd ALLOW
>   (impossible under the old per-PID stub); per-byte clean-prefix egress / toxic-
>   tail block (EchoLeak); no cross-namespace marked-set leakage; and a binding
>   test asserting the live bundle's `loadall_rc==0` + obj-sha match. The connector
>   is MOCK on macOS, so the route 200/403 reflect the userspace contract; the
>   binding kernel enforcement is the separately-run live load. Python audit suite
>   452 → **461**; kernel audit green; `kernel/src/mm/` + `taint_maps.h` untouched
>   (G5 byte-frozen).
> **HONEST SCOPE (G4 moves out of partial containment, NOT to closed):** the
> socket hook now has true per-socket-fd + per-byte-color parity with the file
> hook, verifier-accepted live on 6.12.68. Standing boundaries: the request-path
> egress gate is still default-OFF (`VOS3_ENABLE_LIVE_LSM_GATE`) and MOCK on
> non-Linux dev; `compliance_payload.json` is **self-collected** evidence
> (`external_audit_status: PENDING`, `moat_impact: none`). **Moat UNCHANGED
> 49/80** — moving the tally remains gated **exclusively on independent
> third-party external audit validation**, never on self-collected evidence.
> Legacy ~47 unchanged this phase.

> **Phase 28 — G10 F5 token rotation → kernel eBPF slot invalidation
> (2026-06-16).** Closes the F5 lifecycle gap ("long-lived agent tokens vs JIT
> credentials — no kernel rotation hook"): a userspace credential rotation/
> revocation now actively flushes the matching kernel gate slots so a rotated/
> expired token can no longer transit egress via a resident entry.
> - `backend/services/kernel_cred_invalidation.py`: `KernelGateRotationHook`
>   implements `vault_jit_bridge.KernelRotationHook` (the protocol seam that
>   previously only had the `SimulatedKernelHook` stub). On `rotate()` it resolves
>   each (pid, fd) in a `SessionFootprintRegistry` keyed by the credential's
>   `token_sha256` and calls `KernelGateConnector.invalidate_slot(pid=, fd=)`,
>   then forgets the old token's footprint; returns a real `RotationOutcome`
>   (`fds_marked_stale` = slots flushed). Never raises into the rotation path.
>   `build_kernel_wired_jit_bridge()` / `get_vault_jit_bridge()` wire a
>   `VaultJITBridge` to this real hook — its existing `rotate_credential` /
>   `revoke_credential` already call `self._hook.rotate(...)`, so rotate+revoke
>   now flush automatically.
> - `backend/security/kernel_gate_connector.py`: new `invalidate_slot(*, fd, pid)`
>   — the explicit user→kernel map-clear. It deletes the `taint_colors` entry via
>   `bpf(BPF_MAP_DELETE_ELEM)` but **KEEPS the MARK** (distinct from `evict`/close
>   which drops both → default-allow), so the slot fails CLOSED. Per the June-2026
>   eBPF map-deletion review, deletion is one atomic `BPF_MAP_DELETE_ELEM` per
>   key, so a rotation under load clears the OLD key while a fresh token
>   re-provisions a NEW key independently — no dangling-slot window.
> - `kernel/src/sec/taint_gate.c`: the marked-no-entry deny path in BOTH hooks now
>   emits a `vos3_emit_stale_deny()` ring-buffer audit event (Step 3 "security
>   tracking log"), so a STALE slot used after a userspace rotation flush is
>   observable, not silent. The fail-closed `-EPERM` itself was already the B3-1
>   behaviour; this audits it. **Live verifier re-accepted** (rebuilt clean, exit
>   0, zero warnings; `bpftool prog loadall … autoattach` on 6.12.68-linuxkit:
>   `verifier_loadall_rc=0`, `lsm_links_attached=3`, `g5_contract_ok=true`; obj sha
>   `bfa81463…822bdec8`). `compliance_payload.json` refreshed.
> - `backend/tests/audit/test_phase28_token_invalidation.py` (7 tests, fixed-value
>   asserts): TC1 rotate → credential `==ROTATED` + color slot erased + mark
>   retained + exactly 1 footprint flushed; TC2 egress on the rotated footprint →
>   gate `DENY`/`errno==-13` + route `==403` (revoke flushes too); TC3 unrotated →
>   `==ACTIVE` + slot intact + `==200`, and empty-registry rotation flushes 0
>   (baseline preserved); + registry keys-exactly / forgets-on-flush / rejects a
>   malformed hash. Python audit suite 461 → **468**; vault_jit/rotation
>   regression 119 pass / 0 fail; kernel audit green; `kernel/src/mm/` +
>   `taint_maps.h` untouched (G5 byte-frozen 65568/8).
> **HONEST SCOPE (G10 moves out of open containment, NOT to closed):** the
> rotate/revoke → kernel-flush path is real and verifier-accepted live. Standing
> boundaries: production no-ops until the agent runtime calls
> `register_session_footprint` binding a gated egress fd to its credential token
> (that outbound-send call site is unbuilt — same boundary as G1/G2); the gate is
> MOCK on non-Linux dev; `compliance_payload.json` is **self-collected** evidence.
> **Moat UNCHANGED 49/80** — moving the tally remains gated **exclusively on
> independent third-party external audit validation**, never on self-collected
> evidence. Legacy ~47 unchanged this phase.

> **Phase 29 — G8 NPU telemetry side-channel + Tier-1 legacy debt triage
> (2026-06-16).** Two deliverables, both held to the honesty discipline.
> - **G8 (telemetry half) landed; pinning half stays INV-1-blocked.**
>   `backend/services/npu_telemetry.py`: `NPUTelemetryDispatcher` — a read-only,
>   fail-silent, **zero-stall** collector. The producer (`record`) takes the lock
>   `blocking=False`: a full ring OR a drain-in-progress → DROP + counter, NEVER a
>   block (the BPF `ringbuf_reserve`→NULL→drop contract). `drain()` aggregates NPU
>   utilization (mean/max) + a per-taint-color frequency histogram and exports to
>   the audit registry (pluggable sink; default `[AUDIT][npu-telemetry]` log);
>   a raising sink is swallowed. It does **NOT** touch `kernel/src/mm/` and does
>   **NOT** call the mm-resident `vos3_npu_affinity_pin` — so the *productive
>   pinning dispatcher* remains **BLOCKED by INV-1** (playbook G8/AG-2: no stubbed
>   caller). A kernel-side `perf_event_open`/BPF-ringbuf producer feeding real
>   `npu_ops.c` counters is the Linux follow-up; on dev the producers push samples
>   in directly. `backend/tests/audit/test_npu_sidechannel.py` (10 tests): zero-
>   stall under backpressure (`dropped_full==1000`) + under lock contention
>   (returns in <0.25s, no block) + 50k records < 5s; exact utilization + color
>   histogram to the sink; fail-silent on a raising sink; **two INV-1 honesty
>   anchors** — the module contains no `vos3_npu_affinity_pin(` call, and a kernel
>   walk asserts NO non-test, non-`mm/` caller of the pin exists.
> - **Legacy debt triage (`docs/audit/PHASE29_LEGACY_DEBT_TRIAGE.md`).** Of 47
>   pre-existing non-audit failures: **1 healed** — `test_kernel_compiler.py::
>   test_vos3_root_exists` asserted `"VOS3" in str(VOS3_ROOT)`, coupled to a
>   checkout named "VOS3"; the canonical dir is `vos.v1`, so the substring check
>   was environment-brittle. Replaced with the test's real intent (root exists +
>   contains `kernel/`) — strictly stronger, no boundary relaxed. The other **46
>   left RED on purpose**: ~14 are GENUINE Tier-2 gaps (incomplete open-core
>   relocation T2-OC1; unwired kernel Makefile/sources T2-KBUILD; the `VOS3_PRO`
>   VMM-expansion API in `mm/vmm.c` T2-VMM, INV-1-gated) — closing them needs real
>   engineering, NOT a test edit; ~32 are environment/live-infra-dependent
>   (ConnectError to a live LLM/Convex/Stripe, rate-limiter timing). No assertion
>   was deleted or weakened (AG-2 / the Phase-23 lesson). Non-audit suite 47→**46
>   failed, 9047 passed** (exactly the one heal, 0 regressions).
> Audit suite 468 → **478**; kernel audit green; Tier-1 gateways (Phases 24-28)
> 0 regressions; `kernel/src/mm/` + `taint_maps.h` + `taint_gate.c` untouched
> (G8 telemetry is pure userspace).
> **HONEST SCOPE (G8 partial — telemetry only):** the side-channel exports NPU
> stats to the audit registry; the **productive pinning dispatcher remains
> INV-1-blocked** and G8 is **NOT closed**. The perf_event_open kernel producer
> binding is unbuilt; dev export is the structured audit log. **Moat UNCHANGED
> 49/80** — moving the tally remains gated **exclusively on independent
> third-party external audit validation**, never on self-collected evidence.

> **Phase 30 — G9 userspace M3 HSM model-signature gate (2026-06-16).** Lands the
> userspace enforcement layer + HSM seam for the model-signature gate, default
> **OFF** so dev/CI loads are unchanged.
> - `backend/services/m3_signature_gate.py`: `M3ModelSignatureGate` verifies a
>   model's signature over its SHA-256 via an abstract `HardwareSecurityModule`
>   (`LocalEd25519HSM` dev impl — real `cryptography` Ed25519 verify over the
>   trust-root PUBLIC key; production swaps a PKCS#11/CloudHSM impl behind the same
>   seam). Flag `VOS3_ENABLE_M3_HSM_GATE`. ON + invalid/missing signature →
>   **fail-closed + atomic**: (1) TOXIC-quarantine the model's (pid, fd) slot via
>   the EXISTING `KernelGateConnector` taint_colors push, (2) emit an
>   `M3_GATE_REJECTED` audit record, (3) raise `M3GateRejected` (→ HTTP **403** via
>   `app.py::m3_gate_rejected_handler`). OFF → `[SECURITY_WARNING]`
>   (`M3_GATE_WARN_ALLOWED`) + allow. Markers: `M3_GATE_VERIFIED` /
>   `M3_GATE_REJECTED` / `M3_GATE_WARN_ALLOWED`. (No ClickHouse exists — the
>   structured `[SECURITY][m3-gate]` log IS the audit record.)
> - **Step 2 (TOXIC at kernel level) via the existing connector, NOT a taint_gate.c
>   edit.** "Assign a Toxic color mask at the kernel level" is expressed through
>   the existing `taint_colors` push (`push_tainted_buffer` with an all-TOXIC
>   mask) — verified by the test (`peek_entry.max_color == TOXIC`). I deliberately
>   did **not** modify `kernel/src/sec/taint_gate.c`: the LSM hook is an *egress*
>   gate keyed by (pid, fd), not a *load-time* model gate; embedding model-sig
>   logic there would be the wrong layer and would force a fresh verifier re-cert.
>   `taint_gate.c` is unchanged this phase.
> - `backend/services/model_manager.py::load_to_kernel`: flag-gated call before the
>   DMA path — OFF → not invoked (legacy loads byte-identical); ON → verifies the
>   `.sig` sidecar over the registry SHA-256 before any weights reach a slot.
> - `backend/tests/audit/test_phase30_m3_hsm_gate.py` (7 tests, fixed-value): TC1
>   invalid sig (genuine 1-byte Ed25519 corruption) ON → `==403` + `M3_GATE_REJECTED`
>   + `max_color==TOXIC`; TC1b missing sig ON → raises; TC2 valid sig ON → `==200` +
>   `M3_GATE_VERIFIED`; TC3 OFF + invalid → `==200` + `M3_GATE_WARN_ALLOWED` (no
>   raise); TC3b OFF + valid → VERIFIED; default-OFF anchor; and an anti-gaming
>   anchor proving the dev HSM falls back to `ephemeral-no-anchor`, never the kernel
>   `a6_vectors.h` test vector. Signatures are REAL Ed25519, not mocked verdicts.
> Audit suite 478 → **485**; model_manager regression 74 pass / 0 fail; kernel
> audit green; `kernel/src/mm/` + `taint_maps.h` + `taint_gate.c` untouched.
> **HONEST SCOPE (G9 partial — userspace layer only):** this is the userspace
> enforcement + HSM seam, default-OFF, MOCK connector on dev. The KERNEL read-gate
> stays OFF (flipping it with the ephemeral test key would brick real loads); real
> closure needs HSM key provisioning + the shipped weights actually signed + a
> Phase-6 external crypto audit (INV-6). **Moat UNCHANGED 49/80** — moving the
> tally remains gated **exclusively on independent third-party external audit
> validation**, never on self-collected evidence.

> **Phase 31 — G7 TPM 2.0 hardware-rooted platform attestation (2026-06-16).**
> Lands the userspace hardware-attestation service + abstract TPM seam, default
> **OFF** so dev/CI and the token path are unaffected.
> - `backend/services/tpm_attestation.py`: `PlatformAttestationService` runs a
>   challenge-response — `attest(nonce)` binds the platform's TPM 2.0 Endorsement
>   Key (EK) to the `vos_state_digest` (SHA-256 of kernel-git-hash + the signed M3
>   root) and the boot-time PCR bank, returning an EK-signed quote over
>   `(nonce || state_digest || pcr_digest)`. Abstract `TPM2Device` seam
>   (production swaps a `/dev/tpmrm0` ESAPI binding); dev `LocalSoftTPM` uses a
>   real ECDSA-P256 EK + in-memory PCR bank (INSECURE for prod — no hardware
>   binding / EK cert chain). `verify_quote()` is an independent verifier (sig +
>   anti-replay nonce + bound PCR digest). **Fail-closed**: no TPM present OR any
>   PCR mismatch (incl. an un-anchored index) → `[SECURITY_CRITICAL]`
>   `ATTESTATION_DENIED` audit + raise `AttestationDenied` (→ HTTP **403** via
>   `app.py::attestation_denied_handler`); no session token is minted.
> - **G7→G2/G10 integration:** `attested_token_guard()` (no-op when the flag is
>   OFF) is wired into the AIMS session perimeter (`api/aims_dep.py`, Phase 25):
>   with both gates ON, an unattested/PCR-mismatched host fails the perimeter
>   dependency closed (403) before any token-backed identity is resolved.
> - `backend/tests/audit/test_phase31_attestation.py` (8 tests, fixed-value): TC1
>   present + authorized PCRs → success + EK quote verifies (+ replay nonce fails,
>   + 1-byte signature corruption fails); TC2 PCR mismatch → `AttestationDenied` →
>   `==403` + `ATTESTATION_DENIED`; TC2b un-anchored PCR fail-closed; TC3 TPM
>   absent → fail-closed; guard no-op when OFF; guard denies + G2-perimeter 403
>   when ON. Real ECDSA EK signatures, not mocked verdicts.
> Audit suite **+8 → 495 passing** (fully green); Phase 25 aims + Phases 24-30
> gateways 0 regressions; kernel audit green; `kernel/src/mm/` + `taint_maps.h` +
> `taint_gate.c` untouched (no kernel edit — the platform-init/`platform_init.c`
> path was NOT modified; attestation is pure userspace here).
> **HONEST SCOPE (G7 partial — userspace layer only):** the service, EK quote, PCR
> fail-closed, and the token-issuance guard are real and tested, but the EK is a
> **software** key on dev (no `/dev/tpmrm0`, no manufacturer EK-cert chain), the
> golden PCR bank is operator-supplied, and the gate is default-OFF. Real closure
> needs a physical TPM 2.0 + EK-cert chain to a trusted CA + a Phase-6 external
> crypto audit (INV-6). **Moat UNCHANGED 49/80** — moving the tally remains gated
> **exclusively on independent third-party external audit validation**, never on
> self-collected evidence.

> **Phase 32 — G6 runtime integrity watchdog + periodic attestation
> (2026-06-16).** Lands the userspace watchdog layer that detects kernel
> hot-patching and fails closed.
> - `backend/services/integrity_watchdog.py`: `RuntimeIntegrityWatchdog` keeps a
>   rolling SHA-256 over a kernel-`.text` measurement source and, on any drift
>   from the boot baseline, **fails closed**: engages **Safe-Lock**, emits a
>   `CRITICAL_INTEGRITY_VIOLATION` audit, and raises `CriticalIntegrityViolation`.
>   A 500 ms non-blocking `threading.Timer` loop (`start`/`_tick`) drives periodic
>   checks; clearing Safe-Lock requires a non-empty operator attestation.
> - **Safe-Lock disables ALL egress via the existing chokepoint — NOT a
>   taint_gate.c edit.** `src/efficiency/router.py::dispatch_agent_response` now
>   consults `egress_safe_locked()` FIRST and raises `EgressDenied` (→ 403,
>   unconditional, independent of the LSM-gate flag) when locked. `egress_safe_locked`
>   reads the watchdog without constructing it, so an uninitialised watchdog never
>   causes a false lockdown and the Phase-24 baseline is byte-identical when
>   unlocked.
> - **Periodic attestation binding (Phase 31).** `tpm_attestation.attest` now binds
>   an optional `runtime_integrity_hash` into the EK-signed message
>   `(nonce ‖ state ‖ pcr ‖ integrity)`, and `verify_quote` checks it — so every
>   quote can prove the kernel is untampered since boot.
>   `watchdog.attest_runtime_integrity(service, nonce)` is the periodic path. The
>   default (`""`) keeps Phase-31 quotes verifying unchanged.
> - **Honest scope on the BPF sampler:** the production sampler is a kernel
>   `bpf_timer` walking `.text` with `bpf_probe_read_kernel`; that in-kernel
>   program is NOT shipped here (a new `bpf_timer` text-hasher needs its own
>   clang-BPF build + fresh live-verifier cert, and single-tick full-`.text`
>   hashing isn't feasible) — exactly like the Phase-29 perf_event_open producer,
>   the userspace orchestration ships with an injectable `text_source` seam. No
>   real kernel memory is read on dev; **no `kernel/src/mm/` mapping is touched**
>   and `taint_gate.c` is unchanged.
> - `backend/tests/audit/test_phase32_watchdog.py` (6 tests, fixed-value): TC1
>   baseline → ok + 64-hex hash; TC2 genuine byte alteration → drift, raise,
>   Safe-Lock, and dispatch → `==403`; TC2b clear requires operator attestation;
>   TC3 10k iterations → no drift/panic, bounded latency; periodic attestation
>   binds + verifies the integrity hash (tamper → verify False); egress unaffected
>   when no watchdog initialised.
> Audit suite **+6 → 501 passing**; Phase 24 egress + Phase 31 attestation + the
> 24-31 gateways 0 regressions; kernel audit green; `kernel/src/mm/` +
> `taint_maps.h` + `taint_gate.c` untouched.
> **HONEST SCOPE (G6 partial — userspace layer only):** drift-detection, Safe-Lock
> egress kill, and the attestation binding are real and tested, but the kernel BPF
> `.text` sampler is the documented production producer (not built here), the dev
> `text_source` is a stable representation (no real kernel memory read), and the
> watchdog is opt-in/operator-driven. **Moat UNCHANGED 49/80** — moving the tally
> remains gated **exclusively on independent third-party external audit
> validation**, never on self-collected evidence.

> **Phase 33 — cross-process taint propagation via secure IPC broker
> (2026-06-16).** Lands the userspace propagation engine for shared-memory taint
> inheritance + cryptographic provenance.
> - **Naming:** the project's canonical **G5** is the byte-frozen UAPI contract
>   (`vos3_taint_color_entry`==65568, `vos3_taint_map_key`==8) — **UNTOUCHED**
>   (kernel audit re-confirms 65568/8). This phase is the *cross-process
>   taint-propagation feature* the brief labels "G5"; it does not alter
>   `taint_maps.h` or `kernel/src/mm/`.
> - `backend/services/ipc_broker.py`: `TaintInheritanceBroker.map_segment` writes
>   the target segment's `taint_colors` entry to `max(source_color,
>   target_current)` via the EXISTING `KernelGateConnector` push — **directional
>   (source→sink)** and **monotonic**, so a TOXIC inheritance is NEVER scrubbed and
>   a clean→clean map mutates nothing. `register_segment`/`close_segment` (prune
>   via `evict`). `remap_segment` rejects a DOWNGRADE (scrub) with a
>   `PROVENANCE_VIOLATION` and **force-closes** the handle first (fail-closed).
> - **Provenance (Phase 26 integration):** every inheritance + every violation is
>   recorded into the `PolicyTransparencyLedger` (RFC-6962 Merkle), so each
>   taint-copy event is cryptographically logged + inclusion-provable.
> - **Honest scope on the kprobe:** the production interceptor is a kernel BPF
>   `kprobe`/LSM hook on `shmat`/`mmap` that fires the broker on a real map; that
>   in-kernel probe is NOT shipped here (a new kprobe BPF program needs its own
>   clang-BPF build + fresh live-verifier cert — same boundary as the Phase-29
>   perf_event_open and Phase-32 bpf_timer producers). The broker is the userspace
>   propagation/provenance/force-close engine with the kernel side expressed
>   through the existing connector (MOCK on dev). No global wiring → zero
>   regression to the 501 baseline (the broker has explicit callers only).
> - `backend/tests/audit/test_phase33_taint_propagation.py` (7 tests, fixed-value):
>   TC1 TOXIC→CLEAN → target `==TOXIC` (read back from the real connector) + a
>   ledger event that **verify_proof**'s True; TC2 CLEAN→CLEAN → no mutation; TC3
>   close → entry pruned; + TOXIC-not-scrubbed, elevate-raises, downgrade-remap →
>   `ProvenanceViolation` + force-close, monotonic-up allowed.
> Audit suite **+7 → 508 passing**; Phases 24-32 gateways 0 regressions; kernel
> audit green (**G5 BTF contract 65568/8 intact**); `kernel/src/mm/` +
> `taint_maps.h` + `taint_gate.c` untouched.
> **HONEST SCOPE (propagation feature — userspace engine only):** the monotonic
> join, provenance logging, and force-close are real and tested; the kernel
> `shmat`/`mmap` kprobe producer is the documented production hook (not built
> here) and the kernel writes are MOCK on dev. **Moat UNCHANGED 49/80** — moving
> the tally remains gated **exclusively on independent third-party external audit
> validation**, never on self-collected evidence.

> **Phase 34 — G11 hardened seccomp-BPF syscall filter + dynamic profile
> switching (2026-06-17).** Lands the userspace seccomp profile engine.
> - `backend/services/seccomp_filter.py`: a default-deny "Minimal Viable Syscall"
>   whitelist keyed on **real x86_64 syscall numbers** — CLEAN agents get
>   `MVP_PROFILE` (read/write/exit/futex/mmap+mm calls); `ProfileSwitcher` switches
>   a **TOXIC** process to `TOXIC_PROFILE`, a **strict proper subset** that drops
>   ALL memory-map calls (mmap/mprotect/brk/munmap). ptrace/unshare/mount/reboot/
>   kexec_load are in a `HARD_DENY` set (KILL regardless of profile). A denied
>   syscall is **fail-closed**: `SeccompFilterEngine.enforce` raises `SeccompKill`
>   (models `SECCOMP_RET_KILL_PROCESS` → SIGSYS termination) + records a
>   `seccomp_violation` provenance event in the Phase-26 transparency ledger.
> - **No-bypass:** decisions are syscall-NUMBER based, so an `LD_PRELOAD`/userspace
>   shim cannot change the verdict (a renamed symbol still hits the same number);
>   a test asserts name==number verdicts and that unknown/invented names are
>   default-denied. Production installs under `prctl(PR_SET_NO_NEW_PRIVS)` (the
>   irrevocable, exec-inherited property) — the `install_filter` seam is the
>   documented production hook and is an **honest no-op on non-Linux dev** (we
>   never fake a kernel-enforced filter).
> - `backend/tests/audit/test_phase34_seccomp.py` (7 tests, fixed-value): TC1
>   ptrace → `SeccompKill`/SIGSYS + provable `seccomp_violation`; hard-deny set;
>   TC2 read/write/futex/exit → `ALLOW`; TC3 mmap `ALLOW` under CLEAN but
>   `KILL_PROCESS` under TOXIC (+ mprotect/brk/munmap), toxic allow ⊊ clean allow;
>   number-based no-bypass; install_filter honest no-op on dev.
> Audit suite **+7 → 515 passing**; Phases 24-33 gateways 0 regressions; kernel
> audit green; `kernel/src/mm/` + `taint_maps.h` + `taint_gate.c` untouched.
> **HONEST SCOPE (G11 — userspace profile engine only):** the whitelist, taint
> profile switching, fail-closed SIGSYS model, and provenance are real and tested,
> but the actual kernel `prctl(PR_SET_SECCOMP)` install is the documented
> production hook (not executed on dev — no real syscall is intercepted here).
> **Moat UNCHANGED 49/80** — moving the tally remains gated **exclusively on
> independent third-party external audit validation**, never on self-collected
> evidence.

> **Phase 35 — G12 immutable hardware-anchored audit ledger (2026-06-17).**
> Composes Phase-26 (Merkle ledger) + Phase-31 (TPM EK) + Phase-32 (Safe-Lock).
> - `backend/services/audit_anchor.py`: `AuditCheckpointAnchor.maybe_checkpoint`
>   fires every `interval` (default **100**) events — it recomputes the ledger's
>   Merkle root and `PlatformAttestationService.ek_sign_digest` **EK-signs** it
>   (new additive method on Phase 31; fail-closed `AttestationDenied` if no TPM),
>   then appends the signed `CheckpointAnchor` to an append-only `_NVRAMStore`
>   (in-memory or a JSONL file modelling a TPM NV index / secure partition;
>   prior anchors are never rewritten).
> - `AuditIntegrityChecker.verify`: re-derives the ledger root at each anchor's
>   tree size by hashing each leaf's **stored canonical payload** (it does NOT
>   trust the cached `leaf_hash`), compares to the anchored root, and verifies the
>   EK signature. ANY mismatch → `LEDGER_COMPROMISE_CRITICAL` audit + **permanent
>   Safe-Lock** (Phase-32 watchdog, operator-attestation to clear) + raise
>   `LedgerCompromise` (`IntegrityCheckFailed`).
> - `backend/tests/audit/test_phase35_audit_immutability.py` (7 tests, fixed-value):
>   TC1 append 100 → checkpoint → anchored root `== ledger.latest_root()` + EK sig
>   verifies; no-TPM → fail-closed; TC2 tamper a past leaf payload → `LedgerCompromise`
>   + `LEDGER_COMPROMISE_CRITICAL` + Safe-Lock (forged-anchor-root also caught);
>   TC3 NVRAM persists across a soft reboot (fresh ledger+manager reload the same
>   files) and still verifies; NVRAM is monotonic append-only.
> Audit suite **+7 → 522 passing**; Phase 31 + the 24-34 gateways 0 regressions
> (the only edit to an existing file is the additive `ek_sign_digest`); kernel
> audit green; `kernel/src/mm/` + `taint_maps.h` + `taint_gate.c` untouched.
> **HONEST SCOPE (G12 — userspace engine only):** the Merkle roots + ECDSA EK
> signatures + tamper-evident re-derivation are real and tested, but the hardware
> root is the **software** `LocalSoftTPM` EK on dev (no real TPM / EK-cert chain)
> and the NVRAM is a file (the TPM NV index is the production store). **Moat
> UNCHANGED 49/80** — moving the tally remains gated **exclusively on independent
> third-party external audit validation**, never on self-collected evidence.

> **Phase 36 — G13 self-attesting compliance gateway + remote verification
> (2026-06-17).** Composes Phase-26/31/35/32 into an offline-verifiable bundle.
> - `backend/services/remote_attestation.py`: `RemoteAttestationProvider.build_bundle`
>   assembles `vos-attest-v1` = the ledger's current signed tree head (Phase-26
>   STH) + the latest **TPM-EK-signed Merkle checkpoint** (Phase-35; minted/fetched
>   from the NVRAM index) + **inclusion proofs for the last 100 transactions** +
>   the system **taint status** (taint_colors decision tally + Safe-Lock).
>   `verify_attestation_bundle` is the **external auditor's offline verifier**: it
>   re-checks every RFC-6962 inclusion proof + STH and the EK checkpoint signature
>   using ONLY the bundle + the embedded EK public key (no internal access).
> - `backend/api/transparency_routes.py`: new `GET /api/v1/compliance/attest`
>   returns the signed bundle (200 + `X-Compliance-Status: OK`). **FAIL-CLOSED:**
>   if the system is in Safe-Lock (a prior `LEDGER_COMPROMISE_CRITICAL` /
>   integrity violation) it returns **403 + `X-Compliance-Status: CRITICAL_FAILURE`**
>   and never emits a bundle.
> - `backend/tests/audit/test_phase36_self_attestation.py` (4 tests, fixed-value):
>   TC1 valid → `==200` + `X-Compliance-Status: OK` + TPM checkpoint + 100 proofs +
>   STH; TC2 Safe-Lock → `==403` + `CRITICAL_FAILURE` header; TC3 external verifier
>   → `all_ok` True, and a 1-byte tamper of the checkpoint root / a proof leaf →
>   verification fails; + no-TPM → `tpm_checkpoint=null` (never faked) and `all_ok`
>   False. The verifier runs with only the bundle (no internal access).
> Audit suite **+4 → 526 passing**; Phase-26 transparency routes + the 24-35
> gateways 0 regressions (only edit to an existing file: the additive
> `/v1/compliance/attest` route); kernel audit green; `kernel/src/mm/` +
> `taint_maps.h` + `taint_gate.c` untouched.
> **HONEST SCOPE (G13 — userspace gateway only):** the bundle's inclusion proofs +
> STH + EK checkpoint signature are real and externally verifiable, but the EK is
> the software `LocalSoftTPM` key on dev (no real TPM / EK-cert chain), so on a
> TPM-less host the checkpoint is `null` (never a faked hardware signature). Per
> INV-6 the bundle is auditor INPUT, not an external audit. **Moat UNCHANGED
> 49/80** — moving the tally remains gated **exclusively on independent
> third-party external audit validation**, never on self-collected evidence.

> **Phase 37 — G14 self-healing policy re-sync (2026-06-17).** Composes Phase-26
> (ledger) + Phase-35 (TPM checkpoint) + Phase-33 (taint colors) + Phase-32
> (Safe-Lock).
> - `backend/services/policy_self_healing.py`: `PolicySelfHealingBroker.heal`
>   finds the **last valid checkpoint** (EK signature verifies AND the recomputed
>   Merkle root matches the ledger), then `PolicyReSync.resync` replays the anchored
>   `ipc_taint_inheritance` events in `[0, tree_size)` back into the kernel
>   `taint_colors` map via the EXISTING connector — healing a transient map
>   corruption from the durable, hardware-anchored ledger. On success it clears
>   Safe-Lock (the verified replay IS the authorized recovery). **Refuses** (stays
>   locked) when NO checkpoint verifies — no silent recovery from a tampered ledger.
> - `backend/tests/audit/test_phase37_self_healing.py` (5 tests): the headline
>   **recovery-from-ledger** — corrupt taint (`evict`) + Safe-Lock → `heal()` →
>   `peek_entry.max_color == TOXIC` restored + Safe-Lock cleared; recovers to the
>   last valid checkpoint; no-checkpoint → `healed=False` + stays locked; a tampered
>   anchor root is rejected as a recovery point. `kernel/src/mm/` untouched.

> **Phase 38 — G15 system lockdown & PKI preparation (2026-06-17).**
> - `backend/services/tpm_attestation.py`: added `HSMBackedTPM2` — a
>   production-shaped anchor that REQUIRES a manufacturer EK-cert chain
>   (`VOS3_HSM_EK_CERT_CHAIN`) validating to a trust CA (`VOS3_HSM_TRUST_CA`) +
>   a real `/dev/tpmrm0`; `present()` is False on a dev host without them →
>   **fail-closed, no software fallback**. `_resolve_default_tpm` returns it under
>   the `VOS3_REQUIRE_HSM_EK_CERT` lockdown flag; default (flag off) keeps the dev
>   `LocalSoftTPM` (zero regression). `LocalSoftTPM` is retained (dev/test seam,
>   clearly marked INSECURE) — not ripped out.
> - `backend/services/lockdown.py` + `app.py`: audit-readiness **absence guard** —
>   a code audit found NO `/debug` / `dump_taints` / `/dev/mem` endpoints in the
>   surface, so `assert_audit_ready` enforces (and CI-guards) their absence;
>   `create_app` hard-fails boot under `VOS3_AUDIT_LOCKDOWN` if any appear.
> - `backend/tests/audit/test_phase38_lockdown.py` (6 tests): lockdown resolves the
>   HSM anchor (fail-closed without a cert chain; partial provision still
>   fail-closed); flag-off keeps the soft dev anchor; the live app exposes no
>   forbidden debug endpoints; the guard detects an injected `/debug/dump_taints`.
> Audit suite **→ 539 passing** (Phases 24-36 gateways 0 regressions; the only
> edits to existing files are the additive HSM anchor + the flag-gated lockdown
> guard wire); kernel audit green; `kernel/src/mm/` + `taint_maps.h` +
> `taint_gate.c` untouched.

> **AUDIT-READINESS POSTURE (end of the G-series remediation).** Gaps G1-G15 now
> have a **userspace enforcement layer + tests + honest scope** landed
> (default-OFF / MOCK-on-dev where a kernel/hardware producer is deferred). The
> recurring deferred producers are documented and consistent: the live eBPF/kprobe/
> bpf_timer programs, the real `prctl(PR_SET_SECCOMP)` install, the physical TPM
> 2.0 EK-cert chain, and the kernel NV index. **Moat REMAINS 49/80.** Per **INV-6**
> the moat tally advances ONLY on an independent third-party external audit report
> — the self-collected evidence, the attestation bundles, and these tests are
> auditor INPUT, **not** an external audit. Status: **AUDIT-READY** — the 50/80
> (and onward) transition is gated on that external report and is NOT claimed here.

```
backend/tests/audit/
  test_h17_fail_open_sweep.py            61
  test_b8_semantic_firewall_boundary.py   3
  test_b5_ibct_capabilities.py           22
  test_b6_gpu_isolation.py               25
  test_b3_ebpf_bridge.py                 29   (17 live + 12 live mark-push bridge)
  test_b7_maif_integrity.py              24
  test_b9_spiffe_federation.py           17
  test_b4_llm_quarantine.py              26
kernel/tests/audit/
  test_a1_w_x_sanitizer.c                23   (real vos3_vmm_flags_to_pte + cited twins)
  test_a3_vbus_hmac.c                    15   (real HMAC-SHA256 + cited verify twin)
  test_a6_model_signature.c              12   (real vvfs_model_verify + ed25519/sha512)
  test_a8_vector_vfs_sandbox.c           16   (verbatim tokenizer + cited resolver/ACL twins)
  test_m3_model_sig_enforcement.c         7   (real verify + cited gate twin, dual-compiled)
  test_b3_mark_push_struct.c              7   (host sizeof/offset probe of vos3_taint_mark_push_arg, G5)
  a6_vectors.h                                (Python-cryptography Ed25519 vectors)
  run_kernel_audit.sh                          (master C host-runner: A1+A6+A3+A8+M3×2+B3-MP)
```

### 1.4 M3 — read-gate enforcement validated host-side (added 2026-06-13, no moat change)

The dormant M3 model-signature read gate (`vvfs_model_sig_verify`,
`vvfs_transport.c:84-97`) was validated **with enforcement compiled on**
(`-DVOS3_VVFS_REQUIRE_MODEL_SIG=1`) without touching the production default
(`vvfs.h:202` remains `0`) or the unprovisioned build-time pubkey placeholder.
The gate body is mirrored verbatim under the same macro and calls the **real**
`vvfs_model_slot_is_verified()` (`vvfs_model_verify.c`); the trusted anchor is
provisioned in test scope only via the exported `vvfs_set_trusted_model_key()`.

The suite is compiled **both ways** by the runner:
- **enforce ON (5 tests):** verified slot → ALLOW; unregistered, foreign-signed,
  and re-registered(unverified) slots → `KEYREJECTED` (fail-closed).
- **default OFF (2 tests):** an unverified slot is pass-through — proving the
  shipped default is a safe no-op and that flipping the flag alone, without a
  provisioned trusted key, would fail-close (brick) every model load.

> **Honest accounting:** this validates the enforcement *path*; it does **not**
> close M3 or change the moat (**still 49/80**). M3 closure requires a
> provisioned Ed25519 anchor baked into the kernel build plus the QEMU
> enforcement matrix (Phase 5) and external crypto audit (Phase 6).

### 1.3 A8 — vVFS isolation (added 2026-06-12, all green, no new finding)

The vVFS storage surface was audited across two layers, both fail-closed:
- **Path-traversal jail:** the resolver clamps `..` at the root because
  `root->parent == root` (`vvfs.c:252`), so no path — `../../../../etc/passwd`,
  relative climbs, or absolute escapes — can resolve *above* the jail root. The
  pure tokenizer (`skip_slashes`/`next_component`) is copied verbatim from
  `vfs.c`; over-long components truncate to `NAME_MAX-1` with no overflow, and
  embedded NUL bytes truncate the path at the `strncpy_from_user` boundary
  (`fs_syscall.c:192`).
- **Multi-tenant ACL:** `vvfs_transport_acl` (`vvfs_transport.c:51-74`) denies
  with `-EACCES` on out-of-range slot, unmounted slot, ownerless slot, and —
  the cross-tenant case — when the caller's `tid` does not equal the slot's
  `owner_tid`. Per-slot `vecvfs_*` ops bound `slot_id` (no OOB-array read),
  index only `&g_vecvfs_index[slot_id]` (no cross-slot bleed, canary-verified),
  and clamp payloads to `VECVFS_PAYLOAD_SIZE` (no shard overrun).

> **Observation (not a finding):** the M3 model-signature read-gate
> (`vvfs_model_sig_verify`) is compiled **OFF by default**
> (`VOS3_VVFS_REQUIRE_MODEL_SIG=0`) — this is the documented moat-49/80 ceiling
> (M3 pending the QEMU enforcement matrix), not a regression. When enabled it is
> fail-closed (rejects an unverified slot with `KEYREJECTED`).

### 1.5 Sprint 2 — B9 federation + B4 dual-LLM quarantine (added 2026-06-13, +43 tests, no new finding)

Two backend identity/isolation subsystems were swept adversarially; **both are
fail-closed and no new vulnerability or fail-open path was found.**

- **B9 — `identity_federation_bridge.py` (17 tests):** a spoofed bundle whose
  declared `trust_domain` ≠ the registered partner → `PARSE_ERROR` (no swap); a
  rolled-back/"expired" lower-sequence bundle → `REGRESSION` rejected by default
  (only an explicit `force_rollback=True` overrides); a tampered body under a
  pinned anchor → `PIN_MISMATCH`; transport failure → `TRANSPORT_ERROR` (no
  swap); self-domain / non-HTTPS / sub-30s-refresh / unpinned-`HTTPS_SPIFFE`
  registrations refused at the door; `HTTPS_SPIFFE` bootstrap fail-closes
  without a pinned trust anchor.
- **B4 — `dual_llm_router.py` + `quarantined_llm.py` (26 tests):** all ten
  injection families detected + surfaced; the model `INJECTION_DETECTED`
  sentinel and a back-end LLM failure both fail-closed (`is_safe=False`,
  empty `sanitized_text`); the privileged path receives only the sanitized
  `safe_summary`, never the raw attacker bytes; output re-scan catches
  model-passthrough exfil; the holding path is stateless (no cross-call bleed);
  the only pass-through (bypass) is **explicit** (`bypassed=True` + WARNING),
  never a silent fail-open.

> **Documented ceilings (observations, not findings):** B9's `HTTPS_SPIFFE`
> synced-key validation trusts transport TLS + the in-bundle trust-domain
> cross-check (full SVID-cert-vs-synced-JWK binding is a follow-up); B4 is
> defense-in-depth by design — it **surfaces** `was_blocked` for the caller's
> policy to refuse rather than hard-refusing in the router. Neither weakens the
> fail-closed guarantees verified above. **Moat unchanged at 49/80.**

---

## 2. Active Root-Cause Patches (S3 Remediations)

Two genuine fail-open defects were found **and fixed** in source during the
initial sweep (documented in this section). Both are minimal, reversible, and
covered by the new tests. Two further S3 sharp edges — **B5-1** (prefix
confusion) and **B7-1** (empty-`model_sha256` anti-swap skip) — were
subsequently patched as well; their fixes are detailed in §3 alongside their
findings. A third sharp edge — **A1** (W^X asymmetry + ELF-loader RWX bypass,
reclassified S2) — was also patched; see §3. **A3-1** (HMAC-strip) was hardened
to a three-valued fail-closed contract in Sprint 3 (see §3), and the
TS-2026-PF_MEMSET_SCRUB boot-scrub #PF was fixed. Total active patches: **6**.

### 2.1 Patch 1 — H.17: silent exception swallow in the guardrails engine

- **File:** `backend/ai/agents/guardrails.py` (`ArchitecturalGuardrails.validate_project`, ~line 803)
- **Severity:** S3 (silent safety-check miss)
- **Defect:** Each Iron Rule's `detect()` ran inside `try: … except Exception: pass`.
  A guardrail rule meant to catch unsafe generated code (embedded credentials,
  `eval`, missing error boundaries) that *threw* during evaluation was silently
  dropped — the violation it would have raised vanished with no trace. A
  crashing rule reads as a clean file.
- **Detection:** H.17 AST fail-open sweep flagged the `except: pass` inside a
  safety gate (`test_gate_has_no_swallowing_except`).
- **Fix:** Added a module `logger` and replaced the bare swallow with a
  `logger.warning(..., exc_info=True)` that records the crashing rule id +
  filepath and treats the rule as DID-NOT-EVALUATE — the "don't crash the whole
  analysis" intent is preserved while the miss is now loud, not hidden.
- **Verification:** H.17 sweep green (61/61); `guardrails` import + rule-count
  smoke passes; no regression in the security suite.

### 2.2 Patch 2 — B8: oracle-enumeration leak at the semantic-firewall boundary

- **File:** `backend/api/chat_routes.py` (`_semantic_firewall_preflight`)
- **Severity:** S3 (information disclosure / blocklist enumeration oracle)
- **Defect:** On a DENY, the HTTP 400 body returned the firewall's internal
  `reason` (`"banned-substring:high-NNN"`) **and** the `confidence` score to the
  caller. An attacker could map probes to corpus indices and learn the
  blocklist's structure/size and exactly which rule fired — a classic
  enumeration oracle. The precise verdict was already written to the server log.
- **Detection:** B8 anti-oracle test (`test_b8_04_route_rejection_does_not_leak_corpus_oracle`)
  asserted the 400 body must not contain `banned-substring`, the phrase index,
  or `confidence`.
- **Fix:** The 400 detail is now a generic `{"error":
  "input_rejected_by_semantic_firewall"}`. The precise reason-id + confidence
  remain in the server-side WARNING log only (defender keeps full fidelity;
  attacker gets nothing actionable).
- **Verification:** B8 suite green (3/3); full `tests/security` regression
  762 passed / 9 skipped (baseline 740 — no regression).

---

## 3. The Sharp-Edges Register — 5 Operational & Architectural Findings

These are **not** patched. Each is either a documented ceiling, a
caller-responsibility contract, or a compensated design choice whose
disposition belongs to the operator. Each has a characterization test that
catches any silent regression.

### Finding B5-1 — Prefix confusion in the agent capability table (S3) — ✅ PATCHED

- **File:** `backend/core/security/agent_capability_table.py`
  (`AgentCapability.covers` → `_scope_prefix_covers`)
- **Vector:** Scope was matched with a bare `resource.startswith(self.scope_prefix)`.
  A capability granted with a scope prefix that does **not** end in a delimiter
  (e.g. `https://corp.example` without the trailing `/`) was escapable by a
  look-alike host: `https://corp.example.evil.com/steal` satisfied `startswith`.
- **Risk:** A scoped tool capability could be exercised against an
  attacker-chosen sibling resource — capability-boundary escape.
- **Fix (landed):** new delimiter-aware `_scope_prefix_covers()` — a non-exact
  match must terminate on a `/`/`:` boundary (the prefix already ends in a
  delimiter, or the next resource char is one). Empty prefix = whole-tool scope
  and exact match are preserved; legitimate boundary matches
  (`https://corp.example/data`) still resolve. The delimiter-less rogue sibling
  is now refused with `CapabilityDenied`.
- **Test:** `test_b5_prefix_confusion_without_delimiter_now_blocked` (asserts
  `CapabilityDenied`) + `test_b5_delimiterless_prefix_still_covers_real_boundary`
  (no over-blocking) + `test_b5_prefix_scope_with_delimiter_blocks_sibling_host`.

### Finding B3-1 — eBPF write-gate "no entry → default ALLOW" race ceiling (S2-ceiling) — ✅ PATCHED & VERIFIED (kernel enforcement proven LIVE & per-fd on 6.12 BPF-LSM; production-path wiring + external audit are follow-on hardening — moat held 49/80)

- **Files:** `backend/security/kernel_gate_connector.py` (bridge) +
  `kernel/include/vos/taint_maps.h` (contract) + `kernel/src/sec/taint_gate.c`
  (eBPF LSM program).
- **Vector (original):** A write to a `(pid, fd)` with no pushed color entry was
  **ALLOWED** ("no entry → default ALLOW"); a compromised agent could write
  before/instead of the push and escape byte-level egress gating.
- **Phase 19.0/19.1 landed (host-side, 2026-06-14):**
  - `struct vos3_taint_mark_push_arg` + `VOS3_TAINT_IOC_MARK_PUSH` added to
    `taint_maps.h` — a 16-byte header + the **UNCHANGED** color entry == 65 584
    bytes (G5, proven by the `B3-MARK-PUSH` host probe). The ioctl encodes a
    **pointer**, not the struct by value: the 65 584-byte struct overflows the
    `_IOC` size field, so the spec draft's literal `_IOW(...struct...)` was
    corrected here (handler `copy_from_user`s the full arg).
  - `KernelGateConnector.mark_push()` — atomic mark+push marshalling, per-fd MARK
    bit, and a fail-closed ladder: a **MARKED** fd with no entry now DENies
    (`-EPERM`); an **UNMARKED** fd stays default-allow (untainted I/O, G3). The
    commit re-derives `max(colors)` and overrides caller metadata (G4).
  - `taint_gate.c` LSM hooks (`file_permission`, `socket_sendmsg`) gain the same
    MARK-aware fail-closed ladder at the two former default-allow sites.
  - 12 B3 tests repointed from the reference model to this live bridge API
    (`test_b3_ebpf_bridge.py`, all green in MOCK) + 7 C struct-contract tests.
- **Phase 19.2 build-gate — COMPILE-PATH CERTIFIED (2026-06-14):** the
  `taint_gate.c` LSM program now **compiles clean** under `-target bpf` with
  `__VOS3_TAINT_GATE_REAL_BPF_BUILD=1` (exit 0, **zero warnings**), producing a
  valid BPF ELF with `lsm/file_permission` + `lsm/socket_sendmsg` TEXT sections
  and the `.maps` table. Toolchain: `infra/runners/linux_ebpf_builder.Dockerfile`
  (Ubuntu 24.04, clang 18.1.3, libbpf 1.3.0) driven by
  `infra/runners/run_isolated_ebpf_build.sh`; gate flags `-Wall -Wextra -Werror
  -Wno-unused-parameter` (the last is kernel-idiomatic for fixed LSM hook ABIs).
  Object SHA-256 `9ac1051f687ce3ad3da7c7b69c0543a7f8a6214346d3eb16022970f6440b06f4`
  (artifact under the git-ignored `kernel/build/bpf/`). The skeleton compiles
  against opaque forward decls for `struct file/socket/msghdr` (no BTF/vmlinux.h
  needed since no kernel field is dereferenced).
- **STILL NOT closed (Sprint 19.2-runtime → 19.4) — why this is PARTIAL, not
  "Verified":** a clean **compile** is not a clean **load**. The eBPF **verifier**
  runs at load time on a real kernel and has NOT accepted this program; there is
  no **Linux ≥ 5.17 BPF-LSM runner** here (this host is macOS — the object was
  cross-compiled in a container, never loaded/attached); the LIVE `mark_push`
  ioctl ctypes binding is unwired; the live race-under-load test does not exist;
  and **no external eBPF/LSM audit has run** (spec §8). **The moat is UNCHANGED
  at 49/80.** Build-gate certified ✅; load/verify/attach/runtime pending ⬜.
- **Tests:** host-side `test_b3_atomic1_*`, `test_b3_marked_no_entry_*`,
  `test_b3_max_color_override_*` (12) + the `B3-MARK-PUSH` C probe (7). The
  original ceiling characterization `test_b3_FINDING_no_entry_defaults_to_allow`
  is retained: an **UNMARKED** no-entry fd still ALLOWs by design (G3).

### Finding B7-1 — Empty `model_sha256` skips the anti-swap binding (S3) — ✅ PATCHED

- **File:** `backend/services/maif_v2.py` (`ProvenanceGate.require_slot_start`)
- **Vector:** The "chain terminates at the artifact being loaded" check fired
  **only** when *both* `head_artifact` and `model_sha256` were non-empty:
  `if (head_artifact and model_sha256 and not compare_digest(...))`. A caller
  passing `model_sha256=""` (or whitespace/`None`) got a SLOT_START that accepted
  **any** well-formed chain — the "this chain is for THIS model" guarantee was
  silently lost.
- **Risk:** A swapped checkpoint with a valid-but-unrelated provenance chain
  loaded cleanly when the loader omitted the model hash. Chain *integrity* was
  enforced; chain *binding* was not.
- **Fix (landed):** `require_slot_start` now rejects an empty/whitespace/`None`
  `model_sha256` under `require_provenance` with a loud `ValueError` (caller
  contract violation) **before** any chain is accepted — fail-loud, not
  fail-silent.
- **Test:** `test_b7_empty_model_sha_now_rejected` (parametrized over
  `["", "   ", None]`, asserts `ValueError`) + `test_b7_head_artifact_swap_is_refused`
  (proves binding works when the hash is present).

### Finding A3-1 — Transport-level HMAC-stripping path — ✅ PATCHED

- **File:** `kernel/src/drivers/vbus_transport.c` (`vos3_verify_cmd_hmac`),
  `kernel/src/drivers/vbus_bridge_internal.h` (state macros).
- **Vector:** The verify primitive returned `1` (accept) when `payload_len < 32`
  (no trailer) **or** `key_len == 0` (no session key) — conflated with the
  authentic-MAC case. In isolation this was an HMAC-strip path: a frame with the
  trailer removed verified as "valid". (Compensated at runtime by the D-CRIT1
  handshake downgrade-reject, but the per-frame primitive itself trusted that.)
- **Fix (landed):** `vos3_verify_cmd_hmac` now returns a **three-valued** state
  (`VOS3_HMAC_VALID` / `VOS3_HMAC_INVALID` / `VOS3_HMAC_NO_TRAILER`, int-compatible
  macros so the existing `extern int` call sites stay warning-free). `NO_TRAILER`
  is a distinct, non-`VALID` state, so a call site can **fail-closed on a stripped
  frame** instead of passing it through — defense-in-depth independent of the
  handshake lock. The test caller (`test_phase82_endurance.c` T4.7) and the A3
  host twin were updated to the new contract.
- **Test:** `test_a3_vbus_hmac.c` — A3-1.1-6: a dropped-trailer / no-key frame
  now returns `NO_TRAILER` (was `VALID`), and the downstream ingestion handler
  (`ingest_frame_accepts`, accepts only `VALID`) **refuses** it; the three states
  are proven mutually distinct. A3.1-A3.5 still prove the populated path sound.

### Finding A1 — W^X mapping asymmetry + ELF-loader RWX bypass — ✅ PATCHED

> **Reclassified S2 (was "consistency").** While aligning the cosmetic
> map-vs-`mprotect` asymmetry, the audit found a **real W^X bypass** on the exec
> path that the original A1 test had not covered — see "Bypass" below.

- **Files:** `kernel/src/mm/vmm.c` (`vos3_vmm_map`, `vos3_vmm_map_user`),
  `include/vos/vmm.h` (`vos3_vmm_flags_to_pte`, `vos3_pte_create`),
  `kernel/src/exec/elf.c:96-108` (`elf_flags_to_pte`).
- **Asymmetry (consistency):** `mprotect(WRITE|EXEC)` rejected with `-EINVAL`,
  while the `vos3_vmm_map`/`flags_to_pte` path silently corrected `WRITE|EXEC`
  to `WRITE + NX`.
- **Bypass (the real finding, S2):** the ELF loader does **not** use
  `vos3_vmm_flags_to_pte`. Its own `elf_flags_to_pte()` translates an
  `PF_R|PF_W|PF_X` (RWX) segment to a PTE with `WRITABLE` set and **NX clear**,
  then passes it raw to `vos3_vmm_map_user()` → `vos3_pte_create()` (a bare
  `addr|flags` combine, no W^X check). Result: a malformed/hostile ELF with an
  RWX `PT_LOAD` segment obtained a genuine **Writable+Executable user page** —
  a code-injection primitive — entirely bypassing the W^X sanitizer.
- **Fix (landed):**
  1. `vos3_vmm_map` now **rejects** `W|X` with `VOS3_VMM_ERR_INVALID`, unifying
     the translated-flags API with `mprotect`.
  2. `vos3_vmm_map_user` (the raw-PTE chokepoint for every user mapping incl.
     the ELF loader) now **forces `NO_EXECUTE` on any writable page** before
     `vos3_pte_create`, so no RWX user page can be created regardless of the
     caller's flags. Strip (not reject) here keeps RWX `PT_LOAD` segments
     loadable as W+NX — well-formed binaries are unaffected.
- **Test:** `test_a1_w_x_sanitizer.c` — A1.17-19 (map rejects W|X, allows
  R+X / R+W), **A1.20-21 (RWX-bypass regression: `elf_flags_to_pte(RWX)` is W+X
  pre-fix; the `map_user` sanitize forces NX post-fix)**, A1.22-23 (legitimate
  code/data segments still load), plus the original A1.1-16 (incl. the
  exhaustive 4096-combo no-RWX invariant).

---

## 4. Verification & Reproducibility

All artifacts are deterministic and host-runnable — no QEMU boot and no
cross-compiler are required for these audits (the kernel gates audited are pure
crypto / verify / translation logic, exercised either as real source or as
cited faithful twins).

### 4.1 Python application layer

```bash
cd backend
# Full audit sweep (serial, deterministic):
.venv_p312/bin/python -m pytest tests/audit/ -n 0
# Expected: 149 new audit tests green (within the broader tests/audit/ tree).

# Per-module:
.venv_p312/bin/python -m pytest tests/audit/test_h17_fail_open_sweep.py -q          # 61
.venv_p312/bin/python -m pytest tests/audit/test_b8_semantic_firewall_boundary.py -q #  3
.venv_p312/bin/python -m pytest tests/audit/test_b5_ibct_capabilities.py -q          # 22
.venv_p312/bin/python -m pytest tests/audit/test_b6_gpu_isolation.py -q              # 25
.venv_p312/bin/python -m pytest tests/audit/test_b3_ebpf_bridge.py -q                # 17
.venv_p312/bin/python -m pytest tests/audit/test_b7_maif_integrity.py -q             # 24
```

### 4.2 Microkernel C layer

```bash
# Master host-runner — compiles the freestanding sources directly with clang
# and runs A1 + A6 + A3 in sequence:
bash kernel/tests/audit/run_kernel_audit.sh
# Expected tail: "ALL KERNEL AUDIT TESTS PASSED"  (A1 23/23, A6 12/12, A3 15/15, A8 16/16, M3 7/7 → 73 total)
```

Toolchain notes captured by the runner:
- `sha256.c` pulls the kernel's x86-only `string.h` inline asm; the A3 test
  `#define VOS3_STRING_H` before including it so host libc `memcpy`/`memset` are
  used instead (arm64/x86 host-portable).
- `vmm.h` and the A6 crypto stack (`ed25519_verify.c` + `sha512.c` +
  `vvfs_model_verify.c`) host-compile cleanly with `-Iinclude`.
- A6 Ed25519 vectors in `a6_vectors.h` are real signatures produced with Python
  `cryptography` (the OMS signer scheme), regenerable from the audit session.

### 4.3 Authority boundaries (what these tests do and do NOT prove)

- The C tests prove the **logic** of the W^X sanitizer, HMAC trailer
  verification, and model-signature gate. The in-kernel `vbus_replay_test.c`
  (QEMU) remains the authority for the **live** frame-drop + HMAC-ban behaviour;
  the live eBPF LSM byte-gate still requires a Linux ≥ 5.17 runner (the standing
  GA gate, unchanged by this audit).
- Where a `vmm.c`-resident enforcement point could not host-compile, the test
  uses a **faithful twin** with the exact source lines cited; a divergence
  between twin and source is itself a regression the reviewer must catch.

---

## 5. Disposition & Sign-off Matrix

| Item | Type | State | Operator action |
|---|---|---|---|
| Patch 1 (H.17 guardrails) | S3 fix | ✅ applied (working tree) | review + commit |
| Patch 2 (B8 oracle leak) | S3 fix | ✅ applied (working tree) | review + commit |
| Finding B5-1 (prefix confusion) | S3 | ✅ patched | delimiter-aware `_scope_prefix_covers` (landed) |
| Finding B3-1 (eBPF race ALLOW) | S2-ceiling | ✅ **PATCHED & VERIFIED** — 19.0-19.4 (mark-and-push, verifier-clean, frame-audit, load-sim) + 19.5 (verifier ACCEPTS, attaches, deny-on-mark) + 19.6 (PID-ns translation + **CO-RE per-fd resolution**). **Live on 6.12.68, separate pidns: marked fd→EPERM, unmarked fd→allow.** Follow-on hardening (prod-bridge wiring, live per-color, race-stress, external audit) pending → **moat HELD 49/80** | kernel enforcement VERIFIED |
| Finding B7-1 (empty-sha skip) | S3 | ✅ patched | `require_slot_start` raises `ValueError` on empty hash (landed) |
| Finding A3-1 (HMAC strip) | S2-contingent | ✅ patched | three-valued fail-closed verify return (landed) |
| TS-2026-PF_MEMSET_SCRUB (boot-scrub #PF) | Low (harness) | ✅ patched | map-bounded per-page scrub in kmain.c (landed) |
| Finding A1 (W^X asymmetry + ELF RWX bypass) | S2 (reclassified) | ✅ patched | map rejects W|X; map_user forces NX (landed) |

**Working-tree invariant:** no `git add`, no commit, no staged changes were
made during this audit. The 9 new audit artifacts and 2 source patches live in
the dirty working tree of `feat/shield-integration` for operator review.

---

*Generated by the autonomous security-audit session, 2026-06-12. Cross-reference:
`docs/TEST_PLAN_300.md` (the 300-test matrix this audit draws from),
`infra/persistence/active_context/SESSION_HANDOVER_LOCK.md` (release-gate state).*
