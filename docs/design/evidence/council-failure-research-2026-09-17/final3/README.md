# Final3: context-detach repair qualification

2026-09-17. **Release gate remains failed.** This snapshot follows final2 and
the C–F diagnostic matrix. Its new production change is the limited
AI-context detach-before-release correction; existing-reader lifetime and
concurrent create/destroy are still open.

| Check | Result | Evidence |
|---|---|---|
| Host discovery | 100 tests pass, 56.215 s | [log](host/discovery.log) |
| Native build invariants | 3 + 1 + 1 tests pass | [log](host/build-check.log) |
| Two clean offline builds | All eight artifacts byte-identical | [result](repro/result.json), [independent hashes/BIOS review](repro/independent-review.json) |
| BIOS full suite | FAIL; 57 completed, health only nonzero, 38,776/s | [result](bios/result.json), [serial](bios/serial.log) |
| UEFI full suite | FAIL; 57 completed, health only nonzero, 37,599/s | [result](uefi/result.json), [serial](uefi/serial.log), [independent review](uefi/independent-review.json) |
| Input conservation | All 6,189 manifest files unchanged in freeze and both build trees; working-tree differences limited to docs | [revalidation](source-revalidation.json), [manifest](source-manifest.json) |

Both guests completed their exact 57-program sequence, with no timeout.
The unchanged health threshold is 80,000 messages/second. Each firmware row
contains one sample; no statistical improvement/regression magnitude is
claimed from this pair. NPU denial probes pass while real hardware remains
explicitly unavailable.

ISO SHA-256: `bcd3e545f38de19ce0c248dae1701be9f7c4386cd6453d5dbeac53932429d62c`.
Kernel SHA-256: `dd44631c660b99aee079959a17cff97c25f156baf1ac415d1045e59e8dc8c8d4`.
Manifest SHA-256: `e0fe76941a5eab65d982b19cbb9f1f1ff07bbb73d0894418416a6750b08e7fc4`.

The source includes local tracked and untracked changes over
`c1899b568a7974b46e31841c260cb0f7deb11f33`. The manifest, not the base commit
alone, identifies the tested bytes. Later report edits and new evidence are
outside that frozen input set. `tracked-before-build.patch` records the
tracked delta only; it does not contain untracked inputs by itself.

The same pinned Docker builder was used twice on this host with no network,
normalized source times and fresh build directories. This is a bounded
reproducibility result, not independent toolchain trust. Both build logs retain
generated-directory clock-skew warnings. The precise commands and firmware
hashes are in the result/invocation JSON; the custom UEFI runner is preserved.
No disk was attached to the guests and no Secure Boot claim is made.

`sha256-index.json` records original locations and hashes of verbatim copied
evidence. It excludes this README and itself. The separate context-review
folder preserves the same-harness baseline failure and repaired-source pass.
