# Native thread/memory repair qualification

This directory preserves the first 2026-09-17 host and single-CPU native
qualification for the uncommitted repair snapshot based on commit
`c1899b568a7974b46e31841c260cb0f7deb11f33`.
It predates the MMIO fail-closed and reproducible-packaging follow-up, so its
57/57 BIOS result applies only to the frozen snapshot identified below. The
later final-source bundle is recorded separately in this evidence tree.

## Frozen input

- 6,137 tracked and non-ignored untracked files were copied byte-for-byte.
- Dirty patch SHA-256:
  `94f998d771bfc2fba99f596d4eaeaad9b533f8ddca5bc505cd3fc3491068c627`.
- The container build and QEMU run did not change any file recorded in the
  source manifest; see `source-verification.json`.
- Builder image:
  `sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6`.
- Build environment included `SOURCE_DATE_EPOCH=1700000000`,
  `BENCH_MODE=1`, `HEADLESS_AUDIT=1`, and four build jobs.
- The build reported a clock-skew warning. The build exited successfully, but
  this run alone is not a proof of byte-for-byte reproducibility.

## Artifacts

- ISO SHA-256 before and after QEMU:
  `ce55108cb59e15af215d6861bc2d2a357ea99403bd32fe5f54f8fac750d7212c`.
- ISO size: 13,031,424 bytes.
- Kernel ELF SHA-256:
  `a3ba3609b75b68edba4278a3beb2cecfd07034fd3ef2884010aa77fc22128cd3`.
- `guest-result.json` SHA-256:
  `51e54f99a15567c1510b6d88bd5b7a44a18617751b92d769d703da79fa2bae5b`.
- `guest-serial.log` SHA-256:
  `d9e43f97563cd4a44e3e2c340ab6d984be877ddab2b00099d69ac315f789199e`.
- `source-manifest.json` SHA-256:
  `4c1778634dee0ba7d5c10fb76fd68bbcc403daec7888c83276bb6863e7173f94`.

## Results

- Full host discovery: 96 tests passed in 54.517 seconds.
- `make native-build-check`: all five invoked test groups passed.
- QEMU command used q35/TCG, 3 GiB RAM, one `qemu64` CPU, BIOS CD boot,
  virtio-net user networking and a snapshot disk policy.
- Complete BENCH_MODE sequence: 57/57 programs started and returned zero.
- `test_agent_chaos`: 4/4 passed. Fragmentation completed 1,115 operations,
  zero failures and a one-page final delta.
- `test_health_check`: 4/4 passed. SPSC throughput was 97,389 messages/second,
  above the unchanged 80,000 threshold.
- The observer reported no failures and the ISO hash was unchanged after the
  run.

## Boundaries

This run proves the complete workload on one emulated CPU through the BIOS
path. It does not prove UEFI boot, multi-CPU correctness, physical-hardware
compatibility, hostile DMA isolation, Secure Boot, Windows coexistence or a
second byte-identical ISO build. Host harnesses use mocks at documented
hardware and allocator boundaries. Those claims remain separate gates.

## Separate diagnostic SMP matrix

The [SMP evidence](smp/README.md) records four additional PASS results:
BIOS and UEFI with one and four vCPUs. The gated diagnostic image demonstrated
CPL3 worker execution on APIC IDs 0 and 1 in both four-vCPU runs; all source
inputs and the ISO remained unchanged. ISO SHA-256:
`28acdee910cab6adcad1169ec182e2429d9aeebca5c179d09931a6900d6be5f3`.
This adds diagnostic UEFI/SMP coverage, not a full-suite UEFI/SMP run,
production SMP qualification, simultaneous execution or hardware support.
