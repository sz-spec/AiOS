/**
 * @file tls.h
 * @brief VOS3 TLS 1.3 — Deep-Kernel Transport Security (RFC 8446)
 *
 * @details Minimal TLS 1.3 client for kernel-space HTTPS:
 *          - TLS 1.3 ONLY (no fallback to 1.2/1.1/1.0)
 *          - Primary cipher: TLS_AES_256_GCM_SHA384 (0x13,0x02)
 *          - Fallback cipher: TLS_AES_128_GCM_SHA256 (0x13,0x01)
 *          - Key exchange: X25519 ephemeral (RFC 7748)
 *          - Signature: RSA-PSS-RSAE-SHA256 + ECDSA-SECP256R1-SHA256
 *          - Post-handshake: secret scrub via vos3_cache_wipe()
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 7.2 — Sovereign Crypto Shield (v35.1)
 */

#ifndef VOS3_TLS_H
#define VOS3_TLS_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * TLS 1.3 CONSTANTS
 * ============================================================================ */

/** @brief TLS record content types */
#define TLS_CONTENT_CHANGE_CIPHER   20U
#define TLS_CONTENT_ALERT           21U
#define TLS_CONTENT_HANDSHAKE       22U
#define TLS_CONTENT_APPLICATION     23U

/** @brief TLS versions */
#define TLS_VERSION_12              0x0303U  /**< TLS 1.2 (legacy in record layer) */
#define TLS_VERSION_13              0x0304U  /**< TLS 1.3 (in supported_versions) */

/** @brief TLS 1.3 handshake types */
#define TLS_HS_CLIENT_HELLO         1U
#define TLS_HS_SERVER_HELLO         2U
#define TLS_HS_NEW_SESSION_TICKET   4U
#define TLS_HS_ENCRYPTED_EXTENSIONS 8U
#define TLS_HS_CERTIFICATE          11U
#define TLS_HS_CERTIFICATE_VERIFY   15U
#define TLS_HS_FINISHED             20U

/** @brief TLS cipher suites */
#define TLS_AES_128_GCM_SHA256      0x1301U
#define TLS_AES_256_GCM_SHA384      0x1302U

/** @brief TLS named groups */
#define TLS_GROUP_X25519            0x001DU

/** @brief Hybrid post-quantum group — X25519 + ML-KEM-768.
 *
 * IANA codepoint 0x11EC, per IETF ``draft-ietf-tls-ecdhe-mlkem-04``
 * (Feb 2026, intended Standards Track, expires Aug 2026). OpenSSL 3.5
 * and Go 1.24 both already negotiate this group by default.
 *
 * Wire encoding for a key_share entry under this group:
 *   bytes  0..31    X25519 ephemeral public key
 *   bytes 32..1215  ML-KEM-768 encapsulation key (PK_BYTES = 1184)
 *   total           1216 bytes
 *
 * The shared-secret output is the byte concatenation:
 *   ML-KEM-768 shared (32) || X25519 shared (32)  =  64 bytes
 * fed into the TLS 1.3 key schedule as the "DHE" input (i.e., it
 * replaces the X25519-only 32-byte secret in Derive-Secret).
 *
 * Until the in-tree ML-KEM-768 lattice operations land (Stage 14.B.2,
 * tracked in docs/POST_QUANTUM_INVENTORY.md §4.1), this kernel will
 * OFFER the group in supported_groups but DECLINE to use it in
 * key_share — the named-group offer lets a hybrid-capable peer record
 * our readiness; the key_share fallback keeps handshakes succeeding.
 */
#define TLS_GROUP_X25519MLKEM768    0x11ECU

/** Size of an X25519MLKEM768 key_share entry, in bytes. */
#define TLS_X25519MLKEM768_SHARE_BYTES 1216U

/** @brief TLS signature algorithms */
#define TLS_SIG_RSA_PSS_SHA256      0x0804U
#define TLS_SIG_ECDSA_SHA256        0x0403U
#define TLS_SIG_RSA_PKCS1_SHA256    0x0401U

/** @brief TLS extension types */
#define TLS_EXT_SERVER_NAME         0U
#define TLS_EXT_SUPPORTED_GROUPS    10U
#define TLS_EXT_SIGNATURE_ALGORITHMS 13U
#define TLS_EXT_SUPPORTED_VERSIONS  43U
#define TLS_EXT_KEY_SHARE           51U

/** @brief TLS alert descriptions */
#define TLS_ALERT_CLOSE_NOTIFY      0U
#define TLS_ALERT_UNEXPECTED_MSG    10U
#define TLS_ALERT_BAD_RECORD_MAC    20U
#define TLS_ALERT_HANDSHAKE_FAILURE 40U
#define TLS_ALERT_DECODE_ERROR      50U
#define TLS_ALERT_PROTOCOL_VERSION  70U
#define TLS_ALERT_INTERNAL_ERROR    80U

/** @brief TLS configuration limits */
#define TLS_MAX_RECORD_SIZE         16384U  /**< Max plaintext per record */
#define TLS_MAX_CIPHERTEXT_SIZE     16640U  /**< Max ciphertext (record + tag + overhead) */
#define TLS_HANDSHAKE_BUF_SIZE      8192U   /**< Handshake transcript buffer */
#define TLS_MAX_SERVER_NAME         255U    /**< Max SNI hostname length */

/* ============================================================================
 * TLS 1.3 CONNECTION STATE
 * ============================================================================ */

/** @brief TLS handshake state machine */
typedef enum vos3_tls_state {
    TLS_STATE_INIT              = 0,
    TLS_STATE_CLIENT_HELLO_SENT = 1,
    TLS_STATE_SERVER_HELLO_RECV = 2,
    TLS_STATE_ENCRYPTED_EXT     = 3,
    TLS_STATE_CERTIFICATE       = 4,
    TLS_STATE_CERT_VERIFY       = 5,
    TLS_STATE_SERVER_FINISHED   = 6,
    TLS_STATE_CONNECTED         = 7,  /**< Handshake complete, app data flowing */
    TLS_STATE_CLOSING           = 8,
    TLS_STATE_CLOSED            = 9,
    TLS_STATE_ERROR             = 10
} vos3_tls_state_t;

/**
 * @brief TLS 1.3 connection context
 *
 * Holds all state for a single TLS connection: keys, nonces, sequence
 * numbers, handshake transcript hash, and the underlying TCP PCB index.
 */
typedef struct vos3_tls_ctx {
    /* === Connection state === */
    vos3_tls_state_t    state;
    uint16_t            cipher_suite;     /**< Negotiated cipher suite */
    int                 tcp_fd;           /**< Underlying TCP socket fd */

    /* === X25519 ephemeral keys === */
    uint8_t             eph_privkey[32];  /**< Our ephemeral private key */
    uint8_t             eph_pubkey[32];   /**< Our ephemeral public key */
    uint8_t             peer_pubkey[32];  /**< Peer's ephemeral public key */
    uint8_t             shared_secret[32];/**< X25519 shared secret */

    /* === TLS 1.3 key schedule (RFC 8446 §7.1) === */
    uint8_t             early_secret[32];
    uint8_t             handshake_secret[32];
    uint8_t             master_secret[32];

    /* === Traffic keys === */
    uint8_t             client_hs_key[32];  /**< Client handshake traffic key */
    uint8_t             client_hs_iv[12];   /**< Client handshake traffic IV */
    uint8_t             server_hs_key[32];  /**< Server handshake traffic key */
    uint8_t             server_hs_iv[12];   /**< Server handshake traffic IV */
    uint8_t             client_app_key[32]; /**< Client application traffic key */
    uint8_t             client_app_iv[12];  /**< Client application traffic IV */
    uint8_t             server_app_key[32]; /**< Server application traffic key */
    uint8_t             server_app_iv[12];  /**< Server application traffic IV */

    /* === Sequence numbers (for nonce derivation) === */
    uint64_t            client_seq;
    uint64_t            server_seq;

    /* === Handshake transcript hash === */
    uint8_t             transcript_hash[32]; /**< Running SHA-256 of handshake */
    uint8_t             hs_buf[TLS_HANDSHAKE_BUF_SIZE]; /**< Handshake message accumulator */
    uint16_t            hs_buf_len;

    /* === Client random === */
    uint8_t             client_random[32];

    /* === SNI hostname === */
    char                server_name[TLS_MAX_SERVER_NAME + 1];
    uint8_t             server_name_len;

    /* === Error tracking === */
    uint8_t             last_alert;
    uint8_t             fatal;

    /* === Key size tracking (16 for AES-128, 32 for AES-256) === */
    uint8_t             key_len;
    uint8_t             _padding[3];
} vos3_tls_ctx_t;

/* ============================================================================
 * TLS 1.3 API
 * ============================================================================ */

/**
 * @brief Initialize a TLS context
 *
 * @param[out] ctx  TLS context to initialize
 * @return 0 on success, negative error code on failure
 */
int vos3_tls_init(vos3_tls_ctx_t *ctx);

/**
 * @brief Connect to a TLS 1.3 server
 *
 * Performs the complete TLS 1.3 handshake:
 * 1. Generate X25519 ephemeral keypair via vos3_entropy_extract()
 * 2. Send ClientHello (TLS 1.3 only, X25519, AES-256-GCM)
 * 3. Receive + process ServerHello
 * 4. Derive handshake keys
 * 5. Decrypt + verify EncryptedExtensions, Certificate, CertificateVerify, Finished
 * 6. Send client Finished
 * 7. Derive application traffic keys
 * 8. Scrub handshake secrets via vos3_cache_wipe()
 *
 * SIMD Guard: Wraps AES-NI operations in vos3_fpu_begin/end() to protect
 * AI register state in concurrent inference slots.
 *
 * @param[in,out] ctx         TLS context (must be initialized)
 * @param[in]     tcp_fd      Connected TCP socket file descriptor
 * @param[in]     server_name Server hostname for SNI (NULL for no SNI)
 * @return 0 on success, negative error code on failure
 */
int vos3_tls_connect(vos3_tls_ctx_t *ctx, int tcp_fd, const char *server_name);

/**
 * @brief Send application data over TLS
 *
 * Encrypts data using negotiated cipher and sends as TLS record.
 * Fragments data into 16KB records if necessary.
 *
 * @param[in] ctx   TLS context (must be in CONNECTED state)
 * @param[in] data  Data to send
 * @param[in] len   Data length
 * @return Bytes sent (may be less than len), or negative error
 */
int vos3_tls_send(vos3_tls_ctx_t *ctx, const void *data, size_t len);

/**
 * @brief Receive application data over TLS
 *
 * Reads and decrypts TLS records from the connection.
 *
 * @param[in]  ctx  TLS context (must be in CONNECTED state)
 * @param[out] buf  Buffer to receive into
 * @param[in]  len  Maximum bytes to receive
 * @return Bytes received, 0 on EOF, negative error
 */
int vos3_tls_recv(vos3_tls_ctx_t *ctx, void *buf, size_t len);

/**
 * @brief Close TLS connection gracefully
 *
 * Sends close_notify alert, scrubs all key material.
 *
 * @param[in] ctx  TLS context
 * @return 0 on success
 */
int vos3_tls_close(vos3_tls_ctx_t *ctx);

/**
 * @brief Destroy TLS context and scrub all secrets
 *
 * Zeros and cache-flushes all key material, IVs, and shared secrets.
 * Must be called when done with a TLS connection.
 *
 * @param[in] ctx  TLS context
 */
void vos3_tls_destroy(vos3_tls_ctx_t *ctx);

/**
 * @brief Get TLS connection state name
 * @param[in] state TLS state
 * @return State name string
 */
const char *vos3_tls_state_name(vos3_tls_state_t state);

/* ============================================================================
 * X25519 KEY EXCHANGE (RFC 7748)
 * ============================================================================ */

/**
 * @brief Generate X25519 keypair
 *
 * Uses vos3_entropy_extract() for private key generation.
 * Applies clamping per RFC 7748 §5.
 *
 * @param[out] privkey  32-byte private key
 * @param[out] pubkey   32-byte public key (privkey * basepoint)
 */
void vos3_x25519_keygen(uint8_t privkey[32], uint8_t pubkey[32]);

/**
 * @brief Compute X25519 shared secret
 *
 * shared = privkey * peer_pubkey (scalar multiplication on Curve25519)
 *
 * @param[out] shared      32-byte shared secret
 * @param[in]  privkey     Our 32-byte private key
 * @param[in]  peer_pubkey Peer's 32-byte public key
 * @return 0 on success, -1 if result is all-zeros (invalid peer key)
 */
int vos3_x25519_shared(uint8_t shared[32],
                        const uint8_t privkey[32],
                        const uint8_t peer_pubkey[32]);

/* ============================================================================
 * AES-256-GCM AUTHENTICATED ENCRYPTION (NIST SP 800-38D)
 * ============================================================================ */

/** @brief AES-GCM context */
typedef struct vos3_aes_gcm_ctx {
    uint8_t     round_keys[240];  /**< AES-256 expanded key schedule (15 rounds) */
    uint8_t     H[16];            /**< GCM hash subkey H = AES(K, 0^128) */
    uint8_t     key_len;          /**< Key length: 16 (AES-128) or 32 (AES-256) */
    uint8_t     rounds;           /**< Number of AES rounds: 10 or 14 */
    uint8_t     use_aesni;        /**< 1 = AES-NI available */
    uint8_t     _padding;
} vos3_aes_gcm_ctx_t;

/**
 * @brief Initialize AES-GCM context with key
 *
 * Expands key schedule and computes hash subkey H.
 * Auto-detects AES-NI support.
 *
 * @param[out] ctx      AES-GCM context
 * @param[in]  key      Encryption key
 * @param[in]  key_len  Key length in bytes (16 or 32)
 * @return 0 on success, -1 on invalid key length
 */
int vos3_aes_gcm_init(vos3_aes_gcm_ctx_t *ctx,
                       const uint8_t *key, size_t key_len);

/**
 * @brief AES-GCM encrypt and authenticate
 *
 * @param[in]  ctx         Initialized AES-GCM context
 * @param[in]  iv          12-byte initialization vector (nonce)
 * @param[in]  aad         Additional authenticated data (may be NULL)
 * @param[in]  aad_len     AAD length
 * @param[in]  plaintext   Data to encrypt
 * @param[in]  pt_len      Plaintext length
 * @param[out] ciphertext  Encrypted output (same length as plaintext)
 * @param[out] tag         16-byte authentication tag
 * @return 0 on success
 */
int vos3_aes_gcm_encrypt(const vos3_aes_gcm_ctx_t *ctx,
                          const uint8_t iv[12],
                          const uint8_t *aad, size_t aad_len,
                          const uint8_t *plaintext, size_t pt_len,
                          uint8_t *ciphertext,
                          uint8_t tag[16]);

/**
 * @brief AES-GCM decrypt and verify
 *
 * @param[in]  ctx         Initialized AES-GCM context
 * @param[in]  iv          12-byte initialization vector (nonce)
 * @param[in]  aad         Additional authenticated data (may be NULL)
 * @param[in]  aad_len     AAD length
 * @param[in]  ciphertext  Data to decrypt
 * @param[in]  ct_len      Ciphertext length
 * @param[in]  tag         16-byte authentication tag to verify
 * @param[out] plaintext   Decrypted output (same length as ciphertext)
 * @return 0 on success (tag verified), -1 on authentication failure
 *
 * @note On authentication failure, plaintext buffer is zeroed (no partial output)
 */
int vos3_aes_gcm_decrypt(const vos3_aes_gcm_ctx_t *ctx,
                          const uint8_t iv[12],
                          const uint8_t *aad, size_t aad_len,
                          const uint8_t *ciphertext, size_t ct_len,
                          const uint8_t tag[16],
                          uint8_t *plaintext);

/**
 * @brief Destroy AES-GCM context (scrub key schedule)
 * @param[in] ctx  Context to destroy
 */
void vos3_aes_gcm_destroy(vos3_aes_gcm_ctx_t *ctx);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_TLS_H */
