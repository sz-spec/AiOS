/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 Universal Driver Contract
 * ===============================
 *
 *   [QUANTUM-LEAP-SCAFFOLD]  Operation HOME-DOMINANCE v21.0  2026-05-02
 *
 * Lifts docs/kernel/universal_driver_spec.md (v20.2.0, 2026-04-24)
 * into a real header file. v1 preserves the original single-vector
 * dispatch contract; v2 adds opt-in multi-vector MSI-X hooks needed
 * for modern NVIDIA Blackwell and Intel Gaudi-class NPUs (which
 * expose 64+ MSI-X vectors per device).
 *
 * Migration policy:
 *   - Existing single-vector drivers continue to work unchanged. They
 *     register vector 0 via the legacy irq_handler callback.
 *   - New drivers may set max_vectors > 1 and provide
 *     irq_register_vector / irq_set_affinity. Dispatch glue fans
 *     completions out to the per-vector handlers.
 *   - The kernel never silently changes max_vectors. A driver that
 *     wants to expose more vectors must publish them at probe time.
 *
 * STATUS: contract-level only. No driver in-tree consumes v2 today.
 * The struct shape is frozen so subsequent commits add irq_handler
 * implementations without further header churn.
 */

#ifndef VOS3_DRIVER_H
#define VOS3_DRIVER_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum vos3_power_state {
    VOS3_PWR_D0      = 0,   /* Fully operational                       */
    VOS3_PWR_D1      = 1,   /* Light sleep — context preserved         */
    VOS3_PWR_D3COLD  = 3,   /* Power removed — state must be saved     */
} vos3_power_state_t;

/* Forward declarations — concrete types live in driver.c per device. */
struct vos3_device;
struct vos3_dma_request;

/* ----- v1 contract (legacy, single-vector) ----- */

typedef struct vos3_driver_v1 {
    const char *name;
    uint16_t    pci_vendor_id;
    uint16_t    pci_device_id;

    /* Lifecycle */
    int  (*probe)(struct vos3_device *dev);
    int  (*init) (struct vos3_device *dev);
    void (*release)(struct vos3_device *dev);

    /* Single MSI-X vector — vector index implicit (0). */
    void (*irq_handler)(struct vos3_device *dev);

    /* DMA submit — driver owns IOMMU mapping + bounded-window guard. */
    int  (*dma_submit)(struct vos3_device *dev, struct vos3_dma_request *req);

    /* Power state transitions — invoked via VBus DRIVER_SUSPEND/RESUME. */
    int  (*power_set)(struct vos3_device *dev, vos3_power_state_t state);
} vos3_driver_v1_t;

/* ----- v2 contract (multi-vector MSI-X opt-in) -----
 *
 * Drivers that set vos3_driver_v2.max_vectors > 1 MUST also set
 * irq_register_vector. The dispatch glue calls irq_register_vector
 * per active vector at init time and routes per-vector completions
 * to the registered handler. Backwards compatible: if a v1 driver is
 * loaded into a v2 slot, max_vectors is implicitly 1 and only
 * irq_handler fires.
 *
 * irq_set_affinity is optional. When NULL the kernel pins all
 * vectors to the boot CPU.
 */
typedef void (*vos3_irq_vector_handler_t)(struct vos3_device *dev,
                                          uint16_t vector);

typedef struct vos3_driver_v2 {
    /* v1 fields — keep at top so a v2 pointer can be cast to v1*. */
    vos3_driver_v1_t v1;

    /* v2 extensions */
    uint16_t max_vectors;        /* Device-reported, capped at 256       */
    uint16_t _pad;
    uint32_t flags;              /* VOS3_DRIVER_FLAG_* (see below)       */

    int  (*irq_register_vector)(struct vos3_device *dev,
                                uint16_t vector,
                                vos3_irq_vector_handler_t handler);
    int  (*irq_set_affinity)(struct vos3_device *dev,
                             uint16_t vector,
                             uint32_t cpu_mask);

    /* Hot-plug hooks — Plug-and-Play vision for external NPUs.
     * present(): driver self-checks the device is still attached.
     *            Returns 1 if present, 0 if removed.
     * detach():  the kernel notifies the driver of imminent removal;
     *            driver must complete inflight ops + return resources.
     */
    int  (*present)(struct vos3_device *dev);
    void (*detach)(struct vos3_device *dev);
} vos3_driver_v2_t;

/* Legacy alias — pre-existing references to vos3_driver_t map to v2.
 * v1-only drivers may continue to use vos3_driver_v1_t directly. */
typedef vos3_driver_v2_t vos3_driver_t;

/* Flags */
#define VOS3_DRIVER_FLAG_MULTI_VECTOR        (1u << 0)
#define VOS3_DRIVER_FLAG_HOT_PLUG            (1u << 1)
#define VOS3_DRIVER_FLAG_IOMMU_REQUIRED      (1u << 2)
#define VOS3_DRIVER_FLAG_PRO_GATED           (1u << 3)

/* ----- Capacity advertisement helpers (driver → kernel at probe) ----- */

typedef struct vos3_device_caps {
    uint32_t pci_segment;        /* PCIe segment / DSAR cluster id        */
    uint32_t numa_node;
    uint32_t bar0_size;
    uint16_t supported_msix;     /* Hardware-reported max vectors         */
    uint16_t reserved;
} vos3_device_caps_t;

#ifdef __cplusplus
}
#endif

#endif /* VOS3_DRIVER_H */
