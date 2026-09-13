/**
 * @file acpi.c
 * @brief VOS3 ACPI Table Parser — Sovereign Integrity
 *
 * @details Parses ACPI tables from the RSDP provided by the UEFI boot stub.
 *          Implements the following security guarantees:
 *
 *          1. RSDP AUTHENTICITY: Validates "RSD PTR " signature, ACPI 1.0
 *             checksum (first 20 bytes), and ACPI 2.0 extended checksum
 *             (all 36 bytes).
 *
 *          2. RSDP CROSS-CHECK: Scans legacy EBDA/BIOS ROM (0xE0000-0xFFFFF)
 *             for a secondary RSDP.  If found and differs from UEFI RSDP,
 *             triggers CRITICAL_SECURITY_VIOLATION: PLATFORM_HIJACK_ATTEMPT.
 *
 *          3. CHECKSUM PURITY: Every SDT header is checksummed before parsing.
 *             Sum of all bytes in [0..length-1] must equal 0.
 *
 *          4. OOB GUARD: Table length is bounds-checked against
 *             VOS3_ACPI_MAX_TABLE_LEN (16 MiB).  No read extends beyond
 *             the declared table boundary.
 *
 *          5. ZERO AML: No AML/DSDT interpreter.  DSDT address is recorded
 *             but never executed.  This is a data-only parser.
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include <vos/acpi.h>
#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * KERNEL DEPENDENCIES (forward declarations for freestanding kernel)
 * ============================================================================ */

/* Console output */
extern void vos3_console_printf(const char *fmt, ...);

/* Logging macros — defined in kernel headers, fallback here */
#ifndef VOS3_INFO
#define VOS3_INFO(fmt, ...)  vos3_console_printf("[ACPI] " fmt "\n", ##__VA_ARGS__)
#endif
#ifndef VOS3_WARN
#define VOS3_WARN(fmt, ...)  vos3_console_printf("[ACPI WARN] " fmt "\n", ##__VA_ARGS__)
#endif
#ifndef VOS3_ERROR
#define VOS3_ERROR(fmt, ...) vos3_console_printf("[ACPI ERROR] " fmt "\n", ##__VA_ARGS__)
#endif

/* ============================================================================
 * STATIC STATE
 * ============================================================================ */

/** @brief Global parsed ACPI info — zero-initialized */
static vos3_acpi_info_t g_acpi_info;

/** @brief HHDM offset cached after init */
static uint64_t g_hhdm_offset;

/** @brief Initialization flag */
static int g_acpi_initialized;

/* ============================================================================
 * HELPER: Physical-to-Virtual via HHDM
 * ============================================================================ */

/**
 * @brief Convert physical address to virtual via HHDM
 *
 * @param phys Physical address
 * @return Virtual pointer in HHDM region
 *
 * @note Caller must ensure phys is within mapped physical memory
 */
static inline const void* acpi_phys_to_virt(uint64_t phys)
{
    return (const void*)(uintptr_t)(phys + g_hhdm_offset);
}

/* ============================================================================
 * HELPER: Freestanding memcmp / memcpy / memset
 * ============================================================================ */

static int acpi_memcmp(const void *a, const void *b, size_t n)
{
    const uint8_t *pa = (const uint8_t *)a;
    const uint8_t *pb = (const uint8_t *)b;
    for (size_t i = 0; i < n; i++) {
        if (pa[i] != pb[i]) {
            return (int)pa[i] - (int)pb[i];
        }
    }
    return 0;
}

static void acpi_memset(void *dst, int val, size_t n)
{
    uint8_t *p = (uint8_t *)dst;
    for (size_t i = 0; i < n; i++) {
        p[i] = (uint8_t)val;
    }
}

static void acpi_memcpy(void *dst, const void *src, size_t n)
{
    uint8_t *d = (uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    for (size_t i = 0; i < n; i++) {
        d[i] = s[i];
    }
}

/* ============================================================================
 * CHECKSUM VERIFICATION
 * ============================================================================ */

/**
 * @brief Compute byte-sum checksum over a region
 *
 * @param data  Pointer to data
 * @param len   Number of bytes
 * @return Sum of all bytes (valid ACPI table sums to 0)
 */
static uint8_t acpi_checksum(const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    uint8_t sum = 0;
    for (size_t i = 0; i < len; i++) {
        sum += p[i];
    }
    return sum;
}

/* ============================================================================
 * RSDP VALIDATION
 * ============================================================================ */

/**
 * @brief Validate an RSDP at the given virtual address
 *
 * Checks:
 * 1. Signature == "RSD PTR "
 * 2. ACPI 1.0 checksum (first 20 bytes)
 * 3. If revision >= 2: extended checksum (all 36 bytes)
 *
 * @param rsdp   Virtual pointer to candidate RSDP
 * @return VOS3_ACPI_OK if valid, negative error code otherwise
 */
static int acpi_validate_rsdp(const vos3_acpi_rsdp_t *rsdp)
{
    /* Check signature: "RSD PTR " (8 bytes, space at end) */
    if (acpi_memcmp(rsdp->signature, VOS3_RSDP_SIGNATURE, 8) != 0) {
        return VOS3_ACPI_E_BAD_SIG;
    }

    /* ACPI 1.0 checksum: first 20 bytes */
    if (acpi_checksum(rsdp, 20) != 0) {
        VOS3_ERROR("RSDP ACPI 1.0 checksum failure");
        return VOS3_ACPI_E_BAD_CKSUM;
    }

    /* ACPI 2.0+ extended checksum: all 36 bytes */
    if (rsdp->revision >= 2) {
        if (rsdp->length < 36) {
            VOS3_ERROR("RSDP 2.0 claims length %u < 36", rsdp->length);
            return VOS3_ACPI_E_BAD_LEN;
        }
        if (acpi_checksum(rsdp, 36) != 0) {
            VOS3_ERROR("RSDP ACPI 2.0 extended checksum failure");
            return VOS3_ACPI_E_BAD_CKSUM;
        }
    }

    return VOS3_ACPI_OK;
}

/* ============================================================================
 * LEGACY RSDP SCAN (EBDA + BIOS ROM)
 * ============================================================================ */

/**
 * @brief Scan legacy memory regions for RSDP
 *
 * Per ACPI spec, RSDP can be found in:
 * 1. First 1 KiB of EBDA (Extended BIOS Data Area, pointer at 0x040E)
 * 2. BIOS ROM area: 0xE0000 - 0xFFFFF
 *
 * RSDP is always 16-byte aligned.
 *
 * @return Physical address of legacy RSDP, or 0 if not found
 */
static uint64_t acpi_legacy_rsdp_scan(void)
{
    /* Read EBDA segment pointer from BDA (BIOS Data Area) at phys 0x040E */
    const uint16_t *bda_ebda = (const uint16_t *)acpi_phys_to_virt(0x040EU);
    uint64_t ebda_base = ((uint64_t)(*bda_ebda)) << 4;

    /* Scan EBDA: first 1 KiB, 16-byte aligned */
    if (ebda_base >= 0x80000 && ebda_base < 0xA0000) {
        for (uint64_t addr = ebda_base; addr < ebda_base + 1024; addr += 16) {
            const vos3_acpi_rsdp_t *candidate =
                (const vos3_acpi_rsdp_t *)acpi_phys_to_virt(addr);
            if (acpi_validate_rsdp(candidate) == VOS3_ACPI_OK) {
                return addr;
            }
        }
    }

    /* Scan BIOS ROM: 0xE0000 - 0xFFFFF, 16-byte aligned */
    for (uint64_t addr = 0xE0000; addr < 0x100000; addr += 16) {
        const vos3_acpi_rsdp_t *candidate =
            (const vos3_acpi_rsdp_t *)acpi_phys_to_virt(addr);
        if (acpi_validate_rsdp(candidate) == VOS3_ACPI_OK) {
            return addr;
        }
    }

    return 0; /* Not found — acceptable on pure-UEFI platforms */
}

/* ============================================================================
 * SDT HEADER VALIDATION
 * ============================================================================ */

/**
 * @brief Validate an SDT header (checksum + length bounds)
 *
 * @param hdr    Virtual pointer to SDT header
 * @return VOS3_ACPI_OK if valid, negative error code otherwise
 */
static int acpi_validate_sdt(const vos3_acpi_sdt_hdr_t *hdr)
{
    /* Length sanity: must be at least header size (36 bytes) */
    if (hdr->length < VOS3_ACPI_SDT_HDR_LEN) {
        VOS3_ERROR("SDT '%.4s' length %u < minimum %u",
                   hdr->signature, hdr->length, VOS3_ACPI_SDT_HDR_LEN);
        return VOS3_ACPI_E_BAD_LEN;
    }

    /* OOB guard: reject absurdly large tables */
    if (hdr->length > VOS3_ACPI_MAX_TABLE_LEN) {
        VOS3_ERROR("SDT '%.4s' length %u exceeds max %u — OOB rejected",
                   hdr->signature, hdr->length, VOS3_ACPI_MAX_TABLE_LEN);
        return VOS3_ACPI_E_BAD_LEN;
    }

    /* Whole-table checksum */
    if (acpi_checksum(hdr, hdr->length) != 0) {
        VOS3_ERROR("SDT '%.4s' checksum failure (length %u)",
                   hdr->signature, hdr->length);
        return VOS3_ACPI_E_BAD_CKSUM;
    }

    return VOS3_ACPI_OK;
}

/* ============================================================================
 * MADT PARSER
 * ============================================================================ */

/**
 * @brief Parse MADT entries (LAPIC, IOAPIC, ISO, NMI)
 *
 * @param madt  Virtual pointer to validated MADT table
 * @return VOS3_ACPI_OK on success
 */
static int acpi_parse_madt(const vos3_acpi_madt_t *madt)
{
    g_acpi_info.lapic_addr = (uint64_t)madt->lapic_addr;
    g_acpi_info.madt_flags = madt->flags;

    /* Walk variable-length entries after MADT fixed header (44 bytes) */
    const uint8_t *start = (const uint8_t *)madt + sizeof(vos3_acpi_madt_t);
    const uint8_t *end   = (const uint8_t *)madt + madt->header.length;

    const uint8_t *ptr = start;
    while (ptr + 2 <= end) {
        const vos3_madt_entry_hdr_t *entry = (const vos3_madt_entry_hdr_t *)ptr;

        /* Entry length must be at least 2 (type + length) */
        if (entry->length < 2) {
            VOS3_WARN("MADT entry with length %u < 2 — stopping parse", entry->length);
            break;
        }

        /* Don't read past table end */
        if (ptr + entry->length > end) {
            VOS3_WARN("MADT entry overflows table boundary — stopping parse");
            break;
        }

        switch (entry->type) {
        case VOS3_MADT_LAPIC: {
            if (entry->length < sizeof(vos3_madt_lapic_t)) break;
            const vos3_madt_lapic_t *lapic = (const vos3_madt_lapic_t *)ptr;
            /* Bit 0: Processor Enabled, Bit 1: Online Capable */
            if ((lapic->flags & 0x3U) && g_acpi_info.cpu_count < VOS3_ACPI_MAX_CPUS) {
                vos3_acpi_cpu_t *cpu = &g_acpi_info.cpus[g_acpi_info.cpu_count];
                cpu->processor_id = lapic->processor_id;
                cpu->apic_id     = lapic->apic_id;
                cpu->enabled     = 1;
                cpu->is_bsp      = 0; /* BSP detection done later */
                g_acpi_info.cpu_count++;
            }
            break;
        }

        case VOS3_MADT_IOAPIC: {
            if (entry->length < sizeof(vos3_madt_ioapic_t)) break;
            const vos3_madt_ioapic_t *ioapic = (const vos3_madt_ioapic_t *)ptr;
            if (g_acpi_info.ioapic_count < VOS3_ACPI_MAX_IOAPICS) {
                vos3_acpi_ioapic_t *io = &g_acpi_info.ioapics[g_acpi_info.ioapic_count];
                io->id       = ioapic->ioapic_id;
                io->addr     = ioapic->ioapic_addr;
                io->gsi_base = ioapic->gsi_base;
                g_acpi_info.ioapic_count++;
            }
            break;
        }

        case VOS3_MADT_ISO: {
            if (entry->length < sizeof(vos3_madt_iso_t)) break;
            const vos3_madt_iso_t *iso = (const vos3_madt_iso_t *)ptr;
            if (g_acpi_info.iso_count < VOS3_ACPI_MAX_ISO) {
                vos3_acpi_iso_t *o = &g_acpi_info.isos[g_acpi_info.iso_count];
                o->bus    = iso->bus;
                o->source = iso->source;
                o->gsi    = iso->gsi;
                o->flags  = iso->flags;
                g_acpi_info.iso_count++;
            }
            break;
        }

        case VOS3_MADT_NMI_SRC: {
            if (entry->length < sizeof(vos3_madt_nmi_src_t)) break;
            const vos3_madt_nmi_src_t *nmi = (const vos3_madt_nmi_src_t *)ptr;
            if (g_acpi_info.nmi_count < VOS3_ACPI_MAX_NMI) {
                vos3_acpi_nmi_t *n = &g_acpi_info.nmis[g_acpi_info.nmi_count];
                n->gsi   = nmi->gsi;
                n->flags = nmi->flags;
                g_acpi_info.nmi_count++;
            }
            break;
        }

        case VOS3_MADT_LAPIC_OVERRIDE: {
            if (entry->length < sizeof(vos3_madt_lapic_override_t)) break;
            const vos3_madt_lapic_override_t *ov =
                (const vos3_madt_lapic_override_t *)ptr;
            /* 64-bit LAPIC address overrides the 32-bit one in MADT header */
            g_acpi_info.lapic_addr = ov->lapic_addr_64;
            VOS3_INFO("LAPIC address override: 0x%llx",
                      (unsigned long long)ov->lapic_addr_64);
            break;
        }

        case VOS3_MADT_X2APIC: {
            /* x2APIC entries have 16-byte structure */
            if (entry->length < 16) break;
            const uint8_t *x2 = ptr;
            uint32_t x2_id    = *(const uint32_t *)(x2 + 4);
            uint32_t x2_flags = *(const uint32_t *)(x2 + 8);
            uint32_t x2_proc  = *(const uint32_t *)(x2 + 12);
            /* The current IPI path uses 8-bit xAPIC destinations. Do not
             * truncate a wider ID and accidentally target another CPU. */
            if (x2_id >= 255U) {
                VOS3_WARN("[ACPI] x2APIC ID %u requires x2APIC startup support", x2_id);
                break;
            }
            if ((x2_flags & 0x3U) && g_acpi_info.cpu_count < VOS3_ACPI_MAX_CPUS) {
                vos3_acpi_cpu_t *cpu = &g_acpi_info.cpus[g_acpi_info.cpu_count];
                cpu->processor_id = (uint8_t)(x2_proc & 0xFF);
                cpu->apic_id     = (uint8_t)(x2_id & 0xFF);
                cpu->enabled     = 1;
                cpu->is_bsp      = 0;
                g_acpi_info.cpu_count++;
            }
            break;
        }

        default:
            /* Unknown entry type — skip silently */
            break;
        }

        ptr += entry->length;
    }

    return VOS3_ACPI_OK;
}

/* ============================================================================
 * FADT PARSER (minimal)
 * ============================================================================ */

/**
 * @brief Parse FADT (Fixed ACPI Description Table) — extract power fields
 *
 * @param fadt  Virtual pointer to validated FADT
 */
static void acpi_parse_fadt(const vos3_acpi_fadt_t *fadt)
{
    g_acpi_info.fadt_found = 1;
    g_acpi_info.sci_int    = fadt->sci_int;
    g_acpi_info.smi_cmd    = fadt->smi_cmd;
    g_acpi_info.pm_profile = fadt->preferred_pm_profile;
}

/* ============================================================================
 * DMAR — DMA Remapping Table Parser
 * ============================================================================ */

/**
 * @brief DMAR table header (Intel VT-d Spec, Section 8.1)
 */
typedef struct __attribute__((packed)) acpi_dmar_hdr {
    vos3_acpi_sdt_hdr_t sdt;       /**< Standard ACPI header (sig="DMAR") */
    uint8_t             host_addr_width; /**< Host address width (bits) */
    uint8_t             flags;      /**< Bit 0: INTR_REMAP, Bit 1: X2APIC */
    uint8_t             reserved[10];
} acpi_dmar_hdr_t;

/**
 * @brief DMAR Remapping Structure Header (common prefix for all DRHD/RMRR/etc.)
 */
typedef struct __attribute__((packed)) acpi_dmar_entry_hdr {
    uint16_t    type;               /**< 0=DRHD, 1=RMRR, 2=ATSR, 3=RHSA, 4=ANDD */
    uint16_t    length;             /**< Total length of this entry */
} acpi_dmar_entry_hdr_t;

/**
 * @brief DRHD — DMA Remapping Hardware Unit Definition (type 0)
 */
typedef struct __attribute__((packed)) acpi_dmar_drhd {
    acpi_dmar_entry_hdr_t hdr;
    uint8_t     flags;              /**< Bit 0: INCLUDE_PCI_ALL */
    uint8_t     reserved;
    uint16_t    segment;            /**< PCI segment number */
    uint64_t    reg_base;           /**< Register base physical address */
    /* followed by Device Scope structures */
} acpi_dmar_drhd_t;

/**
 * @brief DMAR Device Scope Entry (Intel VT-d Spec, Section 8.3.1)
 */
typedef struct __attribute__((packed)) acpi_dmar_device_scope {
    uint8_t     type;               /**< 1=endpoint, 2=sub-hierarchy, 3=IOAPIC */
    uint8_t     length;             /**< Entry length (>=6) */
    uint16_t    reserved;
    uint8_t     enumeration_id;     /**< IOAPIC/HPET ID (type-dependent) */
    uint8_t     start_bus;          /**< PCI start bus number */
    /* followed by (length-6)/2 pairs of {device, function} path entries */
} acpi_dmar_device_scope_t;

/**
 * @brief Parse DMAR table — extract IOMMU hardware unit definitions
 *
 * @details Walks DMAR remapping structures, parses DRHD entries to extract
 *          IOMMU register base addresses and device scope. Used by the NPU
 *          IOMMU seal to program hardware DMA isolation fences.
 *
 * @param hdr  Pointer to validated DMAR table header
 */
static void acpi_parse_dmar(const vos3_acpi_sdt_hdr_t *hdr)
{
    const acpi_dmar_hdr_t *dmar = (const acpi_dmar_hdr_t *)hdr;
    const uint8_t *ptr;
    const uint8_t *end;

    g_acpi_info.dmar_found      = 1;
    g_acpi_info.dmar_host_width = dmar->host_addr_width;
    g_acpi_info.dmar_flags      = dmar->flags;
    g_acpi_info.dmar_unit_count = 0;

    VOS3_INFO("DMAR: host_width=%u flags=0x%02x",
              dmar->host_addr_width, dmar->flags);

    /* Walk remapping structures after the fixed header (48 bytes) */
    ptr = (const uint8_t *)dmar + sizeof(acpi_dmar_hdr_t);
    end = (const uint8_t *)dmar + hdr->length;

    while (ptr + sizeof(acpi_dmar_entry_hdr_t) <= end) {
        const acpi_dmar_entry_hdr_t *entry = (const acpi_dmar_entry_hdr_t *)ptr;

        /* Length sanity: must be at least 4 bytes (header size) */
        if (entry->length < sizeof(acpi_dmar_entry_hdr_t) ||
            ptr + entry->length > end) {
            VOS3_WARN("DMAR: truncated entry at offset %u",
                      (uint32_t)(ptr - (const uint8_t *)dmar));
            break;
        }

        /* Type 0: DRHD — DMA Remapping Hardware Unit */
        if (entry->type == 0 && entry->length >= sizeof(acpi_dmar_drhd_t)) {
            const acpi_dmar_drhd_t *drhd = (const acpi_dmar_drhd_t *)entry;

            if (g_acpi_info.dmar_unit_count < 4) {
                uint8_t idx = g_acpi_info.dmar_unit_count;

                g_acpi_info.dmar_units[idx].reg_base_phys = drhd->reg_base;
                g_acpi_info.dmar_units[idx].segment       = drhd->segment;
                g_acpi_info.dmar_units[idx].flags         = drhd->flags;
                g_acpi_info.dmar_units[idx].scope_count   = 0;

                /* Parse device scope entries within this DRHD */
                const uint8_t *scope_ptr = (const uint8_t *)drhd +
                                           sizeof(acpi_dmar_drhd_t);
                const uint8_t *scope_end = (const uint8_t *)entry +
                                           entry->length;

                while (scope_ptr + sizeof(acpi_dmar_device_scope_t) <= scope_end) {
                    const acpi_dmar_device_scope_t *ds =
                        (const acpi_dmar_device_scope_t *)scope_ptr;

                    if (ds->length < 6 || scope_ptr + ds->length > scope_end) {
                        break;
                    }

                    if (g_acpi_info.dmar_units[idx].scope_count < 8) {
                        uint8_t si = g_acpi_info.dmar_units[idx].scope_count;
                        g_acpi_info.dmar_units[idx].scopes[si].type = ds->type;
                        g_acpi_info.dmar_units[idx].scopes[si].bus  = ds->start_bus;

                        /* Extract device/function from first path entry */
                        if (ds->length >= 8) {
                            const uint8_t *path = (const uint8_t *)ds + 6;
                            g_acpi_info.dmar_units[idx].scopes[si].dev  = path[0];
                            g_acpi_info.dmar_units[idx].scopes[si].func = path[1];
                        }
                        g_acpi_info.dmar_units[idx].scope_count++;
                    }
                    scope_ptr += ds->length;
                }

                VOS3_INFO("DRHD[%u]: reg=0x%llx seg=%u flags=0x%x scopes=%u",
                          idx,
                          (unsigned long long)drhd->reg_base,
                          drhd->segment, drhd->flags,
                          g_acpi_info.dmar_units[idx].scope_count);

                g_acpi_info.dmar_unit_count++;
            }
        }

        ptr += entry->length;
    }

    VOS3_INFO("DMAR parsed: %u IOMMU unit(s) found", g_acpi_info.dmar_unit_count);
}

/* ============================================================================
 * DSAR — Device-Specific ACPI Resources (NPU Cluster Topology)
 * ============================================================================ */

/**
 * DSAR entry format (vendor-defined, VOS3 interpretation):
 *   Offset  Size  Field
 *   0       4     device_id       PCI device ID of the NPU
 *   4       1     cluster_id      Cluster index within the CPU package
 *   5       3     _reserved
 *   8       4     compute_capacity  Relative TOPS (normalized by vendor)
 *   12      4     memory_bandwidth  Peak bandwidth in MB/s
 *   16      --    (total per entry: 16 bytes)
 *
 * DSAR table layout:
 *   [SDT header 36 bytes][entry_count u16][entries...]
 */
#define DSAR_ENTRY_SIZE  16U
#define DSAR_MAX_CLUSTERS 8U

typedef struct __attribute__((packed)) acpi_dsar_hdr {
    vos3_acpi_sdt_hdr_t sdt;    /* Standard ACPI header (sig="DSAR") */
    uint16_t            entry_count;
} acpi_dsar_hdr_t;

typedef struct __attribute__((packed)) acpi_dsar_entry {
    uint32_t device_id;
    uint8_t  cluster_id;
    uint8_t  _reserved[3];
    uint32_t compute_capacity;
    uint32_t memory_bandwidth;
} acpi_dsar_entry_t;

static void acpi_parse_dsar(const vos3_acpi_sdt_hdr_t *hdr)
{
    if (hdr->length < sizeof(acpi_dsar_hdr_t)) {
        VOS3_WARN("DSAR: table too small (%u bytes)", hdr->length);
        return;
    }

    const acpi_dsar_hdr_t *dsar = (const acpi_dsar_hdr_t *)hdr;
    uint16_t count = dsar->entry_count;

    /* Bounds check: don't read past declared table length */
    uint32_t max_entries = (hdr->length - sizeof(acpi_dsar_hdr_t)) / DSAR_ENTRY_SIZE;
    if (count > max_entries) {
        VOS3_WARN("DSAR: entry_count=%u exceeds table bounds (max=%u), clamping",
                  count, max_entries);
        count = (uint16_t)max_entries;
    }
    if (count > DSAR_MAX_CLUSTERS) {
        VOS3_WARN("DSAR: %u entries exceeds max %u, clamping", count, DSAR_MAX_CLUSTERS);
        count = DSAR_MAX_CLUSTERS;
    }

    const acpi_dsar_entry_t *entries = (const acpi_dsar_entry_t *)(dsar + 1);

    for (uint16_t i = 0; i < count; i++) {
        const acpi_dsar_entry_t *e = &entries[i];
        uint8_t idx = g_acpi_info.dsar_cluster_count;

        g_acpi_info.dsar_clusters[idx].device_id        = e->device_id;
        g_acpi_info.dsar_clusters[idx].cluster_id       = e->cluster_id;
        g_acpi_info.dsar_clusters[idx].compute_capacity = e->compute_capacity;
        g_acpi_info.dsar_clusters[idx].memory_bandwidth = e->memory_bandwidth;
        g_acpi_info.dsar_cluster_count++;

        VOS3_INFO("DSAR cluster[%u]: dev_id=0x%04x cluster=%u capacity=%u BW=%u MB/s",
                  idx, e->device_id, e->cluster_id,
                  e->compute_capacity, e->memory_bandwidth);
    }

    g_acpi_info.dsar_found = 1;
    VOS3_INFO("DSAR parsed: %u NPU cluster(s) enumerated", g_acpi_info.dsar_cluster_count);
}

/* ============================================================================
 * XSDT / RSDT WALKER
 * ============================================================================ */

/**
 * @brief Walk XSDT or RSDT and parse all child tables
 *
 * @param use_xsdt  1 to use XSDT (64-bit pointers), 0 for RSDT (32-bit)
 * @param tbl_phys  Physical address of XSDT or RSDT
 * @return VOS3_ACPI_OK on success, negative error on table corruption
 */
static int acpi_walk_sdt(int use_xsdt, uint64_t tbl_phys)
{
    const vos3_acpi_sdt_hdr_t *hdr =
        (const vos3_acpi_sdt_hdr_t *)acpi_phys_to_virt(tbl_phys);

    /* Validate the root table itself */
    int rc = acpi_validate_sdt(hdr);
    if (rc != VOS3_ACPI_OK) {
        VOS3_ERROR("Root SDT (%.4s) validation failed", hdr->signature);
        return rc;
    }

    /* Calculate number of child table pointers */
    uint32_t ptr_size = use_xsdt ? 8U : 4U;
    uint32_t payload  = hdr->length - VOS3_ACPI_SDT_HDR_LEN;
    uint32_t count    = payload / ptr_size;

    if (count > VOS3_ACPI_MAX_TABLES) {
        count = VOS3_ACPI_MAX_TABLES;
    }

    const uint8_t *ptrs = (const uint8_t *)hdr + VOS3_ACPI_SDT_HDR_LEN;
    int madt_found = 0;

    for (uint32_t i = 0; i < count; i++) {
        uint64_t child_phys;
        if (use_xsdt) {
            /* Read 64-bit pointer (unaligned-safe via byte copy) */
            acpi_memcpy(&child_phys, ptrs + i * 8, 8);
        } else {
            uint32_t child32;
            acpi_memcpy(&child32, ptrs + i * 4, 4);
            child_phys = (uint64_t)child32;
        }

        /* Skip null pointers */
        if (child_phys == 0) continue;

        const vos3_acpi_sdt_hdr_t *child =
            (const vos3_acpi_sdt_hdr_t *)acpi_phys_to_virt(child_phys);

        /* Validate child table */
        rc = acpi_validate_sdt(child);
        if (rc != VOS3_ACPI_OK) {
            VOS3_WARN("Child table %u at phys 0x%llx: validation failed (%d) — skipping",
                      i, (unsigned long long)child_phys, rc);
            continue;
        }

        /* Record table signature + physical address in inventory.
         * [OLYMPUS-FIX APEX-HOME v21.2.1 — HARDWARE-UNBLOCK] The PA
         * is the missing link tpm2_init / apic.c need to locate
         * hardware. Without this, every driver had to re-walk XSDT. */
        if (g_acpi_info.table_count < VOS3_ACPI_MAX_TABLES) {
            acpi_memcpy(g_acpi_info.table_sigs[g_acpi_info.table_count],
                        child->signature, 4);
            g_acpi_info.table_sigs[g_acpi_info.table_count][4] = '\0';
            g_acpi_info.table_phys[g_acpi_info.table_count] = child_phys;
            g_acpi_info.table_count++;
        }

        /* Parse known tables */
        if (acpi_memcmp(child->signature, "APIC", 4) == 0) {
            /* MADT — Multiple APIC Description Table */
            VOS3_INFO("Parsing MADT (length=%u)", child->length);
            acpi_parse_madt((const vos3_acpi_madt_t *)child);
            madt_found = 1;
        }
        else if (acpi_memcmp(child->signature, "FACP", 4) == 0) {
            /* FADT — Fixed ACPI Description Table */
            if (child->length >= sizeof(vos3_acpi_fadt_t)) {
                VOS3_INFO("Parsing FADT (length=%u)", child->length);
                acpi_parse_fadt((const vos3_acpi_fadt_t *)child);
            }
        }
        else if (acpi_memcmp(child->signature, "DMAR", 4) == 0) {
            /* DMAR — DMA Remapping Table (Intel VT-d / IOMMU) */
            VOS3_INFO("Parsing DMAR (length=%u)", child->length);
            acpi_parse_dmar(child);
        }
        else if (acpi_memcmp(child->signature, "DSAR", 4) == 0) {
            /* DSAR — Device-Specific ACPI Resources (NPU cluster topology) */
            VOS3_INFO("Parsing DSAR (length=%u)", child->length);
            acpi_parse_dsar(child);
        }
        /* Other tables (HPET, MCFG, SSDT, etc.) — recorded but not parsed */
    }

    if (!madt_found) {
        VOS3_WARN("MADT not found in ACPI tables — SMP topology unavailable");
    }

    return VOS3_ACPI_OK;
}

/* ============================================================================
 * BSP DETECTION
 * ============================================================================ */

/**
 * @brief Read current BSP APIC ID via CPUID and mark in CPU list
 */
static void acpi_detect_bsp(void)
{
    /* CPUID leaf 0x01: EBX[31:24] = Initial APIC ID */
    uint32_t eax, ebx, ecx, edx;
    __asm__ volatile("cpuid" : "=a"(eax), "=b"(ebx), "=c"(ecx), "=d"(edx)
                     : "a"(1));
    uint8_t bsp_id = (uint8_t)((ebx >> 24) & 0xFF);

    for (uint32_t i = 0; i < g_acpi_info.cpu_count; i++) {
        if (g_acpi_info.cpus[i].apic_id == bsp_id) {
            g_acpi_info.cpus[i].is_bsp = 1;
            break;
        }
    }
}

/* ============================================================================
 * PUBLIC API: vos3_acpi_init
 * ============================================================================ */

int vos3_acpi_init(uint64_t rsdp_phys, uint64_t hhdm_off)
{
    int rc;

    /* Cache HHDM offset */
    g_hhdm_offset = hhdm_off;

    /* Zero all state */
    acpi_memset(&g_acpi_info, 0, sizeof(g_acpi_info));
    g_acpi_initialized = 0;

    VOS3_INFO("Initializing ACPI subsystem (RSDP at phys 0x%llx)",
              (unsigned long long)rsdp_phys);

    /* ===== Step 1: Validate UEFI-provided RSDP ===== */
    if (rsdp_phys == 0) {
        VOS3_ERROR("No RSDP address provided");
        return VOS3_ACPI_E_NO_RSDP;
    }

    const vos3_acpi_rsdp_t *rsdp =
        (const vos3_acpi_rsdp_t *)acpi_phys_to_virt(rsdp_phys);

    rc = acpi_validate_rsdp(rsdp);
    if (rc != VOS3_ACPI_OK) {
        VOS3_ERROR("UEFI RSDP validation failed (%d)", rc);
        return rc;
    }

    /* Store provenance info */
    g_acpi_info.rsdp_phys     = rsdp_phys;
    g_acpi_info.acpi_revision = rsdp->revision;
    acpi_memcpy(g_acpi_info.oem_id, rsdp->oem_id, 6);
    g_acpi_info.oem_id[6] = '\0';

    VOS3_INFO("RSDP validated: revision=%u, OEM='%s'",
              rsdp->revision, g_acpi_info.oem_id);

    /* ===== Step 2: Legacy RSDP Cross-Check ===== */
    uint64_t legacy_rsdp = acpi_legacy_rsdp_scan();
    g_acpi_info.rsdp_legacy_phys = legacy_rsdp;

    if (legacy_rsdp != 0) {
        if (legacy_rsdp == rsdp_phys) {
            /* Same address — perfect match */
            g_acpi_info.cross_check_pass = 1;
            VOS3_INFO("RSDP cross-check: UEFI == Legacy at 0x%llx — PASS",
                      (unsigned long long)legacy_rsdp);
        } else {
            /* Different addresses — compare RSDP contents */
            const vos3_acpi_rsdp_t *legacy =
                (const vos3_acpi_rsdp_t *)acpi_phys_to_virt(legacy_rsdp);

            /* Compare first 20 bytes (ACPI 1.0 common fields) */
            if (acpi_memcmp(rsdp, legacy, 20) == 0) {
                g_acpi_info.cross_check_pass = 1;
                VOS3_INFO("RSDP cross-check: contents match (diff addrs 0x%llx vs 0x%llx) — PASS",
                          (unsigned long long)rsdp_phys,
                          (unsigned long long)legacy_rsdp);
            } else {
                /* CRITICAL SECURITY VIOLATION */
                g_acpi_info.cross_check_pass = 0;
                VOS3_ERROR("CRITICAL_SECURITY_VIOLATION: PLATFORM_HIJACK_ATTEMPT");
                VOS3_ERROR("UEFI RSDP at 0x%llx differs from Legacy RSDP at 0x%llx",
                           (unsigned long long)rsdp_phys,
                           (unsigned long long)legacy_rsdp);
                VOS3_ERROR("UEFI OEM='%.6s' rev=%u, Legacy OEM='%.6s' rev=%u",
                           rsdp->oem_id, rsdp->revision,
                           legacy->oem_id, legacy->revision);
                return VOS3_ACPI_E_CROSS_CHECK;
            }
        }
    } else {
        /* No legacy RSDP found — acceptable on pure-UEFI platforms */
        g_acpi_info.cross_check_pass = 1;
        VOS3_INFO("RSDP cross-check: no legacy RSDP (pure-UEFI) — PASS");
    }

    /* ===== Step 3: Walk XSDT (preferred) or RSDT (fallback) ===== */
    if (rsdp->revision >= 2 && rsdp->xsdt_addr != 0) {
        VOS3_INFO("Walking XSDT at phys 0x%llx",
                  (unsigned long long)rsdp->xsdt_addr);
        rc = acpi_walk_sdt(1, rsdp->xsdt_addr);
    } else if (rsdp->rsdt_addr != 0) {
        VOS3_INFO("Walking RSDT at phys 0x%llx (ACPI 1.0 fallback)",
                  (unsigned long long)rsdp->rsdt_addr);
        rc = acpi_walk_sdt(0, (uint64_t)rsdp->rsdt_addr);
    } else {
        VOS3_ERROR("Neither XSDT nor RSDT available");
        return VOS3_ACPI_E_NO_XSDT;
    }

    if (rc != VOS3_ACPI_OK) {
        return rc;
    }

    /* ===== Step 4: BSP detection ===== */
    acpi_detect_bsp();

    /* ===== Done ===== */
    g_acpi_initialized = 1;
    VOS3_INFO("ACPI init complete: %u CPUs, %u IOAPICs, %u ISOs, %u NMIs, %u tables",
              g_acpi_info.cpu_count, g_acpi_info.ioapic_count,
              g_acpi_info.iso_count, g_acpi_info.nmi_count,
              g_acpi_info.table_count);

    return VOS3_ACPI_OK;
}

/* ============================================================================
 * PUBLIC API: vos3_acpi_get_info
 * ============================================================================ */

const vos3_acpi_info_t* vos3_acpi_get_info(void)
{
    if (!g_acpi_initialized) {
        return (const vos3_acpi_info_t *)0;
    }
    return &g_acpi_info;
}

uint8_t vos3_acpi_npu_cluster_count(void)
{
    return g_acpi_info.dsar_found ? g_acpi_info.dsar_cluster_count : 0;
}

/* ============================================================================
 * PUBLIC API: vos3_acpi_print_summary
 * ============================================================================ */

void vos3_acpi_print_summary(void)
{
    if (!g_acpi_initialized) {
        vos3_console_printf("  ACPI: not initialized\n");
        return;
    }

    vos3_console_printf("  ACPI Revision: %u.0\n",
                        g_acpi_info.acpi_revision >= 2 ? 2 : 1);
    vos3_console_printf("  OEM: %s\n", g_acpi_info.oem_id);
    vos3_console_printf("  RSDP Cross-Check: %s\n",
                        g_acpi_info.cross_check_pass ? "PASS" : "FAIL");
    vos3_console_printf("  LAPIC Address: 0x%llx\n",
                        (unsigned long long)g_acpi_info.lapic_addr);
    vos3_console_printf("  CPUs: %u\n", g_acpi_info.cpu_count);

    for (uint32_t i = 0; i < g_acpi_info.cpu_count; i++) {
        const vos3_acpi_cpu_t *c = &g_acpi_info.cpus[i];
        vos3_console_printf("    CPU %u: APIC_ID=%u ProcID=%u %s%s\n",
                            i, c->apic_id, c->processor_id,
                            c->enabled ? "enabled" : "disabled",
                            c->is_bsp  ? " [BSP]" : "");
    }

    vos3_console_printf("  IOAPICs: %u\n", g_acpi_info.ioapic_count);
    for (uint32_t i = 0; i < g_acpi_info.ioapic_count; i++) {
        const vos3_acpi_ioapic_t *io = &g_acpi_info.ioapics[i];
        vos3_console_printf("    IOAPIC %u: addr=0x%08x, GSI base=%u\n",
                            io->id, io->addr, io->gsi_base);
    }

    if (g_acpi_info.iso_count > 0) {
        vos3_console_printf("  IRQ Source Overrides: %u\n", g_acpi_info.iso_count);
        for (uint32_t i = 0; i < g_acpi_info.iso_count; i++) {
            const vos3_acpi_iso_t *o = &g_acpi_info.isos[i];
            vos3_console_printf("    IRQ %u -> GSI %u (flags=0x%04x)\n",
                                o->source, o->gsi, o->flags);
        }
    }

    if (g_acpi_info.nmi_count > 0) {
        vos3_console_printf("  NMI Sources: %u\n", g_acpi_info.nmi_count);
        for (uint32_t i = 0; i < g_acpi_info.nmi_count; i++) {
            vos3_console_printf("    NMI GSI=%u (flags=0x%04x)\n",
                                g_acpi_info.nmis[i].gsi,
                                g_acpi_info.nmis[i].flags);
        }
    }

    if (g_acpi_info.fadt_found) {
        vos3_console_printf("  FADT: SCI_INT=%u, SMI_CMD=0x%x, PM_Profile=%u\n",
                            g_acpi_info.sci_int, g_acpi_info.smi_cmd,
                            g_acpi_info.pm_profile);
    }

    vos3_console_printf("  ACPI Tables (%u):", g_acpi_info.table_count);
    for (uint32_t i = 0; i < g_acpi_info.table_count; i++) {
        vos3_console_printf(" %s", g_acpi_info.table_sigs[i]);
    }
    vos3_console_printf("\n");
}

/* ============================================================================
 * PUBLIC API: vos3_acpi_irq_to_gsi
 * ============================================================================ */

uint32_t vos3_acpi_irq_to_gsi(uint8_t irq)
{
    for (uint32_t i = 0; i < g_acpi_info.iso_count; i++) {
        if (g_acpi_info.isos[i].source == irq) {
            return g_acpi_info.isos[i].gsi;
        }
    }
    /* No override — identity mapping */
    return (uint32_t)irq;
}
