/**
 * @file console.c
 * @brief VOS3 Early Boot Console Implementation
 *
 * @details Minimal console for early boot debugging.
 *          Outputs to serial port (COM1) and/or VGA text mode.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/console.h"
#include "../../include/arch/x86_64/memory_map.h"
#ifdef NATIVE_SMP_WORKLOAD
#include "../../include/vos/atomic.h"
static vos3_spinlock_t g_record_lock = VOS3_SPINLOCK_INIT;
uint64_t vos3_console_record_begin(void)
{
    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_record_lock);
    return flags;
}
void vos3_console_record_end(uint64_t flags)
{
    vos3_spinlock_unlock(&g_record_lock);
    vos3_irq_restore(flags);
}
#endif

/* ============================================================================
 * PORT I/O HELPERS (x86_64)
 * ============================================================================ */

/**
 * @brief Read byte from I/O port
 */
static inline uint8_t port_inb(uint16_t port)
{
    uint8_t value;
    __asm__ volatile ("inb %1, %0" : "=a"(value) : "Nd"(port));
    return value;
}

/**
 * @brief Write byte to I/O port
 */
static inline void port_outb(uint16_t port, uint8_t value)
{
    __asm__ volatile ("outb %0, %1" :: "a"(value), "Nd"(port));
}

/* ============================================================================
 * CONSOLE STATE
 * ============================================================================ */

/** @brief Console state structure */
typedef struct vos3_console_state {
    /* Configuration */
    vos3_console_target_t targets;
    uint16_t serial_port;

    /* VGA state */
    volatile uint16_t* vga_buffer;
    uint16_t vga_col;
    uint16_t vga_row;
    uint8_t  vga_attr;

    /* Initialization flag */
    uint32_t initialized;
} vos3_console_state_t;

/** @brief Global console state */
static vos3_console_state_t g_console = {
    .targets = VOS3_CONSOLE_BOTH,
    .serial_port = VOS3_SERIAL_DEFAULT,
    .vga_buffer = NULL,
    .vga_col = 0U,
    .vga_row = 0U,
    .vga_attr = VOS3_VGA_DEFAULT_ATTR,
    .initialized = 0U
};

/* ============================================================================
 * SERIAL PORT
 * ============================================================================ */

/** @brief Serial port registers (offsets from base) */
#define SERIAL_DATA         0U      /**< Data register (R/W) */
#define SERIAL_IER          1U      /**< Interrupt enable */
#define SERIAL_FCR          2U      /**< FIFO control */
#define SERIAL_LCR          3U      /**< Line control */
#define SERIAL_MCR          4U      /**< Modem control */
#define SERIAL_LSR          5U      /**< Line status */
#define SERIAL_MSR          6U      /**< Modem status */

/** @brief Serial line status flags */
#define SERIAL_LSR_DR       (1U << 0)   /**< Data ready */
#define SERIAL_LSR_THRE     (1U << 5)   /**< Transmitter holding register empty */

/**
 * @brief Initialize serial port
 * @param[in] port Serial port base address
 * @param[in] baud Baud rate
 */
static void serial_init(uint16_t port, uint32_t baud)
{
    uint16_t divisor = (uint16_t)(115200U / baud);

    /* Disable interrupts */
    port_outb(port + SERIAL_IER, 0x00U);

    /* Set DLAB to access divisor */
    port_outb(port + SERIAL_LCR, 0x80U);

    /* Set divisor (low and high bytes) */
    port_outb(port + SERIAL_DATA, (uint8_t)(divisor & 0xFFU));
    port_outb(port + SERIAL_IER, (uint8_t)((divisor >> 8U) & 0xFFU));

    /* 8 bits, no parity, 1 stop bit */
    port_outb(port + SERIAL_LCR, 0x03U);

    /* Enable FIFO, clear buffers, 14-byte threshold */
    port_outb(port + SERIAL_FCR, 0xC7U);

    /* RTS/DSR set, IRQs enabled */
    port_outb(port + SERIAL_MCR, 0x0BU);
}

/**
 * @brief Check if serial transmitter is ready
 */
static inline int serial_is_transmit_ready(uint16_t port)
{
    return (port_inb(port + SERIAL_LSR) & SERIAL_LSR_THRE) != 0U;
}

/**
 * @brief Write character to serial port
 */
static void serial_putc(uint16_t port, char c)
{
    /* Wait for transmitter to be ready */
    while (!serial_is_transmit_ready(port)) {
        __asm__ volatile ("pause" ::: "memory");
    }

    port_outb(port + SERIAL_DATA, (uint8_t)c);

    /* Send CR after LF for proper line endings */
    if (c == '\n') {
        while (!serial_is_transmit_ready(port)) {
            __asm__ volatile ("pause" ::: "memory");
        }
        port_outb(port + SERIAL_DATA, '\r');
    }
}

/* ============================================================================
 * VGA TEXT MODE
 * ============================================================================ */

/**
 * @brief Create VGA character entry
 */
static inline uint16_t vga_entry(char c, uint8_t attr)
{
    return (uint16_t)(((uint16_t)attr << 8U) | (uint16_t)(uint8_t)c);
}

/**
 * @brief Scroll VGA screen up one line
 */
static void vga_scroll(void)
{
    volatile uint16_t* buffer = g_console.vga_buffer;
    if (buffer == NULL) {
        return;
    }

    /* Move all lines up by one */
    for (uint16_t row = 1U; row < VOS3_VGA_HEIGHT; row++) {
        for (uint16_t col = 0U; col < VOS3_VGA_WIDTH; col++) {
            size_t dst = (size_t)((row - 1U) * VOS3_VGA_WIDTH + col);
            size_t src = (size_t)(row * VOS3_VGA_WIDTH + col);
            buffer[dst] = buffer[src];
        }
    }

    /* Clear last line */
    uint16_t blank = vga_entry(' ', g_console.vga_attr);
    for (uint16_t col = 0U; col < VOS3_VGA_WIDTH; col++) {
        size_t idx = (size_t)((VOS3_VGA_HEIGHT - 1U) * VOS3_VGA_WIDTH + col);
        buffer[idx] = blank;
    }
}

/**
 * @brief Write character to VGA buffer
 */
static void vga_putc(char c)
{
    volatile uint16_t* buffer = g_console.vga_buffer;
    if (buffer == NULL) {
        return;
    }

    if (c == '\n') {
        /* Newline */
        g_console.vga_col = 0U;
        g_console.vga_row++;
    } else if (c == '\r') {
        /* Carriage return */
        g_console.vga_col = 0U;
    } else if (c == '\t') {
        /* Tab (8-space aligned) */
        g_console.vga_col = (g_console.vga_col + 8U) & ~7U;
        if (g_console.vga_col >= VOS3_VGA_WIDTH) {
            g_console.vga_col = 0U;
            g_console.vga_row++;
        }
    } else if (c == '\b') {
        /* Backspace */
        if (g_console.vga_col > 0U) {
            g_console.vga_col--;
            size_t idx = (size_t)(g_console.vga_row * VOS3_VGA_WIDTH + g_console.vga_col);
            buffer[idx] = vga_entry(' ', g_console.vga_attr);
        }
    } else if (c >= ' ') {
        /* Printable character */
        size_t idx = (size_t)(g_console.vga_row * VOS3_VGA_WIDTH + g_console.vga_col);
        buffer[idx] = vga_entry(c, g_console.vga_attr);
        g_console.vga_col++;

        if (g_console.vga_col >= VOS3_VGA_WIDTH) {
            g_console.vga_col = 0U;
            g_console.vga_row++;
        }
    }

    /* Handle scrolling */
    if (g_console.vga_row >= VOS3_VGA_HEIGHT) {
        vga_scroll();
        g_console.vga_row = VOS3_VGA_HEIGHT - 1U;
    }
}

/**
 * @brief Update VGA hardware cursor
 */
static void vga_update_cursor(void)
{
    uint16_t pos = g_console.vga_row * VOS3_VGA_WIDTH + g_console.vga_col;

    /* CRT controller ports */
    port_outb(0x3D4U, 0x0FU);
    port_outb(0x3D5U, (uint8_t)(pos & 0xFFU));
    port_outb(0x3D4U, 0x0EU);
    port_outb(0x3D5U, (uint8_t)((pos >> 8U) & 0xFFU));
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

int vos3_console_init(vos3_console_target_t targets, uint16_t serial_port)
{
    g_console.targets = targets;
    g_console.serial_port = (serial_port != 0U) ? serial_port : VOS3_SERIAL_DEFAULT;

    /* Initialize serial port */
    if ((targets & VOS3_CONSOLE_SERIAL) != 0U) {
        serial_init(g_console.serial_port, VOS3_SERIAL_BAUD_DEFAULT);
    }

    /* Initialize VGA */
    if ((targets & VOS3_CONSOLE_VGA) != 0U) {
        /* Map VGA buffer through direct mapping */
        g_console.vga_buffer = (volatile uint16_t*)vos3_phys_to_virt(VOS3_VGA_TEXT_BUFFER);
        g_console.vga_col = 0U;
        g_console.vga_row = 0U;
        g_console.vga_attr = VOS3_VGA_DEFAULT_ATTR;
    }

    g_console.initialized = 1U;

    return 0;
}

void vos3_console_putc(char c)
{
    if ((g_console.targets & VOS3_CONSOLE_SERIAL) != 0U) {
        serial_putc(g_console.serial_port, c);
    }

    if ((g_console.targets & VOS3_CONSOLE_VGA) != 0U) {
        vga_putc(c);
    }
}

void vos3_console_puts(const char* str)
{
    if (str == NULL) {
        return;
    }

    while (*str != '\0') {
        vos3_console_putc(*str);
        str++;
    }
}

void vos3_console_write(const char* str, size_t len)
{
    if (str == NULL) {
        return;
    }

    for (size_t i = 0U; i < len; i++) {
        vos3_console_putc(str[i]);
    }
}

void vos3_console_clear(void)
{
    if (g_console.vga_buffer == NULL) {
        return;
    }

    uint16_t blank = vga_entry(' ', g_console.vga_attr);

    for (size_t i = 0U; i < (VOS3_VGA_WIDTH * VOS3_VGA_HEIGHT); i++) {
        g_console.vga_buffer[i] = blank;
    }

    g_console.vga_col = 0U;
    g_console.vga_row = 0U;
    vga_update_cursor();
}

void vos3_console_set_color(uint8_t attr)
{
    g_console.vga_attr = attr;
}

void vos3_console_get_cursor(uint16_t* col, uint16_t* row)
{
    if (col != NULL) {
        *col = g_console.vga_col;
    }
    if (row != NULL) {
        *row = g_console.vga_row;
    }
}

void vos3_console_set_cursor(uint16_t col, uint16_t row)
{
    if (col < VOS3_VGA_WIDTH) {
        g_console.vga_col = col;
    }
    if (row < VOS3_VGA_HEIGHT) {
        g_console.vga_row = row;
    }
    vga_update_cursor();
}

/* ============================================================================
 * NUMBER PRINTING
 * ============================================================================ */

void vos3_console_puthex(uint64_t value, uint8_t width)
{
    static const char hex_chars[] = "0123456789ABCDEF";
    char buffer[17];  /* Max 16 hex digits + null */
    int pos = 16;

    buffer[pos] = '\0';

    /* Convert to hex (reverse order) */
    if (value == 0ULL) {
        pos--;
        buffer[pos] = '0';
    } else {
        while (value != 0ULL && pos > 0) {
            pos--;
            buffer[pos] = hex_chars[value & 0xFULL];
            value >>= 4U;
        }
    }

    /* Pad with zeros if needed */
    while ((16 - pos) < width && pos > 0) {
        pos--;
        buffer[pos] = '0';
    }

    vos3_console_puts(&buffer[pos]);
}

void vos3_console_putdec(int64_t value)
{
    char buffer[21];  /* Max 19 digits + sign + null */
    int pos = 20;
    int negative = 0;
    uint64_t abs_val;

    buffer[pos] = '\0';

    if (value < 0) {
        negative = 1;
        abs_val = (uint64_t)(-(value + 1)) + 1ULL;  /* Handle INT64_MIN */
    } else {
        abs_val = (uint64_t)value;
    }

    /* Convert to decimal (reverse order) */
    if (abs_val == 0ULL) {
        pos--;
        buffer[pos] = '0';
    } else {
        while (abs_val != 0ULL && pos > 0) {
            pos--;
            buffer[pos] = (char)('0' + (abs_val % 10ULL));
            abs_val /= 10ULL;
        }
    }

    /* Add sign */
    if (negative && pos > 0) {
        pos--;
        buffer[pos] = '-';
    }

    vos3_console_puts(&buffer[pos]);
}

/* ============================================================================
 * PRINTF IMPLEMENTATION (Minimal)
 * ============================================================================ */

int vos3_console_vprintf(const char* fmt, va_list args)
{
    if (fmt == NULL) {
        return 0;
    }

    int count = 0;
    char c;

    while ((c = *fmt++) != '\0') {
        if (c != '%') {
            vos3_console_putc(c);
            count++;
            continue;
        }

        /* Parse width */
        uint8_t width = 0U;
        int zero_pad = 0;

        c = *fmt++;
        if (c == '0') {
            zero_pad = 1;
            c = *fmt++;
        }
        (void)zero_pad;

        while (c >= '0' && c <= '9') {
            width = (uint8_t)(width * 10U + (uint8_t)(c - '0'));
            c = *fmt++;
        }

        /* Handle length modifiers — vOS·Adaptive·Phase=P3.1-polish.
         *
         * 'l'  → long
         * 'll' → long long
         * 'z'  → size_t  (x86_64-elf ABI: unsigned long, 64-bit)
         *
         * Pre-2026-05-17 bug: 'z' was unhandled, causing the default
         * switch arm to emit the literal "%z" then fall through to
         * the next switch iteration with the type char ('u'/'x'/'d')
         * also being printed verbatim. All %zu prints across the
         * kernel (~30 call sites in net/, mm/ debug paths) were
         * silently broken. Fixing here closes them all in one place. */
        int is_long = 0;
        int is_longlong = 0;

        if (c == 'l') {
            is_long = 1;
            c = *fmt++;
            if (c == 'l') {
                is_longlong = 1;
                c = *fmt++;
            }
        } else if (c == 'z') {
            /* size_t — 64-bit on x86_64. Treat as long. */
            is_long = 1;
            c = *fmt++;
        }

        /* Format specifier */
        switch (c) {
            case 's': {
                const char* s = va_arg(args, const char*);
                if (s == NULL) {
                    s = "(null)";
                }
                vos3_console_puts(s);
                break;
            }

            case 'c': {
                char ch = (char)va_arg(args, int);
                vos3_console_putc(ch);
                count++;
                break;
            }

            case 'd':
            case 'i': {
                int64_t val;
                if (is_longlong) {
                    val = va_arg(args, int64_t);
                } else if (is_long) {
                    val = va_arg(args, long);
                } else {
                    val = va_arg(args, int);
                }
                vos3_console_putdec(val);
                break;
            }

            case 'u': {
                uint64_t val;
                if (is_longlong) {
                    val = va_arg(args, uint64_t);
                } else if (is_long) {
                    val = va_arg(args, unsigned long);
                } else {
                    val = va_arg(args, unsigned int);
                }
                vos3_console_putdec((int64_t)val);
                break;
            }

            case 'x':
            case 'X': {
                uint64_t val;
                if (is_longlong) {
                    val = va_arg(args, uint64_t);
                } else if (is_long) {
                    val = va_arg(args, unsigned long);
                } else {
                    val = va_arg(args, unsigned int);
                }
                vos3_console_puthex(val, width);
                break;
            }

            case 'p': {
                void* ptr = va_arg(args, void*);
                vos3_console_puts("0x");
                vos3_console_puthex((uint64_t)(uintptr_t)ptr, 16U);
                break;
            }

            case '%':
                vos3_console_putc('%');
                count++;
                break;

            default:
                /* Unknown format, print as-is */
                vos3_console_putc('%');
                vos3_console_putc(c);
                count += 2;
                break;
        }
    }

    /* Update cursor after printing */
    if ((g_console.targets & VOS3_CONSOLE_VGA) != 0U) {
        vga_update_cursor();
    }

    return count;
}

int vos3_console_printf(const char* fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    int result = vos3_console_vprintf(fmt, args);
    va_end(args);
    return result;
}

/* ============================================================================
 * LOGGING
 * ============================================================================ */

/** @brief Log level prefixes */
static const char* const log_prefixes[] = {
    "[DEBUG] ",
    "[INFO]  ",
    "[WARN]  ",
    "[ERROR] ",
    "[PANIC] "
};

/** @brief Log level colors */
static const uint8_t log_colors[] = {
    VOS3_VGA_DEFAULT_ATTR,      /* DEBUG - gray */
    VOS3_VGA_INFO_ATTR,         /* INFO  - cyan */
    VOS3_VGA_WARN_ATTR,         /* WARN  - yellow */
    VOS3_VGA_ERROR_ATTR,        /* ERROR - red */
    VOS3_VGA_ERROR_ATTR         /* PANIC - red */
};

/* ============================================================================
 * KERNEL LOG RING BUFFER
 * ============================================================================ */

/** @brief Single ring buffer entry */
typedef struct {
    char     msg[VOS3_KLOG_MSG_LEN];
    uint8_t  level;
    uint8_t  _pad[7];
} klog_entry_t;

/** @brief Ring buffer state (static BSS — no heap needed) */
static klog_entry_t g_klog_entries[VOS3_KLOG_CAPACITY];
static volatile uint64_t g_klog_write_pos = 0ULL;
static volatile uint64_t g_klog_dropped   = 0ULL;
static volatile int      g_klog_active    = 0;

void vos3_klog_init(void)
{
    g_klog_write_pos = 0ULL;
    g_klog_dropped   = 0ULL;
    g_klog_active    = 1;
}

void vos3_klog_stats(uint64_t* total_written, uint64_t* total_dropped)
{
    if (total_written != NULL) *total_written = g_klog_write_pos;
    if (total_dropped != NULL) *total_dropped = g_klog_dropped;
}

/** @brief Format message into ring buffer entry (truncates if too long) */
static void klog_store(vos3_log_level_t level, const char* fmt, va_list args)
{
    uint64_t pos = __atomic_fetch_add(&g_klog_write_pos, 1ULL, __ATOMIC_RELEASE);
    uint64_t idx = pos & (uint64_t)(VOS3_KLOG_CAPACITY - 1U);
    klog_entry_t* e = &g_klog_entries[idx];

    e->level = (uint8_t)level;

    /* Format prefix + message into entry buffer */
    const char* prefix = log_prefixes[level];
    int pi = 0;
    while (prefix[pi] != '\0' && pi < (int)(VOS3_KLOG_MSG_LEN - 2)) {
        e->msg[pi] = prefix[pi];
        pi++;
    }

    /* Simple vsnprintf into remaining space */
    int remaining = (int)VOS3_KLOG_MSG_LEN - pi - 1;
    if (remaining > 0) {
        /* Use console_vprintf equivalent — but we can't easily snprintf
         * in the kernel. Instead, store the formatted output directly. */
        va_list args_copy;
        va_copy(args_copy, args);

        /* Write the format string character by character, expanding %d/%s/%x */
        int wi = pi;
        const char* f = fmt;
        while (*f != '\0' && wi < (int)(VOS3_KLOG_MSG_LEN - 1)) {
            if (*f == '%' && *(f+1) != '\0') {
                f++;
                if (*f == 's') {
                    const char* s = va_arg(args_copy, const char*);
                    if (s == NULL) s = "(null)";
                    while (*s != '\0' && wi < (int)(VOS3_KLOG_MSG_LEN - 1))
                        e->msg[wi++] = *s++;
                } else if (*f == 'd' || *f == 'i') {
                    long val = va_arg(args_copy, int);
                    char numbuf[24];
                    int ni = 0;
                    if (val < 0) { e->msg[wi++] = '-'; val = -val; }
                    if (val == 0) { numbuf[ni++] = '0'; }
                    else { while (val > 0) { numbuf[ni++] = '0' + (char)(val % 10); val /= 10; } }
                    while (ni > 0 && wi < (int)(VOS3_KLOG_MSG_LEN - 1))
                        e->msg[wi++] = numbuf[--ni];
                } else if (*f == 'u') {
                    unsigned long val = va_arg(args_copy, unsigned int);
                    char numbuf[24];
                    int ni = 0;
                    if (val == 0) { numbuf[ni++] = '0'; }
                    else { while (val > 0) { numbuf[ni++] = '0' + (char)(val % 10); val /= 10; } }
                    while (ni > 0 && wi < (int)(VOS3_KLOG_MSG_LEN - 1))
                        e->msg[wi++] = numbuf[--ni];
                } else if (*f == 'x' || *f == 'X' || *f == 'p') {
                    unsigned long val = va_arg(args_copy, unsigned long);
                    char hexbuf[20];
                    int hi = 0;
                    const char* hc = (*f == 'X') ? "0123456789ABCDEF" : "0123456789abcdef";
                    if (val == 0) { hexbuf[hi++] = '0'; }
                    else { while (val > 0) { hexbuf[hi++] = hc[val & 0xF]; val >>= 4; } }
                    while (hi > 0 && wi < (int)(VOS3_KLOG_MSG_LEN - 1))
                        e->msg[wi++] = hexbuf[--hi];
                } else if (*f == 'l') {
                    /* Handle %ld, %lu, %lx, %lld, %llu, %llx */
                    f++;
                    if (*f == 'l') { f++; } /* skip second 'l' */
                    if (*f == 'd' || *f == 'i') {
                        long long val = va_arg(args_copy, long long);
                        char numbuf[24]; int ni = 0;
                        if (val < 0) { e->msg[wi++] = '-'; val = -val; }
                        if (val == 0) { numbuf[ni++] = '0'; }
                        else { while (val > 0) { numbuf[ni++] = '0' + (char)(val % 10); val /= 10; } }
                        while (ni > 0 && wi < (int)(VOS3_KLOG_MSG_LEN - 1))
                            e->msg[wi++] = numbuf[--ni];
                    } else if (*f == 'u') {
                        unsigned long long val = va_arg(args_copy, unsigned long long);
                        char numbuf[24]; int ni = 0;
                        if (val == 0) { numbuf[ni++] = '0'; }
                        else { while (val > 0) { numbuf[ni++] = '0' + (char)(val % 10); val /= 10; } }
                        while (ni > 0 && wi < (int)(VOS3_KLOG_MSG_LEN - 1))
                            e->msg[wi++] = numbuf[--ni];
                    } else if (*f == 'x' || *f == 'X') {
                        unsigned long long val = va_arg(args_copy, unsigned long long);
                        char hexbuf[20]; int hi = 0;
                        const char* hc = "0123456789abcdef";
                        if (val == 0) { hexbuf[hi++] = '0'; }
                        else { while (val > 0) { hexbuf[hi++] = hc[val & 0xF]; val >>= 4; } }
                        while (hi > 0 && wi < (int)(VOS3_KLOG_MSG_LEN - 1))
                            e->msg[wi++] = hexbuf[--hi];
                    }
                } else if (*f == '%') {
                    e->msg[wi++] = '%';
                } else if (*f == 'c') {
                    e->msg[wi++] = (char)va_arg(args_copy, int);
                } else {
                    /* Unknown format: skip */
                    (void)va_arg(args_copy, int);
                }
            } else {
                e->msg[wi++] = *f;
            }
            f++;
        }
        e->msg[wi] = '\0';
        va_end(args_copy);
    } else {
        e->msg[pi] = '\0';
    }
}

void vos3_log(vos3_log_level_t level, const char* fmt, ...)
{
#ifdef NATIVE_SMP_WORKLOAD
    uint64_t record_flags = vos3_console_record_begin();
#endif
    if (level > VOS3_LOG_PANIC) {
        level = VOS3_LOG_PANIC;
    }

    /* Store in ring buffer (if active) */
    if (g_klog_active) {
        va_list ring_args;
        va_start(ring_args, fmt);
        klog_store(level, fmt, ring_args);
        va_end(ring_args);
    }

    /* Only output to serial/VGA if level meets the serial threshold */
    if ((unsigned int)level < (unsigned int)VOS3_SERIAL_MIN_LEVEL) {
#ifdef NATIVE_SMP_WORKLOAD
        vos3_console_record_end(record_flags);
#endif
        return;  /* Silenced — stored in ring buffer only */
    }

    /* Save current color */
    uint8_t saved_attr = g_console.vga_attr;

    /* Set log color */
    g_console.vga_attr = log_colors[level];

    /* Print prefix */
    vos3_console_puts(log_prefixes[level]);

    /* Print message */
    va_list args;
    va_start(args, fmt);
    vos3_console_vprintf(fmt, args);
    va_end(args);

    /* Newline */
    vos3_console_putc('\n');

    /* Restore color */
    g_console.vga_attr = saved_attr;
#ifdef NATIVE_SMP_WORKLOAD
    vos3_console_record_end(record_flags);
#endif
}

/* ============================================================================
 * HEX DUMP
 * ============================================================================ */

void vos3_console_hexdump(const void* addr, size_t size)
{
    const uint8_t* ptr = (const uint8_t*)addr;
    size_t offset = 0U;

    while (offset < size) {
        /* Print address */
        vos3_console_puthex((uint64_t)(uintptr_t)(ptr + offset), 16U);
        vos3_console_puts(": ");

        /* Print hex bytes */
        for (size_t i = 0U; i < 16U; i++) {
            if (offset + i < size) {
                vos3_console_puthex(ptr[offset + i], 2U);
                vos3_console_putc(' ');
            } else {
                vos3_console_puts("   ");
            }

            if (i == 7U) {
                vos3_console_putc(' ');
            }
        }

        vos3_console_puts(" |");

        /* Print ASCII representation */
        for (size_t i = 0U; i < 16U && (offset + i) < size; i++) {
            char c = (char)ptr[offset + i];
            if (c >= ' ' && c <= '~') {
                vos3_console_putc(c);
            } else {
                vos3_console_putc('.');
            }
        }

        vos3_console_puts("|\n");
        offset += 16U;
    }
}

/* ============================================================================
 * PANIC
 * ============================================================================ */

__attribute__((noreturn))
void vos3_panic(const char* fmt, ...)
{
    /* Disable interrupts */
    __asm__ volatile ("cli");

    /* Set panic colors */
    g_console.vga_attr = VOS3_VGA_ERROR_ATTR;

    /* Print panic header */
    vos3_console_puts("\n");
    vos3_console_puts("================================================================================\n");
    vos3_console_puts("                           *** KERNEL PANIC ***\n");
    vos3_console_puts("================================================================================\n\n");

    /* Print message */
    va_list args;
    va_start(args, fmt);
    vos3_console_vprintf(fmt, args);
    va_end(args);

    vos3_console_puts("\n\n");
    vos3_console_puts("System halted. Please reboot.\n");

    /* Halt forever */
    for (;;) {
        __asm__ volatile ("hlt");
    }
}
