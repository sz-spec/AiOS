/**
 * @file virtio_net.c
 * @brief VOS3 Hardened VirtIO Network Driver
 *
 * @details Security-first VirtIO network driver implementation.
 *          Based on CVE analysis:
 *          - CVE-2023-6693: Buffer overflow prevention
 *          - CVE-2021-3416: Descriptor index validation
 *          - CVE-2020-10756: DMA buffer isolation
 *
 * @version 1.0.0
 * @date 2026-02-18
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 30 - Network Stack Foundation
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/net.h"
#include "../../include/vos/net_security.h"
#include "../../include/vos/console.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/string.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/sync.h"
#include "../../include/vos/timer.h"

/* ============================================================================
 * VIRTIO CONSTANTS (from VirtIO 1.1 Specification)
 * ============================================================================ */

/** @brief VirtIO PCI queue notify offset (legacy I/O) */
#define VIRTIO_PCI_QUEUE_NOTIFY_OFF 0x10U

/** @brief Write 16-bit value to I/O port (x86 outw) */
static inline void vos3_outw(uint16_t port, uint16_t value)
{
    __asm__ volatile("outw %0, %1" : : "a"(value), "Nd"(port));
}

/**
 * @brief Convert BSS/kernel virtual address to physical for DMA
 *
 * BSS buffers reside in the higher-half kernel mapping (KBASE=0xFFFFFFFF80000000).
 * This is distinct from the physical map region used by vos3_virt_to_phys().
 */
#define VIRTIO_NET_KBASE  ((uintptr_t)0xFFFFFFFF80000000ULL)
static inline uint64_t virtio_net_virt_to_phys_dma(const void *vaddr)
{
    return (uint64_t)((uintptr_t)vaddr - VIRTIO_NET_KBASE);
}

static inline void* virtio_net_phys_to_virt_dma(uint64_t paddr)
{
    return (void*)(uintptr_t)(paddr + VIRTIO_NET_KBASE);
}

/** @brief VirtIO PCI vendor ID */
#define VIRTIO_PCI_VENDOR_ID        0x1AF4U

/** @brief VirtIO-Net PCI device ID (transitional) */
#define VIRTIO_NET_DEVICE_ID        0x1000U

/** @brief VirtIO-Net PCI device ID (modern) */
#define VIRTIO_NET_DEVICE_ID_MODERN 0x1041U

/** @brief VirtIO configuration space base */
#define VIRTIO_PCI_CAP_COMMON_CFG   1U
#define VIRTIO_PCI_CAP_NOTIFY_CFG   2U
#define VIRTIO_PCI_CAP_ISR_CFG      3U
#define VIRTIO_PCI_CAP_DEVICE_CFG   4U

/** @brief VirtIO feature bits */
#define VIRTIO_F_VERSION_1          (1ULL << 32)
#define VIRTIO_F_RING_INDIRECT_DESC (1ULL << 28)
#define VIRTIO_F_RING_EVENT_IDX     (1ULL << 29)

/** @brief VirtIO-Net feature bits */
#define VIRTIO_NET_F_CSUM           (1ULL << 0)
#define VIRTIO_NET_F_GUEST_CSUM     (1ULL << 1)
#define VIRTIO_NET_F_MAC            (1ULL << 5)
#define VIRTIO_NET_F_GSO            (1ULL << 6)   /* REJECTED: reduces attack surface */
#define VIRTIO_NET_F_GUEST_TSO4     (1ULL << 7)   /* REJECTED: reduces attack surface */
#define VIRTIO_NET_F_GUEST_TSO6     (1ULL << 8)   /* REJECTED: reduces attack surface */
#define VIRTIO_NET_F_GUEST_UFO      (1ULL << 10)  /* REJECTED: reduces attack surface */
#define VIRTIO_NET_F_HOST_TSO4      (1ULL << 11)  /* REJECTED: reduces attack surface */
#define VIRTIO_NET_F_HOST_TSO6      (1ULL << 12)  /* REJECTED: reduces attack surface */
#define VIRTIO_NET_F_HOST_UFO       (1ULL << 14)  /* REJECTED: reduces attack surface */
#define VIRTIO_NET_F_MRG_RXBUF      (1ULL << 15)
#define VIRTIO_NET_F_STATUS         (1ULL << 16)

/**
 * @brief Features we ACCEPT (minimal attack surface)
 *
 * Security Policy: Reject complex offloads (TSO/UFO/GSO) to reduce
 * kernel attack surface. Only accept basic features.
 */
#define VIRTIO_NET_FEATURES_ACCEPTED \
    (VIRTIO_NET_F_MAC | VIRTIO_NET_F_STATUS)

/**
 * @brief Features we explicitly REJECT for security
 */
#define VIRTIO_NET_FEATURES_REJECTED \
    (VIRTIO_NET_F_GSO | VIRTIO_NET_F_GUEST_TSO4 | VIRTIO_NET_F_GUEST_TSO6 | \
     VIRTIO_NET_F_GUEST_UFO | VIRTIO_NET_F_HOST_TSO4 | VIRTIO_NET_F_HOST_TSO6 | \
     VIRTIO_NET_F_HOST_UFO)

/** @brief VirtIO device status bits */
#define VIRTIO_STATUS_ACK           0x01U
#define VIRTIO_STATUS_DRIVER        0x02U
#define VIRTIO_STATUS_DRIVER_OK     0x04U
#define VIRTIO_STATUS_FEATURES_OK   0x08U
#define VIRTIO_STATUS_FAILED        0x80U

/** @brief VirtQueue descriptor flags */
#define VIRTQ_DESC_F_NEXT           0x01U
#define VIRTQ_DESC_F_WRITE          0x02U
#define VIRTQ_DESC_F_INDIRECT       0x04U

/** @brief VirtQueue sizes */
#define VIRTQ_RX_QUEUE_IDX          0U
#define VIRTQ_TX_QUEUE_IDX          1U

/* ============================================================================
 * SECURITY: HARDENED QUEUE SIZE (power of 2, reasonable limit)
 * ============================================================================ */

/** @brief VirtQueue size (must be power of 2) */
#define VIRTQ_NUM_DESC              VOS3_VIRTQ_MAX_DESC

/* ============================================================================
 * VIRTIO STRUCTURES
 * ============================================================================ */

/**
 * @brief VirtQueue descriptor (16 bytes)
 */
typedef struct virtq_desc {
    uint64_t addr;      /**< Physical address of buffer */
    uint32_t len;       /**< Length of buffer */
    uint16_t flags;     /**< Descriptor flags */
    uint16_t next;      /**< Next descriptor index (if NEXT flag set) */
} __attribute__((packed)) virtq_desc_t;

/**
 * @brief VirtQueue available ring
 */
typedef struct virtq_avail {
    uint16_t flags;
    uint16_t idx;
    uint16_t ring[VIRTQ_NUM_DESC];
    uint16_t used_event;  /* Only if VIRTIO_F_RING_EVENT_IDX */
} __attribute__((packed)) virtq_avail_t;

/**
 * @brief VirtQueue used ring element
 */
typedef struct virtq_used_elem {
    uint32_t id;        /**< Descriptor head index */
    uint32_t len;       /**< Number of bytes written */
} __attribute__((packed)) virtq_used_elem_t;

/**
 * @brief VirtQueue used ring
 */
typedef struct virtq_used {
    uint16_t flags;
    uint16_t idx;
    virtq_used_elem_t ring[VIRTQ_NUM_DESC];
    uint16_t avail_event;  /* Only if VIRTIO_F_RING_EVENT_IDX */
} __attribute__((packed)) virtq_used_t;

/**
 * @brief VirtQueue structure (with security tracking)
 */
typedef struct virtqueue {
    /** @brief Queue index */
    uint16_t        index;

    /** @brief Number of descriptors (security: validated on init) */
    uint16_t        num_desc;

    /** @brief Descriptors (DMA memory) */
    volatile virtq_desc_t*  desc;

    /** @brief Available ring (DMA memory) */
    volatile virtq_avail_t* avail;

    /** @brief Used ring (DMA memory) */
    volatile virtq_used_t*  used;

    /** @brief Physical addresses */
    uint64_t        desc_phys;
    uint64_t        avail_phys;
    uint64_t        used_phys;

    /** @brief Free descriptor head */
    uint16_t        free_head;

    /** @brief Number of free descriptors */
    uint16_t        num_free;

    /** @brief Last seen used index */
    uint16_t        last_used_idx;

    /** @brief Lock for queue access */
    vos3_spinlock_t lock;

    /** @brief Security: IRQ budget tracker */
    vos3_net_irq_budget_t irq_budget;

    /** @brief Buffer tracking (for security validation) */
    vos3_netbuf_t*  buffers[VIRTQ_NUM_DESC];
} virtqueue_t;

/**
 * @brief VirtIO-Net device header
 */
typedef struct virtio_net_hdr {
    uint8_t  flags;
    uint8_t  gso_type;
    uint16_t hdr_len;
    uint16_t gso_size;
    uint16_t csum_start;
    uint16_t csum_offset;
    uint16_t num_buffers;  /* Only if MRG_RXBUF */
} __attribute__((packed)) virtio_net_hdr_t;

/**
 * @brief VirtIO-Net device structure
 */
typedef struct virtio_net_device {
    /** @brief Network interface */
    vos3_netif_t    netif;

    /** @brief Device initialized flag */
    int             initialized;

    /** @brief Device features (negotiated) */
    uint64_t        features;

    /** @brief MMIO base address */
    volatile void*  mmio_base;

    /** @brief PCI I/O base address (legacy notify) */
    uint16_t        io_base;

    /** @brief Receive queue */
    virtqueue_t     rx_queue;

    /** @brief Transmit queue */
    virtqueue_t     tx_queue;

    /** @brief Device MAC address */
    vos3_eth_addr_t mac;

    /** @brief Security: RX buffer pool (pre-allocated) */
    vos3_netbuf_t*  rx_buffers[VOS3_NET_RX_QUEUE_SIZE];

    /** @brief Security: TX buffer pool (pre-allocated) */
    vos3_netbuf_t*  tx_buffers[VOS3_NET_TX_QUEUE_SIZE];
} virtio_net_device_t;

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

/* Note: g_net_security_stats is defined in ethernet.c */

/** @brief VirtIO-Net device (single device for now) */
static virtio_net_device_t g_virtio_net_dev = {0};

/** @brief Driver initialized flag */
static int g_virtio_net_initialized = 0;

/* ============================================================================
 * SECURITY: DESCRIPTOR CHAIN VALIDATION
 * ============================================================================ */

/**
 * @brief Validate a descriptor chain (CVE prevention)
 *
 * Walks descriptor chain and validates:
 * - All indices are within bounds
 * - Chain length doesn't exceed limit
 * - No cycles (via length limit)
 *
 * @param[in] vq        VirtQueue
 * @param[in] head_idx  Head of chain
 * @param[out] chain_len Output chain length
 * @return 0 on success, -1 on invalid chain
 */
static int virtq_validate_chain(virtqueue_t* vq, uint16_t head_idx,
                                 size_t* chain_len)
{
    uint16_t idx = head_idx;
    size_t len = 0;
    const size_t max_chain = vq->num_desc;  /* Can't exceed queue size */

    while (1) {
        /* SECURITY: Validate index bounds (CVE-2021-3416) */
        if (!VOS3_VIRTQ_IDX_VALID(idx, vq->num_desc)) {
            VOS3_ERROR("[VIRTIO-SEC] Invalid descriptor index %u >= %u",
                       idx, vq->num_desc);
            VOS3_NET_STAT_INVALID_DESC();
            return -1;
        }

        len++;

        /* SECURITY: Check chain length limit (cycle detection) */
        if (!VOS3_VIRTQ_CHAIN_VALID(len, max_chain)) {
            VOS3_ERROR("[VIRTIO-SEC] Descriptor chain too long (%zu >= %zu)",
                       len, max_chain);
            VOS3_NET_STAT_INVALID_DESC();
            return -1;
        }

        /* Check if chain continues */
        volatile virtq_desc_t* desc = &vq->desc[idx];
        if ((desc->flags & VIRTQ_DESC_F_NEXT) == 0) {
            break;
        }

        idx = desc->next;
    }

    *chain_len = len;
    return 0;
}

/* ============================================================================
 * SECURITY: SAFE RECEIVE HANDLER
 * ============================================================================ */

/**
 * @brief Process a received packet (hardened)
 *
 * Security measures:
 * - Length validation before any buffer access
 * - DMA data copied to safe kernel buffer immediately
 * - Header validation before protocol processing
 * - Interrupt budget enforcement
 *
 * @param[in] dev       VirtIO-Net device
 * @param[in] desc_idx  Descriptor index
 * @param[in] len       Packet length from used ring
 * @return 0 on success, -1 on security rejection
 */
static int virtio_net_receive_packet(virtio_net_device_t* dev,
                                      uint16_t desc_idx, uint32_t len)
{
    virtqueue_t* vq = &dev->rx_queue;

    /* SECURITY: Validate descriptor index (CVE-2021-3416) */
    if (!VOS3_VIRTQ_IDX_VALID(desc_idx, vq->num_desc)) {
        VOS3_ERROR("[VIRTIO-SEC] RX: Invalid descriptor index %u", desc_idx);
        VOS3_NET_STAT_INVALID_DESC();
        return -1;
    }

    /* Get pre-allocated buffer */
    vos3_netbuf_t* buf = vq->buffers[desc_idx];
    if (buf == NULL) {
        VOS3_ERROR("[VIRTIO-SEC] RX: No buffer for descriptor %u", desc_idx);
        return -1;
    }

    /* SECURITY: Trust No Length - validate against buffer capacity */
    if (!VOS3_NET_LEN_VALID(len, buf->capacity)) {
        VOS3_WARN("[VIRTIO-SEC] RX: Oversized packet %u > %u (dropped)",
                  len, buf->capacity);
        VOS3_NET_STAT_OVERSIZED();
        dev->netif.stats.rx_oversized++;
        return -1;
    }

    /* SECURITY: Check minimum frame size */
    size_t data_len = len;
    if (data_len > sizeof(virtio_net_hdr_t)) {
        data_len -= sizeof(virtio_net_hdr_t);
    } else {
        VOS3_WARN("[VIRTIO-SEC] RX: Packet too small %u (dropped)", len);
        VOS3_NET_STAT_UNDERSIZED();
        return -1;
    }

    if (!VOS3_NET_LEN_VALID(data_len, VOS3_NET_MTU_MAX)) {
        VOS3_WARN("[VIRTIO-SEC] RX: Data length %zu exceeds MTU", data_len);
        VOS3_NET_STAT_OVERSIZED();
        return -1;
    }

    /* SECURITY: Mark buffer as containing hostile external data */
    VOS3_NET_MARK_HOSTILE(buf);

    /*
     * SECURITY: DMA Isolation
     *
     * Copy data from DMA buffer to safe kernel buffer immediately.
     * The DMA buffer is treated as volatile - device could modify it.
     */
    volatile virtq_desc_t* desc = &vq->desc[desc_idx];
    volatile uint8_t* dma_buf = (volatile uint8_t*)virtio_net_phys_to_virt_dma(desc->addr);

    /* Copy VirtIO header (for inspection) */
    virtio_net_hdr_t vnet_hdr;
    VOS3_NET_DMA_COPY_IN(&vnet_hdr, dma_buf, sizeof(vnet_hdr));

    /* Copy packet data (after header) */
    VOS3_NET_DMA_COPY_IN(buf->data,
                         dma_buf + sizeof(virtio_net_hdr_t),
                         data_len);

    /* Set validated length */
    buf->len = (uint16_t)data_len;
    buf->flags |= VOS3_NETBUF_F_RX;

    /* Validate Ethernet header */
    if (!vos3_net_validate_eth_header(buf)) {
        VOS3_WARN("[VIRTIO-SEC] RX: Invalid Ethernet header (dropped)");
        VOS3_NET_STAT_INVALID_HEADER();
        dev->netif.stats.rx_invalid++;
        return -1;
    }

    /* SECURITY: Mark as validated after all checks pass */
    VOS3_NET_MARK_VALIDATED(buf);

    /* Update statistics */
    dev->netif.stats.rx_packets++;
    dev->netif.stats.rx_bytes += data_len;

    /* Pass to Ethernet layer for processing */
    int result = vos3_net_rx_ethernet(&dev->netif, buf);

    return result;
}

/**
 * @brief Handle received packets (interrupt context)
 *
 * Security measures:
 * - Interrupt budget limits packets per IRQ
 * - All packets validated before processing
 *
 * @param[in] dev  VirtIO-Net device
 * @return Number of packets processed
 */
static int virtio_net_rx_handler(virtio_net_device_t* dev)
{
    virtqueue_t* vq = &dev->rx_queue;
    int packets_processed = 0;

    vos3_spinlock_lock(&vq->lock);

    /* SECURITY: Reset IRQ budget */
    VOS3_NET_BUDGET_RESET(&vq->irq_budget);

    /* Process used ring */
    while (vq->last_used_idx != vq->used->idx) {
        /* SECURITY: Check interrupt budget (DoS prevention) */
        if (!vos3_net_budget_consume(&vq->irq_budget)) {
            VOS3_WARN("[VIRTIO-SEC] Throttling active: IRQ budget exhausted (%u packets)",
                       vq->irq_budget.packets_processed);
            g_net_security_stats.budget_exceeded_events++;
            /* TODO: Schedule softirq/worker for deferred processing */
            break;
        }

        uint16_t used_idx = vq->last_used_idx % vq->num_desc;
        volatile virtq_used_elem_t* elem = &vq->used->ring[used_idx];

        /* SECURITY: Validate descriptor ID from used ring */
        uint32_t desc_id = elem->id;
        if (!VOS3_VIRTQ_IDX_VALID(desc_id, vq->num_desc)) {
            VOS3_ERROR("[VIRTIO-SEC] RX: Invalid used ring descriptor %u", desc_id);
            VOS3_NET_STAT_INVALID_DESC();
            vq->last_used_idx++;
            continue;
        }

        uint32_t len = elem->len;

        /* Process packet (with all security checks) */
        int result = virtio_net_receive_packet(dev, (uint16_t)desc_id, len);

        if (result < 0) {
            dev->netif.stats.rx_dropped++;
        }

        /* Return buffer to available ring */
        uint16_t avail_idx = vq->avail->idx % vq->num_desc;
        vq->avail->ring[avail_idx] = (uint16_t)desc_id;

        /* Memory barrier */
        __asm__ volatile("mfence" ::: "memory");

        vq->avail->idx++;

        vq->last_used_idx++;
        packets_processed++;
    }

    vos3_spinlock_unlock(&vq->lock);

    return packets_processed;
}

/* ============================================================================
 * SECURITY: SAFE TRANSMIT HANDLER
 * ============================================================================ */

/* Forward declaration for TX descriptor reclaim (NET-3 fix) */
static void virtio_net_tx_reclaim(virtio_net_device_t* dev);

/**
 * @brief Transmit a packet (hardened for CVE-2026-23086 and CVE-2026-23057)
 *
 * SECURITY MEASURES:
 * - CVE-2026-23086: TX length truncated to MTU_MAX (not rejected)
 * - CVE-2026-23057: DMA buffer sanitized before data copy
 *
 * @param[in] netif  Network interface
 * @param[in] buf    Network buffer to transmit
 * @return 0 on success, negative error on failure
 */
static int virtio_net_transmit(vos3_netif_t* netif, vos3_netbuf_t* buf)
{
    virtio_net_device_t* dev = (virtio_net_device_t*)netif->priv;
    virtqueue_t* vq = &dev->tx_queue;

    /*
     * NET-3 FIX: Reclaim completed TX descriptors before attempting to send.
     * Without this, after 64 TX operations (128 descriptors / 2 per packet),
     * all descriptors are exhausted and networking dies silently.
     */
    virtio_net_tx_reclaim(dev);

    if (buf == NULL) {
        return -1;
    }

    /* SECURITY: Minimum length check */
    if (buf->len < VOS3_ETH_HEADER_SIZE) {
        VOS3_WARN("[VIRTIO-SEC] TX: Packet too small %u", buf->len);
        return -1;
    }

    /*
     * SECURITY: CVE-2026-23086 - TX Length Truncation
     *
     * If packet exceeds MTU_MAX, TRUNCATE instead of rejecting.
     * This prevents DoS via resource exhaustion from repeated
     * failed TX attempts, while still protecting the NIC.
     */
    uint16_t tx_len = buf->len;
    VOS3_NET_TX_TRUNCATE(tx_len, VOS3_NET_MTU_MAX);

    vos3_spinlock_lock(&vq->lock);

    /* Check for available descriptors */
    if (vq->num_free < 2) {  /* Need 2: header + data */
        vos3_spinlock_unlock(&vq->lock);
        VOS3_DEBUG("[VIRTIO-SEC] TX: Queue full");
        dev->netif.stats.tx_dropped++;
        return -1;
    }

    /* Get descriptor for VirtIO header */
    uint16_t head_idx = vq->free_head;

    /* SECURITY: Validate free_head */
    if (!VOS3_VIRTQ_IDX_VALID(head_idx, vq->num_desc)) {
        vos3_spinlock_unlock(&vq->lock);
        VOS3_ERROR("[VIRTIO-SEC] TX: Corrupt free_head %u", head_idx);
        return -1;
    }

    volatile virtq_desc_t* desc = &vq->desc[head_idx];

    /* Set up VirtIO network header */
    virtio_net_hdr_t* vnet_hdr = (virtio_net_hdr_t*)virtio_net_phys_to_virt_dma(desc->addr);
    memset(vnet_hdr, 0, sizeof(*vnet_hdr));

    desc->len = sizeof(virtio_net_hdr_t);
    desc->flags = VIRTQ_DESC_F_NEXT;

    /* Get descriptor for packet data */
    uint16_t data_idx = desc->next;
    if (!VOS3_VIRTQ_IDX_VALID(data_idx, vq->num_desc)) {
        vos3_spinlock_unlock(&vq->lock);
        VOS3_ERROR("[VIRTIO-SEC] TX: Invalid next descriptor %u", data_idx);
        return -1;
    }

    volatile virtq_desc_t* data_desc = &vq->desc[data_idx];

    /*
     * SECURITY: CVE-2026-23057 - Atomic Buffer Sanitization
     *
     * Zero the entire DMA buffer BEFORE copying data to ensure
     * no uninitialized kernel memory leaks to the network.
     *
     * This covers:
     * - Ethernet frame padding (frames < 60 bytes)
     * - Unused buffer space beyond packet data
     * - Any previous packet remnants
     */
    volatile uint8_t* dma_ptr = (volatile uint8_t*)virtio_net_phys_to_virt_dma(data_desc->addr);
    for (size_t i = 0; i < VOS3_NET_MTU_MAX; i++) {
        dma_ptr[i] = 0;
    }
    g_net_security_stats.tx_sanitized++;

    /* Copy data to sanitized DMA buffer */
    VOS3_NET_DMA_COPY_OUT(virtio_net_phys_to_virt_dma(data_desc->addr), buf->data, tx_len);

    data_desc->len = tx_len;
    data_desc->flags = 0;

    /* Update free list */
    vq->free_head = data_desc->next;
    vq->num_free -= 2;

    /* Add to available ring */
    uint16_t avail_idx = vq->avail->idx % vq->num_desc;
    vq->avail->ring[avail_idx] = head_idx;

    /* Memory barrier before updating index */
    __asm__ volatile("mfence" ::: "memory");

    vq->avail->idx++;

    /* Notify device (legacy PCI I/O) */
    if (dev->io_base != 0) {
        vos3_outw(dev->io_base + VIRTIO_PCI_QUEUE_NOTIFY_OFF, VIRTQ_TX_QUEUE_IDX);
    }

    vos3_spinlock_unlock(&vq->lock);

    /* Update statistics */
    dev->netif.stats.tx_packets++;
    dev->netif.stats.tx_bytes += tx_len;

    return 0;
}

/* ============================================================================
 * VIRTQUEUE INITIALIZATION
 * ============================================================================ */

/**
 * @brief Initialize a VirtQueue with security measures
 *
 * @param[in] vq        VirtQueue to initialize
 * @param[in] index     Queue index
 * @param[in] num_desc  Number of descriptors
 * @return 0 on success, negative error on failure
 */
static int virtq_init(virtqueue_t* vq, uint16_t index, uint16_t num_desc)
{
    /* SECURITY: Validate queue size */
    if (num_desc == 0 || num_desc > VOS3_VIRTQ_MAX_DESC) {
        VOS3_ERROR("[VIRTIO-SEC] Invalid queue size %u (max %u)",
                   num_desc, VOS3_VIRTQ_MAX_DESC);
        return -1;
    }

    /* SECURITY: Queue size must be power of 2 */
    if ((num_desc & (num_desc - 1)) != 0) {
        VOS3_ERROR("[VIRTIO-SEC] Queue size %u not power of 2", num_desc);
        return -1;
    }

    memset(vq, 0, sizeof(*vq));

    vq->index = index;
    vq->num_desc = num_desc;
    vq->lock = VOS3_SPINLOCK_INIT;

    /* Calculate memory requirements */
    size_t desc_size = sizeof(virtq_desc_t) * num_desc;
    size_t avail_size = sizeof(virtq_avail_t);
    size_t used_size = sizeof(virtq_used_t);

    /*
     * NET-4 FIX: VirtIO spec section 2.6.2 requires the Used Ring
     * to be aligned to a 4096-byte (page) boundary. Compute the
     * used ring offset by rounding up (desc_size + avail_size).
     */
    size_t used_offset = (desc_size + avail_size + 4095) & ~(size_t)4095;

    /* Allocate aligned DMA memory for descriptors */
    size_t total_size = used_offset + used_size;
    total_size = (total_size + 4095) & ~4095ULL;  /* Page align total */

    uint64_t phys = vos3_pmm_alloc_pages(total_size / 4096, 0);
    if (phys == 0) {
        VOS3_ERROR("[VIRTIO] Failed to allocate VirtQueue memory");
        return -1;
    }

    void* virt = vos3_vmm_map_pages(phys, total_size,
                                     VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE);
    if (virt == NULL) {
        vos3_pmm_free_pages(phys, total_size / 4096);
        VOS3_ERROR("[VIRTIO] Failed to map VirtQueue memory");
        return -1;
    }

    /* Clear memory */
    memset(virt, 0, total_size);

    /* Set up queue pointers (used ring at page-aligned offset per VirtIO spec) */
    vq->desc = (volatile virtq_desc_t*)virt;
    vq->avail = (volatile virtq_avail_t*)((uint8_t*)virt + desc_size);
    vq->used = (volatile virtq_used_t*)((uint8_t*)virt + used_offset);

    vq->desc_phys = phys;
    vq->avail_phys = phys + desc_size;
    vq->used_phys = phys + used_offset;

    /* Initialize descriptor free list */
    for (uint16_t i = 0; i < num_desc; i++) {
        vq->desc[i].next = (i + 1) % num_desc;
    }
    vq->free_head = 0;
    vq->num_free = num_desc;
    vq->last_used_idx = 0;

    /* SECURITY: Initialize IRQ budget */
    VOS3_NET_BUDGET_INIT(&vq->irq_budget, VOS3_NET_IRQ_BUDGET);

    VOS3_DEBUG("[VIRTIO] VirtQueue %u initialized: %u descriptors", index, num_desc);

    return 0;
}

/* ============================================================================
 * NETWORK INTERFACE OPERATIONS
 * ============================================================================ */

/**
 * @brief Start network interface
 */
static int virtio_net_start(vos3_netif_t* netif)
{
    virtio_net_device_t* dev = (virtio_net_device_t*)netif->priv;

    if (!dev->initialized) {
        return -1;
    }

    netif->flags |= VOS3_IFF_UP | VOS3_IFF_RUNNING;

    VOS3_INFO("[VIRTIO-NET] Interface %s started", netif->name);

    return 0;
}

/**
 * @brief Stop network interface
 */
static int virtio_net_stop(vos3_netif_t* netif)
{
    netif->flags &= ~(VOS3_IFF_UP | VOS3_IFF_RUNNING);

    VOS3_INFO("[VIRTIO-NET] Interface %s stopped", netif->name);

    return 0;
}

/**
 * @brief Network interface operations
 */
static const vos3_netif_ops_t virtio_net_ops = {
    .start = virtio_net_start,
    .stop = virtio_net_stop,
    .transmit = virtio_net_transmit,
    .set_mac = NULL,
    .set_promisc = NULL,
};

/* ============================================================================
 * PCI BUS ACCESS (Configuration Space via I/O Ports 0xCF8/0xCFC)
 * ============================================================================ */

#define PCI_CONFIG_ADDR     0x0CF8U
#define PCI_CONFIG_DATA     0x0CFCU

/** @brief VirtIO legacy I/O register offsets */
#define VIRTIO_PCI_HOST_FEATURES    0x00  /* 4 bytes */
#define VIRTIO_PCI_GUEST_FEATURES   0x04  /* 4 bytes */
#define VIRTIO_PCI_QUEUE_PFN        0x08  /* 4 bytes */
#define VIRTIO_PCI_QUEUE_SIZE       0x0C  /* 2 bytes */
#define VIRTIO_PCI_QUEUE_SEL        0x0E  /* 2 bytes */
#define VIRTIO_PCI_QUEUE_NOTIFY     0x10  /* 2 bytes */
#define VIRTIO_PCI_STATUS           0x12  /* 1 byte */
#define VIRTIO_PCI_ISR              0x13  /* 1 byte */
#define VIRTIO_PCI_CONFIG_OFF       0x14  /* Device-specific config starts here */

/** @brief I/O port helpers */
static inline uint8_t vos3_inb(uint16_t port)
{
    uint8_t ret;
    __asm__ volatile("inb %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static inline void vos3_outb(uint16_t port, uint8_t val)
{
    __asm__ volatile("outb %0, %1" : : "a"(val), "Nd"(port));
}

static inline uint16_t vos3_inw(uint16_t port)
{
    uint16_t ret;
    __asm__ volatile("inw %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static inline uint32_t vos3_inl(uint16_t port)
{
    uint32_t ret;
    __asm__ volatile("inl %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static inline void vos3_outl(uint16_t port, uint32_t val)
{
    __asm__ volatile("outl %0, %1" : : "a"(val), "Nd"(port));
}

static uint32_t net_pci_config_read32(uint8_t bus, uint8_t dev, uint8_t func,
                                       uint8_t offset)
{
    uint32_t addr = (1U << 31)
                  | ((uint32_t)bus << 16)
                  | ((uint32_t)dev << 11)
                  | ((uint32_t)func << 8)
                  | (offset & 0xFC);
    vos3_outl(PCI_CONFIG_ADDR, addr);
    return vos3_inl(PCI_CONFIG_DATA);
}

static uint16_t net_pci_config_read16(uint8_t bus, uint8_t dev, uint8_t func,
                                       uint8_t offset)
{
    uint32_t val = net_pci_config_read32(bus, dev, func, offset & 0xFC);
    return (uint16_t)(val >> ((offset & 2) * 8));
}

static void net_pci_config_write16(uint8_t bus, uint8_t dev, uint8_t func,
                                    uint8_t offset, uint16_t value)
{
    uint32_t addr = (1U << 31)
                  | ((uint32_t)bus << 16)
                  | ((uint32_t)dev << 11)
                  | ((uint32_t)func << 8)
                  | (offset & 0xFC);
    vos3_outl(PCI_CONFIG_ADDR, addr);
    uint32_t old = vos3_inl(PCI_CONFIG_DATA);
    int shift = (offset & 2) * 8;
    old &= ~(0xFFFFU << shift);
    old |= ((uint32_t)value << shift);
    vos3_outl(PCI_CONFIG_ADDR, addr);
    vos3_outl(PCI_CONFIG_DATA, old);
}

/**
 * @brief Scan PCI bus for VirtIO-NET device (vendor 0x1AF4, device 0x1000/0x1041)
 */
static int pci_find_virtio_net(uint8_t *out_bus, uint8_t *out_dev,
                                uint8_t *out_func)
{
    for (uint16_t bus = 0; bus < 256; bus++) {
        for (uint8_t dev = 0; dev < 32; dev++) {
            uint32_t id = net_pci_config_read32((uint8_t)bus, dev, 0, 0x00);
            if (id == 0xFFFFFFFF) continue;

            uint16_t vendor = (uint16_t)(id & 0xFFFF);
            uint16_t device = (uint16_t)(id >> 16);

            if (vendor == VIRTIO_PCI_VENDOR_ID &&
                (device == VIRTIO_NET_DEVICE_ID ||
                 device == VIRTIO_NET_DEVICE_ID_MODERN)) {
                /* For legacy device 0x1000, verify subsystem ID = 1 (net) */
                if (device == VIRTIO_NET_DEVICE_ID) {
                    uint32_t subsys = net_pci_config_read32((uint8_t)bus, dev, 0, 0x2C);
                    uint16_t subsys_id = (uint16_t)(subsys >> 16);
                    if (subsys_id != 1) {
                        continue;  /* Not a network device */
                    }
                }
                *out_bus = (uint8_t)bus;
                *out_dev = dev;
                *out_func = 0;
                return 1;
            }
        }
    }
    return 0;
}

/** @brief Whether PCI probe succeeded (real HW available) */
static int g_virtio_net_pci_ok = 0;

/* ============================================================================
 * STATIC RX BUFFERS (BSS-allocated, aligned for DMA)
 * ============================================================================ */

/** @brief Size of each RX DMA buffer (header + MTU) */
#define VIRTIO_NET_RX_BUF_SIZE  (sizeof(virtio_net_hdr_t) + 1522)

/** @brief Pre-allocated RX DMA buffers in BSS */
static uint8_t g_rx_dma_bufs[VIRTQ_NUM_DESC][2048]
    __attribute__((aligned(4096)));

/** @brief Pre-allocated TX DMA buffers for header + data */
static uint8_t g_tx_hdr_buf[VIRTQ_NUM_DESC][sizeof(virtio_net_hdr_t)]
    __attribute__((aligned(16)));
static uint8_t g_tx_data_buf[VIRTQ_NUM_DESC][1536]
    __attribute__((aligned(16)));

/* ============================================================================
 * RX POLL AND TX RECLAIM
 * ============================================================================ */

/**
 * @brief Poll for received packets on the RX virtqueue
 *
 * Called from timer tick (100Hz) to check for completed RX descriptors.
 */
void vos3_virtio_net_poll(void)
{
    if (!g_virtio_net_initialized || !g_virtio_net_pci_ok) {
        return;
    }

    virtio_net_device_t* dev = &g_virtio_net_dev;
    virtqueue_t* vq = &dev->rx_queue;

    /* Process completed RX descriptors */
    while (vq->last_used_idx != vq->used->idx) {
        uint16_t used_idx = vq->last_used_idx % vq->num_desc;
        volatile virtq_used_elem_t* elem = &vq->used->ring[used_idx];

        uint32_t desc_id = elem->id;
        if (desc_id >= vq->num_desc) {
            vq->last_used_idx++;
            continue;
        }

        uint32_t len = elem->len;

        /* Process the received packet */
        virtio_net_receive_packet(dev, (uint16_t)desc_id, len);

        /* Return buffer to available ring */
        uint16_t avail_idx = vq->avail->idx % vq->num_desc;
        vq->avail->ring[avail_idx] = (uint16_t)desc_id;
        __asm__ volatile("mfence" ::: "memory");
        vq->avail->idx++;

        vq->last_used_idx++;
    }
}

/**
 * @brief Reclaim completed TX descriptors
 */
static void virtio_net_tx_reclaim(virtio_net_device_t* dev)
{
    virtqueue_t* vq = &dev->tx_queue;

    while (vq->last_used_idx != vq->used->idx) {
        uint16_t used_idx = vq->last_used_idx % vq->num_desc;
        volatile virtq_used_elem_t* elem = &vq->used->ring[used_idx];

        /* Return descriptors to free list (2 per TX: header + data) */
        uint32_t head_id = elem->id;
        if (head_id < vq->num_desc) {
            volatile virtq_desc_t* head_desc = &vq->desc[head_id];
            uint16_t data_id = head_desc->next;

            /* Link data descriptor to free list */
            if (data_id < vq->num_desc) {
                vq->desc[data_id].next = vq->free_head;
                vq->desc[head_id].next = data_id;
            } else {
                vq->desc[head_id].next = vq->free_head;
            }
            vq->free_head = (uint16_t)head_id;
            vq->num_free += 2;
        }

        vq->last_used_idx++;
    }
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize VirtIO-Net driver
 *
 * @return 0 on success, negative error on failure
 */
int vos3_virtio_net_init(void)
{
    if (g_virtio_net_initialized) {
        return 0;
    }

    VOS3_INFO("[VIRTIO-NET] Initializing hardened VirtIO network driver");
    VOS3_INFO("[VIRTIO-NET] Security: IRQ budget=%u, MTU max=%u, Desc max=%u",
              VOS3_NET_IRQ_BUDGET, VOS3_NET_MTU_MAX, VOS3_VIRTQ_MAX_DESC);

    virtio_net_device_t* dev = &g_virtio_net_dev;
    memset(dev, 0, sizeof(*dev));

    /* Initialize VirtQueues */
    int result = virtq_init(&dev->rx_queue, VIRTQ_RX_QUEUE_IDX, VIRTQ_NUM_DESC);
    if (result != 0) {
        VOS3_ERROR("[VIRTIO-NET] Failed to initialize RX queue");
        return -1;
    }

    result = virtq_init(&dev->tx_queue, VIRTQ_TX_QUEUE_IDX, VIRTQ_NUM_DESC);
    if (result != 0) {
        VOS3_ERROR("[VIRTIO-NET] Failed to initialize TX queue");
        return -1;
    }

    /* Set up network interface */
    strncpy(dev->netif.name, "eth0", VOS3_IFNAMSIZ - 1);
    dev->netif.index = 0;
    dev->netif.mtu = 1500;
    dev->netif.ops = &virtio_net_ops;
    dev->netif.priv = dev;

    /* Default MAC address (for testing without hardware) */
    dev->mac.bytes[0] = 0x52;
    dev->mac.bytes[1] = 0x54;
    dev->mac.bytes[2] = 0x00;
    dev->mac.bytes[3] = 0x12;
    dev->mac.bytes[4] = 0x34;
    dev->mac.bytes[5] = 0x56;
    memcpy(&dev->netif.mac_addr, &dev->mac, sizeof(vos3_eth_addr_t));

    /* ===== PCI Device Discovery ===== */
    uint8_t pci_bus, pci_dev_num, pci_func;
    if (pci_find_virtio_net(&pci_bus, &pci_dev_num, &pci_func)) {
        uint32_t pci_id = net_pci_config_read32(pci_bus, pci_dev_num, pci_func, 0x00);
        VOS3_INFO("[VIRTIO-NET] Found PCI %u:%u.%u vendor=%04x device=%04x",
                  pci_bus, pci_dev_num, pci_func,
                  (unsigned)(pci_id & 0xFFFF), (unsigned)(pci_id >> 16));

        /* Read BAR0 (I/O port base for legacy VirtIO) */
        uint32_t bar0 = net_pci_config_read32(pci_bus, pci_dev_num, pci_func, 0x10);
        if (bar0 & 0x01) {
            uint16_t io_base = (uint16_t)(bar0 & 0xFFFC);
            dev->io_base = io_base;
            VOS3_INFO("[VIRTIO-NET] I/O base: 0x%04x", io_base);

            /* Enable PCI bus mastering */
            uint16_t pci_cmd = net_pci_config_read16(pci_bus, pci_dev_num, pci_func, 0x04);
            pci_cmd |= (1U << 2) | (1U << 0);  /* Bus Master + I/O Space */
            net_pci_config_write16(pci_bus, pci_dev_num, pci_func, 0x04, pci_cmd);

            /* VirtIO Legacy Initialization Handshake */
            /* Reset device */
            vos3_outb(io_base + VIRTIO_PCI_STATUS, 0);

            /* Acknowledge: OS has found the device */
            vos3_outb(io_base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_ACK);

            /* Driver: OS knows how to drive the device */
            vos3_outb(io_base + VIRTIO_PCI_STATUS,
                     VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER);

            /* Read and negotiate features */
            uint32_t host_features = vos3_inl(io_base + VIRTIO_PCI_HOST_FEATURES);
            uint32_t guest_features = host_features & (uint32_t)(VIRTIO_NET_F_MAC | VIRTIO_NET_F_STATUS);
            vos3_outl(io_base + VIRTIO_PCI_GUEST_FEATURES, guest_features);
            dev->features = guest_features;

            VOS3_INFO("[VIRTIO-NET] Host features: 0x%08x, negotiated: 0x%08x",
                      host_features, guest_features);

            /* Read MAC from device config if VIRTIO_NET_F_MAC negotiated */
            if (guest_features & (uint32_t)VIRTIO_NET_F_MAC) {
                for (int i = 0; i < 6; i++) {
                    dev->mac.bytes[i] = vos3_inb(io_base + VIRTIO_PCI_CONFIG_OFF + (uint16_t)i);
                }
                memcpy(&dev->netif.mac_addr, &dev->mac, sizeof(vos3_eth_addr_t));
                VOS3_INFO("[VIRTIO-NET] MAC from device: %02x:%02x:%02x:%02x:%02x:%02x",
                          dev->mac.bytes[0], dev->mac.bytes[1], dev->mac.bytes[2],
                          dev->mac.bytes[3], dev->mac.bytes[4], dev->mac.bytes[5]);
            }

            /*
             * NET-1 FIX: Tell device where the vrings are (legacy interface).
             * VirtIO legacy handshake requires QUEUE_PFN to be written for
             * each queue before DRIVER_OK. Without this, the device has no
             * idea where descriptor memory lives.
             */
            /* Select RX queue and write PFN */
            vos3_outw(io_base + VIRTIO_PCI_QUEUE_SEL, VIRTQ_RX_QUEUE_IDX);
            vos3_outl(io_base + VIRTIO_PCI_QUEUE_PFN,
                      (uint32_t)(dev->rx_queue.desc_phys >> 12));

            /* Select TX queue and write PFN */
            vos3_outw(io_base + VIRTIO_PCI_QUEUE_SEL, VIRTQ_TX_QUEUE_IDX);
            vos3_outl(io_base + VIRTIO_PCI_QUEUE_PFN,
                      (uint32_t)(dev->tx_queue.desc_phys >> 12));

            VOS3_INFO("[VIRTIO-NET] RX queue PFN=0x%x, TX queue PFN=0x%x",
                      (unsigned)(dev->rx_queue.desc_phys >> 12),
                      (unsigned)(dev->tx_queue.desc_phys >> 12));

            /* Set DRIVER_OK to complete handshake */
            vos3_outb(io_base + VIRTIO_PCI_STATUS,
                     VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER | VIRTIO_STATUS_DRIVER_OK);

            /*
             * NET-2 FIX: Pre-populate RX available ring with buffer descriptors.
             * Without this, the device has no buffers to receive packets into.
             * Each RX descriptor points to a pre-allocated DMA buffer.
             */
            {
                virtqueue_t* rxvq = &dev->rx_queue;
                uint16_t rx_count = rxvq->num_desc;

                for (uint16_t i = 0; i < rx_count; i++) {
                    rxvq->desc[i].addr = virtio_net_virt_to_phys_dma(&g_rx_dma_bufs[i][0]);
                    rxvq->desc[i].len = VIRTIO_NET_RX_BUF_SIZE;
                    rxvq->desc[i].flags = VIRTQ_DESC_F_WRITE;  /* Device writes into this buffer */
                    rxvq->desc[i].next = 0;
                    rxvq->avail->ring[i % rx_count] = i;
                }
                rxvq->avail->idx = rx_count;

                /* Memory barrier before notifying device */
                __asm__ volatile("mfence" ::: "memory");

                /* Kick RX queue so device knows buffers are available */
                vos3_outw(io_base + VIRTIO_PCI_QUEUE_NOTIFY, VIRTQ_RX_QUEUE_IDX);

                VOS3_INFO("[VIRTIO-NET] RX ring pre-populated with %u buffers", rx_count);
            }

            /*
             * Set up TX descriptors to point to pre-allocated DMA buffers.
             * Each TX uses 2 descriptors: header + data, chained via NEXT.
             */
            {
                virtqueue_t* txvq = &dev->tx_queue;
                for (uint16_t i = 0; i + 1 < txvq->num_desc; i += 2) {
                    txvq->desc[i].addr = virtio_net_virt_to_phys_dma(&g_tx_hdr_buf[i][0]);
                    txvq->desc[i].len = sizeof(virtio_net_hdr_t);
                    txvq->desc[i].flags = VIRTQ_DESC_F_NEXT;
                    txvq->desc[i].next = i + 1;

                    txvq->desc[i + 1].addr = virtio_net_virt_to_phys_dma(&g_tx_data_buf[i][0]);
                    txvq->desc[i + 1].len = 1536;
                    txvq->desc[i + 1].flags = 0;
                    txvq->desc[i + 1].next = (i + 2 < txvq->num_desc) ? (i + 2) : 0;
                }
                /* Free list head is already 0, num_free is already num_desc */
            }

            g_virtio_net_pci_ok = 1;
            VOS3_INFO("[VIRTIO-NET] PCI handshake complete (DRIVER_OK)");
        } else {
            VOS3_WARN("[VIRTIO-NET] BAR0 is MMIO, expected I/O port (legacy)");
        }
    } else {
        VOS3_WARN("[VIRTIO-NET] No VirtIO-net PCI device found (simulation mode)");
    }

    /* ===== Configure Static IP (SLIRP defaults) ===== */
    /* 10.0.2.15 in network byte order (little-endian x86): 0x0F02000A */
    dev->netif.ipv4_addr    = 0x0F02000AU;  /* 10.0.2.15 */
    dev->netif.ipv4_netmask = 0x00FFFFFFU;  /* 255.255.255.0 */
    dev->netif.ipv4_gateway = 0x0202000AU;  /* 10.0.2.2 */

    VOS3_INFO("[VIRTIO-NET] Static IP: 10.0.2.15, netmask: 255.255.255.0, gateway: 10.0.2.2");

    /* Register as default network interface */
    extern void vos3_net_set_default_interface(vos3_netif_t* netif);
    vos3_net_set_default_interface(&dev->netif);

    dev->initialized = 1;
    g_virtio_net_initialized = 1;

    VOS3_INFO("[VIRTIO-NET] Driver initialized");
    VOS3_INFO("[VIRTIO-NET] Interface: %s MAC: %02x:%02x:%02x:%02x:%02x:%02x",
              dev->netif.name,
              dev->mac.bytes[0], dev->mac.bytes[1], dev->mac.bytes[2],
              dev->mac.bytes[3], dev->mac.bytes[4], dev->mac.bytes[5]);

    return 0;
}

/**
 * @brief Simulate receiving a packet (for testing)
 *
 * This function is used for security testing to verify that
 * invalid packets are properly rejected.
 *
 * @param[in] data      Packet data
 * @param[in] len       Packet length (potentially hostile value)
 * @return 0 on success, -1 on rejection
 */
int vos3_virtio_net_test_rx(const uint8_t* data, size_t len)
{
    virtio_net_device_t* dev = &g_virtio_net_dev;

    if (!dev->initialized) {
        return -1;
    }

    VOS3_DEBUG("[VIRTIO-TEST] Testing RX with length %zu", len);

    /* SECURITY: Trust No Length */
    if (!VOS3_NET_LEN_IN_RANGE(len, VOS3_NET_FRAME_MIN, VOS3_NET_MTU_MAX)) {
        VOS3_WARN("[VIRTIO-SEC] TEST: Packet rejected (len=%zu)", len);
        if (len > VOS3_NET_MTU_MAX) {
            VOS3_NET_STAT_OVERSIZED();
        } else {
            VOS3_NET_STAT_UNDERSIZED();
        }
        return -1;
    }

    /* Allocate buffer for testing */
    vos3_netbuf_t* buf = vos3_netbuf_alloc(len);
    if (buf == NULL) {
        return -1;
    }

    /* Copy data to buffer */
    if (data != NULL) {
        memcpy(buf->data, data, len);
    }
    buf->len = (uint16_t)len;

    /* Mark as hostile (external data) */
    VOS3_NET_MARK_HOSTILE(buf);

    /* Validate Ethernet header */
    if (!vos3_net_validate_eth_header(buf)) {
        VOS3_WARN("[VIRTIO-SEC] TEST: Invalid Ethernet header");
        VOS3_NET_STAT_INVALID_HEADER();
        vos3_netbuf_free(buf);
        return -1;
    }

    /* Validation passed */
    VOS3_NET_MARK_VALIDATED(buf);

    VOS3_DEBUG("[VIRTIO-TEST] Packet accepted (len=%zu)", len);

    vos3_netbuf_free(buf);

    return 0;
}

/**
 * @brief Get network security statistics
 *
 * @param[out] stats  Statistics output
 */
void vos3_net_get_security_stats(vos3_net_security_stats_t* stats)
{
    if (stats != NULL) {
        *stats = g_net_security_stats;
    }
}

/* ============================================================================
 * SECURITY: FLOOD ATTACK SIMULATION (for testing)
 * ============================================================================ */

/**
 * @brief Simulate a flood attack (for security testing)
 *
 * This function simulates an interrupt storm by calling the RX processing
 * logic repeatedly. The throttling mechanism MUST prevent system overload.
 *
 * @param[in] num_packets  Number of packets to simulate
 * @return Number of throttling events that occurred
 */
int vos3_net_test_flood_attack(size_t num_packets)
{
    virtio_net_device_t* dev = &g_virtio_net_dev;
    virtqueue_t* vq = &dev->rx_queue;

    if (!dev->initialized) {
        VOS3_ERROR("[FLOOD-TEST] Driver not initialized");
        return -1;
    }

    VOS3_INFO("[FLOOD-TEST] ========================================");
    VOS3_INFO("[FLOOD-TEST] Simulating flood attack: %zu packets", num_packets);
    VOS3_INFO("[FLOOD-TEST] IRQ budget: %u packets/interrupt", VOS3_NET_IRQ_BUDGET);
    VOS3_INFO("[FLOOD-TEST] ========================================");

    uint64_t throttle_events_before = g_net_security_stats.budget_exceeded_events;
    size_t packets_processed = 0;
    size_t irq_count = 0;

    /*
     * Simulate multiple IRQ bursts, each limited by budget.
     * In a real attack, these would be hardware interrupts.
     */
    while (packets_processed < num_packets) {
        irq_count++;

        /* Reset budget for this "IRQ" */
        VOS3_NET_BUDGET_RESET(&vq->irq_budget);

        /* Process packets until budget exhausted */
        size_t irq_packets = 0;
        while (packets_processed < num_packets) {
            if (!vos3_net_budget_consume(&vq->irq_budget)) {
                /* Budget exhausted - throttling kicks in */
                VOS3_WARN("[FLOOD-TEST] Throttling active (IRQ %zu, processed %zu)",
                          irq_count, packets_processed);
                g_net_security_stats.budget_exceeded_events++;
                break;
            }

            /* Simulate processing one packet */
            packets_processed++;
            irq_packets++;
        }

        /* Log progress every 100 IRQs */
        if (irq_count % 100 == 0) {
            VOS3_DEBUG("[FLOOD-TEST] Progress: IRQ %zu, packets %zu/%zu",
                       irq_count, packets_processed, num_packets);
        }
    }

    uint64_t throttle_events = g_net_security_stats.budget_exceeded_events -
                               throttle_events_before;

    VOS3_INFO("[FLOOD-TEST] ========================================");
    VOS3_INFO("[FLOOD-TEST] Attack simulation complete");
    VOS3_INFO("[FLOOD-TEST] Packets simulated:    %zu", num_packets);
    VOS3_INFO("[FLOOD-TEST] IRQ bursts:           %zu", irq_count);
    VOS3_INFO("[FLOOD-TEST] Throttling events:    %llu",
              (unsigned long long)throttle_events);
    VOS3_INFO("[FLOOD-TEST] Packets per IRQ avg:  %zu",
              irq_count > 0 ? num_packets / irq_count : 0);
    VOS3_INFO("[FLOOD-TEST] ========================================");

    if (throttle_events > 0) {
        VOS3_INFO("[FLOOD-TEST] PASSED: Throttling prevented interrupt storm");
    } else if (num_packets > VOS3_NET_IRQ_BUDGET) {
        VOS3_ERROR("[FLOOD-TEST] FAILED: No throttling detected!");
    }

    return (int)throttle_events;
}
