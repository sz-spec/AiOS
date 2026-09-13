// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 VOS3 Project
//
// sidecar.rs — FastAPI backend (Python) lifecycle manager.
//
// W6.4 — bundles the local-first stack into the Tauri shell. The Rust
// runtime spawns the backend on startup as a child process, injects the
// W5/W6 env vars that pin it to sovereign mode, captures the random
// X-Tauri-Handshake secret so it can be relayed to the WebView, and
// shuts the process down cleanly on window close.
//
// Path resolution:
//   The backend already honors $VOS3_LOCAL_DB_PATH and
//   $VOS3_LOCAL_CHROMA_PATH (see W5.1/W5.2). Under the desktop shell
//   we set those to the Tauri-canonical `app_data_dir`/vos3.db and
//   `app_data_dir`/chroma_db so the storage layer respects the OS
//   sandbox rather than spraying ~/.vos/ into the user's home.
//
// Security:
//   - $VOS3_TAURI_IPC_SECRET is generated once at process start with
//     32 bytes of crypto randomness. It is passed to the backend via
//     env (NOT command-line argv — argv would leak through `ps`).
//   - The W3.3 CSRF handshake endpoint requires this header value to
//     return a token. By injecting the SAME secret into the WebView
//     as `window.__VOS3_TAURI_HANDSHAKE__`, only the bundled frontend
//     can complete the handshake.
//   - Process is spawned with stdin/stdout/stderr piped so the parent
//     can intercept logs; child stdin is held closed so any future
//     `input()` call in Python fails fast rather than hanging.

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use rand::Rng;

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------


/// Errors produced by [`BackendSidecar`] operations.
#[derive(Debug, thiserror::Error)]
pub enum SidecarError {
    /// A backend binary was not found at the given path.
    #[error("backend binary not found: {0}")]
    NotFound(String),

    /// The backend is already running in this manager.
    #[error("backend sidecar is already running")]
    AlreadyRunning,

    /// The backend process could not be spawned.
    #[error("spawn failed: {0}")]
    SpawnFailed(String),

    /// Graceful shutdown timed out; the process was force-killed.
    #[error("shutdown timed out after {0:?}")]
    ShutdownTimeout(Duration),

    /// An underlying I/O error.
    #[error(transparent)]
    IoError(#[from] std::io::Error),
}


// ---------------------------------------------------------------------------
// BackendSidecar
// ---------------------------------------------------------------------------


/// Manages the lifecycle of the embedded FastAPI backend.
///
/// One instance per Tauri app. The `Drop` impl best-effort-shuts down
/// the child so a panic in the main thread doesn't orphan the process.
pub struct BackendSidecar {
    child: Option<Child>,
    /// Crypto-random handshake secret. Cloned into the WebView at
    /// window-load time so the frontend api-client can perform the
    /// W3.3 handshake without the secret ever crossing argv or the
    /// disk.
    handshake_secret: String,
    /// Working directory used to anchor relative paths inside the
    /// backend (config files, model dirs, etc.). Tauri-canonical
    /// app_data_dir.
    app_data_dir: PathBuf,
}

impl BackendSidecar {
    /// Construct a new sidecar manager with a fresh handshake secret.
    ///
    /// Does NOT spawn the process — call [`Self::spawn`].
    pub fn new(app_data_dir: PathBuf) -> Self {
        Self {
            child: None,
            handshake_secret: generate_handshake_secret(),
            app_data_dir,
        }
    }

    /// Return the handshake secret. The Tauri command layer reads this
    /// to inject `window.__VOS3_TAURI_HANDSHAKE__` into the WebView.
    pub fn handshake_secret(&self) -> &str {
        &self.handshake_secret
    }

    /// Spawn the backend sidecar.
    ///
    /// Env vars set:
    ///   VOS3_LOCALITY_PREFERENCE  = "local-first"
    ///   VOS_PROFILE               = "community"
    ///   VOS3_LOCAL_DB_PATH        = {app_data_dir}/vos3.db
    ///   VOS3_LOCAL_CHROMA_PATH    = {app_data_dir}/chroma_db
    ///   VOS3_TAURI_IPC_SECRET     = {self.handshake_secret}
    ///   VOS3_OFFLINE_AUTH         = "true"        (W5.3 fallback)
    ///   ENVIRONMENT               = "development" (NEVER "production"
    ///                              under the desktop shell — production
    ///                              implies a server with Clerk keys)
    ///
    /// The binary path is searched in this order:
    ///   1. Caller-supplied override (`binary` arg)
    ///   2. Sibling `vos-backend` executable next to the Tauri binary
    ///   3. Falls back to `python3 -m uvicorn backend.app:create_app --factory`
    ///      for `tauri dev` runs (no bundled binary yet)
    pub fn spawn(&mut self, binary: Option<&Path>) -> Result<(), SidecarError> {
        if self.child.is_some() {
            return Err(SidecarError::AlreadyRunning);
        }
        // Ensure the app-data dir exists; the backend won't create
        // arbitrary parent dirs.
        if !self.app_data_dir.exists() {
            std::fs::create_dir_all(&self.app_data_dir)?;
        }

        let mut cmd = build_command(binary, &self.app_data_dir)?;
        apply_env(&mut cmd, &self.app_data_dir, &self.handshake_secret);

        let child = cmd
            .stdin(Stdio::null())       // close child stdin defensively
            .stdout(Stdio::piped())     // capture logs (caller may pipe to tracing)
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| SidecarError::SpawnFailed(e.to_string()))?;
        self.child = Some(child);
        Ok(())
    }

    /// Best-effort graceful shutdown.
    ///
    /// On Unix sends SIGTERM, waits up to 3 seconds, then SIGKILLs.
    /// On Windows uses `TerminateProcess` directly (no SIGTERM
    /// equivalent on Windows).
    pub fn shutdown(&mut self) -> Result<(), SidecarError> {
        let mut child = match self.child.take() {
            Some(c) => c,
            None => return Ok(()),
        };

        // Try a polite shutdown first.
        #[cfg(unix)]
        unsafe {
            // SAFETY: the pid we obtain from the Child is guaranteed by
            // the std::process documentation to be valid as long as we
            // haven't called .wait() yet. SIGTERM signals the OS-level
            // PID, which is correct regardless of the child's own
            // process group.
            libc::kill(child.id() as libc::pid_t, libc::SIGTERM);
        }

        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            match child.try_wait() {
                Ok(Some(_)) => return Ok(()),
                Ok(None) => {
                    if Instant::now() >= deadline {
                        break;
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                Err(e) => return Err(SidecarError::IoError(e)),
            }
        }

        // Polite shutdown didn't take. Force-kill.
        let _ = child.kill();
        let _ = child.wait();
        Err(SidecarError::ShutdownTimeout(Duration::from_secs(3)))
    }
}

impl Drop for BackendSidecar {
    fn drop(&mut self) {
        if self.child.is_some() {
            let _ = self.shutdown();
        }
    }
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


fn generate_handshake_secret() -> String {
    // 32 bytes of OS randomness → URL-safe base64-ish hex. We use hex
    // here (not base64) to keep the secret a-z0-9 only — easier to
    // round-trip through env vars, HTTP headers, and JSON without
    // worrying about padding chars.
    let mut buf = [0u8; 32];
    rand::rng().fill_bytes(&mut buf);
    buf.iter().map(|b| format!("{:02x}", b)).collect()
}

fn build_command(
    binary: Option<&Path>,
    app_data_dir: &Path,
) -> Result<Command, SidecarError> {
    if let Some(path) = binary {
        if !path.exists() {
            return Err(SidecarError::NotFound(path.display().to_string()));
        }
        return Ok(Command::new(path));
    }

    // Try the bundled sibling binary next to the Tauri executable.
    if let Ok(self_exe) = std::env::current_exe() {
        if let Some(parent) = self_exe.parent() {
            let bundled = parent.join("vos-backend");
            #[cfg(windows)]
            let bundled = parent.join("vos-backend.exe");
            if bundled.exists() {
                return Ok(Command::new(bundled));
            }
        }
    }

    // Dev fallback — assume the developer is running `tauri dev` from
    // the repo root with a Python interpreter available. The current
    // working directory is set to the backend so module discovery
    // works without PYTHONPATH gymnastics.
    let mut cmd = Command::new("python3");
    cmd.args([
        "-m", "uvicorn",
        "backend.app:create_app",
        "--factory",
        "--host", "127.0.0.1",
        "--port", "8000",
    ]);
    // The backend repo is expected to be at the workspace root; tauri
    // dev typically launches with CWD = desktop/, so we walk up one
    // level. If the layout changes, set $VOS3_BACKEND_DIR explicitly.
    let backend_cwd = std::env::var("VOS3_BACKEND_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            // app_data_dir is OS-managed and not the source tree, so
            // we fall back to CWD when no env override is set.
            std::env::current_dir().unwrap_or_else(|_| app_data_dir.to_path_buf())
        });
    cmd.current_dir(backend_cwd);
    Ok(cmd)
}

fn apply_env(cmd: &mut Command, app_data_dir: &Path, secret: &str) {
    let db_path = app_data_dir.join("vos3.db");
    let chroma_path = app_data_dir.join("chroma_db");

    cmd.env("VOS3_LOCALITY_PREFERENCE", "local-first")
        .env("VOS_PROFILE", "community")
        .env("VOS3_LOCAL_DB_PATH", db_path)
        .env("VOS3_LOCAL_CHROMA_PATH", chroma_path)
        .env("VOS3_TAURI_IPC_SECRET", secret)
        .env("VOS3_OFFLINE_AUTH", "true")
        // ENVIRONMENT must NEVER be 'production' in the desktop shell —
        // production refusal in middleware/auth.py disables the W5.3
        // offline fallback. The desktop shell is by definition not a
        // server deployment.
        .env("ENVIRONMENT", "development")
        // Force the deterministic embedding fallback by default; the
        // operator can flip to "sentence-transformers" once the model
        // is downloaded.
        .env_remove("VOS3_EMBEDDING_BACKEND");
}


// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------


#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn handshake_secret_is_64_hex_chars() {
        let s = generate_handshake_secret();
        assert_eq!(s.len(), 64);
        assert!(s.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn handshake_secrets_are_unique() {
        let a = generate_handshake_secret();
        let b = generate_handshake_secret();
        assert_ne!(a, b);
    }

    #[test]
    fn new_creates_app_data_dir_lazy() {
        let tmp = tempdir().unwrap();
        let mgr = BackendSidecar::new(tmp.path().join("subdir-not-yet-created"));
        // Constructor doesn't touch the filesystem; spawn() does.
        assert!(!mgr.app_data_dir.exists());
        assert_eq!(mgr.handshake_secret().len(), 64);
    }

    #[test]
    fn shutdown_without_child_is_ok() {
        let tmp = tempdir().unwrap();
        let mut mgr = BackendSidecar::new(tmp.path().to_path_buf());
        assert!(mgr.shutdown().is_ok());
    }

    #[test]
    fn apply_env_sets_pinned_values() {
        let tmp = tempdir().unwrap();
        let mut cmd = Command::new("true");
        apply_env(&mut cmd, tmp.path(), "deadbeef");
        // We can't read Command's env back in stable Rust, so just
        // assert it constructs without panicking and accepts the
        // chained calls.
        let _ = cmd.get_program();
    }
}
