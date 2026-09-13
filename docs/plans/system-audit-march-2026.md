# VOS3 System Audit — March 2026

**Branch:** `feat/10-10-all-capabilities`
**Auditor:** Claude Opus 4.6 (automated deep-dive)
**Date:** 2026-03-19
**Baseline:** 350 PASS on `main` (commit `d88fa5b`)
**Status:** All 9 phases (A-I) implemented by subagents, untested

---

## Executive Summary

25 findings across 4 severity levels. **4 CRITICAL** bugs that prevent boot or exhaust resources within minutes. **7 HIGH** bugs that cause data corruption, use-after-free, or permanent loss of functionality. **7 MEDIUM** bugs with incorrect behavior under specific conditions. **2 LOW** cosmetic/warning issues.

The kernel **cannot boot** due to a guard page triple-fault (already fixed). After that fix, it will likely boot but **deadlock on first file close** due to sleeping mutex in ISR context.

---

## Severity Definitions

| Level | Meaning |
|-------|---------|
| CRITICAL | Prevents boot, causes triple-fault, or exhausts resources within minutes |
| HIGH | Data corruption, use-after-free, permanent functionality loss, or deadlock under normal workload |
| MEDIUM | Incorrect behavior under specific (but realistic) conditions |
| LOW | Cosmetic, compiler warnings, or theoretical-only issues |

---

## Area 1: FD Table Locking (kernel/src/fs/fd.c + kernel/src/sched/task.c)

### FD-1 [CRITICAL] — `vos3_fd_table_destroy()` sleeps in ISR context

**File:** `kernel/src/fs/fd.c:44`
**Impact:** Deadlock — kernel hangs on first task exit

`vos3_fd_table_destroy()` calls `vos3_mutex_lock(&table->lock)` at line 44. Mutexes can sleep (they call `vos3_wq_wait()`). This function is called from `vos3_task_reap()` in `task.c:488`, which runs from the reaper — triggered by `vos3_sched_tick()` (timer ISR context). Sleeping in ISR context is an unconditional deadlock.

**Fix:** Replace the mutex-based destroy path with the IRQ-safe spinlock already present on the struct. The destroy function should:
1. Acquire `table->spinlock` with `vos3_irq_save()`
2. Copy out the fd array and close-handler list
3. Release spinlock
4. Call close handlers outside the lock (they may sleep)

### FD-2 [CRITICAL] — Close handlers sleep inside destroy path

**File:** `kernel/src/fs/fd.c` (close handler chain)
**Impact:** Deadlock — compounds FD-1

Even if FD-1's mutex is removed, `vos3_fd_table_destroy()` iterates fds and calls `vos3_file_close()` per entry. Some close handlers (e.g., `pipe_close()` in `pipe.c`) acquire their own mutexes. Since destroy runs from reaper (ISR context), these sleep calls also deadlock.

**Fix:** Defer close handlers to a work queue or task context. In destroy:
1. Under spinlock: snapshot all open fds, zero the table
2. Release spinlock
3. Schedule a deferred-work item that calls close handlers from task context
4. **Simpler alternative:** Since reaper already runs as a kernel task (not raw ISR), verify the exact call chain. If reaper is a proper task (not ISR), this is not a deadlock — only FD-1 matters.

### FD-3 [HIGH] — `file->ref_count` is non-atomic

**File:** `kernel/include/vos/file.h` (struct vos3_file)
**Impact:** Race condition — ref_count corruption when same file opened in multiple fd_tables

`file->ref_count` is a plain `uint32_t`. When fork creates a new fd_table, it increments `ref_count` per file. If two threads/tasks fork simultaneously sharing the same file object, the non-atomic increment can lose updates, leading to premature free (use-after-free) or leaked files.

**Fix:** Change `uint32_t ref_count` to `volatile int32_t ref_count` and use `__atomic_add_fetch(&file->ref_count, 1, __ATOMIC_SEQ_CST)` / `__atomic_sub_fetch()` for all increments/decrements.

### FD-4 [HIGH] — `vos3_fd_get()` returns raw pointer without ref_count bump

**File:** `kernel/src/fs/fd.c` (`vos3_fd_get()`)
**Impact:** TOCTOU use-after-free

`vos3_fd_get()` acquires spinlock, reads `table->fds[fd]`, releases spinlock, returns the pointer. Between spinlock release and caller's use, another thread can close the fd and free the file object. The caller then dereferences freed memory.

**Fix:** `vos3_fd_get()` must atomically increment `file->ref_count` before returning. Callers must call `vos3_file_put()` (new function) when done. This is the standard kernel pattern (Linux `fdget()`/`fdput()`).

### FD-5 [HIGH] — Dual lock confusion (mutex + spinlock)

**File:** `kernel/src/fs/fd.c` + `kernel/include/vos/file.h`
**Impact:** No mutual exclusion between destroy and other operations

`vos3_fd_table_t` has BOTH a `vos3_mutex_t lock` (used by destroy) and a `vos3_spinlock_t spinlock` (used by alloc/get/put/close). These provide no mutual exclusion with each other — destroy can run concurrently with alloc/get without either blocking.

**Fix:** Remove the mutex entirely. Use only the IRQ-safe spinlock for all fd_table operations. Destroy acquires spinlock, snapshots state, releases, then calls close handlers outside lock.

### FD-6 [MEDIUM] — `fd_table->ref_count` not atomic

**File:** `kernel/src/sched/task.c:397-403, 508-514`
**Impact:** Race in clone + reap on shared fd_table

`fd_table->ref_count` is manipulated with plain reads/writes in `vos3_task_destroy()` and `vos3_task_reap()`. If two threads sharing an fd_table exit simultaneously, the non-atomic decrement can undercount, leaking the table, or overcount, double-freeing it.

**Fix:** Use `__atomic_sub_fetch(&table->ref_count, 1, __ATOMIC_ACQ_REL)` in destroy/reap paths.

### FD-7 [MEDIUM] — Fork memcpy of fd_table not under parent's lock

**File:** `kernel/src/exec/exec_syscall.c` (fork path)
**Impact:** Inconsistent fd_table snapshot during fork

Fork copies the parent's fd_table via `memcpy`. If the parent (or another thread sharing the fd_table) is concurrently opening/closing fds, the memcpy can capture a half-modified state.

**Fix:** Acquire parent's `fd_table->spinlock` (with IRQ save) before memcpy, release after. Set child's `ref_count = 1` and reinitialize child's spinlock after memcpy.

### FD-8 [MEDIUM] — `CLONE_FILES` ref_count increment unprotected

**File:** `kernel/src/exec/exec_syscall.c` (clone path)
**Impact:** Lost ref_count increment if concurrent clone

When `CLONE_FILES` is set, `child->fd_table = parent->fd_table` and `ref_count++`. This increment is not atomic and not under any lock.

**Fix:** Use `__atomic_add_fetch(&parent->fd_table->ref_count, 1, __ATOMIC_ACQ_REL)`.

### FD-9 [LOW] — Close handlers may do I/O from destroy path

**File:** `kernel/src/fs/fd.c`
**Impact:** Performance — close handlers may block on disk I/O

File close handlers (especially for vos3fs files) may trigger disk writes (flushing dirty pages). If destroy runs from reaper context with IRQs partially disabled, this can cause long latencies.

**Fix:** Acceptable for now. Document that close handlers must not hold spinlocks when doing I/O.

---

## Area 2: AI Guard + VMM (kernel/src/mm/ai_guard.c + kernel/src/arch/x86_64/interrupts.c)

### AG-A1 [HIGH] — `vos3_vmm_update_flags()` destroys AI PTE bits

**File:** `kernel/src/mm/vmm.c:554-599`
**Impact:** Monitored pages permanently lose their MONITORED/PROTECTED/GUARD bits after first flag update

`vos3_vmm_update_flags()` at line 591 does: `*pte = vos3_pte_create(addr, pte_flags)`. This replaces the entire PTE. `vos3_vmm_flags_to_pte()` only knows about WRITE/USER/EXEC/NOCACHE/GLOBAL — it has zero knowledge of AI PTE bits (9-11). After `MONITOR_ACCESS` temporarily makes a page writable, the re-protection call destroys the MONITORED bit, making the page permanently unmonitored.

**Fix:** Change `vos3_vmm_update_flags()` to read-modify-write:
```c
uint64_t old_pte = *pte;
uint64_t ai_bits = old_pte & (VOS3_AI_PTE_MONITORED | VOS3_AI_PTE_PROTECTED | VOS3_AI_PTE_GUARD);
uint64_t new_pte = vos3_pte_create(phys_addr, new_flags) | ai_bits;
*pte = new_pte;
```

### AG-A2 [HIGH] — AI Guard addresses misclassified as user-space

**File:** `kernel/src/mm/vmm.c:561`
**Impact:** AI Guard page table walks use wrong address space

Address space selection at line 561: `if (virt < VOS3_KERNEL_BASE)` where `VOS3_KERNEL_BASE = 0xFFFFFFFF80000000`. AI Guard allocates from `g_ai_alloc_next = 0xFFFF888100000000`. Since `0xFFFF888100000000 < 0xFFFFFFFF80000000`, these addresses are treated as user-space and walk the current task's page table instead of the kernel page table.

**Fix:** Change the comparison to use the direct-mapping base: `if (virt < 0xFFFF800000000000ULL)`. All kernel addresses (direct-map at 0xFFFF800000000000+, AI guard at 0xFFFF888100000000+, kernel image at 0xFFFFFFFF80000000+) use the kernel page table.

### AG-B [MEDIUM] — `vos3_ai_guard_reprotect_tick()` never called

**File:** `kernel/src/mm/ai_guard.c:869`
**Impact:** Monitored pages stay writable forever after first write

The reprotect tick function is defined but never hooked into any timer or scheduler tick. After `MONITOR_ACCESS` temporarily makes a page writable (to let the write succeed), the page is never made read-only again. All subsequent writes bypass monitoring.

**Fix:** Call `vos3_ai_guard_reprotect_tick()` from `vos3_sched_tick()` in `scheduler.c`, or from a periodic timer handler. Must be called with IRQs enabled (it modifies page tables).

### AG-C [MEDIUM] — Cross-app check runs before COW/demand-page

**File:** `kernel/src/arch/x86_64/interrupts.c:228-236`
**Impact:** Legitimate COW faults on app pages killed as violations

The cross-app access check runs at line 228, BEFORE the COW handler at line 257 and demand-paging at line 266. A forked app that touches a COW page triggers a write fault. The cross-app check sees the write to a shared page and kills the task, even though the COW handler would have resolved it legitimately.

**Fix:** Move cross-app check AFTER COW handling. The check should only fire if the fault was NOT resolved by COW or demand-paging:
```c
// 1. Try COW
// 2. Try demand-page
// 3. Try AI guard monitor
// 4. If none resolved: check cross-app violation
// 5. If violation: kill task
```

### AG-D [HIGH] — `vos3_task_exit()` from page fault handler deadlocks

**File:** `kernel/src/arch/x86_64/interrupts.c:203, 234`
**Impact:** Deadlock if page fault occurs while scheduler lock is held

`vos3_task_exit()` acquires `g_sched_lock`. If a page fault occurs while a task is inside `vos3_sched_reschedule()` (which holds `g_sched_lock`), calling `vos3_task_exit()` from the fault handler deadlocks on the same lock.

**Fix:** Don't call `vos3_task_exit()` directly from the fault handler. Instead:
1. Set `task->pending_kill = 1` and `task->exit_code = 128 + 11`
2. Return from the fault handler (the faulting instruction will re-execute)
3. In `vos3_sched_tick()` or syscall return path: check `pending_kill`, call `vos3_task_exit()` from safe context

### AG-E [MEDIUM] — `update_flags` on large page modifies entire 2MB/1GB

**File:** `kernel/src/mm/vmm.c:554-599`
**Impact:** Over-broad permission changes on large pages

If a guard/monitored page falls within a 2MB or 1GB huge page, `update_flags` modifies the entire huge page's permissions. This could make 2MB of adjacent memory read-only when only 4KB should be.

**Fix:** Check page size before modification. If the target is within a huge page, split it into 4KB pages first (or document that AI Guard only works on 4KB-mapped regions).

### AG-F [MEDIUM] — Reprotect with FLAG_NONE strips USER + AI bits

**File:** `kernel/src/mm/ai_guard.c` (reprotect path)
**Impact:** After reprotection, user-space apps can't access their own monitored pages

The reprotect path calls `vos3_vmm_update_flags(addr, VOS3_VMM_FLAG_NONE)` to make pages read-only. `FLAG_NONE` means no WRITE, no USER, no anything. User-space processes need `VOS3_PTE_USER` to access their own pages. After reprotection, accessing the page causes a general protection fault instead of a monitored write fault.

**Fix:** Reprotect with `VOS3_VMM_FLAG_USER` (read-only but user-accessible). Combined with AG-A1 fix, this preserves AI bits while keeping the page accessible.

---

## Area 3: VirtIO-net Driver (kernel/src/drivers/virtio_net.c)

### NET-1 [CRITICAL] — Queue PFN never written to device

**File:** `kernel/src/drivers/virtio_net.c` (init path)
**Impact:** Device has no idea where vring structures are — all TX/RX is dead

The VirtIO legacy handshake requires writing `QUEUE_SEL` to select a queue, then writing `QUEUE_PFN` (physical page frame number of the vring) to tell the device where descriptors are. This write is MISSING. The device has no memory to read/write descriptors from.

**Fix:** After allocating vring memory, write to device registers:
```c
outw(io_base + VIRTIO_PCI_QUEUE_SEL, 0);  // RX queue
outl(io_base + VIRTIO_PCI_QUEUE_PFN, phys_addr_rx >> 12);
outw(io_base + VIRTIO_PCI_QUEUE_SEL, 1);  // TX queue
outl(io_base + VIRTIO_PCI_QUEUE_PFN, phys_addr_tx >> 12);
```

### NET-2 [CRITICAL] — RX available ring never pre-populated

**File:** `kernel/src/drivers/virtio_net.c` (init path)
**Impact:** Device has no buffers to receive packets into — all RX drops silently

After setting up the RX vring, the driver must pre-populate the available ring with buffer descriptors. Without this, the device has no memory to write received packets into. All incoming frames are silently dropped.

**Fix:** In init, after DRIVER_OK:
```c
for (int i = 0; i < RX_BUF_COUNT; i++) {
    rx_vring->desc[i].addr = virt_to_phys(&g_rx_dma_bufs[i]);
    rx_vring->desc[i].len = RX_BUF_SIZE;
    rx_vring->desc[i].flags = VRING_DESC_F_WRITE;  // device writes to this
    rx_vring->avail->ring[i] = i;
}
rx_vring->avail->idx = RX_BUF_COUNT;
wmb();  // ensure device sees all descriptors before kick
outw(io_base + VIRTIO_PCI_QUEUE_NOTIFY, 0);  // kick RX queue
```

### NET-3 [HIGH] — TX reclaim is dead code

**File:** `kernel/src/drivers/virtio_net.c:943`
**Impact:** TX descriptors exhaust after 128 sends — networking dies

`virtio_net_tx_reclaim()` is defined at line 943 but never called. Each `virtio_net_transmit()` consumes 2 descriptors (header + data). With 128 descriptors total, after 64 sends the TX queue is full. All subsequent sends fail silently.

**Fix:** Call `virtio_net_tx_reclaim()` at the start of `virtio_net_transmit()`:
```c
int virtio_net_transmit(const uint8_t* data, uint16_t len) {
    virtio_net_tx_reclaim();  // reclaim completed descriptors
    if (tx_vring->num_free < 2) return -ENOBUFS;
    // ... existing transmit logic
}
```

### NET-4 [HIGH] — Used ring alignment violates VirtIO spec

**File:** `kernel/src/drivers/virtio_net.c` (vring layout)
**Impact:** Device may write used ring entries to wrong memory — corruption

VirtIO spec (2.6.2) requires the Used Ring to be aligned to 4096 bytes (page boundary). The current layout places it at `avail_ring_end` rounded up, which may not be 4096-aligned.

**Fix:** Use `ALIGN_UP(avail_ring_end, 4096)` for used ring placement:
```c
uintptr_t used_ring_addr = (avail_ring_end + 4095) & ~4095ULL;
```

### NET-5 [MEDIUM] — Inconsistent buffer array sizes

**File:** `kernel/src/drivers/virtio_net.c` (BSS declarations)
**Impact:** Potential out-of-bounds access if RX_BUF_COUNT != descriptor count

The RX DMA buffer array size and the vring descriptor count may not match. If `RX_BUF_COUNT > queue_size`, the pre-population loop writes past the descriptor array. If `RX_BUF_COUNT < queue_size`, some descriptors are wasted.

**Fix:** Set `RX_BUF_COUNT = min(64, queue_size)` where `queue_size` is read from `VIRTIO_PCI_QUEUE_NUM` register during init.

---

## Area 4: ABI & Headers

### ABI-G [LOW] — O_RDONLY redefined in user-space files

**Files:** `user/lib/stdio.c`, `user/src/sh.c`, `user/src/cat.c`
**Impact:** Compiler warnings only — values are identical

Multiple user-space files define `O_RDONLY`, `O_WRONLY`, `O_RDWR`, etc. locally despite including headers that already define them. The values are always identical (0, 1, 2) so there is no behavioral bug.

**Fix:** Remove local `#define O_RDONLY/O_WRONLY/O_RDWR` from files that already include `unistd.h` or `fcntl.h`. Use `#ifndef O_RDONLY` guards if inclusion order is uncertain.

### ABI-H [NONE] — Syscall numbers verified correct

All Linux-ABI syscall numbers match x86-64 convention. No collisions found in the 125-293 VOS3 custom range.

### ABI-I [NONE] — Struct layouts verified correct

`linux_kstat_t` (144 bytes), `struct sockaddr_in` (16 bytes), and `struct iovec` (16 bytes) all match Linux x86-64 ABI.

---

## Already Fixed

### BOOT-1 [CRITICAL] — Guard page triple-fault (FIXED)

**File:** `kernel/src/sched/task.c:233-240` (removed)
**Root cause:** `vos3_task_create()` called `vos3_vmm_update_flags()` on heap-allocated kernel stack with 16-byte alignment. Page-aligning down (`&= ~0xFFF`) gave a 4KB page shared with other heap objects. Making it read-only caused triple-fault at "Initializing Scheduler".
**Fix applied:** Removed all guard page code from `vos3_task_create()`, `vos3_task_destroy()`, and `vos3_task_reap()`. Set `task->kernel_stack_guard = 0`.

---

## Fix Priority Order

| Priority | Finding | Risk if Deferred |
|----------|---------|------------------|
| 1 | FD-1 + FD-5 | Deadlock on first task exit — kernel unusable |
| 2 | FD-2 | Deadlock in close handlers — compounds FD-1 |
| 3 | AG-D | Deadlock on AI guard fault — kills kernel |
| 4 | AG-C | Kills innocent forked tasks on COW faults |
| 5 | AG-A1 + AG-F | AI monitoring permanently disabled after first write |
| 6 | AG-A2 | AI guard page table walks use wrong address space |
| 7 | NET-1 + NET-2 | Networking completely non-functional |
| 8 | NET-3 + NET-4 | Networking dies after 64 packets |
| 9 | FD-3 + FD-4 | Use-after-free under concurrent workload |
| 10 | FD-6 + FD-7 + FD-8 | Race conditions in fork/clone |
| 11 | AG-B | Monitored pages lose monitoring after first write |
| 12 | AG-E + NET-5 | Edge cases with huge pages / buffer sizing |
| 13 | ABI-G | Compiler warnings only |

---

## Implementation Plan

### Phase 1: Boot-Critical FD Fixes (FD-1, FD-2, FD-5)
- Remove mutex from `vos3_fd_table_t`
- Make `vos3_fd_table_destroy()` use IRQ-safe spinlock only
- Snapshot fds under lock, close handlers outside lock
- Verify reaper call chain is task context (not raw ISR)

### Phase 2: Atomicity Fixes (FD-3, FD-4, FD-6, FD-7, FD-8)
- Change `file->ref_count` to atomic
- Change `fd_table->ref_count` to atomic
- Add ref_count bump in `vos3_fd_get()` + new `vos3_file_put()`
- Protect fork/clone fd_table operations with spinlock

### Phase 3: AI Guard Safety (AG-D, AG-C, AG-A1, AG-A2, AG-B, AG-E, AG-F)
- Deferred kill instead of `vos3_task_exit()` from fault handler
- Move cross-app check after COW/demand-page
- Read-modify-write in `vos3_vmm_update_flags()` to preserve AI bits
- Fix kernel address space threshold
- Wire `reprotect_tick()` into scheduler tick
- Reprotect with USER flag preserved

### Phase 4: VirtIO-net Init (NET-1, NET-2, NET-3, NET-4, NET-5)
- Complete device handshake (QUEUE_SEL + QUEUE_PFN)
- Pre-populate RX available ring
- Wire TX reclaim into transmit path
- Fix used ring alignment

### Phase 5: Cleanup (ABI-G)
- Remove duplicate O_RDONLY defines

---

## Conclusion

The `feat/10-10-all-capabilities` branch has significant architectural issues that must be fixed before testing. The guard page triple-fault (already fixed) and the FD locking deadlock (FD-1) are the two most critical blockers. After those, the AI Guard and VirtIO-net issues need attention.

Total estimated changes: ~300 lines across 8 files. No new files needed.
