/**
 * @file ai_monitor.c
 * @brief VOS3 AI Memory Access Monitor
 *
 * @details Real-time monitoring of AI memory access patterns.
 *          Tracks read/write frequencies, detects anomalies,
 *          and provides telemetry for optimization.
 *
 * @version 2.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 * @note Phase 17.3 - Full Implementation
 */

#include "../../include/vos/ai_guard.h"
#include "../../include/vos/console.h"
#include "../../include/vos/timer.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/task.h"
#include "../../include/vos/atomic.h"

/* ============================================================================
 * MONITOR CONFIGURATION
 * ============================================================================ */

/** @brief Maximum monitored pages */
#define VOS3_AI_MONITOR_MAX_PAGES       ((size_t)4096U)

/** @brief Ring buffer size for access events */
#define VOS3_AI_ACCESS_LOG_SIZE         ((size_t)1024U)

/** @brief Default anomaly threshold: accesses per window */
#define VOS3_AI_DEFAULT_THRESHOLD       ((uint64_t)10000U)

/** @brief Default anomaly detection window in ticks */
#define VOS3_AI_DEFAULT_WINDOW          ((uint64_t)10U)

/** @brief Default telemetry interval in ticks */
#define VOS3_AI_DEFAULT_TELEMETRY       ((uint32_t)1000U)

/* ============================================================================
 * ACCESS LOG STRUCTURES
 * ============================================================================ */

/**
 * @brief Access event type
 */
typedef enum vos3_access_type {
    VOS3_ACCESS_READ    = 0U,
    VOS3_ACCESS_WRITE   = 1U,
    VOS3_ACCESS_FAULT   = 2U,
} vos3_access_type_t;

/**
 * @brief Access log entry
 */
typedef struct vos3_access_log_entry {
    uint64_t            timestamp;      /**< Tick when access occurred */
    uintptr_t           address;        /**< Access address */
    uint32_t            pid;            /**< Process ID */
    uint32_t            region_id;      /**< AI region ID */
    vos3_access_type_t  type;           /**< Read/Write/Fault */
    uint32_t            padding;        /**< Alignment padding */
} vos3_access_log_entry_t;

/**
 * @brief Ring buffer for access logging
 */
typedef struct vos3_access_log {
    vos3_access_log_entry_t entries[VOS3_AI_ACCESS_LOG_SIZE];
    volatile uint32_t       head;       /**< Write position */
    volatile uint32_t       tail;       /**< Read position */
    volatile uint32_t       count;      /**< Entry count */
    uint32_t                overflow;   /**< Overflow counter */
} vos3_access_log_t;

/* ============================================================================
 * INTERNAL DATA
 * ============================================================================ */

/** @brief Monitor initialized flag */
static volatile int g_ai_monitor_initialized = 0;

/** @brief Global access log (ring buffer) */
static vos3_access_log_t g_access_log;

/** @brief Monitor statistics */
static struct {
    uint64_t tick_count;            /**< Ticks since init */
    uint64_t total_accesses;        /**< Total access events */
    uint64_t total_reads;           /**< Total reads */
    uint64_t total_writes;          /**< Total writes */
    uint64_t total_faults;          /**< Total faults */
    uint64_t anomaly_count;         /**< Anomalies detected */
    uint64_t last_report_tick;      /**< Last report tick */
    uint64_t window_accesses;       /**< Accesses in current window */
    uint64_t window_start_tick;     /**< Start of current window */
} g_monitor_stats = {0};

/** @brief Simple spinlock for atomic operations */
static vos3_spinlock_t g_monitor_lock = VOS3_SPINLOCK_INIT;

/** @brief Runtime configuration (Phase 17.4) */
static vos3_ai_monitor_config_t g_config = {
    .threshold = VOS3_AI_DEFAULT_THRESHOLD,
    .window = VOS3_AI_DEFAULT_WINDOW,
    .type_thresholds = { 20000U, 10000U, 15000U },  /* MODEL, TENSOR, SCRATCH */
    .type_windows = { 10U, 10U, 10U },
    .type_override_mask = 0U,
    .telemetry_interval = VOS3_AI_DEFAULT_TELEMETRY,
};

/** @brief Alert callback (Phase 17.4) */
static vos3_ai_alert_callback_t g_alert_callback = NULL;

/* ============================================================================
 * LOCK HELPERS
 * ============================================================================ */

static inline void monitor_lock(void)
{
    vos3_spinlock_acquire(&g_monitor_lock);
}

static inline void monitor_unlock(void)
{
    vos3_spinlock_release(&g_monitor_lock);
}

/* ============================================================================
 * RING BUFFER OPERATIONS
 * ============================================================================ */

/**
 * @brief Add entry to access log (atomic-safe)
 */
static void access_log_push(uintptr_t addr, uint32_t pid, uint32_t region_id,
                            vos3_access_type_t type)
{
    monitor_lock();

    /* Get next write position */
    uint32_t pos = g_access_log.head;

    /* Write entry */
    g_access_log.entries[pos].timestamp = vos3_timer_get_ticks();
    g_access_log.entries[pos].address = addr;
    g_access_log.entries[pos].pid = pid;
    g_access_log.entries[pos].region_id = region_id;
    g_access_log.entries[pos].type = type;

    /* Advance head (circular) */
    g_access_log.head = (pos + 1U) % VOS3_AI_ACCESS_LOG_SIZE;

    /* Update count */
    if (g_access_log.count < VOS3_AI_ACCESS_LOG_SIZE) {
        g_access_log.count++;
    } else {
        /* Buffer full - overwrite oldest */
        g_access_log.tail = (g_access_log.tail + 1U) % VOS3_AI_ACCESS_LOG_SIZE;
        g_access_log.overflow++;
    }

    monitor_unlock();
}

/* ============================================================================
 * ANOMALY DETECTION
 * ============================================================================ */

/**
 * @brief Get threshold for a region type
 */
static uint64_t get_threshold_for_type(vos3_ai_guard_type_t type)
{
    if (type < VOS3_AI_GUARD_TYPE_COUNT &&
        (g_config.type_override_mask & (1U << type))) {
        return g_config.type_thresholds[type];
    }
    return g_config.threshold;
}

/**
 * @brief Get window for a region type
 */
static uint64_t get_window_for_type(vos3_ai_guard_type_t type)
{
    if (type < VOS3_AI_GUARD_TYPE_COUNT &&
        (g_config.type_override_mask & (1U << type))) {
        return g_config.type_windows[type];
    }
    return g_config.window;
}

/**
 * @brief Check for access anomalies
 */
static void check_anomaly(vos3_ai_guard_region_t* region, uint64_t current_tick)
{
    /* Get appropriate threshold/window for region type */
    vos3_ai_guard_type_t type = region ? region->type : VOS3_AI_GUARD_TENSOR;
    uint64_t threshold = get_threshold_for_type(type);
    uint64_t window = get_window_for_type(type);

    /* Reset window if needed */
    if (current_tick - g_monitor_stats.window_start_tick >= window) {
        /* Check threshold before resetting */
        if (g_monitor_stats.window_accesses > threshold) {
            g_monitor_stats.anomaly_count++;

            /* Update region anomaly stats */
            if (region != NULL) {
                region->stats.anomaly_count++;
                region->stats.last_anomaly_time = current_tick;

                /* Update rolling window */
                region->stats.window_accesses[region->stats.window_index] =
                    g_monitor_stats.window_accesses;
                region->stats.window_index =
                    (region->stats.window_index + 1U) % 4U;
            }

            /* Fire alert (Phase 17.4) */
            vos3_ai_alert_t alert = {
                .timestamp = current_tick,
                .type = VOS3_AI_ALERT_ANOMALY,
                .severity = VOS3_AI_ALERT_WARNING,
                .region_id = region ? region->id : 0U,
                .pid = 0U,
                .value1 = g_monitor_stats.window_accesses,
                .value2 = threshold,
            };
            vos3_task_t* current = vos3_sched_current();
            if (current != NULL) {
                alert.pid = current->pid;
            }
            vos3_ai_monitor_fire_alert(&alert);
        }
        g_monitor_stats.window_accesses = 0ULL;
        g_monitor_stats.window_start_tick = current_tick;
    }

    g_monitor_stats.window_accesses++;
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize AI memory monitor
 * @return 0 on success
 */
int vos3_ai_monitor_init(void)
{
    if (g_ai_monitor_initialized) {
        return 0;
    }

    /* Initialize ring buffer */
    g_access_log.head = 0U;
    g_access_log.tail = 0U;
    g_access_log.count = 0U;
    g_access_log.overflow = 0U;

    /* Initialize statistics */
    g_monitor_stats.tick_count = 0ULL;
    g_monitor_stats.total_accesses = 0ULL;
    g_monitor_stats.total_reads = 0ULL;
    g_monitor_stats.total_writes = 0ULL;
    g_monitor_stats.total_faults = 0ULL;
    g_monitor_stats.anomaly_count = 0ULL;
    g_monitor_stats.last_report_tick = 0ULL;
    g_monitor_stats.window_accesses = 0ULL;
    g_monitor_stats.window_start_tick = vos3_timer_get_ticks();

    g_ai_monitor_initialized = 1;

    VOS3_INFO("[AI-MONITOR] Access monitoring initialized");
    VOS3_INFO("[AI-MONITOR] Config: threshold=%llu window=%llu ticks",
              (unsigned long long)g_config.threshold,
              (unsigned long long)g_config.window);

    return 0;
}

/**
 * @brief Start monitoring a region
 * @param[in] region Region to monitor
 * @return 0 on success
 */
int vos3_ai_monitor_start(vos3_ai_guard_region_t* region)
{
    if (region == NULL) {
        return -1;
    }

    if (!g_ai_monitor_initialized) {
        vos3_ai_monitor_init();
    }

    VOS3_INFO("[AI-MONITOR] Started monitoring region %u (%s)",
              region->id, vos3_ai_guard_type_name(region->type));
    return 0;
}

/**
 * @brief Stop monitoring a region
 * @param[in] region Region to stop monitoring
 * @return 0 on success
 */
int vos3_ai_monitor_stop(vos3_ai_guard_region_t* region)
{
    if (region == NULL) {
        return -1;
    }

    VOS3_INFO("[AI-MONITOR] Stopped monitoring region %u (R=%llu W=%llu F=%llu)",
              region->id,
              (unsigned long long)region->stats.read_count,
              (unsigned long long)region->stats.write_count,
              (unsigned long long)region->stats.fault_count);
    return 0;
}

/**
 * @brief Record a memory access event (atomic-safe)
 * @param[in] region Region accessed
 * @param[in] addr Access address
 * @param[in] is_write 1 if write, 0 if read
 */
void vos3_ai_monitor_record_access(vos3_ai_guard_region_t* region,
                                    uintptr_t addr,
                                    int is_write)
{
    if (!g_ai_monitor_initialized) {
        return;
    }

    /* Get current task PID */
    uint32_t pid = 0U;
    vos3_task_t* current = vos3_sched_current();
    if (current != NULL) {
        pid = current->pid;
    }

    /* Get region ID */
    uint32_t region_id = (region != NULL) ? region->id : 0U;

    /* Determine access type */
    vos3_access_type_t type = is_write ? VOS3_ACCESS_WRITE : VOS3_ACCESS_READ;

    /* Log to ring buffer */
    access_log_push(addr, pid, region_id, type);

    /* Update global statistics */
    g_monitor_stats.total_accesses++;
    if (is_write) {
        g_monitor_stats.total_writes++;
    } else {
        g_monitor_stats.total_reads++;
    }

    /* Update region statistics */
    if (region != NULL) {
        uint64_t now = vos3_timer_get_ticks();
        if (is_write) {
            region->stats.write_count++;
            region->stats.last_write_time = now;
        } else {
            region->stats.read_count++;
        }
        region->stats.last_access_time = now;

        /* Check for anomalies */
        check_anomaly(region, now);
    }
}

/**
 * @brief Record a fault event
 * @param[in] region Region where fault occurred
 * @param[in] addr Fault address
 */
void vos3_ai_monitor_record_fault(vos3_ai_guard_region_t* region, uintptr_t addr)
{
    if (!g_ai_monitor_initialized) {
        return;
    }

    uint32_t pid = 0U;
    vos3_task_t* current = vos3_sched_current();
    if (current != NULL) {
        pid = current->pid;
    }

    uint32_t region_id = (region != NULL) ? region->id : 0U;

    /* Log fault event */
    access_log_push(addr, pid, region_id, VOS3_ACCESS_FAULT);

    g_monitor_stats.total_faults++;

    if (region != NULL) {
        region->stats.fault_count++;
    }

    VOS3_WARN("[AI-MONITOR] Fault recorded: addr=0x%llx pid=%u region=%u",
              (unsigned long long)addr, pid, region_id);
}

/**
 * @brief Get monitor report for a region
 * @param[in] region Region to report on
 */
void vos3_ai_monitor_report(vos3_ai_guard_region_t* region)
{
    if (region == NULL) {
        return;
    }

    vos3_console_printf("\n[AI-MONITOR] Region %u Report (%s):\n",
                        region->id,
                        vos3_ai_guard_type_name(region->type));
    vos3_console_printf("  Base:       0x%llx\n", (unsigned long long)region->base);
    vos3_console_printf("  Size:       %llu bytes\n", (unsigned long long)region->size);
    vos3_console_printf("  State:      %s\n", vos3_ai_guard_state_name(region->state));
    vos3_console_printf("  Reads:      %llu\n", (unsigned long long)region->stats.read_count);
    vos3_console_printf("  Writes:     %llu\n", (unsigned long long)region->stats.write_count);
    vos3_console_printf("  Faults:     %llu\n", (unsigned long long)region->stats.fault_count);
    vos3_console_printf("  Violations: %llu\n", (unsigned long long)region->stats.violation_count);
    vos3_console_printf("  Last access: tick %llu\n",
                        (unsigned long long)region->stats.last_access_time);
}

/* External: Phase 17.5 tick handlers */
extern void vos3_ai_guard_integrity_tick(uint64_t current_tick);
extern void vos3_ai_guard_pressure_tick(void);
extern void vos3_ai_telemetry_queue_alert(const vos3_ai_alert_t* alert);
/* External: Phase 4.2.5 timer + heartbeat tick */
extern void vos3_ai_timer_tick(uint64_t current_tick);
/* External: Phase 4.2.7 instruction drift watchdog */
extern void vos3_ai_drift_watchdog_tick(uint64_t current_tick);
/* External: Phase 4.7 NMI streaming stall watchdog */
extern void vos3_nmi_watchdog_tick(uint64_t current_tick);

/**
 * @brief Periodic monitor work (called from a scheduler process-context safe point)
 */
void vos3_ai_monitor_tick(void)
{
    if (!g_ai_monitor_initialized) {
        return;
    }

    /* Timer IRQs only publish a pending bit. Read the absolute timer clock so
     * coalesced work still expires deadlines at the correct logical time. */
    uint64_t current_tick = vos3_timer_get_ticks();
    if (current_tick <= g_monitor_stats.tick_count) {
        return;
    }
    g_monitor_stats.tick_count = current_tick;

    /* Phase 17.5.1: Integrity automation tick */
    vos3_ai_guard_integrity_tick(current_tick);

    /* Phase 17.5.4: Memory pressure tick */
    vos3_ai_guard_pressure_tick();

    /* Phase 4.2.5: AI-Timer expiry + heartbeat events */
    vos3_ai_timer_tick(current_tick);

    /* Phase 4.2.7: Instruction drift watchdog */
    vos3_ai_drift_watchdog_tick(current_tick);

    /* Phase 4.7: NMI streaming stall watchdog */
    vos3_nmi_watchdog_tick(current_tick);

    /* Periodic telemetry report */
    uint32_t interval = g_config.telemetry_interval;
    /* Deferred runs can skip the exact boundary (e.g. 999 -> 1001). Report
     * once when crossing a period, without replaying every missed interval.
     * Division preserves the original absolute period boundaries and avoids
     * an overflowing last_report_tick + interval deadline. */
    if (interval > 0U &&
        current_tick / interval > g_monitor_stats.last_report_tick / interval) {
        /* Only report if there's activity */
        if (g_monitor_stats.total_accesses > 0ULL ||
            g_monitor_stats.anomaly_count > 0ULL) {
            VOS3_INFO("[AI-MONITOR] Tick %llu: R=%llu W=%llu F=%llu A=%llu log=%u/%zu",
                      (unsigned long long)current_tick,
                      (unsigned long long)g_monitor_stats.total_reads,
                      (unsigned long long)g_monitor_stats.total_writes,
                      (unsigned long long)g_monitor_stats.total_faults,
                      (unsigned long long)g_monitor_stats.anomaly_count,
                      g_access_log.count,
                      VOS3_AI_ACCESS_LOG_SIZE);
        }
        g_monitor_stats.last_report_tick = current_tick;
    }
}

/**
 * @brief Get monitor statistics
 */
void vos3_ai_monitor_get_stats(uint64_t* accesses, uint64_t* reads,
                                uint64_t* writes, uint64_t* faults,
                                uint64_t* anomalies)
{
    if (accesses != NULL) *accesses = g_monitor_stats.total_accesses;
    if (reads != NULL) *reads = g_monitor_stats.total_reads;
    if (writes != NULL) *writes = g_monitor_stats.total_writes;
    if (faults != NULL) *faults = g_monitor_stats.total_faults;
    if (anomalies != NULL) *anomalies = g_monitor_stats.anomaly_count;
}

/**
 * @brief Check if monitor is initialized
 */
int vos3_ai_monitor_is_initialized(void)
{
    return g_ai_monitor_initialized;
}

/* ============================================================================
 * PHASE 17.4: ALERT SYSTEM
 * ============================================================================ */

/**
 * @brief Fire an alert (calls callback + logs + telemetry queue)
 * @param[in] alert Alert to fire
 */
void vos3_ai_monitor_fire_alert(const vos3_ai_alert_t* alert)
{
    if (alert == NULL) {
        return;
    }

    /* Call registered callback */
    if (g_alert_callback != NULL) {
        g_alert_callback(alert);
    }

    /* Queue to telemetry export (Phase 17.5.5) */
    vos3_ai_telemetry_queue_alert(alert);

    /* Log to console */
    static const char* type_names[] = {
        "ANOMALY", "QUOTA_EXCEEDED", "GUARD_FAULT", "INTEGRITY_FAIL"
    };
    static const char* sev_names[] = {
        "INFO", "WARN", "ERROR", "CRIT"
    };

    const char* type_str = (alert->type <= VOS3_AI_ALERT_INTEGRITY_FAIL) ?
                            type_names[alert->type] : "UNKNOWN";
    const char* sev_str = (alert->severity <= VOS3_AI_ALERT_CRITICAL) ?
                           sev_names[alert->severity] : "?";

    VOS3_WARN("[AI-ALERT] %s/%s: region=%u pid=%u val=%llu/%llu",
              type_str, sev_str,
              alert->region_id, alert->pid,
              (unsigned long long)alert->value1,
              (unsigned long long)alert->value2);
}

/**
 * @brief Register alert callback
 * @param[in] callback Function to call on alerts (NULL to disable)
 * @return 0 on success
 */
int vos3_ai_monitor_set_alert_callback(vos3_ai_alert_callback_t callback)
{
    g_alert_callback = callback;
    return 0;
}

/* ============================================================================
 * PHASE 17.4: CONFIGURATION
 * ============================================================================ */

/**
 * @brief Set anomaly detection threshold
 * @param[in] threshold Accesses before anomaly
 * @param[in] window Window in timer ticks
 * @return 0 on success
 */
int vos3_ai_monitor_set_threshold(uint64_t threshold, uint64_t window)
{
    if (threshold == 0ULL || window == 0ULL) {
        return -1;
    }

    monitor_lock();
    g_config.threshold = threshold;
    g_config.window = window;
    monitor_unlock();

    VOS3_INFO("[AI-MONITOR] Threshold updated: %llu/%llu ticks",
              (unsigned long long)threshold, (unsigned long long)window);
    return 0;
}

/**
 * @brief Set full monitor configuration
 * @param[in] config New configuration
 * @return 0 on success
 */
int vos3_ai_monitor_set_config(const vos3_ai_monitor_config_t* config)
{
    if (config == NULL) {
        return -1;
    }

    monitor_lock();
    g_config = *config;
    monitor_unlock();

    VOS3_INFO("[AI-MONITOR] Config updated: threshold=%llu window=%llu",
              (unsigned long long)g_config.threshold,
              (unsigned long long)g_config.window);
    return 0;
}

/**
 * @brief Get current monitor configuration
 * @param[out] config Output configuration
 * @return 0 on success
 */
int vos3_ai_monitor_get_config(vos3_ai_monitor_config_t* config)
{
    if (config == NULL) {
        return -1;
    }

    monitor_lock();
    *config = g_config;
    monitor_unlock();

    return 0;
}
