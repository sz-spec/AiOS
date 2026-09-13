# VOS3 Release Notes — Phase 4.9-Final

**Date:** 2026-04-04
**Binary:** `vos3_gold_final_v3.elf`
**SHA-256:** `7c93672b13c9b612bcb485f64ea4c63e624c11c73b4d9f97324ad49958f6b2a6`

---

## Summary

Phase 4.9-Final breaks the 2GB memory ceiling and expands the filesystem from 70KB to 32MB max file size. The kernel now supports 4GB RAM with PCI memory hole handling, 1024 HugePages (up from 128), and VOS3FS v2 with double-indirect block pointers.

---

## PMM: 4GB Memory Support

### Changes
- **Bitmap expanded**: `g_early_bitmap` from 8192 to 16384 entries (1,048,576 pages = 4 GiB)
- **Refcount array**: `VOS3_PMM_EARLY_REFCOUNT_SIZE` from 512K to 1M entries
- **HugePage pool**: `VOS3_HUGEPAGE_POOL_MAX` from 128 to 1024 (50% safety cap still active)
- **CMOS detection**: Cap raised from 2GB to 4GB

### PCI Memory Hole
Standard x86 PCs reserve 3GB-4GB for PCI MMIO devices. When QEMU is launched with `-m 4096M`:
- RAM below 3GB: mapped as usable (entry [2] in memory map)
- 3GB-4GB: reserved for PCI (entry [3])
- Above 4GB: QEMU remaps excess RAM here (entry [4])

The PMM memory map now has 5 entries (up from 3) to handle this split.

### Boot Output
```
PVH boot detected - CMOS reports 3072 MB RAM
PMM: reserved 761 HugePages (1522 MB)
```

### Files Modified
- `kernel/src/mm/pmm.c` — bitmap, refcount, hugepage pool expansion
- `kernel/src/boot/kmain.c` — CMOS cap, PCI hole handling, hugepage reservation

---

## VOS3FS v2: 32MB File Support

### Disk Layout Changes (v1 -> v2)
| Component | v1 | v2 |
|---|---|---|
| Version | 1 | 2 |
| Data bitmap | 2 sectors (8,192 blocks) | 32 sectors (131,072 blocks) |
| Inode table start | Sector 4 | Sector 34 |
| Data start | Sector 68 | Sector 98 |
| Direct blocks/inode | 10 | 6 |
| Double-indirect blocks | 0 | 4 |
| Max file size | 70,656 bytes | 33,623,040 bytes (~32 MB) |

### Inode Layout (64 bytes, unchanged total size)
```
[mode:4][size:4][nlink:4][ctime:4][mtime:4] = 20 bytes header
Union (44 bytes):
  Regular file/directory:
    [direct[6]:24][indirect:4][dindirect[4]:16] = 44 bytes
  Symlink:
    [target:44] = 44 bytes inline
```

### Key Implementation
- `vos3fs_bmap()` — 3-level block lookup: direct -> indirect -> double-indirect
- `vos3fs_bmap_alloc()` — On-demand allocation at all three levels
- `vos3fs_free_all_blocks()` — Unified block freeing helper (replaces 3 inline copies)
- `vos3fs_write_data_bitmap()` — Loop over 32 sectors instead of 2
- Mount: auto-detects v1 and reformats to v2

### Boot Output
```
[vos3fs] v1 disk detected — reformatting to v2
[vos3fs] mounted v2 filesystem (32MB max, double-indirect)
```

### Files Modified
- `kernel/include/vos/vos3fs.h` — New constants, inode struct with dindirect[4]
- `kernel/src/fs/vos3fs.c` — bmap, bmap_alloc, free_all_blocks, mount, mkfs

---

## VBus Driver Fix

### Socket Drain on Connect
The VBusDriver now drains stale frames (kernel heartbeat PINGs) from the socket buffer before performing the VBUS2 handshake. This fixes connection failures when reconnecting to a long-running QEMU instance.

### File Modified
- `backend/services/vbus_driver.py` — Non-blocking drain in `connect()`

---

## QEMU Launch Config

```bash
qemu-system-x86_64 -kernel build/vos3.elf -m 4096M -smp 2 \
  -cpu max \
  -chardev file,id=con,path=/tmp/vos3_console.log -serial chardev:con \
  -chardev socket,id=vbus,path=/tmp/vos3_bridge.sock,server=on,wait=off \
  -device virtio-serial-pci -device virtconsole,chardev=vbus \
  -drive file=disk.img,format=raw,if=none,id=disk0,cache=directsync \
  -device virtio-blk-pci,drive=disk0 \
  -display none -daemonize -pidfile /tmp/vos3_qemu.pid
```

**Changed from v2:** `-m 4096M` (was 1024M)

---

## Test Results

| Suite | Pass | Fail | Notes |
|---|---|---|---|
| stress_warp_4_9b.py | 11 | 0 | Warp drive + integrity + sentinel |
| stress_audit_4_2_5.py | 5 | 3 | 3 pre-existing socket timeouts |
| test_db_integrity.py | 11 | 0 | DB production guards + schema SSoT |
| stress_centurion_swap.py | — | 2 | Pre-existing 100-iter timeout |

**Total new regressions: 0**
