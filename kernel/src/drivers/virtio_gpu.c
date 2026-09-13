/**
 * @file virtio_gpu.c
 * @brief VOS3 VirtIO-GPU 3D Driver — PCI Discovery, Queue Init, Command Dispatch
 *
 * @details Implements the VirtIO-GPU device specification (OASIS virtio-v1.2,
 *          §5.7) using the legacy I/O port interface. Provides:
 *          - PCI bus scan for VirtIO-GPU device
 *          - Feature negotiation (virgl 3D, EDID, blob resources)
 *          - Split virtqueue management (controlq + cursorq)
 *          - 2D resource lifecycle (create, attach, transfer, flush)
 *          - 3D context management and command submission
 *          - Fence-based synchronization
 *
 *          QEMU invocation: -device virtio-gpu-gl  (virgl backend)
 *                          -device virtio-gpu      (2D only)
 *
 * @version 1.0.0
 * @date 2026-04-08
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.1 — GPU + Compute Foundation
 */

#include "../../include/vos/virtio_gpu.h"
#include "../../include/vos/virtio_core.h"
#include "../../include/vos/pci.h"
#include "../../include/vos/console.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * MODULE STATE
 * ============================================================================ */

/** @brief Global GPU device state */
static vos3_virtio_gpu_t g_gpu;

/** @brief Command submission counter */
static uint64_t g_cmds_submitted = 0;

/* ============================================================================
 * DMA ALLOCATION CONSTANTS
 * ============================================================================ */

/** @brief Size of DMA region for each virtqueue (2 pages = 8KB) */
#define GPU_QUEUE_DMA_SIZE      (2U * 4096U)

/** @brief Size of command/response bounce buffers (1 page each) */
#define GPU_BOUNCE_SIZE         4096U

/* ============================================================================
 * PCI DISCOVERY — Find VirtIO-GPU on PCI bus
 * ============================================================================ */

/**
 * @brief Scan PCI bus for VirtIO-GPU device (non-transitional first, then legacy)
 *
 * @return 0 on success, -1 if not found
 */
static int gpu_pci_probe(void)
{
    /* First try: use the HAL scanner (if available from vos3_pci_bus_scan) */
    const vos3_pci_device_t *d = vos3_pci_find_device(
        VIRTIO_PCI_VENDOR_ID, VIRTIO_GPU_DEVICE_ID);
    if (d != NULL) {
        g_gpu.bus  = d->bus;
        g_gpu.dev  = d->dev;
        g_gpu.func = d->func;

        /* Non-transitional: BAR0 may be MMIO or I/O.
         * For QEMU legacy mode we expect I/O port in BAR0. */
        uint32_t bar0 = d->bar[0];
        if (bar0 & 0x01U) {
            g_gpu.io_base = (uint16_t)(bar0 & 0xFFFCU);
        } else {
            /* MMIO BAR — not supported in legacy mode yet */
            vos3_console_puts("[GPU] BAR0 is MMIO — legacy I/O expected\n");
            return -1;
        }

        vos3_console_printf("[GPU] Found VirtIO-GPU (non-transitional) at "
                            "PCI %u:%u.%u io=0x%04x\n",
                            g_gpu.bus, g_gpu.dev, g_gpu.func, g_gpu.io_base);
        return 0;
    }

    /* Second try: transitional scan via subsystem ID */
    uint8_t bus, dev, func;
    uint16_t io_base;
    int rc = virtio_pci_find_device(VIRTIO_GPU_SUBSYS_ID,
                                     &bus, &dev, &func, &io_base);
    if (rc == 0) {
        g_gpu.bus     = bus;
        g_gpu.dev     = dev;
        g_gpu.func    = func;
        g_gpu.io_base = io_base;

        vos3_console_printf("[GPU] Found VirtIO-GPU (transitional) at "
                            "PCI %u:%u.%u io=0x%04x\n",
                            bus, dev, func, io_base);
        return 0;
    }

    return -1;
}

/* ============================================================================
 * DEVICE RESET AND FEATURE NEGOTIATION
 * ============================================================================ */

/**
 * @brief Reset device and negotiate features
 *
 * @return 0 on success, -1 on failure
 */
static int gpu_negotiate_features(void)
{
    uint16_t base = g_gpu.io_base;

    /* Reset device */
    virtio_outb(base + VIRTIO_PCI_STATUS, 0);
    virtio_mb();

    /* Acknowledge device */
    virtio_outb(base + VIRTIO_PCI_STATUS, VIRTIO_STATUS_ACK);
    virtio_outb(base + VIRTIO_PCI_STATUS,
                VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER);

    /* Read host features */
    g_gpu.host_features = virtio_inl(base + VIRTIO_PCI_HOST_FEATURES);

    vos3_console_printf("[GPU] Host features: 0x%08x\n", g_gpu.host_features);

    /* Select features we want */
    g_gpu.guest_features = 0;

    if (g_gpu.host_features & VIRTIO_GPU_F_VIRGL) {
        g_gpu.guest_features |= VIRTIO_GPU_F_VIRGL;
        g_gpu.virgl_supported = 1;
        vos3_console_puts("[GPU] virgl 3D rendering available\n");
    }

    if (g_gpu.host_features & VIRTIO_GPU_F_RESOURCE_BLOB) {
        g_gpu.guest_features |= VIRTIO_GPU_F_RESOURCE_BLOB;
        vos3_console_puts("[GPU] Resource blob objects available\n");
    }

    if (g_gpu.host_features & VIRTIO_GPU_F_CONTEXT_INIT) {
        g_gpu.guest_features |= VIRTIO_GPU_F_CONTEXT_INIT;
    }

    /* Write negotiated features */
    virtio_outl(base + VIRTIO_PCI_GUEST_FEATURES, g_gpu.guest_features);

    return 0;
}

/* ============================================================================
 * VIRTQUEUE SETUP
 * ============================================================================ */

/**
 * @brief Allocate DMA pages and initialize a virtqueue
 *
 * @param[out] vq      Virtqueue to initialize
 * @param[in]  index   Queue index (0=controlq, 1=cursorq)
 * @param[out] dma_out Pointer to store DMA buffer address
 * @return 0 on success, -1 on failure
 */
static int gpu_setup_queue(virtio_queue_t *vq, uint16_t index, void **dma_out)
{
    uint16_t base = g_gpu.io_base;

    /* Select queue */
    virtio_outw(base + VIRTIO_PCI_QUEUE_SEL, index);

    /* Read queue size from device */
    uint16_t qsize = virtio_inw(base + VIRTIO_PCI_QUEUE_SIZE);
    if (qsize == 0) {
        vos3_console_printf("[GPU] Queue %u size is 0 — not available\n", index);
        return -1;
    }

    /* Cap at 256 (our maximum) */
    if (qsize > 256) {
        qsize = 256;
    }

    /* Allocate DMA pages for the queue (must be in low memory for DMA) */
    uintptr_t dma_phys = vos3_pmm_alloc_pages(
        GPU_QUEUE_DMA_SIZE / 4096, VOS3_PMM_FLAG_DMA32);
    if (dma_phys == 0) {
        vos3_console_printf("[GPU] Failed to allocate DMA for queue %u\n", index);
        return -1;
    }

    /* Convert physical to kernel virtual (KBASE mapping) */
    void *dma_virt = (void *)(dma_phys + VIRTIO_KBASE);
    *dma_out = dma_virt;

    /* Initialize the virtqueue */
    int rc = virtio_queue_init(vq, index, qsize, base, dma_virt, GPU_QUEUE_DMA_SIZE);
    if (rc != 0) {
        vos3_pmm_free_pages(dma_phys, GPU_QUEUE_DMA_SIZE / 4096);
        return -1;
    }

    vos3_console_printf("[GPU] Queue %u: %u descriptors @ phys 0x%llx\n",
                        index, qsize, (unsigned long long)dma_phys);
    return 0;
}

/* ============================================================================
 * COMMAND SUBMISSION ENGINE
 * ============================================================================ */

/**
 * @brief Submit a command to the GPU control queue and wait for response
 *
 * Uses bounce buffers for DMA-safe command/response transfer.
 * Follows the VirtIO split queue protocol:
 *   1. Copy command to DMA-visible bounce buffer
 *   2. Chain descriptors: cmd (device-readable) → resp (device-writable)
 *   3. Add to available ring and notify device
 *   4. Poll used ring for completion
 *
 * @param[in]  cmd       Command data
 * @param[in]  cmd_size  Command size in bytes
 * @param[out] resp      Response buffer (caller-provided)
 * @param[in]  resp_size Expected response size
 * @return 0 on success, -1 on failure or timeout
 */
static int gpu_submit_cmd(const void *cmd, size_t cmd_size,
                           void *resp, size_t resp_size)
{
    if (cmd_size > GPU_BOUNCE_SIZE || resp_size > GPU_BOUNCE_SIZE) {
        return -1;
    }

    virtio_queue_t *vq = &g_gpu.controlq;
    uint16_t base = g_gpu.io_base;

    vos3_spinlock_lock(&vq->lock);

    /* Need 2 free descriptors for command chain */
    if (vq->num_free < 2) {
        vos3_spinlock_unlock(&vq->lock);
        vos3_console_puts("[GPU] No free descriptors\n");
        return -1;
    }

    /* Copy command to bounce buffer */
    memcpy(g_gpu.cmd_buf, cmd, cmd_size);

    /* Allocate descriptor 0: command (device-readable) */
    uint16_t cmd_idx = vq->free_head;
    vq->free_head = vq->desc[cmd_idx].next;
    vq->num_free--;

    vq->desc[cmd_idx].addr  = g_gpu.cmd_buf_phys;
    vq->desc[cmd_idx].len   = (uint32_t)cmd_size;
    vq->desc[cmd_idx].flags = VIRTQ_DESC_F_NEXT;

    /* Allocate descriptor 1: response (device-writable) */
    uint16_t resp_idx = vq->free_head;
    vq->free_head = vq->desc[resp_idx].next;
    vq->num_free--;

    vq->desc[cmd_idx].next  = resp_idx;
    vq->desc[resp_idx].addr  = g_gpu.resp_buf_phys;
    vq->desc[resp_idx].len   = (uint32_t)resp_size;
    vq->desc[resp_idx].flags = VIRTQ_DESC_F_WRITE;
    vq->desc[resp_idx].next  = 0;

    /* Clear response buffer */
    memset(g_gpu.resp_buf, 0, resp_size);

    /* Add to available ring */
    virtio_mb();
    uint16_t avail_idx = vq->avail->idx;
    vq->avail->ring[avail_idx % vq->num_desc] = cmd_idx;
    virtio_mb();
    vq->avail->idx = avail_idx + 1;
    virtio_mb();

    /* Notify device */
    virtio_outw(base + VIRTIO_PCI_QUEUE_NOTIFY, vq->index);

    /* Poll for completion (timeout ~100ms at ~100Hz tick) */
    uint32_t timeout = 10000;
    while (vq->used->idx == vq->last_used_idx && timeout > 0) {
        virtio_yield();
        timeout--;
    }

    if (timeout == 0) {
        vos3_spinlock_unlock(&vq->lock);
        vos3_console_puts("[GPU] Command timeout\n");
        /* Return descriptors to free list */
        vq->desc[resp_idx].next = vq->free_head;
        vq->free_head = cmd_idx;
        vq->desc[cmd_idx].next = resp_idx;
        vq->num_free += 2;
        return -1;
    }

    /* Consume used entry */
    vq->last_used_idx++;

    /* Copy response from bounce buffer */
    memcpy(resp, g_gpu.resp_buf, resp_size);

    /* Return descriptors to free list */
    vq->desc[resp_idx].next = vq->free_head;
    vq->free_head = cmd_idx;
    vq->desc[cmd_idx].next = resp_idx;
    vq->num_free += 2;

    vos3_spinlock_unlock(&vq->lock);

    g_cmds_submitted++;
    return 0;
}

/* ============================================================================
 * DEVICE CONFIGURATION READ
 * ============================================================================ */

/**
 * @brief Read GPU device configuration
 */
static void gpu_read_config(void)
{
    uint16_t base = g_gpu.io_base;

    /* VirtIO legacy: device config starts at offset 0x14 */
    uint32_t events_read = virtio_inl(base + VIRTIO_PCI_CONFIG + 0);
    uint32_t num_scanouts = virtio_inl(base + VIRTIO_PCI_CONFIG + 8);
    uint32_t num_capsets = virtio_inl(base + VIRTIO_PCI_CONFIG + 12);

    g_gpu.num_scanouts = num_scanouts;
    g_gpu.num_capsets  = num_capsets;

    (void)events_read;

    vos3_console_printf("[GPU] Config: %u scanouts, %u capsets\n",
                        num_scanouts, num_capsets);
}

/* ============================================================================
 * PUBLIC API — Initialization
 * ============================================================================ */

int vos3_virtio_gpu_init(void)
{
    memset(&g_gpu, 0, sizeof(g_gpu));

    vos3_console_puts("[GPU] Initializing VirtIO-GPU driver...\n");

    /* Step 1: Find device on PCI bus */
    if (gpu_pci_probe() != 0) {
        vos3_console_puts("[GPU] VirtIO-GPU not found on PCI bus\n");
        return -1;
    }

    /* Step 2: Negotiate features */
    if (gpu_negotiate_features() != 0) {
        vos3_console_puts("[GPU] Feature negotiation failed\n");
        return -1;
    }

    /* Step 3: Set up control queue (index 0) */
    if (gpu_setup_queue(&g_gpu.controlq, 0, &g_gpu.controlq_dma) != 0) {
        vos3_console_puts("[GPU] Control queue setup failed\n");
        return -1;
    }

    /* Step 4: Set up cursor queue (index 1) */
    if (gpu_setup_queue(&g_gpu.cursorq, 1, &g_gpu.cursorq_dma) != 0) {
        vos3_console_puts("[GPU] Cursor queue setup failed (non-fatal)\n");
        /* Cursor queue failure is non-fatal — continue without cursor */
    }

    /* Step 5: Allocate command/response bounce buffers */
    uintptr_t cmd_phys = vos3_pmm_alloc(VOS3_PMM_FLAG_DMA32);
    uintptr_t resp_phys = vos3_pmm_alloc(VOS3_PMM_FLAG_DMA32);
    if (cmd_phys == 0 || resp_phys == 0) {
        vos3_console_puts("[GPU] Failed to allocate bounce buffers\n");
        return -1;
    }

    g_gpu.cmd_buf      = (void *)(cmd_phys + VIRTIO_KBASE);
    g_gpu.resp_buf     = (void *)(resp_phys + VIRTIO_KBASE);
    g_gpu.cmd_buf_phys = cmd_phys;
    g_gpu.resp_buf_phys= resp_phys;

    /* Step 6: Mark device as DRIVER_OK */
    virtio_outb(g_gpu.io_base + VIRTIO_PCI_STATUS,
                VIRTIO_STATUS_ACK | VIRTIO_STATUS_DRIVER |
                VIRTIO_STATUS_FEATURES_OK | VIRTIO_STATUS_DRIVER_OK);

    /* Step 7: Read device configuration */
    gpu_read_config();

    /* Initialize resource tracking */
    g_gpu.next_resource_id = 1;
    g_gpu.next_context_id  = 1;
    g_gpu.next_fence_id    = 1;
    g_gpu.initialized      = 1;

    vos3_console_printf("[GPU] VirtIO-GPU initialized: virgl=%s, "
                        "%u scanouts, %u capsets\n",
                        g_gpu.virgl_supported ? "yes" : "no",
                        g_gpu.num_scanouts, g_gpu.num_capsets);

    return 0;
}

int vos3_virtio_gpu_available(void)
{
    return g_gpu.initialized ? 1 : 0;
}

int vos3_virtio_gpu_has_virgl(void)
{
    return (g_gpu.initialized && g_gpu.virgl_supported) ? 1 : 0;
}

/* ============================================================================
 * PUBLIC API — Display Info
 * ============================================================================ */

int vos3_virtio_gpu_get_display_info(virtio_gpu_display_one_t *info)
{
    if (!g_gpu.initialized || info == NULL) return -1;

    virtio_gpu_ctrl_hdr_t cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.type = VIRTIO_GPU_CMD_GET_DISPLAY_INFO;

    virtio_gpu_resp_display_info_t resp;
    memset(&resp, 0, sizeof(resp));

    int rc = gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
    if (rc != 0) return -1;

    if (resp.hdr.type != VIRTIO_GPU_RESP_OK_DISPLAY_INFO) {
        vos3_console_printf("[GPU] GET_DISPLAY_INFO failed: type=0x%04x\n",
                            resp.hdr.type);
        return -1;
    }

    int enabled = 0;
    for (int i = 0; i < VIRTIO_GPU_MAX_SCANOUTS; i++) {
        info[i] = resp.pmodes[i];
        if (resp.pmodes[i].enabled) {
            enabled++;
            vos3_console_printf("[GPU] Scanout %d: %ux%u\n",
                                i, resp.pmodes[i].r.width,
                                resp.pmodes[i].r.height);
        }
    }

    return enabled;
}

/* ============================================================================
 * PUBLIC API — 2D Resource Management
 * ============================================================================ */

uint32_t vos3_virtio_gpu_resource_create_2d(uint32_t width, uint32_t height,
                                             uint32_t format)
{
    if (!g_gpu.initialized) return 0;

    uint32_t rid = g_gpu.next_resource_id++;

    virtio_gpu_resource_create_2d_t cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.hdr.type    = VIRTIO_GPU_CMD_RESOURCE_CREATE_2D;
    cmd.resource_id = rid;
    cmd.format      = format;
    cmd.width       = width;
    cmd.height      = height;

    virtio_gpu_ctrl_hdr_t resp;
    memset(&resp, 0, sizeof(resp));

    int rc = gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
    if (rc != 0 || resp.type != VIRTIO_GPU_RESP_OK_NODATA) {
        vos3_console_printf("[GPU] RESOURCE_CREATE_2D failed: rc=%d resp=0x%04x\n",
                            rc, resp.type);
        return 0;
    }

    /* Track resource */
    if (rid < VOS3_GPU_MAX_RESOURCES) {
        g_gpu.resources[rid].id     = rid;
        g_gpu.resources[rid].format = format;
        g_gpu.resources[rid].width  = width;
        g_gpu.resources[rid].height = height;
        g_gpu.resources[rid].in_use = 1;
    }

    return rid;
}

int vos3_virtio_gpu_resource_attach_backing(uint32_t resource_id,
                                             uint64_t phys_addr,
                                             uint32_t size)
{
    if (!g_gpu.initialized) return -1;

    /* Build command with one memory entry inline.
     * Since the bounce buffer is only 4KB, we pack the attach_backing
     * header + 1 mem_entry into a single buffer. */
    struct {
        virtio_gpu_resource_attach_backing_t hdr;
        virtio_gpu_mem_entry_t entry;
    } __attribute__((packed)) cmd;

    memset(&cmd, 0, sizeof(cmd));
    cmd.hdr.hdr.type     = VIRTIO_GPU_CMD_RESOURCE_ATTACH_BACKING;
    cmd.hdr.resource_id  = resource_id;
    cmd.hdr.nr_entries   = 1;
    cmd.entry.addr       = phys_addr;
    cmd.entry.length     = size;

    virtio_gpu_ctrl_hdr_t resp;
    memset(&resp, 0, sizeof(resp));

    int rc = gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
    if (rc != 0 || resp.type != VIRTIO_GPU_RESP_OK_NODATA) {
        vos3_console_printf("[GPU] ATTACH_BACKING failed: rc=%d resp=0x%04x\n",
                            rc, resp.type);
        return -1;
    }

    /* Track backing */
    if (resource_id < VOS3_GPU_MAX_RESOURCES) {
        g_gpu.resources[resource_id].backing_phys = (uintptr_t)phys_addr;
        g_gpu.resources[resource_id].backing_size = size;
        g_gpu.resources[resource_id].attached     = 1;
    }

    return 0;
}

int vos3_virtio_gpu_resource_unref(uint32_t resource_id)
{
    if (!g_gpu.initialized) return -1;

    virtio_gpu_resource_unref_t cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.hdr.type    = VIRTIO_GPU_CMD_RESOURCE_UNREF;
    cmd.resource_id = resource_id;

    virtio_gpu_ctrl_hdr_t resp;
    memset(&resp, 0, sizeof(resp));

    int rc = gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
    if (rc != 0 || resp.type != VIRTIO_GPU_RESP_OK_NODATA) {
        return -1;
    }

    /* Clear tracking */
    if (resource_id < VOS3_GPU_MAX_RESOURCES) {
        memset(&g_gpu.resources[resource_id], 0, sizeof(vos3_gpu_resource_t));
    }

    return 0;
}

int vos3_virtio_gpu_set_scanout(uint32_t scanout_id, uint32_t resource_id,
                                 uint32_t width, uint32_t height)
{
    if (!g_gpu.initialized) return -1;

    virtio_gpu_set_scanout_t cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.hdr.type    = VIRTIO_GPU_CMD_SET_SCANOUT;
    cmd.r.x         = 0;
    cmd.r.y         = 0;
    cmd.r.width     = width;
    cmd.r.height    = height;
    cmd.scanout_id  = scanout_id;
    cmd.resource_id = resource_id;

    virtio_gpu_ctrl_hdr_t resp;
    memset(&resp, 0, sizeof(resp));

    return gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
}

int vos3_virtio_gpu_transfer_to_host_2d(uint32_t resource_id,
                                         uint32_t x, uint32_t y,
                                         uint32_t width, uint32_t height)
{
    if (!g_gpu.initialized) return -1;

    virtio_gpu_transfer_to_host_2d_t cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.hdr.type    = VIRTIO_GPU_CMD_TRANSFER_TO_HOST_2D;
    cmd.r.x         = x;
    cmd.r.y         = y;
    cmd.r.width     = width;
    cmd.r.height    = height;
    cmd.resource_id = resource_id;
    cmd.offset      = 0;

    virtio_gpu_ctrl_hdr_t resp;
    memset(&resp, 0, sizeof(resp));

    return gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
}

int vos3_virtio_gpu_flush(uint32_t resource_id,
                           uint32_t x, uint32_t y,
                           uint32_t width, uint32_t height)
{
    if (!g_gpu.initialized) return -1;

    virtio_gpu_resource_flush_t cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.hdr.type    = VIRTIO_GPU_CMD_RESOURCE_FLUSH;
    cmd.r.x         = x;
    cmd.r.y         = y;
    cmd.r.width     = width;
    cmd.r.height    = height;
    cmd.resource_id = resource_id;

    virtio_gpu_ctrl_hdr_t resp;
    memset(&resp, 0, sizeof(resp));

    return gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
}

/* ============================================================================
 * PUBLIC API — 3D Context Management
 * ============================================================================ */

uint32_t vos3_virtio_gpu_ctx_create(const char *name)
{
    if (!g_gpu.initialized || !g_gpu.virgl_supported) return 0;

    uint32_t cid = g_gpu.next_context_id++;

    virtio_gpu_ctx_create_t cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.hdr.type   = VIRTIO_GPU_CMD_CTX_CREATE;
    cmd.hdr.ctx_id = cid;

    /* Copy debug name */
    size_t nlen = 0;
    if (name != NULL) {
        while (name[nlen] != '\0' && nlen < 63) {
            cmd.debug_name[nlen] = name[nlen];
            nlen++;
        }
        cmd.debug_name[nlen] = '\0';
    }
    cmd.nlen = (uint32_t)nlen;

    virtio_gpu_ctrl_hdr_t resp;
    memset(&resp, 0, sizeof(resp));

    int rc = gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
    if (rc != 0 || resp.type != VIRTIO_GPU_RESP_OK_NODATA) {
        vos3_console_printf("[GPU] CTX_CREATE failed: rc=%d resp=0x%04x\n",
                            rc, resp.type);
        return 0;
    }

    /* Track context */
    if (cid < VOS3_GPU_MAX_CONTEXTS) {
        g_gpu.contexts[cid].id     = cid;
        g_gpu.contexts[cid].active = 1;
        for (size_t i = 0; i < nlen && i < 63; i++) {
            g_gpu.contexts[cid].name[i] = name[i];
        }
    }

    vos3_console_printf("[GPU] 3D context %u created: '%s'\n", cid,
                        name ? name : "");
    return cid;
}

int vos3_virtio_gpu_ctx_destroy(uint32_t ctx_id)
{
    if (!g_gpu.initialized) return -1;

    virtio_gpu_ctx_destroy_t cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.hdr.type   = VIRTIO_GPU_CMD_CTX_DESTROY;
    cmd.hdr.ctx_id = ctx_id;

    virtio_gpu_ctrl_hdr_t resp;
    memset(&resp, 0, sizeof(resp));

    int rc = gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
    if (rc != 0) return -1;

    /* Clear tracking */
    if (ctx_id < VOS3_GPU_MAX_CONTEXTS) {
        memset(&g_gpu.contexts[ctx_id], 0, sizeof(vos3_gpu_context_t));
    }

    return 0;
}

int vos3_virtio_gpu_ctx_attach_resource(uint32_t ctx_id, uint32_t resource_id)
{
    if (!g_gpu.initialized) return -1;

    virtio_gpu_ctx_attach_resource_t cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.hdr.type    = VIRTIO_GPU_CMD_CTX_ATTACH_RESOURCE;
    cmd.hdr.ctx_id  = ctx_id;
    cmd.resource_id = resource_id;

    virtio_gpu_ctrl_hdr_t resp;
    memset(&resp, 0, sizeof(resp));

    return gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
}

int vos3_virtio_gpu_submit_3d(uint32_t ctx_id, const void *cmdbuf,
                               uint32_t size)
{
    if (!g_gpu.initialized || !g_gpu.virgl_supported) return -1;
    if (cmdbuf == NULL || size == 0) return -1;

    /* The submit command header is followed by the command buffer data.
     * We need to fit both in the bounce buffer. */
    size_t total = sizeof(virtio_gpu_cmd_submit_t) + size;
    if (total > GPU_BOUNCE_SIZE) {
        vos3_console_printf("[GPU] Submit too large: %u bytes (max %u)\n",
                            size, (uint32_t)(GPU_BOUNCE_SIZE -
                            sizeof(virtio_gpu_cmd_submit_t)));
        return -1;
    }

    /* Build command in a stack buffer, then submit */
    uint8_t buf[GPU_BOUNCE_SIZE];
    virtio_gpu_cmd_submit_t *cmd = (virtio_gpu_cmd_submit_t *)buf;
    memset(cmd, 0, sizeof(*cmd));
    cmd->hdr.type   = VIRTIO_GPU_CMD_SUBMIT_3D;
    cmd->hdr.ctx_id = ctx_id;
    cmd->size       = size;

    /* Copy command buffer data after the header */
    memcpy(buf + sizeof(virtio_gpu_cmd_submit_t), cmdbuf, size);

    virtio_gpu_ctrl_hdr_t resp;
    memset(&resp, 0, sizeof(resp));

    return gpu_submit_cmd(buf, total, &resp, sizeof(resp));
}

/* ============================================================================
 * PUBLIC API — Capability Sets
 * ============================================================================ */

int vos3_virtio_gpu_get_capset_info(uint32_t index,
                                     uint32_t *capset_id,
                                     uint32_t *capset_max_ver,
                                     uint32_t *capset_max_size)
{
    if (!g_gpu.initialized) return -1;

    virtio_gpu_get_capset_info_t cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.hdr.type     = VIRTIO_GPU_CMD_GET_CAPSET_INFO;
    cmd.capset_index = index;

    virtio_gpu_resp_capset_info_t resp;
    memset(&resp, 0, sizeof(resp));

    int rc = gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
    if (rc != 0 || resp.hdr.type != VIRTIO_GPU_RESP_OK_CAPSET_INFO) {
        return -1;
    }

    if (capset_id)       *capset_id       = resp.capset_id;
    if (capset_max_ver)  *capset_max_ver  = resp.capset_max_version;
    if (capset_max_size) *capset_max_size = resp.capset_max_size;

    return 0;
}

/* ============================================================================
 * PUBLIC API — Fence Synchronization
 * ============================================================================ */

int vos3_virtio_gpu_fence_wait(uint64_t fence_id, uint32_t timeout_ms)
{
    if (!g_gpu.initialized) return -1;

    /* Submit a no-op command with fence flag to synchronize */
    virtio_gpu_ctrl_hdr_t cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.type     = VIRTIO_GPU_CMD_GET_DISPLAY_INFO; /* Lightweight query */
    cmd.flags    = VIRTIO_GPU_FLAG_FENCE;
    cmd.fence_id = fence_id;

    virtio_gpu_resp_display_info_t resp;
    memset(&resp, 0, sizeof(resp));

    (void)timeout_ms; /* Timeout handled by gpu_submit_cmd polling */

    int rc = gpu_submit_cmd(&cmd, sizeof(cmd), &resp, sizeof(resp));
    return rc;
}

/* ============================================================================
 * PUBLIC API — Statistics
 * ============================================================================ */

void vos3_virtio_gpu_stats(uint32_t *resources_used,
                            uint32_t *contexts_used,
                            uint64_t *cmds_submitted)
{
    uint32_t res = 0, ctx = 0;

    for (uint32_t i = 0; i < VOS3_GPU_MAX_RESOURCES; i++) {
        if (g_gpu.resources[i].in_use) res++;
    }
    for (uint32_t i = 0; i < VOS3_GPU_MAX_CONTEXTS; i++) {
        if (g_gpu.contexts[i].active) ctx++;
    }

    if (resources_used) *resources_used = res;
    if (contexts_used)  *contexts_used  = ctx;
    if (cmds_submitted) *cmds_submitted = g_cmds_submitted;
}
