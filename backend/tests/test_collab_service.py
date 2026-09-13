"""Tests for CollabService — collaboration and presence."""

from services.collab_service import get_collab_service


class TestCollabService:
    def _svc(self):
        return get_collab_service()

    def test_invite(self):
        svc = self._svc()
        c = svc.invite("proj-1", "a@b.com", "editor")
        assert c.id
        assert c.user_email == "a@b.com"
        assert c.role == "editor"
        assert c.accepted is False

    def test_list_empty(self):
        assert self._svc().list_collaborators("x") == []

    def test_list_after_invite(self):
        svc = self._svc()
        svc.invite("proj-1", "a@b.com")
        svc.invite("proj-1", "c@d.com")
        assert len(svc.list_collaborators("proj-1")) == 2

    def test_remove(self):
        svc = self._svc()
        c = svc.invite("proj-1", "a@b.com")
        assert svc.remove("proj-1", c.id) is True
        assert len(svc.list_collaborators("proj-1")) == 0

    def test_remove_nonexistent(self):
        assert self._svc().remove("x", "y") is False

    def test_presence_set_and_get(self):
        svc = self._svc()
        svc.set_presence("proj-1", "user-1", True)
        presence = svc.get_presence("proj-1")
        assert presence.get("user-1") is True

    def test_presence_default_empty(self):
        assert self._svc().get_presence("nonexistent") == {}

    def test_presence_toggle(self):
        svc = self._svc()
        svc.set_presence("proj-1", "user-1", True)
        svc.set_presence("proj-1", "user-1", False)
        assert svc.get_presence("proj-1").get("user-1") is False
