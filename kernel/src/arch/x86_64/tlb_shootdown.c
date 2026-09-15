#include "../../../include/vos/tlb_shootdown.h"
#include "../../../include/vos/atomic.h"
#include "../../../include/vos/percpu.h"
#include "../../../include/vos/console.h"
#include "../../../include/vos/vmm.h"
#include "../../../include/arch/x86_64/smp.h"

uint32_t vos3_tlb_arch_cpu(void) { return get_cpu_id(); }
uint32_t vos3_tlb_arch_count(void) { return vos3_smp_cpu_count(); }
int vos3_tlb_arch_target(uint32_t cpu, uint32_t *apic)
{
    const vos3_smp_cpu_info_t *info = vos3_smp_get_cpu_info(cpu);
    if (info == NULL || !info->started) return 0;
    *apic = info->apic_id;
    return 1;
}
uint64_t vos3_tlb_arch_irq_save(void) { return vos3_irq_save(); }
void vos3_tlb_arch_irq_restore(uint64_t flags) { vos3_irq_restore(flags); }
int vos3_tlb_arch_send(uint32_t apic)
{
    return vos3_lapic_send_ipi_checked(apic, VOS3_IPI_TLB_FLUSH,
                                      VOS3_TLB_WAIT_BUDGET);
}
void vos3_tlb_arch_pause(void) { __asm__ volatile("pause" ::: "memory"); }

void vos3_tlb_arch_flush_all(void)
{
#ifdef NATIVE_TLB_TEST
    extern void vos3_native_tlb_before_flush(void);
    vos3_native_tlb_before_flush();
#endif
    unsigned eax = 1, ebx, ecx, edx;
    __asm__ volatile("cpuid" : "+a"(eax), "=b"(ebx), "=c"(ecx), "=d"(edx));
    if (!(edx & (1U << 13)))
        vos3_panic("TLB all-context invalidation requires PGE support");
    uint64_t original;
    __asm__ volatile("mov %%cr4, %0" : "=r"(original));
    uint64_t toggled = original ^ (1ULL << 7);
    /* Intel SDM Vol3A section 5.10.4.1: changing PGE invalidates every
     * PCID, global translation and paging-structure cache. Toggle even
     * when PGE was initially zero, then restore all original control bits.
     * This conservative flush deliberately prioritizes correctness over
     * range-flush performance. Callers/IPI entry exclude local interrupts. */
#ifdef NATIVE_TLB_SKIP_FLUSH
    extern int vos3_native_tlb_skip_flush(void);
    if (!vos3_native_tlb_skip_flush())
#endif
    __asm__ volatile("mov %0, %%cr4; mov %1, %%cr4"
                     :: "r"(toggled), "r"(original) : "memory");
#ifdef NATIVE_TLB_TEST
    extern void vos3_native_tlb_observe(void);
    vos3_native_tlb_observe();
#endif
}
