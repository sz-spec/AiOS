//! Supply Chain Integrity Tests — Phase 2.1 Extreme Sovereign Audit
//!
//! Verifies Cargo.lock integrity for security-critical crates:
//! - memmap2: memory-mapped I/O for Warp Drive shared memory
//! - subtle: constant-time comparison for HMAC-SHA256 VBus auth
//!
//! 4 tests: checksum verification, typosquat scanning, duplicate detection

use std::fs;
use std::path::Path;

/// Expected checksums from known-good Cargo.lock (pinned supply chain)
const MEMMAP2_VERSION: &str = "0.9.10";
const MEMMAP2_CHECKSUM: &str =
    "714098028fe011992e1c3962653c96b2d578c4b4bce9036e15ff220319b1e0e3";

const SUBTLE_VERSION: &str = "2.6.1";
const SUBTLE_CHECKSUM: &str =
    "13c2bddecc57b384dee18652358fb23172facb8a2c51ccc10d74c157bdea3292";

/// Known typosquat patterns for security-critical crates
const TYPOSQUAT_PATTERNS: &[&str] = &[
    "memmap_2",
    "memmap-2",
    "subtl",
    "subtel",
    "memmap22",
];

/// Locate the Cargo.lock file relative to the test binary
fn cargo_lock_contents() -> String {
    // Try multiple paths to find Cargo.lock
    let candidates = [
        Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.lock"),
        Path::new("Cargo.lock").to_path_buf(),
    ];

    for path in &candidates {
        if path.exists() {
            return fs::read_to_string(path)
                .unwrap_or_else(|e| panic!("Failed to read {}: {}", path.display(), e));
        }
    }

    panic!(
        "Cargo.lock not found. Searched: {:?}",
        candidates.iter().map(|p| p.display().to_string()).collect::<Vec<_>>()
    );
}

/// Parse a Cargo.lock file and find a package entry by name and version.
/// Returns the checksum if found.
fn find_package_checksum(contents: &str, name: &str, version: &str) -> Option<String> {
    let mut in_target_package = false;

    for line in contents.lines() {
        let trimmed = line.trim();

        if trimmed == "[[package]]" {
            in_target_package = false;
            continue;
        }

        if in_target_package {
            if let Some(checksum) = trimmed.strip_prefix("checksum = \"") {
                if let Some(checksum) = checksum.strip_suffix('"') {
                    return Some(checksum.to_string());
                }
            }
        }

        // Check if this is the start of our target package
        if trimmed == format!("name = \"{}\"", name) {
            in_target_package = false; // Reset, need version match too
            // Peek ahead: version should be next line
        }

        // Match name+version pattern in Cargo.lock
        if trimmed == format!("name = \"{}\"", name) {
            // Mark we found the name, version check comes next
            in_target_package = true;
        } else if in_target_package && trimmed == format!("version = \"{}\"", version) {
            // Name matched, version matches — stay in target
        } else if in_target_package
            && trimmed.starts_with("version = \"")
            && trimmed != format!("version = \"{}\"", version)
        {
            // Wrong version — reset
            in_target_package = false;
        }
    }

    None
}

/// Count how many times a package name appears in Cargo.lock
fn count_package_entries(contents: &str, name: &str) -> usize {
    let target = format!("name = \"{}\"", name);
    contents.lines().filter(|line| line.trim() == target).count()
}

#[test]
fn test_memmap2_checksum() {
    let contents = cargo_lock_contents();
    let checksum = find_package_checksum(&contents, "memmap2", MEMMAP2_VERSION)
        .expect("memmap2 not found in Cargo.lock — supply chain broken");

    assert_eq!(
        checksum, MEMMAP2_CHECKSUM,
        "memmap2 v{} checksum mismatch!\n  expected: {}\n  found:    {}\n  \
         This indicates a supply chain substitution attack.",
        MEMMAP2_VERSION, MEMMAP2_CHECKSUM, checksum
    );
}

#[test]
fn test_subtle_checksum() {
    let contents = cargo_lock_contents();
    let checksum = find_package_checksum(&contents, "subtle", SUBTLE_VERSION)
        .expect("subtle not found in Cargo.lock — supply chain broken");

    assert_eq!(
        checksum, SUBTLE_CHECKSUM,
        "subtle v{} checksum mismatch!\n  expected: {}\n  found:    {}\n  \
         This indicates a supply chain substitution attack.",
        SUBTLE_VERSION, SUBTLE_CHECKSUM, checksum
    );
}

#[test]
fn test_typosquat_scan() {
    let contents = cargo_lock_contents();
    let mut found_typosquats = Vec::new();

    for pattern in TYPOSQUAT_PATTERNS {
        let search = format!("name = \"{}\"", pattern);
        if contents.contains(&search) {
            found_typosquats.push(*pattern);
        }
    }

    assert!(
        found_typosquats.is_empty(),
        "Typosquat packages detected in Cargo.lock: {:?}\n  \
         These are known malicious package name variants.\n  \
         Remove them immediately and audit your dependency tree.",
        found_typosquats
    );
}

#[test]
fn test_no_duplicate_crate_versions() {
    let contents = cargo_lock_contents();

    let memmap2_count = count_package_entries(&contents, "memmap2");
    let subtle_count = count_package_entries(&contents, "subtle");

    assert_eq!(
        memmap2_count, 1,
        "memmap2 appears {} times in Cargo.lock (expected 1). \
         Multiple versions indicate a version-splitting attack.",
        memmap2_count
    );

    assert_eq!(
        subtle_count, 1,
        "subtle appears {} times in Cargo.lock (expected 1). \
         Multiple versions indicate a version-splitting attack.",
        subtle_count
    );
}
