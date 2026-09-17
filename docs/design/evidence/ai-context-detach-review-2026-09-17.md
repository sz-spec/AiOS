# AI context detach ordering: independent review, 2026-09-17

Reviewer: math_build_review; implementation and test authored by root. Scope is publication removal before release, not complete context lifetime or authorization.

## Results

The unchanged actual-source host harness passed against the working source and failed against `git show HEAD:kernel/src/mm/ai_guard.c`, supplied through a temporary module ROOT override. The baseline compiled successfully and failed at runtime in `assert_detached`: the global pointer still exposed the object at release. No production files were changed by this review.

Evidence: `/private/tmp/vos-ai-lifetime-review-20260917/{current.log,baseline.log,result.json}`.

- Current source SHA-256: `39c995cd46b61640398f153aa1265262991d047437fea063ac7af05145d2f254`.
- Baseline source SHA-256: `e8554922b492e7b138e28d0c54060550ac7c89c7722d1cd445a079b72ec867d9`.
- Identical harness SHA-256: `ef2f8a9f08842a0347e8b888e240f2de11332a5e939360c8edaa80f303c0428f`.

The harness checks app/global detachment at both region-structure frees and context free, two-region traversal, total context count conservation, sequential duplicate creation rejection, repeated destroy rejection and preservation of another context's global registration. Region payload mappings, red zones and PMM release are mocked, not hardware-qualified.

## Bounded acceptance

Approved for the narrow sequential invariant: destruction through destroy_app_ctx removes its app slot, then conditionally removes matching global publication before region/context release. Destroying a non-global context does not clear a different global pointer. Global publication uses release/relaxed CAS; removal uses acquire-release/acquire CAS. App pointer accesses within this module consistently use atomics. The final patch does not introduce the previously proposed duplicate-create CAS/loser protocol.

## Unresolved boundaries

An existing raw reader remains unprotected after loading a pointer. This includes UP preemption: syscall_entry.S enables IRQ before dispatch; syscall.c invokes deferred processing with IF enabled; scheduler.c calls reprotect without masking IRQ; the BSP PIT handler in interrupts.c may reschedule interrupted kernel code. The per-CPU deferred-active guard prevents a second drain, not a context-destroy syscall. SMP requires an explicit reader lifetime protocol as well.

Concurrent duplicate create remains a load-then-store race and may overwrite/leak a context; concurrent create/destroy, active-app identity, raw pointer getters, object ownership and caller authorization remain unresolved. This test is sequential and makes no claim to cover those races, allocator ABA, or full AS/security isolation.

The F experiment's health result of 39,934/sec disproves a Frontier-only causal attribution. Detach ordering is an independently identified defect correction, not an established cause or fix of the SPSC performance difference. No native build or QEMU run was performed for this review.
