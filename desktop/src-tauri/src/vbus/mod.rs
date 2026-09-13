/// VBus v3 protocol stack for VOS3 kernel communication.
///
/// Provides CRC32C integrity, HMAC-SHA256 authentication, HKDF key derivation,
/// AAAK (Token-Aware Shorthand) codec, binary frame serialization, async Unix
/// socket transport, and high-level command wrappers.

pub mod aaak;
pub mod client;
pub mod commands;
pub mod crc32c;
pub mod hmac;
pub mod protocol;

pub use client::VBusClient;
#[allow(unused_imports)]
pub use client::ClientError;
#[allow(unused_imports)]
pub use client::TokenFrame;
#[allow(unused_imports)]
pub use protocol::{Frame, FrameHeader, FrameType, ProtocolError};
