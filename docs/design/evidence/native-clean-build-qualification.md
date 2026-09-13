# Native clean build and boot qualification — 2026-09-14

**PASS for the bounded clean-source build and emulator boot gate.** A fresh
archive of commit `4082424c3f61b837c7052494566f385f4a0df745` built with one
`make native -j4` invocation, without network access or prior project outputs.
The same resulting ISO passed BIOS and UEFI with one and four CPUs. This closes
the build/boot prerequisite; adversarial process isolation is still unqualified.

## Reproduce

With Docker, the prepared cross-compiler image, QEMU and firmware installed:

```sh
python3 scripts/native_clean_qualification.py --output /tmp/vos5-clean-run
```

The output path must be new. The command archives committed HEAD, so commit any
intended source changes first. The recorded run used
`/private/tmp/vos5-native-clean-20260914-verified`; the resulting ISO remains at
`source/dist/vos5.iso` beneath that directory. A convenience copy is available
locally at `dist/vos5-clean-qualified.iso` (not tracked in Git).
Builder and firmware overrides are documented in [NATIVE_BUILD.md](../NATIVE_BUILD.md).

The compiler environment was prebuilt, pinned by image ID and used without
network access. This does not rebuild the compiler from source or provision
all host dependencies. The fresh source archive contained 5,328 files; original
source hashes remained unchanged after the build. Output directories and tracked
compiled inputs were checked before building. Reviewer inspection additionally
found no tracked bootloader `.bin`, `.efi` or `.sys` inputs reused by this build.
The host harness/classifier changes were separate from the archived OS sources;
their exact hashes are recorded in the harness manifest.

## Results

Each VM used QEMU 10.2.2, q35 with TCG, qemu64, 1,024 MiB RAM and a 35-second
observation window. No host disk or network was attached. UEFI used a private
copy of the recorded variables template for each VM.

| Firmware | Requested / online CPUs | PMM + VMM | Scheduler | User program output | Result |
| --- | --- | --- | --- | --- | --- |
| BIOS | 1 / 1 | observed | observed | setup wizard | PASS |
| BIOS | 4 / 4 | observed | observed | setup wizard | PASS |
| UEFI | 1 / 1 | observed | observed | setup wizard | PASS |
| UEFI | 4 / 4 | observed | observed | setup wizard | PASS |

All runs ended at the observation timeout with no classified panic, SIGSEGV,
wizard exec failure or address-space mismatch. All pre/post ISO hashes matched.
The build exited 0, and successful exact-name Docker queries confirmed removal
of its owned build container. Total corrected-run time was 105.38 seconds.
The strengthened classifier's nine regression tests passed; its author was
reviewed independently by the security reviewer.

Key SHA-256 identities:

| Artifact | SHA-256 |
| --- | --- |
| Source archive | `fe0a4047232e0ea8e284a0202bc4e115473740071a610124afd42be7b50a45f7` |
| Builder image | `3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6` |
| Qualified ISO (12,738,560 bytes) | `4b85acf0a51583aff2361408125fda4800827d0c13b92e4d1c596b19656009d8` |
| Kernel ELF | `137a383ef839840e1192b3d7f55b4944360597968b61ff76d5ef93e7c25ac610` |

## Preserved evidence and review

- [Aggregate result and firmware identities](native-clean-2026-09-14/result.json)
- [Source-file manifest](native-clean-2026-09-14/source-manifest.json),
  [harness identities](native-clean-2026-09-14/harness-manifest.json),
  [evidence-file hashes](native-clean-2026-09-14/evidence-sha256.json)
- [Compiler versions](native-clean-2026-09-14/tool-versions.txt) and
  [complete build output](native-clean-2026-09-14/build.txt)
- Raw serial logs: [BIOS1](native-clean-2026-09-14/bios1/serial.txt),
  [BIOS4](native-clean-2026-09-14/bios4/serial.txt),
  [UEFI1](native-clean-2026-09-14/uefi1/serial.txt),
  [UEFI4](native-clean-2026-09-14/uefi4/serial.txt).
  Each directory also contains its individual `result.json`.
- [Security review](native-clean-build-security.md) and
  [mathematical/evidence-invariant review](native-clean-build-invariants.md)
- [Secret-scan review](native-clean-2026-09-14/secret-scan-review.json): all 131
  generic-key findings were source-manifest SHA-256 values, each recomputed
  against its source file; no unresolved findings or new suppressions.

## Failures, warnings and limits

The [initial result](native-clean-2026-09-14/initial-failed-result.json) remains
`passed: false`: its build and all four boots succeeded, but the cleanup oracle
expected `No such` while Docker returned lowercase `no such object`. An independent
successful label query found no remaining build container. The fix uses successful
exact-name listing, ownership checks before removal, and an empty requery; the
entire build and matrix were then repeated in another fresh directory. The initial
result was not rewritten. Its [build log](native-clean-2026-09-14/initial-build.txt)
is preserved separately.

Compiler warnings remain, including unused variables, GNU-stack notes and an
ignored linker option. Make reported a newly created output-directory timestamp
0.00026 seconds in the future, followed by its clock-skew warning. This occurred
on the Docker bind mount; source inputs were unchanged, no previous outputs
existed, the build exited successfully, and the resulting ISO passed the matrix.
This is not a warning-free build qualification.

**Bit-for-bit ISO reproducibility remains open.** The two fresh runs produced
identical kernel and setup-wizard bytes but different ISO hashes. Read-only
comparison found directory/Rock Ridge timestamp differences and three differing
GNU build-ID payloads in `limine-bios.sys`; `limine-bios-cd.bin` differences were
not fully attributed. UEFI images/executables were identical. Consequently these
results prove a fresh build and the tested boots, not reproducible ISO packaging.

PMM/VMM messages, scheduler entry and wizard output are bounded observations.
They do not establish allocator correctness, scheduling fairness, CPL3 by an
explicit privilege probe, concurrent AP user workloads, successful installation,
full process isolation or physical hardware compatibility. Cleanup is confirmed
for this run; arbitrary interruption of the unnamed tool-version probe or a stuck
outer VM runner is not qualified by the harness.

The next gate is the [native process-isolation plan](native-isolation-test-plan.md):
four actual user-mode read/write attempts against another process and a synthetic
supervisor kernel page, correlated task-local faults, unchanged victim canaries
and continued victim/kernel progress. Missing setup or syscall support must fail
that gate rather than count as isolation success.
