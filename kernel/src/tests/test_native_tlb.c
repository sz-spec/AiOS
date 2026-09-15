/* Diagnostic shared kernel mappings only: no shared user-VM activation. */
#include "../../include/vos/vmm.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/percpu.h"
#include "../../include/vos/console.h"
#include "../../include/arch/x86_64/smp.h"

/* Dedicated diagnostic VA; reject any preexisting leaf before mapping. */
#define PROBE_BASE UINT64_C(0xffffd10000000000)
static unsigned phase;
static struct {
    uint32_t apic;
    uint64_t before0, before1, after0, after1;
} observations[256];

void vos3_native_tlb_before_flush(void)
{
    if (!__atomic_load_n(&phase, __ATOMIC_ACQUIRE)) return;
    unsigned cpu = get_cpu_id();
    if (cpu >= 256) vos3_panic("NATIVE_TLB FAIL invalid_cpu");
    observations[cpu].apic = vos3_lapic_id();
    observations[cpu].before0 = *(volatile uint64_t *)PROBE_BASE;
    observations[cpu].before1 = *(volatile uint64_t *)(PROBE_BASE + 4096);
}

void vos3_native_tlb_observe(void)
{
    if (!__atomic_load_n(&phase, __ATOMIC_ACQUIRE)) return;
    unsigned cpu = get_cpu_id();
    observations[cpu].after0 = *(volatile uint64_t *)PROBE_BASE;
    observations[cpu].after1 = *(volatile uint64_t *)(PROBE_BASE + 4096);
}

#ifdef NATIVE_TLB_SKIP_FLUSH
int vos3_native_tlb_skip_flush(void)
{
    /* On SMP corrupt exactly one remote response, leaving the BSP and other
     * APs as controls. The one-CPU image uses CPU0 as its negative control. */
    return phase == 2 && get_cpu_id() == (vos3_smp_online_count() > 1 ? 1U : 0U);
}
#endif

static void verify(unsigned round)
{
    int failed_cpu = -1;
    for (unsigned cpu = 0; cpu < vos3_smp_cpu_count(); cpu++) {
        const vos3_smp_cpu_info_t *info = vos3_smp_get_cpu_info(cpu);
        if (info == NULL || !info->started) continue;
        VOS3_INFO("NATIVE_TLB phase=%s cpu=%u apic=%u before0=%llu before1=%llu after0=%llu after1=%llu",
                  round == 1 ? "warm" : "remap", cpu, observations[cpu].apic,
                  (unsigned long long)observations[cpu].before0,
                  (unsigned long long)observations[cpu].before1,
                  (unsigned long long)observations[cpu].after0,
                  (unsigned long long)observations[cpu].after1);
        if (observations[cpu].apic != info->apic_id ||
            observations[cpu].before0 != 17 || observations[cpu].before1 != 34 ||
            observations[cpu].after0 != 17 ||
            observations[cpu].after1 != (round == 1 ? 34U : 51U))
            failed_cpu = (int)cpu;
    }
    /* Preserve every CPU's observation before stopping a negative image. */
    if (failed_cpu >= 0)
        vos3_panic("NATIVE_TLB FAIL translation cpu=%u round=%u", (unsigned)failed_cpu, round);
}

void vos3_native_tlb_test(void)
{
    uintptr_t pages[3];
    for (unsigned i = 0; i < 3; i++) {
        pages[i] = vos3_pmm_alloc_pages(1, VOS3_PMM_FLAG_ZERO);
        if (!pages[i]) vos3_panic("NATIVE_TLB FAIL allocation");
        *(uint64_t *)vos3_phys_to_virt(pages[i]) = 17U * (i + 1U);
    }
    for (unsigned i = 0; i < 2; i++) {
        vos3_pte_t old = 0;
        if (vos3_vmm_get_pte(PROBE_BASE + 4096U * i, &old) == 0 && old != 0)
            vos3_panic("NATIVE_TLB FAIL occupied_va");
        if (vos3_vmm_map(PROBE_BASE + 4096U * i, pages[i],
                         VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_GLOBAL) != 0)
            vos3_panic("NATIVE_TLB FAIL map");
    }
    __atomic_store_n(&phase, 1, __ATOMIC_RELEASE);
    if (vos3_vmm_flush_range_checked(PROBE_BASE, 8192) != 0)
        vos3_panic("NATIVE_TLB FAIL warm_ack");
    verify(1);

    /* Atomic replacement deliberately leaves the old translation cached.
     * Keep every backing page allocated until the later acknowledgement. */
    vos3_pte_t old;
    if (vos3_vmm_get_pte(PROBE_BASE + 4096, &old) != 0)
        vos3_panic("NATIVE_TLB FAIL pte");
    vos3_pte_t replacement = (old & ~VOS3_PTE_ADDR_MASK) | pages[2];
    if (vos3_vmm_cas_pte(PROBE_BASE + 4096, &old, replacement) != 0)
        vos3_panic("NATIVE_TLB FAIL remap");
    __atomic_store_n(&phase, 2, __ATOMIC_RELEASE);
    if (vos3_vmm_flush_range_checked(PROBE_BASE + 4095, 2) != 0)
        vos3_panic("NATIVE_TLB FAIL remap_ack");
    verify(2);
    __atomic_store_n(&phase, 0, __ATOMIC_RELEASE);
    if (vos3_vmm_unmap(PROBE_BASE) != 0 || vos3_vmm_unmap(PROBE_BASE + 4096) != 0 ||
        vos3_vmm_flush_range_checked(PROBE_BASE, 8192) != 0)
        vos3_panic("NATIVE_TLB FAIL cleanup");
    for (unsigned i = 0; i < 3; i++) vos3_pmm_free(pages[i]);
    VOS3_INFO("NATIVE_TLB complete=1 cpus=%u rounds=2", vos3_smp_online_count());
}
