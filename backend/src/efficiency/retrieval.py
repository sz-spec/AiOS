"""
Retrieval Utilities
===================
Hybrid search and context management for efficient retrieval.
"""

from typing import List, Dict, Any

from .llm import create_efficient_embeddings

# Optional FAISS import
try:
    from langchain_community.vectorstores import FAISS

    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False


class HybridRetriever:
    """
    Hybrid retriever combining BM25 + semantic search.

    Reduces LLM calls from hundreds to single calls by:
    - Using keyword matching (BM25) for exact matches
    - Using embeddings for semantic similarity
    - Combining results for best of both

    Usage:
        retriever = HybridRetriever(docs)
        results = retriever.search("query", k=5)
    """

    def __init__(self, documents: List[str], embeddings=None, use_cache: bool = True):
        self.documents = documents
        self.embeddings = embeddings or create_efficient_embeddings()
        self.use_cache = use_cache
        self._cache: Dict[str, List[str]] = {}
        self._vectorstore = None

        if FAISS_AVAILABLE and self.embeddings:
            try:
                self._vectorstore = FAISS.from_texts(documents, self.embeddings)
            except Exception as e:
                print(f"Warning: Could not create vectorstore: {e}")

    def search(
        self, query: str, k: int = 5, alpha: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search with BM25 + semantic.

        Args:
            query: Search query
            k: Number of results
            alpha: Weight for semantic (0=BM25 only, 1=semantic only)

        Returns:
            List of results with scores
        """
        # Check cache
        cache_key = f"{query}:{k}:{alpha}"
        if self.use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        # BM25-style keyword matching
        bm25_results = self._bm25_search(query, k)

        # Semantic search
        semantic_results = self._semantic_search(query, k)

        # Combine with weights
        combined = self._combine_results(bm25_results, semantic_results, alpha, k)

        # Cache results
        if self.use_cache:
            self._cache[cache_key] = combined

        return combined

    def _bm25_search(self, query: str, k: int) -> List[Dict[str, Any]]:
        """Simple TF-IDF based search."""
        query_terms = set(query.lower().split())
        scored = []

        for i, doc in enumerate(self.documents):
            doc_terms = set(doc.lower().split())
            overlap = len(query_terms & doc_terms)
            if overlap > 0:
                score = overlap / len(query_terms)
                scored.append({"index": i, "text": doc, "score": score, "type": "bm25"})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]

    def _semantic_search(self, query: str, k: int) -> List[Dict[str, Any]]:
        """Semantic similarity search."""
        if not self._vectorstore:
            return []

        try:
            docs = self._vectorstore.similarity_search_with_score(query, k=k)
            return [
                {
                    "index": i,
                    "text": doc.page_content,
                    "score": 1 - score,
                    "type": "semantic",
                }
                for i, (doc, score) in enumerate(docs)
            ]
        except Exception:
            return []

    def _combine_results(
        self, bm25: List[Dict], semantic: List[Dict], alpha: float, k: int
    ) -> List[Dict[str, Any]]:
        """Combine and re-rank results."""
        seen = set()
        combined = []

        # Normalize and combine
        for result in bm25:
            text = result["text"]
            if text not in seen:
                seen.add(text)
                result["final_score"] = result["score"] * (1 - alpha)
                combined.append(result)

        for result in semantic:
            text = result["text"]
            if text in seen:
                # Boost score for items in both
                for c in combined:
                    if c["text"] == text:
                        c["final_score"] += result["score"] * alpha
                        break
            else:
                seen.add(text)
                result["final_score"] = result["score"] * alpha
                combined.append(result)

        combined.sort(key=lambda x: x["final_score"], reverse=True)
        return combined[:k]


class ContextManager:
    """
    Efficient context management to reduce token usage.

    Features:
    - Automatic summarization of long contexts
    - Sliding window for recent messages
    - Token counting and limiting

    Usage:
        manager = ContextManager(max_tokens=4096)
        optimized = manager.optimize_context(messages)
    """

    def __init__(self, max_tokens: int = 4096, window_size: int = 10):
        self.max_tokens = max_tokens
        self.window_size = window_size

    def optimize_context(
        self, messages: List[Dict[str, str]], prioritize_recent: bool = True
    ) -> List[Dict[str, str]]:
        """
        Optimize context to fit within token limit.

        Args:
            messages: List of message dicts
            prioritize_recent: Keep recent messages

        Returns:
            Optimized message list
        """
        if not messages:
            return []

        # Estimate tokens (rough: 4 chars = 1 token)
        def estimate_tokens(msg):
            content = msg.get("content", "")
            return len(content) // 4

        total_tokens = sum(estimate_tokens(m) for m in messages)

        if total_tokens <= self.max_tokens:
            return messages

        # Strategy: Keep system + recent + summarize middle
        optimized = []

        # Keep system messages
        system_msgs = [m for m in messages if m.get("role") == "system"]
        optimized.extend(system_msgs)

        # Keep recent messages
        non_system = [m for m in messages if m.get("role") != "system"]
        recent = (
            non_system[-self.window_size :]
            if prioritize_recent
            else non_system[: self.window_size]
        )

        # Summarize middle if too long
        middle = (
            non_system[: -self.window_size]
            if prioritize_recent
            else non_system[self.window_size :]
        )
        if middle:
            summary = self._summarize_messages(middle)
            if summary:
                optimized.append(
                    {
                        "role": "system",
                        "content": f"[Previous conversation summary: {summary}]",
                    }
                )

        optimized.extend(recent)

        return optimized

    def _summarize_messages(self, messages: List[Dict[str, str]]) -> str:
        """Create a brief summary of messages."""
        if not messages:
            return ""

        # Simple summary: count messages and topics
        count = len(messages)
        topics = set()

        for msg in messages:
            content = msg.get("content", "")[:100]
            # Extract potential topics (nouns, roughly)
            words = content.split()[:5]
            topics.update(w for w in words if len(w) > 4)

        topics_str = ", ".join(list(topics)[:5])
        return f"{count} messages about: {topics_str}"


# ------------------------------------------------------------------
# Context Quality Scoring (Phase I5)
# ------------------------------------------------------------------

from dataclasses import dataclass


@dataclass
class ContextScore:
    """Quality score for a set of context parts."""

    total_tokens: int = 0
    useful_tokens: int = 0
    relevance: float = 0.0  # 0.0 - 1.0
    duplicates: int = 0
    recommendation: str = ""  # "use_as_is", "trim", "augment"


def score_context(context_parts: List[str], query: str = "") -> ContextScore:
    """Score the quality of context parts for LLM consumption.

    Analyzes token usage, relevance, and duplication to help decide
    whether context should be used as-is, trimmed, or augmented.

    Args:
        context_parts: List of context strings.
        query: Optional query to measure relevance against.

    Returns:
        ContextScore with metrics and recommendation.
    """
    if not context_parts:
        return ContextScore(recommendation="augment")

    # Estimate tokens (rough: 4 chars = 1 token)
    total_tokens = sum(len(p) // 4 for p in context_parts)

    # Detect duplicates via fingerprinting
    fingerprints = set()
    duplicates = 0
    unique_parts = []
    for part in context_parts:
        # Use first 100 chars as fingerprint
        fp = part[:100].strip().lower()
        if fp in fingerprints:
            duplicates += 1
        else:
            fingerprints.add(fp)
            unique_parts.append(part)

    useful_tokens = sum(len(p) // 4 for p in unique_parts)

    # Relevance scoring (keyword overlap with query)
    relevance = 0.0
    if query:
        query_terms = set(query.lower().split())
        if query_terms:
            scores = []
            for part in unique_parts:
                part_terms = set(part.lower().split())
                overlap = len(query_terms & part_terms)
                scores.append(overlap / len(query_terms) if query_terms else 0)
            relevance = sum(scores) / len(scores) if scores else 0.0
    else:
        relevance = 0.5  # Unknown relevance without query

    # Recommendation
    if total_tokens == 0:
        recommendation = "augment"
    elif duplicates > len(context_parts) * 0.3:
        recommendation = "trim"
    elif total_tokens > 8000:
        recommendation = "trim"
    elif relevance < 0.2 and query:
        recommendation = "augment"
    elif useful_tokens < 100:
        recommendation = "augment"
    else:
        recommendation = "use_as_is"

    return ContextScore(
        total_tokens=total_tokens,
        useful_tokens=useful_tokens,
        relevance=round(relevance, 3),
        duplicates=duplicates,
        recommendation=recommendation,
    )


__all__ = [
    "HybridRetriever",
    "ContextManager",
    "ContextScore",
    "score_context",
    "FAISS_AVAILABLE",
]
