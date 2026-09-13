# VOS 5 core requirements

Authority: the user's consolidation instructions and the supplied
[vOS Technical Brief](vOS_Technical_Brief.pdf), especially pages 3–10.
The PDF's SHA-256 is
`c4a209477383e00f660699707fa83de2ef19294bba8284da3be6d7483644207e`.
This document records the requirements to preserve during consolidation; it
does not certify that they have all been implemented or validated.

The final project belongs in `vos 5/`, in one new Git repository. Its purpose
is a sovereign AI operating system, including a native kernel. A hosted web
application alone does not satisfy the objective.

## Invariants and required evidence

| ID | Required principle | Evidence required before claiming completion |
|---|---|---|
| CORE-01 | The custom freestanding kernel can be the operating system on a machine with no Linux or other host OS. | Boot an installable BIOS/UEFI image into the real kernel and run native user-space and AI workloads. An ELF build or hosted API test is insufficient. |
| CORE-02 | Hosted mode coexists with Windows, macOS and Linux; the native and hosted modes share the canonical kernel and authenticated VBus protocol. | Build/run each host adapter and validate startup, shutdown, frame authentication, device transport and resource isolation. Windows must be tested with its actual process/socket/shared-memory APIs. |
| CORE-03 | Broad PC compatibility is a design requirement, including older systems and enterprise hardware. | A published architecture/CPU/RAM/firmware/storage/network matrix, with actual boot and workload evidence. Feature-detect optional instructions and accelerators. Do not claim all PCs from one VM or silently equate x86_64 coverage with all PC architectures. |
| CORE-04 | AI execution is local-first and can operate without a cloud service. | Air-gap execution with outbound networking blocked; kernel inference where the model fits and explicit supported local runtimes elsewhere. No forced cloud fallback, cloud authentication or telemetry in the offline workflow. Cloud use requires the appropriate manifest scope. |
| CORE-05 | Inference slots and tenant memory remain isolated; slot 0 is the privileged coordinator. | Adversarial cross-slot tests, read-only model mappings, KV/cache isolation, memory reclamation and concurrent workload tests in a booted kernel. Historical slot counts and model limits may increase only with corresponding allocation and bounds checks. |
| CORE-06 | Hardware security is detected and enabled where supported; software permission checks still enforce denial on every platform. | CPUID/CR validation and boot tests with and without optional features, plus W^X, stack, pointer and syscall isolation tests. Missing hardware protections must be reported explicitly; never report nonexistent TPM/SMAP/SMEP/attestation as active or bypass an authorization failure to keep a demo running. |
| CORE-07 | Manifest scopes, deny-precedence and PermissionGate govern actions. | Tests that denied/revoked/isolated apps cannot execute, cache invalidation takes effect immediately, and missing or malformed scope information fails closed. |
| CORE-08 | Filesystem and process resource boundaries contain untrusted apps. | Traversal, absolute/Windows/UNC/NUL paths, symlink-race and sandbox escape tests; CPU/RSS/FD/process/file quotas, concurrency limits and automatic isolation tested on each supported host. |
| CORE-09 | Cryptographic identity, tamper detection and encrypted state are real enforcement mechanisms. | Signed canonical workflow/approval bytes, pinned key identities, constant-time MAC checks and replay/tamper rejection. Fortress storage requires SQLCipher; a plaintext fallback does not satisfy it. Test keys and developer certificates cannot be represented as production trust anchors. |
| CORE-10 | Human approval gates sensitive host actions before dispatch. | Integration tests for scope checks, amount/rate limits, pending approval, signature validation, revocation and exactly-once dispatch into the host executor, with durable audit records. |
| CORE-11 | P2P discovery and synchronization need no cloud and maintain workspace boundaries. | Two-node tests covering signed beacons, mutual authenticated X25519 handshake, workspace mismatch, malicious/replayed peer messages and synchronization persistence. |
| CORE-12 | Efficiency is demonstrated at the system level. | Reproducible latency, memory, throughput and cost measurements across hardware tiers and representative model sizes, with isolation/security enabled. Historical cost-saving percentages and benchmark claims are not current evidence. |
| CORE-13 | One integrated backend, frontend, kernel, desktop shell and storage/identity model replace the parallel source trees. | File/capability reconciliation across all eleven inputs, tests for retained unique features and local modifications, reproducible installation, one startup path and end-to-end workflows. Old repositories must not be required at runtime. |
| CORE-14 | Release claims must match verified evidence. | Reproducible artifacts, dependency/security checks, build provenance, signed release procedures, and a clear distinction between host unit tests, simulated hardware, actual hardware and independent audit. The superlatives in the product objective require comparative evidence, not labels. |

## Interpretation of the historical brief

The brief specifies a minimum hosted hardware tier and an x86_64 bare-metal
tier; it does not prove support for every existing PC. The user's broader
compatibility objective remains open until its hardware coverage is established.
Model names, ceilings, binary hashes, version numbers and performance figures in
the historical document are snapshots. Preserve their underlying security and
sovereignty properties while updating implementation and measuring current results.

SQLCipher, real signing-key provisioning, native Windows execution and physical
hardware validation are distinct gates. Passing macOS tests does not satisfy them.

## Current evidence and remaining work

- The canonical code is in `vos 5/`; source repositories remain outside it.
- A file-level baseline import manifest records 5,072 imported paths from
  `vos.v1`. Other sources still require reconciliation; importing the baseline
  is not completion of CORE-13.
- The kernel builds in the final directory, including its space in the path.
  Generated embedded binaries now belong to each build directory, so audit
  flavors do not rewrite source or clean another build's objects.
- 539 backend audit tests and 293 agent-boundary/pipeline tests passed after
  the initial integration. These were run with the source project's Python
  environment; repeat the relevant gates in VOS 5's independent environment.
- The imported agent handoff implementation was strengthened to bind the
  actual consumed architecture to the signed record and an independent active
  run. Missing records, stale emissions and cross-run records reject before
  invoking a downstream agent.
- The independent Python environment passed 62 focused handoff, expander and
  prompt-wrapping tests, and its installed dependencies pass `pip check`.
- After the CPU-local syscall-entry changes, the complete 539-test backend
  audit suite passed in VOS 5's independent `.venv` (103.69 seconds). This
  does not qualify native user-space boot or multiprocessor operation.
- The native ISO now reaches actual user-space setup through BIOS and x64
  UEFI in QEMU TCG, including two CPUs on UEFI and four on BIOS. A
  disposable two-CPU VM completes the keyboard-driven wizard. Concurrent
  user workloads on secondary CPUs remain unqualified. See [native boot status](NATIVE_BOOT_STATUS.md) for artifact hashes,
  limited hardware coverage and the remaining isolation/SMP gates.
  Full native user-space operation, device support, the hosted product flows,
  remaining legacy features and release qualification are still open.
