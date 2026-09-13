"""API tests for payment_routes — payment integration setup."""


def _create_project(client):
    resp = client.post(
        "/api/v1/projects/wizard",
        json={"category": "ecommerce", "description": "test payments"},
    )
    return resp.json()["project_id"]


def _sample_products():
    return [
        {
            "id": "prod-1",
            "name": "Pro Plan",
            "price": "29",
            "description": "Full access",
            "recurring": True,
            "interval": "month",
        }
    ]


class TestPaymentsAPI:
    def test_setup_payments(self, client):
        pid = _create_project(client)
        resp = client.post(
            "/api/v1/payments/setup",
            json={"project_id": pid, "products": _sample_products()},
        )
        assert resp.status_code == 200
        assert resp.json()["files_generated"] == 3

    def test_payment_files_in_project(self, client):
        pid = _create_project(client)
        client.post(
            "/api/v1/payments/setup",
            json={"project_id": pid, "products": _sample_products()},
        )
        proj = client.get(f"/api/v1/projects/{pid}").json()
        file_keys = " ".join(proj["files"].keys()).lower()
        assert "pricing" in file_keys or "stripe" in file_keys

    def test_payments_nonexistent_project(self, client):
        resp = client.post(
            "/api/v1/payments/setup",
            json={"project_id": "fake-id", "products": _sample_products()},
        )
        assert resp.status_code == 404
