/* Native real-PMM/VMM/SHM lifecycle checks; run with a current task and no
 * concurrent users of its address space. No privileged user-access claims. */
#include "vos/vmm.h"
#include "vos/pmm.h"
#include "vos/ipc.h"
#include "vos/task.h"
#include "vos/scheduler.h"
#include "vos/console.h"
#include "vos/vfs.h"
#include "vos/atomic.h"

static void require(int ok, const char* reason)
{
    if (!ok) VOS3_PANIC("VM-BACKING: %s", reason);
}

/* Test-only principal substitution, never a task pointer replacement. Local
 * IRQ exclusion prevents this task being scheduled with a temporary cookie. */
static void test_shm_identity(void)
{
    vos3_task_t* task = vos3_sched_current();
    require(task != NULL && task->identity_cookie != 0, "identity test needs principal");
    uint64_t saved = task->identity_cookie;
    uint32_t tid = task->tid;
    vos3_ipc_id_t id = vos3_shm_create("native-shm-identity", VOS3_PAGE_SIZE, 0);
    require(id != VOS3_IPC_INVALID, "identity SHM create");
    void* mapped = vos3_shm_map(id, 0);
    require(mapped != NULL, "identity SHM map");
    uintptr_t phys = 0;
    require(vos3_vmm_virt_to_phys((uintptr_t)mapped, &phys) == 0, "identity translation");
    uint32_t refs = vos3_pmm_ref_get(phys);
    volatile uint64_t* data = (volatile uint64_t*)vos3_phys_to_virt(phys);
    data[0] = 0xAABBCCDD11223344ULL;
    uint64_t other = saved == UINT64_MAX ? 1 : saved + 1;
    vos3_irqflags_t irq = vos3_irq_save();
    task->identity_cookie = 0;
    int zero_result = vos3_shm_destroy(id);
    task->identity_cookie = other;
    int recycled_tid_result = vos3_shm_destroy(id);
    task->identity_cookie = saved;
    vos3_irq_restore(irq);
    /* Restore identity and IRQ state before any assertion/panic. */
    require(task->tid == tid && task->identity_cookie == saved, "identity restoration");
    require(zero_result == VOS3_IPC_ERR_ACCESS && recycled_tid_result == VOS3_IPC_ERR_ACCESS,
            "zero or different principal accepted numeric TID authority");
    require(vos3_shm_size(id) == VOS3_PAGE_SIZE && refs > 0 &&
            vos3_pmm_ref_get(phys) == refs && data[0] == 0xAABBCCDD11223344ULL,
            "denied principal changed live backing");
    require(vos3_shm_destroy(id) == VOS3_IPC_OK, "restored owner rejected");
    require(vos3_shm_unmap(id, mapped) == VOS3_IPC_OK && vos3_shm_size(id) == 0,
            "identity test final mapping release");
    VOS3_INFO("[SHM-IDENTITY] PASS: zero-cookie denied, same-TID different-cookie denied, owner restored, backing intact, final release");
}

/* Only filesystem close is synthetic; retain, clone, CPU pins and deferred
 * reclamation execute the production implementation with real page tables. */
static unsigned backing_close_count;
static int count_backing_close(vos3_file_t* file)
{
    require(file->ref_count == 0, "close with live backing refs");
    ++backing_close_count;
    return 0; /* Stack-owned synthetic file, deliberately no allocator free. */
}
static void test_file_reference_ownership(void)
{
    static const vos3_file_ops_t ops = {.close = count_backing_close};
    vos3_file_t file = {0};
    require(vos3_file_retain(NULL) == -22, "null backing retain");
    require(vos3_file_retain(&file) == -5 && file.ref_count == 0,
            "backing resurrection");
    file.ref_count = UINT32_MAX;
    require(vos3_file_retain(&file) == -75 && file.ref_count == UINT32_MAX,
            "backing retain overflow");
    file.ops = &ops;
    backing_close_count = 0;
    vos3_address_space_t* original = vos3_vmm_get_current_space();
    size_t available = vos3_pmm_free_pages_count();
    vos3_address_space_t* owner = vos3_vmm_create_address_space();
    require(owner != NULL, "file owner allocation");
    owner->vmas[0] = (vos3_vma_t){.vm_start = 0x7300000000ULL,
        .vm_end = 0x7300001000ULL, .vm_prot = 1, .vm_flags = 2,
        .vm_file = &file, .valid = 1};
    owner->num_vmas = 1;
    /* Saturation is injected only as a rejection control, then restored. */
    require(vos3_vmm_clone_cow(owner) == NULL, "overflow clone published");
    vos3_vmm_reap_address_spaces();
    require(file.ref_count == UINT32_MAX && backing_close_count == 0,
            "failed clone released borrowed backing");
    file.ref_count = 1;
    vos3_address_space_t* child = vos3_vmm_clone_cow(owner);
    require(child && file.ref_count == 2, "clone did not retain backing");
    require(vos3_vmm_retain_address_space(child) == 0 && file.ref_count == 2,
            "AS retain duplicated backing ownership");
    vos3_vmm_destroy_address_space(child);
    vos3_vmm_reap_address_spaces();
    require(file.ref_count == 2 && backing_close_count == 0,
            "partial AS release dropped backing");
    vos3_vmm_destroy_address_space(child);
    vos3_vmm_reap_address_spaces();
    require(file.ref_count == 1 && backing_close_count == 0,
            "child final release imbalance");
    /* Model a CPU-only pin without exposing a task/CPU binding mismatch to
     * preemption. Reaps here cannot close the still-pinned backing. */
    vos3_irqflags_t irq = vos3_irq_save();
    vos3_vmm_switch_address_space(owner);
    vos3_vmm_destroy_address_space(owner);
    vos3_vmm_reap_address_spaces();
    require(file.ref_count == 1 && backing_close_count == 0,
            "active CPU lost backing");
    vos3_vmm_switch_address_space(original);
    vos3_irq_restore(irq);
    vos3_vmm_reap_address_spaces();
    require(file.ref_count == 0 && backing_close_count == 1,
            "last release did not close exactly once");
    vos3_vmm_reap_address_spaces();
    require(backing_close_count == 1, "repeated reap closed backing twice");
    require(vos3_pmm_free_pages_count() == available, "file test page leak");
    VOS3_INFO("[VM-FILE-REFS] PASS: checked retain, clone rollback, partial release, CPU pin, exactly-once close");
}

static void test_creator_first_shm(void)
{
    vos3_task_t* task = vos3_sched_current();
    vos3_address_space_t* saved_task = task->address_space;
    vos3_address_space_t* saved_cpu = vos3_vmm_get_current_space();
    for (unsigned reap = 0; reap < 2; ++reap) {
        vos3_address_space_t* as = vos3_vmm_create_address_space();
        require(as != NULL, "creator-first AS allocation");
        vos3_ipc_id_t id = vos3_shm_create("native-creator-first", VOS3_PAGE_SIZE, 0);
        require(id != VOS3_IPC_INVALID, "creator-first SHM allocation");
        task->address_space = as;
        vos3_vmm_switch_address_space(as);
        void* addr = vos3_shm_map(id, 0);
        require(addr != NULL, "creator-first map");
        uintptr_t phys = 0;
        require(vos3_vmm_virt_to_phys((uintptr_t)addr, &phys) == 0, "creator-first translation");
        require(vos3_shm_destroy(id) == VOS3_IPC_OK, "creator-first close");
        require(vos3_shm_destroy(id) == VOS3_IPC_ERR_INVALID, "duplicate creator close stole mapping");
        require(vos3_shm_size(id) == VOS3_PAGE_SIZE && vos3_pmm_ref_get(phys) > 0,
                "creator close freed live mapping");
        ((volatile uint64_t*)vos3_phys_to_virt(phys))[0] = 0x1234ABCD;
        size_t before = vos3_pmm_free_pages_count();
        vos3_vmm_stats_t old_stats, new_stats;
        vos3_vmm_get_stats(&old_stats);
        if (!reap) {
            require(vos3_shm_unmap(id, addr) == VOS3_IPC_OK, "last explicit mapping close");
        } else {
            vos3_vmm_destroy_address_space(as);
            task->address_space = saved_task;
            vos3_vmm_switch_address_space(saved_cpu);
            vos3_vmm_reap_address_spaces();
        }
        /* Only query registry/PMM after final put; never dereference freed backing. */
        require(vos3_shm_size(id) == 0 && vos3_shm_find("native-creator-first") == VOS3_IPC_INVALID,
                "last mapping release leaked region");
        require(vos3_pmm_ref_get(phys) == 0, "last mapping release leaked backing");
        vos3_vmm_get_stats(&new_stats);
        require(new_stats.page_tables <= old_stats.page_tables,
                "final release increased page-table count");
        size_t released_tables = old_stats.page_tables - new_stats.page_tables;
        /* Reaping also releases the AS metadata allocation; explicit detach
         * leaves page-table allocation intact. Require at least backing+tables. */
        size_t free_after = vos3_pmm_free_pages_count();
        require(released_tables < SIZE_MAX && free_after >= before &&
                free_after - before >= 1 + released_tables,
                "last mapping release missing PMM backing/table reclamation");
        require(vos3_shm_destroy(id) == VOS3_IPC_ERR_NOTFOUND, "destroy resurrected finalized slot");
        if (!reap) {
            task->address_space = saved_task;
            vos3_vmm_switch_address_space(saved_cpu);
            vos3_vmm_destroy_address_space(as);
            vos3_vmm_reap_address_spaces();
        }
    }
}

static void test_metadata_cow(void)
{
    const uintptr_t va = 0x7200000000ULL;
    vos3_task_t* task = vos3_sched_current();
    vos3_address_space_t* saved_task = task->address_space;
    vos3_address_space_t* saved_cpu = vos3_vmm_get_current_space();
    vos3_vmm_reap_address_spaces();
    size_t before = vos3_pmm_free_pages_count();
    vos3_address_space_t* parent = vos3_vmm_create_address_space();
    require(parent != NULL, "metadata parent allocation");
    uintptr_t original = vos3_pmm_alloc(VOS3_PMM_FLAG_ZERO);
    require(original != 0, "metadata page allocation");
    task->address_space = parent;
    vos3_vmm_switch_address_space(parent);
    require(vos3_vmm_map_user(va, original, VOS3_PTE_PRESENT | VOS3_PTE_USER |
            VOS3_PTE_WRITABLE | VOS3_PTE_NO_EXECUTE) == 0, "metadata map");
    volatile uint64_t* original_data = (volatile uint64_t*)vos3_phys_to_virt(original);
    for (unsigned i = 0; i < VOS3_PAGE_SIZE / sizeof(uint64_t); ++i)
        original_data[i] = 0xDADA000000000000ULL + i;
    require(vos3_vmm_set_cognitive_priority(va) == 0, "cognitive tagging");
    vos3_pte_t pte;
    require(vos3_vmm_get_pte(va, &pte) == 0 && (pte & VOS3_PTE_COGNITIVE) &&
            !(pte & VOS3_PTE_COW), "cognitive tag became COW");
    vos3_address_space_t* child = vos3_vmm_clone_cow(parent);
    require(child != NULL, "metadata COW clone");
    require(vos3_vmm_get_pte(va, &pte) == 0 && (pte & VOS3_PTE_COGNITIVE) &&
            (pte & VOS3_PTE_COW) && !(pte & VOS3_PTE_WRITABLE), "parent COW flags");
    task->address_space = child;
    vos3_vmm_switch_address_space(child);
    require(vos3_vmm_get_pte(va, &pte) == 0 && (pte & VOS3_PTE_COGNITIVE) &&
            (pte & VOS3_PTE_COW) && !(pte & VOS3_PTE_WRITABLE), "child COW flags");
    /* Invoke the production fault resolver; this is not a ring-3 fault test. */
    require(vos3_vmm_handle_cow_fault(va, 7) == 0, "real COW resolver");
    require(vos3_vmm_get_pte(va, &pte) == 0 && (pte & VOS3_PTE_COGNITIVE) &&
            !(pte & VOS3_PTE_COW) && (pte & VOS3_PTE_WRITABLE) &&
            (pte & VOS3_PTE_NO_EXECUTE), "COW metadata lost");
    uintptr_t copied = vos3_pte_get_addr(pte);
    require(copied != original, "COW reused shared physical page");
    volatile uint64_t* child_data = (volatile uint64_t*)vos3_phys_to_virt(copied);
    for (unsigned i = 0; i < VOS3_PAGE_SIZE / sizeof(uint64_t); ++i)
        require(child_data[i] == original_data[i], "COW content copy mismatch");
    child_data[0] ^= 1;
    for (unsigned i = 0; i < VOS3_PAGE_SIZE / sizeof(uint64_t); ++i)
        require(original_data[i] == 0xDADA000000000000ULL + i, "COW changed parent data");
    for (int prot = 1; prot <= 3; prot += 2) {
        require(vos3_vmm_mprotect_range(va, VOS3_PAGE_SIZE, prot) == 0,
                "metadata mprotect");
        require(vos3_vmm_get_pte(va, &pte) == 0 && (pte & VOS3_PTE_COGNITIVE) &&
                !(pte & VOS3_PTE_COW) &&
                !!(pte & VOS3_PTE_WRITABLE) == !!(prot & 2), "mprotect lost metadata/permissions");
    }
    require(vos3_vmm_update_flags(va, VOS3_VMM_FLAG_USER | VOS3_VMM_FLAG_WRITE) == 0,
            "metadata update_flags");
    require(vos3_vmm_get_pte(va, &pte) == 0 && (pte & VOS3_PTE_COGNITIVE) &&
            !(pte & VOS3_PTE_COW), "update_flags lost cognitive metadata");
    task->address_space = saved_task;
    vos3_vmm_switch_address_space(saved_cpu);
    vos3_vmm_destroy_address_space(child);
    vos3_vmm_destroy_address_space(parent);
    vos3_vmm_reap_address_spaces();
    require(vos3_pmm_free_pages_count() == before, "metadata lifecycle page leak");
    VOS3_INFO("[VM-METADATA] PASS: distinct tags, real COW copy, parent integrity, mprotect, flag updates, final accounting");
}

void vos3_test_vm_backing(void)
{
    vos3_task_t* task = vos3_sched_current();
    require(task != NULL, "test needs scheduler current task");
    vos3_address_space_t* saved_task_as = task->address_space;
    vos3_address_space_t* saved_cpu_as = vos3_vmm_get_current_space();
    vos3_address_space_t* owner = vos3_vmm_create_address_space();
    vos3_address_space_t* stranger = vos3_vmm_create_address_space();
    require(owner && stranger, "address-space allocation");
    vos3_ipc_id_t id = vos3_shm_create("native-vm-backing", VOS3_PAGE_SIZE, 0);
    require(id != VOS3_IPC_INVALID, "SHM creation");
    task->address_space = owner;
    vos3_vmm_switch_address_space(owner);
    void* maps[VOS3_AS_MAX_SHM];
    for (unsigned i = 0; i < VOS3_AS_MAX_SHM; ++i) {
        maps[i] = vos3_shm_map(id, 0);
        require(maps[i] != NULL, "tracked map rejected");
    }
    require(owner->shm_count == VOS3_AS_MAX_SHM, "tracking count");
    size_t available = vos3_pmm_free_pages_count();
    require(vos3_shm_map(id, 0) == NULL, "untracked overflow map accepted");
    require(owner->shm_count == VOS3_AS_MAX_SHM &&
            vos3_pmm_free_pages_count() == available, "overflow changed resources");
    require(vos3_vmm_clone_cow(owner) == NULL, "SHM fork silently accepted");
    require(vos3_pmm_free_pages_count() == available, "rejected SHM clone allocated");
    uintptr_t backing = 0;
    require(vos3_vmm_virt_to_phys((uintptr_t)maps[0], &backing) == 0,
            "missing owner translation");
    volatile uint64_t* bytes = (volatile uint64_t*)vos3_phys_to_virt(backing);
    for (unsigned i = 0; i < VOS3_PAGE_SIZE / sizeof(uint64_t); ++i)
        bytes[i] = 0xC0FFEE0000000000ULL + i;
    task->address_space = stranger;
    vos3_vmm_switch_address_space(stranger);
    require(vos3_shm_unmap(id, maps[0]) == VOS3_IPC_ERR_INVALID,
            "foreign address-space unmap accepted");
    require(vos3_shm_unmap(id, NULL) == VOS3_IPC_ERR_INVALID,
            "NULL user unmap dropped reference");
    require(owner->shm_count == VOS3_AS_MAX_SHM && stranger->shm_count == 0,
            "foreign unmap changed tracking");
    task->address_space = owner;
    vos3_vmm_switch_address_space(owner);
    uintptr_t still_backing = 0;
    require(vos3_vmm_virt_to_phys((uintptr_t)maps[0], &still_backing) == 0 &&
            still_backing == backing, "foreign unmap removed owner mapping");
    require(vos3_shm_unmap(id, maps[1]) == VOS3_IPC_OK, "owned unmap failed");
    require(vos3_pmm_ref_get(backing) > 0, "owned detach freed shared backing");
    require(owner->shm_count == VOS3_AS_MAX_SHM - 1, "owned unmap tracking");
    require(vos3_vmm_retain_address_space(owner) == 0, "shared owner retain");
    vos3_vmm_destroy_address_space(owner);
    vos3_vmm_reap_address_spaces();
    require(owner->shm_count == VOS3_AS_MAX_SHM - 1, "nonfinal release lost SHM");
    for (unsigned i = 0; i < VOS3_PAGE_SIZE / sizeof(uint64_t); ++i)
        require(bytes[i] == 0xC0FFEE0000000000ULL + i, "survivor backing changed");
    vos3_vmm_destroy_address_space(owner);
    task->address_space = saved_task_as;
    vos3_vmm_switch_address_space(saved_cpu_as);
    vos3_vmm_destroy_address_space(stranger);
    vos3_vmm_reap_address_spaces();
    /* Backing remains owned by the SHM creator after all mappings vanish. */
    require(vos3_pmm_ref_get(backing) > 0, "AS destructor freed SHM backing");
    for (unsigned i = 0; i < VOS3_PAGE_SIZE / sizeof(uint64_t); ++i)
        require(bytes[i] == 0xC0FFEE0000000000ULL + i, "final release corrupted backing");
    require(vos3_shm_destroy(id) == VOS3_IPC_OK, "creator release failed");
    require(vos3_shm_size(id) == 0, "mapping reference leaked after final release");
    test_creator_first_shm();
    VOS3_INFO("[VM-BACKING] PASS: tracking cap, foreign unmap, unsupported SHM fork, surviving owner, final mapping cleanup, creator-first explicit/reap, duplicate creator close");
    test_metadata_cow();
    test_file_reference_ownership();
    test_shm_identity();
}
