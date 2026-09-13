/// Tauri IPC command handlers for the VOS3 Enclave.
///
/// Each `#[tauri::command]` function is registered in `main.rs` and callable
/// from the frontend via `invoke("command_name", { ... })`.
///
/// Provenance: 100% original VOS3 code. Channel pattern guided by official
/// Tauri 2.0 docs (https://v2.tauri.app/develop/calling-rust/) — re-implemented
/// in VOS3 style with tagged enum streaming. Audited 2026-04-12.

use std::path::{Path, PathBuf};

use serde::Serialize;
use tauri::ipc::Channel;
use tauri::State;

use crate::qemu::QemuManager;
use crate::state::AppState;
use crate::vbus::VBusClient;
use crate::warp::WarpDrive;

// ---------------------------------------------------------------------------
// W1.1 (REV-2.1) — Path validation helpers for IPC commands accepting paths.
//
// Rationale: every `tauri::command` taking a user-controlled path is an
// RCE surface unless the path is canonicalised (resolving symlinks) and
// then verified to live under one of an allowlist of trusted roots.
// This mirrors what `tauri::scope::Scope::is_allowed` does internally
// in v2 — we apply the same logic at the command boundary so the gate
// fires even when the `tauri-plugin-fs` plugin isn't loaded.
//
// Trusted roots (for kernel binaries):
//   1. `$VOS3_KERNEL_DIR` if set (sovereign-mode operator override)
//   2. `$HOME/.vos3/kernels`     (default install location)
//   3. `$VOS3_DEV_KERNEL_PATH`   ONLY when `cfg!(debug_assertions)` —
//                                 dev-mode override; ignored in release.
//
// References:
//   Tauri v2 Capability docs   https://v2.tauri.app/reference/acl/capability/
//   GHSA-q9wv-22m9-vhqh        Filesystem Scope partial bypass
//   GHSA-6mv3-wm7j-h4w5        Glob Pattern too permissive
// ---------------------------------------------------------------------------

/// Build the list of allowed kernel-binary root directories at runtime.
fn allowed_kernel_roots() -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();

    if let Ok(custom) = std::env::var("VOS3_KERNEL_DIR") {
        if !custom.is_empty() {
            if let Ok(canonical) = PathBuf::from(custom).canonicalize() {
                roots.push(canonical);
            }
        }
    }

    if let Some(home) = std::env::var_os("HOME") {
        let default_root = PathBuf::from(home).join(".vos3").join("kernels");
        if let Ok(canonical) = default_root.canonicalize() {
            roots.push(canonical);
        }
    }

    // Dev-mode escape hatch — release builds (`cargo tauri build`) ignore it.
    #[cfg(debug_assertions)]
    {
        if let Ok(dev_root) = std::env::var("VOS3_DEV_KERNEL_PATH") {
            if !dev_root.is_empty() {
                if let Ok(canonical) = PathBuf::from(dev_root).canonicalize() {
                    roots.push(canonical);
                }
            }
        }
    }

    roots
}

/// Canonicalise + scope-check a user-supplied kernel path.
///
/// Returns the canonical form of `input` if it resolves under any
/// trusted root AND has an allowed extension. Symlinks are resolved
/// before the prefix check, so a symlink-poisoning attack of the form
/// `<allowed-root>/foo.elf -> /etc/shadow` is rejected.
fn validate_kernel_path(input: &str) -> Result<PathBuf, String> {
    if input.is_empty() {
        return Err("kernel_path is empty".into());
    }

    let candidate = Path::new(input);
    let canonical = candidate
        .canonicalize()
        .map_err(|e| format!("kernel_path does not resolve: {e}"))?;

    // Allowed extension (mirrors the kernel-binary file types we accept).
    let ext_ok = canonical
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| s.eq_ignore_ascii_case("elf") || s.eq_ignore_ascii_case("efi"))
        .unwrap_or(false);
    if !ext_ok {
        return Err(format!(
            "kernel_path must end with .elf or .efi (got: {})",
            canonical.display()
        ));
    }

    // Scope check — canonical path must live under one of the trusted roots.
    let roots = allowed_kernel_roots();
    if roots.is_empty() {
        return Err(
            "no allowed kernel root configured (set VOS3_KERNEL_DIR or place \
             kernels under $HOME/.vos3/kernels)"
                .into(),
        );
    }
    let allowed = roots.iter().any(|r| canonical.starts_with(r));
    if !allowed {
        return Err(format!(
            "kernel_path is outside every allowed root: canonical={} roots={:?}",
            canonical.display(),
            roots
        ));
    }

    Ok(canonical)
}

/// Canonicalise + lightly validate a user-supplied QEMU binary path.
///
/// QEMU is a system tool — its location is OS-distribution-defined
/// (`/usr/bin/qemu-system-x86_64`, `/opt/homebrew/bin/qemu-system-x86_64`).
/// We don't pin it to a sovereign-app-resource root; we only require:
///   - the path canonicalises (i.e. the binary actually exists);
///   - the basename starts with `qemu-system-` to refuse unrelated binaries.
fn validate_qemu_path(input: &str) -> Result<PathBuf, String> {
    if input.is_empty() {
        return Err("qemu_path is empty".into());
    }
    let canonical = Path::new(input)
        .canonicalize()
        .map_err(|e| format!("qemu_path does not resolve: {e}"))?;
    let basename_ok = canonical
        .file_name()
        .and_then(|s| s.to_str())
        .map(|s| s.starts_with("qemu-system-"))
        .unwrap_or(false);
    if !basename_ok {
        return Err(format!(
            "qemu_path basename must start with 'qemu-system-' (got: {})",
            canonical.display()
        ));
    }
    Ok(canonical)
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Maximum valid AI slot index, derived from Warp Drive zone count.
const MAX_SLOT_ID: u8 = (crate::warp::ZONE_COUNT - 1) as u8;

/// Maximum message length for send_chat (32 KiB).
const MAX_MESSAGE_LEN: usize = 32_768;

/// Allowed model file extensions for load_model path validation.
const ALLOWED_MODEL_EXTENSIONS: &[&str] = &["gguf", "ggml", "bin", "safetensors"];

// ---------------------------------------------------------------------------
// Channel payload for model-load progress streaming (Tauri 2.0 Channels)
// ---------------------------------------------------------------------------

/// Default maximum tokens for inference when the caller does not specify.
const DEFAULT_MAX_TOKENS: u32 = 128;

/// Default temperature in x100 fixed-point (1.0 = 100).
const DEFAULT_TEMPERATURE: u16 = 100;

/// Progress event streamed to the frontend via Tauri Channel during model load.
#[derive(Clone, Serialize)]
#[serde(tag = "event", content = "data")]
pub enum ModelLoadEvent {
    /// Emitted once when the load begins.
    #[serde(rename = "started")]
    Started {
        slot_id: u8,
        total_bytes: u64,
    },
    /// Emitted after each chunk is written to Warp Drive.
    #[serde(rename = "progress")]
    Progress {
        slot_id: u8,
        bytes_written: u64,
        total_bytes: u64,
    },
    /// Emitted once when the load completes successfully.
    #[serde(rename = "finished")]
    Finished {
        slot_id: u8,
        total_bytes: u64,
    },
}

// ---------------------------------------------------------------------------
// Channel payload for chat streaming (Tauri 2.0 Channels)
// ---------------------------------------------------------------------------

/// Streaming event sent to the frontend via Tauri Channel during inference.
///
/// The frontend receives a sequence of `TokenReceived` events (one per token),
/// followed by either a `Done` event (successful completion) or an `Error`
/// event (inference failure).
#[derive(Clone, Serialize)]
#[serde(tag = "event", content = "data")]
pub enum ChatStreamEvent {
    /// A single token has been generated.
    #[serde(rename = "token")]
    TokenReceived {
        /// The decoded text for this token.
        text: String,
        /// Sequence number within the generation (0-based).
        seq: u16,
    },
    /// The generation completed successfully.
    #[serde(rename = "done")]
    Done {
        /// The full concatenated text of all tokens.
        full_text: String,
    },
    /// An error occurred during generation.
    #[serde(rename = "error")]
    Error {
        /// Human-readable error description.
        message: String,
    },
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Converts any error into a serialisable string for Tauri IPC.
fn err_str<E: std::fmt::Display>(e: E) -> String {
    e.to_string()
}

/// Validates that `slot_id` is within the 0–7 range.
fn validate_slot_id(slot_id: u8) -> Result<(), String> {
    if slot_id > MAX_SLOT_ID {
        return Err(format!(
            "slot_id {slot_id} out of range (must be 0-{MAX_SLOT_ID})"
        ));
    }
    Ok(())
}

/// Attempts to locate the QEMU binary on the system PATH.
fn find_qemu() -> Result<PathBuf, String> {
    which_qemu()
        .ok_or_else(|| "qemu-system-x86_64 not found in PATH".to_string())
}

/// Searches common locations for qemu-system-x86_64.
///
/// Uses `where` on Windows and `which` on Unix for cross-platform support.
fn which_qemu() -> Option<PathBuf> {
    let candidates = [
        "qemu-system-x86_64",
    ];
    let lookup_cmd = if cfg!(target_os = "windows") { "where" } else { "which" };
    for name in &candidates {
        if let Ok(output) = std::process::Command::new(lookup_cmd)
            .arg(name)
            .output()
        {
            if output.status.success() {
                let raw = String::from_utf8_lossy(&output.stdout);
                // `where` on Windows may return multiple lines; take the first.
                let path = raw.lines().next().unwrap_or("").trim();
                if !path.is_empty() {
                    return Some(PathBuf::from(path));
                }
            }
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Kernel lifecycle
// ---------------------------------------------------------------------------

/// Starts the VOS3 kernel in QEMU and connects VBus + Warp Drive.
///
/// # Arguments
///
/// * `kernel_path` - Path to the `vos3.elf` kernel binary.
/// * `qemu_path` - Optional path to the QEMU binary. If `None`, searches PATH.
#[tauri::command]
pub async fn start_kernel(
    state: State<'_, AppState>,
    kernel_path: String,
    qemu_path: Option<String>,
) -> Result<String, String> {
    // W1.1 (REV-2.1) — canonicalise + scope-check the user-supplied paths
    // BEFORE handing them to QemuManager. This is the IPC trust boundary;
    // anything below this line treats the paths as already-validated.
    let kernel = validate_kernel_path(&kernel_path)?;
    let qemu = match qemu_path {
        Some(ref p) => validate_qemu_path(p)?,
        None => find_qemu()?,
    };
    tracing::info!(
        "start_kernel: paths validated kernel={} qemu={}",
        kernel.display(),
        qemu.display()
    );

    // Create and start QemuManager.
    let mut manager = QemuManager::new(kernel, qemu).map_err(err_str)?;
    manager.start().map_err(err_str)?;

    let socket_path = manager.socket_path();
    let warp_path = manager.warp_path();

    // Store QemuManager.
    {
        let mut qemu_lock = state.qemu.lock().map_err(err_str)?;
        *qemu_lock = Some(manager);
    }

    // Wait for kernel boot before VBus connect.
    tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;

    // Connect VBus.
    let mut vbus_client = VBusClient::new();
    vbus_client.connect(&socket_path).await.map_err(err_str)?;

    {
        let mut vbus_lock = state.vbus.lock().await;
        *vbus_lock = Some(vbus_client);
    }

    // Open Warp Drive.
    let mut warp_drive = WarpDrive::new(warp_path);
    warp_drive.open().map_err(err_str)?;

    {
        let mut warp_lock = state.warp.lock().map_err(err_str)?;
        *warp_lock = Some(warp_drive);
    }

    Ok("kernel started, VBus connected, Warp Drive open".to_string())
}

/// Stops the VOS3 kernel and disconnects all subsystems.
#[tauri::command]
pub async fn stop_kernel(state: State<'_, AppState>) -> Result<String, String> {
    // Disconnect VBus.
    {
        let mut vbus_lock = state.vbus.lock().await;
        if let Some(ref mut client) = *vbus_lock {
            client.disconnect().await;
        }
        *vbus_lock = None;
    }

    // Close Warp Drive.
    {
        let mut warp_lock = state.warp.lock().map_err(err_str)?;
        if let Some(ref mut warp) = *warp_lock {
            warp.close();
        }
        *warp_lock = None;
    }

    // Shutdown QEMU.
    {
        let mut qemu_lock = state.qemu.lock().map_err(err_str)?;
        if let Some(ref mut manager) = *qemu_lock {
            let _ = manager.shutdown(); // Best-effort.
        }
        *qemu_lock = None;
    }

    Ok("kernel stopped".to_string())
}

/// Returns the current kernel/VBus/Warp status.
#[tauri::command]
pub async fn kernel_status(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let qemu_alive = {
        let mut qemu_lock = state.qemu.lock().map_err(err_str)?;
        match qemu_lock.as_mut() {
            Some(manager) => manager.is_alive(),
            None => false,
        }
    };

    let vbus_connected = {
        let vbus_lock = state.vbus.lock().await;
        vbus_lock.as_ref().map_or(false, |c| c.is_connected())
    };

    let vbus_hmac = {
        let vbus_lock = state.vbus.lock().await;
        vbus_lock.as_ref().map_or(false, |c| c.is_hmac_enabled())
    };

    let warp_open = {
        let warp_lock = state.warp.lock().map_err(err_str)?;
        warp_lock.as_ref().map_or(false, |w| w.is_open())
    };

    Ok(serde_json::json!({
        "qemu_alive": qemu_alive,
        "vbus_connected": vbus_connected,
        "vbus_hmac": vbus_hmac,
        "warp_open": warp_open,
    }))
}

// ---------------------------------------------------------------------------
// VBus commands
// ---------------------------------------------------------------------------

/// Sends a VBus PING and returns the response.
#[tauri::command]
pub async fn vbus_ping(state: State<'_, AppState>) -> Result<String, String> {
    let mut vbus_lock = state.vbus.lock().await;
    let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
    crate::vbus::commands::ping(client).await.map_err(err_str)
}

/// Queries kernel system information via VBus.
#[tauri::command]
pub async fn system_info(state: State<'_, AppState>) -> Result<String, String> {
    let mut vbus_lock = state.vbus.lock().await;
    let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
    crate::vbus::commands::sysinfo(client)
        .await
        .map_err(err_str)
}

/// Lists all AI model slots and their states.
#[tauri::command]
pub async fn list_slots(state: State<'_, AppState>) -> Result<String, String> {
    let mut vbus_lock = state.vbus.lock().await;
    let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
    crate::vbus::commands::slot_enumerate(client)
        .await
        .map_err(err_str)
}

/// Loads a model file into a kernel AI slot via Warp Drive.
///
/// Uses Tauri 2.0 Channel for real-time progress streaming to the frontend.
/// The `on_progress` channel receives [`ModelLoadEvent`] messages: started,
/// progress (per-chunk), and finished.
///
/// # Arguments
///
/// * `model_path` - Path to the GGML/GGUF model file on the host.
/// * `slot_id` - Target AI slot (0–7).
/// * `on_progress` - Tauri Channel for streaming progress events.
///
/// # Security
///
/// - Path is canonicalized to prevent symlink traversal.
/// - File extension is validated against an allowlist.
/// - Slot ID is range-checked (0–7).
#[tauri::command]
pub async fn load_model(
    state: State<'_, AppState>,
    model_path: String,
    slot_id: u8,
    on_progress: Channel<ModelLoadEvent>,
) -> Result<String, String> {
    // Security: validate slot ID range.
    validate_slot_id(slot_id)?;

    // Security: canonicalize path to resolve symlinks and prevent traversal.
    let model = PathBuf::from(&model_path)
        .canonicalize()
        .map_err(|e| format!("path resolution failed: {e}"))?;

    // Security: validate file extension against allowlist.
    let ext = model
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("");
    if !ALLOWED_MODEL_EXTENSIONS.contains(&ext) {
        return Err(format!("unsupported model format: .{ext}"));
    }

    // Start slot.
    {
        let mut vbus_lock = state.vbus.lock().await;
        let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
        crate::vbus::commands::slot_start(client, slot_id)
            .await
            .map_err(err_str)?;
    }

    // Stream model to Warp Drive in 8KB chunks (avoids reading entire file into RAM).
    use std::io::Read;
    let mut file = std::fs::File::open(&model).map_err(err_str)?;
    let total_bytes = file.metadata().map_err(err_str)?.len();
    let chunk_size = 8 * 1024; // 8KB chunks (MAX_CHUNK_SIZE from bridge hardening)
    let mut offset = 0usize;
    let mut buf = vec![0u8; chunk_size];

    // Stream: started event.
    let _ = on_progress.send(ModelLoadEvent::Started {
        slot_id,
        total_bytes,
    });

    {
        let mut warp_lock = state.warp.lock().map_err(err_str)?;
        let warp = warp_lock.as_mut().ok_or("Warp Drive not open")?;

        loop {
            let n = file.read(&mut buf).map_err(err_str)?;
            if n == 0 {
                break;
            }
            warp.write_chunk(slot_id as usize, offset, &buf[..n])
                .map_err(err_str)?;
            offset += n;

            // Stream: progress event per chunk.
            let _ = on_progress.send(ModelLoadEvent::Progress {
                slot_id,
                bytes_written: offset as u64,
                total_bytes,
            });
        }

        // Use flush_async (msync MS_ASYNC) to avoid blocking the UI thread.
        // This is synchronous at the Rust level but non-blocking at the OS level.
        warp.flush_nonblocking().map_err(err_str)?;
    }

    // Notify kernel via WARP_POST.
    {
        let mut vbus_lock = state.vbus.lock().await;
        let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
        crate::vbus::commands::warp_post(client, slot_id, 0, total_bytes)
            .await
            .map_err(err_str)?;
    }

    // Finalize slot.
    {
        let mut vbus_lock = state.vbus.lock().await;
        let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
        crate::vbus::commands::slot_finish(client, slot_id)
            .await
            .map_err(err_str)?;
    }

    // Stream: finished event.
    let _ = on_progress.send(ModelLoadEvent::Finished {
        slot_id,
        total_bytes,
    });

    Ok(format!(
        "model loaded into slot {slot_id} ({total_bytes} bytes)"
    ))
}

/// Reads the latest inference output from a slot's Warp zone (first 4KB).
///
/// # Security
///
/// - Slot ID is range-checked (0–7).
#[tauri::command]
pub async fn read_inference_output(
    state: State<'_, AppState>,
    slot_id: u8,
) -> Result<Vec<u8>, String> {
    validate_slot_id(slot_id)?;
    let warp_lock = state.warp.lock().map_err(err_str)?;
    let warp = warp_lock.as_ref().ok_or("Warp Drive not open")?;
    warp.read_zone_raw(slot_id as usize, 0, 4096)
        .map_err(err_str)
}

/// General-purpose zone read with caller-specified offset and length.
///
/// # Security
///
/// - Slot ID is range-checked (0–7).
/// - Read length capped at 1MB for IPC safety.
#[tauri::command]
pub async fn warp_read_zone(
    state: State<'_, AppState>,
    slot_id: u8,
    offset: usize,
    len: usize,
) -> Result<Vec<u8>, String> {
    validate_slot_id(slot_id)?;
    if len > 1024 * 1024 {
        return Err("read length exceeds 1MB limit".to_string());
    }
    let warp_lock = state.warp.lock().map_err(err_str)?;
    let warp = warp_lock.as_ref().ok_or("Warp Drive not open")?;
    warp.read_zone_raw(slot_id as usize, offset, len)
        .map_err(err_str)
}

/// Sends a chat message to the kernel for inference and returns the full response.
///
/// Writes the prompt to the slot's Warp Drive zone, sends `KIM_GENERATE`,
/// reads the OK/ERR acknowledgement, then collects all TOKEN_STREAM frames
/// until FLAG_LAST. Returns the concatenated token text.
///
/// # Arguments
///
/// * `message` - The user prompt text (max 32 KiB).
/// * `slot_id` - Target AI slot (0-7, must be in READY state).
/// * `max_tokens` - Maximum tokens to generate (default: 128).
/// * `temperature` - Sampling temperature in x100 fixed-point (default: 100 = 1.0).
///
/// # Security
///
/// - Slot ID is range-checked (0-7).
/// - Message length is capped at 32 KiB.
#[tauri::command]
pub async fn send_chat(
    state: State<'_, AppState>,
    message: String,
    slot_id: u8,
    max_tokens: Option<u32>,
    temperature: Option<u16>,
) -> Result<String, String> {
    // Security: validate slot ID range.
    validate_slot_id(slot_id)?;

    // Security: enforce message size limit.
    if message.len() > MAX_MESSAGE_LEN {
        return Err(format!(
            "message too large: {} bytes (max {MAX_MESSAGE_LEN})",
            message.len()
        ));
    }

    let max_tok = max_tokens.unwrap_or(DEFAULT_MAX_TOKENS);
    let temp = temperature.unwrap_or(DEFAULT_TEMPERATURE);

    // Write the prompt into the slot's Warp Drive zone so the kernel can read it.
    {
        let mut warp_lock = state.warp.lock().map_err(err_str)?;
        let warp = warp_lock.as_mut().ok_or("Warp Drive not open")?;
        let prompt_bytes = message.as_bytes();
        warp.write_chunk(slot_id as usize, 0, prompt_bytes)
            .map_err(err_str)?;
        warp.flush_nonblocking().map_err(err_str)?;
    }

    // Notify kernel of the prompt data via WARP_POST.
    {
        let mut vbus_lock = state.vbus.lock().await;
        let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
        crate::vbus::commands::warp_post(client, slot_id, 0, message.len() as u64)
            .await
            .map_err(err_str)?;
    }

    // Send KIM_GENERATE command and read the acknowledgement.
    {
        let mut vbus_lock = state.vbus.lock().await;
        let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
        crate::vbus::commands::kim_generate(client, slot_id, max_tok, temp)
            .await
            .map_err(err_str)?;
    }

    // Read the token stream until completion.
    let tokens = {
        let mut vbus_lock = state.vbus.lock().await;
        let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
        client.read_token_stream().await.map_err(err_str)?
    };

    // Concatenate all token text into the final response.
    let full_text: String = tokens.iter().map(|t| t.text.as_str()).collect();

    Ok(full_text)
}

/// Streaming variant of [`send_chat`] that pushes tokens to the frontend in real time.
///
/// Uses Tauri 2.0 Channel pattern to emit [`ChatStreamEvent`] messages as
/// each token arrives from the kernel. The frontend receives `token` events
/// during generation, followed by a final `done` or `error` event.
///
/// # Arguments
///
/// * `message` - The user prompt text (max 32 KiB).
/// * `slot_id` - Target AI slot (0-7, must be in READY state).
/// * `max_tokens` - Maximum tokens to generate (default: 128).
/// * `temperature` - Sampling temperature in x100 fixed-point (default: 100 = 1.0).
/// * `on_token` - Tauri Channel for streaming [`ChatStreamEvent`] messages.
///
/// # Security
///
/// - Slot ID is range-checked (0-7).
/// - Message length is capped at 32 KiB.
#[tauri::command]
pub async fn send_chat_stream(
    state: State<'_, AppState>,
    message: String,
    slot_id: u8,
    max_tokens: Option<u32>,
    temperature: Option<u16>,
    on_token: Channel<ChatStreamEvent>,
) -> Result<String, String> {
    // Security: validate slot ID range.
    validate_slot_id(slot_id)?;

    // Security: enforce message size limit.
    if message.len() > MAX_MESSAGE_LEN {
        return Err(format!(
            "message too large: {} bytes (max {MAX_MESSAGE_LEN})",
            message.len()
        ));
    }

    let max_tok = max_tokens.unwrap_or(DEFAULT_MAX_TOKENS);
    let temp = temperature.unwrap_or(DEFAULT_TEMPERATURE);

    // Write the prompt into the slot's Warp Drive zone.
    {
        let mut warp_lock = state.warp.lock().map_err(err_str)?;
        let warp = warp_lock.as_mut().ok_or("Warp Drive not open")?;
        let prompt_bytes = message.as_bytes();
        warp.write_chunk(slot_id as usize, 0, prompt_bytes)
            .map_err(err_str)?;
        warp.flush_nonblocking().map_err(err_str)?;
    }

    // Notify kernel of the prompt data via WARP_POST.
    {
        let mut vbus_lock = state.vbus.lock().await;
        let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
        crate::vbus::commands::warp_post(client, slot_id, 0, message.len() as u64)
            .await
            .map_err(err_str)?;
    }

    // Send KIM_GENERATE command and read the acknowledgement.
    {
        let mut vbus_lock = state.vbus.lock().await;
        let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
        crate::vbus::commands::kim_generate(client, slot_id, max_tok, temp)
            .await
            .map_err(err_str)?;
    }

    // Read the token stream with per-token callbacks for real-time streaming.
    let result = {
        let mut vbus_lock = state.vbus.lock().await;
        let client = vbus_lock.as_mut().ok_or("VBus not connected")?;

        client
            .read_token_stream_with_callback(|token| {
                let _ = on_token.send(ChatStreamEvent::TokenReceived {
                    text: token.text.clone(),
                    seq: token.seq,
                });
            })
            .await
    };

    match result {
        Ok(tokens) => {
            let full_text: String = tokens.iter().map(|t| t.text.as_str()).collect();
            let _ = on_token.send(ChatStreamEvent::Done {
                full_text: full_text.clone(),
            });
            Ok(full_text)
        }
        Err(e) => {
            let msg = e.to_string();
            let _ = on_token.send(ChatStreamEvent::Error {
                message: msg.clone(),
            });
            Err(msg)
        }
    }
}

// ===========================================================================
// Stage 10.3 — Sovereign Control Panel IPC commands
//
// Thin pass-through to the kernel's POLICY_* / AUDIT_FAIL_QUOTE VBus commands.
// All four panel widgets (force-permit toggle, global-floor slider, live
// HALLUCINATION_BLOCK feed, real-time confidence) read these from the
// frontend. The shape is plain string, kept compatible with the rest of
// commands.rs — the frontend parses the wire format and the existing
// VBus driver handles framing / HMAC.
// ===========================================================================

/// POLICY_STATUS — read force_permit + per-slot gates for the dashboard.
/// Reply shape (passed through verbatim):
///   POLICY|force_permit=N|gates=g0,g1,g2,g3
#[tauri::command]
pub async fn policy_status(state: State<'_, AppState>) -> Result<String, String> {
    let mut vbus_lock = state.vbus.lock().await;
    let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
    client.send_command("POLICY_STATUS").await.map_err(err_str)
}

/// POLICY_FORCE_PERMIT — Safe-Rollout toggle.
/// Param `enabled` is the boolean from the frontend toggle; the kernel
/// expects 0 or 1.
#[tauri::command]
pub async fn policy_set_force_permit(
    state: State<'_, AppState>,
    enabled: bool,
) -> Result<String, String> {
    let mut vbus_lock = state.vbus.lock().await;
    let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
    let val = if enabled { 1 } else { 0 };
    client
        .send_command(&format!("POLICY_FORCE_PERMIT|{val}"))
        .await
        .map_err(err_str)
}

/// POLICY_OVERRIDE — direct per-slot threshold override.
/// `score` is the 0..1000 fixed-point value from the slider.
#[tauri::command]
pub async fn policy_override_slot(
    state: State<'_, AppState>,
    slot_id: u8,
    score: u16,
) -> Result<String, String> {
    if slot_id > MAX_SLOT_ID {
        return Err(format!(
            "slot_id {slot_id} out of range [0..{MAX_SLOT_ID}]"
        ));
    }
    if score > 1000 {
        return Err(format!("score {score} out of range [0..1000]"));
    }
    let mut vbus_lock = state.vbus.lock().await;
    let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
    client
        .send_command(&format!("POLICY_OVERRIDE|{slot_id}|{score}"))
        .await
        .map_err(err_str)
}

/// AUDIT_FAIL_QUOTE — pull the kernel's compliance failure ring.
/// The frontend parses the wire format (see vbus_ai_cmds.c) and maintains
/// its own dedup-by-seq cache; the backend's compliance_store is the
/// long-term archive (Stage 10.3.A). This Tauri command is the
/// dashboard's *real-time* read — sub-2s polling target.
#[tauri::command]
pub async fn policy_drain_audit(state: State<'_, AppState>) -> Result<String, String> {
    let mut vbus_lock = state.vbus.lock().await;
    let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
    client.send_command("AUDIT_FAIL_QUOTE").await.map_err(err_str)
}

/// ACTION_CHECK_CONFIDENCE — used by the panel's "test a confidence value"
/// fixture, which lets the operator manually exercise an agent's gate
/// from the dashboard without waiting for a real LLM emission. Helpful
/// for demoing the Safe-Rollout toggle on a live system.
#[tauri::command]
pub async fn policy_check_confidence(
    state: State<'_, AppState>,
    slot_id: u8,
    score: u16,
) -> Result<String, String> {
    if slot_id > MAX_SLOT_ID {
        return Err(format!(
            "slot_id {slot_id} out of range [0..{MAX_SLOT_ID}]"
        ));
    }
    if score > 1000 {
        return Err(format!("score {score} out of range [0..1000]"));
    }
    let mut vbus_lock = state.vbus.lock().await;
    let client = vbus_lock.as_mut().ok_or("VBus not connected")?;
    client
        .send_command(&format!("ACTION_CHECK_CONFIDENCE|{slot_id}|{score}"))
        .await
        .map_err(err_str)
}
