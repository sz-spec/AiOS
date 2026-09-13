# Native toolchain container update — 2026-09-14

Dockerfile.kernel moves from GCC13.2/binutils2.42 to GCC16.2/binutils2.47.
Latest release evidence: [GCC release table](https://gcc.gnu.org/releases.html)
and [GNU binutils project](https://sourceware.org/binutils/). Base images move
to [Debian13 trixie](https://www.debian.org/releases/stable/) and, for the
separate Linux eBPF runner, [Ubuntu26.04 LTS](https://releases.ubuntu.com/).

Both GNU source archives were downloaded over official HTTPS and hashed locally.
Exact URLs and SHA256 values are in dependencies/toolchain-upgrade.json. The
Dockerfile verifies each pinned digest before extraction. Detached signatures
were not verified, so this is not an independent signer-authentication claim.
Version overrides require corresponding digest overrides; mismatches fail.

Freestanding C compiler/libgcc configuration and x86_64-elf target are retained.
Runtime now includes host build tools, clang/lld, nasm, autotools and compiler
runtime libraries, needed for building modern Limine sources and running the
cross-compiler. OVMF supports UEFI test tooling. GRUB PC tools remain available
on amd64 containers; Limine is the primary native boot path on ARM64 builders.

The actual host compiler remains GCC15.2 and binutils2.46 snapshot; it was not
silently replaced. Existing native image tests against that host compiler do
not automatically qualify the newly built GCC16 container toolchain.

Actual Docker builds started on linux/aarch64:

- vos5-builder:dependency-upgrade, log /private/tmp/vos5-toolchain-docker-build.log.
- vos5-ebpf-builder:dependency-upgrade, log /private/tmp/vos5-ebpf-docker-build.log.

The eBPF image performs compilation at container run time. The wrapper requires
bpftool and running-kernel BTF for its CO-RE field access; updated comments no
longer claim opaque declarations eliminate this dependency. Image construction
does not demonstrate BPF verifier acceptance, attach success or race closure.

Pinned source bytes are only part of reproducibility. Base-image tags, apt
packages, host architecture, paths and complete build environment also affect
outputs. The prior claim of identical kernel bytes across all host operating
systems was removed because that property has not been demonstrated. Native
compilation and boot verification with the new compiler remain a distinct gate.

## Executed eBPF validation and hash correction

Ubuntu26.04 no longer brings bpftool through linux-tools-generic when installing
without recommendations. Added bpftool explicitly and retained the existing
wrapper's executable search path. Rebuilt image and executed the real CO-RE
compile against Docker's kernel BTF: zero warnings, exit0. taint_gate.o SHA256:
`de1266da0983a55ec84d0794269405fc01625e871484ed936703e285d444e6cc`.
This still does not claim program load or attach.

The first GCC build rejected an incorrect SHA measured while the download was
still running. Two subsequent completed official HTTPS downloads agree on
`e6738e29597f733270731aa90600f37ffdc045079dfc27ec7e8192cc81085c3e`;
the pin was corrected and the image build restarted. Verification was never
disabled to bypass the rejected artifact.

## Final container and native qualification

Builder image completed successfully. Runtime reports GCC16.2.0 and
GNU ld2.47.20260726. With network disabled, it compiled the vendored Limine,
musl, user programs and native kernel into a separate ISO:
`dist/vos5-toolchain-latest.iso`, SHA256
`929643fae8662cb9d794ad7028bca0ede27c1457b4e3ab35ea88cbd0c151e412`.
BIOS with4 CPUs and UEFI with2 CPUs both reached the user-space wizard without
classified faults in25-second observations. Results are preserved in
`toolchain-2026-09-14/bios4.json` and `toolchain-2026-09-14/uefi2.json`.

The initial Docker bind-mount build reported a new directory mtime0.00051
seconds in the future. A subsequent unchanged build completed without that
warning or rebuilding outputs and retained the exact ISO hash. Existing
GNU-stack linker warnings remain visible. This finite qualification does not
prove all program ABIs, physical hardware compatibility or cross-host byte
reproducibility. The previously validated host-built default ISO was preserved.
