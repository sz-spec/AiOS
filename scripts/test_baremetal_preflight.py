#!/usr/bin/env python3
"""Compile and exercise the production CPU preflight evaluator."""

import subprocess
import tempfile
import unittest
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BaremetalPreflightTests(unittest.TestCase):
    def test_mandatory_and_optional_feature_matrix(self):
        production = (ROOT / "kernel/src/boot/baremetal_preflight.c").read_text()
        production = production.replace(
            "#include <vos/baremetal_preflight.h>", ""
        )
        source = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>

#define VOS3_CPUID_FEATURES 1U
#define VOS3_CPUID_EXTENDED_FEAT 7U
#define VOS3_CPUID_EXT_MAX 0x80000000U
#define VOS3_CPUID_EXT_FEATURES 0x80000001U
#define VOS3_CPU_FEAT_FPU (1U << 0)
#define VOS3_CPU_FEAT_TSC (1U << 4)
#define VOS3_CPU_FEAT_MSR (1U << 5)
#define VOS3_CPU_FEAT_PAE (1U << 6)
#define VOS3_CPU_FEAT_PGE (1U << 13)
#define VOS3_CPU_FEAT_PAT (1U << 16)
#define VOS3_CPU_FEAT_FXSR (1U << 24)
#define VOS3_CPU_FEAT_SSE (1U << 25)
#define VOS3_CPU_FEAT_SSE2 (1U << 26)
#define VOS3_CPU_FEAT_PCID (1U << 17)
#define VOS3_CPU_FEAT_X2APIC (1U << 21)
#define VOS3_CPU_FEAT_NX (1U << 20)
#define VOS3_CPU_FEAT_SYSCALL (1U << 11)
#define VOS3_CPU_FEAT_LM (1U << 29)
#define VOS3_CPU_EXT7_INVPCID (1U << 10)
#define VOS3_CPU_EXT7_SHA (1U << 29)

typedef struct vos3_cpu_info {
    uint32_t features_edx, features_ecx, ext_features_edx;
    uint32_t ext7_ebx, max_std_leaf, max_ext_leaf;
} vos3_cpu_info_t;
typedef enum vos3_preflight_status {
    VOS3_PREFLIGHT_OK = 0, VOS3_PREFLIGHT_NO_CPUID = -1,
    VOS3_PREFLIGHT_NO_FPU = -2, VOS3_PREFLIGHT_NO_SSE = -3,
    VOS3_PREFLIGHT_NO_SSE2 = -4, VOS3_PREFLIGHT_NO_NX = -5,
    VOS3_PREFLIGHT_NO_TSC = -6, VOS3_PREFLIGHT_INVALID = -7,
    VOS3_PREFLIGHT_NO_MSR = -8, VOS3_PREFLIGHT_NO_PAE = -9,
    VOS3_PREFLIGHT_NO_PGE = -10, VOS3_PREFLIGHT_NO_PAT = -11,
    VOS3_PREFLIGHT_NO_FXSR = -12, VOS3_PREFLIGHT_NO_SYSCALL = -13,
    VOS3_PREFLIGHT_NO_LONG_MODE = -14,
} vos3_preflight_status_t;
typedef struct vos3_preflight_features {
    uint8_t pcid_supported, invpcid_supported, sha_ni_supported;
    uint8_t x2apic_supported, reserved[4];
} vos3_preflight_features_t;

static vos3_cpu_info_t detected;
void vos3_cpu_detect_features(vos3_cpu_info_t *out) { *out = detected; }

''' + production + r'''

static vos3_cpu_info_t good_cpu(void) {
    vos3_cpu_info_t cpu;
    memset(&cpu, 0, sizeof(cpu));
    cpu.max_std_leaf = VOS3_CPUID_EXTENDED_FEAT;
    cpu.max_ext_leaf = VOS3_CPUID_EXT_FEATURES;
    cpu.features_edx = VOS3_CPU_FEAT_FPU | VOS3_CPU_FEAT_TSC |
                       VOS3_CPU_FEAT_MSR | VOS3_CPU_FEAT_PAE |
                       VOS3_CPU_FEAT_PGE | VOS3_CPU_FEAT_PAT |
                       VOS3_CPU_FEAT_FXSR | VOS3_CPU_FEAT_SSE |
                       VOS3_CPU_FEAT_SSE2;
    cpu.features_ecx = VOS3_CPU_FEAT_PCID | VOS3_CPU_FEAT_X2APIC;
    cpu.ext_features_edx = VOS3_CPU_FEAT_SYSCALL | VOS3_CPU_FEAT_NX |
                           VOS3_CPU_FEAT_LM;
    cpu.ext7_ebx = VOS3_CPU_EXT7_INVPCID | VOS3_CPU_EXT7_SHA;
    return cpu;
}

int main(void) {
    vos3_preflight_features_t f;
    vos3_cpu_info_t cpu = good_cpu();
    assert(vos3_baremetal_preflight_evaluate(NULL, &f) == VOS3_PREFLIGHT_INVALID);
    assert(vos3_baremetal_preflight_evaluate(&cpu, &f) == VOS3_PREFLIGHT_OK);
    assert(f.pcid_supported && f.invpcid_supported &&
           f.sha_ni_supported && f.x2apic_supported);

    struct { uint32_t bit; int error; } mandatory[] = {
        { VOS3_CPU_FEAT_FPU, VOS3_PREFLIGHT_NO_FPU },
        { VOS3_CPU_FEAT_TSC, VOS3_PREFLIGHT_NO_TSC },
        { VOS3_CPU_FEAT_MSR, VOS3_PREFLIGHT_NO_MSR },
        { VOS3_CPU_FEAT_PAE, VOS3_PREFLIGHT_NO_PAE },
        { VOS3_CPU_FEAT_PGE, VOS3_PREFLIGHT_NO_PGE },
        { VOS3_CPU_FEAT_PAT, VOS3_PREFLIGHT_NO_PAT },
        { VOS3_CPU_FEAT_FXSR, VOS3_PREFLIGHT_NO_FXSR },
        { VOS3_CPU_FEAT_SSE, VOS3_PREFLIGHT_NO_SSE },
        { VOS3_CPU_FEAT_SSE2, VOS3_PREFLIGHT_NO_SSE2 },
    };
    for (unsigned i = 0; i < sizeof(mandatory) / sizeof(mandatory[0]); i++) {
        cpu = good_cpu();
        memset(&f, 0xA5, sizeof(f));
        cpu.features_edx &= ~mandatory[i].bit;
        assert(vos3_baremetal_preflight_evaluate(&cpu, &f) == mandatory[i].error);
        for (unsigned j = 0; j < sizeof(f); j++)
            assert(((unsigned char *)&f)[j] == 0);
    }

    cpu = good_cpu();
    cpu.max_std_leaf = 0;
    assert(vos3_baremetal_preflight_evaluate(&cpu, &f) == VOS3_PREFLIGHT_NO_CPUID);
    cpu = good_cpu();
    cpu.max_ext_leaf = VOS3_CPUID_EXT_MAX;
    assert(vos3_baremetal_preflight_evaluate(&cpu, &f) == VOS3_PREFLIGHT_NO_NX);
    cpu = good_cpu();
    cpu.ext_features_edx &= ~VOS3_CPU_FEAT_NX;
    assert(vos3_baremetal_preflight_evaluate(&cpu, &f) == VOS3_PREFLIGHT_NO_NX);

    struct { uint32_t bit; int error; } extended[] = {
        { VOS3_CPU_FEAT_SYSCALL, VOS3_PREFLIGHT_NO_SYSCALL },
        { VOS3_CPU_FEAT_NX, VOS3_PREFLIGHT_NO_NX },
        { VOS3_CPU_FEAT_LM, VOS3_PREFLIGHT_NO_LONG_MODE },
    };
    for (unsigned i = 0; i < sizeof(extended) / sizeof(extended[0]); i++) {
        cpu = good_cpu();
        cpu.ext_features_edx &= ~extended[i].bit;
        assert(vos3_baremetal_preflight_evaluate(&cpu, &f) == extended[i].error);
    }

    cpu = good_cpu();
    cpu.max_std_leaf = VOS3_CPUID_FEATURES;
    cpu.features_ecx = 0;
    cpu.ext7_ebx = ~0U;
    assert(vos3_baremetal_preflight_evaluate(&cpu, &f) == VOS3_PREFLIGHT_OK);
    assert(!f.pcid_supported && !f.invpcid_supported &&
           !f.sha_ni_supported && !f.x2apic_supported);

    for (unsigned mask = 0; mask < 16; mask++) {
        cpu = good_cpu();
        cpu.features_ecx = (mask & 1 ? VOS3_CPU_FEAT_PCID : 0) |
                           (mask & 2 ? VOS3_CPU_FEAT_X2APIC : 0);
        cpu.ext7_ebx = (mask & 4 ? VOS3_CPU_EXT7_INVPCID : 0) |
                       (mask & 8 ? VOS3_CPU_EXT7_SHA : 0);
        assert(vos3_baremetal_preflight_evaluate(&cpu, &f) == VOS3_PREFLIGHT_OK);
        assert(f.pcid_supported == !!(mask & 1));
        assert(f.x2apic_supported == !!(mask & 2));
        assert(f.invpcid_supported == !!(mask & 4));
        assert(f.sha_ni_supported == !!(mask & 8));
    }

    detected = good_cpu();
    assert(vos3_baremetal_preflight(&f) == VOS3_PREFLIGHT_OK);
    assert(strcmp(vos3_preflight_status_str(VOS3_PREFLIGHT_NO_NX),
                  "NX/XD unavailable") == 0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-preflight-") as directory:
            directory = Path(directory)
            test_c = directory / "test.c"
            binary = directory / "test"
            test_c.write_text(source)
            build = subprocess.run(
                [
                    "cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    str(test_c),
                    "-o", str(binary),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run(
                [str(binary)], capture_output=True, text=True, timeout=10
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_boot_paths_gate_features_before_efer_nxe(self):
        multiboot = (ROOT / "kernel/src/boot/multiboot2_entry.S").read_text()
        trampoline = (ROOT / "kernel/src/arch/x86_64/trampoline.S").read_text()
        efi = (ROOT / "kernel/src/boot/efi_stub.c").read_text()
        self.assertLess(multiboot.index("cmpl $EXT_REQUIRED"),
                        multiboot.index("orl $(EFER_LME | EFER_NXE)"))
        self.assertLess(trampoline.index("cmpl $AP_EXT_REQUIRED"),
                        trampoline.index("orl $0x00000900"))
        self.assertLess(efi.index("efi_cpu_preflight()"),
                        efi.index("efer_lo |= (1U << 11)"))

    def test_validated_snapshot_is_not_recollected(self):
        kmain = (ROOT / "kernel/src/boot/kmain.c").read_text()
        phase10 = kmain[kmain.index("Phase 10: CPU Detection"):]
        self.assertIn("vos3_cpu_detect_microcode(&cpu_info)", phase10)
        self.assertNotIn("vos3_cpu_detect(&cpu_info)", phase10)

    def test_boot_path_masks_cannot_drift(self):
        expected_basic = {0, 4, 5, 6, 13, 16, 24, 25, 26}
        expected_extended = {11, 20, 29}
        sources = [
            (ROOT / "kernel/src/boot/multiboot2_entry.S").read_text(),
            (ROOT / "kernel/src/arch/x86_64/trampoline.S").read_text(),
            (ROOT / "kernel/src/boot/efi_stub.c").read_text(),
        ]
        names = [
            ("BASIC_REQUIRED", "EXT_REQUIRED"),
            ("AP_BASIC_REQUIRED", "AP_EXT_REQUIRED"),
            ("VOS3_EFI_BASIC_REQUIRED", "VOS3_EFI_EXT_REQUIRED"),
        ]
        for source, (basic_name, extended_name) in zip(sources, names):
            logical_source = source.replace("\\\n", " ")
            def bits(name):
                match = re.search(
                    rf"(?:\.set\s+|#define\s+){name}[^\n]*",
                    logical_source,
                )
                self.assertIsNotNone(match, name)
                return {int(bit) for bit in re.findall(r"1U?\s*<<\s*(\d+)", match.group())}
            self.assertEqual(bits(basic_name), expected_basic, basic_name)
            self.assertEqual(bits(extended_name), expected_extended, extended_name)

        evaluator = (ROOT / "kernel/src/boot/baremetal_preflight.c").read_text()
        for feature in ("FPU", "TSC", "MSR", "PAE", "PGE", "PAT", "FXSR",
                        "SSE", "SSE2", "SYSCALL", "NX", "LM"):
            self.assertIn(f"VOS3_CPU_FEAT_{feature}", evaluator)


if __name__ == "__main__":
    unittest.main()
