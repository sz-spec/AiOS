# SHM creator authorization — 2026-09-15

This stage closes the council's direct foreign-caller SHM destruction defect and
prevents stale region handles from targeting a replacement region. It qualifies
sequential authorization and ownership behavior, not complete IPC security or
concurrent shared-address-space mutation.

## Authorization and identity

Source `9e58227056eeb0ec0f6a5b14330406488a26a542` gives every registered task a
nonzero immutable 64-bit identity cookie. The central allocator runs under the
task-table lock before publication; fork and CLONE_VM registration replace the
copied parent cookie. Numeric PID/TID collisions therefore do not confer creator
authority. Exhaustion refuses further identities instead of wrapping. The new
field is appended so existing assembly offsets remain unchanged. Failed task
creation releases its empty descriptor table, XSAVE allocation and stack.

The creator's identity is captured when SHM is created. Public destruction checks
the current task's nonzero cookie under the same registry lock used for the
creator-close token and reference decrement. Failure changes neither token nor
reference ownership. NULL current tasks, kernel address spaces and numeric TID 0
or 1 receive no destruction bypass. There is no newly exported trusted-destroy
API. Internal mapping/rollback/reaper releases retain their existing owned-reference
contract and do not impersonate a creator.

Authority is task-local: fork/CLONE_VM children cannot close the creator reference,
even when sharing an address space; PUBLIC permits mapping, not destruction.
Exec preserves the task principal. Creator exit does not automatically transfer
or reclaim creator authority in this stage. Cross-owner dispatcher housekeeping
also fails the checked API; a future delegated cleanup protocol needs explicit
ownership rather than a TID-only bypass.

## Region handle and syscall contract

SHM IDs are opaque positive 32-bit handles: six low slot bits and 25 generation
bits. Slots 1–63 are usable; each allocation increments its slot's generation,
starting at one. Lookup validates the complete stored ID. A generation-saturated
slot is retired permanently, never wrapped; the slot remains reserved throughout
last-reference cleanup. Name lookup returns the complete handle and size lookup
holds the registry lock while reading the region. Handles are not secret tokens:
authorization still depends on creator identity.

SHM destroy/map/unmap/size wrappers reject a 64-bit argument larger than UINT32_MAX
before narrowing it. Destruction returns -13 for a foreign or missing principal,
-3 for an absent/stale region and -2 for duplicate creator close with live mappings.
Wide arguments return -22. Linux-number alias 31 supports only command 0
(IPC_RMID); other commands return -22 without destruction. Other legacy aliases
remain partial VOS conventions, not a qualified SysV IPC ABI implementation.

Ordinary creation rejects the DEVICE flag and overflowing size rounding. Device
creation remains a separate API with its existing checks; physical device and
hardware-authorization qualification are not supplied by these RAM tests.

## Adversarial controls

The existing memory workload now requires 46 user records and five exact kernel
markers. Added native controls cover:

- A foreign fork child denied destruction through syscalls 411 and 31 and denied
  private mapping; a CLONE_VM child also denied creator close, with the parent's
  canary and valid owner cleanup preserved.
- A PUBLIC region mapped by another process without destruction authority. After
  creator close, the child's mapping remains readable/writable until its last
  detach, after which the region disappears.
- Reallocation of the same registry slot with a new generation. Old destroy,
  map, unmap and size requests cannot affect the replacement. Unsupported alias
  commands and wide IDs leave the new canary and valid ownership intact.
- A real kernel region tested with zero and different identity cookies while
  numeric TID stays unchanged. Local interrupts are excluded during this test-only
  substitution; identity is restored before interrupts and assertions. Denials
  preserve backing; the restored owner closes normally. This is a controlled
  identity substitution, not an actual billions-of-tasks TID-wrap experiment.

Host tests compile extracted production handle and cookie helpers to exercise
maximum values, exhausted slots, stale generations and permanent cookie exhaustion.
They do not substitute for hardware or locking evidence. The observer rejects
missing/repeated records, forged identities, wrong slot/generation relationships,
missing CLONE_VM denial and the historical shorter kernel markers.

## Qualification

All four clean-source matrices pass BIOS/UEFI × one/four CPUs in QEMU q35/TCG,
using source `9e58227056eeb0ec0f6a5b14330406488a26a542`. Each archive contains
5763 files, begins without generated outputs and retains unchanged inputs.

| Matrix | Passed boots | ISO SHA-256 |
|---|---:|---|
| memory | 4/4 | `3bfbb2771ae8d605d70c273c8d67445c03cec9408c4a6622e11e4f95a3e12a94` |
| normal | 4/4 | `1dd93751f4c7ba96317dbfcb61ce4e5cb7f7cbf1960b593c57f8e2a188d314b1` |
| tlb | 4/4 | `559433d883ced60ad1f145f983ff27ea120ec8d221e405fd9474f3686df1b405` |
| isolation | 4/4 | `865a12d6a916aedfff4e65023afeea3d0765458d4dd75cdd4c2fe6a8913f837a` |

All sixteen boots preserve the ISO hash. The memory matrix includes 48 original
memory cases across four runs plus the prior lifecycle/backing controls and all
new authorization controls. The prior isolation matrix retains sixteen direct
cross-process/kernel access attempts; the TLB matrix retains its bounded kernel
translation/AP workload. Neither is proof of general concurrent shared-VM safety.

The local diagnostic image is `dist/vos5-shm-auth-qualified.iso`; the normal image
is `dist/vos5-shm-auth-normal-qualified.iso`. Both are read-only copies with the
hashes above. Independent security review recomputes hashes and reclassifies the
raw archived logs; the mathematical review independently checks the new memory
records. All 37 observer methods and three actual-C test methods pass.

Reproduce with a fresh output directory:

```sh
python3 scripts/native_clean_qualification.py --revision 9e58227056eeb0ec0f6a5b14330406488a26a542 --memory --output /private/tmp/vos5-shm-auth-new
```

Omit `--memory` for the normal image; use `--tlb` or `--isolation` for the other
separate diagnostics. The normal image keeps diagnostic defines disabled.


Build logs preserve the existing clock-skew/objdump diagnostics and legacy
benchmark signed-overflow warning. The benchmark is not part of this gate.
Fresh output checks, unchanged source hashes and native observations qualify the
stated behaviors; they do not constitute a warning-free build or a performance claim.

## Remaining boundaries

General PID/TID consistency, concurrent SHM lookup/unmap lifetime, shared VMA/PTE
mutation and remote permission revocation remain open. Creator-orphan reclamation,
capability delegation, full Linux IPC compatibility, device access, physical PC
coverage, signed installation/update recovery and byte-identical ISO rebuilding
are separate gates. No claim of exhaustive IPC security follows from these tests.

[Security review](native-shm-authorization-security.md) ·
[Mathematical review](native-shm-authorization-invariants.md) ·
[Portable evidence](native-shm-authorization-2026-09-15/) ·
[Council actions](expert-council-2026-09-15/ACTIONS.md)
