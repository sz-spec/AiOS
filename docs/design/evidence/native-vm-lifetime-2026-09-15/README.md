# Portable lifetime evidence

The [qualification report](../native-vm-lifetime-qualification.md) states the
implementation, source identity, measured limits and unresolved release gates.

- `memory`, `normal`, `tlb`, `isolation`: final matrices from source
  `3daf340141b90690a47bad84c51523c44c243e20`; each result binds the ISO and source.
- `source-manifest.json`: hashes of all archived source inputs for that commit.
- `pre-shm-*`: intermediate evidence from `3ce56da73271db2e30ac4b226db88bb72a979d50`,
  before the creator-first finalizer correction. Historical scan reports retain
  paths used before these intermediate records were renamed.
- `initial-failed-memory`: source `32187957757ab57d7820d0a63fa62c409003b126`, whose
  four-CPU orphan adoption failed. The failure remains part of the record.
- `dev-*` and `debug*`: development observations and QMP diagnosis. The first
  register-capture series repeats BSP state; see `debug-notes.json` for the limit.
- `observer-tests.txt`, `c-tests.txt`: final host regression output. `syntax.txt`
  preserves the earlier syntax checks and their compiler warnings.
- `security-independent.json`: independent archived-source/hash/raw-log review.
- `source-gitleaks.json`: scan of three implementation commits, no findings.
- `evidence-gitleaks-redacted.json`, `secret-scan-review.json`: retained hash
  detections, each checked against the corresponding clean source archive.
- `manifest.json`: SHA-256 of every other file in this packet, including failures.

Raw serial logs are named `serial.txt` and build logs `build.txt` for portable
review. Paths inside captured results describe the original local run. The
large source archives, compiler image and bootable ISOs remain local artifacts;
this packet contains hashes and observations, not copies of those binaries.

Observation timeout is the planned end of a successful smoke run; success
requires all scenario-specific records and intact artifact hashes. These tests
do not qualify all hardware, complete IPC authorization, concurrent shared-VM
protection or byte-identical ISO reconstruction.
