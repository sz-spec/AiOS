/**
 * @file tpm2.h
 * @brief VOS3 TPM 2.0 PCR Binding — Arrow of Time Invariant
 *
 * Binds VOS3's kernel integrity measurement to hardware TPM 2.0 PCRs.
 * Once PCR[0] is extended at boot, no software can reset it without
 * a full TPM clear (requiring physical presence or owner authorization).
 *
 * Arrow of Time invariant:
 *   PCR[0] ← SHA-256(ktext_hash || kaslr_slide)  — written ONCE at boot
 *   PCR[8] ← MMR root hash                        — extended periodically
 *
 * These values are monotone: they can only change forward (by extension),
 * never backward (by reset without physical presence).
 *
 * @version 1.0.0
 * @date 2026-04-24
 *
 * @note Full CRB implementation requires ACPI TPM2 table + swtpm or
 *       physical TPM. This header defines the interface; the .c file
 *       provides a safe no-op stub when no TPM is present.
 */

#ifndef VOS3_TPM2_H
#define VOS3_TPM2_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * TPM 2.0 Constants (TCG TPM 2.0 Part 2, Section 6)
 * ============================================================================ */

#define TPM2_CC_STARTUP             0x00000144U
#define TPM2_CC_FLUSH_CONTEXT       0x00000165U
#define TPM2_CC_START_AUTH_SESSION  0x00000176U
#define TPM2_CC_GET_RANDOM          0x0000017BU
#define TPM2_CC_PCR_EXTEND          0x00000182U
#define TPM2_SU_CLEAR               0x0000U
#define TPM2_SU_STATE               0x0001U

#define TPM2_ALG_SHA256             0x000BU
#define TPM2_ALG_NULL               0x0010U
#define TPM2_SHA256_SIZE            32U

/* Permanent handles (TPM 2.0 Part 2, §6.5) */
#define TPM_RH_NULL                 0x40000007U
#define TPM_RS_PW                   0x40000009U  /* Password session (legacy) */

/* Session types (TPM 2.0 Part 1, §19.4) */
#define TPM_SE_HMAC                 0x00U
#define TPM_SE_POLICY               0x01U
#define TPM_SE_TRIAL                0x03U

/* sessionAttributes byte (TPMA_SESSION) */
#define TPMA_SESSION_CONTINUE       0x01U
#define TPMA_SESSION_AUDIT_EXCLUSIVE 0x02U
#define TPMA_SESSION_AUDIT_RESET    0x04U
#define TPMA_SESSION_DECRYPT        0x20U
#define TPMA_SESSION_ENCRYPT        0x40U
#define TPMA_SESSION_AUDIT          0x80U

/* Session nonce size — VOS3 uses full SHA-256-sized nonces (32B) for HMAC sessions */
#define TPM2_NONCE_SIZE             32U

/* PCR indices used by VOS3 */
#define VOS3_TPM2_PCR_KTEXT         0U   /* SHA-256(ktext_hash || kaslr_slide) — boot-time */
#define VOS3_TPM2_PCR_MMR           8U   /* MMR root hash — periodic extension */

/* ============================================================================
 * CRB (Command Response Buffer) interface constants
 * ============================================================================ */

/* ACPI TPM2 table signature */
#define TPM2_ACPI_SIG           "TPM2"

/* CRB Control Area register offsets, relative to the CRB control-area base
 * address parsed from the ACPI TPM2 table.
 * Source: TCG PC Client Platform TPM Profile (PTP) Spec, §6.5.2 + the QEMU
 * tpm_crb.c reference implementation (qemu/hw/tpm/tpm_crb.c). */

/* Locality block (0x00..0x0F) */
#define CRB_LOC_STATE           0x000U  /* Locality state — bit 1 = LocalityActive */
#define CRB_LOC_CTRL            0x008U  /* Locality control — bit 0 = RequestAccess, bit 1 = Relinquish */
#define CRB_LOC_STS             0x00CU  /* Locality status — bit 0 = TPM Established */

/* Interface ID (0x30..0x37) */
#define CRB_INTF_ID_LO          0x030U
#define CRB_INTF_ID_HI          0x034U

/* Control Request / Status / Cancel / Start (0x40..0x4F) */
#define CRB_CTRL_REQ            0x040U  /* bit 0 = cmdReady, bit 1 = goIdle */
#define CRB_CTRL_STS            0x044U  /* bit 0 = tpmSts (response ready), bit 1 = tpmIdle */
#define CRB_CTRL_CANCEL         0x048U
#define CRB_CTRL_START          0x04CU  /* bit 0 = start (write to begin command) */

/* Interrupt control (0x50..0x57) — not used by this driver */
#define CRB_CTRL_INT_ENABLE     0x050U
#define CRB_CTRL_INT_STS        0x054U

/* Command/response buffer descriptors (0x58..0x6F) */
#define CRB_CMD_SIZE            0x058U  /* Command buffer size */
#define CRB_CMD_ADDR_LO         0x05CU  /* Command buffer phys addr low */
#define CRB_CMD_ADDR_HI         0x060U  /* Command buffer phys addr high */
#define CRB_RSP_SIZE            0x064U  /* Response buffer size */
#define CRB_RSP_ADDR            0x068U  /* Response buffer phys addr (64-bit) */

/* Bit fields */
#define CRB_LOC_STATE_ACTIVE    (1U << 1)
#define CRB_LOC_CTRL_REQUEST    (1U << 0)
#define CRB_LOC_CTRL_RELINQUISH (1U << 1)
#define CRB_REQ_CMD_READY       (1U << 0)
#define CRB_REQ_GO_IDLE         (1U << 1)
#define CRB_STS_TPM_STS         (1U << 0)
#define CRB_STS_TPM_IDLE        (1U << 1)
#define CRB_CTRL_START_BIT      (1U << 0)
/* Legacy alias kept for callers that referenced the old name */
#define CRB_STS_IDLE            CRB_STS_TPM_IDLE

/* ============================================================================
 * API
 * ============================================================================ */

/**
 * Initialize TPM 2.0 CRB interface.
 *
 * Locates the ACPI TPM2 table, maps CRB base MMIO, verifies locality 0,
 * and issues TPM2_CC_Startup(TPM_SU_CLEAR).
 *
 * Returns 0 on success, -1 if no TPM present (safe no-op), negative errno
 * on CRB protocol error.
 */
int tpm2_init(void);

/**
 * Extend a PCR with a 32-byte measurement.
 *
 * Issues TPM2_CC_PCRExtend to the CRB through a salted HMAC session
 * (auto-started on first call). The new PCR value is:
 *   PCR[n] ← SHA-256(PCR[n] || measurement)
 *
 * Authentication uses the salted HMAC session installed by
 * tpm2_start_hmac_session(); the legacy TPM_RS_PW (plaintext password)
 * path is no longer used. If the session has been flushed, this call
 * automatically restarts one before issuing the extend.
 *
 * This operation is irreversible without TPM owner clear + physical presence.
 *
 * @param pcr_index   0–23 (VOS3 uses 0 and 8)
 * @param measurement 32-byte hash to extend with
 * @return 0 on success, -1 if TPM not initialized, negative errno on error
 */
int tpm2_extend_pcr(uint8_t pcr_index, const uint8_t measurement[TPM2_SHA256_SIZE]);

/**
 * Return 1 if a TPM 2.0 was detected and initialized, 0 otherwise.
 */
int tpm2_is_present(void);

/* ============================================================================
 * Salted HMAC Session API (Last-Fortress hardware hardening)
 * ============================================================================
 *
 * Replaces the previous TPM_RS_PW (plaintext password) authorization path
 * for PCR_Extend with a TPM 2.0 HMAC session bound to per-command nonces
 * and salted with TPM-provided entropy (TPM2_GetRandom). Replay protection
 * comes from a fresh nonceTPM rotated by the TPM on every command;
 * parameter integrity is bound by cpHash inside the auth HMAC.
 *
 * Architectural caveat: strict TPM 2.0 spec salting transports the salt to
 * the TPM encrypted with the TPM's primary-key public part (RSA-OAEP or
 * ECDH on NIST P-256). VOS3's freestanding kernel crypto inventory is
 * SHA-256/HMAC + X25519 — no NIST P-256, no RSA. This implementation
 * therefore uses a NULL-tpmKey wire-format session (encryptedSalt empty)
 * but mixes TPM2_GetRandom output into a kernel-side derived "session
 * salt" used by tpm2_session_salt_get() for downstream key derivations
 * (see backend/sec/hybrid_key.c). The wire HMAC remains spec-compliant.
 */

/* Get random bytes from the TPM's true RNG via TPM2_CC_GetRandom.
 * Issued without sessions (TPM_ST_NO_SESSIONS), no auth required.
 *
 * @param out  Destination buffer
 * @param len  Bytes requested (max TPM2_SHA256_SIZE per request — TPM
 *             returns up to digest-size of the named hash algorithm)
 * @return 0 on success, -1 if TPM unavailable or short read */
int tpm2_get_random(uint8_t *out, size_t len);

/* Start a salted HMAC session for PCR auth. Issues TPM2_StartAuthSession
 * with sessionType=HMAC, tpmKey=TPM_RH_NULL, bind=TPM_RH_NULL,
 * symmetric=TPM_ALG_NULL, authHash=SHA-256. Caller nonce is fresh kernel
 * entropy; the salt material from TPM2_GetRandom is mixed into the
 * kernel-side session-salt buffer (not the wire encryptedSalt).
 *
 * Idempotent: returns 0 if a session is already active. The active
 * session is reused across PCR_Extend calls until tpm2_flush_hmac_session().
 *
 * @return 0 on success, -1 if TPM unavailable or StartAuthSession failed */
int tpm2_start_hmac_session(void);

/* Flush the active HMAC session via TPM2_CC_FlushContext. After this the
 * next tpm2_extend_pcr() will auto-start a fresh session. */
void tpm2_flush_hmac_session(void);

/* Return 1 if an HMAC session is currently installed. */
int tpm2_session_is_active(void);

/* Copy out the kernel-side session salt (32B) — the locally-derived
 * mixture of TPM2_GetRandom output and kernel entropy used as additional
 * keying material for hybrid identity key derivation. Returns -1 if
 * no session is active.
 *
 * This is NOT the TPM's session_key — that one is empty per spec when
 * tpmKey/bind are NULL. This is VOS3's defense-in-depth supplement. */
int tpm2_session_salt_get(uint8_t out[TPM2_SHA256_SIZE]);

#endif /* VOS3_TPM2_H */
