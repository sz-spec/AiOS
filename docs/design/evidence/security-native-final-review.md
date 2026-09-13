# Native/build security review — 2026-09-14

## Scope and independence

Reviewed current kernel/user/musl Make dependencies, configuration fingerprints,
Limine state tracking, ISO publication, toolchain provenance, AP readiness code
and recorded boot/isolation evidence. No applicable AGENTS.md was found in the
workspace ancestry or scoped repository discovery. No production files were
changed and no redundant full builds were run for this review.

This reviewer did not author the native dependency graph, AP readiness or ISO
publication changes, so that inspection is independent of their implementation.
This same agent previously authored the musl upstream merge and toolchain
Dockerfile upgrade: examination of those portions is a self-review, **not an
independent supply-chain approval**. A second reviewer must cover those portions
if independent approval of every track is required.

## Evidence supporting the current change

- Previously identified assembly-header omission is corrected: kernel DEPS now
  includes every object and assembly compilation emits dependency files.
- Musl-linked programs depend on a content fingerprint of libc.so plus crt1,
  crti and crtn; a CRT-only change is no longer absent from the dependency graph.
- Kernel compiler/linker versions and musl assembler/linker/objcopy commands and
  versions are now recorded. Limine tracks vendored sources and output hashes.
- ISO packaging fingerprints the script and boot inputs, verifies BIOS/UEFI
  catalog entries, copies into destination-filesystem temporary storage and
  renames the complete image. Failed copying cannot truncate the prior ISO.
- Toolchain evidence records GCC16.2/binutils2.47 compilation and a networkless
  native build. BIOS4 and UEFI2 results under toolchain-2026-09-14 bind to ISO
  SHA256 929643fae8662cb9d794ad7028bca0ede27c1457b4e3ab35ea88cbd0c151e412.
  Both observations reached the user-space wizard for25 seconds. Repeated build
  retained that ISO hash. These checks do not demonstrate persistent install.
- AP readiness uses explicit starting/prepared/released/failed/canceled states;
  failed stack setup parks the AP rather than releasing it to scheduling. The
  earlier injected-failure evidence is distinct from ordinary successful boot.

## Concrete unresolved findings

| Finding | Consequence and required boundary |
| --- | --- |
| Download identity is pinned, publisher signatures are not verified | GNU/musl archives have recorded HTTPS download hashes; two matching downloads do not provide independent signer authentication. Document trusted publisher keys/signature checks before claiming authenticated release provenance. |
| Base images and apt packages remain mutable | Source pins alone cannot reproduce the complete toolchain or establish cross-host binary equality. Pin image digests and package snapshots if that release guarantee is required. |
| Tool fingerprints use names/version strings, not executable content | Replacing a tool in place with the same reported version can evade invalidation. CC overrides in Limine also are not individually queried for their executable identity. This graph assumes trusted, stable tool installations. |
| One writer per output tree is assumed | Source/stamp hashing and later compilation are not one transaction. A concurrent writer can alter inputs between them; Limine publishes several files individually. Use isolated worktrees/output trees and avoid shared flavor writes in CI. |
| ISO sidecar and image publication are separate renames | A crash between catalog and image rename can pair a new catalog with an old ISO. Consumers must validate the actual image/hash; do not trust the sidecar alone as an atomic release manifest. |
| UEFI boot evidence is not Secure Boot evidence | No tested firmware trust enrollment, signed EFI/kernel chain, revocation or rollback policy is established by the current BIOS/UEFI smoke results. |
| Process isolation remains explicitly partial | NATIVE_BOOT_STATUS documents retained direct-map PML4[256] and kernel PML4[511] in restricted roots. Do not claim complete KPTI/Meltdown protection from task-root creation alone. |
| New libc ABI paths need runtime qualification | Upstream pwritev2/RWF_NOAPPEND fallbacks, new interfaces and malformed iconv input behavior are not exhaustively covered by reaching the wizard. Preserved port-file hashes do not prove compatibility of all new call paths. |

The unsigned-source and mutable-base items are supply-chain assurance gaps;
they are not evidence that downloaded code is malicious. The single-writer
assumption protects build correctness only when enforced operationally; neither
a Makefile nor a digest file is a sandbox against a writer controlling the tree.

## Mathematical claims and implementation limits

Configuration invalidation requires that every consumed input influences either
a dependency edge or a checked fingerprint. Inspected corrections close the
specific missing assembly and CRT edges; they do not prove the graph complete
for arbitrary tool replacements or concurrently changing source trees.

No-op preservation is a measured property of the exercised build. Equal hashes
identify the tested artifact, not its behavioral security. A bounded Z3 result
proves a formula under its model assumptions; without an established mapping
from actual machine code, page tables, interrupt interleavings and hardware
semantics to that model it cannot certify the running OS. Likewise eBPF CO-RE
compilation does not establish verifier acceptance, live attachment or race
freedom on a target Linux kernel.

Conclusion: the reviewed build changes materially improve traceability and
invalidation, with concrete successful native boot evidence. This report does
not approve claims of complete process isolation, verified Secure Boot,
all-hardware support or overall OS security. Independent review of this agent's
own toolchain/musl changes remains a separate review obligation.
