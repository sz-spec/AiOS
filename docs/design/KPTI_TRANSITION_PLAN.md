# KPTI transition implementation contract

The basic transitions below are now implemented and have passed one-CPU
BIOS/UEFI boot and a complete diskless keyboard setup workflow. This contract
also records remaining qualification work; it does not certify full isolation.

The earlier failing syscall loaded the boot kernel CR3, losing the executing
process's mappings. Each process now owns full and restricted roots, and each
CPU has a GS-addressed entry structure and independent VMM binding. The
implementation connects these pieces as follows; review each invariant as
concurrent workloads and stronger restricted-root mappings are introduced.

1. Extend the C/assembly entry ABI with CPU-local full/restricted CR3 values,
   a trampoline stack top and scratch registers, retaining compile-time
   offset assertions. Never replace boot-global CR3 values with mutable
   process values in global variables: that races between CPUs.
2. Bind the selected process's full root during VMM/scheduler/exec switches.
   A kernel-mode syscall handler needs the executing process's user mappings
   in that full root for uaccess. A missing required root must fail closed.
3. Use CPU-local trampoline storage on both sides of CR3 changes. SYSCALL
   must load the full CR3 before touching its task's dynamic kernel stack.
   Interrupts entering from user mode need TSS.RSP0 on the trampoline, then
   move the saved frame to the selected task's stack before C dispatch or
   enabling preemption. Do not map every task stack into every restricted root.
4. Route syscall return, IRQ return, initial exec and fork/clone return
   through a shared, register-preserving trampoline. Copy the IRET frame to
   mapped trampoline storage before loading restricted CR3. Never read the
   old task stack afterward. Preserve return values, all user registers,
   GS state and interrupt state; review nested faults/NMI/SWAPGS windows.
5. Synchronize the current process's lower PML4 entries into its restricted
   root before returning to user mode, with ownership/locking suitable for
   shared-VM threads. New mmap/exec/fork mappings must be visible without
   copying another process's user entries. Restricted roots share lower
   tables; destruction must not release those tables twice.
6. Retain explicit evidence of remaining exposure. The current boot root
   keeps whole kernel-image and direct-physical-map PML4 entries; a functioning
   syscall with that arrangement is still only partial isolation. Full
   qualification requires mapping only entry code/data, descriptor tables
   and dedicated interrupt/trampoline stacks in the restricted root, including
   correct access permissions and TLB/PCID handling.

Required runtime gates include real setup-wizard output, repeated syscalls,
timer IRQs during user execution, signal delivery/return, exec/fork/clone,
FS/GS state across context switches, concurrent processes and shared-VM threads,
and allocation/revocation/teardown failures. Run with and without optional
CPU features, then BIOS/UEFI and multiple CPUs. The existing two-CPU startup
failure is a separate gate and has been reproduced on the pre-change image.
