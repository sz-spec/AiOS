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
