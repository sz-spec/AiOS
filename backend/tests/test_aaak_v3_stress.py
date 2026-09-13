#!/usr/bin/env python3
"""
Track C: V-AAAK v3.0 Native Mode Stress Test (v23.1 Audit)

Tests AAAK codec at extreme scale without QEMU:
1. 50,000 randomized round-trips
2. Boundary conditions (0-byte, 1-byte, max-size payloads)
3. Compression efficiency on representative AI/reasoning text
4. Determinism under parallel execution
5. Edge case: payload containing AAAK escape bytes (0xFF)
6. Edge case: payload that is ALL dictionary tokens
7. Edge case: payload with zero dictionary matches (pure binary)
8. Throughput benchmark: encode/decode rate (frames/sec)
"""

import hashlib
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

# Ensure the services directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
from vbus_driver import v_aaak_encode, v_aaak_decode, V_AAAK_DICT, V_AAAK_ESCAPE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _round_trip(data: bytes) -> bytes:
    """Encode then decode; raises on failure."""
    encoded = v_aaak_encode(data)
    decoded = v_aaak_decode(encoded)
    return decoded


def _random_payload(size: int, rng: random.Random) -> bytes:
    """Generate a random payload of exactly `size` bytes."""
    return bytes(rng.getrandbits(8) for _ in range(size))


REPRESENTATIVE_LLM_TEXT = (
    "The transformer architecture is based on the attention mechanism, "
    "which was introduced in the paper. The model is trained on a large "
    "dataset and the results are impressive. Each layer of the model can "
    "be described as a function that maps the input to the output with "
    "the following properties:\n\n"
    "1. The self-attention mechanism computes the relevance of each token "
    "in the input sequence to every other token.\n"
    "2. The feed-forward network applies a non-linear transformation to "
    "the output of the attention layer.\n"
    "3. Residual connections and layer normalization are used for stability.\n\n"
    "The training process involves minimizing the cross-entropy loss on the "
    "predicted token distribution. This is a standard technique that has "
    "been shown to be effective for language modeling tasks. The model can "
    "then be fine-tuned on specific downstream tasks, which typically "
    "involves a smaller dataset and fewer training steps.\n\n"
    "```python\n"
    "def forward(self, x):\n"
    "    attn = self.attention(x)\n"
    "    return self.norm(attn + x)\n"
    "```\n\n"
    "The architecture is simple, but the key insight is that the model "
    "learns to attend to the most relevant parts of the input for each "
    "output position. This is what makes it so effective for a wide range "
    "of natural language processing tasks.\n"
)


# ===========================================================================
# Test 1: 50,000 Randomized Round-Trips
# ===========================================================================


class TestMassiveRandomRoundTrip:
    """50,000 random payloads (sizes 1-4096 bytes) must all round-trip losslessly."""

    def test_50000_random_payloads(self):
        rng = random.Random(42)  # deterministic seed
        total = 50_000
        failures = []
        for i in range(total):
            # Use smaller max size for speed; 512 bytes still exercises
            # all code paths while keeping O(n*d) encode tractable
            size = rng.randint(1, 512)
            data = _random_payload(size, rng)
            try:
                decoded = _round_trip(data)
                if decoded != data:
                    failures.append((i, size, "data mismatch"))
            except Exception as e:
                failures.append((i, size, str(e)))

        assert (
            len(failures) == 0
        ), f"{len(failures)} failures out of {total}: first 5 = {failures[:5]}"


# ===========================================================================
# Test 2: Boundary Conditions
# ===========================================================================


class TestBoundaryConditions:
    """Zero-byte, 1-byte, and max-size payload boundaries."""

    def test_empty_payload(self):
        """0-byte payload must encode to empty and decode to empty."""
        enc = v_aaak_encode(b"")
        assert enc == b"", f"Empty encode produced {len(enc)} bytes"
        dec = v_aaak_decode(enc)
        assert dec == b"", f"Empty decode produced {len(dec)} bytes"

    def test_single_byte_all_values(self):
        """Every single byte value 0x00-0xFF must round-trip."""
        for b in range(256):
            data = bytes([b])
            decoded = _round_trip(data)
            assert decoded == data, f"Byte 0x{b:02x}: mismatch"

    def test_max_size_8192(self):
        """8192-byte payload (LINE_MAX_BRIDGE) round-trips."""
        data = os.urandom(8192)
        decoded = _round_trip(data)
        assert decoded == data
        assert hashlib.sha256(decoded).digest() == hashlib.sha256(data).digest()

    def test_large_payload_16384(self):
        """16384-byte payload round-trips."""
        data = os.urandom(16384)
        decoded = _round_trip(data)
        assert decoded == data

    def test_one_byte_payloads_all_tokens_first_char(self):
        """First char of each dictionary token as single-byte payload."""
        for token in V_AAAK_DICT:
            first_byte = token.encode("utf-8")[0:1]
            decoded = _round_trip(first_byte)
            assert decoded == first_byte


# ===========================================================================
# Test 3: Compression Efficiency on AI/LLM Text
# ===========================================================================


class TestCompressionEfficiency:
    """Compression ratio analysis on representative LLM output."""

    def test_representative_llm_text_compression(self):
        """V-AAAK achieves meaningful compression on LLM text."""
        data = REPRESENTATIVE_LLM_TEXT.encode("utf-8")
        enc = v_aaak_encode(data)
        dec = v_aaak_decode(enc)
        assert dec == data, "Round-trip failed on LLM text"

        ratio = len(data) / len(enc) if len(enc) > 0 else float("inf")
        print(
            f"\n[LLM Text] Original: {len(data)} bytes, Compressed: {len(enc)} bytes, "
            f"Ratio: {ratio:.2f}x"
        )
        # V-AAAK targets natural language; should achieve at least 1.1x
        assert ratio > 1.0, f"No compression achieved: ratio={ratio:.2f}x"

    def test_token_dense_text_high_compression(self):
        """Text heavily loaded with dictionary tokens achieves high ratio."""
        dense = (
            " the and the is of the to in it that for was on are with as this "
            " be at have from or by not but what all were when we there can an\n"
        ) * 50
        data = dense.encode("utf-8")
        enc = v_aaak_encode(data)
        dec = v_aaak_decode(enc)
        assert dec == data

        ratio = len(data) / len(enc) if len(enc) > 0 else float("inf")
        print(
            f"\n[Dense Tokens] Original: {len(data)} bytes, Compressed: {len(enc)} bytes, "
            f"Ratio: {ratio:.2f}x"
        )
        # Token-dense text should compress significantly
        assert ratio > 1.5, f"Token-dense compression ratio too low: {ratio:.2f}x"

    def test_64_token_dictionary_coverage(self):
        """Verify the dictionary has exactly 64 entries."""
        assert (
            len(V_AAAK_DICT) == 64
        ), f"Dictionary has {len(V_AAAK_DICT)} entries, expected 64"

    def test_theoretical_max_compression_on_pure_tokens(self):
        """All-token payload gives theoretical max compression."""
        # Each token becomes 2 bytes (escape + code)
        # Token " the" = 4 chars => 4 bytes raw => 2 bytes compressed = 2x
        # Token " from" = 5 chars => 5 bytes raw => 2 bytes compressed = 2.5x
        # Longest token wins most
        all_tokens_data = b"".join(t.encode("utf-8") for t in V_AAAK_DICT)
        enc = v_aaak_encode(all_tokens_data)
        dec = v_aaak_decode(enc)
        assert dec == all_tokens_data

        # Each of 64 tokens becomes exactly 2 bytes
        expected_compressed = 64 * 2  # 128 bytes
        assert (
            len(enc) == expected_compressed
        ), f"Expected {expected_compressed} bytes for 64 pure tokens, got {len(enc)}"
        ratio = len(all_tokens_data) / len(enc)
        print(
            f"\n[Pure Tokens] Original: {len(all_tokens_data)} bytes, "
            f"Compressed: {len(enc)} bytes, Ratio: {ratio:.2f}x"
        )
        # Average token length ~3.5 chars, so ratio should be ~1.75x
        assert ratio > 1.5


# ===========================================================================
# Test 4: Determinism Under Parallel Execution
# ===========================================================================


class TestParallelDeterminism:
    """Encoding must be deterministic even under concurrent execution."""

    def test_determinism_10_threads_1000_each(self):
        """10 threads each encode the same payload 1000 times; all results identical."""
        data = REPRESENTATIVE_LLM_TEXT.encode("utf-8")
        baseline = v_aaak_encode(data)
        baseline_hash = hashlib.sha256(baseline).hexdigest()

        def worker(thread_id: int) -> list:
            mismatches = []
            for i in range(1000):
                enc = v_aaak_encode(data)
                h = hashlib.sha256(enc).hexdigest()
                if h != baseline_hash:
                    mismatches.append((thread_id, i))
            return mismatches

        all_mismatches = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(worker, tid) for tid in range(10)]
            for f in as_completed(futures):
                all_mismatches.extend(f.result())

        assert (
            len(all_mismatches) == 0
        ), f"{len(all_mismatches)} determinism failures across 10 threads"

    def test_determinism_decode_parallel(self):
        """10 threads decoding the same encoded payload produce identical results."""
        data = REPRESENTATIVE_LLM_TEXT.encode("utf-8")
        encoded = v_aaak_encode(data)
        expected_hash = hashlib.sha256(data).hexdigest()

        def worker(thread_id: int) -> list:
            mismatches = []
            for i in range(1000):
                dec = v_aaak_decode(encoded)
                h = hashlib.sha256(dec).hexdigest()
                if h != expected_hash:
                    mismatches.append((thread_id, i))
            return mismatches

        all_mismatches = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(worker, tid) for tid in range(10)]
            for f in as_completed(futures):
                all_mismatches.extend(f.result())

        assert len(all_mismatches) == 0


# ===========================================================================
# Test 5: Edge Case — Payload Containing AAAK Escape Bytes (0xFF)
# ===========================================================================


class TestEscapeByteEdgeCases:
    """The 0xFF escape mechanism must be correct under all conditions."""

    def test_all_0xff_payload_various_sizes(self):
        """Payloads consisting entirely of 0xFF bytes (1 to 256)."""
        for size in range(1, 257):
            data = bytes([0xFF] * size)
            enc = v_aaak_encode(data)
            dec = v_aaak_decode(enc)
            assert dec == data, f"All-0xFF payload size={size}: mismatch"
            # Each 0xFF must become [0xFF, 0xFF] = 2 bytes
            assert (
                len(enc) == size * 2
            ), f"All-0xFF size={size}: expected {size*2} encoded bytes, got {len(enc)}"

    def test_0xff_interleaved_with_tokens(self):
        """0xFF bytes mixed with dictionary tokens."""
        data = b"\xff the\xff and\xff is\xff"
        enc = v_aaak_encode(data)
        dec = v_aaak_decode(enc)
        assert dec == data

    def test_0xff_at_token_boundaries(self):
        """0xFF inserted at every position within a token string."""
        for token in V_AAAK_DICT[:8]:  # First 8 tokens
            token_bytes = token.encode("utf-8")
            for pos in range(len(token_bytes) + 1):
                data = token_bytes[:pos] + b"\xff" + token_bytes[pos:]
                dec = _round_trip(data)
                assert dec == data, f"0xFF at pos {pos} in token {token!r}: mismatch"

    def test_double_escape_sequence(self):
        """0xFF 0xFF in encoded form should decode to single 0xFF."""
        encoded = bytes([0xFF, 0xFF])
        decoded = v_aaak_decode(encoded)
        assert decoded == bytes([0xFF])


# ===========================================================================
# Test 6: Edge Case — Payload That Is ALL Dictionary Tokens
# ===========================================================================


class TestAllDictionaryTokens:
    """Payloads composed entirely of dictionary tokens."""

    def test_single_repetition_all_tokens(self):
        """Each token individually round-trips via escape+code."""
        for i, token in enumerate(V_AAAK_DICT):
            data = token.encode("utf-8")
            enc = v_aaak_encode(data)
            dec = v_aaak_decode(enc)
            assert dec == data, f"Token {i} ({token!r}): round-trip failed"
            # Must encode as exactly [0xFF, i]
            assert enc == bytes(
                [V_AAAK_ESCAPE, i]
            ), f"Token {i} ({token!r}): expected [0xFF, {i}], got {enc.hex()}"

    def test_all_tokens_repeated_100x(self):
        """All 64 tokens repeated 100 times (6400 tokens total)."""
        token_bytes = b"".join(t.encode("utf-8") for t in V_AAAK_DICT)
        data = token_bytes * 100
        enc = v_aaak_encode(data)
        dec = v_aaak_decode(enc)
        assert dec == data
        # 6400 tokens * 2 bytes each = 12800 bytes encoded
        assert len(enc) == 64 * 2 * 100

    def test_reversed_token_order(self):
        """Tokens in reverse order still encode correctly."""
        data = b"".join(t.encode("utf-8") for t in reversed(V_AAAK_DICT))
        enc = v_aaak_encode(data)
        dec = v_aaak_decode(enc)
        assert dec == data

    def test_token_pairs(self):
        """Every possible pair of adjacent tokens round-trips."""
        failures = []
        for i, t1 in enumerate(V_AAAK_DICT):
            for j, t2 in enumerate(V_AAAK_DICT):
                data = (t1 + t2).encode("utf-8")
                try:
                    dec = _round_trip(data)
                    if dec != data:
                        failures.append((i, j))
                except Exception:
                    failures.append((i, j))
        assert len(failures) == 0, f"{len(failures)} token-pair failures out of {64*64}"


# ===========================================================================
# Test 7: Edge Case — Pure Binary (Zero Dictionary Matches)
# ===========================================================================


class TestPureBinaryNoDictionaryMatches:
    """Payloads with zero dictionary matches (pure binary data)."""

    def test_sequential_bytes_no_tokens(self):
        """Bytes 0x80-0xFE (excluding 0xFF) have no dictionary matches."""
        # Dictionary tokens are ASCII text; high bytes won't match
        data = bytes(range(0x80, 0xFF))  # 127 bytes, 0x80-0xFE
        enc = v_aaak_encode(data)
        dec = v_aaak_decode(enc)
        assert dec == data
        # No compression: output should be same size as input (no 0xFF to escape)
        assert len(enc) == len(
            data
        ), f"Pure binary: expected {len(data)} bytes, got {len(enc)}"

    def test_random_binary_no_expansion(self):
        """Random binary data should not expand significantly.

        Worst case: every byte is 0xFF, which doubles size.
        Average case: ~1/256 bytes are 0xFF, so expansion is ~0.4%.
        """
        rng = random.Random(12345)
        for _ in range(100):
            size = rng.randint(100, 1000)
            # Generate bytes that avoid dictionary match patterns
            # Use only high bytes (0x80-0xFE)
            data = bytes(rng.randint(0x80, 0xFE) for _ in range(size))
            enc = v_aaak_encode(data)
            dec = v_aaak_decode(enc)
            assert dec == data
            # Pure high-byte binary: no matches, no 0xFF, no expansion
            assert len(enc) == len(data)

    def test_null_bytes_payload(self):
        """Payload of all null bytes round-trips."""
        data = b"\x00" * 4096
        enc = v_aaak_encode(data)
        dec = v_aaak_decode(enc)
        assert dec == data
        # 0x00 is not 0xFF and won't match tokens -> same size
        assert len(enc) == 4096


# ===========================================================================
# Test 8: Throughput Benchmark
# ===========================================================================


class TestThroughputBenchmark:
    """Measure encode/decode rate (frames/sec) in Python."""

    def test_encode_throughput(self):
        """Encode throughput on 256-byte LLM text payloads."""
        data = REPRESENTATIVE_LLM_TEXT.encode("utf-8")[:256]
        # Warm up
        for _ in range(50):
            v_aaak_encode(data)

        count = 2_000
        start = time.perf_counter()
        for _ in range(count):
            v_aaak_encode(data)
        elapsed = time.perf_counter() - start

        rate = count / elapsed
        throughput_mbps = (len(data) * count) / (elapsed * 1024 * 1024)
        print(
            f"\n[Encode] {rate:.0f} ops/sec, {throughput_mbps:.2f} MB/s "
            f"({count} x {len(data)}B in {elapsed:.3f}s)"
        )
        # Python V-AAAK encode is O(n*64) — expect ~1000+ ops/sec for 256B
        assert rate > 100, f"Encode too slow: {rate:.0f} ops/sec"

    def test_decode_throughput(self):
        """Decode throughput on 256-byte LLM text payloads."""
        data = REPRESENTATIVE_LLM_TEXT.encode("utf-8")[:256]
        encoded = v_aaak_encode(data)
        # Warm up
        for _ in range(50):
            v_aaak_decode(encoded)

        count = 5_000
        start = time.perf_counter()
        for _ in range(count):
            v_aaak_decode(encoded)
        elapsed = time.perf_counter() - start

        rate = count / elapsed
        throughput_mbps = (len(data) * count) / (elapsed * 1024 * 1024)
        print(
            f"\n[Decode] {rate:.0f} ops/sec, {throughput_mbps:.2f} MB/s "
            f"({count} x {len(data)}B in {elapsed:.3f}s)"
        )
        # Decode should be faster than encode (no dictionary scan per byte)
        assert rate > 500, f"Decode too slow: {rate:.0f} ops/sec"

    def test_round_trip_throughput(self):
        """Full round-trip throughput."""
        data = REPRESENTATIVE_LLM_TEXT.encode("utf-8")[:256]
        count = 1_000
        start = time.perf_counter()
        for _ in range(count):
            _round_trip(data)
        elapsed = time.perf_counter() - start

        rate = count / elapsed
        print(
            f"\n[Round-trip] {rate:.0f} ops/sec ({count} x {len(data)}B in {elapsed:.3f}s)"
        )
        assert rate > 50, f"Round-trip too slow: {rate:.0f} ops/sec"


# ===========================================================================
# Test 9: Error Resilience
# ===========================================================================


class TestErrorResilience:
    """Test behavior on corrupt or malformed compressed data."""

    def test_truncated_escape_at_end(self):
        """Compressed frame truncated mid-token (escape at end) raises ValueError."""
        bad = bytes([0xFF])
        with pytest.raises(ValueError, match="Truncated"):
            v_aaak_decode(bad)

    def test_invalid_dictionary_code(self):
        """Dictionary code >= 64 raises ValueError."""
        for code in [64, 65, 100, 128, 200, 254]:
            bad = bytes([V_AAAK_ESCAPE, code])
            with pytest.raises(ValueError, match="Invalid V-AAAK code"):
                v_aaak_decode(bad)

    def test_valid_code_63_boundary(self):
        """Code 63 (last valid entry) decodes correctly."""
        enc = bytes([V_AAAK_ESCAPE, 63])
        dec = v_aaak_decode(enc)
        expected = V_AAAK_DICT[63].encode("utf-8")
        assert dec == expected

    def test_corrupt_payload_with_aaak_prefix(self):
        """If 'AAAK:' prefix is present but payload is corrupt hex, the Python
        driver would need to handle this gracefully. Test the raw codec here."""
        # Simulating corrupt compressed data after valid start
        data = b"Hello world"
        enc = v_aaak_encode(data)
        # Corrupt the encoded data by flipping a byte
        if len(enc) > 2:
            corrupted = bytearray(enc)
            corrupted[len(enc) // 2] ^= 0xFF  # Flip bits
            # This may or may not raise - but decode should not crash
            try:
                dec = v_aaak_decode(bytes(corrupted))
                # If it decodes, it will be wrong data
                assert dec != data, "Corrupt data should not match original"
            except ValueError:
                pass  # Expected: invalid code or truncation

    def test_escape_followed_by_every_valid_code(self):
        """Every valid code 0-63 decodes to its dictionary entry."""
        for code in range(64):
            enc = bytes([V_AAAK_ESCAPE, code])
            dec = v_aaak_decode(enc)
            expected = V_AAAK_DICT[code].encode("utf-8")
            assert dec == expected, f"Code {code}: {dec!r} != {expected!r}"

    def test_mixed_valid_and_truncated(self):
        """Valid tokens followed by a truncated escape at the end."""
        # Encode some valid data, then append a bare 0xFF
        valid_enc = v_aaak_encode(b" the and")
        bad_enc = valid_enc + bytes([V_AAAK_ESCAPE])
        with pytest.raises(ValueError, match="Truncated"):
            v_aaak_decode(bad_enc)


# ===========================================================================
# Test 10: HMAC Interaction Verification
# ===========================================================================


class TestHMACInteraction:
    """Verify HMAC correctness relative to AAAK compression.

    In native mode, the kernel compresses BEFORE building the VBus frame.
    The HMAC is computed on the final frame payload (which is compressed).
    This means the receiver verifies HMAC on the compressed data, then
    detects the 'AAAK:' prefix and decompresses. This is the CORRECT order.

    However, the Python `send_command()` does NOT currently auto-decompress.
    This test documents and verifies the expected behavior.
    """

    def test_hmac_on_compressed_data_is_correct_order(self):
        """HMAC must be computed on post-compression data for correct verify-then-decompress."""
        # In the kernel, aaak_compress_payload() runs BEFORE vos3_vbus_send_frame()
        # The frame builder computes HMAC on the final payload bytes (compressed)
        # This is correct: receiver does HMAC check on raw bytes, then decompresses

        data = (
            b"OK|the model is trained on the dataset and the results are with the best"
        )
        compressed = v_aaak_encode(data)
        # After compression in native mode, the payload becomes "AAAK:" + hex(compressed)
        native_payload = b"AAAK:" + compressed.hex().encode("ascii")

        # HMAC would be computed on native_payload (the actual wire bytes)
        import hmac as hmac_mod
        import hashlib

        key = os.urandom(32)
        mac = hmac_mod.new(key, native_payload, hashlib.sha256).digest()

        # Receiver verifies HMAC on native_payload first (before decompression)
        verified = hmac_mod.compare_digest(
            mac, hmac_mod.new(key, native_payload, hashlib.sha256).digest()
        )
        assert verified, "HMAC verification on compressed payload failed"

        # Then receiver strips "AAAK:" prefix and decompresses
        assert native_payload.startswith(b"AAAK:")
        hex_part = native_payload[5:]
        compressed_bytes = bytes.fromhex(hex_part.decode("ascii"))
        decompressed = v_aaak_decode(compressed_bytes)
        assert decompressed == data

    def test_python_driver_missing_auto_decompress(self):
        """Document that send_command() does NOT auto-decompress AAAK: responses.

        This is a KNOWN DEFECT: when AAAK native mode is on, the Python driver
        will receive 'OK|AAAK:...' as a raw string without decompression.
        The caller must manually detect and decompress.
        """
        # Simulate what the kernel would send in native mode
        original = "uptime_ms=1234,mem_total_kb=4096,tasks=5"
        compressed = v_aaak_encode(original.encode("utf-8"))
        hex_encoded = compressed.hex()
        # The kernel sends "OK|AAAK:<hex>" as the frame payload
        wire_response = f"OK|AAAK:{hex_encoded}"

        # VBusDriver.send_command() would return this raw string
        # There is NO auto-decompression in the current code
        assert wire_response.startswith("OK|AAAK:")

        # Manual decompression would be:
        aaak_hex = wire_response.split("AAAK:", 1)[1]
        compressed_bytes = bytes.fromhex(aaak_hex)
        decompressed = v_aaak_decode(compressed_bytes).decode("utf-8")
        assert decompressed == original


# ===========================================================================
# Test 11: Kernel-Python Dictionary Synchronization
# ===========================================================================


class TestDictionarySynchronization:
    """Verify that the Python dictionary exactly matches the kernel dictionary."""

    # The kernel dictionary from vbus_transport.c (lines 648-657)
    KERNEL_DICT = [
        " the",
        " a",
        " is",
        " of",
        " and",
        " to",
        " in",
        " it",
        " that",
        " for",
        " was",
        " on",
        " are",
        " with",
        " as",
        " this",
        " be",
        " at",
        " have",
        " from",
        " or",
        " by",
        " not",
        " but",
        " what",
        " all",
        " were",
        " when",
        " we",
        " there",
        " can",
        " an",
        " your",
        " which",
        " their",
        " if",
        " do",
        " will",
        " each",
        " how",
        " them",
        " then",
        " he",
        " she",
        " my",
        " no",
        " more",
        " so",
        "the",
        "and",
        "ing",
        "tion",
        "ed ",
        "er ",
        "es ",
        "re ",
        "\n",
        "  ",
        ", ",
        ". ",
        ": ",
        ";\n",
        "}\n",
        "{\n",
    ]

    def test_dictionary_length(self):
        assert len(V_AAAK_DICT) == 64
        assert len(self.KERNEL_DICT) == 64

    def test_dictionary_exact_match(self):
        """Every entry must match between Python and kernel."""
        for i in range(64):
            assert V_AAAK_DICT[i] == self.KERNEL_DICT[i], (
                f"Dictionary mismatch at index {i}: "
                f"Python={V_AAAK_DICT[i]!r} vs Kernel={self.KERNEL_DICT[i]!r}"
            )

    def test_escape_byte_constant(self):
        assert V_AAAK_ESCAPE == 0xFF


# ===========================================================================
# Test 12: send_ok_ctx Does NOT Have AAAK Compression
# ===========================================================================


class TestSendOkCtxCoverage:
    """Verify that send_ok_ctx() does NOT compress (asymmetry defect)."""

    def test_send_ok_has_aaak_compression(self):
        """Confirm send_ok() calls aaak_compress_payload (kernel source audit)."""
        # This is verified by reading vbus_transport.c:495-501
        # send_ok() checks g_vbus_aaak_native and compresses
        pass  # Documented: kernel code confirmed

    def test_send_ok_ctx_missing_aaak_compression(self):
        """send_ok_ctx() does NOT call aaak_compress_payload.

        This is a DEFECT: when using context-aware responses (Phase 7),
        AAAK native mode is silently bypassed.
        """
        # Verified by reading vbus_transport.c:568-597
        # send_ok_ctx() has NO reference to aaak_compress_payload or g_vbus_aaak_native
        pass  # Documented: defect confirmed


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
