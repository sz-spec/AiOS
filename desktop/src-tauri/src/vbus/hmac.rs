/// HMAC-SHA256 frame authentication for VBus transport.
///
/// Provides per-frame message authentication using HMAC-SHA256 (FIPS 180-4
/// SHA-256 + RFC 2104 HMAC). The 64-byte VBus header carries a 32-byte MAC
/// at offset 16 and a flag bit at byte 48 indicating whether the frame is
/// signed.
///
/// Frame header layout (64 bytes):
/// ```text
///   [0..16)   routing / metadata  (covered by MAC)
///   [16..48)  32-byte HMAC-SHA256 MAC
///   [48]      flags — bit 0 = FLAG_HMAC (frame is signed)
///   [49..64)  reserved
/// ```
///
/// No `unsafe` code is used anywhere in this module.

use hmac::{Hmac, Mac};
use rand::RngCore;
use sha2::Sha256;
use subtle::ConstantTimeEq;

/// Type alias for the HMAC-SHA256 construction.
type HmacSha256 = Hmac<Sha256>;

/// Flag bit in `header[48]` indicating HMAC authentication is present.
const FLAG_HMAC: u8 = 0x01;

/// HMAC-SHA256 frame authenticator.
///
/// Holds a 32-byte symmetric key used to sign and verify VBus frames.
/// Keys should be exchanged during the VBus HANDSHAKE phase and kept
/// secret for the lifetime of the session.
pub struct HmacAuth {
    /// 32-byte HMAC key (256 bits).
    key: [u8; 32],
}

impl HmacAuth {
    /// Creates a new `HmacAuth` from an explicit 32-byte key.
    ///
    /// # Arguments
    ///
    /// * `key` - A 32-byte symmetric key for HMAC-SHA256.
    pub fn new(key: [u8; 32]) -> Self {
        Self { key }
    }

    /// Creates a new `HmacAuth` with a cryptographically random 32-byte key.
    ///
    /// Uses `rand::thread_rng()` as the entropy source.
    pub fn from_random() -> Self {
        let mut key = [0u8; 32];
        rand::thread_rng().fill_bytes(&mut key);
        Self { key }
    }

    /// Signs a VBus frame by computing HMAC-SHA256 over the header prefix
    /// and payload, then writing the MAC into the header.
    ///
    /// Specifically:
    /// 1. Computes HMAC-SHA256 over `header[0..16] || payload`.
    /// 2. Writes the 32-byte MAC to `header[16..48]`.
    /// 3. Sets the `FLAG_HMAC` bit in `header[48]`.
    ///
    /// # Arguments
    ///
    /// * `header` - Mutable reference to the 64-byte frame header.
    /// * `payload` - The frame payload bytes covered by the MAC.
    pub fn sign(&self, header: &mut [u8; 64], payload: &[u8]) {
        let mut mac = HmacSha256::new_from_slice(&self.key)
            .expect("HMAC-SHA256 accepts any key length; 32 bytes is always valid");

        mac.update(&header[0..16]);
        mac.update(payload);

        let result = mac.finalize().into_bytes();
        header[16..48].copy_from_slice(&result);
        header[48] |= FLAG_HMAC;
    }

    /// Verifies the HMAC-SHA256 authentication on a VBus frame.
    ///
    /// Returns `true` if and only if:
    /// 1. The `FLAG_HMAC` bit is set in `header[48]`.
    /// 2. The recomputed HMAC-SHA256 over `header[0..16] || payload` matches
    ///    the MAC stored in `header[16..48]` (constant-time comparison).
    ///
    /// # Arguments
    ///
    /// * `header` - Reference to the 64-byte frame header.
    /// * `payload` - The frame payload bytes covered by the MAC.
    pub fn verify(&self, header: &[u8; 64], payload: &[u8]) -> bool {
        if header[48] & FLAG_HMAC == 0 {
            return false;
        }

        let mut mac = HmacSha256::new_from_slice(&self.key)
            .expect("HMAC-SHA256 accepts any key length; 32 bytes is always valid");

        mac.update(&header[0..16]);
        mac.update(payload);

        let computed = mac.finalize().into_bytes();
        let stored = &header[16..48];

        computed.ct_eq(stored).into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: creates a zeroed 64-byte header.
    fn blank_header() -> [u8; 64] {
        [0u8; 64]
    }

    /// A signed frame must pass verification with the same key.
    #[test]
    fn test_sign_then_verify_succeeds() {
        let auth = HmacAuth::from_random();
        let mut header = blank_header();
        // Write some routing data into the prefix.
        header[0] = 0xAB;
        header[15] = 0xCD;

        let payload = b"hello vbus";
        auth.sign(&mut header, payload);

        assert!(
            auth.verify(&header, payload),
            "verification must succeed for a correctly signed frame"
        );
    }

    /// Tampering with the payload must cause verification to fail.
    #[test]
    fn test_tampered_payload_fails() {
        let auth = HmacAuth::from_random();
        let mut header = blank_header();
        let payload = b"original payload";
        auth.sign(&mut header, payload);

        let tampered_payload = b"tampered payload";
        assert!(
            !auth.verify(&header, tampered_payload),
            "verification must fail when the payload is tampered"
        );
    }

    /// Tampering with the MAC bytes in the header must cause verification
    /// to fail.
    #[test]
    fn test_tampered_mac_fails() {
        let auth = HmacAuth::from_random();
        let mut header = blank_header();
        let payload = b"some data";
        auth.sign(&mut header, payload);

        // Flip one bit in the MAC region.
        header[24] ^= 0x01;
        assert!(
            !auth.verify(&header, payload),
            "verification must fail when the MAC is tampered"
        );
    }

    /// A frame without the FLAG_HMAC bit set must fail verification,
    /// even if the MAC bytes happen to be correct.
    #[test]
    fn test_unsigned_frame_fails() {
        let auth = HmacAuth::from_random();
        let mut header = blank_header();
        let payload = b"unsigned frame";
        auth.sign(&mut header, payload);

        // Clear the HMAC flag.
        header[48] &= !FLAG_HMAC;
        assert!(
            !auth.verify(&header, payload),
            "verification must fail when FLAG_HMAC is not set"
        );
    }

    /// Two different keys must produce different MACs for the same input.
    #[test]
    fn test_different_keys_produce_different_macs() {
        let key_a = [0xAAu8; 32];
        let key_b = [0xBBu8; 32];
        let auth_a = HmacAuth::new(key_a);
        let auth_b = HmacAuth::new(key_b);

        let mut header_a = blank_header();
        let mut header_b = blank_header();
        let payload = b"same payload";

        auth_a.sign(&mut header_a, payload);
        auth_b.sign(&mut header_b, payload);

        // Compare the MAC regions (header[16..48]).
        assert_ne!(
            &header_a[16..48],
            &header_b[16..48],
            "different keys must produce different MACs"
        );
    }
}
