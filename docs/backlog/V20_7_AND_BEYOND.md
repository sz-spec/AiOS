<!--
SPDX-License-Identifier: MIT
SPDX-FileCopyrightText: 2026 VOS3 Project
-->

# VOS3 v20.7+ Backlog — Deferred Tech Debt

**Source:** v20.6 zero-debt purge (2026-05-01).
**Driver doc:** `docs/technical/V20_6_MASTER_REPAIR_SPEC.md` §5.
**CI gate:** `tools/audit/tech_debt_purge.sh` enforces zero markers in source dirs from v20.6 onwards. The items below are tracked HERE instead of as inline `TODO:` comments.

Each entry has: file:line at the time of purge, original instruction, target version, the `pragma` marker that replaces the inline tag in the source.

---

## v20.7 — Compatibility & POSIX completeness

### B1. Socket flags handling
- **File:** `kernel/src/net/socket.c:416`
- **Was:** `(void)flags;  /* TODO: Handle flags */`
- **Spec instruction:** "Implement `MSG_DONTWAIT`/`MSG_PEEK` or return `-EOPNOTSUPP` for unsupported flags"
- **Target:** v20.7 — POSIX networking sweep
- **Pragma in source:** `/* v20.7: see docs/backlog/V20_7_AND_BEYOND.md#b1 */`

### B2. Socket capability scope
- **File:** `kernel/src/net/socket.c:182`
- **Was:** `/* TODO: Get from current task's capability set */`
- **Spec instruction:** "Use `vos3_sched_current()->capabilities` or remove if unused"
- **Target:** v20.7 — capability propagation pass

### B3. tty.c TIOCGWINSZ
- **File:** `kernel/src/drivers/tty.c:243`
- **Was:** `/* TODO: Return window size */`
- **Spec instruction:** "Implement `TIOCGWINSZ` returning fixed 80x25, or stub `return -ENOTTY`"
- **Target:** v20.7 — TTY POSIX sweep

### B4. virtio_net softirq scheduling
- **File:** `kernel/src/drivers/virtio_net.c:471`
- **Was:** `/* TODO: Schedule softirq/worker for deferred processing */`
- **Spec instruction:** "Use existing `vos3_workqueue_schedule()`"
- **Target:** v20.7 — net path optimization

### B5. virtio_blk write-back
- **File:** `kernel/src/drivers/virtio_blk.c:214`
- **Was:** `/* TODO: Write back to disk */`
- **Spec instruction:** "Implement `virtio_blk_write()` real path or `panic('write path required')` if not yet exposed"
- **Target:** v20.7 — block driver completion

### B6. Sync timeout
- **File:** `kernel/src/sched/sync.c:427`
- **Was:** `/* TODO: Implement timeout support */`
- **Spec instruction:** "Use existing `vos3_timer_after_ticks()`"
- **Target:** v20.7

### B7. Per-task CPU time
- **File:** `kernel/src/time/time_syscall.c:135`
- **Was:** `/* TODO: Track per-process/thread CPU time */`
- **Spec instruction:** "Wire to `vos3_task_t->total_runtime` (already exists)"
- **Target:** v20.7

### B8. exec_syscall — signal-interrupt remaining time
- **File:** `kernel/src/exec/exec_syscall.c:1002`
- **Was:** `(void)user_rem;  /* TODO: Fill remaining time on signal interrupt */`
- **Spec instruction:** "Compute via `vos3_timer_remaining(timer_id)`"
- **Target:** v20.7

### B9. Mount-data validation
- **File:** `kernel/src/fs/fs_syscall.c:801`
- **Was:** `(void)data;  /* TODO: Handle mount data */`
- **Spec instruction:** "Validate via `vfs_validate_mount_data()` or return `-EINVAL`"
- **Target:** v20.7

### B10. vfs.c cwd accessor
- **File:** `kernel/src/fs/vfs.c:452`
- **Was:** `/* TODO: Get current working directory from task */`
- **Spec instruction:** "Use `vos3_sched_current()->cwd`"
- **Target:** v20.7

### B11. ai_telemetry pid + wakeup
- **File:** `kernel/src/mm/ai_telemetry.c:428,475`
- **Spec instruction:** "Wire to `vos3_sched_current()->pid` and `vos3_wakeup(wait_q)`"
- **Target:** v20.7

### B12. ai_guard pid
- **File:** `kernel/src/mm/ai_guard.c:1509,1568`
- **Spec instruction:** "Use `vos3_sched_current()->pid`"
- **Target:** v20.7

### B13. signal.c core dump
- **File:** `kernel/src/ipc/signal.c:148`
- **Was:** `/* TODO: Generate core dump */`
- **Spec instruction:** "Move to v20.7"
- **Target:** v20.7 — core-dump subsystem

### B14. arch/x86_64/user.c address-space + page-fault
- **File:** `kernel/src/arch/x86_64/user.c:141, 322, 342`
- **Spec instruction:** "Replace placeholder error messages with real implementation or `vos3_panic('unimplemented')`"
- **Target:** v20.7 — user-arch completion

### B15. metrics_routes Redis/Postgres checkpointer
- **File:** `backend/api/metrics_routes.py:200`
- **Was:** `# TODO: Add Redis/Postgres checkpointer support when needed`
- **Target:** v20.7 — observability backend pluggability

---

## v20.7+ — Deferred to subsequent milestones

### C1. storage_hal IDENTIFY
- **File:** `kernel/src/drivers/storage_hal.c:188`
- **Spec instruction:** "Move to v20.7 milestone — convert to GitHub issue"
- **Target:** v20.7 — storage HAL completion

### C2. Scheduler regression-validation gates
- **File:** `kernel/src/sched/scheduler.c:574, 715`
- **Was:** `/* TODO(Task 5): Enable after regression validation. */`
- **Action:** "Run regression suite; if green → enable + delete comment"
- **Target:** v20.8 — scheduler optimizations after extended QEMU CI

### C3. accel.c GPU/NPU dispatch
- **File:** `kernel/src/drivers/accel.c:679`
- **Was:** `/* TODO(Phase 6): Replace with real GPU/NPU dispatch. */`
- **Spec instruction:** "Move to v21.0 milestone"
- **Target:** **v21.0 — Project Aurora (NPU pipeline)**

### C4. Per-process address-space isolation
- **File:** `kernel/src/mm/user_copy.c:521`
- **Was:** `/* TODO: Per-process address space isolation */`
- **Spec instruction:** "Move to v21.0"
- **Target:** **v21.0 — full process isolation**

---

## Frontend / desktop deferrals

### F1. studio page — local-fs/GitHub delete UX
- **File:** `frontend/app/studio/page.tsx:161`
- **Was:** `{/* TODO: discuss when to delete from local filesystem and GitHub to prevent mistakes */}`
- **Target:** v20.7 — frontend safety polish

### F2. visual-edit — error handling
- **File:** `frontend/components/visual-edit/PropertyPanel.tsx:143`
- **Was:** `// TODO: error handling`
- **Target:** v20.7 — frontend error-toast pass

---

## Intentional non-debt — codegen templates

The following are **NOT** debt — they are intentional content of code-template f-strings that the codegen feature emits to end-users:

- `backend/api/codegen_routes.py:154` — Python template's `def main(): """TODO: Implement based on prompt."""`
- `backend/api/codegen_routes.py:155` — TypeScript template's `// TODO: Implement based on prompt`
- `backend/api/codegen_routes.py:156` — JavaScript template's `// TODO: Implement based on prompt`
- `backend/api/codegen_routes.py:157` — React template's `{/* TODO: Implement based on prompt */}`
- `backend/api/codegen_routes.py:499, 503` — `"    // TODO: Implement with real LLM\n"` in stub strings
- `backend/api/codegen_routes.py:582` — pytest stub `"""TODO: Implement tests."""`

These markers ship to *user-generated code* as guidance; removing them breaks the codegen feature. The `tech_debt_purge.sh` script is updated to skip lines tagged with the `pragma: codegen-template` annotation.

---

## How to dequeue an item

1. Pick an entry; implement the spec instruction.
2. Remove the `v20.7: see docs/backlog/...` reference comment from the source.
3. Delete or update this entry in the backlog.
4. The CI gate (`tech_debt_purge.sh`) stays green automatically because no marker word is reintroduced.
