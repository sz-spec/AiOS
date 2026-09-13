/// Asynchronous VBus client over Unix domain sockets.
///
/// Manages connection lifecycle, VBus v2.19 handshake with optional
/// HMAC-SHA256 authentication, and command/response correlation via
/// monotonic tags.
///
/// # Protocol constraints
///
/// - Post-connect delay: 1.0 s (QEMU 16550 FCR=0x00 workaround)
/// - Command timeout: 5 s per round-trip
/// - Handshake: skip up to 5 unsolicited EVENT/PING frames

use std::path::Path;

use rand::Rng;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::UnixStream;
use tokio::time::{timeout, Duration};

use super::aaak::{extract_handshake_salt, hkdf_sha256_derive, VBUS_HKDF_INFO};
use super::hmac::HmacAuth;
use super::protocol::{Frame, FrameHeader, FrameType, ProtocolError, HEADER_SIZE};

// ---------------------------------------------------------------------------
// Token stream parsing
// ---------------------------------------------------------------------------

/// Flags carried in the token frame payload at byte offset 7.
pub const TOKEN_FLAG_FIRST: u8 = 0x01;
pub const TOKEN_FLAG_LAST: u8 = 0x02;
pub const TOKEN_FLAG_ERROR: u8 = 0x04;

/// A single decoded token from a TOKEN_STREAM (0x07) frame.
///
/// # Payload wire layout
///
/// | Offset | Size   | Field     | Description                        |
/// |--------|--------|-----------|------------------------------------|
/// | 0      | 1      | slot_id   | Source AI slot                     |
/// | 1      | 4      | token_id  | u32 LE vocabulary token ID         |
/// | 5      | 2      | seq       | u16 LE sequence number             |
/// | 7      | 1      | flags     | FLAG_FIRST=0x01, FLAG_LAST=0x02, FLAG_ERROR=0x04 |
/// | 8      | ...    | text      | UTF-8 token text (variable length) |
#[derive(Debug, Clone)]
pub struct TokenFrame {
    /// Source AI slot index (0-7).
    pub slot_id: u8,
    /// Vocabulary token ID.
    pub token_id: u32,
    /// Sequence number within the generation.
    pub seq: u16,
    /// Token flags (FLAG_FIRST, FLAG_LAST, FLAG_ERROR).
    pub flags: u8,
    /// Decoded UTF-8 text for this token.
    pub text: String,
}

impl TokenFrame {
    /// Minimum payload size: 1 (slot_id) + 4 (token_id) + 2 (seq) + 1 (flags) = 8 bytes.
    const MIN_PAYLOAD_SIZE: usize = 8;

    /// Parses a TOKEN_STREAM frame payload into a [`TokenFrame`].
    ///
    /// Returns `None` if the payload is too short.
    pub fn from_payload(payload: &[u8]) -> Option<Self> {
        if payload.len() < Self::MIN_PAYLOAD_SIZE {
            return None;
        }

        let slot_id = payload[0];
        let token_id = u32::from_le_bytes([payload[1], payload[2], payload[3], payload[4]]);
        let seq = u16::from_le_bytes([payload[5], payload[6]]);
        let flags = payload[7];
        let text = String::from_utf8_lossy(&payload[Self::MIN_PAYLOAD_SIZE..]).into_owned();

        Some(Self {
            slot_id,
            token_id,
            seq,
            flags,
            text,
        })
    }

    /// Returns `true` if this is the first token in the generation.
    pub fn is_first(&self) -> bool {
        self.flags & TOKEN_FLAG_FIRST != 0
    }

    /// Returns `true` if this is the last token (generation complete).
    pub fn is_last(&self) -> bool {
        self.flags & TOKEN_FLAG_LAST != 0
    }

    /// Returns `true` if the kernel signalled an error.
    pub fn is_error(&self) -> bool {
        self.flags & TOKEN_FLAG_ERROR != 0
    }
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Delay after socket connect before sending handshake (QEMU 16550 quirk).
const POST_CONNECT_DELAY: Duration = Duration::from_secs(1);

/// Per-command round-trip timeout.
const COMMAND_TIMEOUT: Duration = Duration::from_secs(5);

/// Maximum unsolicited frames to skip during handshake.
const MAX_HANDSHAKE_SKIPS: usize = 5;

/// Per-token frame timeout during inference streaming (30 s).
///
/// Longer than the command timeout because inference latency varies
/// significantly with model size, prompt length, and hardware.
const TOKEN_STREAM_TIMEOUT: Duration = Duration::from_secs(30);

/// Handshake magic prefix sent by the client.
const HANDSHAKE_MAGIC: &[u8] = b"VBUS3";

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/// Errors produced by [`VBusClient`] operations.
#[derive(Debug, thiserror::Error)]
pub enum ClientError {
    /// An I/O error on the underlying Unix socket.
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    /// A VBus protocol-level error (bad magic, CRC mismatch, etc.).
    #[error("protocol error: {0}")]
    Protocol(#[from] ProtocolError),

    /// The operation timed out.
    #[error("operation timed out after {0:?}")]
    Timeout(Duration),

    /// The client is not connected.
    #[error("not connected")]
    NotConnected,

    /// The handshake with the kernel failed.
    #[error("handshake failed: {0}")]
    HandshakeFailed(String),

    /// The kernel returned an error response.
    #[error("kernel error {code}: {message}")]
    KernelError {
        /// Numeric error code from the kernel.
        code: String,
        /// Human-readable error message.
        message: String,
    },

    /// Response tag did not match the request tag.
    #[error("tag mismatch: expected {expected}, got {got}")]
    TagMismatch {
        /// The tag sent with the command.
        expected: u16,
        /// The tag received in the response.
        got: u16,
    },
}

// ---------------------------------------------------------------------------
// VBusClient
// ---------------------------------------------------------------------------

/// Asynchronous VBus protocol client.
///
/// Connects to the VOS3 kernel via a QEMU virtio-serial Unix socket,
/// performs the VBUS3 handshake, and provides command/response messaging
/// with optional HMAC-SHA256 frame authentication.
pub struct VBusClient {
    stream: Option<UnixStream>,
    hmac: Option<HmacAuth>,
    tag_counter: u16,
}

impl VBusClient {
    /// Creates a new, disconnected client.
    pub fn new() -> Self {
        Self {
            stream: None,
            hmac: None,
            tag_counter: 0,
        }
    }

    /// Returns `true` if the client has an active connection.
    pub fn is_connected(&self) -> bool {
        self.stream.is_some()
    }

    /// Returns `true` if HMAC authentication is enabled (established during handshake).
    pub fn is_hmac_enabled(&self) -> bool {
        self.hmac.is_some()
    }

    /// Connects to the VBus socket and performs the protocol handshake.
    ///
    /// # Protocol
    ///
    /// 1. Connect to the Unix socket at `path`.
    /// 2. Wait [`POST_CONNECT_DELAY`] (QEMU 16550 FCR quirk).
    /// 3. Drain any stale bytes in the read buffer.
    /// 4. Send `HANDSHAKE` frame with `VBUS3` magic + 32 random bytes.
    /// 5. Skip up to 5 unsolicited `EVENT`/`PING` frames.
    /// 6. Expect a `HANDSHAKE` response.
    /// 7. If response payload contains `"HMAC"`, enable HMAC-SHA256 with
    ///    the 32 random bytes as the session key.
    ///
    /// # Errors
    ///
    /// Returns [`ClientError`] on I/O failure, timeout, or handshake rejection.
    pub async fn connect(&mut self, path: &Path) -> Result<(), ClientError> {
        let stream = UnixStream::connect(path).await?;
        self.stream = Some(stream);

        // Post-connect delay (QEMU 16550 FCR=0x00 workaround).
        tokio::time::sleep(POST_CONNECT_DELAY).await;

        // Drain stale bytes.
        self.drain_stale().await?;

        // Build handshake payload: "VBUS3" + 32 random bytes.
        let mut session_key = [0u8; 32];
        rand::rng().fill_bytes(&mut session_key);

        let mut payload = Vec::with_capacity(HANDSHAKE_MAGIC.len() + 32);
        payload.extend_from_slice(HANDSHAKE_MAGIC);
        payload.extend_from_slice(&session_key);

        // Send HANDSHAKE frame.
        let tag = self.next_tag();
        self.send_frame(FrameType::Handshake, tag, &payload).await?;

        // Read response, skipping up to MAX_HANDSHAKE_SKIPS unsolicited frames.
        let resp = self.read_response_skipping(tag, MAX_HANDSHAKE_SKIPS).await?;

        // Check if kernel wants HMAC and extract salt for HKDF key derivation.
        let (hmac_requested, salt) = extract_handshake_salt(&resp.payload);
        if hmac_requested {
            // Derive the actual HMAC session key via HKDF-SHA256 (RFC 5869).
            // IKM = raw 32 random handshake bytes
            // Salt = kernel-provided salt (or zero if not provided)
            // Info = "vbus-hmac-session-key-v3"
            let derived_key = hkdf_sha256_derive(&session_key, &salt, VBUS_HKDF_INFO);
            self.hmac = Some(HmacAuth::new(derived_key));
        }

        Ok(())
    }

    /// Sends a pipe-delimited command string and waits for the kernel response.
    ///
    /// Returns the response payload as a `String`. The kernel uses the
    /// convention `OK|data...` for success and `ERR|code|message` for errors.
    ///
    /// # Errors
    ///
    /// - [`ClientError::NotConnected`] if no connection is active.
    /// - [`ClientError::Timeout`] if the kernel does not respond within 5 s.
    /// - [`ClientError::KernelError`] if the response starts with `ERR|`.
    /// - [`ClientError::TagMismatch`] if the response tag does not match.
    pub async fn send_command(&mut self, cmd: &str) -> Result<String, ClientError> {
        if self.stream.is_none() {
            return Err(ClientError::NotConnected);
        }

        let tag = self.next_tag();
        self.send_frame(FrameType::Cmd, tag, cmd.as_bytes())
            .await?;

        let resp = timeout(COMMAND_TIMEOUT, self.read_response(tag))
            .await
            .map_err(|_| ClientError::Timeout(COMMAND_TIMEOUT))??;

        let body = String::from_utf8_lossy(&resp.payload).into_owned();

        // Parse kernel response convention.
        if body.starts_with("ERR|") {
            let parts: Vec<&str> = body.splitn(3, '|').collect();
            let code = parts.get(1).unwrap_or(&"?").to_string();
            let message = parts.get(2).unwrap_or(&"unknown error").to_string();
            return Err(ClientError::KernelError { code, message });
        }

        // Strip leading "OK|" if present.
        if let Some(data) = body.strip_prefix("OK|") {
            return Ok(data.to_string());
        }

        Ok(body)
    }

    /// Reads TOKEN_STREAM (0x07) frames until FLAG_LAST or FLAG_ERROR is received.
    ///
    /// After a `KIM_GENERATE` command has been acknowledged with an `OK` response,
    /// the kernel begins streaming token frames. This method collects all token
    /// text into a `Vec<TokenFrame>` and returns once the generation is complete.
    ///
    /// # Token stream timeout
    ///
    /// Each individual token frame must arrive within [`TOKEN_STREAM_TIMEOUT`]
    /// (30 seconds). This is longer than the command timeout because inference
    /// latency varies with model size and prompt length.
    ///
    /// # Errors
    ///
    /// - [`ClientError::NotConnected`] if no connection is active.
    /// - [`ClientError::Timeout`] if a token frame is not received within 30 s.
    /// - [`ClientError::KernelError`] if the kernel sends FLAG_ERROR.
    /// - [`ClientError::Protocol`] if a frame has an invalid payload.
    pub async fn read_token_stream(&mut self) -> Result<Vec<TokenFrame>, ClientError> {
        let mut tokens = Vec::new();

        loop {
            let frame = timeout(TOKEN_STREAM_TIMEOUT, self.read_frame())
                .await
                .map_err(|_| ClientError::Timeout(TOKEN_STREAM_TIMEOUT))??;

            // Skip non-TokenStream frames (e.g. stale EVENTs, PINGs).
            if frame.header.frame_type != FrameType::TokenStream {
                continue;
            }

            let token = TokenFrame::from_payload(&frame.payload).ok_or_else(|| {
                ClientError::Protocol(ProtocolError::BufferTooSmall)
            })?;

            let is_last = token.is_last();
            let is_error = token.is_error();

            if is_error {
                let err_msg = if token.text.is_empty() {
                    "inference error".to_string()
                } else {
                    token.text.clone()
                };
                return Err(ClientError::KernelError {
                    code: "INFERENCE".to_string(),
                    message: err_msg,
                });
            }

            tokens.push(token);

            if is_last {
                break;
            }
        }

        Ok(tokens)
    }

    /// Reads TOKEN_STREAM frames, invoking `callback` for each token.
    ///
    /// This is the streaming variant of [`read_token_stream`]. The callback
    /// receives each [`TokenFrame`] as it arrives, enabling real-time UI
    /// updates. Returns the full list of tokens after the stream completes.
    ///
    /// The callback signature is `FnMut(&TokenFrame)`. It is called
    /// synchronously for each token before the next frame is read.
    ///
    /// # Errors
    ///
    /// Same as [`read_token_stream`].
    pub async fn read_token_stream_with_callback<F>(
        &mut self,
        mut callback: F,
    ) -> Result<Vec<TokenFrame>, ClientError>
    where
        F: FnMut(&TokenFrame),
    {
        let mut tokens = Vec::new();

        loop {
            let frame = timeout(TOKEN_STREAM_TIMEOUT, self.read_frame())
                .await
                .map_err(|_| ClientError::Timeout(TOKEN_STREAM_TIMEOUT))??;

            // Skip non-TokenStream frames.
            if frame.header.frame_type != FrameType::TokenStream {
                continue;
            }

            let token = TokenFrame::from_payload(&frame.payload).ok_or_else(|| {
                ClientError::Protocol(ProtocolError::BufferTooSmall)
            })?;

            let is_last = token.is_last();
            let is_error = token.is_error();

            if is_error {
                let err_msg = if token.text.is_empty() {
                    "inference error".to_string()
                } else {
                    token.text.clone()
                };
                return Err(ClientError::KernelError {
                    code: "INFERENCE".to_string(),
                    message: err_msg,
                });
            }

            callback(&token);
            tokens.push(token);

            if is_last {
                break;
            }
        }

        Ok(tokens)
    }

    /// Disconnects from the VBus socket.
    ///
    /// Safe to call even if not connected.
    pub async fn disconnect(&mut self) {
        if let Some(mut stream) = self.stream.take() {
            let _ = stream.shutdown().await;
        }
        self.hmac = None;
        self.tag_counter = 0;
    }

    // -----------------------------------------------------------------------
    // Internal helpers
    // -----------------------------------------------------------------------

    /// Returns the next monotonically increasing tag.
    fn next_tag(&mut self) -> u16 {
        let tag = self.tag_counter;
        self.tag_counter = self.tag_counter.wrapping_add(1);
        tag
    }

    /// Drains any stale bytes from the socket (non-blocking read).
    async fn drain_stale(&mut self) -> Result<(), ClientError> {
        let stream = self.stream.as_mut().ok_or(ClientError::NotConnected)?;
        let mut buf = [0u8; 4096];
        loop {
            match timeout(Duration::from_millis(100), stream.read(&mut buf)).await {
                Ok(Ok(0)) => break,      // EOF
                Ok(Ok(_)) => continue,   // Drained some bytes, try again
                Ok(Err(e)) => return Err(ClientError::Io(e)),
                Err(_) => break,          // Timeout = no more stale data
            }
        }
        Ok(())
    }

    /// Serializes and sends a frame, applying HMAC if enabled.
    async fn send_frame(
        &mut self,
        frame_type: FrameType,
        tag: u16,
        payload: &[u8],
    ) -> Result<(), ClientError> {
        let frame = Frame::new(frame_type, tag, payload.to_vec())?;
        let mut data = frame.serialize();

        // Apply HMAC signature if enabled.
        if let Some(ref hmac) = self.hmac {
            if data.len() >= HEADER_SIZE {
                let (header_bytes, payload_bytes) = data.split_at_mut(HEADER_SIZE);
                let header_arr: &mut [u8; 64] = header_bytes
                    .try_into()
                    .map_err(|_| ClientError::Protocol(ProtocolError::BufferTooSmall))?;
                hmac.sign(header_arr, payload_bytes);
            }
        }

        let stream = self.stream.as_mut().ok_or(ClientError::NotConnected)?;
        stream.write_all(&data).await?;
        stream.flush().await?;
        Ok(())
    }

    /// Reads a single frame from the socket.
    async fn read_frame(&mut self) -> Result<Frame, ClientError> {
        let stream = self.stream.as_mut().ok_or(ClientError::NotConnected)?;

        // Read header.
        let mut hdr_buf = [0u8; HEADER_SIZE];
        stream.read_exact(&mut hdr_buf).await?;

        let header = FrameHeader::deserialize(&hdr_buf)?;

        // Read payload.
        let payload_len = header.payload_len as usize;
        let mut payload = vec![0u8; payload_len];
        if payload_len > 0 {
            stream.read_exact(&mut payload).await?;
        }

        // Verify CRC.
        if !header.verify_crc(&payload) {
            return Err(ClientError::Protocol(ProtocolError::CrcMismatch));
        }

        // Verify HMAC if authentication is enabled.
        if let Some(ref hmac) = self.hmac {
            if hdr_buf[48] & 0x01 != 0 {
                // Frame is signed — verify the signature.
                if !hmac.verify(&hdr_buf, &payload) {
                    return Err(ClientError::HandshakeFailed(
                        "HMAC verification failed".to_string(),
                    ));
                }
            } else {
                // HMAC session is active but frame is unsigned — reject
                // to prevent downgrade attacks.
                return Err(ClientError::HandshakeFailed(
                    "unsigned frame in HMAC session (possible downgrade attack)".to_string(),
                ));
            }
        }

        Ok(Frame { header, payload })
    }

    /// Reads the next RESP frame matching `expected_tag`.
    async fn read_response(&mut self, expected_tag: u16) -> Result<Frame, ClientError> {
        let frame = self.read_frame().await?;

        if frame.header.frame_type != FrameType::Resp {
            return Err(ClientError::HandshakeFailed(format!(
                "expected RESP frame, got {:?}",
                frame.header.frame_type
            )));
        }

        if frame.header.tag != expected_tag {
            return Err(ClientError::TagMismatch {
                expected: expected_tag,
                got: frame.header.tag,
            });
        }

        Ok(frame)
    }

    /// Reads frames until a HANDSHAKE with the expected tag arrives,
    /// skipping up to `max_skips` unsolicited EVENT/PING frames.
    async fn read_response_skipping(
        &mut self,
        expected_tag: u16,
        max_skips: usize,
    ) -> Result<Frame, ClientError> {
        let mut skipped = 0;

        loop {
            let frame = timeout(COMMAND_TIMEOUT, self.read_frame())
                .await
                .map_err(|_| ClientError::Timeout(COMMAND_TIMEOUT))??;

            match frame.header.frame_type {
                FrameType::Handshake => {
                    if frame.header.tag == expected_tag {
                        return Ok(frame);
                    }
                    // Wrong tag — treat as stale and skip.
                    skipped += 1;
                }
                FrameType::Event | FrameType::Ping | FrameType::Resp => {
                    // Skip unsolicited or stale frames.
                    skipped += 1;
                }
                _ => {
                    skipped += 1;
                }
            }

            if skipped > max_skips {
                return Err(ClientError::HandshakeFailed(format!(
                    "skipped {max_skips} frames without receiving handshake response"
                )));
            }
        }
    }
}

impl Default for VBusClient {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_new_client_is_disconnected() {
        let client = VBusClient::new();
        assert!(!client.is_connected());
        assert!(!client.is_hmac_enabled());
    }

    #[test]
    fn test_default_is_disconnected() {
        let client = VBusClient::default();
        assert!(!client.is_connected());
    }

    #[test]
    fn test_tag_counter_wraps() {
        let mut client = VBusClient::new();
        client.tag_counter = u16::MAX;
        let tag = client.next_tag();
        assert_eq!(tag, u16::MAX);
        let next = client.next_tag();
        assert_eq!(next, 0); // Wrapped around.
    }

    #[tokio::test]
    async fn test_send_command_not_connected() {
        let mut client = VBusClient::new();
        let err = client.send_command("PING").await;
        assert!(matches!(err, Err(ClientError::NotConnected)));
    }

    #[tokio::test]
    async fn test_disconnect_when_not_connected() {
        let mut client = VBusClient::new();
        // Should not panic.
        client.disconnect().await;
        assert!(!client.is_connected());
    }

    #[tokio::test]
    async fn test_connect_nonexistent_path() {
        let mut client = VBusClient::new();
        let result = client.connect(Path::new("/tmp/nonexistent_vbus_socket_12345")).await;
        assert!(result.is_err());
    }

    // -----------------------------------------------------------------------
    // TokenFrame parsing tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_token_frame_parse_valid() {
        // Build a payload: slot=2, token_id=42, seq=7, flags=FLAG_FIRST, text="hello"
        let mut payload = Vec::new();
        payload.push(2u8); // slot_id
        payload.extend_from_slice(&42u32.to_le_bytes()); // token_id
        payload.extend_from_slice(&7u16.to_le_bytes()); // seq
        payload.push(TOKEN_FLAG_FIRST); // flags
        payload.extend_from_slice(b"hello"); // text

        let token = TokenFrame::from_payload(&payload).expect("should parse");
        assert_eq!(token.slot_id, 2);
        assert_eq!(token.token_id, 42);
        assert_eq!(token.seq, 7);
        assert!(token.is_first());
        assert!(!token.is_last());
        assert!(!token.is_error());
        assert_eq!(token.text, "hello");
    }

    #[test]
    fn test_token_frame_parse_last_flag() {
        let mut payload = Vec::new();
        payload.push(0u8);
        payload.extend_from_slice(&0u32.to_le_bytes());
        payload.extend_from_slice(&99u16.to_le_bytes());
        payload.push(TOKEN_FLAG_LAST);
        payload.extend_from_slice(b".");

        let token = TokenFrame::from_payload(&payload).expect("should parse");
        assert!(token.is_last());
        assert!(!token.is_first());
        assert!(!token.is_error());
        assert_eq!(token.seq, 99);
        assert_eq!(token.text, ".");
    }

    #[test]
    fn test_token_frame_parse_error_flag() {
        let mut payload = Vec::new();
        payload.push(1u8);
        payload.extend_from_slice(&0u32.to_le_bytes());
        payload.extend_from_slice(&0u16.to_le_bytes());
        payload.push(TOKEN_FLAG_ERROR);
        payload.extend_from_slice(b"out of memory");

        let token = TokenFrame::from_payload(&payload).expect("should parse");
        assert!(token.is_error());
        assert_eq!(token.text, "out of memory");
    }

    #[test]
    fn test_token_frame_parse_combined_flags() {
        // FLAG_FIRST | FLAG_LAST (single-token generation)
        let mut payload = Vec::new();
        payload.push(0u8);
        payload.extend_from_slice(&1u32.to_le_bytes());
        payload.extend_from_slice(&0u16.to_le_bytes());
        payload.push(TOKEN_FLAG_FIRST | TOKEN_FLAG_LAST);
        payload.extend_from_slice(b"yes");

        let token = TokenFrame::from_payload(&payload).expect("should parse");
        assert!(token.is_first());
        assert!(token.is_last());
        assert!(!token.is_error());
        assert_eq!(token.text, "yes");
    }

    #[test]
    fn test_token_frame_parse_empty_text() {
        // Minimum valid payload: 8 bytes header, 0 bytes text
        let mut payload = Vec::new();
        payload.push(0u8);
        payload.extend_from_slice(&0u32.to_le_bytes());
        payload.extend_from_slice(&0u16.to_le_bytes());
        payload.push(TOKEN_FLAG_LAST);

        let token = TokenFrame::from_payload(&payload).expect("should parse");
        assert_eq!(token.text, "");
        assert!(token.is_last());
    }

    #[test]
    fn test_token_frame_parse_too_short() {
        // Only 7 bytes -- below the 8-byte minimum
        let payload = vec![0u8; 7];
        assert!(TokenFrame::from_payload(&payload).is_none());
    }

    #[test]
    fn test_token_frame_parse_empty() {
        assert!(TokenFrame::from_payload(&[]).is_none());
    }
}
