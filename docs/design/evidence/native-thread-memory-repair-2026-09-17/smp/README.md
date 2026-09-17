# Diagnostic native SMP qualification — 2026-09-17

Fresh snapshot of tracked and non-ignored untracked files, based on commit
`c1899b568a7974b46e31841c260cb0f7deb11f33`, built with
`HEADLESS_AUDIT=1 NATIVE_SMP_WORKLOAD=1 NATIVE_SMP_TEST=1`.
The pinned builder and complete command are recorded in `result.json`.

| Firmware | vCPUs | Result | Worker APIC IDs |
|---|---:|---|---|
| BIOS | 1 | PASS | 0 |
| BIOS | 4 | PASS | 0, 1 |
| UEFI | 1 | PASS | 0 |
| UEFI | 4 | PASS | 0, 1 |

Build and all four observer commands exited zero. Each observation lasted
45 seconds and ended with the observer's expected `observation_timeout`;
PASS requires complete deterministic CPL3 checkpoints, kernel scheduling
correlation, successful child waits and subsequent parent progress.
Total build/matrix duration was 208.637 seconds. Source and ISO hashes remained
unchanged. The build's clock-skew warning is preserved in `build.log`.

SHA-256 values:

- Dirty patch: `94f998d771bfc2fba99f596d4eaeaad9b533f8ddca5bc505cd3fc3491068c627`
- Source manifest: `cd9829304273c733e45295a40ff66b5fcb8926ed270203ede422b0ad02d4d92e`
- ISO: `28acdee910cab6adcad1169ec182e2429d9aeebca5c179d09931a6900d6be5f3`
- Kernel: `22e20e399dce99556c1045a8c490fdccd2e9854f74b018f64c76a17a44e2f230`

This is **diagnostic-only** evidence: real user execution on two hardware
CPU identities in QEMU's four-vCPU configurations. It does not establish
simultaneous execution, fairness, workload execution on every online CPU,
normal production SMP enablement, remote kill/reclamation safety, physical
hardware support, Secure Boot or byte-identical rebuilds. The full 57-program
suite was separately qualified on one BIOS CPU, not in this matrix.

Raw serial, per-case results, runner logs, build log, source manifest and patch
are copied unchanged. `source-verification.json` is a labeled extraction of
verification fields in the original aggregate result. `driver.py` preserves
the original local execution procedure; its absolute paths are provenance,
not a portable one-command rerun interface. No ISO binary is included here.
