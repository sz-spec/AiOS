# TS-2026-PF_MEMSET_SCRUB — post-DONE `memset` page-fault in `VOS3_ASSERT_HARNESS` boot

| Field | Value |
|---|---|
| **Ticket** | TS-2026-PF_MEMSET_SCRUB |
| **Filed** | 2026-06-13 |
| **Severity** | Low (harness-only; occurs *after* the cert sentinel; the cert runner never observes it) |
| **Status** | ✅ PATCHED (Sprint 3, 2026-06-13) |
| **Found during** | A1 boot-smoke validation (PR #18) |
| **Affects** | Kernel images built with `VOS3_ASSERT_HARNESS=1`, booted past the `~~CERT~~ DONE` sentinel |
| **Does NOT affect** | The cert-harness flow (`tools/runner/qemu_assert_runner.py` stops reading at `DONE`); the default (non-harness) production build, which does not take this post-DONE path |
| **Relation to A1 / PR #18** | **None** — proven pre-existing by baseline A/B (see below) |

## Summary

When a kernel built with `VOS3_ASSERT_HARNESS=1` is booted under QEMU and
allowed to continue executing **after** the certification harness emits its
`~~CERT~~ DONE <n>` sentinel, the BSP takes a page fault inside the post-DONE
"boot secrets scrubbed" path and the kernel halts.

## Reproduction

```bash
cd kernel
make clean && make BENCH_MODE=1 VOS3_ASSERT_HARNESS=1 EXTRA_CFLAGS=-DBENCH_MODE all
qemu-system-x86_64 -kernel build/vos3.elf -m 1024M -cpu max -smp 1 \
    -serial stdio -no-reboot -no-shutdown \
    -nic user,model=virtio-net-pci -display none
# … 67 ~~CERT~~ asserts PASS … "~~CERT~~ DONE 67" …
# then:
```

## Observed fault (verbatim from serial)

```
~~CERT~~ DONE 67

================================================================================
                           *** KERNEL PANIC ***
================================================================================

Page Fault (#PF)
  Faulting Address: 0x0000000000105000
  Error Code: 0x0002 (kernel write not-present)
  RIP: 0xFFFFFFFF80107A52
  RSP: 0xFFFFFFFF84480F30

System halted. Please reboot.
```

## Diagnosis

- **`RIP 0xFFFFFFFF80107A52` resolves to `memset`** (`include/vos/string.h:87`,
  via `x86_64-elf-addr2line`).
- **`CR2 = 0x105000`** is in the low ~1 MiB region (32-bit boot stack / boot
  params / SMP trampoline scratch). The error code `0x0002` = *kernel write to a
  not-present page*.
- The fault occurs in the post-`DONE` **boot-secret scrub** (the
  `"Boot secrets scrubbed (32-bit stack + 64KB boot stack)"` step that runs
  after `vos3_run_cert_harness()` in `kernel/src/boot/kmain.c`). The scrub
  `memset`s a low-memory region whose virtual address is **no longer mapped**
  (or never mapped in the higher-half space) at that point in the harness build.

## Attribution — independent of A1 (baseline A/B)

To rule out the PR #18 A1 change (`vos3_vmm_map` W|X reject +
`vos3_vmm_map_user` strip-to-NX), `kernel/src/mm/vmm.c` was reverted to its
pre-A1 state (`1a6ad72`) and rebuilt with the **identical** flags. The baseline
produced the **byte-identical** fault — same `CR2=0x105000`, same error
`0x0002`, same `RIP=0xFFFFFFFF80107A52`. The fault is therefore **pre-existing
and unrelated to A1**. (Independently, `vos3_vmm_map_user` is never even called
on this boot — no user-init ELF exists on the `-kernel` ramfs boot, so PID 1
enters emergency idle.)

## Resolution (Sprint 3, 2026-06-13)

**Fixed in `kernel/src/boot/kmain.c`** — option 1 (map-bounded scrub). The two
boot-secret `memset`s (the 4 KiB 32-bit bootstrap stack + the 64 KiB boot stack)
are now scrubbed **one page at a time**, each page guarded by the read-only
`vos3_vmm_is_mapped()` query; any page that is not currently mapped (the low
32-bit stack at `~0x105000` after the KPTI PML4-strip, or a KASLR-stripped
boot-stack page) is **skipped** with an informational log
(`"Boot-secret scrub: skipped N unmapped page(s) … TS-2026-PF_MEMSET_SCRUB"`)
instead of faulting. No `vmm` mapping logic was modified — only the scrub loop,
which calls the existing read-only `is_mapped` API. Verified: clean cross-compile
(0 errors / 0 new warnings) and the host audit suite stays green.

## Recommendation (original — for reference)

Inspect the memory-bounds / mapping validity of the boot-secret-scrub target in
the harness teardown phase (`kmain.c`, post-`vos3_run_cert_harness`). Options:

1. **Map-before-scrub:** ensure the low boot-stack / boot-params VA is mapped in
   the active (higher-half) address space before the scrub `memset`, or scrub
   via its higher-half alias (`0xFFFF800000000000 + phys`) instead of the raw
   low VA.
2. **Bound the scrub** to regions proven mapped at teardown time (validate with
   `vos3_vmm_is_mapped()` before writing).
3. **Skip the scrub under `VOS3_ASSERT_HARNESS`** — the harness build is a
   synthetic cert vehicle that exits at `DONE`; the boot-secret scrub is a
   production-path concern and need not run after the sentinel.

## Notes

- This was surfaced as a *bonus* finding while validating PR #18; it is tracked
  here as standalone technical debt and was **not** bundled into PR #18.
- The cert harness itself is healthy: all 67 `~~CERT~~` asserts (incl. the M3
  SecureBoot certs 120–126) PASS and `DONE 67` is emitted before the fault.
