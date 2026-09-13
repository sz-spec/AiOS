# VOS3 Final Structural Integrity Audit Report

**Date:** 2026-03-20
**Branch:** `feat/10-10-all-capabilities`
**Result:** 367 PASS, 0 FAIL (baseline: 350 PASS on `main`)
**Time:** ~12 seconds (was 420s timeout pre-fix)

---

## Executive Summary

A comprehensive structural integrity audit was performed on the `feat/10-10-all-capabilities` branch after Phase A-I implementations. The audit identified 25 findings across 4 critical subsystems (FD locking, AI Guard, VirtIO-net, ABI collisions). All findings were fixed, verified, and the test suite now passes 367/367 tests — 17 more than the baseline.

---

## Audit Findings & Resolutions

### 1. FD Table Locking (6 findings — ALL FIXED)

| ID | Severity | Finding | Resolution |
|----|----------|---------|------------|
| FD-1 | CRITICAL | `vos3_fd_table_t` contained `vos3_mutex_t lock` — mutex sleeps, but `vos3_task_reap()` runs from timer ISR context (scheduler.c:509) where sleeping deadlocks | Removed mutex entirely. All FD ops use IRQ-safe spinlock (`pushfq; pop; cli` / `push; popfq`) |
| FD-2 | HIGH | `vos3_fd_table_destroy()` closed file handlers under mutex — close ops may sleep (e.g., socket flush, disk sync) | Two-phase destroy: snapshot fds under spinlock, close handlers outside lock |
| FD-3 | HIGH | `file->ref_count` not volatile, not atomic — concurrent threads (CLONE_FILES) could corrupt | Made `volatile uint32_t`, all ops via `__atomic_add_fetch`/`__atomic_sub_fetch` with `__ATOMIC_ACQ_REL` |
| FD-5 | MEDIUM | `fd_table->ref_count` non-atomic — fork + thread exit race | Same fix as FD-3: volatile + atomic builtins |
| FD-7 | HIGH | `exec.c` fork path called `vos3_mutex_init()` on removed lock field — compilation error | Removed, replaced with spinlock init + atomic ref_count store |
| FD-8 | MEDIUM | `exec_syscall.c` CLONE_FILES path used non-atomic ref_count increment | Changed to `__atomic_add_fetch` |

**Files modified:** `kernel/include/vos/vfs.h`, `kernel/src/fs/fd.c`, `kernel/src/sched/task.c`, `kernel/src/exec/exec.c`, `kernel/src/exec/exec_syscall.c`

### 2. AI Guard Page Fault Handler (8 findings — ALL FIXED)

| ID | Severity | Finding | Resolution |
|----|----------|---------|------------|
| AG-A1 | CRITICAL | `vos3_vmm_update_flags()` destroyed AI PTE bits (9-11) during COW/demand-paging | Read-modify-write: `ai_bits = (*pte) & VOS3_PTE_AI_MASK; *pte = create(flags) \| ai_bits` |
| AG-A2 | HIGH | User/kernel address threshold used `VOS3_KERNEL_BASE (0xFFFFFFFF80000000)` instead of canonical split `0xFFFF800000000000` | Changed to `0xFFFF800000000000ULL` |
| AG-B | HIGH | `vos3_ai_guard_reprotect_tick()` never called from scheduler tick | Added call in `vos3_sched_tick()` in scheduler.c |
| AG-C | HIGH | Cross-app check ran BEFORE COW handler — forked app touching COW page killed as cross-app violation | Moved cross-app check AFTER COW and demand-paging handlers |
| AG-D | CRITICAL | Kill paths called `vos3_task_exit()` which acquires `g_sched_lock` — deadlock if fault occurred while lock held | Created `fault_kill_current()`: sets ZOMBIE, removes from run queue, wakes parent, yields. No lock acquisition. |
| AG-D2 | CRITICAL | `vos3_task_defer_destroy()` sets state to DEAD immediately — parent's `waitpid()` scans for ZOMBIE, misses the child, blocks forever | `fault_kill_current()` does NOT call defer_destroy. Task stays ZOMBIE for parent to collect via waitpid. |
| AG-D3 | HIGH | Missing parent wake-up in fault kill path — parent blocked in waitpid never notified of child death | `fault_kill_current()` explicitly calls `vos3_task_unblock(parent)` if parent is BLOCKED |
| AG-F | MEDIUM | Monitor reprotect used `VOS3_VMM_FLAG_NONE` — user can't access monitored pages | Changed to `VOS3_VMM_FLAG_USER` (read-only + user-accessible) |

**Files modified:** `kernel/src/arch/x86_64/interrupts.c`, `kernel/src/mm/vmm.c`, `kernel/src/sched/scheduler.c`, `kernel/src/mm/ai_guard.c`

### 3. VirtIO-net Driver (4 findings — ALL FIXED)

| ID | Severity | Finding | Resolution |
|----|----------|---------|------------|
| NET-1 | CRITICAL | Missing `QUEUE_PFN` writes for RX/TX queues — device didn't know where vrings were located | Added QUEUE_SEL + QUEUE_PFN writes for both queues during init |
| NET-2 | HIGH | RX available ring not pre-populated — device had no buffers to receive into | Pre-populated RX ring with 64 DMA buffer descriptors |
| NET-3 | HIGH | No TX descriptor reclaim — after 64 sends, all descriptors exhausted | Added `virtio_net_tx_reclaim()` called at start of transmit |
| NET-4 | MEDIUM | Used ring not 4096-byte aligned — VirtIO spec 2.6.2 violation | Aligned: `(desc_size + avail_size + 4095) & ~4095` |

**Files modified:** `kernel/src/drivers/virtio_net.c`

### 4. Syscall ABI Collisions (3 findings — ALL FIXED)

| ID | Severity | Finding | Resolution |
|----|----------|---------|------------|
| ABI-1 | HIGH | `VOS3_SYS_GETSOCKOPT=160` collided with Linux `SYS_SETRLIMIT=160` — socket init overwrote POSIX handler | Moved to 163/164 (getsockopt/setsockopt) |
| ABI-2 | MEDIUM | Exit code for fault-killed tasks was `-(128+11)=-139` — `waitpid` does `& 0xFF = 117`, not 139 | Changed to positive `128+11 = 139` |
| ABI-3 | LOW | Comment referenced old collision at slot 160 — misleading | Updated comment |

**Files modified:** `kernel/include/vos/syscall.h`, `kernel/include/vos/socket.h`, `kernel/src/arch/x86_64/syscall.c`

---

## Key Architectural Fix: `fault_kill_current()`

The most impactful fix was creating a dedicated function for killing tasks from exception handlers. This replaces the broken `ZOMBIE + defer_destroy + yield` pattern that caused the 420-second timeout.

```c
static void __attribute__((noreturn))
fault_kill_current(vos3_task_t* task, int exit_code)
{
    vos3_irqflags_t flags = vos3_irq_save();
    task->exit_code = exit_code;
    task->state = VOS3_TASK_ZOMBIE;
    vos3_sched_remove_task(task);           // Remove from run queue
    vos3_task_t* parent = task->parent;
    if (parent != NULL && parent->state == VOS3_TASK_BLOCKED)
        vos3_task_unblock(parent);          // Wake parent's waitpid()
    vos3_sched_yield();                     // Switch away
    for (;;) { __asm__ volatile ("hlt"); }  // Safety net
}
```

**Why it works:**
1. Sets ZOMBIE (not DEAD) — parent can find and collect via `waitpid()`
2. Wakes parent — `waitpid()` was blocking forever without this
3. Does NOT call `vos3_task_defer_destroy()` — that would set DEAD before parent sees ZOMBIE
4. IRQ-disabled — prevents timer preemption between ZOMBIE set and context switch
5. No lock acquisition — safe even when `g_sched_lock` is already held by the faulting syscall path

---

## Test Results Progression

| Stage | PASS | FAIL | Time | Root Cause of Improvement |
|-------|------|------|------|---------------------------|
| Baseline (`main`) | 350 | 0 | ~12s | — |
| Phase A-I (before audit) | 142 | 0 (timeout) | 420s | `fault_kill_current` missing → waitpid hang |
| After audit fixes | 365 | 2 | ~12s | Parent wake-up + no defer_destroy |
| After ABI fixes | 367 | 0 | ~12s | Exit code sign fix + setrlimit collision |

**New tests added by Phase A-I:** 17 (AI guard enforcement, network e2e, integration, etc.)

---

## Files Modified (Complete List)

| File | Changes |
|------|---------|
| `kernel/include/vos/vfs.h` | Removed mutex from fd_table_t, made ref_count volatile |
| `kernel/include/vos/syscall.h` | Moved GETSOCKOPT/SETSOCKOPT from 160/161 to 163/164 |
| `kernel/include/vos/socket.h` | Same syscall number update |
| `kernel/src/fs/fd.c` | Complete rewrite: spinlock-only, atomic ref_counts, two-phase destroy |
| `kernel/src/sched/task.c` | Atomic fd_table ref_count in destroy/reap paths |
| `kernel/src/exec/exec.c` | Removed mutex init, atomic ref_count in fork path |
| `kernel/src/exec/exec_syscall.c` | Atomic ref_count in CLONE_FILES |
| `kernel/src/arch/x86_64/interrupts.c` | Created `fault_kill_current()`, fixed all 4 kill paths, positive exit codes |
| `kernel/src/arch/x86_64/syscall.c` | Updated permission allowlist for slot 160 (setrlimit) |
| `kernel/src/mm/vmm.c` | Preserve AI PTE bits, fix address threshold |
| `kernel/src/sched/scheduler.c` | Wire reprotect_tick from sched_tick |
| `kernel/src/mm/ai_guard.c` | Reprotect with USER flag |
| `kernel/src/drivers/virtio_net.c` | Queue PFN, RX ring pre-population, TX reclaim, alignment |

---

## Verification

```
==========================================
  Kernel Test Results
==========================================
[OK] Benchmark suite completed

  PASS: 367
  FAIL: 0

==========================================
  ALL KERNEL TESTS PASSED
==========================================
```

**Ready to merge `feat/10-10-all-capabilities` to `main`.**
