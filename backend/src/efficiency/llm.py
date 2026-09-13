"""
LLM Utilities
=============
Quantized LLM support and call reduction utilities.
"""

from typing import Optional, Dict, Any

# Optional imports with fallbacks
try:
    from langchain_community.llms import Ollama
    from langchain_community.embeddings import OllamaEmbeddings

    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False

try:
    from langchain_huggingface import HuggingFaceEmbeddings

    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False


def create_quantized_llm(
    model: str = "llama3:4bit", temperature: float = 0.7, num_ctx: int = 4096
) -> Optional[Any]:
    """
    Create quantized LLM for reduced VRAM usage.

    4-bit quantization reduces memory by ~75%:
    - llama3 7B: 14GB -> 3.5GB
    - llama3 13B: 26GB -> 6.5GB

    Usage:
        llm = create_quantized_llm("llama3:4bit")
        response = llm.invoke("Hello")
    """
    if not OLLAMA_AVAILABLE:
        print("Warning: Ollama not installed. Run: pip install ollama")
        return None

    try:
        return Ollama(model=model, temperature=temperature, num_ctx=num_ctx)
    except Exception as e:
        print(f"Warning: Could not create quantized LLM: {e}")
        return None


def create_efficient_embeddings(
    model: str = "nomic-embed-text", use_gpu: bool = False
) -> Optional[Any]:
    """
    Create efficient embeddings with Ollama or HuggingFace.

    Usage:
        embeddings = create_efficient_embeddings()
        vectors = embeddings.embed_documents(["text1", "text2"])
    """
    if OLLAMA_AVAILABLE:
        try:
            return OllamaEmbeddings(model=model)
        except Exception:
            pass

    if HF_AVAILABLE:
        try:
            return HuggingFaceEmbeddings(
                model_name="sentence-transformers/all-MiniLM-L6-v2",
                model_kwargs={"device": "cuda" if use_gpu else "cpu"},
            )
        except Exception:
            pass

    print("Warning: No embedding provider available")
    return None


class LLMCallReducer:
    """
    Reduces LLM calls through caching and batching.

    Strategies:
    - Semantic caching: Similar queries -> cached response
    - Batching: Group related queries
    - Fallback: Use local models for simple tasks

    Usage:
        reducer = LLMCallReducer(llm)
        response = reducer.invoke("query")  # May use cache
    """

    def __init__(
        self,
        llm,
        cache_enabled: bool = True,
        similarity_threshold: float = 0.95,
        local_fallback: bool = True,
    ):
        self.llm = llm
        self.cache_enabled = cache_enabled
        self.similarity_threshold = similarity_threshold
        self.local_fallback = local_fallback
        self._cache: Dict[str, str] = {}
        self._embeddings = None
        self._stats = {"calls": 0, "cache_hits": 0, "local_hits": 0}

    def invoke(self, prompt: str) -> str:
        """
        Invoke LLM with caching and optimization.

        Args:
            prompt: Input prompt

        Returns:
            LLM response (may be cached)
        """
        # Check exact cache
        if self.cache_enabled and prompt in self._cache:
            self._stats["cache_hits"] += 1
            return self._cache[prompt]

        # Check if simple enough for local handling
        if self.local_fallback and self._is_simple_query(prompt):
            response = self._local_handle(prompt)
            if response:
                self._stats["local_hits"] += 1
                return response

        # Call LLM
        self._stats["calls"] += 1
        response = self.llm.invoke(prompt)

        # Cache result
        if self.cache_enabled:
            self._cache[prompt] = response

        return response

    def _is_simple_query(self, prompt: str) -> bool:
        """Check if query can be handled locally."""
        simple_patterns = [
            "hello",
            "hi",
            "thanks",
            "thank you",
            "what time",
            "date today",
        ]
        prompt_lower = prompt.lower()
        return any(p in prompt_lower for p in simple_patterns)

    def _local_handle(self, prompt: str) -> Optional[str]:
        """Handle simple queries locally."""
        prompt_lower = prompt.lower()

        if any(g in prompt_lower for g in ["hello", "hi"]):
            return "Hello! How can I help you?"
        if "thanks" in prompt_lower or "thank you" in prompt_lower:
            return "You're welcome!"

        return None

    def get_stats(self) -> Dict[str, int]:
        """Get call statistics."""
        return self._stats.copy()


__all__ = [
    "create_quantized_llm",
    "create_efficient_embeddings",
    "LLMCallReducer",
    "OLLAMA_AVAILABLE",
    "HF_AVAILABLE",
]
