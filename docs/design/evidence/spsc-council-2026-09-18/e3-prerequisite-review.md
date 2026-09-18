# E3 prerequisite failure: read-only review

Reviewer: `/root/mcp_upgrade`, 2026-09-18. Existing evidence and frozen source only; no edits to production, build or VM execution. The preserved failed run is `runs/e3-a-1/`.

## Observed contradiction

The fixed-icount-shift3 old-ISO run did not reach a measured SPSC body before its 500-second host deadline. Serial lines6846–6885 show the immediately preceding `test_model_load` creating **16/16 threads**, completing **0/16**, and producing **190/992 files with zero reported operation errors**. It explicitly prints `[FAIL] vfs_thread_stress` and exits1. Lines6887–6890 show health launch/fork/exec loading, not its body or timed loop. The result contains empty `spsc_rates` and rejects incomplete workload, nonzero exit, explicit failure and timeout. This is an invalid full-suite prerequisite, not a measured zero-throughput SPSC result. The raw serial identity remains `aca5d765b03e6c23d18bda89dd882eb6c1c7f11583e5cca83e5e609d27ff6e63` as recorded in result.json.

## Time budget and residual workers

Review used `/private/tmp/vos-health-final-20260917/source`, the old ISO's source, rather than silently substituting current SYSINFO callers. `user/src/test_model_load.c`, `vfs_thread_stress`, waits on per-thread completion flags with a **30,000 guest-millisecond** deadline and calls yield while waiting. Fixed icount changes the relationship between executed instructions and guest time. Therefore exhausting that guest deadline under shift3 does not establish host starvation, an MMIO effect, a kernel deadlock or a clone-allocation failure. The log explicitly confirms successful creation of all16 workers. The ~438-second host arrival at model completion is a coordinator observation; it is not derivable as a per-record timestamp from the plain serial log.

A concrete lifecycle weakness makes simply increasing the outer host timeout insufficient: on timeout, the test sums counters and returns without joining, cancelling or establishing termination of its workers. It then executes the huge-page audit and exits. Workers set their completion flags only immediately before their own SYS_EXIT. Thus zero completion flags at the check means none had reached that completion point; it does not prove their later state or eventual completion.

Frozen `kernel/src/exec/exec_syscall.c:744` implements `sys_exit_group` as a call to `sys_exit`. Its comment that these are identical on a single-core OS is not valid for multiple live threads. `kernel/src/sched/task.c:1018–1072` marks/removes the current task and reparents children; it does not terminate every CLONE_THREAD sibling. Consequently the suite can advance with surviving workers sharing the old address space and FD table. This can contaminate later scheduling/resource conditions. Existing retained AS ownership means shared mappings need not be freed merely because their creator exits; this review does **not** establish a freed-AS use-after-free or the cause of health's incomplete exec. Broader parent/thread-list and group-exit lifetime safety needs a separate targeted regression.

## Recommendation

Stop treating this shift3 full-suite run as an E3 performance sample. Preserve it as a prerequisite failure; do not suppress the model failure, increase its guest deadline just to reach health, or classify a launched-but-unentered health program as measured.

A single icount0 support trial is defensible only as a separately declared diagnostic: hypothesis that a smaller guest-time-per-instruction shift allows the unchanged model workload to complete before its existing guest deadline. It is not a repair, not a comparable canonical wall-time result, and cannot establish support after one pass. Stop again on any predecessor failure. Specify a finite host budget before launch; do not assume the shift change guarantees timely progress.

Prefer **early isolated E6** next for the intended syscall/SPSC question. Boot directly into separately labeled GETPID/SPSC diagnostics with runnable-task identities and clear initial state, preserving the original SPSC body/threshold in the canonical image. That avoids importing known failed predecessor conditions and separates fixed-icount feasibility from full-suite side effects. Its output remains diagnostic; original full-suite BIOS/UEFI gates and all failures stay unchanged. Any conclusion still needs the council's repeated configurations and host isolation requirements.
