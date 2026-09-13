# vOS v1 — 300-Test Audit Matrix

> Purpose: a single, executable test plan to surface **bugs**, **security
> vulnerabilities**, **code duplication / dead code**, and **performance
> regressions** across every layer of vOS. Designed as a working backlog —
> each row is one concrete, independently-runnable check.
>
> Generated 2026-06-12 against `feat/shield-integration` (10 commits over
> `main@b3a6883`). Test counts are a target, not a claim of existing coverage.

## How to read this

Each test has: **ID** · **target** (file/subsystem) · **type** · **what it asserts**.

Types:
- 🐞 `BUG` — correctness / logic / edge-case defect
- 🔓 `SEC` — security vulnerability / boundary bypass / fail-open path
- ♻️ `DUP` — code duplication, dead code, or seam that should be unified
- ⚡ `PERF` — latency / throughput / resource regression
- 📐 `CONTRACT` — interface/spec drift between two layers that must agree

Severity tag on findings: `S1` (exploitable / data-loss) … `S4` (cosmetic).

Allocation (300 total):

| Area | Tests |
|---|---|
| A. Kernel (C) | 70 |
| B. Backend security layer | 75 |
| C. Backend AI engine + services | 45 |
| D. API + V-Core + persistence | 35 |
| E. Frontend (Next.js/Convex) | 22 |
| F. Desktop (Tauri/Rust) | 18 |
| G. Infra / supply-chain / compliance | 18 |
| H. Cross-cutting (dup / dead code / static / perf) | 17 |

---

## A. Kernel (C) — 70 tests

### A1. Memory management — `kernel/src/mm/{pmm,vmm,slab}.c` (12)
- A1.01 🐞 PMM double-free of the same physical frame is rejected, not silently re-added to the freelist.
- A1.02 🔓 `vmm` map operations reject any VA below the higher-half boundary `0xFFFF800000000000` (S1).
- A1.03 🔓 W^X: `mmap`/`mprotect` with `PROT_WRITE|PROT_EXEC` returns `-EINVAL` AND the PTE sanitizer strips W+X at hardware level (two independent layers).
- A1.04 🐞 Slab page reclamation (`vos3_heap_shrink()`) returns empty slabs to PMM without corrupting partially-used slabs.
- A1.05 🐞 Allocation under PMM exhaustion fails cleanly (NULL/-ENOMEM), no wrap-around / off-by-one into reserved frames.
- A1.06 🔓 KASLR: two boots produce different kernel base; entropy ≥ documented bits.
- A1.07 🐞 `brk`/heap grow then shrink leaves no stale PTE mappings (TLB shootdown correctness).
- A1.08 🔓 Inference-memory region mapped `VOS3_AI_FLAG_READ_ONLY` (PTE bit 10, no bit 1) faults on any write attempt from user/agent context (S1).
- A1.09 🐞 Page-coloring governor (A4) assigns colors deterministically and never hands the same colored page to two tenants.
- A1.10 ⚡ Slab alloc/free hot path latency under load (1M cycles) within budget; no fragmentation creep.
- A1.11 🐞 `pmm.c` recent diff (this branch, +9 lines) — verify the change doesn't regress freelist accounting (compare free-count invariant before/after).
- A1.12 🔓 Guard-page / red-zone present between kernel stacks; overflow faults instead of corrupting neighbor (regression of phase51 2 MiB stack-overflow fix `c4212d6`).

### A2. Scheduler — `kernel/src/sched/*` (8)
- A2.01 🐞 `SCHED_COOKIE_STATS` reports consistent counts across siblings.
- A2.02 🔓 Core-scheduling cookie isolation: tasks with different cookies never co-run on SMT siblings (`SCHED_SIBLING_CHECK`).
- A2.03 🐞 RT inference class (J5) bounded by cgroup `cpu.max`; cannot starve CFS tasks.
- A2.04 🐞 Futex hash table (16 buckets) wake fairness — no lost wakeups under contention.
- A2.05 ⚡ Context-switch cost with `BENCH_MODE=1` vs DEBUG; verify 93% log reduction claim holds.
- A2.06 🐞 `pthread_join` lifecycle: `CLONE_CHILD_CLEARTID` + futex-wake-on-exit fires exactly once.
- A2.07 🔓 Sibling-check returns `compat=0` for incompatible cookie pairs (no false-permit).
- A2.08 🐞 Thread TGID inheritance (`CLONE_THREAD`) — getpid consistent across threads of one process.

### A3. VBus protocol — `kernel/src/drivers/vbus*`, `kernel/include/vos/*` (12)
- A3.01 🔓 HMAC-SHA256 frame auth: tampered payload byte → frame rejected (S1).
- A3.02 🔓 Forged MAC with wrong key rejected; verification is constant-time (timing-invariant across mismatch position).
- A3.03 🔓 Downgrade attack: frame requesting no-HMAC mode after handshake is refused.
- A3.04 🐞 Empty payload and max-size payload both handled without under/overflow.
- A3.05 🔓 Per-session random key exchange during HANDSHAKE — keys not reused across sessions.
- A3.06 🐞 Ring-buffer RX zero-copy: no memmove, CRC computed in place; wrap-around correctness at buffer boundary.
- A3.07 🔓 `ivshmem` Zone ACL: agent A cannot `zone_base()` into agent B's zone (owner_tid check) (S1).
- A3.08 🔓 Slot 0 Coordinator zone is kernel-only; user agent bind refused.
- A3.09 🐞 Ownership cleared on slot reset — no dangling owner_tid after agent death.
- A3.10 🔓 `vos3_bridge_bound_window()` (CVE-2026-23086) — out-of-window offset rejected; MAX_CHUNK_SIZE (8KB) enforced.
- A3.11 📐 `vbus_ai_cmds.c` (new this branch, +72 lines) — every new command has a reply path and an error path; no unhandled opcode falls through.
- A3.12 ⚡ VBus throughput regression vs documented 228.8 cmd/s, P99 7.6ms baseline.

### A4. AI Guard / KIM / model slots — `kernel/include/vos/{ai_guard,ai_kim}.h`, `kernel/src/ai/*` (12)
- A4.01 🔓 `SYS_AGENT_KILL_ALL` (497) is privileged; unprivileged caller gets `-EPERM` (S1).
- A4.02 🐞 Kill-all scrubs all 8 model slots and zeroes inference memory (no residual weights).
- A4.03 🔓 Model slot isolation: agent in slot N cannot read slot M's inference/scratchpad memory.
- A4.04 🐞 Heartbeat VA `0xFFFFFFFFFFFFF000` synced between `ai_guard.h:368` and `vos3_sdk.h:31` (CONTRACT drift would silently break liveness).
- A4.05 🐞 `ai_kim.h` ceiling constants (MAX_VOCAB/DIM/SEQ_LEN/KV_PAGES) — a model exceeding any ceiling is rejected at load, not truncated.
- A4.06 🔓 Inference memory retention policy: SCRUB zero-fills on kill; PERSIST keeps RO; verify no SCRUB→leak.
- A4.07 🐞 KV-cache paging (`ai_kv_managed.h`) — page eviction under pressure doesn't corrupt active context.
- A4.08 🔓 INTENT_SUBMIT binds RTMR[1] extend; replay of an old intent hex with stale RTMR is rejected.
- A4.09 🐞 Confidence gate `ACTION_CHECK_CONFIDENCE` — score below threshold → `ERR 13 CONF_BLOCK`.
- A4.10 🔓 `POLICY_FORCE_PERMIT` toggle is privileged and audited; cannot be set silently.
- A4.11 🐞 `vvfs_model_verify.c` (new, +95 lines) — Ed25519 signature check on model load; unsigned/tampered model refused (M3 work).
- A4.12 ⚡ KIM_GENERATE latency per token within budget; no regression from read-only PTE mapping.

### A5. Crypto — `kernel/src/crypto/*` (8)
- A5.01 🔓 SHA-256 matches FIPS 180-4 known-answer vectors (KAT).
- A5.02 🔓 `sha512.c` (new, +179 lines) matches NIST KAT vectors.
- A5.03 🔓 `ed25519_verify.c` (new, +413 lines) — RFC 8032 KAT; bad signature rejected; `kernel/tests/ed25519_kat.c` passes.
- A5.04 🔓 HMAC-SHA256 matches RFC 2104 / RFC 4231 test vectors.
- A5.05 🐞 CRC32C matches Castagnoli reference for empty, 1-byte, and large inputs.
- A5.06 🔓 Ed25519 verify is constant-time w.r.t. signature validity (no early-exit timing leak).
- A5.07 🐞 GPR-only freestanding SHA (no SSE) produces identical output to reference under XSTATE_BV=0.
- A5.08 🔓 No fixed/test key leaks into production crypto path (grep for hardcoded keys in crypto/).

### A6. Security primitives — `kernel/src/sec/*` (12)
- A6.01 🔓 `taint_gate.c` eBPF LSM: write whose byte-range exceeds sink-policy ceiling returns `-EPERM` (S1).
- A6.02 🔓 Per-byte mode: clean prefix of a buffer writes through while TOXIC region of the SAME buffer is refused (EchoLeak).
- A6.03 🐞 `bpf_loop` bounded scan caps at 4096 bytes/chunk; full 64 KiB buffer covered across chunks (no gap).
- A6.04 🔓 SMAP: kernel access to user pages without STAC faults; 4 stac/7 clac pairing balanced (no leaked AC flag).
- A6.05 🔓 Stack canary `__stack_chk_fail` triggers on overflow; all 394 sites compiled in.
- A6.06 🔓 CET (`cet.h`) shadow-stack / IBT — indirect branch to non-endbr target faults (if enabled).
- A6.07 🐞 Syscall pointer validation: `access_ok()`/`strncpy_from_user()` on sys_puts/arch_prctl/clone/wait4/nanosleep → EFAULT on bad ptr (8/8 break-fix).
- A6.08 🔓 Capability table (`capability.h`) — over-scoped capability request denied at kernel.
- A6.09 🔓 Serialization barriers present on security-critical paths (mfence/sfence/lfence counts as documented).
- A6.10 🔓 KTEXT_HASH live `.text` CRC32C integrity check detects in-memory code patching.
- A6.11 🐞 `license_check.c` (new, +11 lines) — verify no fail-open path if license blob absent.
- A6.12 🔓 cred rotation (`cred_rotate.h`) — old credential invalidated atomically; no window where both valid.

### A7. Syscalls / musl / pthreads / signals (8)
- A7.01 🐞 Signal delivery: stack frame + `sa_restorer` → `__restore_rt` → `rt_sigreturn` round-trips correctly.
- A7.02 🐞 `openat(257)`/`newfstatat(262)` resolve relative to dirfd correctly; AT_FDCWD path.
- A7.03 🔓 `getdents64` on `/proc/self` returns live PID (VOS3_FS_NO_DCACHE — no stale cache) (regression).
- A7.04 🐞 `mmap`/`munmap` partial-unmap splits VMA correctly; `mprotect` on sub-range.
- A7.05 🐞 `futex(202)` WAIT/WAKE with timeout; spurious-wake handling.
- A7.06 🔓 `getrandom(318)` blocks until entropy or returns `-EAGAIN` with GRND_NONBLOCK; never returns predictable bytes.
- A7.07 🐞 `fork`+`exec`+`wait4` reaps zombie; no PID leak across 10k cycles.
- A7.08 🐞 `pipe2(293)`/`eventfd2(284)`/`dup3(292)` flag handling (O_CLOEXEC, O_NONBLOCK).

### A8. Filesystems (10)
- A8.01 🐞 VecVFS persistence: 1000 hard-reset cycles, zero data loss (Genesis-Gate regression).
- A8.02 🔓 `fscrypt` policy (K5) — encrypted file unreadable without key; policy inherited by new files.
- A8.03 🐞 `vvfs_transport.c` (modified, +41/-... this branch) — chunked transfer reassembly correctness at boundaries.
- A8.04 🐞 procfs no-dcache: rapid PID reuse doesn't surface stale `/proc/<pid>` entries.
- A8.05 🔓 Path traversal in any VFS open: `../` escape outside mount confined.
- A8.06 🐞 `getcwd`/`chdir` consistency after `rename` of an ancestor dir.
- A8.07 🐞 `vbus_fs` provenance (K4) — file origin metadata preserved across read/write.
- A8.08 🐞 Cold-boot Genesis flush: AI Guard scrub + cache wipe + DLP scrub, 50 iterations, no residue.
- A8.09 ⚡ VecVFS read/write throughput under concurrent access.
- A8.10 🔓 `readlink`/`symlink` cannot create a symlink escaping the sandbox root.

---

## B. Backend security layer — 75 tests

### B1. Byte-level IFC — `backend/security/taint_engine_v2.py`, `ifc_engine.py` (10)
- B1.01 🐞 `TaintedBuffer` slice preserves per-byte color array exactly (no off-by-one at slice bounds).
- B1.02 🐞 Concat of two tainted buffers merges color arrays in order; length invariant.
- B1.03 🔓 Egress of a buffer with any byte above sink ceiling is refused (S1).
- B1.04 🔓 `DeclassEvidence`: downgrade requires Ed25519 sig over SHA-256 of EXACT reviewed bytes; mismatched bytes → reject (S1).
- B1.05 🔓 Declass evidence expired (>5 min window) → reject.
- B1.06 🔓 Declass evidence for label X cannot downgrade to label Y (target-label binding).
- B1.07 🐞 Empty buffer / single-byte buffer color tracking edge cases.
- B1.08 🔓 Forged signature on DeclassEvidence rejected (wrong key).
- B1.09 ♻️ `ifc_engine.py` (blob-level) vs `taint_engine_v2.py` (byte-level) — confirm the blob engine is not silently shadowing the byte engine on any path; dedupe or document the seam.
- B1.10 ⚡ Per-byte coloring overhead on a 64 KiB tool result within latency budget.

### B2. Outbound PII shield — `backend/security/outbound_pii_shield.py` (8)
- B2.01 🔓 Raw email/phone/SSN/credit-card/national-ID to untrusted destination → fail-closed (S1).
- B2.02 🐞 Same PII to a TRUSTED destination → allowed (no false-positive block).
- B2.03 🔓 `ReleaseContext` is kind-scoped — an email-release context does not authorize an SSN send.
- B2.04 🔓 PII split across two sends (evasion) — confirm detection scope and document the ceiling if per-send only.
- B2.05 🐞 Unicode/obfuscated PII (full-width digits, zero-width chars) still detected.
- B2.06 🔓 `require_bbs_release()` (BBS+ path) — only disclosed attributes pass; undisclosed remain hidden.
- B2.07 🐞 False-positive rate on benign text containing number sequences (e.g. order IDs).
- B2.08 ♻️ Overlap with `egress_policy_combined.py` and `semantic_firewall.py` egress check — verify no conflicting verdicts; one authoritative decision point.

### B3. Kernel-gate bridge — `backend/security/kernel_gate_connector.py` (8)
- B3.01 🐞 MOCK mode records intended kernel action without enforcing (dev correctness).
- B3.02 🔓 LIVE mode on Linux uses real bpf() syscall; non-Linux hard-refuses (no Darwin nr collision) (S1).
- B3.03 🐞 Safe-fallback to MOCK on any binding error emits WARNING (no silent enforce-off without log).
- B3.04 📐 struct.pack format strings match `kernel/include/vos/taint_maps.h` byte-for-byte (65568-byte value, 8-byte key).
- B3.05 🐞 SHA-256 high/low pack mirrors C-side `vos3_taint_sha_pack`.
- B3.06 🔓 bpf() NR table correct per-arch (x86_64=321, aarch64=280, riscv64=280); unknown arch raises.
- B3.07 🐞 64 KB color budget — larger buffers chunked at push.
- B3.08 ⚡ Push latency MOCK vs LIVE.

### B4. Dual-LLM router / quarantine — `backend/ai/agents/dual_llm_router.py`, `backend/services/quarantined_llm.py` (8)
- B4.01 🔓 Untrusted tool result is sanitized through QuarantinedLLM before privileged agent sees it (S1).
- B4.02 🔓 Fail-closed: if quarantine LLM unavailable, the untrusted content is NOT passed through raw.
- B4.03 🐞 `ToolResultPacket.tainted_buffer` attached at the chokepoint for every tool result.
- B4.04 🔓 Prompt-injection payload in web_search result cannot alter privileged agent's instructions.
- B4.05 🐞 Env-flag disable path is explicit and logged (no accidental disable).
- B4.06 🔓 Quarantine LLM output is structurally constrained (cannot smuggle instructions via formatting).
- B4.07 ♻️ Overlap with `prompt_sanitization.py` (AA1) + `tool_output_sanitization.py` (AA2) + `semantic_firewall.py` — map the layering, ensure no redundant double-scan or gap.
- B4.08 ⚡ Added latency of the dual-LLM hop.

### B5. IBCT / capability tables — `backend/core/security/{ibct_engine,agent_capability_table}.py`, `intent_manifest_builder.py` (10)
- B5.01 🔓 `require_capability()` refuses an uncapped tool call (S1).
- B5.02 🔓 Over-scoped capability (broader than granted) refused.
- B5.03 🔓 Forged Ed25519 capability token rejected.
- B5.04 🔓 `require_delegated_tool()` — child op whose attenuated chain doesn't verify to trusted root → refuse.
- B5.05 🔓 Delegation can only attenuate, never amplify, authority.
- B5.06 🔓 `require_cross_agent_invoke()` — invoke outside proven Datalog graph refused.
- B5.07 🔓 Invocation-bound token cannot be replayed across a different invocation (S1).
- B5.08 🐞 Datalog fixpoint terminates (no infinite loop) on a cyclic delegation graph.
- B5.09 🐞 Token expiry / nonce single-use enforced.
- B5.10 ♻️ `agent_capability_table.py` vs `backend/security/capability_table.py` — two capability tables; confirm roles distinct or unify (DUP risk).

### B6. Hardware / GPU gates (10)
- B6.01 🔓 `iommu_dma_guard.require_iommu_for_gpu_bind()` fail-closes unless IOMMU provably ENFORCING (S1).
- B6.02 🔓 `perf_counter_lockdown` refuses multitenant inference unless `perf_event_paranoid≥2` + `RmProfilingAdminOnly:1`.
- B6.03 🔓 `gpu_driver_gate.require_trusted_driver_for_gpu_bind()` refuses closed/unsigned/unpinned module.
- B6.04 🔓 `accel_ioctl_filter` is default-deny; unlisted ioctl refused.
- B6.05 🔓 `cvm_launch_gate.require_cvm_launch()` refuses unless restricted-injection attested; D6 refuses CC-off.
- B6.06 🔓 `gpu_alloc_validator` refuses shared-host bind without MIG+CC+fresh-attest+cgroup-caps.
- B6.07 🐞 Dev-override env vars (`VOS3_IOMMU_DEV_OVERRIDE`, `VOS3_PERF_DEV_OVERRIDE`) log a loud WARNING and are off by default.
- B6.08 🔓 Override env var cannot be set via request header / user-controlled input (only process env) (S1).
- B6.09 📐 Injected fake procfs/sysfs in tests matches real kernel sysfs layout (CONTRACT — a divergence would make the gate untested).
- B6.10 🐞 `dra_mig_validator` / `rtmr_validator` — stale attestation nonce rejected.

### B7. Model integrity / provenance — `model_integrity_watchdog.py`, `maif_v2.py`, `weight_overlay.py` (8)
- B7.01 🔓 Watchdog re-checksums resident model regions; bit-drift → evict + `ModelIntegrityCompromised` (fail-closed).
- B7.02 🐞 Watchdog under thread contention (concurrent inference + checksum) — no false positive, no missed flip.
- B7.03 🔓 MAIF v2 `require_slot_start()` refuses missing/broken/mismatched/unsigned hash chain (S1).
- B7.04 🔓 Blocklisted training-dataset hash → load refused.
- B7.05 🔓 `weight_overlay.apply_overlay()` verifies base SHA-256 before applying sparse diff; wrong base → refuse.
- B7.06 🐞 Overlay integrity check — corrupted diff block detected.
- B7.07 🐞 Many overlays sharing one base — no cross-overlay contamination.
- B7.08 ♻️ Watchdog vs MAIF vs `hf_config_scanner` — three integrity-ish modules; map responsibilities, dedupe checksum logic.

### B8. Semantic firewall (NEW this branch) — `backend/services/semantic_firewall.py` (8)
- B8.01 🐞 Stage-1 banned-substring set: known-bad phrase fires DENY; case-folded/normalized match works.
- B8.02 🔓 Paraphrased injection ("disregard all that has been told to you") — document Stage-1 miss honestly; assert it is NOT claimed as caught (anti-overclaim).
- B8.03 🔓 Wiring in `chat_routes.py` (commit a7e52d1) — flagged input returns 4xx and never reaches the LLM (S1).
- B8.04 🐞 4xx rejection does not leak the matched banned phrase back to the caller (no oracle).
- B8.05 🐞 Empty / very large input handled without ReDoS or unbounded scan.
- B8.06 ⚡ Per-request scan latency on the input boundary (must not dominate request time).
- B8.07 ♻️ Overlap with AA1 `prompt_sanitization.py` and AA2 `tool_output_sanitization.py` — confirm complementary, not duplicate detection; one normalization helper (`_charclass.py`) shared.
- B8.08 🐞 DevMemory write path (AA7) routed through `scan()` before commit, per docstring claim — verify it actually is.

### B9. SPIFFE / AIMS / federation (6)
- B9.01 🔓 `identity_federation_bridge` — REGRESSION sequence number rejected; monotonicity enforced.
- B9.02 🔓 Pin-SHA256 mismatch on partner bundle → refuse swap.
- B9.03 🔓 `aims_partner_verifier` — Ed25519 sig over canonical `to_json()`; non-canonical encoder fails to verify (document).
- B9.04 🔓 Verify before sync → `PARTNER_NOT_SYNCED` (no verify against empty bundle).
- B9.05 🐞 Atomic verifier swap — no window where a half-rotated bundle verifies.
- B9.06 🔓 HTTPS_SPIFFE profile returns `PROFILE_UNSUPPORTED` (not a silent fail-open) until B.4 lands.

### B10. RCE / sandbox / SSRF / path (4)
- B10.01 🔓 Command allowlist: non-whitelisted executable refused; `shell=False` everywhere (no metachar injection) (S1).
- B10.02 🔓 SSRF: DNS-pinned httpx blocks the 10 internal networks; rebind after resolve blocked.
- B10.03 🔓 Sandbox RLIMITs (AS/CPU/NPROC/NOFILE/FSIZE) enforced; fork-bomb / fd-exhaust contained.
- B10.04 🔓 `_validate_path` on all 11 disk endpoints — `../` and absolute escapes blocked.

---

## C. Backend AI engine + services — 45 tests

### C1. LLM providers / routing (8)
- C1.01 🐞 `assign_model(role, complexity)` — complexity ≥9 routes to opus (gpt for architect), else default.
- C1.02 🐞 Provider fallback to dev-mode when no API key (echo/template), no crash.
- C1.03 🔓 API keys never logged / never returned in error envelopes.
- C1.04 🐞 `kernel_provider` KIM path streams tokens over VBus; backpressure handled.
- C1.05 🔓 `kernel_provider` calls E6 perf-counter lockdown before KIM_GENERATE (wiring intact).
- C1.06 🐞 `kv_prefix_cache` returns correct cached prefix; cache-key collision impossible across users.
- C1.07 ⚡ Router selection overhead negligible vs request.
- C1.08 ♻️ `src/efficiency.py` vs `backend/ai/llm/efficiency.py` — two efficiency modules; confirm distinct or unify.

### C2. Multi-agent orchestration (8)
- C2.01 🐞 LangGraph state transitions: Architect→Frontend→Backend→Tester→Reviewer ordering honored.
- C2.02 🔓 `guardrails.py` output validation blocks unsafe generated code (e.g. embedded creds, eval).
- C2.03 🐞 `context_manager` propagates auth/identity to sub-agents (no privilege drop or escalation).
- C2.04 🔓 `auth_injector` binds the right principal; cannot inject a different user's identity.
- C2.05 🐞 Agent execution-history ring buffer (`history_validator`) — forbidden multi-step sequence (read-SECRET→external-send) blocked.
- C2.06 🐞 Checkpoint/resume (`checkpoint_service`) restores agent state faithfully.
- C2.07 🔓 `model_integrity_watchdog` eviction mid-run aborts the agent (fail-closed), not continue on corrupt weights.
- C2.08 ⚡ Multi-agent end-to-end latency baseline.

### C3. RAG (6)
- C3.01 🐞 REFRAG / CLaRa compression round-trips without dropping relevant chunks.
- C3.02 🐞 EntropyGuard dedup doesn't remove distinct-but-similar docs.
- C3.03 🔓 RAG retrieval respects tenant boundary — no cross-org document leak (S1).
- C3.04 🐞 Multi-hop (FB-RAG) terminates; no infinite hop loop.
- C3.05 ⚡ Cache-Augmented Generation hit path latency vs cold.
- C3.06 🐞 Spotlighting markers survive into the prompt (untrusted-data demarcation).

### C4. Codegen (4)
- C4.01 🐞 Generated code passes syntax validation per language (HTML/TS/Python/SQL).
- C4.02 🔓 Generator output sanitized — no injected `eval`, no hardcoded secrets.
- C4.03 🐞 Large-spec generation doesn't truncate silently.
- C4.04 ♻️ `AGENT_PROMPTS` (multi_agent.py) vs `DEFAULT_PROMPTS` (agents_routes.py) — two prompt sources; confirm intentional, flag drift.

### C5. Services — manifest / policy / compliance / packaging (12)
- C5.01 🐞 `intent_manifest_builder` v2/v3 envelope schema valid; required fields enforced.
- C5.02 🔓 `policy_override` — override requires admin + writes audit trail; REVIEW_REQUIRED fallback path.
- C5.03 🔓 `policy_override` Safe-Rollout gate cannot be bypassed by malformed score.
- C5.04 🐞 `compliance_store` SQLCipher persistence — events durable across restart; encrypted at rest.
- C5.05 🔓 `compliance_store` — tampering with a stored event detectable (integrity).
- C5.06 🐞 `vpacker` enforces 512 KB total / 65 KB per-file; oversized .vpk rejected.
- C5.07 🔓 `vpacker` SystemManifest syscall allowlist is honored by `native_deploy_service` sandbox.
- C5.08 🔓 `native_deploy_service` app sandbox — fs/IPC isolation between two deployed apps.
- C5.09 🐞 `integrity_worker` async SHA-384 — streaming fidelity, no partial-hash race.
- C5.10 🔓 `eu_compliance` right-to-erasure actually purges across stores (no orphan copy).
- C5.11 🐞 `regional_policy` geo-fence / residency — request from disallowed region blocked.
- C5.12 ♻️ `agent_service.py` vs `agent_orchestration.py` vs `agent_orchestrator.py` — three agent-lifecycle modules; high DUP risk, map and consolidate.

### C6. Cache / quota (4)
- C6.01 🐞 `semantic_cache` returns semantically-equivalent hit; no stale wrong answer.
- C6.02 🔓 Cache isolation per user/tenant (no cross-user cache hit) (S1).
- C6.03 🐞 `quota_manager` enforces token/cost ceiling; over-quota request refused.
- C6.04 🐞 Quota counter atomic under concurrent requests (no underflow).

### C7. Telemetry / observability (3)
- C7.01 🔓 OTel GenAI spans don't record PII / prompt contents in attributes.
- C7.02 🐞 `observability.track_request` cost/latency persists to DevMemory across restart.
- C7.03 🐞 Error recording (`record_error`) doesn't swallow the original exception.

---

## D. API + V-Core + persistence — 35 tests

### D1. Auth / middleware (8)
- D1.01 🔓 Every non-public endpoint requires Bearer token; missing → 401 (S1).
- D1.02 🔓 CSRF middleware (W3.3) — mutating request without valid token → 403.
- D1.03 🔓 API-key middleware — invalid/expired key rejected.
- D1.04 🔓 Rate-limit middleware — burst beyond limit → 429; per-principal isolation.
- D1.05 🐞 `AuthenticatedUser` dataclass used everywhere (0 `user: dict`, 0 untyped `Depends`) — static assertion.
- D1.06 🔓 Content-size limit (100KB/500msg) — oversized body rejected before parsing.
- D1.07 🔓 Security headers / CSP present, no `unsafe-eval`.
- D1.08 🐞 Middleware ordering correct (auth before handler, CSRF before mutation).

### D2. Route-level (8)
- D2.01 🔓 `chat_routes` — semantic_firewall 4xx fires before any LLM call (re-verify B8.03 at route level).
- D2.02 🔓 `codegen_routes` — generated code not executed server-side.
- D2.03 🔓 `agents_routes` — agent creation scoped to caller's org.
- D2.04 🔓 `billing_routes` — user cannot modify another user's subscription/credits (IDOR).
- D2.05 🔓 `kernel_routes` — privileged kernel ops gated by role.
- D2.06 🐞 `metrics_routes` — no sensitive data in public metrics.
- D2.07 🔓 `compliance_routes` admin endpoints — admin-only; read-only where documented (leak_detector, AI-BOM).
- D2.08 🐞 Streaming endpoints (SSE) close cleanly on client disconnect; no goroutine/task leak.

### D3. V-Core RBAC / IDOR (6)
- D3.01 🔓 `control_plane` RBAC — role without permission denied at service layer, not just UI.
- D3.02 🔓 Cross-org access: user in org A cannot read org B entity/record (S1).
- D3.03 🔓 Field-level security — restricted field not returned to unauthorized role.
- D3.04 🐞 API-key scope — key limited to its granted permissions.
- D3.05 🔓 Audit-log append-only; `history_validator` blocks backfill/edit.
- D3.06 🐞 SSO/OIDC token validation — expired/wrong-issuer rejected.

### D4. business_core / workflow_engine (5)
- D4.01 🐞 Dynamic entity validation — type/required/formula fields enforced on write.
- D4.02 🐞 Relation integrity — deleting a referenced record handled (cascade/restrict defined).
- D4.03 🔓 Workflow trigger (webhook) — signature verified; forged webhook rejected.
- D4.04 🐞 Workflow loop/condition — no infinite loop; max-iteration guard.
- D4.05 🐞 Workflow action retry/backoff — idempotent on retry (no double-charge / double-send).

### D5. Convex / repositories / circuit breaker (5)
- D5.01 🔓 `db/convex.py` — CONVEX_URL mandatory in prod; HTTPS enforced; in-memory fallback disabled in prod.
- D5.02 🐞 Retry/backoff on 429/502/503/504; OCC conflict surfaced not swallowed.
- D5.03 🐞 Circuit breaker opens after threshold; half-open recovery works.
- D5.04 🔓 `requireProjectOwnership` enforced on project/file/chat/memory/builds/yjsUpdates (Convex-side IDOR).
- D5.05 🐞 Convex indexes (`by_document_seq`, `search_apps`, `by_owner`) present and used (no full scan).

### D6. Misc (3)
- D6.01 🐞 `/health` reports each service truthfully (down service → unhealthy, not green).
- D6.02 🔓 `clerk_webhook` signature verification; replay rejected.
- D6.03 ♻️ Mounted vs available routes (26 listed, ~45 in registry) — flag dead/unmounted route files.

---

## E. Frontend (Next.js / Convex) — 22 tests

- E1.01 🔓 All 11 API-calling hooks send `Authorization: Bearer` header (regression).
- E1.02 🔓 Convex `useQuery`/`useMutation` calls go through `requireProjectOwnership` (no client-trusted IDs).
- E1.03 🐞 `useChat` model selection passes through; streaming renders incrementally.
- E1.04 🐞 `useVCore` optimistic update rolls back on mutation failure.
- E1.05 🔓 No secret (Clerk secret, Convex deploy key) bundled into client JS.
- E1.06 🔓 XSS: user-supplied content in chat/builder rendered escaped (no `dangerouslySetInnerHTML` on untrusted) (S1).
- E1.07 🔓 CSP from backend enforced; inline-script violations reported.
- E1.08 🐞 API proxy rewrite (`next.config.js`) targets correct backend; no open proxy to arbitrary host.
- E1.09 🔓 Auth token stored httpOnly / not in localStorage where avoidable.
- E1.10 🐞 `VoiceInput` — mic permission denial handled gracefully.
- E1.11 🐞 A11y: WCAG AA contrast + ARIA roles on key components (Navigation, forms).
- E1.12 🐞 Vision pixel-sync overlay opacity control bounded [0,1].
- E1.13 🐞 Error boundary catches render errors; no white-screen on API 500.
- E1.14 🔓 Clerk publishableKey-missing path degrades safely (no crash, no auth bypass).
- E1.15 ♻️ Hooks call FastAPI which calls Convex — flag the documented double-hop; candidates for direct Convex migration.
- E1.16 🐞 Form validation client-side mirrors server contract (no client-only validation gap).
- E1.17 ⚡ Bundle size / route-level code-split sane (no monolithic bundle).
- E1.18 🐞 `ClientLayout` hydration — no server/client markup mismatch.
- E1.19 🔓 File upload (builder) — type/size validated client + server.
- E1.20 🐞 Metrics/health dashboards handle empty/loading/error states.
- E1.21 🐞 i18n / RTL (voice 11 languages) — layout doesn't break for RTL.
- E1.22 ♻️ Dead components — scan for unimported components in `components/`.

## F. Desktop (Tauri / Rust) — 18 tests

- F1.01 🐞 `qemu.rs` start → health → stop lifecycle; orphaned QEMU child reaped on app exit.
- F1.02 🔓 QEMU launched with the documented hardening flags (no extra network/device exposure).
- F1.03 🐞 Kernel binary SHA verified before boot (matches CLAUDE.md canonical hash).
- F1.04 🐞 QEMU crash → app reports error, doesn't hang.
- F1.05 🐞 `vbus/` CRC32C matches kernel implementation (cross-impl KAT).
- F1.06 🔓 `vbus/` HMAC-SHA256 frame auth matches kernel; tampered frame rejected client-side too.
- F1.07 🔓 Per-session key exchange — Rust client never reuses a session key.
- F1.08 🐞 Async Unix socket client handles partial reads / reconnect.
- F1.09 🐞 All 22 VBus commands have Rust-side encode/decode; round-trip test.
- F1.10 🔓 `warp.rs` mmap zones (4×16MB) — bounds-checked; no OOB write into neighbor zone (S1).
- F1.11 🐞 Warp Drive zone ownership matches kernel ACL (agent can't write foreign zone).
- F1.12 🐞 mmap cleanup on disconnect — no leaked shared-memory mapping.
- F1.13 🔓 `commands.rs` `#[tauri::command]` handlers validate inputs (no path/arg injection into QEMU).
- F1.14 🔓 `load_model` command — model path confined; can't load arbitrary host file.
- F1.15 🐞 `state.rs` AppState concurrency — no data race on QemuManager/VBusClient (run under `cargo test` + TSan if available).
- F1.16 🐞 IPC error surfaced to frontend with structured error, not panic.
- F1.17 ⚡ VBus round-trip latency from desktop client.
- F1.18 ♻️ `desktop/src-tauri/tauri.conf.json` is a long-standing dirty file — decide commit vs restore; flag config drift across branches.

## G. Infra / supply-chain / compliance — 18 tests

- G1.01 🔓 `sigstore_v3_bundle.py` — Signer/Verifier round-trip; tampered artifact fails verify (S1).
- G1.02 🔓 Bundle binds the correct SHA-256/384; swapped artifact detected.
- G1.03 🐞 Dev-tier CN (`VOS3-DEV-NOT-FULCIO`) clearly flagged, not mistaken for prod Fulcio.
- G1.04 🔓 ECDSA P-256 signature verify rejects bad sig.
- G1.05 🐞 `scripts/verify_release.sh` returns PASS only on the genuine GA artifact.
- G2.01 🔓 `rekor_v2_log.py` RFC-6962 Merkle inclusion proof verifies; forged leaf fails.
- G2.02 🐞 Inclusion proof for all logged entries (75/75 claim) reproducible.
- G2.03 🐞 Log append is consistent (consistency proof between two tree sizes).
- G2.04 🐞 Tampered intermediate hash detected.
- G3.01 🐞 `build_sbom.py` CycloneDX 1.5 schema-valid; component count matches (~1374).
- G3.02 🔓 Embedded VEX statements reference real CVEs; no fabricated entry.
- G3.03 ♻️ SBOM not stale — regenerate and diff against committed; flag drift.
- G4.01 🐞 Reproducible build: rebuild with `SOURCE_DATE_EPOCH=1700000000` → identical SHA (or documented divergence per handover lock).
- G4.02 📐 CLAUDE.md canonical kernel SHA matches actual `kernel/build/vos3.elf` (currently DIVERGED per lock — assert the lock's documented hash, not the stale one).
- G4.03 🐞 `--build-id=none` + `-ffile-prefix-map` flags present in Makefile.
- G5.01 🔓 4 Z3 proofs (sched_core, egress_policy, ai_oom, merkle_inclusion) still return UNSAT against current kernel.
- G5.02 📐 `EU_AI_ACT_COMPLIANCE.md` Article 73 timeline (2026-08-02) consistent across all docs that cite it.
- G5.03 ♻️ Moat count (49/80) consistent across CLAUDE.md, handover lock, and AGENT_ERA docs (flag any inflated count).

## H. Cross-cutting — duplication / dead code / static / perf — 17 tests

- H.01 ♻️ `backend/rag/` ↔ `backend/ai/rag/` and `backend/agents/` ↔ `backend/ai/agents/` — confirm root dirs removed (Sprint 14.3) and zero orphaned imports remain.
- H.02 ♻️ Three agent-orchestration modules (C5.12) — measure code overlap, propose single owner.
- H.03 ♻️ Two capability tables (B5.10) and two efficiency modules (C1.08) — dedupe or document.
- H.04 ♻️ Sanitization stack (AA1/AA2/semantic_firewall/dual_llm) — produce one diagram of the layering; find redundant scans.
- H.05 ♻️ Dead code: `vulture backend/` (or `ruff` unused) — list unreferenced functions/classes.
- H.06 ♻️ Unmounted route files (D6.03) — decide keep/delete; remove dead routers.
- H.07 🔓 `bandit -r backend/` — triage all findings; no `shell=True`, no `pickle`, no `eval` on untrusted.
- H.08 🔓 `pip-audit` / `npm audit` / `cargo audit` — no known-vuln dependency unpatched.
- H.09 🐞 `ruff check .` + `black --check` clean (CI-debt regression).
- H.10 🐞 `mypy` / type-coverage on security-critical modules (security/, core/security/).
- H.11 🔓 Secret scan (`gitleaks`/`trufflehog`) — no committed API keys/tokens (note: CLAUDE.md claims API-key purge).
- H.12 ♻️ Frontend `npm run lint` + unused-export scan (E1.22).
- H.13 🐞 `cargo clippy` on desktop — no warnings on unsafe/mmap code.
- H.14 ⚡ Full backend test-suite wall-clock — confirm the CSRF/hang stabilization holds (no return of the 6h stall); the 267 known-failing legacy tests are tracked, not regressed further.
- H.15 📐 SDK header contract: `vos3_sdk.h` / `VOS3_SDK.h` syscall stubs match kernel syscall numbers (drift = silent ABI break).
- H.16 ♻️ Stale doc references — grep docs for removed paths/old SHAs; the handover lock's TTL (2026-07-15) and the CLAUDE.md handover lock TTL (2026-05-31) conflict — reconcile.
- H.17 🔓 Fail-open audit: grep all `require_*()` gates for any early `return True` / `except: pass` that bypasses enforcement (the single highest-value security sweep).

---

## Execution notes

1. **Highest-value first**: H.17 (fail-open sweep), B-section gate tests, and A3/A6 (VBus + kernel security) are the S1 concentration — run these before the long tail.
2. **macOS vs Linux**: kernel eBPF LIVE tests (A6.01–03, B3.02) only execute on a Linux ≥ 5.17 runner; on macOS they must skip cleanly (assert the skip, don't assert pass).
3. **Tooling per layer**: kernel — `kernel/tests/*` + QEMU harness; backend — `pytest` (security/ + services/ hard-gate); frontend — `vitest` + Playwright; desktop — `cargo test` (+ TSan/clippy); infra — the existing `scripts/verify_release.sh` + Z3 proof scripts.
4. **Honesty constraint**: where a gate's docstring documents a ceiling (e.g. semantic_firewall Stage-1 misses paraphrases, BBS+ stub is not privacy-complete), the test asserts the *documented* behavior — it must NOT claim coverage the code doesn't have.
