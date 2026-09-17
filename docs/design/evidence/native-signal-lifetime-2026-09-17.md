# Task-owned signal state — 2026-09-17

Implementation author: `/root/mcp_upgrade`. Independent lifecycle test author and integration reviewer: `/root/math_build_review`. Coordinator `/root` owns the libc wrapper and native execution workload. This report does not represent an independent security audit of the implementation by its author.

The previous signal storage indexed a fixed `VOS3_MAX_TASKS` array by monotonically increasing task ID. Valid tasks with IDs beyond that bound could not acquire signal state. Each task now owns a separately allocated pending/mask object. Reference-counted disposition tables are copied for fork and shared only for `CLONE_SIGHAND`; pending signals are never inherited and blocked masks remain private. Initialization happens before task publication, and failed construction, direct destruction, and deferred reaping release ownership.

Exec allocates a replacement disposition table, preserves ignored dispositions and the calling task's blocked/pending state, and resets caught handlers. Shared siblings retain their table. Exec retains its previous address space until all fallible ELF, stack, and signal preparation succeeds; late failure restores the prior image instead of returning into a released address space.

The Linux syscall boundary translates signal mask bit numbering and supported action flags. Custom user handlers require a valid user-space restorer; unsupported action flags are rejected. User signal delivery never directly invokes a handler in kernel mode. Its frame preserves the 128-byte SysV red zone and prior mask. Sigreturn validates return addresses, strips privileged RFLAGS bits, and excludes SIGKILL/SIGSTOP from the restored mask. Explicit kernel tasks retain the internal callback facility.

## Completed validation

- Freestanding x86-64 syntax validation passed for `signal.c`, `signal_syscall.c`, `task.c`, `exec.c`, and `exec_syscall.c`. Only the existing Clang warning about an unsupported GCC diagnostic name remains. Log: `/private/tmp/vos-goal-native-20260917-signal-syntax.log`.
- `python3 scripts/test_signal_lifetime.py` passed one compiled actual-source C harness. It checks every lifecycle allocation failure, fork/share ownership, reference saturation, exec rollback/detachment, high task IDs, 300 release/reuse cycles, allocation conservation, and lock/IRQ restoration. Extracted production digest: `b746d02373dfe7f1ac9133ca30262d08040332c066edbe8908e53f45e3597fde`. Log: `/private/tmp/vos-goal-native-20260917-signal-lifecycle-host.log`.
- `git diff --check` passed.
- The coordinator's filtered BIOS/QEMU TCG, one-vCPU run passed `test_signal_musl` and `test_signal_lifetime`, both exit 0. This author checked the raw serial log: 15 lifetime PASS markers, no FAIL marker, actual PID 1202, handler-observed CS privilege level 3, invalid installation rejection, blocked/pending and fork controls, and exec disposition/mask controls. Recomputed ISO SHA-256 matches both recorded pre/post hashes: `537e86de502b825f28d1b63e9788f013531498d8b705be743f51707de9d21df7`. Evidence: `/private/tmp/vos-goal-signals-20260917-run/{result.json,serial.log}`. The result explicitly declares a selected subset; it is not the full native suite, an SMP result, or a physical-hardware result. The planned privileged-CLI handler negative is not present in this captured workload and is not claimed.
- Follow-up source review found and corrected a compatibility regression in strict action-flag validation: musl sign-extends signed `sa_flags` when `SA_RESETHAND` is set. The decoder now accepts zero extension or exact signed-int extension, then rejects unsupported low bits. `scripts/test_signal_abi_flags.py` compiles the actual decoder and passed all 16 supported combinations in both encodings, unsupported-bit negatives, malformed extensions, and `SA_SIGINFO` rejection. Log: `/private/tmp/vos-goal-native-20260917-signal-abi-flags.log`. This correction postdates the filtered native run and requires the coordinator's rebuild/rerun before runtime qualification of the final snapshot.

## Explicit limits

`SA_RESTART` is preserved as a disposition flag for compatibility; automatic syscall restart is not implemented. Signals above 31, queued realtime signals, `SA_SIGINFO`, alternate stacks, and full POSIX thread/process signal routing are not qualified. Legacy custom-handler IPC calls without a restorer now fail closed for user tasks.

The task lookup API still returns a raw pointer without a remote lifetime pin. Local IRQ exclusion in signal sending is not a proof of remote SMP lifetime safety. Existing `sigwait` unlock-before-block behavior and `pause` consumption semantics require separate correction/qualification; this lifecycle change does not claim to resolve them. Native AP scheduling remains separately gated.

Additional source findings outside this ownership fix: the group/broadcast `sys_kill` branches still iterate numeric IDs below `VOS3_MAX_TASKS`, excluding high IDs, and negative process-group conversion uses signed `-pid` for `INT32_MIN`. These preexisting group-routing issues are not resolved by the passing individual-process workload.

## Follow-up: signal syscall authorization

Source inspection confirmed that legacy syscall 450 and Linux `tkill`/`tgkill` variants bypassed the permission policy already present in `kill(62)`. The policy now resides in `vos3_signal_check_permission`, shared by all these public variants: root or matching UID may send, and SIGCONT additionally permits matching sessions. Missing identities fail closed. Internal kernel timer/pipe senders keep their separate internal API. Legacy 450 retains its TID-based ABI and rejection of signal zero, and now rejects truncated high-bit arguments. POSIX signal-zero probes also enforce permission. `tgkill` requires positive IDs and the actual matching thread group.

`scripts/test_signal_authorization.py` compiles the actual legacy wrapper and shared policy, checking foreign UID/session denial with zero send calls, same-UID/root success, the SIGCONT-only session exception, missing identities, wide arguments, and propagation of send errors. The independent reviewer's `scripts/test_signal_return.py` compiles the actual full dispatcher and actual common policy, testing tkill/tgkill allow/deny cases, forged return addresses, complete register restoration, and every RFLAGS bit. Together with lifecycle and flag-decoder tests, all four host modules passed in the combined run recorded at `/private/tmp/vos-goal-native-20260917-signal-host-combined.log`. Mocked task lookup/uaccess do not prove concurrent lifetime safety or hardware return behavior. Final native qualification after the ABI and authorization follow-ups remains pending.

## Full native run and integration snapshot

Native source commit: `81c7f77` (hosted-only changes followed in `99f8c22`). The final worktree ISO build completed and the complete **56-program** BIOS/one-vCPU benchmark sequence reached its halt marker. Thirteen programs still exited nonzero, so the suite correctly remains **failed**. Final ISO SHA-256: `f5c67e4df8bf5843a8db48efadc217464c219135fa546aa4681773b2e9e04a7e`, unchanged across the run. This includes the final flag decoder and signal authorization changes.

`bench_sigpipe_test`, `test_signal_musl`, `test_signal_lifetime`, `bench_phase13_test`, and `bench_csw_1ms` all exited zero. The signal workload checked actual user privilege level, blocked/pending delivery, independent fork dispositions, and exec reset/preservation. The filtered earlier run additionally forced 1,100 sequential fork/reap cycles before testing high identities; the full run already reached high identities and did not repeat that setup branch.

Correct signal delivery exposed a benchmark bug: the context-switch child served 2,000 messages while its parent sent 50 warmup messages plus 2,000 measured messages. The corrected child serves all 2,050, and both sides check transfers, replies and child completion. Four actual-function host tests include the original-count mutation and short-I/O/child-failure negatives. The SIGPIPE fixture initializes its entire action structure. Phase13 now detaches its public SHM mapping before fork, then maps the same retained object in each process and verifies both directions, exact wait status and cleanup; mapped-SHM fork remains fail-closed. Existing networking cases in Phase13 that label unavailable scenarios as PASS have not been qualified by this change.

Remaining nonzero programs: `bench_fuzz_test` (139), `stress_thread`, `test_shm_dispatch`, `diag_shm_stress`, `test_sustained`, `bench_ai_throughput`, `bench_2026_frontier`, `test_agent_chaos`, `test_advanced_chaos`, `test_health_check`, `test_agent_cluster`, `bench_ai_scale`, and `test_stress_mt`. The fuzz crash is associated with a kernel user-copy access into an unmapped user page. Other failures include mapped-SHM fork contracts, thread/task resource exhaustion, and zombie cleanup; these require further production or contract investigation, not skipped tests.

The scripts discovery run passed **75 tests**. The final focused actual-source run passed **nine tests**. These host tests do not establish real CPU fault-return safety or remote SMP lifetime safety. Four clean-build firmware/CPU matrices are being recorded separately; this worktree benchmark is not itself a clean-archive qualification. Build logs retain compiler/linker and clock-skew warnings; no warning-free or byte-identical rebuild claim is made.

Raw filtered, intermediate and final benchmark results, serial logs, final build output, and host test logs are preserved in [the evidence directory](native-signal-lifetime-2026-09-17/manifest.json), with SHA-256 digests. Earlier failing evidence is retained.

## Clean archive qualification at 81c7f77

All four images were built from a fresh Git archive using the pinned builder, with no previous generated outputs and unchanged source inputs. **15 of 16 boots passed**. The TLB matrix remains failed: UEFI/four-CPU workers completed their arithmetic and wait checks but both executed on APIC 1, so the unchanged observer correctly rejected missing worker CPU diversity. Every CPU's TLB replacement observations succeeded; that does not override the workload failure. The scheduler's first-dispatch ownership choice is under investigation. The original failed result and serial log are preserved.

| Image | BIOS 1/4, UEFI 1/4 | ISO SHA-256 |
|---|---|---|
| Normal | all four passed | `1273e47c6de2b98b05855537974d191b551f117ab15b36d3ab959d3fbcc06c82` |
| Isolation | all four passed | `33ccf7dea2b9c5660778e562b9222a994192addf634e65b8e52a5068a1649978` |
| Memory | all four passed | `9a6c7a4a5d611a17365946bb7ea8d1d0582225023b82c075f582a6980198e677` |
| TLB/AP workload | first three passed; UEFI4 failed | `e77a26c27ed1cc2c2eac873387ee0124f1c9b02c96c5973c441e8f5e568477fc` |

Memory logs independently verified 12 cases, 51 user records, seven kernel checks and 11 expected faults in each configuration. Isolation verified four containment cases per configuration. These are emulator and scoped diagnostic results, not physical hardware qualification or complete concurrent process isolation.

Staged-evidence secret scanning reported 568 candidates, all confined to four archived source manifests (142 repeated content hashes each). Each candidate was independently checked against the corresponding archived file's SHA-256 and staged manifest line. No secret suppression was added.
