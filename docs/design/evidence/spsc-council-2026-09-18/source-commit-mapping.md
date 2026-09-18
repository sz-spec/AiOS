# Retrospective source-to-commit mapping

Reviewer: `/root/mcp_upgrade`, 2026-09-18. Read-only comparison; no build, VM, commit or tag. This establishes byte equivalence for the explicitly classified inputs, not that the historical ISO was built from a clean committed checkout. Manifest base revisions remain historical facts.

Method: enumerate each target Git tree with `git ls-tree -r -z`; read Git blobs with `git cat-file --batch`; SHA-256 each blob and compare every recorded manifest entry. Separately rehash manifest-listed files in the frozen source directory. Conservative native/build scope comprises all `kernel/`, `user/`, `infra/`, `scripts/`, `dependencies/` paths plus root Makefile and Dockerfile names; it is a superset, not a traced compiler-dependency closure. Other repository inputs remain explicitly separate.

## A: `cfd23f6a8595b13da005c59f1efacb6744e53832`

Frozen source: `/private/tmp/vos-health-final-20260917/source`. Manifest SHA-256 `31a1de002cb493da3c5835e74a5949e77817bff8ea94ff66824e9f26695e5bcf`; recorded original base `9728f7143fb0a012f0415cfff73b7f25cca5c37b`.

- Manifest entries: 6321; Git blob matches: 6320; differing: 1; absent in commit: 0.
- Frozen on-disk mismatches against manifest: 0.
- Conservative native/build scope: 3689 entries; mismatches/missing: 0.
- Commit-only entries outside manifest: 67; native/build additions among these: 0.

Differing manifest paths: 

- `docs/design/evidence/health-performance-research-2026-09-17.md`

Manifest paths absent from commit: none.

Native/build commit-only additions: none.

Frozen source mismatch: none.

Commit-only nonbuild paths (classification only, not included in the historical snapshot):

- `docs/design/evidence/health-performance-2026-09-17.md`
- `docs/design/evidence/health-performance-2026-09-17/SHA256SUMS`
- `docs/design/evidence/health-performance-2026-09-17/drivers/vos-final-uefi-runner.py`
- `docs/design/evidence/health-performance-2026-09-17/drivers/vos-health-diag-user.README.md`
- `docs/design/evidence/health-performance-2026-09-17/drivers/vos-health-diag-user.patch`
- `docs/design/evidence/health-performance-2026-09-17/drivers/vos-health-observe.py`
- `docs/design/evidence/health-performance-2026-09-17/drivers/vos-health-repeat.py`
- `docs/design/evidence/health-performance-2026-09-17/drivers/vos-preserve-health.py`
- `docs/design/evidence/health-performance-2026-09-17/experiments/diagnostic-current/build.log`
- `docs/design/evidence/health-performance-2026-09-17/experiments/diagnostic-current/source-manifest.json`
- `docs/design/evidence/health-performance-2026-09-17/experiments/diagnostic-old/build.log`
- `docs/design/evidence/health-performance-2026-09-17/experiments/diagnostic-old/source-manifest.json`
- `docs/design/evidence/health-performance-2026-09-17/experiments/pending-only/build.log`
- `docs/design/evidence/health-performance-2026-09-17/experiments/pending-only/dirty.patch`
- `docs/design/evidence/health-performance-2026-09-17/experiments/pending-only/source-manifest.json`
- `docs/design/evidence/health-performance-2026-09-17/experiments/rejected-compare/build-approved.log`
- `docs/design/evidence/health-performance-2026-09-17/experiments/rejected-compare/build.log`
- `docs/design/evidence/health-performance-2026-09-17/experiments/rejected-compare/candidate.patch`
- `docs/design/evidence/health-performance-2026-09-17/experiments/rejected-compare/dirty.patch`
- `docs/design/evidence/health-performance-2026-09-17/experiments/rejected-compare/kpti-tests.log`
- `docs/design/evidence/health-performance-2026-09-17/experiments/rejected-compare/source-manifest.json`
- `docs/design/evidence/health-performance-2026-09-17/final/assembly-review.json`
- `docs/design/evidence/health-performance-2026-09-17/final/build.log`
- `docs/design/evidence/health-performance-2026-09-17/final/deferred-part.disassembly.txt`
- `docs/design/evidence/health-performance-2026-09-17/final/deferred.disassembly.txt`
- `docs/design/evidence/health-performance-2026-09-17/final/dirty.patch`
- `docs/design/evidence/health-performance-2026-09-17/final/host-tests.log`
- `docs/design/evidence/health-performance-2026-09-17/final/kpti-roots.disassembly.txt`
- `docs/design/evidence/health-performance-2026-09-17/final/kpti-tests.log`
- `docs/design/evidence/health-performance-2026-09-17/final/rebuild.log`
- `docs/design/evidence/health-performance-2026-09-17/final/reconstruction-review.json`
- `docs/design/evidence/health-performance-2026-09-17/final/repeat-driver.log`
- `docs/design/evidence/health-performance-2026-09-17/final/repeat-summary.json`
- `docs/design/evidence/health-performance-2026-09-17/final/reproducibility.json`
- `docs/design/evidence/health-performance-2026-09-17/final/runtime-review.json`
- `docs/design/evidence/health-performance-2026-09-17/final/source-manifest.json`
- `docs/design/evidence/health-performance-2026-09-17/frozen-additions/docs/design/evidence/health-performance-research-2026-09-17.md`
- `docs/design/evidence/health-performance-2026-09-17/frozen-additions/scripts/test_deferred_empty_path.py`
- `docs/design/evidence/health-performance-2026-09-17/runs/baseline-replay/host-timing.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/baseline-replay/result.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/baseline-replay/serial.log`
- `docs/design/evidence/health-performance-2026-09-17/runs/bios-1/host-timing.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/bios-1/result.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/bios-1/serial.log`
- `docs/design/evidence/health-performance-2026-09-17/runs/bios-2/host-timing.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/bios-2/result.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/bios-2/serial.log`
- `docs/design/evidence/health-performance-2026-09-17/runs/diagnostic-current/host-timing.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/diagnostic-current/result.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/diagnostic-current/serial.log`
- `docs/design/evidence/health-performance-2026-09-17/runs/diagnostic-old/host-timing.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/diagnostic-old/result.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/diagnostic-old/serial.log`
- `docs/design/evidence/health-performance-2026-09-17/runs/pending-only/host-timing.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/pending-only/result.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/pending-only/serial.log`
- `docs/design/evidence/health-performance-2026-09-17/runs/rejected-compare/result.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/rejected-compare/serial.log`
- `docs/design/evidence/health-performance-2026-09-17/runs/uefi-1/invocation.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/uefi-1/result.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/uefi-1/serial.log`
- `docs/design/evidence/health-performance-2026-09-17/runs/uefi-2/invocation.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/uefi-2/result.json`
- `docs/design/evidence/health-performance-2026-09-17/runs/uefi-2/serial.log`
- `docs/design/evidence/health-performance-2026-09-17/summary.json`
- `docs/design/evidence/health-performance-invariants-2026-09-17.md`
- `docs/design/evidence/health-performance-security-2026-09-17.md`

## B: `0324521a2494b25c29a01b055ff8b38029819fb9`

Frozen source: `/private/tmp/vos-sysinfo-qualified-20260918/source`. Manifest SHA-256 `ac36f6a5bf65dab033f43696a21c571beb79d7ce3f1bd3db3b8a7d946a01d019`; recorded original base `cfd23f6a8595b13da005c59f1efacb6744e53832`.

- Manifest entries: 6392; Git blob matches: 6392; differing: 0; absent in commit: 0.
- Frozen on-disk mismatches against manifest: 0.
- Conservative native/build scope: 3693 entries; mismatches/missing: 0.
- Commit-only entries outside manifest: 48; native/build additions among these: 0.

Differing manifest paths: none.

Manifest paths absent from commit: none.

Native/build commit-only additions: none.

Frozen source mismatch: none.

Commit-only nonbuild paths (classification only, not included in the historical snapshot):

- `docs/design/evidence/sysinfo-abi-2026-09-18.md`
- `docs/design/evidence/sysinfo-abi-2026-09-18/SHA256SUMS`
- `docs/design/evidence/sysinfo-abi-2026-09-18/drivers/vos-create-failure-report.py`
- `docs/design/evidence/sysinfo-abi-2026-09-18/drivers/vos-final-uefi-runner.py`
- `docs/design/evidence/sysinfo-abi-2026-09-18/drivers/vos-health-observe.py`
- `docs/design/evidence/sysinfo-abi-2026-09-18/drivers/vos-preserve-sysinfo.py`
- `docs/design/evidence/sysinfo-abi-2026-09-18/drivers/vos-render-failure-pdf.py`
- `docs/design/evidence/sysinfo-abi-2026-09-18/final/build.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/final/dirty.patch`
- `docs/design/evidence/sysinfo-abi-2026-09-18/final/failure-report-data.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/final/host-tests.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/final/independent-review.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/final/musl-symbols.txt`
- `docs/design/evidence/sysinfo-abi-2026-09-18/final/reconstruction-review.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/final/runtime-review.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/final/source-manifest.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/frozen-additions/kernel/include/uapi/vos_sysinfo.h`
- `docs/design/evidence/sysinfo-abi-2026-09-18/frozen-additions/scripts/test_musl_sysinfo_failure.py`
- `docs/design/evidence/sysinfo-abi-2026-09-18/frozen-additions/scripts/test_sysinfo_abi.py`
- `docs/design/evidence/sysinfo-abi-2026-09-18/frozen-additions/user/include/vos_sysinfo.h`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/before-caller-review/bios/host-timing.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/before-caller-review/bios/result.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/before-caller-review/bios/serial.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/before-caller-review/build.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/before-caller-review/dirty.patch`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/before-caller-review/host-tests.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/before-caller-review/source-manifest.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/first-build/build-approved.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/first-build/build.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/first-build/dirty.patch`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/first-build/focused-tests.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/intermediate/first-build/source-manifest.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/runs/bios/host-timing.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/runs/bios/result.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/runs/bios/runner.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/runs/bios/serial.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/runs/old-iso-control/host-timing.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/runs/old-iso-control/result.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/runs/old-iso-control/serial.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/runs/uefi/invocation.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/runs/uefi/result.json`
- `docs/design/evidence/sysinfo-abi-2026-09-18/runs/uefi/runner.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/runs/uefi/serial.log`
- `docs/design/evidence/sysinfo-abi-2026-09-18/summary.json`
- `docs/design/evidence/sysinfo-abi-math-2026-09-18.md`
- `docs/design/evidence/sysinfo-abi-security-2026-09-18.md`
- `docs/reports/vos-performance-failure-2026-09-18.html`
- `docs/reports/vos-performance-failure-2026-09-18.pdf`

## Interpretation

Native/build equivalence is conditional on the counts above and does not prove binary reproducibility, identical toolchain/environment, executable mode identity, or absence of undeclared/generated inputs. This comparison hashes file content rather than Git mode metadata. Historical build logs, pinned image, manifests, ISO hashes and invocation evidence remain necessary. No release/tag/push authorization is inferred.
