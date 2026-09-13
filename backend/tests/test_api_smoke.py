"""Smoke test: verify all V Creator routers are mounted."""


class TestRoutesMounted:
    def test_v_creator_routes_mounted(self, client):
        """All 12 V Creator route prefixes should be reachable (not 404 for valid methods)."""
        # These should return something other than 404-with-"Not Found" for the base prefix.
        # Some may return 405 (method not allowed) or 422 (validation) — that's fine,
        # it proves the router is mounted.
        prefixes = [
            ("POST", "/api/v1/projects/wizard"),
            ("GET", "/api/v1/projects"),
            ("GET", "/api/v1/build/test/stream"),
            ("POST", "/api/v1/edit"),
            ("GET", "/api/v1/projects/test/checkpoints"),
            ("POST", "/api/v1/database/generate"),
            ("POST", "/api/v1/auth/setup"),
            ("POST", "/api/v1/payments/setup"),
            ("POST", "/api/v1/deploy"),
            ("POST", "/api/v1/figma/frames"),
            ("POST", "/api/v1/collab/invite"),
            ("GET", "/api/v1/marketplace/components"),
        ]
        for method, path in prefixes:
            if method == "GET":
                resp = client.get(path)
            else:
                resp = client.post(path, json={})
            # 404 with "Not Found" means route not mounted.
            # 422, 400, 200 all mean the route IS mounted.
            assert resp.status_code != 404 or "not found" not in resp.text.lower().replace(
                "project", ""
            ).replace(
                "component", ""
            ), f"Route {method} {path} appears unmounted (got {resp.status_code}: {resp.text[:100]})"
