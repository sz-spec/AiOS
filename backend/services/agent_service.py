"""Agent lifecycle service — extracts business logic from agents_routes.py."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory agent registry (migrated from route-level dicts in agents_routes.py)
_active_agents: dict = {}
_agent_logs: dict = {}


class AgentService:
    """Manages agent lifecycle and execution state."""

    def __init__(self):
        self.agents = _active_agents
        self.logs = _agent_logs

    def register_agent(self, agent_id: str, config: dict) -> dict:
        """Register a new agent with config."""
        self.agents[agent_id] = {**config, "status": "registered"}
        return self.agents[agent_id]

    def get_agent(self, agent_id: str) -> Optional[dict]:
        return self.agents.get(agent_id)

    def list_agents(self) -> list:
        return list(self.agents.values())

    def add_log(self, agent_id: str, entry: dict):
        self.logs.setdefault(agent_id, []).append(entry)

    def get_logs(self, agent_id: str) -> list:
        return self.logs.get(agent_id, [])


_service: Optional[AgentService] = None


def get_agent_service() -> AgentService:
    global _service
    if _service is None:
        _service = AgentService()
    return _service
