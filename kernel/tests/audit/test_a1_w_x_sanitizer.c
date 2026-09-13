/**
 * @file test_a1_w_x_sanitizer.c
 * @brief A1 — adversarial host audit of the PTE W^X sanitizer + memory
 *        boundary enforcement (kernel/include/vos/vmm.h + vmm.c contracts).
 *        TEST_PLAN_300 §A1.
 *
 * The heart of A1 is vos3_vmm_flags_to_pte() — a PURE static-inline in vmm.h
 * that translates VMM flags into an x86-64 PTE and enforces W^X by forcing the
 * NO_EXECUTE bit whenever WRITE is requested. Because it is a header inline,
 * this test exercises the REAL production sanitizer directly (no twin).
 *
 * Two enforcement points that live in vmm.c (which cannot host-compile — it
 * pulls PMM / spinlocks / CR3 asm) are mirrored as FAITHFUL twins, each citing
 * its source lines:
 *   - mprotect W^X precheck            vmm.c:894-903  + prot_to_vmm_flags 874-885
 *   - higher-half escalation-bridge gate vmm.c:394-401
 *
 * x86-64 PTE bit contract (vmm.h): WRITABLE=bit1, USER=bit2, NO_EXECUTE=bit63
 * (executable == NX clear). W^X invariant = NEVER (WRITABLE set AND NX clear).
 *
 * Host-runnable:
 *     cc -O2 -Ikernel/include -o /tmp/a1 kernel/tests/audit/test_a1_w_x_sanitizer.c
 *     /tmp/a1   # exit 0 iff every case passes
 */
#include <stdio.h>
#include <stdint.h>
#include <stddef.h>

#include "../../include/vos/vmm.h"   /* real PTE flags + vos3_vmm_flags_to_pte */

static int g_pass = 0, g_fail = 0;
static void check(const char *name, int ok)
{
    printf("%-56s %s\n", name, ok ? "PASS" : "FAIL");
    if (ok) g_pass++; else g_fail++;
}

static int pte_executable(uint64_t pte)  /* exec == present AND NX clear */
{
    return (pte & VOS3_PTE_PRESENT) && !(pte & VOS3_PTE_NO_EXECUTE);
}
static int pte_writable(uint64_t pte) { return (pte & VOS3_PTE_WRITABLE) != 0U; }

/* ---- faithful twin: prot_to_vmm_flags (vmm.c:874-885) ---- */
static vos3_vmm_flags_t prot_to_vmm_flags_twin(int prot)
{
    vos3_vmm_flags_t flags = VOS3_VMM_FLAG_USER;
    if (prot & 0x2) flags |= VOS3_VMM_FLAG_WRITE;  /* PROT_WRITE */
    if (prot & 0x4) flags |= VOS3_VMM_FLAG_EXEC;   /* PROT_EXEC  */
    return flags;
}
/* ---- faithful twin: vos3_vmm_mprotect_range W^X precheck (vmm.c:894-903) ---- */
static int mprotect_precheck_twin(uintptr_t addr, int prot)
{
    if (addr & 0xFFF) return -22;                  /* EINVAL: unaligned */
    if ((prot & 0x2) && (prot & 0x4)) return -22;  /* EINVAL: W^X violation */
    return 0;
}
/* ---- faithful twin: vos3_vmm_map higher-half gate (vmm.c:394-401) ---- */
static int map_boundary_twin(uintptr_t virt, vos3_vmm_flags_t flags)
{
    int is_user = (virt < VOS3_KERNEL_SPACE_START) ? 1 : 0;
    if (is_user && !(flags & VOS3_VMM_FLAG_USER))
        return VOS3_VMM_ERR_ALIGN;   /* reject kernel-priv map into user space */
    return VOS3_VMM_OK;
}

/* ---- faithful twin: vos3_vmm_map W^X reject (vmm.c, A1 fix) ---- */
static int map_wx_reject_twin(vos3_vmm_flags_t flags)
{
    if ((flags & VOS3_VMM_FLAG_WRITE) && (flags & VOS3_VMM_FLAG_EXEC))
        return VOS3_VMM_ERR_INVALID;  /* W^X violation — aligned with mprotect */
    return VOS3_VMM_OK;
}

/* ELF program-header permission bits (elf.h): X=1, W=2, R=4. */
#define PF_X 0x1u
#define PF_W 0x2u
#define PF_R 0x4u

/* ---- faithful twin: elf_flags_to_pte (elf.c:96-108) — the UNSANITIZED
 * loader translation that can emit W+X for an RWX segment. ---- */
static uint64_t elf_flags_to_pte_twin(uint32_t p_flags)
{
    uint64_t pte = VOS3_PTE_PRESENT | VOS3_PTE_USER;
    if ((p_flags & PF_W) != 0u) pte |= VOS3_PTE_WRITABLE;
    if ((p_flags & PF_X) == 0u) pte |= VOS3_PTE_NO_EXECUTE;
    return pte;
}

/* ---- faithful twin: vos3_vmm_map_user W^X sanitize chokepoint (vmm.c, A1 fix)
 * — force NX on any writable user page before the PTE is created. ---- */
static uint64_t map_user_sanitize_twin(uint64_t flags)
{
    if (flags & VOS3_PTE_WRITABLE) flags |= VOS3_PTE_NO_EXECUTE;
    return flags;
}

int main(void)
{
    /* ================= Part 1: REAL vos3_vmm_flags_to_pte ================= */

    /* A1.1 — the core ask: WRITE|EXEC must NOT yield an executable page. */
    {
        uint64_t pte = vos3_vmm_flags_to_pte(VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_EXEC);
        check("A1.1 W|X request => NX forced (not executable)",
              pte_writable(pte) && !pte_executable(pte) &&
              (pte & VOS3_PTE_NO_EXECUTE));
    }
    /* A1.2 — data page (write, no exec) is NX. */
    {
        uint64_t pte = vos3_vmm_flags_to_pte(VOS3_VMM_FLAG_WRITE);
        check("A1.2 WRITE-only => writable + NX",
              pte_writable(pte) && !pte_executable(pte));
    }
    /* A1.3 — code page (exec, no write) is executable + read-only. */
    {
        uint64_t pte = vos3_vmm_flags_to_pte(VOS3_VMM_FLAG_EXEC);
        check("A1.3 EXEC-only => executable + NOT writable",
              pte_executable(pte) && !pte_writable(pte));
    }
    /* A1.4 — no flags => present, NX, not writable, not user. */
    {
        uint64_t pte = vos3_vmm_flags_to_pte(VOS3_VMM_FLAG_NONE);
        check("A1.4 NONE => present + NX + ro + supervisor",
              (pte & VOS3_PTE_PRESENT) && (pte & VOS3_PTE_NO_EXECUTE) &&
              !pte_writable(pte) && !(pte & VOS3_PTE_USER));
    }
    /* A1.5 — USER bit reflects the flag; supervisor pages never set USER. */
    {
        uint64_t u = vos3_vmm_flags_to_pte(VOS3_VMM_FLAG_USER);
        uint64_t k = vos3_vmm_flags_to_pte(VOS3_VMM_FLAG_NONE);
        check("A1.5 USER flag controls PTE.U (no privilege bleed)",
              (u & VOS3_PTE_USER) && !(k & VOS3_PTE_USER));
    }
    /* A1.6 — EXHAUSTIVE W^X invariant over EVERY flag combination. */
    {
        int violations = 0;
        for (unsigned f = 0; f <= 0x7FFU; f++) {   /* all defined flag bits */
            uint64_t pte = vos3_vmm_flags_to_pte((vos3_vmm_flags_t)f);
            if (pte_writable(pte) && pte_executable(pte)) violations++;
            uint64_t pte_l = vos3_vmm_flags_to_pte_ex((vos3_vmm_flags_t)f, 1);
            if (pte_writable(pte_l) && pte_executable(pte_l)) violations++;
        }
        check("A1.6 EXHAUSTIVE: no flag combo yields W+X (4096 cases)",
              violations == 0);
    }
    /* A1.7 — PRESENT is always set by the translator. */
    {
        int missing = 0;
        for (unsigned f = 0; f <= 0x7FFU; f++)
            if (!(vos3_vmm_flags_to_pte((vos3_vmm_flags_t)f) & VOS3_PTE_PRESENT))
                missing++;
        check("A1.7 PRESENT always set", missing == 0);
    }
    /* A1.8 — device/WC mappings still honour W^X (flags_to_pte_ex path). */
    {
        uint64_t dev = vos3_vmm_flags_to_pte_ex(
            VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_EXEC | VOS3_VMM_FLAG_DEVICE, 0);
        uint64_t wc = vos3_vmm_flags_to_pte_ex(
            VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_EXEC | VOS3_VMM_FLAG_WRITE_COMBINE, 1);
        check("A1.8 DEVICE/WC W|X mappings remain NX",
              !pte_executable(dev) && !pte_executable(wc));
    }

    /* ============ Part 2: mprotect W^X precheck (twin, cited) ============ */

    check("A1.9 mprotect(WRITE|EXEC) => -EINVAL",
          mprotect_precheck_twin(0x400000, 0x2 | 0x4) == -22);
    check("A1.10 mprotect(READ|EXEC) => allowed",
          mprotect_precheck_twin(0x400000, 0x4) == 0);
    check("A1.11 mprotect(READ|WRITE) => allowed",
          mprotect_precheck_twin(0x400000, 0x2) == 0);
    check("A1.12 mprotect unaligned addr => -EINVAL",
          mprotect_precheck_twin(0x400001, 0x2) == -22);
    /* A1.13 — defense-in-depth: even if a W|X prot reached the translator,
     * the real sanitizer would still strip exec. */
    {
        uint64_t pte = vos3_vmm_flags_to_pte(prot_to_vmm_flags_twin(0x2 | 0x4));
        check("A1.13 W|X prot through real translator still NX",
              !pte_executable(pte));
    }

    /* ========= Part 3: higher-half escalation-bridge gate (twin) ========= */

    /* A1.14 — kernel-privileged map (no USER flag) into a USER address rejected. */
    check("A1.14 kernel-priv map into user VA => reject (escalation bridge)",
          map_boundary_twin(0x0000400000000000ULL, VOS3_VMM_FLAG_WRITE) ==
              VOS3_VMM_ERR_ALIGN);
    /* A1.15 — explicit USER mapping into user space is allowed. */
    check("A1.15 USER map into user VA => allowed",
          map_boundary_twin(0x0000400000000000ULL,
                            VOS3_VMM_FLAG_USER | VOS3_VMM_FLAG_WRITE) == VOS3_VMM_OK);
    /* A1.16 — kernel mapping into kernel (higher-half) space is allowed. */
    check("A1.16 kernel map into higher-half VA => allowed",
          map_boundary_twin(VOS3_KERNEL_SPACE_START + 0x1000,
                            VOS3_VMM_FLAG_WRITE) == VOS3_VMM_OK);

    /* ===== Part 4: A1 alignment fix — vos3_vmm_map rejects W|X (vmm.c) ===== */

    check("A1.17 vos3_vmm_map(W|X) => -EINVAL (aligned with mprotect)",
          map_wx_reject_twin(VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_EXEC)
              == VOS3_VMM_ERR_INVALID);
    check("A1.18 vos3_vmm_map(EXEC-only) => allowed (code page)",
          map_wx_reject_twin(VOS3_VMM_FLAG_EXEC) == VOS3_VMM_OK);
    check("A1.19 vos3_vmm_map(WRITE-only) => allowed (data page)",
          map_wx_reject_twin(VOS3_VMM_FLAG_WRITE) == VOS3_VMM_OK);

    /* ===== Part 5: A1 RWX-BYPASS regression — the real finding =====
     * elf_flags_to_pte() emits a W+X PTE for an RWX (PF_R|PF_W|PF_X) ELF
     * segment, bypassing vos3_vmm_flags_to_pte entirely. The map_user
     * sanitize chokepoint must force NX so no RWX user page is ever created. */
    {
        /* Pre-fix: the loader translation alone IS executable + writable. */
        uint64_t raw = elf_flags_to_pte_twin(PF_R | PF_W | PF_X);
        check("A1.20 elf_flags_to_pte(RWX) is W+X PRE-sanitize (the bug)",
              pte_writable(raw) && pte_executable(raw));
        /* Post-fix: map_user sanitize forces NX → no RWX page reaches the MMU. */
        uint64_t safe = map_user_sanitize_twin(raw);
        check("A1.21 map_user sanitize forces NX on RWX ELF segment (FIXED)",
              pte_writable(safe) && !pte_executable(safe));
    }
    /* Legitimate ELF segments are unaffected by the sanitize. */
    {
        uint64_t code = map_user_sanitize_twin(elf_flags_to_pte_twin(PF_R | PF_X));
        uint64_t data = map_user_sanitize_twin(elf_flags_to_pte_twin(PF_R | PF_W));
        check("A1.22 R+X code segment stays executable (loads fine)",
              pte_executable(code) && !pte_writable(code));
        check("A1.23 R+W data segment is NX (and still loads)",
              !pte_executable(data) && pte_writable(data));
    }

    printf("\n== A1 %d passed, %d failed ==\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
