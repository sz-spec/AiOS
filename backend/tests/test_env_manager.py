"""Tests for EnvManager — per-project environment variable storage."""

from services.env_manager import get_env_manager


class TestEnvManager:
    def _svc(self):
        return get_env_manager()

    def test_set_and_get(self):
        svc = self._svc()
        svc.set_vars("proj-1", {"KEY": "val"})
        assert svc.get_vars("proj-1") == {"KEY": "val"}

    def test_get_empty(self):
        assert self._svc().get_vars("nonexistent") == {}

    def test_get_single_var(self):
        svc = self._svc()
        svc.set_vars("proj-1", {"KEY": "val"})
        assert svc.get_var("proj-1", "KEY") == "val"

    def test_get_var_missing(self):
        svc = self._svc()
        svc.set_vars("proj-1", {"KEY": "val"})
        assert svc.get_var("proj-1", "MISSING") is None

    def test_delete_var(self):
        svc = self._svc()
        svc.set_vars("proj-1", {"KEY": "val"})
        assert svc.delete_var("proj-1", "KEY") is True
        assert svc.get_var("proj-1", "KEY") is None

    def test_delete_missing(self):
        svc = self._svc()
        svc.set_vars("proj-1", {"KEY": "val"})
        assert svc.delete_var("proj-1", "MISSING") is False

    def test_get_masked(self):
        svc = self._svc()
        svc.set_vars("proj-1", {"KEY": "abcdefgh"})
        masked = svc.get_masked("proj-1")
        assert masked["KEY"].startswith("abcd")
        assert "*" in masked["KEY"]

    def test_get_masked_short(self):
        svc = self._svc()
        svc.set_vars("proj-1", {"KEY": "abc"})
        masked = svc.get_masked("proj-1")
        assert masked["KEY"] == "****"

    def test_merge(self):
        svc = self._svc()
        svc.set_vars("proj-1", {"A": "1"})
        svc.set_vars("proj-1", {"B": "2"})
        v = svc.get_vars("proj-1")
        assert v["A"] == "1"
        assert v["B"] == "2"

    def test_overwrite_existing_var(self):
        svc = self._svc()
        svc.set_vars("proj-1", {"A": "1"})
        svc.set_vars("proj-1", {"A": "2"})
        assert svc.get_var("proj-1", "A") == "2"
