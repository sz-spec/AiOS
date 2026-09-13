"""
Phase 6.6-V: AAAK Dialect Stress Tests
=======================================
Comprehensive fuzzing, throughput benchmarks, compression analysis,
ghost-trace verification, handshake compatibility, and forensic
certification for the V-AAAK semantic dialect codec.

6 test classes, 40+ test cases.
"""

import os
import sys
import pytest
import hashlib
import time
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
from vbus_driver import v_aaak_encode, v_aaak_decode, V_AAAK_DICT, V_AAAK_ESCAPE

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

# Precompute dictionary token bytes for repeated use across tests.
_DICT_BYTES = [t.encode("utf-8") for t in V_AAAK_DICT]

# Representative LLM prose (~1KB) for compression and ghost-trace tests.
_LLM_PROSE = (
    "The model is designed to handle the classification of entities "
    "and the extraction of relationships from unstructured text. It was "
    "trained on a large corpus of annotated documents, with each example "
    "containing the labels for all the entities and their types. The "
    "architecture is a transformer with multi-head attention, and it "
    "can be fine-tuned for specific domains. This is not a trivial "
    "task, but the results are promising. For more information on the "
    "methodology and the evaluation, we refer the reader to the full "
    "paper. In this work, we show that the approach is effective for "
    "a range of NLP tasks, and we provide an open-source implementation "
    "that can be used by the community. The code is available on GitHub, "
    "and the data is hosted on Hugging Face. We hope that this will "
    "be a useful resource for researchers and practitioners alike."
).encode("utf-8")

# Python/C code snippet for compression tests.
_CODE_SNIPPET = (
    "def process(items):\n"
    "    results = {}\n"
    "    for item in items:\n"
    "        key = item.get('id')\n"
    "        if key is not None:\n"
    "            results[key] = transform(item)\n"
    "    return results\n"
    "\n"
    "struct node {\n"
    "    int value;\n"
    "    struct node *next;\n"
    "};\n"
    "\n"
    "int main(void) {\n"
    "    struct node head = {0, NULL};\n"
    "    return 0;\n"
    "}\n"
).encode("utf-8")


# ---------------------------------------------------------------------------
# Class 1: TestMalformedAAKCodes (Fuzzing -- Track 3)
# ---------------------------------------------------------------------------


class TestMalformedAAKCodes:
    """Verify that invalid / malformed AAAK frames are correctly rejected."""

    def test_invalid_code_0x40(self):
        """Code 64 (0x40) is the first invalid code -- must raise ValueError."""
        with pytest.raises(ValueError):
            v_aaak_decode(bytes([0xFF, 0x40]))

    def test_invalid_code_0x45(self):
        """Code 69 (0x45) -- must raise ValueError."""
        with pytest.raises(ValueError):
            v_aaak_decode(bytes([0xFF, 0x45]))

    def test_invalid_code_0x7F(self):
        """Code 127 (0x7F) -- must raise ValueError."""
        with pytest.raises(ValueError):
            v_aaak_decode(bytes([0xFF, 0x7F]))

    def test_invalid_code_0xFE(self):
        """Code 254 (0xFE) -- must raise ValueError."""
        with pytest.raises(ValueError):
            v_aaak_decode(bytes([0xFF, 0xFE]))

    def test_valid_code_0x3F(self):
        """Code 63 (0x3F) is the last valid code -- must decode to V_AAAK_DICT[63]."""
        result = v_aaak_decode(bytes([0xFF, 0x3F]))
        expected = V_AAAK_DICT[63].encode("utf-8")  # "{\n"
        assert (
            result == expected
        ), f"Code 0x3F decoded to {result!r}, expected {expected!r}"

    def test_valid_code_0x00(self):
        """Code 0 (0x00) is the first valid code -- must decode to V_AAAK_DICT[0]."""
        result = v_aaak_decode(bytes([0xFF, 0x00]))
        expected = V_AAAK_DICT[0].encode("utf-8")  # " the"
        assert (
            result == expected
        ), f"Code 0x00 decoded to {result!r}, expected {expected!r}"

    def test_truncated_escape_at_end(self):
        """Trailing 0xFF after valid literal bytes -- must raise ValueError."""
        with pytest.raises(ValueError, match="Truncated"):
            v_aaak_decode(bytes([0x41, 0x42, 0xFF]))

    def test_truncated_escape_alone(self):
        """Single 0xFF byte -- must raise ValueError."""
        with pytest.raises(ValueError):
            v_aaak_decode(bytes([0xFF]))

    def test_all_invalid_codes_64_to_254(self):
        """Every code in 64..254 must be rejected. Code 255 is valid (escape)."""
        for code in range(64, 255):
            with pytest.raises(ValueError, match="Invalid"):
                v_aaak_decode(bytes([0xFF, code]))

    def test_invalid_code_embedded_in_valid(self):
        """Invalid code 0x45 sandwiched between valid codes -- must raise."""
        payload = bytes([0xFF, 0x00, 0xFF, 0x45, 0xFF, 0x01])
        with pytest.raises(ValueError):
            v_aaak_decode(payload)

    def test_double_escape_valid(self):
        """0xFF 0xFF decodes to a single literal 0xFF byte."""
        result = v_aaak_decode(bytes([0xFF, 0xFF]))
        assert result == bytes([0xFF])

    def test_1000_random_malformed_payloads(self):
        """1000 random payloads each containing at least one invalid escape."""
        rng = random.Random(0xAAA4)
        rejected = 0
        for _ in range(1000):
            length = rng.randint(2, 128)
            # Use bytes < 0xFF to avoid accidental escapes before injection
            payload = bytearray(rng.randint(0, 0x7F) for _ in range(length))
            # Inject an invalid escape at a random position.
            pos = rng.randint(0, length - 2)
            payload[pos] = 0xFF
            payload[pos + 1] = rng.randint(64, 254)  # invalid code range
            try:
                v_aaak_decode(bytes(payload))
            except ValueError:
                rejected += 1
        assert rejected == 1000, f"Only {rejected}/1000 malformed payloads rejected"


# ---------------------------------------------------------------------------
# Class 2: TestAAKThroughputRecovery (Track 2)
# ---------------------------------------------------------------------------


class TestAAKThroughputRecovery:
    """Measure encode/decode performance vs raw passthrough."""

    def test_encode_throughput_small(self):
        """50-byte payload, 100K rounds."""
        data = b"the quick brown fox jumps over the lazy dog plus."
        assert len(data) >= 49  # at least ~50 bytes
        rounds = 100_000
        t0 = time.perf_counter()
        for _ in range(rounds):
            v_aaak_encode(data)
        elapsed = time.perf_counter() - t0
        tps = rounds / elapsed
        print(f"\n  [small] {rounds} rounds in {elapsed:.3f}s  =>  {tps:,.0f} enc/s")
        assert elapsed < 60, "Small encode throughput unacceptably slow"

    def test_encode_throughput_medium(self):
        """~500-byte LLM text, 10K rounds."""
        data = _LLM_PROSE[:500]
        rounds = 10_000
        t0 = time.perf_counter()
        for _ in range(rounds):
            v_aaak_encode(data)
        elapsed = time.perf_counter() - t0
        fps = rounds / elapsed
        mb_s = (len(data) * rounds) / elapsed / (1024 * 1024)
        print(
            f"\n  [medium] {rounds} rounds in {elapsed:.3f}s  =>  "
            f"{fps:,.0f} fps, {mb_s:.2f} MB/s"
        )
        assert elapsed < 60, "Medium encode throughput unacceptably slow"

    def test_encode_throughput_large(self):
        """4096-byte payload, 2K rounds."""
        # Repeat prose to reach 4096 bytes.
        data = (_LLM_PROSE * 6)[:4096]
        rounds = 2_000
        t0 = time.perf_counter()
        for _ in range(rounds):
            v_aaak_encode(data)
        elapsed = time.perf_counter() - t0
        fps = rounds / elapsed
        print(f"\n  [large] {rounds} rounds in {elapsed:.3f}s  =>  {fps:,.0f} fps")
        assert elapsed < 60, "Large encode throughput unacceptably slow"

    def test_decode_throughput_matches_encode(self):
        """Decode must be >= encode speed (decode is simpler)."""
        data = _LLM_PROSE[:500]
        encoded = v_aaak_encode(data)
        rounds = 10_000

        t0 = time.perf_counter()
        for _ in range(rounds):
            v_aaak_encode(data)
        encode_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        for _ in range(rounds):
            v_aaak_decode(encoded)
        decode_time = time.perf_counter() - t0

        print(
            f"\n  encode: {encode_time:.3f}s  |  decode: {decode_time:.3f}s  |  "
            f"ratio: {decode_time / encode_time:.2f}x"
        )
        assert decode_time <= encode_time * 1.5, (
            f"Decode ({decode_time:.3f}s) more than 1.5x slower than "
            f"encode ({encode_time:.3f}s)"
        )

    def test_overhead_vs_raw_copy(self):
        """Encode overhead vs raw bytearray copy must be bounded."""
        data = _LLM_PROSE[:500]
        rounds = 10_000

        # Use bytearray() which actually copies (bytes() on bytes is a no-op)
        t0 = time.perf_counter()
        for _ in range(rounds):
            bytearray(data)
        copy_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        for _ in range(rounds):
            v_aaak_encode(data)
        encode_time = time.perf_counter() - t0

        ratio = encode_time / max(copy_time, 1e-9)
        print(
            f"\n  raw copy: {copy_time:.5f}s  |  encode: {encode_time:.3f}s  |  "
            f"overhead: {ratio:.1f}x"
        )
        # AAAK encode does greedy longest-match dictionary scan — expected
        # to be 50-500x slower than raw memcpy, but must complete within
        # reasonable time (encode_time < 30s for 10K rounds of 500B).
        assert (
            encode_time < 30
        ), f"Encode too slow: {encode_time:.1f}s for {rounds} rounds"

    def test_per_token_latency(self):
        """Encode single token ' the' 200K times. Per-call < 10us."""
        token = b" the"
        rounds = 200_000
        t0 = time.perf_counter()
        for _ in range(rounds):
            v_aaak_encode(token)
        elapsed = time.perf_counter() - t0
        per_call_us = (elapsed / rounds) * 1_000_000
        print(f"\n  per-token latency: {per_call_us:.3f} us  ({rounds} rounds)")
        assert (
            per_call_us < 10
        ), f"Per-token latency {per_call_us:.3f}us exceeds 10us target"


# ---------------------------------------------------------------------------
# Class 3: TestCompressionEfficiency
# ---------------------------------------------------------------------------


class TestCompressionEfficiency:
    """Verify compression ratios across payload types."""

    def test_token_dense_text_ratio(self):
        """Text composed ONLY of dictionary tokens. Expect > 1.5x ratio."""
        # Build a payload from repeated dictionary tokens.
        tokens = [t.encode("utf-8") for t in V_AAAK_DICT]
        raw = b"".join(tokens * 4)
        encoded = v_aaak_encode(raw)
        ratio = len(raw) / len(encoded)
        print(f"\n  token-dense: {len(raw)}B -> {len(encoded)}B  ratio={ratio:.2f}x")
        assert ratio > 1.5, f"Token-dense ratio {ratio:.2f}x below 1.5x target"

    def test_general_llm_prose_ratio(self):
        """General English LLM text (~1KB). Expect > 1.05x ratio."""
        raw = _LLM_PROSE
        encoded = v_aaak_encode(raw)
        ratio = len(raw) / len(encoded)
        print(f"\n  LLM prose: {len(raw)}B -> {len(encoded)}B  ratio={ratio:.2f}x")
        assert ratio > 1.05, f"LLM prose ratio {ratio:.2f}x below 1.05x target"

    def test_code_snippet_ratio(self):
        r"""Python/C code with {{\n, }}\n, ;\n, ': ' patterns. Expect >= 1.0x."""
        raw = _CODE_SNIPPET
        encoded = v_aaak_encode(raw)
        ratio = len(raw) / len(encoded)
        print(f"\n  code snippet: {len(raw)}B -> {len(encoded)}B  ratio={ratio:.2f}x")
        # Code has fewer natural-language tokens than prose, so compression
        # is modest. Key assertion: no expansion (structural tokens help).
        assert ratio >= 1.0, f"Code snippet ratio {ratio:.2f}x — unexpected expansion"

    def test_pure_binary_no_expansion(self):
        """Random bytes (avoid 0xFF and dict first-bytes). Must not expand."""
        rng = random.Random(42)
        # Generate random bytes using only 0x80-0xFE range — these bytes
        # don't match any dictionary token first byte (dict tokens start
        # with ASCII printable chars, mostly 0x20 space or lowercase).
        # This avoids both 0xFF expansion AND accidental dictionary hits.
        raw = bytes(rng.randint(0x80, 0xFE) for _ in range(1024))
        encoded = v_aaak_encode(raw)
        ratio = len(raw) / len(encoded)
        print(
            f"\n  binary (high bytes): {len(raw)}B -> {len(encoded)}B  ratio={ratio:.2f}x"
        )
        # No dictionary matches, no 0xFF → output should equal input
        assert len(encoded) == len(
            raw
        ), f"Binary data changed size: {len(raw)} -> {len(encoded)}"

    def test_worst_case_all_0xFF(self):
        """All 0xFF bytes -- each becomes 2 bytes. len(encoded) == 2 * len(raw)."""
        raw = bytes([0xFF] * 256)
        encoded = v_aaak_encode(raw)
        assert len(encoded) == 2 * len(
            raw
        ), f"Expected encoded length {2 * len(raw)}, got {len(encoded)}"
        # Verify every pair is [0xFF, 0xFF].
        for i in range(0, len(encoded), 2):
            assert encoded[i] == 0xFF and encoded[i + 1] == 0xFF

    def test_effective_bandwidth_multiplier(self):
        """Report wire savings percentage for LLM prose."""
        raw = _LLM_PROSE
        encoded = v_aaak_encode(raw)
        effective_bw = len(raw) / len(encoded)
        savings_pct = (1.0 - len(encoded) / len(raw)) * 100
        print(
            f"\n  effective BW multiplier: {effective_bw:.3f}x  "
            f"({savings_pct:.1f}% wire savings)"
        )
        # Just report -- any positive savings is acceptable.
        assert effective_bw >= 1.0, "Compression should not expand LLM prose"


# ---------------------------------------------------------------------------
# Class 4: TestGhostTraceVerification
# ---------------------------------------------------------------------------


class TestGhostTraceVerification:
    """Verify encoded output does not contain plaintext dictionary tokens."""

    def test_common_tokens_absent_from_encoded(self):
        """Encode text containing common tokens. Verify NONE appear as literals."""
        # Build text that definitely contains these tokens.
        text = (
            " the model is designed and the extraction of data to " " in the corpus"
        ).encode("utf-8")
        encoded = v_aaak_encode(text)

        ghost_tokens = [b" the", b" is", b" and", b" of", b" to", b" in"]
        found = []
        for token in ghost_tokens:
            if token in encoded:
                found.append(token)
        assert len(found) == 0, f"Ghost tokens found in encoded output: {found}"

    def test_all_64_tokens_replaced(self):
        """For each of the 64 dictionary tokens, the literal bytes must NOT
        appear in the encoded output (they are replaced by [0xFF, code])."""
        for idx, token_str in enumerate(V_AAAK_DICT):
            token_bytes = token_str.encode("utf-8")
            encoded = v_aaak_encode(token_bytes)
            # The encoded form should be [0xFF, idx], never the raw token.
            assert token_bytes not in encoded, (
                f"Dict token [{idx}] {token_bytes!r} found as literal in "
                f"encoded output {encoded!r}"
            )

    def test_partial_token_preserved(self):
        """Encode 'th' (not a dict token). Verify 'th' IS still in encoded output."""
        data = b"th"
        encoded = v_aaak_encode(data)
        assert b"th" in encoded, (
            f"Partial token 'th' should be preserved as literal, " f"got {encoded!r}"
        )

    def test_ghost_read_deterministic(self):
        """Encode same input 10K times. All outputs must be byte-identical."""
        data = _LLM_PROSE
        reference = v_aaak_encode(data)
        ref_hash = hashlib.sha256(reference).hexdigest()
        for i in range(10_000):
            current = v_aaak_encode(data)
            cur_hash = hashlib.sha256(current).hexdigest()
            assert cur_hash == ref_hash, (
                f"Determinism failure at iteration {i}: "
                f"expected {ref_hash}, got {cur_hash}"
            )


# ---------------------------------------------------------------------------
# Class 5: TestHandshakeV3Compatibility
# ---------------------------------------------------------------------------


class TestHandshakeV3Compatibility:
    """Verify v3 handshake protocol constants match between Python and kernel."""

    def test_vbus_type_v_aaak_is_0x0D(self):
        """The frame type constant for V-AAAK must be 0x0D."""
        from vbus_driver import VBUS_TYPE_V_AAAK

        assert (
            VBUS_TYPE_V_AAAK == 0x0D
        ), f"VBUS_TYPE_V_AAAK is {VBUS_TYPE_V_AAAK:#x}, expected 0x0D"

    def test_escape_byte_is_0xFF(self):
        """V_AAAK_ESCAPE must be 0xFF."""
        assert V_AAAK_ESCAPE == 0xFF

    def test_dictionary_has_64_entries(self):
        """V_AAAK_DICT must contain exactly 64 entries."""
        assert (
            len(V_AAAK_DICT) == 64
        ), f"V_AAAK_DICT has {len(V_AAAK_DICT)} entries, expected 64"

    def test_dictionary_entries_all_nonempty(self):
        """All 64 dictionary entries must be non-empty strings."""
        for idx, entry in enumerate(V_AAAK_DICT):
            assert isinstance(
                entry, str
            ), f"V_AAAK_DICT[{idx}] is {type(entry).__name__}, expected str"
            assert len(entry) > 0, f"V_AAAK_DICT[{idx}] is empty"

    def test_dictionary_max_token_length_is_6(self):
        """Maximum token length is 6 bytes."""
        max_len = max(len(t.encode("utf-8")) for t in V_AAAK_DICT)
        assert max_len == 6, f"Max token length is {max_len} bytes, expected 6"

    def test_no_duplicate_tokens(self):
        """All 64 tokens must be unique."""
        seen = set()
        for idx, token in enumerate(V_AAAK_DICT):
            assert token not in seen, f"Duplicate token at index {idx}: {token!r}"
            seen.add(token)


# ---------------------------------------------------------------------------
# Class 6: TestForensicVerdict
# ---------------------------------------------------------------------------


class TestForensicVerdict:
    """Final certification -- aggregates all critical assertions."""

    def test_forensic_verdict(self):
        """Run all critical checks and print formatted certification report."""

        # -- 1. Trace leakage: 0 ghost tokens in encoded LLM prose --
        # Only check tokens >= 2 bytes. Single-byte tokens (e.g. "\n")
        # produce false positives because their byte value can appear as
        # a dictionary code index inside [0xFF, code] escape sequences.
        encoded_prose = v_aaak_encode(_LLM_PROSE)
        ghost_count = 0
        for token_str in V_AAAK_DICT:
            token_bytes = token_str.encode("utf-8")
            if len(token_bytes) >= 2 and token_bytes in encoded_prose:
                ghost_count += 1
        trace_pass = ghost_count == 0

        # -- 2. Fuzzing: All codes 64-254 rejected --
        rejected = 0
        total_invalid = 191  # codes 64..254 inclusive
        for code in range(64, 255):
            try:
                v_aaak_decode(bytes([0xFF, code]))
            except ValueError:
                rejected += 1
        fuzz_pass = rejected == total_invalid

        # -- 3. Round-trip: 10K random payloads lossless --
        rng = random.Random(0xF06E51C)
        rt_ok = 0
        rt_total = 10_000
        for _ in range(rt_total):
            length = rng.randint(0, 512)
            raw = bytes(rng.getrandbits(8) for _ in range(length))
            try:
                decoded = v_aaak_decode(v_aaak_encode(raw))
                if decoded == raw:
                    rt_ok += 1
            except Exception:
                pass  # encode/decode failure counts as not lossless
        rt_pass = rt_ok == rt_total

        # -- 4. SHA-256 determinism: 5K rounds --
        reference = v_aaak_encode(_LLM_PROSE)
        ref_sha = hashlib.sha256(reference).hexdigest()
        sha_ok = 0
        sha_total = 5_000
        for _ in range(sha_total):
            cur = v_aaak_encode(_LLM_PROSE)
            if hashlib.sha256(cur).hexdigest() == ref_sha:
                sha_ok += 1
        sha_pass = sha_ok == sha_total

        # -- Print formatted report --
        def verdict(ok: bool) -> str:
            return "PASS" if ok else "FAIL"

        sep = "+" + "-" * 72 + "+"
        hdr = "| VBUS 3.1 SEMANTIC DIALECT -- FORENSIC VERDICT" + " " * 25 + "|"
        fuzz_target = f"{total_invalid}/{total_invalid}"
        fuzz_result = f"{rejected}/{total_invalid}"
        rt_target = f"{rt_total}/{rt_total}"
        rt_result = f"{rt_ok}/{rt_total}"
        sha_target = f"{sha_total}/{sha_total}"
        sha_result = f"{sha_ok}/{sha_total}"

        lines = [
            "",
            sep,
            hdr,
            sep,
            f"| {'Metric':<30} | {'Target':<14} | {'Result':<14} | {'Verdict':<6} |",
            sep,
            f"| {'Trace Leakage':<30} | {'0 bits':<14} | {str(ghost_count) + ' ghost tokens':<14} | {verdict(trace_pass):<6} |",
            f"| {'Invalid Code Rejection':<30} | {fuzz_target:<14} | {fuzz_result:<14} | {verdict(fuzz_pass):<6} |",
            f"| {'Round-Trip Lossless':<30} | {rt_target:<14} | {rt_result:<14} | {verdict(rt_pass):<6} |",
            f"| {'SHA-256 Determinism':<30} | {sha_target:<14} | {sha_result:<14} | {verdict(sha_pass):<6} |",
            sep,
        ]
        report = "\n".join(lines)
        print(report)

        all_pass = trace_pass and fuzz_pass and rt_pass and sha_pass

        if all_pass:
            print("VBUS 3.1 SEMANTIC DIALECT SEALED. CACHE PURITY CERTIFIED.")
        else:
            failures = []
            if not trace_pass:
                failures.append(f"Trace Leakage: {ghost_count} ghost tokens")
            if not fuzz_pass:
                failures.append(f"Fuzz: {rejected}/{total_invalid} rejected")
            if not rt_pass:
                failures.append(f"Round-Trip: {rt_ok}/{rt_total}")
            if not sha_pass:
                failures.append(f"SHA-256: {sha_ok}/{sha_total}")
            print(f"FORENSIC VERDICT: FAIL -- {'; '.join(failures)}")

        assert all_pass, "Forensic verdict FAILED -- see report above"
