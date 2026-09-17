# Retained AI-context readers: portable evidence

See the [checkpoint report](../ai-context-lifetime-2026-09-17.md) for results,
ownership assumptions and open safety/performance gates. No file in this packet
certifies full cancellation safety, concurrent SMP isolation or physical hardware.

## Inputs and reconstruction

`source-manifest.json` records 6,255 paths and SHA-256 values. Reconstruct its
source into an empty directory by extracting Git commit
`f8c0b827c650e66dd2059d8bb27a307cb69ff6da`, applying `tracked.patch`, and copying the
six paths under `frozen-additions/` onto their corresponding source paths.
Preserve executable modes. Rehash every path and reject extra/missing files.
`source-reconstruction.json` records an independent reconstruction that matched
every file without reading the original frozen tree. The additions preserve
documentation as it stood when frozen; the current report supersedes its status.

The local original snapshot is `/private/tmp/vos-ai-context-freeze-20260917/source`.
The independent reconstructed snapshot is
`/private/tmp/vos-ai-context-reconstructed-20260917/source`.

## Tests and artifacts

- `host-discovery-frozen.log`: final 106-test source-only discovery. The initial
  working-tree run and five native build checks are retained separately.
- `diagnostic/`: clean build invocation/configuration/log, artifact hashes,
  QEMU/firmware identity, sequential BIOS/UEFI 1/4-CPU commands and original
  serial logs. The diagnostic context test requires subsequent userspace output.
- `native-oracle-review.json`: independent reclassification of all four logs.
- `repro/`: two clean offline BENCH build logs/configurations and equality of
  the ISO, kernel and six Limine artifacts. Exact Docker commands are in the result.
- `full-suite/`: sequential BIOS/UEFI observations of that full-suite ISO;
  both complete 57 programs, with health alone nonzero. Original logs and failed
  results are retained. `drivers/full-suite-uefi.py` preserves the UEFI driver.
- `cancellation/`: diagnostic against the frozen production source. A zero
  diagnostic exit reproduces an orphaned reference; `closure` is explicitly false.
  `cancellation-pre-freeze/` preserves its earlier source version.
- `warning-review.json`: normalized compiler-warning comparison against final3;
  unchanged warnings are not thereby proven harmless.
- `local-artifacts.json`: identities of local ISO copies under
  `dist/qualification-ai-context-20260917/`; these are not final release images.
- `final-independent-review.json`: rehashed sixteen artifacts, both source trees,
  unchanged health ELF and independently reproduced full-suite failures.

`SHA256SUMS` covers every other file in this packet. It is an integrity index,
not a signature or an authenticated release manifest.

The diagnostic builder uses `AI_CONTEXT_LIFETIME_TEST=1 HEADLESS_AUDIT=1
BENCH_MODE=0`; the full suite uses `BENCH_MODE=1 HEADLESS_AUDIT=1` without the
lifetime diagnostic flag. Their ISO and kernel hashes intentionally differ.
Both use the pinned image and epoch recorded in their invocations. Generated
binaries and private UEFI variable stores are not committed as source evidence.

For two clean BENCH builds from reconstructed inputs, use the recorded builder:

```sh
python3 scripts/check_native_reproducibility.py \
  --source /path/to/reconstructed/source \
  --manifest /path/to/this/source-manifest.json \
  --output /path/to/a/new/output-directory \
  --builder sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6
```

The output directory must not already exist. Docker access and the locally
available pinned image are required; compilation itself has network disabled.
