# Native TLB invalidation qualification — 2026-09-15

The replacement TLB primitive passes a native two-page kernel-mapping probe in
BIOS/UEFI with one and four CPUs. Every participating CPU observes the old cached
translation immediately before invalidation and the replacement immediately
afterward. This is a prerequisite for future cross-CPU protection work, not proof
of complete process isolation or concurrent shared-user-address-space safety.

## Corrected behavior

The previous range helper stepped by 2 MiB, including when its caller supplied
4 KiB context pages. It did not check address arithmetic and used a global
acknowledgement count without CPU or request identity. A timeout allowed request
state reuse, and the void wrapper continued after warning about missing replies.
Its LAPIC delivery waits were themselves unbounded.

The new implementation validates the current four-level canonical address space
and inclusive range end without overflow. For nonempty requests it conservatively
invalidates all local contexts, including global translations, and requests the
same operation from the snapshot of started CPUs. The architecture adapter checks
PGE support, toggles CR4.PGE and restores the original CR4. The
[Intel SDM, Volume 3A, section 5.10.4.1](https://cdrdv2-public.intel.com/835754/253668-sdm-vol-3a.pdf)
specifies invalidation across PCIDs and paging-structure caches when that bit
changes. This is the architectural basis; the native probe does not independently
exercise every possible inactive PCID or physical CPU implementation.

Each CPU acknowledges its published 64-bit generation after invalidation. The
requester checks each target's generation rather than a total count. A duplicate
response cannot replace another CPU's response. Delivery or acknowledgement
failure permanently poisons the protocol until reboot; late responses cannot
turn failure into success or permit a new request to reuse uncertain state.
Concurrent checked requests return busy instead of spinning with interrupts
disabled. Invalid ranges return an error without invalidating translations.

Both LAPIC pending waits and the acknowledgement wait use finite iteration
budgets. These are not hard wall-clock deadlines. Ordinary local interrupts are
excluded during the operation; stable boot-time topology and no NMI reentry into
ICR programming are assumptions. The legacy void API now panics on any error
because its callers cannot consume a failure result. This deliberately changes
availability behavior; it must not be described as a recoverable retry protocol.
Broader invalidation also has a performance cost that has not been benchmarked.

This replacement is part of the normal kernel. The AP scheduler, translation
probe and negative bypass remain explicitly diagnostic and disabled by default.
Existing user mprotect, unmap and COW paths still need separate synchronization
and integration before they can claim cross-CPU revocation safety.

## Native proof and negative control

The diagnostic reserves two previously unmapped adjacent 4 KiB kernel aliases,
backed by retained physical pages containing values 17 and 34. Every CPU reads
both pages during a warm-up request. The BSP then atomically replaces only the
second PTE with a page containing 51, without INVLPG. A two-byte flush request
starting at the last byte of the first page crosses into the second page.

For every CPU the test requires `before0=17 before1=34` and
`after0=17 after1=51`. Thus the old translation must actually remain cached before
the invalidation; early eviction makes the test fail rather than manufacture a
pass. The first page provides an unchanged-content control. Both aliases are
unmapped and another acknowledged invalidation completes before backing pages
are released. The existing ring-3 SMP computation workload then runs normally.

The final positive matrix validates twenty warm/remap records across ten CPU
instances and all 64 user computation checkpoints. Independent reviewers checked
the raw observations, topology and actual artifact hashes.

Separate clean builds from the same commit also pass all existing regressions:

| Build flavor | BIOS/UEFI ×1/4 CPUs | Verified scope |
|---|---|---|
| TLB diagnostic | 4/4 pass | Twenty warm/remap records and 64 CPL3 computation checkpoints |
| Normal | 4/4 pass | Memory initialization, scheduler and user setup wizard |
| Memory transitions | 4/4 pass | 48 sequential COW/protection/unmap cases |
| Process isolation | 4/4 pass | 16 sequential foreign/kernel access fault-containment cases |

The regression builds do not enable the experimental AP scheduler. Their passes
preserve the earlier bounded guarantees and do not upgrade them to concurrent
shared-VM guarantees. The normal build configuration contains none of the
diagnostic defines. The new diagnostic and normal local artifacts are
`dist/vos5-tlb-qualified.iso` and `dist/vos5-tlb-normal-qualified.iso` respectively.

The separately built negative image uses exactly the same source and observer.
`NATIVE_TLB_SKIP_FLUSH=1` omits the actual remap invalidation on CPU1 in SMP, or
CPU0 in the one-CPU control. The chosen CPU still acknowledges but reads stale
value 34 afterward; every other SMP CPU reads 51. All CPU observations are emitted
before the expected panic. This tests the difference between an acknowledgement
and a real translation change. All four configurations exhibit the expected
failure, as recorded in the [negative review](native-tlb-2026-09-15/negative-review.json).
The original harness stopped on the expected BIOS1 failure and did not run UEFI4;
UEFI4 was completed separately for 35 seconds using the same negative ISO. The
original failed aggregate is preserved. The negative image is not a bootable release.

## Host tests and source identity

The host protocol test compiles the actual production C translation unit with
mock architecture hooks, using `-Wall -Wextra -Werror`. Fourteen fresh-process
scenarios cover empty/cross-page/top-address ranges, overflow and canonical holes,
sparse topology, duplicate destinations and acknowledgements, old acknowledgements,
concurrent entry, missing acknowledgements, and immediate/partial delivery failure
with permanent poison. These establish software protocol behavior, not hardware
TLB semantics. The native observer suite passes 35 test methods, including six
TLB tests that reject missing, reordered, corrupt and skip-flush observations.

Final native sources are commit `8c20f26e442a3525ffbc83a6d4e3ede68040c04f`.
Fresh Git archives contain 5,511 source files and no prior compiled outputs.
The positive and negative images have identical source manifests, user executable,
observer and pinned compiler environment. The negative flag is their only
additional build argument. All tracked source inputs remain unchanged after
building. QEMU uses q35/TCG, qemu64, 1,024 MiB RAM and finite serial observation;
these are emulator results, not physical hardware qualification.

| Artifact | SHA-256 |
|---|---|
| Final diagnostic ISO | `df4ee8536d477063f9096750ec45ed05c75cf220f56a29d70e3468b24e39ed6b` |
| Final diagnostic kernel | `24784224d262870f8b29ddf62116479dc93fb1cc778e750d3077a2b1ea49d8c7` |
| Normal ISO | `1fbd1ef0f96ea6938ecfd82b435a9ef452a0b5b26ef629346b84fd069234ec05` |
| Normal kernel | `335949fe6371f7317b15fff68bdabb547f4483cfb80adfb14e119f58768f9b90` |
| Negative ISO | `7cce491f6344609e8e22a1b76dceb5ed03da451b2799dff1c88f61491eb177cc` |
| Negative kernel | `dcd1fc3f617415f228abfc9f47e93eb49d353283f0b05fdc5aa5055829f4e48e` |
| Shared user workload | `cb65e27bc559bb36b6a7c16421b25663a8f31cd7d8ae1efa566bd2408295d32b` |
| Shared native observer | `c56a1535d637fe752e95f64d50be563d87e20d3fd3e162e492435578c8e172d7` |

Raw observations, failures, build logs and source hashes are retained in
[native-tlb-2026-09-15](native-tlb-2026-09-15/). The
[security review](native-tlb-security.md) and
[mathematical review](native-tlb-invariants.md) state their independent checks
and the difference between mocked protocol tests and native evidence.

The two implementation commits have zero secret-scan findings. The evidence
scan flagged 135 source-manifest entries; every value was recomputed against its
archived source file and confirmed to be a SHA-256 digest. No suppression was
added. The redacted scan and individual checks are preserved with the evidence.
The final staged scan flags those same 135 entries plus the bundle-manifest
digest of `secret-scan-review.json`; all 136 values were independently recomputed
and verified as file hashes. The bundle manifest validates all 78 evidence files.

## Reproduction and unresolved isolation gates

Use a fresh output directory for each command:

```sh
python3 scripts/native_clean_qualification.py --revision 8c20f26e442a3525ffbc83a6d4e3ede68040c04f --tlb --output /private/tmp/vos5-tlb-new
python3 scripts/native_clean_qualification.py --revision 8c20f26e442a3525ffbc83a6d4e3ede68040c04f --tlb-skip-flush --output /private/tmp/vos5-tlb-negative-new
```

The second command must fail its boot qualification. Shared-VM retain/release
across clone, exit and exec, serialized VMA/PTE/COW mutation, permission revocation
under overlapping user access, and prevention of early physical-page reuse still
require dedicated implementation and tests. During review an existing collision
was confirmed: the cognitive-priority marker and COW marker both use PTE bit 52.
That combination is unqualified and needs a distinct bit allocation and regression
before extending shared-memory execution. CPU hotplug, NMI delivery behavior,
sustained SMP performance, universal hardware compatibility and byte-identical
ISO reconstruction are also outside this result.
