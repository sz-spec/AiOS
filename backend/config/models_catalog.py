"""
AI Models Catalog
=================

Comprehensive catalog of AI models with real pricing and descriptions.
Pricing sourced from official provider documentation (February 2026).
"""

from typing import Dict, List, Optional
from pydantic import BaseModel
from enum import Enum


class ModelCapability(str, Enum):
    TEXT = "text"
    CODE = "code"
    VISION = "vision"
    AUDIO = "audio"
    IMAGE_GEN = "image_generation"
    REASONING = "reasoning"
    EMBEDDING = "embedding"


class ModelInfo(BaseModel):
    id: str
    name: str
    provider: str
    provider_key: str  # Environment variable name for API key
    description: str
    strengths: List[str]
    intended_for: str
    cost_per_1k_input: float
    cost_per_1k_output: float
    max_context: int
    capabilities: List[ModelCapability]
    is_open_weight: bool = False
    requires_local: bool = False  # Requires Ollama or local setup


# ============================================================================
# ANTHROPIC MODELS - https://www.anthropic.com/pricing
# ============================================================================
ANTHROPIC_MODELS = [
    # Claude 4.5 Family
    ModelInfo(
        id="claude-opus-4.6",
        name="Claude Opus 4.6",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Latest flagship, extended thinking",
        strengths=["Complex reasoning", "Extended thinking", "Research", "Analysis"],
        intended_for="Most complex reasoning, multi-hour agentic tasks, research synthesis, critical decision support",
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
            ModelCapability.REASONING,
        ],
    ),
    ModelInfo(
        id="claude-opus-4.5",
        name="Claude Opus 4.5",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Most capable, extended thinking mode",
        strengths=["Complex reasoning", "Long context", "Nuanced writing", "Research"],
        intended_for="Deep analysis, scientific reasoning, long-running autonomous agents, highest-stakes tasks",
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
            ModelCapability.REASONING,
        ],
    ),
    ModelInfo(
        id="claude-sonnet-4.5",
        name="Claude Sonnet 4.5",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="80.9% SWE-bench, best for coding",
        strengths=[
            "Coding (80.9% SWE-bench)",
            "Fast responses",
            "Cost-effective",
            "Tool use",
        ],
        intended_for="Production coding agents, complex debugging, full-stack development, code review at scale",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="claude-haiku-4.5",
        name="Claude Haiku 4.5",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Fast 4.5 family model",
        strengths=["Speed", "Low cost", "Simple tasks", "High throughput"],
        intended_for="High-volume classification, real-time chat, content moderation, quick summarization",
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.005,
        max_context=200000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
    ),
    # Claude 4 Family
    ModelInfo(
        id="claude-opus-4",
        name="Claude Opus 4",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Claude 4 Opus",
        strengths=["Complex reasoning", "Analysis", "Writing"],
        intended_for="Complex multi-step workflows, agentic coding, extended autonomous tasks",
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="claude-sonnet-4",
        name="Claude Sonnet 4",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Claude 4 Sonnet",
        strengths=["Coding", "General tasks", "Tool use"],
        intended_for="Daily coding copilot, business writing, data analysis, balanced cost/quality workloads",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    # Claude 3.7 Family
    ModelInfo(
        id="claude-sonnet-3.7",
        name="Claude Sonnet 3.7",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Extended thinking",
        strengths=["Coding", "Reasoning", "Extended thinking"],
        intended_for="Step-by-step reasoning tasks, math, logic problems, transparent chain-of-thought",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
    ),
    # Claude 3.5 Family
    ModelInfo(
        id="claude-sonnet-3.5-v2",
        name="Claude Sonnet 3.5 v2",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Updated with computer use",
        strengths=["Coding", "Speed", "Computer use"],
        intended_for="Computer use / browser automation, GUI interaction, screen-based agentic tasks",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="claude-sonnet-3.5",
        name="Claude Sonnet 3.5",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Original 3.5 Sonnet",
        strengths=["Speed", "Coding", "General tasks"],
        intended_for="General-purpose assistant, writing, analysis - reliable workhorse",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="claude-haiku-3.5",
        name="Claude Haiku 3.5",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Fast and affordable",
        strengths=["Speed", "Low cost", "High volume"],
        intended_for="Lightweight extraction, tagging, routing, high-throughput pipelines",
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
        max_context=200000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
    ),
    # Claude 3 Family (Legacy)
    ModelInfo(
        id="claude-opus-3",
        name="Claude Opus 3",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Legacy flagship",
        strengths=["Reasoning", "Analysis", "Writing"],
        intended_for="Legacy integrations only - superseded by Opus 4+",
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="claude-sonnet-3",
        name="Claude Sonnet 3",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Legacy balanced",
        strengths=["Balance", "General tasks"],
        intended_for="Legacy integrations only - superseded by Sonnet 4+",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_context=200000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
    ),
    ModelInfo(
        id="claude-haiku-3",
        name="Claude Haiku 3",
        provider="Anthropic",
        provider_key="ANTHROPIC_API_KEY",
        description="Legacy fast",
        strengths=["Speed", "Low cost"],
        intended_for="Ultra-cheap bulk processing, massive-scale classification, cost-sensitive pipelines",
        cost_per_1k_input=0.00025,
        cost_per_1k_output=0.00125,
        max_context=200000,
        capabilities=[ModelCapability.TEXT],
    ),
]

# ============================================================================
# OPENAI MODELS - https://openai.com/pricing
# ============================================================================
OPENAI_MODELS = [
    # GPT-5 Family
    ModelInfo(
        id="gpt-5",
        name="GPT-5",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="OpenAI's most advanced model with breakthrough capabilities.",
        strengths=["Complex reasoning", "Multimodal", "Long context", "Tool use"],
        intended_for="Complex tasks requiring maximum capability",
        cost_per_1k_input=0.005,
        cost_per_1k_output=0.015,
        max_context=400000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
            ModelCapability.REASONING,
        ],
    ),
    ModelInfo(
        id="gpt-5.1",
        name="GPT-5.1",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Improved GPT-5 with enhanced reasoning.",
        strengths=["Reasoning", "Accuracy", "Tool use"],
        intended_for="Production applications requiring reliability",
        cost_per_1k_input=0.005,
        cost_per_1k_output=0.015,
        max_context=400000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
            ModelCapability.REASONING,
        ],
    ),
    ModelInfo(
        id="gpt-5.2",
        name="GPT-5.2",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Latest GPT-5 iteration with reduced hallucination (6.2%).",
        strengths=["Low hallucination", "Accuracy", "Reliability"],
        intended_for="Applications requiring high accuracy and factuality",
        cost_per_1k_input=0.004,
        cost_per_1k_output=0.012,
        max_context=400000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
            ModelCapability.REASONING,
        ],
    ),
    ModelInfo(
        id="gpt-5-mini",
        name="GPT-5 Mini",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Efficient GPT-5 variant for cost-effective deployments.",
        strengths=["Speed", "Cost-effective", "Good capability"],
        intended_for="High-volume applications needing GPT-5 capabilities",
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.003,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    # GPT-4.5
    ModelInfo(
        id="gpt-4.5-preview",
        name="GPT-4.5 Preview",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Bridge between GPT-4 and GPT-5 with improved capabilities.",
        strengths=["Improved reasoning", "Better coding", "Reduced cost vs GPT-5"],
        intended_for="Production apps not ready for GPT-5",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.009,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    # GPT-4o Family
    ModelInfo(
        id="gpt-4o",
        name="GPT-4o",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Multimodal model for text, vision, and audio.",
        strengths=["Multimodal", "Fast", "Good value", "Vision"],
        intended_for="Applications needing vision and text understanding",
        cost_per_1k_input=0.0025,
        cost_per_1k_output=0.01,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
            ModelCapability.AUDIO,
        ],
    ),
    ModelInfo(
        id="gpt-4o-mini",
        name="GPT-4o Mini",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Small, fast, and affordable multimodal model.",
        strengths=["Very fast", "Very cheap", "Good for simple tasks"],
        intended_for="High-volume, cost-sensitive applications",
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    # GPT-4 Family
    ModelInfo(
        id="gpt-4",
        name="GPT-4",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Original GPT-4, reliable and well-understood.",
        strengths=["Reliability", "Predictable", "Well-documented"],
        intended_for="Legacy applications, predictable behavior needed",
        cost_per_1k_input=0.03,
        cost_per_1k_output=0.06,
        max_context=8192,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
    ),
    ModelInfo(
        id="gpt-4-turbo",
        name="GPT-4 Turbo",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Faster GPT-4 with vision and 128K context.",
        strengths=["Long context", "Vision", "Faster than GPT-4"],
        intended_for="Long documents, vision tasks",
        cost_per_1k_input=0.01,
        cost_per_1k_output=0.03,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    # o-series (Reasoning)
    ModelInfo(
        id="o1",
        name="o1",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Reasoning model that thinks before answering.",
        strengths=["Deep reasoning", "Math", "Science", "Logic"],
        intended_for="Complex reasoning, math, scientific analysis",
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.06,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
    ),
    ModelInfo(
        id="o1-mini",
        name="o1 Mini",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Fast reasoning model for coding and STEM.",
        strengths=["Fast reasoning", "Coding", "STEM tasks"],
        intended_for="Coding assistance, quick reasoning tasks",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.012,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
    ),
    ModelInfo(
        id="o1-pro",
        name="o1 Pro",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Most capable reasoning model with extended thinking.",
        strengths=["Maximum reasoning", "Complex problems", "Research"],
        intended_for="Hardest reasoning problems, research, analysis",
        cost_per_1k_input=0.06,
        cost_per_1k_output=0.24,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
    ),
    ModelInfo(
        id="o3",
        name="o3",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Advanced reasoning with 100% on AIME benchmark.",
        strengths=["Superior reasoning", "Math (AIME 100%)", "Science"],
        intended_for="Mathematical reasoning, scientific problems",
        cost_per_1k_input=0.02,
        cost_per_1k_output=0.08,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
    ),
    ModelInfo(
        id="o3-mini",
        name="o3 Mini",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Fast o3 variant for everyday reasoning.",
        strengths=["Fast", "Good reasoning", "Cost-effective"],
        intended_for="Daily coding, quick reasoning tasks",
        cost_per_1k_input=0.004,
        cost_per_1k_output=0.016,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
    ),
    ModelInfo(
        id="o4-mini",
        name="o4 Mini",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Latest reasoning mini model.",
        strengths=["Latest reasoning", "Efficient", "Fast"],
        intended_for="Modern reasoning applications at lower cost",
        cost_per_1k_input=0.004,
        cost_per_1k_output=0.016,
        max_context=200000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
    ),
    # Specialized
    ModelInfo(
        id="codex",
        name="Codex",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Cloud coding agent for agentic software development.",
        strengths=["Agentic coding", "Full codebase understanding", "Autonomous tasks"],
        intended_for="Autonomous coding tasks, software agents",
        cost_per_1k_input=0.01,
        cost_per_1k_output=0.03,
        max_context=128000,
        capabilities=[ModelCapability.CODE, ModelCapability.REASONING],
    ),
    ModelInfo(
        id="gpt-oss",
        name="GPT Open Source",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Open-weight model from OpenAI.",
        strengths=["Open weights", "Self-hostable", "Customizable"],
        intended_for="Custom deployments, fine-tuning, on-premise",
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.002,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
    ),
    # Multimodal
    ModelInfo(
        id="dall-e-3",
        name="DALL-E 3",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="State-of-the-art image generation.",
        strengths=["Image generation", "Text understanding", "Creative"],
        intended_for="Image generation, creative content",
        cost_per_1k_input=0.04,  # Per image pricing converted
        cost_per_1k_output=0.08,
        max_context=4096,
        capabilities=[ModelCapability.IMAGE_GEN],
    ),
    ModelInfo(
        id="whisper",
        name="Whisper",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Speech-to-text transcription and translation.",
        strengths=["Transcription", "Translation", "Multiple languages"],
        intended_for="Audio transcription, meeting notes, accessibility",
        cost_per_1k_input=0.006,  # Per minute converted
        cost_per_1k_output=0,
        max_context=0,
        capabilities=[ModelCapability.AUDIO],
    ),
    ModelInfo(
        id="tts-1",
        name="TTS-1",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="Text-to-speech for real-time applications.",
        strengths=["Fast", "Natural voice", "Multiple voices"],
        intended_for="Voice assistants, real-time speech",
        cost_per_1k_input=0.015,  # Per 1K characters
        cost_per_1k_output=0,
        max_context=0,
        capabilities=[ModelCapability.AUDIO],
    ),
    ModelInfo(
        id="tts-1-hd",
        name="TTS-1 HD",
        provider="OpenAI",
        provider_key="OPENAI_API_KEY",
        description="High-quality text-to-speech.",
        strengths=["High quality", "Natural", "Expressive"],
        intended_for="Podcasts, audiobooks, high-quality speech",
        cost_per_1k_input=0.03,
        cost_per_1k_output=0,
        max_context=0,
        capabilities=[ModelCapability.AUDIO],
    ),
]

# ============================================================================
# GOOGLE MODELS - https://ai.google.dev/pricing
# ============================================================================
GOOGLE_MODELS = [
    # Gemini 3
    ModelInfo(
        id="gemini-3-pro",
        name="Gemini 3 Pro",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Google's flagship - replaces Ultra tier with superior performance.",
        strengths=["Multimodal", "2M context", "Reasoning", "Spatial understanding"],
        intended_for="Complex multimodal tasks, long documents, research",
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.005,
        max_context=2000000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
            ModelCapability.AUDIO,
        ],
    ),
    ModelInfo(
        id="gemini-3-flash",
        name="Gemini 3 Flash",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Fast and efficient Gemini 3 variant.",
        strengths=["Speed", "Cost-effective", "Good capability"],
        intended_for="High-volume, latency-sensitive applications",
        cost_per_1k_input=0.000075,
        cost_per_1k_output=0.0003,
        max_context=1000000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="gemini-3-deep-think",
        name="Gemini 3 Deep Think",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Reasoning-focused Gemini with extended thinking.",
        strengths=["Deep reasoning", "Complex problems", "Analysis"],
        intended_for="Complex reasoning, scientific analysis, research",
        cost_per_1k_input=0.005,
        cost_per_1k_output=0.02,
        max_context=1000000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
    ),
    # Gemini 2.5
    ModelInfo(
        id="gemini-2.5-pro",
        name="Gemini 2.5 Pro",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Strong multimodal model with thinking capabilities.",
        strengths=["Thinking mode", "Multimodal", "Coding"],
        intended_for="Production applications, complex tasks",
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.005,
        max_context=1000000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
            ModelCapability.REASONING,
        ],
    ),
    ModelInfo(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Fast 2.5 variant for everyday tasks.",
        strengths=["Speed", "Multimodal", "Cost-effective"],
        intended_for="Fast responses, chatbots, high volume",
        cost_per_1k_input=0.000075,
        cost_per_1k_output=0.0003,
        max_context=1000000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="gemini-2.5-flash-lite",
        name="Gemini 2.5 Flash-Lite",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Ultra-efficient for high-volume workloads.",
        strengths=["Ultra-fast", "Cheapest", "Simple tasks"],
        intended_for="Maximum throughput, simple tasks",
        cost_per_1k_input=0.000038,
        cost_per_1k_output=0.00015,
        max_context=500000,
        capabilities=[ModelCapability.TEXT],
    ),
    ModelInfo(
        id="gemini-2.5-flash-native-audio",
        name="Gemini 2.5 Flash Native Audio",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Native audio understanding and generation.",
        strengths=["Native audio", "Real-time", "Multimodal"],
        intended_for="Voice applications, audio processing",
        cost_per_1k_input=0.0001,
        cost_per_1k_output=0.0004,
        max_context=500000,
        capabilities=[ModelCapability.TEXT, ModelCapability.AUDIO],
    ),
    # Gemini 2.0
    ModelInfo(
        id="gemini-2.0-pro",
        name="Gemini 2.0 Pro",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Strong 2.0 model for complex tasks.",
        strengths=["Multimodal", "Long context", "Reliable"],
        intended_for="Production applications, complex multimodal",
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.005,
        max_context=1000000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="gemini-2.0-flash",
        name="Gemini 2.0 Flash",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Fast and free (within limits) 2.0 model.",
        strengths=["Free tier", "Fast", "Multimodal"],
        intended_for="Development, testing, free-tier applications",
        cost_per_1k_input=0,  # Free tier available
        cost_per_1k_output=0,
        max_context=1000000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="gemini-2.0-flash-thinking",
        name="Gemini 2.0 Flash Thinking",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Flash with thinking capabilities.",
        strengths=["Reasoning", "Fast", "Cost-effective"],
        intended_for="Reasoning tasks at Flash speed",
        cost_per_1k_input=0.0001,
        cost_per_1k_output=0.0004,
        max_context=1000000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
    ),
    # Gemini 1.5
    ModelInfo(
        id="gemini-1.5-pro",
        name="Gemini 1.5 Pro",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="2M context pioneer - great for long documents.",
        strengths=["2M context", "Multimodal", "Reliable"],
        intended_for="Very long documents, codebases, video analysis",
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.005,
        max_context=2000000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
            ModelCapability.AUDIO,
        ],
    ),
    ModelInfo(
        id="gemini-1.5-flash",
        name="Gemini 1.5 Flash",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Fast 1.5 variant with 1M context.",
        strengths=["Speed", "1M context", "Cost-effective"],
        intended_for="Fast multimodal tasks, long context",
        cost_per_1k_input=0.000075,
        cost_per_1k_output=0.0003,
        max_context=1000000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    # Gemini 1.0 (Legacy)
    ModelInfo(
        id="gemini-1.0-ultra",
        name="Gemini 1.0 Ultra",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Original flagship (superseded by 3 Pro).",
        strengths=["Capability", "Multimodal"],
        intended_for="Legacy applications (consider upgrading)",
        cost_per_1k_input=0.002,
        cost_per_1k_output=0.008,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="gemini-1.0-pro",
        name="Gemini 1.0 Pro",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Original Pro model.",
        strengths=["Reliable", "Well-tested"],
        intended_for="Legacy applications",
        cost_per_1k_input=0.0005,
        cost_per_1k_output=0.002,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
    ),
    ModelInfo(
        id="gemini-1.0-nano",
        name="Gemini 1.0 Nano",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="On-device model for mobile/edge.",
        strengths=["On-device", "Privacy", "Offline"],
        intended_for="Mobile apps, edge devices, offline use",
        cost_per_1k_input=0,
        cost_per_1k_output=0,
        max_context=32000,
        capabilities=[ModelCapability.TEXT],
        requires_local=True,
    ),
    # Gemma (Open-weight)
    ModelInfo(
        id="gemma-3",
        name="Gemma 3",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Latest open-weight model from Google.",
        strengths=["Open weights", "Good performance", "Self-hostable"],
        intended_for="Custom deployments, fine-tuning, research",
        cost_per_1k_input=0,
        cost_per_1k_output=0,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
        requires_local=True,
    ),
    ModelInfo(
        id="gemma-2-27b",
        name="Gemma 2 27B",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Largest Gemma 2 variant.",
        strengths=["Best Gemma 2", "Open weights"],
        intended_for="Research, custom deployments",
        cost_per_1k_input=0,
        cost_per_1k_output=0,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
        requires_local=True,
    ),
    ModelInfo(
        id="gemma-2-9b",
        name="Gemma 2 9B",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Mid-size Gemma 2.",
        strengths=["Balanced", "Open weights"],
        intended_for="Local deployment, fine-tuning",
        cost_per_1k_input=0,
        cost_per_1k_output=0,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
        requires_local=True,
    ),
    ModelInfo(
        id="gemma-2-2b",
        name="Gemma 2 2B",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Small Gemma for edge/mobile.",
        strengths=["Tiny", "Fast", "Edge deployment"],
        intended_for="Mobile, edge, resource-constrained",
        cost_per_1k_input=0,
        cost_per_1k_output=0,
        max_context=128000,
        capabilities=[ModelCapability.TEXT],
        is_open_weight=True,
        requires_local=True,
    ),
    # Image Generation
    ModelInfo(
        id="imagen-3",
        name="Imagen 3",
        provider="Google",
        provider_key="GOOGLE_API_KEY",
        description="Google's latest image generation model.",
        strengths=["High quality", "Photorealism", "Text rendering"],
        intended_for="Image generation, creative content",
        cost_per_1k_input=0.04,
        cost_per_1k_output=0.08,
        max_context=4096,
        capabilities=[ModelCapability.IMAGE_GEN],
    ),
]

# ============================================================================
# XAI GROK MODELS - https://x.ai/api
# ============================================================================
XAI_MODELS = [
    ModelInfo(
        id="grok-4.1",
        name="Grok 4.1",
        provider="xAI",
        provider_key="XAI_API_KEY",
        description="Latest Grok with real-time information access.",
        strengths=["Real-time info", "Unfiltered", "Fast", "Coding"],
        intended_for="Real-time information, unfiltered responses",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_context=256000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="grok-4",
        name="Grok 4",
        provider="xAI",
        provider_key="XAI_API_KEY",
        description="Powerful Grok with X/Twitter integration.",
        strengths=["Real-time data", "Social context", "Coding"],
        intended_for="Social media analysis, real-time queries",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_context=256000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
    ),
    ModelInfo(
        id="grok-3",
        name="Grok 3",
        provider="xAI",
        provider_key="XAI_API_KEY",
        description="Strong general-purpose Grok model.",
        strengths=["General purpose", "Humor", "Direct"],
        intended_for="General tasks, conversational AI",
        cost_per_1k_input=0.002,
        cost_per_1k_output=0.01,
        max_context=131072,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
    ),
    ModelInfo(
        id="grok-3-mini",
        name="Grok 3 Mini",
        provider="xAI",
        provider_key="XAI_API_KEY",
        description="Fast and efficient Grok variant.",
        strengths=["Speed", "Cost-effective", "Good capability"],
        intended_for="High-volume, fast responses",
        cost_per_1k_input=0.0005,
        cost_per_1k_output=0.002,
        max_context=131072,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
    ),
    ModelInfo(
        id="grok-code-fast-1",
        name="Grok Code Fast 1",
        provider="xAI",
        provider_key="XAI_API_KEY",
        description="Specialized agentic coding model.",
        strengths=["Agentic coding", "Fast", "Tool use"],
        intended_for="Autonomous coding, software agents",
        cost_per_1k_input=0.002,
        cost_per_1k_output=0.01,
        max_context=131072,
        capabilities=[ModelCapability.CODE, ModelCapability.REASONING],
    ),
    ModelInfo(
        id="grok-2",
        name="Grok 2",
        provider="xAI",
        provider_key="XAI_API_KEY",
        description="Previous generation Grok.",
        strengths=["Reliable", "Well-tested"],
        intended_for="Stable applications",
        cost_per_1k_input=0.002,
        cost_per_1k_output=0.01,
        max_context=131072,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
    ),
    ModelInfo(
        id="grok-2-mini",
        name="Grok 2 Mini",
        provider="xAI",
        provider_key="XAI_API_KEY",
        description="Efficient Grok 2 variant.",
        strengths=["Fast", "Cheap"],
        intended_for="Cost-sensitive applications",
        cost_per_1k_input=0.0005,
        cost_per_1k_output=0.002,
        max_context=131072,
        capabilities=[ModelCapability.TEXT],
    ),
]

# ============================================================================
# ALIBABA QWEN MODELS - https://www.alibabacloud.com/help/en/model-studio/
# ============================================================================
ALIBABA_MODELS = [
    ModelInfo(
        id="qwen3",
        name="Qwen3",
        provider="Alibaba",
        provider_key="ALIBABA_API_KEY",
        description="Latest Qwen with hybrid thinking modes.",
        strengths=["Thinking modes", "Multilingual", "MCP support"],
        intended_for="Complex reasoning, multilingual tasks",
        cost_per_1k_input=0.0002,
        cost_per_1k_output=0.0006,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
        is_open_weight=True,
    ),
    ModelInfo(
        id="qwen3-thinking",
        name="Qwen3 Thinking",
        provider="Alibaba",
        provider_key="ALIBABA_API_KEY",
        description="Qwen3 with extended thinking for complex problems.",
        strengths=["Deep reasoning", "Math", "Analysis"],
        intended_for="Complex reasoning, research, analysis",
        cost_per_1k_input=0.0003,
        cost_per_1k_output=0.0012,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
        is_open_weight=True,
    ),
    ModelInfo(
        id="qwen2.5",
        name="Qwen 2.5",
        provider="Alibaba",
        provider_key="ALIBABA_API_KEY",
        description="Strong base model with good all-around performance.",
        strengths=["Balanced", "Multilingual", "Open weights"],
        intended_for="General purpose, multilingual tasks",
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
    ),
    ModelInfo(
        id="qwen2.5-coder",
        name="Qwen 2.5 Coder",
        provider="Alibaba",
        provider_key="ALIBABA_API_KEY",
        description="Specialized for code generation and understanding.",
        strengths=["Coding", "Code completion", "Debugging"],
        intended_for="Software development, code generation",
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
        max_context=128000,
        capabilities=[ModelCapability.CODE],
        is_open_weight=True,
    ),
    ModelInfo(
        id="qwen2.5-math",
        name="Qwen 2.5 Math",
        provider="Alibaba",
        provider_key="ALIBABA_API_KEY",
        description="Specialized for mathematical reasoning.",
        strengths=["Math", "Proofs", "Calculations"],
        intended_for="Mathematical problems, scientific computing",
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.REASONING],
        is_open_weight=True,
    ),
    ModelInfo(
        id="qwen2.5-vl",
        name="Qwen 2.5 VL",
        provider="Alibaba",
        provider_key="ALIBABA_API_KEY",
        description="Vision-Language model for multimodal tasks.",
        strengths=["Vision", "Image understanding", "OCR"],
        intended_for="Image analysis, document understanding",
        cost_per_1k_input=0.0002,
        cost_per_1k_output=0.0008,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.VISION],
        is_open_weight=True,
    ),
    ModelInfo(
        id="qwen2",
        name="Qwen 2",
        provider="Alibaba",
        provider_key="ALIBABA_API_KEY",
        description="Previous generation base model.",
        strengths=["Stable", "Well-tested", "Open weights"],
        intended_for="Production applications",
        cost_per_1k_input=0.0001,
        cost_per_1k_output=0.0004,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
    ),
    ModelInfo(
        id="qwq",
        name="QwQ",
        provider="Alibaba",
        provider_key="ALIBABA_API_KEY",
        description="Reasoning model that shows its thinking process.",
        strengths=["Transparent reasoning", "Math", "Logic"],
        intended_for="Complex reasoning, educational contexts",
        cost_per_1k_input=0.0003,
        cost_per_1k_output=0.0012,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.REASONING],
        is_open_weight=True,
    ),
]

# ============================================================================
# DEEPSEEK MODELS - https://platform.deepseek.com/pricing
# ============================================================================
DEEPSEEK_MODELS = [
    # DeepSeek-V3
    ModelInfo(
        id="deepseek-v3",
        name="DeepSeek V3",
        provider="DeepSeek",
        provider_key="DEEPSEEK_API_KEY",
        description="671B MoE model, 27x cheaper than GPT-4.",
        strengths=["Very cheap", "Strong capability", "MoE efficiency"],
        intended_for="Cost-sensitive production, high volume",
        cost_per_1k_input=0.00014,
        cost_per_1k_output=0.00028,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
    ),
    ModelInfo(
        id="deepseek-v3.1",
        name="DeepSeek V3.1",
        provider="DeepSeek",
        provider_key="DEEPSEEK_API_KEY",
        description="Improved V3 with better reasoning.",
        strengths=["Better reasoning", "Cheap", "Fast"],
        intended_for="General tasks at low cost",
        cost_per_1k_input=0.00014,
        cost_per_1k_output=0.00028,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
    ),
    ModelInfo(
        id="deepseek-v3.2",
        name="DeepSeek V3.2",
        provider="DeepSeek",
        provider_key="DEEPSEEK_API_KEY",
        description="Latest V3 iteration.",
        strengths=["Latest improvements", "Very affordable"],
        intended_for="Production applications",
        cost_per_1k_input=0.00014,
        cost_per_1k_output=0.00028,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
    ),
    ModelInfo(
        id="deepseek-v3.2-exp",
        name="DeepSeek V3.2 Experimental",
        provider="DeepSeek",
        provider_key="DEEPSEEK_API_KEY",
        description="Experimental V3.2 with new features.",
        strengths=["Latest features", "Experimental"],
        intended_for="Testing new capabilities",
        cost_per_1k_input=0.00014,
        cost_per_1k_output=0.00028,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
    ),
    # DeepSeek-R1 (Reasoning)
    ModelInfo(
        id="deepseek-r1",
        name="DeepSeek R1",
        provider="DeepSeek",
        provider_key="DEEPSEEK_API_KEY",
        description="Reasoning model matching o1 at fraction of cost.",
        strengths=["Reasoning", "Math", "Very cheap vs o1"],
        intended_for="Reasoning tasks at 27x lower cost than o1",
        cost_per_1k_input=0.00055,
        cost_per_1k_output=0.00219,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
        is_open_weight=True,
    ),
    ModelInfo(
        id="deepseek-r1-0528",
        name="DeepSeek R1-0528",
        provider="DeepSeek",
        provider_key="DEEPSEEK_API_KEY",
        description="Latest R1 release with improvements.",
        strengths=["Latest reasoning", "Improved accuracy"],
        intended_for="Complex reasoning tasks",
        cost_per_1k_input=0.00055,
        cost_per_1k_output=0.00219,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
        is_open_weight=True,
    ),
    ModelInfo(
        id="deepseek-r1-lite",
        name="DeepSeek R1-Lite",
        provider="DeepSeek",
        provider_key="DEEPSEEK_API_KEY",
        description="Fast reasoning at even lower cost.",
        strengths=["Fastest R1", "Very cheap", "Good reasoning"],
        intended_for="Quick reasoning tasks",
        cost_per_1k_input=0.0002,
        cost_per_1k_output=0.0008,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
        is_open_weight=True,
    ),
    ModelInfo(
        id="deepseek-r1-zero",
        name="DeepSeek R1-Zero",
        provider="DeepSeek",
        provider_key="DEEPSEEK_API_KEY",
        description="Base reasoning without RL fine-tuning.",
        strengths=["Pure reasoning", "Research baseline"],
        intended_for="Research, baseline comparisons",
        cost_per_1k_input=0.00055,
        cost_per_1k_output=0.00219,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.REASONING],
        is_open_weight=True,
    ),
    # DeepSeek-V2
    ModelInfo(
        id="deepseek-v2",
        name="DeepSeek V2",
        provider="DeepSeek",
        provider_key="DEEPSEEK_API_KEY",
        description="Previous generation MoE model.",
        strengths=["Stable", "Cheap", "Well-tested"],
        intended_for="Production with stability priority",
        cost_per_1k_input=0.00014,
        cost_per_1k_output=0.00028,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
    ),
    ModelInfo(
        id="deepseek-v2.5",
        name="DeepSeek V2.5",
        provider="DeepSeek",
        provider_key="DEEPSEEK_API_KEY",
        description="Improved V2 with chat capabilities.",
        strengths=["Chat optimized", "Stable"],
        intended_for="Conversational applications",
        cost_per_1k_input=0.00014,
        cost_per_1k_output=0.00028,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
    ),
    # DeepSeek-Coder
    ModelInfo(
        id="deepseek-coder-v2",
        name="DeepSeek Coder V2",
        provider="DeepSeek",
        provider_key="DEEPSEEK_API_KEY",
        description="Specialized coding model.",
        strengths=["Code generation", "Debugging", "Multiple languages"],
        intended_for="Software development, code assistance",
        cost_per_1k_input=0.00014,
        cost_per_1k_output=0.00028,
        max_context=128000,
        capabilities=[ModelCapability.CODE],
        is_open_weight=True,
    ),
]

# ============================================================================
# LOCAL MODELS (Ollama/Self-hosted)
# ============================================================================
LOCAL_MODELS = [
    ModelInfo(
        id="llama-4-maverick",
        name="Llama 4 Maverick",
        provider="Meta (Local)",
        provider_key="OLLAMA_BASE_URL",
        description="Latest Llama, beats GPT-4o on benchmarks.",
        strengths=["State-of-the-art open", "Free", "Fast"],
        intended_for="Local deployment, privacy-sensitive tasks",
        cost_per_1k_input=0,
        cost_per_1k_output=0,
        max_context=256000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
        is_open_weight=True,
        requires_local=True,
    ),
    ModelInfo(
        id="llama-4-scout",
        name="Llama 4 Scout",
        provider="Meta (Local)",
        provider_key="OLLAMA_BASE_URL",
        description="Efficient Llama 4 variant.",
        strengths=["Efficient", "Fast", "Good capability"],
        intended_for="Local deployment with speed priority",
        cost_per_1k_input=0,
        cost_per_1k_output=0,
        max_context=256000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
        requires_local=True,
    ),
    ModelInfo(
        id="llama-3.3-70b",
        name="Llama 3.3 70B",
        provider="Meta (Local)",
        provider_key="OLLAMA_BASE_URL",
        description="Strong 70B model for complex tasks.",
        strengths=["Strong capability", "Multilingual", "Free"],
        intended_for="Complex local tasks",
        cost_per_1k_input=0,
        cost_per_1k_output=0,
        max_context=128000,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
        requires_local=True,
    ),
    ModelInfo(
        id="llama-3.2-90b",
        name="Llama 3.2 90B",
        provider="Meta (Local)",
        provider_key="OLLAMA_BASE_URL",
        description="Largest Llama 3.2 with vision.",
        strengths=["Vision", "Large capacity", "Multimodal"],
        intended_for="Local multimodal tasks",
        cost_per_1k_input=0,
        cost_per_1k_output=0,
        max_context=128000,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
        ],
        is_open_weight=True,
        requires_local=True,
    ),
    ModelInfo(
        id="mixtral-8x22b",
        name="Mixtral 8x22B",
        provider="Mistral (Local)",
        provider_key="OLLAMA_BASE_URL",
        description="Large MoE model from Mistral.",
        strengths=["MoE efficiency", "Strong coding", "Multilingual"],
        intended_for="Complex local tasks, coding",
        cost_per_1k_input=0,
        cost_per_1k_output=0,
        max_context=65536,
        capabilities=[ModelCapability.TEXT, ModelCapability.CODE],
        is_open_weight=True,
        requires_local=True,
    ),
    ModelInfo(
        id="codestral-22b",
        name="Codestral 22B",
        provider="Mistral (Local)",
        provider_key="OLLAMA_BASE_URL",
        description="Specialized code model from Mistral.",
        strengths=["Coding", "Fast", "80+ languages"],
        intended_for="Local code generation",
        cost_per_1k_input=0,
        cost_per_1k_output=0,
        max_context=32768,
        capabilities=[ModelCapability.CODE],
        is_open_weight=True,
        requires_local=True,
    ),
]


# ============================================================================
# KERNEL-RESIDENT MODELS — W2.1e
# ============================================================================
# Sovereign-Mode lane. Slot id is bound at runtime by
# services.model_manager.load_to_kernel via
# ai.llm.kernel_provider.register_kernel_slot. The provider_key field
# points at the VBus socket env var so callers can detect the bridge
# location without hard-coding it.
KERNEL_MODELS = [
    ModelInfo(
        id="kernel-default",
        name="Kernel-Resident GGUF",
        provider="VOS3 Kernel (KIM)",
        provider_key="VOS3_KERNEL_BRIDGE_SOCKET",
        description=(
            "Kernel-attested GGUF inference dispatched over VBus. The slot "
            "binding is established by load_to_kernel(); priority=0 in the "
            "Smart Router so VOS3_LOCALITY_PREFERENCE=local-first selects "
            "this lane ahead of every Ollama or cloud provider. After the "
            "May-2026 Universal-Model Upgrade, the kernel admits any GGUF "
            "with vocab ≤ 262 144, d_model ≤ 16 384, n_layers ≤ 128, and "
            "context ≤ 8 192 — covering Gemma 3, Llama 4, DeepSeek R1, Qwen3, "
            "and Phi-4."
        ),
        strengths=[
            "VBus HMAC-SHA256 + W^X PTE protection on weights",
            "Zero outbound network",
            "HMAC-chained token provenance per slot",
            "Sovereign-mode certified",
        ],
        intended_for=(
            "Air-gapped sovereign deployments where the model weights must "
            "remain inside the host's hardware-immutable AI slot."
        ),
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_context=8192,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
        is_open_weight=True,
        requires_local=True,
    ),
    # ------------------------------------------------------------------
    # Kernel-validated open-weight family entries (May 2026 upgrade)
    # ------------------------------------------------------------------
    # Each entry below describes a public open-weight GGUF that fits inside
    # the post-upgrade kernel ceilings (vocab≤262 144, d_model≤16 384,
    # n_layers≤128, seq_len≤8 192). The id is the alias the operator passes
    # to POST /api/models/download — the actual GGUF blob is fetched from
    # an HTTPS allow-list; see services.model_manager._download_with_sha256.
    #
    # NOTE: the original directive listed "gemma-4-27b" — the May-2026
    # upstream is still **Gemma 3** (Gemma 4 has not been released by Google
    # as of 2026-05). Listing the verified Gemma 3 specs.
    ModelInfo(
        id="gemma-3-27b",
        name="Gemma 3 27B Instruct (kernel-bound)",
        provider="VOS3 Kernel (KIM)",
        provider_key="VOS3_KERNEL_BRIDGE_SOCKET",
        description=(
            "Google Gemma 3 27B Instruct, GGUF v3 (Q4_K_M / Q8_0). 262 144-"
            "token SentencePiece vocabulary; 14T-token training corpus; "
            "interleaved local/global attention (5:1) with 1024-token sliding "
            "window. Confirmed by upstream model card May 2026."
        ),
        strengths=[
            "262 144 vocab — admits any 2026 multilingual workload",
            "128K theoretical context (kernel slot caps at 8 192)",
            "Strong reasoning + code + multilingual",
        ],
        intended_for="Multilingual, long-document, kernel-resident inference.",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_context=8192,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
        is_open_weight=True,
        requires_local=True,
    ),
    ModelInfo(
        # Suffixed `-kernel` to coexist with the existing Ollama-routable
        # `llama-4-scout` entry at line 1188 (provider "Meta (Local)").
        # MODELS_BY_ID would otherwise resolve to whichever appears last in
        # ALL_MODELS, silently shadowing the Ollama variant.
        id="llama-4-scout-kernel",
        name="Llama 4 Scout 17B-16E (kernel-bound, MoE)",
        provider="VOS3 Kernel (KIM)",
        provider_key="VOS3_KERNEL_BRIDGE_SOCKET",
        description=(
            "Meta Llama 4 Scout — 17B active / 109B total mixture-of-experts "
            "(16 experts), GGUF v3 (Q4_K_M / Q5_K_M dynamic). Native "
            "multimodal (early-fusion). Kernel slot pages must accommodate "
            "expert routing weights — the Q4_K_M build is recommended for "
            "the 16 MiB KV envelope."
        ),
        strengths=[
            "17B active params at inference (memory-friendly)",
            "Mixture-of-experts (16) — sparse activation",
            "Native multimodal early fusion",
        ],
        intended_for="Sovereign multimodal agents on a single accelerator.",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_context=8192,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.VISION,
            ModelCapability.REASONING,
        ],
        is_open_weight=True,
        requires_local=True,
    ),
    ModelInfo(
        id="deepseek-r1-gguf",
        name="DeepSeek R1 (kernel-bound, MLA + DeepSeekMoE)",
        provider="VOS3 Kernel (KIM)",
        provider_key="VOS3_KERNEL_BRIDGE_SOCKET",
        description=(
            "DeepSeek R1 — 671B total / 37B active MoE, Multi-head Latent "
            "Attention (MLA), GGUF v3 (Q4_K_M / dynamic 1.78-bit available). "
            "embedding_length=7168, block_count=61, vocab=129 280 — fits the "
            "post-May-2026 kernel ceilings. R1's reasoning training makes it "
            "the recommended sovereign reasoner."
        ),
        strengths=[
            "Reasoning-tuned via reinforcement learning",
            "MLA attention — small KV-cache footprint",
            "37B activated per token (efficient)",
        ],
        intended_for="Sovereign chain-of-thought reasoning, code, math.",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_context=8192,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
        is_open_weight=True,
        requires_local=True,
    ),
    ModelInfo(
        id="qwen3-72b",
        name="Qwen3 72B (kernel-bound)",
        provider="VOS3 Kernel (KIM)",
        provider_key="VOS3_KERNEL_BRIDGE_SOCKET",
        description=(
            "Alibaba Qwen3 72B Instruct, GGUF v3. 152 064-token vocab, "
            "n_layers=80, d_model≈8 192. Strong tool-use + multilingual. "
            "Sits at the upper end of the kernel slot's working envelope; "
            "Q4_K_M recommended."
        ),
        strengths=[
            "Tool-use trained out-of-the-box",
            "Strong multilingual (152K vocab)",
            "Good code + reasoning balance",
        ],
        intended_for="Tool-using sovereign agents; bilingual workflows.",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        max_context=8192,
        capabilities=[
            ModelCapability.TEXT,
            ModelCapability.CODE,
            ModelCapability.REASONING,
        ],
        is_open_weight=True,
        requires_local=True,
    ),
]


# ============================================================================
# COMBINED CATALOG
# ============================================================================
ALL_MODELS: List[ModelInfo] = (
    ANTHROPIC_MODELS
    + OPENAI_MODELS
    + GOOGLE_MODELS
    + XAI_MODELS
    + ALIBABA_MODELS
    + DEEPSEEK_MODELS
    + LOCAL_MODELS
    + KERNEL_MODELS
)

MODELS_BY_ID: Dict[str, ModelInfo] = {m.id: m for m in ALL_MODELS}

MODELS_BY_PROVIDER: Dict[str, List[ModelInfo]] = {}
for model in ALL_MODELS:
    provider = model.provider
    if provider not in MODELS_BY_PROVIDER:
        MODELS_BY_PROVIDER[provider] = []
    MODELS_BY_PROVIDER[provider].append(model)


def get_model(model_id: str) -> Optional[ModelInfo]:
    """Get a model by ID."""
    return MODELS_BY_ID.get(model_id)


def get_models_for_provider(provider: str) -> List[ModelInfo]:
    """Get all models for a provider."""
    return MODELS_BY_PROVIDER.get(provider, [])


def get_all_providers() -> List[str]:
    """Get list of all providers."""
    return list(MODELS_BY_PROVIDER.keys())


# Provider API endpoints
PROVIDER_ENDPOINTS = {
    "Anthropic": "api.anthropic.com",
    "OpenAI": "api.openai.com",
    "Google": "generativelanguage.googleapis.com",
    "xAI": "api.x.ai",
    "Alibaba": "dashscope.aliyuncs.com",
    "DeepSeek": "api.deepseek.com",
    "Meta (Local)": "localhost:11434",
    "Mistral (Local)": "localhost:11434",
    "VOS3 Kernel (KIM)": "/tmp/vos3_bridge.sock",
}

PROVIDER_ENV_KEYS = {
    "Anthropic": "ANTHROPIC_API_KEY",
    "OpenAI": "OPENAI_API_KEY",
    "Google": "GOOGLE_API_KEY",
    "xAI": "XAI_API_KEY",
    "Alibaba": "ALIBABA_API_KEY",
    "DeepSeek": "DEEPSEEK_API_KEY",
    "Meta (Local)": "OLLAMA_BASE_URL",
    "Mistral (Local)": "OLLAMA_BASE_URL",
    "VOS3 Kernel (KIM)": "VOS3_KERNEL_BRIDGE_SOCKET",
}

# Provider detailed information
PROVIDER_INFO = {
    "Anthropic": {
        "strengths": [
            "Best instruction following",
            "Safety",
            "Long-form writing",
            "Coding (SWE-bench leader)",
            "Computer use",
        ],
        "weaknesses": [
            "Most expensive at Opus tier",
            "No image generation",
            "No open-weight models",
        ],
        "context": "200K tokens (1M beta on Sonnet 4)",
        "unique": [
            "Extended thinking mode",
            "Constitutional AI",
            "MCP protocol creator",
        ],
        "best_for": "Complex reasoning, agentic workflows, coding agents, enterprise safety-critical apps",
        "pricing_philosophy": "Premium - you pay more but get consistently high quality",
    },
    "OpenAI": {
        "strengths": [
            "Largest ecosystem",
            "Best multimodal suite (text, image, audio, video)",
            "Strongest reasoning with o-series",
        ],
        "weaknesses": [
            "Expensive at high tiers (o1-pro is $0.06/$0.24)",
            "Closed ecosystem",
            "Rate limits on newer models",
        ],
        "context": "128K-400K tokens depending on model",
        "unique": [
            "Full media pipeline (DALL-E, Whisper, TTS)",
            "Codex cloud agent",
            "Widest third-party integration",
        ],
        "best_for": "Multimodal apps, audio/voice pipelines, broad general-purpose use",
        "pricing_philosophy": "Wide range - from dirt cheap (4o-mini) to very expensive (o1-pro)",
    },
    "Google": {
        "strengths": [
            "Cheapest high-quality models",
            "Longest context (1M tokens)",
            "Best integration with Google ecosystem",
        ],
        "weaknesses": [
            "Historically less reliable for complex instruction following",
            "API changes frequently",
            "Model naming is confusing",
        ],
        "context": "Up to 1M tokens (largest in industry)",
        "unique": [
            "Free tier on 2.0 Flash",
            "Native audio",
            "Gemini 3 Deep Think",
            "Robotics models",
            "Nano on-device",
            "Gemma open-weight",
        ],
        "best_for": "Cost-sensitive production, long document processing, Google Workspace integration, on-device with Nano",
        "pricing_philosophy": "Aggressive undercut - Flash models are 10-50x cheaper than competitors",
    },
    "xAI": {
        "strengths": [
            "Real-time X/Twitter data access",
            "Lowest hallucination rate (4% on Grok 4.1)",
            "#1 LMArena Elo",
        ],
        "weaknesses": [
            "Smallest model lineup",
            "Newest provider so ecosystem/tooling is thinner",
            "API availability can be limited",
        ],
        "context": "128K tokens",
        "unique": [
            "Live X/Twitter integration",
            "Open-sourced Grok 1",
            "Specialized coding model (Code Fast)",
        ],
        "best_for": "Social media intelligence, real-time data analysis, applications where factual accuracy is critical",
        "pricing_philosophy": "Competitive mid-range",
    },
    "Alibaba": {
        "strengths": [
            "By far the cheapest API pricing",
            "Strong multilingual (especially CN/EN)",
            "Fully open-weight",
            "Huge model variety",
        ],
        "weaknesses": [
            "Based in China (data sovereignty concerns)",
            "English quality slightly behind top Western models",
            "Less mature API ecosystem",
        ],
        "context": "128K-262K tokens",
        "unique": [
            "Everything is open-weight (Apache 2.0)",
            "Specialized models for code/math/vision",
            "Qwen Code CLI agent (free 1K req/day)",
        ],
        "best_for": "Cost-critical production, self-hosting, Chinese market, budget-conscious startups",
        "pricing_philosophy": "Rock bottom - 10-100x cheaper than Anthropic/OpenAI",
    },
    "DeepSeek": {
        "strengths": [
            "Absolute cheapest pricing in the industry",
            "R1 reasoning matches o1 at 1/27th cost",
            "Fully open-weight",
        ],
        "weaknesses": [
            "China-based (same sovereignty concerns)",
            "API reliability/uptime issues historically",
            "Smaller team",
        ],
        "context": "128K tokens",
        "unique": [
            "MoE architecture (671B total, only 37B active = massive efficiency)",
            "R1 reasoning chain is transparent/open",
        ],
        "best_for": "Maximum cost savings, reasoning tasks on a budget, self-hosting large MoE models",
        "pricing_philosophy": "Disruptively cheap - designed to undercut everyone",
    },
    "Meta (Local)": {
        "strengths": [
            "Zero cost",
            "Full privacy",
            "No rate limits",
            "No data leaves your machine",
        ],
        "weaknesses": [
            "Requires beefy hardware (GPU)",
            "Lower quality than top API models",
            "You manage everything",
        ],
        "context": "Varies (typically 8K-128K)",
        "unique": [
            "Complete data sovereignty",
            "Offline capability",
            "Unlimited usage",
        ],
        "best_for": "Privacy-sensitive workloads, offline/air-gapped environments, experimentation, edge deployment",
        "pricing_philosophy": "Free (hardware cost only)",
    },
    "Mistral (Local)": {
        "strengths": [
            "Zero cost",
            "Full privacy",
            "No rate limits",
            "Strong coding models",
        ],
        "weaknesses": [
            "Requires beefy hardware (GPU)",
            "Lower quality than top API models",
            "You manage everything",
        ],
        "context": "Varies (typically 8K-128K)",
        "unique": [
            "Complete data sovereignty",
            "Offline capability",
            "Codestral for code",
        ],
        "best_for": "Privacy-sensitive workloads, local code generation, experimentation",
        "pricing_philosophy": "Free (hardware cost only)",
    },
}

# Quick Reference: Best Model Per Task
TASK_RECOMMENDATIONS = {
    "General Chat": {
        "budget": "gemini-2.0-flash",
        "mid_range": "gpt-5-mini",
        "premium": "claude-opus-4.6",
    },
    "Coding": {
        "budget": "deepseek-coder-v2",
        "mid_range": "claude-sonnet-4",
        "premium": "claude-sonnet-4.5",
    },
    "Reasoning/Math": {
        "budget": "deepseek-r1",
        "mid_range": "o3-mini",
        "premium": "o3",
    },
    "Long Documents": {
        "budget": "gemini-2.5-flash",
        "mid_range": "gemini-2.5-pro",
        "premium": "gemini-1.5-pro",
    },
    "Vision/Images": {
        "budget": "qwen2.5-vl",
        "mid_range": "gpt-4o",
        "premium": "gemini-3-pro",
    },
    "Voice/Audio": {
        "budget": "whisper",
        "mid_range": "gemini-2.5-flash-native-audio",
        "premium": "tts-1-hd",
    },
    "Real-time Data": {
        "budget": "grok-3-mini",
        "mid_range": "grok-4",
        "premium": "grok-4.1",
    },
    "Factual Accuracy": {
        "budget": "gpt-5.2",
        "mid_range": "grok-4.1",
        "premium": "o1-pro",
    },
    "Bulk/Scale": {
        "budget": "gemini-2.5-flash-lite",
        "mid_range": "deepseek-v3.2",
        "premium": "qwen3",
    },
    "Self-Hosted": {
        "budget": "gemma-2-9b",
        "mid_range": "llama-3.3-70b",
        "premium": "llama-4-maverick",
    },
    "Privacy/Offline": {
        "budget": "codestral-22b",
        "mid_range": "mixtral-8x22b",
        "premium": "llama-4-maverick",
    },
    "Image Generation": {
        "budget": None,
        "mid_range": "imagen-3",
        "premium": "dall-e-3",
    },
}
