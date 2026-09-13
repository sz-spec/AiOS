// SPDX-License-Identifier: MIT
//
// qemu.rs -- QEMU child-process lifecycle manager for VOS3 Enclave.
//
// Spawns, monitors, and tears down a QEMU instance that boots the VOS3
// kernel image.  Every session gets its own temp directory so multiple
// enclaves can coexist without path collisions.

use std::path::PathBuf;
use std::process::{Child, Command};
use std::thread;
use std::time::{Duration, Instant};

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/// Errors produced by [`QemuManager`] operations.
#[derive(Debug, thiserror::Error)]
pub enum QemuError {
    /// A required file or directory was not found at the given path.
    #[error("not found: {0}")]
    NotFound(String),

    /// A QEMU instance is already running in this manager.
    #[error("QEMU is already running")]
    AlreadyRunning,

    /// No QEMU instance is currently running.
    #[error("QEMU is not running")]
    NotRunning,

    /// The QEMU process could not be spawned.
    #[error("spawn failed: {0}")]
    SpawnFailed(String),

    /// Graceful shutdown timed out; the process was force-killed.
    #[error("shutdown timed out after 3 seconds")]
    ShutdownTimeout,

    /// An underlying I/O error.
    #[error(transparent)]
    IoError(#[from] std::io::Error),
}

// ---------------------------------------------------------------------------
// QemuManager
// ---------------------------------------------------------------------------

/// Manages the full lifecycle of a single QEMU child process that boots
/// the VOS3 kernel.
///
/// # Temp directory layout
///
/// ```text
/// {temp_dir}/
///   vbus.sock      -- VBus virtio-console UNIX socket
///   warp.raw       -- ivshmem shared-memory backing file
///   console.log    -- serial console log
///   disk.img       -- 64 MiB raw disk image
///   qemu.pid       -- QEMU PID file
/// ```
pub struct QemuManager {
    child: Option<Child>,
    kernel_path: PathBuf,
    qemu_path: PathBuf,
    temp_dir: PathBuf,
}

impl std::fmt::Debug for QemuManager {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("QemuManager")
            .field("kernel_path", &self.kernel_path)
            .field("qemu_path", &self.qemu_path)
            .field("temp_dir", &self.temp_dir)
            .field("has_child", &self.child.is_some())
            .finish()
    }
}

impl QemuManager {
    /// Create a new manager, validating that both `kernel_path` and
    /// `qemu_path` exist on disk.
    ///
    /// A fresh temporary directory is created under the system temp root
    /// (`$TMPDIR` / `/tmp`) with a UUID suffix to avoid collisions.
    ///
    /// # Errors
    ///
    /// - [`QemuError::NotFound`] if either path does not exist.
    /// - [`QemuError::IoError`] if the temp directory cannot be created.
    pub fn new(kernel_path: PathBuf, qemu_path: PathBuf) -> Result<Self, QemuError> {
        if !kernel_path.exists() {
            return Err(QemuError::NotFound(format!(
                "kernel not found: {}",
                kernel_path.display()
            )));
        }
        if !qemu_path.exists() {
            return Err(QemuError::NotFound(format!(
                "qemu binary not found: {}",
                qemu_path.display()
            )));
        }

        let temp_dir = std::env::temp_dir().join(format!("vos3_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&temp_dir)?;

        Ok(Self {
            child: None,
            kernel_path,
            qemu_path,
            temp_dir,
        })
    }

    /// Start QEMU with the certified argument set.
    ///
    /// A 64 MiB raw disk image is created on first launch if it does not
    /// already exist.  The process is spawned directly (no shell) to avoid
    /// metacharacter injection.
    ///
    /// # Errors
    ///
    /// - [`QemuError::AlreadyRunning`] if a live child process exists.
    /// - [`QemuError::SpawnFailed`] if `Command::spawn` fails.
    /// - [`QemuError::IoError`] if the disk image cannot be created.
    pub fn start(&mut self) -> Result<(), QemuError> {
        if self.is_alive() {
            return Err(QemuError::AlreadyRunning);
        }

        // Create empty 64 MiB disk image if absent.
        let disk = self.disk_path();
        if !disk.exists() {
            let file = std::fs::File::create(&disk)?;
            file.set_len(64 * 1024 * 1024)?;
        }

        let console_log = self.console_log_path();
        let socket = self.socket_path();
        let warp = self.warp_path();
        let pid = self.pid_path();

        let args: Vec<String> = vec![
            "-kernel".into(),
            self.kernel_path.display().to_string(),
            "-m".into(),
            "4096M".into(),
            "-smp".into(),
            "2".into(),
            "-cpu".into(),
            "max".into(),
            // Serial console redirected to file
            "-chardev".into(),
            format!("file,id=con,path={}", console_log.display()),
            "-serial".into(),
            "chardev:con".into(),
            // VBus UNIX socket
            "-chardev".into(),
            format!(
                "socket,id=vbus,path={},server=on,wait=off",
                socket.display()
            ),
            "-device".into(),
            "virtio-serial-pci".into(),
            "-device".into(),
            "virtconsole,chardev=vbus".into(),
            // Raw disk via virtio-blk
            "-drive".into(),
            format!(
                "file={},format=raw,if=none,id=disk0,cache=directsync",
                disk.display()
            ),
            "-device".into(),
            "virtio-blk-pci,drive=disk0".into(),
            // ivshmem shared-memory warp region
            "-object".into(),
            format!(
                "memory-backend-file,id=warp,size=64M,mem-path={},share=on",
                warp.display()
            ),
            "-device".into(),
            "ivshmem-plain,memdev=warp".into(),
            // Headless
            "-display".into(),
            "none".into(),
            // PID file
            "-pidfile".into(),
            pid.display().to_string(),
        ];

        let child = Command::new(&self.qemu_path)
            .args(&args)
            .spawn()
            .map_err(|e| QemuError::SpawnFailed(e.to_string()))?;

        self.child = Some(child);
        Ok(())
    }

    /// Returns `true` if the QEMU child process is still running.
    pub fn is_alive(&mut self) -> bool {
        match self.child.as_mut() {
            Some(child) => match child.try_wait() {
                Ok(Some(_)) => false, // exited
                Ok(None) => true,     // still running
                Err(_) => false,      // cannot query -- treat as dead
            },
            None => false,
        }
    }

    /// Path to the VBus UNIX socket inside the temp directory.
    pub fn socket_path(&self) -> PathBuf {
        self.temp_dir.join("vbus.sock")
    }

    /// Path to the ivshmem warp shared-memory backing file.
    pub fn warp_path(&self) -> PathBuf {
        self.temp_dir.join("warp.raw")
    }

    /// Path to the serial console log file.
    pub fn console_log_path(&self) -> PathBuf {
        self.temp_dir.join("console.log")
    }

    /// Path to the raw disk image.
    pub fn disk_path(&self) -> PathBuf {
        self.temp_dir.join("disk.img")
    }

    /// Path to the QEMU PID file.
    pub fn pid_path(&self) -> PathBuf {
        self.temp_dir.join("qemu.pid")
    }

    /// Gracefully shut down the QEMU process.
    ///
    /// Sends a kill signal and then polls for up to 3 seconds (in 100 ms
    /// intervals).  If the process has not exited by then it is
    /// force-killed and [`QemuError::ShutdownTimeout`] is returned.
    ///
    /// # Errors
    ///
    /// - [`QemuError::NotRunning`] if there is no child process.
    /// - [`QemuError::ShutdownTimeout`] if the process did not exit
    ///   within the 3-second window.
    /// - [`QemuError::IoError`] on underlying I/O failures.
    pub fn shutdown(&mut self) -> Result<(), QemuError> {
        let mut child = match self.child.take() {
            Some(c) => c,
            None => return Err(QemuError::NotRunning),
        };

        // Request termination.
        child.kill()?;

        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            match child.try_wait()? {
                Some(_) => return Ok(()),
                None => {
                    if Instant::now() >= deadline {
                        // Force-kill as a last resort (already killed above,
                        // but ensure the OS reaps).
                        let _ = child.kill();
                        let _ = child.wait();
                        return Err(QemuError::ShutdownTimeout);
                    }
                    thread::sleep(Duration::from_millis(100));
                }
            }
        }
    }
}

impl Drop for QemuManager {
    /// Best-effort cleanup: shut down any running QEMU child and remove temp dir.
    fn drop(&mut self) {
        if self.child.is_some() {
            let _ = self.shutdown();
        }
        // Clean up the session temp directory (socket, warp file, disk, logs).
        let _ = std::fs::remove_dir_all(&self.temp_dir);
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::Path;

    /// Helper: create a real file at the given path so `exists()` passes.
    fn touch(path: &Path) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).expect("test setup: create parent dir");
        }
        fs::File::create(path).expect("test setup: create file");
    }

    /// Helper: return a path guaranteed not to exist.
    fn nonexistent_path() -> PathBuf {
        std::env::temp_dir().join(format!("vos3_test_nonexistent_{}", uuid::Uuid::new_v4()))
    }

    /// Helper: create a disposable temp dir with dummy kernel and qemu files.
    fn make_fixtures() -> (PathBuf, PathBuf, PathBuf) {
        let base = std::env::temp_dir().join(format!("vos3_test_fixtures_{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&base).expect("test setup: create fixtures dir");

        let kernel = base.join("vos3.elf");
        let qemu = base.join("qemu-system-x86_64");
        touch(&kernel);
        touch(&qemu);

        (base, kernel, qemu)
    }

    #[test]
    fn new_with_nonexistent_kernel_returns_not_found() {
        let bogus_kernel = nonexistent_path();
        let bogus_qemu = nonexistent_path();

        let result = QemuManager::new(bogus_kernel, bogus_qemu);
        assert!(result.is_err());
        match result {
            Err(QemuError::NotFound(msg)) => {
                assert!(msg.contains("kernel"), "message should mention kernel: {msg}");
            }
            other => panic!("expected NotFound, got: {other:?}"),
        }
    }

    #[test]
    fn new_with_nonexistent_qemu_returns_not_found() {
        let (_base, kernel, _qemu) = make_fixtures();
        let bogus_qemu = nonexistent_path();

        let result = QemuManager::new(kernel, bogus_qemu);
        assert!(result.is_err());
        match result {
            Err(QemuError::NotFound(msg)) => {
                assert!(msg.contains("qemu"), "message should mention qemu: {msg}");
            }
            other => panic!("expected NotFound, got: {other:?}"),
        }
    }

    #[test]
    fn new_creates_temp_dir() {
        let (_base, kernel, qemu) = make_fixtures();

        let mgr = QemuManager::new(kernel, qemu).expect("new should succeed");
        assert!(mgr.temp_dir.exists(), "temp dir should be created on disk");

        // Cleanup
        let _ = fs::remove_dir_all(&mgr.temp_dir);
    }

    #[test]
    fn path_helpers_return_expected_filenames() {
        let (_base, kernel, qemu) = make_fixtures();

        let mgr = QemuManager::new(kernel, qemu).expect("new should succeed");

        assert_eq!(mgr.socket_path(), mgr.temp_dir.join("vbus.sock"));
        assert_eq!(mgr.warp_path(), mgr.temp_dir.join("warp.raw"));
        assert_eq!(mgr.console_log_path(), mgr.temp_dir.join("console.log"));
        assert_eq!(mgr.disk_path(), mgr.temp_dir.join("disk.img"));
        assert_eq!(mgr.pid_path(), mgr.temp_dir.join("qemu.pid"));

        // Cleanup
        let _ = fs::remove_dir_all(&mgr.temp_dir);
    }

    #[test]
    fn start_with_nonexistent_qemu_binary_fails() {
        // We create fixtures so `new()` passes, then delete the qemu binary
        // before calling `start()`.  The binary exists at construction time
        // but is no longer executable at spawn time, triggering SpawnFailed.
        let (base, kernel, qemu) = make_fixtures();

        let mut mgr = QemuManager::new(kernel, qemu.clone()).expect("new should succeed");

        // Remove the qemu binary so spawn cannot execute it.
        fs::remove_file(&qemu).expect("test setup: remove qemu binary");

        let result = mgr.start();
        assert!(result.is_err(), "start should fail when qemu binary is gone");
        match result {
            Err(QemuError::SpawnFailed(_)) => { /* expected */ }
            other => panic!("expected SpawnFailed, got: {other:?}"),
        }

        // Cleanup
        let _ = fs::remove_dir_all(&mgr.temp_dir);
        let _ = fs::remove_dir_all(&base);
    }

    #[test]
    fn is_alive_returns_false_when_no_child() {
        let (_base, kernel, qemu) = make_fixtures();

        let mut mgr = QemuManager::new(kernel, qemu).expect("new should succeed");
        assert!(!mgr.is_alive(), "no child process should mean not alive");

        // Cleanup
        let _ = fs::remove_dir_all(&mgr.temp_dir);
    }

    #[test]
    fn shutdown_without_child_returns_not_running() {
        let (_base, kernel, qemu) = make_fixtures();

        let mut mgr = QemuManager::new(kernel, qemu).expect("new should succeed");

        let result = mgr.shutdown();
        assert!(result.is_err());
        match result {
            Err(QemuError::NotRunning) => { /* expected */ }
            other => panic!("expected NotRunning, got: {other:?}"),
        }

        // Cleanup
        let _ = fs::remove_dir_all(&mgr.temp_dir);
    }
}
