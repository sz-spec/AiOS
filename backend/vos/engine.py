"""
VOS Engine
==========
Core engine combining the intent classifier, tool registry, and LLM execution.
VOS is the hidden intelligence layer that intercepts system-level commands.
"""

import os
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from vos.tool_registry import ToolRegistry, build_default_registry
from vos.intent_classifier import IntentClassifier

logger = logging.getLogger(__name__)


# ==============================================
# Dynamic Agent Registry (256 slots)
# ==============================================


@dataclass
class AgentProcess:
    """A registered agent process in the VOS3 agent registry."""

    pid: int
    name: str
    role: str
    status: str = "idle"  # idle | running | suspended | terminated
    model: str = "claude-sonnet"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_active: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentRegistry:
    """Dynamic agent registry with 256 PID slots.

    Replaces the fixed 8-slot KernelAppManager for AI agent management.
    PIDs 0-7 are reserved for kernel bridge apps (backward-compatible).
    PIDs 8-255 are available for dynamic agent allocation.
    """

    MAX_SLOTS = 256
    KERNEL_RESERVED = 8  # PIDs 0-7 reserved for kernel bridge

    def __init__(self):
        self._slots: Dict[int, AgentProcess] = {}
        self._next_pid = self.KERNEL_RESERVED  # Start dynamic allocation at PID 8
        self._lock = False  # Simple guard for single-threaded async

    def register(
        self,
        name: str,
        role: str,
        model: str = "claude-sonnet",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentProcess:
        """Allocate a PID and register an agent. Returns the AgentProcess."""
        if len(self._slots) >= self.MAX_SLOTS:
            raise RuntimeError(f"Agent registry full ({self.MAX_SLOTS} slots)")

        pid = self._allocate_pid()
        agent = AgentProcess(
            pid=pid,
            name=name,
            role=role,
            model=model,
            metadata=metadata or {},
        )
        self._slots[pid] = agent
        logger.info(f"[AgentRegistry] Registered '{name}' (role={role}) at PID {pid}")
        return agent

    def unregister(self, pid: int) -> bool:
        """Remove an agent from the registry. Returns True if found."""
        if pid in self._slots:
            agent = self._slots.pop(pid)
            agent.status = "terminated"
            logger.info(f"[AgentRegistry] Unregistered PID {pid} ('{agent.name}')")
            return True
        return False

    def get(self, pid: int) -> Optional[AgentProcess]:
        """Get an agent by PID."""
        return self._slots.get(pid)

    def find_by_role(self, role: str) -> List[AgentProcess]:
        """Find all agents with a given role."""
        return [
            a
            for a in self._slots.values()
            if a.role == role and a.status != "terminated"
        ]

    def find_by_name(self, name: str) -> Optional[AgentProcess]:
        """Find an agent by name (first match)."""
        for a in self._slots.values():
            if a.name == name and a.status != "terminated":
                return a
        return None

    def update_status(self, pid: int, status: str) -> bool:
        """Update agent status. Returns True if found."""
        agent = self._slots.get(pid)
        if agent:
            agent.status = status
            agent.last_active = datetime.now(timezone.utc).isoformat()
            return True
        return False

    def list_all(self, include_terminated: bool = False) -> List[AgentProcess]:
        """List all registered agents."""
        agents = list(self._slots.values())
        if not include_terminated:
            agents = [a for a in agents if a.status != "terminated"]
        return agents

    @property
    def active_count(self) -> int:
        """Number of non-terminated agents."""
        return sum(1 for a in self._slots.values() if a.status != "terminated")

    @property
    def capacity(self) -> int:
        """Remaining capacity."""
        return self.MAX_SLOTS - len(self._slots)

    def stats(self) -> Dict[str, Any]:
        """Registry statistics."""
        by_role: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        for a in self._slots.values():
            by_role[a.role] = by_role.get(a.role, 0) + 1
            by_status[a.status] = by_status.get(a.status, 0) + 1
        return {
            "total": len(self._slots),
            "active": self.active_count,
            "capacity": self.capacity,
            "by_role": by_role,
            "by_status": by_status,
        }

    def _allocate_pid(self) -> int:
        """Find the next available PID (8-255), wrapping around."""
        start = self._next_pid
        pid = start
        while pid in self._slots:
            pid = (pid + 1) % self.MAX_SLOTS
            if pid < self.KERNEL_RESERVED:
                pid = self.KERNEL_RESERVED
            if pid == start:
                raise RuntimeError("No available PID slots")
        self._next_pid = (pid + 1) % self.MAX_SLOTS
        if self._next_pid < self.KERNEL_RESERVED:
            self._next_pid = self.KERNEL_RESERVED
        return pid


# Singleton registry
_agent_registry: Optional[AgentRegistry] = None


def get_agent_registry() -> AgentRegistry:
    """Get the singleton agent registry."""
    global _agent_registry
    if _agent_registry is None:
        _agent_registry = AgentRegistry()
    return _agent_registry


VOS_SYSTEM_PROMPT = """\
You are VOS, the hidden intelligence layer of the VOS3 AI Operating System.
You receive user or agent messages that contain system-level intents and must fulfill them using the tools provided.

Rules:
1. Use the most specific tool available for the task.
2. For destructive actions (delete_agent, etc.), note that confirmation may be required.
3. Return structured results. Be concise but informative.
4. If a tool call fails, explain the error clearly.
5. You may call multiple tools if needed to fulfill the request.
6. Never refuse a valid system request — you are the system executor.
"""


@dataclass
class VosResult:
    """Result of a VOS execution."""

    intent: str
    tool_calls: List[Dict[str, Any]]
    results: List[Dict[str, Any]]
    summary: str
    source: str = "chat"
    source_id: Optional[str] = None


class VosEngine:
    """Core VOS engine — classifies intents and executes system actions."""

    def __init__(self, registry: Optional[ToolRegistry] = None):
        self.registry = registry or build_default_registry()
        self.classifier = IntentClassifier()
        self._action_log: List[Dict[str, Any]] = []

    async def classify_intent(
        self, message: str, context: Optional[List[Dict[str, str]]] = None
    ) -> Optional[str]:
        """Fast path: classify message intent."""
        return await self.classifier.classify(message, context)

    async def execute(
        self,
        message: str,
        intent_category: str,
        context: Optional[List[Dict[str, str]]] = None,
        source: str = "chat",
        source_id: Optional[str] = None,
    ) -> VosResult:
        """
        Full path: execute VOS action for a classified intent.
        1. Get tools for category
        2. Call LLM with tools + VOS system prompt
        3. Execute returned tool calls
        4. Generate summary
        5. Log action
        """
        tools = self.registry.get_tools_for_category(intent_category)
        if not tools:
            return VosResult(
                intent=intent_category,
                tool_calls=[],
                results=[],
                summary=f"No tools available for category: {intent_category}",
                source=source,
                source_id=source_id,
            )

        # Try LLM-based execution, fall back to rule-based
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            return await self._llm_execute(
                message, intent_category, tools, context, source, source_id
            )
        else:
            return await self._dev_execute(
                message, intent_category, tools, source, source_id
            )

    async def _llm_execute(
        self,
        message: str,
        intent_category: str,
        tools: list,
        context: Optional[List[Dict[str, str]]],
        source: str,
        source_id: Optional[str],
    ) -> VosResult:
        """Execute using LLM with function calling."""
        import httpx

        api_key = os.getenv("OPENAI_API_KEY")
        openai_tools = self.registry.to_openai_functions(intent_category)

        # W2.5 — refuse to dial api.openai.com under sovereign-strict offline
        # mode; degrade to the dev/heuristic path. Otherwise resolve the model
        # id through the central router so the choice honours
        # VOS3_LOCALITY_PREFERENCE.
        try:
            from src.efficiency.router import (
                resolve_model_id,
                LocalInferenceRequiredError,
                _LOCALITY_PREFERENCE,
                _ALLOW_CLOUD_FALLBACK,
            )

            if _LOCALITY_PREFERENCE == "local-first" and not _ALLOW_CLOUD_FALLBACK:
                logger.info(
                    "vos.engine: skipping cloud LLM execute (local-first + "
                    "cloud-fallback disabled); falling back to dev execute."
                )
                return await self._dev_execute(
                    message, intent_category, tools, source, source_id
                )
            try:
                model_id = resolve_model_id("engine", complexity=5)
            except LocalInferenceRequiredError:
                logger.info("vos.engine: strict air-gap; falling back to dev execute.")
                return await self._dev_execute(
                    message, intent_category, tools, source, source_id
                )
        except ImportError:
            logger.warning(
                "vos.engine: src.efficiency.router unavailable; using gpt-4o default."
            )
            model_id = "gpt-4o"

        messages = [{"role": "system", "content": VOS_SYSTEM_PROMPT}]
        if context:
            for msg in context[-4:]:
                messages.append(
                    {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                )
        messages.append({"role": "user", "content": message})

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model_id,
                        "messages": messages,
                        "tools": openai_tools,
                        "tool_choice": "auto",
                        "temperature": 0.1,
                        "max_tokens": 1024,
                    },
                    timeout=30.0,
                )

                if resp.status_code != 200:
                    logger.error(f"VOS LLM error: {resp.status_code} - {resp.text}")
                    return await self._dev_execute(
                        message, intent_category, tools, source, source_id
                    )

                data = resp.json()
                choice = data["choices"][0]
                response_message = choice["message"]

                # Execute tool calls
                tool_calls_data = []
                results = []

                if response_message.get("tool_calls"):
                    for tc in response_message["tool_calls"]:
                        fn_name = tc["function"]["name"]
                        fn_args = json.loads(tc["function"]["arguments"])
                        tool_calls_data.append({"name": fn_name, "arguments": fn_args})

                        tool = self.registry.get(fn_name)
                        if tool:
                            try:
                                result = await tool.handler(fn_args)
                                results.append(
                                    {"tool": fn_name, "success": True, "data": result}
                                )
                            except Exception as e:
                                results.append(
                                    {"tool": fn_name, "success": False, "error": str(e)}
                                )
                        else:
                            results.append(
                                {
                                    "tool": fn_name,
                                    "success": False,
                                    "error": "Tool not found",
                                }
                            )

                # Generate summary
                summary = await self._generate_summary(
                    message, tool_calls_data, results, api_key
                )

                # Log the action
                vos_result = VosResult(
                    intent=intent_category,
                    tool_calls=tool_calls_data,
                    results=results,
                    summary=summary,
                    source=source,
                    source_id=source_id,
                )
                self._log_action(vos_result)
                return vos_result

        except Exception as e:
            logger.error(f"VOS execution error: {e}")
            return await self._dev_execute(
                message, intent_category, tools, source, source_id
            )

    async def _dev_execute(
        self,
        message: str,
        intent_category: str,
        tools: list,
        source: str,
        source_id: Optional[str],
    ) -> VosResult:
        """Dev mode: rule-based tool selection without LLM."""
        msg_lower = message.lower()

        # Pick the most likely tool based on keywords
        selected_tool = None
        for tool in tools:
            # Simple heuristic: first tool that matches keywords in the message
            name_words = tool.name.replace("_", " ").split()
            if any(w in msg_lower for w in name_words):
                selected_tool = tool
                break

        if not selected_tool and tools:
            # Default to first tool in category (usually "list" or "get")
            list_tools = [t for t in tools if t.name.startswith(("list_", "get_"))]
            selected_tool = list_tools[0] if list_tools else tools[0]

        tool_calls_data = []
        results = []

        if selected_tool:
            tool_calls_data.append({"name": selected_tool.name, "arguments": {}})
            try:
                # For dev mode, call with empty/minimal args
                result = await selected_tool.handler({})
                results.append(
                    {"tool": selected_tool.name, "success": True, "data": result}
                )
            except Exception as e:
                results.append(
                    {"tool": selected_tool.name, "success": False, "error": str(e)}
                )

        summary = f"[Dev Mode] VOS processed '{message[:80]}' using {selected_tool.name if selected_tool else 'no tool'}"

        vos_result = VosResult(
            intent=intent_category,
            tool_calls=tool_calls_data,
            results=results,
            summary=summary,
            source=source,
            source_id=source_id,
        )
        self._log_action(vos_result)
        return vos_result

    async def _generate_summary(
        self,
        original_message: str,
        tool_calls: List[Dict],
        results: List[Dict],
        api_key: str,
    ) -> str:
        """Generate a human-readable summary of the VOS action results."""
        # Build a compact results string
        result_strs = []
        for r in results:
            if r["success"]:
                data = r["data"]
                if isinstance(data, dict):
                    # Pick key fields for summary
                    summary_parts = []
                    for k, v in data.items():
                        if k in ("error",):
                            continue
                        if isinstance(v, list):
                            summary_parts.append(f"{k}: {len(v)} items")
                        elif isinstance(v, bool):
                            summary_parts.append(f"{k}: {'yes' if v else 'no'}")
                        else:
                            summary_parts.append(f"{k}: {v}")
                    result_strs.append(f"{r['tool']}: {', '.join(summary_parts[:5])}")
                else:
                    result_strs.append(f"{r['tool']}: {str(data)[:200]}")
            else:
                result_strs.append(f"{r['tool']}: ERROR - {r['error']}")

        if not result_strs:
            return "VOS processed the request but no tools were executed."

        # For simple, single-tool results, just format directly
        if len(results) == 1 and results[0]["success"]:
            tool_name = results[0]["tool"]
            data = results[0]["data"]

            if tool_name == "create_agent" and isinstance(data, dict):
                return f"Created agent '{data.get('name')}' (role: {data.get('role')}, model: {data.get('model')}, id: {data.get('agent_id')})"
            elif tool_name == "list_agents" and isinstance(data, dict):
                agents = data.get("agents", [])
                if not agents:
                    return "No agents currently configured."
                names = [f"{a['name']} ({a['role']})" for a in agents[:5]]
                return (
                    f"Found {data.get('total', len(agents))} agents: {', '.join(names)}"
                )
            elif tool_name == "delete_agent" and isinstance(data, dict):
                return (
                    f"Deleted agent '{data.get('name')}' (id: {data.get('agent_id')})"
                )
            elif tool_name == "store_memory" and isinstance(data, dict):
                return f"Memory stored successfully (id: {data.get('id', 'N/A')})"
            elif tool_name == "recall_memory" and isinstance(data, dict):
                count = data.get("count", len(data.get("results", [])))
                return f"Found {count} relevant memories."
            elif tool_name == "get_system_health" and isinstance(data, dict):
                return f"System status: {data.get('status', 'unknown')}"
            elif tool_name == "get_metrics" and isinstance(data, dict):
                summary = data.get("summary", {})
                return f"Metrics: {summary.get('total_requests', 0)} requests, ${summary.get('total_cost', 0):.4f} total cost"

        return " | ".join(result_strs)

    def _log_action(self, result: VosResult):
        """Log a VOS action for observability."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "intent": result.intent,
            "tools": [tc["name"] for tc in result.tool_calls],
            "success": all(r.get("success", False) for r in result.results),
            "summary": result.summary[:200],
            "source": result.source,
            "source_id": result.source_id,
        }
        self._action_log.append(entry)

        # Keep last 500 actions
        if len(self._action_log) > 500:
            self._action_log = self._action_log[-500:]

        # Persist to DevMemory
        try:
            from memory.dev_memory import get_dev_memory

            memory = get_dev_memory()
            memory.add(
                content=f"VOS action: {result.intent} -> {', '.join(tc['name'] for tc in result.tool_calls)} | {result.summary[:100]}",
                memory_type="context",
                metadata={
                    "type": "vos_action",
                    "intent": result.intent,
                    "source": result.source,
                },
            )
        except Exception:
            pass

    def get_recent_actions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent VOS actions for admin observability."""
        return list(reversed(self._action_log[-limit:]))

    def get_action_stats(self) -> Dict[str, Any]:
        """Get VOS action statistics."""
        if not self._action_log:
            return {"total": 0, "by_intent": {}, "by_source": {}, "by_tool": {}}

        by_intent: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        by_tool: Dict[str, int] = {}

        for entry in self._action_log:
            intent = entry["intent"]
            by_intent[intent] = by_intent.get(intent, 0) + 1

            source = entry["source"]
            by_source[source] = by_source.get(source, 0) + 1

            for tool in entry.get("tools", []):
                by_tool[tool] = by_tool.get(tool, 0) + 1

        return {
            "total": len(self._action_log),
            "by_intent": by_intent,
            "by_source": by_source,
            "by_tool": by_tool,
        }

    def get_available_tools(self) -> List[Dict[str, Any]]:
        """Get list of available tools for admin view."""
        return [
            {
                "name": t.name,
                "category": t.category,
                "description": t.description,
                "requires_confirmation": t.requires_confirmation,
            }
            for t in self.registry.get_all_tools()
        ]


# Singleton
_vos_engine: Optional[VosEngine] = None


def get_vos_engine() -> VosEngine:
    """Get the singleton VOS engine instance."""
    global _vos_engine
    if _vos_engine is None:
        _vos_engine = VosEngine()
    return _vos_engine
