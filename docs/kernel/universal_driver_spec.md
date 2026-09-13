# VOS3 Universal Driver Specification
## VOS-UDrv v1.0 — Kernel Driver Contract
**v20.2.0 | 2026-04-24**

---

## 1. Purpose

VOS-UDrv defines the contract every hardware driver in VOS3 must satisfy. A driver that implements this interface:

- Runs in its own ivshmem zone (hardware fault cannot corrupt other zones)
- Gets zero-copy DMA via the VBus Warp Drive
- Receives power state transitions via VBus `DRIVER_SUSPEND`/`DRIVER_RESUME`
- Is enumerable from Python via `PCI_LIST` without kernel recompile

Existing reference implementations: `kernel/src/drivers/pci.c`, `kernel/src/drivers/npu.c`.

---

## 2. Driver Struct — `vos3_driver_t`

```c
/* kernel/include/vos/driver.h */

typedef enum vos3_power_state {
    VOS3_PWR_D0      = 0,   /* Fully operational */
    VOS3_PWR_D1      = 1,   /* Light sleep — context preserved */
    VOS3_PWR_D3COLD  = 3,   /* Power removed — state must be saved */
} vos3_power_state_t;

typedef struct vos3_dma_req {
    uint64_t  phys_src;    /* Physical address of source buffer */
    uint64_t  phys_dst;    /* Physical address of destination buffer */
    size_t    len;         /* Transfer length in bytes */
    uint32_t  flags;       /* VOS3_DMA_READ | VOS3_DMA_WRITE | VOS3_DMA_FENCE */
    void    (*completion)(struct vos3_dma_req *req, int status);
} vos3_dma_req_t;

#define VOS3_DMA_READ    (1U << 0)
#define VOS3_DMA_WRITE   (1U << 1)
#define VOS3_DMA_FENCE   (1U << 2)   /* Issue mfence before completion */

typedef struct vos3_driver {
    /* Identity */
    const char   *name;           /* ASCII driver name, max 31 chars */
    uint16_t      vendor_id;      /* PCI vendor ID (0 = non-PCI driver) */
    uint16_t      device_id;      /* PCI device ID */

    /* Lifecycle */
    int  (*probe)(uint32_t pci_bdf);      /* Return 0 if device claimed */
    int  (*init)(void);                   /* Allocate resources */
    void (*shutdown)(void);               /* Release resources */

    /* Runtime */
    void (*irq_handler)(uint8_t vector);  /* MSI-X handler (must be fast) */
    int  (*dma_submit)(vos3_dma_req_t *req);  /* Enqueue DMA transfer */
    int  (*power_set)(vos3_power_state_t state);

    /* Diagnostics */
    void (*dump_stats)(void);             /* Log stats to VOS3_INFO */

    /* Security */
    uint8_t ivshmem_zone_id;   /* Zone this driver owns (for zone_base ACL) */
} vos3_driver_t;
```

---

## 3. Memory Model

### 3.1 DMA-Coherent Allocation

```c
/* Allocate physically contiguous, cache-coherent DMA buffer */
void *vos3_dma_alloc(size_t size, uint64_t *phys_out);
void  vos3_dma_free(void *va, size_t size);
```

**Rule:** All driver DMA buffers must be allocated via `vos3_dma_alloc`. Direct use of `kmalloc` for DMA is forbidden — the PMM does not guarantee physical contiguity.

### 3.2 MMIO Mapping

```c
/* Map device MMIO BAR into kernel virtual address space */
void *vos3_mmio_map(uint64_t phys_base, size_t size);
```

**Rule:** All MMIO maps are validated against the ivshmem `zone_base()` ownership check. A driver may only map MMIO into its assigned `ivshmem_zone_id`. Attempting to map outside the owned zone returns NULL and logs an audit event.

### 3.3 ivshmem Zone Assignment

Each driver is assigned one ivshmem zone at `init()` time. The zone ID is stored in `vos3_driver_t.ivshmem_zone_id`. The zone provides:

- Isolated virtual address range (no overlap with other drivers or AI model slots)
- `owner_tid` enforcement: only the driver's kernel thread can call `zone_base(zone_id)`
- Scrub-on-shutdown: zone memory is zeroed when `shutdown()` is called

---

## 4. Interrupt Model

Preference order (highest to lowest):
1. **MSI-X** — per-vector routing, no sharing, lowest latency
2. **MSI** — single vector, mask-capable
3. **INTx (legacy PCI)** — shared line, requires `IOAPIC` redirection entry

Registration:
```c
/* Register irq_handler for this driver's MSI-X vector */
int vos3_irq_register(vos3_driver_t *drv, uint8_t vector);
```

The `irq_handler` runs in interrupt context. It must:
- Read and clear the device status register
- Signal a kernel semaphore or set a flag
- Return within 500 cycles

Blocking operations (DMA waits, logging) belong in the driver's kernel thread, not the IRQ handler.

---

## 5. Power State Transitions

VBus sends `DRIVER_SUSPEND <zone_id>` / `DRIVER_RESUME <zone_id>` when system pressure changes. The driver must implement:

```c
int power_set(vos3_power_state_t state) {
    switch (state) {
    case VOS3_PWR_D1:
        /* Flush pending DMA, save register state, reduce clock */
        return 0;
    case VOS3_PWR_D3COLD:
        /* Save all state to scratchpad, power off device */
        return 0;
    case VOS3_PWR_D0:
        /* Restore from scratchpad, re-initialize device */
        return 0;
    }
    return -1;  /* EINVAL */
}
```

**Timeout:** VBus waits 50ms for `power_set()` to return. If the driver hangs, the kernel marks the zone as `SUSPENDED_FORCED` and sends an audit event to the MMR.

---

## 6. VBus Integration

Every registered driver exposes its stats via the VBus `DRIVER_PRESSURE` command (extended). The Python backend can also call:

```
PCI_LIST                    → all registered driver names + PCI BDFs
DRIVER_SUSPEND <zone_id>    → transition driver to D1
DRIVER_RESUME  <zone_id>    → transition driver back to D0
```

New drivers do not require a new VBus command — they are automatically enumerable via `PCI_LIST` once registered.

---

## 7. Registration

Drivers register at boot via a `__attribute__((constructor))` entry or explicit call from `dev_init.c`:

```c
extern vos3_driver_t g_npu_driver;   /* defined in npu.c */

/* In dev_init.c: */
vos3_driver_register(&g_npu_driver);
```

`vos3_driver_register()` calls `probe()` for each PCI device. If `probe()` returns 0, the driver claims the device and `init()` is called.

---

## 8. Reference Implementations

| File | Driver | Notes |
|------|--------|-------|
| `kernel/src/drivers/pci.c` | PCI bus scanner | probe/init pattern, BDF enumeration |
| `kernel/src/drivers/npu.c` | NPU cluster driver | MMIO queue, DMA fence, 4 vendors |
| `kernel/src/drivers/vbus_transport.c` | VirtIO VBus | Ring buffer RX, zero-copy CRC |

Study `pci.c` for the probe/init lifecycle and `npu.c` for MMIO + DMA patterns before writing a new driver.

---

## 9. Security Checklist

Before submitting a driver for review:

- [ ] `dma_submit()` validates `req->len ≤ VOS3_DMA_MAX` (prevent buffer overflow)
- [ ] `irq_handler()` does not call `kmalloc`, `vos3_heap_alloc`, or any blocking primitive
- [ ] `init()` calls `vos3_dma_alloc` for all DMA buffers (never raw PMM alloc)
- [ ] MMIO mapped via `vos3_mmio_map` only (never raw `vmm_map`)
- [ ] `shutdown()` calls `vos3_dma_free` for every allocation in `init()`
- [ ] `ivshmem_zone_id` set before `vos3_driver_register()` is called
- [ ] `dump_stats()` does not log raw pointers or DMA addresses (PII/ASLR leak)
- [ ] `power_set(D3COLD)` saves all state needed for `power_set(D0)` restore
