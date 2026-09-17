# Preserved failure and factorial research — 2026-09-17

This is diagnostic evidence, not release approval. Originals were copied byte-for-byte; `sha256-index.json` records their original absolute paths, sizes and hashes. No binaries or source trees are included. All seven guest runs completed the expected 57-program sequence. A zero program exit is not NPU hardware qualification.

| Experiment | Configuration | Nonzero programs | Health msgs/s | Pushed / consumed | Health elapsed | Suite result |
|---|---|---|---:|---|---:|---|
| [final2-bios](final2-bios/result.json) | new baseline, BIOS | test_health_check=1 | 39047 | 39047 / 39047 | 1000 ms | FAIL |
| [final2-uefi](final2-uefi/result.json) | new baseline, UEFI | test_health_check=1 | 38987 | 39377 / 39377 | 1010 ms | FAIL |
| [old-control](old-control/result.json) | old unchanged ISO, BIOS rerun | None | 98165 | 98165 / 98165 | 1000 ms | PASS |
| [factorial-c](factorial-c/run/result.json) | new baseline with both old NPU/frontier user sources | test_npu_direct=1 | 93690 | 93690 / 93690 | 1000 ms | FAIL |
| [factorial-d](factorial-d/run/result.json) | old baseline with both new NPU/frontier user sources | test_npu_direct=1, bench_2026_frontier=1 | 96778 | 96778 / 96778 | 1000 ms | FAIL |
| [factorial-e](factorial-e/run/result.json) | new baseline with old standalone NPU source only | test_npu_direct=1, test_health_check=1 | 55227 | 55780 / 55780 | 1010 ms | FAIL |
| [factorial-f](factorial-f/run/result.json) | new baseline with old frontier source only | test_health_check=1 | 39934 | 39934 / 39934 | 1000 ms | FAIL |

## Artifact identities

- final2-bios: ISO SHA-256 `a0c99dc7e3aedf77e85c2e94cd128600fc40a87470b78a5aa352627901441472`; raw serial/result under `final2-bios/`.
- final2-uefi: ISO SHA-256 `a0c99dc7e3aedf77e85c2e94cd128600fc40a87470b78a5aa352627901441472`; raw serial/result under `final2-uefi/`.
- old-control: ISO SHA-256 `ce55108cb59e15af215d6861bc2d2a357ea99403bd32fe5f54f8fac750d7212c`; raw serial/result under `old-control/`.
- factorial-c: ISO SHA-256 `49188a78e05870281c23798ffc7216bd986e38e969e187c30f6ce337a35201ed`; raw serial/result under `factorial-c/run/`.
- factorial-d: ISO SHA-256 `637a8d56e0e5f6557d4488306e626e6ca326e54d23aeb2a760f203b5f947e8bb`; raw serial/result under `factorial-d/run/`.
- factorial-e: ISO SHA-256 `9f53f3e3e50c75a0a24295bf5345a14bdce09508ac1feeb8d8073b227d7af98a`; raw serial/result under `factorial-e/run/`.
- factorial-f: ISO SHA-256 `36f32b04e920e52dcc150ca1c0707d38ec79f07228277e3a46a707574f102285`; raw serial/result under `factorial-f/run/`.

## Provenance, interpretation and limits

- The baseline is based on commit `c1899b568a7974b46e31841c260cb0f7deb11f33` plus recorded local/untracked inputs. Per-experiment manifests identify the precise inputs; this is not qualification of that commit alone.
- Final2 reproducibility results and independent review are in `repro/`; both build logs retain clock-skew warnings. Matching artifacts are reproducibility evidence, not passing workload evidence.
- Host inventory records 99 tests in 57.364 seconds, focused checks and commands. Its tracked-diff capture was taken at inventory generation, not proof that all prior tests ran against a frozen tree.
- C/E build-result files were written before runtime and still say awaiting slot. Their later `run/result.json` and serial are the authoritative completed runtime records; originals were not rewritten. C also preserves the initial sandbox-denied build attempt and successful approved build log.
- D/F aggregate elapsed includes waiting for coordinator run-go. Their explicit run_elapsed_seconds are 116.944233 and 155.345786 respectively; do not compare the aggregate wait time as guest runtime.
- D preserves expected failures of new denial assertions against the old permissive kernel. C/E preserve the old standalone NPU probe failure against the new deny-all policy; other zero exits are not device validation.
- F retains the new standalone NPU probe and restores only old frontier, yet health stays slow. It rejects attributing this observed slowdown solely to the frontier-source change. D alone shows new user probes can coexist with fast health under the old kernel; this is not a complete mechanism diagnosis.
- Each cell contains one sample. Firmware, host scheduling, prior workload, guest state, compiler/build metadata and binary layout are potential confounders. Different kernel/user combinations need not imply an isolated kernel runtime effect. Do not lower the 80K threshold or discard failures.
- All reported health counters balance at completion; equality does not prove payload integrity, fairness or adequate throughput. These runs are single-CPU QEMU TCG evidence, not physical-hardware or production SMP qualification.
- Drivers retain original absolute paths and are provenance rather than portable one-command execution interfaces. Invocations also appear in build-command/result and guest result JSON. No QEMU was run while packaging this evidence.
- The coordinator is preparing a separate narrowed lifetime repair. None of these artifacts qualifies that later source; it requires a new manifest and new validation.
