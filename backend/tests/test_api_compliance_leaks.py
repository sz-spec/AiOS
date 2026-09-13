"""Tests for the Sovereign-Shield on-demand leak-detection route.

POST /api/compliance/leaks/snapshot — admin/audit-gated, bounded, on-demand.
Runs the leak detector's synthetic-agent-load self-test (tracemalloc started AND
stopped inside the call) and returns its LeakReport as JSON. The `test_api_`
prefix triggers conftest's `_default_api_auth` (admin auth + CSRF bypass).

Tests use tiny agents/cycles so the synthetic load is fast.
"""

from main import app  # noqa: F401


class TestLeakSnapshot:
    _SMALL = {"agents": 5, "cycles": 3, "top_n": 3}

    def test_returns_200(self, client):
        r = client.post("/api/compliance/leaks/snapshot", json=self._SMALL)
        assert r.status_code == 200

    def test_report_is_well_formed_and_truth_backed(self, client):
        j = client.post("/api/compliance/leaks/snapshot", json=self._SMALL).json()
        for k in (
            "agents", "cycles", "wall_seconds", "rss_before_bytes",
            "rss_after_bytes", "drift_kb", "threshold_kb", "pass",
            "top_growers", "mode",
        ):
            assert k in j, f"missing field: {k}"
        # echoes the requested bounded params (truth-backed, not canned)
        assert j["agents"] == 5 and j["cycles"] == 3
        assert isinstance(j["pass"], bool)
        assert isinstance(j["top_growers"], list)
        assert j["wall_seconds"] >= 0.0
        assert "synthetic" in j["mode"]  # honest scope label

    def test_out_of_range_params_rejected(self, client):
        # agents above the clamp ceiling (200) -> 422 validation error
        r = client.post("/api/compliance/leaks/snapshot", json={"agents": 99999})
        assert r.status_code == 422
