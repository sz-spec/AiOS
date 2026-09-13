#!/usr/bin/env python3
"""
Bridge script for SmartRouter - enables TypeScript/Node.js integration.

Usage:
    python router_bridge.py get_model <role> <complexity>
    python router_bridge.py list_models
    python router_bridge.py analyze "<message>"
    python router_bridge.py route "<prompt>" [--task-type <type>] [--model <name>]
"""

import sys
import json
import asyncio
import re
from pathlib import Path

from smart_router import SmartRouter

# ============================================================================
# MESSAGE ANALYSIS
# ============================================================================

ROLE_PATTERNS: dict[str, list[re.Pattern]] = {
    # Architect → gpt-5.2-pro
    "architect": [
        re.compile(r"architect|design.*system|infrastructure", re.I),
        re.compile(r"scale.*system|distributed|microservice", re.I),
        re.compile(r"system.*design|high.*level", re.I),
    ],
    # Frontend → claude-sonnet-4-5
    "frontend": [
        re.compile(r"frontend|front.?end|ui|ux", re.I),
        re.compile(r"react|vue|angular|svelte", re.I),
        re.compile(r"css|html|component|layout|responsive", re.I),
    ],
    # Backend → claude-sonnet-4-5
    "backend": [
        re.compile(r"backend|back.?end|server|api", re.I),
        re.compile(r"database|sql|nosql|mongo", re.I),
        re.compile(r"rest|graphql|endpoint|node|express", re.I),
    ],
    # Tester → gemini-3-flash
    "tester": [
        re.compile(r"test|testing|spec|coverage", re.I),
        re.compile(r"unit.*test|integration.*test|e2e", re.I),
        re.compile(r"jest|mocha|pytest|cypress|qa", re.I),
    ],
    # Reviewer → claude-opus-4-5
    "reviewer": [
        re.compile(r"review|check.*code|audit|security.*review", re.I),
        re.compile(r"best.*practice|code.*quality|pr.*review", re.I),
    ],
    # Fallback roles
    "researcher": [
        re.compile(r"research|investigate|analyze|study", re.I),
        re.compile(r"compare.*approach|trade.?off|explain.*deep", re.I),
        re.compile(r"how.*work|what.*best.*way", re.I),
    ],
    "coding": [
        re.compile(r"write.*code|implement|fix.*bug|function", re.I),
        re.compile(r"refactor|debug|class|method|variable", re.I),
    ],
}

TECHNICAL_TERMS = [
    "algorithm",
    "optimization",
    "performance",
    "security",
    "authentication",
    "database",
    "api",
    "microservice",
    "distributed",
    "concurrent",
    "async",
    "memory",
    "cache",
    "index",
    "query",
    "transaction",
    "architecture",
    "pattern",
    "scalability",
    "latency",
    "sorting",
    "data structure",
]


def analyze_message(message: str) -> dict:
    """Analyze a message to determine role and complexity."""
    role = "coding"  # default
    complexity = 5  # default mid-range
    matched_patterns = []

    # Determine role from message patterns
    for role_name, patterns in ROLE_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(message):
                role = role_name
                matched_patterns.append(f"{role_name}:{pattern.pattern[:30]}")
                break
        if matched_patterns:
            break

    # Calculate complexity (1-10)
    factors = {
        "length": min(len(message) / 500, 3),  # 0-3 points
        "code_blocks": message.count("```") * 0.5,  # 0.5 per code block
        "technical_terms": sum(
            1 for term in TECHNICAL_TERMS if term.lower() in message.lower()
        )
        * 0.3,
        "questions": message.count("?") * 0.2,
    }

    raw_complexity = (
        factors["length"]
        + factors["code_blocks"]
        + factors["technical_terms"]
        + factors["questions"]
        + 3  # base
    )
    complexity = max(1, min(10, round(raw_complexity)))

    return {
        "role": role,
        "complexity": complexity,
        "factors": factors,
        "matched_patterns": matched_patterns,
    }


CONFIG_PATH = Path(__file__).parent.parent / "config" / "router.yaml"


def get_router() -> SmartRouter:
    return SmartRouter(config_path=CONFIG_PATH)


def cmd_get_model(role: str, complexity: int) -> dict:
    """Get the best model for a given role and complexity."""
    router = get_router()
    model_name = router.get_model_for_role(role=role, complexity=complexity)
    model = router.get_model(model_name)
    return {
        "name": model.name,
        "model_id": model.model_id,
        "provider": model.provider,
        "max_tokens": model.max_tokens,
        "temperature": model.temperature,
    }


def cmd_list_models() -> dict:
    """List all available models."""
    router = get_router()
    return {
        "all": router.list_models(),
        "enabled": router.list_enabled_models(),
    }


async def cmd_route(
    prompt: str, task_type: str | None = None, model_name: str | None = None
) -> dict:
    """Route a prompt to the appropriate model."""
    router = get_router()
    response = await router.route(prompt, task_type=task_type, model_name=model_name)
    return {"response": response}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No command specified"}))
        sys.exit(1)

    command = sys.argv[1]

    try:
        if command == "get_model":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "Usage: get_model <role> <complexity>"}))
                sys.exit(1)
            role = sys.argv[2]
            complexity = int(sys.argv[3])
            result = cmd_get_model(role, complexity)

        elif command == "list_models":
            result = cmd_list_models()

        elif command == "analyze":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "Usage: analyze <message>"}))
                sys.exit(1)
            message = sys.argv[2]
            analysis = analyze_message(message)
            router = get_router()
            model_name = router.get_model_for_role(
                role=analysis["role"], complexity=analysis["complexity"]
            )
            model = router.get_model(model_name)
            result = {
                "analysis": analysis,
                "model": {
                    "name": model.name,
                    "model_id": model.model_id,
                    "provider": model.provider,
                    "max_tokens": model.max_tokens,
                    "temperature": model.temperature,
                },
            }

        elif command == "route":
            if len(sys.argv) < 3:
                print(
                    json.dumps(
                        {
                            "error": "Usage: route <prompt> [--task-type <type>] [--model <name>]"
                        }
                    )
                )
                sys.exit(1)
            prompt = sys.argv[2]
            task_type = None
            model_name = None

            i = 3
            while i < len(sys.argv):
                if sys.argv[i] == "--task-type" and i + 1 < len(sys.argv):
                    task_type = sys.argv[i + 1]
                    i += 2
                elif sys.argv[i] == "--model" and i + 1 < len(sys.argv):
                    model_name = sys.argv[i + 1]
                    i += 2
                else:
                    i += 1

            result = asyncio.run(cmd_route(prompt, task_type, model_name))

        else:
            print(json.dumps({"error": f"Unknown command: {command}"}))
            sys.exit(1)

        print(json.dumps(result))

    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
