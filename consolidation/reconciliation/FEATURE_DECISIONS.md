# Reviewed capability decisions

Comparison date: 2026-09-13. Paths below are relative to the named donor or to
canonical `vos 5`. This step selects integration targets and identifies work;
it does not copy implementations or certify runtime support.

## Native operating system — priority integration decisions

| Capability | Donor evidence | Selected implementation and disposition | Open acceptance gate |
|---|---|---|---|
| BIOS/UEFI firmware handoff | Donor `kernel/src/boot/*`; canonical `multiboot2_entry.S`, `multiboot2_parse.c` | Keep VOS 5 corrected magic preservation, firmware map/RSDP parsing and 32 MiB load layout. Older baseline entry was replaced. | Physical firmware matrix, persistent installation, malformed-input containment. |
| AP startup and scheduling | Canonical `kernel/src/arch/x86_64/smp.c`, `trampoline.S`, `kernel/src/sched/scheduler.c` | Keep VOS 5 private AP root, NXE, CPU-local syscall MSRs and readiness handshake; do not restore guessed APIC enumeration or premature readiness. | AP user-space load, timeout/retry resource lifecycle, x2APIC. |
| Process page tables and entry isolation | `consolidation/kernel-memory-boundaries.json`, canonical VMM/KPTI/entry files | Current canonical per-process full/restricted roots replace older boot-global roots. Preserve current changes. | Restricted roots still expose broad supervisor maps; full KPTI/TLB/NMI correctness remains open. |
| PCID optimization | VOS3 `kernel/src/mm/pcid.c` explicitly says its helper has no production callers and assumes shared address space | Reject this module as a drop-in replacement. Keep canonical CR3 switching; design a process-aware PCID implementation against it. This rejects an implementation shortcut, not the optimization requirement. | Tagged TLB ownership, global mappings, invalidation and SMP tests. |
| AI per-slot OOM | VOS3-Cyber `kernel/src/mm/ai_oom.c` | Select its overflow-safe quota predicate as a review/port candidate. Module is absent; it refers to donor globals and declarations and cannot simply be copied. | Port into actual canonical allocation path; quota, overflow, reclamation and isolation tests. |
| Kernel egress policy | VOS3-Cyber `kernel/src/net/egress_policy.c`, `vos3_net_egress_allowed` | Candidate policy logic, not a complete firewall. Code checks blocklist before private ranges and ignores the port argument. Keep canonical network stack and integrate scoped deny-precedence at dispatch. | Prove connect/send paths consult it; tenant/workspace boundaries and explicit public-egress scopes. |
| PCIe ECAM | vos/vos4 and track-2 `kernel/src/drivers/pci_ecam.c`; canonical Makefile selects `src/arch/x86_64/pci_ecam.c` | Select canonical ECAM as the single implementation. Port/review donor legacy and extended capability walkers as additions; do not link two ECAM implementations. Donor file's absence is not absence of all ECAM support. | Validate capability-chain bounds/loops, segments and actual device enumeration. |
| Bare-metal preflight | VOS3 `kernel/src/boot/baremetal_preflight.c` | Candidate source for early capability checks; retain current boot path. Its feature assumptions must not silently narrow the user’s hardware objective. | Feature-specific behavior, explicit unsupported hardware report, tested matrix. |
| Hyper-V transport | VOS3 `kernel/src/drivers/hyperv_vsock.c` | Do not select donor loopback/default-success behavior as production transport. Keep native driver tree; transport requires a real implementation behind one authenticated VBus contract. | Actual Windows host transport, no fabricated send success, isolation and reconnect tests. |
| Attestation | VOS3 `kernel/src/arch/x86_64/hal_attestation.c` | Reference-only candidate: source returns SOFTWARE_STUB/HW_REJECTED for unsupported paths. Do not claim attestation based on module presence. | Real trusted measurements/key provisioning and verifier integration. |
| Portable VBS/eBPF hooks | VOS3-Cyber `kernel/src/arch/platform_hooks.h`, `portable/*` | Preserve interfaces as references; `win_vbs_stub.c` explicitly declares a non-working scaffold returning unsupported. Not selected as a working Windows port. | Platform-specific implementation and actual host tests. |
| Native MMR audit | VOS3 `kernel/src/crypto/mmr.c`, `include/vos/mmr.h` | Additive integration candidate, paired with backend translator/ledger. Retain current crypto until bounds, format and licensing are reconciled. | Cross-language record bytes/root agreement, persistence, replay/tamper tests. |
| libc and user programs | Filtered ledger: canonical content retained for selected libc/user paths | Keep canonical musl/user baseline. Old inventory's large musl divergence included generated outputs; do not initiate a wholesale libc replacement from that count. | Program ABI tests, fork/exec/signals, build-option invalidation. |

## Hosted AI, identity and product capabilities

| Capability | Donor evidence | Selection/disposition | Remaining work |
|---|---|---|---|
| Agent handoff/provenance/sanitation | vos/vos4 agent files and `agent-boundaries.json`; canonical handoff tests | Already merged into VOS 5, with later payload/run binding fixes. Keep these over earlier weaker donor behavior. | Full pipeline and scoped egress integration; historical 293/62-test runs are not a current full release gate. |
| Fleet discovery and borrowed inference slots | VOS3 `backend/services/fleet_manager.py`: announce/heartbeat/peer failure/borrow_slot/return_slot | Missing candidate. Select canonical P2P/identity services as the owning subsystem; port fleet functionality into it rather than start a parallel trust model. | Authenticate peer discovery and slot leases, tenant isolation, cancellation/recovery, no forced cloud. |
| Evidence ledger and MMR proofs | VOS3 `backend/services/evidence_bundle.py`, `evidence_bundle_mmr.py`, `kernel_mmr_translator.py` | Missing additive candidates. Select one canonical audit API with interchangeable storage/proof implementation, not duplicate incompatible ledgers. | Signer/key source, durability, exact native record codec and license review. |
| Blueprint deployment | VOS3 `frontend/convex/blueprints.ts` plus locally modified `schema.ts` | Missing capability and unmerged local schema addition. Preserve blueprint/blueprintDeployment tables as a coupled port candidate. | Authorization, project/org ownership, migration and canonical offline storage equivalents. |
| Builder snapshots | VOS3 `frontend/convex/builders.ts`: snapshot/latestSnapshot/listSnapshots/getSnapshotByNode | Candidate missing module; use canonical product model rather than an independent schema. | Tenant-scoped writes/reads and snapshot persistence. |
| Idempotent actions and cleanup | VOS3 `frontend/convex/idempotency.ts`, `crons.ts` | Missing candidate. Keep canonical webhook idempotency but do not equate it with a generic operation lifecycle. | Atomic get/create/complete/fail, retries, expiry and exactly-once external dispatch. |
| Organization ownership | Canonical `frontend/convex/organizations.ts` and `frontend/test/convex-organizations.test.ts` | Retain VOS 5 membership/owner fixes; old donor behavior is replaced. | Broader auth/storage integration and frontend failures. |
| Convex generated helpers | Donor `_generated/*.js`/`.d.ts`, canonical generation script | Select real generated output from the canonical schema/build. Missing old generated filenames are regeneration/migration work, not a separate lost product capability. | Reproducible codegen and correct type imports. |
| Windows C# SDK | VOS3 `sdk/vos-windows-bridge/` including `HyperVSocket.cs` | Missing Windows-specific candidate paired with transport implementation; do not select stub guest transport to make host tests appear green. | Compile/run on Windows and authenticated round trip. |
| MCP Python/JS SDK additions | Donor `sdk/vos-mcp-python`, `sdk/vos-mcp-js` and server variants | Keep canonical server contract; port missing SDK methods only against its verified protocol. | Cross-version auth, tool schema and transport compatibility. |
| Local vault/pool and EDR connectors | Variant review entries for `backend/core/repositories/*`, `backend/core/security/*` | Retain canonical identity/storage boundary; unretained source variants need method-level review. Symbol list is a review index, not proof a capability is absent everywhere. | SQLCipher requirement, key lifecycle, revocation and host-specific behavior. |
| Desktop shell | `desktop/src-tauri/*` variants | Select VOS 5 shell as integration target, preserve donor variants in ledger. | Host startup/shutdown, IPC and Windows/macOS/Linux execution. |
| CI and deployment | Missing `.github/workflows/*`, infrastructure/runner variants | Select AiOS-specific workflow configuration to be built from relevant checks. Donor repository triggers are not automatically applicable. | Clean checkout build, backend/frontend/native checks and dependency/provenance gates. |

## Local changes that must not disappear

Nine tracked source files differ from their respective HEADs:

- VOS3 `frontend/convex/schema.ts`: adds `blueprints` and `blueprintDeployments`.
  Bytes are not retained in the canonical selected schema. Keep as an open coupled migration.
- VOS3 `frontend/middleware.ts`: removes Node crypto/Buffer for nonce generation
  and updates Clerk protection invocation. Canonical already awaits `auth.protect()`
  but still uses Node crypto/Buffer. Partially overlapping behavior; do not mark
  the full local change merged or copy it without checking the actual runtime.
- VOS3 `frontend/package.json` and lockfile: dependency/override changes. Canonical
  versions differ (including Clerk/Next). Keep current canonical dependency line
  as target; resolve vulnerabilities and compatibility before selecting exact versions.
- VOS3 `frontend/next-env.d.ts`, `frontend/tsconfig.json`: Next-generated typing and
  JSX/include/target changes. Reconcile with canonical framework/config, not by timestamp.
- vos/vos4 `frontend/next-env.d.ts`: exact local content is already retained.
- vos/vos4 `frontend/tsconfig.json`: local variant differs; compare compiler semantics.
- vos.v1 `docs/release_notes/vOS_Cloud_Enclave_Sovereign_Spec.md`: modified working-tree
  content was preserved by the baseline import (exact bytes found in canonical).

All included untracked files are separately enumerated. They are not assumed to
be intentional product features. VOS-Cyber-Standard has no resolvable HEAD, so its
2,089 included files are a history-unavailable snapshot, not a clean repository.
The six relocated worktrees have readable HEAD metadata and no included local
content changes; their committed differences still appear in the variant ledger.

## Licensing and dependency boundaries

VOS3 candidate files carry `LicenseRef-VOS3-Sovereign-1.0`; retain the applicable
license and provenance before integration. Do not relabel them under the baseline
license. Limine dtc/tinf source differences are third-party build-dependency review,
not hundreds of first-party OS features. Build caches, compiled outputs, credentials,
session/runtime data and symlinks are explicitly listed as exclusions; no exclusions
were silently merged or declared equivalent.

No donor source tree was edited and no production implementation was replaced in
this comparison step. Every included path has a disposition in `file-ledger.json`;
all 43 components have a selected integration target in `COMPONENTS.md`. Open
variants are retained as work, not converted into “done” by the selection itself.
