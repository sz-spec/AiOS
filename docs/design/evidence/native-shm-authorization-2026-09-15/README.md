# Portable SHM authorization evidence

See the [qualification report](../native-shm-authorization.md) for the source,
contracts, tests and remaining security boundaries.

Each of `memory`, `normal`, `tlb`, `isolation` contains the recorded aggregate,
build/tool output and four BIOS/UEFI × one/four CPU observations. Raw serial logs
are retained as `serial.txt`, build logs as `build.txt`. Paths inside records are
original execution paths. `matrices.json` and `matrix-driver.py` preserve the
orchestration; `source-manifest.json` hashes the clean archived source inputs.

`security-independent.json` reclassifies raw observations with the archived
observers and recomputes source/artifact hashes. `observer-tests.txt` and
`c-tests.txt` preserve host controls, including actual production helper boundary
tests. `source-gitleaks.json` scans the implementation commit. The redacted
evidence scan and review identify recomputed source hashes rather than secrets.
`manifest.json` hashes every other packet file and excludes only itself.

An observation timeout is intentional; passing requires the complete strict
scenario records and unchanged artifacts. QEMU observations do not prove physical
PC support, concurrent SHM safety, complete IPC authorization, full SysV ABI
compatibility or byte-identical ISO rebuilding. Large binaries remain local;
this packet contains their hashes and observations.
