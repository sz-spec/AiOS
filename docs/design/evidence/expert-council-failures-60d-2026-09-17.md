# Expert council: failure review and 60-day research

Date: 2026-09-17. Research window: **2026-07-19 through 2026-09-17**.
Coordinator: `/root`; parallel reviewers: `/root/math_build_review`,
`/root/mcp_upgrade`, `/root/rust_upgrade`.

## Decision and execution scope

The independent native OS remains under development. Release qualification
is **not complete**: the current full-suite baseline fails its unchanged
80,000 messages/second health requirement on both BIOS and UEFI. Context
lifetime, concurrent memory revocation, physical devices, Secure Boot and
installation also have explicit open gates. A reproducible ISO is useful
evidence but does not close any of those runtime/security gates.

The council covers the [twenty specialized review roles](expert-council-2026-09-15/README.md)
using three reviewer agents and the coordinator, within the four-thread
execution limit. This is not twenty simultaneous agents, an external
certification, or a claim that every line of the repository was audited.
The reviews combine source inspection, actual-source host regressions,
isolated guest experiments and recent primary engineering discussions.

This report supersedes stale status statements in the original council
action table; historical reports/logs remain intact. “All failures” here
means all known finding classes in the twenty-role packet and subsequent
qualification, including unsupported capabilities and missing evidence.
Unknown defects cannot be enumerated by a finite review.

## Findings, causes and required closures

| Finding | Evidence / established cause | Disposition and next acceptance gate |
|---|---|---|
| Full-suite health throughput | Current retained-reader checkpoint: BIOS 37,923/s; UEFI 40,208/s; both 57 programs completed with health alone nonzero. Prior final3: 38,776/37,599/s; final2: 39,047/38,987/s. Old BIOS control 98,165/s. Exact cause remains open. | **Open.** Repeat controlled comparisons, then instrument scheduling, time calls and prior-workload state. Preserve 80K, workloads and failed samples. See the [current checkpoint](ai-context-lifetime-2026-09-17.md), historical matrix below and source validation. |
| Crash-recovery test polling cost | After collecting its producer, `test_agent_chaos` still performs up to 500,000,000 empty-ring polls. Long silence after the child-exit log is therefore not by itself evidence of deadlock; final3 BIOS completed this test. | **Open test-efficiency issue.** In a separate diagnostic change, use the established producer-death/expected-count contract and explicit queue-empty checks to bound completion. Keep this out of the current performance comparison: changing prior work changes the experiment. |
| AI-context publication and reader lifetime | Ordering-only repair is followed by retained lookup readers, one serialized registry and zero-reference deferred retirement. Competing creation discards its unpublished loser safely. | **Bounded repair; cancellation remains blocking.** Readers whose continuations complete are covered; asynchronous kill can abandon pins, detached cleanup work or deferred-active state. Region mutation remains separate. [Current checkpoint](ai-context-lifetime-2026-09-17.md), [cancellation diagnostic](ai-context-cancellation-review-2026-09-17.md). |
| AI context authorization/inheritance/startup | Full-width syscall ID checks prevent truncation aliases; valid-ID ownership is still unbound and active app identity remains global. APPLOAD now returns unsupported because its old stack request/task publication path was unsafe and did not enqueue the task. Guarded fork/clone inheritance remains refused. | **Open/unsupported.** Immutable caller ownership, safe unpublished task construction/enqueue/startup/reaping, and retained/clone policy are prerequisites. Integrity success remains conditional on CHECKSUMMED being enabled. Do not lift ENOTSUP without the acceptance contract. [Caller review](ai-context-external-callers-2026-09-17.md). |
| Arbitrary user MMIO authorization | An AI context authorized any non-PMM address; address and size were ignored. Hardcoded pseudo-NPU address had no verified device BAR. | **Exposure closed by denial; hardware feature unavailable.** Exact enumerated BAR ranges, immutable task/device capability and enforcing IOMMU are prerequisites. Denial-test exit 0 is not NPU performance validation. [MMIO evidence](mmio-fail-closed-2026-09-17.md). |
| DMA and platform trust | Pass-through device setup does not provide a bounded second-level DMA domain. EFI loader boot does not establish a signed trust chain; broad direct-map/KPTI limits remain. | **Unverified.** Legal/illegal DMA, teardown/IOTLB invalidation, device reset; modified/revoked image rejection, rollback policy, firmware db/dbx and recovery tests on declared hardware. |
| Shared mmap allocation and VMA mutation | Clone siblings used separate cursors for one address space; allocation could collide. Per-page overlap scanning admitted excessive IRQ-disabled work. Split/permission/backing changes needed a transaction. | **Bounded repair.** Shared cursor, serialized mmap/munmap/mprotect, bounded sparse preflight and fork snapshot now have host and guest coverage. Demand-fault readers, file-backed concurrency and remote TLB acknowledgement remain separate. [Repair record](native-thread-memory-repair-2026-09-17.md). |
| Usercopy and remote permission revocation | Preflight permission checks do not stabilize mappings; local invalidation does not establish remote revocation-before-reuse. A passing diagnostic shootdown is narrower than every mutation path. | **Open.** Pin/stabilize mappings through the copy and acknowledge all relevant CPUs before page or page-table reuse. Exercise hostile concurrent remap/unmap, COW and borrowed-MM readers. |
| File-backed faults and multi-user VFS | Seek/read/restore can interleave on shared file position; a VMA reference protects object lifetime only. chown/fchmod wrapper credential checks remain a review lead, not a demonstrated exploit. | **Open.** Position-independent I/O, stable VMA/backing reads and cross-user syscall authorization negatives. Test storage corruption/recovery separately. |
| Retired thread memory and deferred progress | Retired clone stacks explained the prior 3,204 KiB loss; completion counters preceded actual retirement. Pending work could fail to revisit its owner. | **Bounded repair.** Deferred process-context draining and owner pending bits pass their preserved gates. General remote stopping, PID/TID lookup/reparent concurrency and fairness remain open. |
| Waitqueues, synchronization and message queues | Lost wakeup/kill ordering, stale queue handles, find/destroy lifetime, reference cleanup and owner-zero destruction were concrete defects. | **Bounded repair.** Membership protocol, pins, generation handles and caller identity have regressions. Destroy-with-active-owner and general remote task lifecycle need independent hostile tests. |
| Huge-page allocation/release | Foreign return changed pool accounting; page references were not initialized for some claims; arbitrary prefix selection missed available contiguous runs. | **Bounded repair.** Provenance, exact-once extent release and contiguous selection pass host/guest controls. Physical-address ABA and runtime pool-reservation concurrency remain unqualified. |
| Earlier orphan/SHM failures | AP idle PID 1 could adopt user orphans; creator-first SHM leaked; creator identity/stale handles and creator-exit cleanup were incomplete. | **Previously repaired within linked sequential contracts.** See [dated dispositions](expert-council-2026-09-15/README.md). Independent SHM fork remains unsupported; arbitrary concurrent SHM lifetime/delegation is not certified. |
| ISO byte differences | Limine build-ID inputs/private paths and ISO dates caused differences. The retained-reader clean pair, following final2/final3, again matches ISO, kernel and six Limine artifacts byte for byte. | **Passed for recorded inputs and builder.** This is two builds on one host with one pinned builder, not independent compiler trust. Repeat after source changes and preserve inputs. |
| Build clock-skew warnings | Small future mtimes remain on generated directories despite normalized source times. Cause not established. | **Open warning gate.** Compare bind-mounted and container-internal builds with clock/stat samples; do not suppress warnings or claim Docker causation without the experiment. |
| Native network / hosted trust / updates | Native capability checks are PID-based bootstrap policy. Hosted offline credential tests do not prove live provider ACL/revocation. Update flow lacks a complete signed immutable release/anti-rollback chain. | **Open.** Per-principal capabilities, hostile packets/socket ownership, provider-backed identity tests, authenticated release manifests and recoverable update/rollback. GNU/musl publisher-signature provenance gaps remain distinct from hash equality. |
| BIOS/UEFI/SMP and physical PCs | Separate diagnostic one/four-vCPU matrix observed CPL3 work on APIC 0 and 1. It did not prove full simultaneous four-core workloads or real devices. | **Bounded diagnostic pass, broader support unverified.** Keep experimental SMP distinct; require concurrency/revocation gates and exact Intel/AMD firmware/device matrices before expanding claims. |
| Installation, Windows and local-first operation | GRUB/Limine operator instructions disagree; persistent installation, Windows bridge, ESP preservation and recovery are unqualified. Network-enabled QEMU does not prove offline local AI workflow. | **Open.** Rehearsable current build docs; disposable-disk install/remove-ISO/reboot/recovery; real authenticated Windows hosted tests; separate dual-boot tests; blocked-egress workflow. |
| Eleven-source feature preservation and product claims | Disposition ledger is not behavioral equivalence. Universal PC support, enterprise readiness and comparative cost/performance lack the required evidence. Legacy signed-overflow pattern code now uses explicit unsigned arithmetic. | **Open product gates.** Capability-specific acceptance for unresolved SDK/transport/workflows; one dated claim register; repeated comparable workloads with security enabled. Arithmetic repair alone does not validate historical benchmark claims. |

## Controlled performance experiments

[Original results, serial logs, invocations, build logs and SHA-256 index](council-failure-research-2026-09-17/experiment-matrix.md)
preserve every outcome. These are single-CPU QEMU TCG runs. Each cell has
one sample and therefore provides no variance estimate or confidence interval.

| Kernel / two user probes | Health msgs/s | Interpretation |
|---|---:|---|
| Old kernel + old probes, rerun control | 98,165 | Pass; an invariant host-wide slowdown is not supported. Time-varying host load remains possible. |
| New kernel + new probes, BIOS | 39,047 | Fail; health is the only nonzero program. |
| New kernel + new probes, UEFI | 38,987 | Fail on the same final2 ISO. |
| C: new kernel + both old probes | 93,690 | Health passes; suite still fails old NPU assertion against denied MMIO. |
| D: old kernel + both new probes | 96,778 | Health passes; two new denial assertions fail against permissive old kernel. |
| E: new kernel + old NPU / new Frontier | 55,227 | Health fails. |
| F: new kernel + new NPU / old Frontier | 39,934 | Health fails; restoring Frontier alone did not restore throughput. |

**Correction to the progress discussion:** E alone did not establish Frontier
as the central cause. F rules out that simple attribution. A combined
kernel/probe-state or binary-layout effect remains a hypothesis, not a finding
that identifies a particular faulty instruction.

The health source, SPSC header and ELF are identical across the old/final2
snapshots; ELF SHA-256 is
`28cac8eb9131f7f9c9bcac756542f5eb3d6af79d51de51a200085b239aa7b56a`.
The runtime source differences are the queue owner-zero check, MMIO denial,
and two user probes. The scheduler, timer, sync and memory code did not change
between those two baselines. In another SPSC workload both logs record
2,000,000 messages in 9,760 guest ms. That does not support an assumed
system-wide 2.5x slowdown.

Health polls GETTIME per attempted producer iteration, yields on a full
1,024-slot ring and measures a nominal one-second interval using the guest
100 Hz PIT clock. Consumer drain time is included. Equal pushed/consumed
counts prove only count balance in those runs, not payload integrity,
fairness or adequate service. In E, 55,780 messages over 1,010 ms gives the
reported integer rate 55,227; raw counts must not be confused with rates.

## Recent primary discussions and the resulting recommendations

Dates below use original message headers/publication or material advisory
updates. Crawl dates, search ranking and linked older posts are not treated
as publication dates. Forum claims and submitted patches are evidence of
their authors' observations/proposals, not automatic upstream consensus.
The recommendations are our inferences for vOS.

| Date / source | Relevant lesson and its limit |
|---|---|
| 2026-09-01, [QEMU TCG dispatch series](https://patchew.org/QEMU/20260901034808.3524945-1-mattst88%40gmail.com/) | Dispatch/cache costs can affect emulated performance; profile actual translated execution. The author reuses v3 measurements for an Alpha user-mode workload. This is neither a new v5 measurement nor our x86 system-mode workload on macOS; applying the RFC is not an established fix. |
| 2026-09-08, [QEMU VM-elapsed PMU review](https://www.mail-archive.com/qemu-devel%40nongnu.org/msg1224160.html) | Distinguish guest/host elapsed clocks and instruction counters. This is RISC-V PMU/icount work, not proof our PIT is wrong. Synthetic icount seconds must not be used to make the 80K performance gate pass. |
| 2026-09-10, [Linux deferred-free test-boundary discussion](https://www.mail-archive.com/linux-kernel%40vger.kernel.org/msg2655706.html) | An ordinary barrier can leave other deferred-free batches outstanding. Record resource and deferred-work state around each workload; zero zombies alone does not prove a clean baseline. vOS does not use that Linux RCU implementation. |
| 2026-08-28, [deferred drain scheduling](https://patchew.org/linux/20260828135036.7d44361f%40fangorn/) | Re-arm outstanding owner work until it is actually drained. Preserve liveness and exact-once resource tests; a wakeup flag is not completion. |
| 2026-09-01/02, [scheduler MM lifetime series](https://patchew.org/linux/cover.1788305725.git.tim.c.chen%40linux.intel.com/) | A scheduler lock is not an MM lifetime reference. Define reader ownership across exit and reclamation rather than relying on atomic pointer loads. |
| 2026-09-04 and 09, [borrowed MM shootdown](https://lists.openwall.net/linux-kernel/2026/09/04/2237), [page-table reclamation](https://lists.openwall.net/linux-kernel/2026/09/09/2352) | Revocation must cover every CPU that can use the address space and delayed table readers. These discussions inform hostile remote-access gates; they do not prove vOS has completed them. |
| 2026-09-08, [oss-security ZcopyReaper](https://www.openwall.com/lists/oss-security/2026/09/08/1) | Real exploitation of a Linux object-lifetime bug reinforces testing last-access/last-reference races. It is not a CVE assigned to vOS. |
| 2026-08-26–31, [OSDev AHCI BAR5 thread](https://forum.osdev.org/viewtopic.php?t=58312) | Verify mapped BAR sizes and register offsets against discovery, including teardown. A developer's AHCI debug report is not proof a chosen physical address backs an NPU. |
| 2026-09-08/09, [DMA address types](https://lists.openwall.net/linux-kernel/2026/09/08/1865), [IOMMU domain reuse](https://lists.openwall.net/linux-kernel/2026/09/09/781) | Separate VA/PA/IOVA, constrain DMA domains and invalidate before owner reuse. Software ownership flags do not enforce DMA isolation. |
| 2026-09-08, revised 09-15, [Cisco UEFI advisory](https://sec.cloudapps.cisco.com/security/center/content/CiscoSecurityAdvisory/cisco-sa-ucs-uefi-sb-bypass-eb6xC5GW); [CERT/CC](https://kb.cert.org/vuls/id/718077) | Include firmware shell, boot variables and revocation in the platform threat model. A signed ISO alone is insufficient platform evidence. |
| 2026-08-19–20, [Fedora dual-boot installer discussion](https://discussion.fedoraproject.org/t/is-it-expected-to-delete-to-format-the-windows-efi-for-manual-dual-boot-installations/199753) | Verify the advertised ESP action plan against actual writes and recovery on disposable disks. The discussion reports a product failure mode, not vOS installation results. |
| 2026-09-02, [Adoptium reproducible verification](https://adoptium.net/news/2026/09/temurin-reproducible-verification-builds-2026) | Preserve exact compiler/options, provenance and complete byte comparison. Rebuilding a toolchain from verified sources is a stronger trust exercise than repeating one pinned image. |

Additional source/date qualifications and security findings are in the
[security research report](expert-council-security-research-60d-2026-09-17.md),
[technical review](expert-council-review-2026-09-17.md), and
[product/hardware review](expert-council-product-gaps-2026-09-17.md).
Some forum archives were accessible as indexed original-message text but
failed a repeated direct fetch; that limitation is recorded in the security
report. No verified recent source established our precise performance cause
or the generated-directory clock-skew cause. Older Docker/QEMU forum hits
were excluded from the 60-day evidence.

## Ordered work recommended by the council

1. **Context and memory safety:** qualify cancellation at explicit safe kernel
   boundaries or provide complete ownership transfer/cleanup after a proven
   stop. The retained-reader checkpoint does not cover abandoned continuations,
   held locks or detached reclamation batches. Test cancellation against ordinary
   completion, owner CPU acknowledgement, and renewed deferred progress. Define
   foreign-destroy authorization. Audit region lifetime separately; pinning only
   the outer context does not make a mutable region list safe. Keep MMIO denied.
2. **Performance diagnosis:** use a frozen old/current pair, same QEMU,
   firmware and arguments; collect at least ten counterbalanced fresh-VM
   pairs with no concurrent builds/VMs. Predeclare order, sample count and
   ratio statistic; retain every raw sample and compare paired uncertainty.
   Ten pairs do not guarantee statistical power; do not stop early after a
   favorable result. Then compare health first versus after the suite
   in explicitly diagnostic images. Record GETTIME calls, yields, full/empty
   ring events, task ticks/switches, faults and deferred-work counts, printing
   only after the measurement window. Instrument both sides identically.
   Do not change the acceptance threshold, ticks or workload to obtain PASS.
3. **Frozen qualification:** clean build pair, full BIOS/UEFI suite, and then
   dedicated remote-isolation/SMP matrices on the same applicable source.
   Preserve artifact/source hashes, expected negatives and build warnings.
   Separately investigate clock skew with three bind-mount and three internal
   filesystem builds before assigning a cause.
4. **Hardware and deployment:** start with named Intel/AMD machines and
   firmware/device versions; qualify storage/network/reset behavior, Secure
   Boot and persistent installation. Test Windows hosted and dual-boot paths
   separately. Universal-PC support remains a goal, never an inferred result.
5. **Product and consolidation:** align operator docs and public claims with
   tested capabilities; close each unresolved eleven-source feature by an
   explicit implementation decision and behavioral acceptance test.

## Latest source validation: retained readers

The [retained-reader checkpoint](ai-context-lifetime-2026-09-17.md) passes 106
frozen-source host tests, the native build checks, a BIOS/UEFI 1/4-CPU diagnostic
matrix, exact source reconstruction and an eight-artifact clean build comparison.
Its full BENCH image completes all 57 programs on BIOS and UEFI with health alone
failing. The context test covers controlled readers whose continuations complete;
an independent cancellation diagnostic still reproduces an orphaned reference.
The safety and performance gates remain open. All earlier logs below are preserved.

## Previous source validation: final3

The narrowed context-ordering repair passed **100 host discovery tests in
56.215 seconds** and native-build-check's three groups (3 + 1 + 1 tests).
The independent unchanged-harness negative control fails on the old source
and passes on the repair. These results do not close existing-reader races.

The final3 frozen manifest contains 6,189 files and has SHA-256
`e0fe76941a5eab65d982b19cbb9f1f1ff07bbb73d0894418416a6750b08e7fc4`.
It was captured before later documentation edits. All 6,189 recorded files
remain unchanged in the frozen source and both clean build trees. Rechecking
the working tree found only documentation changes among those inputs.

Two fresh offline builds using builder
`sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6`
and `SOURCE_DATE_EPOCH=1700000000` produced identical ISO, kernel and six
Limine artifacts. The evidence reviewer independently verified all sixteen
file hashes against both build results.

- ISO SHA-256: `bcd3e545f38de19ce0c248dae1701be9f7c4386cd6453d5dbeac53932429d62c`.
- Kernel SHA-256: `dd44631c660b99aee079959a17cff97c25f156baf1ac415d1045e59e8dc8c8d4`.
- **BIOS: FAIL**, 57 completed / 56 zero exits; health 38,776 messages over
  1,000 guest ms (38,776/s), four tasks, zero zombies.
- **UEFI: FAIL**, 57 completed / 56 zero exits; health 37,599 messages over
  1,000 guest ms (37,599/s), four tasks, zero zombies. Host elapsed 156.571 s.
- Both guests completed the suite; neither timed out. `test_health_check`
  was the only nonzero program. The frozen classifier independently
  reproduced both failures; ISO and UEFI firmware/template hashes match.
- Build logs retain future-directory mtime warnings of 0.000640 and
  0.000025 seconds. No claim of warning-free building is made.

[Final3 results, original logs, manifest and independent reviews](council-failure-research-2026-09-17/final3/README.md)
qualify this precise snapshot only. The context-order correction did not
restore health in either observation and is not an established performance
fix. No physical-machine, full concurrent-SMP, signed-boot or deployment
qualification was added by these runs.

## Independent report review

`/root/mcp_upgrade` reviewed this synthesis against the twenty-role action
table and found no blocker in the security/evidence scope. The mathematics
reviewer verified the seven rates/counts and requested the host-load and
sampling qualifications now incorporated above. The product/evidence reviewer
verified the separate security report's final2/F updates and its bounded
build/hardware claims. These reviews do not expand the runtime coverage.
