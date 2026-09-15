# AI policy and capabilities

Reviewer: `/root/mcp_upgrade`. Source: `32187957757ab57d7820d0a63fa62c409003b126`. Date: 2026-09-15. Bounded source and evidence review; no production edits.

1. **P1 — policy inheritance is explicitly unsupported.** [kernel/src/exec/exec.c](../../../../kernel/src/exec/exec.c) and `exec_syscall.c` reject fork/clone when `ai_guard_ctx` is present. [kernel/include/vos/ai_guard.h](../../../../kernel/include/vos/ai_guard.h) exposes creation/destruction without a reviewed inheritance contract. This refusal avoids silently dropping the guard policy or sharing an unowned pointer. Require policy-preserving inheritance and negative tests before lifting it.
2. **P1 — integrity success depends on policy activation.** `kernel/src/mm/ai_guard.c:vos3_ai_guard_verify_integrity` returns success when the CHECKSUMMED flag is absent. That is conditional API behavior, not proof every AI allocation is authenticated. Inventory registration and flag-setting callers, then test unregistered and disabled-policy cases separately from detected corruption.
3. **P1 — capability enforcement is incomplete.** `kernel/src/net/socket.c:task_has_cap` grants all capabilities to PID 0/1 and otherwise returns false while ignoring the requested capability. This is a restrictive placeholder for ordinary tasks, not a complete per-principal capability system. Define actual credential and delegation semantics before enterprise policy claims.

Validation: inspected these branches and the context interface at the stated revision. Not reviewed: all AI model-loading paths, adversarial model behavior, prompt injection across connectors, hardware enforcement, or cryptographic authenticity of model provenance. A guard name or checksum API does not establish these properties.
