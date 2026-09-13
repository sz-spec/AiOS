#!/usr/bin/env python3
"""
Router Bridge - Convex-Powered Model Router
============================================
Uses Convex cloud database as the primary source for model routing.
Requires CONVEX_URL environment variable.

Usage:
    Interactive Mode:
        python router_bridge.py
        # Send JSON commands via stdin:
        # {"command": "get_model", "params": {"role": "coding", "complexity": 5}}
        # {"command": "list_models"}
        # {"command": "config"}
        # {"command": "exit"}

    CLI Mode (backward compatible):
        python router_bridge.py get_model <role> <complexity>
        python router_bridge.py list_models
        python router_bridge.py config

Environment:
    CONVEX_URL - Convex deployment URL (required)
"""

import sys
import json
import os
from convex import ConvexClient  # Version 0.7.0

# Convex deployment URL
CONVEX_URL = os.getenv("CONVEX_URL")

# ============================================================================
# SECURITY: FILE PATH ALLOWLIST (February 2026)
# ============================================================================
# Defense in depth - validate paths at both TypeScript AND Python layers.
# Agents can only write to these directory prefixes.

ALLOWED_PREFIXES = [
    "backend/convex/",
    "backend/api/",
    "backend/src/",
    "backend/tests/",
    "frontend/src/",
]

# ============================================================================
# STATIC FALLBACK MODELS (February 2026 Stack)
# Used when Convex query fails
# ============================================================================

FALLBACK_MODELS = {
    # Manager model for lightweight orchestration (dispatcher, aggregator, finalize)
    # Liquid LFM 2.5 - 359 tokens/sec, SOTA SLM for routing tasks
    "manager": {
        "modelId": "liquid-lfm-2.5-1.2b",
        "provider": "liquid",  # Via OpenRouter or local Ollama
        "thinkingMode": False,
        "maxTokens": 4096,
        "temperature": 0.3,  # Low temp for deterministic routing
        "supportsStreaming": True,
        "tokensPerSec": 359,
        "source": "SOTA_SLM_2026",
    },
    "architect": {
        "modelId": "gpt-5.2-pro",
        "provider": "openai",
        "thinkingMode": True,
        "maxTokens": 16384,
        "temperature": 0.7,
        "supportsStreaming": True,
    },
    "coding": {
        "modelId": os.getenv(
            "VOS3_FALLBACK_MODEL", "claude-3-opus-20260210"
        ),  # Opus 4.6
        "provider": "anthropic",
        "thinkingMode": False,
        "maxTokens": 16384,  # Extended output support
        "temperature": 0.7,
        "supportsStreaming": True,
    },
    "reviewer": {
        "modelId": "gpt-5.2",
        "provider": "openai",
        "thinkingMode": False,
        "maxTokens": 8192,
        "temperature": 0.7,
        "supportsStreaming": True,
    },
    "researcher": {
        "modelId": "gemini-3-pro-latest",
        "provider": "google",
        "thinkingMode": False,
        "maxTokens": 32768,
        "temperature": 0.7,
        "supportsStreaming": True,
    },
    "tester": {
        "modelId": "gemini-3-flash-latest",
        "provider": "google",
        "thinkingMode": False,
        "maxTokens": 8192,
        "temperature": 0.5,
        "supportsStreaming": True,
    },
    # Mistral 7B v4 - "The Runner Up" for context escalation
    # Used when Liquid LFM context limit (32K) is exceeded
    "mistral": {
        "modelId": "mistral-7b-v4-instruct",
        "provider": "mistral",
        "thinkingMode": False,
        "maxTokens": 32768,
        "temperature": 0.3,
        "supportsStreaming": True,
        "contextLimit": 128000,  # 128K context window
    },
}

COMPLEXITY_THRESHOLD = 7


# ============================================================================
# CONVEX ROUTER CLASS
# ============================================================================


class ConvexRouter:
    def __init__(self, require_convex: bool = True):
        self.client = None
        self.connected = False

        if CONVEX_URL:
            try:
                # ConvexClient 0.7.0 supports automatic reconnection
                self.client = ConvexClient(CONVEX_URL)
                self.connected = True
            except Exception as e:
                if require_convex:
                    print(
                        json.dumps(
                            {
                                "status": "error",
                                "message": f"Convex connection failed: {str(e)}",
                            }
                        ),
                        flush=True,
                    )
                    sys.exit(1)
        elif require_convex:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "message": "CONVEX_URL environment variable is required",
                    }
                ),
                flush=True,
            )
            sys.exit(1)

        print(
            json.dumps(
                {
                    "status": "ready",
                    "mode": "interactive",
                    "version": "0.7.0",
                    "convex_connected": self.connected,
                }
            ),
            flush=True,
        )

    def resolve_routing(self, role: str, complexity: int, state: dict = None) -> dict:
        """
        Get model routing from Convex DB.
        Falls back to static config on error.

        Args:
            role: Task role (architect, coding, reviewer, etc.)
            complexity: Task complexity 1-10
            state: Optional multi-agent state with current_phase, iteration, etc.
        """
        # Extract state information for smarter routing
        state = state or {}
        current_phase = state.get("current_phase", role)
        iteration = state.get("iteration", 0)
        previous_models = state.get("previous_models", [])
        error_count = state.get("error_count", 0)

        # Use phase as role if provided
        effective_role = current_phase if current_phase else role

        # Increase complexity on retries/errors
        effective_complexity = min(10, complexity + error_count)

        if self.connected and self.client:
            try:
                # Query from modelRouting.ts in Convex with full context
                result = self.client.query(
                    "modelRouting:getBestModel",
                    {
                        "role": effective_role,
                        "complexity": effective_complexity,
                        "iteration": iteration,
                        "previousModels": previous_models,
                        "errorCount": error_count,
                    },
                )

                if result:
                    return {
                        "name": result.get("name", effective_role),
                        "model_id": result.get("modelId"),
                        "provider": result.get("provider"),
                        "max_tokens": result.get("maxTokens", 8192),
                        "temperature": result.get("temperature", 0.7),
                        "thinking_mode": result.get("thinkingMode", False),
                        "supports_streaming": result.get("supportsStreaming", True),
                        "complexity_used": effective_complexity,
                        "role_used": effective_role,
                        "iteration": iteration,
                        "source": "convex",
                    }
            except Exception as e:
                print(
                    json.dumps(
                        {
                            "status": "warning",
                            "message": f"Convex query failed: {str(e)}",
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )

        # Fallback to static config
        return self._get_fallback(effective_role, effective_complexity, iteration)

    def _get_fallback(self, role: str, complexity: int, iteration: int = 0) -> dict:
        """Static fallback when Convex is unavailable."""
        # Premium model for high complexity coding
        if complexity >= COMPLEXITY_THRESHOLD and role == "coding":
            model = FALLBACK_MODELS["architect"]
        # Use thinking model for later iterations (self-correction)
        elif iteration >= 2 and role in ["coding", "reviewer"]:
            model = FALLBACK_MODELS["architect"]
        else:
            model = FALLBACK_MODELS.get(role, FALLBACK_MODELS["coding"])

        return {
            "name": role,
            "model_id": model["modelId"],
            "provider": model["provider"],
            "max_tokens": model["maxTokens"],
            "temperature": model["temperature"],
            "thinking_mode": model["thinkingMode"],
            "supports_streaming": model.get("supportsStreaming", True),
            "complexity_used": complexity,
            "role_used": role,
            "iteration": iteration,
            "source": "fallback",
        }

    def resolve_pipeline_routing(self, state: dict) -> dict:
        """
        Pipeline-aware model routing with dynamic escalation and speculative execution.

        Features (February 2026):
        1. Phase-specific complexity boost
        2. Convex-based routing with speculation
        3. Confidence scoring for model selection
        4. Automatic escalation on low confidence or retries

        Args:
            state: PipelineState with current_phase, iteration, complexity, tags, enable_speculation

        Returns:
            Model configuration dict with confidence score
        """
        phase = state.get("current_phase", "coding")
        iteration = state.get("iteration", 0)
        complexity = state.get("complexity", 5)
        tags = state.get("tags", [])
        previous_models = state.get("previous_models", [])
        error_count = state.get("error_count", 0)
        context_length = state.get("context_length", 0)
        enable_speculation = state.get("enable_speculation", False)
        force_confidence = state.get("force_confidence")  # For testing purposes

        # Adjust complexity based on phase requirements
        phase_complexity_boost = {
            "architect": 2,
            "reviewer": 1,
            "finalize": 1,
        }
        effective_complexity = min(
            10, complexity + phase_complexity_boost.get(phase, 0) + error_count
        )

        # Calculate confidence based on multiple factors
        # Or use forced confidence for testing
        if force_confidence is not None:
            confidence = float(force_confidence)
            print(
                json.dumps(
                    {
                        "status": "info",
                        "message": f"[Test Mode] Using forced confidence: {confidence}",
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
        else:
            confidence = self._calculate_confidence(
                phase=phase,
                complexity=effective_complexity,
                iteration=iteration,
                error_count=error_count,
                context_length=context_length,
            )

        # Try Convex first
        if self.connected and self.client:
            try:
                result = self.client.query(
                    "modelRouting:getStepModel",
                    {
                        "phase": phase,
                        "iteration": iteration,
                        "complexity": effective_complexity,
                        "tags": tags,
                        "previousModels": previous_models,
                        "enableSpeculation": enable_speculation,
                    },
                )

                if result:
                    # Dynamic Escalation: upgrade to Opus 4.6 on retry iterations
                    if (
                        iteration > 1
                        and result.get("modelId") != "claude-3-opus-20260210"
                    ):
                        print(
                            json.dumps(
                                {
                                    "status": "info",
                                    "message": f"Escalating to Opus 4.6 on iteration {iteration}",
                                }
                            ),
                            file=sys.stderr,
                            flush=True,
                        )
                        return self._build_escalated_response(
                            phase, iteration, effective_complexity
                        )

                    return {
                        "name": result.get("name", phase),
                        "model_id": result.get("modelId"),
                        "provider": result.get("provider"),
                        "max_tokens": result.get("maxTokens", 16384),
                        "temperature": result.get("temperature", 0.7),
                        "thinking_mode": result.get("thinkingMode", False),
                        "supports_streaming": result.get("supportsStreaming", True),
                        "phase": phase,
                        "complexity_used": effective_complexity,
                        "iteration": iteration,
                        "escalated": False,
                        "confidence": confidence,
                        "source": "convex" if not enable_speculation else "speculative",
                    }

            except Exception as e:
                print(
                    json.dumps(
                        {
                            "status": "warning",
                            "message": f"Convex pipeline query failed: {str(e)}",
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )

        # Fallback with escalation logic
        return self._get_pipeline_fallback(
            phase, iteration, effective_complexity, context_length, confidence
        )

    def _calculate_confidence(
        self,
        phase: str,
        complexity: int,
        iteration: int,
        error_count: int,
        context_length: int,
    ) -> float:
        """
        Calculate confidence score for model selection (February 2026).

        Factors that reduce confidence:
        - High complexity (>7)
        - Multiple iterations (retries)
        - Previous errors
        - Very large context
        - Complex phases (architect, reviewer)

        Returns:
            Confidence score between 0.0 and 1.0
        """
        confidence = 1.0

        # Complexity penalty
        if complexity >= 9:
            confidence -= 0.15
        elif complexity >= 7:
            confidence -= 0.10

        # Iteration penalty (each retry reduces confidence)
        confidence -= iteration * 0.12

        # Error penalty
        confidence -= error_count * 0.08

        # Large context penalty (approaching model limits)
        if context_length > 24000:
            confidence -= 0.10
        elif context_length > 16000:
            confidence -= 0.05

        # Phase-specific adjustments
        if phase in ["architect", "reviewer"]:
            # Complex phases start with lower baseline confidence
            confidence -= 0.05
        elif phase in ["dispatcher", "aggregator", "finalize"]:
            # Orchestration phases are well-understood
            confidence += 0.05

        # Ensure confidence stays in valid range
        return max(0.0, min(1.0, confidence))

    def _build_escalated_response(
        self, phase: str, iteration: int, complexity: int, confidence: float = 1.0
    ) -> dict:
        """Build response for escalated model (Opus 4.6 or GPT-5.2-thinking)."""
        # Use thinking model for complex phases, Opus for coding
        if phase in ["architect", "reviewer", "finalize"]:
            model = FALLBACK_MODELS["architect"]  # gpt-5.2-pro
        else:
            model = FALLBACK_MODELS["coding"]  # claude-3-opus-20260210

        return {
            "name": phase,
            "model_id": model["modelId"],
            "provider": model["provider"],
            "max_tokens": model["maxTokens"],
            "temperature": model["temperature"],
            "thinking_mode": model["thinkingMode"],
            "supports_streaming": model.get("supportsStreaming", True),
            "phase": phase,
            "complexity_used": complexity,
            "iteration": iteration,
            "escalated": True,
            "confidence": confidence,  # Escalated responses have full confidence
            "source": "escalation",
        }

    def _get_pipeline_fallback(
        self,
        phase: str,
        iteration: int,
        complexity: int,
        context_length: int = 0,
        confidence: float = 1.0,
    ) -> dict:
        """
        Pipeline-specific fallback routing with confidence scoring.

        Args:
            phase: Pipeline phase (dispatcher, aggregator, finalize, etc.)
            iteration: Current retry iteration
            complexity: Task complexity 1-10
            context_length: Estimated context size in tokens
            confidence: Confidence score for model selection (0.0-1.0)

        Returns:
            Model configuration dict with confidence score
        """

        # Context limit for Liquid LFM 2.5
        LIQUID_LFM_CONTEXT_LIMIT = 32000

        # Confidence threshold for escalation (February 2026)
        CONFIDENCE_THRESHOLD = 0.85

        # Lightweight phases use Liquid LFM manager model (fast, cheap)
        # But escalate to Mistral 7B if context exceeds 32K
        # Or escalate to Opus if confidence is low
        if phase in ["dispatcher", "aggregator", "finalize"]:
            # Rule 1: Context escalation (32K Rule)
            if context_length > LIQUID_LFM_CONTEXT_LIMIT:
                # Escalate to Mistral 7B ("The Runner Up") for large contexts
                model = FALLBACK_MODELS["mistral"]
                return {
                    "name": phase,
                    "model_id": model["modelId"],
                    "provider": model["provider"],
                    "max_tokens": model["maxTokens"],
                    "temperature": model["temperature"],
                    "thinking_mode": model["thinkingMode"],
                    "supports_streaming": model.get("supportsStreaming", True),
                    "phase": phase,
                    "complexity_used": complexity,
                    "iteration": iteration,
                    "escalated": True,
                    "confidence": confidence,
                    "source": "context_escalation_32k_rule",
                    "context_length": context_length,
                }

            # Rule 2: Confidence escalation (even for orchestration phases)
            if confidence < CONFIDENCE_THRESHOLD:
                print(
                    json.dumps(
                        {
                            "status": "info",
                            "message": f"[Confidence Escalation] Phase {phase}: {confidence:.2f} < {CONFIDENCE_THRESHOLD} threshold. Upgrading to Opus 4.6.",
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                return self._build_escalated_response(
                    phase, iteration, complexity, confidence
                )

            # Default: Use Liquid LFM for fast, cheap orchestration
            model = FALLBACK_MODELS["manager"]
            return {
                "name": phase,
                "model_id": model["modelId"],
                "provider": model["provider"],
                "max_tokens": model["maxTokens"],
                "temperature": model["temperature"],
                "thinking_mode": model["thinkingMode"],
                "supports_streaming": model.get("supportsStreaming", True),
                "phase": phase,
                "complexity_used": complexity,
                "iteration": iteration,
                "escalated": False,
                "confidence": confidence,
                "source": "fallback",
                "tokens_per_sec": model.get("tokensPerSec", 359),
            }

        # Phase to role mapping for other phases
        phase_role_map = {
            "architect": "architect",
            "expander": "researcher",  # Gemini 3 Pro for large context
            "frontend": "coding",
            "backend": "coding",
            "tester": "tester",
            "reviewer": "reviewer",
        }
        role = phase_role_map.get(phase, "coding")

        # Escalate on retry iterations
        if iteration > 1:
            return self._build_escalated_response(
                phase, iteration, complexity, confidence
            )

        # High complexity always gets premium model
        if complexity >= COMPLEXITY_THRESHOLD:
            return self._build_escalated_response(
                phase, iteration, complexity, confidence
            )

        # Low confidence escalation (February 2026)
        # If confidence is below threshold, escalate to premium model
        if confidence < CONFIDENCE_THRESHOLD:
            return self._build_escalated_response(
                phase, iteration, complexity, confidence
            )

        # Standard routing
        model = FALLBACK_MODELS.get(role, FALLBACK_MODELS["coding"])

        return {
            "name": phase,
            "model_id": model["modelId"],
            "provider": model["provider"],
            "max_tokens": model["maxTokens"],
            "temperature": model["temperature"],
            "thinking_mode": model["thinkingMode"],
            "supports_streaming": model.get("supportsStreaming", True),
            "phase": phase,
            "complexity_used": complexity,
            "iteration": iteration,
            "escalated": False,
            "confidence": confidence,
            "source": "fallback",
        }

    def list_models(self) -> dict:
        """List available models from Convex."""
        if self.connected and self.client:
            try:
                result = self.client.query("modelRouting:listModels", {})
                if result:
                    return {
                        "all": result.get("all", []),
                        "enabled": result.get("enabled", []),
                        "source": "convex",
                    }
            except Exception:
                pass

        # Fallback
        models = list(FALLBACK_MODELS.keys())
        return {"all": models, "enabled": models, "source": "fallback"}

    def get_config(self) -> dict:
        """Get router configuration from Convex."""
        if self.connected and self.client:
            try:
                result = self.client.query("modelRouting:getConfig", {})
                if result:
                    result["source"] = "convex"
                    return result
            except Exception:
                pass

        # Fallback
        return {
            "complexity_threshold": COMPLEXITY_THRESHOLD,
            "models": FALLBACK_MODELS,
            "source": "fallback",
        }

    def write_file(self, params: dict) -> dict:
        """
        Write file to disk with security sandboxing.

        Defense in Depth (February 2026):
        - TypeScript validates path against allowlist BEFORE calling Python
        - Python validates AGAIN to prevent bypass attempts
        - Both layers must approve for write to succeed
        """
        file_path = params.get("path", "")
        content = params.get("content", "")

        if not file_path:
            return {"success": False, "error": "No path provided"}

        # =====================================================================
        # SECURITY: Defense in Depth - Validate at Python layer too
        # =====================================================================

        # Normalize path separators
        normalized_path = file_path.replace("\\", "/")

        # Block path traversal attacks (../)
        if ".." in normalized_path:
            print(
                json.dumps(
                    {
                        "status": "warning",
                        "message": f"[Security Alert] Blocked path traversal: {file_path}",
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
            return {
                "success": False,
                "error": f"Security Violation: Path traversal blocked: {file_path}",
            }

        # Block absolute paths
        if os.path.isabs(file_path):
            print(
                json.dumps(
                    {
                        "status": "warning",
                        "message": f"[Security Alert] Blocked absolute path: {file_path}",
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
            return {
                "success": False,
                "error": f"Security Violation: Absolute paths not allowed: {file_path}",
            }

        # Check against allowlist
        is_allowed = any(
            normalized_path.startswith(prefix) for prefix in ALLOWED_PREFIXES
        )
        if not is_allowed:
            print(
                json.dumps(
                    {
                        "status": "warning",
                        "message": f"[Security Violation] Unauthorized path: {file_path}",
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
            return {
                "success": False,
                "error": f"Security Violation: Path '{file_path}' is outside the sandbox. "
                f"Allowed prefixes: {', '.join(ALLOWED_PREFIXES)}",
            }

        # =====================================================================
        # Path validated - proceed with write
        # =====================================================================

        try:
            # Resolve to absolute path relative to VOS3 root
            vos3_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            absolute_path = os.path.join(vos3_root, file_path)

            # Final safety check: ensure resolved path is still within VOS3 root
            real_path = os.path.realpath(absolute_path)
            real_root = os.path.realpath(vos3_root)
            if not real_path.startswith(real_root):
                return {
                    "success": False,
                    "error": "Security Violation: Resolved path escapes project root",
                }

            # Create parent directories if they don't exist
            parent_dir = os.path.dirname(absolute_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)

            # Write the file
            with open(absolute_path, "w", encoding="utf-8") as f:
                f.write(content)

            return {
                "success": True,
                "path": file_path,
                "bytes_written": len(content.encode("utf-8")),
            }

        except Exception as e:
            return {"success": False, "error": str(e), "path": file_path}

    def route_stream(self, params: dict):
        """
        Stream LLM response via Factory-selected provider.
        Outputs JSON chunks to stdout for TypeScript bridge consumption.
        """
        fallback_model = os.getenv("VOS3_FALLBACK_MODEL", "claude-opus-4-6")
        model_id = params.get("modelId", fallback_model)
        prompt = params.get("prompt", "")
        max_tokens = params.get("maxTokens", 16384)
        temperature = params.get("temperature", 0.7)
        system_prompt = params.get("system", None)

        try:
            # Use Factory to create LLM — model-agnostic
            sys.path.insert(
                0,
                os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                ),
            )
            from src.efficiency.factory import (
                _create_llm,
                _get_provider_and_model,
                MODEL_PROVIDERS,
            )

            # Determine provider from model_id
            provider_name = None
            for name, prov in MODEL_PROVIDERS.items():
                if name in model_id.lower() or model_id.lower().startswith(
                    name.split("-")[0]
                ):
                    provider_name = prov
                    break

            if not provider_name:
                # Detect from available env vars
                if os.getenv("ANTHROPIC_API_KEY"):
                    provider_name = "anthropic"
                elif os.getenv("OPENAI_API_KEY"):
                    provider_name = "openai"
                elif os.getenv("OLLAMA_BASE_URL"):
                    provider_name = "ollama"
                else:
                    print(
                        json.dumps(
                            {
                                "status": "error",
                                "message": "No LLM provider available. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OLLAMA_BASE_URL.",
                            }
                        ),
                        flush=True,
                    )
                    return

            llm = _create_llm(
                provider=provider_name,
                model_id=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # Build messages
            from langchain_core.messages import HumanMessage, SystemMessage

            messages = []
            if system_prompt:
                messages.append(SystemMessage(content=system_prompt))
            messages.append(HumanMessage(content=prompt))

            # Stream response
            for chunk in llm.stream(messages):
                text = chunk.content if hasattr(chunk, "content") else str(chunk)
                if text:
                    print(
                        json.dumps({"status": "stream", "data": {"chunk": text}}),
                        flush=True,
                    )

            # Signal stream completion
            print(
                json.dumps({"status": "ok", "data": {"done": True, "model": model_id}}),
                flush=True,
            )

        except ImportError as e:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "message": f"Missing LLM dependency: {e}. Install langchain-anthropic, langchain-openai, or langchain-ollama.",
                    }
                ),
                flush=True,
            )
        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}), flush=True)

    def handle_command(self, data: dict):
        """Handle a single command."""
        cmd = data.get("command")
        params = data.get("params", {})
        state = params.get("state")  # Multi-agent state from LangGraph

        if cmd == "get_model":
            result = self.resolve_routing(
                params.get("role", "coding"),
                int(params.get("complexity", 5)),
                state=state,
            )
            print(json.dumps({"status": "ok", "data": result}), flush=True)

        elif cmd == "get_pipeline_model":
            # Pipeline-aware routing with full state
            pipeline_state = state or params
            result = self.resolve_pipeline_routing(pipeline_state)
            print(json.dumps({"status": "ok", "data": result}), flush=True)

        elif cmd == "list_models":
            result = self.list_models()
            print(json.dumps({"status": "ok", "data": result}), flush=True)

        elif cmd == "config":
            result = self.get_config()
            print(json.dumps({"status": "ok", "data": result}), flush=True)

        elif cmd == "ping":
            print(
                json.dumps(
                    {"status": "ok", "data": {"pong": True, "convex": self.connected}}
                ),
                flush=True,
            )

        elif cmd == "route_stream":
            self.route_stream(params)

        elif cmd == "write_file":
            # Secure file write (path validation done in TypeScript)
            result = self.write_file(params)
            print(json.dumps({"status": "ok", "data": result}), flush=True)

        elif cmd == "exit":
            print(json.dumps({"status": "exit"}), flush=True)
            return False

        else:
            print(
                json.dumps({"status": "error", "message": f"Unknown command: {cmd}"}),
                flush=True,
            )

        return True

    def loop(self):
        """Interactive mode - read commands from stdin."""
        for line in sys.stdin:
            if not line.strip():
                continue

            try:
                data = json.loads(line)
                if not self.handle_command(data):
                    break
            except json.JSONDecodeError as e:
                print(
                    json.dumps(
                        {"status": "error", "message": f"Invalid JSON: {str(e)}"}
                    ),
                    flush=True,
                )
            except Exception as e:
                print(json.dumps({"status": "error", "message": str(e)}), flush=True)


# ============================================================================
# CLI MODE (Backward Compatible)
# ============================================================================


def run_cli_mode():
    """Run in CLI mode with arguments."""
    import io
    from contextlib import redirect_stdout

    # Suppress ready message in CLI mode
    with redirect_stdout(io.StringIO()):
        router = ConvexRouter(require_convex=False)

    command = sys.argv[1]

    if command == "get_model":
        if len(sys.argv) < 4:
            print(json.dumps({"error": "Usage: get_model <role> <complexity>"}))
            sys.exit(1)
        result = router.resolve_routing(sys.argv[2], int(sys.argv[3]))
        print(json.dumps(result))

    elif command == "list_models":
        result = router.list_models()
        print(json.dumps(result))

    elif command == "config":
        result = router.get_config()
        print(json.dumps(result))

    else:
        print(json.dumps({"error": f"Unknown command: {command}"}))
        sys.exit(1)


def main():
    if len(sys.argv) > 1:
        # CLI mode
        run_cli_mode()
    else:
        # Interactive mode - require Convex
        router = ConvexRouter(require_convex=False)  # Set to True in production
        router.loop()


if __name__ == "__main__":
    main()
