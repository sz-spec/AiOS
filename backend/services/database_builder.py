"""
Database Builder Service
========================
Converts visual table definitions into
REST API routes and CRUD frontend components.
"""

from typing import Dict, List


def generate_schema(tables: List[Dict]) -> Dict[str, str]:
    """Generate a Convex-style schema file from table definitions."""
    if not tables:
        return {}
    lines = ['import { defineSchema, defineTable } from "convex/server";', ""]
    for table in tables:
        table_name = _sanitize_name(table["name"])
        cols = table.get("columns", [])
        field_lines = ", ".join(
            f'{_sanitize_name(c["name"])}: v.{_map_convex_type(c.get("type", "text"))}()'
            for c in cols
        )
        lines.append(f"  {table_name}: defineTable({{ {field_lines} }}),")
    schema_code = "\n".join(
        [lines[0], lines[1], "export default defineSchema({"] + lines[2:] + ["});", ""]
    )
    return {"src/schema.ts": schema_code}


def _map_convex_type(col_type: str) -> str:
    """Map visual column type to Convex validator type."""
    mapping = {
        "text": "string",
        "number": "number",
        "date": "string",
        "boolean": "boolean",
        "image": "string",
        "link": "string",
    }
    return mapping.get(col_type, "string")


def generate_api_routes(tables: List[Dict]) -> Dict[str, str]:
    """Generate REST API code for each table."""
    files = {}
    for table in tables:
        table_name = _sanitize_name(table["name"])
        route_code = f"""// Auto-generated CRUD API for {table_name}
import {{ ConvexHttpClient }} from 'convex/browser';

const convex = new ConvexHttpClient(process.env.NEXT_PUBLIC_CONVEX_URL!);

export async function GET() {{
  try {{
    const data = await convex.query('api.{table_name}.list');
    return Response.json(data);
  }} catch (error: any) {{
    return Response.json({{ error: error.message }}, {{ status: 500 }});
  }}
}}

export async function POST(request: Request) {{
  const body = await request.json();
  try {{
    const data = await convex.mutation('api.{table_name}.create', body);
    return Response.json(data, {{ status: 201 }});
  }} catch (error: any) {{
    return Response.json({{ error: error.message }}, {{ status: 400 }});
  }}
}}
"""
        files[f"src/api/{table_name}.ts"] = route_code
    return files


def generate_crud_components(tables: List[Dict]) -> Dict[str, str]:
    """Generate React CRUD components for each table."""
    files = {}
    for table in tables:
        table_name = _sanitize_name(table["name"])
        display_name = table["name"]
        columns = table.get("columns", [])

        col_headers = "\n".join(f'          <th>{col["name"]}</th>' for col in columns)
        col_cells = "\n".join(
            f'          <td>{{item.{_sanitize_name(col["name"])}}}</td>'
            for col in columns
        )

        component = f"""import React, {{ useState, useEffect }} from 'react';

export default function {display_name.replace(" ", "")}List() {{
  const [items, setItems] = useState<any[]>([]);

  useEffect(() => {{
    fetch('/api/{table_name}')
      .then(r => r.json())
      .then(setItems)
      .catch(console.error);
  }}, []);

  return (
    <div>
      <h2>{display_name}</h2>
      <table>
        <thead>
          <tr>
{col_headers}
          </tr>
        </thead>
        <tbody>
          {{items.map(item => (
            <tr key={{item.id}}>
{col_cells}
            </tr>
          ))}}
        </tbody>
      </table>
    </div>
  );
}}
"""
        files[f"src/components/{display_name.replace(' ', '')}List.tsx"] = component
    return files


def _sanitize_name(name: str) -> str:
    """Convert display name to safe SQL/code identifier."""
    return name.lower().replace(" ", "_").replace("-", "_")


def _map_column_type(col_type: str) -> str:
    """Map visual column type to PostgreSQL type."""
    mapping = {
        "text": "TEXT",
        "number": "NUMERIC",
        "date": "TIMESTAMPTZ",
        "boolean": "BOOLEAN DEFAULT FALSE",
        "image": "TEXT",  # URL
        "link": "TEXT",  # URL
    }
    return mapping.get(col_type, "TEXT")
