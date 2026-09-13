/**
 * @file numa.h
 * @brief VOS3 NUMA (Non-Uniform Memory Access) Support
 *
 * @details Provides topology-aware memory allocation for systems with
 *          multiple NUMA nodes. Parses ACPI SRAT tables to detect nodes.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 * @note Phase 17.5.2 - NUMA Awareness
 */

#ifndef VOS3_NUMA_H
#define VOS3_NUMA_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * NUMA CONFIGURATION
 * ============================================================================ */

/** @brief Maximum supported NUMA nodes */
#define VOS3_NUMA_MAX_NODES     8U

/** @brief Any NUMA node (no preference) */
#define VOS3_NUMA_NODE_ANY      ((vos3_numa_node_t)0xFFU)

/** @brief Local NUMA node (current CPU) */
#define VOS3_NUMA_NODE_LOCAL    ((vos3_numa_node_t)0xFEU)

/* ============================================================================
 * NUMA TYPES
 * ============================================================================ */

/** @brief NUMA node identifier */
typedef uint8_t vos3_numa_node_t;

/**
 * @brief NUMA node flags
 */
typedef enum vos3_numa_flags {
    VOS3_NUMA_FLAG_NONE         = 0U,
    VOS3_NUMA_FLAG_ONLINE       = (1U << 0),    /**< Node is online */
    VOS3_NUMA_FLAG_HAS_CPU      = (1U << 1),    /**< Node has CPUs */
    VOS3_NUMA_FLAG_HAS_MEMORY   = (1U << 2),    /**< Node has memory */
    VOS3_NUMA_FLAG_HOTPLUG      = (1U << 3),    /**< Node supports hotplug */
} vos3_numa_flags_t;

/**
 * @brief NUMA node statistics
 */
typedef struct vos3_numa_node_stats {
    uint64_t    total_memory;       /**< Total memory on node */
    uint64_t    free_memory;        /**< Free memory on node */
    uint64_t    alloc_count;        /**< Allocation count */
    uint32_t    cpu_count;          /**< CPUs on this node */
    uint32_t    flags;              /**< Node flags */
} vos3_numa_node_stats_t;

/**
 * @brief NUMA memory range
 */
typedef struct vos3_numa_range {
    uintptr_t   base;               /**< Range base address */
    uint64_t    length;             /**< Range length */
    vos3_numa_node_t node;          /**< NUMA node */
    uint8_t     _pad[7];            /**< Alignment */
} vos3_numa_range_t;

/**
 * @brief Maximum memory ranges per node */
#define VOS3_NUMA_MAX_RANGES    16U

/**
 * @brief NUMA topology structure
 */
typedef struct vos3_numa_topology {
    uint32_t                node_count;         /**< Number of NUMA nodes */
    uint32_t                range_count;        /**< Number of memory ranges */
    vos3_numa_node_stats_t  nodes[VOS3_NUMA_MAX_NODES];
    vos3_numa_range_t       ranges[VOS3_NUMA_MAX_RANGES];
    uint8_t                 cpu_to_node[256];   /**< CPU to NUMA node mapping */
    uint32_t                initialized;        /**< Initialization flag */
    uint32_t                _pad;               /**< Alignment */
} vos3_numa_topology_t;

/* ============================================================================
 * NUMA FUNCTIONS
 * ============================================================================ */

/**
 * @brief Initialize NUMA subsystem
 * @return 0 on success, negative on error
 */
int vos3_numa_init(void);

/**
 * @brief Get number of NUMA nodes
 * @return Number of nodes (at least 1)
 */
uint32_t vos3_numa_node_count(void);

/**
 * @brief Get NUMA node for a physical address
 * @param[in] phys_addr Physical address
 * @return NUMA node, or VOS3_NUMA_NODE_ANY if unknown
 */
vos3_numa_node_t vos3_numa_get_node(uintptr_t phys_addr);

/**
 * @brief Get NUMA node for current CPU
 * @return NUMA node
 */
vos3_numa_node_t vos3_numa_current_node(void);

/**
 * @brief Allocate a page from a specific NUMA node
 * @param[in] node Target NUMA node
 * @param[in] flags Allocation flags (from pmm.h)
 * @return Physical address, or 0 on failure
 */
uintptr_t vos3_numa_alloc_page(vos3_numa_node_t node, uint32_t flags);

/**
 * @brief Free a page (NUMA-aware)
 * @param[in] addr Physical address
 */
void vos3_numa_free_page(uintptr_t addr);

/**
 * @brief Get statistics for a NUMA node
 * @param[in] node Node to query
 * @param[out] stats Statistics output
 * @return 0 on success
 */
int vos3_numa_get_stats(vos3_numa_node_t node, vos3_numa_node_stats_t* stats);

/**
 * @brief Check if NUMA is available
 * @return 1 if NUMA available, 0 if single-node (UMA)
 */
int vos3_numa_is_available(void);

/**
 * @brief Get the full NUMA topology
 * @return Pointer to topology structure
 */
const vos3_numa_topology_t* vos3_numa_get_topology(void);

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_NUMA_OK            (0)
#define VOS3_NUMA_ERR_NOTINIT   (-1)
#define VOS3_NUMA_ERR_INVALID   (-2)
#define VOS3_NUMA_ERR_NOMEM     (-3)

#ifdef __cplusplus
}
#endif

#endif /* VOS3_NUMA_H */
