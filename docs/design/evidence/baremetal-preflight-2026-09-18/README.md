# Bare-metal CPU preflight evidence — 2026-09-18

This package binds the early CPU prerequisite gate to one production ELF,
one standalone EFI build and one canonical Limine BIOS/UEFI ISO.

## Result

- The host evaluator and source invariants pass 4/4.
- The production kernel ELF and standalone EFI object compile.
- The canonical Limine ISO reaches user space in BIOS and UEFI with one and
  four vCPUs.
- Each of the twelve mandatory QEMU CPU features, when hidden in isolation,
  produces `CPUF` and halts before the Multiboot2 bootstrap writes EFER.
- The validated BSP CPUID snapshot is preserved; the later phase adds only
  the microcode revision.

The mandatory contract is FPU, TSC, MSR, PAE, PGE, PAT, FXSR, SSE, SSE2,
SYSCALL, NX and long mode. The BSP, AP trampoline, EFI entry and C evaluator
use identical masks; `scripts/test_baremetal_preflight.py` enforces this.

## Deliberately open validation

The negative feature-mask logs exercise the canonical Multiboot2 path. A
heterogeneous AP whose features differ from the BSP cannot be modeled by the
installed QEMU configuration, so the AP refusal is code-reviewed and compiled
but not observed at runtime.

The standalone `vos3.efi` build is rejected by OVMF as `Unsupported` before
its banner under both the normal and `-nx` configurations. Those failed runs
are retained in `direct-efi/`. Therefore this package does not claim direct-EFI
runtime qualification. The shipped ISO uses Limine with Multiboot2 for both
BIOS and UEFI and is covered by the four positive boot observations.

This is QEMU TCG evidence. It is not physical-hardware qualification.

## Contents

- `boot/`: raw serial logs and classifier results for BIOS/UEFI × 1/4 vCPU.
- `feature-matrix-runner/`: reproducible twelve-feature refusal matrix.
- `feature-matrix/`: the original diagnostic run retained without deletion.
- `direct-efi/`: preserved unsuccessful standalone-EFI observations.
- `manifest.json`: source and artifact identity, host and commands.
- `SHA256SUMS`: hashes for every evidence file.
