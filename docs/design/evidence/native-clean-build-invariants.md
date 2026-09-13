# Native clean-build and boot evidence invariants — 2026-09-14

Independent review of scripts/native_boot_smoke.py, its regression tests,
native_wizard_smoke.py and the PMM/VMM/scheduler/user output paths. No production
changes or Docker runs by this reviewer. Root owns fixes and execution.

## Artifact and matrix obligations

A clean build must start with absent, uniquely owned output directories for
kernel, user, musl and bootloader artifacts; deleting only the kernel ELF is not
equivalent. Record source/tool/config fingerprints and image SHA256. Hash the
image before and after every run and require equality, otherwise the log can
be attributed to bytes different from those QEMU read. Record QEMU version,
firmware hashes, CPU model, requested CPU count and observation duration.

Run each tuple in {BIOS, UEFI} x {1, multiple CPUs} separately against the same
image. A successful multi-CPU BIOS observation cannot substitute for single-CPU
UEFI. Require a new serial log per run and a private UEFI variable store copied
from the identified template. Host disks and network should remain unattached.

## Observable pass predicate

For each run define P = artifact identity stable AND observation ended by the
bounded harness timeout AND PMM progress AND VMM completion AND scheduler
transition AND user-program output AND exact requested CPU count AND no
classified fatal condition. Every conjunct must influence passed; reporting a
boolean without including it in P does not enforce the invariant.

- PMM evidence should include its statistics after initialization, not merely
  the initial "Initializing Physical Memory Manager" message. Available memory
  must be nonzero for this test profile. Do not impose free+used=total without
  accounting for separately reserved kernel/firmware pages and rounding.
- VMM evidence should include its explicit initialization-complete marker;
  page-table physical address should be nonzero/page-aligned if validated.
  The identity-map-cleared message is useful corroboration, not proof every
  remaining mapping has correct permissions.
- Scheduler evidence must be mandatory rather than informational. The printed
  entry transition alone is not a scheduling fairness or context-switch proof.
- The wizard banner is emitted by user/src/setup_wizard.c. It demonstrates that
  this program's output path executed in the current boot design. It does not
  independently measure CPL3; call it user-program output unless an explicit
  privilege probe is executed and validated.
- Parse the final online count after ANSI normalization and require equality
  for single-CPU as well as SMP runs. Missing evidence must fail. Interleaved
  SMP serial text may create false negatives; don't cure that by accepting any
  requested count without a validated final summary.
- Unexpected VM exit, including exit0 under -no-reboot, must fail. Reject fatal
  markers appearing before or after the banner. A timeout proves an observed
  process remained present, not that it was responsive throughout the interval.

## Concrete existing classifier findings

The inspected classifier passes banner+timeout with scheduler=False; its last
unit test explicitly accepts that insufficient trace. CPU equality is checked
only for requested counts above1. CPU regex operates on raw output even though
fault/banner processing removes ANSI cursor sequences. ISO digest is computed
after execution only. Sent all four findings to the integration owner.

Recommended table-driven regressions: valid complete trace; delete each required
marker in turn; missing/surplus/insufficient online count including requested1;
ANSI-interleaved fatal text and CPU summary; unexpected exit0; fatal condition
after valid user output. Preserve the existing negative case proving "wizard
task created" is not equivalent to user execution. Use before/after image hash
fixtures to detect artifact mutation rather than trusting a post-run hash alone.

The keyboard wizard adds stronger observable responsiveness: prompts are
matched in order and keys drive progress to completion. It still does not
prove persistent installation or sustained scheduler liveness. Its own
single-CPU coverage shortcut and post-run-only hashing have the same boundaries.

## Next process-isolation gate

Boot acceptance is a prerequisite, not an isolation result. For a later actual
ring3 test, observe a user program reading CS and require CPL=3, then use two
processes with different address-space roots and independent canaries at the
same virtual address. Valid private writes must preserve the other canary.
An attempted unauthorized mapping/access must produce the expected task-local
fault/denial while a separate witness process continues and the kernel remains
responsive. Require complete test IDs and a final summary; task creation or
kernel-side model assertions alone cannot satisfy this gate.

Allocation failure cleanup, fork/exec transitions, permissions on executable
and writable pages, and stale-TLB behavior under SMP are separate properties.
The documented retained direct/kernel mappings mean full KPTI is not established.
Bounded symbolic proofs apply to their modeled state transitions; without a
verified correspondence to actual page tables and machine instructions they
cannot be promoted to proofs of this running kernel's isolation.

## Classifier correction authorship and validation

After the initial independent review, the integration owner assigned this agent
implementation of the boot classifier corrections. This agent is therefore
the author of that correction and cannot independently approve its own code;
the MCP/build reviewer is assigned that second review.

The classifier now requires PMM statistics, VMM completion, scheduler transition,
user-program output and exact CPU count including1, using ANSI-normalized text.
The CLI measures ISO SHA256 immediately before execution and after observation,
rejecting a changed artifact. Nine focused tests pass, covering missing markers,
CPU mismatches, ANSI summary, final-summary precedence, faults before/after valid
boot output, unexpected exits and artifact mutation. Reclassification of prior
GCC16 BIOS4/UEFI2 serial logs passes; this is not a new clean-build matrix run.
PMM statistics presence is required, but allocation correctness and positive
free-memory counters are not independently proven by this classifier.

## Clean qualification harness review

Independently inspected scripts/native_clean_qualification.py. It archives a
resolved commit into a new directory, rejects tracked compiled artifacts and
preexisting outputs, pins the builder image ID, builds without network and
checks original source-file hashes afterward. One resulting ISO is used for
BIOS1, BIOS4, UEFI1 and UEFI4. Each case must pass the classifier and bind to the
same image hash. Classifier/ISO hashes are checked again after all cases.

First run /private/tmp/vos5-native-clean-20260914/result.json records commit
4082424c3f61b837c7052494566f385f4a0df745, clean build exit0 and unchanged source
inputs. All four boot cases pass with exact online counts and stable artifacts,
using ISO SHA2563051122da2e169456d5b5dc499730cc79c9587d6ea11933652adf83fa841f524.
Its overall passed=false is correct for the harness's recorded cleanup failure:
Docker's lowercase "no such object" was not accepted by a case-sensitive test.
This does not erase the individual boot results or justify changing the saved
overall result to true.

Reviewed proposed correction: successful exact-name container listing establishes
absence; if present, verify ownership before removal, then require a successful
empty requery. This avoids relying on localized/case-sensitive error text.
Final approval awaits evidence from the corrected harness. Recommended recording
the harness SHA256 in addition to classifier/source identities.

Additional lifecycle boundaries: the unnamed --rm tool-version probe is not
covered by the named build-container cleanup if its Docker client times out.
An outer timeout killing a stuck Python boot runner can also leave its QEMU child
unless group/process cleanup is enforced. Neither occurred in the inspected
passing matrix, but this harness does not prove cleanup under every interruption.
The archived sources are clean; the prebuilt compiler environment is separately
pinned and is not rebuilt from compiler sources by this gate.

## Corrected final run: bounded gate approved

Reviewed /private/tmp/vos5-native-clean-20260914-verified/result.json and its
harness-manifest.json. Recomputed current harness/classifier/test hashes and
actual generated ISO/kernel hashes; all match their recorded values. The
corrected cleanup requires successful exact-name queries, validates ownership
before any removal, and confirms absence afterward. The final result records
passed=true and build_container_removed=true in105.38 seconds.

The run archived commit4082424c3f61b837c7052494566f385f4a0df745 with5328 source
files and no preexisting generated outputs. Build exited0, original input hashes
remained unchanged, and BIOS1/4 plus UEFI1/4 each passed mandatory PMM, VMM,
scheduler, user-output, exact-online-count and stable-image checks.

- ISO SHA256: `4b85acf0a51583aff2361408125fda4800827d0c13b92e4d1c596b19656009d8`
- Kernel SHA256: `137a383ef839840e1192b3d7f55b4944360597968b61ff76d5ef93e7c25ac610`
- Harness SHA256: `a0a94e463ad3a8b85884566c90394c48cb78fb75a7cc7c7796432088056a148e`

Approval covers fresh application-source build and the specified emulator boot
matrix using the pinned prebuilt compiler image. The first and corrected runs
have matching kernel hashes but different ISO hashes; no claim of reproducible
ISO bytes is approved. Packaging metadata is being reviewed separately. Ring3
privilege measurements, process-isolation adversarial tests and physical
hardware qualification remain the next stages, not implied successes here.
Classifier authorship remains disclosed above; a separate agent reviews that
implementation while this review covers root's qualification harness/evidence.
