"""Unit tests for tools/log_pii_scan.py.

Verifies that planted PII is detected and that allowlisted patterns are
skipped, so the CI gate can be trusted to actually back the
"Zero PII in logs" Annex IV claim.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from tools.log_pii_scan import (  # noqa: E402
    luhn_check,
    israeli_id_check,
    scan_text,
    scan_path,
    main as scan_main,
)


def test_luhn_check_recognises_known_test_card():
    # Visa test number — passes Luhn.
    assert luhn_check("4242424242424242") is True
    # Same digits altered — fails Luhn.
    assert luhn_check("4242424242424243") is False


def test_luhn_check_rejects_short():
    assert luhn_check("4242") is False


def test_israeli_id_check():
    # Known-valid synthetic IDs (Luhn pass).
    assert israeli_id_check("123456782") is True
    # Same digits with checksum digit perturbed.
    assert israeli_id_check("123456781") is False


def test_scan_text_finds_email_phone_ssn():
    text = textwrap.dedent("""
        2026-05-09 INFO contact: alice@aidg.com
        2026-05-09 INFO calling +14155551234 from gateway
        2026-05-09 WARN ssn=123-45-6789 leaked
        2026-05-09 INFO test@example.com is allowlisted by suffix
    """)
    hits = scan_text(text, allowlist=[])
    {kind for _kind, kind in [(k, k) for k, _ in hits]}
    # Three hits expected; the @example.com email is auto-skipped.
    assert ("email", "alice@aidg.com") in hits
    assert ("phone", "+14155551234") in hits
    assert ("ssn", "123-45-6789") in hits
    assert all(m != "test@example.com" for _, m in hits)


def test_scan_text_respects_allowlist():
    text = "Internal contact: ops@aidg.com"
    hits = scan_text(text, allowlist=["ops@aidg.com"])
    assert hits == []


def test_scan_text_finds_credit_card_via_luhn():
    text = "Token: 4242424242424242 (test)"
    hits = scan_text(text, allowlist=[])
    assert ("credit_card", "4242424242424242") in hits


def test_scan_text_skips_random_long_digits_that_fail_luhn():
    text = "Random digits: 1234567890123456"
    hits = scan_text(text, allowlist=[])
    # 1234567890123456 fails Luhn — should not be flagged as a card.
    assert all(kind != "credit_card" for kind, _ in hits)


def test_scan_path_walks_directory(tmp_path):
    (tmp_path / "a.log").write_text("ok\n")
    (tmp_path / "b.log").write_text("alice@aidg.com\n")
    (tmp_path / "c.txt").write_text("+14155551234\n")
    hits = scan_path(tmp_path, allowlist=[])
    matches = {match for _path, _kind, match in hits}
    assert "alice@aidg.com" in matches
    assert "+14155551234" in matches


def test_main_returns_nonzero_on_hits(tmp_path):
    log = tmp_path / "boot.log"
    log.write_text("2026 - leaked: ops@aidg.com")
    rc = scan_main([str(tmp_path), "--allowlist", "/dev/null"])
    assert rc == 1


def test_main_returns_zero_when_clean(tmp_path):
    log = tmp_path / "boot.log"
    log.write_text("2026 - boot complete; no PII here.")
    rc = scan_main([str(tmp_path), "--allowlist", "/dev/null"])
    assert rc == 0


def test_main_returns_zero_after_allowlist(tmp_path):
    log = tmp_path / "boot.log"
    log.write_text("ops@aidg.com is the noreply alias")
    allow = tmp_path / "allow.txt"
    allow.write_text("ops@aidg.com\n")
    rc = scan_main([str(tmp_path), "--allowlist", str(allow)])
    assert rc == 0
