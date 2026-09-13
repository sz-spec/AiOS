/**
 * @file nvme.c
 * @brief VOS3 NVMe 1.4 Block Driver — Sovereign Storage
 *
 * @details Freestanding polled-mode NVMe driver for VOS3.  Implements the
 *          NVMe 1.4 specification subset required for block I/O:
 *
 *          - Admin queue (Identify Controller, Identify Namespace, Create
 *            I/O CQ, Create I/O SQ)
 *          - I/O queues (NVM Read, NVM Write)
 *          - PRP-based DMA with full boundary validation
 *          - Controller reset and health monitoring
 *
 *          Security guarantees:
 *          - Every PRP entry validated via vos3_nvme_validate_prp()
 *          - Integer overflow protection on all transfer size computations
 *          - Self-referencing PRP list detection (anti-DMA loop)
 *          - PCI memory hole (3GB-4GB) rejected for all DMA addresses
 *          - sfence after SQ writes, lfence after CQ reads (Spectre safety)
 *          - Fatal status zero-fills destination before returning error
 *          - No interrupts, no dynamic dispatch, no shell execution
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 9: Bare-Metal Peak — NVMe Driver
 */

#include "../../include/vos/nvme.h"
#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * KERNEL DEPENDENCIES (FREESTANDING — FORWARD DECLARATIONS)
 * ============================================================================ */

extern void vos3_console_printf(const char *fmt, ...);
extern uintptr_t vos3_pmm_alloc_pages(size_t count, int flags);
extern void vos3_pmm_free_pages(uintptr_t addr, size_t count);

/* ============================================================================
 * LOGGING MACROS
 * ============================================================================ */

#ifndef VOS3_INFO
#define VOS3_INFO(fmt, ...)  vos3_console_printf("[NVMe] " fmt "\n", ##__VA_ARGS__)
#endif
#ifndef VOS3_WARN
#define VOS3_WARN(fmt, ...)  vos3_console_printf("[NVMe WARN] " fmt "\n", ##__VA_ARGS__)
#endif
#ifndef VOS3_ERROR
#define VOS3_ERROR(fmt, ...) vos3_console_printf("[NVMe ERROR] " fmt "\n", ##__VA_ARGS__)
#endif

/* ============================================================================
 * PHYSICAL / VIRTUAL ADDRESS CONVERSION
 * ============================================================================ */

/** @brief HHDM offset for physical<->virtual conversion */
#define NVME_HHDM_OFFSET    0xFFFF800000000000ULL

/* ============================================================================
 * STATIC STATE
 * ============================================================================ */

/** @brief Global NVMe controller state — single controller support */
static vos3_nvme_ctrl_t g_nvme_ctrl;

/* ============================================================================
 * MMIO HELPERS — Volatile Memory-Mapped I/O
 * ============================================================================
 *
 * All register accesses go through volatile pointers to prevent the compiler
 * from reordering, caching, or eliding any MMIO read/write.
 * ============================================================================ */

/**
 * @brief Read a 32-bit MMIO register
 *
 * @param base  MMIO virtual base address
 * @param offset  Register offset in bytes
 * @return 32-bit register value
 */
static inline uint32_t nvme_read32(uintptr_t base, uint32_t offset)
{
    return *(volatile uint32_t *)(base + offset);
}

/**
 * @brief Write a 32-bit MMIO register
 *
 * @param base  MMIO virtual base address
 * @param offset  Register offset in bytes
 * @param val  Value to write
 */
static inline void nvme_write32(uintptr_t base, uint32_t offset, uint32_t val)
{
    *(volatile uint32_t *)(base + offset) = val;
}

/**
 * @brief Read a 64-bit MMIO register
 *
 * @param base  MMIO virtual base address
 * @param offset  Register offset in bytes
 * @return 64-bit register value
 *
 * @note NVMe spec permits 64-bit access; if hardware requires two 32-bit
 *       reads, the low half must be read first (NVMe 1.4 section 2.1.11).
 */
static inline uint64_t nvme_read64(uintptr_t base, uint32_t offset)
{
    uint32_t lo = *(volatile uint32_t *)(base + offset);
    uint32_t hi = *(volatile uint32_t *)(base + offset + 4U);
    return ((uint64_t)hi << 32) | (uint64_t)lo;
}

/**
 * @brief Write a 64-bit MMIO register
 *
 * @param base  MMIO virtual base address
 * @param offset  Register offset in bytes
 * @param val  Value to write
 *
 * @note Low 32 bits written first per NVMe 1.4 spec section 2.1.11.
 */
static inline void nvme_write64(uintptr_t base, uint32_t offset, uint64_t val)
{
    *(volatile uint32_t *)(base + offset)      = (uint32_t)(val & 0xFFFFFFFFU);
    *(volatile uint32_t *)(base + offset + 4U) = (uint32_t)(val >> 32);
}

/* ============================================================================
 * PHYSICAL / VIRTUAL ADDRESS CONVERSION HELPERS
 * ============================================================================ */

/**
 * @brief Convert a physical address to a kernel virtual address via HHDM
 *
 * @param phys  Physical address
 * @return Virtual address in the higher-half direct map
 */
static inline void *nvme_phys_to_virt(uintptr_t phys)
{
    return (void *)(phys + NVME_HHDM_OFFSET);
}

/**
 * @brief Convert a kernel virtual address to a physical address via HHDM
 *
 * @param virt  Virtual address in the higher-half direct map
 * @return Physical address
 */
static inline uintptr_t nvme_virt_to_phys(const void *virt)
{
    return (uintptr_t)virt - NVME_HHDM_OFFSET;
}

/* ============================================================================
 * FREESTANDING MEMORY HELPERS
 * ============================================================================ */

/**
 * @brief Freestanding memset — fill memory with a byte value
 *
 * @param dst  Destination buffer
 * @param val  Byte value to fill (cast to unsigned char)
 * @param n    Number of bytes to fill
 */
static void nvme_memset(void *dst, int val, size_t n)
{
    volatile uint8_t *d = (volatile uint8_t *)dst;
    for (size_t i = 0; i < n; i++) {
        d[i] = (uint8_t)val;
    }
}

/**
 * @brief Freestanding memcpy — copy memory
 *
 * @param dst  Destination buffer
 * @param src  Source buffer
 * @param n    Number of bytes to copy
 */
static void nvme_memcpy(void *dst, const void *src, size_t n)
{
    volatile uint8_t *d = (volatile uint8_t *)dst;
    const volatile uint8_t *s = (const volatile uint8_t *)src;
    for (size_t i = 0; i < n; i++) {
        d[i] = s[i];
    }
}

/* ============================================================================
 * 1. DMA BOUNDARY GUARD — SECURITY CRITICAL
 * ============================================================================ */

/**
 * @brief Validate a PRP (Physical Region Page) address for DMA safety
 *
 * @details SECURITY-CRITICAL function.  Every PRP entry used by the NVMe
 *          controller for DMA must pass this check.  Rejects addresses that:
 *          - Are not page-aligned (4KB boundary)
 *          - Fall below 1MB (BIOS/legacy area)
 *          - Exceed 4GB (VOS3 physical address cap)
 *          - Land in the PCI memory hole (0xC0000000 - 0x100000000)
 *
 * @param phys_addr  Physical address to validate
 * @return VOS3_NVME_OK (0) if safe, VOS3_NVME_E_DMA_BOUNDARY (-1) if rejected
 */
int vos3_nvme_validate_prp(uint64_t phys_addr)
{
    /* Page alignment check: bits [11:0] must be zero */
    if (phys_addr & 0xFFFULL) {
        VOS3_ERROR("DMA_BOUNDARY_VIOLATION: PRP 0x%llx not page-aligned",
                   (unsigned long long)phys_addr);
        return VOS3_NVME_E_DMA_BOUNDARY;
    }

    /* Below BIOS/legacy area (< 1MB) */
    if (phys_addr < VOS3_NVME_DMA_LOWER_BOUND) {
        VOS3_ERROR("DMA_BOUNDARY_VIOLATION: PRP 0x%llx below 1MB safe zone",
                   (unsigned long long)phys_addr);
        return VOS3_NVME_E_DMA_BOUNDARY;
    }

    /* Beyond 4GB physical cap */
    if (phys_addr >= VOS3_NVME_DMA_UPPER_BOUND) {
        VOS3_ERROR("DMA_BOUNDARY_VIOLATION: PRP 0x%llx beyond 4GB physical cap",
                   (unsigned long long)phys_addr);
        return VOS3_NVME_E_DMA_BOUNDARY;
    }

    /* PCI memory hole: 3GB (0xC0000000) to 4GB (0x100000000) */
    if (phys_addr >= 0xC0000000ULL && phys_addr < 0x100000000ULL) {
        VOS3_ERROR("DMA_BOUNDARY_VIOLATION: PRP 0x%llx in PCI memory hole",
                   (unsigned long long)phys_addr);
        return VOS3_NVME_E_DMA_BOUNDARY;
    }

    return VOS3_NVME_OK;
}

/* ============================================================================
 * 2. QUEUE ALLOCATION
 * ============================================================================ */

/**
 * @brief Allocate and initialize an NVMe queue pair (SQ + CQ)
 *
 * @details Allocates physically contiguous pages for the submission and
 *          completion queues, zeros them, and sets up doorbell pointers
 *          based on the queue ID and the controller's doorbell stride.
 *          Phase bit is initialized to 1 for the first completion cycle.
 *
 * @param q      Queue pair structure to initialize
 * @param depth  Number of entries in each queue
 * @param qid    Queue identifier (0 = admin, 1+ = I/O)
 * @return VOS3_NVME_OK on success, VOS3_NVME_E_NO_MEM on allocation failure
 */
static int nvme_alloc_queue(vos3_nvme_queue_t *q, uint16_t depth, uint8_t qid)
{
    /* Calculate SQ size: depth * 64 bytes (SQE size) */
    size_t sq_bytes = (size_t)depth * sizeof(vos3_nvme_sqe_t);
    size_t sq_pages = (sq_bytes + 4095U) / 4096U;

    /* Calculate CQ size: depth * 16 bytes (CQE size) */
    size_t cq_bytes = (size_t)depth * sizeof(vos3_nvme_cqe_t);
    size_t cq_pages = (cq_bytes + 4095U) / 4096U;

    /* Allocate SQ pages */
    uintptr_t sq_phys = vos3_pmm_alloc_pages(sq_pages, 0);
    if (sq_phys == 0) {
        VOS3_ERROR("Failed to allocate %zu pages for SQ (qid=%u)", sq_pages, qid);
        return VOS3_NVME_E_NO_MEM;
    }

    /* Allocate CQ pages */
    uintptr_t cq_phys = vos3_pmm_alloc_pages(cq_pages, 0);
    if (cq_phys == 0) {
        VOS3_ERROR("Failed to allocate %zu pages for CQ (qid=%u)", cq_pages, qid);
        vos3_pmm_free_pages(sq_phys, sq_pages);
        return VOS3_NVME_E_NO_MEM;
    }

    /* Zero-fill both queues (DMA isolation) */
    void *sq_virt = nvme_phys_to_virt(sq_phys);
    void *cq_virt = nvme_phys_to_virt(cq_phys);
    nvme_memset(sq_virt, 0, sq_pages * 4096U);
    nvme_memset(cq_virt, 0, cq_pages * 4096U);

    /* Populate queue structure */
    q->sq       = (volatile vos3_nvme_sqe_t *)sq_virt;
    q->cq       = (volatile vos3_nvme_cqe_t *)cq_virt;
    q->sq_phys  = sq_phys;
    q->cq_phys  = cq_phys;
    q->depth    = depth;
    q->sq_tail  = 0;
    q->cq_head  = 0;
    q->cid_counter = 0;
    q->cq_phase = 1;   /* First completion cycle expects phase=1 */
    q->id       = qid;

    /* Doorbell addresses:
     *   SQ tail doorbell = mmio_base + 0x1000 + (2 * qid) * (4 << dstrd)
     *   CQ head doorbell = mmio_base + 0x1000 + (2 * qid + 1) * (4 << dstrd) */
    uint32_t dstrd = g_nvme_ctrl.dstrd;
    uintptr_t base = g_nvme_ctrl.mmio_base;
    uint32_t stride = 4U << dstrd;

    q->sq_doorbell = (volatile uint32_t *)(base + 0x1000U + (2U * (uint32_t)qid) * stride);
    q->cq_doorbell = (volatile uint32_t *)(base + 0x1000U + (2U * (uint32_t)qid + 1U) * stride);

    VOS3_INFO("Queue %u allocated: depth=%u SQ@0x%llx CQ@0x%llx",
              qid, depth,
              (unsigned long long)sq_phys,
              (unsigned long long)cq_phys);

    return VOS3_NVME_OK;
}

/* ============================================================================
 * 3. COMMAND SUBMISSION
 * ============================================================================ */

/**
 * @brief Submit a command to an NVMe submission queue
 *
 * @details Copies the SQE into the queue at the current tail position,
 *          assigns a unique command ID, issues an sfence to ensure the
 *          write is visible before the doorbell ring, then advances the
 *          tail pointer and rings the SQ doorbell.
 *
 * @param q    Queue pair to submit to
 * @param cmd  Submission queue entry (copied, caller retains ownership)
 * @return Assigned command ID for matching against completions
 */
static uint16_t nvme_submit_cmd(vos3_nvme_queue_t *q, const vos3_nvme_sqe_t *cmd)
{
    uint16_t tail = q->sq_tail;
    uint16_t cid  = q->cid_counter++;

    /* Copy the SQE into the ring at the current tail */
    volatile vos3_nvme_sqe_t *dest = &q->sq[tail];
    dest->opcode = cmd->opcode;
    dest->flags  = cmd->flags;
    dest->cid    = cid;
    dest->nsid   = cmd->nsid;
    dest->rsvd2  = cmd->rsvd2;
    dest->mptr   = cmd->mptr;
    dest->prp1   = cmd->prp1;
    dest->prp2   = cmd->prp2;
    dest->cdw10  = cmd->cdw10;
    dest->cdw11  = cmd->cdw11;
    dest->cdw12  = cmd->cdw12;
    dest->cdw13  = cmd->cdw13;
    dest->cdw14  = cmd->cdw14;
    dest->cdw15  = cmd->cdw15;

    /* sfence: ensure the SQE write is globally visible before doorbell ring.
     * Without this, the controller could read stale/partial SQE data via DMA. */
    __asm__ volatile("sfence" ::: "memory");

    /* Advance tail (wrap around) */
    q->sq_tail = (uint16_t)((tail + 1U) % q->depth);

    /* Ring the SQ doorbell — controller starts processing */
    *q->sq_doorbell = q->sq_tail;

    return cid;
}

/* ============================================================================
 * 4. COMPLETION POLLING
 * ============================================================================ */

/**
 * @brief Poll for completion of a specific command
 *
 * @details Spin-polls the CQ for a matching CQE with the expected phase bit.
 *          Issues an lfence after reading the CQE to prevent speculative
 *          execution from consuming stale data.  On timeout, returns
 *          VOS3_NVME_E_TIMEOUT.
 *
 * @param q    Queue pair to poll
 * @param cid  Command ID to wait for
 * @param out  Output CQE (filled on success, may be NULL)
 * @return 0 on success (status bits [15:1] >> 1), negative on error
 */
static int nvme_poll_completion(vos3_nvme_queue_t *q, uint16_t cid,
                                vos3_nvme_cqe_t *out)
{
    uint32_t attempts = 0;

    while (attempts < VOS3_NVME_POLL_ATTEMPTS) {
        volatile vos3_nvme_cqe_t *entry = &q->cq[q->cq_head];

        /* Check phase bit: bit 0 of status field must match expected phase */
        uint16_t raw_status = entry->status;
        uint8_t  phase_bit  = (uint8_t)(raw_status & 1U);

        if (phase_bit == q->cq_phase) {
            /* lfence: prevent speculative execution from reading stale CQE
             * fields before the phase-bit check has architecturally committed.
             * Spectre-v1 mitigation for completion queue reads. */
            __asm__ volatile("lfence" ::: "memory");

            /* Copy CQE out if requested */
            if (out != NULL) {
                out->result  = entry->result;
                out->rsvd    = entry->rsvd;
                out->sq_head = entry->sq_head;
                out->sq_id   = entry->sq_id;
                out->cid     = entry->cid;
                out->status  = entry->status;
            }

            /* Extract NVMe status code: bits [15:1] shifted right by 1 */
            uint16_t status_code = (uint16_t)((raw_status >> 1) & 0x7FFFU);

            /* Advance CQ head */
            q->cq_head = (uint16_t)((q->cq_head + 1U) % q->depth);

            /* Flip phase if we wrapped around the CQ ring */
            if (q->cq_head == 0) {
                q->cq_phase ^= 1U;
            }

            /* Ring the CQ doorbell to inform controller we consumed the entry */
            *q->cq_doorbell = q->cq_head;

            /* Check if the CID matches what we expected.
             * If not, it may be a stale or out-of-order completion;
             * we consume it anyway to keep the CQ moving. */
            if (entry->cid != cid) {
                VOS3_WARN("CID mismatch: expected %u, got %u (qid=%u)",
                          cid, entry->cid, q->id);
            }

            /* Return NVMe status (0 = success) */
            if (status_code != 0) {
                VOS3_ERROR("Command CID=%u failed: status=0x%x (qid=%u)",
                           cid, status_code, q->id);
                return VOS3_NVME_E_CMD_FAILED;
            }

            return VOS3_NVME_OK;
        }

        /* Pause to reduce bus contention during spin-poll */
        __asm__ volatile("pause" ::: "memory");
        attempts++;
    }

    VOS3_ERROR("Poll timeout after %u attempts (qid=%u, cid=%u)",
               VOS3_NVME_POLL_ATTEMPTS, q->id, cid);
    return VOS3_NVME_E_TIMEOUT;
}

/* ============================================================================
 * 5. ADMIN: IDENTIFY COMMAND
 * ============================================================================ */

/**
 * @brief Issue an Identify command on the admin queue
 *
 * @details Allocates a 4KB page for the Identify data, builds the SQE
 *          with the appropriate CNS value (Controller or Namespace),
 *          submits to the admin queue, polls for completion, and copies
 *          results to the caller's buffer.
 *
 * @param cns       CNS value (0x00 = Namespace, 0x01 = Controller)
 * @param nsid      Namespace ID (0 for Controller identify)
 * @param data_out  Destination buffer (must be at least 4096 bytes)
 * @return VOS3_NVME_OK on success, negative error code on failure
 */
static int nvme_admin_identify(uint8_t cns, uint32_t nsid, void *data_out)
{
    /* Allocate a 4KB page for Identify data (DMA target) */
    uintptr_t id_phys = vos3_pmm_alloc_pages(1, 0);
    if (id_phys == 0) {
        VOS3_ERROR("Failed to allocate Identify data page");
        return VOS3_NVME_E_NO_MEM;
    }

    void *id_virt = nvme_phys_to_virt(id_phys);
    nvme_memset(id_virt, 0, 4096U);

    /* Validate the PRP (even though we just allocated it) */
    int rc = vos3_nvme_validate_prp((uint64_t)id_phys);
    if (rc != VOS3_NVME_OK) {
        vos3_pmm_free_pages(id_phys, 1);
        return rc;
    }

    /* Build the Identify SQE */
    vos3_nvme_sqe_t cmd;
    nvme_memset(&cmd, 0, sizeof(cmd));
    cmd.opcode = VOS3_NVME_ADMIN_IDENTIFY;
    cmd.nsid   = nsid;
    cmd.prp1   = (uint64_t)id_phys;
    cmd.prp2   = 0;    /* Identify data fits in one page */
    cmd.cdw10  = (uint32_t)cns;

    /* Submit and poll */
    uint16_t cid = nvme_submit_cmd(&g_nvme_ctrl.admin_q, &cmd);
    rc = nvme_poll_completion(&g_nvme_ctrl.admin_q, cid, NULL);

    if (rc == VOS3_NVME_OK) {
        nvme_memcpy(data_out, id_virt, 4096U);
    }

    /* Free the DMA page */
    vos3_pmm_free_pages(id_phys, 1);

    return rc;
}

/* ============================================================================
 * 6. ADMIN: CREATE I/O COMPLETION QUEUE
 * ============================================================================ */

/**
 * @brief Create an I/O Completion Queue via admin command
 *
 * @details Builds and submits a Create I/O CQ admin command. The CQ is
 *          configured as physically contiguous with interrupts disabled
 *          (polled mode).
 *
 * @param ioq  I/O queue pair (must have CQ already allocated)
 * @return VOS3_NVME_OK on success, negative error code on failure
 */
static int nvme_admin_create_cq(vos3_nvme_queue_t *ioq)
{
    vos3_nvme_sqe_t cmd;
    nvme_memset(&cmd, 0, sizeof(cmd));

    cmd.opcode = VOS3_NVME_ADMIN_CREATE_CQ;

    /* PRP1 = physical address of the CQ buffer */
    cmd.prp1 = (uint64_t)ioq->cq_phys;

    /* cdw10: bits [15:0] = QID, bits [31:16] = queue size (0-based) */
    cmd.cdw10 = ((uint32_t)(ioq->depth - 1U) << 16) | (uint32_t)ioq->id;

    /* cdw11: bit 0 = Physically Contiguous (PC=1)
     *        bit 1 = Interrupts Enabled (IEN=0 for polled mode)
     *        bits [31:16] = Interrupt Vector (not used) */
    cmd.cdw11 = 0x01U;  /* PC=1, IEN=0 */

    uint16_t cid = nvme_submit_cmd(&g_nvme_ctrl.admin_q, &cmd);
    int rc = nvme_poll_completion(&g_nvme_ctrl.admin_q, cid, NULL);

    if (rc == VOS3_NVME_OK) {
        VOS3_INFO("Created I/O CQ: qid=%u depth=%u", ioq->id, ioq->depth);
    } else {
        VOS3_ERROR("Failed to create I/O CQ: qid=%u rc=%d", ioq->id, rc);
    }

    return rc;
}

/* ============================================================================
 * 7. ADMIN: CREATE I/O SUBMISSION QUEUE
 * ============================================================================ */

/**
 * @brief Create an I/O Submission Queue via admin command
 *
 * @details Builds and submits a Create I/O SQ admin command. The SQ is
 *          configured as physically contiguous, medium priority, and
 *          associated with the corresponding CQ (same QID).
 *
 * @param ioq  I/O queue pair (must have SQ already allocated, CQ already created)
 * @return VOS3_NVME_OK on success, negative error code on failure
 */
static int nvme_admin_create_sq(vos3_nvme_queue_t *ioq)
{
    vos3_nvme_sqe_t cmd;
    nvme_memset(&cmd, 0, sizeof(cmd));

    cmd.opcode = VOS3_NVME_ADMIN_CREATE_SQ;

    /* PRP1 = physical address of the SQ buffer */
    cmd.prp1 = (uint64_t)ioq->sq_phys;

    /* cdw10: bits [15:0] = QID, bits [31:16] = queue size (0-based) */
    cmd.cdw10 = ((uint32_t)(ioq->depth - 1U) << 16) | (uint32_t)ioq->id;

    /* cdw11: bit 0 = Physically Contiguous (PC=1)
     *        bits [2:1] = Queue Priority (10b = Medium)
     *        bits [31:16] = Completion Queue ID (same as SQ ID) */
    cmd.cdw11 = 0x01U                              /* PC=1 */
              | (0x02U << 1)                        /* QPRIO = Medium (10b) */
              | ((uint32_t)ioq->id << 16);          /* CQID = queue ID */

    uint16_t cid = nvme_submit_cmd(&g_nvme_ctrl.admin_q, &cmd);
    int rc = nvme_poll_completion(&g_nvme_ctrl.admin_q, cid, NULL);

    if (rc == VOS3_NVME_OK) {
        VOS3_INFO("Created I/O SQ: qid=%u depth=%u cqid=%u",
                  ioq->id, ioq->depth, ioq->id);
    } else {
        VOS3_ERROR("Failed to create I/O SQ: qid=%u rc=%d", ioq->id, rc);
    }

    return rc;
}

/* ============================================================================
 * 8. PRP LIST BUILDER — SECURITY CRITICAL
 * ============================================================================ */

/**
 * @brief Build a PRP list for a multi-page DMA transfer
 *
 * @details Constructs PRP1/PRP2 entries for a given buffer and byte count.
 *          For transfers <= 4KB, only PRP1 is needed.  For 4KB < size <= 8KB,
 *          PRP1 and PRP2 are both direct physical addresses.  For larger
 *          transfers, PRP2 points to a PRP list page containing the remaining
 *          addresses.
 *
 *          SECURITY-CRITICAL:
 *          - Every PRP entry passes vos3_nvme_validate_prp()
 *          - Self-referencing PRP detection: abort if any entry == list_phys
 *          - Integer overflow check on count * sector_size
 *
 * @param prp        PRP list structure (pre-allocated per queue)
 * @param buf        Virtual buffer address (in HHDM range)
 * @param byte_count Total bytes to transfer
 * @param prp1_out   Output: PRP Entry 1 (first page physical address)
 * @param prp2_out   Output: PRP Entry 2 (second page or PRP list physical)
 * @return VOS3_NVME_OK on success, negative error code on failure
 */
static int nvme_build_prp_list(vos3_nvme_prp_list_t *prp, void *buf,
                                uint32_t byte_count,
                                uint64_t *prp1_out, uint64_t *prp2_out)
{
    if (byte_count == 0 || buf == NULL) {
        return VOS3_NVME_E_INVALID;
    }

    uintptr_t buf_phys = nvme_virt_to_phys(buf);

    /* PRP1: always the first page */
    uint64_t p1 = (uint64_t)buf_phys;
    int rc = vos3_nvme_validate_prp(p1 & ~0xFFFULL);
    if (rc != VOS3_NVME_OK) {
        return rc;
    }

    *prp1_out = p1;
    *prp2_out = 0;
    prp->count = 0;

    /* Single page transfer: PRP1 is sufficient */
    if (byte_count <= 4096U) {
        return VOS3_NVME_OK;
    }

    /* Calculate how many additional pages are needed beyond PRP1 */
    /* The first page covers from buf_phys to the next page boundary */
    uint32_t first_page_bytes = (uint32_t)(4096U - (buf_phys & 0xFFFU));
    if (first_page_bytes > byte_count) {
        first_page_bytes = byte_count;
    }
    uint32_t remaining = byte_count - first_page_bytes;
    uint32_t extra_pages = (remaining + 4095U) / 4096U;

    /* Two-page transfer: PRP1 + PRP2 (both direct addresses) */
    if (extra_pages == 1) {
        uint64_t p2 = (uint64_t)(buf_phys + first_page_bytes);
        /* Align to page boundary */
        p2 &= ~0xFFFULL;
        rc = vos3_nvme_validate_prp(p2);
        if (rc != VOS3_NVME_OK) {
            return rc;
        }
        *prp2_out = p2;
        return VOS3_NVME_OK;
    }

    /* Multi-page transfer: PRP2 points to a PRP list page.
     * The PRP list page itself must be page-aligned and validated. */
    if (extra_pages > VOS3_NVME_MAX_PRP_PER_CMD) {
        VOS3_ERROR("Transfer requires %u PRP entries, max is %u",
                   extra_pages, VOS3_NVME_MAX_PRP_PER_CMD);
        return VOS3_NVME_E_INVALID;
    }

    /* Use the pre-allocated PRP list structure.
     * The list_phys field was set up during queue allocation or at init. */
    uintptr_t list_phys = prp->list_phys;
    uint64_t *list_virt = (uint64_t *)nvme_phys_to_virt(list_phys);

    /* Populate PRP list entries */
    uintptr_t page_addr = (buf_phys + first_page_bytes) & ~0xFFFULL;

    for (uint32_t i = 0; i < extra_pages; i++) {
        uint64_t entry = (uint64_t)page_addr;

        /* Validate each PRP entry */
        rc = vos3_nvme_validate_prp(entry);
        if (rc != VOS3_NVME_OK) {
            return rc;
        }

        /* Self-referencing PRP detection: if the entry points back to the
         * PRP list page itself, this is a DMA loop — abort immediately */
        if (entry == (uint64_t)list_phys) {
            VOS3_ERROR("DMA_BOUNDARY_VIOLATION: Self-referencing PRP at entry %u "
                       "(0x%llx == list_phys)", i, (unsigned long long)entry);
            return VOS3_NVME_E_DMA_BOUNDARY;
        }

        list_virt[i] = entry;
        page_addr += 4096U;
    }

    prp->count = extra_pages;
    *prp2_out = (uint64_t)list_phys;

    /* sfence: ensure PRP list entries are visible to the controller via DMA */
    __asm__ volatile("sfence" ::: "memory");

    return VOS3_NVME_OK;
}

/* ============================================================================
 * HELPER: TRIM IDENTIFY STRING
 * ============================================================================ */

/**
 * @brief Copy and trim trailing spaces from an NVMe Identify string
 *
 * @param dst    Destination buffer (must be at least len+1 bytes)
 * @param src    Source bytes from Identify data
 * @param len    Number of bytes to copy
 */
static void nvme_trim_string(char *dst, const uint8_t *src, size_t len)
{
    nvme_memcpy(dst, src, len);
    dst[len] = '\0';

    /* Trim trailing spaces */
    for (int i = (int)len - 1; i >= 0; i--) {
        if (dst[i] == ' ' || dst[i] == '\0') {
            dst[i] = '\0';
        } else {
            break;
        }
    }
}

/* ============================================================================
 * 9. CONTROLLER INITIALIZATION — FULL SEQUENCE
 * ============================================================================ */

/**
 * @brief Initialize the NVMe controller from probe state
 *
 * @details Full NVMe 1.4 initialization sequence:
 *          1. Cache MMIO base and read CAP register
 *          2. Disable controller (CC.EN = 0), wait for CSTS.RDY = 0
 *          3. Allocate admin queue pair
 *          4. Write AQA, ASQ, ACQ registers
 *          5. Enable controller (CC.EN = 1), wait for CSTS.RDY = 1
 *          6. Identify Controller -> extract serial/model/firmware
 *          7. Identify Namespace (NSID=1) -> extract size and LBA format
 *          8. Create I/O CQ, then I/O SQ
 *          9. Mark initialized, log summary
 *
 * @param mmio_base  Kernel-mapped MMIO virtual address
 * @param bar0_phys  Physical BAR0 address (for logging/diagnostics)
 * @return VOS3_NVME_OK on success, negative error code on failure
 *
 * @note Called from storage_hal after successful PCI probe.
 * @note DMA memory allocated via vos3_pmm_alloc_pages().
 */
int vos3_nvme_init(uintptr_t mmio_base, uint32_t bar0_phys)
{
    int rc;
    uint32_t timeout;

    VOS3_INFO("Initializing NVMe controller at MMIO 0x%llx (BAR0 phys 0x%x)",
              (unsigned long long)mmio_base, bar0_phys);

    /* Zero controller state */
    nvme_memset(&g_nvme_ctrl, 0, sizeof(g_nvme_ctrl));
    g_nvme_ctrl.mmio_base = mmio_base;
    g_nvme_ctrl.version   = bar0_phys;

    /* ---- Step 1: Read CAP register ---- */
    g_nvme_ctrl.cap = nvme_read64(mmio_base, VOS3_NVME_REG_CAP);
    g_nvme_ctrl.dstrd = (uint32_t)((g_nvme_ctrl.cap >> 32) & 0xFU);
    g_nvme_ctrl.mqes  = (uint32_t)(g_nvme_ctrl.cap & 0xFFFFU) + 1U;

    uint32_t version = nvme_read32(mmio_base, VOS3_NVME_REG_VS);
    g_nvme_ctrl.version = version;

    VOS3_INFO("CAP: MQES=%u DSTRD=%u Version=%u.%u.%u",
              g_nvme_ctrl.mqes, g_nvme_ctrl.dstrd,
              (version >> 16) & 0xFFFFU,
              (version >> 8) & 0xFFU,
              version & 0xFFU);

    /* ---- Step 2: Disable controller ---- */
    uint32_t cc = nvme_read32(mmio_base, VOS3_NVME_REG_CC);
    if (cc & VOS3_NVME_CC_EN) {
        /* Controller is enabled — disable it first */
        cc &= ~VOS3_NVME_CC_EN;
        nvme_write32(mmio_base, VOS3_NVME_REG_CC, cc);
    }

    /* Wait for CSTS.RDY = 0 (controller not ready = disabled) */
    timeout = VOS3_NVME_POLL_ATTEMPTS;
    while (timeout > 0) {
        uint32_t csts = nvme_read32(mmio_base, VOS3_NVME_REG_CSTS);
        if ((csts & VOS3_NVME_CSTS_RDY) == 0) {
            break;
        }
        __asm__ volatile("pause" ::: "memory");
        timeout--;
    }
    if (timeout == 0) {
        VOS3_ERROR("Timeout waiting for controller disable (CSTS.RDY stuck)");
        return VOS3_NVME_E_TIMEOUT;
    }
    VOS3_INFO("Controller disabled successfully");

    /* ---- Step 3: Allocate admin queue pair ---- */
    uint16_t admin_depth = VOS3_NVME_ADMIN_QUEUE_DEPTH;
    if (admin_depth > g_nvme_ctrl.mqes) {
        admin_depth = (uint16_t)g_nvme_ctrl.mqes;
    }

    rc = nvme_alloc_queue(&g_nvme_ctrl.admin_q, admin_depth, 0);
    if (rc != VOS3_NVME_OK) {
        VOS3_ERROR("Failed to allocate admin queue");
        return rc;
    }

    /* ---- Step 4: Write AQA, ASQ, ACQ registers ---- */
    /* AQA: bits [27:16] = ACQS (CQ size - 1), bits [11:0] = ASQS (SQ size - 1) */
    uint32_t aqa = ((uint32_t)(admin_depth - 1U) << 16) | (uint32_t)(admin_depth - 1U);
    nvme_write32(mmio_base, VOS3_NVME_REG_AQA, aqa);

    /* ASQ: physical address of admin submission queue */
    nvme_write64(mmio_base, VOS3_NVME_REG_ASQ, (uint64_t)g_nvme_ctrl.admin_q.sq_phys);

    /* ACQ: physical address of admin completion queue */
    nvme_write64(mmio_base, VOS3_NVME_REG_ACQ, (uint64_t)g_nvme_ctrl.admin_q.cq_phys);

    VOS3_INFO("Admin queues configured: AQA=0x%x ASQ=0x%llx ACQ=0x%llx",
              aqa,
              (unsigned long long)g_nvme_ctrl.admin_q.sq_phys,
              (unsigned long long)g_nvme_ctrl.admin_q.cq_phys);

    /* ---- Step 5: Enable controller ---- */
    cc = VOS3_NVME_CC_EN
       | VOS3_NVME_CC_CSS_NVM
       | VOS3_NVME_CC_MPS_4K
       | VOS3_NVME_CC_IOSQES(6)    /* 2^6 = 64-byte SQE */
       | VOS3_NVME_CC_IOCQES(4);   /* 2^4 = 16-byte CQE */

    nvme_write32(mmio_base, VOS3_NVME_REG_CC, cc);

    /* Wait for CSTS.RDY = 1 (controller ready) */
    timeout = VOS3_NVME_POLL_ATTEMPTS;
    while (timeout > 0) {
        uint32_t csts = nvme_read32(mmio_base, VOS3_NVME_REG_CSTS);

        /* Check for fatal status during enable */
        if (csts & VOS3_NVME_CSTS_CFS) {
            VOS3_ERROR("Controller fatal status during enable (CSTS=0x%x)", csts);
            return VOS3_NVME_E_FATAL;
        }

        if (csts & VOS3_NVME_CSTS_RDY) {
            break;
        }

        __asm__ volatile("pause" ::: "memory");
        timeout--;
    }
    if (timeout == 0) {
        VOS3_ERROR("Timeout waiting for controller enable (CSTS.RDY not set)");
        return VOS3_NVME_E_TIMEOUT;
    }
    VOS3_INFO("Controller enabled and ready");

    /* ---- Step 6: Identify Controller ---- */
    uint8_t id_ctrl_buf[4096] __attribute__((aligned(16)));
    rc = nvme_admin_identify(VOS3_NVME_IDENTIFY_CTRL, 0, id_ctrl_buf);
    if (rc != VOS3_NVME_OK) {
        VOS3_ERROR("Identify Controller failed: rc=%d", rc);
        return rc;
    }

    /* Extract serial, model, firmware from Identify Controller data
     * NVMe 1.4 Spec: SN at offset 4 (20 bytes), MN at offset 24 (40 bytes),
     * FR at offset 64 (8 bytes) */
    nvme_trim_string(g_nvme_ctrl.serial,   &id_ctrl_buf[4],  20);
    nvme_trim_string(g_nvme_ctrl.model,    &id_ctrl_buf[24], 40);
    nvme_trim_string(g_nvme_ctrl.firmware, &id_ctrl_buf[64],  8);

    VOS3_INFO("Controller: %s | Serial: %s | FW: %s",
              g_nvme_ctrl.model, g_nvme_ctrl.serial, g_nvme_ctrl.firmware);

    /* ---- Step 7: Identify Namespace (NSID=1) ---- */
    uint8_t id_ns_buf[4096] __attribute__((aligned(16)));
    rc = nvme_admin_identify(VOS3_NVME_IDENTIFY_NS, 1, id_ns_buf);
    if (rc != VOS3_NVME_OK) {
        VOS3_ERROR("Identify Namespace failed: rc=%d", rc);
        return rc;
    }

    /* Extract namespace size (NSZE at offset 0, 8 bytes, little-endian) */
    uint64_t nsze = 0;
    nvme_memcpy(&nsze, &id_ns_buf[0], sizeof(uint64_t));
    g_nvme_ctrl.ns_size = nsze;
    g_nvme_ctrl.nsid    = 1;

    /* Extract LBA format (FLBAS at offset 26, 1 byte; bits [3:0] = format index)
     * LBA Format table starts at offset 128, each entry is 4 bytes.
     * Bits [23:16] of each entry = LBADS (LBA data size as power of 2). */
    uint8_t flbas = id_ns_buf[26];
    uint8_t lba_format_idx = flbas & 0x0FU;
    uint32_t lba_format;
    nvme_memcpy(&lba_format, &id_ns_buf[128 + lba_format_idx * 4], sizeof(uint32_t));
    uint8_t lbads = (uint8_t)((lba_format >> 16) & 0xFFU);

    if (lbads == 0) {
        /* Default to 512-byte sectors if LBADS is unset */
        g_nvme_ctrl.ns_lba_size = VOS3_NVME_SECTOR_SIZE;
    } else {
        g_nvme_ctrl.ns_lba_size = 1U << lbads;
    }

    VOS3_INFO("Namespace 1: size=%llu LBAs, sector_size=%u bytes, total=%llu MB",
              (unsigned long long)g_nvme_ctrl.ns_size,
              g_nvme_ctrl.ns_lba_size,
              (unsigned long long)(g_nvme_ctrl.ns_size *
                                   g_nvme_ctrl.ns_lba_size / (1024ULL * 1024ULL)));

    /* ---- Step 8: Create I/O queues ---- */
    uint16_t io_depth = VOS3_NVME_IO_QUEUE_DEPTH;
    if (io_depth > g_nvme_ctrl.mqes) {
        io_depth = (uint16_t)g_nvme_ctrl.mqes;
    }

    /* Allocate one I/O queue pair (expand to per-CPU in future SMP phases) */
    uint32_t num_io_queues = 1;
    for (uint32_t i = 0; i < num_io_queues; i++) {
        uint8_t qid = (uint8_t)(i + 1);

        rc = nvme_alloc_queue(&g_nvme_ctrl.io_q[i], io_depth, qid);
        if (rc != VOS3_NVME_OK) {
            VOS3_ERROR("Failed to allocate I/O queue %u", qid);
            return rc;
        }

        /* Allocate a PRP list page for this I/O queue */
        uintptr_t prp_phys = vos3_pmm_alloc_pages(1, 0);
        if (prp_phys == 0) {
            VOS3_ERROR("Failed to allocate PRP list page for queue %u", qid);
            return VOS3_NVME_E_NO_MEM;
        }
        nvme_memset(nvme_phys_to_virt(prp_phys), 0, 4096U);
        g_nvme_ctrl.prp_lists[i].list_phys = prp_phys;
        g_nvme_ctrl.prp_lists[i].count     = 0;

        /* Create I/O CQ first, then I/O SQ (spec requirement) */
        rc = nvme_admin_create_cq(&g_nvme_ctrl.io_q[i]);
        if (rc != VOS3_NVME_OK) {
            return rc;
        }

        rc = nvme_admin_create_sq(&g_nvme_ctrl.io_q[i]);
        if (rc != VOS3_NVME_OK) {
            return rc;
        }
    }
    g_nvme_ctrl.io_queue_count = num_io_queues;

    /* ---- Step 9: Mark initialized ---- */
    g_nvme_ctrl.initialized = 1;
    g_nvme_ctrl.fatal       = 0;

    VOS3_INFO("=== NVMe Initialization Complete ===");
    VOS3_INFO("  Controller: %s", g_nvme_ctrl.model);
    VOS3_INFO("  Firmware:   %s", g_nvme_ctrl.firmware);
    VOS3_INFO("  Serial:     %s", g_nvme_ctrl.serial);
    VOS3_INFO("  Namespace:  %llu sectors (%u bytes/sector)",
              (unsigned long long)g_nvme_ctrl.ns_size,
              g_nvme_ctrl.ns_lba_size);
    VOS3_INFO("  I/O Queues: %u (depth=%u)", num_io_queues, io_depth);
    VOS3_INFO("  DMA Guard:  [0x%llx, 0x%llx) excluding PCI hole [0xC0000000, 0x100000000)",
              (unsigned long long)VOS3_NVME_DMA_LOWER_BOUND,
              (unsigned long long)VOS3_NVME_DMA_UPPER_BOUND);

    return VOS3_NVME_OK;
}

/* ============================================================================
 * 10. BLOCK I/O: READ
 * ============================================================================ */

/**
 * @brief Read sectors from the NVMe namespace
 *
 * @details Validates parameters, builds a PRP list with full DMA boundary
 *          checking, submits an NVM Read command to I/O queue 0, and polls
 *          for completion.  On fatal controller status, the destination
 *          buffer is zero-filled before returning the error.
 *
 * @param lba    Starting Logical Block Address
 * @param count  Number of sectors to read (must be > 0)
 * @param buf    Destination buffer (must be in HHDM range)
 * @return VOS3_NVME_OK on success, negative error code on failure
 */
int vos3_nvme_read(uint64_t lba, uint32_t count, void *buf)
{
    if (!g_nvme_ctrl.initialized) {
        VOS3_ERROR("Controller not initialized");
        return VOS3_NVME_E_NOT_READY;
    }

    /* Check for fatal status before issuing any I/O */
    if (g_nvme_ctrl.fatal) {
        VOS3_ERROR("Controller in fatal state — read rejected");
        if (buf != NULL && count > 0) {
            nvme_memset(buf, 0, (size_t)count * g_nvme_ctrl.ns_lba_size);
        }
        return VOS3_NVME_E_FATAL;
    }

    /* Parameter validation */
    if (count == 0 || buf == NULL) {
        VOS3_ERROR("Invalid read parameters: count=%u buf=%p", count, buf);
        return VOS3_NVME_E_INVALID;
    }

    /* Integer overflow guard: (uint64_t)count * sector_size */
    uint64_t total_bytes = (uint64_t)count * (uint64_t)g_nvme_ctrl.ns_lba_size;
    if (total_bytes > VOS3_NVME_MAX_TRANSFER_SIZE) {
        VOS3_ERROR("Read too large: %llu bytes > %u max",
                   (unsigned long long)total_bytes, VOS3_NVME_MAX_TRANSFER_SIZE);
        return VOS3_NVME_E_INVALID;
    }

    /* LBA range check */
    if (lba + count > g_nvme_ctrl.ns_size) {
        VOS3_ERROR("Read beyond namespace: LBA %llu + count %u > %llu",
                   (unsigned long long)lba, count,
                   (unsigned long long)g_nvme_ctrl.ns_size);
        return VOS3_NVME_E_INVALID;
    }

    /* Check CSTS for newly-detected fatal status */
    uint32_t csts = nvme_read32(g_nvme_ctrl.mmio_base, VOS3_NVME_REG_CSTS);
    if (csts & VOS3_NVME_CSTS_CFS) {
        VOS3_ERROR("CSTS.CFS detected before read — controller fatal");
        g_nvme_ctrl.fatal = 1;
        nvme_memset(buf, 0, (size_t)total_bytes);
        return VOS3_NVME_E_FATAL;
    }

    /* Build PRP list with boundary validation */
    uint64_t prp1, prp2;
    int rc = nvme_build_prp_list(&g_nvme_ctrl.prp_lists[0], buf,
                                  (uint32_t)total_bytes, &prp1, &prp2);
    if (rc != VOS3_NVME_OK) {
        VOS3_ERROR("PRP list build failed for read: rc=%d", rc);
        return rc;
    }

    /* Build the NVM Read SQE */
    vos3_nvme_sqe_t cmd;
    nvme_memset(&cmd, 0, sizeof(cmd));
    cmd.opcode = VOS3_NVME_IO_READ;
    cmd.nsid   = g_nvme_ctrl.nsid;
    cmd.prp1   = prp1;
    cmd.prp2   = prp2;
    cmd.cdw10  = (uint32_t)(lba & 0xFFFFFFFFU);        /* LBA bits [31:0] */
    cmd.cdw11  = (uint32_t)((lba >> 32) & 0xFFFFFFFFU); /* LBA bits [63:32] */
    cmd.cdw12  = count - 1U;                             /* NVMe: 0-based count */

    /* Submit to I/O queue 0 */
    uint16_t cid = nvme_submit_cmd(&g_nvme_ctrl.io_q[0], &cmd);
    rc = nvme_poll_completion(&g_nvme_ctrl.io_q[0], cid, NULL);

    if (rc != VOS3_NVME_OK) {
        VOS3_ERROR("NVM Read failed: LBA=%llu count=%u rc=%d",
                   (unsigned long long)lba, count, rc);
        g_nvme_ctrl.total_errors++;

        /* Zero-fill on failure to prevent information leakage */
        nvme_memset(buf, 0, (size_t)total_bytes);
        return rc;
    }

    g_nvme_ctrl.total_reads++;
    return VOS3_NVME_OK;
}

/* ============================================================================
 * 10b. BLOCK I/O: WRITE
 * ============================================================================ */

/**
 * @brief Write sectors to the NVMe namespace
 *
 * @details Validates parameters, builds a PRP list with full DMA boundary
 *          checking, submits an NVM Write command to I/O queue 0, and polls
 *          for completion.
 *
 * @param lba    Starting Logical Block Address
 * @param count  Number of sectors to write (must be > 0)
 * @param buf    Source buffer (must be in HHDM range)
 * @return VOS3_NVME_OK on success, negative error code on failure
 */
int vos3_nvme_write(uint64_t lba, uint32_t count, const void *buf)
{
    if (!g_nvme_ctrl.initialized) {
        VOS3_ERROR("Controller not initialized");
        return VOS3_NVME_E_NOT_READY;
    }

    /* Check for fatal status before issuing any I/O */
    if (g_nvme_ctrl.fatal) {
        VOS3_ERROR("Controller in fatal state — write rejected");
        return VOS3_NVME_E_FATAL;
    }

    /* Parameter validation */
    if (count == 0 || buf == NULL) {
        VOS3_ERROR("Invalid write parameters: count=%u buf=%p", count, buf);
        return VOS3_NVME_E_INVALID;
    }

    /* Integer overflow guard: (uint64_t)count * sector_size */
    uint64_t total_bytes = (uint64_t)count * (uint64_t)g_nvme_ctrl.ns_lba_size;
    if (total_bytes > VOS3_NVME_MAX_TRANSFER_SIZE) {
        VOS3_ERROR("Write too large: %llu bytes > %u max",
                   (unsigned long long)total_bytes, VOS3_NVME_MAX_TRANSFER_SIZE);
        return VOS3_NVME_E_INVALID;
    }

    /* LBA range check */
    if (lba + count > g_nvme_ctrl.ns_size) {
        VOS3_ERROR("Write beyond namespace: LBA %llu + count %u > %llu",
                   (unsigned long long)lba, count,
                   (unsigned long long)g_nvme_ctrl.ns_size);
        return VOS3_NVME_E_INVALID;
    }

    /* Check CSTS for newly-detected fatal status */
    uint32_t csts = nvme_read32(g_nvme_ctrl.mmio_base, VOS3_NVME_REG_CSTS);
    if (csts & VOS3_NVME_CSTS_CFS) {
        VOS3_ERROR("CSTS.CFS detected before write — controller fatal");
        g_nvme_ctrl.fatal = 1;
        return VOS3_NVME_E_FATAL;
    }

    /* Build PRP list with boundary validation.
     * Cast away const: PRP builder needs the virtual address for phys
     * conversion, but the data is only read by the controller via DMA. */
    uint64_t prp1, prp2;
    int rc = nvme_build_prp_list(&g_nvme_ctrl.prp_lists[0], (void *)(uintptr_t)buf,
                                  (uint32_t)total_bytes, &prp1, &prp2);
    if (rc != VOS3_NVME_OK) {
        VOS3_ERROR("PRP list build failed for write: rc=%d", rc);
        return rc;
    }

    /* Build the NVM Write SQE */
    vos3_nvme_sqe_t cmd;
    nvme_memset(&cmd, 0, sizeof(cmd));
    cmd.opcode = VOS3_NVME_IO_WRITE;
    cmd.nsid   = g_nvme_ctrl.nsid;
    cmd.prp1   = prp1;
    cmd.prp2   = prp2;
    cmd.cdw10  = (uint32_t)(lba & 0xFFFFFFFFU);        /* LBA bits [31:0] */
    cmd.cdw11  = (uint32_t)((lba >> 32) & 0xFFFFFFFFU); /* LBA bits [63:32] */
    cmd.cdw12  = count - 1U;                             /* NVMe: 0-based count */

    /* Submit to I/O queue 0 */
    uint16_t cid = nvme_submit_cmd(&g_nvme_ctrl.io_q[0], &cmd);
    rc = nvme_poll_completion(&g_nvme_ctrl.io_q[0], cid, NULL);

    if (rc != VOS3_NVME_OK) {
        VOS3_ERROR("NVM Write failed: LBA=%llu count=%u rc=%d",
                   (unsigned long long)lba, count, rc);
        g_nvme_ctrl.total_errors++;
        return rc;
    }

    g_nvme_ctrl.total_writes++;
    return VOS3_NVME_OK;
}

/* ============================================================================
 * 11. CONTROLLER RESET
 * ============================================================================ */

/**
 * @brief Reset the NVMe controller after a fatal error
 *
 * @details Performs a full controller reset:
 *          1. Disable controller (CC.EN = 0), wait for CSTS.RDY = 0
 *          2. Zero all queue head/tail pointers and reset phase bits
 *          3. Re-configure admin queue registers (AQA, ASQ, ACQ)
 *          4. Re-enable controller, wait for CSTS.RDY = 1
 *          5. Re-create I/O queues
 *          6. Clear fatal flag
 *
 *          All in-flight I/O is lost — "zero zombie I/O".
 *
 * @return VOS3_NVME_OK on success, negative error code on failure
 */
int vos3_nvme_reset(void)
{
    uint32_t timeout;
    int rc;

    if (!g_nvme_ctrl.initialized) {
        VOS3_ERROR("Cannot reset — controller not initialized");
        return VOS3_NVME_E_NOT_READY;
    }

    uintptr_t base = g_nvme_ctrl.mmio_base;

    VOS3_WARN("Controller reset initiated");

    /* ---- Step 1: Disable controller ---- */
    uint32_t cc = nvme_read32(base, VOS3_NVME_REG_CC);
    cc &= ~VOS3_NVME_CC_EN;
    nvme_write32(base, VOS3_NVME_REG_CC, cc);

    /* Wait for CSTS.RDY = 0 */
    timeout = VOS3_NVME_POLL_ATTEMPTS;
    while (timeout > 0) {
        uint32_t csts = nvme_read32(base, VOS3_NVME_REG_CSTS);
        if ((csts & VOS3_NVME_CSTS_RDY) == 0) {
            break;
        }
        __asm__ volatile("pause" ::: "memory");
        timeout--;
    }
    if (timeout == 0) {
        VOS3_ERROR("Reset timeout: controller did not disable");
        return VOS3_NVME_E_TIMEOUT;
    }

    /* ---- Step 2: Zero all queue state ---- */

    /* Admin queue */
    g_nvme_ctrl.admin_q.sq_tail     = 0;
    g_nvme_ctrl.admin_q.cq_head     = 0;
    g_nvme_ctrl.admin_q.cq_phase    = 1;
    g_nvme_ctrl.admin_q.cid_counter = 0;

    /* Zero the admin queue memory */
    nvme_memset((void *)g_nvme_ctrl.admin_q.sq, 0,
                (size_t)g_nvme_ctrl.admin_q.depth * sizeof(vos3_nvme_sqe_t));
    nvme_memset((void *)g_nvme_ctrl.admin_q.cq, 0,
                (size_t)g_nvme_ctrl.admin_q.depth * sizeof(vos3_nvme_cqe_t));

    /* I/O queues */
    for (uint32_t i = 0; i < g_nvme_ctrl.io_queue_count; i++) {
        g_nvme_ctrl.io_q[i].sq_tail     = 0;
        g_nvme_ctrl.io_q[i].cq_head     = 0;
        g_nvme_ctrl.io_q[i].cq_phase    = 1;
        g_nvme_ctrl.io_q[i].cid_counter = 0;

        nvme_memset((void *)g_nvme_ctrl.io_q[i].sq, 0,
                    (size_t)g_nvme_ctrl.io_q[i].depth * sizeof(vos3_nvme_sqe_t));
        nvme_memset((void *)g_nvme_ctrl.io_q[i].cq, 0,
                    (size_t)g_nvme_ctrl.io_q[i].depth * sizeof(vos3_nvme_cqe_t));
    }

    /* ---- Step 3: Re-write admin queue registers ---- */
    uint32_t admin_depth = g_nvme_ctrl.admin_q.depth;
    uint32_t aqa = ((admin_depth - 1U) << 16) | (admin_depth - 1U);
    nvme_write32(base, VOS3_NVME_REG_AQA, aqa);
    nvme_write64(base, VOS3_NVME_REG_ASQ, (uint64_t)g_nvme_ctrl.admin_q.sq_phys);
    nvme_write64(base, VOS3_NVME_REG_ACQ, (uint64_t)g_nvme_ctrl.admin_q.cq_phys);

    /* ---- Step 4: Re-enable controller ---- */
    cc = VOS3_NVME_CC_EN
       | VOS3_NVME_CC_CSS_NVM
       | VOS3_NVME_CC_MPS_4K
       | VOS3_NVME_CC_IOSQES(6)
       | VOS3_NVME_CC_IOCQES(4);
    nvme_write32(base, VOS3_NVME_REG_CC, cc);

    /* Wait for CSTS.RDY = 1 */
    timeout = VOS3_NVME_POLL_ATTEMPTS;
    while (timeout > 0) {
        uint32_t csts = nvme_read32(base, VOS3_NVME_REG_CSTS);
        if (csts & VOS3_NVME_CSTS_CFS) {
            VOS3_ERROR("Controller fatal during re-enable");
            return VOS3_NVME_E_FATAL;
        }
        if (csts & VOS3_NVME_CSTS_RDY) {
            break;
        }
        __asm__ volatile("pause" ::: "memory");
        timeout--;
    }
    if (timeout == 0) {
        VOS3_ERROR("Reset timeout: controller did not re-enable");
        return VOS3_NVME_E_TIMEOUT;
    }

    /* ---- Step 5: Re-create I/O queues ---- */
    for (uint32_t i = 0; i < g_nvme_ctrl.io_queue_count; i++) {
        rc = nvme_admin_create_cq(&g_nvme_ctrl.io_q[i]);
        if (rc != VOS3_NVME_OK) {
            VOS3_ERROR("Failed to re-create I/O CQ %u during reset", i + 1);
            return rc;
        }

        rc = nvme_admin_create_sq(&g_nvme_ctrl.io_q[i]);
        if (rc != VOS3_NVME_OK) {
            VOS3_ERROR("Failed to re-create I/O SQ %u during reset", i + 1);
            return rc;
        }
    }

    /* ---- Step 6: Clear fatal flag ---- */
    g_nvme_ctrl.fatal = 0;

    VOS3_INFO("Controller reset complete — zero zombie I/O");

    return VOS3_NVME_OK;
}

/* ============================================================================
 * 12. HEALTH CHECK
 * ============================================================================ */

/**
 * @brief Check if the NVMe controller is healthy
 *
 * @details Verifies the controller is initialized and not in a fatal state.
 *          Reads CSTS register live to detect newly-asserted CFS bit.
 *          If CFS is detected for the first time, sets the fatal flag.
 *
 * @return 1 if healthy, 0 if unhealthy or not initialized
 */
int vos3_nvme_is_healthy(void)
{
    if (!g_nvme_ctrl.initialized) {
        return 0;
    }

    if (g_nvme_ctrl.fatal) {
        return 0;
    }

    /* Live check: read CSTS for CFS bit */
    uint32_t csts = nvme_read32(g_nvme_ctrl.mmio_base, VOS3_NVME_REG_CSTS);
    if (csts & VOS3_NVME_CSTS_CFS) {
        VOS3_ERROR("CSTS.CFS newly detected — marking controller fatal");
        g_nvme_ctrl.fatal = 1;
        return 0;
    }

    return 1;
}

/* ============================================================================
 * 13. CONTROLLER INFO ACCESSOR
 * ============================================================================ */

/**
 * @brief Get read-only pointer to the NVMe controller state
 *
 * @return Pointer to the controller state structure if initialized,
 *         or NULL if the controller has not been initialized.
 */
const vos3_nvme_ctrl_t *vos3_nvme_get_ctrl(void)
{
    if (!g_nvme_ctrl.initialized) {
        return NULL;
    }

    return &g_nvme_ctrl;
}
