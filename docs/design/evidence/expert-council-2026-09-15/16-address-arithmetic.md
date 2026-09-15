# Address and counter arithmetic

Reviewer: `/root/math_build_review`. Source reviewed: `32187957757ab57d7820d0a63fa62c409003b126`. This is one specialized review task performed by an existing agent, not an additional independent agent or comprehensive audit.

- **P1 — preserve checked half-open ranges.** `user_mapping_range` validates nonzero lengths, page alignment, rounding overflow and the exclusive user bound before mmap/munmap modify mappings. mprotect separately validates permissions and preserves a valid empty-range no-op. These checks must remain ahead of any ownership publication.
- **P2 — full TLB invalidation still validates input.** `tlb_shootdown.c` checks `size-1` against remaining address capacity and rejects crossing the four-level canonical hole. It avoids exclusive-end wrap at the highest byte. This is a four-level-paging contract, not LA57 coverage.
- **P2 — reference counters require bounded transitions.** AS and file retain reject zero and UINT32_MAX. File release still lacks a symmetric underflow check. SHM mapping-count limits must remain enforced before allocating or publishing tracking entries.

Validation reviewed: fourteen actual-C TLB scenarios include overflow, canonical hole and last-byte boundaries; native memory API guards cover kernel addresses, overflowing lengths and W+X. These are selected boundary tests, not exhaustive arithmetic verification. Physical-address width variation, huge-page partial operations and every SHM size conversion were not exhaustively audited in this task.
