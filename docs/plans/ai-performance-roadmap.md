# VOS3 v2.0: AI-Era Architecture & Performance Roadmap

**Date:** 2026-03-20
**Author:** Performance Engineering
**Baseline:** 352 PASS, 0 FAIL | Branch `feat/10-10-all-capabilities` (post-Phase 1 HugePages)
**Target:** Production-grade AI workloads (LLM inference, multi-agent orchestration, 10GB+ context windows)

---

## Motivation

The Performance Baseline Report (2026-03-20) identified three critical gaps for March 2026 AI workloads:

1. **TLB pressure**: A 4MB SHM segment requires 1024 PTEs. A 10GB LLM context window would consume 2,621,440 TLB entries — impossible on x86_64's 1536-entry L2 TLB. **HugePages reduce this 512x.**
2. **IPC latency**: SHM dispatch uses `g_shm_lock` (global spinlock) for table ops. Under 8-agent concurrent load, throughput drops 3.3x (3200 → 984 MB/s). **Lockless ring buffers eliminate this contention.**
3. **Scheduling fairness**: All agents get equal quanta (10 ticks). An agent generating Time-To-First-Token (TTFT) should preempt background batch work. **Priority preemption solves this.**

---

## Phase 1: High-Performance Memory (HugePages)

**Goal:** Enable 2MB page mappings for AI memory segments. 512x TLB pressure reduction.
**Effort:** ~300 lines kernel code | **Priority:** CRITICAL

### 1.1 Extend `vos3_vmm_map()` for 2MB Pages

**File:** `kernel/src/mm/vmm.c` (lines 270-320)

Currently, `vos3_vmm_map()` walks 4 levels of page tables and rejects anything that isn't level 4 (4KB PTE):

```c
// Current code at lines 292-296:
if (level != 4) {
    vos3_spinlock_release(&as->lock);
    vos3_irq_restore(irqflags);
    return VOS3_VMM_ERR_MAPPED;
}
```

**Change:** Accept level 3 (Page Directory) entries when `VOS3_VMM_FLAG_LARGE` is set:

```c
// New logic:
if (flags & VOS3_VMM_FLAG_LARGE) {
    // Caller requests 2MB mapping — stop at PD level (level 3)
    if (level != 3) {
        // Need to reach PD level; if we hit an existing PT, error
        vos3_spinlock_release(&as->lock);
        vos3_irq_restore(irqflags);
        return VOS3_VMM_ERR_MAPPED;
    }
    // Install 2MB PDE with PS bit
    uint64_t pde = (phys & VOS3_PTE_LARGE_ADDR_MASK)
                 | VOS3_PTE_PRESENT | VOS3_PTE_LARGE
                 | (pte_flags & (VOS3_PTE_WRITABLE | VOS3_PTE_USER
                                | VOS3_PTE_NX | VOS3_PTE_WRITETHROUGH));
    *pte_ptr = pde;
    __asm__ volatile("invlpg (%0)" : : "r"(virt) : "memory");
} else {
    // Existing 4KB path (unchanged)
    if (level != 4) { ... return VOS3_VMM_ERR_MAPPED; }
    *pte_ptr = (phys & VOS3_PTE_ADDR_MASK) | pte_flags;
    __asm__ volatile("invlpg (%0)" : : "r"(virt) : "memory");
}
```

**Constants already defined:**
- `kernel/include/vos/vmm.h:98` — `VOS3_PTE_LARGE ((uint64_t)(1ULL << 7))`
- `kernel/include/vos/vmm.h:48` — `VOS3_PTE_LARGE_ADDR_MASK (0x000FFFFFFFE00000ULL)`
- `kernel/include/arch/x86_64/memory_map.h:44` — `VOS3_LARGE_PAGE_SIZE 0x200000U`

**New constant to add in `vmm.h`:**
```c
#define VOS3_VMM_FLAG_LARGE  (1U << 8)  /* Request 2MB page mapping */
```

### 1.2 Boot-Time HugePage Reservation Pool

**File:** `kernel/src/mm/pmm.c` (lines 404-463)

**Problem:** After boot, physical memory fragments as 4KB pages are allocated for page tables, kernel stacks, and SHM segments. Late HugePage allocation may fail.

**Design:** Pre-reserved pool at boot time (simpler than buddy allocator, eliminates fragmentation):

```c
// New data structures in pmm.c:
#define VOS3_HUGEPAGE_RESERVE_MAX  128   // Max 256MB of HugePages
static uint64_t g_hugepage_pool[VOS3_HUGEPAGE_RESERVE_MAX];
static uint32_t g_hugepage_pool_count = 0;
static uint32_t g_hugepage_pool_used = 0;
static vos3_spinlock_t g_hugepage_lock = VOS3_SPINLOCK_INIT;
```

**API:**
```c
// New functions:
uint64_t vos3_pmm_alloc_huge(void);   // Returns 2MB-aligned physical address (from pool)
void     vos3_pmm_free_huge(uint64_t phys);  // Returns to pool

// Existing function unchanged for 4KB:
uint64_t vos3_pmm_alloc_page(void);   // Existing atomic CAS path
```

**Bootstrap:** At boot (before any 4KB allocations), `vos3_pmm_reserve_hugepages()` scans the available memory and pre-populates `g_hugepage_pool` with all 2MB-aligned, 2MB-contiguous free blocks. Estimated yield from 512MB RAM: ~240 HugePages (480MB), reserve ~64 (128MB) for AI workloads.

### 1.3 SHM HugePage Integration & `walk_to_pd()` Audit

**File:** `kernel/src/mm/vmm.c` — validate 2MB page mapping audit

The current `walk_page_tables()` function always creates a PT (page table) at the PD (page directory) level before returning. For 2MB pages, we need a new internal function `walk_to_pd()` that stops at the PD level without creating a PT:

```c
// New internal function in vmm.c:
// Returns pointer to PDE, does NOT create PT on mismatch
static uint64_t* walk_to_pd(vos3_as_t* as, uint64_t virt, int* level) {
    // Same logic as walk_page_tables() but stop at level 3 (PD)
    // If we hit an existing PT at level 3, return error code
    // Caller (vos3_vmm_map) checks level == 3 for LARGE mappings
}
```

**Why:** `walk_page_tables()` always creates a PT, which wastes 4KB for each 2MB page mapping attempt. `walk_to_pd()` ensures we can map 2MB pages directly to PDEs without unnecessary PT allocations.

### 1.4 SHM HugePage Integration & Boot-Time Reservation Call

**File:** `kernel/src/ipc/shm.c` (lines 92-98) + `kernel/src/init/init.c`

When `SHM_HUGETLB` flag is set on `vos3_shm_create()`:

```c
#define VOS3_SHM_HUGETLB  (1U << 4)

// In vos3_shm_create():
if (flags & VOS3_SHM_HUGETLB) {
    // Round size up to 2MB boundary
    size_t huge_count = (size + VOS3_LARGE_PAGE_SIZE - 1) / VOS3_LARGE_PAGE_SIZE;
    shm->phys_addr = 0;
    // Allocate from HugePage pool
    for (size_t i = 0; i < huge_count; i++) {
        uint64_t hp = vos3_pmm_alloc_huge();
        if (hp == 0) { /* rollback + return -ENOMEM */ }
        if (i == 0) shm->phys_addr = hp;
    }
    shm->size = huge_count * VOS3_LARGE_PAGE_SIZE;
    shm->flags |= VOS3_SHM_HUGETLB;
}
```

In `vos3_shm_map()` for HugePage regions:
```c
if (shm->flags & VOS3_SHM_HUGETLB) {
    // Map 2MB pages instead of 4KB
    for (size_t off = 0; off < shm->size; off += VOS3_LARGE_PAGE_SIZE) {
        vos3_vmm_map(user_virt + off, shm->phys_addr + off,
                     VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE | VOS3_PTE_USER
                     | VOS3_VMM_FLAG_LARGE);
    }
}
```

**Boot-time initialization:**
```c
// In kernel/src/init/init.c, after vos3_pmm_init():
#define VOS3_HUGEPAGE_RESERVE_COUNT  64  // 64 * 2MB = 128MB reserved
vos3_pmm_reserve_hugepages(VOS3_HUGEPAGE_RESERVE_COUNT);
VOS3_INFO("PMM: Reserved %u HugePages for AI workloads", VOS3_HUGEPAGE_RESERVE_COUNT);
```

### 1.5 TLB Impact Analysis

| Segment Size | 4KB Pages | 2MB Pages | TLB Reduction |
|-------------|-----------|-----------|---------------|
| 4 MB | 1,024 | 2 | 512x |
| 128 MB | 32,768 | 64 | 512x |
| 1 GB | 262,144 | 512 | 512x |
| 10 GB | 2,621,440 | 5,120 | 512x |

x86_64 L2 TLB capacity: 1,536 entries (Intel Skylake+). A 10GB context window with 4KB pages causes **1,705x TLB oversubscription**. With 2MB pages: **3.3x oversubscription** — manageable with hardware prefetch.

---

## Phase 1.2: Infrastructure Stabilization

**Goal:** Harden kernel subsystems exposed by Phase 1 testing. Fix demand paging races, reorganize syscall table, add FPU/SSE context switch isolation.
**Effort:** ~250 lines kernel code | **Priority:** CRITICAL (blocks Phase 2+)
**Baseline:** 352 PASS, 0 FAIL (post-Phase 1 HugePage integration)

### 1.2.1 Demand Paging TLB Invalidation Race

**File:** `kernel/src/mm/vmm.c` (lines 1028-1031)

**Bug:** `vos3_vmm_map_user()` releases the address space spinlock *before* issuing `invlpg`. On SMP (or with interrupt-driven rescheduling), another CPU/task can read the stale TLB entry between spinlock release and TLB flush:

```c
// Current code (vmm.c:1028-1031) — RACE WINDOW:
vos3_spinlock_release(&as->lock);   // line 1028
vos3_irq_restore(irqflags);          // line 1029
                                      // ← STALE TLB VISIBLE HERE
vos3_vmm_invlpg((uintptr_t)vaddr);   // line 1031
```

**Fix:** Move `invlpg` before releasing the spinlock. The TLB invalidation is a single-instruction operation (~100 cycles) and safe to execute under the lock:

```c
// Fixed code:
vos3_vmm_invlpg((uintptr_t)vaddr);   // Flush BEFORE releasing lock
vos3_spinlock_release(&as->lock);
vos3_irq_restore(irqflags);
```

**Impact:** Eliminates intermittent `demand_paging_heap` failures under load. The race manifests when:
1. Page fault handler allocates a page and calls `vos3_vmm_map_user()`
2. Lock released, but TLB still holds "not present" entry
3. Timer IRQ fires, preempts to another task in same address space
4. That task touches the same page → second (spurious) page fault
5. Double-fault or stale data depending on timing

### 1.2.2 Demand Paging Zero-Fill Atomicity

**File:** `kernel/src/arch/x86_64/interrupts.c` (lines 320-324)

**Issue:** Zero-fill of anonymous demand-paged memory happens *without* holding any lock on the physical page. If two tasks fault on the same COW-shared page simultaneously, they may race on the zero-fill. Current single-CPU VOS3 is safe (interrupts disabled during fault handler), but SMP would break.

```c
// Current code (interrupts.c:320-324) — safe on single-CPU only:
uint64_t* ptr = (uint64_t*)kvirt;
for (size_t i = 0; i < VOS3_PAGE_SIZE / sizeof(uint64_t); i++) {
    ptr[i] = 0ULL;
}
```

**Status:** Low priority for SMP. Document as technical debt. No code change needed for single-CPU.

### 1.2.3 Syscall Table Reorganization

**Problem:** VOS3 custom syscalls collide with Linux standard numbers in several ranges:

| VOS3 Range | Collisions | Linux Occupants |
|------------|-----------|-----------------|
| 150-164 (sockets) | ~15 | inotify_init1(150), preadv(156), pwritev(157), etc. |
| 220-221 (blkdev) | 2 | semctl(220), shmctl(221) |
| 74 (SHM_SIZE, fixed) | 1 | fsync(74) — already moved to 129 |

**Proposal:** Migrate all 40+ VOS3 custom syscalls to the **400-499** range (well above Linux's ~450 defined syscalls which end around 350 on x86_64):

```
400-409: IPC Core     (msgq_create, msgq_send, msgq_recv, msgq_destroy, ...)
410-419: SHM          (shm_create, shm_destroy, shm_map, shm_unmap, shm_size, shm_find)
420-429: Pipes        (pipe_create, pipe_read, pipe_write, pipe_close)
430-439: Sockets      (socket, bind, listen, accept, connect, sendto, recvfrom, ...)
440-449: Block Device (blk_read, blk_write, blk_info, blk_flush)
450-459: Network Mgmt (ifconfig, route_add, route_del, dns_resolve)
460-469: AI Guard     (app_ctx_create, app_ctx_destroy, app_ctx_switch, guard_check)
470-479: Config       (config_get, config_set, delegation_get, delegation_set)
480-489: Diagnostics  (sysinfo, klog_read, perf_counter)
490-499: Reserved     (future phases: KV-Cache, MCP, channel)
```

**Migration strategy:**
1. Define `VOS3_SYSCALL_BASE 400` in `kernel/include/vos/syscall.h`
2. Renumber all custom syscalls relative to base
3. Update user-space headers (`user/include/syscall.h`)
4. Keep Linux-standard syscalls (read=0, write=1, fork=57, etc.) unchanged
5. Expand `g_syscall_table` from 350 to 512 entries

### 1.2.4 Context Switch FPU/SSE State Isolation

**File:** `kernel/src/sched/context.S` (lines 41-77)

**CRITICAL Security Gap:** The context switch saves only callee-saved GPRs (R15, R14, R13, R12, RBX, RBP). It does NOT save:
- **XMM0-XMM15** (256 bytes) — SSE/SSE2 registers used by musl libc (`memcpy`, `strlen`, math)
- **x87 FPU state** (108 bytes) — floating-point stack, control/status words
- **MXCSR** (4 bytes) — SSE control/status register

**Impact:** Task A's XMM register contents leak to Task B after context switch. This is:
- **Security vulnerability:** AI agent A can read agent B's SSE-computed data
- **Correctness bug:** musl `memcpy` uses XMM0-XMM3 internally; if Task B's `memcpy` resumes with Task A's XMM state, data corruption occurs

**Fix:** Add FXSAVE/FXRSTOR to context switch:

```asm
vos3_context_switch:
    testq %rdi, %rdi
    jz .Lload_new_context

    pushq %rbp
    pushq %rbx
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15

    /* Save FPU/SSE state (512 bytes, 16-byte aligned) */
    subq $512, %rsp
    fxsave (%rsp)

    movq %rsp, (%rdi)

.Lload_new_context:
    movq %rsi, %rsp

    /* Restore FPU/SSE state */
    fxrstor (%rsp)
    addq $512, %rsp

    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbx
    popq %rbp
    ret
```

**Requirements:**
- Stack must be 16-byte aligned for FXSAVE (already guaranteed by pushq sequence: 6×8 = 48, then sub 512 → 560, need to verify alignment)
- `vos3_context_t` size increases from 56 to 568 bytes
- Task kernel stack usage increases by 512 bytes per context save (well within 64KB vmap stacks)
- CR4.OSFXSR must be set (already enabled for musl SSE support)

### 1.2.5 Implementation Priority

| Sub-task | Priority | Effort | Risk if Deferred |
|----------|----------|--------|------------------|
| TLB invalidation fix (1.2.1) | P0 | 5 lines | Intermittent page faults |
| Syscall reorganization (1.2.3) | P1 | ~150 lines | More collisions as syscalls grow |
| FPU/SSE context switch (1.2.4) | P1 | ~30 lines ASM | Data leak between tasks |
| Zero-fill atomicity (1.2.2) | P2 | Document only | SMP-only; single-CPU safe |

---

## Phase 1.5: NPU/CPU Unified Memory Visibility

**Goal:** Enable explicit cache policy control for NPU (Neural Processing Unit) and PCIe device MMIO BAR spaces. CPU-side Write-Back caching is incorrect for device registers (stale reads) and suboptimal for data buffers (cache pollution).
**Effort:** ~200 lines kernel code | **Priority:** HIGH

### 1.5.1 New VMM Cache Policy Flags

**File:** `kernel/include/vos/vmm.h`

NPU and PCIe devices expose MMIO BAR spaces requiring explicit cache policy control:

```c
// New VMM flags (added to vos3_vmm_flags_t enum):
VOS3_VMM_FLAG_WRITETHROUGH  = (1U << 7),   // Write-Through caching
VOS3_VMM_FLAG_WRITECOMBINE  = (1U << 8),   // Write-Combining (optimal for GPU/NPU buffers)
VOS3_VMM_FLAG_DEVICE        = (1U << 9),   // Uncacheable (UC) for MMIO registers
```

### 1.5.2 PAT Bit Position Constants

**File:** `kernel/include/vos/vmm.h`

The PAT (Page Attribute Table) bit position differs between 4KB PTEs and 2MB PDEs because the PS (Page Size) bit occupies bit 7 in PDEs:

```c
#define VOS3_PTE_PAT_4K    ((uint64_t)(1ULL << 7))   // PAT bit for 4KB PTEs
#define VOS3_PTE_PAT_LARGE ((uint64_t)(1ULL << 12))  // PAT bit for 2MB PDEs
```

### 1.5.3 Extended PTE Flag Conversion

**Function:** `vos3_vmm_flags_to_pte_ex(flags, is_large)`

Full cache policy mapping table using Intel default PAT MSR:

| Policy | VMM Flag | PCD | PWT | PAT | Intel PAT Entry |
|--------|----------|-----|-----|-----|-----------------|
| Write-Back (default) | none | 0 | 0 | 0 | Entry 0 (WB) |
| Write-Through | `WRITETHROUGH` | 0 | 1 | 0 | Entry 1 (WT) |
| Uncacheable-Minus | `NOCACHE` | 1 | 0 | 0 | Entry 2 (UC-) |
| Uncacheable (strong) | `DEVICE` | 1 | 1 | 0 | Entry 3 (UC) |
| Write-Combining | `WRITECOMBINE` | 0 | 0 | 1 | Entry 4 (WC) |

For `WRITECOMBINE`, the PAT bit position depends on `is_large`: bit 7 for 4KB PTEs, bit 12 for 2MB PDEs.

### 1.5.4 PMM Memory Tiering

**File:** `kernel/src/mm/pmm.c`

`pmm.c` must distinguish RAM ranges (normal allocation) from DEVICE BAR ranges:

- Add `VOS3_PMM_RANGE_DEVICE` flag to physical range table
- DEVICE pages are identity-mapped, never COW'd, never in free pool
- PMM allocation functions skip DEVICE ranges

### 1.5.5 Page Fault Safety

Fault handler must detect DEVICE pages (PCD+PWT set) and reject COW/demand-paging:
- User-space faults on DEVICE pages → deliver SIGSEGV
- Kernel faults on DEVICE pages → panic (indicates kernel bug)

### 1.5.6 AI Guard Device Access Control

`vos3_ai_guard_check_device_access(addr, app_id)` enforces cross-app isolation on NPU memory regions. Each app_id has an explicit allowlist of MMIO BAR ranges it may access.

### 1.5.7 SHM Device Type

`VOS3_SHM_TYPE_NPU` for device-backed shared memory regions. These regions:
- Use `VOS3_VMM_FLAG_WRITECOMBINE` for data buffers
- Use `VOS3_VMM_FLAG_DEVICE` for control registers
- Are excluded from COW cloning during fork()

---

## Phase 2: Ultra-Low Latency IPC (Lockless Ring Buffers)

**Goal:** Replace spinlock-based SHM dispatch with lockless SPSC ring buffers. Zero contention between producer/consumer agents.
**Effort:** ~400 lines kernel + user code | **Priority:** HIGH

### 2.1 SPSC Ring Buffer Design

**New file:** `kernel/include/vos/ringbuf.h`

```c
#include <vos/atomic.h>

#define VOS3_RINGBUF_SIZE     4096   // Must be power of 2
#define VOS3_RINGBUF_MASK     (VOS3_RINGBUF_SIZE - 1)
#define VOS3_CACHELINE_PAD    128    // 2 cache lines to prevent false sharing

typedef struct __attribute__((aligned(128))) {
    /* Producer side — exclusively written by producer */
    volatile uint64_t head __attribute__((aligned(VOS3_CACHELINE_PAD)));
    uint8_t _pad_producer[VOS3_CACHELINE_PAD - sizeof(uint64_t)];

    /* Consumer side — exclusively written by consumer */
    volatile uint64_t tail __attribute__((aligned(VOS3_CACHELINE_PAD)));
    uint8_t _pad_consumer[VOS3_CACHELINE_PAD - sizeof(uint64_t)];

    /* Shared data — read by both, written by neither (via indices) */
    uint8_t data[VOS3_RINGBUF_SIZE] __attribute__((aligned(VOS3_CACHELINE_PAD)));
} vos3_ringbuf_t;
```

**Atomic semantics (C11 acquire/release):**

The producer thread executes `vos3_ringbuf_write()`, the consumer thread executes `vos3_ringbuf_read()`. Cross-thread memory ordering is guaranteed via `__ATOMIC_ACQUIRE` on tail loads and `__ATOMIC_RELEASE` on head stores (and vice versa for consumer).

```c
static inline int vos3_ringbuf_write(vos3_ringbuf_t* rb,
                                      const void* buf, size_t len)
{
    uint64_t head = __atomic_load_n(&rb->head, __ATOMIC_RELAXED);
    uint64_t tail = __atomic_load_n(&rb->tail, __ATOMIC_ACQUIRE);  // Full barrier

    size_t avail = VOS3_RINGBUF_SIZE - (head - tail);
    if (len > avail) return -1;  // Full

    // Copy data with wrap-around
    for (size_t i = 0; i < len; i++)
        rb->data[(head + i) & VOS3_RINGBUF_MASK] = ((const uint8_t*)buf)[i];

    __atomic_store_n(&rb->head, head + len, __ATOMIC_RELEASE);  // Full barrier
    return (int)len;
}

static inline int vos3_ringbuf_read(vos3_ringbuf_t* rb,
                                     void* buf, size_t len)
{
    uint64_t tail = __atomic_load_n(&rb->tail, __ATOMIC_RELAXED);
    uint64_t head = __atomic_load_n(&rb->head, __ATOMIC_ACQUIRE);  // Full barrier

    size_t avail = head - tail;
    if (len > avail) len = avail;
    if (len == 0) return 0;

    for (size_t i = 0; i < len; i++)
        ((uint8_t*)buf)[i] = rb->data[(tail + i) & VOS3_RINGBUF_MASK];

    __atomic_store_n(&rb->tail, tail + len, __ATOMIC_RELEASE);  // Full barrier
    return (int)len;
}
```

### 2.2 Cache-Line Padding for False Sharing Prevention

**Current state:** `VOS3_CACHE_LINE_SIZE` = 64 bytes (`memory_map.h:38`). Task structs are already 64-byte aligned (`task.h:253`).

**Problem:** When producer (agent A) and consumer (agent B) access adjacent fields in a shared struct, they cause cache-line bouncing on SMP systems. Even on single-CPU VOS3, QEMU's cache simulation can exhibit this.

**Solution:** 128-byte padding (2 cache lines) between producer and consumer indices in the ring buffer struct. This guarantees that `head` and `tail` live on different cache lines even if the struct is misaligned by one cache line.

**Macro for general use:**
```c
// kernel/include/vos/atomic.h:
#define VOS3_CACHELINE_ALIGNED __attribute__((aligned(128)))
#define VOS3_CACHELINE_PAD(name) uint8_t name[128]
```

### 2.3 SHM Channel Abstraction

**New file:** `kernel/src/ipc/channel.c`

A channel wraps two ring buffers (one per direction) in a shared memory region:

```c
typedef struct __attribute__((aligned(4096))) {
    vos3_ringbuf_t  producer_to_consumer;  // Agent A → Agent B
    vos3_ringbuf_t  consumer_to_producer;  // Agent B → Agent A
    volatile uint32_t magic;               // 0x4348414E ('CHAN')
    volatile uint32_t state;               // OPEN, CLOSED, ERROR
} vos3_channel_t;

// Syscall interface:
// SYS_CHANNEL_CREATE (270): Creates SHM-backed channel, returns channel_id
// SYS_CHANNEL_SEND   (271): Non-blocking write to ring buffer
// SYS_CHANNEL_RECV   (272): Non-blocking read from ring buffer
// SYS_CHANNEL_CLOSE  (273): Marks channel closed, wakes waiters
```

**Blocking semantics:** When `vos3_ringbuf_read()` returns 0 (empty), the consumer calls `futex_wait(&rb->head, current_head)`. The producer calls `futex_wake(&rb->head, 1)` after writing. This gives zero-spin blocking with sub-microsecond wake latency.

### 2.4 Migration Path from Spinlock SHM

| Current (spinlock) | New (lockless) | Compatibility |
|--------------------|---------------|---------------|
| `vos3_shm_create()` | `vos3_channel_create()` | SHM API unchanged, channel is additive |
| `g_shm_lock` per table op | No lock on data path | Table ops still use spinlock (rare) |
| `memcpy` to/from SHM | `ringbuf_write/read` | Zero-copy for small messages |
| User maps full SHM | User maps channel struct | Smaller mapping footprint |

**Backward compatibility:** Existing SHM API (`SYS_SHM_CREATE/MAP/UNMAP/DESTROY`) is unchanged. Channels are a new, higher-performance API layered on top of SHM physical pages.

---

## Phase 3: Agentic Scheduling (Affinity & Priority)

**Goal:** Pin inference threads to cores. Prioritize TTFT over batch work.
**Effort:** ~200 lines kernel code | **Priority:** HIGH

### 3.1 CPU Affinity Mask

**File:** `kernel/include/vos/task.h`

Currently, the task struct has `uint32_t cpu_id` (line 159) and `VOS3_TASK_FLAG_PINNED` (line 74) for basic pinning. This is insufficient for multi-core affinity control.

**Change:** Add a CPU affinity bitmask:

```c
// In vos3_task_t struct:
uint64_t cpu_affinity;  // Bitmask: bit N = allowed on CPU N (0 = any)
```

**Scheduler integration** in `kernel/src/sched/scheduler.c`:

```c
// In pick_next_task() — lines 559+:
static vos3_task_t* pick_next_task(void) {
    uint32_t this_cpu = vos3_get_cpu_id();
    for (int p = VOS3_PRIORITY_REALTIME; p >= VOS3_PRIORITY_IDLE; p--) {
        vos3_task_t* t = g_run_queues[p].head;
        while (t) {
            // Skip tasks not allowed on this CPU
            if (t->cpu_affinity != 0 &&
                !(t->cpu_affinity & (1ULL << this_cpu))) {
                t = t->next_ready;
                continue;
            }
            dequeue_task(&g_run_queues[p], t);
            return t;
        }
    }
    return &g_idle_tasks[this_cpu];
}
```

**Syscall:** `SYS_SCHED_SETAFFINITY (203)` — Linux-compatible:
```c
// rdi = pid (0 = self), rsi = cpusetsize, rdx = mask_ptr
long sys_sched_setaffinity(long pid, long cpusetsize, long mask_ptr) {
    vos3_task_t* task = (pid == 0) ? vos3_sched_current() : find_task(pid);
    if (!task) return -ESRCH;
    uint64_t mask;
    if (vos3_copy_from_user(&mask, (void*)mask_ptr, sizeof(mask)))
        return -EFAULT;
    task->cpu_affinity = mask;
    return 0;
}
```

### 3.2 Priority Preemption for TTFT

**Problem:** When an agent is generating Time-To-First-Token (responding to a user query), it should preempt lower-priority batch inference that can tolerate latency.

**Design:** A new priority level `VOS3_PRIORITY_TTFT` between HIGH and REALTIME:

```c
// kernel/include/vos/task.h — updated enum:
typedef enum vos3_task_priority {
    VOS3_PRIORITY_IDLE      = 0U,
    VOS3_PRIORITY_LOW       = 1U,  // Batch inference, background tasks
    VOS3_PRIORITY_NORMAL    = 2U,  // Standard agent work
    VOS3_PRIORITY_HIGH      = 3U,  // Active agent responding
    VOS3_PRIORITY_TTFT      = 4U,  // Time-To-First-Token (preempts HIGH)
    VOS3_PRIORITY_REALTIME  = 5U,  // Kernel tasks, bridge
    VOS3_PRIORITY_COUNT     = 6U
} vos3_task_priority_t;
```

**Time slice allocation:**
```c
// kernel/include/vos/scheduler.h:
#define VOS3_SCHED_SLICE_TTFT     30U  // 300ms uninterrupted for token gen
```

**Dynamic priority boost:** When an agent begins token generation (signaled via `SYS_SCHED_SETPRIORITY` or automatically when writing to a response pipe), temporarily boost to TTFT for sub-microsecond preemption latency. When the first token is emitted, decay back to HIGH.

```c
// Syscall: SYS_SCHED_SETPRIORITY (not yet assigned)
// rdi = pid (0 = self), rsi = new_priority
long sys_sched_setpriority(long pid, long priority) {
    if (priority >= VOS3_PRIORITY_REALTIME) return -EPERM;  // Only kernel
    vos3_task_t* task = (pid == 0) ? vos3_sched_current() : find_task(pid);
    if (!task) return -ESRCH;

    vos3_irqflags_t f = vos3_irq_save();
    vos3_spinlock_acquire(&g_sched_lock);

    // Move between run queues
    if (task->state == VOS3_TASK_READY) {
        dequeue_task(&g_run_queues[task->priority], task);
        task->priority = (vos3_task_priority_t)priority;
        task->time_slice = priority_to_slice(priority);
        enqueue_task(&g_run_queues[priority], task);
    } else {
        task->priority = (vos3_task_priority_t)priority;
        task->time_slice = priority_to_slice(priority);
    }

    vos3_spinlock_release(&g_sched_lock);
    vos3_irq_restore(f);

    // If raised above current task, trigger immediate reschedule
    // This gives sub-microsecond preemption for TTFT agents
    vos3_task_t* cur = vos3_sched_current();
    if (priority > cur->priority)
        vos3_sched_reschedule();

    return 0;
}
```

**Sub-microsecond preemption:** When priority is raised above the currently running task, `vos3_sched_reschedule()` immediately triggers a context switch. On x86_64, the task scheduling overhead is ~500 cycles (5 microseconds on 1 GHz CPU), but the latency between raising priority and preemption is under 1 microsecond due to the synchronous `reschedule()` call (not waiting for next timer tick).

### 3.3 Immediate Preemption on Priority Raise

**File:** `kernel/src/sched/scheduler.c`

Currently, preemption only happens on timer tick (100Hz). A TTFT task raised to priority 4 may wait up to 10ms before being scheduled.

**Fix:** After `sys_sched_setpriority()` raises a task's priority above the current task, call `vos3_sched_reschedule()` immediately (as shown above). This gives sub-microsecond preemption latency for TTFT.

---

## Phase 4: MCP Bridge Integration

**Goal:** Kernel-level device driver `/dev/mcp` enabling isolated agents to access Model Context Protocol tools under AI Guard monitoring.
**Effort:** ~500 lines kernel + user code | **Priority:** MEDIUM

### 4.1 Architecture

```
┌─────────────────────────────────────────────────────┐
│  Agent Process (user-space)                         │
│  ┌───────────────┐  ┌────────────────┐             │
│  │ LLM Inference  │  │ MCP Client Lib │             │
│  └───────────────┘  └───────┬────────┘             │
│                             │ write(fd, json_req)   │
│                             │ read(fd, json_resp)   │
├─────────────────────────────┼───────────────────────┤
│  Kernel                     │                       │
│  ┌──────────────────────────▼──────────────────┐   │
│  │  /dev/mcp (chardev)                         │   │
│  │  ┌─────────────┐  ┌──────────────────────┐ │   │
│  │  │ JSON Parser  │  │ AI Guard Policy Check│ │   │
│  │  └──────┬──────┘  └──────────┬───────────┘ │   │
│  │         │                     │              │   │
│  │  ┌──────▼─────────────────────▼────────┐    │   │
│  │  │  Tool Dispatcher                     │    │   │
│  │  │  ┌────────┐ ┌────────┐ ┌──────────┐│    │   │
│  │  │  │FS Tools│ │Net Tool│ │Exec Tool ││    │   │
│  │  │  └────────┘ └────────┘ └──────────┘│    │   │
│  │  └────────────────────────────────────┘    │   │
│  └────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### 4.2 Device Driver: `/dev/mcp`

**New file:** `kernel/src/drivers/mcp_dev.c`

```c
// Character device operations:
static vos3_file_ops_t mcp_fops = {
    .read  = mcp_read,    // Read JSON response
    .write = mcp_write,   // Write JSON request
    .poll  = mcp_poll,    // Poll for response ready
    .close = mcp_close,   // Cleanup per-session state
};

// Per-open session state:
typedef struct {
    uint32_t magic;           // 0x4D435044 ('MCPD')
    uint32_t app_id;          // AI Guard application ID
    vos3_ringbuf_t request;   // Agent → kernel (JSON requests)
    vos3_ringbuf_t response;  // Kernel → agent (JSON responses)
    uint32_t tool_allowlist;  // Bitmask of allowed tools
} mcp_session_t;
```

### 4.3 AI Guard Integration — Deep-Packet Inspection

Every tool invocation through `/dev/mcp` passes through AI Guard policy with deep-packet inspection:

```c
static int mcp_check_policy(mcp_session_t* sess, const char* tool_name,
                            const char* json_params) {
    // 1. Deep-packet inspection: parse JSON request
    //    Extract tool name and parameter values from request
    //    Example: {"tool": "fs.read", "params": {"path": "/etc/passwd"}}
    //    → Check if /etc/passwd is in allowlist for this app_id

    int tool_id = mcp_tool_name_to_id(tool_name);
    if (tool_id < 0) return -EINVAL;

    // 2. Check tool allowlist for this app_id
    if (!(sess->tool_allowlist & (1U << tool_id))) {
        vos3_ai_guard_log_violation(sess->app_id, tool_name, "tool_not_allowed");
        return -EPERM;
    }

    // 3. Validate parameters against tool-specific policy
    //    For fs.read: check path prefix allowlist
    //    For net.http_get: check domain allowlist
    //    For proc.exec: check binary allowlist
    if (!mcp_validate_tool_params(sess->app_id, tool_id, json_params)) {
        vos3_ai_guard_log_violation(sess->app_id, tool_name, "invalid_params");
        return -EPERM;
    }

    // 4. Check AI Guard region access
    if (!vos3_ai_guard_check_app_access_by_id(sess->app_id)) {
        vos3_ai_guard_log_violation(sess->app_id, tool_name, "guard_blocked");
        return -EACCES;
    }

    // 5. Rate limiting: max 100 tool calls per second per app
    if (mcp_rate_limit_exceeded(sess)) {
        vos3_ai_guard_log_violation(sess->app_id, tool_name, "rate_limit");
        return -EBUSY;
    }

    return 0;
}
```

**Deep-packet inspection ensures** that even if an agent receives an attacker-controlled JSON request, the kernel validates every parameter value before dispatch. Example: if an agent is permitted `fs.read` but only from `/data/*`, a request to read `/etc/shadow` is rejected at the kernel boundary.

### 4.4 MCP Tool Registry

Built-in tools available through `/dev/mcp`:

| Tool ID | Name | Description | Guard Level |
|---------|------|-------------|-------------|
| 0 | `fs.read` | Read file contents | MONITOR |
| 1 | `fs.write` | Write file contents | PROTECTED |
| 2 | `fs.list` | List directory | MONITOR |
| 3 | `fs.stat` | File metadata | MONITOR |
| 4 | `net.http_get` | HTTP GET (via SLIRP) | PROTECTED |
| 5 | `proc.exec` | Execute binary | GUARD |
| 6 | `proc.info` | Process list / sysinfo | MONITOR |
| 7 | `mem.sysinfo` | Memory telemetry | MONITOR |

**Guard levels:**
- `MONITOR`: Log access, allow unconditionally
- `PROTECTED`: Log access, check app_id isolation, allow
- `GUARD`: Log access, require explicit allowlist entry, kill on violation

### 4.5 User-Space MCP Client Library

**New file:** `user/lib/mcp.c`

```c
// Simple API for agent programs:
int mcp_open(void);   // Opens /dev/mcp, returns fd
int mcp_call(int fd, const char* tool, const char* params_json,
             char* response_buf, size_t buf_size);
void mcp_close(int fd);

// Usage:
int mcp = mcp_open();
char resp[4096];
mcp_call(mcp, "fs.read", "{\"path\":\"/data/model.bin\"}", resp, sizeof(resp));
mcp_close(mcp);
```

---

## Phase 5: KV-Cache Tiering Strategy

**Goal:** Shared KV-Cache mechanism where multiple agents reuse prefilled context stored in kernel-managed SHM blocks.
**Effort:** ~600 lines kernel + user code | **Priority:** MEDIUM

### 5.1 Architecture: Kernel-Managed KV-Cache Pool

```
┌──────────────────────────────────────────────────────────┐
│  KV-Cache Pool (kernel-managed HugePage SHM)             │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Slot 0   │  │ Slot 1   │  │ Slot 2   │  ...         │
│  │ System   │  │ Tool     │  │ User     │              │
│  │ Prompt   │  │ Defs     │  │ Session  │              │
│  │ 2MB      │  │ 4MB      │  │ 8MB      │              │
│  │ ref=3    │  │ ref=2    │  │ ref=1    │              │
│  │ tier=HOT │  │ tier=HOT │  │ tier=WARM│              │
│  └──────────┘  └──────────┘  └──────────┘              │
│                                                          │
│  Agent A maps: [Slot 0, Slot 1, Slot 2]                 │
│  Agent B maps: [Slot 0, Slot 1, Slot 3]                 │
│  Agent C maps: [Slot 0, Slot 4]                         │
│  ─── Slot 0 (system prompt) shared by all 3 agents ──  │
└──────────────────────────────────────────────────────────┘
```

### 5.2 KV-Cache Slot Structure

```c
// kernel/include/vos/kvcache.h:
#define KVCACHE_MAX_SLOTS    64
#define KVCACHE_MAGIC        0x4B564348  // 'KVCH'

typedef enum {
    KVCACHE_TIER_HOT  = 0,  // In physical memory, mapped by active agents
    KVCACHE_TIER_WARM = 1,  // In physical memory, no active mappings
    KVCACHE_TIER_COLD = 2,  // Evicted to disk (vos3fs)
} kvcache_tier_t;

typedef struct {
    uint32_t         magic;
    uint32_t         slot_id;
    char             name[32];       // Human-readable: "system_prompt_v3"
    uint64_t         content_hash;   // SHA256 truncated for dedup
    uint64_t         phys_addr;      // Physical base (HugePage-aligned)
    size_t           size;           // Actual data size
    size_t           capacity;       // Allocated size (2MB-aligned)
    volatile int32_t ref_count;      // Atomic: number of agent mappings
    kvcache_tier_t   tier;
    uint64_t         last_access_tick;
    uint32_t         flags;          // READONLY, COPY_ON_WRITE
} kvcache_slot_t;
```

### 5.3 Tiering Policy

```
                 ref_count > 0
    COLD ─────────────────────► HOT
     ▲                            │
     │ evict (idle > 60s          │ ref_count drops to 0
     │  && tier != PINNED)        │
     │                            ▼
     └───────────────────────── WARM
           idle > 300s
```

**Eviction to disk:**
```c
// When memory pressure detected (free pages < 10%):
for (int i = 0; i < KVCACHE_MAX_SLOTS; i++) {
    kvcache_slot_t* s = &g_kvcache_slots[i];
    if (s->tier == KVCACHE_TIER_WARM &&
        (current_tick - s->last_access_tick) > 30000) { // 300s idle
        // Write to /var/kvcache/<name>.bin
        kvcache_evict_to_disk(s);
        vos3_pmm_free_huge(s->phys_addr);
        s->tier = KVCACHE_TIER_COLD;
    }
}
```

### 5.4 Syscall Interface

```c
// New syscalls:
#define SYS_KVCACHE_CREATE  275  // Create slot: name, size, flags → slot_id
#define SYS_KVCACHE_MAP     276  // Map slot into caller's address space → addr
#define SYS_KVCACHE_UNMAP   277  // Unmap slot
#define SYS_KVCACHE_LOOKUP  278  // Lookup by content_hash → slot_id (dedup)
#define SYS_KVCACHE_STAT    279  // Get slot metadata (tier, ref_count, size)
```

### 5.5 Deduplication

When an agent creates a KV-Cache slot, the kernel computes a content hash:

```c
// In sys_kvcache_create():
uint64_t hash = vos3_entropy_get_u64();  // Initial seed
// Hash first 4KB of content (enough for system prompt identification):
for (size_t i = 0; i < 4096 && i < size; i += 8) {
    hash ^= *(uint64_t*)(data + i);
    hash = (hash << 13) | (hash >> 51);  // Rotate
    hash *= 0x9E3779B97F4A7C15ULL;       // Golden ratio hash
}

// Check existing slots for match:
for (int i = 0; i < KVCACHE_MAX_SLOTS; i++) {
    if (g_kvcache_slots[i].content_hash == hash &&
        g_kvcache_slots[i].size == size) {
        // Dedup hit — return existing slot_id, increment ref_count
        __atomic_fetch_add(&g_kvcache_slots[i].ref_count, 1, __ATOMIC_ACQ_REL);
        return i;
    }
}
```

**Impact:** If 8 agents all use the same system prompt (128KB), only one physical copy exists. Saves 7 * 128KB = 896KB of memory per shared prompt.

**HugePage-backed slots for large context windows:**

```c
// In sys_kvcache_create(), when size >= 2MB:
if (size >= VOS3_LARGE_PAGE_SIZE) {
    // Allocate from HugePage pool for better TLB utilization
    size_t huge_count = (size + VOS3_LARGE_PAGE_SIZE - 1) / VOS3_LARGE_PAGE_SIZE;
    for (size_t i = 0; i < huge_count; i++) {
        uint64_t hp = vos3_pmm_alloc_huge();
        if (hp == 0) { /* fallback to 4KB pages */ }
        slot->phys_pages[i] = hp;
    }
    slot->flags |= KVCACHE_FLAG_HUGEPAGE;
}
```

System prompt dedup across 8 agents: if all agents share the same 2MB system prompt (HugePage-backed), they all map the same physical 2MB slot. Memory footprint: 1 HugePage (2MB) + 8 MMU page table entries instead of 8 * (1024 PTEs) = 8KB saved per agent × 8 agents = 64KB saved on page tables alone, plus 14MB saved on physical memory (7 copies eliminated).

---

## Phase 6: Benchmarking Suite v2

**Goal:** Validate performance gains from Phases 1-5.
**Effort:** ~400 lines user code | **Priority:** HIGH (runs in parallel with implementation)

### 6.1 `bench_ttft_sim` — Response Latency Benchmark

**New file:** `user/src/bench_ttft_sim.c`

Simulates the Time-To-First-Token path: agent receives request → reads KV-Cache → generates first token → writes to response pipe.

```c
// Test scenario:
// 1. Create SHM "context" (simulates KV-Cache, 1MB)
// 2. Fork 4 agents, each maps context (shared read)
// 3. Measure time from "request arrives" to "first token written"
//    - Request: parent writes 1 byte to agent's request pipe
//    - Agent: reads request, reads 64KB from SHM context, writes 1 byte to response pipe
//    - TTFT = time between parent write and parent read

// Metrics:
// - TTFT_min, TTFT_avg, TTFT_max (cycles and microseconds)
// - TTFT_p50, TTFT_p99 (from 1000 iterations)
// - Impact of priority boost: measure with NORMAL vs TTFT priority

// Verdicts:
// - ttft_under_1ms: avg TTFT < 1ms (reasonable for single-CPU QEMU)
// - ttft_priority_helps: TTFT with boost < TTFT without boost
// - ttft_no_starvation: background agents still make progress
```

### 6.2 `bench_hugepage_tlb` — HugePage Performance Benchmark

**New file:** `user/src/bench_hugepage_tlb.c`

Measures TLB miss impact by comparing random-access patterns on 4KB vs 2MB mapped memory.

```c
// Test scenario:
// Phase 1: Allocate 8MB via mmap (4KB pages)
//   - Random read pattern: access data[random_offset] for 100K iterations
//   - Measure cycles via RDTSC
//
// Phase 2: Allocate 8MB via SHM with SHM_HUGETLB (2MB pages)
//   - Same random read pattern, same 100K iterations
//   - Measure cycles via RDTSC
//
// Phase 3: Sequential access comparison (both page sizes)
//   - Full 8MB sequential read, measure cycles

// Metrics:
// - Random access: cycles/access for 4KB vs 2MB pages
// - Sequential access: MB/s for 4KB vs 2MB pages
// - TLB miss estimate: (random_cycles - sequential_cycles) / random_count
//   → approximates per-miss penalty

// Verdicts:
// - hugepage_random_faster: 2MB random access < 4KB random access
// - hugepage_sequential_parity: sequential throughput within 5%
// - hugepage_no_leak: memory fully reclaimed after test
```

### 6.3 `bench_hugepage_tlb` Details

**New file:** `user/src/bench_hugepage_tlb.c`

Measures TLB miss impact by comparing random-access patterns on 4KB vs 2MB mapped memory. This validates Phase 1 HugePage performance gains:

```c
// Phase 1: Allocate 8MB via mmap (4KB pages)
// Random read pattern: access data[random_offset] for 100K iterations
// Measure cycles via RDTSC

// Phase 2: Allocate 8MB via SHM with SHM_HUGETLB flag (2MB pages)
// Same random read pattern, same 100K iterations
// Measure cycles via RDTSC

// Phase 3: Sequential access comparison (both page sizes)
// Full 8MB sequential read, measure cycles

// Verdicts:
// - hugepage_random_faster: 2MB random access < 4KB random access
// - hugepage_sequential_parity: sequential throughput within 5%
// - hugepage_no_leak: memory fully reclaimed after test
```

**Expected result:** Random access on 2MB pages should be 10-50% faster due to fewer TLB misses. Sequential throughput should be nearly identical (no TLB difference).

### 6.4 `bench_npu_throughput` Details

**New file:** `user/src/bench_npu_throughput.c`

Simulates NPU memory access patterns with different cache policies:

```c
// Test Phase 1: Write-Combining (WC) policy for data buffers
// 1. Create SHM with VOS3_SHM_TYPE_NPU flag
// 2. Write 100MB of random data in 64-byte chunks (simulating DMA writes)
// 3. Measure throughput in MB/s

// Test Phase 2: Default Write-Back (WB) policy for comparison
// Same 100MB write, measure throughput

// Test Phase 3: Device register access (UC policy)
// Simulate 10,000 reads of volatile register
// Measure latency per read (should be high due to UC guarantees)

// Verdicts:
// - wc_faster_than_wb: WC throughput >= WB throughput (no stalls)
// - register_latency_high: UC register reads > 100 cycles (correct, no buffering)
```

**Expected result:** Write-Combining should match or exceed Write-Back for bulk buffer operations. Device register access (UC) should have high latency but guarantee immediate effect.

### 6.5 Additional Benchmark Enhancements

| Benchmark | Enhancement | Purpose |
|-----------|-------------|---------|
| `bench_ai_throughput` | Add `SHM_HUGETLB` mode | Compare 4KB vs 2MB SHM throughput |
| `bench_csw_1ms` | Add TTFT priority test | Measure preemption latency with priority boost |
| `bench_soak_5min` | Add KV-Cache stress | Create/map/unmap slots for 5 minutes |
| NEW: `bench_channel` | Ring buffer IPC throughput | Compare lockless channel vs spinlock SHM |
| NEW: `bench_affinity` | CPU pinning validation | Verify task runs only on specified CPU |
| NEW: `bench_hugepage_tlb` | HugePage TLB performance | Validate 512x TLB reduction (Phase 1) |
| NEW: `bench_npu_throughput` | NPU cache policy validation | Verify WC and UC policies (Phase 1.5) |

---

## Implementation Schedule

```
Phase 1   (HugePages)          ████████░░░░░░░░░░░░  Week 1-2    ✅ DONE (352 PASS)
Phase 1.2 (Stabilization)      ░░░░░░████░░░░░░░░░░  Week 2      ◄── CURRENT
Phase 1.5 (NPU-Direct)         ░░░░░░░░████░░░░░░░░  Week 2-3
Phase 2   (Lockless IPC)       ░░░░░░░░░░████████░░  Week 3-4
Phase 3   (Agentic Scheduling) ░░░░░░░░░░░░░░████░░  Week 4
Phase 4   (MCP Bridge)         ░░░░░░░░░░░░░░░░████  Week 4-5
Phase 5   (KV-Cache Tiering)   ░░░░░░░░░░░░░░░░░░██  Week 5
Phase 6   (Benchmarks v2)      ████████████████████░  Continuous
```

**Dependencies:**
```
Phase 1 (HugePages) ──► Phase 1.2 (Stabilization: fixes races exposed by Phase 1)
Phase 1.2 ──► Phase 1.5 (NPU needs stable VMM + reorganized syscalls)
Phase 1.5 (NPU-Direct) ──► Phase 5 (KV-Cache uses cache policy for tiering)
Phase 2 (Ring Buffers) ──► Phase 4 (MCP uses ring buffers internally)
Phase 3 (Scheduling) ──► independent (no deps, but benefits from FPU fix in 1.2)
Phase 6 (Benchmarks) ──► runs in parallel with all phases
```

---

## Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| HugePage fragmentation after uptime | HIGH | Boot-time reservation pool (Phase 1) |
| TLB invalidation race in vmm_map_user | HIGH | Move invlpg before spinlock release (Phase 1.2.1) |
| XMM/FPU state leaks between tasks | CRITICAL | FXSAVE/FXRSTOR in context switch (Phase 1.2.4) |
| Syscall number collisions (15+ known) | HIGH | Reorganize to 400-499 range (Phase 1.2.3) |
| 2MB COW copy latency spike | MEDIUM | COW handler already implemented; benchmark to validate |
| Ring buffer overflow under burst | MEDIUM | Backpressure via futex_wait; bounded buffer prevents OOM |
| TTFT priority inversion | HIGH | Priority inheritance on held spinlocks (future work) |
| MCP tool escape | CRITICAL | AI Guard deep-packet inspection on every tool call; allowlist per app_id |
| KV-Cache stale data | MEDIUM | Content hash + explicit invalidation syscall |
| PMM HugePage pool exhaustion | MEDIUM | Monitor pool free count via sysfs; alert if <5 pages remain |
| PAT MSR non-default values in QEMU | LOW | Use standard Intel defaults; QEMU emulates default PAT MSR |
| DEVICE pages bypassing COW | MEDIUM | Explicit check in fault handler: PCD+PWT → reject COW |
| Bit 9 conflict (COW vs AI_MONITORED) on DEVICE pages | LOW | DEVICE pages never COW'd; bit 9 safe for AI_MONITORED only |

---

## Success Metrics

| Metric | Current Baseline | v2.0 Target | Improvement |
|--------|-----------------|-------------|-------------|
| SHM 8-agent throughput | 984 MB/s | >2,000 MB/s | 2x |
| TLB entries for 1GB context | 262,144 | 512 | 512x |
| IPC latency (agent→agent) | ~45K cycles | <5K cycles | 9x |
| TTFT preemption latency | 10 ms (full tick) | <100 us | 100x |
| KV-Cache dedup savings | 0% (no sharing) | 80%+ (shared prompts) | — |
| Memory utilization (10GB context) | Impossible | 64 HugePages | Enabled |
| NPU memory throughput (WC policy) | N/A | Enabled (WC policy) | — |
| Device register latency (UC policy) | N/A | UC guarantees no stale reads | — |

---

## Files Modified/Created (Summary)

| File | Phase | Change |
|------|-------|--------|
| `kernel/src/mm/vmm.c` | 1 | Extend `vos3_vmm_map()` for 2MB pages; add `walk_to_pd()` |
| `kernel/include/vos/vmm.h` | 1, 1.5 | Add `VOS3_VMM_FLAG_LARGE`, cache policy flags, PAT constants |
| `kernel/src/mm/pmm.c` | 1, 1.5 | Boot-time HugePage pool + device range tiering |
| `kernel/src/ipc/shm.c` | 1, 2 | HugePage SHM + channel creation |
| `kernel/src/init/init.c` | 1 | Call `vos3_pmm_reserve_hugepages()` at boot |
| `kernel/src/mm/vmm.c` | 1.2 | Fix TLB invalidation ordering in `vos3_vmm_map_user()` |
| `kernel/src/sched/context.S` | 1.2 | Add FXSAVE/FXRSTOR for XMM/FPU state isolation |
| `kernel/include/vos/syscall.h` | 1.2 | Syscall renumbering to 400-499 range |
| `kernel/src/arch/x86_64/syscall.c` | 1.2 | Expand syscall table, update sandbox allowlist |
| `kernel/include/vos/ringbuf.h` | 2 | NEW: Lockless SPSC ring buffer (128-byte cache-line pad) |
| `kernel/src/ipc/channel.c` | 2 | NEW: Channel abstraction |
| `kernel/include/vos/task.h` | 3 | `cpu_affinity` field, TTFT priority |
| `kernel/include/vos/scheduler.h` | 3 | TTFT slice, new priority level |
| `kernel/src/sched/scheduler.c` | 3 | Affinity-aware `pick_next_task()`, sub-microsecond preemption |
| `kernel/src/drivers/mcp_dev.c` | 4 | NEW: `/dev/mcp` device driver with AI Guard deep-packet inspection |
| `kernel/include/vos/kvcache.h` | 5 | NEW: KV-Cache slot structures, HugePage-backed slots |
| `kernel/src/mm/kvcache.c` | 5 | NEW: KV-Cache pool manager with content-hash dedup |
| `user/src/bench_ttft_sim.c` | 6 | NEW: TTFT latency benchmark |
| `user/src/bench_hugepage_tlb.c` | 6 | NEW: HugePage TLB benchmark |
| `user/src/bench_npu_throughput.c` | 6 | NEW: NPU cache policy validation benchmark |
| `user/lib/mcp.c` | 4 | NEW: MCP client library |

---

*Roadmap drafted: 2026-03-20*
*Baseline: 383 PASS, 0 FAIL*
*Architecture: x86_64, single-CPU, 512MB RAM, QEMU + VirtIO*
