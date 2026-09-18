# VOS 5

The canonical VOS AI operating system project, assembled in `vos 5/` with its
own local Git repository. It contains the native x86_64 kernel, user-space
programs, backend, frontend, desktop shell, SDKs and deployment tools.

**Status: integration and release qualification in progress. This is not yet
a final release.** Native BIOS/UEFI boot reaches user-space setup in QEMU,
including one and four CPUs on both firmware paths; a disposable two-CPU VM completes
the keyboard-driven wizard. Full kernel isolation, concurrent user workloads
on secondary CPUs, persistent installation, hosted workflows
and remaining legacy capabilities still require work and validation.

The [SHM creator-exit stage](docs/design/evidence/native-shm-exit.md) releases
creator references through safe deferred work while surviving mappings retain backing.

The [SHM authorization stage](docs/design/evidence/native-shm-authorization.md)
binds creator close to immutable task identities and rejects stale region handles.
Its evidence distinguishes tested denials from remaining IPC/concurrency limits.

The [shared-VM lifetime stage](docs/design/evidence/native-vm-lifetime-qualification.md)
separates COW/cognitive metadata and adds task/CPU ownership across clone and exec.
The [review council](docs/design/evidence/expert-council-2026-09-15/README.md)
records twenty specialized reviews by three reviewing agents and one coordinator.
Its findings and the lifetime qualification limits remain explicit.

The [60-day failure review](docs/design/evidence/expert-council-failures-60d-2026-09-17.md)
records the current defects, recent primary engineering research and recommended
acceptance gates. Clean builds now produce byte-identical ISO artifacts for the
recorded source and pinned builder. Full-suite health throughput still fails;
asynchronous task cancellation, build clock warnings, physical hardware and deployment
remain open. Matching ISO bytes do not establish release readiness.

The [AI-context lifetime checkpoint](docs/design/evidence/ai-context-lifetime-2026-09-17.md)
adds retained readers, serialized publication and deferred final reclamation.
Its contract requires continuations to complete: cancellation can still abandon
pins or cleanup work. APPLOAD explicitly returns unsupported while safe task
startup and authorization remain incomplete. Region mutation and full concurrent
SMP are separate open gates.

The [native isolation gate](docs/design/evidence/native-isolation-qualification.md)
tests four direct user-mode read/write attempts against another process and a
supervisor kernel page, with correlated faults and continued victim progress.
Its bounded evidence does not certify every memory-isolation mechanism.

[Memory-transition qualification](docs/design/evidence/native-memory-transitions-qualification.md)
now covers COW, read-only/PROT_NONE/NX permissions and unmap behavior on 4 KiB
anonymous pages. Kernel-address mprotect and permission bypasses were corrected.
Concurrent shared-address-space execution and remote TLB revocation remain open.

[AP user-execution qualification](docs/design/evidence/native-smp-qualification.md)
now demonstrates actual CPL3 computation on secondary CPUs in a separately gated
diagnostic image, with BIOS/UEFI and single-CPU controls. The normal scheduler
remains unchanged; simultaneous execution and cross-CPU memory revocation are
separate, unqualified gates.

[Native TLB qualification](docs/design/evidence/native-tlb-qualification.md)
now observes stale kernel translations replaced on every tested CPU, with a
negative control that deliberately omits one CPU's invalidation. The normal
kernel uses per-CPU generation acknowledgements and fails closed on incomplete
shootdowns. The later shared-VM stage corrected the COW/cognitive PTE-bit
collision and qualified bounded sequential lifetime transitions. Concurrent
shared-user-VM mutation and remote permission revocation remain unresolved.

The governing architecture is recorded in
[core requirements](docs/design/CORE_REQUIREMENTS.md), alongside the supplied
[historical technical brief](docs/design/vOS_Technical_Brief.pdf).
Native operation without Linux, coexistence with Windows, local-first AI,
permission enforcement, cryptographic identity and tenant isolation are release
requirements. Hardware coverage must be demonstrated with actual tests.

## Native build

The unified native entry is `make native -j4`; see
[native build dependencies](docs/design/NATIVE_BUILD.md) for configuration,
isolated user/musl outputs and remaining packaging gates.

From this directory, with `x86_64-elf-gcc`/binutils, Make, Python 3, NASM,
mtools and xorriso installed:

```sh
make native -j4
```

The bootloader builds offline from vendored source. `dist/vos5.iso` contains
BIOS and IA32/x64 UEFI boot images, verified through its El Torito catalog.
This verifies packaging, not successful installation. The current kernel uses
Limine's Multiboot2 loading path at a fixed physical address; its separate
Limine-protocol/KASLR entry remains unqualified. A 32-bit UEFI bootloader does
not make the x86_64 kernel support a 32-bit-only CPU.

Kernel objects track the effective compiler/linker options and source list in
`build-config.json`; changing those options invalidates the affected build.
Use separate build directories for concurrent configurations.

```sh
python3 scripts/native_boot_smoke.py --iso dist/vos5.iso --output /tmp/vos5-bios-check
```

The smoke gate requires physical/virtual memory initialization, the scheduler
transition and output from the actual user-space setup program. It rejects
panics, process faults and exec failures, verifies the exact online CPU count
and checks ISO identity before and after observation.
For UEFI, also supply `--firmware-code` and `--firmware-vars`; the variables
template is copied before use. No physical disk is attached by the smoke test.

## Consolidation and evidence

`consolidation/baseline.json` records the initial 5,072-path import from
`vos.v1`; additional manifests record reconciled agent boundaries and Limine
build dependencies. Legacy repositories remain in the parent directory and
are not runtime dependencies. The [eleven-source comparison](consolidation/reconciliation/README.md)
now records file coverage, local modifications and implementation choices for
43 components. Unretained variants and missing capabilities remain explicit
integration work; this comparison does not certify their implementation.
The [dated Hebrew unification status](docs/design/UNIFICATION_STATUS_2026-09-18_HE.md)
separates retained bytes, semantic decisions and runtime qualification, and
defines the remaining stages and exit criteria.

`python3 consolidation/inventory.py` compares the eleven historical source
trees by path and SHA-256. Its ignored JSON output is a comparison aid, not a
secret scan or proof of completed migration.

See [native boot evidence](docs/design/NATIVE_BOOT_STATUS.md) for current
results and remaining release gates, and the
[clean-build qualification](docs/design/evidence/native-clean-build-qualification.md)
for the fresh-source BIOS/UEFI matrix. The canonical remote is
`https://github.com/sz-spec/AiOS.git`.

### Unified build and dependency maintenance

From this directory: `make native -j4` builds the BIOS/UEFI ISO;
`make native-build-check` checks native dependency/packaging invariants;
`make dependencies-check` checks generated Python profiles and Python/Node locks.
See [dependency versions and compatibility exceptions](dependencies/README.md)
and [native build evidence](docs/design/NATIVE_BUILD.md). Hosted components require
their locked Python/Node environments; they are not dependencies of native boot.
