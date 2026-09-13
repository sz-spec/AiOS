/// CRC32C (Castagnoli) implementation using polynomial 0x82F63B78.
///
/// This module provides a pure-Rust, safe CRC32C calculator with a 256-entry
/// lookup table generated at compile time. No `unsafe` blocks are used.

/// The CRC32C (Castagnoli) polynomial in reflected form.
const POLYNOMIAL: u32 = 0x82F63B78;

/// Compile-time generated 256-entry lookup table for CRC32C.
const TABLE: [u32; 256] = generate_table();

/// Generates the 256-entry CRC32C lookup table at compile time.
///
/// Each entry is computed by iterating 8 bits of the byte index through
/// the reflected CRC32C polynomial.
const fn generate_table() -> [u32; 256] {
    let mut table = [0u32; 256];
    let mut i: usize = 0;
    while i < 256 {
        let mut crc = i as u32;
        let mut bit = 0;
        while bit < 8 {
            if crc & 1 != 0 {
                crc = (crc >> 1) ^ POLYNOMIAL;
            } else {
                crc >>= 1;
            }
            bit += 1;
        }
        table[i] = crc;
        i += 1;
    }
    table
}

/// Computes the CRC32C checksum of the given data.
///
/// Uses the standard initial seed of `0xFFFFFFFF` and applies a final XOR
/// with `0xFFFFFFFF` to produce the result.
///
/// # Examples
///
/// ```
/// use crate::vbus::crc32c::crc32c;
///
/// assert_eq!(crc32c(b"123456789"), 0xE3069283);
/// ```
pub fn crc32c(data: &[u8]) -> u32 {
    crc32c_with_seed(data, 0xFFFFFFFF)
}

/// Computes the CRC32C checksum of the given data using a caller-supplied seed.
///
/// The seed is used as the initial CRC register value. After processing all
/// bytes, the result is XORed with `0xFFFFFFFF`.
///
/// # Arguments
///
/// * `data` - The byte slice to compute the checksum over.
/// * `seed` - The initial value of the CRC register.
///
/// # Examples
///
/// ```
/// use crate::vbus::crc32c::crc32c_with_seed;
///
/// let checksum = crc32c_with_seed(b"hello", 0xFFFFFFFF);
/// ```
pub fn crc32c_with_seed(data: &[u8], seed: u32) -> u32 {
    let mut crc = seed;
    for &byte in data {
        let index = ((crc ^ byte as u32) & 0xFF) as usize;
        crc = (crc >> 8) ^ TABLE[index];
    }
    crc ^ 0xFFFFFFFF
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Validates the canonical CRC32C check value.
    /// The ASCII string "123456789" must produce 0xE3069283.
    #[test]
    fn test_crc32c_check_value() {
        assert_eq!(crc32c(b"123456789"), 0xE3069283);
    }

    /// CRC32C of empty data must equal 0x00000000.
    /// (0xFFFFFFFF XOR 0xFFFFFFFF with no byte processing.)
    #[test]
    fn test_crc32c_empty() {
        assert_eq!(crc32c(b""), 0x00000000);
    }

    /// CRC32C of a single zero byte.
    #[test]
    fn test_crc32c_single_byte() {
        let result = crc32c(&[0x00]);
        // Manually verify: table[0xFF & (0xFFFFFFFF ^ 0x00)] = table[0xFF]
        // Then (0xFFFFFFFF >> 8) ^ table[0xFF], XOR final 0xFFFFFFFF.
        // The expected value is deterministic from the polynomial.
        let expected = (0x00FFFFFFu32) ^ TABLE[0xFF] ^ 0xFFFFFFFF;
        assert_eq!(result, expected);
    }

    /// The seeded variant with seed 0xFFFFFFFF must match the standard function.
    #[test]
    fn test_crc32c_with_seed_default() {
        let data = b"The quick brown fox jumps over the lazy dog";
        assert_eq!(crc32c(data), crc32c_with_seed(data, 0xFFFFFFFF));
    }

    /// A non-default seed must produce a different result than the standard call.
    #[test]
    fn test_crc32c_with_seed_nondefault() {
        let data = b"123456789";
        let standard = crc32c(data);
        let seeded = crc32c_with_seed(data, 0x00000000);
        assert_ne!(standard, seeded);
    }

    /// The lookup table must have exactly 256 entries and index 0 must be 0.
    #[test]
    fn test_table_properties() {
        assert_eq!(TABLE.len(), 256);
        // Entry 0: CRC of 0x00 through 8 shifts is 0.
        assert_eq!(TABLE[0], 0x00000000);
    }
}
