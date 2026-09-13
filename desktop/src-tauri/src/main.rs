// VOS3 Enclave — Tauri 2.0 desktop application entry point.
//
// Wraps the existing Next.js frontend in a native window and provides
// Rust-powered IPC commands for QEMU lifecycle, VBus communication,
// and Warp Drive shared memory.

#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

mod commands;
mod qemu;
mod sidecar;
mod state;
mod vbus;
mod warp;

use sidecar::BackendSidecar;
use state::AppState;
use tauri::Manager;

/// W6.4 — Tauri command that returns the sidecar's W3.3 handshake
/// secret. The frontend api-client calls `invoke('get_handshake')`
/// on cold mount to obtain the secret it needs to perform the
/// X-Tauri-Handshake exchange. The secret is per-process and
/// generated cryptographically by the sidecar manager.
#[tauri::command]
fn get_handshake(state: tauri::State<AppState>) -> Result<String, String> {
    let lock = state.backend.lock().map_err(|p| p.to_string())?;
    match lock.as_ref() {
        Some(b) => Ok(b.handshake_secret().to_string()),
        None => Err("backend sidecar not yet initialized".to_string()),
    }
}

fn main() {
    tracing_subscriber::fmt::init();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .manage(AppState::new())
        // W6.4 — spawn the FastAPI backend sidecar at app start. Lives
        // for the lifetime of the Tauri process; shut down in the
        // on_window_event close handler below.
        .setup(|app| {
            let state = app.state::<AppState>();
            let app_data_dir = app
                .path()
                .app_data_dir()
                .map_err(|e| format!("could not resolve app_data_dir: {}", e))?;
            let mut backend = BackendSidecar::new(app_data_dir);
            match backend.spawn(None) {
                Ok(_) => {
                    tracing::info!("[W6.4] backend sidecar spawned");
                }
                Err(e) => {
                    // Don't crash the shell — log and continue so the
                    // user at least sees an error UI rather than a
                    // window that never opens.
                    tracing::warn!(
                        "[W6.4] backend sidecar spawn failed: {} \
                         — falling back to no embedded backend",
                        e
                    );
                }
            }
            let mut slot = state.backend.lock().unwrap_or_else(|p| p.into_inner());
            *slot = Some(backend);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::start_kernel,
            commands::stop_kernel,
            commands::kernel_status,
            commands::vbus_ping,
            commands::system_info,
            commands::list_slots,
            commands::load_model,
            commands::send_chat,
            commands::send_chat_stream,
            commands::read_inference_output,
            commands::warp_read_zone,
            // Stage 10.3 — Sovereign Control Panel
            commands::policy_status,
            commands::policy_set_force_permit,
            commands::policy_override_slot,
            commands::policy_drain_audit,
            commands::policy_check_confidence,
            // W6.4 — sidecar handshake bridge
            get_handshake,
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let state = window.state::<AppState>();
                // QEMU shutdown first (existing behaviour).
                let mut qemu_lock = state.qemu.lock().unwrap_or_else(|p| p.into_inner());
                if let Some(ref mut manager) = *qemu_lock {
                    let _: Result<(), qemu::QemuError> = manager.shutdown();
                }
                // W6.4 — sidecar shutdown. Best-effort; the Drop impl
                // on BackendSidecar provides a backstop if this path
                // doesn't run (panic, abnormal exit).
                let mut backend_lock = state
                    .backend
                    .lock()
                    .unwrap_or_else(|p| p.into_inner());
                if let Some(ref mut sidecar) = *backend_lock {
                    let _: Result<(), sidecar::SidecarError> = sidecar.shutdown();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error running VOS3 Enclave");
}
