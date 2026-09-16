# Native boot qualification — updated 2026-09-17

The [SHM creator-exit report](evidence/native-shm-exit.md) records normal and
fault termination, deferred release and surviving-mapping controls.

The [SHM authorization report](evidence/native-shm-authorization.md) records
creator identity, stale-handle protection and native foreign-process controls.

The [shared-VM lifetime report](evidence/native-vm-lifetime-qualification.md)
records COW metadata separation, explicit task/CPU references, owned file/SHM
backings and the observed AP-idle/init reparenting failure and correction.
The [review council](evidence/expert-council-2026-09-15/README.md) tracks the
remaining native, hardware, hosted and release requirements.

The [native TLB probe](evidence/native-tlb-qualification.md) now observes old
kernel translations before invalidation and replacements afterward on every
tested CPU, in BIOS/UEFI ×1/4 configurations. This qualifies the bounded kernel
remap primitive; shared-user-VM protection and lifetime are still separate gates.

The [gated AP user-execution test](evidence/native-smp-qualification.md) passes
BIOS/UEFI ×1/4 CPUs, including actual CPL3 CPUID observations on non-BSP CPUs.
The same workload with AP scheduling disabled fails the multi-CPU criterion while
passing single-CPU controls. This diagnostic gate does not enable production SMP
or establish simultaneous progress, shared-VM synchronization or TLB revocation.

The [memory-transition gate](evidence/native-memory-transitions-qualification.md)
passes 48 sequential cases after VMA/COW/mapping-boundary fixes. Normal boot and
the prior isolation suite also pass BIOS/UEFI ×1/4 CPUs on the changed kernel.
Shared-VM concurrency and remote TLB revocation remain unqualified.

The [fresh-source qualification](evidence/native-clean-build-qualification.md)
now passes BIOS and UEFI with both one and four CPUs against one ISO, with
mandatory PMM/VMM initialization, scheduler entry and user-program output.
It records the initial harness cleanup failure, corrected full rerun, exact
hashes and independent security/evidence reviews. Direct adversarial process
isolation is now covered for [four direct CPU access cases](evidence/native-isolation-qualification.md);
full isolation and bit-for-bit ISO reproducibility remain open. The detailed
implementation and earlier runtime results below retain their original scope.

**Native multi-CPU boot and the diskless setup workflow now work in QEMU.
This remains an integration build, not a final operating-system release.**
The previous setup-wizard syscall CR3 mismatch has been corrected. Evidence
for earlier failing builds remains under `evidence/native-2026-09-13/`.

## Current implementation

Each process owns full and restricted PML4 roots. Create/clone failure paths
release partial allocations; destruction frees the restricted top-level page
without freeing shared lower tables twice. The VMM software binding and the
entry structure are CPU-local. Scheduler/exec bind the selected process's
roots, and user-return preparation synchronizes that process's user mappings.
No syscall or IRQ transition uses a boot-global CR3 pair.

SYSCALL loads the process's full root before touching its dynamic kernel
stack. User IRQs enter on a CPU-local trampoline via TSS.RSP0; their complete
frames move to the task stack before C dispatch. Syscall, IRQ, initial-user
and fork/clone returns share an IRET trampoline that moves the return frame
before selecting the restricted root. User RSP travels in the saved task
frame for fork, clone and signals. Kernel and user GS are separate; FS/GS
base requests reject noncanonical/kernel addresses before MSR writes.

**Isolation is still partial.** Restricted roots retain PML4[256] (the direct
physical map) and PML4[511] (the kernel image). Full Meltdown isolation requires
minimal entry/descriptor/IST mappings, removal of the broad retained mappings,
and correct global-TLB/PCID treatment. CR3 changes currently use PCID 0 with
reload flushing; separate-PCID optimization and performance claims are pending.
Nested fault/NMI/SWAPGS windows, concurrent processes, shared-VM threads, mapping
revocation and signal/fork/clone execution need further adversarial runtime tests.

## Evidence and its limits

The latest SMP image results are recorded as `smp-*.json` beneath the
native evidence directory; `kpti-final-*.json` records the preceding image. Check each JSON's hash: earlier observations belong
to earlier images and do not certify the current artifact.

- QEMU TCG, q35, qemu64, one CPU, 1 GiB RAM: BIOS and EDK2 x64 UEFI reach real
  user-space setup output without the previous panic or SIGSEGV.
- The keyboard test uses a disposable VM with no attached disk or network.
  It selects private mode/workshop, enters a test owner, confirms setup and
  completes the wizard. This exercises real keyboard IRQs, blocking reads,
  user/kernel transitions and filesystem calls. It does not prove persistent
  installation, enterprise enrollment, AI inference or physical hardware support.
- The AP startup fault is corrected: APs now start on a private bootstrap
  root mapping only the low trampoline page, then switch to the runtime root
  from high kernel text. Runtime roots retain no low identity mapping. APs
  enable NXE before using page tables containing NX bits.
- The updated image passes UEFI with two CPUs and BIOS with four CPUs; the
  gate verifies the exact requested online count. A two-CPU diskless VM also
  completes all six keyboard-driven setup prompts. These checks do not yet
  qualify concurrent user workloads on APs. Startup failure handling still
  requires review.
- SYSCALL MSR setup is now separate from the shared dispatch-table setup and
  runs on each AP after GDT/GS initialization. The resulting image passes the
  four-CPU BIOS boot and two-CPU keyboard setup gates (`ap-syscall-*.json`).
  These are regression checks, not evidence of user syscalls executing on APs.
- The preceding image's one-CPU `max` CPU-model BIOS gate passes as well. This extends
  simulated CPU coverage only; it does not prove all optional protection paths.
- 27 host tests execute the actual production `kpti_roots.c` synchronizer via
  a compiled shared library, checking interleaved process roots, revocation,
  poisoned kernel slots, overlapping storage and missing roots. These replaced
  obsolete source-string assertions requiring the broken global-CR3 design.
- The 539-test backend audit suite passed after the initial transition wiring
  (107.24 seconds). It is not a privileged-runtime or hardware qualification.
- The native process-root lifecycle test passed allocation, clone, ownership,
  four injected top-level allocation failures and exact page-accounting checks.
  Its allocator fault-injection hook is excluded from production builds.

## Firmware topology and boot information correction

The latest `madt-*.json` evidence follows removal of guessed APIC IDs. SMP
uses the parsed ACPI MADT list and filters duplicate IDs. If firmware topology
is absent, only the BSP is retained; unsupported wide x2APIC destinations are
reported and skipped rather than truncated into another CPU's ID. Sparse-ID
and wide-ID physical systems remain unqualified, as does recovery from a
late AP after a startup timeout.

The assembly entry previously overwrote EAX's boot protocol magic while
printing to serial, causing genuine Multiboot2 boots to take the CMOS-based
PVH fallback. It now preserves that magic. The C adapter bounds tag iteration
and memory-map entries, copies ACPI RSDP tags into kernel-owned storage, and
prefers the ACPI 2.0 tag. The corrected UEFI boot validates the RSDP, walks the
XSDT/MADT and brings up the advertised two CPUs. Earlier images' successful
wizard output did not prove that the firmware memory map was being honored.

The boot parser is separated from the privileged entry wrapper. Its host
regression harness compiles the production C directly and checks valid memory
maps, zero/short entry strides, truncated/oversized tags, ACPI 2.0 precedence,
RSDP copy ownership and state reset. Tag alignment uses 64-bit arithmetic to
avoid wrapping a large 32-bit size. These checks do not certify arbitrary
firmware memory or the entire ACPI parser.

```sh
cc -std=c11 -Wall -Wextra -Werror kernel/tests/host/multiboot2_parse_test.c -o /tmp/vos5-multiboot2-test
/tmp/vos5-multiboot2-test
```

## Reproduction

Kernel objects now depend on a content-preserving configuration stamp containing
compiler/linker commands, flags, source list and reproducible-build epoch. A
local build check verified that unchanged flags preserve object timestamps,
while changed and restored flags rebuild objects, including with legacy make
mtime precision. Separate directories remain required for concurrent builds.
This does not yet track every external tool version or user-program build option.

```sh
bash infra/build_limine.sh
make -C kernel BUILD_DIR=build/native PRODUCTION=1 HEADLESS_AUDIT=0 iso
python3 scripts/native_boot_smoke.py --iso dist/vos5.iso --output /tmp/vos5-native-bios
python3 scripts/native_wizard_smoke.py --iso dist/vos5.iso --output /tmp/vos5-native-wizard --smp 2
```

UEFI smoke tests additionally take `--firmware-code` and `--firmware-vars`.
The variables template is copied before use. The keyboard test uses a local
QMP Unix socket, which may require sandbox permission; it attaches no physical
disk. Neither command installs the system on the host.

The lifecycle test is enabled only with `PROCESS_ROOTS_TEST=1`:

```sh
make -C kernel BUILD_DIR=build/process-roots-test PRODUCTION=1 HEADLESS_AUDIT=0 PROCESS_ROOTS_TEST=1 -j4
qemu-system-x86_64 -machine q35,accel=tcg -cpu qemu64 -m 1024 -smp 1 -display none -serial stdio -monitor none -nic none -no-reboot -kernel kernel/build/process-roots-test/vos3.elf
```

Its `[PROCESS-ROOTS] PASS:` marker certifies only the lifecycle checks.

## Other native corrections retained

Production defaults to provisioning, suppresses DEBUG logging and excludes
destructive boot-time filesystem diagnostics. The ELF loader copies through
a writable, non-executable alias. User-copy STAC/CLAC instructions are gated
by CR4.SMAP. The kernel loads at 32 MiB to avoid the tested UEFI low-memory
reservation; kernel stacks belong to the ELF data segment. Limine builds
independently from vendored source, and ISO packaging checks BIOS and UEFI
El Torito entries. The current Multiboot2 path is fixed-address; the separate
Limine-protocol/KASLR path remains unqualified.

Windows coexistence, Secure Boot/key enrollment, persistent storage/network
support, air-gap AI inference, broad physical-hardware coverage and the full
cross-source consolidation remain open release requirements.

## AP readiness and allocation-failure containment

AP startup now uses a per-CPU atomic handshake: starting, prepared, released,
failed or canceled. Readiness is published only after IST stacks, local syscall
MSRs, LAPIC and scheduler initialization succeed. The BSP counts/releases only
a prepared AP. A canceled late AP cannot enter its scheduler. Missing critical
IST storage parks the CPU with interrupts disabled instead of continuing.
Firmware topology indices are preserved after startup failure.

`ap-ready-*.json` records BIOS4, UEFI2 and keyboard2 regression tests. The
injection build uses `EXTRA_CFLAGS=-DVOS3_TEST_AP_IST_FAILURE_CPU=1` in a separate
build directory and ISO. Its ordinary two-CPU smoke fails as expected;
`ap-ready-negative-check.json` verifies that CPU 1 is parked, never reported
online or entering its scheduler, while BSP user-space setup still runs.
This does not simulate every allocation failure or prove concurrent AP user
workloads. Shared bootstrap parameters still require stopping further startup
after any failure. Some acquired resources remain reserved for parked CPUs.

Concurrent serial writes can interleave the SMP initialization summary. Smoke
tools also accept the kernel-main online-count summary, which reads the same
SMP count; they still require the requested CPU count and real user output.
