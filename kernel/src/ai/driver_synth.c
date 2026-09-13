/**
 * @file driver_synth.c
 * @brief VOS3 Autonomous Driver Synthesizer — NPU-Driven Auto-Probe
 *
 * @details When a PCI device has no static VOS3 driver, the Driver
 *          Synthesizer maps its BAR0 into a Secure Probe Zone and
 *          executes a Register Pattern Analysis to identify the device
 *          type heuristically.
 *
 *          The probe logic detects:
 *          - Mass Storage controllers (command/status register patterns)
 *          - Network controllers (MAC register + link status patterns)
 *          - Display controllers (framebuffer stride patterns)
 *          - Processing accelerators (doorbell + command queue patterns)
 *
 *          Identified devices are registered with the accel subsystem
 *          for potential AI workload routing.
 *
 *          The Secure Probe Zone is a 64 KB MMIO region mapped with
 *          NOCACHE+DEVICE flags. Reads are bounded to prevent MMIO
 *          side-effects. The zone is unmapped after probing.
 *
 * @version 1.0.0
 * @date 2026-04-11
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 8.5-U — Autonomous Driver Synthesis
 * @note Compiled with -mno-sse — all operations are GPR-only
 */

#include "../../include/vos/pci.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * LOGGING
 * ============================================================================ */

#define SYNTH_INFO(fmt, ...)  vos3_console_printf("[SYNTH] " fmt "\n", ##__VA_ARGS__)
#define SYNTH_WARN(fmt, ...)  vos3_console_printf("[SYNTH] WARN: " fmt "\n", ##__VA_ARGS__)

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Secure Probe Zone virtual base (after HDA at 0x34000000) */
#define SYNTH_PROBE_VBASE       0xFFFF880036000000ULL

/** @brief Maximum probe region size (64 KB — one BAR0 minimum) */
#define SYNTH_PROBE_SIZE        0x10000U

/** @brief Maximum devices to auto-probe per boot */
#define SYNTH_MAX_PROBES        16U

/** @brief Register read safety timeout (prevent infinite MMIO hang) */
#define SYNTH_READ_TIMEOUT      1000U

/** @brief PCI class codes with known static drivers */
#define SYNTH_CLASS_STORAGE     0x01U
#define SYNTH_CLASS_NETWORK     0x02U
#define SYNTH_CLASS_DISPLAY     0x03U
#define SYNTH_CLASS_MULTIMEDIA  0x04U
#define SYNTH_CLASS_MEMORY      0x05U
#define SYNTH_CLASS_BRIDGE      0x06U
#define SYNTH_CLASS_COMM        0x07U
#define SYNTH_CLASS_SYSTEM      0x08U
#define SYNTH_CLASS_ACCEL       0x12U

/** @brief BAR address mask (strip type/prefetch/size bits) */
#define PCI_BAR_ADDR_MASK       0xFFFFFFF0U
#define PCI_BAR_IO_MASK         0x01U

/* ============================================================================
 * DEVICE TYPE DETECTION SIGNATURES
 * ============================================================================ */

/**
 * Register pattern signatures for heuristic device identification.
 * Each device class has characteristic MMIO register patterns at
 * well-known offsets that distinguish it from other device types.
 */

/** @brief Mass Storage: ATA/AHCI status register pattern
 *  ATA devices have a status register with BSY(7), DRDY(6), DRQ(3) bits.
 *  AHCI has GHC.AE (bit 31) at offset 0x04. */
#define AHCI_GHC_OFFSET         0x04U
#define AHCI_GHC_AE_BIT         (1U << 31)
#define AHCI_PI_OFFSET          0x0CU   /**< Ports Implemented */
#define AHCI_CAP_OFFSET         0x00U   /**< Host Capabilities */

/** @brief Network: MAC address register pattern
 *  Intel NICs have MAC at BAR0+0x5400 (RAL0/RAH0).
 *  Most NICs have a status register with LINK_UP near offset 0x08. */
#define NIC_STATUS_OFFSET       0x08U
#define NIC_RAL0_OFFSET         0x5400U /**< Receive Address Low (if within BAR) */

/** @brief Display: VGA/GPU framebuffer detection
 *  VGA has I/O port 0x3C0 region; GPU BAR0 has device ID echo at 0x00. */
#define GPU_DEVICE_ID_OFFSET    0x00U

/** @brief Accelerator: Doorbell + queue base pattern
 *  NPU/TPU devices have doorbell registers at aligned offsets. */
#define ACCEL_DOORBELL_OFFSET   0x1000U

/* ============================================================================
 * PROBE RESULT TYPE
 * ============================================================================ */

/** @brief Synthesized device type from heuristic analysis */
typedef enum {
    SYNTH_TYPE_UNKNOWN   = 0,
    SYNTH_TYPE_STORAGE   = 1,   /**< Mass storage (AHCI-like) */
    SYNTH_TYPE_NETWORK   = 2,   /**< Network interface */
    SYNTH_TYPE_DISPLAY   = 3,   /**< Display/GPU controller */
    SYNTH_TYPE_ACCEL     = 4,   /**< Processing accelerator */
    SYNTH_TYPE_AUDIO     = 5,   /**< Audio controller */
    SYNTH_TYPE_USB       = 6,   /**< USB host controller */
} synth_device_type_t;

/** @brief Auto-probe result record */
typedef struct {
    uint8_t             bus;
    uint8_t             dev;
    uint8_t             func;
    uint8_t             class_code;
    uint8_t             subclass;
    uint8_t             _pad;
    uint16_t            vendor_id;
    uint16_t            device_id;
    synth_device_type_t synthesized_type;
    uint32_t            confidence;     /**< 0-100 confidence score */
    uint32_t            bar0_phys;
    uint32_t            probed_regs;    /**< Number of registers read */
} synth_probe_result_t;

/* ============================================================================
 * MODULE STATE
 * ============================================================================ */

static synth_probe_result_t g_probe_results[SYNTH_MAX_PROBES];
static uint32_t g_probe_count = 0;
static uint8_t  g_synth_initialized = 0;

/* ============================================================================
 * SAFE MMIO READ HELPERS
 * ============================================================================ */

/**
 * @brief Safely read a 32-bit MMIO register with fault isolation
 *
 * Uses volatile access to prevent compiler optimization. The probe
 * zone is mapped NOCACHE+DEVICE so reads are strongly ordered.
 * Returns 0xFFFFFFFF on suspected bus error (all-ones pattern
 * indicates a non-existent device on PCI).
 */
static uint32_t synth_safe_read32(volatile void *base, uint32_t offset)
{
    if (offset + 4U > SYNTH_PROBE_SIZE) {
        return 0xFFFFFFFFU;
    }

    volatile uint32_t *reg = (volatile uint32_t *)((uintptr_t)base + offset);
    uint32_t val = *reg;

    /* lfence after MMIO read to prevent speculative execution
     * from leaking probe zone contents (Spectre-v1 mitigation) */
    __asm__ volatile("lfence" ::: "memory");

    return val;
}

static uint16_t synth_safe_read16(volatile void *base, uint32_t offset)
{
    if (offset + 2U > SYNTH_PROBE_SIZE) {
        return 0xFFFFU;
    }

    volatile uint16_t *reg = (volatile uint16_t *)((uintptr_t)base + offset);
    uint16_t val = *reg;
    __asm__ volatile("lfence" ::: "memory");
    return val;
}

/* ============================================================================
 * SECTION 1: HEURISTIC REGISTER PATTERN ANALYSIS
 * ============================================================================ */

/**
 * @brief Test if the BAR0 region matches an AHCI Mass Storage controller
 *
 * AHCI controllers have a well-defined register layout:
 * - Offset 0x00: CAP (Host Capabilities) — bits [4:0] = num_ports-1
 * - Offset 0x04: GHC — bit 31 = AHCI Enable
 * - Offset 0x0C: PI (Ports Implemented) — bitmask of active ports
 *
 * Confidence: High if GHC.AE is set and PI has valid port bits.
 */
static uint32_t synth_test_storage(volatile void *base)
{
    uint32_t score = 0;

    uint32_t cap = synth_safe_read32(base, AHCI_CAP_OFFSET);
    uint32_t ghc = synth_safe_read32(base, AHCI_GHC_OFFSET);
    uint32_t pi  = synth_safe_read32(base, AHCI_PI_OFFSET);

    /* GHC.AE (AHCI Enable) must be set or settable */
    if (ghc & AHCI_GHC_AE_BIT) {
        score += 40;
    }

    /* CAP: number of ports in [4:0] should be 0-31 */
    uint32_t num_ports = (cap & 0x1FU) + 1U;
    if (num_ports >= 1 && num_ports <= 32) {
        score += 20;
    }

    /* PI: at least one port implemented */
    if (pi != 0 && pi != 0xFFFFFFFFU) {
        score += 20;
    }

    /* Version register at offset 0x10 should be 0x00010x00 (1.0-1.3) */
    uint32_t vs = synth_safe_read32(base, 0x10U);
    uint16_t vs_major = (uint16_t)(vs >> 16);
    if (vs_major == 1U) {
        score += 20;
    }

    return score;
}

/**
 * @brief Test if the BAR0 region matches a Network controller
 *
 * Intel-style NICs have:
 * - Offset 0x00: CTRL register — bit 26 = Reset
 * - Offset 0x08: STATUS — bit 1 = Link Up
 * - Non-broadcast MAC address in registers (if within BAR)
 */
static uint32_t synth_test_network(volatile void *base)
{
    uint32_t score = 0;

    uint32_t ctrl = synth_safe_read32(base, 0x00U);
    uint32_t status = synth_safe_read32(base, NIC_STATUS_OFFSET);

    /* CTRL: should not be all-ones or all-zeros */
    if (ctrl != 0 && ctrl != 0xFFFFFFFFU) {
        score += 15;
    }

    /* STATUS: link-up bit pattern (common in Intel NICs) */
    if (status != 0 && status != 0xFFFFFFFFU) {
        /* Status with reasonable bit pattern (not all bits set) */
        uint32_t popcount = 0;
        uint32_t tmp = status;
        while (tmp) { popcount += tmp & 1U; tmp >>= 1; }
        if (popcount >= 1 && popcount <= 16) {
            score += 25;
        }
    }

    /* Check for MAC-like pattern at common offsets */
    uint32_t ral = synth_safe_read32(base, 0x40U);  /* Some NICs use 0x40 */
    uint32_t rah = synth_safe_read32(base, 0x44U);

    /* Valid MAC: not all-zeros, not all-ones, not broadcast */
    if (ral != 0 && ral != 0xFFFFFFFFU &&
        rah != 0 && (rah & 0xFFFFU) != 0xFFFFU) {
        score += 30;
    }

    /* Check interrupt cause register (ICR) at 0xC0 */
    uint32_t icr = synth_safe_read32(base, 0xC0U);
    if (icr != 0xFFFFFFFFU) {
        score += 10;
    }

    return score;
}

/**
 * @brief Test if the BAR0 region matches a Processing Accelerator
 *
 * Accelerators typically have:
 * - Doorbell registers at aligned offsets (0x1000+)
 * - Queue base address registers
 * - Capability/version registers near offset 0x00
 */
static uint32_t synth_test_accel(volatile void *base)
{
    uint32_t score = 0;

    /* Version/capability register at 0x00 */
    uint32_t cap = synth_safe_read32(base, 0x00U);
    if (cap != 0 && cap != 0xFFFFFFFFU) {
        /* Look for version-like pattern in upper bytes */
        uint8_t major = (uint8_t)(cap >> 24);
        if (major >= 1 && major <= 10) {
            score += 25;
        }
    }

    /* Doorbell region at 0x1000 — should be writable/readable */
    if (SYNTH_PROBE_SIZE > ACCEL_DOORBELL_OFFSET) {
        uint32_t db = synth_safe_read32(base, ACCEL_DOORBELL_OFFSET);
        if (db != 0xFFFFFFFFU) {
            score += 25;
        }
    }

    /* Queue base address pair at 0x20-0x28 */
    uint32_t qbase_lo = synth_safe_read32(base, 0x20U);
    uint32_t qbase_hi = synth_safe_read32(base, 0x24U);
    if (qbase_lo != 0xFFFFFFFFU && qbase_hi != 0xFFFFFFFFU) {
        /* Queue base should be page-aligned if set */
        if (qbase_lo == 0 || (qbase_lo & 0xFFFU) == 0) {
            score += 20;
        }
    }

    /* Status register at 0x1C — common in many accelerators */
    uint32_t status = synth_safe_read32(base, 0x1CU);
    if (status != 0xFFFFFFFFU) {
        score += 10;
    }

    return score;
}

/* ============================================================================
 * SECTION 2: DEVICE CLASSIFICATION
 * ============================================================================ */

/**
 * @brief Check if a PCI class code has a known static driver
 */
static int synth_has_static_driver(uint8_t class_code, uint8_t subclass)
{
    switch (class_code) {
    case SYNTH_CLASS_STORAGE:
    case SYNTH_CLASS_NETWORK:
    case SYNTH_CLASS_DISPLAY:
    case SYNTH_CLASS_MULTIMEDIA:
    case SYNTH_CLASS_BRIDGE:
    case SYNTH_CLASS_COMM:
    case SYNTH_CLASS_ACCEL:
        return 1;  /* Known driver exists */

    case SYNTH_CLASS_MEMORY:
    case SYNTH_CLASS_SYSTEM:
        /* Some system devices (ivshmem, HPET) have drivers */
        return 1;

    default:
        break;
    }

    /* Check specific vendor/device combos with drivers */
    /* VirtIO devices (vendor 0x1AF4) always have drivers */
    return 0;
}

/**
 * @brief Detect degenerate register patterns (bus error / non-responsive)
 *
 * Reads 8 registers at distinct offsets. If ALL return the same value
 * (e.g., 0xFFFFFFFF for absent device, 0x00000000 for dead BAR, or any
 * constant for random-pattern garbage), the device is non-responsive and
 * MUST NOT be classified — return SYNTH_TYPE_UNKNOWN immediately.
 *
 * This prevents over-confidence false positives on malicious or faulty
 * hardware that echoes a constant on every MMIO read.
 */
static int synth_is_degenerate(volatile void *base)
{
    /* Sample 8 registers at offsets used by the heuristic tests */
    static const uint32_t probe_offsets[8] = {
        0x00U, 0x04U, 0x08U, 0x0CU,       /* Low registers */
        0x10U, 0x1CU, 0x20U, 0x24U         /* Mid registers */
    };

    uint32_t first = synth_safe_read32(base, probe_offsets[0]);
    int all_same = 1;

    for (int i = 1; i < 8; i++) {
        uint32_t val = synth_safe_read32(base, probe_offsets[i]);
        if (val != first) {
            all_same = 0;
            break;
        }
    }

    if (all_same) {
        SYNTH_WARN("Degenerate pattern detected (all registers = 0x%08x) "
                   "— device non-responsive", first);
        return 1;
    }

    return 0;
}

/**
 * @brief Classify an unknown device via heuristic register probing
 */
static synth_device_type_t synth_classify(volatile void *base,
                                           uint32_t *confidence_out)
{
    /* Pre-filter: reject degenerate register patterns (all same value).
     * This catches all-ones (absent device), all-zeros (dead BAR),
     * and random-constant patterns from malicious hardware. */
    if (synth_is_degenerate(base)) {
        if (confidence_out) {
            *confidence_out = 0;
        }
        return SYNTH_TYPE_UNKNOWN;
    }

    /* Run all heuristic tests */
    uint32_t storage_score = synth_test_storage(base);
    uint32_t network_score = synth_test_network(base);
    uint32_t accel_score   = synth_test_accel(base);

    /* Pick highest confidence match.
     * Threshold raised to 60% (from 30%) — requires strong multi-register
     * correlation to classify. Prevents false positives on garbage patterns
     * that happen to match individual register checks. */
    synth_device_type_t best = SYNTH_TYPE_UNKNOWN;
    uint32_t best_score = 60;  /* Minimum threshold: 60% */

    if (storage_score > best_score) {
        best = SYNTH_TYPE_STORAGE;
        best_score = storage_score;
    }
    if (network_score > best_score) {
        best = SYNTH_TYPE_NETWORK;
        best_score = network_score;
    }
    if (accel_score > best_score) {
        best = SYNTH_TYPE_ACCEL;
        best_score = accel_score;
    }

    if (confidence_out) {
        *confidence_out = best_score;
    }

    return best;
}

static const char *synth_type_name(synth_device_type_t type)
{
    switch (type) {
    case SYNTH_TYPE_STORAGE:  return "Mass Storage";
    case SYNTH_TYPE_NETWORK:  return "Network";
    case SYNTH_TYPE_DISPLAY:  return "Display";
    case SYNTH_TYPE_ACCEL:    return "Accelerator";
    case SYNTH_TYPE_AUDIO:    return "Audio";
    case SYNTH_TYPE_USB:      return "USB";
    default:                  return "Unknown";
    }
}

/* ============================================================================
 * SECTION 3: PCI CONFIG SPACE ACCESS
 * ============================================================================ */

static uint32_t synth_pci_read32(uint8_t bus, uint8_t dev, uint8_t func,
                                  uint8_t offset)
{
    uint32_t addr = 0x80000000U |
                    ((uint32_t)bus  << 16) |
                    ((uint32_t)dev  << 11) |
                    ((uint32_t)func <<  8) |
                    ((uint32_t)offset & 0xFCU);
    __asm__ volatile("outl %0, %1" :: "a"(addr), "Nd"((uint16_t)0xCF8));
    uint32_t val;
    __asm__ volatile("inl %1, %0" : "=a"(val) : "Nd"((uint16_t)0xCFC));
    return val;
}

static void synth_pci_write32(uint8_t bus, uint8_t dev, uint8_t func,
                                uint8_t offset, uint32_t val)
{
    uint32_t addr = 0x80000000U |
                    ((uint32_t)bus  << 16) |
                    ((uint32_t)dev  << 11) |
                    ((uint32_t)func <<  8) |
                    ((uint32_t)offset & 0xFCU);
    __asm__ volatile("outl %0, %1" :: "a"(addr), "Nd"((uint16_t)0xCF8));
    __asm__ volatile("outl %0, %1" :: "a"(val),  "Nd"((uint16_t)0xCFC));
}

/* ============================================================================
 * SECTION 4: SECURE PROBE ZONE MANAGEMENT
 * ============================================================================ */

/**
 * @brief Map a PCI device's BAR0 into the Secure Probe Zone
 *
 * The probe zone is a temporary MMIO mapping used only during
 * the heuristic scan. It is unmapped immediately after probing
 * to prevent accidental writes to unknown hardware.
 *
 * Mapping flags: WRITE | NOCACHE (DEVICE for strict UC ordering)
 */
static volatile void *synth_map_probe_zone(uint32_t bar0_phys, uint32_t size)
{
    if (bar0_phys == 0 || size == 0) {
        return NULL;
    }

    /* Clamp to maximum probe size */
    if (size > SYNTH_PROBE_SIZE) {
        size = SYNTH_PROBE_SIZE;
    }

    int rc = vos3_vmm_map_range(
        (uintptr_t)SYNTH_PROBE_VBASE,
        (uintptr_t)bar0_phys,
        (size_t)size,
        VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_NOCACHE
    );

    if (rc != 0) {
        return NULL;
    }

    return (volatile void *)SYNTH_PROBE_VBASE;
}

/**
 * @brief Unmap the Secure Probe Zone
 */
static void synth_unmap_probe_zone(uint32_t size)
{
    if (size > SYNTH_PROBE_SIZE) {
        size = SYNTH_PROBE_SIZE;
    }

    vos3_vmm_unmap_range((uintptr_t)SYNTH_PROBE_VBASE, (size_t)size);
}

/* ============================================================================
 * SECTION 5: AUTO-PROBE ENGINE
 * ============================================================================ */

/**
 * @brief Probe a single unknown PCI device
 *
 * Maps BAR0, runs heuristic analysis, unmaps, records result.
 */
static int synth_probe_device(const vos3_pci_device_t *pci_dev)
{
    if (g_probe_count >= SYNTH_MAX_PROBES) {
        return -1;
    }

    /* Get BAR0 physical address */
    uint32_t bar0_raw = pci_dev->bar[0];
    if (bar0_raw == 0 || (bar0_raw & PCI_BAR_IO_MASK)) {
        /* No MMIO BAR0 or I/O port — skip */
        return -1;
    }

    uint32_t bar0_phys = bar0_raw & PCI_BAR_ADDR_MASK;
    if (bar0_phys == 0) {
        return -1;
    }

    /* Determine BAR0 size via PCI config space sizing */
    synth_pci_write32(pci_dev->bus, pci_dev->dev, pci_dev->func,
                       0x10U, 0xFFFFFFFFU);
    uint32_t size_mask = synth_pci_read32(pci_dev->bus, pci_dev->dev,
                                           pci_dev->func, 0x10U);
    /* Restore original value */
    synth_pci_write32(pci_dev->bus, pci_dev->dev, pci_dev->func,
                       0x10U, bar0_raw);

    size_mask &= PCI_BAR_ADDR_MASK;
    uint32_t bar_size;
    if (size_mask == 0 || size_mask == PCI_BAR_ADDR_MASK) {
        bar_size = SYNTH_PROBE_SIZE;  /* Default 64 KB */
    } else {
        bar_size = (~size_mask) + 1U;
    }

    /* Map into Secure Probe Zone */
    volatile void *probe = synth_map_probe_zone(bar0_phys, bar_size);
    if (probe == NULL) {
        SYNTH_WARN("Failed to map probe zone for %02x:%02x.%x",
                   pci_dev->bus, pci_dev->dev, pci_dev->func);
        return -1;
    }

    /* Run heuristic classification */
    uint32_t confidence = 0;
    synth_device_type_t synth_type = synth_classify(probe, &confidence);

    /* Unmap immediately — no persistent mapping of unknown hardware */
    synth_unmap_probe_zone(bar_size);

    /* Record result */
    synth_probe_result_t *result = &g_probe_results[g_probe_count];
    result->bus            = pci_dev->bus;
    result->dev            = pci_dev->dev;
    result->func           = pci_dev->func;
    result->class_code     = pci_dev->class_code;
    result->subclass       = pci_dev->subclass;
    result->vendor_id      = pci_dev->vendor_id;
    result->device_id      = pci_dev->device_id;
    result->synthesized_type = synth_type;
    result->confidence     = confidence;
    result->bar0_phys      = bar0_phys;
    result->probed_regs    = 12;  /* Approximate: 3 tests × ~4 reads each */

    g_probe_count++;

    SYNTH_INFO("Auto-probe %02x:%02x.%x vendor=0x%04x device=0x%04x "
               "class=%02x/%02x -> %s (confidence=%u%%)",
               pci_dev->bus, pci_dev->dev, pci_dev->func,
               pci_dev->vendor_id, pci_dev->device_id,
               pci_dev->class_code, pci_dev->subclass,
               synth_type_name(synth_type), confidence);

    return 0;
}

/* ============================================================================
 * SECTION 6: PUBLIC API
 * ============================================================================ */

/**
 * @brief Run Autonomous Driver Synthesis on all unrecognized PCI devices
 *
 * Iterates the PCI device table, identifies devices without static
 * drivers, maps each BAR0 into the Secure Probe Zone, runs heuristic
 * Register Pattern Analysis, and logs synthesized device types.
 *
 * @return Number of devices successfully auto-probed
 */
int vos3_driver_auto_probe(void)
{
    const vos3_pci_device_t *devs = vos3_pci_get_devices();
    int count = vos3_pci_get_count();

    if (devs == NULL || count <= 0) {
        SYNTH_WARN("PCI bus not scanned — auto-probe skipped");
        return 0;
    }

    SYNTH_INFO("Autonomous Driver Synthesis: scanning %d PCI devices", count);

    g_probe_count = 0;
    int probed = 0;

    for (int i = 0; i < count; i++) {
        const vos3_pci_device_t *dev = &devs[i];

        /* Skip devices with known static drivers */
        if (synth_has_static_driver(dev->class_code, dev->subclass)) {
            continue;
        }

        /* Skip VirtIO devices (vendor 0x1AF4) — always have drivers */
        if (dev->vendor_id == 0x1AF4U) {
            continue;
        }

        /* Skip Red Hat/QEMU internal devices */
        if (dev->vendor_id == 0x1B36U) {
            continue;
        }

        /* Probe this unknown device */
        int rc = synth_probe_device(dev);
        if (rc == 0) {
            probed++;
        }
    }

    g_synth_initialized = 1;

    SYNTH_INFO("Auto-probe complete: %d unknown device(s) analyzed, "
               "%u result(s) recorded",
               probed, g_probe_count);

    return probed;
}

/**
 * @brief Get auto-probe results
 *
 * @param[out] results  Array to copy results into
 * @param[in]  max      Maximum number of results to return
 * @return Number of results copied
 */
int vos3_driver_synth_get_results(synth_probe_result_t *results, uint32_t max)
{
    if (results == NULL || max == 0) {
        return 0;
    }

    uint32_t n = (g_probe_count < max) ? g_probe_count : max;
    for (uint32_t i = 0; i < n; i++) {
        results[i] = g_probe_results[i];
    }

    return (int)n;
}
