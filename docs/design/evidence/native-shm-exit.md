# SHM creator death and deferred cleanup — 2026-09-17

Source `8c23007eed4acd731b98bdd2998c9634f2ba3f75` adds automatic release of SHM
creator ownership when its task dies. Remaining mappings keep the backing alive;
creator-only regions are reclaimed without waiting for a parent to collect the
zombie. This extends the [creator authorization gate](native-shm-authorization.md)
and does not grant surviving processes the creator's authority.

## Ownership and safe reclamation

Under the SHM registry lock, death notification matches the immutable creator
identity, claims its creator-close token once and stores the full generation-tagged
handle in a bounded pending array. The existing creator reference becomes the
deferred worker's reference: its count does not transiently reach zero. Duplicate
notifications and explicit-close-before-death do not enqueue another release.
Explicit close after notification sees the claimed token and cannot consume the
pending reference. The pin prevents slot reuse until the pending put completes.

Notification performs no allocation, region-mutex acquisition, page-table teardown
or physical release. Every registry-lock holder now excludes local interrupts so
a timer/fault death notification cannot wait for its interrupted lock holder.
Each unlock restores the caller's original IRQ state.

The scheduler's existing safe deferred-work path drains pending creator handles
before address-space cleanup. A drain detaches at most 63 handles under the lock,
then releases each owned reference outside it; the common last-reference finalizer
retains exactly-once cleanup. An IRQ-disabled drain returns without consuming work.
IF being set alone is not proof of arbitrary interrupt nesting safety: this API
requires the documented process-context caller, just like the address-space reaper.

Hooks cover normal exit, terminal user faults, explicit task kill, CPU-quota death,
bridge timeout, direct task destruction and deferred-destruction/reap fallbacks.
Terminal state is published before notification. Ordinary and device creation
check for a dying current task inside the registry critical section before
publication, using the existing allocation unwind when denied. Creation logging
no longer dereferences the region after releasing the registry lock.

These changes do not prove that every remote kill stops an already running CPU
continuation safely. The existing task-stop and shared-address-space concurrency
contracts remain separate prerequisites for broader parallel execution.

## Controls and evidence limits

The strict memory observer requires 51 user records, six complete kernel markers
and eleven precisely correlated user faults. Added user tests cover:

- An unmapped creator exits without close. Registry retirement is observed before
  `waitpid`, rejecting cleanup that depends solely on parent collection.
- A PUBLIC creator exits while a parent mapping survives. Full-page reads and
  writes continue after termination; final detach eventually retires the region.
- Explicit creator close followed by exit does not consume the surviving mapping.
- A creator faults on a read at `0x7400000000`. The matching PID, error 4, terminal
  status 35584 and registry retirement before collection are all mandatory.

User polling is bounded and tests registry retirement, not physical free by itself.
The separate native kernel control exercises real PMM/VMM backing: two creator-only
regions stay live through duplicate notifications and an IRQ-disabled drain, then
lose their registry entries and physical references after enabled draining. The
free-page delta must recover at least the two backing pages; this is not an exact
whole-allocator conservation proof. Additional mapped-survivor and explicit-close
controls preserve canaries and reclaim the last backing. Freed objects are not
inspected through dangling pointers.

Host tests extract the production notification, drain and final-release functions
and execute them with mocked locks/current task/hardware cleanup. They cover all
63 pending slots, unrelated owner identities, repeated operations and both explicit
close orders. Host IF is enabled; only the native control tests the IF-clear path.
The pre-existing production handle/cookie, PTE metadata and TLB C controls remain.

## Qualification

All four clean-source matrices pass BIOS/UEFI × one/four CPUs in QEMU q35/TCG.
Each archive contains 5823 files, starts without generated outputs and retains
unchanged source inputs. One ISO per flavor is reused unchanged across its four boots.

| Matrix | Passed boots | ISO SHA-256 |
|---|---:|---|
| memory | 4/4 | `704021198cbf1e63e1268091aeeaced3279d0fab386f4e0a76eb6a277ba96fa3` |
| normal | 4/4 | `b481602ec3bc4086c70014874b038131b6fd7d5878967a190b6759d07588735f` |
| tlb | 4/4 | `e704c4bb42b70f24744a2f0707bc32045d92571fa87b6c13f617645e241c2c32` |
| isolation | 4/4 | `036928b6abf142c99a3ffddde9f412e8c310f3e10830c33d9d3a6dc766ba8f2f` |

All sixteen boots retain the existing regression gates. Across the four memory
runs, 48 original cases, prior lifecycle/authorization controls and the four new
creator-exit scenarios per run pass. The prior isolation suite retains sixteen
direct foreign/kernel-access attempts. The TLB suite retains its bounded kernel
translation and AP-user-workload checks. The normal configuration excludes the
diagnostic defines. No whole-system or arbitrary-concurrency certification follows.

All 38 observer methods and four actual-C test methods pass. The security review
independently reclassifies raw logs and recomputes source/artifact hashes; the
mathematical review independently verifies the 51/6/11 memory evidence and hashes.
Local read-only copies are `dist/vos5-shm-exit-qualified.iso` (memory diagnostics)
and `dist/vos5-shm-exit-normal-qualified.iso` (normal boot), with hashes above.

Reproduce in a fresh output directory:

```sh
python3 scripts/native_clean_qualification.py --revision 8c23007eed4acd731b98bdd2998c9634f2ba3f75 --memory --output /private/tmp/vos5-shm-exit-new
```

Omit `--memory` for normal boot, or use `--tlb`/`--isolation` for separate diagnostic
builds. Tool versions, firmware hashes, build command, actual source/artifact
identities and the driver are preserved in the portable packet. Temporary paths
retain their original `20260915` suffix; qualification was finalized September 17.


The raw build logs retain existing clock-skew/objdump diagnostics and the legacy
benchmark signed-overflow warning. The benchmark is not executed by this gate.
Clean-output checks, source hashes and runtime observations support the bounded
result; this is not a warning-free or performance qualification.

## Remaining gates

Concurrent SHM lookup/unmap lifetime, VMA/PTE mutation and remote permission
revocation remain unqualified. Cross-process delegated creator authority is not
implemented. General PID/TID identity consistency, remote task-stop safety, full
SysV IPC compatibility, physical device/PC coverage, persistent installation and
byte-identical ISO rebuilding remain separate requirements. Exec continues to
preserve the task principal; this change releases ownership on death, not exec.
Mappings retained by an uncollected zombie's address space still pin backing until
that address space is reclaimed. This stage does not redesign zombie address-space
retention or imply immediate reclamation of every resource at the death instruction.

[Security review](native-shm-exit-security.md) ·
[Mathematical review](native-shm-exit-invariants.md) ·
[Portable evidence](native-shm-exit-2026-09-17/) ·
[Council actions](expert-council-2026-09-15/ACTIONS.md)
