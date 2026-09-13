"""
Environment Manager Service
============================
Manages encrypted environment variables per project.
"""

from typing import Dict, Optional


class EnvManager:
    """In-memory env var storage. Replace with encrypted DB storage for production."""

    def __init__(self):
        self._envs: Dict[str, Dict[str, str]] = {}

    def set_vars(self, project_id: str, env_vars: Dict[str, str]) -> None:
        if project_id not in self._envs:
            self._envs[project_id] = {}
        self._envs[project_id].update(env_vars)

    def get_vars(self, project_id: str) -> Dict[str, str]:
        return dict(self._envs.get(project_id, {}))

    def get_var(self, project_id: str, key: str) -> Optional[str]:
        return self._envs.get(project_id, {}).get(key)

    def delete_var(self, project_id: str, key: str) -> bool:
        if project_id in self._envs and key in self._envs[project_id]:
            del self._envs[project_id][key]
            return True
        return False

    def get_masked(self, project_id: str) -> Dict[str, str]:
        """Return env vars with values masked (first 4 chars shown)."""
        raw = self._envs.get(project_id, {})
        return {
            key: (
                f"{value[:4]}{'*' * max(0, len(value) - 4)}"
                if len(value) > 4
                else "****"
            )
            for key, value in raw.items()
        }


_manager: Optional[EnvManager] = None


def get_env_manager() -> EnvManager:
    global _manager
    if _manager is None:
        _manager = EnvManager()
    return _manager
