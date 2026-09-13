"""
MCP Provider Integration
========================
Routes LLM requests through V-OS-MCP-Agent-Server for:
- Semantic caching (save 40-60% on repeated queries)
- User quotas (per-user spending limits)
- Smart routing (automatic model selection)
- Cost tracking (real-time analytics)

Usage:
    from src.mcp_provider import MCPProvider, mcp_generate

    # As a provider
    provider = MCPProvider()
    response = provider.generate(messages)

    # Quick function
    result = await mcp_generate("What is Python?", user_id="user-123")
"""

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Generator
import httpx

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

from .llm import LLMProvider, LLMResponse, Provider
from .errors import ModelError, get_logger


@dataclass
class MCPConfig:
    """Configuration for MCP server connection."""

    base_url: str = "http://localhost:8080"
    timeout: float = 120.0  # LLM calls can take time
    retry_attempts: int = 3
    retry_delay: float = 1.0


class MCPProvider(LLMProvider):
    """
    LLM Provider that routes through V-OS-MCP-Agent-Server.

    Benefits over direct API calls:
    - Semantic caching: Similar queries return cached responses instantly
    - Smart routing: Automatically picks cheapest appropriate model
    - User quotas: Enforce spending limits per user
    - Cost tracking: Monitor usage in real-time
    """

    def __init__(self, config: MCPConfig = None, user_id: str = None):
        self.config = config or MCPConfig()
        self.user_id = user_id or "default"
        self.logger = get_logger()
        self._session_initialized = False
        self._client = None

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url, timeout=self.config.timeout
            )
        return self._client

    def _messages_to_string(self, messages: List[BaseMessage]) -> str:
        """Convert LangChain messages to a single prompt string."""
        parts = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                parts.append(f"System: {msg.content}")
            elif isinstance(msg, HumanMessage):
                parts.append(f"User: {msg.content}")
            elif isinstance(msg, AIMessage):
                parts.append(f"Assistant: {msg.content}")
            else:
                parts.append(msg.content)
        return "\n\n".join(parts)

    def _ensure_session(self, client: httpx.Client):
        """Initialize MCP session if not already done."""
        if self._session_initialized:
            return

        try:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "clientInfo": {"name": "vos3-backend", "version": "1.0.0"},
                        "capabilities": {},
                    },
                },
            )
            response.raise_for_status()
            self._session_initialized = True
            self.logger.info("MCP session initialized")
        except Exception as e:
            self.logger.warning(f"MCP session init failed (may already exist): {e}")
            self._session_initialized = True  # Try anyway

    def generate(self, messages: List[BaseMessage], **kwargs) -> LLMResponse:
        """
        Generate response through MCP server.

        The MCP server will:
        1. Check semantic cache for similar queries
        2. Check user quota
        3. Route to optimal model based on complexity
        4. Track costs
        """
        start_time = time.time()
        client = self._get_client()

        self._ensure_session(client)

        # Convert messages to prompt
        prompt = self._messages_to_string(messages)

        # Call MCP server
        for attempt in range(self.config.retry_attempts):
            try:
                response = client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "v_agent_chat",
                            "arguments": {
                                "message": prompt,
                                "userId": self.user_id,
                                "streaming": False,
                            },
                        },
                    },
                )
                response.raise_for_status()
                result = response.json()

                # Check for errors
                if "error" in result:
                    error = result["error"]
                    if "quota" in str(error).lower():
                        raise ModelError(f"Quota exceeded for user {self.user_id}")
                    raise ModelError(f"MCP error: {error}")

                # Parse response
                content = ""
                model = "unknown"
                tokens = 0
                cached = False

                if "result" in result and "content" in result["result"]:
                    for item in result["result"]["content"]:
                        if item.get("type") == "text":
                            text = item.get("text", "")
                            # Try to parse as JSON for metadata
                            try:
                                import json

                                data = json.loads(text)
                                content = data.get("text", data.get("response", text))
                                model = data.get("model", "mcp-routed")
                                tokens = data.get("tokens", 0)
                                cached = data.get("cached", False)
                            except:
                                content = text

                latency = (time.time() - start_time) * 1000

                return LLMResponse(
                    content=content,
                    model=model,
                    provider=Provider.OPENAI,  # Generic, actual provider chosen by MCP
                    tokens_used=tokens,
                    latency_ms=latency,
                    cached=cached,
                    metadata={"mcp_routed": True, "user_id": self.user_id},
                )

            except httpx.HTTPStatusError as e:
                self.logger.warning(f"MCP request failed (attempt {attempt + 1}): {e}")
                if attempt < self.config.retry_attempts - 1:
                    time.sleep(self.config.retry_delay * (attempt + 1))
                else:
                    raise ModelError(
                        f"MCP server error after {self.config.retry_attempts} attempts: {e}"
                    )

            except httpx.ConnectError as e:
                raise ModelError(
                    f"Cannot connect to MCP server at {self.config.base_url}. "
                    f"Make sure v-os-mcp-agent-server is running. Error: {e}"
                )

    def stream(
        self, messages: List[BaseMessage], **kwargs
    ) -> Generator[str, None, None]:
        """
        Stream response through MCP server.

        Note: Currently falls back to non-streaming as MCP streaming requires SSE.
        """
        # For now, use non-streaming and yield the full response
        response = self.generate(messages, **kwargs)
        yield response.content

    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the MCP-routed model."""
        return {
            "provider": "mcp",
            "base_url": self.config.base_url,
            "user_id": self.user_id,
            "features": ["semantic_cache", "smart_routing", "quotas", "cost_tracking"],
        }

    def get_quota_status(self) -> Dict[str, Any]:
        """Check current quota status for user."""
        client = self._get_client()
        self._ensure_session(client)

        try:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "v_quota_status",
                        "arguments": {"userId": self.user_id},
                    },
                },
            )
            result = response.json()

            if "result" in result and "content" in result["result"]:
                for item in result["result"]["content"]:
                    if item.get("type") == "text":
                        import json

                        return json.loads(item["text"])

            return {"error": "Could not parse quota status"}

        except Exception as e:
            return {"error": str(e)}

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get semantic cache statistics."""
        client = self._get_client()
        self._ensure_session(client)

        try:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "v_cache_stats", "arguments": {}},
                },
            )
            result = response.json()

            if "result" in result and "content" in result["result"]:
                for item in result["result"]["content"]:
                    if item.get("type") == "text":
                        import json

                        return json.loads(item["text"])

            return {"error": "Could not parse cache stats"}

        except Exception as e:
            return {"error": str(e)}

    def close(self):
        """Close HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


# Async version for FastAPI endpoints
class AsyncMCPProvider:
    """Async version of MCP Provider for use in FastAPI routes."""

    def __init__(self, config: MCPConfig = None, user_id: str = None):
        self.config = config or MCPConfig()
        self.user_id = user_id or "default"
        self.logger = get_logger()
        self._session_initialized = False

    async def generate(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """Generate response through MCP server (async)."""
        start_time = time.time()

        async with httpx.AsyncClient(
            base_url=self.config.base_url, timeout=self.config.timeout
        ) as client:
            # Initialize session
            if not self._session_initialized:
                await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "clientInfo": {"name": "vos3-backend", "version": "1.0.0"},
                            "capabilities": {},
                        },
                    },
                )
                self._session_initialized = True

            # Call v_agent_chat
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "v_agent_chat",
                        "arguments": {
                            "message": prompt,
                            "userId": self.user_id,
                            "streaming": False,
                        },
                    },
                },
            )

            result = response.json()
            latency = (time.time() - start_time) * 1000

            # Parse response
            content = ""
            model = "mcp-routed"
            cached = False

            if "result" in result and "content" in result["result"]:
                for item in result["result"]["content"]:
                    if item.get("type") == "text":
                        text = item.get("text", "")
                        try:
                            import json

                            data = json.loads(text)
                            content = data.get("text", data.get("response", text))
                            model = data.get("model", "mcp-routed")
                            cached = data.get("cached", False)
                        except:
                            content = text

            return {
                "content": content,
                "model": model,
                "latency_ms": latency,
                "cached": cached,
                "user_id": self.user_id,
            }


# Convenience functions
def mcp_generate(prompt: str, user_id: str = "default", **kwargs) -> str:
    """Quick generate function through MCP."""
    provider = MCPProvider(user_id=user_id)
    messages = [HumanMessage(content=prompt)]
    response = provider.generate(messages, **kwargs)
    provider.close()
    return response.content


async def mcp_generate_async(prompt: str, user_id: str = "default", **kwargs) -> str:
    """Quick async generate function through MCP."""
    provider = AsyncMCPProvider(user_id=user_id)
    result = await provider.generate(prompt, **kwargs)
    return result["content"]
