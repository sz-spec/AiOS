"""Model registry service — tracks active model state."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ModelRegistryService:
    """Manages model activation state and routing metadata."""

    def __init__(self):
        self._active_models: dict = {}

    def activate_model(self, model_id: str, config: dict) -> dict:
        self._active_models[model_id] = {**config, "active": True}
        return self._active_models[model_id]

    def deactivate_model(self, model_id: str) -> bool:
        if model_id in self._active_models:
            self._active_models[model_id]["active"] = False
            return True
        return False

    def get_active_models(self) -> list:
        return [m for m in self._active_models.values() if m.get("active")]

    def get_model(self, model_id: str) -> Optional[dict]:
        return self._active_models.get(model_id)


_service: Optional[ModelRegistryService] = None


def get_model_registry_service() -> ModelRegistryService:
    global _service
    if _service is None:
        _service = ModelRegistryService()
    return _service
