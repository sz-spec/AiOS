/** Multiboot2 architecture entry; parsing itself uses no privileged operations. */
#include "../../include/vos/boot_info.h"
extern const vos3_boot_info_t *vos3_multiboot2_parse(uint64_t address);
extern void kernel_main(const vos3_boot_info_t *info);
void multiboot2_process(uint64_t address)
{
    kernel_main(vos3_multiboot2_parse(address));
    for (;;) __asm__ volatile ("hlt");
}
