"""
ToolUseProvider — Model-Agnostic Tool-Use Abstraction
=====================================================
Phase 1 of VOS3 Model-Agnostic Layer v3.2.

Provides a unified interface for tool-use conversations across providers:
- AnthropicToolProvider: wraps Anthropic's native tool_use API
- OpenAICompatToolProvider: wraps any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM)
- get_tool_provider(): factory with auto-detection, fast-path routing, and escalation

Protocol rules:
- AAAK compression FORBIDDEN in all reasoning paths
- All tool interactions use Structured JSON Blocks
- Parallel tool calls via asyncio.gather for Tier 2+ models
- ContextSnapshot for seamless escalation handoff
"""

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ContextSnapshot — cross-model escalation handoff
# ---------------------------------------------------------------------------


@dataclass
class ContextSnapshot:
    """Captures conversation state for cross-model handoff."""

    messages: List[Dict[str, Any]]
    tool_state: Dict[str, Any]
    task_metadata: Dict[str, Any]
    model_origin: str
    escalation_reason: str = ""
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Abstract ToolUseProvider
# ---------------------------------------------------------------------------


class ToolUseProvider(ABC):
    """Abstract base for tool-use conversations across LLM providers."""

    def __init__(self):
        self._message_history: List[Dict[str, Any]] = []
        self._pending_tools: Dict[str, Any] = {}
        self._malformed_count: int = 0
        self._agent_role: Optional[str] = None  # Q2-2026: bitmask gate binding

    def set_agent_role(self, role: str) -> None:
        """Bind this provider to an agent role for tool permission enforcement."""
        self._agent_role = role

    @property
    @abstractmethod
    def metadata(self) -> Dict[str, Any]:
        """Provider metadata including processing_locality, tier, etc."""
        ...

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """Synchronous tool-use chat. Returns full response with tool_calls."""
        ...

    @abstractmethod
    async def achat(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """Async tool-use chat."""
        ...

    @abstractmethod
    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ):
        """Streaming tool-use chat. Yields events."""
        ...

    def snapshot(self) -> ContextSnapshot:
        """Capture current conversation state for handoff."""
        return ContextSnapshot(
            messages=list(self._message_history),
            tool_state=dict(self._pending_tools),
            task_metadata=self.metadata,
            model_origin=self.metadata.get("model_id", "unknown"),
            timestamp=time.time(),
        )

    @classmethod
    def from_snapshot(
        cls, snapshot: ContextSnapshot, target_provider: "ToolUseProvider"
    ) -> "ToolUseProvider":
        """Resume conversation on a different provider."""
        target_provider._message_history = list(snapshot.messages)
        target_provider._pending_tools = dict(snapshot.tool_state)
        return target_provider


# ---------------------------------------------------------------------------
# AnthropicToolProvider
# ---------------------------------------------------------------------------


def get_raw_anthropic_client(api_key: Optional[str] = None):
    """Create a raw Anthropic SDK client.

    Centralizes the ``import anthropic`` so application code never imports the
    SDK directly (all SDK usage goes through tool_provider.py).
    """
    import anthropic

    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic(api_key=key)


class AnthropicToolProvider(ToolUseProvider):
    """Wraps Anthropic's native tool_use API (preserves existing behavior)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
        **kwargs,
    ):
        super().__init__()
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._model = model
        self._client = None
        self._async_client = None

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _get_async_client(self):
        if self._async_client is None:
            import anthropic

            self._async_client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._async_client

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "processing_locality": "cloud",
            "provider": "anthropic",
            "model_id": self._model,
            "tier": 3,
            "supports_parallel_tools": True,
        }

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        client = self._get_client()
        kwargs = {
            "model": self._model,
            "max_tokens": max_tokens,
            "tools": tools,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = client.messages.create(**kwargs)
        self._message_history = list(messages)
        return self._parse_anthropic_response(response, agent_role=self._agent_role)

    async def achat(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        client = self._get_async_client()
        kwargs = {
            "model": self._model,
            "max_tokens": max_tokens,
            "tools": tools,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = await client.messages.create(**kwargs)
        self._message_history = list(messages)
        return self._parse_anthropic_response(response, agent_role=self._agent_role)

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ):
        """Returns the Anthropic stream context manager for SSE consumption."""
        client = self._get_client()
        kwargs = {
            "model": self._model,
            "max_tokens": max_tokens,
            "tools": tools,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        self._message_history = list(messages)
        return client.messages.stream(**kwargs)

    @staticmethod
    def _parse_anthropic_response(
        response, agent_role: Optional[str] = None
    ) -> Dict[str, Any]:
        """Convert Anthropic response to unified format.

        Q2-2026 Hardening: When agent_role is set, tool calls are filtered
        through the bitmask permission gate before being returned.
        """
        text_parts = []
        tool_calls = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
            elif block.type == "tool_use":
                # ── BITMASK GATE (Q2-2026) ──
                if agent_role is not None:
                    from ai.agents.multi_agent import check_tool_permission

                    if not check_tool_permission(agent_role, block.name):
                        logger.warning(
                            "BLOCKED tool=%s role=%s (bitmask deny)",
                            block.name,
                            agent_role,
                        )
                        continue
                tool_calls.append(
                    {
                        "id": block.id,
                        "tool_name": block.name,
                        "arguments": block.input,
                    }
                )
        return {
            "text": "".join(text_parts),
            "tool_calls": tool_calls,
            "raw_content": response.content,
            "stop_reason": response.stop_reason,
            "model": response.model,
        }


# ---------------------------------------------------------------------------
# OpenAICompatToolProvider
# ---------------------------------------------------------------------------


class OpenAICompatToolProvider(ToolUseProvider):
    """Wraps any OpenAI-compatible /v1/chat/completions endpoint.

    Works with Ollama, LM Studio, vLLM, llama.cpp, and OpenAI itself.
    Uses Structured JSON Blocks for tool definitions (no free-form text).
    """

    TOOL_FORMAT = "json_schema"  # Mandatory structured format

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama-3.3-70b",
        api_key: Optional[str] = None,
        tier: int = 2,
        processing_locality: str = "local",
        supports_parallel_tools: bool = True,
        **kwargs,
    ):
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key or "ollama"  # Ollama doesn't need a real key
        self._tier = tier
        self._processing_locality = processing_locality
        self._supports_parallel_tools = supports_parallel_tools
        self._extra_metadata = kwargs

    @property
    def metadata(self) -> Dict[str, Any]:
        meta = {
            "processing_locality": self._processing_locality,
            "provider": "openai_compat",
            "model_id": self._model,
            "tier": self._tier,
            "supports_parallel_tools": self._supports_parallel_tools,
        }
        meta.update(self._extra_metadata)
        return meta

    def _convert_tools_to_openai(
        self, tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert Anthropic-style tool defs to OpenAI function format."""
        openai_tools = []
        for tool in tools:
            if "function" in tool:
                # Already OpenAI format
                openai_tools.append(tool)
            else:
                # Anthropic format: {name, description, input_schema}
                openai_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.get("name", ""),
                            "description": tool.get("description", ""),
                            "parameters": tool.get("input_schema", {}),
                        },
                    }
                )
        return openai_tools

    def _convert_messages_to_openai(
        self, messages: List[Dict[str, Any]], system: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """Convert messages to OpenAI chat format."""
        oai_msgs = []
        if system:
            oai_msgs.append({"role": "system", "content": system})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "user":
                # Handle tool_result blocks (Anthropic format)
                if isinstance(content, list):
                    tool_results = []
                    for block in content:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_result"
                        ):
                            tool_results.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": block.get("tool_use_id", ""),
                                    "content": block.get("content", ""),
                                }
                            )
                    if tool_results:
                        oai_msgs.extend(tool_results)
                        continue
                oai_msgs.append({"role": "user", "content": str(content)})

            elif role == "assistant":
                # Handle assistant content blocks with tool_use
                if isinstance(content, list):
                    text_parts = []
                    tool_calls = []
                    for block in content:
                        if hasattr(block, "type"):
                            if block.type == "text":
                                text_parts.append(block.text)
                            elif block.type == "tool_use":
                                tool_calls.append(
                                    {
                                        "id": block.id,
                                        "type": "function",
                                        "function": {
                                            "name": block.name,
                                            "arguments": json.dumps(block.input),
                                        },
                                    }
                                )
                    assistant_msg = {"role": "assistant"}
                    if text_parts:
                        assistant_msg["content"] = "".join(text_parts)
                    if tool_calls:
                        assistant_msg["tool_calls"] = tool_calls
                    oai_msgs.append(assistant_msg)
                else:
                    oai_msgs.append({"role": "assistant", "content": str(content)})
            else:
                oai_msgs.append({"role": role, "content": str(content)})

        return oai_msgs

    def _build_request_body(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        max_tokens: int,
        stream: bool = False,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
            if self._supports_parallel_tools and self._tier >= 2:
                body["parallel_tool_calls"] = True
        return body

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        import httpx

        oai_messages = self._convert_messages_to_openai(messages, system)
        oai_tools = self._convert_tools_to_openai(tools) if tools else []
        body = self._build_request_body(oai_messages, oai_tools, max_tokens)

        # Determine endpoint: Ollama uses /v1/chat/completions via compat layer
        url = f"{self._base_url}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        resp = httpx.post(url, json=body, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        self._message_history = list(messages)
        return self._parse_openai_response(data, agent_role=self._agent_role)

    async def achat(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        import httpx

        oai_messages = self._convert_messages_to_openai(messages, system)
        oai_tools = self._convert_tools_to_openai(tools) if tools else []
        body = self._build_request_body(oai_messages, oai_tools, max_tokens)

        url = f"{self._base_url}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        self._message_history = list(messages)
        return self._parse_openai_response(data, agent_role=self._agent_role)

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ):
        """Returns a streaming context manager yielding SSE-like events."""

        oai_messages = self._convert_messages_to_openai(messages, system)
        oai_tools = self._convert_tools_to_openai(tools) if tools else []
        body = self._build_request_body(
            oai_messages, oai_tools, max_tokens, stream=True
        )

        url = f"{self._base_url}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        self._message_history = list(messages)
        return _OpenAIStreamContext(url, headers, body)

    @staticmethod
    def _parse_openai_response(
        data: Dict[str, Any], agent_role: Optional[str] = None
    ) -> Dict[str, Any]:
        """Convert OpenAI response to unified format.

        Q2-2026 Hardening: When agent_role is set, tool calls are filtered
        through the bitmask permission gate before being returned.
        """
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})

        text = message.get("content", "") or ""
        tool_calls = []
        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            tool_name = func.get("name", "")
            # ── BITMASK GATE (Q2-2026) ──
            if agent_role is not None:
                from ai.agents.multi_agent import check_tool_permission

                if not check_tool_permission(agent_role, tool_name):
                    logger.warning(
                        "BLOCKED tool=%s role=%s (bitmask deny)",
                        tool_name,
                        agent_role,
                    )
                    continue
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                {
                    "id": tc.get("id", ""),
                    "tool_name": tool_name,
                    "arguments": args,
                }
            )

        return {
            "text": text,
            "tool_calls": tool_calls,
            "raw_content": message,
            "stop_reason": choice.get("finish_reason", ""),
            "model": data.get("model", ""),
        }


class _OpenAIStreamContext:
    """Context manager for OpenAI-compatible streaming responses."""

    def __init__(self, url: str, headers: Dict, body: Dict):
        self._url = url
        self._headers = headers
        self._body = body
        self._client = None
        self._response = None
        self._collected_text = ""
        self._tool_calls: List[Dict] = []

    def __enter__(self):
        import httpx

        self._client = httpx.Client(timeout=120)
        self._response = self._client.stream(
            "POST", self._url, json=self._body, headers=self._headers
        )
        self._response.__enter__()
        return self

    def __exit__(self, *args):
        if self._response:
            self._response.__exit__(*args)
        if self._client:
            self._client.close()

    def __iter__(self):
        for line in self._response.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                if "content" in delta and delta["content"]:
                    self._collected_text += delta["content"]
                    yield _StreamEvent("text", delta["content"])
                if "tool_calls" in delta:
                    for tc in delta["tool_calls"]:
                        yield _StreamEvent("tool_call_delta", tc)
            except json.JSONDecodeError:
                continue

    def get_collected_text(self) -> str:
        return self._collected_text


@dataclass
class _StreamEvent:
    type: str
    data: Any


# ---------------------------------------------------------------------------
# Snappy availability check
# ---------------------------------------------------------------------------


def _is_snappy_available() -> bool:
    """Check if the local-snappy model (Gemma 4 27B) is available via Ollama."""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        import httpx

        resp = httpx.get(f"{base_url}/api/tags", timeout=2)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            return any("gemma" in m and "27b" in m for m in models)
    except Exception:
        pass
    return False


def _is_ollama_available() -> bool:
    """Check if Ollama is reachable."""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        import httpx

        resp = httpx.get(f"{base_url}/api/tags", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Factory: get_tool_provider()
# ---------------------------------------------------------------------------

# Task types that route to local-snappy fast path
_SNAPPY_TASKS = {"monitor", "telemetry", "vbus_status", "health_check"}


def get_tool_provider(
    task_type: Optional[str] = None,
    complexity: int = 5,
    preferred_provider: Optional[str] = None,
) -> ToolUseProvider:
    """
    Factory that picks the optimal ToolUseProvider based on env vars and task.

    Routing priority:
    1. local-snappy fast-path for monitoring/telemetry/low-complexity (<=3)
    2. VOS3_PREFERRED_PROVIDER env var (anthropic|openai|local|auto)
    3. Auto-detect: ANTHROPIC_API_KEY -> OPENAI_API_KEY -> OLLAMA_BASE_URL

    Bandwidth-aware routing (VOS3_LOCALITY_PREFERENCE):
    - local-first: prefer local for complexity >= 7
    - cloud-first: standard auto routing
    - auto: default behavior
    """
    pref = preferred_provider or os.getenv("VOS3_PREFERRED_PROVIDER", "auto")
    locality = os.getenv("VOS3_LOCALITY_PREFERENCE", "auto")
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # Fast-path: route monitoring/telemetry to local-snappy
    if (task_type in _SNAPPY_TASKS or complexity <= 3) and _is_snappy_available():
        logger.info("Fast-path: routing to local-snappy (Gemma 4 27B)")
        return OpenAICompatToolProvider(
            base_url=ollama_url,
            model="gemma-4-27b",
            tier=2,
            processing_locality="local",
            latency="ultra-low",
        )

    # Bandwidth-aware: heavy local tasks stay local when preference is local-first
    if locality == "local-first" and complexity >= 7 and _is_ollama_available():
        logger.info("Locality preference: routing heavy task to local-default")
        return OpenAICompatToolProvider(
            base_url=ollama_url,
            model="llama-3.3-70b",
            tier=2,
            processing_locality="local",
        )

    # Explicit provider preference
    if pref == "anthropic" or (pref == "auto" and os.getenv("ANTHROPIC_API_KEY")):
        return AnthropicToolProvider()

    if pref == "openai" or (pref == "auto" and os.getenv("OPENAI_API_KEY")):
        api_key = os.getenv("OPENAI_API_KEY")
        return OpenAICompatToolProvider(
            base_url="https://api.openai.com",
            model=os.getenv("VOS3_DEFAULT_CHAT_MODEL", "gpt-4o"),
            api_key=api_key,
            tier=3,
            processing_locality="cloud",
        )

    if pref == "local" or (pref == "auto" and _is_ollama_available()):
        return OpenAICompatToolProvider(
            base_url=ollama_url,
            model="llama-3.3-70b",
            tier=2,
            processing_locality="local",
        )

    # No provider available — raise
    raise RuntimeError(
        "No LLM provider available. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
        "or OLLAMA_BASE_URL to enable at least one provider."
    )


def check_escalation_needed(
    provider: ToolUseProvider,
    malformed_count: int = 0,
    context_tokens: int = 0,
) -> Optional[str]:
    """Check if the current provider needs escalation.

    Returns escalation reason string or None.
    """
    meta = provider.metadata
    model_id = meta.get("model_id", "")

    # Only escalate from snappy (27B) tier
    if "gemma" not in model_id and "27b" not in model_id:
        return None

    if malformed_count > 3:
        return "malformed_tool_calls"
    if context_tokens > 28000:  # 90% of 32K window
        return "context_overflow"
    return None


def escalate_provider(
    current: ToolUseProvider,
    reason: str,
) -> ToolUseProvider:
    """Escalate from current provider to a more capable one, preserving context."""
    snap = current.snapshot()
    snap.escalation_reason = reason
    logger.warning(f"Escalating from {snap.model_origin}: {reason}")

    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # Try local-default (70B) first, then cloud
    if _is_ollama_available():
        target = OpenAICompatToolProvider(
            base_url=ollama_url,
            model="llama-3.3-70b",
            tier=2,
            processing_locality="local",
        )
    elif os.getenv("ANTHROPIC_API_KEY"):
        target = AnthropicToolProvider()
    else:
        target = get_tool_provider(complexity=8)

    return ToolUseProvider.from_snapshot(snap, target)


__all__ = [
    "ToolUseProvider",
    "AnthropicToolProvider",
    "OpenAICompatToolProvider",
    "ContextSnapshot",
    "get_tool_provider",
    "check_escalation_needed",
    "escalate_provider",
]
