"""
Terminal API Routes - Web-based AI Code Terminal
=================================================

API endpoints for the web-based AI terminal that can modify local files.
Supports multiple providers via ToolUseProvider abstraction:
- Anthropic (Claude) when ANTHROPIC_API_KEY is set
- OpenAI-compatible (Ollama, LM Studio, vLLM) for local/offline operation
- Auto-detection: picks best available provider

All endpoints require authentication via Depends(get_current_user).
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any
from collections import OrderedDict
import json
import asyncio
import os
import logging
import time

from tools.file_tools import TOOL_DEFINITIONS, get_file_tools
from middleware.auth import get_current_user, AuthenticatedUser
from ai.llm.tool_provider import (
    get_tool_provider,
    get_raw_anthropic_client,
    AnthropicToolProvider,
)

logger = logging.getLogger(__name__)


def get_anthropic_client():
    """Get an Anthropic SDK client via tool_provider (centralized SDK import)."""
    return get_raw_anthropic_client()


router = APIRouter()

# Store conversation history per session (LRU with TTL)
MAX_SESSIONS = 1000
MAX_MESSAGES_PER_SESSION = 100
SESSION_TTL_SECONDS = 3600  # 1 hour

sessions: OrderedDict[str, dict] = OrderedDict()
_session_lock = asyncio.Lock()
# Each value: {"messages": [...], "last_access": float}


async def _get_session(session_id: str) -> list:
    """Get or create session with TTL and LRU eviction."""
    async with _session_lock:
        now = time.time()
        # Prune expired sessions (lazy - only on access)
        expired = [
            k
            for k, v in sessions.items()
            if now - v["last_access"] > SESSION_TTL_SECONDS
        ]
        for k in expired:
            del sessions[k]
        # LRU eviction if at capacity
        while len(sessions) >= MAX_SESSIONS:
            sessions.popitem(last=False)
        # Get or create
        if session_id not in sessions:
            sessions[session_id] = {"messages": [], "last_access": now}
        else:
            sessions.move_to_end(session_id)
            sessions[session_id]["last_access"] = now
        # Trim messages
        msgs = sessions[session_id]["messages"]
        if len(msgs) > MAX_MESSAGES_PER_SESSION:
            sessions[session_id]["messages"] = msgs[-MAX_MESSAGES_PER_SESSION:]
        return sessions[session_id]["messages"]


def _get_available_models() -> Dict[str, str]:
    """Build available models dict based on configured providers."""
    models = {}

    # Cloud models (if API keys present)
    if os.getenv("ANTHROPIC_API_KEY"):
        models.update(
            {
                "claude-sonnet-4": "claude-sonnet-4-20250514",
                "claude-opus-4": "claude-opus-4-20250514",
                "claude-haiku": "claude-3-5-haiku-20241022",
            }
        )
    if os.getenv("OPENAI_API_KEY"):
        models.update(
            {
                "gpt-4o": "gpt-4o",
                "gpt-4o-mini": "gpt-4o-mini",
            }
        )

    # Local models (if Ollama reachable)
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        import httpx

        resp = httpx.get(f"{ollama_url}/api/tags", timeout=2)
        if resp.status_code == 200:
            for m in resp.json().get("models", []):
                name = m["name"]
                models[f"local:{name}"] = name
    except (OSError, httpx.HTTPError, ValueError, KeyError):
        pass

    # Fallback: at least show something
    if not models:
        models["local:llama-3.3-70b"] = "llama-3.3-70b"

    return models


DEFAULT_MODEL = os.getenv("VOS3_DEFAULT_CHAT_MODEL", "claude-sonnet-4")


class TerminalMessage(BaseModel):
    message: str
    session_id: Optional[str] = "default"
    base_path: Optional[str] = None
    model: Optional[str] = None


class ToolResult(BaseModel):
    tool_use_id: str
    result: Dict[str, Any]


SYSTEM_PROMPT = """You are an AI coding assistant with direct access to the local file system. You can read, write, and modify files, run commands, and help users build and maintain their codebase.

You have access to these tools:
- read_file: Read file contents
- write_file: Write/create files
- edit_file: Make targeted edits to files
- list_files: List directory contents or glob patterns
- search_files: Search for text patterns in files
- run_command: Execute shell commands (git, npm, pip, etc.)
- create_directory: Create directories
- delete_file: Delete files

Guidelines:
1. Always read a file before editing it to understand its current state
2. Use edit_file for small changes, write_file for complete rewrites
3. Be careful with destructive operations - confirm before deleting
4. Explain what you're doing and why
5. When writing code, follow the existing project's style and conventions
6. If a task requires multiple steps, explain the plan first

The user is interacting with you through a web interface to modify their local project files."""


@router.get("/models")
async def list_models(user: AuthenticatedUser = Depends(get_current_user)):
    """List available models from all configured providers."""
    models = _get_available_models()
    return {
        "models": [
            {"key": k, "id": v, "name": k.replace("-", " ").replace(":", " — ").title()}
            for k, v in models.items()
        ],
        "default": DEFAULT_MODEL,
    }


@router.post("/chat")
async def terminal_chat(
    request: TerminalMessage, user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Chat endpoint with tool execution loop.
    Processes user message, executes tools as needed, returns final response.
    Tries direct Anthropic SDK first, falls back to ToolUseProvider abstraction.
    """
    models = _get_available_models()
    model_key = request.model or DEFAULT_MODEL
    model_id = models.get(model_key, model_key)

    session_id = request.session_id or "default"

    # Try direct Anthropic SDK path
    try:
        client = get_anthropic_client()
    except Exception:
        raise HTTPException(status_code=500, detail="No LLM provider available")

    file_tools = get_file_tools(request.base_path)

    # Get or create session
    if session_id not in sessions:
        sessions[session_id] = {"messages": [], "last_access": time.time()}
    session_msgs = sessions[session_id]["messages"]

    session_msgs.append({"role": "user", "content": request.message})

    messages = session_msgs.copy()
    tool_calls_made = []

    max_iterations = 10
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        try:
            response = client.messages.create(
                model=model_id,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
        except Exception as e:
            logger.error(f"Anthropic API failed: {e}")
            raise HTTPException(status_code=502, detail=f"LLM provider error: {e}")

        # Extract text and tool_use blocks
        text_parts = []
        tool_uses = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(block)

        text_content = "".join(text_parts)

        if not tool_uses:
            session_msgs.append({"role": "assistant", "content": text_content})

            return {
                "response": text_content,
                "tool_calls": tool_calls_made,
                "session_id": session_id,
                "model": model_id,
                "provider": "anthropic",
                "mode": "api",
            }

        # Execute tool calls
        tool_results = []
        for tool_use in tool_uses:
            tool_name = tool_use.name
            tool_input = tool_use.input

            tool_result = file_tools.execute_tool(tool_name, tool_input)

            tool_calls_made.append(
                {
                    "tool": tool_name,
                    "input": tool_input,
                    "result": tool_result,
                }
            )

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": json.dumps(tool_result),
                }
            )

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    return {
        "response": "Maximum tool iterations reached. Please try a simpler request.",
        "tool_calls": tool_calls_made,
        "session_id": session_id,
        "mode": "api",
    }


@router.post("/chat/stream")
async def terminal_chat_stream(
    request: TerminalMessage, user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Streaming chat endpoint with tool execution.
    Uses ToolUseProvider abstraction for model-agnostic streaming.
    """
    provider = get_tool_provider(task_type="terminal", complexity=5)
    file_tools = get_file_tools(request.base_path)

    # B-HIGH-14 fix: Scope session by user_id to prevent cross-user hijacking.
    session_id = f"{user.id}:{request.session_id or 'default'}"
    session_msgs = await _get_session(session_id)

    session_msgs.append({"role": "user", "content": request.message})

    async def generate():
        messages = session_msgs.copy()
        tool_calls_made = []
        max_iterations = 10
        iteration = 0
        meta = provider.metadata

        # Send model info
        yield f"data: {json.dumps({'type': 'info', 'model': meta.get('model_id', 'unknown'), 'provider': meta.get('provider', 'unknown'), 'mode': 'api'})}\n\n"

        while iteration < max_iterations:
            iteration += 1

            # For Anthropic provider, use native streaming
            if isinstance(provider, AnthropicToolProvider):
                collected_text = ""
                tool_uses = []

                with provider.chat_stream(
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    system=SYSTEM_PROMPT,
                    max_tokens=4096,
                ) as stream:
                    for event in stream:
                        if hasattr(event, "type"):
                            if event.type == "content_block_delta":
                                if hasattr(event.delta, "text"):
                                    collected_text += event.delta.text
                                    yield f"data: {json.dumps({'type': 'text', 'content': event.delta.text})}\n\n"

                    final_message = stream.get_final_message()
                    tool_uses = [
                        block
                        for block in final_message.content
                        if block.type == "tool_use"
                    ]

                if not tool_uses:
                    session_msgs.append(
                        {"role": "assistant", "content": collected_text}
                    )
                    yield f"data: {json.dumps({'type': 'done', 'tool_calls': tool_calls_made, 'mode': 'api'})}\n\n"
                    return

                tool_results = []
                for tool_use in tool_uses:
                    tool_name = tool_use.name
                    tool_input = tool_use.input

                    yield f"data: {json.dumps({'type': 'tool_start', 'tool': tool_name, 'input': tool_input})}\n\n"

                    result = file_tools.execute_tool(tool_name, tool_input)

                    tool_calls_made.append(
                        {
                            "tool": tool_name,
                            "input": tool_input,
                            "result": result,
                        }
                    )

                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'result': result})}\n\n"

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use.id,
                            "content": json.dumps(result),
                        }
                    )

                messages.append({"role": "assistant", "content": final_message.content})
                messages.append({"role": "user", "content": tool_results})

            else:
                # OpenAI-compat streaming: collect text then check tool calls
                collected_text = ""

                with provider.chat_stream(
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    system=SYSTEM_PROMPT,
                    max_tokens=4096,
                ) as stream:
                    for event in stream:
                        if event.type == "text" and event.data:
                            collected_text += event.data
                            yield f"data: {json.dumps({'type': 'text', 'content': event.data})}\n\n"

                # After streaming, do a non-streaming call to check tool calls
                result = provider.chat(
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    system=SYSTEM_PROMPT,
                    max_tokens=4096,
                )

                tool_calls = result.get("tool_calls", [])

                if not tool_calls:
                    text = result.get("text", collected_text)
                    session_msgs.append(
                        {
                            "role": "assistant",
                            "content": text,
                        }
                    )
                    yield f"data: {json.dumps({'type': 'done', 'tool_calls': tool_calls_made, 'mode': 'api'})}\n\n"
                    return

                tool_results = []
                for tc in tool_calls:
                    tool_name = tc.get("tool_name", "")
                    tool_input = tc.get("arguments", {})
                    tc_id = tc.get("id", "")

                    yield f"data: {json.dumps({'type': 'tool_start', 'tool': tool_name, 'input': tool_input})}\n\n"

                    tool_result = file_tools.execute_tool(tool_name, tool_input)

                    tool_calls_made.append(
                        {
                            "tool": tool_name,
                            "input": tool_input,
                            "result": tool_result,
                        }
                    )

                    yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'result': tool_result})}\n\n"

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc_id,
                            "content": json.dumps(tool_result),
                        }
                    )

                raw = result.get("raw_content")
                if raw is not None:
                    messages.append({"role": "assistant", "content": raw})
                else:
                    messages.append(
                        {"role": "assistant", "content": result.get("text", "")}
                    )
                messages.append({"role": "user", "content": tool_results})

        yield f"data: {json.dumps({'type': 'error', 'message': 'Max iterations reached'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get conversation history for a session."""
    if session_id not in sessions:
        return {"messages": [], "session_id": session_id}

    return {"messages": sessions[session_id]["messages"], "session_id": session_id}


@router.delete("/sessions/{session_id}")
async def clear_session(
    session_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Clear a session's history."""
    if session_id in sessions:
        del sessions[session_id]
    return {"success": True, "message": f"Session {session_id} cleared"}


@router.get("/tools")
async def list_tools(user: AuthenticatedUser = Depends(get_current_user)):
    """List available tools and their schemas."""
    return {"tools": TOOL_DEFINITIONS, "count": len(TOOL_DEFINITIONS)}
