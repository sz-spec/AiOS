/** User MMIO denial regression. No NPU hardware/performance qualification. */
#include "stdio.h"
#include "syscall.h"
#define SYS_SHM_CREATE_DEVICE 415
#define SYS_SHM_DESTROY 411
#define SYS_APP_CTX_CREATE 460
#define SYS_APP_CTX_DESTROY 461
#define SYS_APP_CTX_SWITCH 462
#define VOS3_SHM_FLAG_DEVICE (1U << 5)
int main(void)
{
    if (syscall1(SYS_APP_CTX_CREATE, 7) < 0) return 1;
    if (syscall1(SYS_APP_CTX_SWITCH, 7) < 0) {
        syscall1(SYS_APP_CTX_DESTROY, 7);
        return 1;
    }
    int failed = 0;
    long id = syscall4(SYS_SHM_CREATE_DEVICE, (long)"unowned_mmio",
                       (long)0xFD000000ULL, (long)4096,
                       (long)VOS3_SHM_FLAG_DEVICE);
    /* This is an unowned address, not an asserted device or RAM fixture. */
    if (id >= 0 && id != (long)0xFFFFFFFFU) {
        syscall1(SYS_SHM_DESTROY, id);
        printf("[FAIL] unowned user MMIO was authorized\n");
        failed = 1;
    } else printf("[PASS] user MMIO denied even with AI context\n");
    id = syscall4(SYS_SHM_CREATE_DEVICE, (long)"managed_ram",
                  (long)0x1000, (long)4096, (long)VOS3_SHM_FLAG_DEVICE);
    if (id >= 0 && id != (long)0xFFFFFFFFU) {
        syscall1(SYS_SHM_DESTROY, id);
        printf("[FAIL] managed RAM was authorized as device\n");
        failed = 1;
    } else printf("[PASS] managed RAM device request denied\n");
    syscall1(SYS_APP_CTX_SWITCH, 0);
    syscall1(SYS_APP_CTX_DESTROY, 7);
    printf("[UNAVAILABLE] NPU hardware/WC performance: no authorized BAR registry; denial checks only\n");
    return failed;
}
