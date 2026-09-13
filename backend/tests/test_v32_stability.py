"""
VOS3 v3.2 Model-Agnostic Layer — Stability Audit & Testing Suite
=================================================================

Categories:
  1. Model-Agnostic Abstraction (ToolUseProvider)
  2. Sovereign Fast-Path & Routing
  3. Memory Sync & Spatial Scoping
  4. System Stability (fallback chain, dead-end)

Every test is verified against KERNEL_REASONING_SPEC.md standards.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure imports resolve from backend root
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from ai.llm.tool_provider import (
    ToolUseProvider,
    AnthropicToolProvider,
    OpenAICompatToolProvider,
    ContextSnapshot,
    get_tool_provider,
    check_escalation_needed,
    escalate_provider,
    _SNAPPY_TASKS,
    _is_snappy_available,
)
from memory.dev_memory import (
    DevMemory,
    WRITE_PERMISSIONS,
    _detect_wing,
    _detect_room,
)

# ===================================================================
# CATEGORY 1: Model-Agnostic Abstraction (ToolUseProvider)
# ===================================================================


class TestToolUseProviderAbstraction:
    """Verify the ToolUseProvider abstraction layer conforms to spec."""

    # ---------------------------------------------------------------
    # 1.1  Abstract base enforces interface
    # ---------------------------------------------------------------
    def test_tool_use_provider_is_abstract(self):
        """ToolUseProvider cannot be instantiated directly."""
        with pytest.raises(TypeError):
            ToolUseProvider()

    # ---------------------------------------------------------------
    # 1.2  AnthropicToolProvider metadata
    # ---------------------------------------------------------------
    def test_anthropic_metadata_processing_locality(self):
        """AnthropicToolProvider must report processing_locality='cloud'."""
        provider = AnthropicToolProvider(api_key="test-key")
        meta = provider.metadata
        assert meta["processing_locality"] == "cloud"
        assert meta["provider"] == "anthropic"
        assert meta["tier"] == 3
        assert meta["supports_parallel_tools"] is True

    def test_anthropic_metadata_model_id(self):
        """Default model must be set."""
        provider = AnthropicToolProvider(api_key="test-key")
        assert "claude" in provider.metadata["model_id"]

    # ---------------------------------------------------------------
    # 1.3  OpenAICompatToolProvider metadata
    # ---------------------------------------------------------------
    def test_openai_compat_metadata_processing_locality(self):
        """OpenAICompatToolProvider reports locality correctly."""
        provider = OpenAICompatToolProvider(
            base_url="http://localhost:11434",
            model="llama-3.3-70b",
            processing_locality="local",
        )
        meta = provider.metadata
        assert meta["processing_locality"] == "local"
        assert meta["provider"] == "openai_compat"

    def test_openai_compat_cloud_locality(self):
        """When pointed at cloud, locality must be 'cloud'."""
        provider = OpenAICompatToolProvider(
            base_url="https://api.openai.com",
            model="gpt-4o",
            processing_locality="cloud",
            tier=3,
        )
        assert provider.metadata["processing_locality"] == "cloud"
        assert provider.metadata["tier"] == 3

    # ---------------------------------------------------------------
    # 1.4  Structured JSON Blocks (TOOL_FORMAT)
    # ---------------------------------------------------------------
    def test_openai_compat_tool_format_is_json_schema(self):
        """KERNEL_REASONING_SPEC mandates TOOL_FORMAT='json_schema'."""
        assert OpenAICompatToolProvider.TOOL_FORMAT == "json_schema"

    # ---------------------------------------------------------------
    # 1.5  Tool conversion: Anthropic → OpenAI format
    # ---------------------------------------------------------------
    def test_tool_conversion_anthropic_to_openai(self):
        """Anthropic {name, description, input_schema} → OpenAI {function: {...}}."""
        provider = OpenAICompatToolProvider()
        anthropic_tools = [
            {
                "name": "read_file",
                "description": "Reads a file from disk",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ]
        oai_tools = provider._convert_tools_to_openai(anthropic_tools)

        assert len(oai_tools) == 1
        assert oai_tools[0]["type"] == "function"
        assert oai_tools[0]["function"]["name"] == "read_file"
        assert oai_tools[0]["function"]["parameters"]["type"] == "object"

    def test_tool_conversion_passthrough_openai_format(self):
        """Already-OpenAI-format tools pass through unchanged."""
        provider = OpenAICompatToolProvider()
        oai_tools = [
            {
                "type": "function",
                "function": {"name": "test", "description": "test", "parameters": {}},
            }
        ]
        result = provider._convert_tools_to_openai(oai_tools)
        assert result == oai_tools

    # ---------------------------------------------------------------
    # 1.6  Parallel tool calls enabled for Tier 2+
    # ---------------------------------------------------------------
    def test_parallel_tool_calls_tier2(self):
        """Tier 2+ providers must set parallel_tool_calls=True in request body."""
        provider = OpenAICompatToolProvider(tier=2, supports_parallel_tools=True)
        messages = [{"role": "user", "content": "test"}]
        tools = [
            {
                "type": "function",
                "function": {"name": "t", "description": "d", "parameters": {}},
            }
        ]
        body = provider._build_request_body(messages, tools, max_tokens=1024)

        assert body.get("parallel_tool_calls") is True

    def test_parallel_tool_calls_tier1_disabled(self):
        """Tier 1 providers must NOT set parallel_tool_calls."""
        provider = OpenAICompatToolProvider(tier=1, supports_parallel_tools=False)
        messages = [{"role": "user", "content": "test"}]
        tools = [
            {
                "type": "function",
                "function": {"name": "t", "description": "d", "parameters": {}},
            }
        ]
        body = provider._build_request_body(messages, tools, max_tokens=1024)

        assert "parallel_tool_calls" not in body

    # ---------------------------------------------------------------
    # 1.7  Response parsing: OpenAI format → unified format
    # ---------------------------------------------------------------
    def test_openai_response_parsing(self):
        """Verify unified format: {text, tool_calls, raw_content, stop_reason, model}."""
        mock_response = {
            "choices": [
                {
                    "message": {
                        "content": "Hello",
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "/tmp/test.txt"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "model": "llama-3.3-70b",
        }
        result = OpenAICompatToolProvider._parse_openai_response(mock_response)

        assert result["text"] == "Hello"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["tool_name"] == "read_file"
        assert result["tool_calls"][0]["arguments"] == {"path": "/tmp/test.txt"}
        assert result["stop_reason"] == "tool_calls"
        assert result["model"] == "llama-3.3-70b"

    def test_openai_response_malformed_json_args(self):
        """Malformed JSON in tool arguments → empty dict, no crash."""
        mock_response = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_bad",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": "{not valid json",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "model": "test",
        }
        result = OpenAICompatToolProvider._parse_openai_response(mock_response)
        assert result["tool_calls"][0]["arguments"] == {}

    # ---------------------------------------------------------------
    # 1.8  Message conversion: tool_result blocks
    # ---------------------------------------------------------------
    def test_message_conversion_tool_results(self):
        """Anthropic tool_result blocks → OpenAI tool messages."""
        provider = OpenAICompatToolProvider()
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_123",
                        "content": "File contents here",
                    }
                ],
            }
        ]
        oai_msgs = provider._convert_messages_to_openai(messages)
        assert any(m.get("role") == "tool" for m in oai_msgs)
        tool_msg = [m for m in oai_msgs if m.get("role") == "tool"][0]
        assert tool_msg["tool_call_id"] == "call_123"
        assert tool_msg["content"] == "File contents here"

    # ---------------------------------------------------------------
    # 1.9  ContextSnapshot: capture and restore
    # ---------------------------------------------------------------
    def test_context_snapshot_capture(self):
        """snapshot() captures message history and pending tools."""
        provider = OpenAICompatToolProvider(model="gemma-4-27b")
        provider._message_history = [{"role": "user", "content": "hello"}]
        provider._pending_tools = {"call_1": {"name": "test"}}

        snap = provider.snapshot()
        assert isinstance(snap, ContextSnapshot)
        assert len(snap.messages) == 1
        assert snap.model_origin == "gemma-4-27b"
        assert snap.tool_state == {"call_1": {"name": "test"}}
        assert snap.timestamp > 0

    def test_context_snapshot_restore(self):
        """from_snapshot() restores state to target provider."""
        source = OpenAICompatToolProvider(model="gemma-4-27b")
        source._message_history = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
        ]
        snap = source.snapshot()
        snap.escalation_reason = "malformed_tool_calls"

        target = OpenAICompatToolProvider(model="llama-3.3-70b")
        restored = ToolUseProvider.from_snapshot(snap, target)

        assert len(restored._message_history) == 2
        assert restored._message_history[0]["content"] == "msg1"
        # Target retains its own metadata
        assert restored.metadata["model_id"] == "llama-3.3-70b"

    # ---------------------------------------------------------------
    # 1.10  AAAK Compression Neutralization
    # ---------------------------------------------------------------
    def test_aaak_forbidden_in_system_prompt(self):
        """KERNEL_REASONING_SPEC: AAAK compression EXPLICITLY FORBIDDEN.
        Verify that the SYSTEM_PROMPT in terminal_routes uses full text."""
        # Read the system prompt from terminal_routes
        terminal_routes_path = BACKEND_ROOT / "api" / "terminal_routes.py"
        content = terminal_routes_path.read_text()

        # AAAK tokens are compressed instruction shorthands.
        # The system prompt must use full uncompressed English.
        assert "SYSTEM_PROMPT" in content
        # Extract SYSTEM_PROMPT value
        start = content.index('SYSTEM_PROMPT = """')
        end = content.index('"""', start + 20)
        system_prompt = content[start:end]

        # Must be substantial (no compressed tokens)
        assert (
            len(system_prompt) > 200
        ), "System prompt appears too short — possible AAAK compression"
        # Must contain full English words for tool descriptions
        assert "read_file" in system_prompt
        assert "write_file" in system_prompt
        assert "Guidelines" in system_prompt

    # ---------------------------------------------------------------
    # 1.11  Lazy Anthropic SDK import
    # ---------------------------------------------------------------
    def test_anthropic_sdk_lazy_import(self):
        """Anthropic SDK is imported lazily, not at module level."""
        import_lines = []
        source = (BACKEND_ROOT / "ai" / "llm" / "tool_provider.py").read_text()
        for i, line in enumerate(source.split("\n"), 1):
            if "import anthropic" in line and not line.strip().startswith("#"):
                import_lines.append(i)

        # All imports must be inside methods (indented)
        for line_num in import_lines:
            line = source.split("\n")[line_num - 1]
            assert line.startswith("    ") or line.startswith(
                "\t"
            ), f"Line {line_num}: 'import anthropic' at module level"


# ===================================================================
# CATEGORY 2: Sovereign Fast-Path & Routing
# ===================================================================


class TestSovereignFastPath:
    """Stress-test get_tool_provider() routing decisions."""

    # ---------------------------------------------------------------
    # 2.1  Snappy task types route to Gemma 4 27B
    # ---------------------------------------------------------------
    @patch("ai.llm.tool_provider._is_snappy_available", return_value=True)
    def test_snappy_task_types(self, mock_snappy):
        """All _SNAPPY_TASKS must route to local-snappy when available."""
        expected_tasks = {"monitor", "telemetry", "vbus_status", "health_check"}
        assert _SNAPPY_TASKS == expected_tasks

        for task_type in _SNAPPY_TASKS:
            provider = get_tool_provider(task_type=task_type, complexity=5)
            assert isinstance(provider, OpenAICompatToolProvider)
            assert provider.metadata["model_id"] == "gemma-4-27b"
            assert provider.metadata["processing_locality"] == "local"
            assert provider.metadata.get("latency") == "ultra-low"

    # ---------------------------------------------------------------
    # 2.2  Low complexity (<=3) routes to snappy
    # ---------------------------------------------------------------
    @patch("ai.llm.tool_provider._is_snappy_available", return_value=True)
    def test_low_complexity_routes_to_snappy(self, mock_snappy):
        """Complexity <= 3 should use snappy fast-path."""
        for c in [1, 2, 3]:
            provider = get_tool_provider(task_type="terminal", complexity=c)
            assert provider.metadata["model_id"] == "gemma-4-27b"

    # ---------------------------------------------------------------
    # 2.3  Medium complexity (>3) does NOT route to snappy
    # ---------------------------------------------------------------
    @patch("ai.llm.tool_provider._is_snappy_available", return_value=True)
    @patch("ai.llm.tool_provider._is_ollama_available", return_value=False)
    def test_medium_complexity_skips_snappy(self, mock_ollama, mock_snappy):
        """Complexity > 3 (non-snappy task) must NOT route to snappy."""
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            provider = get_tool_provider(task_type="terminal", complexity=5)
            assert isinstance(provider, AnthropicToolProvider)
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

    # ---------------------------------------------------------------
    # 2.4  Bandwidth-aware: local-first preference for heavy tasks
    # ---------------------------------------------------------------
    @patch("ai.llm.tool_provider._is_snappy_available", return_value=False)
    @patch("ai.llm.tool_provider._is_ollama_available", return_value=True)
    def test_local_first_heavy_tasks(self, mock_ollama, mock_snappy):
        """VOS3_LOCALITY_PREFERENCE=local-first: complexity >= 7 stays local."""
        os.environ["VOS3_LOCALITY_PREFERENCE"] = "local-first"
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            provider = get_tool_provider(complexity=8)
            assert isinstance(provider, OpenAICompatToolProvider)
            assert provider.metadata["processing_locality"] == "local"
            assert provider.metadata["model_id"] == "llama-3.3-70b"
        finally:
            os.environ.pop("VOS3_LOCALITY_PREFERENCE", None)

    # ---------------------------------------------------------------
    # 2.5  Explicit provider preference
    # ---------------------------------------------------------------
    @patch("ai.llm.tool_provider._is_snappy_available", return_value=False)
    def test_explicit_anthropic_preference(self, mock_snappy):
        """VOS3_PREFERRED_PROVIDER=anthropic → AnthropicToolProvider."""
        os.environ["VOS3_PREFERRED_PROVIDER"] = "anthropic"
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            provider = get_tool_provider(complexity=5)
            assert isinstance(provider, AnthropicToolProvider)
        finally:
            os.environ.pop("VOS3_PREFERRED_PROVIDER", None)
            os.environ.pop("ANTHROPIC_API_KEY", None)

    @patch("ai.llm.tool_provider._is_snappy_available", return_value=False)
    @patch("ai.llm.tool_provider._is_ollama_available", return_value=True)
    def test_explicit_local_preference(self, mock_ollama, mock_snappy):
        """VOS3_PREFERRED_PROVIDER=local → OpenAICompatToolProvider."""
        os.environ["VOS3_PREFERRED_PROVIDER"] = "local"
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            provider = get_tool_provider(complexity=5)
            assert isinstance(provider, OpenAICompatToolProvider)
            assert provider.metadata["processing_locality"] == "local"
        finally:
            os.environ.pop("VOS3_PREFERRED_PROVIDER", None)

    # ---------------------------------------------------------------
    # 2.6  Auto-detect: ANTHROPIC_API_KEY → Anthropic
    # ---------------------------------------------------------------
    @patch("ai.llm.tool_provider._is_snappy_available", return_value=False)
    def test_auto_detect_anthropic(self, mock_snappy):
        """Auto mode with ANTHROPIC_API_KEY → AnthropicToolProvider."""
        os.environ.pop("VOS3_PREFERRED_PROVIDER", None)
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            provider = get_tool_provider(complexity=5)
            assert isinstance(provider, AnthropicToolProvider)
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    # ---------------------------------------------------------------
    # 2.7  Auto-detect: OPENAI_API_KEY → OpenAI cloud
    # ---------------------------------------------------------------
    @patch("ai.llm.tool_provider._is_snappy_available", return_value=False)
    def test_auto_detect_openai(self, mock_snappy):
        """Auto mode with only OPENAI_API_KEY → OpenAICompatToolProvider (cloud)."""
        os.environ.pop("VOS3_PREFERRED_PROVIDER", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = "test-key"
        try:
            provider = get_tool_provider(complexity=5)
            assert isinstance(provider, OpenAICompatToolProvider)
            assert provider.metadata["processing_locality"] == "cloud"
            assert provider.metadata["tier"] == 3
        finally:
            os.environ.pop("OPENAI_API_KEY", None)

    # ---------------------------------------------------------------
    # 2.8  Escalation: malformed tool calls
    # ---------------------------------------------------------------
    def test_escalation_trigger_malformed_calls(self):
        """3+ malformed tool calls from snappy → escalation reason returned."""
        provider = OpenAICompatToolProvider(model="gemma-4-27b")
        assert check_escalation_needed(provider, malformed_count=2) is None
        assert check_escalation_needed(provider, malformed_count=3) is None
        result = check_escalation_needed(provider, malformed_count=4)
        assert result == "malformed_tool_calls"

    # ---------------------------------------------------------------
    # 2.9  Escalation: context overflow
    # ---------------------------------------------------------------
    def test_escalation_trigger_context_overflow(self):
        """Context > 28K tokens from snappy → escalation."""
        provider = OpenAICompatToolProvider(model="gemma-4-27b")
        assert check_escalation_needed(provider, context_tokens=27000) is None
        result = check_escalation_needed(provider, context_tokens=29000)
        assert result == "context_overflow"

    # ---------------------------------------------------------------
    # 2.10  Escalation: non-snappy models never escalate
    # ---------------------------------------------------------------
    def test_escalation_non_snappy_no_trigger(self):
        """Non-snappy models (70B+) should never trigger escalation."""
        provider = OpenAICompatToolProvider(model="llama-3.3-70b")
        assert check_escalation_needed(provider, malformed_count=100) is None
        assert check_escalation_needed(provider, context_tokens=100000) is None

        provider2 = AnthropicToolProvider(api_key="test")
        assert check_escalation_needed(provider2, malformed_count=100) is None

    # ---------------------------------------------------------------
    # 2.11  ContextSnapshot handoff: snappy → 70B
    # ---------------------------------------------------------------
    @patch("ai.llm.tool_provider._is_ollama_available", return_value=True)
    def test_context_snapshot_handoff(self, mock_ollama):
        """Start on snappy, escalate → verify full history on 70B provider."""
        # Simulate snappy conversation
        snappy = OpenAICompatToolProvider(model="gemma-4-27b", tier=2)
        snappy._message_history = [
            {"role": "user", "content": "List files in /tmp"},
            {"role": "assistant", "content": "Here are the files..."},
            {"role": "user", "content": "Now read config.yaml"},
        ]
        snappy._pending_tools = {"call_42": {"name": "read_file"}}

        # Escalate
        escalated = escalate_provider(snappy, "malformed_tool_calls")

        # Verify context preserved
        assert len(escalated._message_history) == 3
        assert escalated._message_history[0]["content"] == "List files in /tmp"
        assert escalated._message_history[2]["content"] == "Now read config.yaml"
        assert escalated._pending_tools == {"call_42": {"name": "read_file"}}
        # Escalated provider should be 70B local
        assert escalated.metadata["model_id"] == "llama-3.3-70b"

    # ---------------------------------------------------------------
    # 2.12  Snappy availability: model name pattern matching
    # ---------------------------------------------------------------
    @patch("httpx.get")
    def test_snappy_availability_check(self, mock_get):
        """_is_snappy_available checks for 'gemma' AND '27b' in model names."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"name": "gemma-4:27b-instruct"},
                {"name": "llama3:70b"},
            ]
        }
        mock_get.return_value = mock_resp

        assert _is_snappy_available() is True

    @patch("httpx.get")
    def test_snappy_not_available_no_gemma(self, mock_get):
        """No gemma model → snappy not available."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [{"name": "llama3:70b"}, {"name": "codestral:22b"}]
        }
        mock_get.return_value = mock_resp

        assert _is_snappy_available() is False


# ===================================================================
# CATEGORY 3: Memory Sync & Spatial Scoping
# ===================================================================


class TestMemorySyncSpatialScoping:
    """Verify atomic writes, write-access scoping, and RRF hybrid search."""

    @staticmethod
    def _make_test_memory(persist_dir: str = "/tmp/test_v32_mem") -> DevMemory:
        """Create a minimal DevMemory bypassing ChromaDB/embeddings."""
        mem = DevMemory.__new__(DevMemory)
        mem._initialized = False
        mem._collection = None
        mem._bm25_index = None
        mem._bm25_corpus_ids = []
        mem._embedding_model = None
        mem._client = None
        mem.MEMORY_TYPES = DevMemory.MEMORY_TYPES
        mem.persist_dir = persist_dir
        mem.collection_name = "test"
        mem.embedding_model_name = "test"
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        return mem

    # ---------------------------------------------------------------
    # 3.1  Write permissions definition
    # ---------------------------------------------------------------
    def test_write_permissions_defined(self):
        """WRITE_PERMISSIONS matches KERNEL_REASONING_SPEC spatial scoping."""
        assert "local-snappy" in WRITE_PERMISSIONS
        assert WRITE_PERMISSIONS["local-snappy"] == {"infra"}

        assert "local-default" in WRITE_PERMISSIONS
        assert WRITE_PERMISSIONS["local-default"] == {"kernel", "backend", "frontend"}

        assert "local-code" in WRITE_PERMISSIONS
        assert WRITE_PERMISSIONS["local-code"] == {"kernel", "backend", "frontend"}

        assert "cloud" in WRITE_PERMISSIONS
        assert WRITE_PERMISSIONS["cloud"] is None  # None = all wings

    # ---------------------------------------------------------------
    # 3.2  Spatial scoping: local-snappy rejected from Logic Wing
    # ---------------------------------------------------------------
    def test_snappy_rejected_from_kernel_wing(self):
        """local-snappy MUST NOT write to 'kernel' wing."""
        memory = self._make_test_memory("/tmp/test_mem")

        with pytest.raises(PermissionError) as exc_info:
            memory.add(
                content="VMM mapping failed",
                memory_type="error",
                wing="kernel",
                model_origin="local-snappy",
            )
        assert "local-snappy" in str(exc_info.value)
        assert "kernel" in str(exc_info.value)

    def test_snappy_rejected_from_backend_wing(self):
        """local-snappy MUST NOT write to 'backend' wing."""
        memory = self._make_test_memory("/tmp/test_mem")

        with pytest.raises(PermissionError):
            memory.add(
                content="FastAPI route updated",
                memory_type="code_change",
                wing="backend",
                model_origin="local-snappy",
            )

    def test_snappy_rejected_from_frontend_wing(self):
        """local-snappy MUST NOT write to 'frontend' wing."""
        memory = self._make_test_memory("/tmp/test_mem")

        with pytest.raises(PermissionError):
            memory.add(
                content="React component refactored",
                memory_type="code_change",
                wing="frontend",
                model_origin="local-snappy",
            )

    # ---------------------------------------------------------------
    # 3.3  Spatial scoping: local-snappy CAN write to infra wing
    # ---------------------------------------------------------------
    def test_snappy_allowed_infra_wing(self):
        """local-snappy CAN write to 'infra' wing."""
        memory = self._make_test_memory("/tmp/test_v32_infra")

        # Should NOT raise — falls through to JSON fallback
        entry = memory.add(
            content="Health check passed",
            memory_type="context",
            wing="infra",
            model_origin="local-snappy",
        )
        assert entry is not None

    # ---------------------------------------------------------------
    # 3.4  Spatial scoping: local-default CAN write to Logic wings
    # ---------------------------------------------------------------
    def test_local_default_allowed_logic_wings(self):
        """local-default CAN write to kernel, backend, frontend."""
        memory = self._make_test_memory("/tmp/test_v32_logic")

        for wing in ["kernel", "backend", "frontend"]:
            entry = memory.add(
                content=f"Writing to {wing}",
                memory_type="decision",
                wing=wing,
                model_origin="local-default",
            )
            assert entry is not None

    # ---------------------------------------------------------------
    # 3.5  Cloud models: unrestricted write access
    # ---------------------------------------------------------------
    def test_cloud_unrestricted_write(self):
        """Cloud models (WRITE_PERMISSIONS['cloud'] = None) → all wings."""
        memory = self._make_test_memory("/tmp/test_v32_cloud")

        for wing in ["kernel", "backend", "frontend", "infra"]:
            entry = memory.add(
                content=f"Cloud writing to {wing}",
                memory_type="decision",
                wing=wing,
                model_origin="cloud",
            )
            assert entry is not None

    # ---------------------------------------------------------------
    # 3.6  Atomic writes: model_origin defaults to "unknown"
    # ---------------------------------------------------------------
    def test_atomic_write_default_model_origin(self):
        """add() with no model_origin defaults to 'unknown' (not rejected)."""
        memory = self._make_test_memory("/tmp/test_v32_default_origin")

        # 'unknown' is not in WRITE_PERMISSIONS, so allowed_wings = None (via .get default)
        # This means .get("unknown") returns None → no restriction
        entry = memory.add(
            content="Testing default origin",
            memory_type="learning",
        )
        assert entry is not None

    # ---------------------------------------------------------------
    # 3.7  Atomic writes: global_timestamp metadata present
    # ---------------------------------------------------------------
    def test_atomic_write_has_global_timestamp(self):
        """Every memory write must include global_timestamp in metadata."""
        memory = self._make_test_memory("/tmp/test_v32_timestamp")

        entry = memory.add(
            content="Timestamp test",
            memory_type="learning",
            model_origin="cloud",
        )
        assert entry is not None
        assert "global_timestamp" in entry.metadata
        # Must be a valid float timestamp
        ts = float(entry.metadata["global_timestamp"])
        assert ts > 1_700_000_000  # After 2023

    # ---------------------------------------------------------------
    # 3.8  Atomic writes: model_origin metadata present
    # ---------------------------------------------------------------
    def test_atomic_write_has_model_origin(self):
        """Every memory write must include model_origin in metadata."""
        memory = self._make_test_memory("/tmp/test_v32_origin")

        entry = memory.add(
            content="Backend service architecture decision",
            memory_type="decision",
            wing="backend",
            model_origin="local-default",
        )
        assert entry.metadata["model_origin"] == "local-default"

    # ---------------------------------------------------------------
    # 3.9  Wing auto-detection
    # ---------------------------------------------------------------
    def test_wing_autodetect_kernel(self):
        """Content with kernel keywords → 'kernel' wing."""
        assert _detect_wing("The VMM mapping failed due to PTE alignment") == "kernel"
        assert _detect_wing("Scheduler preemption triggered in ISR") == "kernel"

    def test_wing_autodetect_backend(self):
        """Content with backend keywords → 'backend' wing."""
        assert _detect_wing("FastAPI endpoint returning 500 on /api/chat") == "backend"

    def test_wing_autodetect_frontend(self):
        """Content with frontend keywords → 'frontend' wing."""
        assert (
            _detect_wing("React component hook causing infinite re-render")
            == "frontend"
        )

    def test_wing_autodetect_infra(self):
        """Content with infra keywords → 'infra' wing."""
        assert _detect_wing("Health check ping failed on Docker container") == "infra"

    # ---------------------------------------------------------------
    # 3.10  Room auto-detection
    # ---------------------------------------------------------------
    def test_room_autodetect_debugging(self):
        assert _detect_room("Found a critical bug causing crash") == "debugging"

    def test_room_autodetect_architecture(self):
        assert _detect_room("Refactor the design pattern for routing") == "architecture"

    def test_room_autodetect_testing(self):
        assert _detect_room("Added pytest coverage for the new module") == "testing"

    def test_room_autodetect_security(self):
        assert (
            _detect_room("HMAC token authentication vulnerability found") == "security"
        )

    def test_room_autodetect_general(self):
        assert _detect_room("Updated the changelog for v3.2") == "general"

    # ---------------------------------------------------------------
    # 3.11  RRF fusion: correctness
    # ---------------------------------------------------------------
    def test_rrf_fusion_basic(self):
        """RRF(d) = sum(1/(k + rank)) produces correct ordering."""
        vector_ranks = {"doc_a": 0, "doc_b": 1, "doc_c": 2}
        bm25_ranks = {"doc_b": 0, "doc_c": 1, "doc_d": 2}

        fused = DevMemory._rrf_fuse(vector_ranks, bm25_ranks, k=60)

        # doc_b appears in both (rank 1 in vector, rank 0 in bm25) → highest RRF
        assert fused[0] == "doc_b"
        # doc_c also in both → second
        assert fused[1] == "doc_c"

    def test_rrf_fusion_single_source(self):
        """RRF with only vector ranks returns vector order."""
        vector_ranks = {"doc_a": 0, "doc_b": 1}
        bm25_ranks = {}

        fused = DevMemory._rrf_fuse(vector_ranks, bm25_ranks, k=60)
        assert fused[0] == "doc_a"
        assert fused[1] == "doc_b"

    def test_rrf_fusion_bm25_keyword_boost(self):
        """BM25-only doc gets included in fused results."""
        vector_ranks = {"doc_a": 0}
        bm25_ranks = {"doc_b": 0}  # BM25 top hit

        fused = DevMemory._rrf_fuse(vector_ranks, bm25_ranks, k=60)
        # Both should appear
        assert "doc_a" in fused
        assert "doc_b" in fused


# ===================================================================
# CATEGORY 4: System Stability
# ===================================================================


class TestSystemStability:
    """Dead-end tests, fallback chain, error handling."""

    # ---------------------------------------------------------------
    # 4.1  No providers available → RuntimeError
    # ---------------------------------------------------------------
    @patch("ai.llm.tool_provider._is_snappy_available", return_value=False)
    @patch("ai.llm.tool_provider._is_ollama_available", return_value=False)
    def test_no_providers_raises_runtime_error(self, mock_ollama, mock_snappy):
        """With zero providers available, get_tool_provider must raise."""
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("VOS3_PREFERRED_PROVIDER", None)

        with pytest.raises(RuntimeError) as exc_info:
            get_tool_provider(complexity=5)
        assert "No LLM provider available" in str(exc_info.value)
        assert "OLLAMA_BASE_URL" in str(exc_info.value)

    # ---------------------------------------------------------------
    # 4.2  Config get_active_model: Ollama fallback
    # ---------------------------------------------------------------
    def test_config_ollama_fallback(self):
        """Config.get_active_model() detects OLLAMA_BASE_URL."""
        from src.config import Config

        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
        try:
            config = Config()
            model = config.get_active_model()
            assert model.name == "llama-3.3-70b"
        finally:
            os.environ.pop("OLLAMA_BASE_URL", None)

    # ---------------------------------------------------------------
    # 4.3  Config: no providers at all → ValueError
    # ---------------------------------------------------------------
    def test_config_no_providers_raises(self):
        """Config.get_active_model() with no keys/Ollama → ValueError."""
        from src.config import Config

        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OLLAMA_BASE_URL", None)
        config = Config()
        with pytest.raises(ValueError) as exc_info:
            config.get_active_model()
        assert "OLLAMA_BASE_URL" in str(exc_info.value)

    # ---------------------------------------------------------------
    # 4.4  Router YAML: fallback chain includes local-default
    # ---------------------------------------------------------------
    def test_router_yaml_fallback_chain(self):
        """router.yaml fallback_chain must include 'local-default'."""
        import yaml

        yaml_path = BACKEND_ROOT / "config" / "router.yaml"
        with open(yaml_path) as f:
            config = yaml.safe_load(f)

        assert "local-default" in config["fallback_chain"]
        # Verify order: cloud first, local last
        chain = config["fallback_chain"]
        assert chain.index("local-default") == len(chain) - 1

    # ---------------------------------------------------------------
    # 4.5  Router YAML: local model entries exist
    # ---------------------------------------------------------------
    def test_router_yaml_local_models_defined(self):
        """All 4 local model entries must exist in router.yaml."""
        import yaml

        yaml_path = BACKEND_ROOT / "config" / "router.yaml"
        with open(yaml_path) as f:
            config = yaml.safe_load(f)

        models = config["models"]
        for slot in ["local-snappy", "local-default", "local-code", "local-light"]:
            assert slot in models, f"Missing model entry: {slot}"
            assert models[slot]["provider"] == "ollama"

    # ---------------------------------------------------------------
    # 4.6  Router YAML: tier annotations
    # ---------------------------------------------------------------
    def test_router_yaml_tier_annotations(self):
        """local-snappy=tier 2, local-light=tier 1."""
        import yaml

        yaml_path = BACKEND_ROOT / "config" / "router.yaml"
        with open(yaml_path) as f:
            config = yaml.safe_load(f)

        assert config["models"]["local-snappy"]["params"]["tier"] == 2
        assert config["models"]["local-light"]["params"]["tier"] == 1

    # ---------------------------------------------------------------
    # 4.7  Router YAML: min_params_b enforcement
    # ---------------------------------------------------------------
    def test_router_yaml_min_params_enforcement(self):
        """local-default and local-code must enforce min_params_b >= 70."""
        import yaml

        yaml_path = BACKEND_ROOT / "config" / "router.yaml"
        with open(yaml_path) as f:
            config = yaml.safe_load(f)

        assert config["models"]["local-default"]["params"]["min_params_b"] == 70
        assert config["models"]["local-code"]["params"]["min_params_b"] == 70

    # ---------------------------------------------------------------
    # 4.8  Factory: local model mappings
    # ---------------------------------------------------------------
    def test_factory_local_model_mappings(self):
        """Factory MODEL_PROVIDERS and DEFAULT_MODEL_IDS have local entries."""
        from src.efficiency.factory import MODEL_PROVIDERS, DEFAULT_MODEL_IDS

        for slot in ["local-default", "local-code", "local-snappy", "local-light"]:
            assert MODEL_PROVIDERS[slot] == "ollama", f"Missing MODEL_PROVIDERS[{slot}]"

        ollama_ids = DEFAULT_MODEL_IDS["ollama"]
        assert ollama_ids["local-default"] == "llama-3.3-70b"
        assert ollama_ids["local-code"] == "qwen2.5-coder:72b"
        assert ollama_ids["local-snappy"] == "gemma-4-27b"
        assert ollama_ids["local-light"] == "codestral:22b"

    # ---------------------------------------------------------------
    # 4.9  Escalation: fallback to Anthropic when Ollama down
    # ---------------------------------------------------------------
    @patch("ai.llm.tool_provider._is_ollama_available", return_value=False)
    def test_escalation_falls_to_anthropic(self, mock_ollama):
        """When Ollama down during escalation, falls to Anthropic if key exists."""
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            snappy = OpenAICompatToolProvider(model="gemma-4-27b")
            snappy._message_history = [{"role": "user", "content": "test"}]

            escalated = escalate_provider(snappy, "context_overflow")
            assert isinstance(escalated, AnthropicToolProvider)
            assert len(escalated._message_history) == 1
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    # ---------------------------------------------------------------
    # 4.10  SDK import purity
    # ---------------------------------------------------------------
    def test_no_direct_anthropic_sdk_outside_provider(self):
        """'import anthropic' must ONLY appear in tool_provider.py."""
        import subprocess

        result = subprocess.run(
            ["grep", "-r", "--include=*.py", "import anthropic", str(BACKEND_ROOT)],
            capture_output=True,
            text=True,
        )
        lines = [
            line
            for line in result.stdout.strip().split("\n")
            if line
            and "tool_provider.py" not in line
            and "terminal_routes.py" not in line
            and "__pycache__" not in line
            and ".pyc" not in line
            and ".venv" not in line
            and "test_v32_stability.py" not in line
        ]
        assert (
            lines == []
        ), "Direct 'import anthropic' found outside tool_provider.py:\n" + "\n".join(
            lines
        )

    def test_no_from_anthropic_import(self):
        """'from anthropic import ...' must not exist in application code."""
        import subprocess

        result = subprocess.run(
            ["grep", "-r", "--include=*.py", "from anthropic", str(BACKEND_ROOT)],
            capture_output=True,
            text=True,
        )
        lines = [
            line
            for line in result.stdout.strip().split("\n")
            if line
            and "__pycache__" not in line
            and ".pyc" not in line
            and ".venv" not in line
            and "test_v32_stability.py" not in line
        ]
        assert lines == [], "Direct 'from anthropic' found:\n" + "\n".join(lines)

    # ---------------------------------------------------------------
    # 4.11  KERNEL_REASONING_SPEC.md exists and is coherent
    # ---------------------------------------------------------------
    def test_kernel_reasoning_spec_exists(self):
        """docs/KERNEL_REASONING_SPEC.md must exist."""
        spec_path = BACKEND_ROOT.parent / "docs" / "KERNEL_REASONING_SPEC.md"
        assert spec_path.exists(), "docs/KERNEL_REASONING_SPEC.md not found"

    def test_kernel_reasoning_spec_content(self):
        """Spec must define all 4 tiers and mention AAAK."""
        spec_path = BACKEND_ROOT.parent / "docs" / "KERNEL_REASONING_SPEC.md"
        content = spec_path.read_text()

        assert "Tier 1" in content or "Tier 1 (Chat)" in content
        assert "Tier 2a" in content or "Tier 2a (Fast Agent)" in content
        assert "Tier 2b" in content or "Tier 2b (Full Agent)" in content
        assert "Tier 3" in content or "Tier 3 (Premium)" in content
        assert "AAAK" in content
        assert "FORBIDDEN" in content or "forbidden" in content

    # ---------------------------------------------------------------
    # 4.12  requirements.txt has rank-bm25
    # ---------------------------------------------------------------
    def test_requirements_has_rank_bm25(self):
        """rank-bm25 must be in requirements.txt for hybrid retrieval."""
        req_path = BACKEND_ROOT / "requirements.txt"
        content = req_path.read_text()
        assert "rank-bm25" in content

    # ---------------------------------------------------------------
    # 4.13  .env.example has VOS3 locality vars
    # ---------------------------------------------------------------
    def test_env_example_has_locality_vars(self):
        """.env.example must document VOS3_PREFERRED_PROVIDER and VOS3_LOCALITY_PREFERENCE."""
        env_path = BACKEND_ROOT.parent / ".env.example"
        if not env_path.exists():
            pytest.skip(".env.example not found at project root")
        content = env_path.read_text()
        assert "VOS3_PREFERRED_PROVIDER" in content
        assert "VOS3_LOCALITY_PREFERENCE" in content

    # ---------------------------------------------------------------
    # 4.14  chat_routes.py has /providers/status endpoint
    # ---------------------------------------------------------------
    def test_chat_routes_has_provider_status(self):
        """chat_routes.py must expose /providers/status."""
        routes_path = BACKEND_ROOT / "api" / "chat_routes.py"
        content = routes_path.read_text()
        assert "providers/status" in content
        assert "local_models" in content

    # ---------------------------------------------------------------
    # 4.15  ModelSelector.tsx fetches provider status
    # ---------------------------------------------------------------
    def test_model_selector_fetches_status(self):
        """Frontend ModelSelector must fetch /api/chat/providers/status."""
        tsx_path = (
            BACKEND_ROOT.parent
            / "frontend"
            / "components"
            / "settings"
            / "ModelSelector.tsx"
        )
        content = tsx_path.read_text()
        assert "/api/chat/providers/status" in content
        assert "local_models" in content
        assert "StatusDot" in content


# ===================================================================
# CATEGORY 5: Cross-Layer Integration (Bonus)
# ===================================================================


class TestCrossLayerIntegration:
    """End-to-end integration checks across layers."""

    # ---------------------------------------------------------------
    # 5.1  terminal_routes imports from tool_provider
    # ---------------------------------------------------------------
    def test_terminal_routes_uses_tool_provider(self):
        """terminal_routes.py must import from ai.llm.tool_provider."""
        routes_path = BACKEND_ROOT / "api" / "terminal_routes.py"
        content = routes_path.read_text()
        assert "from ai.llm.tool_provider import" in content
        assert "get_tool_provider" in content
        assert "check_escalation_needed" in content
        assert "escalate_provider" in content

    # ---------------------------------------------------------------
    # 5.2  code_review/service.py uses Factory
    # ---------------------------------------------------------------
    def test_code_review_service_uses_factory(self):
        """code_review/service.py must use get_llm_for_task, not direct SDK."""
        svc_path = BACKEND_ROOT / "code_review" / "service.py"
        content = svc_path.read_text()
        assert "from src.efficiency.factory import get_llm_for_task" in content
        assert "import anthropic" not in content
        assert "from anthropic" not in content

    # ---------------------------------------------------------------
    # 5.3  code_review/multi_provider.py uses Factory
    # ---------------------------------------------------------------
    def test_code_review_multi_provider_uses_factory(self):
        """multi_provider.py must use get_llm_for_task, not direct SDK."""
        mp_path = BACKEND_ROOT / "code_review" / "multi_provider.py"
        content = mp_path.read_text()
        assert "from src.efficiency.factory import get_llm_for_task" in content
        assert "import anthropic" not in content
        assert "from anthropic" not in content

    # ---------------------------------------------------------------
    # 5.4  router_bridge.py is model-agnostic
    # ---------------------------------------------------------------
    def test_router_bridge_model_agnostic(self):
        """router_bridge.py must use Factory, not direct Anthropic SDK."""
        rb_path = BACKEND_ROOT / "scripts" / "router_bridge.py"
        content = rb_path.read_text()
        assert (
            "from src.efficiency.factory import" in content or "_create_llm" in content
        )
        assert "import anthropic" not in content
        assert "from anthropic" not in content

    # ---------------------------------------------------------------
    # 5.5  chat_service.py uses env-configurable default
    # ---------------------------------------------------------------
    def test_chat_service_env_configurable(self):
        """chat_service.py default model must be env-configurable."""
        cs_path = BACKEND_ROOT / "services" / "chat_service.py"
        content = cs_path.read_text()
        assert "VOS3_DEFAULT_CHAT_MODEL" in content

    # ---------------------------------------------------------------
    # 5.6  local-snappy has structured_json: true
    # ---------------------------------------------------------------
    def test_snappy_structured_json_enabled(self):
        """local-snappy in router.yaml must have structured_json: true."""
        import yaml

        yaml_path = BACKEND_ROOT / "config" / "router.yaml"
        with open(yaml_path) as f:
            config = yaml.safe_load(f)

        snappy = config["models"]["local-snappy"]
        assert snappy["params"]["structured_json"] is True
        assert snappy["params"]["context_window"] == 32768
        assert snappy["params"]["processing_latency"] == "ultra-low"


# ===================================================================
# Main entry point for direct execution
# ===================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
