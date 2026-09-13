/// Shared application state for the VOS3 Enclave.
///
/// Holds the QEMU process manager, VBus client, and Warp Drive mmap,
/// each behind appropriate synchronisation primitives for concurrent
/// access from Tauri command handlers.

use std::sync::Mutex;

use crate::qemu::QemuManager;
use crate::sidecar::BackendSidecar;
use crate::vbus::VBusClient;
use crate::warp::WarpDrive;

/// Central state managed by the Tauri application.
///
/// Stored as Tauri managed state and accessed via `tauri::State<AppState>`
/// in `#[tauri::command]` handlers.
pub struct AppState {
    /// QEMU child-process manager (blocking operations, std Mutex).
    pub qemu: Mutex<Option<QemuManager>>,

    /// Async VBus client (async operations, tokio Mutex).
    pub vbus: tokio::sync::Mutex<Option<VBusClient>>,

    /// Warp Drive shared-memory interface (blocking mmap, std Mutex).
    pub warp: Mutex<Option<WarpDrive>>,

    /// W6.4 — embedded FastAPI backend sidecar. None until the Tauri
    /// `setup` hook spawns it at first window creation.
    pub backend: Mutex<Option<BackendSidecar>>,
}

impl AppState {
    /// Creates a new `AppState` with all components uninitialised.
    pub fn new() -> Self {
        Self {
            qemu: Mutex::new(None),
            vbus: tokio::sync::Mutex::new(None),
            warp: Mutex::new(None),
            backend: Mutex::new(None),
        }
    }
}

impl Default for AppState {
    fn default() -> Self {
        Self::new()
    }
}
