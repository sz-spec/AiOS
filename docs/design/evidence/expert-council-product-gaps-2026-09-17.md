# Product, hardware and QA council review — 2026-09-17

Reviewer: `/root/rust_upgrade`, one agent acting in three specialties. Read-only
source review; this report is the only file created for this task. Scope:
README, PROJECT_INTRO, DEPLOYMENT, PLATFORM_GUIDE, CORE_REQUIREMENTS,
reconciliation decisions and latest thread/memory/SMP evidence. This is not
an exhaustive implementation audit or a new hardware qualification.

## Evidence baseline and product positioning

[CORE_REQUIREMENTS](../CORE_REQUIREMENTS.md) explicitly requires a native OS
without Linux, actual Windows adapters, local-first operation and measured
hardware coverage. Its universal-PC/security/efficiency goals are requirements,
not demonstrated comparative claims.

The latest [repair qualification](native-thread-memory-repair-2026-09-17.md)
records 96 host tests and 57/57 native programs returning zero on one BIOS
QEMU CPU. Its [separate SMP matrix](native-thread-memory-repair-2026-09-17/smp/README.md)
passes BIOS/UEFI with one/four vCPUs. Both four-vCPU cases observe deterministic
CPL3 work on APIC IDs 0 and 1. These are different images and workloads:
57/57 does not extend to UEFI/SMP, and the SMP result does not prove concurrent
execution, fairness or execution on all four CPUs.

Both snapshots are based on `c1899b568a7974b46e31841c260cb0f7deb11f33` plus
patch SHA-256 `94f998d771bfc2fba99f596d4eaeaad9b533f8ddca5bc505cd3fc3491068c627`.
They include non-ignored untracked inputs in their individual manifests;
commit or patch identity alone is insufficient to reconstruct those inputs.
Build warnings and lack of bit-identical rebuild proof remain explicit.

## Claim/evidence gaps

1. **Conflicting readiness claims.** [README](../../../README.md) calls the
   project integration-in-progress, while [PROJECT_INTRO](../../PROJECT_INTRO.md)
   calls it enterprise-grade, promises production-ready generated code and
   reports 40–60% cost reduction. The reviewed qualification does not establish
   those product outcomes. Label historical claims or bind them to current
   end-to-end evidence and a reproducible comparative dataset.
2. **Stale negative claims also mislead.** README's TLB paragraph still says
   the COW/cognitive PTE collision is unresolved, while the linked lifetime
   stage describes its separation. Replace broad old status statements with
   precise remaining concurrency boundaries; do not erase unresolved remote
   revocation or lifetime limits merely because a related bounded gate passed.
3. **Operator path mismatch.** [DEPLOYMENT](../../DEPLOYMENT.md) describes
   GRUB and `vos3_installer.iso`; current [build_iso.sh](../../../infra/build_iso.sh)
   packages Limine/Multiboot2 and `vos5.iso`. [PLATFORM_GUIDE](../../PLATFORM_GUIDE.md)
   and [generate_dist.sh](../../../infra/generate_dist.sh) use the old ISO
   default. The guide explicitly limits converted disks to live-CD semantics,
   which must remain visible. Conversion success is not proof that Hyper-V
   boots that disk or that installed state persists.
4. **Windows support is selected work, not delivery.**
   [FEATURE_DECISIONS](../../../consolidation/reconciliation/FEATURE_DECISIONS.md)
   rejects default-success Hyper-V transport and nonworking VBS scaffolds;
   the C# bridge requires real Windows execution. Hosted coexistence and
   native dual boot need separate acceptance records.
5. **Hardware and security evidence is bounded.** No reviewed matrix certifies
   physical PC devices, firmware trust enrollment/revocation, DMA containment
   or persistent installation. [kpti.c](../../../kernel/src/arch/x86_64/kpti.c)
   still explicitly documents broad kernel/direct-map entries. EFI loader
   presence and `[KPTI] init: ready` output cannot certify Secure Boot or full
   speculative isolation.
6. **Consolidation is not semantic completeness.** The reconciliation ledger
   and component decisions identify retained, replaced and candidate features.
   Missing blueprint/snapshot/SDK/transport acceptance gates cannot be closed
   by counting copied files or compiling the selected base.

## Prioritized backlog with acceptance gates

These are proposed release gates, not results already achieved.

| Priority | Deliverable | Measurable acceptance |
|---|---|---|
| P0 | One current claim register | Every externally visible readiness, security, performance and platform claim links to a dated artifact/test or is marked planned; remove stale unresolved PTE wording without overstating remaining isolation. |
| P0 | Rehearsable build/deployment instructions | One clean checkout follows documented commands with no undocumented edits, produces the named ISO, and completes normal BIOS/UEFI smoke; legacy examples are labeled historical or corrected. |
| P0 | Stable release provenance | Record commit, dirty and untracked bytes, compiler/image identity and all artifact hashes; verify source unchanged; rerun full 57-program suite after production changes, preserving failures and thresholds. |
| P0 | Honest SMP boundary | Keep diagnostic enablement distinct; require full workload matrix and adversarial remote lifetime/revocation gates before normal SMP support is advertised. Retain observed CPU identities rather than equating online count with work. |
| P1 | Persistent bare-metal installation | Disposable blank and prepartitioned disks: install, remove ISO, reboot, verify written data, interrupt installation and recover. Compare actual disk writes with the approved partition plan. |
| P1 | Initial physical-PC support matrix | Publish exact CPU, firmware version, RAM, storage/controller, NIC and input devices for each supported model; proposed minimum 20 cold/warm boots and 24-hour storage/network workload per model, with zero corruption and recorded resets/timeouts. Expand only after these gates. |
| P1 | Secure Boot | On declared firmware, signed image boots; unsigned, modified and revoked images fail; key rotation and failed-update recovery preserve the intended trust policy. Disabling Secure Boot is a separate unsupported-security mode, not a passing workaround. |
| P1 | Windows hosted mode | Real Windows build and authenticated bridge round trip, startup/shutdown/reconnect, denied/replayed requests and resource isolation; report actual Windows editions and transport used. |
| P1 | Windows dual boot | Disposable Windows disks with one/two ESPs and one/two drives: retain existing boot files/data, verify both OS boots and update/uninstall recovery; separately record encryption/recovery behavior. No production-disk experiment implied. |
| P1 | Eleven-source feature closure | Every selected unique capability/local change has an implementation disposition and behavioral acceptance test; unresolved candidates remain listed, with no file-copy equivalence claim. |
| P1 | Offline local-first workflow | Block outbound networking and execute the supported local AI workflow without cloud auth/fallback; preserve denial and audit evidence. Existing network-enabled QEMU tests do not establish this. |
| P2 | Comparative efficiency claim | Versioned tasks/models/hardware and quality criteria, repeated measurements with security enabled; publish latency/cost distributions and baseline. Retain 40–60% only if reproduced. |
| P2 | Binary reproducibility and enterprise rollout | Two independent clean builds compare artifact bytes with differences explained; signed update/rollback, staged deployment, privilege checks and audit trails pass failure-injection tests. |

## Recent engineering sources: 2026-08-18 through 2026-09-17

Search performed 2026-09-17, date-bounded to this window. Dates below are
publication/post dates, not crawl dates or user registration dates. Forum
reports are primary observations/opinions, not established root causes or
proof of a vOS defect. Recommendations below are our inference. Search did
not justify prescribing third-party firmware changes to users.

- **2026-08-18 — [IGEL Dual Boot 2.0.1 release notes](https://kb.igel.com/igel-business-continuity-disaster-recovery/current/igel-os-dual-boot-2-0-1-release-notes).**
  Official vendor release specifies Windows 11/UEFI prerequisites and
  permission-controlled emergency transitions. Inference: enterprise
  coexistence needs recovery, authorization and return workflows, not only
  a bootable alternate image. This does not endorse its cloud dependencies.
- **2026-08-19–20 — [Fedora ESP action-summary discussion](https://discussion.fedoraproject.org/t/is-it-expected-to-delete-to-format-the-windows-efi-for-manual-dual-boot-installations/199753).**
  Users report misleading format-versus-mount labels and later test an
  improved installer. Inference: verify the UI action plan against disk
  mutations, including two-ESP layouts; a reassuring wizard alone is weak
  evidence. Post dates/content were verified in the opened discussion.
- **2026-08-25 — [Duplicate Fedora EFI files and UEFI entries](https://discussion.fedoraproject.org/t/duplicate-fedora-efi-files-and-uefi-bios-entries/200295).**
  A user reports duplicated directories/entries after manual relocation.
  Inference: include idempotent installation and boot-entry cleanup checks.
  Date/content verified in indexed primary-forum text.
- **2026-09-02 — [Black screen after updates with Secure Boot/TPM configuration](https://discussion.fedoraproject.org/t/black-screen-after-automatic-reboot-following-updates/201131).**
  A dual-GPU laptop owner reports update/reboot failure with encrypted storage
  and Secure Boot configured. Causation is not established. Inference: qualify
  the combined update, graphics and disk-unlock path, without treating
  disabled Secure Boot as equivalent success. Date/content verified in
  indexed primary-forum text.
- **2026-09-03 — [ASUS GX10 firmware watchdog report](https://forum.proxmox.com/threads/pve-arm64-on-asus-gx10-gb10-hard-reset-every-20-minutes-fix-is-loading-sbsa_gwdt.186164/).**
  A firsthand report attributes periodic reset to an unserviced firmware
  watchdog. This is ARM hardware, not evidence of an x86 vOS bug. Inference:
  qualify long-running hardware behavior beyond brief boot smoke. Opened
  thread explicitly dates the report September 3.
- **2026-09-11 — [Clevo notebook boot failure after upgrade](https://discussion.fedoraproject.org/t/updating-fedora-from-43-to-44-left-me-with-a-white-underscore/201702).**
  Report supplies CPU/GPU, Insyde firmware and dual-NVMe layout; both Linux
  media and Windows boot are reportedly affected. No confirmed cause is
  inferred. This illustrates why compatibility evidence must identify the
  complete machine/firmware combination and recovery path. Date/content
  verified in indexed primary-forum text.

Out-of-window search hits were excluded, including a July Proxmox Secure
Boot packaging report. No standards change or universal hardware-support
conclusion is inferred from these limited recent discussions.
