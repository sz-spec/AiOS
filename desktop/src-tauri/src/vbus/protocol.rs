/// VBus v2.19 binary protocol — 64-byte frame header with CRC32C integrity
/// and HMAC-SHA256 authentication support.
///
/// # Wire Layout (64 bytes)
///
/// | Offset | Size | Field         | Description                                    |
/// |--------|------|---------------|------------------------------------------------|
/// | 0      | 2    | magic         | 0x5642 ("VB" little-endian)                    |
/// | 2      | 1    | version       | 0x03                                           |
/// | 3      | 1    | frame_type    | CMD/RESP/EVENT/PING/PONG/HANDSHAKE/ERROR/FEEDBACK |
/// | 4      | 2    | tag           | u16 LE request/response correlation            |
/// | 6      | 2    | payload_len   | u16 LE payload byte count                      |
/// | 8      | 4    | hdr_crc       | crc32c(header\[0:8\])                          |
/// | 12     | 4    | payload_crc   | crc32c_with_seed(payload, crc32c(header\[0:8\])) |
/// | 16     | 32   | mac           | HMAC-SHA256 (zeroed if unsigned)                |
/// | 48     | 1    | flags         | bit 0 = HMAC signed                            |
/// | 49     | 15   | reserved      | zeroed                                         |

use super::crc32c::{crc32c, crc32c_with_seed};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Magic number identifying a VBus frame ("VB" in little-endian).
pub const MAGIC: u16 = 0x5642;

/// Protocol version supported by this implementation.
pub const VERSION: u8 = 0x03;

/// Total size of the serialized frame header in bytes.
pub const HEADER_SIZE: usize = 64;

/// Maximum payload length (u16::MAX).
/// Aligned with C kernel `VBUS_MAX_PAYLOAD` — both use u16 payload_len,
/// so the ceiling is 65 535 bytes per frame.
pub const MAX_PAYLOAD: usize = 65535;

/// Flag bit indicating the frame carries a valid HMAC-SHA256 signature.
pub const FLAG_HMAC: u8 = 0x01;

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/// Errors that can occur while parsing or validating VBus frames.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProtocolError {
    /// The magic bytes at offset 0 do not match `0x5642`.
    InvalidMagic,
    /// The version byte does not match the expected protocol version.
    InvalidVersion,
    /// The frame_type byte does not correspond to a known variant.
    InvalidFrameType,
    /// The declared payload length exceeds [`MAX_PAYLOAD`].
    PayloadTooLarge,
    /// The provided buffer is too small for the expected data.
    BufferTooSmall,
    /// A CRC32C integrity check failed.
    CrcMismatch,
}

impl std::fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidMagic => write!(f, "invalid magic: expected 0x{MAGIC:04X}"),
            Self::InvalidVersion => write!(f, "invalid version: expected 0x{VERSION:02X}"),
            Self::InvalidFrameType => write!(f, "unrecognised frame type"),
            Self::PayloadTooLarge => {
                write!(f, "payload length exceeds maximum of {MAX_PAYLOAD} bytes")
            }
            Self::BufferTooSmall => write!(f, "buffer too small for frame data"),
            Self::CrcMismatch => write!(f, "CRC32C integrity check failed"),
        }
    }
}

impl std::error::Error for ProtocolError {}

// ---------------------------------------------------------------------------
// FrameType
// ---------------------------------------------------------------------------

/// Discriminator for the kind of VBus frame.
///
/// Values MUST match the C defines in `kernel/include/vos/virtio_vbus.h`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum FrameType {
    /// Command request sent to the kernel (VBUS_TYPE_CMD).
    Cmd = 0x01,
    /// Response to a previous command (VBUS_TYPE_RESP).
    Resp = 0x02,
    /// Bulk data frame (VBUS_TYPE_DATA).
    Data = 0x03,
    /// Session handshake / key exchange (VBUS_TYPE_HANDSHAKE).
    Handshake = 0x04,
    /// Keep-alive ping (VBUS_TYPE_PING).
    Ping = 0x05,
    /// Asynchronous event from the kernel (VBUS_TYPE_EVENT).
    Event = 0x06,
    /// Token stream frame — KIM inference tokens (VBUS_TYPE_TOKEN_STREAM).
    TokenStream = 0x07,
    /// Deep diagnostic feedback frame (VBUS_TYPE_FEEDBACK).
    Feedback = 0x08,
    /// Atomic batch frame (VBUS_TYPE_BATCH).
    Batch = 0x09,
    /// Real-time background push — KAIROS (VBUS_TYPE_NOTIFY).
    Notify = 0x0A,
    /// Backend-to-kernel slot wake (VBUS_TYPE_INTERRUPT).
    Interrupt = 0x0B,
    /// Event subscription management (VBUS_TYPE_SUBSCRIBE).
    Subscribe = 0x0C,
    /// Token-Aware Shorthand frame (VBUS_TYPE_V_AAAK).
    VAaak = 0x0D,
    /// Speculative token stream (ghost/verified) — Phase 2.3 (VBUS_TYPE_STREAM_SPEC).
    StreamSpec = 0x0E,
    /// Keep-alive pong / reply to ping (VBUS_TYPE_PONG, desktop-only).
    Pong = 0x0F,
    /// Error notification (VBUS_TYPE_ERROR, desktop-only).
    Error = 0x10,
}

impl FrameType {
    /// Converts a raw `u8` into a [`FrameType`], returning an error for
    /// unrecognised values.
    pub fn from_u8(value: u8) -> Result<Self, ProtocolError> {
        match value {
            0x01 => Ok(Self::Cmd),
            0x02 => Ok(Self::Resp),
            0x03 => Ok(Self::Data),
            0x04 => Ok(Self::Handshake),
            0x05 => Ok(Self::Ping),
            0x06 => Ok(Self::Event),
            0x07 => Ok(Self::TokenStream),
            0x08 => Ok(Self::Feedback),
            0x09 => Ok(Self::Batch),
            0x0A => Ok(Self::Notify),
            0x0B => Ok(Self::Interrupt),
            0x0C => Ok(Self::Subscribe),
            0x0D => Ok(Self::VAaak),
            0x0E => Ok(Self::StreamSpec),
            0x0F => Ok(Self::Pong),
            0x10 => Ok(Self::Error),
            _ => Err(ProtocolError::InvalidFrameType),
        }
    }
}

// ---------------------------------------------------------------------------
// FrameHeader
// ---------------------------------------------------------------------------

/// The 64-byte VBus v2.19 frame header.
///
/// Contains protocol identification, request correlation, integrity CRCs,
/// an optional HMAC-SHA256 MAC, flags, and reserved padding.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FrameHeader {
    /// Protocol magic (`0x5642`).
    pub magic: u16,
    /// Protocol version (`0x03`).
    pub version: u8,
    /// Type of this frame.
    pub frame_type: FrameType,
    /// Correlation tag for matching requests to responses.
    pub tag: u16,
    /// Length of the payload in bytes.
    pub payload_len: u16,
    /// CRC32C of the first 8 header bytes.
    pub hdr_crc: u32,
    /// CRC32C of the payload, seeded with `hdr_crc`.
    pub payload_crc: u32,
    /// HMAC-SHA256 message authentication code (zeroed when unsigned).
    pub mac: [u8; 32],
    /// Flags byte. Bit 0 ([`FLAG_HMAC`]) indicates the MAC is valid.
    pub flags: u8,
    /// Reserved bytes (must be zero).
    pub reserved: [u8; 15],
}

impl FrameHeader {
    /// Creates a new [`FrameHeader`] with computed CRC fields.
    ///
    /// The header CRC covers the first 8 bytes of the serialized header
    /// (magic, version, frame_type, tag, payload_len). The payload CRC is
    /// seeded with the header CRC so that header and payload are
    /// cryptographically chained.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::PayloadTooLarge`] if `payload` exceeds
    /// [`MAX_PAYLOAD`] bytes.
    pub fn new(
        frame_type: FrameType,
        tag: u16,
        payload: &[u8],
    ) -> Result<Self, ProtocolError> {
        if payload.len() > MAX_PAYLOAD {
            return Err(ProtocolError::PayloadTooLarge);
        }
        let payload_len = payload.len() as u16;

        // Build the first 8 bytes for CRC computation.
        let prefix = Self::build_prefix(frame_type, tag, payload_len);
        let hdr_crc = crc32c(&prefix);
        let payload_crc = crc32c_with_seed(payload, hdr_crc);

        Ok(Self {
            magic: MAGIC,
            version: VERSION,
            frame_type,
            tag,
            payload_len,
            hdr_crc,
            payload_crc,
            mac: [0u8; 32],
            flags: 0,
            reserved: [0u8; 15],
        })
    }

    /// Serializes the header into a 64-byte array using explicit
    /// little-endian byte packing.
    pub fn serialize(&self) -> [u8; HEADER_SIZE] {
        let mut buf = [0u8; HEADER_SIZE];

        // [0:2] magic
        let magic_bytes = self.magic.to_le_bytes();
        buf[0] = magic_bytes[0];
        buf[1] = magic_bytes[1];

        // [2] version
        buf[2] = self.version;

        // [3] frame_type
        buf[3] = self.frame_type as u8;

        // [4:6] tag
        let tag_bytes = self.tag.to_le_bytes();
        buf[4] = tag_bytes[0];
        buf[5] = tag_bytes[1];

        // [6:8] payload_len
        let len_bytes = self.payload_len.to_le_bytes();
        buf[6] = len_bytes[0];
        buf[7] = len_bytes[1];

        // [8:12] hdr_crc
        let hdr_crc_bytes = self.hdr_crc.to_le_bytes();
        buf[8] = hdr_crc_bytes[0];
        buf[9] = hdr_crc_bytes[1];
        buf[10] = hdr_crc_bytes[2];
        buf[11] = hdr_crc_bytes[3];

        // [12:16] payload_crc
        let payload_crc_bytes = self.payload_crc.to_le_bytes();
        buf[12] = payload_crc_bytes[0];
        buf[13] = payload_crc_bytes[1];
        buf[14] = payload_crc_bytes[2];
        buf[15] = payload_crc_bytes[3];

        // [16:48] mac
        buf[16..48].copy_from_slice(&self.mac);

        // [48] flags
        buf[48] = self.flags;

        // [49:64] reserved (already zeroed)
        buf[49..64].copy_from_slice(&self.reserved);

        buf
    }

    /// Deserializes a 64-byte buffer into a [`FrameHeader`].
    ///
    /// # Errors
    ///
    /// - [`ProtocolError::InvalidMagic`] if the first two bytes are not `0x5642`.
    /// - [`ProtocolError::InvalidVersion`] if the version byte is not `0x03`.
    /// - [`ProtocolError::InvalidFrameType`] if the frame type byte is unrecognised.
    pub fn deserialize(buf: &[u8; HEADER_SIZE]) -> Result<Self, ProtocolError> {
        // [0:2] magic
        let magic = u16::from_le_bytes([buf[0], buf[1]]);
        if magic != MAGIC {
            return Err(ProtocolError::InvalidMagic);
        }

        // [2] version
        let version = buf[2];
        if version != VERSION {
            return Err(ProtocolError::InvalidVersion);
        }

        // [3] frame_type
        let frame_type = FrameType::from_u8(buf[3])?;

        // [4:6] tag
        let tag = u16::from_le_bytes([buf[4], buf[5]]);

        // [6:8] payload_len
        let payload_len = u16::from_le_bytes([buf[6], buf[7]]);

        // [8:12] hdr_crc
        let hdr_crc = u32::from_le_bytes([buf[8], buf[9], buf[10], buf[11]]);

        // [12:16] payload_crc
        let payload_crc = u32::from_le_bytes([buf[12], buf[13], buf[14], buf[15]]);

        // [16:48] mac
        let mut mac = [0u8; 32];
        mac.copy_from_slice(&buf[16..48]);

        // [48] flags
        let flags = buf[48];

        // [49:64] reserved
        let mut reserved = [0u8; 15];
        reserved.copy_from_slice(&buf[49..64]);

        Ok(Self {
            magic,
            version,
            frame_type,
            tag,
            payload_len,
            hdr_crc,
            payload_crc,
            mac,
            flags,
            reserved,
        })
    }

    /// Verifies header and payload CRC integrity.
    ///
    /// Recomputes `hdr_crc` over the first 8 bytes and `payload_crc` over
    /// the payload seeded with the recomputed header CRC, then compares both
    /// against the stored values.
    pub fn verify_crc(&self, payload: &[u8]) -> bool {
        let prefix = Self::build_prefix(self.frame_type, self.tag, self.payload_len);
        let expected_hdr_crc = crc32c(&prefix);
        if expected_hdr_crc != self.hdr_crc {
            return false;
        }
        let expected_payload_crc = crc32c_with_seed(payload, expected_hdr_crc);
        expected_payload_crc == self.payload_crc
    }

    /// Builds the 8-byte prefix (magic, version, frame_type, tag, payload_len)
    /// used as the CRC input range.
    fn build_prefix(frame_type: FrameType, tag: u16, payload_len: u16) -> [u8; 8] {
        let mut prefix = [0u8; 8];
        let magic_bytes = MAGIC.to_le_bytes();
        prefix[0] = magic_bytes[0];
        prefix[1] = magic_bytes[1];
        prefix[2] = VERSION;
        prefix[3] = frame_type as u8;
        let tag_bytes = tag.to_le_bytes();
        prefix[4] = tag_bytes[0];
        prefix[5] = tag_bytes[1];
        let len_bytes = payload_len.to_le_bytes();
        prefix[6] = len_bytes[0];
        prefix[7] = len_bytes[1];
        prefix
    }
}

// ---------------------------------------------------------------------------
// SlottedPayload
// ---------------------------------------------------------------------------

/// Helper for commands that need slot routing.
/// Since v3 removed slot_id from the wire header, commands encode it
/// as the first byte of the payload.
pub struct SlottedPayload;

impl SlottedPayload {
    /// Prepend slot_id to the front of a payload.
    pub fn encode(slot_id: u8, inner: &[u8]) -> Vec<u8> {
        let mut buf = Vec::with_capacity(1 + inner.len());
        buf.push(slot_id);
        buf.extend_from_slice(inner);
        buf
    }

    /// Extract slot_id from the first byte of a payload.
    pub fn decode(payload: &[u8]) -> Option<(u8, &[u8])> {
        if payload.is_empty() {
            None
        } else {
            Some((payload[0], &payload[1..]))
        }
    }
}

// ---------------------------------------------------------------------------
// Frame
// ---------------------------------------------------------------------------

/// A complete VBus frame consisting of a [`FrameHeader`] and a payload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    /// The 64-byte frame header.
    pub header: FrameHeader,
    /// The variable-length payload (0 to [`MAX_PAYLOAD`] bytes).
    pub payload: Vec<u8>,
}

impl Frame {
    /// Creates a new [`Frame`] with the given type, correlation tag, and payload.
    ///
    /// CRC fields in the header are computed automatically.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::PayloadTooLarge`] if `payload` exceeds
    /// [`MAX_PAYLOAD`] bytes.
    pub fn new(
        frame_type: FrameType,
        tag: u16,
        payload: Vec<u8>,
    ) -> Result<Self, ProtocolError> {
        let header = FrameHeader::new(frame_type, tag, &payload)?;
        Ok(Self { header, payload })
    }

    /// Serializes the frame into a byte vector (header bytes followed by payload).
    pub fn serialize(&self) -> Vec<u8> {
        let header_bytes = self.header.serialize();
        let mut out = Vec::with_capacity(HEADER_SIZE + self.payload.len());
        out.extend_from_slice(&header_bytes);
        out.extend_from_slice(&self.payload);
        out
    }

    /// Deserializes a byte slice into a [`Frame`].
    ///
    /// The input must contain at least [`HEADER_SIZE`] bytes plus the number
    /// of payload bytes declared in the header's `payload_len` field.
    ///
    /// # Errors
    ///
    /// - [`ProtocolError::BufferTooSmall`] if `data` is shorter than 64 bytes
    ///   or shorter than `HEADER_SIZE + payload_len`.
    /// - Any error from [`FrameHeader::deserialize`] (magic, version, frame type).
    /// - [`ProtocolError::CrcMismatch`] if the CRC integrity check fails.
    pub fn deserialize(data: &[u8]) -> Result<Self, ProtocolError> {
        if data.len() < HEADER_SIZE {
            return Err(ProtocolError::BufferTooSmall);
        }

        let mut hdr_buf = [0u8; HEADER_SIZE];
        hdr_buf.copy_from_slice(&data[..HEADER_SIZE]);
        let header = FrameHeader::deserialize(&hdr_buf)?;

        let payload_len = header.payload_len as usize;
        let total_len = HEADER_SIZE + payload_len;
        if data.len() < total_len {
            return Err(ProtocolError::BufferTooSmall);
        }

        let payload = data[HEADER_SIZE..total_len].to_vec();

        if !header.verify_crc(&payload) {
            return Err(ProtocolError::CrcMismatch);
        }

        Ok(Self { header, payload })
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    /// A serialize/deserialize round-trip must produce an identical frame.
    #[test]
    fn test_round_trip() {
        let payload = b"hello vbus".to_vec();
        let frame = Frame::new(FrameType::Cmd, 42, payload.clone())
            .expect("frame construction should succeed");
        let bytes = frame.serialize();
        let restored = Frame::deserialize(&bytes)
            .expect("deserialization should succeed");
        assert_eq!(frame, restored);
        assert_eq!(restored.payload, payload);
        assert_eq!(restored.header.tag, 42);
        assert_eq!(restored.header.frame_type, FrameType::Cmd);
    }

    /// Round-trip with an empty payload.
    #[test]
    fn test_round_trip_empty_payload() {
        let frame = Frame::new(FrameType::Ping, 0, Vec::new())
            .expect("frame construction should succeed");
        let bytes = frame.serialize();
        assert_eq!(bytes.len(), HEADER_SIZE);
        let restored = Frame::deserialize(&bytes)
            .expect("deserialization should succeed");
        assert_eq!(frame, restored);
        assert!(restored.payload.is_empty());
    }

    /// CRC verification must succeed for a valid frame and fail when the
    /// payload is tampered with.
    #[test]
    fn test_crc_verification() {
        let payload = b"integrity check".to_vec();
        let header = FrameHeader::new(FrameType::Resp, 7, &payload)
            .expect("header construction should succeed");

        // Correct payload passes.
        assert!(header.verify_crc(&payload));

        // Tampered payload fails.
        let mut bad_payload = payload.clone();
        bad_payload[0] ^= 0xFF;
        assert!(!header.verify_crc(&bad_payload));

        // Wrong-length payload fails.
        assert!(!header.verify_crc(b"short"));
    }

    /// Deserializing a buffer with an invalid magic must return `InvalidMagic`.
    #[test]
    fn test_invalid_magic() {
        let frame = Frame::new(FrameType::Event, 1, b"test".to_vec())
            .expect("frame construction should succeed");
        let mut bytes = frame.serialize();
        // Corrupt the magic bytes.
        bytes[0] = 0xFF;
        bytes[1] = 0xFF;
        let err = Frame::deserialize(&bytes).expect_err("should reject bad magic");
        assert_eq!(err, ProtocolError::InvalidMagic);
    }

    /// Deserializing a buffer with an invalid version must return `InvalidVersion`.
    #[test]
    fn test_invalid_version() {
        let frame = Frame::new(FrameType::Cmd, 1, Vec::new())
            .expect("frame construction should succeed");
        let mut bytes = frame.serialize();
        bytes[2] = 0xFF;
        let err = Frame::deserialize(&bytes).expect_err("should reject bad version");
        assert_eq!(err, ProtocolError::InvalidVersion);
    }

    /// Deserializing a buffer with an invalid frame type must return `InvalidFrameType`.
    #[test]
    fn test_invalid_frame_type() {
        let frame = Frame::new(FrameType::Cmd, 1, Vec::new())
            .expect("frame construction should succeed");
        let mut bytes = frame.serialize();
        bytes[3] = 0x00; // 0x00 is not a valid frame type.
        let err = Frame::deserialize(&bytes).expect_err("should reject bad frame type");
        assert_eq!(err, ProtocolError::InvalidFrameType);
    }

    /// The payload CRC uses the header CRC as its seed, chaining the two
    /// integrity checks together. Verify that changing the tag (which alters
    /// the header CRC) invalidates the payload CRC even when the payload
    /// bytes are unchanged.
    #[test]
    fn test_payload_crc_seed_chaining() {
        let payload = b"seeded".to_vec();
        let hdr_a = FrameHeader::new(FrameType::Cmd, 1, &payload)
            .expect("header construction should succeed");
        let hdr_b = FrameHeader::new(FrameType::Cmd, 2, &payload)
            .expect("header construction should succeed");

        // Same payload, different tags -> different payload CRCs.
        assert_ne!(hdr_a.payload_crc, hdr_b.payload_crc);

        // Each header verifies against its own CRC pair.
        assert!(hdr_a.verify_crc(&payload));
        assert!(hdr_b.verify_crc(&payload));

        // Cross-check must fail: header A's CRCs do not match header B's
        // prefix, so manually swapping would break verification.
        let mut cross = hdr_a.clone();
        cross.payload_crc = hdr_b.payload_crc;
        assert!(!cross.verify_crc(&payload));
    }

    /// A buffer shorter than HEADER_SIZE must return `BufferTooSmall`.
    #[test]
    fn test_buffer_too_small() {
        let err = Frame::deserialize(&[0u8; 10])
            .expect_err("should reject undersized buffer");
        assert_eq!(err, ProtocolError::BufferTooSmall);
    }

    /// A buffer with a valid header but insufficient payload bytes must
    /// return `BufferTooSmall`.
    #[test]
    fn test_truncated_payload() {
        let frame = Frame::new(FrameType::Cmd, 1, b"payload".to_vec())
            .expect("frame construction should succeed");
        let bytes = frame.serialize();
        // Truncate one byte from the end.
        let truncated = &bytes[..bytes.len() - 1];
        let err = Frame::deserialize(truncated)
            .expect_err("should reject truncated payload");
        assert_eq!(err, ProtocolError::BufferTooSmall);
    }

    /// Corrupted payload bytes must cause a `CrcMismatch` during deserialization.
    #[test]
    fn test_corrupted_payload_crc_mismatch() {
        let frame = Frame::new(FrameType::Feedback, 99, b"data".to_vec())
            .expect("frame construction should succeed");
        let mut bytes = frame.serialize();
        // Flip a bit in the payload region.
        let last = bytes.len() - 1;
        bytes[last] ^= 0x01;
        let err = Frame::deserialize(&bytes)
            .expect_err("should reject corrupted payload");
        assert_eq!(err, ProtocolError::CrcMismatch);
    }

    /// All sixteen frame types must survive a round-trip.
    #[test]
    fn test_all_frame_types_round_trip() {
        let types = [
            FrameType::Cmd,
            FrameType::Resp,
            FrameType::Data,
            FrameType::Handshake,
            FrameType::Ping,
            FrameType::Event,
            FrameType::TokenStream,
            FrameType::Feedback,
            FrameType::Batch,
            FrameType::Notify,
            FrameType::Interrupt,
            FrameType::Subscribe,
            FrameType::VAaak,
            FrameType::StreamSpec,
            FrameType::Pong,
            FrameType::Error,
        ];
        assert_eq!(types.len(), 16, "expected 16 frame type variants");
        for ft in types {
            let frame = Frame::new(ft, 100, b"ft-test".to_vec())
                .expect("frame construction should succeed");
            let bytes = frame.serialize();
            let restored = Frame::deserialize(&bytes)
                .expect("deserialization should succeed");
            assert_eq!(restored.header.frame_type, ft);
        }
    }

    /// Payload exceeding MAX_PAYLOAD must be rejected.
    #[test]
    fn test_payload_too_large() {
        let big = vec![0xAA; MAX_PAYLOAD + 1];
        let err = Frame::new(FrameType::Cmd, 1, big)
            .expect_err("should reject oversized payload");
        assert_eq!(err, ProtocolError::PayloadTooLarge);
    }

    /// The serialized header must be exactly 64 bytes.
    #[test]
    fn test_header_size() {
        let hdr = FrameHeader::new(FrameType::Cmd, 0, b"")
            .expect("header construction should succeed");
        let bytes = hdr.serialize();
        assert_eq!(bytes.len(), HEADER_SIZE);
    }

    /// The serialized header must contain the correct magic and version at
    /// their expected offsets.
    #[test]
    fn test_header_wire_format() {
        let hdr = FrameHeader::new(FrameType::Handshake, 0x1234, b"wire")
            .expect("header construction should succeed");
        let buf = hdr.serialize();

        // Magic at [0:2] little-endian.
        assert_eq!(buf[0], 0x42); // 'B'
        assert_eq!(buf[1], 0x56); // 'V'

        // Version at [2].
        assert_eq!(buf[2], 0x03);

        // FrameType at [3].
        assert_eq!(buf[3], FrameType::Handshake as u8);

        // Tag at [4:6] little-endian.
        assert_eq!(u16::from_le_bytes([buf[4], buf[5]]), 0x1234);

        // payload_len at [6:8] little-endian.
        assert_eq!(u16::from_le_bytes([buf[6], buf[7]]), 4);

        // Flags at [48] must be 0 (no HMAC).
        assert_eq!(buf[48], 0x00);

        // Reserved [49:64] must be zeroed.
        assert_eq!(&buf[49..64], &[0u8; 15]);
    }

    /// ProtocolError must implement Display and Error.
    #[test]
    fn test_error_display() {
        let err = ProtocolError::InvalidMagic;
        let msg = format!("{err}");
        assert!(!msg.is_empty());

        // Verify Error trait is implemented (compile-time check via trait bound).
        fn _assert_error<E: std::error::Error>() {}
        _assert_error::<ProtocolError>();
    }
}
