# Native process-isolation test plan

Status: the four direct CPU access cases are now implemented in a gated native ELF.
See the [qualification report](native-isolation-qualification.md) for actual
build/boot evidence and remaining scope. The original design below is retained;
the final implementation forks attackers from the coordinator, which never maps
the victim page, and obtains effective user-root permissions from fault diagnostics.

## Existing mechanisms and limits

- `user/src/bench_app_isolation_test.c` and its explicit rule at `user/Makefile:763` supply a build-pattern reference, but do not prove hardware memory isolation. The bench tests context registration, permits several broad success conditions, describes its sandbox check as running without the app flag, and infers CPU tracking from getpid/yield. Do not reuse those assertions as a security oracle.
- `user/include/unistd.h` and `user/lib/syscalls.c` expose fork, execve, waitpid and pipe. `kernel/src/exec/exec_syscall.c` implements wait4 and anonymous mmap (including MAP_FIXED); mmap initially records a VMA and faults allocate the page. Use the existing native syscall wrappers and normal ELF loader.
- `kernel/src/mm/vmm.c:1091` creates separate process PML4 and user PML4 roots, clears the lower half and copies the kernel upper half. `kernel/src/sched/scheduler.c:451` switches address spaces when scheduling a different one. These are implementation facts, not runtime proof that every transition is correct.
- `kernel/src/arch/x86_64/interrupts.c:269` decodes CR2 and hardware error bits. COW and demand allocation precede terminal user faults. `vos3_vmm_is_valid_user_addr` admits the current process's heap, stack and VMA ranges: an attacker address must lie outside all of these or allocation can legitimately succeed.
- Unhandled user faults call `fault_kill_current(current, 139)`, mark the process zombie, remove it from the run queue and wake its parent. `kernel/src/exec/exec.c:801` encodes this as `(139 & 255) << 8` for waitpid. Therefore require the actual current ABI status `139 << 8`, plus correlated fault evidence; do not incorrectly require POSIX WIFSIGNALED or let a child deliberately exit 139 stand in for a real fault.

## Proposed native test

1. Launch a dedicated native coordinator ELF through the normal user loader after clean boot. Establish parent/child pipe control channels. Create victim B and attacker A as separate processes; never use threads or CLONE_VM. Fork both before B creates its secret page, so A cannot inherit it through legitimate COW. Exec workers if needed to simplify mapping layout; verify the resulting workers really enter ring 3.
2. B maps one page at a checked, page-aligned low-half test address using anonymous private MAP_FIXED, touches it, writes a synthetic canary and announces readiness/address. Select the address outside ELF, heap, stack and coordinator mappings. A must have no VMA or PTE there. A virtual pointer alone never grants access to B's physical page. In a diagnostic test build, record distinct address-space roots, B's resolved physical frame and the absence of A's mapping before the attempt; do not expose arbitrary page-table inspection as a production syscall.
3. Run separate disposable attackers for volatile read and volatile write of B's address. Print a unique pre-attempt marker. Any return from the access emits FAIL and exits with a distinct non-139 status. Parent requires the exact child PID, current fault status, matching CR2 and user-mode fault error (read versus write), and no post-access marker. B must respond to a new challenge after each attack, verify its full canary page unchanged, perform an ordinary syscall and acknowledge continued execution.
4. Repeat read and write attacks against a dedicated synthetic supervisor-only kernel page. Supply its runtime canonical virtual address from test-only boot diagnostics rather than assuming a physical or randomized kernel address. Verify supervisor permissions and mapping existence in the active roots first. Under KPTI the page may be absent from the user root; accept the appropriate non-present fault only with that mapping evidence. Never use a noncanonical address (which tests #GP), a user-mapped trampoline, or sensitive live kernel data as the target.
5. Keep a coordinator/BSP heartbeat or trusted tick/task-progress observation running. After all four attackers die, require B's final challenge, coordinator completion and subsequent BSP progress. Add a bounded deadline for every wait/read and the overall QEMU run: hangs, reboot, panic, missing fault records or missing victim acknowledgements fail the run. Preserve serial output and exact ELF/kernel/ISO hashes.

First run the smallest clean-boot configuration, then repeat the exact tests on the supported SMP BIOS/UEFI configurations. A timer tick alone is insufficient proof of victim survival. Do not conflate a legitimate COW write to an inherited private page with access to another process's unshared page: include a separate COW control if that path is being qualified.

## Implementation questions to close at the gate

Confirm the current image's user-program packaging and normal launch command before adding the ELF to the native harness; an explicit Makefile rule alone does not prove image inclusion. Confirm pipe lifecycle and wait deadlines on this kernel with a benign worker before hostile accesses. Inspect effective page permissions through every page-table level, CR3/KPTI transition and any AI-fault recovery hook for the chosen addresses. The demand allocator currently creates writable user pages without consulting VMA protection bits in this handler; PROT_NONE/mprotect qualification is a separate concern and must not be silently claimed by this test.

Passing this plan qualifies four concrete direct CPU access attempts and fault containment on the tested native images. It does not qualify DMA/IOMMU isolation, speculative side channels, arbitrary syscall pointer handling, all page-protection combinations, all hardware, or sustained SMP stress.
