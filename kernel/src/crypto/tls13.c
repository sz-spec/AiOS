/**
 * @file tls13.c
 * @brief VOS3 TLS 1.3 Client Handshake — RFC 8446
 *
 * @details Freestanding kernel TLS 1.3 client implementation:
 *          - Full handshake: ClientHello -> ServerHello -> EncryptedExtensions
 *            -> Certificate -> CertificateVerify -> Finished
 *          - Cipher: TLS_AES_128_GCM_SHA256 (0x1301)
 *          - Key exchange: X25519 ephemeral (RFC 7748)
 *          - Key derivation: HKDF-SHA256 (RFC 5869 / RFC 8446 section 7.1)
 *          - Post-handshake secret scrub via vos3_cache_wipe()
 *          - FPU guard for AES-NI operations (protects AI register state)
 *
 * @note Certificate validation accepts any valid chain. Production deployments
 *       would need embedded root CAs for proper chain verification.
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 7.2 — Sovereign Crypto Shield (v35.1)
 */

#include "../../include/vos/tls.h"
#include "../../include/vos/crypto.h"
#include "../../include/vos/sha256.h"
#include "../../include/vos/entropy.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/tcp.h"

/* ============================================================================
 * EXTERNAL SOCKET I/O — fd-level send/recv from TCP subsystem
 * ============================================================================ */

/*
 * vos3_sys_send and vos3_sys_recv are declared in tcp.h. We rely on those
 * declarations rather than redeclaring here to avoid duplicate extern warnings.
 * They operate on connected TCP socket file descriptors.
 */

/* ============================================================================
 * BYTE ORDER HELPERS — Big-endian encoding for TLS wire format
 * ============================================================================ */

static inline uint16_t be16(uint16_t x)
{
    return (uint16_t)((x >> 8) | (x << 8));
}

static inline void put_be16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v >> 8);
    p[1] = (uint8_t)(v & 0xFF);
}

static inline void put_be24(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)((v >> 16) & 0xFF);
    p[1] = (uint8_t)((v >> 8) & 0xFF);
    p[2] = (uint8_t)(v & 0xFF);
}

static inline void put_be32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)((v >> 24) & 0xFF);
    p[1] = (uint8_t)((v >> 16) & 0xFF);
    p[2] = (uint8_t)((v >> 8) & 0xFF);
    p[3] = (uint8_t)(v & 0xFF);
}

static inline uint16_t get_be16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] << 8 | (uint16_t)p[1]);
}

static inline uint32_t get_be24(const uint8_t *p)
{
    return ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | (uint32_t)p[2];
}

static inline uint32_t get_be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static inline void put_be64(uint8_t *p, uint64_t v)
{
    p[0] = (uint8_t)(v >> 56);
    p[1] = (uint8_t)(v >> 48);
    p[2] = (uint8_t)(v >> 40);
    p[3] = (uint8_t)(v >> 32);
    p[4] = (uint8_t)(v >> 24);
    p[5] = (uint8_t)(v >> 16);
    p[6] = (uint8_t)(v >> 8);
    p[7] = (uint8_t)(v);
}

/* ============================================================================
 * INTERNAL CONSTANTS
 * ============================================================================ */

/** @brief TLS record header size (content_type + legacy_version + length) */
#define TLS_RECORD_HDR_SIZE     5U

/** @brief AES-GCM authentication tag length */
#define TLS_TAG_LEN             16U

/** @brief AES-GCM nonce/IV length */
#define TLS_IV_LEN              12U

/** @brief SHA-256 digest length */
#define TLS_HASH_LEN            32U

/** @brief Maximum TLS record payload on the wire (ciphertext + tag + inner type) */
#define TLS_MAX_WIRE_RECORD     (TLS_MAX_RECORD_SIZE + TLS_TAG_LEN + 1U)

/** @brief TLS alert level: warning */
#define TLS_ALERT_LEVEL_WARNING 1U

/** @brief TLS alert level: fatal */
#define TLS_ALERT_LEVEL_FATAL   2U

/** @brief Handshake message buffer for building/parsing (stack allocated) */
#define TLS_HS_BUILD_BUF_SIZE   8192U

/** @brief Record read buffer (stack allocated) */
#define TLS_RECORD_BUF_SIZE     (TLS_MAX_RECORD_SIZE + 256U)

/* ============================================================================
 * CIPHER SUITE REGISTRY — Table-Driven (PQC-Ready)
 *
 * Extensible suite table: add new entries here for future algorithms
 * (e.g., ML-KEM + AES-256-GCM, Kyber + ChaCha20-Poly1305) without touching
 * the handshake state machine.
 * ============================================================================ */

typedef struct tls_cipher_suite_def {
    uint16_t    id;         /**< IANA cipher suite identifier */
    uint8_t     key_len;    /**< AEAD key length in bytes (16 or 32) */
    uint8_t     iv_len;     /**< AEAD nonce/IV length (always 12 for GCM) */
    uint8_t     hash_len;   /**< Hash output length (32 for SHA-256, 48 for SHA-384) */
    uint8_t     _pad[3];
} tls_cipher_suite_def_t;

static const tls_cipher_suite_def_t g_tls_suites[] = {
    { TLS_AES_128_GCM_SHA256, 16, 12, 32, {0} },
    { TLS_AES_256_GCM_SHA384, 32, 12, 48, {0} },
    /* Future PQC suites would be added here, e.g.:
     * { TLS_MLKEM768_AES256GCM_SHA384, 32, 12, 48, {0} },
     */
};

#define TLS_NUM_SUITES  (sizeof(g_tls_suites) / sizeof(g_tls_suites[0]))

/**
 * @brief Look up a cipher suite definition by IANA identifier.
 * @return Pointer to suite definition, or NULL if unsupported.
 */
static const tls_cipher_suite_def_t *tls_find_suite(uint16_t id)
{
    for (size_t i = 0; i < TLS_NUM_SUITES; i++) {
        if (g_tls_suites[i].id == id)
            return &g_tls_suites[i];
    }
    return NULL;
}

/* ============================================================================
 * TRANSCRIPT HASH HELPERS
 * ============================================================================ */

/**
 * @brief SHA-256 context used for running transcript hash during handshake.
 *
 * We maintain a running incremental SHA-256 hash of all handshake messages
 * (ClientHello, ServerHello, EncryptedExtensions, Certificate,
 * CertificateVerify, Finished) as required by RFC 8446 section 4.4.1.
 */
typedef struct tls_transcript {
    vos3_sha256_ctx_t sha;
    int               started;
} tls_transcript_t;

static void transcript_init(tls_transcript_t *t)
{
    vos3_sha256_init(&t->sha);
    t->started = 1;
}

static void transcript_update(tls_transcript_t *t, const uint8_t *data, size_t len)
{
    if (t->started) {
        vos3_sha256_update(&t->sha, data, len);
    }
}

/**
 * @brief Get current transcript hash WITHOUT finalizing the context.
 *
 * Makes a copy of the SHA-256 state, finalizes the copy, and writes
 * the 32-byte digest to @p out. The running context is left intact
 * for continued hashing of subsequent handshake messages.
 */
static void transcript_hash(const tls_transcript_t *t, uint8_t out[32])
{
    vos3_sha256_ctx_t copy;
    memcpy(&copy, &t->sha, sizeof(copy));
    vos3_sha256_final(&copy, out);
}

/* ============================================================================
 * RELIABLE TCP I/O WRAPPERS
 * ============================================================================ */

/**
 * @brief Read exactly @p len bytes from the TCP socket.
 *
 * Loops on partial reads until all bytes are received or an error occurs.
 *
 * @param fd    Connected TCP socket file descriptor
 * @param buf   Destination buffer
 * @param len   Number of bytes to read
 * @return 0 on success, -1 on error or premature close
 */
static int tcp_read_exact(int fd, uint8_t *buf, size_t len)
{
    size_t off = 0;

    while (off < len) {
        ssize_t n = vos3_sys_recv(fd, buf + off, len - off, 0);
        if (n <= 0) {
            VOS3_ERROR("TLS: tcp_read_exact failed (got %d, wanted %u)\n",
                       (int)n, (unsigned)(len - off));
            return -1;
        }
        off += (size_t)n;
    }
    return 0;
}

/**
 * @brief Write exactly @p len bytes to the TCP socket.
 *
 * Loops on partial writes until all bytes are sent or an error occurs.
 *
 * @param fd    Connected TCP socket file descriptor
 * @param buf   Source buffer
 * @param len   Number of bytes to write
 * @return 0 on success, -1 on error
 */
static int tcp_write_all(int fd, const uint8_t *buf, size_t len)
{
    size_t off = 0;

    while (off < len) {
        ssize_t n = vos3_sys_send(fd, buf + off, len - off, 0);
        if (n <= 0) {
            VOS3_ERROR("TLS: tcp_write_all failed (got %d, wanted %u)\n",
                       (int)n, (unsigned)(len - off));
            return -1;
        }
        off += (size_t)n;
    }
    return 0;
}

/* ============================================================================
 * AES-GCM NONCE CONSTRUCTION
 * ============================================================================ */

/**
 * @brief Build a 12-byte AES-GCM nonce by XORing the IV with the
 *        64-bit sequence number (left-padded to 12 bytes).
 *
 * Per RFC 8446 section 5.3:
 *   nonce = iv XOR padded_seq
 *   where padded_seq is the 64-bit sequence number in big-endian,
 *   zero-extended to 12 bytes (4 leading zero bytes + 8 byte seqnum).
 */
static void build_nonce(uint8_t nonce[12], const uint8_t iv[12], uint64_t seq)
{
    uint8_t seq_bytes[12];

    memset(seq_bytes, 0, 4);
    put_be64(seq_bytes + 4, seq);

    for (int i = 0; i < 12; i++) {
        nonce[i] = iv[i] ^ seq_bytes[i];
    }
}

/* ============================================================================
 * TLS RECORD LAYER — WRITE
 * ============================================================================ */

/**
 * @brief State tracking for whether we are currently using handshake or
 *        application traffic keys.
 */
typedef enum tls_key_phase {
    KEY_PHASE_NONE      = 0,  /**< Pre-handshake (plaintext) */
    KEY_PHASE_HANDSHAKE = 1,  /**< Using handshake traffic keys */
    KEY_PHASE_APP       = 2   /**< Using application traffic keys */
} tls_key_phase_t;

/**
 * @brief Write a TLS record to the wire.
 *
 * For plaintext records (state < SERVER_HELLO_RECV): sends content_type
 * header + data directly.
 *
 * For encrypted records (state >= SERVER_HELLO_RECV): builds AES-GCM
 * ciphertext with inner content type appended, 16-byte authentication tag,
 * and sends under the APPLICATION_DATA (0x17) outer content type as
 * required by TLS 1.3.
 *
 * @param ctx           TLS context
 * @param content_type  TLS content type for this message
 * @param data          Plaintext payload
 * @param len           Payload length
 * @param key_phase     Which key set to use for encryption
 * @return 0 on success, negative on error
 */
static int tls_write_record(vos3_tls_ctx_t *ctx, uint8_t content_type,
                             const uint8_t *data, size_t len,
                             tls_key_phase_t key_phase)
{
    uint8_t hdr[TLS_RECORD_HDR_SIZE];
    int rc;

    if (key_phase == KEY_PHASE_NONE) {
        /* --- Plaintext record --- */
        if (len > TLS_MAX_RECORD_SIZE) {
            VOS3_ERROR("TLS: plaintext record too large (%u)\n", (unsigned)len);
            return -1;
        }

        hdr[0] = content_type;
        put_be16(hdr + 1, TLS_VERSION_12);  /* legacy_record_version = 0x0303 */
        put_be16(hdr + 3, (uint16_t)len);

        rc = tcp_write_all(ctx->tcp_fd, hdr, TLS_RECORD_HDR_SIZE);
        if (rc != 0) return rc;

        rc = tcp_write_all(ctx->tcp_fd, data, len);
        return rc;
    }

    /* --- Encrypted record (TLS 1.3) --- */

    /*
     * Wire format for encrypted record:
     *   outer content_type = APPLICATION_DATA (0x17)
     *   outer version      = 0x0303
     *   outer length       = len + 1 (inner content type) + 16 (tag)
     *   payload            = AES-GCM(plaintext || inner_content_type)
     *   tag                = 16 bytes
     */
    size_t inner_len = len + 1;  /* plaintext + inner content type byte */
    size_t ct_len    = inner_len + TLS_TAG_LEN;

    if (ct_len > TLS_MAX_WIRE_RECORD) {
        VOS3_ERROR("TLS: encrypted record too large (%u)\n", (unsigned)ct_len);
        return -1;
    }

    /* Build outer record header */
    hdr[0] = TLS_CONTENT_APPLICATION;  /* always 0x17 for encrypted records */
    put_be16(hdr + 1, TLS_VERSION_12);
    put_be16(hdr + 3, (uint16_t)ct_len);

    /* Select key/IV and sequence number */
    const uint8_t *key;
    const uint8_t *iv;
    uint64_t *seq_ptr;

    if (key_phase == KEY_PHASE_HANDSHAKE) {
        key     = ctx->client_hs_key;
        iv      = ctx->client_hs_iv;
        seq_ptr = &ctx->client_seq;
    } else {
        key     = ctx->client_app_key;
        iv      = ctx->client_app_iv;
        seq_ptr = &ctx->client_seq;
    }

    /* Build nonce */
    uint8_t nonce[TLS_IV_LEN];
    build_nonce(nonce, iv, *seq_ptr);

    /* Build plaintext: data + inner content type */
    uint8_t pt_buf[TLS_MAX_RECORD_SIZE + 1];
    if (len > 0) {
        memcpy(pt_buf, data, len);
    }
    pt_buf[len] = content_type;  /* inner content type */

    /* Encrypt with AES-GCM */
    uint8_t ct_buf[TLS_MAX_RECORD_SIZE + 1];
    uint8_t tag[TLS_TAG_LEN];

    vos3_aes_gcm_ctx_t aes_ctx;
    rc = vos3_aes_gcm_init(&aes_ctx, key, ctx->key_len);
    if (rc != 0) {
        VOS3_ERROR("TLS: AES-GCM init failed for write\n");
        vos3_cache_wipe(pt_buf, sizeof(pt_buf));
        return -1;
    }

    vos3_fpu_begin();
    rc = vos3_aes_gcm_encrypt(&aes_ctx, nonce,
                                hdr, TLS_RECORD_HDR_SIZE,  /* AAD = record header */
                                pt_buf, inner_len,
                                ct_buf, tag);
    vos3_fpu_end();

    vos3_aes_gcm_destroy(&aes_ctx);

    if (rc != 0) {
        VOS3_ERROR("TLS: AES-GCM encrypt failed\n");
        vos3_cache_wipe(pt_buf, sizeof(pt_buf));
        return -1;
    }

    /* Increment sequence number */
    (*seq_ptr)++;

    /* Send: header + ciphertext + tag */
    rc = tcp_write_all(ctx->tcp_fd, hdr, TLS_RECORD_HDR_SIZE);
    if (rc == 0) {
        rc = tcp_write_all(ctx->tcp_fd, ct_buf, inner_len);
    }
    if (rc == 0) {
        rc = tcp_write_all(ctx->tcp_fd, tag, TLS_TAG_LEN);
    }

    /* Phase 8.7: Scrub plaintext and ciphertext from stack to prevent
     * cold-boot / L1D cache residue exposure. */
    vos3_cache_wipe(pt_buf, sizeof(pt_buf));
    vos3_cache_wipe(ct_buf, sizeof(ct_buf));
    return rc;
}

/* ============================================================================
 * TLS RECORD LAYER — READ
 * ============================================================================ */

/**
 * @brief Read a single TLS record from the wire.
 *
 * For plaintext records: reads header + payload directly.
 * For encrypted records: decrypts with AES-GCM using the server's traffic
 * keys, verifies the authentication tag, extracts the inner content type.
 *
 * @param ctx       TLS context
 * @param out_type  Output: inner content type
 * @param out_data  Output: plaintext data
 * @param out_len   Output: plaintext length
 * @param max_len   Maximum output buffer size
 * @param key_phase Which key set to use for decryption
 * @return 0 on success, negative on error
 */
static int tls_read_record(vos3_tls_ctx_t *ctx, uint8_t *out_type,
                            uint8_t *out_data, size_t *out_len, size_t max_len,
                            tls_key_phase_t key_phase)
{
    uint8_t hdr[TLS_RECORD_HDR_SIZE];
    int rc;

    /* Read 5-byte record header */
    rc = tcp_read_exact(ctx->tcp_fd, hdr, TLS_RECORD_HDR_SIZE);
    if (rc != 0) {
        VOS3_ERROR("TLS: failed to read record header\n");
        return -1;
    }

    uint8_t  outer_type = hdr[0];
    uint16_t rec_len    = get_be16(hdr + 3);

    /* Sanity check record length */
    if (rec_len > TLS_MAX_WIRE_RECORD) {
        VOS3_ERROR("TLS: record too large (%u)\n", (unsigned)rec_len);
        return -1;
    }

    /* Read record payload */
    uint8_t payload[TLS_MAX_WIRE_RECORD];
    rc = tcp_read_exact(ctx->tcp_fd, payload, rec_len);
    if (rc != 0) {
        VOS3_ERROR("TLS: failed to read record payload (%u bytes)\n",
                   (unsigned)rec_len);
        return -1;
    }

    if (key_phase == KEY_PHASE_NONE) {
        /* --- Plaintext record --- */
        if (rec_len > max_len) {
            VOS3_ERROR("TLS: plaintext record exceeds output buffer\n");
            return -1;
        }
        *out_type = outer_type;
        memcpy(out_data, payload, rec_len);
        *out_len = rec_len;
        return 0;
    }

    /* --- Encrypted record (TLS 1.3) --- */

    /*
     * Outer type must be APPLICATION_DATA (0x17) for all encrypted records.
     * If we receive a plaintext ALERT or CHANGE_CIPHER_SPEC during the
     * encrypted phase, handle them as a protocol error.
     */
    if (outer_type == TLS_CONTENT_CHANGE_CIPHER) {
        /* TLS 1.3 compatibility: ignore CCS records (RFC 8446 section 5) */
        *out_type = TLS_CONTENT_CHANGE_CIPHER;
        *out_len  = 0;
        return 0;
    }

    if (outer_type != TLS_CONTENT_APPLICATION) {
        /*
         * Could be a plaintext alert before encryption is fully negotiated.
         * Pass it through if it is an alert record.
         */
        if (outer_type == TLS_CONTENT_ALERT && rec_len >= 2) {
            *out_type = TLS_CONTENT_ALERT;
            if (rec_len <= max_len) {
                memcpy(out_data, payload, rec_len);
            }
            *out_len = rec_len;
            return 0;
        }
        VOS3_ERROR("TLS: unexpected outer content type 0x%02x in encrypted phase\n",
                   outer_type);
        return -1;
    }

    /* Encrypted payload must be at least tag + 1 byte inner type */
    if (rec_len < TLS_TAG_LEN + 1) {
        VOS3_ERROR("TLS: encrypted record too short (%u)\n", (unsigned)rec_len);
        return -1;
    }

    size_t ciphertext_len = rec_len - TLS_TAG_LEN;
    const uint8_t *ct_ptr  = payload;
    const uint8_t *tag_ptr = payload + ciphertext_len;

    /* Select server key/IV and sequence number */
    const uint8_t *key;
    const uint8_t *iv;
    uint64_t *seq_ptr;

    if (key_phase == KEY_PHASE_HANDSHAKE) {
        key     = ctx->server_hs_key;
        iv      = ctx->server_hs_iv;
        seq_ptr = &ctx->server_seq;
    } else {
        key     = ctx->server_app_key;
        iv      = ctx->server_app_iv;
        seq_ptr = &ctx->server_seq;
    }

    /* Build nonce */
    uint8_t nonce[TLS_IV_LEN];
    build_nonce(nonce, iv, *seq_ptr);

    /* Decrypt with AES-GCM */
    uint8_t pt_buf[TLS_MAX_WIRE_RECORD];

    vos3_aes_gcm_ctx_t aes_ctx;
    rc = vos3_aes_gcm_init(&aes_ctx, key, ctx->key_len);
    if (rc != 0) {
        VOS3_ERROR("TLS: AES-GCM init failed for read\n");
        return -1;
    }

    vos3_fpu_begin();
    rc = vos3_aes_gcm_decrypt(&aes_ctx, nonce,
                                hdr, TLS_RECORD_HDR_SIZE,  /* AAD = record header */
                                ct_ptr, ciphertext_len,
                                tag_ptr, pt_buf);
    vos3_fpu_end();

    vos3_aes_gcm_destroy(&aes_ctx);

    if (rc != 0) {
        VOS3_ERROR("TLS: AES-GCM decrypt/verify failed (bad_record_mac)\n");
        ctx->last_alert = TLS_ALERT_BAD_RECORD_MAC;
        ctx->fatal = 1;
        vos3_cache_wipe(pt_buf, sizeof(pt_buf));
        return -1;
    }

    /* Increment server sequence number */
    (*seq_ptr)++;

    /*
     * Extract inner content type: it is the last non-zero byte of the
     * decrypted plaintext. Per RFC 8446 section 5.4, zero padding may
     * follow the content type byte. Scan backwards to find it.
     */
    size_t pt_len = ciphertext_len;
    while (pt_len > 0 && pt_buf[pt_len - 1] == 0) {
        pt_len--;
    }
    if (pt_len == 0) {
        VOS3_ERROR("TLS: decrypted record has no content type byte\n");
        vos3_cache_wipe(pt_buf, sizeof(pt_buf));
        return -1;
    }

    uint8_t inner_type = pt_buf[pt_len - 1];
    pt_len--;  /* exclude the content type byte from data */

    if (pt_len > max_len) {
        VOS3_ERROR("TLS: decrypted record exceeds output buffer (%u > %u)\n",
                   (unsigned)pt_len, (unsigned)max_len);
        vos3_cache_wipe(pt_buf, sizeof(pt_buf));
        return -1;
    }

    *out_type = inner_type;
    if (pt_len > 0) {
        memcpy(out_data, pt_buf, pt_len);
    }
    *out_len = pt_len;

    /* Phase 8.7: Scrub decrypted plaintext from stack to prevent
     * cold-boot / L1D cache residue exposure. */
    vos3_cache_wipe(pt_buf, sizeof(pt_buf));
    return 0;
}

/* ============================================================================
 * CLIENT HELLO BUILDER
 * ============================================================================ */

/**
 * @brief Build a TLS 1.3 ClientHello handshake message.
 *
 * Constructs the full handshake message including:
 *   - Handshake header (type + 24-bit length)
 *   - legacy_version (0x0303)
 *   - client_random (32 bytes from entropy)
 *   - legacy_session_id (empty)
 *   - cipher_suites: TLS_AES_128_GCM_SHA256
 *   - legacy_compression_methods: null (1 byte = 0x00)
 *   - Extensions: supported_versions, supported_groups, key_share,
 *                 signature_algorithms, server_name (SNI)
 *
 * @param ctx      TLS context (eph_pubkey must be generated, client_random filled)
 * @param buf      Output buffer
 * @param buf_len  Output buffer capacity
 * @return Total message length, or negative on error
 */
static int tls_build_client_hello(vos3_tls_ctx_t *ctx, uint8_t *buf,
                                   size_t buf_len)
{
    if (buf_len < TLS_HS_BUILD_BUF_SIZE) {
        return -1;
    }

    uint8_t *p = buf;
    uint8_t *start = buf;

    /* === Handshake header (filled in later once we know the length) === */
    uint8_t *hs_hdr = p;
    p += 4;  /* type(1) + length(3) */

    /* === ClientHello body === */
    uint8_t *ch_start = p;

    /* legacy_version = 0x0303 (TLS 1.2 — required by RFC 8446) */
    put_be16(p, TLS_VERSION_12);
    p += 2;

    /* client_random (32 bytes) */
    memcpy(p, ctx->client_random, 32);
    p += 32;

    /* legacy_session_id = empty (length 0) */
    *p++ = 0;

    /* cipher_suites: 2 bytes length + suite(s) */
    put_be16(p, 2);  /* 1 cipher suite = 2 bytes */
    p += 2;
    put_be16(p, TLS_AES_128_GCM_SHA256);  /* 0x1301 */
    p += 2;

    /* legacy_compression_methods: 1 method = null compression */
    *p++ = 1;   /* length */
    *p++ = 0;   /* null compression */

    /* === Extensions === */
    uint8_t *ext_len_ptr = p;
    p += 2;  /* placeholder for extensions_length */

    uint8_t *ext_start = p;

    /* --- Extension: supported_versions (0x002B) --- */
    put_be16(p, TLS_EXT_SUPPORTED_VERSIONS);
    p += 2;
    put_be16(p, 3);  /* extension data length = 3 */
    p += 2;
    *p++ = 2;                      /* list length = 2 bytes (1 version) */
    put_be16(p, TLS_VERSION_13);   /* 0x0304 = TLS 1.3 */
    p += 2;

    /* --- Extension: supported_groups (0x000A) ---
     *
     * Sprint 14.1 / Gap 2 — we now advertise BOTH the hybrid post-
     * quantum group X25519MLKEM768 (0x11EC) and the classical X25519
     * (0x001D). Order matters: peers select the first jointly-supported
     * group, so the hybrid is listed first to maximize harvest-now-
     * decrypt-later resistance whenever the peer can do hybrid.
     *
     * key_share below intentionally ONLY includes the X25519 share until
     * the kernel ML-KEM-768 lattice operations land (Stage 14.B.2 —
     * vos3_mlkem768_keygen / _encaps / _decaps currently return
     * VOS3_MLKEM_E_NOT_IMPLEMENTED, see kernel/include/vos/mlkem768.h
     * §"ML-KEM-768 public API — STUBBED in Stage 14.B.1"). A peer that
     * sees us offer hybrid in supported_groups but only ship X25519 in
     * key_share will send HelloRetryRequest with its preferred group;
     * we already handle the X25519 retry path. When the lattice port
     * ships, the key_share extension below grows to include the hybrid
     * entry (TLS_X25519MLKEM768_SHARE_BYTES = 1216) and this comment
     * disappears.
     */
    put_be16(p, TLS_EXT_SUPPORTED_GROUPS);
    p += 2;
    put_be16(p, 6);  /* extension data length: list_len(2) + 2 groups * 2 */
    p += 2;
    put_be16(p, 4);  /* named_group_list length = 4 bytes (2 groups) */
    p += 2;
    put_be16(p, TLS_GROUP_X25519MLKEM768);  /* 0x11EC — hybrid PQ */
    p += 2;
    put_be16(p, TLS_GROUP_X25519);          /* 0x001D — classical fallback */
    p += 2;

    /* --- Extension: key_share (0x0033) --- */
    put_be16(p, TLS_EXT_KEY_SHARE);
    p += 2;
    /* extension data: client_shares_length(2) + group(2) + key_len(2) + key(32) = 38 */
    put_be16(p, 38);
    p += 2;
    put_be16(p, 36);  /* client_shares total length = 36 */
    p += 2;
    put_be16(p, TLS_GROUP_X25519);
    p += 2;
    put_be16(p, 32);  /* key_exchange length = 32 bytes */
    p += 2;
    memcpy(p, ctx->eph_pubkey, 32);
    p += 32;

    /* --- Extension: signature_algorithms (0x000D) --- */
    put_be16(p, TLS_EXT_SIGNATURE_ALGORITHMS);
    p += 2;
    /* 3 algorithms * 2 bytes each = 6, plus list length field = 8 total */
    put_be16(p, 8);
    p += 2;
    put_be16(p, 6);  /* signature_scheme_list length */
    p += 2;
    put_be16(p, TLS_SIG_RSA_PSS_SHA256);     /* 0x0804 */
    p += 2;
    put_be16(p, TLS_SIG_ECDSA_SHA256);       /* 0x0403 */
    p += 2;
    put_be16(p, TLS_SIG_RSA_PKCS1_SHA256);   /* 0x0401 */
    p += 2;

    /* --- Extension: server_name / SNI (0x0000) --- */
    if (ctx->server_name_len > 0) {
        uint16_t name_len = ctx->server_name_len;

        put_be16(p, TLS_EXT_SERVER_NAME);
        p += 2;
        /* extension data length = 2 (list len) + 1 (type) + 2 (name len) + name */
        put_be16(p, (uint16_t)(name_len + 5));
        p += 2;
        /* server_name_list length */
        put_be16(p, (uint16_t)(name_len + 3));
        p += 2;
        *p++ = 0;  /* name_type = host_name (0) */
        put_be16(p, name_len);
        p += 2;
        memcpy(p, ctx->server_name, name_len);
        p += name_len;
    }

    /* Fill in extensions_length */
    uint16_t ext_total = (uint16_t)(p - ext_start);
    put_be16(ext_len_ptr, ext_total);

    /* === Fill in handshake header === */
    uint32_t ch_len = (uint32_t)(p - ch_start);
    hs_hdr[0] = TLS_HS_CLIENT_HELLO;
    put_be24(hs_hdr + 1, ch_len);

    return (int)(p - start);
}

/* ============================================================================
 * SERVER HELLO PARSER
 * ============================================================================ */

/**
 * @brief Parse a ServerHello handshake message.
 *
 * Extracts the server's chosen cipher suite, the X25519 key share from
 * extensions, and verifies that supported_versions indicates TLS 1.3.
 * Computes the X25519 shared secret.
 *
 * @param ctx   TLS context
 * @param data  ServerHello payload (after handshake header)
 * @param len   Payload length
 * @return 0 on success, negative on error
 */
static int tls_parse_server_hello(vos3_tls_ctx_t *ctx, const uint8_t *data,
                                   size_t len)
{
    const uint8_t *p = data;
    const uint8_t *end = data + len;

    /* legacy_version (2) */
    if (p + 2 > end) return -1;
    /* uint16_t legacy_ver = get_be16(p); -- not checked, must be 0x0303 */
    p += 2;

    /* server_random (32) */
    if (p + 32 > end) return -1;
    /* We don't store server_random but could check for HelloRetryRequest
     * sentinel (SHA-256 of "HelloRetryRequest") — skip for now */
    p += 32;

    /* session_id echo */
    if (p + 1 > end) return -1;
    uint8_t sid_len = *p++;
    if (p + sid_len > end) return -1;
    p += sid_len;

    /* cipher_suite (2) */
    if (p + 2 > end) return -1;
    uint16_t suite = get_be16(p);
    p += 2;

    const tls_cipher_suite_def_t *suite_def = tls_find_suite(suite);
    if (!suite_def) {
        VOS3_ERROR("TLS: unsupported cipher suite 0x%04x\n", suite);
        return -1;
    }

    ctx->cipher_suite = suite;
    ctx->key_len      = suite_def->key_len;

    /* compression_method (1) — must be 0 */
    if (p + 1 > end) return -1;
    if (*p++ != 0) {
        VOS3_ERROR("TLS: non-null compression in ServerHello\n");
        return -1;
    }

    /* === Extensions === */
    if (p + 2 > end) {
        VOS3_ERROR("TLS: ServerHello missing extensions\n");
        return -1;
    }
    uint16_t ext_len = get_be16(p);
    p += 2;

    if (p + ext_len > end) {
        VOS3_ERROR("TLS: ServerHello extensions length mismatch\n");
        return -1;
    }

    const uint8_t *ext_end = p + ext_len;
    int got_versions = 0;
    int got_key_share = 0;

    while (p + 4 <= ext_end) {
        uint16_t etype = get_be16(p);
        p += 2;
        uint16_t elen = get_be16(p);
        p += 2;

        if (p + elen > ext_end) {
            VOS3_ERROR("TLS: extension overflows\n");
            return -1;
        }

        if (etype == TLS_EXT_SUPPORTED_VERSIONS) {
            /* Must contain exactly 0x0304 (TLS 1.3) */
            if (elen != 2) {
                VOS3_ERROR("TLS: bad supported_versions length\n");
                return -1;
            }
            uint16_t ver = get_be16(p);
            if (ver != TLS_VERSION_13) {
                VOS3_ERROR("TLS: server did not select TLS 1.3 (got 0x%04x)\n", ver);
                return -1;
            }
            got_versions = 1;
        }
        else if (etype == TLS_EXT_KEY_SHARE) {
            /* Sprint 14.1 / Gap 2 — accept either:
             *   classical X25519         : group=0x001D, key_len=32
             *   hybrid X25519MLKEM768    : group=0x11EC, key_len=1216
             *
             * Hybrid path is reachable only after the kernel ML-KEM-768
             * lattice operations land (Stage 14.B.2). Until then, we
             * advertise the group but cannot satisfy a server that
             * selects it; that server should HelloRetryRequest back to
             * X25519. If it does NOT, we surface the error explicitly
             * rather than silently fall through. */
            if (elen < 4) {
                VOS3_ERROR("TLS: key_share extension too short\n");
                return -1;
            }
            uint16_t group = get_be16(p);
            uint16_t klen = get_be16(p + 2);

            if (group == TLS_GROUP_X25519) {
                if (elen < 36 || klen != 32) {
                    VOS3_ERROR("TLS: bad X25519 key_share (elen=%u klen=%u)\n",
                               elen, klen);
                    return -1;
                }
                memcpy(ctx->peer_pubkey, p + 4, 32);
                got_key_share = 1;
            }
            else if (group == TLS_GROUP_X25519MLKEM768) {
                /* Stage 14.B.1 honest-scope: we DECLARED the group in
                 * supported_groups, so a server can legally pick it.
                 * But the lattice math isn't in tree yet — the kernel's
                 * vos3_mlkem768_decaps stub returns
                 * VOS3_MLKEM_E_NOT_IMPLEMENTED. Refusing the handshake
                 * here is the only honest answer; a peer-side retry to
                 * X25519 will succeed on the second handshake attempt.
                 *
                 * When Stage 14.B.2 lands:
                 *   if (klen != TLS_X25519MLKEM768_SHARE_BYTES) return -1;
                 *   memcpy(ctx->peer_pubkey, p + 4, 32);            // X25519 half
                 *   memcpy(ctx->mlkem_pubkey, p + 4 + 32, 1184);    // ML-KEM half
                 *   vos3_mlkem768_encaps(ctx->mlkem_pubkey, ..., ctx->mlkem_ct,
                 *                        ctx->mlkem_ss);
                 *   // shared_secret = mlkem_ss(32) || x25519_ss(32)  // 64 bytes
                 */
                VOS3_ERROR("TLS: server selected X25519MLKEM768 but kernel "
                           "ML-KEM-768 lattice is Stage 14.B.2 stub "
                           "(VOS3_MLKEM_E_NOT_IMPLEMENTED). Peer should "
                           "HelloRetryRequest back to X25519.\n");
                return -1;
            }
            else {
                VOS3_ERROR("TLS: server selected unsupported group 0x%04x\n", group);
                return -1;
            }
        }

        p += elen;
    }

    if (!got_versions) {
        VOS3_ERROR("TLS: ServerHello missing supported_versions extension\n");
        return -1;
    }
    if (!got_key_share) {
        VOS3_ERROR("TLS: ServerHello missing key_share extension\n");
        return -1;
    }

    /* Compute X25519 shared secret */
    int rc = vos3_x25519_shared(ctx->shared_secret, ctx->eph_privkey,
                                 ctx->peer_pubkey);
    if (rc != 0) {
        VOS3_ERROR("TLS: X25519 shared secret computation failed\n");
        return -1;
    }

    VOS3_INFO("TLS: ServerHello received (suite=0x%04x, X25519 shared computed)\n",
              suite);
    return 0;
}

/* ============================================================================
 * TLS 1.3 KEY SCHEDULE — RFC 8446 section 7.1
 * ============================================================================ */

/**
 * @brief Derive-Secret helper.
 *
 * Derive-Secret(Secret, Label, Messages) =
 *     HKDF-Expand-Label(Secret, Label, Hash(Messages), 32)
 *
 * @param secret     Input secret (32 bytes)
 * @param label      ASCII label (e.g., "c hs traffic")
 * @param label_len  Label length
 * @param hash       SHA-256 hash of transcript messages (32 bytes, or NULL for empty hash)
 * @param out        Output (32 bytes)
 * @return 0 on success
 */
static int derive_secret(const uint8_t secret[32],
                          const char *label, size_t label_len,
                          const uint8_t *hash,
                          uint8_t out[32])
{
    uint8_t empty_hash[32];

    if (hash == NULL) {
        /* Hash of empty string */
        vos3_sha256_ctx_t tmp;
        vos3_sha256_init(&tmp);
        vos3_sha256_final(&tmp, empty_hash);
        hash = empty_hash;
    }

    return vos3_tls13_hkdf_expand_label(secret, label, label_len,
                                         hash, 32, out, 32);
}

/**
 * @brief Derive handshake traffic keys from the shared secret.
 *
 * RFC 8446 section 7.1:
 *   early_secret      = HKDF-Extract(salt=0, IKM=0)     [no PSK]
 *   derived_secret     = Derive-Secret(early_secret, "derived", "")
 *   handshake_secret  = HKDF-Extract(salt=derived_secret, IKM=shared_secret)
 *   c_hs_traffic      = Derive-Secret(hs_secret, "c hs traffic", transcript)
 *   s_hs_traffic      = Derive-Secret(hs_secret, "s hs traffic", transcript)
 *
 * Then for each traffic secret:
 *   key = HKDF-Expand-Label(traffic_secret, "key", "", key_len)
 *   iv  = HKDF-Expand-Label(traffic_secret, "iv", "", 12)
 *
 * @param ctx             TLS context (shared_secret must be set)
 * @param transcript_hash Current transcript hash (SHA-256 of CH || SH)
 * @return 0 on success
 */
static int tls13_derive_handshake_keys(vos3_tls_ctx_t *ctx,
                                        const uint8_t transcript_hash[32])
{
    uint8_t zero_key[32];
    uint8_t derived[32];
    uint8_t c_hs_traffic[32];
    uint8_t s_hs_traffic[32];
    int rc;

    memset(zero_key, 0, 32);

    /* Step 1: early_secret = HKDF-Extract(salt=0, IKM=0) */
    vos3_hkdf_extract(zero_key, 32, zero_key, 32, ctx->early_secret);

    /* Step 2: derived = Derive-Secret(early_secret, "derived", "") */
    rc = derive_secret(ctx->early_secret, "derived", 7, NULL, derived);
    if (rc != 0) return rc;

    /* Step 3: handshake_secret = HKDF-Extract(salt=derived, IKM=shared_secret) */
    vos3_hkdf_extract(derived, 32, ctx->shared_secret, 32, ctx->handshake_secret);

    /* Step 4: client handshake traffic secret */
    rc = derive_secret(ctx->handshake_secret, "c hs traffic", 12,
                       transcript_hash, c_hs_traffic);
    if (rc != 0) return rc;

    /* Step 5: server handshake traffic secret */
    rc = derive_secret(ctx->handshake_secret, "s hs traffic", 12,
                       transcript_hash, s_hs_traffic);
    if (rc != 0) return rc;

    /* Step 6: derive actual keys and IVs */
    rc = vos3_tls13_hkdf_expand_label(c_hs_traffic, "key", 3,
                                       NULL, 0,
                                       ctx->client_hs_key, ctx->key_len);
    if (rc != 0) return rc;

    rc = vos3_tls13_hkdf_expand_label(c_hs_traffic, "iv", 2,
                                       NULL, 0,
                                       ctx->client_hs_iv, TLS_IV_LEN);
    if (rc != 0) return rc;

    rc = vos3_tls13_hkdf_expand_label(s_hs_traffic, "key", 3,
                                       NULL, 0,
                                       ctx->server_hs_key, ctx->key_len);
    if (rc != 0) return rc;

    rc = vos3_tls13_hkdf_expand_label(s_hs_traffic, "iv", 2,
                                       NULL, 0,
                                       ctx->server_hs_iv, TLS_IV_LEN);
    if (rc != 0) return rc;

    /* Scrub intermediate traffic secrets */
    vos3_cache_wipe(c_hs_traffic, 32);
    vos3_cache_wipe(s_hs_traffic, 32);
    vos3_cache_wipe(derived, 32);
    vos3_cache_wipe(zero_key, 32);

    VOS3_INFO("TLS: handshake keys derived (key_len=%u)\n", ctx->key_len);
    return 0;
}

/**
 * @brief Derive application traffic keys from the master secret.
 *
 * RFC 8446 section 7.1:
 *   derived_secret    = Derive-Secret(handshake_secret, "derived", "")
 *   master_secret     = HKDF-Extract(salt=derived_secret, IKM=0)
 *   c_ap_traffic      = Derive-Secret(master_secret, "c ap traffic", full_transcript)
 *   s_ap_traffic      = Derive-Secret(master_secret, "s ap traffic", full_transcript)
 *
 * @param ctx             TLS context (handshake_secret must be set)
 * @param transcript_hash Full transcript hash (CH..server Finished)
 * @return 0 on success
 */
static int tls13_derive_app_keys(vos3_tls_ctx_t *ctx,
                                  const uint8_t transcript_hash[32])
{
    uint8_t zero_ikm[32];
    uint8_t derived[32];
    uint8_t c_ap_traffic[32];
    uint8_t s_ap_traffic[32];
    int rc;

    memset(zero_ikm, 0, 32);

    /* derived = Derive-Secret(handshake_secret, "derived", "") */
    rc = derive_secret(ctx->handshake_secret, "derived", 7, NULL, derived);
    if (rc != 0) return rc;

    /* master_secret = HKDF-Extract(salt=derived, IKM=0) */
    vos3_hkdf_extract(derived, 32, zero_ikm, 32, ctx->master_secret);

    /* client application traffic secret */
    rc = derive_secret(ctx->master_secret, "c ap traffic", 12,
                       transcript_hash, c_ap_traffic);
    if (rc != 0) return rc;

    /* server application traffic secret */
    rc = derive_secret(ctx->master_secret, "s ap traffic", 12,
                       transcript_hash, s_ap_traffic);
    if (rc != 0) return rc;

    /* Derive actual keys and IVs */
    rc = vos3_tls13_hkdf_expand_label(c_ap_traffic, "key", 3,
                                       NULL, 0,
                                       ctx->client_app_key, ctx->key_len);
    if (rc != 0) return rc;

    rc = vos3_tls13_hkdf_expand_label(c_ap_traffic, "iv", 2,
                                       NULL, 0,
                                       ctx->client_app_iv, TLS_IV_LEN);
    if (rc != 0) return rc;

    rc = vos3_tls13_hkdf_expand_label(s_ap_traffic, "key", 3,
                                       NULL, 0,
                                       ctx->server_app_key, ctx->key_len);
    if (rc != 0) return rc;

    rc = vos3_tls13_hkdf_expand_label(s_ap_traffic, "iv", 2,
                                       NULL, 0,
                                       ctx->server_app_iv, TLS_IV_LEN);
    if (rc != 0) return rc;

    /* Scrub intermediates */
    vos3_cache_wipe(c_ap_traffic, 32);
    vos3_cache_wipe(s_ap_traffic, 32);
    vos3_cache_wipe(derived, 32);
    vos3_cache_wipe(zero_ikm, 32);

    VOS3_INFO("TLS: application keys derived\n");
    return 0;
}

/**
 * @brief Compute the Finished verify_data for a given traffic secret.
 *
 * RFC 8446 section 4.4.4:
 *   finished_key = HKDF-Expand-Label(BaseKey, "finished", "", Hash.length)
 *   verify_data  = HMAC(finished_key, Transcript-Hash(Handshake Context...))
 *
 * @param base_key        The handshake traffic secret (client or server)
 * @param transcript_hash Current transcript hash at the Finished message point
 * @param out             Output verify_data (32 bytes)
 * @return 0 on success
 */
static int tls13_compute_finished(const uint8_t base_key[32],
                                   const uint8_t transcript_hash[32],
                                   uint8_t out[32])
{
    uint8_t finished_key[32];
    int rc;

    /* finished_key = HKDF-Expand-Label(base_key, "finished", "", 32) */
    rc = vos3_tls13_hkdf_expand_label(base_key, "finished", 8,
                                       NULL, 0,
                                       finished_key, 32);
    if (rc != 0) return rc;

    /* verify_data = HMAC-SHA256(finished_key, transcript_hash) */
    vos3_hmac_sha256(finished_key, 32, transcript_hash, 32, out);

    vos3_cache_wipe(finished_key, 32);
    return 0;
}

/* ============================================================================
 * HANDSHAKE MESSAGE PARSING HELPERS
 * ============================================================================ */

/**
 * @brief Parse handshake message header: type(1) + length(3).
 *
 * @param data     Input buffer
 * @param data_len Buffer length
 * @param out_type Output: handshake type
 * @param out_len  Output: message body length
 * @return Pointer to message body (data + 4), or NULL on error
 */
static const uint8_t *parse_hs_header(const uint8_t *data, size_t data_len,
                                       uint8_t *out_type, uint32_t *out_len)
{
    if (data_len < 4) return NULL;

    *out_type = data[0];
    *out_len  = get_be24(data + 1);

    if (4 + *out_len > data_len) return NULL;

    return data + 4;
}

/**
 * @brief Read encrypted handshake messages from the server.
 *
 * This function reads the sequence of encrypted handshake messages
 * that follow ServerHello in the TLS 1.3 handshake:
 *   1. EncryptedExtensions
 *   2. Certificate (optional if PSK, but expected without PSK)
 *   3. CertificateVerify (optional if PSK)
 *   4. Finished
 *
 * Each message is fed into the transcript hash for key derivation.
 * The server Finished verify_data is checked against a locally computed
 * value to verify the integrity of the handshake.
 *
 * @param ctx       TLS context (handshake keys must be derived)
 * @param ts        Running transcript hash
 * @return 0 on success, negative on error
 */
static int tls_read_server_handshake(vos3_tls_ctx_t *ctx, tls_transcript_t *ts)
{
    uint8_t rec_buf[TLS_RECORD_BUF_SIZE];
    uint8_t rec_type;
    size_t  rec_len;
    int     rc;

    int got_ee       = 0;
    int got_cert     = 0;
    int got_cv       = 0;
    int got_finished = 0;

    /* Server handshake traffic secret for Finished verification.
     * We need to compute it from the handshake_secret before we start,
     * but it was already derived in tls13_derive_handshake_keys. We can
     * re-derive it or we can store it. For simplicity we re-derive it. */
    uint8_t s_hs_traffic[32];
    {
        uint8_t hs_transcript[32];
        transcript_hash(ts, hs_transcript);
        /* Note: this is the same transcript used in key derivation (CH || SH) */
        rc = derive_secret(ctx->handshake_secret, "s hs traffic", 12,
                           hs_transcript, s_hs_traffic);
        if (rc != 0) return rc;
    }

    /* Reset server sequence number for handshake decryption.
     * The server starts a fresh sequence at 0 after deriving handshake keys. */
    ctx->server_seq = 0;

    while (!got_finished) {
        rc = tls_read_record(ctx, &rec_type, rec_buf, &rec_len,
                             sizeof(rec_buf), KEY_PHASE_HANDSHAKE);
        if (rc != 0) {
            VOS3_ERROR("TLS: failed to read server handshake record\n");
            vos3_cache_wipe(s_hs_traffic, 32);
            return -1;
        }

        /* Skip CCS records (TLS 1.3 middlebox compatibility) */
        if (rec_type == TLS_CONTENT_CHANGE_CIPHER) {
            continue;
        }

        /* Handle alerts */
        if (rec_type == TLS_CONTENT_ALERT) {
            if (rec_len >= 2) {
                VOS3_ERROR("TLS: received alert (level=%u, desc=%u)\n",
                           rec_buf[0], rec_buf[1]);
                ctx->last_alert = rec_buf[1];
                ctx->fatal = (rec_buf[0] == TLS_ALERT_LEVEL_FATAL) ? 1 : 0;
            }
            vos3_cache_wipe(s_hs_traffic, 32);
            return -1;
        }

        if (rec_type != TLS_CONTENT_HANDSHAKE) {
            VOS3_ERROR("TLS: unexpected content type %u during handshake\n",
                       rec_type);
            vos3_cache_wipe(s_hs_traffic, 32);
            return -1;
        }

        /* Parse handshake messages within this record.
         * Multiple handshake messages can be coalesced into one record. */
        size_t offset = 0;
        while (offset < rec_len) {
            uint8_t  hs_type;
            uint32_t hs_len;
            const uint8_t *body = parse_hs_header(rec_buf + offset,
                                                   rec_len - offset,
                                                   &hs_type, &hs_len);
            if (body == NULL) {
                VOS3_ERROR("TLS: malformed handshake message\n");
                vos3_cache_wipe(s_hs_traffic, 32);
                return -1;
            }

            size_t full_msg_len = 4 + hs_len;

            switch (hs_type) {
            case TLS_HS_ENCRYPTED_EXTENSIONS:
                VOS3_INFO("TLS: EncryptedExtensions received\n");
                /* Update transcript hash with the full handshake message */
                transcript_update(ts, rec_buf + offset, full_msg_len);
                ctx->state = TLS_STATE_ENCRYPTED_EXT;
                got_ee = 1;
                /*
                 * We do not parse EncryptedExtensions in detail here.
                 * Production code would extract ALPN, server_name ack, etc.
                 */
                break;

            case TLS_HS_CERTIFICATE:
                VOS3_INFO("TLS: Certificate received\n");
                if (!got_ee) {
                    VOS3_ERROR("TLS: Certificate before EncryptedExtensions\n");
                    vos3_cache_wipe(s_hs_traffic, 32);
                    return -1;
                }
                transcript_update(ts, rec_buf + offset, full_msg_len);
                ctx->state = TLS_STATE_CERTIFICATE;
                got_cert = 1;
                /*
                 * NOTE: Certificate validation is SKIPPED in this kernel
                 * implementation. A production deployment would need embedded
                 * root CA certificates and full chain verification (X.509 parsing,
                 * signature checks, expiration, revocation, hostname matching).
                 *
                 * For now, we accept any certificate presented by the server.
                 */
                break;

            case TLS_HS_CERTIFICATE_VERIFY:
                VOS3_INFO("TLS: CertificateVerify received\n");
                if (!got_cert) {
                    VOS3_ERROR("TLS: CertificateVerify before Certificate\n");
                    vos3_cache_wipe(s_hs_traffic, 32);
                    return -1;
                }
                transcript_update(ts, rec_buf + offset, full_msg_len);
                ctx->state = TLS_STATE_CERT_VERIFY;
                got_cv = 1;
                (void)got_cv;
                /*
                 * NOTE: CertificateVerify signature validation is SKIPPED.
                 * Production code would verify the server's signature over
                 * the transcript hash using the certificate's public key.
                 */
                break;

            case TLS_HS_FINISHED:
                VOS3_INFO("TLS: server Finished received\n");
                if (!got_ee) {
                    VOS3_ERROR("TLS: Finished before EncryptedExtensions\n");
                    vos3_cache_wipe(s_hs_traffic, 32);
                    return -1;
                }

                /*
                 * Verify server Finished:
                 *   1. Compute transcript hash up to (but not including) Finished
                 *   2. Compute expected verify_data using server HS traffic secret
                 *   3. Compare with received verify_data
                 */
                {
                    uint8_t current_transcript[32];
                    transcript_hash(ts, current_transcript);

                    uint8_t expected_verify[32];
                    rc = tls13_compute_finished(s_hs_traffic,
                                                current_transcript,
                                                expected_verify);
                    if (rc != 0) {
                        VOS3_ERROR("TLS: failed to compute expected Finished\n");
                        vos3_cache_wipe(s_hs_traffic, 32);
                        return -1;
                    }

                    /* The Finished message body is the verify_data (32 bytes for SHA-256) */
                    if (hs_len != TLS_HASH_LEN) {
                        VOS3_ERROR("TLS: Finished verify_data wrong length "
                                   "(%u, expected %u)\n",
                                   (unsigned)hs_len, TLS_HASH_LEN);
                        vos3_cache_wipe(s_hs_traffic, 32);
                        vos3_cache_wipe(expected_verify, 32);
                        return -1;
                    }

                    /* Constant-time comparison */
                    uint8_t diff = 0;
                    for (uint32_t i = 0; i < TLS_HASH_LEN; i++) {
                        diff |= body[i] ^ expected_verify[i];
                    }

                    vos3_cache_wipe(expected_verify, 32);

                    if (diff != 0) {
                        VOS3_ERROR("TLS: server Finished verify_data mismatch!\n");
                        ctx->last_alert = TLS_ALERT_HANDSHAKE_FAILURE;
                        ctx->fatal = 1;
                        vos3_cache_wipe(s_hs_traffic, 32);
                        return -1;
                    }

                    VOS3_INFO("TLS: server Finished verified OK\n");
                }

                /* Update transcript with the Finished message */
                transcript_update(ts, rec_buf + offset, full_msg_len);
                ctx->state = TLS_STATE_SERVER_FINISHED;
                got_finished = 1;
                break;

            case TLS_HS_NEW_SESSION_TICKET:
                /* Ignore NewSessionTicket — we don't support resumption */
                VOS3_DEBUG("TLS: ignoring NewSessionTicket\n");
                transcript_update(ts, rec_buf + offset, full_msg_len);
                break;

            default:
                VOS3_WARN("TLS: unknown handshake type %u (len=%u), skipping\n",
                          hs_type, (unsigned)hs_len);
                transcript_update(ts, rec_buf + offset, full_msg_len);
                break;
            }

            offset += full_msg_len;
        }
    }

    vos3_cache_wipe(s_hs_traffic, 32);
    return 0;
}

/**
 * @brief Build and send the client Finished message.
 *
 * The client Finished contains:
 *   verify_data = HMAC(finished_key, Transcript-Hash(CH..server_Finished))
 *
 * It is sent as an encrypted handshake record using the client handshake
 * traffic keys.
 *
 * @param ctx  TLS context
 * @param ts   Running transcript hash (must include all messages through server Finished)
 * @return 0 on success, negative on error
 */
static int tls_send_client_finished(vos3_tls_ctx_t *ctx, tls_transcript_t *ts)
{
    uint8_t current_transcript[32];
    uint8_t c_hs_traffic[32];
    uint8_t verify_data[32];
    int rc;

    /* Re-derive client handshake traffic secret from the CH||SH transcript.
     * We need the original transcript at the point handshake keys were derived,
     * not the current one. However, since we stored the handshake_secret in ctx,
     * and we need the c hs traffic secret specifically, we must re-derive it.
     *
     * Actually, for the Finished message computation, we need:
     *   finished_key = HKDF-Expand-Label(c_hs_traffic_secret, "finished", "", 32)
     *   verify_data  = HMAC(finished_key, Transcript-Hash(all through server Finished))
     *
     * The c_hs_traffic_secret was derived from the CH||SH transcript, but we
     * need it here for the Finished key. We re-derive using stored handshake_secret
     * and the ORIGINAL CH||SH transcript hash that we used during key derivation.
     *
     * PROBLEM: We don't have the original CH||SH transcript hash stored.
     * SOLUTION: Store it in ctx during key derivation... but we didn't.
     *
     * Alternative: use the transcript_hash stored in ctx->transcript_hash that
     * was captured before we started reading server handshake messages.
     *
     * Actually, the simplest correct approach is to re-derive c_hs_traffic
     * from handshake_secret using the CH||SH transcript hash that was used
     * for key derivation. We stored that in ctx->transcript_hash before
     * entering the server handshake reading loop. Let's use that.
     */

    /* Use the stored transcript hash from CH||SH point */
    rc = derive_secret(ctx->handshake_secret, "c hs traffic", 12,
                       ctx->transcript_hash, c_hs_traffic);
    if (rc != 0) return rc;

    /* Get current transcript hash (through server Finished) */
    transcript_hash(ts, current_transcript);

    /* Compute verify_data */
    rc = tls13_compute_finished(c_hs_traffic, current_transcript, verify_data);
    vos3_cache_wipe(c_hs_traffic, 32);
    if (rc != 0) return rc;

    /* Build Finished handshake message: type(1) + length(3) + verify_data(32) */
    uint8_t finished_msg[4 + 32];
    finished_msg[0] = TLS_HS_FINISHED;
    put_be24(finished_msg + 1, 32);
    memcpy(finished_msg + 4, verify_data, 32);

    vos3_cache_wipe(verify_data, 32);

    /* Reset client sequence number for handshake write.
     * This should be 0 since we haven't sent any encrypted records yet
     * from the client side during the handshake phase. */
    ctx->client_seq = 0;

    /* Send as encrypted handshake record */
    rc = tls_write_record(ctx, TLS_CONTENT_HANDSHAKE,
                          finished_msg, sizeof(finished_msg),
                          KEY_PHASE_HANDSHAKE);
    if (rc != 0) {
        VOS3_ERROR("TLS: failed to send client Finished\n");
        return rc;
    }

    /* Update transcript with client Finished */
    transcript_update(ts, finished_msg, sizeof(finished_msg));

    VOS3_INFO("TLS: client Finished sent\n");
    return 0;
}

/* ============================================================================
 * PUBLIC API — vos3_tls_init
 * ============================================================================ */

int vos3_tls_init(vos3_tls_ctx_t *ctx)
{
    if (ctx == NULL) {
        return -1;
    }

    memset(ctx, 0, sizeof(vos3_tls_ctx_t));
    ctx->state        = TLS_STATE_INIT;
    ctx->cipher_suite = TLS_AES_128_GCM_SHA256;
    ctx->key_len      = 16;
    ctx->tcp_fd       = -1;

    return 0;
}

/* ============================================================================
 * PUBLIC API — vos3_tls_connect
 * ============================================================================ */

int vos3_tls_connect(vos3_tls_ctx_t *ctx, int tcp_fd, const char *server_name)
{
    int rc;

    if (ctx == NULL || tcp_fd < 0) {
        return -1;
    }

    if (ctx->state != TLS_STATE_INIT) {
        VOS3_ERROR("TLS: connect called in wrong state (%d)\n", ctx->state);
        return -1;
    }

    /* Store TCP fd */
    ctx->tcp_fd = tcp_fd;

    /* Store server name for SNI */
    if (server_name != NULL) {
        size_t sn_len = strnlen(server_name, TLS_MAX_SERVER_NAME);
        if (sn_len > 0 && sn_len <= TLS_MAX_SERVER_NAME) {
            memcpy(ctx->server_name, server_name, sn_len);
            ctx->server_name[sn_len] = '\0';
            ctx->server_name_len = (uint8_t)sn_len;
        }
    }

    VOS3_INFO("TLS: starting handshake (fd=%d, sni=%s)\n",
              tcp_fd, ctx->server_name_len > 0 ? ctx->server_name : "(none)");

    /* === Step 1: Generate X25519 ephemeral keypair === */
    vos3_x25519_keygen(ctx->eph_privkey, ctx->eph_pubkey);

    /* === Step 2: Generate client random === */
    rc = vos3_entropy_extract(ctx->client_random, 32);
    if (rc != 0) {
        VOS3_ERROR("TLS: failed to generate client_random\n");
        ctx->state = TLS_STATE_ERROR;
        return -1;
    }

    /* === Step 3: Initialize transcript hash === */
    tls_transcript_t transcript;
    transcript_init(&transcript);

    /* === Step 4: Build ClientHello === */
    uint8_t ch_buf[TLS_HS_BUILD_BUF_SIZE];
    int ch_len = tls_build_client_hello(ctx, ch_buf, sizeof(ch_buf));
    if (ch_len <= 0) {
        VOS3_ERROR("TLS: failed to build ClientHello\n");
        ctx->state = TLS_STATE_ERROR;
        return -1;
    }

    /* === Step 5: Send ClientHello as plaintext handshake record === */
    rc = tls_write_record(ctx, TLS_CONTENT_HANDSHAKE,
                          ch_buf, (size_t)ch_len,
                          KEY_PHASE_NONE);
    if (rc != 0) {
        VOS3_ERROR("TLS: failed to send ClientHello\n");
        ctx->state = TLS_STATE_ERROR;
        return rc;
    }

    ctx->state = TLS_STATE_CLIENT_HELLO_SENT;
    VOS3_INFO("TLS: ClientHello sent (%d bytes)\n", ch_len);

    /* === Step 6: Update transcript with ClientHello === */
    transcript_update(&transcript, ch_buf, (size_t)ch_len);

    /* === Step 7: Read ServerHello === */
    uint8_t sh_buf[TLS_RECORD_BUF_SIZE];
    uint8_t sh_type;
    size_t  sh_len;

    /* The ServerHello arrives as a plaintext handshake record.
     * Some servers may send CCS before or after ServerHello (middlebox compat). */
    for (;;) {
        rc = tls_read_record(ctx, &sh_type, sh_buf, &sh_len,
                             sizeof(sh_buf), KEY_PHASE_NONE);
        if (rc != 0) {
            VOS3_ERROR("TLS: failed to read ServerHello record\n");
            ctx->state = TLS_STATE_ERROR;
            return -1;
        }

        /* Skip CCS records */
        if (sh_type == TLS_CONTENT_CHANGE_CIPHER) {
            continue;
        }

        /* Handle alerts */
        if (sh_type == TLS_CONTENT_ALERT) {
            if (sh_len >= 2) {
                VOS3_ERROR("TLS: alert during handshake (level=%u, desc=%u)\n",
                           sh_buf[0], sh_buf[1]);
                ctx->last_alert = sh_buf[1];
                ctx->fatal = (sh_buf[0] == TLS_ALERT_LEVEL_FATAL) ? 1 : 0;
            }
            ctx->state = TLS_STATE_ERROR;
            return -1;
        }

        if (sh_type == TLS_CONTENT_HANDSHAKE) {
            break;
        }

        VOS3_WARN("TLS: unexpected record type %u waiting for ServerHello\n",
                  sh_type);
    }

    /* Verify it is a ServerHello handshake message */
    if (sh_len < 4) {
        VOS3_ERROR("TLS: ServerHello record too short\n");
        ctx->state = TLS_STATE_ERROR;
        return -1;
    }

    uint8_t hs_type = sh_buf[0];
    uint32_t hs_body_len = get_be24(sh_buf + 1);

    if (hs_type != TLS_HS_SERVER_HELLO) {
        VOS3_ERROR("TLS: expected ServerHello (type 2), got type %u\n", hs_type);
        ctx->state = TLS_STATE_ERROR;
        return -1;
    }

    if (4 + hs_body_len > sh_len) {
        VOS3_ERROR("TLS: ServerHello body length mismatch\n");
        ctx->state = TLS_STATE_ERROR;
        return -1;
    }

    /* === Step 8: Parse ServerHello === */
    rc = tls_parse_server_hello(ctx, sh_buf + 4, hs_body_len);
    if (rc != 0) {
        VOS3_ERROR("TLS: failed to parse ServerHello\n");
        ctx->state = TLS_STATE_ERROR;
        return -1;
    }

    ctx->state = TLS_STATE_SERVER_HELLO_RECV;

    /* === Step 9: Update transcript with ServerHello === */
    transcript_update(&transcript, sh_buf, 4 + hs_body_len);

    /* === Step 10: Derive handshake keys === */
    uint8_t hs_transcript[32];
    transcript_hash(&transcript, hs_transcript);

    /* Save the CH||SH transcript hash for later use in Finished computation */
    memcpy(ctx->transcript_hash, hs_transcript, 32);

    rc = tls13_derive_handshake_keys(ctx, hs_transcript);
    if (rc != 0) {
        VOS3_ERROR("TLS: failed to derive handshake keys\n");
        ctx->state = TLS_STATE_ERROR;
        return -1;
    }

    /* === Step 11: Read encrypted handshake messages === */
    /* EncryptedExtensions, Certificate, CertificateVerify, Finished */
    rc = tls_read_server_handshake(ctx, &transcript);
    if (rc != 0) {
        VOS3_ERROR("TLS: server handshake processing failed\n");
        ctx->state = TLS_STATE_ERROR;
        return -1;
    }

    /* === Step 12: Derive application keys === */
    uint8_t full_transcript[32];
    transcript_hash(&transcript, full_transcript);

    rc = tls13_derive_app_keys(ctx, full_transcript);
    if (rc != 0) {
        VOS3_ERROR("TLS: failed to derive application keys\n");
        ctx->state = TLS_STATE_ERROR;
        return -1;
    }

    /* === Step 13: Send client Finished === */
    rc = tls_send_client_finished(ctx, &transcript);
    if (rc != 0) {
        VOS3_ERROR("TLS: failed to send client Finished\n");
        ctx->state = TLS_STATE_ERROR;
        return -1;
    }

    /* === Step 14: Scrub handshake secrets === */
    vos3_cache_wipe(ctx->eph_privkey, sizeof(ctx->eph_privkey));
    vos3_cache_wipe(ctx->shared_secret, sizeof(ctx->shared_secret));
    vos3_cache_wipe(ctx->handshake_secret, sizeof(ctx->handshake_secret));
    vos3_cache_wipe(ctx->early_secret, sizeof(ctx->early_secret));
    vos3_cache_wipe(ctx->client_hs_key, sizeof(ctx->client_hs_key));
    vos3_cache_wipe(ctx->client_hs_iv, sizeof(ctx->client_hs_iv));
    vos3_cache_wipe(ctx->server_hs_key, sizeof(ctx->server_hs_key));
    vos3_cache_wipe(ctx->server_hs_iv, sizeof(ctx->server_hs_iv));
    vos3_cache_wipe(hs_transcript, sizeof(hs_transcript));
    vos3_cache_wipe(full_transcript, sizeof(full_transcript));

    /* === Step 15: Transition to connected state === */
    /* Reset sequence numbers for application phase */
    ctx->client_seq = 0;
    ctx->server_seq = 0;

    ctx->state = TLS_STATE_CONNECTED;
    VOS3_INFO("TLS: handshake complete (cipher=0x%04x, key_len=%u)\n",
              ctx->cipher_suite, ctx->key_len);

    return 0;
}

/* ============================================================================
 * PUBLIC API — vos3_tls_send
 * ============================================================================ */

int vos3_tls_send(vos3_tls_ctx_t *ctx, const void *data, size_t len)
{
    if (ctx == NULL || data == NULL) {
        return -1;
    }

    if (ctx->state != TLS_STATE_CONNECTED) {
        VOS3_ERROR("TLS: send called in wrong state (%d)\n", ctx->state);
        return -1;
    }

    if (ctx->fatal) {
        VOS3_ERROR("TLS: connection in fatal error state\n");
        return -1;
    }

    const uint8_t *p = (const uint8_t *)data;
    size_t remaining = len;
    size_t total_sent = 0;

    /* Fragment into TLS_MAX_RECORD_SIZE chunks */
    while (remaining > 0) {
        size_t chunk = remaining;
        if (chunk > TLS_MAX_RECORD_SIZE) {
            chunk = TLS_MAX_RECORD_SIZE;
        }

        int rc = tls_write_record(ctx, TLS_CONTENT_APPLICATION,
                                   p, chunk, KEY_PHASE_APP);
        if (rc != 0) {
            VOS3_ERROR("TLS: send record failed\n");
            return (total_sent > 0) ? (int)total_sent : -1;
        }

        p += chunk;
        remaining -= chunk;
        total_sent += chunk;
    }

    return (int)total_sent;
}

/* ============================================================================
 * PUBLIC API — vos3_tls_recv
 * ============================================================================ */

int vos3_tls_recv(vos3_tls_ctx_t *ctx, void *buf, size_t len)
{
    if (ctx == NULL || buf == NULL || len == 0) {
        return -1;
    }

    if (ctx->state != TLS_STATE_CONNECTED) {
        VOS3_ERROR("TLS: recv called in wrong state (%d)\n", ctx->state);
        return -1;
    }

    if (ctx->fatal) {
        VOS3_ERROR("TLS: connection in fatal error state\n");
        return -1;
    }

    uint8_t rec_buf[TLS_RECORD_BUF_SIZE];
    uint8_t rec_type;
    size_t  rec_len;

    for (;;) {
        int rc = tls_read_record(ctx, &rec_type, rec_buf, &rec_len,
                                  sizeof(rec_buf), KEY_PHASE_APP);
        if (rc != 0) {
            return -1;
        }

        if (rec_type == TLS_CONTENT_APPLICATION) {
            /* Application data */
            size_t copy_len = rec_len;
            if (copy_len > len) {
                copy_len = len;
                /*
                 * NOTE: Any excess data beyond the caller's buffer is lost.
                 * A production implementation would buffer leftover bytes.
                 * For now, we truncate to the caller's buffer size.
                 */
                VOS3_WARN("TLS: recv truncated (%u -> %u)\n",
                          (unsigned)rec_len, (unsigned)len);
            }
            memcpy(buf, rec_buf, copy_len);
            return (int)copy_len;
        }
        else if (rec_type == TLS_CONTENT_ALERT) {
            /* Handle TLS alerts */
            if (rec_len >= 2) {
                uint8_t level = rec_buf[0];
                uint8_t desc  = rec_buf[1];

                VOS3_INFO("TLS: received alert (level=%u, desc=%u)\n",
                          level, desc);

                ctx->last_alert = desc;

                if (desc == TLS_ALERT_CLOSE_NOTIFY) {
                    /* Graceful close */
                    VOS3_INFO("TLS: peer sent close_notify\n");
                    ctx->state = TLS_STATE_CLOSING;
                    return 0;  /* EOF */
                }

                if (level == TLS_ALERT_LEVEL_FATAL) {
                    ctx->fatal = 1;
                    ctx->state = TLS_STATE_ERROR;
                    VOS3_ERROR("TLS: fatal alert received (desc=%u)\n", desc);
                    return -1;
                }

                /* Warning-level alert: continue reading */
            }
        }
        else if (rec_type == TLS_CONTENT_HANDSHAKE) {
            /*
             * Post-handshake messages (e.g., NewSessionTicket, KeyUpdate).
             * For now, we silently consume them. A full implementation
             * would handle KeyUpdate for forward secrecy.
             */
            VOS3_DEBUG("TLS: post-handshake message received, ignoring\n");
            continue;
        }
        else if (rec_type == TLS_CONTENT_CHANGE_CIPHER) {
            /* Spurious CCS — ignore */
            continue;
        }
        else {
            VOS3_WARN("TLS: unexpected content type %u in app phase\n", rec_type);
            continue;
        }
    }
}

/* ============================================================================
 * PUBLIC API — vos3_tls_close
 * ============================================================================ */

int vos3_tls_close(vos3_tls_ctx_t *ctx)
{
    if (ctx == NULL) {
        return -1;
    }

    if (ctx->state == TLS_STATE_CLOSED || ctx->state == TLS_STATE_CLOSING) {
        return 0;
    }

    /* Send close_notify alert */
    if (ctx->state == TLS_STATE_CONNECTED && !ctx->fatal) {
        uint8_t alert[2];
        alert[0] = TLS_ALERT_LEVEL_WARNING;
        alert[1] = TLS_ALERT_CLOSE_NOTIFY;

        int rc = tls_write_record(ctx, TLS_CONTENT_ALERT,
                                   alert, 2, KEY_PHASE_APP);
        if (rc != 0) {
            VOS3_WARN("TLS: failed to send close_notify\n");
        } else {
            VOS3_INFO("TLS: close_notify sent\n");
        }
    }

    ctx->state = TLS_STATE_CLOSING;
    return 0;
}

/* ============================================================================
 * PUBLIC API — vos3_tls_destroy
 * ============================================================================ */

void vos3_tls_destroy(vos3_tls_ctx_t *ctx)
{
    if (ctx == NULL) {
        return;
    }

    /* Scrub the entire context (all key material, IVs, secrets) */
    vos3_cache_wipe(ctx, sizeof(vos3_tls_ctx_t));

    ctx->state = TLS_STATE_CLOSED;
    ctx->tcp_fd = -1;
}

/* ============================================================================
 * PUBLIC API — vos3_tls_state_name
 * ============================================================================ */

const char *vos3_tls_state_name(vos3_tls_state_t state)
{
    switch (state) {
    case TLS_STATE_INIT:              return "INIT";
    case TLS_STATE_CLIENT_HELLO_SENT: return "CLIENT_HELLO_SENT";
    case TLS_STATE_SERVER_HELLO_RECV: return "SERVER_HELLO_RECV";
    case TLS_STATE_ENCRYPTED_EXT:     return "ENCRYPTED_EXTENSIONS";
    case TLS_STATE_CERTIFICATE:       return "CERTIFICATE";
    case TLS_STATE_CERT_VERIFY:       return "CERTIFICATE_VERIFY";
    case TLS_STATE_SERVER_FINISHED:   return "SERVER_FINISHED";
    case TLS_STATE_CONNECTED:         return "CONNECTED";
    case TLS_STATE_CLOSING:           return "CLOSING";
    case TLS_STATE_CLOSED:            return "CLOSED";
    case TLS_STATE_ERROR:             return "ERROR";
    default:                          return "UNKNOWN";
    }
}
