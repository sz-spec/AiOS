"""
Locust Load Test Scenarios for VOS3 Backend
=============================================
Standalone Locust file -- NOT a pytest test module.

Run:
    # Start the backend first:
    #   cd backend && uvicorn main:app --port 8000

    # Basic run (50 users, 5 spawn/s, 60s):
    locust -f tests/perf/locustfile.py --headless \
        -u 50 -r 5 -t 60s --host http://localhost:8000

    # Web UI:
    locust -f tests/perf/locustfile.py --host http://localhost:8000
"""

from locust import HttpUser, task, between, events, tag

# ---------------------------------------------------------------------------
# 1. Health-only user (lightweight baseline)
# ---------------------------------------------------------------------------


class HealthUser(HttpUser):
    """Hammers /health to establish a latency baseline."""

    wait_time = between(0.1, 0.5)
    weight = 3  # 30% of spawned users

    @task(10)
    def health_check(self):
        self.client.get("/health")

    @task(1)
    def root_check(self):
        self.client.get("/")


# ---------------------------------------------------------------------------
# 2. CRUD user (authenticated reads)
# ---------------------------------------------------------------------------


class CRUDUser(HttpUser):
    """Simulates a logged-in user browsing agents, metrics, and settings."""

    wait_time = between(0.5, 2.0)
    weight = 3

    def _auth_headers(self):
        return {"Authorization": "Bearer test_token_crud"}

    @task(3)
    @tag("read")
    def list_agents(self):
        self.client.get("/api/agents", headers=self._auth_headers())

    @task(2)
    @tag("read")
    def list_metrics(self):
        self.client.get("/api/metrics/overview", headers=self._auth_headers())

    @task(1)
    @tag("read")
    def get_settings(self):
        self.client.get("/api/settings", headers=self._auth_headers())

    @task(1)
    @tag("read")
    def get_memory(self):
        self.client.get("/api/memory", headers=self._auth_headers())


# ---------------------------------------------------------------------------
# 3. Mixed read/write user
# ---------------------------------------------------------------------------


class MixedUser(HttpUser):
    """Simulates a user who reads often but also sends chats and creates data."""

    wait_time = between(0.5, 1.5)
    weight = 2

    def _auth_headers(self):
        return {"Authorization": "Bearer test_token_mixed"}

    @task(6)
    @tag("read")
    def read_ops(self):
        self.client.get("/health")

    @task(3)
    @tag("write")
    def write_ops(self):
        self.client.post(
            "/api/chat/send",
            json={"message": "Hello from locust", "model": "default"},
            headers=self._auth_headers(),
        )

    @task(1)
    @tag("read")
    def heavy_ops(self):
        self.client.get("/api/v-core/entities", headers=self._auth_headers())


# ---------------------------------------------------------------------------
# 4. Auth-stress user (targets rate-limited identity paths)
# ---------------------------------------------------------------------------


class AuthStressUser(HttpUser):
    """Stresses authentication and identity-sensitive paths."""

    wait_time = between(0.2, 1.0)
    weight = 1

    @task(5)
    @tag("auth")
    def auth_login_attempt(self):
        self.client.post(
            "/api/auth/login",
            json={"email": "stress@test.com", "password": "locust"},
            headers={"Content-Type": "application/json"},
        )

    @task(2)
    @tag("auth")
    def clerk_webhook(self):
        self.client.post(
            "/api/webhooks/clerk",
            json={"type": "user.created", "data": {"id": "usr_locust"}},
            headers={"Content-Type": "application/json"},
        )

    @task(1)
    @tag("auth")
    def token_exchange(self):
        self.client.post(
            "/api/apps/authorize",
            json={"app_id": "app_locust", "scope": "read"},
            headers={"Authorization": "Bearer test_token_auth_stress"},
        )


# ---------------------------------------------------------------------------
# 5. Spike user (aggressive bursts with minimal wait)
# ---------------------------------------------------------------------------


class SpikeUser(HttpUser):
    """Simulates a traffic spike with very short wait times."""

    wait_time = between(0.01, 0.1)
    weight = 1

    def _auth_headers(self):
        return {"Authorization": "Bearer test_token_spike"}

    @task(5)
    @tag("spike")
    def rapid_health(self):
        self.client.get("/health")

    @task(3)
    @tag("spike")
    def rapid_agents(self):
        self.client.get("/api/agents", headers=self._auth_headers())

    @task(2)
    @tag("spike")
    def rapid_chat(self):
        self.client.post(
            "/api/chat/send",
            json={"message": "spike test", "model": "default"},
            headers=self._auth_headers(),
        )


# ---------------------------------------------------------------------------
# 6. Kernel bridge user (VOS3 kernel-specific endpoints)
# ---------------------------------------------------------------------------


class KernelUser(HttpUser):
    """Simulates kernel bridge operations (process list, fs ops)."""

    wait_time = between(1.0, 3.0)
    weight = 1

    def _auth_headers(self):
        return {"Authorization": "Bearer test_token_kernel"}

    @task(3)
    @tag("kernel")
    def list_processes(self):
        self.client.get("/api/kernel/processes", headers=self._auth_headers())

    @task(2)
    @tag("kernel")
    def fs_list(self):
        self.client.get("/api/kernel/fs/list", headers=self._auth_headers())

    @task(1)
    @tag("kernel")
    def kernel_status(self):
        self.client.get("/api/kernel/status", headers=self._auth_headers())


# ---------------------------------------------------------------------------
# 7. V-Core business user
# ---------------------------------------------------------------------------


class VCoreUser(HttpUser):
    """Simulates V-Core business operations: entities, workflows, monitoring."""

    wait_time = between(0.5, 2.0)
    weight = 1

    def _auth_headers(self):
        return {"Authorization": "Bearer test_token_vcore"}

    @task(4)
    @tag("vcore")
    def list_entities(self):
        self.client.get("/api/v-core/entities", headers=self._auth_headers())

    @task(2)
    @tag("vcore")
    def list_workflows(self):
        self.client.get("/api/v-core/workflows", headers=self._auth_headers())

    @task(1)
    @tag("vcore")
    def monitoring_overview(self):
        self.client.get("/api/v-core/monitoring", headers=self._auth_headers())

    @task(1)
    @tag("vcore", "write")
    def create_entity(self):
        self.client.post(
            "/api/v-core/entities",
            json={
                "name": "LocustEntity",
                "type": "custom",
                "fields": [{"name": "title", "type": "text"}],
            },
            headers=self._auth_headers(),
        )


# ---------------------------------------------------------------------------
# Event hooks (optional reporting)
# ---------------------------------------------------------------------------


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("=== VOS3 Load Test Starting ===")
    print(f"  Host: {environment.host}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("=== VOS3 Load Test Complete ===")
