/* Serialized, fail-closed TLB shootdown. No address-space lifetime guarantee:
 * callers must publish PTE changes first and retain old pages until success. */
#include "../../include/vos/tlb_shootdown.h"

static unsigned active;
static unsigned poisoned;
static uint64_t generation;
static uint64_t requests[VOS3_TLB_MAX_CPUS];
static uint64_t acknowledgements[VOS3_TLB_MAX_CPUS];

static int valid_range(uintptr_t base, size_t size)
{
    if (size == 0) return 1;
    if (size - 1 > UINTPTR_MAX - base) return 0;
    uintptr_t last = base + size - 1;
    /* Current kernel uses four-level paging, not LA57. Validate both ends
     * and prevent crossing the noncanonical hole. No exclusive-end overflow. */
    return (base <= UINT64_C(0x00007fffffffffff) &&
            last <= UINT64_C(0x00007fffffffffff)) ||
           (base >= UINT64_C(0xffff800000000000) &&
            last >= UINT64_C(0xffff800000000000));
}

void vos3_tlb_shootdown_handle(void)
{
    uint32_t cpu = vos3_tlb_arch_cpu();
    if (cpu >= VOS3_TLB_MAX_CPUS) return;
    uint64_t request = __atomic_load_n(&requests[cpu], __ATOMIC_ACQUIRE);
    if (request == 0 ||
        __atomic_load_n(&acknowledgements[cpu], __ATOMIC_RELAXED) == request)
        return;
    /* Full invalidation covers 4K/huge pages and inactive tagged contexts.
     * A duplicate/late vector cannot acknowledge a different CPU. */
    vos3_tlb_arch_flush_all();
    __atomic_store_n(&acknowledgements[cpu], request, __ATOMIC_RELEASE);
}

int vos3_tlb_shootdown_checked(uintptr_t base, size_t size)
{
    if (!valid_range(base, size)) return VOS3_TLB_INVALID;
    if (__atomic_load_n(&poisoned, __ATOMIC_ACQUIRE)) return VOS3_TLB_POISONED;
    if (size == 0) return 0;

    uint64_t flags = vos3_tlb_arch_irq_save();
    unsigned expected = 0;
    if (!__atomic_compare_exchange_n(&active, &expected, 1, 0,
                                     __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) {
        vos3_tlb_arch_irq_restore(flags);
        return VOS3_TLB_BUSY; /* Never spin with a peer's IPI blocked. */
    }
    int result = 0;
    uint32_t self = vos3_tlb_arch_cpu();
    uint32_t count = vos3_tlb_arch_count();
    uint32_t targets[VOS3_TLB_MAX_CPUS];
    uint32_t apics[VOS3_TLB_MAX_CPUS];
    uint32_t target_count = 0;
    if (__atomic_load_n(&poisoned, __ATOMIC_ACQUIRE)) {
        result = VOS3_TLB_POISONED;
        goto done;
    }
    if (self >= VOS3_TLB_MAX_CPUS || count > VOS3_TLB_MAX_CPUS ||
        (count != 0 && self >= count)) {
        result = VOS3_TLB_INVALID;
        goto done;
    }
    for (uint32_t cpu = 0; cpu < count; cpu++) {
        uint32_t apic;
        if (!vos3_tlb_arch_target(cpu, &apic)) continue;
        if (apic > 255U) { result = VOS3_TLB_INVALID; goto done; }
        /* Duplicate physical destinations would leave a logical target
         * unacknowledged. Reject rather than dispatch an ambiguous map. */
        for (uint32_t i = 0; i < target_count; i++)
            if (apics[i] == apic) { result = VOS3_TLB_INVALID; goto done; }
        targets[target_count] = cpu;
        apics[target_count++] = apic;
    }
    if (generation == UINT64_MAX) {
        __atomic_store_n(&poisoned, 1, __ATOMIC_RELEASE);
        result = VOS3_TLB_POISONED;
        goto done;
    }
    uint64_t request = ++generation;
    vos3_tlb_arch_flush_all();
    for (uint32_t i = 0; i < target_count; i++) {
        if (targets[i] == self) continue;
        __atomic_store_n(&requests[targets[i]], request, __ATOMIC_RELEASE);
    }
    for (uint32_t i = 0; i < target_count; i++) {
        if (targets[i] != self && vos3_tlb_arch_send(apics[i]) != 0) {
            __atomic_store_n(&poisoned, 1, __ATOMIC_RELEASE);
            result = VOS3_TLB_TIMEOUT;
            goto done;
        }
    }

    for (unsigned budget = VOS3_TLB_WAIT_BUDGET; budget != 0; budget--) {
        int complete = 1;
        for (uint32_t i = 0; i < target_count; i++)
            if (targets[i] != self &&
                __atomic_load_n(&acknowledgements[targets[i]], __ATOMIC_ACQUIRE) != request)
                complete = 0;
        if (complete) goto done;
        vos3_tlb_arch_pause();
    }
    /* No request/descriptor reuse after a timeout. Late handlers may finish,
     * but cannot make this failure or a later operation appear successful. */
    __atomic_store_n(&poisoned, 1, __ATOMIC_RELEASE);
    result = VOS3_TLB_TIMEOUT;
done:
    __atomic_store_n(&active, 0, __ATOMIC_RELEASE);
    vos3_tlb_arch_irq_restore(flags);
    return result;
}
