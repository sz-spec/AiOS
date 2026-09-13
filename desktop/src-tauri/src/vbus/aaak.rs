/// AAAK (Token-Aware Shorthand) codec and HKDF-SHA256 key derivation.
///
/// # V_AAAK frame (0x0D) wire layout
///
/// The V_AAAK frame carries key-exchange material during the post-handshake
/// phase.  It is sent by either side to rotate HMAC session keys using
/// HKDF-SHA256 (RFC 5869).
///
/// | Offset | Size | Field     | Description                            |
/// |--------|------|-----------|----------------------------------------|
/// | 0      | 1    | op        | Operation: 0x01=ROTATE, 0x02=ACK      |
/// | 1      | 32   | salt      | Fresh 32-byte salt for HKDF-Extract    |
/// | 33     | 32   | info      | Context info for HKDF-Expand           |
/// | 65     | ...  | extra     | Reserved / future token shorthand data |
///
/// # HKDF-SHA256 key derivation (RFC 5869)
///
/// Instead of using the raw 32 random handshake bytes as the HMAC session
/// key, both sides derive the key via:
///
/// ```text
/// PRK  = HMAC-SHA256(salt, IKM)           // Extract
/// OKM  = HMAC-SHA256(PRK, info || 0x01)   // Expand (single block = 32 bytes)
/// ```
///
/// This ensures that even if the same random bytes are reused across
/// sessions (extremely unlikely but possible), the derived key differs
/// because the kernel provides a fresh salt in each handshake response.

use hmac::{Hmac, Mac};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

// ---------------------------------------------------------------------------
// AAAK Operation codes
// ---------------------------------------------------------------------------

/// Request key rotation with fresh salt + info.
pub const AAAK_OP_ROTATE: u8 = 0x01;
/// Acknowledge key rotation.
pub const AAAK_OP_ACK: u8 = 0x02;

/// Minimum payload size for a V_AAAK frame: op(1) + salt(32) + info(32) = 65.
const AAAK_MIN_PAYLOAD: usize = 65;

// ---------------------------------------------------------------------------
// HKDF-SHA256 (RFC 5869, single-block expand)
// ---------------------------------------------------------------------------

/// HKDF-Extract: `PRK = HMAC-SHA256(salt, ikm)`.
///
/// If `salt` is empty, uses a 32-byte zero key per RFC 5869 Section 2.2.
fn hkdf_extract(salt: &[u8], ikm: &[u8]) -> [u8; 32] {
    let salt_key = if salt.is_empty() { &[0u8; 32][..] } else { salt };
    let mut mac = HmacSha256::new_from_slice(salt_key)
        .expect("HMAC-SHA256 accepts any key length");
    mac.update(ikm);
    let result = mac.finalize().into_bytes();
    let mut prk = [0u8; 32];
    prk.copy_from_slice(&result);
    prk
}

/// HKDF-Expand: `OKM = HMAC-SHA256(PRK, info || 0x01)`.
///
/// Produces exactly 32 bytes (one HMAC block). This is sufficient for a
/// 256-bit HMAC key.
fn hkdf_expand(prk: &[u8; 32], info: &[u8]) -> [u8; 32] {
    let mut mac = HmacSha256::new_from_slice(prk)
        .expect("HMAC-SHA256 accepts any key length");
    mac.update(info);
    mac.update(&[0x01u8]); // counter byte for T(1)
    let result = mac.finalize().into_bytes();
    let mut okm = [0u8; 32];
    okm.copy_from_slice(&result);
    okm
}

/// Derives a 32-byte session key from raw input keying material (IKM),
/// an optional salt, and context info using HKDF-SHA256.
///
/// # Arguments
///
/// * `ikm` - Input keying material (e.g., the 32 random handshake bytes).
/// * `salt` - Salt from the kernel's handshake response (or empty).
/// * `info` - Context string (e.g., `b"vbus-hmac-session-key"`).
///
/// # Returns
///
/// A 32-byte derived key suitable for use as an HMAC-SHA256 session key.
pub fn hkdf_sha256_derive(ikm: &[u8], salt: &[u8], info: &[u8]) -> [u8; 32] {
    let prk = hkdf_extract(salt, ikm);
    hkdf_expand(&prk, info)
}

// ---------------------------------------------------------------------------
// AAAK Frame Codec
// ---------------------------------------------------------------------------

/// Decoded V_AAAK frame payload.
#[derive(Debug, Clone)]
pub struct AaakFrame {
    /// Operation code (ROTATE or ACK).
    pub op: u8,
    /// 32-byte salt for HKDF-Extract.
    pub salt: [u8; 32],
    /// 32-byte context info for HKDF-Expand.
    pub info: [u8; 32],
    /// Extra data (future use: token shorthand tables, etc.).
    pub extra: Vec<u8>,
}

impl AaakFrame {
    /// Creates a ROTATE request with the given salt and info.
    pub fn new_rotate(salt: [u8; 32], info: [u8; 32]) -> Self {
        Self {
            op: AAAK_OP_ROTATE,
            salt,
            info,
            extra: Vec::new(),
        }
    }

    /// Creates an ACK response echoing the salt and info.
    pub fn new_ack(salt: [u8; 32], info: [u8; 32]) -> Self {
        Self {
            op: AAAK_OP_ACK,
            salt,
            info,
            extra: Vec::new(),
        }
    }

    /// Serializes the AAAK frame into a payload byte vector.
    pub fn encode(&self) -> Vec<u8> {
        let mut buf = Vec::with_capacity(AAAK_MIN_PAYLOAD + self.extra.len());
        buf.push(self.op);
        buf.extend_from_slice(&self.salt);
        buf.extend_from_slice(&self.info);
        buf.extend_from_slice(&self.extra);
        buf
    }

    /// Parses a V_AAAK frame payload.
    ///
    /// Returns `None` if the payload is too short (< 65 bytes).
    pub fn decode(payload: &[u8]) -> Option<Self> {
        if payload.len() < AAAK_MIN_PAYLOAD {
            return None;
        }

        let op = payload[0];
        let mut salt = [0u8; 32];
        salt.copy_from_slice(&payload[1..33]);
        let mut info = [0u8; 32];
        info.copy_from_slice(&payload[33..65]);
        let extra = payload[65..].to_vec();

        Some(Self {
            op,
            salt,
            info,
            extra,
        })
    }
}

// ---------------------------------------------------------------------------
// Handshake salt extraction
// ---------------------------------------------------------------------------

/// Default HKDF info string used when deriving the VBus HMAC session key.
pub const VBUS_HKDF_INFO: &[u8] = b"vbus-hmac-session-key-v3";

/// Extracts the kernel-provided salt from a HANDSHAKE response payload.
///
/// The kernel HANDSHAKE response format is:
/// ```text
/// "HMAC" [optional 32 bytes of salt]
/// ```
///
/// If the response contains "HMAC" followed by at least 32 bytes, those
/// bytes are used as the HKDF salt. Otherwise, a zero salt is returned
/// (which HKDF handles per RFC 5869 Section 2.2).
///
/// # Returns
///
/// `(hmac_requested, salt)` where:
/// - `hmac_requested` is `true` if the response contains "HMAC".
/// - `salt` is the 32-byte salt (zeroed if not provided by kernel).
pub fn extract_handshake_salt(response_payload: &[u8]) -> (bool, [u8; 32]) {
    let text = String::from_utf8_lossy(response_payload);
    if !text.contains("HMAC") {
        return (false, [0u8; 32]);
    }

    // Look for salt bytes after the "HMAC" marker.
    if let Some(pos) = response_payload
        .windows(4)
        .position(|w| w == b"HMAC")
    {
        let after_hmac = &response_payload[pos + 4..];
        if after_hmac.len() >= 32 {
            let mut salt = [0u8; 32];
            salt.copy_from_slice(&after_hmac[..32]);
            return (true, salt);
        }
    }

    // "HMAC" found but no salt — use zero salt.
    (true, [0u8; 32])
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hkdf_derive_produces_32_bytes() {
        let ikm = [0xAA; 32];
        let salt = [0xBB; 32];
        let info = b"test-info";
        let key = hkdf_sha256_derive(&ikm, &salt, info);
        assert_eq!(key.len(), 32);
        // Must not be all zeros.
        assert_ne!(key, [0u8; 32]);
    }

    #[test]
    fn test_hkdf_derive_different_salts_produce_different_keys() {
        let ikm = [0xCC; 32];
        let info = b"test";
        let key_a = hkdf_sha256_derive(&ikm, &[0x01; 32], info);
        let key_b = hkdf_sha256_derive(&ikm, &[0x02; 32], info);
        assert_ne!(key_a, key_b);
    }

    #[test]
    fn test_hkdf_derive_different_info_produce_different_keys() {
        let ikm = [0xDD; 32];
        let salt = [0xEE; 32];
        let key_a = hkdf_sha256_derive(&ikm, &salt, b"info-a");
        let key_b = hkdf_sha256_derive(&ikm, &salt, b"info-b");
        assert_ne!(key_a, key_b);
    }

    #[test]
    fn test_hkdf_derive_empty_salt() {
        let ikm = [0xFF; 32];
        let key = hkdf_sha256_derive(&ikm, &[], b"info");
        assert_ne!(key, [0u8; 32]);
    }

    #[test]
    fn test_aaak_encode_decode_round_trip() {
        let salt = [0x11; 32];
        let info = [0x22; 32];
        let frame = AaakFrame::new_rotate(salt, info);
        let encoded = frame.encode();
        let decoded = AaakFrame::decode(&encoded).expect("should decode");
        assert_eq!(decoded.op, AAAK_OP_ROTATE);
        assert_eq!(decoded.salt, salt);
        assert_eq!(decoded.info, info);
        assert!(decoded.extra.is_empty());
    }

    #[test]
    fn test_aaak_ack_round_trip() {
        let salt = [0x33; 32];
        let info = [0x44; 32];
        let frame = AaakFrame::new_ack(salt, info);
        let encoded = frame.encode();
        let decoded = AaakFrame::decode(&encoded).expect("should decode");
        assert_eq!(decoded.op, AAAK_OP_ACK);
        assert_eq!(decoded.salt, salt);
        assert_eq!(decoded.info, info);
    }

    #[test]
    fn test_aaak_decode_too_short() {
        let short = vec![0u8; 64]; // 64 < 65 minimum
        assert!(AaakFrame::decode(&short).is_none());
    }

    #[test]
    fn test_aaak_encode_with_extra_data() {
        let mut frame = AaakFrame::new_rotate([0; 32], [0; 32]);
        frame.extra = vec![0xAA, 0xBB, 0xCC];
        let encoded = frame.encode();
        assert_eq!(encoded.len(), 65 + 3);
        let decoded = AaakFrame::decode(&encoded).expect("should decode");
        assert_eq!(decoded.extra, vec![0xAA, 0xBB, 0xCC]);
    }

    #[test]
    fn test_extract_handshake_salt_with_salt() {
        let mut payload = Vec::new();
        payload.extend_from_slice(b"HMAC");
        payload.extend_from_slice(&[0xAB; 32]);
        let (hmac_req, salt) = extract_handshake_salt(&payload);
        assert!(hmac_req);
        assert_eq!(salt, [0xAB; 32]);
    }

    #[test]
    fn test_extract_handshake_salt_no_salt_bytes() {
        let payload = b"HMAC";
        let (hmac_req, salt) = extract_handshake_salt(payload);
        assert!(hmac_req);
        assert_eq!(salt, [0u8; 32]); // zero salt fallback
    }

    #[test]
    fn test_extract_handshake_salt_no_hmac() {
        let payload = b"OK|HELLO";
        let (hmac_req, salt) = extract_handshake_salt(payload);
        assert!(!hmac_req);
        assert_eq!(salt, [0u8; 32]);
    }

    #[test]
    fn test_extract_handshake_salt_embedded_hmac() {
        // "HMAC" embedded after other text
        let mut payload = b"WELCOME|HMAC".to_vec();
        payload.extend_from_slice(&[0xCD; 32]);
        let (hmac_req, salt) = extract_handshake_salt(&payload);
        assert!(hmac_req);
        assert_eq!(salt, [0xCD; 32]);
    }
}
