# Bounded user-copy fault recovery — 2026-09-17

Implementation author: `/root/mcp_upgrade`. Independent source/test reviewer: `/root/math_build_review`. The coordinator owns the real guest workload and native runs. The author does not independently audit their own implementation. Native results below were collected by the coordinator.

`copy_from_user` and `copy_to_user` now use separate assembly `REP MOVSB` instructions with exact static fault and recovery labels. A recovery match requires kernel CPL, a supervisor data fault with no reserved/fetch/other error bits, the exact corresponding instruction address, nonzero remaining count, matching read/write direction, CR2 equal to the current user-side RSI/RDI, and a validated remaining user range. No task, CPU, or global continuation pointer is stored. Recovery returns `-EFAULT` through the original call stack so syscall cleanup can run. A copied prefix may already exist; this is not atomic-copy semantics.

The page-fault handler preserves COW resolution before recovery. Demand paging additionally recognizes this exact validated copy context, checks the existing VMA access policy, and retries valid lazy accesses. Unresolved matched faults redirect only RIP and clear saved AC. Fault handling clears active AC when SMAP is enabled; a successful fault resolution retries with the saved copy state. The wrapper clears AC on normal/error return. The assembly explicitly clears DF and uses only caller-saved registers. Unrelated kernel faults never match this recovery mechanism.

Preflight walks all four page-table levels and requires effective USER permissions for populated mappings. Writes also require effective WRITE, or COW on an actual leaf supported by the existing VMM handler. This prevents supervisor copying from bypassing PROT_NONE or low-address supervisor mappings. Missing entries proceed to the bounded fault/demand path. String helpers use the same safe byte-copy operation. All three legacy `vos3_copy_from_user`, `vos3_copy_to_user`, and `vos3_strncpy_from_user` entry points delegate to the unified implementation while preserving their existing `-2` invalid-argument convention.

## Validation and limits

The actual user-copy translation unit, including assembly labels, compiled to an x86-64 ELF object. Freestanding syntax checks passed for the copy code, interrupt integration, and legacy wrappers. Logs retain the existing Clang warning about the GCC-only `-Wstringop-overflow` diagnostic pragma.

`scripts/test_usercopy_permissions.py` compiles the actual preflight function and VMM flag helpers against simulated page tables. It covers USER and WRITE denial at every level, COW leaf acceptance, nonleaf read-only denial, huge leaves, missing entries, cross-page boundaries, and absent address spaces. The independent reviewer's `scripts/test_usercopy_recovery.py` exercises the actual range validator, exact matcher, preflight, and wrappers with mocked raw copying/SMAP hooks. It checks malformed fault tuples, overflow/range rejection, copy errors, AC cleanup, and legacy wrapper behavior. Neither harness executes a hardware page fault or proves SMAP instruction behavior.

The guest workload covers unmapped/cross-page input and output, paths, read-only/PROT_NONE rejection, continued valid I/O, COW parent preservation, and untouched lazy copyout. Its result must be recorded after the final rebuild; no guest pass is inferred from host tests.

Concurrent shared-address-space mutation between permission preflight and copying remains outside this serialized qualification. This change does not establish general SMP page-table lifetime safety. Existing AI policy enforcement remains before terminal recovery; it has not been replaced with a generic fault escape. The preexisting broad kernel-user-address fatal-fault behavior remains for faults outside the exact copy sites.

## Complete benchmark execution

At native source revision `052d99b`, the final BIOS/one-CPU QEMU image ran all **57** benchmark programs and reached the completion marker. ISO SHA-256 was unchanged: `735824feeb38f292949b9e88be431eb549b1aada0aaed029ae945e4956ed08b5`. `bench_fuzz_test` no longer exited with SIGSEGV and returned zero. All **23** controls in the new `test_usercopy` program passed, including real unmapped faults, copyin/copyout boundaries, invalid strings, RO/PROT_NONE rejection, successful I/O after faults, COW preservation of the parent, and lazy copyout. This is one-CPU emulated runtime evidence, not concurrent remapping or hardware coverage.

The two repaired SHM programs, `test_shm_dispatch` and `diag_shm_stress`, also returned zero. They detach before fork and independently map the retained shared object, check exact child wait results and cleanup, pass the explicit alias-31 destroy command, and reserve completion-control words outside agent payload writes. Existing fail-closed mapped-SHM fork policy remains unchanged; these tests do not establish support for inheriting live SHM mappings at fork. The actual-function SHM host harness passed 11 scenarios using host forks and shared-file mappings, including partial spawn and failed-child controls. Existing unrelated permissive benchmark oracles were not comprehensively audited.

The full suite remains **failed**, with ten nonzero programs: `stress_thread`, `test_sustained`, `bench_ai_throughput`, `bench_2026_frontier`, `test_agent_chaos`, `test_advanced_chaos`, `test_health_check`, `test_agent_cluster`, `bench_ai_scale`, and `test_stress_mt`. Earlier corresponding complete run had 13 nonzero exits across 56 programs; a new regression program was added, no existing program was removed.

The final focused host run passed three test methods. An earlier host failure is preserved: its test incorrectly required rejection of huge COW leaves, despite the existing VMM support for those leaves. The corrected test retains a negative for read-only nonleaf permissions and adds positive supported huge-COW cases. No guest huge-page COW qualification is claimed.

Portable [evidence](native-usercopy-recovery-2026-09-17/) retains full serial output, result classification, both initial and final incremental worktree build logs, and host results. The worktree benchmark build itself is not a clean-archive proof. Independent clean matrices are recorded below after completion.

## Clean-archive qualification

All four diagnostic images were then built from a fresh archive of commit
`052d99b331af71bb80c41c70000826555f73f918`. The harness confirmed that no
generated outputs were present initially, source inputs were unchanged after
the builds, and each owned build container was removed. BIOS and UEFI with one
and four virtual CPUs passed for every image: **16 of 16 boots**.

| Image | Four boot configurations | ISO SHA-256 |
|---|---:|---|
| Normal userspace boot | 4/4 | `0cae5c9ecc88c2d182f4cd809847fb5736e65cfc1da07d17cd9cc48635fbf078` |
| Process isolation | 4/4 | `da3bfa559603a7c9eb60b8b237dadfd31484dd85d233b889e6968b2c54987dbe` |
| Memory transitions | 4/4 | `6f1a2ac3e67bdb24dd127401daa88390fd8cce8ccf94b49adf212ccd01b3483f` |
| TLB/AP workload | 4/4 | `a5ecbdb3c7bcdc63259bf77622911d7a031d8a0025af97c4e7be378df73bcf99` |

The memory logs were independently reclassified: each contains 12 cases, 51
user records, seven kernel checks, and 11 expected faults. The TLB four-CPU
runs observed workers on APIC 0 and APIC 1 and replacement observations on all
four CPUs. These results qualify the existing emulator diagnostics; they do
not establish physical-hardware compatibility, concurrent page-table mutation
safety, or general SMP address-space lifetime safety. A generated output
directory timestamp warning of roughly 4.5 milliseconds is retained in the
build evidence and prevents a warning-free/reproducible-timestamp claim.

`scripts` discovery passed **81 tests** against this source. `SHA256SUMS` in the
evidence directory covers the preserved raw logs, build results, source
manifests, and per-boot records. Raw `.log` files are marked `-text` so Git does
not normalize their captured bytes.

Staged-evidence secret scanning reported 568 candidates, all in the four clean
source manifests. Each candidate was independently recomputed from the matching
archived source file and compared with the staged manifest line. All 568 were
ordinary SHA-256 content hashes; no suppression was added.
