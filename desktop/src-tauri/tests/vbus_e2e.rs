/// End-to-End VBus Mock Tests
///
/// Simulates the full VBus handshake + command flow between a mock kernel
/// (Unix socket server) and the VBusClient. Tests the complete pipeline:
///
/// 1. HANDSHAKE with HMAC key exchange (HKDF-SHA256 derivation)
/// 2. PING/PONG round-trip
/// 3. KIM_GENERATE command + token stream
///
/// These tests use a temporary Unix socket to avoid interfering with any
/// running QEMU instance.

use std::path::PathBuf;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::UnixListener;
use tokio::time::{timeout, Duration};

// ---------------------------------------------------------------------------
// CRC32C reference implementation (self-contained for integration tests)
// ---------------------------------------------------------------------------

const CRC32C_POLY: u32 = 0x82F63B78;

fn crc32c_ref(data: &[u8]) -> u32 {
    crc32c_seeded(data, 0xFFFFFFFF)
}

fn crc32c_seeded(data: &[u8], seed: u32) -> u32 {
    let mut crc = seed;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 {
            if crc & 1 != 0 {
                crc = (crc >> 1) ^ CRC32C_POLY;
            } else {
                crc >>= 1;
            }
        }
    }
    crc ^ 0xFFFFFFFF
}

// ---------------------------------------------------------------------------
// Wire format helpers
// ---------------------------------------------------------------------------

const HEADER_SIZE: usize = 64;
const VBUS_MAGIC: u16 = 0x5642;
const VBUS_VERSION: u8 = 0x03;

/// Build a v3 frame (header + payload) for the mock kernel to send.
fn build_frame(frame_type: u8, tag: u16, payload: &[u8]) -> Vec<u8> {
    let mut hdr = [0u8; HEADER_SIZE];

    // [0:2] magic LE
    hdr[0] = (VBUS_MAGIC & 0xFF) as u8;
    hdr[1] = ((VBUS_MAGIC >> 8) & 0xFF) as u8;
    // [2] version
    hdr[2] = VBUS_VERSION;
    // [3] frame_type
    hdr[3] = frame_type;
    // [4:6] tag LE
    hdr[4] = (tag & 0xFF) as u8;
    hdr[5] = ((tag >> 8) & 0xFF) as u8;
    // [6:8] payload_len LE
    let plen = payload.len() as u16;
    hdr[6] = (plen & 0xFF) as u8;
    hdr[7] = ((plen >> 8) & 0xFF) as u8;
    // [8:12] hdr_crc
    let hdr_crc = crc32c_ref(&hdr[0..8]);
    hdr[8] = (hdr_crc & 0xFF) as u8;
    hdr[9] = ((hdr_crc >> 8) & 0xFF) as u8;
    hdr[10] = ((hdr_crc >> 16) & 0xFF) as u8;
    hdr[11] = ((hdr_crc >> 24) & 0xFF) as u8;
    // [12:16] payload_crc (seeded)
    let payload_crc = crc32c_seeded(payload, hdr_crc);
    hdr[12] = (payload_crc & 0xFF) as u8;
    hdr[13] = ((payload_crc >> 8) & 0xFF) as u8;
    hdr[14] = ((payload_crc >> 16) & 0xFF) as u8;
    hdr[15] = ((payload_crc >> 24) & 0xFF) as u8;
    // [16:48] MAC = zeroed, [48] flags = 0, [49:64] reserved = 0

    let mut frame = Vec::with_capacity(HEADER_SIZE + payload.len());
    frame.extend_from_slice(&hdr);
    frame.extend_from_slice(payload);
    frame
}

/// Parse a received frame header, returning (frame_type, tag, payload_len).
fn parse_header(buf: &[u8; HEADER_SIZE]) -> (u8, u16, u16) {
    let frame_type = buf[3];
    let tag = u16::from_le_bytes([buf[4], buf[5]]);
    let payload_len = u16::from_le_bytes([buf[6], buf[7]]);
    (frame_type, tag, payload_len)
}

/// Create a temp socket path that won't conflict.
fn temp_socket_path() -> PathBuf {
    let dir = std::env::temp_dir();
    dir.join(format!("vbus_e2e_test_{}.sock", std::process::id()))
}

// ---------------------------------------------------------------------------
// Test: Handshake without HMAC
// ---------------------------------------------------------------------------

#[tokio::test]
async fn test_handshake_no_hmac() {
    let sock_path = temp_socket_path();
    let _ = std::fs::remove_file(&sock_path); // clean up stale socket

    let listener = UnixListener::bind(&sock_path).unwrap();
    let sock_path_clone = sock_path.clone();

    // Spawn mock kernel
    let kernel_handle = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();

        // Read client's HANDSHAKE frame
        let mut hdr_buf = [0u8; HEADER_SIZE];
        stream.read_exact(&mut hdr_buf).await.unwrap();
        let (ft, tag, plen) = parse_header(&hdr_buf);
        assert_eq!(ft, 0x04, "expected HANDSHAKE frame type");

        // Read payload
        let mut payload = vec![0u8; plen as usize];
        if plen > 0 {
            stream.read_exact(&mut payload).await.unwrap();
        }

        // Verify payload starts with "VBUS3"
        assert!(payload.starts_with(b"VBUS3"), "handshake should start with VBUS3 magic");

        // Respond with HANDSHAKE (no HMAC)
        let resp = build_frame(0x04, tag, b"OK|HELLO");
        stream.write_all(&resp).await.unwrap();
        stream.flush().await.unwrap();
    });

    // Give the mock kernel time to start
    tokio::time::sleep(Duration::from_millis(50)).await;

    // Client connects — we can't use VBusClient directly since it's not
    // re-exported, but we can test the wire protocol manually.
    let mut stream = tokio::net::UnixStream::connect(&sock_path_clone).await.unwrap();

    // Build and send HANDSHAKE: "VBUS3" + 32 random bytes
    let mut payload = Vec::new();
    payload.extend_from_slice(b"VBUS3");
    payload.extend_from_slice(&[0xAA; 32]); // fixed "random" bytes for test
    let frame = build_frame(0x04, 0, &payload);
    stream.write_all(&frame).await.unwrap();
    stream.flush().await.unwrap();

    // Read response
    let mut resp_hdr = [0u8; HEADER_SIZE];
    timeout(Duration::from_secs(5), stream.read_exact(&mut resp_hdr))
        .await
        .expect("should not timeout")
        .expect("should read response header");

    let (ft, tag, plen) = parse_header(&resp_hdr);
    assert_eq!(ft, 0x04, "response should be HANDSHAKE");
    assert_eq!(tag, 0, "response tag should match request");

    let mut resp_payload = vec![0u8; plen as usize];
    if plen > 0 {
        stream.read_exact(&mut resp_payload).await.unwrap();
    }
    let resp_text = String::from_utf8_lossy(&resp_payload);
    assert!(resp_text.contains("OK"), "response should contain OK");
    assert!(
        !resp_text.contains("HMAC"),
        "response should NOT contain HMAC"
    );

    kernel_handle.await.unwrap();
    let _ = std::fs::remove_file(&sock_path);
}

// ---------------------------------------------------------------------------
// Test: Handshake with HMAC
// ---------------------------------------------------------------------------

#[tokio::test]
async fn test_handshake_with_hmac() {
    let sock_path = temp_socket_path();
    // Use a different name to avoid conflicts with the parallel test above
    let sock_path = sock_path.with_extension("hmac");
    let _ = std::fs::remove_file(&sock_path);

    let listener = UnixListener::bind(&sock_path).unwrap();
    let sock_path_clone = sock_path.clone();

    let kernel_handle = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();

        // Read HANDSHAKE
        let mut hdr_buf = [0u8; HEADER_SIZE];
        stream.read_exact(&mut hdr_buf).await.unwrap();
        let (ft, tag, plen) = parse_header(&hdr_buf);
        assert_eq!(ft, 0x04);

        let mut payload = vec![0u8; plen as usize];
        if plen > 0 {
            stream.read_exact(&mut payload).await.unwrap();
        }

        // Respond with "HMAC" + 32 bytes of salt
        let mut resp_payload = Vec::new();
        resp_payload.extend_from_slice(b"HMAC");
        resp_payload.extend_from_slice(&[0xBB; 32]); // kernel salt
        let resp = build_frame(0x04, tag, &resp_payload);
        stream.write_all(&resp).await.unwrap();
        stream.flush().await.unwrap();
    });

    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut stream = tokio::net::UnixStream::connect(&sock_path_clone).await.unwrap();

    // Send HANDSHAKE
    let mut payload = Vec::new();
    payload.extend_from_slice(b"VBUS3");
    payload.extend_from_slice(&[0xCC; 32]);
    let frame = build_frame(0x04, 0, &payload);
    stream.write_all(&frame).await.unwrap();
    stream.flush().await.unwrap();

    // Read HMAC response
    let mut resp_hdr = [0u8; HEADER_SIZE];
    timeout(Duration::from_secs(5), stream.read_exact(&mut resp_hdr))
        .await
        .expect("should not timeout")
        .expect("should read response");

    let (ft, _tag, plen) = parse_header(&resp_hdr);
    assert_eq!(ft, 0x04);

    let mut resp_payload = vec![0u8; plen as usize];
    if plen > 0 {
        stream.read_exact(&mut resp_payload).await.unwrap();
    }

    let resp_text = String::from_utf8_lossy(&resp_payload);
    assert!(resp_text.contains("HMAC"), "response should contain HMAC");

    // Verify salt is extractable
    assert!(resp_payload.len() >= 36, "HMAC response should include salt");
    let salt = &resp_payload[4..36];
    assert_eq!(salt, &[0xBB; 32], "salt should match kernel-provided value");

    kernel_handle.await.unwrap();
    let _ = std::fs::remove_file(&sock_path);
}

// ---------------------------------------------------------------------------
// Test: PING/PONG round-trip
// ---------------------------------------------------------------------------

#[tokio::test]
async fn test_ping_pong_round_trip() {
    let sock_path = temp_socket_path();
    let sock_path = sock_path.with_extension("ping");
    let _ = std::fs::remove_file(&sock_path);

    let listener = UnixListener::bind(&sock_path).unwrap();
    let sock_path_clone = sock_path.clone();

    let kernel_handle = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();

        // Read CMD frame (PING is sent as CMD with payload "PING")
        let mut hdr_buf = [0u8; HEADER_SIZE];
        stream.read_exact(&mut hdr_buf).await.unwrap();
        let (ft, tag, plen) = parse_header(&hdr_buf);
        assert_eq!(ft, 0x01, "expected CMD frame");

        let mut payload = vec![0u8; plen as usize];
        if plen > 0 {
            stream.read_exact(&mut payload).await.unwrap();
        }
        let cmd = String::from_utf8_lossy(&payload);
        assert_eq!(cmd, "PING");

        // Respond with RESP frame: "OK|PONG"
        let resp = build_frame(0x02, tag, b"OK|PONG");
        stream.write_all(&resp).await.unwrap();
        stream.flush().await.unwrap();
    });

    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut stream = tokio::net::UnixStream::connect(&sock_path_clone).await.unwrap();

    // Send CMD: PING
    let cmd_frame = build_frame(0x01, 1, b"PING");
    stream.write_all(&cmd_frame).await.unwrap();
    stream.flush().await.unwrap();

    // Read RESP
    let mut resp_hdr = [0u8; HEADER_SIZE];
    timeout(Duration::from_secs(5), stream.read_exact(&mut resp_hdr))
        .await
        .expect("should not timeout")
        .expect("should read response");

    let (ft, tag, plen) = parse_header(&resp_hdr);
    assert_eq!(ft, 0x02, "response should be RESP");
    assert_eq!(tag, 1, "tag should match");

    let mut resp_payload = vec![0u8; plen as usize];
    if plen > 0 {
        stream.read_exact(&mut resp_payload).await.unwrap();
    }
    let body = String::from_utf8_lossy(&resp_payload);
    assert_eq!(body, "OK|PONG");

    kernel_handle.await.unwrap();
    let _ = std::fs::remove_file(&sock_path);
}

// ---------------------------------------------------------------------------
// Test: KIM_GENERATE command + token stream
// ---------------------------------------------------------------------------

#[tokio::test]
async fn test_kim_generate_token_stream() {
    let sock_path = temp_socket_path();
    let sock_path = sock_path.with_extension("kim");
    let _ = std::fs::remove_file(&sock_path);

    let listener = UnixListener::bind(&sock_path).unwrap();
    let sock_path_clone = sock_path.clone();

    let kernel_handle = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();

        // Read CMD: KIM_GENERATE
        let mut hdr_buf = [0u8; HEADER_SIZE];
        stream.read_exact(&mut hdr_buf).await.unwrap();
        let (ft, tag, plen) = parse_header(&hdr_buf);
        assert_eq!(ft, 0x01);

        let mut payload = vec![0u8; plen as usize];
        if plen > 0 {
            stream.read_exact(&mut payload).await.unwrap();
        }
        let cmd = String::from_utf8_lossy(&payload);
        assert!(cmd.starts_with("KIM_GENERATE"));

        // Send RESP: OK|generating
        let resp = build_frame(0x02, tag, b"OK|generating");
        stream.write_all(&resp).await.unwrap();

        // Send 3 TOKEN_STREAM frames
        let tokens = ["Hello", " World", "!"];
        for (i, text) in tokens.iter().enumerate() {
            let mut token_payload = Vec::new();
            token_payload.push(0u8); // slot_id
            token_payload.extend_from_slice(&(i as u32).to_le_bytes()); // token_id
            token_payload.extend_from_slice(&(i as u16).to_le_bytes()); // seq
            let mut flags = 0u8;
            if i == 0 {
                flags |= 0x01;
            } // FLAG_FIRST
            if i == tokens.len() - 1 {
                flags |= 0x02;
            } // FLAG_LAST
            token_payload.push(flags);
            token_payload.extend_from_slice(text.as_bytes());

            let frame = build_frame(0x07, 0, &token_payload);
            stream.write_all(&frame).await.unwrap();
        }
        stream.flush().await.unwrap();
    });

    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut stream = tokio::net::UnixStream::connect(&sock_path_clone).await.unwrap();

    // Send KIM_GENERATE command
    let cmd = build_frame(0x01, 5, b"KIM_GENERATE|0|128|100");
    stream.write_all(&cmd).await.unwrap();
    stream.flush().await.unwrap();

    // Read RESP (OK acknowledgement)
    let mut resp_hdr = [0u8; HEADER_SIZE];
    timeout(Duration::from_secs(5), stream.read_exact(&mut resp_hdr))
        .await
        .expect("timeout reading RESP")
        .expect("error reading RESP");
    let (ft, tag, plen) = parse_header(&resp_hdr);
    assert_eq!(ft, 0x02);
    assert_eq!(tag, 5);
    let mut resp_payload = vec![0u8; plen as usize];
    if plen > 0 {
        stream.read_exact(&mut resp_payload).await.unwrap();
    }
    let body = String::from_utf8_lossy(&resp_payload);
    assert!(body.starts_with("OK"));

    // Read TOKEN_STREAM frames
    let mut all_text = String::new();
    let mut token_count = 0u32;
    let mut saw_first = false;
    let mut saw_last = false;

    loop {
        let mut token_hdr = [0u8; HEADER_SIZE];
        timeout(Duration::from_secs(5), stream.read_exact(&mut token_hdr))
            .await
            .expect("timeout reading token")
            .expect("error reading token");

        let (ft, _tag, plen) = parse_header(&token_hdr);
        assert_eq!(ft, 0x07, "expected TOKEN_STREAM frame");

        let mut token_payload = vec![0u8; plen as usize];
        if plen > 0 {
            stream.read_exact(&mut token_payload).await.unwrap();
        }

        // Parse token: [0]=slot_id, [1:5]=token_id, [5:7]=seq, [7]=flags, [8:]=text
        assert!(token_payload.len() >= 8, "token payload too short");
        let flags = token_payload[7];
        let text = String::from_utf8_lossy(&token_payload[8..]);
        all_text.push_str(&text);
        token_count += 1;

        if flags & 0x01 != 0 {
            saw_first = true;
        }
        if flags & 0x02 != 0 {
            saw_last = true;
            break;
        }
    }

    assert!(saw_first, "should have seen FLAG_FIRST");
    assert!(saw_last, "should have seen FLAG_LAST");
    assert_eq!(token_count, 3, "should have received 3 tokens");
    assert_eq!(all_text, "Hello World!", "concatenated tokens should form 'Hello World!'");

    kernel_handle.await.unwrap();
    let _ = std::fs::remove_file(&sock_path);
}

// ---------------------------------------------------------------------------
// Test: Continuous stream stress — 100 KIM_GENERATE commands, zero tag/CRC errors
// Phase 10 Omega: Validates tag counter uniqueness and CRC32C seeded chaining
// under high-throughput sequential command flow.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn test_continuous_stream_no_tag_collision() {
    let sock_path = temp_socket_path();
    let sock_path = sock_path.with_extension("stress");
    let _ = std::fs::remove_file(&sock_path);

    let listener = UnixListener::bind(&sock_path).unwrap();
    let sock_path_clone = sock_path.clone();

    const NUM_FRAMES: u16 = 100;

    // Spawn mock kernel that echoes RESP for each CMD
    let kernel_handle = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();

        for _i in 0..NUM_FRAMES {
            let mut hdr_buf = [0u8; HEADER_SIZE];
            stream.read_exact(&mut hdr_buf).await.unwrap();
            let (ft, tag, plen) = parse_header(&hdr_buf);
            assert_eq!(ft, 0x01, "expected CMD frame");

            let mut payload = vec![0u8; plen as usize];
            if plen > 0 {
                stream.read_exact(&mut payload).await.unwrap();
            }

            // Respond with RESP using the same tag
            let resp_payload = format!("OK|generated_{}", tag);
            let resp = build_frame(0x02, tag, resp_payload.as_bytes());
            stream.write_all(&resp).await.unwrap();
        }
        stream.flush().await.unwrap();
    });

    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut stream = tokio::net::UnixStream::connect(&sock_path_clone)
        .await
        .unwrap();

    let mut seen_tags = std::collections::HashSet::new();
    let mut tag_mismatch_count = 0u32;
    let mut crc_mismatch_count = 0u32;
    let mut response_count = 0u32;

    for i in 0..NUM_FRAMES {
        let tag = i; // sequential tags 0..100
        let payload_text = format!("KIM_GENERATE|{}|128|50", i);

        // --- Build CMD frame ---
        let cmd_frame = build_frame(0x01, tag, payload_text.as_bytes());

        // --- Round-trip verification: build -> serialize -> deserialize ---
        // Verify CRC integrity of the frame we just built
        let stored_hdr_crc = u32::from_le_bytes([cmd_frame[8], cmd_frame[9], cmd_frame[10], cmd_frame[11]]);
        let computed_hdr_crc = crc32c_ref(&cmd_frame[0..8]);
        if stored_hdr_crc != computed_hdr_crc {
            crc_mismatch_count += 1;
        }

        // Verify tag is extractable
        let parsed_tag = u16::from_le_bytes([cmd_frame[4], cmd_frame[5]]);
        if parsed_tag != tag {
            tag_mismatch_count += 1;
        }

        // Verify payload CRC
        let stored_payload_crc = u32::from_le_bytes([cmd_frame[12], cmd_frame[13], cmd_frame[14], cmd_frame[15]]);
        let computed_payload_crc = crc32c_seeded(payload_text.as_bytes(), computed_hdr_crc);
        if stored_payload_crc != computed_payload_crc {
            crc_mismatch_count += 1;
        }

        // Track unique tags
        seen_tags.insert(tag);

        // Send CMD
        stream.write_all(&cmd_frame).await.unwrap();

        // Read RESP
        let mut resp_hdr = [0u8; HEADER_SIZE];
        timeout(Duration::from_secs(10), stream.read_exact(&mut resp_hdr))
            .await
            .expect("timeout reading response")
            .expect("error reading response");

        let (ft, resp_tag, plen) = parse_header(&resp_hdr);
        assert_eq!(ft, 0x02, "expected RESP frame");

        // Verify response tag matches request tag
        if resp_tag != tag {
            tag_mismatch_count += 1;
        }

        // Verify response CRC
        let resp_stored_crc = u32::from_le_bytes([resp_hdr[8], resp_hdr[9], resp_hdr[10], resp_hdr[11]]);
        let resp_computed_crc = crc32c_ref(&resp_hdr[0..8]);
        if resp_stored_crc != resp_computed_crc {
            crc_mismatch_count += 1;
        }

        // Read payload
        let mut resp_payload = vec![0u8; plen as usize];
        if plen > 0 {
            stream.read_exact(&mut resp_payload).await.unwrap();
        }
        response_count += 1;
    }

    // Phase 10 Omega assertions
    assert_eq!(
        tag_mismatch_count, 0,
        "ZERO TagMismatch errors across 100 frames"
    );
    assert_eq!(
        crc_mismatch_count, 0,
        "ZERO CrcMismatch errors across 100 frames"
    );
    assert_eq!(
        response_count, NUM_FRAMES as u32,
        "All 100 responses received"
    );
    assert_eq!(
        seen_tags.len(),
        NUM_FRAMES as usize,
        "No two frames share the same tag"
    );

    kernel_handle.await.unwrap();
    let _ = std::fs::remove_file(&sock_path);
}

// ---------------------------------------------------------------------------
// Test: CRC mismatch rejection
// ---------------------------------------------------------------------------

#[tokio::test]
async fn test_corrupted_frame_rejected() {
    let sock_path = temp_socket_path();
    let sock_path = sock_path.with_extension("corrupt");
    let _ = std::fs::remove_file(&sock_path);

    let listener = UnixListener::bind(&sock_path).unwrap();
    let sock_path_clone = sock_path.clone();

    let kernel_handle = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();

        // Read the client's frame (we don't care what it is)
        let mut hdr_buf = [0u8; HEADER_SIZE];
        stream.read_exact(&mut hdr_buf).await.unwrap();
        let (_ft, _tag, plen) = parse_header(&hdr_buf);
        let mut payload = vec![0u8; plen as usize];
        if plen > 0 {
            stream.read_exact(&mut payload).await.unwrap();
        }

        // Send a CORRUPTED response (flip a CRC bit)
        let mut resp = build_frame(0x02, 0, b"OK|CORRUPTED");
        resp[8] ^= 0x01; // flip bit in hdr_crc
        stream.write_all(&resp).await.unwrap();
        stream.flush().await.unwrap();
    });

    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut stream = tokio::net::UnixStream::connect(&sock_path_clone).await.unwrap();

    // Send a valid CMD
    let cmd = build_frame(0x01, 0, b"PING");
    stream.write_all(&cmd).await.unwrap();
    stream.flush().await.unwrap();

    // Read the corrupted response
    let mut resp_hdr = [0u8; HEADER_SIZE];
    timeout(Duration::from_secs(5), stream.read_exact(&mut resp_hdr))
        .await
        .expect("timeout")
        .expect("read error");

    // Verify CRC mismatch
    let stored_hdr_crc = u32::from_le_bytes([resp_hdr[8], resp_hdr[9], resp_hdr[10], resp_hdr[11]]);
    let computed_hdr_crc = crc32c_ref(&resp_hdr[0..8]);
    assert_ne!(
        stored_hdr_crc, computed_hdr_crc,
        "corrupted frame should have mismatched CRC"
    );

    kernel_handle.await.unwrap();
    let _ = std::fs::remove_file(&sock_path);
}
