# Memory ordering and concurrency

Reviewer: `/root/math_build_review`. Source reviewed: `32187957757ab57d7820d0a63fa62c409003b126`. This is one specialized review task performed by an existing agent, not an additional independent agent or comprehensive audit.

- **P1 — lifetime does not serialize mapping mutation.** `vos3_vmm_find_vma` returns a raw descriptor; demand paging consumes it while mmap/munmap can modify the array. Reference-counted address spaces prevent descriptor destruction but do not independently protect individual VMA/backing observations during concurrent mutation.
- **P1 — positional backing I/O is not atomic.** The demand handler performs seek/read/restore on an owned file object. Ownership fixes use-after-close through another task’s FD table; it does not prevent two users from interfering with the shared file position.
- **P2 — publication ordering is explicit but bounded.** AS CAS operations use acquire/release, retirement uses a short IRQ-excluded queue lock, and root switching excludes local interrupts. These mechanisms support the reviewed sequential ownership transition. They are not a language-level proof of all scheduler and page-table accesses.

Validation reviewed: production retain/switch/queue code and prior TLB protocol tests against actual C. The native workloads use sequential handshakes; experimental shared-VM clones inherit owner affinity. No claim of simultaneous shared-VM mutation, race freedom, weak-memory portability or verified compiler output is warranted. Final BIOS4 lifecycle liveness remains unresolved.
