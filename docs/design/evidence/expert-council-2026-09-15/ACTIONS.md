# Council dispositions and next gates

This is the coordinator's synthesis of the twenty linked reviews. Priorities
reflect this repository's native-OS objective, not a universal vulnerability score.
A bounded review is not a certification of code that was not inspected.

| Priority | Finding or requirement | Current disposition | Evidence needed to close |
|---|---|---|---|
| P1 | COW/cognitive flag collision | Corrected; native sequential metadata checks pass | Preserve regression on subsequent memory changes |
| P1 | Shared address-space lifetime | Task/CPU references and backing ownership implemented; four memory configurations pass | Concurrent mutation and remote revocation remain separate |
| P1 | Orphans adopted by AP idle PID 1 | Corrected by live user-init selection; original failed run retained | Preserve the four-CPU survivor/wait regression |
| P1 | Shared VMA/PTE/COW mutation | Open | Serialized mutation, active-user access tests and acknowledged remote permission revocation before reuse |
| P1 | File-backed page-in concurrency | Open | Positional I/O and safe VMA/backing access under concurrent unmap/exec |
| P1 | AI guard context inheritance | Unsafe copying refused with ENOTSUP | Policy-preserving retain/clone lifecycle and negative policy tests |
| P1, bounded closure | Foreign SHM creator destruction and stale handle reuse | Corrected in `9e58227`: immutable creator identity, generation-tagged handles, atomic check/release and command-aware alias; four native memory configurations pass | Preserve fork/CLONE_VM denial and stale-handle controls; concurrent SHM lifetime, creator-orphan cleanup and capability delegation remain separate |
| P1 | Creator-first SHM resource leak | Corrected in `3daf340`; explicit detach and final AS reap pass all four memory configurations, independently reviewed | Preserve the strict full-marker regression; concurrent ownership remains separate |
| P1, bounded closure | SHM creator exits without closing | Corrected in `8c23007`: IRQ-safe ownership transfer and process-context drain; all four memory configurations verify normal/fault retirement before wait, mapped survivors and duplicate-close safety | Preserve regressions; uncollected-zombie AS mappings and remote task-stop/shared-VM concurrency remain separate |
| P1 | Independent SHM fork | Explicitly unsupported | Region and VA ownership protocol preserving shared semantics and cleanup |
| P1 | General PID/TID and parent-list consistency | Open beyond the corrected init selection | Unique identity/lifetime model, concurrent lookup/reparent/wait tests |
| P1 | Universal PC and firmware support | Unverified | Named supported hardware matrix, physical cold boots, storage/network/device recovery tests |
| P1 | Persistent install and Windows coexistence | Unqualified | Disposable-disk installation/recovery tests, boot selection and preservation of existing installations |
| P1 | Signed update and hosted trust boundaries | Separate review gates remain open | Authenticated release/update verification, rollback protection, live identity/revocation tests |
| P2 | Byte-identical ISO rebuilding | Unverified | Two independent clean builds with identical approved inputs and byte/hash comparison |
| P2 | Legacy benchmark signed overflow | Observed; benchmark excluded from qualification | Correct arithmetic, rerun functional controls before performance claims |
| P2 | Obsolete GRUB deployment instructions | Open documentation mismatch | Match operator instructions to tested Limine build/install procedure |
| P2 | Eleven-source semantic preservation | Ledger evidence is incomplete proof | Requirement/component-specific equivalence tests and documented decisions for every unresolved feature |

No finding is closed merely because several review roles agree. Final release
approval requires the applicable tests and remaining capability decisions. The
normal build keeps experimental AP scheduling and diagnostic fault injection off.
