"""API tests for figma_routes — Figma design import."""


def _create_project(client):
    resp = client.post(
        "/api/v1/projects/wizard",
        json={"category": "website", "description": "test figma"},
    )
    return resp.json()["project_id"]


class TestFigmaAPI:
    def test_fetch_frames(self, client):
        resp = client.post(
            "/api/v1/figma/frames", json={"url": "https://figma.com/file/abc123/Design"}
        )
        assert resp.status_code == 200
        frames = resp.json()["frames"]
        assert len(frames) >= 1
        assert "id" in frames[0]
        assert "name" in frames[0]

    def test_fetch_frames_invalid_url(self, client):
        resp = client.post(
            "/api/v1/figma/frames", json={"url": "https://not-figma.com/xyz"}
        )
        assert resp.status_code == 200
        assert resp.json()["frames"] == []

    def test_convert_frames(self, client):
        pid = _create_project(client)
        resp = client.post(
            "/api/v1/figma/convert",
            json={
                "project_id": pid,
                "url": "https://figma.com/file/abc123/Design",
                "frame_ids": ["frame-1", "frame-2"],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["files_count"] == 2
        assert len(body["files"]) == 2

    def test_convert_nonexistent_project(self, client):
        resp = client.post(
            "/api/v1/figma/convert",
            json={
                "project_id": "fake-id",
                "url": "https://figma.com/file/abc123/Design",
                "frame_ids": ["frame-1"],
            },
        )
        assert resp.status_code == 404

    def test_convert_adds_files_to_project(self, client):
        """Verify converted files are persisted in the project."""
        pid = _create_project(client)
        client.post(
            "/api/v1/figma/convert",
            json={
                "project_id": pid,
                "url": "https://figma.com/file/abc123/Design",
                "frame_ids": ["frame-1"],
            },
        )
        proj = client.get(f"/api/v1/projects/{pid}").json()
        file_keys = list(proj["files"].keys())
        assert any("FigmaComponent" in k for k in file_keys)
