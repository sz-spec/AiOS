# Native process-isolation qualification — 2026-09-14

**PASS: four direct CPU access cases in each of four emulator configurations
(16/16).** The native diagnostic ELF ran at CPL3 on a clean build of commit
`113975f3fc4718f60bec412e07127bc8dca639a0`. Foreign-process read/write and
supervisor-kernel read/write attempts caused the expected task-local hardware
faults; the victim retained its entire canary page and continued after each
attempt. This is bounded runtime evidence, not full isolation certification.

## Reproduce

With the prepared builder image, Docker, QEMU and firmware installed:

```sh
python3 scripts/native_clean_qualification.py --isolation --output /tmp/vos5-isolation-run --seconds 40
```

The output directory must be new; the script archives committed HEAD, excludes
prior project outputs, pins the builder image ID and runs without network access:

```sh
make native -j4 HEADLESS_AUDIT=1 NATIVE_ISOLATION_TEST=1 BUILD_DIR=build/native-isolation INSTALLER_ISO=../dist/vos5-isolation.iso
```

The clean run archived 5,355 files and verified unchanged source inputs after
building. Build exit was 0; the same ISO passed all four VM observations and
its pre/post hashes matched in every case. Its owned build container was
confirmed absent. Total qualification time was 115.59 seconds. The compiler
environment was prebuilt, not rebuilt from compiler sources by this command.

The qualified local image is `dist/vos5-isolation-qualified.iso`, a diagnostic
image that directly launches the test instead of the setup wizard. The clean
source/build workspace remains `/private/tmp/vos5-isolation-clean-20260914`.
ISO binaries are not committed to Git; source, hashes and raw evidence are.

## Actual test and acceptance criteria

The normal ELF loader starts `test_native_isolation`. Parent PID1, victim PID100
and four disposable children explicitly read CS and report CPL3. Only the victim
maps and fills `0x7000000000`; attackers fork from the coordinator, which never
maps that address. Thus these attempts do not inherit the victim page through
legitimate fork/COW. Kernel diagnostics record the victim's physical frame and
distinct victim/attacker page-table roots. Each rejected foreign access sees an
absent mapping in the attacker's user root.

The kernel target is a dedicated aligned synthetic canary page. A test-only
walker checks effective U/S permissions through all page-table levels; the target
is present and supervisor-only in each attacker's user root. A noncanonical or
merely unmapped kernel address cannot satisfy this gate. Diagnostic hooks log
evidence and check canaries; they do not grant permissions, emulate the accesses
or change the fault-handling policy.

| Attempt | Required page-fault error | Required mapping | Result in each VM |
| --- | --- | --- | --- |
| Foreign-process read | `0x4` | absent from attacker | task-local SIGSEGV |
| Foreign-process write | `0x6` | absent from attacker | task-local SIGSEGV |
| Supervisor-kernel read | `0x5` | present, effective USER=0 | task-local SIGSEGV |
| Supervisor-kernel write | `0x7` | present, effective USER=0 | task-local SIGSEGV |

For every case the oracle requires this order: CPL3 access-attempt marker;
matching diagnostic PID/address/root record; actual user SIGSEGV with matching
CR2 and error bits; exact child wait status `139 << 8` (the current vOS ABI);
fresh victim challenge/acknowledgement after checking every byte of its canary.
After all four cases the victim exits zero and the parent executes further
sleep/getpid/privilege checks and emits a progress marker. Merely exiting139,
missing syscall support, a hung witness or a kernel-uaccess fault cannot pass.

## Matrix and artifacts

QEMU 10.2.2, q35/TCG, qemu64, 1,024 MiB RAM, 40 seconds per observation,
no network or host disk, and a private UEFI variables copy for each UEFI VM:

| Firmware | Requested / online CPUs | Cases passed | Victim + parent progress |
| --- | --- | --- | --- |
| BIOS | 1 / 1 | 4 / 4 | verified |
| BIOS | 4 / 4 | 4 / 4 | verified |
| UEFI | 1 / 1 | 4 / 4 | verified |
| UEFI | 4 / 4 | 4 / 4 | verified |

| Artifact | SHA-256 |
| --- | --- |
| ISO (12,832,768 bytes) | `549e8d49235cca5956faa7ae0b3879af7f663639d81a9d44a57f20412d928883` |
| Diagnostic kernel | `b5ee527f574bfcf9b08dd7f2f4c53b6808312beb1bbe35b12fd39dc2f0f85d63` |
| User test ELF | `c84417c80a5905882f4d77d8181cf1ee7841c97a7656ed6f6acbb5ada38ec394` |
| Builder image | `3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6` |

Full identities and outcomes: [aggregate result](native-isolation-2026-09-14/result.json),
[source manifest](native-isolation-2026-09-14/source-manifest.json),
[harness manifest](native-isolation-2026-09-14/harness-manifest.json),
[tool versions](native-isolation-2026-09-14/tool-versions.txt) and
[build output](native-isolation-2026-09-14/build.txt).
The [evidence index](native-isolation-2026-09-14/evidence-sha256.json) hashes the
saved records. The [secret-scan review](native-isolation-2026-09-14/secret-scan-review.json)
records 132 manifest-hash false positives, each checked against its source file;
no unresolved findings or new suppressions remain in that scan.
Raw serial logs and per-VM JSON are preserved under
[BIOS1](native-isolation-2026-09-14/bios1/serial.txt),
[BIOS4](native-isolation-2026-09-14/bios4/serial.txt),
[UEFI1](native-isolation-2026-09-14/uefi1/serial.txt) and
[UEFI4](native-isolation-2026-09-14/uefi4/serial.txt).

## Normal-build regression and independent review

The flag combination `NATIVE_ISOLATION_TEST=1 HEADLESS_AUDIT=0` fails at Makefile
parse time. With both flags zero, a separate normal build excluded the diagnostic
symbols, target environment string, test ELF and embedded program entry.
Its kernel SHA-256 is exactly the previously qualified normal kernel:
`137a383ef839840e1192b3d7f55b4944360597968b61ff76d5ef93e7c25ac610`.
It also passed a normal BIOS1 boot to the setup wizard. This regression reused
the bootloader in the development workspace and is not another clean-source run.
See [exclusion evidence](native-isolation-2026-09-14/production-exclusion.json)
and [normal boot result](native-isolation-2026-09-14/production-bios1/result.json).

The classifier's seven regression-test methods passed, including removal of
every required evidence line, wrong IDs/CPL/permissions/error codes/status,
canary/challenge/order failures, unexpected faults/exit/reboot and changed ISO.
The [security reviewer](native-isolation-security.md) independently reviewed
the kernel/user code and classifier. The classifier author separately reviewed
root's implementation and actual runtime evidence in the
[invariant report](native-isolation-invariants.md); this is not an independent
approval of their own classifier code or a formal proof of the entire kernel.

## Preserved development failures and remaining limits

- The first build failed to link `poll`: the small native library declares it
  but supplies no wrapper. The test now invokes the actual poll syscall.
  [Original link failure](native-isolation-2026-09-14/initial-link-failure.txt).
- The first VM failed before testing because this CRT passes `envp` to main
  while its separate `getenv` stub is uninitialized. The test now scans the
  passed environment with an explicit bound. No production libc change was
  needed. [Failed result](native-isolation-2026-09-14/development-env-failure/result.json)
  and [subsequent development pass](native-isolation-2026-09-14/development-bios1/result.json)
  remain separate from the final clean matrix.
- Existing compiler/linker warnings remain in the full build log. The normal
  regression build also reported a small directory clock-skew warning.
- These tests do not establish COW divergence, PROT_NONE/mprotect/NX correctness,
  stale-TLB revocation, malicious syscall-pointer handling, concurrent AP user
  execution, scheduling fairness, DMA/IOMMU isolation or speculative isolation.
  Four online CPUs are not evidence that these user workloads ran on APs.
- Physical-PC compatibility and bit-for-bit ISO reproducibility remain open.
  The diagnostic image is a test artifact, not a final OS release.

Next isolation work should qualify COW and page-permission transitions with
adversarial tests and victim liveness, then test mapping revocation under actual
concurrent workloads on multiple CPUs.
