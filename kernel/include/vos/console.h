/**
 * @file console.h
 * @brief VOS3 Early Boot Console for Debugging
 *
 * @details Provides minimal console output during early boot,
 *          before the full kernel services are available.
 *          Supports both serial (COM1) and VGA text mode output.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_CONSOLE_H
#define VOS3_CONSOLE_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>

/* ============================================================================
 * CONSOLE CONFIGURATION
 * ============================================================================ */

/** @brief Serial port COM1 base address */
#define VOS3_SERIAL_COM1            ((uint16_t)0x3F8U)

/** @brief Serial port COM2 base address */
#define VOS3_SERIAL_COM2            ((uint16_t)0x2F8U)

/** @brief Default serial port */
#define VOS3_SERIAL_DEFAULT         VOS3_SERIAL_COM1

/** @brief Default serial baud rate */
#define VOS3_SERIAL_BAUD_DEFAULT    ((uint32_t)115200U)

/** @brief VGA text mode buffer physical address */
#define VOS3_VGA_TEXT_BUFFER        ((uintptr_t)0xB8000U)

/** @brief VGA text mode width */
#define VOS3_VGA_WIDTH              ((uint16_t)80U)

/** @brief VGA text mode height */
#define VOS3_VGA_HEIGHT             ((uint16_t)25U)

/* ============================================================================
 * VGA COLOR CODES
 * ============================================================================ */

/** @brief VGA text mode colors */
typedef enum vos3_vga_color {
    VOS3_VGA_BLACK          = 0U,
    VOS3_VGA_BLUE           = 1U,
    VOS3_VGA_GREEN          = 2U,
    VOS3_VGA_CYAN           = 3U,
    VOS3_VGA_RED            = 4U,
    VOS3_VGA_MAGENTA        = 5U,
    VOS3_VGA_BROWN          = 6U,
    VOS3_VGA_LIGHT_GRAY     = 7U,
    VOS3_VGA_DARK_GRAY      = 8U,
    VOS3_VGA_LIGHT_BLUE     = 9U,
    VOS3_VGA_LIGHT_GREEN    = 10U,
    VOS3_VGA_LIGHT_CYAN     = 11U,
    VOS3_VGA_LIGHT_RED      = 12U,
    VOS3_VGA_LIGHT_MAGENTA  = 13U,
    VOS3_VGA_YELLOW         = 14U,
    VOS3_VGA_WHITE          = 15U
} vos3_vga_color_t;

/** @brief Create VGA color attribute */
#define VOS3_VGA_ATTR(fg, bg)  ((uint8_t)(((bg) << 4U) | (fg)))

/** @brief Default VGA color (white on black) */
#define VOS3_VGA_DEFAULT_ATTR  VOS3_VGA_ATTR(VOS3_VGA_LIGHT_GRAY, VOS3_VGA_BLACK)

/** @brief Error VGA color (red on black) */
#define VOS3_VGA_ERROR_ATTR    VOS3_VGA_ATTR(VOS3_VGA_LIGHT_RED, VOS3_VGA_BLACK)

/** @brief Warning VGA color (yellow on black) */
#define VOS3_VGA_WARN_ATTR     VOS3_VGA_ATTR(VOS3_VGA_YELLOW, VOS3_VGA_BLACK)

/** @brief Info VGA color (cyan on black) */
#define VOS3_VGA_INFO_ATTR     VOS3_VGA_ATTR(VOS3_VGA_LIGHT_CYAN, VOS3_VGA_BLACK)

/** @brief Success VGA color (green on black) */
#define VOS3_VGA_OK_ATTR       VOS3_VGA_ATTR(VOS3_VGA_LIGHT_GREEN, VOS3_VGA_BLACK)

/* ============================================================================
 * CONSOLE OUTPUT FLAGS
 * ============================================================================ */

/** @brief Console output targets */
typedef enum vos3_console_target {
    VOS3_CONSOLE_SERIAL     = (1U << 0),    /**< Output to serial port */
    VOS3_CONSOLE_VGA        = (1U << 1),    /**< Output to VGA text mode */
    VOS3_CONSOLE_BOTH       = (VOS3_CONSOLE_SERIAL | VOS3_CONSOLE_VGA)
} vos3_console_target_t;

/* ============================================================================
 * LOG LEVELS
 * ============================================================================ */

/** @brief Log levels */
typedef enum vos3_log_level {
    VOS3_LOG_DEBUG   = 0U,
    VOS3_LOG_INFO    = 1U,
    VOS3_LOG_WARN    = 2U,
    VOS3_LOG_ERROR   = 3U,
    VOS3_LOG_PANIC   = 4U
} vos3_log_level_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the early console
 * @param[in] targets Output target flags (VOS3_CONSOLE_*)
 * @param[in] serial_port Serial port base address (0 for default)
 * @return 0 on success, negative error code on failure
 */
int vos3_console_init(vos3_console_target_t targets, uint16_t serial_port);

/**
 * @brief Write a single character to the console
 * @param[in] c Character to write
 */
void vos3_console_putc(char c);

/**
 * @brief Write a string to the console
 * @param[in] str Null-terminated string
 */
void vos3_console_puts(const char* str);

/**
 * @brief Write a string with length to the console
 * @param[in] str String buffer
 * @param[in] len String length
 */
void vos3_console_write(const char* str, size_t len);

/**
 * @brief Formatted console output (printf-like)
 * @param[in] fmt Format string
 * @param[in] ... Format arguments
 * @return Number of characters written
 */
int vos3_console_printf(const char* fmt, ...);

/**
 * @brief Formatted console output with va_list
 * @param[in] fmt Format string
 * @param[in] args Variable argument list
 * @return Number of characters written
 */
int vos3_console_vprintf(const char* fmt, va_list args);

/**
 * @brief Print a log message with level
 * @param[in] level Log level
 * @param[in] fmt Format string
 * @param[in] ... Format arguments
 */
void vos3_log(vos3_log_level_t level, const char* fmt, ...);

/**
 * @brief Clear the console screen (VGA only)
 */
void vos3_console_clear(void);

/**
 * @brief Set VGA text color
 * @param[in] attr VGA color attribute
 */
void vos3_console_set_color(uint8_t attr);

/**
 * @brief Get VGA cursor position
 * @param[out] col Column (0-79)
 * @param[out] row Row (0-24)
 */
void vos3_console_get_cursor(uint16_t* col, uint16_t* row);

/**
 * @brief Set VGA cursor position
 * @param[in] col Column (0-79)
 * @param[in] row Row (0-24)
 */
void vos3_console_set_cursor(uint16_t col, uint16_t row);

/**
 * @brief Print a hexadecimal value
 * @param[in] value Value to print
 * @param[in] width Minimum width (0 for default)
 */
void vos3_console_puthex(uint64_t value, uint8_t width);

/**
 * @brief Print a decimal value
 * @param[in] value Value to print
 */
void vos3_console_putdec(int64_t value);

/**
 * @brief Print a memory dump
 * @param[in] addr Start address
 * @param[in] size Size in bytes
 */
void vos3_console_hexdump(const void* addr, size_t size);

/**
 * @brief Kernel panic - print message and halt
 * @param[in] fmt Format string
 * @param[in] ... Format arguments
 * @note This function does not return
 */
__attribute__((noreturn))
void vos3_panic(const char* fmt, ...);

/* ============================================================================
 * CONVENIENCE MACROS
 * ============================================================================ */

/** @brief Compile-time minimum log level (default: DEBUG=0, set to 1 to suppress DEBUG) */
#ifndef VOS3_LOG_MIN_LEVEL
#define VOS3_LOG_MIN_LEVEL  VOS3_LOG_DEBUG
#endif

/** @brief Compile-time minimum serial log level for ring buffer mode.
 *  When VOS3_KLOG_RING is defined, only messages >= this level go to serial.
 *  All messages still go to the in-memory ring buffer regardless. */
#ifndef VOS3_SERIAL_MIN_LEVEL
#define VOS3_SERIAL_MIN_LEVEL  VOS3_LOG_DEBUG
#endif

/** @brief Debug log (compiled out when VOS3_LOG_MIN_LEVEL > VOS3_LOG_DEBUG) */
#if VOS3_LOG_MIN_LEVEL <= VOS3_LOG_DEBUG
#define VOS3_DEBUG(fmt, ...)  vos3_log(VOS3_LOG_DEBUG, fmt, ##__VA_ARGS__)
#else
#define VOS3_DEBUG(fmt, ...)  do { (void)0; } while (0)
#endif

/** @brief Info log */
#define VOS3_INFO(fmt, ...)   vos3_log(VOS3_LOG_INFO, fmt, ##__VA_ARGS__)

/** @brief Warning log */
#define VOS3_WARN(fmt, ...)   vos3_log(VOS3_LOG_WARN, fmt, ##__VA_ARGS__)

/** @brief Error log */
#define VOS3_ERROR(fmt, ...)  vos3_log(VOS3_LOG_ERROR, fmt, ##__VA_ARGS__)

/** @brief Panic (does not return) */
#define VOS3_PANIC(fmt, ...)  vos3_panic(fmt, ##__VA_ARGS__)

/** @brief Assert with message */
#define VOS3_ASSERT(cond, msg)  \
    do { \
        if (!(cond)) { \
            vos3_panic("ASSERT FAILED: %s\n  at %s:%d\n  %s", \
                       #cond, __FILE__, __LINE__, msg); \
        } \
    } while (0)

/** @brief Simple assert */
#define VOS3_ASSERT_MSG(cond)  VOS3_ASSERT(cond, "")

/* ============================================================================
 * KERNEL RING BUFFER (klog)
 * ============================================================================ */

/** @brief Ring buffer capacity (must be power of 2) */
#define VOS3_KLOG_CAPACITY  4096U

/** @brief Max message length per entry */
#define VOS3_KLOG_MSG_LEN   128U

/**
 * @brief Initialize the kernel log ring buffer.
 *        After init, vos3_log() writes to ring buffer first.
 *        Serial output is gated by VOS3_SERIAL_MIN_LEVEL.
 */
void vos3_klog_init(void);

/**
 * @brief Get ring buffer statistics.
 * @param[out] total_written  Total entries written (may wrap)
 * @param[out] total_dropped  Entries dropped due to full buffer
 */
void vos3_klog_stats(uint64_t* total_written, uint64_t* total_dropped);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_CONSOLE_H */
