"""
AI Code Review Module

Based on Forum Recommendations (Shakudo, r/ChatGPTCoding, r/nocode - Dec 2025):
- Multi-provider support (Claude, Copilot SDK, Ollama)
- GitHub webhook integration for auto-trigger
- 90% bug detection accuracy with Copilot
- 40% faster than manual review
Components:
- service.py: Main AI review service (Python/Claude)
- routes.py: FastAPI REST endpoints
- rules.py: Static analysis rules (50+)
- multi_provider.py: Multi-provider review service
- github_webhook.py: GitHub webhook handler
- code-review.js: Node.js integration

Usage:
    from code_review import AICodeReviewService, MultiProviderReviewService

    # Single provider
    service = AICodeReviewService()
    result = await service.review_code(project_id, files)

    # Multi-provider with consensus
    service = MultiProviderReviewService()
    result = await service.consensus_review(context)
"""

from .service import (
    AICodeReviewService,
    CodeReviewResult,
    ReviewFinding,
    ReviewSeverity,
    ReviewCategory,
    ReviewSummary,
    CodeLocation,
)
from .rules import (
    SecurityRules,
    PerformanceRules,
    StyleRules,
    BestPracticeRules,
    StaticAnalyzer,
)
from .multi_provider import (
    MultiProviderReviewService,
    ReviewProvider,
    ReviewContext,
    ProviderConfig,
    CopilotReviewProvider,
    ClaudeReviewProvider,
    OllamaReviewProvider,
    ReviewStorage,
    StoredReview,
)

__all__ = [
    # Main service
    "AICodeReviewService",
    "CodeReviewResult",
    "ReviewFinding",
    "ReviewSeverity",
    "ReviewCategory",
    "ReviewSummary",
    "CodeLocation",
    # Static rules
    "SecurityRules",
    "PerformanceRules",
    "StyleRules",
    "BestPracticeRules",
    "StaticAnalyzer",
    # Multi-provider
    "MultiProviderReviewService",
    "ReviewProvider",
    "ReviewContext",
    "ProviderConfig",
    "CopilotReviewProvider",
    "ClaudeReviewProvider",
    "OllamaReviewProvider",
    "ReviewStorage",
    "StoredReview",
]

# Version info
__version__ = "2.0.0"
__providers__ = ["claude", "copilot", "ollama", "openai"]
