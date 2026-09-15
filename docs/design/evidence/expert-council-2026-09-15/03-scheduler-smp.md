# Scheduler and SMP execution

Reviewer: `/root/rust_upgrade` (one actual agent, architecture/build/hardware council track). Source baseline: `32187957757ab57d7820d0a63fa62c409003b126`. These are seven specialized reviews by one reviewer, not seven independent agents.

**P1 — AP user execution is gated, not normal-release concurrency.** [kernel/src/sched/scheduler.c](../../../../kernel/src/sched/scheduler.c) uses `NATIVE_SMP_TEST` around the AP scheduling path. `native-smp-qualification.md` records CPL3 computation on secondary CPUs, with disabled-gate negative controls that remain on APIC 0. This supersedes the earlier reconnaissance in `native-ap-workload-gap.md`; do not repeat its initial lack-of-AP-evidence conclusion as the current result. Normal scheduling remains a separate configuration.

**P1 — Distinct CPU observations do not prove simultaneous shared-VM safety.** The SMP report explicitly excludes concurrent memory revocation. `native-tlb-qualification.md` tests replacement of kernel translations with per-CPU acknowledgements and a deliberately omitted invalidation control. Those primitives do not demonstrate that every user unmap, protection change and COW mutation uses the required cross-CPU transaction. Require an overlapping workload with acknowledged revocation, page-lifetime witnesses and negative controls before enabling unrestricted shared-VM SMP.

**P2 — Refresh lifecycle-dependent tests before broadening claims.** Current ownership changes affect clone, exec, CPU root pins and deferred reclamation. Keep fresh final matrices pending rather than interpreting development logs as qualification. Reviewed scheduler gating and existing report contracts; did not re-audit all scheduler locks, migration/FPU state or stress every interleaving.
