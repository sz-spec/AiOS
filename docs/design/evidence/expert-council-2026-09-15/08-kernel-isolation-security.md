# Kernel isolation security

Reviewer: `/root/mcp_upgrade`. Source: `32187957757ab57d7820d0a63fa62c409003b126`. Date: 2026-09-15. Bounded source and evidence review; no production edits.

1. **P0 release boundary — shared mutation remains unqualified.** [kernel/src/mm/vmm.c](../../../../kernel/src/mm/vmm.c) now pins CPU roots and retires address spaces after the final reference. Its mprotect and unmap paths still use local invalidation; the separately tested cross-CPU TLB protocol does not establish that every permission change or frame release invokes it. Require a shared-address-space adversarial test with actual remote readers before enabling unrestricted concurrent shared-VM execution.
2. **P1 — distinguish lifetime from isolation.** [kernel/src/exec/exec_syscall.c](../../../../kernel/src/exec/exec_syscall.c) retains shared roots and preserves the gated scheduler owner. This protects the bounded sequential workload but is not a proof of concurrent faults, descriptor operations, FPU ownership or scheduler fairness. Keep this compatibility boundary explicit in release notes.
3. **P1 evidence — test both positive and negative permissions.** `user/src/test_native_memory.c` and [scripts/native_memory_smoke.py](../../../../scripts/native_memory_smoke.py) require actual fault addresses/error bits, successful RX execution, parent canaries and continued progress. Preserve these controls when adding lifecycle cases; an exit status alone is insufficient.

Validation reviewed: source ownership order and development BIOS1 markers. Final clean lifetime matrix is tracked separately in `native-vm-lifetime-security.md`. Not reviewed here: physical CPUs, DMA/IOMMU attacks, speculative execution, firmware compromise or an exhaustive syscall audit.

2026-09-15 follow-up: source `3ce56da` fixes idle-task orphan adoption. All four corrected memory/lifetime logs and 5,594 source hashes independently verify; original failure remains preserved. This bounded sequential gate passes, while general PID ambiguity, concurrency and release findings remain open. Original review scope above is unchanged.
