/**
 * @file cpu.h
 * @brief VOS3 CPU Identification and Control
 *
 * @details CPU feature detection, identification, and control utilities
 *          for x86_64 processors.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_ARCH_X86_64_CPU_H
#define VOS3_ARCH_X86_64_CPU_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CPUID LEAF CONSTANTS
 * ============================================================================ */

/** @brief Basic CPUID information */
#define VOS3_CPUID_VENDOR           ((uint32_t)0x00000000U)
#define VOS3_CPUID_FEATURES         ((uint32_t)0x00000001U)
#define VOS3_CPUID_CACHE_TLB        ((uint32_t)0x00000002U)
#define VOS3_CPUID_SERIAL           ((uint32_t)0x00000003U)
#define VOS3_CPUID_CACHE_PARAMS     ((uint32_t)0x00000004U)
#define VOS3_CPUID_MONITOR          ((uint32_t)0x00000005U)
#define VOS3_CPUID_THERMAL          ((uint32_t)0x00000006U)
#define VOS3_CPUID_EXTENDED_FEAT    ((uint32_t)0x00000007U)

/** @brief Extended CPUID information */
#define VOS3_CPUID_EXT_MAX          ((uint32_t)0x80000000U)
#define VOS3_CPUID_EXT_FEATURES     ((uint32_t)0x80000001U)
#define VOS3_CPUID_BRAND1           ((uint32_t)0x80000002U)
#define VOS3_CPUID_BRAND2           ((uint32_t)0x80000003U)
#define VOS3_CPUID_BRAND3           ((uint32_t)0x80000004U)
#define VOS3_CPUID_L1_CACHE         ((uint32_t)0x80000005U)
#define VOS3_CPUID_L2_CACHE         ((uint32_t)0x80000006U)
#define VOS3_CPUID_APM              ((uint32_t)0x80000007U)
#define VOS3_CPUID_ADDR_SIZE        ((uint32_t)0x80000008U)

/* ============================================================================
 * CPU FEATURE FLAGS (CPUID.01H:EDX)
 * ============================================================================ */

#define VOS3_CPU_FEAT_FPU           (1U << 0)   /**< x87 FPU */
#define VOS3_CPU_FEAT_VME           (1U << 1)   /**< Virtual 8086 Mode */
#define VOS3_CPU_FEAT_DE            (1U << 2)   /**< Debugging Extensions */
#define VOS3_CPU_FEAT_PSE           (1U << 3)   /**< Page Size Extension */
#define VOS3_CPU_FEAT_TSC           (1U << 4)   /**< Time Stamp Counter */
#define VOS3_CPU_FEAT_MSR           (1U << 5)   /**< Model Specific Registers */
#define VOS3_CPU_FEAT_PAE           (1U << 6)   /**< Physical Address Extension */
#define VOS3_CPU_FEAT_MCE           (1U << 7)   /**< Machine Check Exception */
#define VOS3_CPU_FEAT_CX8           (1U << 8)   /**< CMPXCHG8B */
#define VOS3_CPU_FEAT_APIC          (1U << 9)   /**< APIC On-Chip */
#define VOS3_CPU_FEAT_SEP           (1U << 11)  /**< SYSENTER/SYSEXIT */
#define VOS3_CPU_FEAT_MTRR          (1U << 12)  /**< Memory Type Range Registers */
#define VOS3_CPU_FEAT_PGE           (1U << 13)  /**< Page Global Enable */
#define VOS3_CPU_FEAT_MCA           (1U << 14)  /**< Machine Check Architecture */
#define VOS3_CPU_FEAT_CMOV          (1U << 15)  /**< Conditional Move */
#define VOS3_CPU_FEAT_PAT           (1U << 16)  /**< Page Attribute Table */
#define VOS3_CPU_FEAT_PSE36         (1U << 17)  /**< 36-bit Page Size Extension */
#define VOS3_CPU_FEAT_PSN           (1U << 18)  /**< Processor Serial Number */
#define VOS3_CPU_FEAT_CLFLUSH       (1U << 19)  /**< CLFLUSH */
#define VOS3_CPU_FEAT_DS            (1U << 21)  /**< Debug Store */
#define VOS3_CPU_FEAT_ACPI          (1U << 22)  /**< Thermal Monitor and Clock */
#define VOS3_CPU_FEAT_MMX           (1U << 23)  /**< MMX */
#define VOS3_CPU_FEAT_FXSR          (1U << 24)  /**< FXSAVE/FXRSTOR */
#define VOS3_CPU_FEAT_SSE           (1U << 25)  /**< SSE */
#define VOS3_CPU_FEAT_SSE2          (1U << 26)  /**< SSE2 */
#define VOS3_CPU_FEAT_SS            (1U << 27)  /**< Self Snoop */
#define VOS3_CPU_FEAT_HTT           (1U << 28)  /**< Hyper-Threading */
#define VOS3_CPU_FEAT_TM            (1U << 29)  /**< Thermal Monitor */
#define VOS3_CPU_FEAT_PBE           (1U << 31)  /**< Pending Break Enable */

/* ============================================================================
 * CPU FEATURE FLAGS (CPUID.01H:ECX)
 * ============================================================================ */

#define VOS3_CPU_FEAT_SSE3          (1U << 0)   /**< SSE3 */
#define VOS3_CPU_FEAT_PCLMULQDQ     (1U << 1)   /**< PCLMULQDQ */
#define VOS3_CPU_FEAT_DTES64        (1U << 2)   /**< 64-bit DS Area */
#define VOS3_CPU_FEAT_MONITOR       (1U << 3)   /**< MONITOR/MWAIT */
#define VOS3_CPU_FEAT_DSCPL         (1U << 4)   /**< CPL Qualified Debug Store */
#define VOS3_CPU_FEAT_VMX           (1U << 5)   /**< Virtual Machine Extensions */
#define VOS3_CPU_FEAT_SMX           (1U << 6)   /**< Safer Mode Extensions */
#define VOS3_CPU_FEAT_EST           (1U << 7)   /**< Enhanced SpeedStep */
#define VOS3_CPU_FEAT_TM2           (1U << 8)   /**< Thermal Monitor 2 */
#define VOS3_CPU_FEAT_SSSE3         (1U << 9)   /**< SSSE3 */
#define VOS3_CPU_FEAT_CNXTID        (1U << 10)  /**< L1 Context ID */
#define VOS3_CPU_FEAT_FMA           (1U << 12)  /**< Fused Multiply-Add */
#define VOS3_CPU_FEAT_CX16          (1U << 13)  /**< CMPXCHG16B */
#define VOS3_CPU_FEAT_XTPR          (1U << 14)  /**< xTPR Update Control */
#define VOS3_CPU_FEAT_PDCM          (1U << 15)  /**< Perfmon and Debug */
#define VOS3_CPU_FEAT_PCID          (1U << 17)  /**< Process Context Identifiers */
#define VOS3_CPU_FEAT_DCA           (1U << 18)  /**< Direct Cache Access */
#define VOS3_CPU_FEAT_SSE41         (1U << 19)  /**< SSE4.1 */
#define VOS3_CPU_FEAT_SSE42         (1U << 20)  /**< SSE4.2 */
#define VOS3_CPU_FEAT_X2APIC        (1U << 21)  /**< x2APIC */
#define VOS3_CPU_FEAT_MOVBE         (1U << 22)  /**< MOVBE */
#define VOS3_CPU_FEAT_POPCNT        (1U << 23)  /**< POPCNT */
#define VOS3_CPU_FEAT_TSCDL         (1U << 24)  /**< TSC-Deadline */
#define VOS3_CPU_FEAT_AES           (1U << 25)  /**< AES */
#define VOS3_CPU_FEAT_XSAVE         (1U << 26)  /**< XSAVE */
#define VOS3_CPU_FEAT_OSXSAVE       (1U << 27)  /**< OSXSAVE */
#define VOS3_CPU_FEAT_AVX           (1U << 28)  /**< AVX */
#define VOS3_CPU_FEAT_F16C          (1U << 29)  /**< F16C */
#define VOS3_CPU_FEAT_RDRAND        (1U << 30)  /**< RDRAND */
#define VOS3_CPU_FEAT_HYPERVISOR    (1U << 31)  /**< Running in VM */

/* ============================================================================
 * EXTENDED FEATURE FLAGS (CPUID.80000001H:EDX)
 * ============================================================================ */

#define VOS3_CPU_FEAT_SYSCALL       (1U << 11)  /**< SYSCALL/SYSRET */
#define VOS3_CPU_FEAT_NX            (1U << 20)  /**< No-Execute */
#define VOS3_CPU_FEAT_PDPE1GB       (1U << 26)  /**< 1GB Pages */
#define VOS3_CPU_FEAT_RDTSCP        (1U << 27)  /**< RDTSCP */
#define VOS3_CPU_FEAT_LM            (1U << 29)  /**< Long Mode (64-bit) */

/* ============================================================================
 * STRUCTURED EXTENDED FEATURES (CPUID.07H)
 * ============================================================================ */

/** @brief CPUID.07H:EBX feature bits */
#define VOS3_CPU_EXT7_INVPCID       (1U << 10)  /**< INVPCID instruction */
#define VOS3_CPU_EXT7_AVX2          (1U << 5)   /**< AVX2 — 256-bit integer SIMD */
#define VOS3_CPU_EXT7_SMEP          (1U << 7)   /**< SMEP (Supervisor Mode Execution Prevention) */
#define VOS3_CPU_EXT7_SMAP          (1U << 20)  /**< SMAP */
#define VOS3_CPU_EXT7_AVX512F       (1U << 16)  /**< AVX-512 Foundation */

/** @brief CPUID.07H:ECX feature bits */
#define VOS3_CPU_EXT7C_UMIP         (1U << 2)   /**< UMIP (User-Mode Instruction Prevention) */
#define VOS3_CPU_EXT7C_CLDEMOTE     (1U << 25)  /**< CLDEMOTE instruction */

/** @brief CR4 register bit definitions */
#define VOS3_CR4_UMIP               (1ULL << 11)  /**< User-Mode Instruction Prevention */
#define VOS3_CR4_SMEP               (1ULL << 20)  /**< Supervisor Mode Execution Prevention */
#define VOS3_CR4_SMAP               (1ULL << 21)  /**< Supervisor Mode Access Prevention */

/* ============================================================================
 * MSR ADDRESSES
 * ============================================================================ */

#define VOS3_MSR_APIC_BASE          ((uint32_t)0x0000001BU)
#define VOS3_MSR_MTRR_CAP           ((uint32_t)0x000000FEU)
#define VOS3_MSR_SYSENTER_CS        ((uint32_t)0x00000174U)
#define VOS3_MSR_SYSENTER_ESP       ((uint32_t)0x00000175U)
#define VOS3_MSR_SYSENTER_EIP       ((uint32_t)0x00000176U)
#define VOS3_MSR_PAT                ((uint32_t)0x00000277U)
#define VOS3_MSR_EFER               ((uint32_t)0xC0000080U)
#define VOS3_MSR_STAR               ((uint32_t)0xC0000081U)
#define VOS3_MSR_LSTAR              ((uint32_t)0xC0000082U)
#define VOS3_MSR_CSTAR              ((uint32_t)0xC0000083U)
#define VOS3_MSR_SFMASK             ((uint32_t)0xC0000084U)
#define VOS3_MSR_FS_BASE            ((uint32_t)0xC0000100U)
#define VOS3_MSR_GS_BASE            ((uint32_t)0xC0000101U)
#define VOS3_MSR_KERNEL_GS_BASE     ((uint32_t)0xC0000102U)

/* ============================================================================
 * EFER FLAGS
 * ============================================================================ */

#define VOS3_EFER_SCE               (1ULL << 0)   /**< SYSCALL Enable */
#define VOS3_EFER_LME               (1ULL << 8)   /**< Long Mode Enable */
#define VOS3_EFER_LMA               (1ULL << 10)  /**< Long Mode Active */
#define VOS3_EFER_NXE               (1ULL << 11)  /**< No-Execute Enable */

/* ============================================================================
 * CPU INFORMATION STRUCTURE
 * ============================================================================ */

/** @brief CPU vendor identifiers */
typedef enum vos3_cpu_vendor {
    VOS3_CPU_VENDOR_UNKNOWN = 0,
    VOS3_CPU_VENDOR_INTEL,
    VOS3_CPU_VENDOR_AMD,
    VOS3_CPU_VENDOR_OTHER
} vos3_cpu_vendor_t;

/**
 * @brief CPU information structure
 */
typedef struct vos3_cpu_info {
    /* Vendor */
    vos3_cpu_vendor_t vendor;
    char vendor_string[13];     /**< "GenuineIntel", "AuthenticAMD", etc. */

    /* Brand string */
    char brand_string[49];      /**< CPU model name */

    /* Version info */
    uint8_t stepping;
    uint8_t model;
    uint8_t family;
    uint8_t type;
    uint8_t ext_model;
    uint8_t ext_family;

    /* Features */
    uint32_t features_edx;      /**< CPUID.01H:EDX */
    uint32_t features_ecx;      /**< CPUID.01H:ECX */
    uint32_t ext_features_edx;  /**< CPUID.80000001H:EDX */
    uint32_t ext_features_ecx;  /**< CPUID.80000001H:ECX */

    /* Extended features (CPUID.07H) */
    uint32_t ext7_ebx;
    uint32_t ext7_ecx;
    uint32_t ext7_edx;

    /* Cache info */
    uint8_t clflush_size;       /**< CLFLUSH line size (bytes) */
    uint8_t max_logical_cpus;   /**< Max logical CPUs per package */
    uint8_t initial_apic_id;    /**< Initial APIC ID */

    /* Address sizes */
    uint8_t phys_addr_bits;     /**< Physical address bits */
    uint8_t virt_addr_bits;     /**< Virtual address bits */

    /* Max CPUID leaves */
    uint32_t max_std_leaf;
    uint32_t max_ext_leaf;

    /* P4.2 — microcode revision read from MSR 0x8B after the CPUID
     * 0x1 "kick" (Intel SDM Vol 3A §9.11.7.1). 0 means "not read yet"
     * or "MSR unsupported on this host" — neither is a panic condition. */
    uint32_t microcode_revision;
} vos3_cpu_info_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Execute CPUID instruction
 * @param[in] leaf CPUID leaf (EAX input)
 * @param[in] subleaf CPUID subleaf (ECX input)
 * @param[out] eax EAX output
 * @param[out] ebx EBX output
 * @param[out] ecx ECX output
 * @param[out] edx EDX output
 */
void vos3_cpuid(uint32_t leaf, uint32_t subleaf,
                uint32_t* eax, uint32_t* ebx,
                uint32_t* ecx, uint32_t* edx);

/**
 * @brief Initialize CPU info structure
 * @param[out] info CPU info structure to fill
 */
void vos3_cpu_detect(vos3_cpu_info_t* info);

/**
 * @brief Check if CPU has a specific feature
 * @param[in] info CPU info structure
 * @param[in] feature Feature flag to check
 * @return 1 if feature present, 0 otherwise
 */
int vos3_cpu_has_feature(const vos3_cpu_info_t* info, uint32_t feature);

/**
 * @brief Print CPU info to console
 * @param[in] info CPU info structure
 */
void vos3_cpu_print_info(const vos3_cpu_info_t* info);

/**
 * @brief Read Model-Specific Register
 * @param[in] msr MSR address
 * @return MSR value
 */
uint64_t vos3_read_msr(uint32_t msr);

/**
 * @brief Write Model-Specific Register
 * @param[in] msr MSR address
 * @param[in] value Value to write
 */
void vos3_write_msr(uint32_t msr, uint64_t value);

/**
 * @brief Read CR0 register
 * @return CR0 value
 */
uint64_t vos3_read_cr0(void);

/**
 * @brief Write CR0 register
 * @param[in] value Value to write
 */
void vos3_write_cr0(uint64_t value);

/**
 * @brief Read CR2 register (page fault address)
 * @return CR2 value
 */
uint64_t vos3_read_cr2(void);

/**
 * @brief Read CR3 register (page table base)
 * @return CR3 value
 */
uint64_t vos3_read_cr3(void);

/**
 * @brief Write CR3 register
 * @param[in] value Value to write
 */
void vos3_write_cr3(uint64_t value);

/**
 * @brief Read CR4 register
 * @return CR4 value
 */
uint64_t vos3_read_cr4(void);

/**
 * @brief Write CR4 register
 * @param[in] value Value to write
 */
void vos3_write_cr4(uint64_t value);

/**
 * @brief Harden CPU silicon security features (UMIP, SMEP, SMAP, WP)
 * @note v23.12: Called on both BSP and AP to ensure feature parity.
 *       Uses CPUID to detect support before enabling each feature.
 */
void vos3_cpu_harden_silicon(void);

/**
 * @brief Halt CPU
 */
void vos3_cpu_halt(void);

/* vos3_cpu_pause() is a static inline defined in atomic.h — no forward decl needed here */

/**
 * @brief Invalidate TLB entry
 * @param[in] addr Virtual address to invalidate
 */
static inline void vos3_invlpg(uintptr_t addr)
{
    __asm__ volatile ("invlpg (%0)" :: "r" (addr) : "memory");
}

/**
 * @brief Flush entire TLB
 */
static inline void vos3_flush_tlb(void)
{
    uint64_t cr3 = vos3_read_cr3();
    vos3_write_cr3(cr3);
}

/**
 * @brief Read Time Stamp Counter
 * @return TSC value
 */
static inline uint64_t vos3_rdtsc(void)
{
    uint32_t lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}

/**
 * @brief Read Time Stamp Counter with processor ID
 * @param[out] aux Auxiliary value (IA32_TSC_AUX)
 * @return TSC value
 */
static inline uint64_t vos3_rdtscp(uint32_t* aux)
{
    uint32_t lo, hi, a;
    __asm__ volatile ("rdtscp" : "=a"(lo), "=d"(hi), "=c"(a));
    if (aux != NULL) {
        *aux = a;
    }
    return ((uint64_t)hi << 32) | lo;
}

/* ============================================================================
 * PMU (PERFORMANCE MONITORING UNIT)
 * ============================================================================ */

/** @brief IA32_PERFEVTSEL0 MSR — configure PMC0 event selection */
#define IA32_PERFEVTSEL0    0x186U

/** @brief IA32_PMC0 MSR — performance counter 0 value */
#define IA32_PMC0           0x0C1U

/** @brief L2 cache miss event code (event 0x2E, umask 0x41 = LLC miss) */
#define PMU_EVT_L2_MISS     0x0041002EU

/** @brief Enable PMC in user+kernel mode (EN=1, USR=1, OS=1) */
#define PMU_ENABLE_MASK      0x00430000U

/**
 * @brief Read Performance Monitoring Counter via RDPMC.
 * @param counter PMC index (0 = PMC0, 1 = PMC1, ...)
 * @return 64-bit counter value
 */
static inline uint64_t vos3_rdpmc(uint32_t counter)
{
    uint32_t lo, hi;
    __asm__ volatile ("rdpmc" : "=a"(lo), "=d"(hi) : "c"(counter));
    return ((uint64_t)hi << 32) | lo;
}

/**
 * @brief Check PMU version from CPUID.0AH.
 * @return PMU architectural version (0 = no PMU support)
 */
static inline uint32_t vos3_pmu_version(void)
{
    uint32_t eax, ebx, ecx, edx;
    __asm__ volatile ("cpuid"
        : "=a"(eax), "=b"(ebx), "=c"(ecx), "=d"(edx)
        : "a"(0x0A));
    return eax & 0xFF;
}

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ARCH_X86_64_CPU_H */
