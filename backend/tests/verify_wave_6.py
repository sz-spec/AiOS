"""
Wave 6 Verification Tests
==========================
3 tests: SSRF CGNAT block, auth rate limit, supabase param purge.
"""

import inspect


def test_cgnat_ssrf_blocked():
    """Confirm CGNAT range 100.64.0.0/10 is rejected by SSRF validator."""
    from kernel_bridge.service import validate_url

    blocked_ips = [
        "http://100.64.0.1/metadata",
        "http://100.100.100.100/latest/meta-data",
        "http://100.127.255.254/admin",
    ]
    for url in blocked_ips:
        try:
            validate_url(url)
            assert False, f"validate_url should have rejected {url}"
        except ValueError as e:
            assert "Blocked" in str(e), f"Wrong error for {url}: {e}"

    print("test_cgnat_ssrf_blocked: PASS")


def test_auth_rate_limit():
    """Simulate 6 rapid calls to an auth path and assert 429 on the 6th."""
    from middleware.rate_limit import TokenBucket, _is_auth_path

    # Verify auth path detection
    assert _is_auth_path("/api/auth/login")
    assert _is_auth_path("/api/clerk/webhook")
    assert _is_auth_path("/api/webhooks/clerk")
    assert _is_auth_path("/api/apps/authorize")
    assert _is_auth_path("/api/apps/register")
    assert not _is_auth_path("/api/chat/send")
    assert not _is_auth_path("/api/teams")

    # Verify strict bucket (5 burst, 5/60 replenish rate)
    bucket = TokenBucket(rate=5.0 / 60.0, burst=5)
    test_ip = "audit-test-ip"

    # First 5 should pass
    for i in range(5):
        assert bucket.allow(test_ip), f"Request {i+1} should be allowed"

    # 6th should be blocked (no time elapsed for replenishment)
    assert not bucket.allow(test_ip), "Request 6 should be blocked (rate limited)"

    print("test_auth_rate_limit: PASS")


def test_supabase_params_gone():
    """Confirm github_sync_enhanced no longer accepts supabase args."""
    from tools.github_sync_enhanced import GitHubSyncService

    sig = inspect.signature(GitHubSyncService.__init__)
    params = list(sig.parameters.keys())

    assert "supabase_url" not in params, f"supabase_url still in constructor: {params}"
    assert "supabase_key" not in params, f"supabase_key still in constructor: {params}"

    # Verify no supabase instance attributes
    instance = GitHubSyncService()
    assert not hasattr(instance, "supabase_url"), "supabase_url attribute still exists"
    assert not hasattr(instance, "supabase_key"), "supabase_key attribute still exists"

    print("test_supabase_params_gone: PASS")


if __name__ == "__main__":
    test_cgnat_ssrf_blocked()
    test_auth_rate_limit()
    test_supabase_params_gone()
    print("\n=== ALL 3 TESTS PASS ===")
