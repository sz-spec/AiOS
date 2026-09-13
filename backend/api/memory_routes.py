"""
Memory API Routes
==================

Endpoints for managing development memory.
"""

from fastapi import APIRouter, HTTPException, Depends
from api.deps import get_current_user, AuthenticatedUser
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime

from memory.dev_memory import get_dev_memory, DevMemory

router = APIRouter()

# Auto-tagging keywords mapping
AUTO_TAG_KEYWORDS = {
    # Priority tags
    "urgent": ["urgent", "asap", "immediately", "critical", "emergency"],
    "important": ["important", "key", "essential", "must", "required"],
    # Scope tags
    "frontend": [
        "frontend",
        "react",
        "vue",
        "angular",
        "css",
        "html",
        "ui",
        "component",
        "jsx",
        "tsx",
    ],
    "backend": [
        "backend",
        "api",
        "server",
        "database",
        "python",
        "node",
        "fastapi",
        "express",
    ],
    "database": ["database", "sql", "postgres", "mongodb", "redis", "query", "schema"],
    "devops": [
        "devops",
        "deploy",
        "docker",
        "kubernetes",
        "ci/cd",
        "pipeline",
        "aws",
        "cloud",
    ],
    # Type tags
    "bug": ["bug", "error", "fix", "issue", "problem", "broken", "crash"],
    "feature": ["feature", "new", "add", "implement", "create"],
    "improvement": ["improve", "optimize", "enhance", "better", "refactor"],
    "documentation": ["document", "readme", "docs", "comment", "explain"],
    # Status tags
    "resolved": ["resolved", "fixed", "done", "completed", "solved"],
    "pending": ["pending", "todo", "later", "backlog", "waiting"],
}


def auto_generate_tags(content: str) -> List[str]:
    """Automatically generate tags based on content keywords."""
    content_lower = content.lower()
    tags = []

    for tag, keywords in AUTO_TAG_KEYWORDS.items():
        for keyword in keywords:
            if keyword in content_lower:
                tags.append(tag)
                break  # Only add tag once

    return tags[:5]  # Limit to 5 auto-tags


def find_similar_memories(memory, content: str, threshold: float = 0.7) -> List[Dict]:
    """Find memories similar to the given content."""
    results = memory.query(content, top_k=5, min_relevance=threshold)
    return results


class StoreMemoryRequest(BaseModel):
    """Request body for POST /store. All fields required for validation."""

    content: str
    memory_type: str = "conversation"
    metadata: Optional[Dict[str, Any]] = None
    auto_tag: bool = True


class AddMemoryRequest(BaseModel):
    content: str
    memory_type: str = "conversation"
    metadata: Optional[Dict[str, Any]] = None
    auto_tag: bool = True  # Auto-generate tags by default


class QueryMemoryRequest(BaseModel):
    query: str
    top_k: int = 5
    memory_type: Optional[str] = None
    min_relevance: float = 0.0


@router.get(
    "/",
    summary="Get memory statistics",
    description="Return aggregate statistics about the DevMemory store including total entries and types",
)
async def get_memory_stats(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get memory statistics."""
    memory = get_dev_memory()
    return memory.get_stats()


@router.post(
    "/store",
    summary="Store memory entry",
    description="Store a new memory entry (alias for /add)",
)
async def store_memory(
    request: StoreMemoryRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Store a new memory entry. Alias for /add with same behavior."""
    memory = get_dev_memory()

    metadata = request.metadata or {}
    if request.auto_tag:
        auto_tags = auto_generate_tags(request.content)
        existing_tags = metadata.get("tags", [])
        if isinstance(existing_tags, list):
            metadata["tags"] = list(set(existing_tags + auto_tags))
        else:
            metadata["tags"] = auto_tags
        metadata["auto_tagged"] = True

    entry = memory.add(
        content=request.content, memory_type=request.memory_type, metadata=metadata
    )

    if entry:
        return {
            "success": True,
            "entry": entry.to_dict(),
            "auto_tags": metadata.get("tags", []),
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to store memory")


@router.post(
    "/add",
    summary="Add memory entry",
    description="Store a new memory entry with optional auto-tagging and duplicate detection",
)
async def add_memory(
    request: AddMemoryRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Add a new memory entry with auto-tagging."""
    memory = get_dev_memory()

    # Check for similar existing memories
    similar = find_similar_memories(memory, request.content, threshold=0.85)

    # Auto-generate tags if enabled
    metadata = request.metadata or {}
    if request.auto_tag:
        auto_tags = auto_generate_tags(request.content)
        existing_tags = metadata.get("tags", [])
        if isinstance(existing_tags, list):
            metadata["tags"] = list(set(existing_tags + auto_tags))
        else:
            metadata["tags"] = auto_tags
        metadata["auto_tagged"] = True

    entry = memory.add(
        content=request.content, memory_type=request.memory_type, metadata=metadata
    )

    if entry:
        return {
            "success": True,
            "entry": entry.to_dict(),
            "auto_tags": metadata.get("tags", []),
            "similar_memories": similar[:3] if similar else [],  # Return top 3 similar
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to add memory")


@router.post(
    "/suggest-tags",
    summary="Suggest tags",
    description="Generate tag suggestions for content based on keyword matching without saving",
)
async def suggest_tags(
    content: str, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Suggest tags for given content without saving."""
    tags = auto_generate_tags(content)
    return {"suggested_tags": tags}


@router.get(
    "/similar/{insight_id}",
    summary="Find similar memories",
    description="Find memories semantically similar to a specific entry above a relevance threshold",
)
async def get_similar_memories(
    insight_id: str,
    threshold: float = 0.7,
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get memories similar to a specific memory."""
    memory = get_dev_memory()
    insight = memory.get_by_id(insight_id)
    if not insight:
        raise HTTPException(status_code=404, detail="Memory not found")

    similar = find_similar_memories(memory, insight.get("content", ""), threshold)
    # Filter out the original memory
    similar = [s for s in similar if s.get("id") != insight_id]

    return {"original": insight, "similar": similar, "count": len(similar)}


@router.post(
    "/consolidate",
    summary="Consolidate similar memories",
    description="Identify and group highly similar memories for potential deduplication",
)
async def consolidate_similar_memories(
    threshold: float = 0.85, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Find and group similar memories for consolidation."""
    memory = get_dev_memory()
    all_memories = memory.get_recent(limit=100)

    # Group similar memories
    groups = []
    processed = set()

    for mem in all_memories:
        if mem["id"] in processed:
            continue

        similar = find_similar_memories(memory, mem["content"], threshold)
        similar_ids = [
            s["id"]
            for s in similar
            if s["id"] != mem["id"] and s["id"] not in processed
        ]

        if similar_ids:
            group = {
                "primary": mem,
                "similar": [s for s in similar if s["id"] in similar_ids],
                "count": len(similar_ids) + 1,
            }
            groups.append(group)
            processed.add(mem["id"])
            processed.update(similar_ids)

    return {
        "groups": groups,
        "total_groups": len(groups),
        "total_memories_grouped": len(processed),
    }


@router.post(
    "/query",
    summary="Query memories",
    description="Search memories by semantic similarity with optional type filtering and relevance threshold",
)
async def query_memory(
    request: QueryMemoryRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Query memories by semantic similarity."""
    memory = get_dev_memory()

    results = memory.query(
        query=request.query,
        top_k=request.top_k,
        memory_type=request.memory_type,
        min_relevance=request.min_relevance,
    )

    return {"query": request.query, "results": results, "count": len(results)}


@router.get(
    "/recent",
    summary="Get recent memories",
    description="Return the most recent memory entries with optional type filtering",
)
async def get_recent_memories(
    limit: int = 20,
    memory_type: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get most recent memories."""
    memory = get_dev_memory()

    results = memory.get_recent(limit=limit, memory_type=memory_type)

    return {"memories": results, "count": len(results)}


@router.get(
    "/types",
    summary="Get memory types",
    description="Return all available memory type categories with descriptions",
)
async def get_memory_types(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get available memory types."""
    return {
        "types": DevMemory.MEMORY_TYPES,
        "descriptions": {
            "conversation": "Chat messages and discussions",
            "decision": "Technical decisions made",
            "code_change": "Code modifications",
            "learning": "Insights and patterns learned",
            "error": "Errors encountered",
            "solution": "Solutions implemented",
            "context": "Project context and structure",
        },
    }


@router.delete(
    "/clear",
    summary="Clear memories",
    description="Delete all memories or only those of a specific type. Use with caution",
)
async def clear_memories(
    memory_type: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Clear memories (optionally by type)."""
    memory = get_dev_memory()

    success = memory.clear(memory_type)

    if success:
        return {
            "success": True,
            "message": f"Cleared {'all' if not memory_type else memory_type} memories",
        }
    else:
        return {
            "success": False,
            "message": "Failed to clear memories (may be using JSON fallback)",
        }


# Convenience endpoint for quick memory addition
@router.post(
    "/remember",
    summary="Quick remember",
    description="Shortcut endpoint to quickly store a memory with optional comma-separated tags and files",
)
async def quick_remember(
    content: str,
    memory_type: str = "conversation",
    tags: Optional[str] = None,
    files: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Quick endpoint to add a memory."""
    memory = get_dev_memory()

    metadata = {}
    if tags:
        metadata["tags"] = [t.strip() for t in tags.split(",")]
    if files:
        metadata["files"] = [f.strip() for f in files.split(",")]

    entry = memory.add(content, memory_type, metadata)

    return {"success": entry is not None, "id": entry.id if entry else None}


# Convenience endpoint for quick recall
@router.get(
    "/recall",
    summary="Quick recall",
    description="Shortcut endpoint to quickly search memories by semantic similarity",
)
async def quick_recall(
    q: str, k: int = 5, user: AuthenticatedUser = Depends(get_current_user)
) -> List[Dict[str, Any]]:
    """Quick endpoint to query insights."""
    memory = get_dev_memory()
    return memory.query(q, top_k=k)


class UpdateInsightRequest(BaseModel):
    content: Optional[str] = None
    memory_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None  # Tags can be updated directly


class BulkDeleteRequest(BaseModel):
    ids: List[str]


class ImportRequest(BaseModel):
    insights: List[Dict[str, Any]]


@router.get("/{insight_id}", summary="Get insight by ID")
async def get_insight(
    insight_id: str, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get a single insight by ID."""
    memory = get_dev_memory()
    result = memory.get_by_id(insight_id)
    if result:
        return result
    raise HTTPException(status_code=404, detail="Insight not found")


@router.put(
    "/{insight_id}",
    summary="Update insight",
    description="Update an existing memory entry's content, type, metadata, or tags",
)
async def update_insight(
    insight_id: str,
    request: UpdateInsightRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Update an insight."""
    memory = get_dev_memory()

    # Handle tags update - merge into metadata
    metadata = request.metadata or {}
    if request.tags is not None:
        metadata["tags"] = request.tags

    success = memory.update(
        insight_id,
        content=request.content,
        memory_type=request.memory_type,
        metadata=metadata if metadata else None,
    )
    if success:
        return {"success": True, "message": "Insight updated"}
    raise HTTPException(status_code=404, detail="Insight not found or update failed")


@router.delete("/{insight_id}", summary="Delete insight")
async def delete_insight(
    insight_id: str, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Delete a single insight."""
    memory = get_dev_memory()
    success = memory.delete(insight_id)
    if success:
        return {"success": True, "message": "Insight deleted"}
    raise HTTPException(status_code=404, detail="Insight not found")


@router.post(
    "/bulk-delete",
    summary="Bulk delete insights",
    description="Delete multiple memory entries by their IDs in a single operation",
)
async def bulk_delete_insights(
    request: BulkDeleteRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Delete multiple insights."""
    memory = get_dev_memory()
    count = memory.delete_bulk(request.ids)
    return {"success": True, "deleted": count}


@router.get(
    "/export/all",
    summary="Export all insights",
    description="Export all memory entries as a JSON payload for backup or migration",
)
async def export_insights(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Export all insights."""
    memory = get_dev_memory()
    insights = memory.export_all()
    return {
        "insights": insights,
        "count": len(insights),
        "exported_at": datetime.now().isoformat(),
    }


@router.post(
    "/import",
    summary="Import insights",
    description="Import memory entries from a previously exported JSON payload",
)
async def import_insights(
    request: ImportRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """Import insights."""
    memory = get_dev_memory()
    count = memory.import_memories(request.insights)
    return {"success": True, "imported": count}
