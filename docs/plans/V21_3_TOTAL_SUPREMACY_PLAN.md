<!--
SPDX-License-Identifier: MIT
SPDX-FileCopyrightText: 2026 VOS3 Project
-->

# VOS3 v21.3 — "Total Supremacy" Implementation Plan

**Goal:** close the four HW-activation items that separate **116/120** from **120/120** PASS.
**Baseline:** `v21.2.4-KERNEL-G1-G4-CLOSED` (HEAD `056d98b`)
**Authoring environment:** **no QEMU, no real hardware, no booted kernel**
**Validation environment:** must be reachable from a workstation with `x86_64-elf-gcc`, `qemu-system-x86_64`, and ≥4 GiB RAM
**Discipline:** **Blind-Implementation Protocol** — every change is gated behind a feature flag with a default-OFF stub. The next maintainer with hardware flips one flag at a time and runs `pytest` to verify. **No unguarded activation.**

---

## §0. Blind-Implementation Protocol (cross-cutting)

The recovery environment cannot run the kernel. The plan therefore must be:

1. **Each HW item is gated** by a `VOS3_HW_*` compile flag. Default OFF.
2. **Boot prerequisites are checked at runtime**, not assumed. If a prerequisite fails, the matching subsystem stays in scaffold mode and logs a `WARN` with the exact reason.
3. **All asm in standalone `.S` files** — never inline in `.c` past the existing `xsave_ctx.c` style.
4. **Each subsystem exports `*_self_test()`** that the future runtime harness (HW-4) can call at boot before user-space starts.
5. **Hardware-touching writes are wrapped** in `vos3_mmio_write32_or_panic_in_dev()` so a wrong address triggers a serial log + halt, not a silent corruption.
6. **Every PR lands tests first**: a `test_<feature>_source_shape.py` that greps for the new symbols and structures, so a static reviewer can verify the shape is right before any boot is attempted.
7. **No commit may simultaneously enable two HW flags.** One flag per merge.

This protocol means the recovery repo can land HW-1 through HW-4 *as code* with full review, and the activation step is a one-line config change in a downstream environment.

---

## §1. HW-1 — XSAVE/XRSTOR live wiring in context switch

### Intel SDM citations (verified 2026-05-02)

| Item | Value | Citation |
|---|---|---|
| XCR0 bit 0 (`X87`) | mandatory; XSETBV faults if cleared | Intel SDM 252046-073 §13.3 |
| XCR0 bit 1 (`SSE`) | enables XMM0-XMM15 save | Intel SDM 252046-073 §13.3 |
| XCR0 bit 2 (`AVX`) | enables YMM_HI128 component | Intel SDM 252046-073 §13.3 |
| XCR0 bits 3-4 (`MPX BNDREG/BNDCSR`) | MPX (deprecated post-Skylake-X) | Intel SDM 252046-073 §13.3 |
| XCR0 bits 5-7 (`AVX-512 OPMASK + ZMM_HI256 + ZMM_HI16`) | full AVX-512 = `0b111` | Intel SDM 252046-073 §13.3 |
| XCR0 bit 8 (`PT`) | Processor Trace | Intel SDM 252046-073 §13.3 |
| XCR0 bit 9 (`PKRU`) | Protection Keys | Intel SDM 252046-073 §13.3 |
| XCR0 bits 17-18 (`AMX TILECFG + TILEDATA`) | AMX state, Sapphire Rapids+ | Intel SDM 252046-074 §13 |
| CR4.OSXSAVE | bit 18 — must be set BEFORE first XSETBV | Intel SDM Vol.3A §2.5 |
| XSETBV opcode | `0F 01 D1`, ECX = XCR index, EDX:EAX = value | Intel SDM Vol.2A |
| APX state (2026 ISA) | new ISA, GPR16-31 + NDD/NF flags; XSAVE component bit subject to SDM revision; **NOT yet wired into VOS3** | Intel AVX10/APX ISA Spec |

The constants in `kernel/src/arch/x86_64/xsave.c:51-63` already match this
table for bits 0-7. Bits 17-18 (AMX) and APX state are documented but
deliberately not enabled in `VOS3_HW_XSAVE_XCR0_MASK_LO` until the
runtime code paths exercise them. This is per the activation roadmap
in `xsave.c:30-34`: AVX-512 ships in v21.4.x at the earliest, AMX/APX
deferred to v21.5+.

### Implementation notes

### Current state
- `kernel/src/sched/xsave_ctx.c:65-113` — `vos3_xsave_save` / `vos3_xsave_restore` issue real `xsave64` / `xrstor64` opcodes when `VOS3_XSAVE_LIVE` is defined; otherwise return immediately.
- `kernel/src/sched/context.S` — saves/restores 6 callee-saved GPRs only; does not call `vos3_xsave_*`.
- `kernel/src/arch/x86_64/xsave.c` (178 lines) — XCR0 helpers exist; `xsave_area_size_for_mask()` present.
- Boot path: CR4.OSXSAVE is **not** set; XCR0 is **not** programmed.

### Files to modify

| File | Change |
|---|---|
| `kernel/src/boot/boot_init.c` (or wherever CR4 is initially programmed) | After CR4.OSFXSR is set, also set CR4.OSXSAVE (bit 18) IF CPUID.1:ECX.XSAVE (bit 26) is 1, then write XCR0 with the desired component mask. |
| `kernel/src/sched/task.c` | In `vos3_task_create()`: allocate aligned `xsave_area` (64-byte aligned, sized via `xsave_area_size_for_mask(VOS3_XSAVE_DEFAULT_MASK_LO, VOS3_XSAVE_DEFAULT_MASK_HI)`). Free in `vos3_task_destroy()`. |
| `kernel/include/vos/task.h` | Add `void* xsave_area;` + `uint32_t xsave_area_size;` to `vos3_task_t` (place after `kernel_stack_size`, in the existing 8-byte-aligned section). |
| `kernel/src/sched/context.S` | Wrap the existing GPR push/pop with `call vos3_xsave_save` (before push) and `call vos3_xsave_restore` (after pop). The xsave_area pointer comes from the task struct. |
| `kernel/Makefile` | Conditional `CFLAGS += -DVOS3_XSAVE_LIVE` when `VOS3_HW_XSAVE=1` is passed via env. |

### Code blueprint — boot prerequisite (boot_init.c)

```c
/* HW-1 boot prerequisite. Caller must have already set CR4.OSFXSR. */
int vos3_xsave_boot_init(void) {
    uint32_t a, b, c, d;
    apic_cpuid(1, &a, &b, &c, &d);
    if (!(c & (1u << 26))) {                          /* CPUID.1:ECX.XSAVE */
        VOS3_WARN("[XSAVE] CPU does not advertise XSAVE — staying inert");
        return -ENODEV;
    }
    /* CR4.OSXSAVE = bit 18 */
    uint64_t cr4 = vos3_read_cr4();
    vos3_write_cr4(cr4 | (1ull << 18));

    /* Program XCR0: x87 (bit 0) + SSE (bit 1) at minimum.
     * AVX (bit 2), AVX-512 (bits 5/6/7) only if CPUID advertises them.
     * vos3_xsave_compute_default_mask() does the CPUID->mask conversion. */
    uint64_t xcr0 = vos3_xsave_compute_default_mask();
    vos3_xsetbv(0, xcr0);

    VOS3_INFO("[XSAVE] OSXSAVE on, XCR0 = 0x%llx", (unsigned long long)xcr0);
    return 0;
}
```

### Code blueprint — context.S addition

```asm
/* HW-1: extended state save/restore, gated behind VOS3_XSAVE_LIVE.
 * The C-side stub is inert when the flag is undefined, so this call
 * is safe to leave in unconditionally — the cost is one no-op call. */
vos3_context_switch:
#ifdef VOS3_XSAVE_LIVE
    /* Save extended state of OUTGOING task BEFORE GPR push.
     * rdi = old_ctx pointer; offset of xsave_area within task is
     * exposed via VOS3_TASK_OFFSETOF_XSAVE_AREA (assembled constant). */
    testq %rdi, %rdi
    jz    .Lxsave_skip_save
    movq  VOS3_TASK_OFFSETOF_XSAVE_AREA(%rdi), %rcx   /* area ptr */
    testq %rcx, %rcx
    jz    .Lxsave_skip_save                            /* still scaffold */
    pushq %rdi                                         /* preserve old_ctx */
    pushq %rsi                                         /* preserve new_ctx */
    movq  %rcx, %rdi                                   /* arg1 = area */
    xorl  %esi, %esi                                   /* arg2/3 = 0 → defaults */
    xorl  %edx, %edx
    call  vos3_xsave_save
    popq  %rsi
    popq  %rdi
.Lxsave_skip_save:
#endif
    /* ... existing GPR push code ... */
```

The symmetric `vos3_xsave_restore` call goes after the GPR pop, before `ret`.

### Self-test (lands first, before activation)

`kernel/src/sched/xsave_self_test.c` — calls `vos3_xsave_save` on a 64-byte-aligned 4-KiB buffer twice, asserts the second `xsave_area` matches the first byte-for-byte (because no FP/SSE state changed between calls). Inert when flag is off.

### Validation gate

```bash
make VOS3_BUILD_TYPE=PRO VOS3_HW_XSAVE=1
qemu-system-x86_64 -kernel build/vos3.elf -m 4096M -smp 2 -cpu max -serial stdio | \
    grep -E "^\[XSAVE\] (OSXSAVE on|self_test PASS)"
```

Expected output: `[XSAVE] OSXSAVE on, XCR0 = 0x7` (or higher) followed by `[XSAVE] self_test PASS`.

### Risk-acceptance contract
If the boot prerequisite fails (CPU does not advertise XSAVE), the kernel stays in scaffold mode. Triple-fault risk is structurally impossible because the activation is gated on the CPUID advertisement.

---

## §2. HW-2 — Local APIC MMIO mapping + ICR write path

### Intel SDM citations (verified 2026-05-02)

#### xAPIC mode (current `apic.c` implementation)

| Item | Value | Citation |
|---|---|---|
| LAPIC MMIO base | from `IA32_APIC_BASE` MSR (0x1B), bits [51:12] | Intel SDM Vol.3A §10.4.4 |
| ICR_LOW | MMIO offset `+0x300` (32-bit) | Intel SDM Vol.3A §10.6.1 |
| ICR_HIGH | MMIO offset `+0x310` (32-bit), dest in bits [31:24] for 8-bit APIC ID | Intel SDM Vol.3A §10.6.1 |
| Vector | ICR_LOW bits [7:0] | Intel SDM Vol.3A §10.6.1 |
| Delivery mode | ICR_LOW bits [10:8] (0=Fixed, 1=LowestPri, 2=SMI, 4=NMI, 5=INIT, 6=SIPI) | Intel SDM Vol.3A §10.6.1 |
| Destination shorthand | ICR_LOW bits [19:18] (00=None, 01=Self, 10=All-incl-self, 11=All-excl-self) | Intel SDM Vol.3A §10.6.1 |
| SPIV register | MMIO offset `+0xF0`, bit 8 = APIC enable | Intel SDM Vol.3A §10.9 |
| LVT entries (mask bit 16) | offsets 0x320 (timer), 0x330 (thermal), 0x340 (perf), 0x350/0x360 (LINT0/1), 0x370 (error) | Intel SDM Vol.3A §10.5.1 |

#### x2APIC mode (planned for v21.4+)

| Item | Value | Citation |
|---|---|---|
| ICR (single 64-bit) | MSR `0x830` — replaces xAPIC ICR_HIGH/LOW pair | Intel SDM Vol.3A §10.12.9 |
| Destination ID | ICR bits [63:32] — 32-bit (vs 8-bit in xAPIC) | Intel SDM Vol.3A §10.12.9 |
| Vector | ICR bits [7:0] | Intel SDM Vol.3A §10.12.9 |
| Delivery mode | ICR bits [10:8] | Intel SDM Vol.3A §10.12.9 |
| Destination shorthand | ICR bits [19:18], `0b11` = ALL-EXCLUDING-SELF | Intel SDM Vol.3A §10.12.9 |
| Mode enable | `IA32_APIC_BASE.EXTD` (bit 10) — set before MSR access | Intel® 64 Architecture x2APIC Spec 318148-004 |

The current `apic.c` uses xAPIC MMIO. The x2APIC migration is a separate
v21.4.x item (replaces MMIO writes with `wrmsr 0x830`). The constants
in `apic.c:79-94` already match the xAPIC table above. ALL-EXCLUDING-SELF
delivery (`VOS3_APIC_ICR_DEST_ALL_EXC = 3u << 18`) matches both layouts —
the bit position is preserved across modes.

### Implementation notes

### Current state
- `kernel/src/arch/x86_64/apic.c:124-150` — CPUID + MSR_APIC_BASE detect; `g_apic_base_phys` populated.
- `g_apic_base_va` stays NULL; ICR opcodes defined as constants but never written.
- `kernel/src/drivers/acpi.c:263-298` — MADT parser **already populates** `g_acpi_info.lapic_addr`. **This work is done.**
- `vmm.c` exports `vmm_map_pages()` for VA-PA mapping.

### Files to modify

| File | Change |
|---|---|
| `kernel/src/arch/x86_64/apic.c` | Add `vos3_apic_init_mmio()` that reads `g_acpi_info.lapic_addr`, calls `vmm_map_pages(VOS3_APIC_VA_BASE, lapic_addr, 1, MMIO_FLAGS)`, stores VA in `g_apic_base_va`. Add `vos3_apic_init_lvt()` that writes SPIV at `+0xF0` with vector 0xFF | enable bit. Implement real `vos3_apic_send_resched_ipi()` writing ICR_HIGH then ICR_LOW. |
| `kernel/include/vos/memory_map.h` | Reserve VA `0xFFFF_FF00_FEE0_0000` (or another VMM-safe slot) for LAPIC MMIO via `VOS3_APIC_VA_BASE`. |
| `kernel/src/arch/x86_64/idt.c` | Register vector 0xFF (spurious) and 0xF0 (resched) when `VOS3_HW_LAPIC=1`. |
| `kernel/Makefile` | `-DVOS3_HW_LAPIC` guard. |

### Code blueprint — MMIO map

```c
/* HW-2: map the LAPIC physical base into kernel virtual memory.
 * Prerequisites: ACPI parsed (g_acpi_info.lapic_addr non-zero), VMM
 * initialized. Returns 0 on success, -EINVAL if prereqs unmet. */
int vos3_apic_init_mmio(void) {
#ifndef VOS3_HW_LAPIC
    VOS3_INFO("[APIC] MMIO mapping skipped — VOS3_HW_LAPIC not defined");
    return -ENODEV;
#else
    if (g_acpi_info.lapic_addr == 0ull) {
        VOS3_WARN("[APIC] ACPI MADT parse did not yield a LAPIC base — "
                  "cannot map MMIO");
        return -EINVAL;
    }

    /* Standard LAPIC MMIO is 4 KiB, strong-uncacheable. */
    int rc = vmm_map_pages(VOS3_APIC_VA_BASE,
                           g_acpi_info.lapic_addr,
                           /*pages=*/1,
                           VOS3_VMM_FLAG_MMIO | VOS3_VMM_FLAG_NX);
    if (rc != 0) {
        VOS3_ERROR("[APIC] vmm_map_pages failed: %d", rc);
        return rc;
    }
    g_apic_base_va = (volatile uint32_t *)(uintptr_t)VOS3_APIC_VA_BASE;
    VOS3_INFO("[APIC] MMIO mapped: phys=0x%llx → va=0x%llx",
              (unsigned long long)g_acpi_info.lapic_addr,
              (unsigned long long)VOS3_APIC_VA_BASE);
    return 0;
#endif
}

/* HW-2: program SPIV (Spurious Interrupt Vector). MUST be called
 * before any other LAPIC register write. */
int vos3_apic_init_lvt(void) {
    if (g_apic_base_va == ((void *)0)) return -EINVAL;
    /* SPIV register at +0xF0:
     *   bits [7:0]  = spurious vector (0xFF)
     *   bit  8      = APIC software enable
     */
    g_apic_base_va[0xF0u >> 2] = 0x1FFu;
    /* Mask all LVT entries until specific drivers configure them.
     * (LVT_TIMER=+0x320, LVT_LINT0=+0x350, LVT_LINT1=+0x360,
     *  LVT_ERROR=+0x370, LVT_THERMAL=+0x330, LVT_PERF=+0x340)
     * Setting bit 16 (mask) is the safe default. */
    static const unsigned lvt_offsets[] = { 0x320, 0x330, 0x340, 0x350, 0x360, 0x370 };
    for (size_t i = 0; i < sizeof(lvt_offsets)/sizeof(lvt_offsets[0]); i++) {
        g_apic_base_va[lvt_offsets[i] >> 2] = (1u << 16);
    }
    return 0;
}

/* HW-2: send IPI to a single CPU. Replaces the -ENODEV stub. */
int vos3_apic_send_resched_ipi(uint32_t cpu) {
    if (g_apic_base_va == ((void *)0)) return -ENODEV;

    /* ICR_HIGH carries dest CPU APIC ID in bits [31:24]. */
    g_apic_base_va[VOS3_APIC_REG_ICR_HIGH >> 2] = (cpu & 0xFFu) << 24;

    /* ICR_LOW carries vector + delivery mode + destination shorthand. */
    uint32_t icr_low = VOS3_APIC_VECTOR_RESCHED
                     | VOS3_APIC_ICR_FIXED
                     | VOS3_APIC_ICR_PHYSICAL
                     | VOS3_APIC_ICR_ASSERT
                     | VOS3_APIC_ICR_DEST_NO_SHORT;
    g_apic_base_va[VOS3_APIC_REG_ICR_LOW >> 2] = icr_low;

    /* Wait up to 1024 cycles for delivery. */
    for (int i = 0; i < 1024; i++) {
        if (!(g_apic_base_va[VOS3_APIC_REG_ICR_LOW >> 2] &
              VOS3_APIC_ICR_DELIV_PEND)) {
            return 0;
        }
        __asm__ volatile ("pause");
    }
    return -EBUSY;
}
```

### Self-test
`vos3_apic_self_test()` — sends a resched IPI to its own APIC ID via `VOS3_APIC_ICR_DEST_SELF`, increments a counter in the IDT vector handler, checks the counter incremented within 1ms.

### Validation gate
```
qemu-system-x86_64 ... -smp 4 ... | grep "^\[APIC\] self_test PASS \(4/4 CPUs\)"
```

---

## §3. HW-3 — IOAPIC redirection table

### Current state
- **No IOAPIC driver exists.**
- `kernel/include/vos/acpi.h` defines `vos3_madt_ioapic_t` struct.
- `acpi.c:298+` parses IOAPIC MADT entries (need to verify it stores them — likely populates `g_acpi_info.ioapic[]`).
- Legacy IRQs are currently handled via PIC in `kernel/src/arch/x86_64/pic.c`.

### Files to create

`kernel/src/arch/x86_64/ioapic.c` (~250 lines, new file).
`kernel/include/vos/ioapic.h` (~80 lines, new header).
`kernel/src/arch/x86_64/ioapic_self_test.c` (~60 lines).

### Files to modify

| File | Change |
|---|---|
| `kernel/Makefile` | Append `ioapic.o` to OBJECTS conditionally on `VOS3_HW_IOAPIC=1`. |
| `kernel/src/drivers/acpi.c` | If not already done — store IOAPIC entries in `g_acpi_info.ioapic[]` array (max 8). |
| `kernel/include/vos/acpi.h` | Add `vos3_acpi_ioapic_descriptor_t` if absent. |

### Code blueprint — ioapic.c (skeleton)

```c
/*
 * IOAPIC driver — programs the legacy interrupt redirection table.
 *
 * IOAPIC MMIO uses indirect register access:
 *   IOREGSEL  at +0x00  (write the register index)
 *   IOWIN     at +0x10  (read/write the selected register's value)
 *
 * Redirection table starts at register 0x10, 2 dwords per entry.
 * Entry N occupies registers (0x10 + 2*N) and (0x11 + 2*N).
 */

#include "../../../include/vos/ioapic.h"
#include "../../../include/vos/acpi.h"
#include "../../../include/vos/console.h"

#define IOAPIC_REG_ID         0x00
#define IOAPIC_REG_VER        0x01
#define IOAPIC_REG_REDTBL(n) (0x10 + (n) * 2)

#define IOAPIC_REDTBL_VECTOR_MASK   0xFFu
#define IOAPIC_REDTBL_DELIV_FIXED   (0u << 8)
#define IOAPIC_REDTBL_DEST_PHYSICAL (0u << 11)
#define IOAPIC_REDTBL_POLARITY_HIGH (0u << 13)
#define IOAPIC_REDTBL_TRIGGER_EDGE  (0u << 15)
#define IOAPIC_REDTBL_MASKED        (1u << 16)

static volatile uint32_t *g_ioapic_base_va = ((void *)0);
static uint8_t            g_ioapic_max_redir = 0;
static uint32_t           g_ioapic_gsi_base  = 0;

static inline uint32_t ioapic_read(uint8_t reg) {
    g_ioapic_base_va[0] = reg;            /* IOREGSEL */
    return g_ioapic_base_va[4];           /* IOWIN at +0x10 → index 4 in u32 array */
}

static inline void ioapic_write(uint8_t reg, uint32_t value) {
    g_ioapic_base_va[0] = reg;
    g_ioapic_base_va[4] = value;
}

int vos3_ioapic_init(void) {
#ifndef VOS3_HW_IOAPIC
    VOS3_INFO("[IOAPIC] init skipped — VOS3_HW_IOAPIC not defined");
    return -ENODEV;
#else
    if (g_acpi_info.ioapic_count == 0) {
        VOS3_WARN("[IOAPIC] ACPI MADT did not enumerate any IOAPIC");
        return -ENODEV;
    }

    /* Use the first IOAPIC for legacy IRQ remapping. */
    const vos3_acpi_ioapic_descriptor_t *first = &g_acpi_info.ioapic[0];

    int rc = vmm_map_pages(VOS3_IOAPIC_VA_BASE,
                           first->address,
                           1, VOS3_VMM_FLAG_MMIO | VOS3_VMM_FLAG_NX);
    if (rc != 0) return rc;
    g_ioapic_base_va = (volatile uint32_t *)(uintptr_t)VOS3_IOAPIC_VA_BASE;
    g_ioapic_gsi_base = first->gsi_base;

    uint32_t ver = ioapic_read(IOAPIC_REG_VER);
    g_ioapic_max_redir = (uint8_t)((ver >> 16) & 0xFFu) + 1u;

    /* Mask every redirection entry by default. */
    for (uint8_t i = 0; i < g_ioapic_max_redir; i++) {
        ioapic_write(IOAPIC_REG_REDTBL(i),     IOAPIC_REDTBL_MASKED);
        ioapic_write(IOAPIC_REG_REDTBL(i) + 1, 0u);  /* dest = APIC ID 0 */
    }

    VOS3_INFO("[IOAPIC] initialized: base=0x%llx, %u redirection entries",
              (unsigned long long)first->address, g_ioapic_max_redir);
    return 0;
#endif
}

/* Program a single IRQ → vector mapping with the given target CPU. */
int vos3_ioapic_program(uint8_t irq, uint8_t vector, uint8_t target_cpu) {
    if (g_ioapic_base_va == ((void *)0)) return -ENODEV;
    if (irq >= g_ioapic_max_redir) return -EINVAL;

    uint32_t low  = (uint32_t)vector
                  | IOAPIC_REDTBL_DELIV_FIXED
                  | IOAPIC_REDTBL_DEST_PHYSICAL
                  | IOAPIC_REDTBL_POLARITY_HIGH
                  | IOAPIC_REDTBL_TRIGGER_EDGE;
                  /* mask bit clear → unmasked */
    uint32_t high = ((uint32_t)target_cpu & 0xFFu) << 24;

    /* Write high first so an in-flight interrupt does not see a half-
     * programmed entry routed to CPU 0 with the new vector. */
    ioapic_write(IOAPIC_REG_REDTBL(irq) + 1, high);
    ioapic_write(IOAPIC_REG_REDTBL(irq),     low);
    return 0;
}
```

### Boot-time wiring (call site)

```c
/* In kernel_main() after vos3_apic_init_lvt(): */
if (vos3_ioapic_init() == 0) {
    /* Standard PC ISA IRQ → vector mapping (IOAPIC GSI = ISA IRQ). */
    vos3_ioapic_program(0,  0x20, /*cpu=*/0);  /* PIT */
    vos3_ioapic_program(1,  0x21, /*cpu=*/0);  /* keyboard */
    vos3_ioapic_program(4,  0x24, /*cpu=*/0);  /* COM1 */
    vos3_ioapic_program(14, 0x2E, /*cpu=*/0);  /* IDE primary */
    /* The PIC must be MASKED before the IOAPIC takes over. */
    vos3_pic_mask_all();
}
```

### Self-test
`vos3_ioapic_self_test()` — programs IRQ 0 to vector 0x20, registers a counter handler, waits one PIT tick (~10ms), asserts counter incremented.

### Validation gate
```
qemu-system-x86_64 ... -d int | grep "^v=20" | head -3
```
Expect at least 3 vector-0x20 deliveries within 30ms.

---

## §4. HW-4 — 1340-ASSERT runnable benchmark suite

### Current state
- **1,548** `VOS3_ASSERT*` call sites compiled into the kernel (`grep -c` over `kernel/src/`).
- No QEMU runner; no JUnit emitter.
- `tools/check_open_core_split.sh` exists for static checks; nothing for runtime asserts.

### Architecture

```
                                  ┌──────────────────────┐
        kernel build              │  qemu_runner.py      │
        (vos3.elf w/ -D           │  (pytest collector)  │
        VOS3_ASSERT_HARNESS=1)    └─────────┬────────────┘
                                            │
                                  ┌─────────▼────────────┐
        spawns QEMU with          │  qemu-system-x86_64  │
        -kernel build/vos3.elf    │  -serial pipe:fifo   │
        -smp 2 -m 4G              │  -nographic          │
        -nographic                │  -no-reboot          │
                                  └─────────┬────────────┘
                                            │ serial fifo
                                  ┌─────────▼────────────┐
                                  │  Line parser         │
                                  │  /^ASSERT\[(\d+)\]   │
                                  │   (PASS|FAIL):(.*)$/ │
                                  └─────────┬────────────┘
                                            │
                                  ┌─────────▼────────────┐
                                  │  pytest test for     │
                                  │  each ASSERT line    │
                                  │  → JUnit XML         │
                                  └──────────────────────┘
```

### Files to create

`tools/runner/qemu_assert_runner.py` (~200 lines).
`tools/runner/junit_emit.py` (~80 lines).
`backend/tests/audit/test_runtime_asserts.py` (~150 lines, pytest harness).
`kernel/src/diag/assert_emit.c` (~120 lines, kernel-side assertion serial-write wrapper).
`kernel/include/vos/assert_emit.h` (~40 lines).

### Kernel-side: assert emit format

```c
/* HW-4: serial-port emit for VOS3_ASSERT* under VOS3_ASSERT_HARNESS.
 * Format: "ASSERT[<id>] <PASS|FAIL>: <file>:<line>:<expr>\n"
 *
 * <id> is monotonic, generated at compile time via __COUNTER__ if available
 * or manually-numbered for fallback. The harness collects unique IDs and
 * complains on duplicates or gaps.
 */
#define VOS3_ASSERT_ID(id, expr) \
    do { \
        if (!(expr)) { \
            vos3_assert_emit(id, 0, __FILE__, __LINE__, #expr); \
            vos3_panic("ASSERT FAIL"); \
        } else { \
            vos3_assert_emit(id, 1, __FILE__, __LINE__, #expr); \
        } \
    } while (0)
```

The existing `VOS3_ASSERT(...)` macro stays untouched; the new `VOS3_ASSERT_ID` is *additive* and only used at canonical certification points (so we don't blow the serial budget with 1,548 emits — only the ~120 designated certification asserts).

### Python-side: runner skeleton

```python
# tools/runner/qemu_assert_runner.py
import re, subprocess, time, os, signal
from dataclasses import dataclass

@dataclass
class AssertResult:
    id: int
    passed: bool
    site: str       # "file.c:line"
    expr: str

ASSERT_RE = re.compile(r"^ASSERT\[(\d+)\]\s+(PASS|FAIL):\s+([^:]+:\d+):(.*)$")

def run(elf_path: str, timeout_s: int = 60) -> list[AssertResult]:
    cmd = [
        "qemu-system-x86_64",
        "-kernel", elf_path,
        "-m", "4096M", "-smp", "2",
        "-cpu", "max",
        "-nographic", "-no-reboot",
        "-serial", "stdio",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    try:
        results: list[AssertResult] = []
        deadline = time.time() + timeout_s
        for line in proc.stdout:
            m = ASSERT_RE.match(line.strip())
            if m:
                results.append(AssertResult(
                    id=int(m.group(1)),
                    passed=(m.group(2) == "PASS"),
                    site=m.group(3),
                    expr=m.group(4).strip(),
                ))
            if "VOS3_ASSERT_HARNESS_DONE" in line:
                break
            if time.time() > deadline:
                break
        return results
    finally:
        try: proc.send_signal(signal.SIGTERM)
        except Exception: pass
        proc.wait(timeout=5)
```

### Pytest integration

```python
# backend/tests/audit/test_runtime_asserts.py
import pytest
from tools.runner.qemu_assert_runner import run

ELF = "kernel/build/vos3.elf"

@pytest.fixture(scope="module")
def assert_results():
    if not shutil.which("qemu-system-x86_64"):
        pytest.skip("QEMU not available — runtime assert suite needs qemu-system-x86_64")
    return run(ELF)

def test_no_duplicate_ids(assert_results):
    ids = [r.id for r in assert_results]
    dups = [i for i in set(ids) if ids.count(i) > 1]
    assert not dups, f"duplicate ASSERT IDs: {dups[:10]}"

def test_no_id_gaps(assert_results):
    ids = sorted({r.id for r in assert_results})
    if not ids: pytest.skip("no asserts emitted")
    gaps = [(a, b) for a, b in zip(ids, ids[1:]) if b - a > 1]
    assert not gaps, f"ID gaps suggest missed emit calls: {gaps[:5]}"

@pytest.mark.parametrize("expected_min", [120])
def test_assert_count_at_least(assert_results, expected_min):
    assert len(assert_results) >= expected_min, \
        f"expected ≥{expected_min} certification asserts, got {len(assert_results)}"

def test_zero_failures(assert_results):
    failures = [r for r in assert_results if not r.passed]
    assert not failures, "\n".join(f"  {r.site}: {r.expr}" for r in failures[:10])
```

### Self-test
The harness itself includes 3 deliberately-passing and 1 deliberately-failing canary assert, gated behind `VOS3_ASSERT_HARNESS_CANARY=1`. Running the canary build expects 1 failure; if the runner reports 0 failures it means the parser is broken.

### Validation gate
```
make VOS3_BUILD_TYPE=PRO VOS3_ASSERT_HARNESS=1
pytest backend/tests/audit/test_runtime_asserts.py -v
```
Expected: green; the test_assert_count_at_least with `expected_min=120` is the audit-doc anchor that turns the 116/120 → 120/120 line truthful (because the harness ran and produced the count, not because someone typed it).

---

## §5. Sequencing and milestones

| Milestone | Tag | Includes | Boots |
|---|---|---|---|
| **M1** | `v21.3.0-M1-XSAVE` | HW-1 code merged, `VOS3_HW_XSAVE` flag still default-OFF, self-test compiles | Same as v21.2.4 (no behavior change) |
| **M2** | `v21.3.0-M2-LAPIC` | HW-2 code merged, `VOS3_HW_LAPIC` flag default-OFF | Same |
| **M3** | `v21.3.0-M3-IOAPIC` | HW-3 code merged, `VOS3_HW_IOAPIC` flag default-OFF | Same |
| **M4** | `v21.3.0-M4-HARNESS` | HW-4 runner + emit code; harness flag default-OFF | Same |
| **M5** | `v21.3.0-RC1` | All four flags set ON in a downstream branch; runs in QEMU; self-tests green | First runnable PRO build with all four lit |
| **M6** | `v21.3.0-CERT` | RC1 + HW-4 produces ≥120 ASSERT[id] PASS lines; audit doc §13 updated to 120/120 with the actual ELF SHA + actual count | **First time the 120/120 number is grounded in a runnable result** |

**Important:** M1-M4 are landable in this recovery environment because none of them flip a flag at build. M5-M6 require a workstation with QEMU.

---

## §6. What this plan does NOT do

- Does not promise a 120/120 result. The plan provides the *path*; a real run might surface unanticipated failures (e.g. SMP race in IOAPIC bring-up, XSAVE area size mismatch on a particular CPU, MMIO permission fault on a hypervisor that doesn't honor `VOS3_VMM_FLAG_MMIO`).
- Does not modify the audit doc score. The score moves only after M6 produces evidence.
- Does not remove any existing scaffold marker. Markers transition to `[VOS3-LIVE-V21]` only when their gating flag is `1` AND the matching self-test passes.
- Does not push to remote. Tag publication is the operator's call.

---

## §7. Reading list before starting

| File | Why |
|---|---|
| `kernel/src/sched/xsave_ctx.c:13-44` | XSAVE activation contract (read before HW-1) |
| `kernel/src/arch/x86_64/apic.c:1-44` | LAPIC activation roadmap (read before HW-2) |
| `kernel/src/drivers/acpi.c:263-300` | MADT parser (HW-2, HW-3 depend on this) |
| `kernel/include/vos/acpi.h` | Struct definitions (especially `vos3_madt_ioapic_t`) |
| `docs/audit/TOTAL_INTEGRITY_120_REPORT.md` §12 + §13 | Honest accounting norm |
| `docs/recovery/RECONCILIATION_FINAL_REPORT.md` §3.5 | Constraint table |

---

## §8. Pull-request checklist (for each milestone)

- [ ] New compile flag added to Makefile, default-OFF.
- [ ] Source-shape pytest grep test added (`test_<feature>_source_shape.py`).
- [ ] Self-test C file added; inert when flag is off; produces `[<SUBSYS>] self_test PASS` on success.
- [ ] Existing certified tests: 93/112 baseline must not regress (clean recovery state, before any HW gaps were closed).
- [ ] `make VOS3_BUILD_TYPE=PRO` succeeds clean.
- [ ] `make audit-no-memcmp-in-crypto` passes (added by G2 in `056d98b`).
- [ ] Commit body documents: which flag, which prerequisites, which self-test, what the validation gate looks like.
- [ ] `TOTAL_INTEGRITY_120_REPORT.md` §13 NOT modified (score change is M6 only).

---

The bridge is documented. The implementation is sequenced. The path from 116/120 to 120/120 runs through M1-M6 in that order, and **only M6 may touch the score**.
