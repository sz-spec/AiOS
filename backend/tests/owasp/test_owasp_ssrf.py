"""
OWASP API7 — Server Side Request Forgery (SSRF) Tests
======================================================

Tests for VOS3 SSRF protection implemented in kernel_bridge/service.py.
Validates that validate_url() correctly blocks internal/private network
access, disallowed schemes, and common SSRF bypass techniques.

Reference: https://owasp.org/API-Security/editions/2023/en/0xa7-server-side-request-forgery/
"""

import socket
import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from kernel_bridge.service import validate_url, _BLOCKED_NETWORKS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_getaddrinfo_for(ip: str):
    """Return a mock getaddrinfo result that resolves to the given IP."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, 443) if family == socket.AF_INET else (ip, 443, 0, 0)
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]


def _patch_dns(ip: str):
    """Convenience: patch socket.getaddrinfo to resolve any hostname to `ip`."""
    return patch(
        "kernel_bridge.service.socket.getaddrinfo",
        return_value=_mock_getaddrinfo_for(ip),
    )


# ===========================================================================
# 1. Blocked CIDR Range Tests — one test per blocked network
# ===========================================================================


@pytest.mark.owasp
class TestBlockedCIDRRanges:
    """Each of the 10 _BLOCKED_NETWORKS must cause validate_url to reject."""

    @pytest.mark.parametrize(
        "cidr,sample_ip",
        [
            ("127.0.0.0/8", "127.0.0.1"),
            ("10.0.0.0/8", "10.0.0.1"),
            ("172.16.0.0/12", "172.16.0.1"),
            ("192.168.0.0/16", "192.168.1.1"),
            ("169.254.0.0/16", "169.254.169.254"),
            ("100.64.0.0/10", "100.64.0.1"),
            ("0.0.0.0/8", "0.0.0.0"),
            ("::1/128", "::1"),
            ("fc00::/7", "fd00::1"),
            ("fe80::/10", "fe80::1"),
        ],
    )
    def test_blocked_cidr(self, cidr, sample_ip):
        """validate_url must reject IPs from each blocked CIDR."""
        with _patch_dns(sample_ip):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("https://evil.example.com/path")

    def test_all_10_networks_present(self):
        """Sanity: _BLOCKED_NETWORKS contains exactly the 10 expected CIDRs."""
        expected = {
            "127.0.0.0/8",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "169.254.0.0/16",
            "100.64.0.0/10",
            "0.0.0.0/8",
            "::1/128",
            "fc00::/7",
            "fe80::/10",
        }
        actual = {str(net) for net in _BLOCKED_NETWORKS}
        assert actual == expected


# ===========================================================================
# 2. Localhost Variants
# ===========================================================================


@pytest.mark.owasp
class TestLocalhostVariants:
    """Various representations of localhost must all be blocked."""

    def test_localhost_127_0_0_1(self):
        with _patch_dns("127.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://127.0.0.1/admin")

    def test_localhost_127_0_0_2(self):
        """127.0.0.2 is still in 127.0.0.0/8 — must be blocked."""
        with _patch_dns("127.0.0.2"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://127.0.0.2/admin")

    def test_localhost_127_1(self):
        """Short form 127.1 — DNS resolves to 127.0.0.1."""
        with _patch_dns("127.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://127.1/admin")

    def test_localhost_hex_ip(self):
        """Hex IP 0x7f000001 — DNS must resolve; if it resolves to 127.0.0.1, blocked."""
        with _patch_dns("127.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://0x7f000001/")


# ===========================================================================
# 3. IPv6 Loopback
# ===========================================================================


@pytest.mark.owasp
class TestIPv6Loopback:
    """IPv6 loopback (::1) must be blocked."""

    def test_ipv6_loopback_bare(self):
        with _patch_dns("::1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://[::1]/")

    def test_ipv6_loopback_bracket(self):
        with _patch_dns("::1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://[::1]:8080/admin")


# ===========================================================================
# 4. AWS IMDS
# ===========================================================================


@pytest.mark.owasp
class TestAWSIMDS:
    """AWS Instance Metadata Service at 169.254.169.254 must be blocked."""

    def test_imds_ip(self):
        with _patch_dns("169.254.169.254"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://169.254.169.254/latest/meta-data/")

    def test_imds_via_hostname(self):
        """Even if a hostname resolves to 169.254.169.254, it must be blocked."""
        with _patch_dns("169.254.169.254"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://metadata.internal/latest/meta-data/")


# ===========================================================================
# 5. Non-HTTP Schemes
# ===========================================================================


@pytest.mark.owasp
class TestBlockedSchemes:
    """Only http:// and https:// are allowed."""

    @pytest.mark.parametrize(
        "url",
        [
            "ftp://evil.example.com/secret.txt",
            "file:///etc/passwd",
            "gopher://evil.example.com/_test",
            "data:text/html,<script>alert(1)</script>",
            "javascript:alert(1)",
        ],
    )
    def test_non_http_scheme_rejected(self, url):
        with pytest.raises(ValueError, match="Blocked scheme"):
            validate_url(url)


# ===========================================================================
# 6. URL-Encoding Bypass Attempts
# ===========================================================================


@pytest.mark.owasp
class TestURLEncodingBypass:
    """URL-encoded payloads should not bypass SSRF checks."""

    def test_url_encoded_slash(self):
        """http://127.0.0.1%2F — urlparse extracts 127.0.0.1 as hostname."""
        with _patch_dns("127.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://127.0.0.1%2F")

    def test_octal_ip_loopback(self):
        """0177.0.0.1 is octal for 127.0.0.1 — DNS resolves it."""
        with _patch_dns("127.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://0177.0.0.1/")


# ===========================================================================
# 7. Decimal IP Bypass
# ===========================================================================


@pytest.mark.owasp
class TestDecimalIPBypass:
    """Decimal IP notation (e.g., 2130706433 = 127.0.0.1) must be blocked."""

    def test_decimal_ip_127_0_0_1(self):
        """http://2130706433 — resolves to 127.0.0.1."""
        with _patch_dns("127.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://2130706433/")

    def test_decimal_ip_10_network(self):
        """http://167772161 = 10.0.0.1."""
        with _patch_dns("10.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://167772161/")


# ===========================================================================
# 8. DNS Resolution Returns Internal IP (DNS Rebinding Vector)
# ===========================================================================


@pytest.mark.owasp
class TestDNSResolutionBlocking:
    """If DNS resolves an external hostname to an internal IP, block it."""

    def test_external_hostname_resolves_to_loopback(self):
        """An attacker-controlled domain resolving to 127.0.0.1 must be caught."""
        with _patch_dns("127.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://attacker-rebind.example.com/steal")

    def test_external_hostname_resolves_to_rfc1918(self):
        """Hostname resolving to 192.168.1.100 must be blocked."""
        with _patch_dns("192.168.1.100"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://internal-service.example.com/api")

    def test_dns_failure_raises_valueerror(self):
        """DNS resolution failure should raise ValueError, not pass through."""
        with patch(
            "kernel_bridge.service.socket.getaddrinfo",
            side_effect=socket.gaierror("Name resolution failed"),
        ):
            with pytest.raises(ValueError, match="DNS resolution failed"):
                validate_url("http://nonexistent.invalid/")


# ===========================================================================
# 9. Valid External URL Passes
# ===========================================================================


@pytest.mark.owasp
class TestValidExternalURL:
    """Legitimate external URLs must be allowed through."""

    def test_valid_https_url(self):
        """A URL resolving to a public IP should pass and return (ip, host)."""
        with _patch_dns("8.8.8.8"):
            resolved_ip, host = validate_url("https://example.com/api/data")
            assert resolved_ip == "8.8.8.8"
            assert host == "example.com"

    def test_valid_http_url(self):
        with _patch_dns("93.184.216.34"):
            resolved_ip, host = validate_url("http://example.com/page")
            assert resolved_ip == "93.184.216.34"
            assert host == "example.com"

    def test_returns_tuple(self):
        """validate_url must return a (resolved_ip, original_host) tuple."""
        with _patch_dns("1.1.1.1"):
            result = validate_url("https://cloudflare.com/dns")
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert result[0] == "1.1.1.1"
            assert result[1] == "cloudflare.com"


# ===========================================================================
# 10. Empty / None / Missing URL
# ===========================================================================


@pytest.mark.owasp
class TestEmptyAndNoneURL:
    """Edge cases: empty string, None, or missing hostname."""

    def test_empty_url(self):
        with pytest.raises((ValueError, AttributeError)):
            validate_url("")

    def test_none_url(self):
        with pytest.raises((ValueError, TypeError, AttributeError)):
            validate_url(None)

    def test_url_no_hostname(self):
        """A URL with no hostname (e.g., just a path) must be rejected."""
        with pytest.raises(ValueError, match="(hostname|scheme)"):
            validate_url("/just/a/path")

    def test_url_only_scheme(self):
        with pytest.raises(ValueError):
            validate_url("http://")


# ===========================================================================
# 11. URL with Auth Component
# ===========================================================================


@pytest.mark.owasp
class TestURLWithAuthComponent:
    """URLs containing user:pass@ must still be validated against the host."""

    def test_auth_component_internal_host(self):
        """http://user:pass@internal resolves to internal IP — blocked."""
        with _patch_dns("10.0.0.5"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://user:pass@internal.corp/admin")

    def test_auth_component_external_host(self):
        """Auth component with a legitimate external host should pass."""
        with _patch_dns("93.184.216.34"):
            resolved_ip, host = validate_url("http://user:pass@example.com/api")
            assert resolved_ip == "93.184.216.34"
            assert host == "example.com"


# ===========================================================================
# 12. Double-URL Encoding Attempts
# ===========================================================================


@pytest.mark.owasp
class TestDoubleURLEncoding:
    """Double-encoded payloads should not bypass SSRF checks."""

    def test_double_encoded_loopback(self):
        """Double-encoded 127.0.0.1 — DNS still resolves the actual hostname."""
        with _patch_dns("127.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://%31%32%37%2e%30%2e%30%2e%31/")

    def test_double_encoded_at_sign(self):
        """http://example.com%40127.0.0.1 — parser extracts host, DNS resolves."""
        with _patch_dns("127.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://example.com%40127.0.0.1/")


# ===========================================================================
# Additional Edge Cases
# ===========================================================================


@pytest.mark.owasp
class TestSSRFEdgeCases:
    """Additional SSRF edge cases for comprehensive coverage."""

    def test_cgnat_range_blocked(self):
        """100.64.x.x (CGNAT / RFC 6598) must be blocked."""
        with _patch_dns("100.64.1.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://cgnat-host.example.com/")

    def test_ipv6_ula_blocked(self):
        """IPv6 ULA (fc00::/7) must be blocked."""
        with _patch_dns("fd12:3456:789a::1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://[fd12:3456:789a::1]/")

    def test_ipv6_link_local_blocked(self):
        """IPv6 link-local (fe80::/10) must be blocked."""
        with _patch_dns("fe80::1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://[fe80::1]/")

    def test_this_network_blocked(self):
        """0.0.0.0/8 ('this' network) must be blocked."""
        with _patch_dns("0.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://0.0.0.1/")

    def test_multiple_dns_results_all_checked(self):
        """If DNS returns multiple IPs, ALL must be checked (not just first)."""
        family = socket.AF_INET
        results = [
            (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443)),
            (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ]
        with patch("kernel_bridge.service.socket.getaddrinfo", return_value=results):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("https://mixed-result.example.com/")

    def test_getaddrinfo_empty_results(self):
        """DNS returning empty results should raise ValueError."""
        with patch("kernel_bridge.service.socket.getaddrinfo", return_value=[]):
            with pytest.raises(ValueError, match="no addresses"):
                validate_url("https://empty-dns.example.com/")

    def test_port_does_not_bypass_check(self):
        """Non-standard port should not bypass IP checks."""
        with _patch_dns("10.0.0.1"):
            with pytest.raises(ValueError, match="Blocked"):
                validate_url("http://evil.example.com:8080/admin")
