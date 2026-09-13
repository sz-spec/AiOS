/**
 * @file boot_fs.c
 * @brief VOS3 Boot — Filesystem Initialization
 *
 * Extracted from kmain.c (Phase 8.5-C Sovereign Consolidation).
 * VirtIO block device, vos3fs mount, Day 8/9 persistence tests,
 * VFS I/O integrity checks, serial bridge, and embedded binaries.
 */

#include "../../include/vos/boot_fs.h"
#include "../../include/vos/console.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/vos3fs.h"
#include "../../include/vos/virtio_blk.h"
#include "../../include/vos/virtio_bridge.h"
#include "../../include/vos/string.h"

/* External init functions */
extern int vos3_virtio_blk_init(void);
extern int vos3fs_init(void);
extern int vos3_init_embedded_binaries(void);

/* ============================================================================
 * BOOT FS INIT
 * ============================================================================ */

int boot_fs_init(void)
{
    int result;

    /* ===== Day 7: VirtIO Block Device ===== */
    VOS3_INFO("Initializing VirtIO Block Device");
    result = vos3_virtio_blk_init();
    if (result != 0) {
        VOS3_WARN("VirtIO block device not available (error %d)", result);
    }

    /* ===== Day 8: vos3fs Persistent Filesystem ===== */
    VOS3_INFO("Initializing vos3fs filesystem");
    result = vos3fs_init();
    if (result != 0) {
        VOS3_WARN("vos3fs registration failed (error %d)", result);
    } else {
        /* Create mount point and mount vos3fs on /disk */
        (void)vos3_mkdir("/disk", VOS3_S_IRWXU | VOS3_S_IRGRP | VOS3_S_IXGRP |
                         VOS3_S_IROTH | VOS3_S_IXOTH);
        result = vos3_mount("virtio0", "/disk", "vos3fs", 0, NULL);
        if (result != 0) {
            VOS3_WARN("vos3fs mount on /disk failed (error %d)", result);
        } else {
            VOS3_INFO("vos3fs mounted on /disk");
#ifndef VOS3_PRODUCTION_BUILD
            /* Destructive storage diagnostics belong only to audit builds. */
            /* Phase 36.5: Storage Stress & Integrity Test */
            result = vos3fs_stress_test();
            if (result != 0) {
                VOS3_WARN("vos3fs stress test failed");
            }

            /* ===== Day 9: Lindy Test - VFS Persistence API ===== */
            vos3_console_puts("\n[DAY9] ========================================\n");
            vos3_console_puts("[DAY9] User-Space Persistence API Test\n");
            vos3_console_puts("[DAY9] ========================================\n");

            /* Step 1: Try to read /disk/test.txt (persistence check from prior boot) */
            {
                int rfd = vos3_open("/disk/test.txt", VOS3_O_RDONLY, 0);
                if (rfd >= 0) {
                    char rbuf[64];
                    memset(rbuf, 0, sizeof(rbuf));
                    int64_t nr = vos3_read(rfd, rbuf, sizeof(rbuf) - 1);
                    vos3_close(rfd);
                    if (nr > 0 && memcmp(rbuf, "VOS3-POWER", 10) == 0) {
                        vos3_console_puts("[DAY9] Persistence: /disk/test.txt survived reboot!\n");
                        vos3_console_puts("[DAY9]   Content: \"");
                        vos3_console_puts(rbuf);
                        vos3_console_puts("\"\n");
                        vos3_console_puts("[DAY9] Lindy Test: PASS (data persisted across cold reboot)\n");
                    } else {
                        vos3_console_puts("[DAY9] Persistence: /disk/test.txt exists but content mismatch\n");
                    }
                } else {
                    vos3_console_puts("[DAY9] First boot: /disk/test.txt not found (expected)\n");
                }
            }

            /* Step 2: Write "VOS3-POWER" to /disk/test.txt via VFS open/write/close */
            {
                int wfd = vos3_open("/disk/test.txt",
                                    VOS3_O_WRONLY | VOS3_O_CREAT | VOS3_O_TRUNC, 0644);
                if (wfd >= 0) {
                    const char *msg = "VOS3-POWER";
                    int64_t nw = vos3_write(wfd, msg, 10);
                    vos3_close(wfd);
                    if (nw == 10) {
                        vos3_console_puts("[DAY9] Write: 'VOS3-POWER' -> /disk/test.txt (10 bytes)\n");
                    } else {
                        vos3_console_puts("[DAY9] Write: FAILED\n");
                    }
                } else {
                    vos3_console_puts("[DAY9] Open for write: FAILED\n");
                }
            }

            /* Step 3: Read it back immediately to verify write path */
            {
                int vfd = vos3_open("/disk/test.txt", VOS3_O_RDONLY, 0);
                if (vfd >= 0) {
                    char vbuf[64];
                    memset(vbuf, 0, sizeof(vbuf));
                    int64_t vn = vos3_read(vfd, vbuf, sizeof(vbuf) - 1);
                    vos3_close(vfd);
                    if (vn == 10 && memcmp(vbuf, "VOS3-POWER", 10) == 0) {
                        vos3_console_puts("[DAY9] Verify: Read-back matches. VFS path OPERATIONAL.\n");
                    } else {
                        vos3_console_puts("[DAY9] Verify: Read-back MISMATCH\n");
                    }
                } else {
                    vos3_console_puts("[DAY9] Verify: Cannot re-open /disk/test.txt\n");
                }
            }

            /* Step 4: Sync to ensure disk persistence */
            vos3_virtio_blk_flush();
            vos3_console_puts("[DAY9] Sync: Flushed to disk\n");

            vos3_console_puts("[DAY9] ========================================\n");
            vos3_console_puts("[DAY9] Reboot to verify persistence.\n");
            vos3_console_puts("[DAY9] ========================================\n\n");

            /* ===== Phase 36.5: VFS I/O Integrity Test ===== */
            {
                vos3_console_puts("[TEST] ========================================\n");
                vos3_console_puts("[TEST] Phase 36.5b: VFS I/O Integrity (4KB)\n");
                vos3_console_puts("[TEST] ========================================\n");

                /* Generate 4KB pseudo-random pattern using LCG */
                uint8_t write_buf[4096];
                uint32_t rng = 0xDEADBEEF;
                for (int i = 0; i < 4096; i++) {
                    rng = rng * 1103515245U + 12345U;
                    write_buf[i] = (uint8_t)((rng >> 16) & 0xFF);
                }

                /* Step 1: Create and write /disk/integrity.bin */
                int wfd = vos3_open("/disk/integrity.bin",
                                    VOS3_O_WRONLY | VOS3_O_CREAT | VOS3_O_TRUNC, 0644);
                if (wfd < 0) {
                    vos3_console_puts("[TEST] VFS Write (4KB): FAILED (open error)\n");
                } else {
                    int64_t nw = vos3_write(wfd, write_buf, 4096);
                    vos3_close(wfd);
                    if (nw == 4096) {
                        vos3_console_puts("[TEST] VFS Write (4KB): SUCCESS\n");
                    } else {
                        vos3_console_puts("[TEST] VFS Write (4KB): FAILED (short write)\n");
                    }
                }

                /* Step 2: Reopen for reading and verify byte-by-byte */
                int rfd = vos3_open("/disk/integrity.bin", VOS3_O_RDONLY, 0);
                if (rfd < 0) {
                    VOS3_PANIC("Integrity test: cannot reopen /disk/integrity.bin");
                }

                uint8_t read_buf[4096];
                memset(read_buf, 0, 4096);
                int64_t nr = vos3_read(rfd, read_buf, 4096);
                vos3_close(rfd);

                if (nr != 4096) {
                    VOS3_PANIC("Integrity test: short read (%d bytes)", (int)nr);
                }

                /* Byte-by-byte comparison */
                for (int i = 0; i < 4096; i++) {
                    if (read_buf[i] != write_buf[i]) {
                        VOS3_PANIC("Integrity test: mismatch at byte %d "
                                   "(wrote 0x%02X, read 0x%02X)",
                                   i, write_buf[i], read_buf[i]);
                    }
                }
                vos3_console_puts("[TEST] Integrity Check: MATCH\n");

                /* Step 3: Verify free block accounting via statfs */
                vos3_statfs_t stbuf;
                if (vos3_statfs("/disk", &stbuf) == VOS3_FS_OK) {
                    vos3_console_puts("[VFS] Free Blocks Updated: ");
                    /* Print f_bfree */
                    char nbuf[16];
                    int ni = 0;
                    uint64_t fv = stbuf.f_bfree;
                    if (fv == 0) { nbuf[ni++] = '0'; }
                    else {
                        char tmp[16]; int ti = 0;
                        while (fv) { tmp[ti++] = '0' + (char)(fv % 10); fv /= 10; }
                        while (ti--) nbuf[ni++] = tmp[ti];
                    }
                    nbuf[ni] = '\0';
                    vos3_console_puts(nbuf);
                    vos3_console_puts(" left\n");
                }

                vos3_console_puts("[TEST] ========================================\n");
                vos3_console_puts("[TEST] VFS I/O Integrity: ALL PASS\n");
                vos3_console_puts("[TEST] ========================================\n\n");
            }

#endif

            /* ===== Day 10: Serial Bridge (COM2) ===== */
            result = vos3_bridge_init();
            if (result != 0) {
                VOS3_WARN("Serial bridge initialization failed (error %d)", result);
            }
        }
    }

    /* ===== Phase 16b: Load embedded user binaries ===== */
    VOS3_INFO("Loading embedded user binaries");
    result = vos3_init_embedded_binaries();
    if (result != 0) {
        VOS3_PANIC("Failed to load embedded binaries (error %d)", result);
    }

    return 0;
}
