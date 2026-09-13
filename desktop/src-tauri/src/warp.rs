//! Warp Drive -- mmap-backed ivshmem shared-memory transport.
//!
//! Provides a safe Rust interface to a memory-mapped file partitioned into
//! fixed-size zones. Each zone corresponds to an ivshmem slot that the VOS3
//! kernel and userspace agents use for zero-copy data exchange.

use std::fmt;
use std::fs::OpenOptions;
use std::path::PathBuf;

/// Number of independent shared-memory zones.
pub const ZONE_COUNT: usize = 4;

/// Size of each zone in bytes (16 MiB).
pub const ZONE_SIZE: usize = 16 * 1024 * 1024;

/// Total backing-file size (64 MiB).
pub const TOTAL_SIZE: usize = ZONE_COUNT * ZONE_SIZE;

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/// Errors that can occur during Warp Drive operations.
#[derive(Debug, thiserror::Error)]
pub enum WarpError {
    /// An underlying I/O error from the OS or filesystem.
    #[error("I/O error: {0}")]
    IoError(#[from] std::io::Error),

    /// The requested slot index is outside `0..ZONE_COUNT`.
    #[error("slot {0} out of range (max {ZONE_COUNT})")]
    SlotOutOfRange(usize),

    /// The requested byte range exceeds the zone boundary.
    #[error("offset {offset} + len {len} exceeds zone size {zone_size}")]
    OffsetOutOfRange {
        /// Starting offset within the zone.
        offset: usize,
        /// Number of bytes requested.
        len: usize,
        /// Maximum zone size.
        zone_size: usize,
    },

    /// The Warp Drive has not been opened yet.
    #[error("warp drive is not open")]
    NotOpen,
}

// ---------------------------------------------------------------------------
// WarpDrive
// ---------------------------------------------------------------------------

/// Memory-mapped shared-memory transport partitioned into [`ZONE_COUNT`] zones
/// of [`ZONE_SIZE`] bytes each.
///
/// # Usage
///
/// ```ignore
/// let mut warp = WarpDrive::new("/dev/shm/vos3_ivshmem".into());
/// warp.open()?;
/// warp.write_chunk(0, 0, b"hello")?;
/// let data = warp.read_chunk(0, 0, 5)?;
/// warp.flush()?;
/// warp.close();
/// ```
pub struct WarpDrive {
    mmap: Option<memmap2::MmapMut>,
    path: PathBuf,
}

impl WarpDrive {
    /// Create a new `WarpDrive` bound to `path`.
    ///
    /// The backing file is **not** opened until [`open`](Self::open) is called.
    pub fn new(path: PathBuf) -> Self {
        Self { mmap: None, path }
    }

    /// Open (or create) the backing file and establish the memory mapping.
    ///
    /// If the file is smaller than [`TOTAL_SIZE`] it will be extended.
    pub fn open(&mut self) -> Result<(), WarpError> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&self.path)?;

        let meta = file.metadata()?;
        if meta.len() < TOTAL_SIZE as u64 {
            file.set_len(TOTAL_SIZE as u64)?;
        }

        // SAFETY: file is exclusively opened and sized to TOTAL_SIZE
        let mapping = unsafe { memmap2::MmapMut::map_mut(&file)? };

        self.mmap = Some(mapping);
        Ok(())
    }

    /// Drop the memory mapping, releasing the mapped region.
    pub fn close(&mut self) {
        self.mmap = None;
    }

    /// Returns `true` if the backing file is currently memory-mapped.
    pub fn is_open(&self) -> bool {
        self.mmap.is_some()
    }

    /// Write `data` into `slot` starting at byte `offset` within the zone.
    ///
    /// # Errors
    ///
    /// - [`WarpError::NotOpen`] if the drive has not been opened.
    /// - [`WarpError::SlotOutOfRange`] if `slot >= ZONE_COUNT`.
    /// - [`WarpError::OffsetOutOfRange`] if the write would exceed the zone.
    pub fn write_chunk(
        &mut self,
        slot: usize,
        offset: usize,
        data: &[u8],
    ) -> Result<(), WarpError> {
        let mmap = self.mmap.as_mut().ok_or(WarpError::NotOpen)?;

        if slot >= ZONE_COUNT {
            return Err(WarpError::SlotOutOfRange(slot));
        }
        if offset.checked_add(data.len()).map_or(true, |end| end > ZONE_SIZE) {
            return Err(WarpError::OffsetOutOfRange {
                offset,
                len: data.len(),
                zone_size: ZONE_SIZE,
            });
        }

        let abs = slot * ZONE_SIZE + offset;
        mmap[abs..abs + data.len()].copy_from_slice(data);
        Ok(())
    }

    /// Read `len` bytes from `slot` starting at byte `offset` within the zone.
    ///
    /// # Errors
    ///
    /// - [`WarpError::NotOpen`] if the drive has not been opened.
    /// - [`WarpError::SlotOutOfRange`] if `slot >= ZONE_COUNT`.
    /// - [`WarpError::OffsetOutOfRange`] if the read would exceed the zone.
    pub fn read_chunk(
        &self,
        slot: usize,
        offset: usize,
        len: usize,
    ) -> Result<Vec<u8>, WarpError> {
        let mmap = self.mmap.as_ref().ok_or(WarpError::NotOpen)?;

        if slot >= ZONE_COUNT {
            return Err(WarpError::SlotOutOfRange(slot));
        }
        if offset.checked_add(len).map_or(true, |end| end > ZONE_SIZE) {
            return Err(WarpError::OffsetOutOfRange {
                offset,
                len,
                zone_size: ZONE_SIZE,
            });
        }

        let abs = slot * ZONE_SIZE + offset;
        Ok(mmap[abs..abs + len].to_vec())
    }

    /// Flush all pending writes to the underlying file (synchronous).
    ///
    /// # Errors
    ///
    /// - [`WarpError::NotOpen`] if the drive has not been opened.
    pub fn flush(&self) -> Result<(), WarpError> {
        let mmap = self.mmap.as_ref().ok_or(WarpError::NotOpen)?;
        mmap.flush()?;
        Ok(())
    }

    /// Non-blocking flush via `memmap2::MmapMut::flush_async()` (msync MS_ASYNC).
    ///
    /// Schedules dirty pages for writeback without waiting for I/O completion,
    /// preventing UI thread stalls during large model loads.
    ///
    /// # Errors
    ///
    /// - [`WarpError::NotOpen`] if the drive has not been opened.
    pub fn flush_nonblocking(&self) -> Result<(), WarpError> {
        let mmap = self.mmap.as_ref().ok_or(WarpError::NotOpen)?;
        mmap.flush_async()?;
        Ok(())
    }

    /// Read raw bytes from a zone without allocation overhead.
    /// Returns a copy of the bytes for zero-copy IPC.
    pub fn read_zone_raw(
        &self,
        slot: usize,
        offset: usize,
        len: usize,
    ) -> Result<Vec<u8>, WarpError> {
        self.read_chunk(slot, offset, len)
    }

    /// Return the absolute byte offset where `slot` begins within the mapping.
    pub fn zone_offset(slot: usize) -> usize {
        slot * ZONE_SIZE
    }
}

impl fmt::Debug for WarpDrive {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WarpDrive")
            .field("path", &self.path)
            .field("is_open", &self.is_open())
            .finish()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slot_out_of_range() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("warp_test_slot");
        let mut warp = WarpDrive::new(path);
        warp.open().unwrap();

        let result = warp.write_chunk(ZONE_COUNT, 0, b"x");
        assert!(
            matches!(result, Err(WarpError::SlotOutOfRange(slot)) if slot == ZONE_COUNT),
            "expected SlotOutOfRange, got {result:?}"
        );

        let result = warp.read_chunk(ZONE_COUNT + 1, 0, 1);
        assert!(
            matches!(result, Err(WarpError::SlotOutOfRange(slot)) if slot == ZONE_COUNT + 1),
            "expected SlotOutOfRange, got {result:?}"
        );
    }

    #[test]
    fn offset_out_of_range() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("warp_test_offset");
        let mut warp = WarpDrive::new(path);
        warp.open().unwrap();

        // Write that would exceed zone boundary.
        let result = warp.write_chunk(0, ZONE_SIZE - 2, &[0u8; 4]);
        assert!(
            matches!(result, Err(WarpError::OffsetOutOfRange { .. })),
            "expected OffsetOutOfRange, got {result:?}"
        );

        // Read that would exceed zone boundary.
        let result = warp.read_chunk(0, ZONE_SIZE - 1, 2);
        assert!(
            matches!(result, Err(WarpError::OffsetOutOfRange { .. })),
            "expected OffsetOutOfRange, got {result:?}"
        );
    }

    #[test]
    fn write_then_read_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("warp_test_rt");
        let mut warp = WarpDrive::new(path);
        warp.open().unwrap();

        let payload = b"VOS3 Warp Drive round-trip";

        // Write into slot 2, offset 128.
        warp.write_chunk(2, 128, payload).unwrap();

        // Read it back.
        let got = warp.read_chunk(2, 128, payload.len()).unwrap();
        assert_eq!(&got, payload);

        // Verify absolute offset helper.
        assert_eq!(WarpDrive::zone_offset(2), 2 * ZONE_SIZE);

        warp.close();
        assert!(!warp.is_open());
    }

    #[test]
    fn flush_unopened_returns_not_open() {
        let warp = WarpDrive::new(PathBuf::from("/nonexistent"));
        let result = warp.flush();
        assert!(
            matches!(result, Err(WarpError::NotOpen)),
            "expected NotOpen, got {result:?}"
        );
    }
}
