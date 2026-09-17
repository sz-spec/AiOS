/* Real syscall user-copy boundaries; faults must return to this same task. */
#include "stdio.h"
#include "unistd.h"
#include "syscall.h"
#include "stdint.h"
static int failed;
static void check(int ok, const char *name) {
    printf("[%s] usercopy: %s\n", ok ? "PASS" : "FAIL", name);
    if (!ok) failed++;
}
int main(void) {
    long base = syscall6(SYS_MMAP, 0, 8192, 3, 0x22, -1, 0);
    if (base < 0) { check(0, "mapping_setup"); return 1; }
    char *p = (char *)(uintptr_t)base;
    for (int i = 0; i < 8192; i++) p[i] = 'Q';
    check(syscall2(SYS_MUNMAP, base + 4096, 4096) == 0, "unmap_second_page");
    int fd = open("/tmp/usercopy-boundary", O_RDWR | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) { check(0, "file_setup"); return 1; }
    long r = syscall3(SYS_WRITE, fd, base + 4088, 16);
    check(r == -14 || r == 8, "write_cross_unmapped_boundary");
    check(syscall3(SYS_WRITE, fd, base + 4096, 1) == -14, "write_unmapped_returns_efault");
    check(syscall3(SYS_OPEN, base + 4096, 0, 0) == -14, "path_unmapped_returns_efault");
    p[4095] = '/';
    check(syscall3(SYS_OPEN, base + 4095, 0, 0) == -14, "path_cross_unmapped_returns_efault");
    check(syscall3(SYS_WRITE, fd, base, 16) == 16, "valid_write_after_fault");
    check(lseek(fd, 0, SEEK_SET) == 0, "rewind");
    check(syscall3(SYS_READ, fd, base + 4096, 1) == -14, "read_unmapped_returns_efault");
    check(lseek(fd, 0, SEEK_SET) == 0, "cross_read_rewind");
    r = syscall3(SYS_READ, fd, base + 4088, 16);
    check(r == -14 || r == 8, "read_cross_unmapped_boundary");
    check(lseek(fd, 0, SEEK_SET) == 0, "readonly_rewind");
    check(syscall3(10, base, 4096, 1) == 0, "readonly_setup");
    check(syscall3(SYS_READ, fd, base, 1) == -14, "read_readonly_returns_efault");
    check(syscall3(10, base, 4096, 0) == 0, "noaccess_setup");
    check(syscall3(SYS_WRITE, fd, base, 1) == -14, "write_noaccess_returns_efault");
    check(syscall3(10, base, 4096, 3) == 0, "restore_access");
    check(lseek(fd, 0, SEEK_SET) == 0 && syscall3(SYS_READ, fd, base, 1) == 1 && p[0] == 'Q', "valid_read_after_fault");
    p[0] = 'P';
    check(lseek(fd, 0, SEEK_SET) == 0, "cow_rewind");
    pid_t child = fork();
    if (child == 0) {
        long copied = syscall3(SYS_READ, fd, base, 1);
        _exit(copied == 1 && p[0] == 'Q' ? 0 : 21);
    }
    int status = -1;
    check(child > 0 && waitpid(child, &status, 0) == child && status == 0 && p[0] == 'P',
          "copyout_cow_preserves_parent");
    long lazy = syscall6(SYS_MMAP, 0, 4096, 3, 0x22, -1, 0);
    if (lazy < 0) check(0, "lazy_mapping");
    else {
        check(lseek(fd, 0, SEEK_SET) == 0 && syscall3(SYS_READ, fd, lazy, 1) == 1 &&
              *(char *)(uintptr_t)lazy == 'Q', "copyout_lazy_page");
        check(syscall2(SYS_MUNMAP, lazy, 4096) == 0, "lazy_cleanup");
    }
    check(close(fd) == 0 && unlink("/tmp/usercopy-boundary") == 0, "file_cleanup");
    check(syscall2(SYS_MUNMAP, base, 4096) == 0, "mapping_cleanup");
    return failed != 0;
}
