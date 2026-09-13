/**
 * @file pci.h
 * @brief VOS3 Generic PCI Bus Scanner
 *
 * @details Enumerates PCI bus 0-255, device 0-31, function 0-7.
 *          Stores discovered devices in a flat array for HAL use.
 *
 * @version 1.0.0
 * @date 2026-04-08
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase v20.0 — HAL Foundation
 */

#ifndef VOS3_PCI_H
#define VOS3_PCI_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * PCI DEVICE DESCRIPTOR
 * ============================================================================ */

/** @brief Maximum number of PCI devices tracked */
#define VOS3_PCI_MAX_DEVICES    64U

/** @brief PCI class codes for device classification */
#define PCI_CLASS_STORAGE       0x01U
#define PCI_CLASS_NETWORK       0x02U
#define PCI_CLASS_DISPLAY       0x03U
#define PCI_CLASS_MULTIMEDIA    0x04U
#define PCI_CLASS_MEMORY        0x05U
#define PCI_CLASS_BRIDGE        0x06U
#define PCI_CLASS_COMM          0x07U
#define PCI_CLASS_SYSTEM        0x08U

/** @brief PCI subclass codes for storage */
#define PCI_SUBCLASS_IDE        0x01U
#define PCI_SUBCLASS_FLOPPY     0x02U
#define PCI_SUBCLASS_AHCI       0x06U
#define PCI_SUBCLASS_NVME       0x08U

/** @brief PCI subclass for display */
#define PCI_SUBCLASS_VGA        0x00U

/** @brief Discovered PCI device entry */
typedef struct vos3_pci_device {
    uint8_t  bus;
    uint8_t  dev;
    uint8_t  func;
    uint16_t vendor_id;
    uint16_t device_id;
    uint8_t  class_code;
    uint8_t  subclass;
    uint8_t  prog_if;
    uint8_t  header_type;
    uint8_t  irq_line;
    uint8_t  _pad;
    uint32_t bar[6];
} vos3_pci_device_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Scan all PCI buses and populate the device table
 *
 * Must be called from kmain after memory init, before driver init.
 *
 * @return Number of devices found
 */
int vos3_pci_bus_scan(void);

/**
 * @brief Get the array of discovered PCI devices
 *
 * @return Pointer to device array (read-only)
 */
const vos3_pci_device_t *vos3_pci_get_devices(void);

/**
 * @brief Get the number of discovered PCI devices
 *
 * @return Device count
 */
int vos3_pci_get_count(void);

/**
 * @brief Find a PCI device by vendor/device ID
 *
 * @param vendor_id PCI vendor ID
 * @param device_id PCI device ID
 * @return Pointer to matching device, or NULL
 */
const vos3_pci_device_t *vos3_pci_find_device(uint16_t vendor_id, uint16_t device_id);

/**
 * @brief Get class name string for a PCI class code
 *
 * @param class_code PCI class code
 * @param subclass PCI subclass code
 * @return Human-readable string
 */
const char *vos3_pci_class_name(uint8_t class_code, uint8_t subclass);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_PCI_H */
