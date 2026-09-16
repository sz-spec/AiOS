# SHM creator-exit ownership invariants

Reviewer: `/root/math_build_review`. Root authors production lifecycle integration; this reviewer authors extracted-C tests and observer checks. Native workload/kernel checks have separate authors and independent security review. Runtime qualification is pending.

## Transfer and finalization review

`vos3_shm_owner_exit` identifies objects by immutable creator cookie under the IRQ-safe registry lock. It marks creator ownership released and stores the full generation-bearing handle in a per-slot pending array, without decrementing the reference count. Thus the existing creator reference becomes a deferred-work pin; there is no zero-to-live resurrection window. Repeat notifications cannot enqueue another pin. Explicit close before notification prevents enqueue; explicit close afterward cannot consume the pending pin.

The reaper detaches at most 63 handles under the registry lock, then performs each noncreator put outside the lock. Clearing a queue cell does not relinquish its pin: the local detached handle owns it until put. The retained object reserves its slot throughout this interval. Mappings remain separate owners, so dropping the creator pin does not free mapped backing. Finalization is unified with ordinary last release. Production callers must supply actual safe process context; an IF check is necessary but does not establish arbitrary interrupt-depth safety by itself.

Arithmetic is bounded by the fixed 64-slot array with slot zero excluded. Full handles prevent accidental interpretation as another generation; existing generation exhaustion never wraps. Counter mutation is performed only once per claimed creator reference. No additional sequential arithmetic blocker was found in these transfer/drain helpers.

## Actual production C validation

`scripts/test_shm_exit.py` extracts the exact production slot/lookup/release/owner-exit/reaper functions. It compiles the actual functions with mocks for registry locks, current identity and physical finalization. The real x86 flags-read instruction remains; on this ARM macOS host the test compiles an x86-64 executable and executes through available translation. It does not model IF-clear execution as passed.

The test passes pin conservation, repeated notifications/drains, explicit-close orderings, mapping survival, unrelated owner exclusion, all 63 pending regions and exactly one backing/finalizer call per completed object. This exercises production control flow, not a rewritten Python algorithm. Native tests must separately establish IRQ-clear deferral and actual PMM reclamation.

## Native observer requirements

Four creator-exit cases are required after authorization checks: unmapped normal exit, mapped survivor, explicit-close then exit, and terminal page-fault exit. The observer requires distinct child identities, completion/retirement markers, survivor evidence where applicable and an exact eleventh SIGSEGV bound to the fault child's PID/address `0x7400000000` with user nonpresent-read error 4. Existing ten memory faults remain mandatory. Missing retirement, duplicate identities, unexpected faults or early completion fail. These tests do not imply concurrent shared-VMA safety or arbitrary creator-capability delegation.

## Final creator-exit native gate — verified 2026-09-17

Independently reclassified the four completed raw logs at `/private/tmp/vos5-shm-exit-memory-release-20260915/`; the temporary directory retains its historical naming convention. BIOS/UEFI × 1/4 CPUs all pass. Every run has exactly 51 user records, all six required kernel checks and eleven precisely correlated SIGSEGV records. The extra terminal fault belongs to the creator-exit child at address `0x7400000000` with error 4 and wait status 35584; all previous ten faults remain required.

The kernel SHM-EXIT check covers unrelated identity retention, duplicate marking, IRQ-disabled drain deferral, creator backing release, surviving mapping and explicit-close idempotence. User records establish creator-only retirement, mapped survivor progress after creator exit, explicit-close followed by exit, and retirement after terminal fault. These observations do not rely solely on successful wait status.

Verified source `8c23007eed4acd731b98bdd2998c9634f2ba3f75`, tree `8f203c5eadaac747f4db8e51edae6c19759d2eac`, unchanged inputs and stable before/after ISO in every run. Independently recomputed actual hashes:

- ISO: `704021198cbf1e63e1268091aeeaced3279d0fab386f4e0a76eb6a277ba96fa3`.
- Kernel: `fb689e166f217036e274a8008f347b8728387b90ab2652ee5908d85ace520487`.
- User workload: `1a01cf9010c28bbc76484a5af9445b7938ee72edb64c03cf28818d75e16587af`.
- Observer: `57e5f09f87643acde1c39dc92d2e12524381624291ab2f7ab27b4708723213cd`.
- Harness: `7d7672a5dbd457c6e58a65ac42d59ee6bd6e6a362ed4a736cf08b43c272687f7`.

All match the result; elapsed time is 107.04 seconds. All 38 observer methods and four actual-C methods also pass, with portable outputs in `native-shm-exit-2026-09-17/observer-tests.txt` and `c-tests.txt`.

Approve the bounded sequential creator-exit cleanup gate for these configurations and checked exit paths. This is observed lifecycle correctness with reviewed ownership invariants, not a proof of every concurrent release schedule, general shared-VMA safety, remote-memory revocation, physical hardware compatibility or release readiness. Authorship/independent-review boundaries stated above remain applicable. Separate regression matrices are not inferred from this memory result.
