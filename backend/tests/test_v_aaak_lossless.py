#!/usr/bin/env python3
"""V-AAAK Lossless Codec — Exhaustive Forensic Test Suite"""

import os, sys, pytest, hashlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
from vbus_driver import v_aaak_encode, v_aaak_decode, V_AAAK_DICT, V_AAAK_ESCAPE


class TestExhaustiveRoundTrip:
    """Every possible input pattern must round-trip losslessly."""

    def test_each_dictionary_token_individually(self):
        """Each of the 64 tokens encodes to [0xFF, code] and decodes back."""
        for i, token in enumerate(V_AAAK_DICT):
            raw = token.encode("utf-8")
            enc = v_aaak_encode(raw)
            # Should contain the escape+code sequence
            assert (
                bytes([V_AAAK_ESCAPE, i]) in enc
            ), f"Token {i} ({token!r}) not encoded as escape+code"
            dec = v_aaak_decode(enc)
            assert (
                dec == raw
            ), f"Token {i} ({token!r}): decode mismatch {dec!r} != {raw!r}"

    def test_all_tokens_concatenated(self):
        """All 64 tokens concatenated must round-trip."""
        raw = b"".join(t.encode("utf-8") for t in V_AAAK_DICT)
        enc = v_aaak_encode(raw)
        dec = v_aaak_decode(enc)
        assert dec == raw, "Concatenated tokens mismatch"
        # Verify compression happened (should be much smaller)
        assert len(enc) < len(raw), f"No compression: {len(enc)} >= {len(raw)}"

    def test_escape_byte_all_positions(self):
        """0xFF at every position in a buffer survives round-trip."""
        for pos in range(16):
            raw = bytearray(16)
            raw[pos] = 0xFF
            enc = v_aaak_encode(bytes(raw))
            dec = v_aaak_decode(enc)
            assert dec == bytes(raw), f"0xFF at position {pos}: mismatch"

    def test_consecutive_escape_bytes(self):
        """Multiple consecutive 0xFF bytes."""
        for count in [1, 2, 3, 4, 8, 16, 64]:
            raw = bytes([0xFF] * count)
            enc = v_aaak_encode(raw)
            dec = v_aaak_decode(enc)
            assert dec == raw, f"{count} consecutive 0xFF: mismatch"

    def test_every_single_byte_value(self):
        """Each individual byte 0x00-0xFF round-trips."""
        for b in range(256):
            raw = bytes([b])
            enc = v_aaak_encode(raw)
            dec = v_aaak_decode(enc)
            assert dec == raw, f"Byte 0x{b:02x}: mismatch"

    def test_all_two_byte_combinations_with_escape(self):
        """Two-byte combos involving 0xFF."""
        for second in range(256):
            raw = bytes([0xFF, second])
            enc = v_aaak_encode(raw)
            dec = v_aaak_decode(enc)
            assert dec == raw, f"0xFF+0x{second:02x}: mismatch"

    def test_token_boundary_overlap(self):
        """Tokens that overlap at boundaries must not cross-contaminate."""
        # " the" and "the" are both in dict — ensure " the" is matched greedily
        raw = b" the"
        enc = v_aaak_encode(raw)
        dec = v_aaak_decode(enc)
        assert dec == raw
        # Should match " the" (token 0, 4 chars) not " " + "the" (token 48, 3 chars)
        assert enc == bytes([V_AAAK_ESCAPE, 0]), f"Greedy match failed: {enc.hex()}"

    def test_sha256_determinism_1000_rounds(self):
        """Same input encoded 1000 times produces identical SHA-256."""
        raw = b"the model is trained on the dataset and the results are with the best"
        baseline_hash = hashlib.sha256(v_aaak_encode(raw)).hexdigest()
        for i in range(1000):
            h = hashlib.sha256(v_aaak_encode(raw)).hexdigest()
            assert h == baseline_hash, f"Non-deterministic at round {i}"

    def test_large_payload_4kb(self):
        """4KB payload round-trips losslessly."""
        raw = (b"the quick brown fox and the lazy dog " * 120)[:4096]
        enc = v_aaak_encode(raw)
        dec = v_aaak_decode(enc)
        assert dec == raw
        assert hashlib.sha256(dec).hexdigest() == hashlib.sha256(raw).hexdigest()

    def test_realistic_llm_output(self):
        """Realistic LLM-generated text round-trips."""
        text = """The transformer architecture is based on the attention mechanism, which was introduced in the paper "Attention is All You Need". The model is trained on a large dataset and the results are impressive. Each layer of the model can be described as:

```python
def forward(self, x):
    return self.attention(x) + x
```

This is a simplified version, but the core idea is that the model learns to attend to the most relevant parts of the input.
"""
        raw = text.encode("utf-8")
        enc = v_aaak_encode(raw)
        dec = v_aaak_decode(enc)
        assert dec == raw
        ratio = len(raw) / len(enc)
        # Should get some compression on natural text
        assert ratio > 1.0, f"No compression: ratio={ratio:.2f}"

    def test_truncated_decode_rejects(self):
        """Truncated encoded data (escape at end) raises error."""
        bad = bytes([V_AAAK_ESCAPE])  # escape with no following byte
        with pytest.raises(ValueError):
            v_aaak_decode(bad)

    def test_invalid_code_rejects(self):
        """Invalid dictionary code (>= 64) raises error."""
        bad = bytes([V_AAAK_ESCAPE, 200])  # code 200 > 64
        with pytest.raises(ValueError):
            v_aaak_decode(bad)

    def test_compression_ratio_benchmark(self):
        """Measure compression ratio on token-dense text."""
        text = (
            " the model is a transformer that was trained on the dataset"
            " and the results are with the best performance for this task."
            " the architecture is based on the attention mechanism which"
            " can be applied to the input and the output of the model.\n"
        ) * 20
        raw = text.encode("utf-8")
        enc = v_aaak_encode(raw)
        ratio = len(raw) / len(enc)
        print(f"\nCompression ratio: {ratio:.2f}x ({len(raw)} -> {len(enc)} bytes)")
        assert ratio > 1.2, f"Compression ratio {ratio:.2f}x too low"
