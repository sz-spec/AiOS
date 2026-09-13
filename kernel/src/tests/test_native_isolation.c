/* Diagnostic evidence only. Does not change page fault handling or permissions. */
#include "../../include/vos/native_isolation_test.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/console.h"
#include "../../include/vos/scheduler.h"

static volatile unsigned char isolation_canary[4096] __attribute__((aligned(4096)));
static char target_env[] = "VOS_NATIVE_KERNEL_TARGET=0x0000000000000000";

/* Compute effective U/S across every traversed level, including large pages. */
static void permissions(vos3_pte_t* table, uintptr_t address, int* present, int* user)
{
    *present = 0;
    *user = 0;
    if (table == NULL) return;
    int effective_user = 1;
    for (int shift = 39; shift >= 12; shift -= 9) {
        uint64_t entry = table[(address >> shift) & 511U];
        if (!(entry & VOS3_PTE_PRESENT)) return;
        effective_user &= !!(entry & VOS3_PTE_USER);
        if (shift == 12 || ((shift == 30 || shift == 21) && (entry & VOS3_PTE_LARGE))) {
            *present = 1;
            *user = effective_user;
            return;
        }
        table = vos3_phys_to_virt(vos3_pte_get_addr(entry));
    }
}

const char* vos3_native_isolation_target(void)
{
    uintptr_t address = (uintptr_t)isolation_canary;
    vos3_address_space_t* as = vos3_vmm_get_current_space();
    int present, user;
    permissions(as->pml4, address, &present, &user);
    if (!present || user || address < 0xFFFF800000000000ULL) return NULL;
    for (unsigned i = 0; i < sizeof(isolation_canary); ++i) isolation_canary[i] = 0xA5;
    static const char hex[] = "0123456789abcdef";
    for (unsigned i = 0; i < 16; ++i)
        target_env[sizeof(target_env) - 2 - i] = hex[(address >> (4 * i)) & 15U];
    VOS3_INFO("NATIVE_ISOLATION kernel_target addr=0x%llx present=%d user=%d",
              (unsigned long long)address, present, user);
    return target_env;
}

void vos3_native_isolation_mapping(vos3_task_t* task, uintptr_t address, uintptr_t phys)
{
    if (address != 0x7000000000ULL || !task || !task->address_space) return;
    vos3_address_space_t* as = task->address_space;
    VOS3_INFO("NATIVE_ISOLATION victim_mapping pid=%u addr=0x%llx root=0x%llx user_root=0x%llx phys=0x%llx",
              task->pid, (unsigned long long)address, (unsigned long long)as->pml4_phys,
              (unsigned long long)as->user_pml4_phys, (unsigned long long)phys);
}

void vos3_native_isolation_fault(vos3_task_t* task, uintptr_t address)
{
    if (!task || !task->address_space) return;
    vos3_address_space_t* as = task->address_space;
    int present, user;
    permissions(as->user_pml4, address, &present, &user);
    VOS3_INFO("NATIVE_ISOLATION fault pid=%u addr=0x%llx root=0x%llx user_root=0x%llx present=%d user=%d",
              task->pid, (unsigned long long)address, (unsigned long long)as->pml4_phys,
              (unsigned long long)as->user_pml4_phys, present, user);
    for (unsigned i = 0; i < sizeof(isolation_canary); ++i) {
        if (isolation_canary[i] != 0xA5) {
            VOS3_ERROR("NATIVE_ISOLATION FAIL kernel_canary");
            break;
        }
    }
}
