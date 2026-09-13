"""Tests for ProjectService — the central project data store."""

from services.project_service import get_project_service


class TestProjectService:
    def _svc(self):
        return get_project_service()

    def _create(self, svc=None, **kw):
        svc = svc or self._svc()
        defaults = dict(
            user_id="user-1", name="Test", description="desc", category="website"
        )
        defaults.update(kw)
        return svc.create(**defaults)

    # -- create --
    def test_create_project(self):
        svc = self._svc()
        p = self._create(svc)
        assert p.id
        assert p.status == "building"
        assert p.name == "Test"
        assert p.category == "website"

    def test_create_sets_timestamps(self):
        p = self._create()
        assert p.created_at
        assert p.updated_at
        assert "T" in p.created_at

    # -- get --
    def test_get_existing(self):
        svc = self._svc()
        p = self._create(svc)
        fetched = svc.get(p.id)
        assert fetched is not None
        assert fetched.id == p.id

    def test_get_nonexistent(self):
        assert self._svc().get("fake-id") is None

    # -- list --
    def test_list_by_user(self):
        svc = self._svc()
        self._create(svc, user_id="A")
        self._create(svc, user_id="A")
        self._create(svc, user_id="B")
        assert len(svc.list_by_user("A")) == 2
        assert len(svc.list_by_user("B")) == 1

    def test_list_all(self):
        svc = self._svc()
        for _ in range(3):
            self._create(svc)
        assert len(svc.list_all()) == 3

    # -- update --
    def test_update_fields(self):
        svc = self._svc()
        p = self._create(svc)
        old_updated = p.updated_at
        updated = svc.update(p.id, name="New Name")
        assert updated.name == "New Name"
        assert updated.updated_at >= old_updated

    def test_update_nonexistent(self):
        assert self._svc().update("fake-id", name="X") is None

    def test_update_ignores_invalid_fields(self):
        svc = self._svc()
        p = self._create(svc)
        updated = svc.update(p.id, totally_fake_field="value")
        assert updated is not None
        assert (
            not hasattr(updated, "totally_fake_field")
            or getattr(updated, "totally_fake_field", None) is None
        )

    def test_update_rejects_dunder_fields(self):
        """Verify that __class__ and similar dunder attrs don't corrupt the object."""
        svc = self._svc()
        p = self._create(svc)
        original_class = type(p)
        svc.update(p.id, __class__="hacked")
        fetched = svc.get(p.id)
        # Object should still be a Project, not corrupted
        assert type(fetched) == original_class

    # -- delete --
    def test_delete(self):
        svc = self._svc()
        p = self._create(svc)
        assert svc.delete(p.id) is True
        assert svc.get(p.id) is None

    def test_delete_nonexistent(self):
        assert self._svc().delete("fake-id") is False

    # -- update_files --
    def test_update_files(self):
        svc = self._svc()
        p = self._create(svc)
        result = svc.update_files(p.id, {"a.tsx": "code"})
        assert result.files["a.tsx"] == "code"

    def test_update_files_merges(self):
        svc = self._svc()
        p = self._create(svc)
        svc.update_files(p.id, {"a.tsx": "1"})
        svc.update_files(p.id, {"b.tsx": "2"})
        proj = svc.get(p.id)
        assert "a.tsx" in proj.files
        assert "b.tsx" in proj.files

    def test_update_files_nonexistent(self):
        assert self._svc().update_files("fake-id", {"a": "b"}) is None

    # -- to_dict --
    def test_to_dict(self):
        p = self._create()
        d = p.to_dict()
        for key in (
            "id",
            "user_id",
            "name",
            "description",
            "category",
            "status",
            "files",
            "settings",
            "created_at",
            "updated_at",
        ):
            assert key in d

    # -- singleton --
    def test_singleton(self):
        a = get_project_service()
        b = get_project_service()
        assert a is b
