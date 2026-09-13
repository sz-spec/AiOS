"""
Database Builder Routes
=======================
API endpoints for visual database schema management.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List

from api.deps import get_current_user, AuthenticatedUser
from services.project_service import get_project_service
from services.database_builder import (
    generate_api_routes,
    generate_crud_components,
    generate_schema,
)

router = APIRouter(prefix="/api/v1/database", tags=["Database"])


class ColumnDef(BaseModel):
    id: str
    name: str
    type: str


class TableDef(BaseModel):
    name: str
    columns: List[ColumnDef]


class GenerateRequest(BaseModel):
    project_id: str
    tables: List[TableDef]


class GenerateResponse(BaseModel):
    files_generated: int
    ddl: str = ""


@router.post("/generate", response_model=GenerateResponse)
async def generate_database(
    req: GenerateRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """Generate API routes and CRUD components from visual table definitions."""
    service = get_project_service()
    project = service.get(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    tables_dicts = [t.model_dump() for t in req.tables]

    # Generate schema
    schema_files = generate_schema(tables_dicts)

    # Generate API routes
    api_files = generate_api_routes(tables_dicts)

    # Generate CRUD components
    component_files = generate_crud_components(tables_dicts)

    # Merge all generated files into project
    all_files = {**schema_files, **api_files, **component_files}
    if all_files:
        service.update_files(req.project_id, all_files)

    # Build DDL from table definitions
    ddl_parts = []
    for t in tables_dicts:
        cols = ", ".join(f"{c['name']} {c['type']}" for c in t.get("columns", []))
        ddl_parts.append(f"CREATE TABLE {t['name']} ({cols});")
    ddl = "\n".join(ddl_parts)

    return GenerateResponse(files_generated=len(all_files), ddl=ddl)
