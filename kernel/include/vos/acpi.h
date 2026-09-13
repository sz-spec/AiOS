/**
 * @file acpi.h
 * @brief VOS3 ACPI Table Parser — Sovereign Integrity
 *
 * @details Freestanding ACPI parser for VOS3.  Validates RSDP signature
 *          and checksums, walks XSDT/RSDT, extracts MADT (LAPIC, IOAPIC,
 *          Interrupt Source Overrides, NMI Sources) and FADT.
 *
 *          Security guarantees:
 *          - RSDP cross-check: UEFI pointer vs legacy EBDA/BIOS scan
 *          - All table checksums verified before parsing
 *          - All table lengths bounds-checked against physical memory
 *          - No AML interpreter — zero dynamic code execution
 *          - Fail-closed: any corruption returns -EBADMSG
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_ACPI_H
#define VOS3_ACPI_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * ACPI CONSTANTS
 * ============================================================================ */

/** @brief Maximum ACPI tables we track */
#define VOS3_ACPI_MAX_TABLES        64U

/** @brief Maximum CPUs from MADT */
#define VOS3_ACPI_MAX_CPUS          256U

/** @brief Maximum IOAPIC entries */
#define VOS3_ACPI_MAX_IOAPICS       8U

/** @brief Maximum Interrupt Source Overrides */
#define VOS3_ACPI_MAX_ISO           32U

/** @brief Maximum NMI Sources */
#define VOS3_ACPI_MAX_NMI           16U

/** @brief Maximum table length we accept (16 MiB) — OOB guard */
#define VOS3_ACPI_MAX_TABLE_LEN     (16U * 1024U * 1024U)

/** @brief Minimum SDT header length */
#define VOS3_ACPI_SDT_HDR_LEN      36U

/** @brief RSDP signature: "RSD PTR " (8 bytes, space-padded) */
#define VOS3_RSDP_SIGNATURE         "RSD PTR "

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_ACPI_OK                 0
#define VOS3_ACPI_E_NO_RSDP         (-1)   /* RSDP not found */
#define VOS3_ACPI_E_BAD_SIG         (-2)   /* Bad RSDP signature */
#define VOS3_ACPI_E_BAD_CKSUM       (-3)   /* Checksum failure */
#define VOS3_ACPI_E_BAD_LEN         (-4)   /* Table length out of bounds */
#define VOS3_ACPI_E_NO_XSDT         (-5)   /* Neither XSDT nor RSDT found */
#define VOS3_ACPI_E_CROSS_CHECK     (-6)   /* RSDP cross-check failure: PLATFORM_HIJACK_ATTEMPT */
#define VOS3_ACPI_E_NO_MADT         (-7)   /* MADT not found */
#define VOS3_ACPI_E_BADMSG          (-74)  /* EBADMSG — generic corruption */

/* ============================================================================
 * ACPI TABLE STRUCTURES (ACPI Spec 6.5 / UEFI Spec 2.10)
 * ============================================================================ */

/**
 * @brief RSDP — Root System Description Pointer (ACPI 1.0 + 2.0)
 */
typedef struct __attribute__((packed)) vos3_acpi_rsdp {
    char        signature[8];       /**< "RSD PTR " (8 bytes) */
    uint8_t     checksum;           /**< ACPI 1.0 checksum (first 20 bytes) */
    char        oem_id[6];          /**< OEM identifier */
    uint8_t     revision;           /**< 0 = ACPI 1.0, 2 = ACPI 2.0+ */
    uint32_t    rsdt_addr;          /**< RSDT physical address (32-bit) */
    /* ACPI 2.0+ fields (only valid if revision >= 2) */
    uint32_t    length;             /**< RSDP length (36 bytes for 2.0) */
    uint64_t    xsdt_addr;          /**< XSDT physical address (64-bit) */
    uint8_t     ext_checksum;       /**< Extended checksum (all 36 bytes) */
    uint8_t     reserved[3];        /**< Reserved */
} vos3_acpi_rsdp_t;

/**
 * @brief SDT Header — Common header for all ACPI System Description Tables
 */
typedef struct __attribute__((packed)) vos3_acpi_sdt_hdr {
    char        signature[4];       /**< Table signature (e.g., "APIC", "FACP") */
    uint32_t    length;             /**< Total table length including header */
    uint8_t     revision;           /**< Table revision */
    uint8_t     checksum;           /**< Whole-table checksum (sum of all bytes = 0) */
    char        oem_id[6];          /**< OEM identifier */
    char        oem_table_id[8];    /**< OEM table identifier */
    uint32_t    oem_revision;       /**< OEM revision */
    char        creator_id[4];      /**< Creator vendor ID */
    uint32_t    creator_revision;   /**< Creator revision */
} vos3_acpi_sdt_hdr_t;

/**
 * @brief XSDT — Extended System Description Table (64-bit pointers)
 */
typedef struct __attribute__((packed)) vos3_acpi_xsdt {
    vos3_acpi_sdt_hdr_t header;
    /* Followed by array of uint64_t physical addresses */
} vos3_acpi_xsdt_t;

/**
 * @brief RSDT — Root System Description Table (32-bit pointers)
 */
typedef struct __attribute__((packed)) vos3_acpi_rsdt {
    vos3_acpi_sdt_hdr_t header;
    /* Followed by array of uint32_t physical addresses */
} vos3_acpi_rsdt_t;

/* ============================================================================
 * MADT (Multiple APIC Description Table) — Signature "APIC"
 * ============================================================================ */

/**
 * @brief MADT header (after SDT header)
 */
typedef struct __attribute__((packed)) vos3_acpi_madt {
    vos3_acpi_sdt_hdr_t header;
    uint32_t    lapic_addr;         /**< Local APIC physical address */
    uint32_t    flags;              /**< Flags (bit 0: dual-8259 legacy PICs) */
    /* Followed by variable-length MADT entry records */
} vos3_acpi_madt_t;

/** @brief MADT entry types */
#define VOS3_MADT_LAPIC             0U   /**< Processor Local APIC */
#define VOS3_MADT_IOAPIC            1U   /**< I/O APIC */
#define VOS3_MADT_ISO               2U   /**< Interrupt Source Override */
#define VOS3_MADT_NMI_SRC           3U   /**< NMI Source */
#define VOS3_MADT_LAPIC_NMI         4U   /**< Local APIC NMI */
#define VOS3_MADT_LAPIC_OVERRIDE    5U   /**< Local APIC Address Override */
#define VOS3_MADT_X2APIC            9U   /**< Processor Local x2APIC */

/**
 * @brief MADT entry header (common to all MADT entry types)
 */
typedef struct __attribute__((packed)) vos3_madt_entry_hdr {
    uint8_t     type;               /**< Entry type (VOS3_MADT_*) */
    uint8_t     length;             /**< Entry length in bytes */
} vos3_madt_entry_hdr_t;

/**
 * @brief MADT Local APIC entry (type 0)
 */
typedef struct __attribute__((packed)) vos3_madt_lapic {
    vos3_madt_entry_hdr_t hdr;
    uint8_t     processor_id;       /**< ACPI Processor ID */
    uint8_t     apic_id;            /**< Local APIC ID */
    uint32_t    flags;              /**< Bit 0: enabled, Bit 1: online capable */
} vos3_madt_lapic_t;

/**
 * @brief MADT I/O APIC entry (type 1)
 */
typedef struct __attribute__((packed)) vos3_madt_ioapic {
    vos3_madt_entry_hdr_t hdr;
    uint8_t     ioapic_id;          /**< I/O APIC ID */
    uint8_t     reserved;
    uint32_t    ioapic_addr;        /**< I/O APIC physical address */
    uint32_t    gsi_base;           /**< Global System Interrupt base */
} vos3_madt_ioapic_t;

/**
 * @brief MADT Interrupt Source Override entry (type 2)
 */
typedef struct __attribute__((packed)) vos3_madt_iso {
    vos3_madt_entry_hdr_t hdr;
    uint8_t     bus;                /**< Bus (always 0 = ISA) */
    uint8_t     source;             /**< ISA IRQ source */
    uint32_t    gsi;                /**< Global System Interrupt number */
    uint16_t    flags;              /**< MPS INTI flags (polarity, trigger) */
} vos3_madt_iso_t;

/**
 * @brief MADT NMI Source entry (type 3)
 */
typedef struct __attribute__((packed)) vos3_madt_nmi_src {
    vos3_madt_entry_hdr_t hdr;
    uint16_t    flags;              /**< MPS INTI flags */
    uint32_t    gsi;                /**< GSI for NMI */
} vos3_madt_nmi_src_t;

/**
 * @brief MADT Local APIC NMI entry (type 4)
 */
typedef struct __attribute__((packed)) vos3_madt_lapic_nmi {
    vos3_madt_entry_hdr_t hdr;
    uint8_t     processor_id;       /**< ACPI Processor ID (0xFF = all) */
    uint16_t    flags;              /**< MPS INTI flags */
    uint8_t     lint;               /**< LINT# (0 or 1) */
} vos3_madt_lapic_nmi_t;

/**
 * @brief MADT Local APIC Address Override entry (type 5)
 */
typedef struct __attribute__((packed)) vos3_madt_lapic_override {
    vos3_madt_entry_hdr_t hdr;
    uint16_t    reserved;
    uint64_t    lapic_addr_64;      /**< 64-bit Local APIC physical address */
} vos3_madt_lapic_override_t;

/* ============================================================================
 * FADT (Fixed ACPI Description Table) — Signature "FACP"
 * ============================================================================ */

/**
 * @brief FADT — partial, fields needed for power management and SCI
 */
typedef struct __attribute__((packed)) vos3_acpi_fadt {
    vos3_acpi_sdt_hdr_t header;
    uint32_t    firmware_ctrl;      /**< FACS physical address (32-bit) */
    uint32_t    dsdt_addr;          /**< DSDT physical address (32-bit) */
    uint8_t     reserved0;
    uint8_t     preferred_pm_profile; /**< Preferred power management profile */
    uint16_t    sci_int;            /**< SCI interrupt vector */
    uint32_t    smi_cmd;            /**< SMI command port */
    uint8_t     acpi_enable;        /**< ACPI enable value */
    uint8_t     acpi_disable;       /**< ACPI disable value */
    /* Many more fields follow — we only parse what we need */
} vos3_acpi_fadt_t;

/* ============================================================================
 * PARSED ACPI STATE — Kernel-Side Results
 * ============================================================================ */

/**
 * @brief Parsed LAPIC info from MADT
 */
typedef struct vos3_acpi_cpu {
    uint8_t     processor_id;       /**< ACPI Processor ID */
    uint8_t     apic_id;            /**< Local APIC ID */
    uint8_t     enabled;            /**< 1 if enabled or online-capable */
    uint8_t     is_bsp;             /**< 1 if bootstrap processor */
} vos3_acpi_cpu_t;

/**
 * @brief Parsed IOAPIC info from MADT
 */
typedef struct vos3_acpi_ioapic {
    uint8_t     id;                 /**< I/O APIC ID */
    uint32_t    addr;               /**< I/O APIC physical address */
    uint32_t    gsi_base;           /**< GSI base */
} vos3_acpi_ioapic_t;

/**
 * @brief Parsed Interrupt Source Override from MADT
 */
typedef struct vos3_acpi_iso {
    uint8_t     bus;                /**< Bus (0 = ISA) */
    uint8_t     source;             /**< ISA IRQ source */
    uint32_t    gsi;                /**< Remapped GSI */
    uint16_t    flags;              /**< Polarity + trigger mode */
} vos3_acpi_iso_t;

/**
 * @brief Parsed NMI Source from MADT
 */
typedef struct vos3_acpi_nmi {
    uint32_t    gsi;                /**< GSI for NMI */
    uint16_t    flags;              /**< MPS INTI flags */
} vos3_acpi_nmi_t;

/**
 * @brief Complete parsed ACPI state
 */
typedef struct vos3_acpi_info {
    /* RSDP provenance */
    uint8_t     acpi_revision;      /**< ACPI revision (0 or 2) */
    char        oem_id[7];          /**< OEM ID (null-terminated) */
    uint64_t    rsdp_phys;          /**< RSDP physical address (UEFI) */
    uint64_t    rsdp_legacy_phys;   /**< RSDP from legacy scan (0 if not found) */
    uint8_t     cross_check_pass;   /**< 1 if UEFI == legacy (or legacy N/A) */

    /* MADT results */
    uint64_t    lapic_addr;         /**< Local APIC physical address */
    uint32_t    madt_flags;         /**< MADT flags (bit 0: dual-8259) */

    /* CPU topology */
    uint32_t    cpu_count;          /**< Total CPUs found */
    vos3_acpi_cpu_t cpus[VOS3_ACPI_MAX_CPUS];

    /* IOAPIC topology */
    uint32_t    ioapic_count;
    vos3_acpi_ioapic_t ioapics[VOS3_ACPI_MAX_IOAPICS];

    /* Interrupt Source Overrides */
    uint32_t    iso_count;
    vos3_acpi_iso_t isos[VOS3_ACPI_MAX_ISO];

    /* NMI Sources */
    uint32_t    nmi_count;
    vos3_acpi_nmi_t nmis[VOS3_ACPI_MAX_NMI];

    /* FADT basics */
    uint8_t     fadt_found;         /**< 1 if FADT was parsed */
    uint16_t    sci_int;            /**< SCI interrupt vector */
    uint32_t    smi_cmd;            /**< SMI command port */
    uint8_t     pm_profile;         /**< Preferred PM profile */

    /* Table inventory */
    uint32_t    table_count;        /**< Number of SDTs found in XSDT/RSDT */
    char        table_sigs[VOS3_ACPI_MAX_TABLES][5]; /**< Null-terminated 4-char signatures */

    /* [OLYMPUS-FIX APEX-HOME v21.2.1 — HARDWARE-UNBLOCK]
     * Physical addresses of every discovered SDT, parallel to
     * table_sigs[]. This is the missing link that lets downstream
     * drivers (tpm2_init, future apic.c MADT consumers) locate their
     * hardware blocks without re-walking the XSDT.
     *
     * Use:
     *   for (uint32_t i = 0; i < g_acpi_info.table_count; i++) {
     *       if (memcmp(g_acpi_info.table_sigs[i], "TPM2", 4) == 0) {
     *           uint64_t pa = g_acpi_info.table_phys[i];
     *           const void *tpm2 = vos3_phys_to_virt(pa);
     *           // ... parse TPM2 table, extract CRB base ...
     *       }
     *   }
     *
     * Closes the v21.3.x-TPM-CRB prerequisite without booting QEMU.
     */
    uint64_t    table_phys[VOS3_ACPI_MAX_TABLES]; /**< PA of each SDT */

    /* DMAR — DMA Remapping Hardware Units (IOMMU) */
    uint8_t     dmar_found;         /**< 1 if DMAR table was parsed */
    uint8_t     dmar_host_width;    /**< Host address width (bits) from DMAR header */
    uint8_t     dmar_flags;         /**< DMAR flags (bit 0: INTR_REMAP, bit 1: X2APIC_OPT) */
    uint8_t     dmar_unit_count;    /**< Number of DRHD entries found */
    struct {
        uint64_t    reg_base_phys;  /**< IOMMU register base physical address */
        uint16_t    segment;        /**< PCI segment number */
        uint8_t     flags;          /**< Bit 0: INCLUDE_PCI_ALL */
        uint8_t     scope_count;    /**< Number of device scope entries */
        struct {
            uint8_t     type;       /**< 1=PCI endpoint, 2=PCI sub-hierarchy, 3=IOAPIC */
            uint8_t     bus;        /**< PCI start bus number */
            uint8_t     dev;        /**< PCI device number */
            uint8_t     func;       /**< PCI function number */
        } scopes[8];               /**< Device scope entries (up to 8 per unit) */
    } dmar_units[4];               /**< IOMMU translation units (up to 4) */

    /* DSAR — Device-Specific ACPI Resources (NPU cluster topology) */
    uint8_t     dsar_found;          /**< 1 if DSAR table was parsed */
    uint8_t     dsar_cluster_count;  /**< Number of NPU clusters enumerated */
    struct {
        uint32_t    device_id;       /**< PCI device ID of NPU */
        uint8_t     cluster_id;      /**< Cluster index within the package */
        uint8_t     _pad[3];
        uint32_t    compute_capacity;  /**< Relative TOPS (vendor-normalized) */
        uint32_t    memory_bandwidth;  /**< Peak BW in MB/s */
    } dsar_clusters[8];              /**< Up to 8 NPU clusters per package */

} vos3_acpi_info_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize ACPI subsystem — parse all tables from boot_info RSDP
 *
 * @param rsdp_phys  Physical address of RSDP (from boot_info.rsdp_addr)
 * @param hhdm_off   Higher-half direct map offset (VOS3_PHYS_MAP_OFFSET)
 * @return VOS3_ACPI_OK on success, negative error code on failure
 *
 * @note Must be called after VMM init (HHDM must be active)
 * @note Fail-closed: any table corruption → error return, no partial state
 */
int vos3_acpi_init(uint64_t rsdp_phys, uint64_t hhdm_off);

/**
 * @brief Get parsed ACPI info (read-only)
 * @return Pointer to global ACPI info structure, or NULL if not initialized
 */
const vos3_acpi_info_t* vos3_acpi_get_info(void);

/**
 * @brief Print ACPI summary to console
 */
void vos3_acpi_print_summary(void);

/**
 * @brief Return number of NPU clusters enumerated from DSAR table
 */
uint8_t vos3_acpi_npu_cluster_count(void);

/**
 * @brief Map legacy ISA IRQ to GSI using Interrupt Source Overrides
 *
 * @param irq   Legacy ISA IRQ number (0-15)
 * @return Remapped GSI, or original IRQ if no override exists
 */
uint32_t vos3_acpi_irq_to_gsi(uint8_t irq);

#endif /* VOS3_ACPI_H */
