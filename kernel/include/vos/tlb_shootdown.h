#ifndef VOS3_TLB_SHOOTDOWN_H
#define VOS3_TLB_SHOOTDOWN_H
#include <stdint.h>
#include <stddef.h>

#define VOS3_TLB_MAX_CPUS 256U
#define VOS3_TLB_WAIT_BUDGET 1000000U
#define VOS3_TLB_INVALID (-22)
#define VOS3_TLB_BUSY (-16)
#define VOS3_TLB_TIMEOUT (-110)
#define VOS3_TLB_POISONED (-5)

int vos3_tlb_shootdown_checked(uintptr_t base, size_t size);
void vos3_tlb_shootdown_handle(void);

/* The architecture adapter is also the boundary for deterministic host tests.
 * It must not allocate or acquire locks in interrupt/flush callbacks.
 * CPU topology is fixed after boot; target() returns 1 only for released CPUs. */
uint32_t vos3_tlb_arch_cpu(void);
uint32_t vos3_tlb_arch_count(void);
int vos3_tlb_arch_target(uint32_t cpu, uint32_t *apic);
uint64_t vos3_tlb_arch_irq_save(void);
void vos3_tlb_arch_irq_restore(uint64_t flags);
void vos3_tlb_arch_flush_all(void);
int vos3_tlb_arch_send(uint32_t apic);
void vos3_tlb_arch_pause(void);
#endif
