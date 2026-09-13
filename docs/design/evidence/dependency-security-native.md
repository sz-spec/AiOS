# Native build dependency security review — 2026-09-13

Scope: build graph integrity and stale-artifact prevention in the root/kernel/user/musl build path. This review is independent of the coordinator's assembly/toolchain tracking changes; it is not a proof of kernel memory isolation or hardware security.

## Findings corrected

- **CRT-only edits did not reach musl-linked programs.** A phony musl check rebuilds CRT objects, but the copied loader preserves its mtime when libc bytes are unchanged. GNU Make then skipped the downstream link-input hash recipe. A minimal Make DAG reproduced this. The link-input stamp now has its own force-check prerequisite, after the loader check, and still preserves its mtime when hashes are unchanged.
- **ISO packaging ignored bootloader and packaging-script changes.** Added a content stamp for all six consumed Limine artifacts, the packaging script, selected bootloader directory and xorriso version. Both installer and in-tree image rules depend on packaging inputs.
- **Switching back to an older build flavor could leave the wrong shared installer.** Added an output-side stamp binding the shared installer path to the selected kernel and that flavor's packaging stamp. This changes when the selected kernel/input path changes even if that kernel's mtime predates the current ISO.
- Added missing compiler depfiles for the small fake/auxv interpreters. Existing kernel `.S` compilation now emits dependencies and includes all object depfiles, as implemented by the coordinator.
- Updated the generated musl version to `1.2.6-vos3` after the separately reviewed upstream merge; its generator now depends on Makefile.vos3 so the version header cannot remain stale after that edit.

## Targeted validation

`python3 scripts/test_native_build_config.py` includes:

1. Real user init/library compile: flags and benchmark marker, header edits, library edits, return to default hash, and stable no-op.
2. Real musl-linked executable compile in a disposable source copy: change only `crt/x86_64/crti.s` by adding a NOP; copied loader timestamp stays unchanged, link-input stamp changes, executable bytes change, subsequent no-op preserves executable timestamp.
3. Actual production ISO Make rules with a deterministic lightweight writer: bootloader edit, packaging-script edit and A → B → older A flavor selection all trigger correct packaging; repeat builds preserve output timestamp. This validates the graph, not ISO format or firmware execution.

The original two compiled checks passed before the vendor update (20.646 s). The standalone packaging graph test passed (5.583 s). The full rerun against musl 1.2.6 passed all **3 tests in 29.133 seconds**, recorded in `/private/tmp/vos5-native-review-config-tests.log`. Production ISO/build/boot validation must use the final Limine vendor snapshot and remains the coordinator's separate gate.

## Reviewed protections and remaining limits

Configuration stamps hash content and publish by atomic replacement, preserving unchanged timestamps. The embedded-binary generator validates filenames/C identifiers and input existence, compares actual output bytes and uses atomic replacement. Missing required packaging inputs fail rather than silently reusing a subset. These mechanisms prevent specified stale-build classes; hashes do not establish trust in the source/toolchain.

- Tool identity uses executable path/version text, not hashes of compiler binaries or every implicit system input. Cross-machine reproducibility and hostile-toolchain resistance are not proven.
- Assembly sources compiled directly as musl `.s` have no generated depfiles; a source scan found no `.include`/`#include` directives in those files. New include usage must add dependency tracking.
- Concurrent builds targeting the same installer output remain unsupported. Use distinct `INSTALLER_ISO` values per concurrent flavor, even though sequential switching is now covered.
- ISO publication now copies verified files to a generated directory on the destination filesystem and renames the complete image into place. Catalog and image are separate renames, not a single atomic pair; filesystem crash durability is not proven. Concurrent writers remain unsupported.
- The coordinator added bootloader source/tool/artifact fingerprint checking. The reviewed state includes all vendored files, build scripts, selected tool versions and output hashes. It does not hash tool binaries or every configure/environment input; this is not a hermetic build attestation.
- ISO boot catalog checks, real BIOS/UEFI execution, and musl syscall/ABI coverage are separate validations. Passing these dependency tests does not imply those runtime properties.


## Limine provenance and ISO publication follow-up

The reviewer independently checked the release-signing fingerprint against the [official Limine release page](https://github.com/Limine-Bootloader/Limine/releases/tag/v12.9.0), then reran GPG verification of the local archive/signature. GPG returned `VALIDSIG 05D29860D0A0668AAEFB9D691F3C021BECA23821` and exit 0. Trust is based on the fingerprint published by upstream; no web-of-trust identity guarantee is claimed.

The archive SHA-256 `adea922af3b9c8179a4676bcecc8e4df2f3ef72ad36b3f4afab44cbf5f265e36` matches `dependencies/limine-upgrade.json`. Independently compared **334 regular files** in that archive to `kernel/boot/limine`: all matched byte-for-byte. The coordinator's old-source comparison records no local modifications or missing old files in `/private/tmp/vos5-limine-localdiff.json`; this review did not repeat the entire old-tree comparison. Archive matching establishes provenance of those files, not runtime safety or absence of vulnerabilities in the bootloader.

`infra/build_iso.sh` was changed to publish through a destination-local generated temporary directory and rename, keeping the previous ISO intact on a partial copy failure. `python3 scripts/test_iso_publication.py` passed: one test with actual wrapper execution verifies partial publication-copy failure (exit 73), absent UEFI boot-catalog rejection, successful replacement, and temporary-file cleanup. Controlled fake ISO/boot tools are used to isolate failure behavior; this does not replace real firmware boot checks. Full native build/boot after the final Limine artifacts remains the coordinator's responsibility.

A separate review finding was sent to the coordinator: build_limine.sh must resolve a relative output path before changing into its temporary build directory. The coordinator owns that script and its final fix/validation.
