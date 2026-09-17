/* Controlled native lifetime interleaving; not an SMP race proof. */
#include "vos/ai_guard.h"
#include "vos/atomic.h"
#include "vos/console.h"
#include "vos/scheduler.h"

extern uint64_t vos3_ai_guard_test_finalized_contexts(void);
static void require(int ok, const char *reason)
{
    if (!ok) VOS3_PANIC("AI-CTX-LIFETIME: %s", reason);
}

void vos3_test_ai_context_lifetime(void)
{
    vos3_irqflags_t flags = vos3_irq_save();
    vos3_irq_restore(flags);
    require((flags & (1ULL << 9)) && vos3_sched_current(), "needs IF-enabled task");
    vos3_ai_guard_reap_contexts();
    uint64_t before = vos3_ai_guard_test_finalized_contexts();
    require(before <= UINT64_MAX - 2, "counter overflow");
    require(vos3_ai_guard_create_app_ctx(7) == VOS3_AI_GUARD_OK, "create");
    vos3_ai_guard_ctx_t *held = vos3_ai_guard_acquire_app_ctx(7);
    require(held != NULL, "acquire");
    held->flags = 0x13572468U;
    require(vos3_ai_guard_create_app_ctx(7) == VOS3_AI_GUARD_ERR_INVALID, "duplicate");
    require(vos3_ai_guard_destroy_app_ctx(7) == VOS3_AI_GUARD_OK, "detach");
    require(vos3_ai_guard_acquire_app_ctx(7) == NULL, "detached lookup");
    vos3_ai_guard_reap_contexts();
    require(held->flags == 0x13572468U &&
            vos3_ai_guard_test_finalized_contexts() == before, "held reader reclaimed");
    require(vos3_ai_guard_create_app_ctx(7) == VOS3_AI_GUARD_OK, "replacement");
    vos3_ai_guard_ctx_t *replacement = vos3_ai_guard_acquire_app_ctx(7);
    require(replacement && replacement != held, "replacement identity");
    replacement->flags = 0x24681357U;
    flags = vos3_irq_save();
    vos3_ai_guard_ctx_put(held);
    held = NULL; /* No access after the last put. */
    vos3_ai_guard_reap_contexts();
    int deferred = vos3_ai_guard_test_finalized_contexts() == before;
    vos3_irq_restore(flags);
    require(deferred, "IRQ-off finalization");
    vos3_ai_guard_reap_contexts();
    require(vos3_ai_guard_test_finalized_contexts() == before + 1, "first exact reclaim");
    require(replacement->flags == 0x24681357U, "replacement changed");
    vos3_ai_guard_ctx_t *lookup = vos3_ai_guard_acquire_app_ctx(7);
    require(lookup == replacement, "replacement publication");
    vos3_ai_guard_ctx_put(lookup);
    vos3_ai_guard_reap_contexts();
    require(vos3_ai_guard_test_finalized_contexts() == before + 1, "repeat reclaim");
    require(vos3_ai_guard_destroy_app_ctx(7) == VOS3_AI_GUARD_OK, "replacement detach");
    require(vos3_ai_guard_destroy_app_ctx(7) == VOS3_AI_GUARD_ERR_NOTFOUND, "repeat detach");
    vos3_ai_guard_ctx_put(replacement);
    vos3_ai_guard_reap_contexts();
    vos3_ai_guard_reap_contexts();
    require(vos3_ai_guard_test_finalized_contexts() == before + 2, "second exact reclaim");
    VOS3_INFO("[AI-CTX-LIFETIME] PASS held=1 detached=1 duplicate=1 replacement=1 irq_deferred=1 finalized=2");
}
