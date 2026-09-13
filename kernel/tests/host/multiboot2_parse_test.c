/* Execute the production boot parser on bounded, synthetic boot records. */
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include "../../src/boot/multiboot2_parse.c"
char _kernel_phys_start[1], _kernel_phys_end[1];
char _kernel_virt_start[1], _kernel_virt_end[1];
static uint8_t data[128] __attribute__((aligned(8)));
static void word(unsigned offset, uint32_t value) { memcpy(data+offset,&value,4); }
static const vos3_boot_info_t *parse(void) { return vos3_multiboot2_parse((uintptr_t)data); }
int main(void) {
    word(0, 128); word(8, 6); word(12, 40); word(16, 24);
    struct multiboot2_mmap_entry entry = {0x100000,0x200000,1,0};
    memcpy(data+24,&entry,sizeof(entry));
    assert(parse()->mem_map_entries == 1);
    assert(parse()->total_memory == 0x200000);
    word(16, 0); assert(parse()->mem_map_entries == 0);
    word(16, 23); assert(parse()->mem_map_entries == 0);
    word(16, 24); word(12, 39); assert(parse()->mem_map_entries == 0);
    word(12, 0); assert(parse()->mem_map_entries == 0);
    word(12, UINT32_MAX); assert(parse()->mem_map_entries == 0);
    memset(data,0,sizeof(data)); word(0,128);
    word(8,15); word(12,44); memset(data+16,0x22,36);
    word(56,14); word(60,28); memset(data+64,0x11,20);
    assert(parse()->flags & VOS3_BOOT_FLAG_ACPI);
    assert(g_rsdp[0] == 0x22 && g_rsdp[35] == 0x22);
    /* Bootloader record mutation must not alter the retained RSDP. */
    data[16]=0; assert(g_rsdp[0] == 0x22);
    memset(data,0,sizeof(data)); word(0,128);
    assert(parse()->rsdp_addr == 0);
    word(8,15); word(12,43); assert(parse()->rsdp_addr == 0);
    word(0,7); assert(parse() == NULL);
    puts("Multiboot2 parser checks passed");
}
