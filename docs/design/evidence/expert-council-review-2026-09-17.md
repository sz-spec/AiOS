# vOS Expert Council Review — 2026-09-17

## Scope and method

Three parallel review tracks examined the current `vos 5` tree and the
available qualification evidence:

1. kernel correctness, mathematics, memory management, scheduling, build and
   performance;
2. security, process isolation, IPC lifetime, user copy, UEFI and DMA;
3. BIOS/UEFI, physical hardware, QA, Windows coexistence and product scope.

The reviewers read the core requirements, consolidation decisions, build and
native-test paths, and the code relevant to their tracks. This was a focused
review rather than a claim that every file in the repository received a full
independent audit. Web research was restricted to material first published or
materially updated from 2026-08-18 through 2026-09-17; the Cisco item below is
an advisory updated during that window. External discussions inform priorities; they do
not prove that vOS has or does not have the same defects.

## Council decision

The immediate order remains:

1. prove synchronization, object lifetime, memory isolation and cross-CPU
   revocation;
2. qualify a frozen native image and independently reproduce its ISO;
3. establish a small, explicit physical-hardware support matrix;
4. implement and qualify installation, Windows coexistence and Secure Boot;
5. optimize only from measured profiles, with VM and physical results kept
   separate.

The current tree supports continued development of an independent native OS.
It does not yet support a release claim of “secure on every PC”, safe Windows
dual boot, malicious-device DMA isolation or enterprise deployment.

## P0: correctness and evidence

- The deferred-reclamation protocol must guarantee that unfinished work is
  re-published for the owning CPU. The current repair uses owner-targeted
  pending bits and every-CPU tick re-arming while the shared reaper list is
  nonempty. Host coverage must include early drain before the grace period,
  remote ownership and exact-once reaping. A recent Linux memory-controller
  discussion describes the same class of pending work becoming unscheduled:
  [deferred drain discussion, 2026-08-28](https://patchew.org/linux/20260828135036.7d44361f%40fangorn/).
- Scheduler locks are not lifetime references for address-space data. Audit
  every scheduler-side access to task MM state and exercise exit/exec/fork
  against remote readers. The analogous lifetime issue is described in
  [sched/cache MM lifetime series, 2026-09-01/02](https://patchew.org/linux/cover.1788305725.git.tim.c.chen%40linux.intel.com/).
- IRQ-state tests must cover IF=0 and IF=1, nested save/restore,
  wake-before-yield and kill-before-removal. Relevant ordering and nesting
  discussions: [IRQ nesting, 2026-08-28](https://lists.openwall.net/linux-kernel/2026/08/28/508)
  and [state/IRQ ordering, 2026-08-27](https://lists.openwall.net/linux-kernel/2026/08/27/1760).
- Qualification must bind revision, dirty-source manifest, builder digest,
  kernel/user binaries, ISO and observer logs. A boot result from the wrong
  source revision is not evidence; see the
  [wrong-branch build report, 2026-08-29 UTC](https://lists.openwall.net/linux-kernel/2026/08/29/12).

## P1: security and isolation

- Complete the current message-queue lifetime qualification: killed blocked
  senders and receivers, close races, two last-reference contenders, stale
  handles and foreign destroy. The present design uses a registry reference,
  per-operation pins, generation handles, creator identity and a task-owned
  cleanup claim. Relevant recent incident class:
  [ZcopyReaper disclosure, 2026-09-08](https://www.openwall.com/lists/oss-security/2026/09/08/1).
- Prove that every permission removal and physical-page release waits for TLB
  invalidation acknowledgements from every CPU that could use the address
  space. Local `invlpg` alone is not a cross-CPU revocation proof. Two current
  upstream discussions make the acceptance condition concrete: CPUs that
  borrow an address space must still participate in shootdown
  ([alpha MMU-context series, 2026-09-04](https://lists.openwall.net/linux-kernel/2026/09/04/2237)),
  and page-table storage itself must not be freed until stale hardware and
  software readers have passed a grace period
  ([RCU-safe user page-table freeing, 2026-09-09](https://lists.openwall.net/linux-kernel/2026/09/09/2352)).
- Define a mapping-stability contract for user copy. Address and permission
  preflight followed by a copy does not by itself close a concurrent
  unmap/remap race. Object-specific copy boundaries should also be audited;
  see the [DLM usercopy whitelist patch, 2026-08-31](https://lists.openwall.net/linux-kernel/2026/08/31/2001).
- Treat the current NPU IOMMU path as pass-through. `bound_slot` and software
  checks do not create a second-level DMA domain. Keep bus mastering disabled
  until a bounded domain is installed and verified with legal and out-of-range
  DMA. Address types must explicitly distinguish VA, PA and IOVA. Related
  engineering discussion:
  [encrypted DMA/IOMMU patch, 2026-09-08](https://lists.openwall.net/linux-kernel/2026/09/08/1865).
  Domain identifiers also need invalidation before reuse: an AMD IOMMU fix
  describes stale device translations becoming reachable by a new owner
  ([nested-domain TLB fix, 2026-09-09](https://lists.openwall.net/linux-kernel/2026/09/09/781)).
- The OVMF qualification exposed that the user MMIO authorization ignored
  `phys_addr` and `size`: merely creating an AI context authorized any address
  outside PMM-managed RAM. The immediate repair is fail-closed, and the old
  hardcoded `0xFD000000` pseudo-NPU test has been replaced by denial checks
  with hardware explicitly unavailable. Re-enable direct user MMIO only after
  an enumerated BAR registry binds exact ranges to immutable task/device
  capabilities and an enforcing IOMMU domain. See the
  [MMIO disposition](mmio-fail-closed-2026-09-17.md).
- Secure Boot is a separate gate from successful UEFI boot. Acceptance tests
  must reject modified loader, kernel and init data, revoked keys and forbidden
  rollback versions. Firmware shell and db/dbx state belong to the threat
  model: [Cisco advisory, updated 2026-09-15](https://sec.cloudapps.cisco.com/security/center/content/CiscoSecurityAdvisory/cisco-sa-ucs-uefi-sb-bypass-eb6xC5GW)
  and [CERT/CC VU#718077, 2026-09-08](https://kb.cert.org/vuls/id/718077).

## P1: build, performance and hardware

- Produce two isolated builds from the same frozen source and compare the full
  ISO hash. Record and explain every difference. Independent reproducible
  verification and signed attestations are described by
  [Eclipse Adoptium, 2026-09-02](https://adoptium.net/en-GB/news/2026/09/temurin-reproducible-verification-builds-2026).
- Report QEMU and physical-hardware performance separately. Preserve raw
  samples, topology, clock source, host load and accelerator state. VM
  preemption can distort lock and cache costs; see
  [vCPU steal/backoff series, 2026-09-03](https://lists.openwall.net/linux-kernel/2026/09/03/515).
- Start physical qualification with a small Intel/AMD matrix covering BIOS and
  UEFI, cold/warm boot, SMP, SMT, heterogeneous cores where available, storage,
  USB input, network, long load and reset/timeout recovery. Expand support only
  after each configuration passes. Recent cache-aware scheduling discussion
  also cautions against treating utilization plots as benchmarks:
  [AMD heterogeneous-core discussion, 2026-09-05](https://lkml.iu.edu/2609.0/12724.html).
- MMIO drivers need checked BAR sizes, offsets and teardown ordering. A recent
  AHCI debugging thread provides a concrete example:
  [OSDev BAR5 mapping, 2026-08-26–31](https://forum.osdev.org/viewtopic.php?t=58312).

## P1: product and deployment

- Reconcile `docs/DEPLOYMENT.md` with the current Limine/Multiboot2 build before
  publishing installation instructions.
- Treat hosted Windows support and dual boot as separate products. Hosted mode
  needs authenticated handshakes, reconnect behavior and resource isolation.
  Dual boot needs disposable-disk qualification for one/two ESP layouts,
  preservation of Windows boot files and data, rollback and safe uninstall.
- The installer must show an exact disk-change plan and verify that the actual
  changes match it. A recent Fedora discussion documents why a misleading EFI
  formatting summary is itself a product risk:
  [Fedora dual-boot installer discussion, 2026-08-19–20](https://discussion.fedoraproject.org/t/is-it-expected-to-delete-to-format-the-windows-efi-for-manual-dual-boot-installations/199753).
- Enterprise qualification additionally requires signed updates, key
  revocation, staged rollout, administrator authorization, audit logs and
  recovery. A current dual-boot product release illustrates the need for an
  explicit recovery contract:
  [IGEL Dual Boot 2.0.1, 2026-08-18](https://kb.igel.com/igel-business-continuity-disaster-recovery/current/igel-os-dual-boot-2-0-1-release-notes).

## Acceptance gates

1. All host regressions pass under the configured sanitizers and the kernel
   builds from a clean tree.
2. A frozen dirty-source snapshot boots and runs the complete guest suite with
   zero failures; health is repeated independently and after load.
3. BIOS/UEFI and single/multi-CPU results preserve source and artifact hashes.
4. Two isolated ISO builds are byte-identical, or every byte difference is
   accounted for and removed before a reproducibility claim.
5. Cross-process memory attacks, cross-CPU stale translations and malicious
   DMA attempts are rejected by the enforcing hardware mechanism.
6. Physical support, Windows coexistence, Secure Boot and enterprise readiness
   are reported as separate, evidence-backed capabilities.

The product, deployment and hardware claim audit is maintained separately in
[`expert-council-product-gaps-2026-09-17.md`](expert-council-product-gaps-2026-09-17.md).
