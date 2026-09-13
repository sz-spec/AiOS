/**
 * @file vbus_transport.c
 * @brief VBus bridge transport layer — framing, response helpers, utilities.
 *
 * Extracted from virtio_bridge.c during the bridge split refactor.
 * Contains: UART I/O, response helpers (send_ok, send_err),
 * hex encode/decode, string helpers, congestion control, globals.
 */

#include "vbus_bridge_internal.h"
#include "../../include/vos/sha256.h"
#include "../../include/vos/vbus.h"  /* [OLYMPUS-FIX A-15] PRO reject codes */

/* ---- Per-CMD HMAC Tracking (Batch F: Instruction Integrity Tags) ---- */

/* [OLYMPUS-FIX P-13] All three HMAC-tracking counters share a single
 * 64-byte cache line. record_hmac_violation() (the hot path on
 * misbehaving clients) increments g_bad_hmac_count AND mutates
 * g_hmac_ban_until in lock-step; concurrent callers from different
 * slots ping-ponged the line on every violation. Per-variable
 * alignment forces each onto its own line.
 *
 * Cache-line padding is hand-rolled because vbus_transport.c does
 * not pull arch/x86_64/memory_map.h transitively; the value (64) is
 * the same VOS3_CACHE_LINE_SIZE used throughout the kernel. */
static uint32_t g_bad_hmac_count        __attribute__((aligned(64)));
static uint64_t g_bad_hmac_window_start __attribute__((aligned(64)));
static uint64_t g_hmac_ban_until        __attribute__((aligned(64)));

/**
 * @brief Check if HMAC ban is active
 * @return 1 if OK (not banned), 0 if banned -- reject
 */
int vos3_check_hmac_ban(void)
{
    uint64_t now = vos3_sched_get_ticks();
    if (g_hmac_ban_until > 0 && now < g_hmac_ban_until) {
        return 0;  /* Banned -- reject */
    }
    g_hmac_ban_until = 0;  /* Ban expired */
    return 1;
}

/**
 * @brief Record an HMAC violation and trigger ban if threshold reached
 */
static void record_hmac_violation(void)
{
    uint64_t now = vos3_sched_get_ticks();
    if (now - g_bad_hmac_window_start > 6000U) {
        g_bad_hmac_count = 0;
        g_bad_hmac_window_start = now;
    }
    g_bad_hmac_count++;
    if (g_bad_hmac_count >= 10) {
        g_hmac_ban_until = now + 6000U;
        VOS3_WARN("[VBus] HMAC ban: 10 violations in 60s -- rejecting all frames for 60s");
    }
}

/**
 * @brief Get cumulative HMAC violation count.
 * v23.14 (D-CRIT2): Exposed via HMAC_STATS bridge command for monitoring.
 */
uint32_t vos3_vbus_get_bad_hmac_count(void)
{
    return g_bad_hmac_count;
}

/**
 * @brief Check if HMAC ban is currently active.
 */
int vos3_vbus_hmac_ban_active(void)
{
    uint64_t now = vos3_sched_get_ticks();
    return (g_hmac_ban_until > 0 && now < g_hmac_ban_until) ? 1 : 0;
}

/**
 * @brief Verify per-command payload HMAC trailer (A3-1 three-valued).
 * @return VOS3_HMAC_VALID      trailer present and MAC authentic
 *         VOS3_HMAC_INVALID    trailer present but MAC failed (tamper/forgery)
 *         VOS3_HMAC_NO_TRAILER no 32-byte trailer / no session key — the caller
 *                              MUST fail-closed on this when HMAC is required
 *                              (the former binary return conflated this with
 *                              VALID, the A3-1 HMAC-strip path).
 */
int vos3_verify_cmd_hmac(uint8_t type, uint8_t slot_id, uint16_t tag,
                         const uint8_t *payload, uint32_t payload_len,
                         const uint8_t *session_key, uint32_t key_len)
{
    if (payload_len < 32 || key_len == 0) {
        /* No trailer or no key — NOT authenticated. Distinct from VALID so the
         * call site can refuse a stripped frame instead of passing it through. */
        return VOS3_HMAC_NO_TRAILER;
    }

    /* Build message: type(1) + slot_id(1) + tag(2) + payload_body(len-32) */
    /* Use incremental HMAC to avoid large stack buffer */
    vos3_hmac_ctx_t ctx;
    uint8_t prefix[4];
    prefix[0] = type;
    prefix[1] = slot_id;
    prefix[2] = (uint8_t)(tag & 0xFF);
    prefix[3] = (uint8_t)(tag >> 8);

    vos3_hmac_sha256_init(&ctx, session_key, key_len);
    vos3_hmac_sha256_update(&ctx, prefix, 4);
    vos3_hmac_sha256_update(&ctx, payload, payload_len - 32);

    uint8_t computed[32];
    vos3_hmac_sha256_final(&ctx, computed);

    /* Constant-time compare against trailer.
     * v23.14: volatile diff prevents compiler from short-circuiting
     * the accumulator loop (D-HIGH2 fix — timing side-channel). */
    const uint8_t *trailer = payload + payload_len - 32;
    volatile uint8_t diff = 0;
    for (int i = 0; i < 32; i++) {
        diff |= computed[i] ^ trailer[i];
    }

    if (diff != 0) {
        record_hmac_violation();
        return VOS3_HMAC_INVALID;
    }

    return VOS3_HMAC_VALID;
}

/* ---- Port I/O ---- */

static inline uint8_t port_inb(uint16_t port)
{
    uint8_t val;
    __asm__ volatile ("inb %1, %0" : "=a"(val) : "Nd"(port));
    return val;
}

static inline void port_outb(uint16_t port, uint8_t val)
{
    __asm__ volatile ("outb %0, %1" :: "a"(val), "Nd"(port));
}

/* ---- UART Constants ---- */

#define COM2        0x2F8U
#define UART_DATA   0U
#define UART_IER    1U
#define UART_FCR    2U
#define UART_IIR    2U
#define UART_LCR    3U
#define UART_MCR    4U
#define UART_LSR    5U
#define UART_MSR    6U
#define UART_SCR    7U

#define LSR_DR      (1U << 0)
#define LSR_THRE    (1U << 5)

/* ---- Phase 7: Per-slot HMAC sequence counter ---- */

uint64_t g_vbus_hmac_seq[8] = {0}; /* Per-slot HMAC sequence counter (64-bit, UINT64_MAX-guarded) */

/* ---- Shared Globals ---- */

int g_use_vbus = 0;  /* 1 = binary VBus transport, 0 = legacy serial */

/* VBus v2 tag/slot echo state — set before each dispatch */
uint16_t g_current_tag = 0;
uint8_t  g_current_slot_id = 0xFF;

/* Phase 4.6: Per-slot write offset for SQ deferred hash (bridge tracks memcpy cursor) */
size_t g_sq_write_off[4] __attribute__((section(".data.gold"))) = {0, 0, 0, 0};
uint32_t g_vbus_protocol_version __attribute__((section(".data.gold"))) = 1;

/* Phase 9: V-AAAK Native Mode — auto-compress text payloads */
int g_vbus_aaak_native = 0;

/* Phase 6.6-U: Salted AAAK — per-session 16-byte XOR mask for dictionary codes.
 * Generated via CSPRNG (vos3_entropy_get_u32 × 4) during v3.1 handshake.
 * Applied to every dictionary code byte in encode/decode:
 *   salted_code = raw_code XOR g_aaak_salt[raw_code % 16]
 * Prevents universal dictionary attacks across different sessions.
 * Cache-line aligned to prevent adjacent-object corruption. */
static uint8_t g_aaak_salt[16] __attribute__((aligned(64)));
static int     g_aaak_salt_active = 0;

/* v23.5: Spinlock protecting static AAAK compression buffers on SMP-2.
 * The AAAK buffers are static (not stack) to avoid exceeding the 64KB
 * vmap kernel stack. This lock serializes concurrent send_ok/send_ok_ctx. */
static vos3_spinlock_t g_aaak_lock = VOS3_SPINLOCK_INIT;

/* Hardening limits */
static uint32_t g_peer_buf_size = 9216U; /* LINE_MAX_BRIDGE */

/* Legacy g_congestion kept as a read-only alias into the token bucket.
 * Code that checks g_congestion still works — it reads 1 when banned or
 * depleted, 0 otherwise.  The authoritative state is g_rate_limiter. */
volatile uint32_t g_congestion = 0U;

/* ---- Token Bucket Rate Limiter ---- */

vos3_token_bucket_t g_rate_limiter;

/** Token bucket parameters */
#define TB_SCALE          1000ULL    /* Fixed-point scale factor */
#define TB_CAPACITY       2000000ULL /* 2000 tokens * 1000 scale */
#define TB_RATE           1000ULL    /* 1 token/tick * 1000 scale */
#define TB_BAD_CRC_LIMIT  50U        /* Bad CRCs before ban */
#define TB_BAD_CRC_WINDOW 6000ULL    /* ~60s at 100Hz tick rate */
#define TB_BAN_DURATION   6000ULL    /* ~60s ban */

void vos3_token_bucket_init(vos3_token_bucket_t *tb)
{
    if (tb == NULL) return;
    tb->capacity      = TB_CAPACITY;
    tb->rate          = TB_RATE;
    tb->tokens        = TB_CAPACITY;
    tb->last_tick     = vos3_sched_get_ticks();
    tb->bad_crc_count = 0U;
    tb->bad_crc_window = tb->last_tick;
    tb->ban_until     = 0ULL;
}

int vos3_token_bucket_consume(vos3_token_bucket_t *tb, uint32_t cost)
{
    if (tb == NULL) return 1;  /* No limiter — allow */

    uint64_t now = vos3_sched_get_ticks();

    /* Check ban */
    if (tb->ban_until != 0ULL) {
        if (now < tb->ban_until) {
            g_congestion = 1U;
            return 0;  /* Still banned */
        }
        /* Ban expired — reset */
        tb->ban_until = 0ULL;
        tb->bad_crc_count = 0U;
        tb->bad_crc_window = now;
    }

    /* Refill tokens based on elapsed ticks */
    uint64_t elapsed = now - tb->last_tick;
    if (elapsed > 0ULL) {
        uint64_t refill = elapsed * tb->rate;
        tb->tokens += refill;
        if (tb->tokens > tb->capacity) {
            tb->tokens = tb->capacity;
        }
        tb->last_tick = now;
    }

    /* Consume */
    uint64_t scaled_cost = (uint64_t)cost * TB_SCALE;
    if (tb->tokens >= scaled_cost) {
        tb->tokens -= scaled_cost;
        g_congestion = 0U;
        return 1;  /* Allowed */
    }

    /* Depleted */
    g_congestion = 1U;
    return 0;
}

void vos3_token_bucket_bad_crc(vos3_token_bucket_t *tb)
{
    if (tb == NULL) return;

    uint64_t now = vos3_sched_get_ticks();

    /* Reset window if expired */
    if (now - tb->bad_crc_window > TB_BAD_CRC_WINDOW) {
        tb->bad_crc_count = 0U;
        tb->bad_crc_window = now;
    }

    tb->bad_crc_count++;

    if (tb->bad_crc_count >= TB_BAD_CRC_LIMIT) {
        tb->ban_until = now + TB_BAN_DURATION;
        g_congestion = 1U;
        VOS3_WARN("[VBUS] CRC flood detected (%u bad CRCs in window) — banned for %llu ticks",
                  tb->bad_crc_count, (unsigned long long)TB_BAN_DURATION);
    }
}

/* ---- Hex characters table ---- */

static const char hex_chars[] = "0123456789abcdef";

/* ---- UART Init: NO FIFO ---- */

void vos3_bridge_uart_reset(void)
{
    port_outb(COM2 + UART_IER, 0x00);   /* Disable interrupts */
    port_outb(COM2 + UART_FCR, 0x00);   /* Disable FIFO */
    port_outb(COM2 + UART_LCR, 0x80);   /* DLAB on */
    port_outb(COM2 + UART_DATA, 0x01);  /* Divisor low = 1 (115200) */
    port_outb(COM2 + UART_IER,  0x00);  /* Divisor high = 0 */
    port_outb(COM2 + UART_LCR, 0x03);   /* 8N1, DLAB off */
    port_outb(COM2 + UART_MCR, 0x0B);   /* DTR + RTS + OUT2 */

    /* Drain stale bytes */
    for (int i = 0; i < 16; i++) {
        if (!(port_inb(COM2 + UART_LSR) & LSR_DR)) break;
        (void)port_inb(COM2 + UART_DATA);
    }
}

/* ---- Low-level serial I/O ---- */

static void bridge_putc(char c)
{
    while (!(port_inb(COM2 + UART_LSR) & LSR_THRE))
        __asm__ volatile ("pause");
    port_outb(COM2 + UART_DATA, (uint8_t)c);
}

static void bridge_puts(const char* s)
{
    if (!s) return;
    while (*s) bridge_putc(*s++);
}

/* ---- CVE-2026-23086: Bound window ---- */

uint32_t vos3_bridge_bound_window(void)
{
    uint32_t window = g_peer_buf_size;
    if (window > LINE_MAX_BRIDGE) window = LINE_MAX_BRIDGE;
    return window;
}

/* ---- Congestion control (legacy API — wraps token bucket) ---- */

void congestion_enter(void)
{
    /* Force depletion by zeroing tokens */
    g_rate_limiter.tokens = 0ULL;
    g_congestion = 1U;
}

void congestion_leave(void)
{
    /* Restore to full capacity */
    g_rate_limiter.tokens = g_rate_limiter.capacity;
    g_rate_limiter.ban_until = 0ULL;
    g_congestion = 0U;
}

/* ---- Hex encode/decode ---- */

void hex_encode(const uint8_t* data, size_t len, char* out)
{
    for (size_t i = 0; i < len; i++) {
        out[i * 2]     = hex_chars[(data[i] >> 4) & 0x0F];
        out[i * 2 + 1] = hex_chars[data[i] & 0x0F];
    }
    out[len * 2] = '\0';
}

int hex_digit(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
    if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
    return -1;
}

int hex_decode(const char* hex, uint8_t* out, size_t max, size_t* out_len)
{
    size_t slen = 0;
    for (const char* p = hex; *p; p++) slen++;
    if (slen & 1) return -1;
    size_t blen = slen / 2;
    if (blen > max) return -1;
    for (size_t i = 0; i < blen; i++) {
        int hi = hex_digit(hex[i * 2]);
        int lo = hex_digit(hex[i * 2 + 1]);
        if (hi < 0 || lo < 0) return -1;
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    *out_len = blen;
    return 0;
}

/* ---- Integer to string ---- */

void uint_to_str(uint64_t val, char* buf, int bufsz)
{
    if (bufsz <= 0) return;
    int idx = 0;
    if (val == 0) {
        if (bufsz > 1) buf[idx++] = '0';
    } else {
        char tmp[20]; int ti = 0;
        while (val && ti < 20) { tmp[ti++] = '0' + (char)(val % 10); val /= 10; }
        while (ti-- && idx < bufsz - 1) buf[idx++] = tmp[ti];
    }
    buf[idx] = '\0';
}

void int_to_str(int32_t val, char* buf, int bufsz)
{
    int idx = 0;
    if (val < 0) {
        if (idx < bufsz - 1) buf[idx++] = '-';
        uint_to_str((uint64_t)(-(int64_t)val), buf + idx, bufsz - idx);
    } else {
        uint_to_str((uint64_t)val, buf, bufsz);
    }
}

/* ---- Errno to string ---- */

const char* errno_str(int code)
{
    if (code < 0) code = -code;
    switch (code) {
    case  1: return "EPERM";
    case  2: return "ENOENT";
    case  5: return "EIO";
    case  9: return "EBADF";
    case 11: return "EAGAIN";
    case 12: return "ENOMEM";
    case 13: return "EACCES";
    case 14: return "EFAULT";
    case 17: return "EEXIST";
    case 20: return "ENOTDIR";
    case 21: return "EISDIR";
    case 22: return "EINVAL";
    case 23: return "ENFILE";
    case 24: return "EMFILE";
    case 27: return "EFBIG";
    case 28: return "ENOSPC";
    case 30: return "EROFS";
    case 36: return "ENAMETOOLONG";
    case 38: return "ENOSYS";
    case 39: return "ENOTEMPTY";
    case 40: return "ELOOP";
    default: return "EUNKNOWN";
    }
}

/* ---- String helpers ---- */

size_t bridge_strlen(const char* s)
{
    size_t len = 0;
    while (s && *s) { len++; s++; }
    return len;
}

void bridge_strcpy(char* dst, const char* src, size_t max)
{
    /* K-CRIT-3 (v20.6): guard max==0 — `i < max - 1` underflows on size_t */
    if (!dst || max == 0U) return;
    if (!src) { dst[0] = '\0'; return; }
    size_t i = 0;
    while (src[i] && i + 1U < max) { dst[i] = src[i]; i++; }
    dst[i] = '\0';
}

/* K-CRIT-4 (v20.6): bounded strlen used by send_ok() to prevent OOB read on
 * non-NUL-terminated or dangling kernel pointers (Copy.Fail-class defense). */
size_t bridge_strnlen(const char* s, size_t max)
{
    size_t len = 0;
    if (!s) return 0;
    while (len < max && s[len]) len++;
    return len;
}

/* ---- Parse helpers ---- */

uint32_t parse_uint(const char* s)
{
    uint32_t val = 0;
    if (!s) return 0;
    while (*s >= '0' && *s <= '9') {
        if (val > UINT32_MAX / 10) return UINT32_MAX; /* overflow guard */
        val = val * 10 + (uint32_t)(*s - '0');
        s++;
    }
    return val;
}

uint64_t parse_u64(const char *s)
{
    uint64_t val = 0;
    if (!s) return 0;
    /* Support decimal or 0x hex */
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        s += 2;
        while (*s) {
            uint8_t d;
            if (*s >= '0' && *s <= '9') d = (uint8_t)(*s - '0');
            else if (*s >= 'a' && *s <= 'f') d = (uint8_t)(*s - 'a' + 10);
            else if (*s >= 'A' && *s <= 'F') d = (uint8_t)(*s - 'A' + 10);
            else break;
            val = (val << 4) | d;
            s++;
        }
    } else {
        while (*s >= '0' && *s <= '9') {
            val = val * 10 + (uint64_t)(*s - '0');
            s++;
        }
    }
    return val;
}

void u64_to_hex(uint64_t val, char *out, int bufsz)
{
    static const char hex[] = "0123456789abcdef";
    int i = 0;
    char tmp[17];
    if (val == 0) { tmp[i++] = '0'; }
    else {
        while (val && i < 16) { tmp[i++] = hex[val & 0xF]; val >>= 4; }
    }
    int pos = 0;
    out[pos++] = '0'; out[pos++] = 'x';
    while (i-- > 0 && pos < bufsz - 1) out[pos++] = tmp[i];
    out[pos] = '\0';
}

/* ---- Response helpers ---- */

/* Phase 9: AAAK-native wrapper for text payloads */
static int aaak_compress_payload(const char *data, char *out, size_t out_max)
{
    if (!g_vbus_aaak_native || !data)
        return 0; /* Not in AAAK mode or no data */

    size_t data_len = bridge_strlen(data);
    if (data_len < 8)
        return 0; /* Too short to benefit from compression */

    uint8_t compressed[LINE_MAX_BRIDGE];
    uint32_t comp_len = vos3_v_aaak_encode((const uint8_t *)data,
                                            (uint32_t)data_len,
                                            compressed, sizeof(compressed));
    if (comp_len == 0 || comp_len >= data_len)
        return 0; /* Compression failed or didn't help */

    /* Prefix with "AAAK:" to signal compressed payload */
    if (5 + comp_len * 2 + 1 > out_max)
        return 0;

    out[0] = 'A'; out[1] = 'A'; out[2] = 'A'; out[3] = 'K'; out[4] = ':';
    hex_encode(compressed, comp_len, out + 5);

    return 1; /* Compressed */
}

void send_ok(const char* data)
{
    /* Phase 9: Try AAAK compression if native mode enabled.
     * v23.4: Static buffer to avoid stack overflow (C2 fix).
     * v23.5: Spinlock guard for SMP-2 safety (C-MED2 fix). */
    static char aaak_buf[LINE_MAX_BRIDGE];
    vos3_spinlock_lock(&g_aaak_lock);
    if (aaak_compress_payload(data, aaak_buf, sizeof(aaak_buf))) {
        data = aaak_buf; /* Use compressed version */
    }
    /* v23.6: Hold lock through data copy — if data points to aaak_buf,
     * releasing early lets another CPU overwrite it mid-copy (C-MED fix). */

    if (g_use_vbus) {
        /* Build "OK|data" in temp buffer, send as RESP frame with tag echo.
         * Task 2.4: Stack-allocated to fix SMP race on SMP-2 (was static). */
        char vbuf[8192];
        size_t pos = 0;
        vbuf[pos++] = 'O'; vbuf[pos++] = 'K';
        if (data && data[0]) {
            vbuf[pos++] = '|';
            /* K-CRIT-4 (v20.6): bound the walk via bridge_strnlen — prevents
             * OOB read if `data` is dangling/non-NUL-terminated kernel
             * pointer (Copy.Fail-class info-leak defense per spec §2.4). */
            size_t max_data = sizeof(vbuf) - pos - 1U;
            size_t data_len = bridge_strnlen(data, max_data);
            for (size_t k = 0; k < data_len; k++) vbuf[pos++] = data[k];
        }
        vos3_spinlock_unlock(&g_aaak_lock);
        /* vbuf is stack-local — safe after unlock */
        /* v23.4: Increment per-slot HMAC sequence counter (C1 fix).
         * Previously only _ctx variants incremented — asymmetric replay. */
        if (g_current_slot_id < 8U &&
            g_vbus_hmac_seq[g_current_slot_id] < UINT64_MAX)
            g_vbus_hmac_seq[g_current_slot_id]++;
        /* Phase 4.2.9: Consensus Gating — buffer RESP if worker slot is gated */
        if (g_current_slot_id < VOS3_MODEL_SLOT_MAX &&
            (vos3_ai_slot_get_caps(g_current_slot_id) & VOS3_CAP_WORKER) &&
            vos3_ai_slot_get_consensus_gate(g_current_slot_id)) {
            vos3_ai_slot_buffer_resp(g_current_slot_id, g_current_tag,
                                      vbuf, (uint16_t)pos);
            return;
        }
        vos3_vbus_send_frame(VBUS_TYPE_RESP, g_current_slot_id,
                              g_current_tag, vbuf, (uint32_t)pos);
        return;
    }
    bridge_puts("OK");
    if (data && data[0]) { bridge_putc('|'); bridge_puts(data); }
    bridge_putc('\n');
    vos3_spinlock_unlock(&g_aaak_lock);
}

void send_err(int code, const char* msg)
{
    /* v23.5: AAAK compression for error payloads (C-MED1 fix).
     * v23.6: Hold lock through msg copy to prevent use-after-unlock. */
    int aaak_err_locked = 0;
    if (g_vbus_aaak_native && msg) {
        static char aaak_err_buf[LINE_MAX_BRIDGE];
        vos3_spinlock_lock(&g_aaak_lock);
        aaak_err_locked = 1;
        if (aaak_compress_payload(msg, aaak_err_buf, sizeof(aaak_err_buf))) {
            msg = aaak_err_buf;
        }
    }

    if (g_use_vbus) {
        /* Build "ERR|code|name: msg" in temp buffer with tag echo.
         * Task 2.4: Stack-allocated to fix SMP race on SMP-2 (was static). */
        char vbuf[512];
        size_t pos = 0;
        const char* s;
        s = "ERR|"; while (*s && pos < sizeof(vbuf) - 1) vbuf[pos++] = *s++;
        char cbuf[21]; uint_to_str((uint64_t)(code < 0 ? -code : code), cbuf, 21);
        s = cbuf; while (*s && pos < sizeof(vbuf) - 1) vbuf[pos++] = *s++;
        vbuf[pos++] = '|';
        s = errno_str(code); while (*s && pos < sizeof(vbuf) - 1) vbuf[pos++] = *s++;
        vbuf[pos++] = ':'; vbuf[pos++] = ' ';
        s = msg; while (*s && pos < sizeof(vbuf) - 1) vbuf[pos++] = *s++;
        if (aaak_err_locked) vos3_spinlock_unlock(&g_aaak_lock);
        /* vbuf is stack-local — safe after unlock */
        /* v23.4: Increment per-slot HMAC sequence counter (C1 fix). */
        if (g_current_slot_id < 8U &&
            g_vbus_hmac_seq[g_current_slot_id] < UINT64_MAX)
            g_vbus_hmac_seq[g_current_slot_id]++;
        /* Phase 4.2.9: Consensus Gating — buffer ERR RESP if worker slot is gated */
        if (g_current_slot_id < VOS3_MODEL_SLOT_MAX &&
            (vos3_ai_slot_get_caps(g_current_slot_id) & VOS3_CAP_WORKER) &&
            vos3_ai_slot_get_consensus_gate(g_current_slot_id)) {
            vos3_ai_slot_buffer_resp(g_current_slot_id, g_current_tag,
                                      vbuf, (uint16_t)pos);
            return;
        }
        vos3_vbus_send_frame(VBUS_TYPE_RESP, g_current_slot_id,
                              g_current_tag, vbuf, (uint32_t)pos);
        return;
    }
    bridge_puts("ERR|");
    char cbuf[21]; uint_to_str((uint64_t)(code < 0 ? -code : code), cbuf, 21);
    bridge_puts(cbuf); bridge_putc('|');
    bridge_puts(errno_str(code));
    bridge_putc(':'); bridge_putc(' ');
    bridge_puts(msg); bridge_putc('\n');
    if (aaak_err_locked) vos3_spinlock_unlock(&g_aaak_lock);
}

/* ---- Phase 7: Context-aware response helpers ---- */

void send_ok_ctx(const vbus_dispatch_ctx_t *ctx, const char *data)
{
    /* v23.3: AAAK compression — matches send_ok() pattern.
     * v23.4: Static buffer to avoid 9KB stack allocation (C2 fix).
     * v23.5: Spinlock guard for SMP-2 safety (C-MED2 fix). */
    static char aaak_ctx_buf[LINE_MAX_BRIDGE];
    vos3_spinlock_lock(&g_aaak_lock);
    if (aaak_compress_payload(data, aaak_ctx_buf, sizeof(aaak_ctx_buf))) {
        data = aaak_ctx_buf;
    }
    /* v23.6: Hold lock through data copy (C-MED use-after-unlock fix). */

    if (g_use_vbus) {
        char vbuf[8192];
        size_t pos = 0;
        vbuf[pos++] = 'O'; vbuf[pos++] = 'K';
        if (data && data[0]) {
            vbuf[pos++] = '|';
            /* K-CRIT-4 (v20.6): bound the walk via bridge_strnlen — same
             * info-leak defense as send_ok above. */
            size_t max_data = sizeof(vbuf) - pos - 1U;
            size_t data_len = bridge_strnlen(data, max_data);
            for (size_t k = 0; k < data_len; k++) vbuf[pos++] = data[k];
        }
        vos3_spinlock_unlock(&g_aaak_lock);
        /* vbuf is stack-local — safe after unlock */
        /* Increment per-slot HMAC sequence counter */
        if (ctx->slot_id < 8U &&
            g_vbus_hmac_seq[ctx->slot_id] < UINT64_MAX)
            g_vbus_hmac_seq[ctx->slot_id]++;
        /* Phase 4.2.9: Consensus Gating — buffer RESP if worker slot is gated */
        if (ctx->slot_id < VOS3_MODEL_SLOT_MAX &&
            (vos3_ai_slot_get_caps(ctx->slot_id) & VOS3_CAP_WORKER) &&
            vos3_ai_slot_get_consensus_gate(ctx->slot_id)) {
            vos3_ai_slot_buffer_resp(ctx->slot_id, ctx->tag,
                                      vbuf, (uint16_t)pos);
            return;
        }
        vos3_vbus_send_frame(VBUS_TYPE_RESP, ctx->slot_id,
                              ctx->tag, vbuf, (uint32_t)pos);
        return;
    }
    bridge_puts("OK");
    if (data && data[0]) { bridge_putc('|'); bridge_puts(data); }
    bridge_putc('\n');
    vos3_spinlock_unlock(&g_aaak_lock);
}

void send_err_ctx(const vbus_dispatch_ctx_t *ctx, int code, const char *msg)
{
    /* v23.5: AAAK compression for ctx error payloads (C-MED1 fix).
     * v23.6: Hold lock through msg copy to prevent use-after-unlock. */
    int aaak_err_ctx_locked = 0;
    if (g_vbus_aaak_native && msg) {
        static char aaak_err_ctx_buf[LINE_MAX_BRIDGE];
        vos3_spinlock_lock(&g_aaak_lock);
        aaak_err_ctx_locked = 1;
        if (aaak_compress_payload(msg, aaak_err_ctx_buf, sizeof(aaak_err_ctx_buf))) {
            msg = aaak_err_ctx_buf;
        }
    }

    if (g_use_vbus) {
        char vbuf[512];
        size_t pos = 0;
        const char *s;
        s = "ERR|"; while (*s && pos < sizeof(vbuf) - 1) vbuf[pos++] = *s++;
        char cbuf[21]; uint_to_str((uint64_t)(code < 0 ? -code : code), cbuf, 21);
        s = cbuf; while (*s && pos < sizeof(vbuf) - 1) vbuf[pos++] = *s++;
        vbuf[pos++] = '|';
        s = errno_str(code); while (*s && pos < sizeof(vbuf) - 1) vbuf[pos++] = *s++;
        vbuf[pos++] = ':'; vbuf[pos++] = ' ';
        s = msg; while (*s && pos < sizeof(vbuf) - 1) vbuf[pos++] = *s++;
        if (aaak_err_ctx_locked) vos3_spinlock_unlock(&g_aaak_lock);
        /* vbuf is stack-local — safe after unlock */
        /* Increment per-slot HMAC sequence counter */
        if (ctx->slot_id < 8U &&
            g_vbus_hmac_seq[ctx->slot_id] < UINT64_MAX)
            g_vbus_hmac_seq[ctx->slot_id]++;
        /* Phase 4.2.9: Consensus Gating — buffer ERR RESP if worker slot is gated */
        if (ctx->slot_id < VOS3_MODEL_SLOT_MAX &&
            (vos3_ai_slot_get_caps(ctx->slot_id) & VOS3_CAP_WORKER) &&
            vos3_ai_slot_get_consensus_gate(ctx->slot_id)) {
            vos3_ai_slot_buffer_resp(ctx->slot_id, ctx->tag,
                                      vbuf, (uint16_t)pos);
            return;
        }
        vos3_vbus_send_frame(VBUS_TYPE_RESP, ctx->slot_id,
                              ctx->tag, vbuf, (uint32_t)pos);
        return;
    }
    bridge_puts("ERR|");
    char cbuf2[21]; uint_to_str((uint64_t)(code < 0 ? -code : code), cbuf2, 21);
    bridge_puts(cbuf2); bridge_putc('|');
    bridge_puts(errno_str(code));
    bridge_putc(':'); bridge_putc(' ');
    bridge_puts(msg); bridge_putc('\n');
    if (aaak_err_ctx_locked) vos3_spinlock_unlock(&g_aaak_lock);
}

/* ============================================================================
 * Phase 6.6-U: AAAK Salt Management + Semantic Jitter API
 * ============================================================================ */

/**
 * @brief Set the per-session AAAK salt.
 *
 * Called during v3.1 handshake after CSPRNG generation.
 * The salt is XOR-applied to every dictionary code byte during encode/decode,
 * making the wire encoding session-specific and defeating replay attacks.
 *
 * @param[in] salt  16-byte salt buffer (VBUS_AAAK_SALT_SIZE)
 */
void vos3_vbus_set_aaak_salt(const uint8_t *salt)
{
    if (!salt) {
        g_aaak_salt_active = 0;
        return;
    }
    for (int i = 0; i < 16; i++)
        g_aaak_salt[i] = salt[i];
    g_aaak_salt_active = 1;
    VOS3_INFO("[V-AAAK] Session salt activated (16 bytes)");
}

/**
 * @brief Check if AAAK salt is active.
 * @return 1 if salted mode is active, 0 otherwise.
 */
int vos3_vbus_aaak_salt_active(void)
{
    return g_aaak_salt_active;
}

/**
 * @brief Apply semantic jitter delay (0-500µs random pause).
 *
 * Formal API wrapping the existing jitter pattern from kim_flush_batch().
 * Entropy-seeded pause loop masks AAAK encoder timing from power-analysis
 * and electromagnetic side-channel attacks.
 *
 * Each `pause` instruction is ~10-40 cycles (~5-20ns at 2GHz).
 * 500µs ≈ 2000 pauses at ~250ns avg. Factor of 4 loop iterations per µs.
 *
 * Entropy source: vos3_entropy_get_u32() — ChaCha20 CSPRNG (RDRAND/RDSEED seeded).
 */
void vos3_vbus_apply_jitter(void)
{
    uint32_t jitter_us = vos3_entropy_get_u32() % 500U;
    volatile uint32_t j;
    for (j = 0; j < jitter_us * 4U; j++)
        __asm__ volatile("pause");
}

/* ============================================================================
 * Phase 8: V-AAAK Token-Aware Shorthand
 * ============================================================================
 * Lossless token-map compression for VBus text payloads.
 * Static dictionary of 256 common LLM tokens mapped to single-byte codes.
 * Format: [0xFF escape][code_byte] for dictionary hits, raw bytes otherwise.
 * Achieves ~30x compression on typical LLM output.
 * ============================================================================ */

/* Escape byte — signals next byte is a dictionary code */
#define V_AAAK_ESCAPE  0xFFU

/* Dictionary: 64 most common tokens (expandable to 256)
 * v23.9: Cache-line aligned + const — prevents stray overwrites from
 *        reaching the codec's lookup table via adjacent-object corruption. */
static const char *const g_v_aaak_dict[] __attribute__((aligned(64))) = {
    " the",   " a",     " is",    " of",    " and",   " to",    " in",    " it",    /*  0- 7 */
    " that",  " for",   " was",   " on",    " are",   " with",  " as",    " this",  /*  8-15 */
    " be",    " at",    " have",  " from",  " or",    " by",    " not",   " but",   /* 16-23 */
    " what",  " all",   " were",  " when",  " we",    " there", " can",   " an",    /* 24-31 */
    " your",  " which", " their", " if",    " do",    " will",  " each",  " how",   /* 32-39 */
    " them",  " then",  " he",    " she",   " my",    " no",    " more",  " so",    /* 40-47 */
    "the",    "and",    "ing",    "tion",   "ed ",    "er ",    "es ",    "re ",     /* 48-55 */
    "\n",     "  ",     ", ",     ". ",     ": ",     ";\n",    "}\n",    "{\n",     /* 56-63 */
};
#define V_AAAK_DICT_SIZE  (sizeof(g_v_aaak_dict) / sizeof(g_v_aaak_dict[0]))

/* Dictionary entry lengths (precomputed) */
static uint8_t g_v_aaak_lens[64]; /* filled on first use */
static int g_v_aaak_inited = 0;

static void v_aaak_init_lens(void)
{
    if (g_v_aaak_inited)
        return;
    for (int i = 0; i < (int)V_AAAK_DICT_SIZE; i++) {
        const char *s = g_v_aaak_dict[i];
        uint8_t len = 0;
        while (s[len]) len++;
        g_v_aaak_lens[i] = len;
    }
    g_v_aaak_inited = 1;
}

/*
 * vos3_v_aaak_encode — compress text using token dictionary
 * Returns encoded length, or 0 on error.
 * out_buf must be at least in_len * 2 bytes (worst case: every byte escaped).
 */
uint32_t vos3_v_aaak_encode(const uint8_t *in, uint32_t in_len,
                             uint8_t *out, uint32_t out_max)
{
    v_aaak_init_lens();
    uint32_t wi = 0; /* write index */
    uint32_t ri = 0; /* read index */
    uint32_t code_seq = 0; /* monotonic code-byte counter for salt index */

    while (ri < in_len) {
        int best = -1;
        uint8_t best_len = 0;

        /* Greedy longest-match against dictionary */
        for (int d = 0; d < (int)V_AAAK_DICT_SIZE; d++) {
            uint8_t dlen = g_v_aaak_lens[d];
            if (dlen <= best_len)
                continue;
            if (ri + dlen > in_len)
                continue;
            /* Compare */
            const char *dp = g_v_aaak_dict[d];
            int match = 1;
            for (uint8_t k = 0; k < dlen; k++) {
                if (in[ri + k] != (uint8_t)dp[k]) {
                    match = 0;
                    break;
                }
            }
            if (match) {
                best = d;
                best_len = dlen;
            }
        }

        if (best >= 0) {
            /* Emit escape + salted code.
             * Salt uses modular addition mod 64 (not XOR) to guarantee the
             * salted code stays in [0, 63] and never collides with the 0xFF
             * escape byte. Monotonic code-byte counter ensures invertibility
             * independent of output stream position.
             * Encode: salted = (code + salt_byte) % 64
             * Decode: code   = (salted + 64 - salt_byte) % 64
             *
             * Resilience-Matrix F13 — wi overflow guard. The wi + N > out_max
             * comparison below assumes wi + N does not wrap. On a >4 GiB
             * encode buffer (out_max near UINT32_MAX) the addition itself
             * could wrap silently. Reject before the wrap could happen. */
            if (wi > UINT32_MAX - 2U)
                return 0;
            if (wi + 2 > out_max)
                return 0; /* overflow */
            out[wi++] = V_AAAK_ESCAPE;
            uint8_t code = (uint8_t)best;
            if (g_aaak_salt_active)
                code = (uint8_t)((code + g_aaak_salt[code_seq % 16]) % 64U);
            code_seq++;
            out[wi++] = code;
            ri += best_len;
        } else {
            /* Emit raw byte — if it's 0xFF, double-escape it */
            if (in[ri] == V_AAAK_ESCAPE) {
                /* Resilience-Matrix F13 — see note above. */
                if (wi > UINT32_MAX - 2U)
                    return 0;
                if (wi + 2 > out_max)
                    return 0;
                out[wi++] = V_AAAK_ESCAPE;
                out[wi++] = V_AAAK_ESCAPE;
            } else {
                /* Resilience-Matrix F13 — see note above. */
                if (wi > UINT32_MAX - 1U)
                    return 0;
                if (wi + 1 > out_max)
                    return 0;
                out[wi++] = in[ri];
            }
            ri++;
        }
    }

    return wi;
}

/*
 * vos3_v_aaak_decode — decompress V-AAAK encoded data
 * Returns decoded length, or 0 on error.
 * Phase 9.1: NULL guard + boundary hardening (truncated escape = -EILSEQ).
 */
uint32_t vos3_v_aaak_decode(const uint8_t *in, uint32_t in_len,
                             uint8_t *out, uint32_t out_max)
{
    v_aaak_init_lens();
    if (!in || !out || out_max == 0)
        return 0; /* NULL/zero guard */
    uint32_t wi = 0;
    uint32_t ri = 0;
    uint32_t code_seq = 0; /* monotonic code-byte counter — matches encoder */

    while (ri < in_len) {
        if (in[ri] == V_AAAK_ESCAPE) {
            ri++;
            if (ri >= in_len) {
                VOS3_WARN("V-AAAK: truncated escape at offset %u/%u", ri - 1, in_len);
                return 0; /* -EILSEQ: truncated escape at buffer end */
            }
            if (in[ri] == V_AAAK_ESCAPE) {
                /* Escaped 0xFF literal */
                if (wi + 1 > out_max)
                    return 0;
                out[wi++] = V_AAAK_ESCAPE;
                ri++;
            } else {
                /* Dictionary lookup (de-salt if active).
                 * Salt index uses monotonic code-byte counter (matches
                 * encoder's code_seq) for position-independent invertibility. */
                uint8_t code = in[ri++];
                if (g_aaak_salt_active)
                    code = (uint8_t)((code + 64U - g_aaak_salt[code_seq % 16]) % 64U);
                code_seq++;
                if (code >= V_AAAK_DICT_SIZE)
                    return 0; /* invalid code */
                uint8_t dlen = g_v_aaak_lens[code];
                if (wi + dlen > out_max)
                    return 0;
                const char *dp = g_v_aaak_dict[code];
                for (uint8_t k = 0; k < dlen; k++)
                    out[wi++] = (uint8_t)dp[k];
            }
        } else {
            if (wi + 1 > out_max)
                return 0;
            out[wi++] = in[ri++];
        }
    }

    return wi;
}

/* ============================================================================
 * [OLYMPUS-FIX A-15] APEX-HOME — VBus PRO-license registration seam
 * ============================================================================
 *
 * Architectural hook for the VBus REGISTER_AGENT path.
 *
 * vbus.h defines VOS3_VBUS_REJECT_PRO_FEATURE_NO_LIC (= 3u) — the
 * response code emitted when an agent requests a profile whose
 * `profile_flags` includes VOS3_VBUS_PROFILE_PRO_FEATURE on a kernel
 * that does NOT carry an active PRO license.
 *
 * Today no in-tree caller emits this code: every charter-protected
 * primitive (W^X, MMR, slot states, VBus protocol itself) lives in
 * CORE. The 10 GiB hugepage ceiling is the only PRO-gated runtime
 * feature, and it degrades silently (kernel/pro/README.md rule 1).
 *
 * What this helper provides — TODAY — is the **architectural seam**.
 * Future PRO features that genuinely refuse to register without a
 * license simply call vos3_vbus_check_pro_license(profile_flags) at
 * the top of their REGISTER_AGENT handler. Returning a non-zero
 * code means "reject with this code"; zero means "proceed".
 *
 * The seam is wired so that adding a real PRO feature later does
 * NOT require any further infrastructure work — the constant, the
 * helper, and the response-code path are all in place.
 */

extern int vos3_pro_license_check(void);  /* kernel/src/pro/license_check.c */

uint32_t vos3_vbus_check_pro_license(uint32_t profile_flags)
{
    /* If the agent is not asking for a PRO feature, accept. */
    if (!(profile_flags & VOS3_VBUS_PROFILE_PRO_FEATURE)) {
        return 0u;
    }

    /* PRO feature requested. The license check is fail-soft: if no
     * valid license is present, return the v3.0 reject code. The
     * caller (REGISTER_AGENT handler) then translates this into the
     * VBus response frame. */
    if (vos3_pro_license_check() == 0) {
        return VOS3_VBUS_REJECT_PRO_FEATURE_NO_LIC;  /* 3u */
    }

    return 0u;
}
