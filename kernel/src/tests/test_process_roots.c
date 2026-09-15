/* Native lifecycle checks: real PMM/VMM allocations, no mock page tables. */
#include "vos/vmm.h"
#include "vos/pmm.h"
#include "vos/console.h"

extern void vos3_vmm_test_table_alloc_budget(int budget);

static void require(int condition, const char* message)
{
    if (!condition) {
        VOS3_PANIC("PROCESS-ROOTS: %s", message);
    }
}

void vos3_test_process_roots(void)
{
    /* Rejection controls use local metadata only, never a freed pointer. */
    vos3_address_space_t invalid = {0};
    require(vos3_vmm_retain_address_space(NULL) == -22, "retain null accepted");
    require(vos3_vmm_retain_address_space(&invalid) == -5,
            "zero-reference address space resurrected");
    invalid.ref_count = UINT32_MAX;
    require(vos3_vmm_retain_address_space(&invalid) == -75,
            "reference overflow accepted");
    require(invalid.ref_count == UINT32_MAX, "overflow changed reference count");
    vos3_address_space_t* original = vos3_vmm_get_current_space();
    require(original != NULL, "missing original CPU binding");
    size_t free_before = vos3_pmm_free_pages_count();
    vos3_vmm_stats_t before, after;
    vos3_vmm_get_stats(&before);
    vos3_address_space_t* first = vos3_vmm_create_address_space();
    vos3_address_space_t* second = vos3_vmm_create_address_space();
    require(first != NULL && second != NULL, "create failed");
    require(first->pml4_phys != first->user_pml4_phys, "full/restricted root alias");
    require(second->pml4_phys != second->user_pml4_phys, "second root alias");
    require(first->user_pml4_phys != second->user_pml4_phys, "process root alias");
    require(first->user_pml4_phys != 0 && second->user_pml4_phys != 0,
            "missing restricted root");
    for (size_t i = 0; i < 512; ++i) {
        require(first->user_pml4[i] == 0 && second->user_pml4[i] == 0,
                "unpublished root contains mappings");
    }
    /* A restricted root is unpublished at this stage. A sentinel proves it
     * is not shared or copied into a sibling/child; it is never loaded in CR3. */
    first->user_pml4[0] = 0xDEAD001ULL;
    require(second->user_pml4[0] == 0, "sibling changed");
    require(first->pml4[0] == 0, "full root changed");
    /* Force each top-level allocation to fail in turn, retaining the real
     * production cleanup paths and the real PMM allocations before failure. */
    for (int cloning = 0; cloning < 2; ++cloning) {
        for (int budget = 0; budget < 2; ++budget) {
            size_t available = vos3_pmm_free_pages_count();
            vos3_vmm_stats_t failure_before, failure_after;
            vos3_vmm_get_stats(&failure_before);
            vos3_vmm_test_table_alloc_budget(budget);
            vos3_address_space_t* failed = cloning
                ? vos3_vmm_clone_cow(first) : vos3_vmm_create_address_space();
            vos3_vmm_test_table_alloc_budget(-1);
            require(failed == NULL, "allocation failure returned partial process");
            vos3_vmm_get_stats(&failure_after);
            require(vos3_pmm_free_pages_count() == available, "failure leaked pages");
            require(failure_after.page_tables == failure_before.page_tables,
                    "failure leaked page-table accounting");
        }
    }
    vos3_address_space_t* child = vos3_vmm_clone_cow(first);
    require(child != NULL, "clone failed");
    require(child->user_pml4_phys != first->user_pml4_phys &&
            child->user_pml4_phys != second->user_pml4_phys &&
            child->user_pml4_phys != child->pml4_phys,
            "child restricted root alias");
    require(child->user_pml4[0] == 0, "clone copied unpublished restricted mappings");
    /* Independent creator and retained task-style ownership. */
    require(child->ref_count == 1, "new clone ownership count");
    require(vos3_vmm_retain_address_space(child) == 0 && child->ref_count == 2,
            "extra ownership retain failed");
    vos3_vmm_destroy_address_space(child);
    require(child->ref_count == 1, "nonfinal owner release freed clone");
    vos3_vmm_reap_address_spaces();
    require(child->ref_count == 1 && child->pml4_phys != 0,
            "reaper reclaimed owned clone");
    vos3_vmm_destroy_address_space(child);
    vos3_vmm_destroy_address_space(second);
    /* Never load the sentinel into hardware, even as an entry-root binding. */
    first->user_pml4[0] = 0;
    require(first->ref_count == 1, "creator ownership count");
    vos3_vmm_switch_address_space(first);
    require(vos3_vmm_get_current_space() == first && first->ref_count == 2,
            "CPU binding did not pin incoming roots");
    vos3_vmm_switch_address_space(first);
    require(first->ref_count == 2, "same-space switch leaked CPU pin");
    vos3_vmm_destroy_address_space(first);
    require(first->ref_count == 1, "creator release lost active CPU pin");
    vos3_vmm_reap_address_spaces();
    require(vos3_vmm_get_current_space() == first && first->ref_count == 1,
            "reaper freed active CPU roots");
    vos3_vmm_switch_address_space(original);
    /* first has reached zero; never dereference it after the final unpin. */
    vos3_vmm_reap_address_spaces();
    vos3_vmm_get_stats(&after);
    require(vos3_pmm_free_pages_count() == free_before, "physical page leak");
    require(after.page_tables == before.page_tables, "page-table accounting leak");
    VOS3_INFO("[PROCESS-ROOTS] PASS: create, clone, independent roots, allocation failures, owner refs, CPU pins, release, accounting");
}
