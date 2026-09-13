# Independent native vendor and toolchain review — 2026-09-14

This reviewer did not author the musl vendor update, toolchain container upgrade or Limine vendor update. Scope: `Dockerfile.kernel`, the three upgrade manifests, preserved vendor files, locally retained release archives, and recorded qualification artifacts. No download, full build, source edit or commit was performed. Earlier build-system work by this reviewer is not reclassified as independent review here.

## Independently checked integrity

Recomputed SHA-256 directly from the retained archives and compared with their manifests:

| Artifact | Result |
| --- | --- |
| musl 1.2.5 and 1.2.6 archives | both match |
| binutils 2.47 archive | matches |
| GCC 16.2.0 original completed archive | matches |
| GCC 16.2.0 second completed archive | matches same digest |
| Limine 12.9.0 archive | matches |

The checks confirm current retained bytes match recorded pins. GCC's pin is `e6738e29597f733270731aa90600f37ffdc045079dfc27ec7e8192cc81085c3e`; binutils is `154ab23b60070e8f27013c22977f1129425d67d1e8acd6e13010e617811e4cff`. These are integrity checks, not independent authentication of GNU/musl release signers. Their detached signatures were not verified by the update and were not verified by this reviewer.

Musl checks independently confirmed all **seven preserved VOS port additions** retain their recorded hashes; all **133 changed plus 14 added** upstream files match both current-tree manifest hashes and bytes read directly from the 1.2.6 archive. All **51 removed** upstream paths are absent. `Makefile.vos3` was deliberately outside the vendor merge and is not counted among preserved port hashes. Thus preservation is supported by actual content checks, not simply a copied directory or version label.

Limine checks independently confirmed all **307 added/updated files** match their recorded hashes and all **343 removed** paths are absent. The manifest reports a valid signature against upstream-published fingerprint `05D29860D0A0668AAEFB9D691F3C021BECA23821`; this reviewer verified the archive/content hashes but did not independently repeat signature verification. Neither the reported signature nor this content check establishes a web-of-trust identity claim.

## Toolchain build security

The Dockerfile downloads versioned source archives over HTTPS and chains `sha256sum -c` with `&&` **before extraction/configuration**. There is no ignored-failure fallback. Overriding a version without supplying its matching digest fails; overriding both is an explicit trusted build-input change. The previously rejected partial-file GCC digest was corrected to the digest of completed downloads; verification was not disabled.

The x86_64-elf target, freestanding C compiler and target libgcc configuration are retained. Runtime/compiler dependencies support the actual builder architecture, with GRUB PC packaging restricted to supported amd64 containers and Limine available for the primary boot path. Downloaded source/build scripts run inside the builder's trust boundary; root container execution is not a sandbox for hostile source when writable host mounts are supplied.

Source pins alone do not provide whole-image reproducibility: Debian tags, apt resolution, build architecture and environment remain mutable. No full compiler/bootstrap trust proof, compiler advisory audit or emitted machine-code proof follows from building an updated compiler. Existing freestanding musl flags such as `-fno-stack-protector` and reduced subsystem/stub coverage must not be advertised as universal hardening merely because upstream musl was upgraded.

## Qualification evidence and its limits

Rehashed `dist/vos5-toolchain-latest.iso` and confirmed:

`929643fae8662cb9d794ad7028bca0ede27c1457b4e3ab35ea88cbd0c151e412`.

The recorded BIOS/4-CPU and UEFI/2-CPU reports in `toolchain-2026-09-14` reference that exact hash, contain no classified failures, and report scheduler/user-space wizard output after 25-second observations. `observation_timeout` is the planned finite observation boundary, not evidence of a completed installation. Their execution was performed by the integration agent; this independent pass verifies artifact/report correspondence rather than claiming another boot run.

The musl update includes changed `pwrite` behavior involving `pwritev2`/`RWF_NOAPPEND` and fallback error handling. Preservation of VOS syscall stubs does not prove new libc paths have compatible syscall numbers, errno, append semantics or cancellation behavior. Iconv source correspondence confirms imported corrected bytes, not an executed malformed-encoding regression against VOS-built libc. Full ABI behavior, physical hardware, process isolation and installer behavior remain separate gates.

The recorded eBPF compile result is compilation only; no verifier/load/attach or running-kernel race/security proof is inferred. No unrelated native or hosted suite was rerun in this review.

## Disposition

No checksum bypass, lost recorded port addition or manifest/current-content mismatch was found. Vendor provenance and bounded build/boot evidence support this upgrade within the stated scope. Release-signature verification gaps for GNU/musl, mutable build inputs, incomplete ABI/runtime qualification and absent broad hardware/security proofs remain explicit limitations.
