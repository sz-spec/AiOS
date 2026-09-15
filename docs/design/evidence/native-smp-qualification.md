# Gated native AP user-execution qualification — 2026-09-15

The diagnostic image demonstrates ordinary CPL3 computation on secondary CPUs
under QEMU. BIOS and UEFI each pass with one CPU and four CPUs. The same source,
user executable and observer with AP scheduling disabled pass the single-CPU
controls but fail both four-CPU cases: every worker checkpoint reports APIC 0.
This closes the actual-AP-execution prerequisite for later isolation work. It
does not establish simultaneous progress or safe concurrent memory revocation.

## Controlled comparison

Both final diagnostic builds archive commit
`c4df93d468585542147ca96f5aba04b6cd2e5843` into fresh directories, containing
5,436 source files and no prior compiled outputs. They use the same source
manifest, pinned offline builder and user executable. `NATIVE_SMP_TEST=1` is the
only additional make argument in the AP build. All source inputs remain unchanged
after building. Each matrix uses one immutable ISO for four 35-second observations,
at most two VMs at a time, with q35/TCG, qemu64 and 1,024 MiB RAM.

A separate clean normal build from the same commit passes all four standard boot
checks in 106.47 seconds. Its configuration contains none of the diagnostic
defines. Its kernel hash remains
`9d0bfc6065797c5c6f2a2d66155719f356ac653c285a9a806832a75ac7b8457f`, matching the
previous qualified normal kernel. Its ISO hash is
`678d6a73baf4d8533cd92a1751cddb7ac2ff103e1517e79485e23efc31ccde45`; that ISO
differs from the preceding normal ISO, so this does not establish byte-identical
ISO reconstruction. A local copy is `dist/vos5-normal-regression-20260915.iso`.

| Configuration | Existing scheduler, serialized observer | Gated AP scheduler |
|---|---|---|
| BIOS, 1 CPU | Pass; worker APIC 0 | Pass; worker APIC 0 |
| BIOS, 4 CPUs | Expected failure; worker APIC 0 only | Pass; worker APICs 1 and 2 |
| UEFI, 1 CPU | Pass; worker APIC 0 | Pass; worker APIC 0 |
| UEFI, 4 CPUs | Expected failure; worker APIC 0 only | Pass; worker APICs 0 and 1 |

Each run checks two distinct forked workers, eight checkpoints per worker,
200,000 unsigned 32-bit LCG iterations per checkpoint, actual CS privilege level,
hardware CPUID identity, kernel topology/schedule correlation, successful waits
and subsequent parent progress. All 64 final AP-image checkpoints validate.
The baseline also completes all arithmetic and lifecycle checks; its only final
multi-CPU failure is missing execution on multiple hardware identities.

The baseline harness exits nonzero intentionally. Its aggregate result reports
the first failed case; all four case-level results and raw logs are retained.
It must not be described as a passing AP qualification.

## Implementation and review

The old global run queues expose the previous task before its context/stack save
finishes. The experimental picker assigns immutable CPU ownership under the
scheduler lock and filters both interactive and priority queues by ownership.
Fork starts unbound; CLONE_VM inherits its parent's owner. Tasks cannot migrate.
Owner-local reclamation excludes the current task; direct remote destruction is
rejected. Lazy-FPU owner release is local and interrupt-protected. A fixed tick
delay alone is no longer relied on to protect a different CPU's active stack.

The BSP's periodic timer sends the existing reserved schedule IPI, vector 0xFC,
to started APs using the actual topology. The handler acknowledges first and
requests scheduling. It preempts only an interrupted CPL3 frame; kernel/idle
code returns to its own continuation. AP idle performs deferred work separately,
then checks pending work with interrupts disabled before STI/HLT. Initial BSP
selection explicitly disables interrupts. Deferred cleanup skips IF-clear entry.
There is no immediate enqueue IPI, migration protocol or general kernel
preemption qualification in this change.

Diagnostic scheduling records are bounded to first dispatch. A diagnostic-only
record lock serializes kernel log messages and TTY writes after syscall user
buffers have been copied into kernel storage. The observer rejects corrupted
records; it does not reconstruct or normalize them into a pass.

The [security review](native-smp-security.md) and
[mathematical review](native-smp-invariants.md) independently reviewed ownership,
reclamation, interrupt ordering, observation logic and final runtime evidence.
The complete native host-observer suite passes 29 tests, including seven SMP
methods and explicit interleaving/truncation regressions. Three invalid diagnostic
flag combinations are also rejected by make.

Gitleaks scans of the four implementation commits report no findings. The
evidence-directory scan reports 134 generic-key matches, all confined to source
manifest SHA-256 values. Every flagged value was recomputed from its archived
source file and matched; the redacted scan and checks are retained. No suppression
was added.
The final staged scan also flags the bundle manifest's hash of
`secret-scan-review.json`: all 135 staged matches were checked as recomputable
file hashes, including that additional manifest entry.

## Artifacts and evidence

| Artifact | SHA-256 |
|---|---|
| Qualified AP ISO | `3bebb24ff547d9ea69b27f535e9cd8df89e27dbc49e84d0ba6b05a1c5fd8d75d` |
| AP kernel | `f239f485af2ce7a4ca8e5764b72c26f714eb79fea28140b47ec6d726cee28af3` |
| Baseline ISO | `c8a9e7b45693f94ba6c83a350d6865895428068f147763474d55271fe6d9230f` |
| Baseline kernel | `002923a156c7f834729c1fb32310f098ebb2d5480f1f116a9b1e3aa80a964728` |
| Identical user test ELF | `cb65e27bc559bb36b6a7c16421b25663a8f31cd7d8ae1efa566bd2408295d32b` |
| Identical observer | `207b34fe145214e4e0223256333db51dd717a55085e449864d233abc3d5cd1a2` |

The local AP artifact is `dist/vos5-smp-qualified.iso`; ISO files remain untracked.
Portable evidence is in [native-smp-2026-09-15](native-smp-2026-09-15/), including
source hashes, build logs, immutable artifact hashes, firmware/tool versions,
raw serial bytes and case-level results. The builder image is pinned to
`sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6`.
The passing clean AP matrix took 106.61 seconds; the final controlled baseline
took 106.55 seconds. Both confirm build-container cleanup.

Earlier failures are retained: the first build lacked an explicit user-ELF link
rule; the first linked baseline had an interleaved BIOS/1 record; the first AP
trial passed the one-CPU controls but had corrupted multi-CPU output. None is
retroactively marked passing. Fresh builds emit a make clock-skew warning in
this environment; successful qualification also checks absence of prior outputs,
unchanged inputs and actual boot results.

## Reproduction and remaining gates

Run from the repository, using new output paths:

```sh
python3 scripts/native_clean_qualification.py --revision c4df93d468585542147ca96f5aba04b6cd2e5843 --smp-workload --output /private/tmp/vos5-smp-baseline-new
python3 scripts/native_clean_qualification.py --revision c4df93d468585542147ca96f5aba04b6cd2e5843 --smp-test --output /private/tmp/vos5-smp-qualified-new
```

The new scheduler remains disabled in the normal build. Enabling it for arbitrary
workloads is not justified by this result. CPUID's legacy APIC field is scoped to
the tested small topology; the test does not prove every AP executes user work.
No simultaneous execution, fairness, sustained SMP stability, FPU isolation,
shared-VM COW/VMA synchronization or remote TLB acknowledgement is claimed.
Diagnostic record serialization is not a general NMI/crash-safe logging design.

The next isolation gate requires a reviewed shared-address-space lifetime and
synchronization protocol, an actual overlapping access/revocation workload, and
remote TLB acknowledgement before permission revocation is considered complete
or physical pages can be reused. Physical PC compatibility, universal isolation
and byte-identical ISO reconstruction remain unqualified.
