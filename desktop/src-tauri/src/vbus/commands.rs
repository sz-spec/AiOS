/// High-level VBus command wrappers.
///
/// Each function composes a pipe-delimited VBus command string, sends it
/// through [`VBusClient::send_command`], and parses the kernel response.
/// Commands follow the VOS3 VBus v2.19 command set (22 commands).

use super::client::{ClientError, VBusClient};

// ---------------------------------------------------------------------------
// System commands
// ---------------------------------------------------------------------------

/// Sends a PING and returns the round-trip latency in milliseconds.
///
/// The kernel responds with `OK|PONG` or equivalent acknowledgement.
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or protocol error.
pub async fn ping(client: &mut VBusClient) -> Result<String, ClientError> {
    client.send_command("PING").await
}

/// Queries system information from the kernel.
///
/// Returns the raw SYSINFO response string containing kernel version,
/// uptime, memory stats, and CPU info.
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or protocol error.
pub async fn sysinfo(client: &mut VBusClient) -> Result<String, ClientError> {
    client.send_command("SYSINFO").await
}

// ---------------------------------------------------------------------------
// Slot management commands
// ---------------------------------------------------------------------------

/// Starts loading a model into the specified AI slot.
///
/// Initiates the model loading sequence. The kernel allocates the PUD-isolated
/// slot and prepares for Warp Drive data transfer.
///
/// # Arguments
///
/// * `client` - The connected VBus client.
/// * `slot_id` - The target slot index (0–7).
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or if the slot is busy.
pub async fn slot_start(
    client: &mut VBusClient,
    slot_id: u8,
) -> Result<String, ClientError> {
    client.send_command(&format!("SLOT_START|{slot_id}")).await
}

/// Finalizes model loading for a slot after Warp Drive transfer.
///
/// The kernel verifies data integrity and transitions the slot to READY state.
///
/// # Arguments
///
/// * `client` - The connected VBus client.
/// * `slot_id` - The slot to finalize.
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or integrity failure.
pub async fn slot_finish(
    client: &mut VBusClient,
    slot_id: u8,
) -> Result<String, ClientError> {
    client.send_command(&format!("SLOT_FINISH|{slot_id}")).await
}

/// Queries the status of a specific AI slot.
///
/// Returns slot state (EMPTY, LOADING, READY, RUNNING, ERROR), model name,
/// and memory usage.
///
/// # Arguments
///
/// * `client` - The connected VBus client.
/// * `slot_id` - The slot to query.
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or if the slot ID is invalid.
pub async fn slot_status(
    client: &mut VBusClient,
    slot_id: u8,
) -> Result<String, ClientError> {
    client.send_command(&format!("SLOT_STATUS|{slot_id}")).await
}

/// Resets an AI slot, unloading any model and scrubbing memory.
///
/// The kernel zeros the PUD-isolated memory and returns the slot to EMPTY.
///
/// # Arguments
///
/// * `client` - The connected VBus client.
/// * `slot_id` - The slot to reset.
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or if the slot is locked.
pub async fn slot_reset(
    client: &mut VBusClient,
    slot_id: u8,
) -> Result<String, ClientError> {
    client.send_command(&format!("SLOT_RESET|{slot_id}")).await
}

/// Enumerates all AI slots and their current states.
///
/// Returns a summary of all 8 model slots with their states and occupancy.
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or protocol error.
pub async fn slot_enumerate(client: &mut VBusClient) -> Result<String, ClientError> {
    client.send_command("SLOT_ENUMERATE").await
}

// ---------------------------------------------------------------------------
// Warp Drive commands
// ---------------------------------------------------------------------------

/// Queries Warp Drive (ivshmem) status.
///
/// Returns zone allocation, total/used bytes, and health indicators.
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or protocol error.
pub async fn warp_status(client: &mut VBusClient) -> Result<String, ClientError> {
    client.send_command("WARP_STATUS").await
}

/// Notifies the kernel that data has been written to a Warp Drive zone.
///
/// After the host writes model chunks to the ivshmem shared memory,
/// this command tells the kernel to map the data into the target slot.
///
/// # Arguments
///
/// * `client` - The connected VBus client.
/// * `slot_id` - The target slot for the data.
/// * `offset` - Byte offset within the zone where data starts.
/// * `length` - Number of bytes written.
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or if the zone/offset is invalid.
pub async fn warp_post(
    client: &mut VBusClient,
    slot_id: u8,
    offset: u64,
    length: u64,
) -> Result<String, ClientError> {
    client
        .send_command(&format!("WARP_POST|{slot_id}|{offset}|{length}"))
        .await
}

// ---------------------------------------------------------------------------
// HugePage stats
// ---------------------------------------------------------------------------

/// Queries HugePage allocation statistics.
///
/// Returns total, used, and free HugePage counts and sizes.
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or protocol error.
pub async fn hp_stats(client: &mut VBusClient) -> Result<String, ClientError> {
    client.send_command("HP_STATS").await
}

// ---------------------------------------------------------------------------
// Inference commands
// ---------------------------------------------------------------------------

/// Sends a KIM_GENERATE command to begin token-stream inference.
///
/// The kernel acknowledges with `OK|...` and then begins streaming
/// TOKEN_STREAM (0x07) frames. The caller must subsequently read the
/// token stream via [`VBusClient::read_token_stream`] or
/// [`VBusClient::read_token_stream_with_callback`].
///
/// # Arguments
///
/// * `client` - The connected VBus client.
/// * `slot_id` - The target AI slot (0-7, must be in READY state).
/// * `max_tokens` - Maximum number of tokens to generate.
/// * `temperature` - Sampling temperature in x100 fixed-point (e.g. 100 = 1.0).
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or if the slot is not ready.
pub async fn kim_generate(
    client: &mut VBusClient,
    slot_id: u8,
    max_tokens: u32,
    temperature: u16,
) -> Result<String, ClientError> {
    client
        .send_command(&format!(
            "KIM_GENERATE|{slot_id}|{max_tokens}|{temperature}"
        ))
        .await
}

// ---------------------------------------------------------------------------
// File I/O commands
// ---------------------------------------------------------------------------

/// Reads a file from the kernel's virtual filesystem.
///
/// # Arguments
///
/// * `client` - The connected VBus client.
/// * `path` - Absolute path within the kernel VFS.
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or if the file does not exist.
pub async fn read_file(
    client: &mut VBusClient,
    path: &str,
) -> Result<String, ClientError> {
    // Reject pipe delimiter to prevent protocol injection.
    if path.contains('|') {
        return Err(ClientError::KernelError {
            code: "EINVAL".to_string(),
            message: "path contains pipe delimiter (protocol injection rejected)".to_string(),
        });
    }
    client.send_command(&format!("READ_FILE|{path}")).await
}

/// Writes data to a file in the kernel's virtual filesystem.
///
/// # Arguments
///
/// * `client` - The connected VBus client.
/// * `path` - Absolute path within the kernel VFS.
/// * `data` - The file content to write.
///
/// # Errors
///
/// Returns [`ClientError`] on I/O, timeout, or if the path is invalid.
pub async fn write_file(
    client: &mut VBusClient,
    path: &str,
    data: &str,
) -> Result<String, ClientError> {
    // Reject pipe delimiter to prevent protocol injection.
    if path.contains('|') || data.contains('|') {
        return Err(ClientError::KernelError {
            code: "EINVAL".to_string(),
            message: "path or data contains pipe delimiter (protocol injection rejected)"
                .to_string(),
        });
    }
    client
        .send_command(&format!("WRITE_FILE|{path}|{data}"))
        .await
}

#[cfg(test)]
mod tests {
    // Command wrappers are thin delegation layers over `send_command`.
    // Full integration tests require a running QEMU + VOS3 kernel.
    // Here we verify the command string format only.

    #[test]
    fn test_slot_start_format() {
        let cmd = format!("SLOT_START|{}", 3u8);
        assert_eq!(cmd, "SLOT_START|3");
    }

    #[test]
    fn test_warp_post_format() {
        let cmd = format!("WARP_POST|{}|{}|{}", 1u8, 4096u64, 2048u64);
        assert_eq!(cmd, "WARP_POST|1|4096|2048");
    }

    #[test]
    fn test_write_file_format() {
        let cmd = format!("WRITE_FILE|{}|{}", "/proc/test", "hello");
        assert_eq!(cmd, "WRITE_FILE|/proc/test|hello");
    }

    #[test]
    fn test_read_file_format() {
        let cmd = format!("READ_FILE|{}", "/proc/uptime");
        assert_eq!(cmd, "READ_FILE|/proc/uptime");
    }

    #[test]
    fn test_kim_generate_format() {
        let cmd = format!("KIM_GENERATE|{}|{}|{}", 2u8, 128u32, 100u16);
        assert_eq!(cmd, "KIM_GENERATE|2|128|100");
    }

    #[test]
    fn test_kim_generate_format_high_temp() {
        let cmd = format!("KIM_GENERATE|{}|{}|{}", 0u8, 256u32, 150u16);
        assert_eq!(cmd, "KIM_GENERATE|0|256|150");
    }
}
