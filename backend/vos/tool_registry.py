"""
VOS Tool Registry
=================
Declarative mapping of internal API endpoints as function-calling tools.
Handlers call services directly (not HTTP self-calls) for performance.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class VosTool:
    """A single VOS tool definition."""

    name: str
    category: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema
    handler: Callable  # async callable
    requires_confirmation: bool = False


class ToolRegistry:
    """Registry of VOS tools grouped by category."""

    def __init__(self):
        self._tools: Dict[str, VosTool] = {}
        self._by_category: Dict[str, List[str]] = {}

    def register(self, tool: VosTool):
        """Register a tool."""
        self._tools[tool.name] = tool
        if tool.category not in self._by_category:
            self._by_category[tool.category] = []
        if tool.name not in self._by_category[tool.category]:
            self._by_category[tool.category].append(tool.name)

    def get(self, name: str) -> Optional[VosTool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_tools_for_category(self, category: str) -> List[VosTool]:
        """Get all tools in a category."""
        names = self._by_category.get(category, [])
        return [self._tools[n] for n in names if n in self._tools]

    def get_all_tools(self) -> List[VosTool]:
        """Get all registered tools."""
        return list(self._tools.values())

    def get_categories(self) -> List[str]:
        """Get all categories."""
        return list(self._by_category.keys())

    def to_openai_functions(
        self, category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Convert tools to OpenAI function-calling format."""
        tools = (
            self.get_tools_for_category(category) if category else self.get_all_tools()
        )
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]


def build_default_registry() -> ToolRegistry:
    """Build the default tool registry with all VOS tools."""
    registry = ToolRegistry()

    # --- Agent Management ---
    registry.register(
        VosTool(
            name="create_agent",
            category="agent_management",
            description="Create a new AI agent with a name, role, and optional model/system prompt.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the agent"},
                    "role": {
                        "type": "string",
                        "description": "Role: architect, frontend, backend, tester, reviewer, researcher, assistant, or custom",
                    },
                    "model": {
                        "type": "string",
                        "description": "Model to use (default: gpt-4o-mini)",
                    },
                    "system_prompt": {
                        "type": "string",
                        "description": "Custom system prompt",
                    },
                    "description": {
                        "type": "string",
                        "description": "Agent description",
                    },
                },
                "required": ["name", "role"],
            },
            handler=_handle_create_agent,
        )
    )

    registry.register(
        VosTool(
            name="list_agents",
            category="agent_management",
            description="List all currently configured agents.",
            parameters={"type": "object", "properties": {}},
            handler=_handle_list_agents,
        )
    )

    registry.register(
        VosTool(
            name="delete_agent",
            category="agent_management",
            description="Delete an agent by ID.",
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "ID of the agent to delete",
                    },
                },
                "required": ["agent_id"],
            },
            handler=_handle_delete_agent,
            requires_confirmation=True,
        )
    )

    registry.register(
        VosTool(
            name="update_agent",
            category="agent_management",
            description="Update an existing agent's configuration.",
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "ID of the agent to update",
                    },
                    "name": {"type": "string", "description": "New name"},
                    "role": {"type": "string", "description": "New role"},
                    "model": {"type": "string", "description": "New model"},
                    "system_prompt": {
                        "type": "string",
                        "description": "New system prompt",
                    },
                },
                "required": ["agent_id"],
            },
            handler=_handle_update_agent,
        )
    )

    registry.register(
        VosTool(
            name="execute_agent_task",
            category="agent_management",
            description="Execute a task using a specific agent.",
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "ID of the agent to execute the task",
                    },
                    "task": {
                        "type": "string",
                        "description": "Task description for the agent",
                    },
                },
                "required": ["agent_id", "task"],
            },
            handler=_handle_execute_agent_task,
        )
    )

    # --- Memory Management ---
    registry.register(
        VosTool(
            name="store_memory",
            category="memory_management",
            description="Store a piece of information in development memory for later recall.",
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to remember"},
                    "memory_type": {
                        "type": "string",
                        "description": "Type: conversation, decision, code_change, learning, error, solution",
                    },
                },
                "required": ["content"],
            },
            handler=_handle_store_memory,
        )
    )

    registry.register(
        VosTool(
            name="recall_memory",
            category="memory_management",
            description="Search development memory for relevant information.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results (default 5)",
                    },
                },
                "required": ["query"],
            },
            handler=_handle_recall_memory,
        )
    )

    registry.register(
        VosTool(
            name="get_recent_memories",
            category="memory_management",
            description="Get the most recent memories stored in the system.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of memories to return (default 10)",
                    },
                },
            },
            handler=_handle_get_recent_memories,
        )
    )

    # --- V-Core Management ---
    registry.register(
        VosTool(
            name="list_entities",
            category="vcore_management",
            description="List all business entities defined in V-Core.",
            parameters={"type": "object", "properties": {}},
            handler=_handle_list_entities,
        )
    )

    registry.register(
        VosTool(
            name="create_record",
            category="vcore_management",
            description="Create a new record in a V-Core entity.",
            parameters={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "Entity ID to create the record in",
                    },
                    "data": {
                        "type": "object",
                        "description": "Record data as key-value pairs",
                    },
                },
                "required": ["entity_id", "data"],
            },
            handler=_handle_create_record,
        )
    )

    registry.register(
        VosTool(
            name="list_workflows",
            category="vcore_management",
            description="List all workflows in the workflow engine.",
            parameters={"type": "object", "properties": {}},
            handler=_handle_list_workflows,
        )
    )

    registry.register(
        VosTool(
            name="trigger_workflow",
            category="vcore_management",
            description="Trigger a workflow by ID with optional input data.",
            parameters={
                "type": "object",
                "properties": {
                    "workflow_id": {
                        "type": "string",
                        "description": "Workflow ID to trigger",
                    },
                    "input_data": {
                        "type": "object",
                        "description": "Input data for the workflow",
                    },
                },
                "required": ["workflow_id"],
            },
            handler=_handle_trigger_workflow,
        )
    )

    # --- System Query ---
    registry.register(
        VosTool(
            name="get_system_health",
            category="system_query",
            description="Get overall system health status including all services.",
            parameters={"type": "object", "properties": {}},
            handler=_handle_get_system_health,
        )
    )

    registry.register(
        VosTool(
            name="get_metrics",
            category="system_query",
            description="Get system metrics including request counts, costs, and response times.",
            parameters={"type": "object", "properties": {}},
            handler=_handle_get_metrics,
        )
    )

    registry.register(
        VosTool(
            name="get_cost_breakdown",
            category="system_query",
            description="Get detailed cost breakdown by model and role.",
            parameters={"type": "object", "properties": {}},
            handler=_handle_get_cost_breakdown,
        )
    )

    # --- Settings Management ---
    registry.register(
        VosTool(
            name="get_settings_status",
            category="settings_management",
            description="Get current settings and API key configuration status.",
            parameters={"type": "object", "properties": {}},
            handler=_handle_get_settings_status,
        )
    )

    # --- Code Generation ---
    registry.register(
        VosTool(
            name="generate_code",
            category="codegen",
            description="Generate code from a natural language description.",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Description of code to generate",
                    },
                    "language": {
                        "type": "string",
                        "description": "Target language (python, javascript, typescript, etc.)",
                    },
                    "framework": {
                        "type": "string",
                        "description": "Target framework if applicable",
                    },
                },
                "required": ["prompt"],
            },
            handler=_handle_generate_code,
        )
    )

    return registry


# ====== Tool Handlers ======
# Each handler calls services directly, matching the existing agent execution pattern.


async def _handle_create_agent(params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new agent via the agents module internals."""
    from api.agents_routes import _agents, _logs, Agent, AgentStatus, DEFAULT_PROMPTS
    from datetime import datetime, timezone
    import uuid

    agent_id = str(uuid.uuid4())[:8]
    role = params.get("role", "assistant")

    # W2.5 — never hardcode a cloud model id. Resolve dynamically through the
    # Smart Router so VOS3_LOCALITY_PREFERENCE=local-first picks a local or
    # kernel-bound model when callers don't specify one. Strict air-gap mode
    # propagates LocalInferenceRequiredError.
    from src.efficiency.router import resolve_model_id

    _default_model = params.get("model") or resolve_model_id("tool_routing", 2)

    agent = Agent(
        id=agent_id,
        name=params["name"],
        role=role,
        model=_default_model,
        system_prompt=params.get("system_prompt")
        or DEFAULT_PROMPTS.get(role.lower(), ""),
        temperature=0.7,
        status=AgentStatus.IDLE,
        created_at=datetime.now(timezone.utc),
        description=params.get("description"),
    )

    _agents[agent_id] = agent
    _logs[agent_id] = []

    return {
        "agent_id": agent_id,
        "name": agent.name,
        "role": agent.role,
        "model": agent.model,
    }


async def _handle_list_agents(params: Dict[str, Any]) -> Dict[str, Any]:
    """List all agents."""
    from api.agents_routes import _agents

    agents = [
        {
            "id": a.id,
            "name": a.name,
            "role": a.role,
            "model": a.model,
            "status": a.status.value,
        }
        for a in _agents.values()
    ]
    return {"agents": agents, "total": len(agents)}


async def _handle_delete_agent(params: Dict[str, Any]) -> Dict[str, Any]:
    """Delete an agent."""
    from api.agents_routes import _agents, _logs

    agent_id = params["agent_id"]
    if agent_id not in _agents:
        return {"error": f"Agent {agent_id} not found"}
    name = _agents[agent_id].name
    del _agents[agent_id]
    _logs.pop(agent_id, None)
    return {"deleted": True, "agent_id": agent_id, "name": name}


async def _handle_update_agent(params: Dict[str, Any]) -> Dict[str, Any]:
    """Update an agent."""
    from api.agents_routes import _agents

    agent_id = params["agent_id"]
    if agent_id not in _agents:
        return {"error": f"Agent {agent_id} not found"}
    agent = _agents[agent_id]
    if "name" in params:
        agent.name = params["name"]
    if "role" in params:
        agent.role = params["role"]
    if "model" in params:
        agent.model = params["model"]
    if "system_prompt" in params:
        agent.system_prompt = params["system_prompt"]
    return {
        "agent_id": agent_id,
        "name": agent.name,
        "role": agent.role,
        "updated": True,
    }


async def _handle_execute_agent_task(params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a task on an agent."""
    from api.agents_routes import _agents

    agent_id = params["agent_id"]
    if agent_id not in _agents:
        return {"error": f"Agent {agent_id} not found"}
    agent = _agents[agent_id]
    # Defer to dev-mode style — real execution requires the full orchestrator
    return {
        "agent_id": agent_id,
        "agent_name": agent.name,
        "task": params["task"],
        "result": f"Task delegated to agent '{agent.name}' ({agent.role}): {params['task']}",
    }


async def _handle_store_memory(params: Dict[str, Any]) -> Dict[str, Any]:
    """Store a memory."""
    try:
        from memory.dev_memory import get_dev_memory

        memory = get_dev_memory()
        entry = memory.add(
            content=params["content"],
            memory_type=params.get("memory_type", "context"),
            metadata={"type": "vos_action", "source": "vos"},
        )
        return {"stored": True, "id": entry.id if entry else None}
    except Exception as e:
        return {"stored": False, "error": str(e)}


async def _handle_recall_memory(params: Dict[str, Any]) -> Dict[str, Any]:
    """Recall memories."""
    try:
        from memory.dev_memory import get_dev_memory

        memory = get_dev_memory()
        results = memory.query(params["query"], top_k=params.get("top_k", 5))
        return {"results": results, "count": len(results)}
    except Exception as e:
        return {"results": [], "error": str(e)}


async def _handle_get_recent_memories(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get recent memories."""
    try:
        from memory.dev_memory import get_dev_memory

        memory = get_dev_memory()
        recent = memory.get_recent(limit=params.get("limit", 10))
        return {"memories": recent, "count": len(recent)}
    except Exception as e:
        return {"memories": [], "error": str(e)}


async def _handle_list_entities(params: Dict[str, Any]) -> Dict[str, Any]:
    """List V-Core entities."""
    try:
        from core import get_business_core_service

        svc = get_business_core_service()
        entities = svc.list_entity_types() if svc else []
        return {"entities": entities, "total": len(entities)}
    except Exception as e:
        return {"entities": [], "error": str(e)}


async def _handle_create_record(params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a V-Core record."""
    try:
        from core import get_business_core_service

        svc = get_business_core_service()
        if not svc:
            return {"error": "Business core service not available"}
        record = svc.create_record(params["entity_id"], params["data"])
        return {"record": record, "created": True}
    except Exception as e:
        return {"error": str(e)}


async def _handle_list_workflows(params: Dict[str, Any]) -> Dict[str, Any]:
    """List workflows."""
    try:
        from core import get_workflow_engine_service

        svc = get_workflow_engine_service()
        workflows = svc.list_workflows() if svc else []
        return {"workflows": workflows, "total": len(workflows)}
    except Exception as e:
        return {"workflows": [], "error": str(e)}


async def _handle_trigger_workflow(params: Dict[str, Any]) -> Dict[str, Any]:
    """Trigger a workflow."""
    try:
        from core import get_workflow_engine_service

        svc = get_workflow_engine_service()
        if not svc:
            return {"error": "Workflow engine not available"}
        result = svc.trigger(params["workflow_id"], params.get("input_data", {}))
        return {
            "triggered": True,
            "workflow_id": params["workflow_id"],
            "result": result,
        }
    except Exception as e:
        return {"error": str(e)}


async def _handle_get_system_health(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get system health."""
    try:
        from src.observability import get_health

        return get_health()
    except Exception as e:
        return {"status": "unknown", "error": str(e)}


async def _handle_get_metrics(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get system metrics."""
    try:
        from src.observability import get_metrics

        return get_metrics()
    except Exception as e:
        return {"error": str(e)}


async def _handle_get_cost_breakdown(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get cost breakdown."""
    try:
        from src.observability import get_cost_breakdown

        return get_cost_breakdown()
    except Exception as e:
        return {"error": str(e)}


async def _handle_get_settings_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get settings status."""
    import os

    keys = {
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "google": bool(os.getenv("GOOGLE_API_KEY")),
        "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY")),
    }
    configured = sum(1 for v in keys.values() if v)
    return {
        "api_keys": keys,
        "configured_count": configured,
        "dev_mode": configured == 0,
    }


async def _handle_generate_code(params: Dict[str, Any]) -> Dict[str, Any]:
    """Generate code."""
    try:
        from ai.codegen.generator import CodeGenerator

        gen = CodeGenerator()
        result = gen.generate(
            prompt=params["prompt"],
            language=params.get("language", "python"),
            framework=params.get("framework"),
        )
        return {
            "code": result.code if hasattr(result, "code") else str(result),
            "generated": True,
        }
    except Exception:
        return {
            "generated": True,
            "code": f"# [Dev Mode] Would generate: {params['prompt']}\n# Language: {params.get('language', 'python')}",
            "dev_mode": True,
        }
