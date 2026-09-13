"""Tests for CheckpointService — version snapshot management."""

from services.checkpoint_service import get_checkpoint_service


class TestCheckpointService:
    def _svc(self):
        return get_checkpoint_service()

    def test_create_checkpoint(self):
        svc = self._svc()
        cp = svc.create("proj-1", {"app.tsx": "code"}, "initial")
        assert cp.id
        assert cp.project_id == "proj-1"
        assert cp.description == "initial"
        assert cp.files_snapshot == {"app.tsx": "code"}

    def test_snapshot_is_deep_copy(self):
        svc = self._svc()
        files = {"app.tsx": "original"}
        cp = svc.create("proj-1", files, "v1")
        files["app.tsx"] = "mutated"
        assert cp.files_snapshot["app.tsx"] == "original"

    def test_list_empty(self):
        assert self._svc().list_by_project("nonexistent") == []

    def test_list_multiple(self):
        svc = self._svc()
        svc.create("proj-1", {"a": "1"}, "v1")
        svc.create("proj-1", {"a": "2"}, "v2")
        svc.create("proj-1", {"a": "3"}, "v3")
        assert len(svc.list_by_project("proj-1")) == 3

    def test_get_by_id(self):
        svc = self._svc()
        cp = svc.create("proj-1", {"a": "1"}, "v1")
        fetched = svc.get("proj-1", cp.id)
        assert fetched is not None
        assert fetched.id == cp.id

    def test_get_wrong_project(self):
        svc = self._svc()
        cp = svc.create("proj-A", {"a": "1"}, "v1")
        assert svc.get("proj-B", cp.id) is None

    def test_restore(self):
        svc = self._svc()
        cp = svc.create("proj-1", {"app.tsx": "snapshot"}, "v1")
        restored = svc.restore("proj-1", cp.id)
        assert restored is not None
        assert restored == {"app.tsx": "snapshot"}

    def test_restore_nonexistent(self):
        assert self._svc().restore("x", "y") is None
