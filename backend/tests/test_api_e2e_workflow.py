"""End-to-end workflow test — full project lifecycle."""

import asyncio
from unittest.mock import patch

from services.project_service import get_project_service


def _instant_sleep(*args, **kwargs):
    """Replace asyncio.sleep with an instant future."""
    f = asyncio.Future()
    f.set_result(None)
    return f


class TestE2EWorkflow:
    def test_full_lifecycle(self, client):
        """
        wizard → build SSE → edit → checkpoint → restore → deploy
        Validates the full project lifecycle end-to-end.
        """
        # 1. Create project via wizard
        resp = client.post(
            "/api/v1/projects/wizard",
            json={"category": "website", "description": "E2E test project"},
        )
        assert resp.status_code == 201
        pid = resp.json()["project_id"]

        # 2. Stream build (monkeypatch asyncio.sleep for speed)
        with patch("asyncio.sleep", side_effect=_instant_sleep):
            resp = client.get(f"/api/v1/build/{pid}/stream")
        assert resp.status_code == 200
        assert "build_complete" in resp.text

        # Verify files were generated
        proj = client.get(f"/api/v1/projects/{pid}").json()
        assert len(proj["files"]) > 0
        assert proj["status"] == "ready"

        # 3. Apply an edit
        resp = client.post(
            "/api/v1/edit", json={"project_id": pid, "instruction": "Change the title"}
        )
        assert resp.status_code == 200
        assert "message" in resp.json()

        # 4. Create a checkpoint
        resp = client.post(
            f"/api/v1/projects/{pid}/checkpoints", json={"description": "after edit"}
        )
        assert resp.status_code == 201
        cp_id = resp.json()["id"]

        # 5. Modify files, then restore checkpoint
        svc = get_project_service()
        svc.update_files(pid, {"new_file.txt": "temporary"})
        resp = client.post(f"/api/v1/projects/{pid}/checkpoints/{cp_id}/restore")
        assert resp.status_code == 200
        assert resp.json()["status"] == "restored"

        # Verify restore removed the temporary file
        proj = client.get(f"/api/v1/projects/{pid}").json()
        assert "new_file.txt" not in proj["files"]

        # 6. Deploy
        resp = client.post(
            "/api/v1/deploy", json={"project_id": pid, "subdomain": "e2e-test"}
        )
        assert resp.status_code == 200
        assert "e2e-test.vcreator.app" in resp.json()["url"]
        assert resp.json()["status"] == "live"

        # Verify project status is deployed
        proj = client.get(f"/api/v1/projects/{pid}").json()
        assert proj["status"] == "deployed"
