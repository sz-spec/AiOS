"""
Edit Agent
==========
Lightweight agent for iterative code edits.
NOT the full 9-agent pipeline — this handles single-instruction changes
like "make the header bigger" or "add a contact form".

Uses a single LLM call with current code + instruction → minimal diff.
"""

import json
from typing import Dict


async def apply_edit(
    instruction: str,
    current_files: Dict[str, str],
    project_description: str = "",
) -> Dict:
    """
    Apply a single edit instruction to project files.

    Returns:
        {
            "message": "Description of what was changed",
            "updated_files": {"path": "new content", ...}
        }
    """
    # In production, this calls the LLM with a focused prompt.
    # For now, return a simulated response.
    try:
        from ai.llm.providers import LLM

        llm = LLM()

        # Build file context (truncate large files)
        file_context = ""
        for path, content in current_files.items():
            truncated = content[:2000] if len(content) > 2000 else content
            file_context += f"\n--- {path} ---\n{truncated}\n"

        prompt = f"""You are a code editor. Apply this change to the project files.

Project description: {project_description}

Current files:
{file_context}

User instruction: {instruction}

Respond with JSON only:
{{
  "message": "Brief description of what you changed",
  "updated_files": {{
    "path/to/file.tsx": "full updated file content"
  }}
}}

Only include files that need changes. Return the COMPLETE file content for each changed file."""

        response = await llm.generate(prompt, role="coding")
        result = json.loads(response.content)
        return result

    except Exception:
        # Fallback: return instruction as message, no file changes
        return {
            "message": f"I understand you want to: {instruction}. This will be applied when AI is configured.",
            "updated_files": {},
        }
