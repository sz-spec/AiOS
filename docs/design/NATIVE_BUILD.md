# Unified native build

From the repository root, `make native -j4` builds the standalone BIOS/UEFI ISO
at `dist/vos5.iso`. It checks required native build tools and bootstraps the
vendored Limine build when source, build settings, tool versions or artifacts change. `make native-bootloader`
explicitly rebuilds Limine after changing its source. Install the documented
cross compiler, NASM, Python, xorriso and mtools first; this command does not
install host dependencies or download them automatically.

For the complete fresh-source build and BIOS/UEFI × 1/4 CPU qualification,
with Docker, the prepared builder image, host QEMU and firmware installed:

```sh
python3 scripts/native_clean_qualification.py --output /tmp/vos5-clean-run
```

The output directory must not exist. The command archives committed `HEAD`
(uncommitted edits are excluded), verifies absent project build outputs,
resolves the builder to its immutable image ID, and executes `make native -j4`
without network access. It boots the resulting ISO in four disposable VMs and
writes source hashes, build output, serial logs and results under that directory.
Use `--builder`, `--revision`, `--firmware-code` and `--firmware-vars` to select
installed inputs; firmware defaults match the recorded macOS/Homebrew runner.
See the [qualification report](evidence/native-clean-build-qualification.md)
for exact identities, the initial harness failure and qualification limits.

The separate native process-isolation gate uses the same clean-build entry:

```sh
python3 scripts/native_clean_qualification.py --isolation --output /tmp/vos5-isolation-run --seconds 40
```

This explicitly enables `NATIVE_ISOLATION_TEST=1 HEADLESS_AUDIT=1`, creates
`dist/vos5-isolation.iso` with isolated kernel/user outputs, and launches the
native diagnostic ELF through the normal loader. It tests four direct CPU
access attempts and requires correlated kernel faults plus victim/parent
progress. Normal builds exclude this ELF and the kernel diagnostic hooks.
The flag combination `NATIVE_ISOLATION_TEST=1 HEADLESS_AUDIT=0` is rejected.
See [isolation evidence and limits](evidence/native-isolation-qualification.md).

The next suite adds COW, page-permission and unmap transition tests:

```sh
python3 scripts/native_clean_qualification.py --memory --output /tmp/vos5-memory-run --seconds 40
```

It adds `MEMORY_TRANSITIONS_TEST=1` to the diagnostic flavor and uses
`test_native_memory`. See [the memory-transition report](evidence/native-memory-transitions-qualification.md)
for corrected behavior, runtime results, explicit mmap compatibility limits and
the remaining shared-VM/remote-TLB qualification gap.

`BUILD_DIR` is relative to `kernel/`, defaults to `build/native-unified`, and
should be a relative path without whitespace. The repository itself may have
spaces in its absolute path. Use distinct build directories for concurrent
configurations; concurrent mutation of the same build directory is unsupported.

## One program list and isolated outputs

`user/programs.mk` owns the native program list. Both user Makefile and kernel
embedding use it, eliminating divergent lists. Each kernel build owns:

- `user/bin/`: compiled user programs and the embedded musl loader.
- `user/musl/obj/` and `user/musl/lib/`: musl objects, generated headers and CRTs.
- `generated/embedded_bins.c`: the matching embedded byte arrays.

The old shared `user/build` and `user/musl/obj` outputs are not required by the
kernel path. Direct user builds still default to `user/build` for convenience.

## Dependency and option handling

Kernel, user and musl configuration stamps have independent namespaces.
User stamps include C/SSE flags, linker options, program list, load base,
compiler version and source-date epoch. musl tracks C/assembly/link options,
source selection, compiler version and epoch. Changed configuration invalidates
objects; identical configuration preserves timestamps. Whole-second timestamp
handling supports the local legacy make version.

User compilation emits dependency files targeted at the resulting program
(or library object), so header changes propagate to the actual linked binary.
The musl-generated header templates/generator and libc linker script are
prerequisites; musl C/CRT dependency files are included. BENCH_MODE is enabled
only by `1`; setting it to `0` does not silently leave benchmarks enabled.

Kernel build checks the recursive user build once, then hashes the selected
program bytes into an embedding stamp. An unchanged stamp does not regenerate
the embedded table. The embedding generator also preserves existing output if
newly generated content is identical. Missing binaries are rebuilt before the
stamp is computed, and failures stop packaging.

## Verification recorded in this change

`make native-build-check` builds real init/library code in a disposable source
copy and checks no-op behavior, benchmark/filter changes, restoration to exact
normal bytes, header changes and library changes. It edits no project sources.

Full kernel experiments additionally checked that benchmark settings reach
embedded program bytes, restoring defaults restores the original init and
embedding hashes, user benchmark changes do not rebuild musl, and a no-op
preserves timestamps for the kernel, embedding, init and musl. See native
boot evidence for the separately recorded ISO runtime checks.

## Unified dependency commands and remaining limits

Python declarations now have one catalog and five hashed platform/profile locks.
`make dependencies-check` verifies Python input/lock fingerprints and all eight
Node manifest/lock pairs offline. `make python-lock` refreshes compatible direct
and transitive Python versions; it requires uv and registry access. With locked
Node dependencies installed, `make hosted-build-check` runs the frontend type
check and builds MCP, SDK and code-review components. Use Node 26.8.2 or newer
compatible Node 26; see [dependency documentation](../../dependencies/README.md).

The native path now uses musl 1.2.6 with preserved VOS additions and Limine 12.9.0
from its signed upstream source archive. Assembly header dependencies, musl CRT
content, compiler/linker identities and ISO packaging inputs are tracked.
Selecting a different flavor updates the shared ISO content fingerprint.
Interrupted final copying preserves the prior ISO through temporary-file rename.
Concurrent writers must still choose distinct `INSTALLER_ISO` paths.

Final BIOS (4 CPUs), UEFI (2 CPUs) and keyboard setup (2 CPUs) passed against ISO
`f53617d8b6c9ef4bbec4790562a539cde992b1a632c1b8b926a9815fa0748e2e`.
A no-op preserves kernel, embedded table, init, musl and ISO bytes and timestamps.
Evidence is under `evidence/native-2026-09-13/latest-dependencies-*.json`.

Hermetic host-tool provisioning, cross-machine reproducibility, complete musl
ABI qualification, production desktop assets and physical hardware qualification
remain open. Root build commands do not certify every hosted feature or every OS
release requirement. Current dependency compatibility exceptions are explicit in
`dependencies/upgrade-exceptions.json`.
