"""
DB Architect Agent — IE-5 Phase 2.5
====================================
Schema diffing + migration generation.

Extracts database schemas from generated backend code (Prisma, SQLAlchemy,
or raw SQL), diffs against the previous build's schema, and generates
ALTER TABLE migration files with rollback scripts.

Pipeline position: after guardrails_gate, before tester.
Non-blocking: failures return empty results, pipeline continues.
"""

import re
import json
import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

# =========================================================================
# Data Structures
# =========================================================================


class ColumnType(str, Enum):
    """Common column types across ORMs."""

    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"
    JSON = "json"
    UUID = "uuid"
    SERIAL = "serial"
    BIGINT = "bigint"
    DECIMAL = "decimal"
    BLOB = "blob"
    UNKNOWN = "unknown"


@dataclass
class Column:
    """A single column in a table."""

    name: str
    type: ColumnType
    nullable: bool = True
    default: Optional[str] = None
    primary_key: bool = False
    unique: bool = False
    references: Optional[str] = None  # "other_table.column"

    def to_dict(self) -> dict:
        return {**asdict(self), "type": self.type.value}


@dataclass
class Index:
    """A database index."""

    name: str
    table: str
    columns: List[str]
    unique: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Table:
    """A database table definition."""

    name: str
    columns: List[Column] = field(default_factory=list)
    indexes: List[Index] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "columns": [c.to_dict() for c in self.columns],
            "indexes": [i.to_dict() for i in self.indexes],
        }

    def column_names(self) -> set:
        return {c.name for c in self.columns}

    def get_column(self, name: str) -> Optional[Column]:
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class Schema:
    """Complete database schema."""

    tables: List[Table] = field(default_factory=list)
    orm_type: str = "unknown"  # "prisma" | "sqlalchemy" | "raw_sql" | "unknown"

    def to_dict(self) -> dict:
        return {
            "tables": [t.to_dict() for t in self.tables],
            "orm_type": self.orm_type,
        }

    def table_names(self) -> set:
        return {t.name for t in self.tables}

    def get_table(self, name: str) -> Optional[Table]:
        return next((t for t in self.tables if t.name == name), None)

    def fingerprint(self) -> str:
        """Deterministic hash of the schema for quick equality check."""
        canonical = json.dumps(self.to_dict(), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# =========================================================================
# Schema Diff
# =========================================================================


class DiffType(str, Enum):
    TABLE_ADDED = "table_added"
    TABLE_REMOVED = "table_removed"
    COLUMN_ADDED = "column_added"
    COLUMN_REMOVED = "column_removed"
    COLUMN_MODIFIED = "column_modified"
    INDEX_ADDED = "index_added"
    INDEX_REMOVED = "index_removed"


@dataclass
class SchemaDiff:
    """A single diff entry between two schemas."""

    diff_type: DiffType
    table_name: str
    column_name: Optional[str] = None
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None
    data_loss_risk: bool = False
    risk_description: str = ""

    def to_dict(self) -> dict:
        return {**asdict(self), "diff_type": self.diff_type.value}


@dataclass
class MigrationFile:
    """A generated migration file."""

    filename: str
    content: str
    rollback_filename: str
    rollback_content: str
    description: str
    diffs: List[SchemaDiff] = field(default_factory=list)
    has_data_loss_risk: bool = False

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "content": self.content,
            "rollback_filename": self.rollback_filename,
            "rollback_content": self.rollback_content,
            "description": self.description,
            "diffs": [d.to_dict() for d in self.diffs],
            "has_data_loss_risk": self.has_data_loss_risk,
        }


# =========================================================================
# Type Mapping
# =========================================================================

# Prisma type → ColumnType
_PRISMA_TYPE_MAP = {
    "string": ColumnType.TEXT,
    "int": ColumnType.INTEGER,
    "float": ColumnType.FLOAT,
    "boolean": ColumnType.BOOLEAN,
    "datetime": ColumnType.TIMESTAMP,
    "json": ColumnType.JSON,
    "bigint": ColumnType.BIGINT,
    "decimal": ColumnType.DECIMAL,
    "bytes": ColumnType.BLOB,
}

# SQLAlchemy type → ColumnType
_SQLALCHEMY_TYPE_MAP = {
    "string": ColumnType.TEXT,
    "text": ColumnType.TEXT,
    "integer": ColumnType.INTEGER,
    "float": ColumnType.FLOAT,
    "boolean": ColumnType.BOOLEAN,
    "datetime": ColumnType.TIMESTAMP,
    "date": ColumnType.TIMESTAMP,
    "json": ColumnType.JSON,
    "uuid": ColumnType.UUID,
    "biginteger": ColumnType.BIGINT,
    "numeric": ColumnType.DECIMAL,
    "largebinary": ColumnType.BLOB,
}

# SQL type → ColumnType
_SQL_TYPE_MAP = {
    "text": ColumnType.TEXT,
    "varchar": ColumnType.TEXT,
    "char": ColumnType.TEXT,
    "character varying": ColumnType.TEXT,
    "int": ColumnType.INTEGER,
    "integer": ColumnType.INTEGER,
    "smallint": ColumnType.INTEGER,
    "serial": ColumnType.SERIAL,
    "bigserial": ColumnType.BIGINT,
    "bigint": ColumnType.BIGINT,
    "float": ColumnType.FLOAT,
    "double precision": ColumnType.FLOAT,
    "real": ColumnType.FLOAT,
    "boolean": ColumnType.BOOLEAN,
    "bool": ColumnType.BOOLEAN,
    "timestamp": ColumnType.TIMESTAMP,
    "timestamptz": ColumnType.TIMESTAMP,
    "date": ColumnType.TIMESTAMP,
    "json": ColumnType.JSON,
    "jsonb": ColumnType.JSON,
    "uuid": ColumnType.UUID,
    "numeric": ColumnType.DECIMAL,
    "decimal": ColumnType.DECIMAL,
    "bytea": ColumnType.BLOB,
    "blob": ColumnType.BLOB,
}


# =========================================================================
# ORM Detection
# =========================================================================


def detect_orm_type(backend_files: Dict[str, str]) -> str:
    """
    Detect which ORM is used in the backend code.

    Heuristics:
    - Any file ending in .prisma or containing 'model ... {' → "prisma"
    - Python file with 'from sqlalchemy' or 'Column(' → "sqlalchemy"
    - TypeScript file with 'pgTable(' or 'sqliteTable(' → "drizzle"
    - .sql files with CREATE TABLE → "raw_sql"
    - Otherwise → "unknown"
    """
    for path, content in backend_files.items():
        path_lower = path.lower()
        if path_lower.endswith(".prisma"):
            return "prisma"
        if re.search(r"^model\s+\w+\s*\{", content, re.MULTILINE):
            return "prisma"

    for path, content in backend_files.items():
        path_lower = path.lower()
        if path_lower.endswith(".py"):
            if "from sqlalchemy" in content or "import sqlalchemy" in content:
                return "sqlalchemy"
            if "Column(" in content and (
                "Base)" in content or "DeclarativeBase" in content
            ):
                return "sqlalchemy"

    for path, content in backend_files.items():
        path_lower = path.lower()
        if path_lower.endswith((".ts", ".js")):
            if (
                "pgTable(" in content
                or "sqliteTable(" in content
                or "mysqlTable(" in content
            ):
                return "drizzle"

    for path, content in backend_files.items():
        if re.search(r"CREATE\s+TABLE", content, re.IGNORECASE):
            return "raw_sql"

    return "unknown"


# =========================================================================
# Schema Parsers
# =========================================================================


def parse_prisma(content: str) -> Schema:
    """
    Parse a Prisma schema file into our Schema model.

    Parses:
    - model Name { ... } blocks
    - Field definitions: name Type @attributes
    - @@index([columns]) directives
    - Relations: @relation(fields: [...], references: [...])
    """
    tables = []

    # Find all model blocks
    model_pattern = re.compile(r"model\s+(\w+)\s*\{([^}]+)\}", re.MULTILINE | re.DOTALL)

    for match in model_pattern.finditer(content):
        model_name = match.group(1)
        body = match.group(2)
        columns = []
        indexes = []

        for line in body.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("//"):
                continue

            # @@index([col1, col2])
            idx_match = re.match(r"@@index\(\[([^\]]+)\]\)", line)
            if idx_match:
                cols = [c.strip().strip('"') for c in idx_match.group(1).split(",")]
                idx_name = f"idx_{model_name.lower()}_{'_'.join(cols)}"
                indexes.append(Index(name=idx_name, table=model_name, columns=cols))
                continue

            # @@unique([col1, col2])
            uniq_match = re.match(r"@@unique\(\[([^\]]+)\]\)", line)
            if uniq_match:
                cols = [c.strip().strip('"') for c in uniq_match.group(1).split(",")]
                idx_name = f"uniq_{model_name.lower()}_{'_'.join(cols)}"
                indexes.append(
                    Index(name=idx_name, table=model_name, columns=cols, unique=True)
                )
                continue

            # Skip other @@ directives
            if line.startswith("@@"):
                continue

            # Field definition: name Type? @attributes
            field_match = re.match(r"(\w+)\s+(\w+)(\[\])?\??(\s+.*)?$", line)
            if not field_match:
                continue

            field_name = field_match.group(1)
            field_type_raw = field_match.group(2).lower()
            is_array = field_match.group(3) is not None
            attrs_str = field_match.group(4) or ""

            # Skip relation fields (array types or types matching model names)
            if is_array:
                continue

            col_type = _PRISMA_TYPE_MAP.get(field_type_raw, ColumnType.UNKNOWN)
            # If type is unknown and it starts uppercase, it's a relation scalar
            if col_type == ColumnType.UNKNOWN and field_type_raw[0:1].isupper():
                continue

            nullable = (
                "?" in line.split(field_match.group(2), 1)[-1].split("@")[0]
                if field_match.group(2) in line
                else False
            )
            # Simpler nullable check: the type is followed by ?
            nullable = bool(re.search(r"\w+\s+\w+\?", line))

            primary_key = "@id" in attrs_str
            unique = "@unique" in attrs_str
            default_val = None
            default_match = re.search(r"@default\(([^)]+)\)", attrs_str)
            if default_match:
                default_val = default_match.group(1)

            # Check for @relation to extract references
            references = None
            rel_match = re.search(r"@relation\(.*?references:\s*\[(\w+)\]", attrs_str)
            if rel_match:
                references = rel_match.group(1)

            if primary_key and col_type == ColumnType.UNKNOWN:
                # Prisma @id with cuid/uuid
                if "cuid" in (default_val or ""):
                    col_type = ColumnType.TEXT
                elif "uuid" in (default_val or ""):
                    col_type = ColumnType.UUID
                elif "autoincrement" in (default_val or ""):
                    col_type = ColumnType.SERIAL

            columns.append(
                Column(
                    name=field_name,
                    type=col_type,
                    nullable=nullable,
                    default=default_val,
                    primary_key=primary_key,
                    unique=unique,
                    references=references,
                )
            )

        if columns:
            tables.append(Table(name=model_name, columns=columns, indexes=indexes))

    return Schema(tables=tables, orm_type="prisma")


def parse_sqlalchemy(content: str) -> Schema:
    """
    Parse SQLAlchemy model definitions.

    Parses:
    - class ModelName(Base): blocks
    - Column(Type, ...) assignments
    - __tablename__ = "name"
    - Index() and UniqueConstraint() in __table_args__
    """
    tables = []

    # Find class definitions that inherit from Base or DeclarativeBase
    # Use a two-step approach: find class headers, then extract body by indentation
    class_header_pattern = re.compile(
        r"^class\s+(\w+)\s*\([^)]*(?:Base|DeclarativeBase|Model)[^)]*\)\s*:",
        re.MULTILINE,
    )

    lines = content.split("\n")
    for header_match in class_header_pattern.finditer(content):
        class_name = header_match.group(1)

        # Find the line number of this class header
        header_start = content[: header_match.start()].count("\n")

        # Extract body: all subsequent lines that are indented or blank (until next non-indented non-blank line)
        body_lines = []
        for i in range(header_start + 1, len(lines)):
            line = lines[i]
            if line.strip() == "":
                body_lines.append(line)
                continue
            if line[0:1] in (" ", "\t"):
                body_lines.append(line)
            else:
                break
        body = "\n".join(body_lines)

        columns = []
        indexes = []

        # Extract __tablename__
        tbl_match = re.search(r'__tablename__\s*=\s*["\'](\w+)["\']', body)
        table_name = tbl_match.group(1) if tbl_match else class_name.lower() + "s"

        # Parse Column() definitions — handle nested parens like Column(String(255), ...)
        col_pattern = re.compile(
            r"(\w+)\s*=\s*(?:db\.)?Column\(([^()]*(?:\([^()]*\)[^()]*)*)\)"
        )
        for col_match in col_pattern.finditer(body):
            col_name = col_match.group(1)
            col_args = col_match.group(2)

            # Extract type (first argument)
            type_match = re.match(r"(?:db\.)?(\w+)(?:\([^)]*\))?", col_args.strip())
            type_raw = type_match.group(1).lower() if type_match else "unknown"
            col_type = _SQLALCHEMY_TYPE_MAP.get(type_raw, ColumnType.UNKNOWN)

            primary_key = (
                "primary_key=True" in col_args or "primary_key = True" in col_args
            )
            nullable = (
                "nullable=False" not in col_args and "nullable = False" not in col_args
            )
            unique = "unique=True" in col_args or "unique = True" in col_args

            default_val = None
            default_match = re.search(r"default=([^,)]+)", col_args)
            if default_match:
                default_val = default_match.group(1).strip()

            # ForeignKey reference
            references = None
            fk_match = re.search(r'ForeignKey\(["\']([^"\']+)["\']\)', col_args)
            if fk_match:
                references = fk_match.group(1)

            columns.append(
                Column(
                    name=col_name,
                    type=col_type,
                    nullable=nullable,
                    default=default_val,
                    primary_key=primary_key,
                    unique=unique,
                    references=references,
                )
            )

        # Parse Index() in __table_args__
        idx_pattern = re.compile(
            r"Index\(['\"](\w+)['\"],\s*['\"](\w+)['\"](?:\s*,\s*['\"](\w+)['\"])*"
        )
        for idx_match in idx_pattern.finditer(body):
            idx_name = idx_match.group(1)
            idx_cols = [g for g in idx_match.groups()[1:] if g]
            indexes.append(Index(name=idx_name, table=table_name, columns=idx_cols))

        if columns:
            tables.append(Table(name=table_name, columns=columns, indexes=indexes))

    return Schema(tables=tables, orm_type="sqlalchemy")


def parse_sql(content: str) -> Schema:
    """
    Parse CREATE TABLE statements from SQL files.

    Parses:
    - CREATE TABLE name (columns...)
    - Column definitions with types and constraints
    - PRIMARY KEY, FOREIGN KEY, UNIQUE, NOT NULL
    - CREATE INDEX statements
    """
    tables = []
    indexes = []

    # Find CREATE TABLE blocks
    table_pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?\s*\(([^;]+)\)",
        re.IGNORECASE | re.DOTALL,
    )

    for match in table_pattern.finditer(content):
        table_name = match.group(1)
        body = match.group(2)
        columns = []

        for line in body.split(","):
            line = line.strip()
            if not line:
                continue

            line_upper = line.upper()

            # Skip standalone constraint lines
            if line_upper.startswith(
                ("PRIMARY KEY", "FOREIGN KEY", "CONSTRAINT", "UNIQUE", "CHECK")
            ):
                continue

            # Column: name TYPE(size)? [constraints]
            col_match = re.match(
                r"[`\"]?(\w+)[`\"]?\s+(\w+)(?:\s*\([\d,\s]+\))?\s*(.*)",
                line,
                re.IGNORECASE,
            )
            if not col_match:
                continue

            col_name = col_match.group(1)
            type_raw = col_match.group(2).strip().lower()
            constraints_raw = col_match.group(3) or ""
            constraints_upper = constraints_raw.upper()

            col_type = _SQL_TYPE_MAP.get(type_raw, ColumnType.UNKNOWN)
            # Handle multi-word types by checking remaining text
            if col_type == ColumnType.UNKNOWN:
                for key, val in _SQL_TYPE_MAP.items():
                    if key in type_raw:
                        col_type = val
                        break

            primary_key = "PRIMARY KEY" in constraints_upper
            nullable = "NOT NULL" not in constraints_upper
            unique = "UNIQUE" in constraints_upper

            default_val = None
            default_match = re.search(
                r"DEFAULT\s+(\S+)", constraints_raw, re.IGNORECASE
            )
            if default_match:
                default_val = default_match.group(1).strip("'\"")

            references = None
            ref_match = re.search(
                r"REFERENCES\s+[`\"]?(\w+)[`\"]?\s*\([`\"]?(\w+)[`\"]?\)",
                constraints_raw,
                re.IGNORECASE,
            )
            if ref_match:
                references = f"{ref_match.group(1)}.{ref_match.group(2)}"

            columns.append(
                Column(
                    name=col_name,
                    type=col_type,
                    nullable=nullable,
                    default=default_val,
                    primary_key=primary_key,
                    unique=unique,
                    references=references,
                )
            )

        if columns:
            tables.append(Table(name=table_name, columns=columns, indexes=[]))

    # Parse CREATE INDEX statements
    idx_pattern = re.compile(
        r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?\s+ON\s+[`\"]?(\w+)[`\"]?\s*\(([^)]+)\)",
        re.IGNORECASE,
    )
    for match in idx_pattern.finditer(content):
        is_unique = match.group(1) is not None
        idx_name = match.group(2)
        tbl_name = match.group(3)
        cols = [c.strip().strip('`"') for c in match.group(4).split(",")]

        idx = Index(name=idx_name, table=tbl_name, columns=cols, unique=is_unique)
        indexes.append(idx)

        # Attach to table if it exists
        for table in tables:
            if table.name == tbl_name:
                table.indexes.append(idx)
                break

    return Schema(tables=tables, orm_type="raw_sql")


# =========================================================================
# Main Entry Point: extract_schema
# =========================================================================


def extract_schema(backend_files: Dict[str, str]) -> Schema:
    """
    Extract database schema from generated backend code.

    Detects ORM type, finds relevant files, parses them into a unified Schema.
    """
    if not backend_files:
        return Schema()

    orm_type = detect_orm_type(backend_files)

    if orm_type == "prisma":
        # Parse all .prisma files
        all_content = ""
        for path, content in backend_files.items():
            if path.lower().endswith(".prisma") or re.search(
                r"^model\s+\w+\s*\{", content, re.MULTILINE
            ):
                all_content += content + "\n"
        if all_content:
            schema = parse_prisma(all_content)
            schema.orm_type = "prisma"
            return schema

    elif orm_type == "sqlalchemy":
        # Parse all Python files with model definitions
        all_content = ""
        for path, content in backend_files.items():
            if path.lower().endswith(".py") and (
                "Column(" in content or "from sqlalchemy" in content
            ):
                all_content += content + "\n"
        if all_content:
            schema = parse_sqlalchemy(all_content)
            schema.orm_type = "sqlalchemy"
            return schema

    elif orm_type in ("raw_sql", "drizzle"):
        # Parse SQL files
        all_content = ""
        for path, content in backend_files.items():
            if re.search(r"CREATE\s+TABLE", content, re.IGNORECASE):
                all_content += content + "\n"
        if all_content:
            schema = parse_sql(all_content)
            schema.orm_type = orm_type
            return schema

    # Fallback: try all parsers
    for path, content in backend_files.items():
        if re.search(r"^model\s+\w+\s*\{", content, re.MULTILINE):
            return parse_prisma(content)
        if "Column(" in content and (
            "Base)" in content or "DeclarativeBase" in content
        ):
            return parse_sqlalchemy(content)
        if re.search(r"CREATE\s+TABLE", content, re.IGNORECASE):
            return parse_sql(content)

    return Schema()


# =========================================================================
# Schema Diff Engine
# =========================================================================


def diff_schemas(old_schema: Schema, new_schema: Schema) -> List[SchemaDiff]:
    """
    Compute the structural diff between two schemas.

    Algorithm:
    1. Find added tables (in new but not in old)
    2. Find removed tables (in old but not in new)
    3. For tables in both:
       a. Find added columns
       b. Find removed columns (DATA LOSS RISK!)
       c. Find modified columns (type change = DATA LOSS RISK!)
       d. Find added/removed indexes

    Data loss risk flagged when:
    - A table is removed (DROP TABLE)
    - A column is removed (DROP COLUMN)
    - A column type changes from wider to narrower (e.g., TEXT → INTEGER)
    - A non-nullable column is added without a default
    """
    diffs = []

    old_tables = old_schema.table_names()
    new_tables = new_schema.table_names()

    # 1. Added tables
    for name in sorted(new_tables - old_tables):
        diffs.append(
            SchemaDiff(
                diff_type=DiffType.TABLE_ADDED,
                table_name=name,
                new_value=new_schema.get_table(name).to_dict(),
            )
        )

    # 2. Removed tables (DATA LOSS!)
    for name in sorted(old_tables - new_tables):
        diffs.append(
            SchemaDiff(
                diff_type=DiffType.TABLE_REMOVED,
                table_name=name,
                old_value=old_schema.get_table(name).to_dict(),
                data_loss_risk=True,
                risk_description=f"Table '{name}' will be dropped. All data will be lost.",
            )
        )

    # 3. Modified tables
    for name in sorted(old_tables & new_tables):
        old_table = old_schema.get_table(name)
        new_table = new_schema.get_table(name)

        old_cols = old_table.column_names()
        new_cols = new_table.column_names()

        # 3a. Added columns
        for col_name in sorted(new_cols - old_cols):
            col = new_table.get_column(col_name)
            risk = not col.nullable and col.default is None
            diffs.append(
                SchemaDiff(
                    diff_type=DiffType.COLUMN_ADDED,
                    table_name=name,
                    column_name=col_name,
                    new_value=col.to_dict(),
                    data_loss_risk=risk,
                    risk_description=(
                        f"Non-nullable column '{col_name}' added without default. "
                        f"Existing rows will fail."
                        if risk
                        else ""
                    ),
                )
            )

        # 3b. Removed columns (DATA LOSS!)
        for col_name in sorted(old_cols - new_cols):
            diffs.append(
                SchemaDiff(
                    diff_type=DiffType.COLUMN_REMOVED,
                    table_name=name,
                    column_name=col_name,
                    old_value=old_table.get_column(col_name).to_dict(),
                    data_loss_risk=True,
                    risk_description=f"Column '{name}.{col_name}' will be dropped.",
                )
            )

        # 3c. Modified columns
        for col_name in sorted(old_cols & new_cols):
            old_col = old_table.get_column(col_name)
            new_col = new_table.get_column(col_name)
            if old_col.type != new_col.type or old_col.nullable != new_col.nullable:
                risk = old_col.type != new_col.type
                diffs.append(
                    SchemaDiff(
                        diff_type=DiffType.COLUMN_MODIFIED,
                        table_name=name,
                        column_name=col_name,
                        old_value=old_col.to_dict(),
                        new_value=new_col.to_dict(),
                        data_loss_risk=risk,
                        risk_description=(
                            f"Type change from {old_col.type.value} to {new_col.type.value} "
                            f"may cause data loss."
                            if risk
                            else ""
                        ),
                    )
                )

        # 3d. Index diffs
        old_idx_names = {i.name for i in old_table.indexes}
        new_idx_names = {i.name for i in new_table.indexes}
        for idx_name in sorted(new_idx_names - old_idx_names):
            idx = next(i for i in new_table.indexes if i.name == idx_name)
            diffs.append(
                SchemaDiff(
                    diff_type=DiffType.INDEX_ADDED,
                    table_name=name,
                    new_value=idx.to_dict(),
                )
            )
        for idx_name in sorted(old_idx_names - new_idx_names):
            idx = next(i for i in old_table.indexes if i.name == idx_name)
            diffs.append(
                SchemaDiff(
                    diff_type=DiffType.INDEX_REMOVED,
                    table_name=name,
                    old_value=idx.to_dict(),
                )
            )

    return diffs


# =========================================================================
# Migration Generator
# =========================================================================


def _column_to_sql(col: Dict[str, Any]) -> str:
    """Convert a column dict to SQL definition string."""
    name = col.get("name", "unnamed")
    col_type = col.get("type", "TEXT").upper()

    # Map our enum values to SQL types
    type_map = {
        "TEXT": "TEXT",
        "INTEGER": "INTEGER",
        "FLOAT": "FLOAT",
        "BOOLEAN": "BOOLEAN",
        "TIMESTAMP": "TIMESTAMP",
        "JSON": "JSONB",
        "UUID": "UUID",
        "SERIAL": "SERIAL",
        "BIGINT": "BIGINT",
        "DECIMAL": "DECIMAL",
        "BLOB": "BYTEA",
        "UNKNOWN": "TEXT",
    }
    sql_type = type_map.get(col_type, "TEXT")

    parts = [name, sql_type]
    if col.get("primary_key"):
        parts.append("PRIMARY KEY")
    if not col.get("nullable", True):
        parts.append("NOT NULL")
    if col.get("unique"):
        parts.append("UNIQUE")
    if col.get("default") is not None:
        parts.append(f"DEFAULT {col['default']}")
    if col.get("references"):
        ref = col["references"]
        # references might be "table.column" or just "table(column)"
        if "." in ref:
            ref_table, ref_col = ref.split(".", 1)
            parts.append(f"REFERENCES {ref_table}({ref_col})")
        else:
            parts.append(f"REFERENCES {ref}")

    return " ".join(parts)


def _diff_to_sql(diff: SchemaDiff) -> Tuple[str, str]:
    """Convert a single SchemaDiff to (forward_sql, rollback_sql)."""

    if diff.diff_type == DiffType.TABLE_ADDED:
        table = diff.new_value
        cols_sql = ", ".join(_column_to_sql(c) for c in table.get("columns", []))
        up = f"CREATE TABLE {diff.table_name} ({cols_sql});"
        down = f"DROP TABLE IF EXISTS {diff.table_name};"

    elif diff.diff_type == DiffType.TABLE_REMOVED:
        up = f"-- WARNING: Data loss! Dropping table.\nDROP TABLE IF EXISTS {diff.table_name};"
        table = diff.old_value
        cols_sql = ", ".join(_column_to_sql(c) for c in table.get("columns", []))
        down = f"CREATE TABLE {diff.table_name} ({cols_sql});"

    elif diff.diff_type == DiffType.COLUMN_ADDED:
        col = diff.new_value
        col_sql = _column_to_sql(col)
        up = f"ALTER TABLE {diff.table_name} ADD COLUMN {col_sql};"
        down = f"ALTER TABLE {diff.table_name} DROP COLUMN {diff.column_name};"

    elif diff.diff_type == DiffType.COLUMN_REMOVED:
        up = f"-- WARNING: Data loss!\nALTER TABLE {diff.table_name} DROP COLUMN {diff.column_name};"
        col = diff.old_value
        col_sql = _column_to_sql(col)
        down = f"ALTER TABLE {diff.table_name} ADD COLUMN {col_sql};"

    elif diff.diff_type == DiffType.COLUMN_MODIFIED:
        new_col = diff.new_value
        old_col = diff.old_value
        new_type = new_col.get("type", "text").upper()
        old_type = old_col.get("type", "text").upper()

        # Map enum values to SQL types for ALTER COLUMN
        type_map = {
            "TEXT": "TEXT",
            "INTEGER": "INTEGER",
            "FLOAT": "FLOAT",
            "BOOLEAN": "BOOLEAN",
            "TIMESTAMP": "TIMESTAMP",
            "JSON": "JSONB",
            "UUID": "UUID",
            "SERIAL": "SERIAL",
            "BIGINT": "BIGINT",
            "DECIMAL": "DECIMAL",
            "BLOB": "BYTEA",
            "UNKNOWN": "TEXT",
        }
        sql_new_type = type_map.get(new_type, "TEXT")
        sql_old_type = type_map.get(old_type, "TEXT")

        up = f"ALTER TABLE {diff.table_name} ALTER COLUMN {diff.column_name} TYPE {sql_new_type};"
        down = f"ALTER TABLE {diff.table_name} ALTER COLUMN {diff.column_name} TYPE {sql_old_type};"

    elif diff.diff_type == DiffType.INDEX_ADDED:
        idx = diff.new_value
        cols = ", ".join(idx.get("columns", []))
        unique = "UNIQUE " if idx.get("unique") else ""
        up = f"CREATE {unique}INDEX {idx['name']} ON {diff.table_name} ({cols});"
        down = f"DROP INDEX IF EXISTS {idx['name']};"

    elif diff.diff_type == DiffType.INDEX_REMOVED:
        idx = diff.old_value
        cols = ", ".join(idx.get("columns", []))
        unique = "UNIQUE " if idx.get("unique") else ""
        up = f"DROP INDEX IF EXISTS {idx['name']};"
        down = f"CREATE {unique}INDEX {idx['name']} ON {diff.table_name} ({cols});"

    else:
        up = f"-- Unknown diff type: {diff.diff_type}"
        down = f"-- Unknown diff type: {diff.diff_type}"

    return up, down


def _summarize_diffs(diffs: List[SchemaDiff]) -> str:
    """Generate a human-readable summary of the diffs."""
    parts = []
    added_tables = [d for d in diffs if d.diff_type == DiffType.TABLE_ADDED]
    removed_tables = [d for d in diffs if d.diff_type == DiffType.TABLE_REMOVED]
    added_cols = [d for d in diffs if d.diff_type == DiffType.COLUMN_ADDED]
    removed_cols = [d for d in diffs if d.diff_type == DiffType.COLUMN_REMOVED]
    modified_cols = [d for d in diffs if d.diff_type == DiffType.COLUMN_MODIFIED]
    added_idx = [d for d in diffs if d.diff_type == DiffType.INDEX_ADDED]
    removed_idx = [d for d in diffs if d.diff_type == DiffType.INDEX_REMOVED]

    if added_tables:
        parts.append(f"add {len(added_tables)} table(s)")
    if removed_tables:
        parts.append(f"drop {len(removed_tables)} table(s)")
    if added_cols:
        parts.append(f"add {len(added_cols)} column(s)")
    if removed_cols:
        parts.append(f"drop {len(removed_cols)} column(s)")
    if modified_cols:
        parts.append(f"modify {len(modified_cols)} column(s)")
    if added_idx:
        parts.append(f"add {len(added_idx)} index(es)")
    if removed_idx:
        parts.append(f"drop {len(removed_idx)} index(es)")

    return ", ".join(parts) if parts else "no changes"


def _slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower())
    return slug.strip("_")[:60]


def generate_migration(
    diffs: List[SchemaDiff],
    orm_type: str = "raw_sql",
    project_name: str = "vbuilder",
) -> Optional[MigrationFile]:
    """
    Generate a migration file from schema diffs.

    If no diffs, returns None.
    Generates forward SQL + rollback SQL for all diff types.
    """
    if not diffs:
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    description = _summarize_diffs(diffs)
    slug = _slugify(description)

    up_statements = []
    down_statements = []

    for diff in diffs:
        up, down = _diff_to_sql(diff)
        up_statements.append(up)
        down_statements.append(down)

    migration_sql = (
        f"-- Migration: {description}\n"
        f"-- Generated by VBuilder DB Architect (IE-5)\n"
        f"-- Timestamp: {timestamp}\n\n" + "\n".join(up_statements)
    )

    rollback_sql = (
        f"-- Rollback: {description}\n"
        f"-- Generated by VBuilder DB Architect (IE-5)\n\n"
        + "\n".join(reversed(down_statements))
    )

    return MigrationFile(
        filename=f"migrations/{timestamp}_{slug}.sql",
        content=migration_sql,
        rollback_filename=f"migrations/{timestamp}_{slug}_down.sql",
        rollback_content=rollback_sql,
        description=description,
        diffs=diffs,
        has_data_loss_risk=any(d.data_loss_risk for d in diffs),
    )


# =========================================================================
# Previous Schema Retrieval
# =========================================================================


def recall_previous_schema(project_id: str) -> Optional[Schema]:
    """
    Retrieve the most recent schema for this project from MemoryManager.

    Stored as a SEMANTIC memory with namespace "schema:{project_id}".
    Falls back to None if MemoryManager unavailable or no schema stored.
    """
    try:
        from memory import MemoryManager

        mm = MemoryManager()
        results = mm.search(
            query="database schema",
            namespace=f"schema:{project_id}",
            limit=1,
        )
        if results:
            content = results[0].get("content", "{}")
            schema_dict = json.loads(content)
            return _dict_to_schema(schema_dict)
    except Exception:
        pass
    return None


def _dict_to_schema(d: Dict[str, Any]) -> Schema:
    """Reconstruct a Schema from its dict representation."""
    tables = []
    for t in d.get("tables", []):
        columns = []
        for c in t.get("columns", []):
            columns.append(
                Column(
                    name=c["name"],
                    type=ColumnType(c.get("type", "unknown")),
                    nullable=c.get("nullable", True),
                    default=c.get("default"),
                    primary_key=c.get("primary_key", False),
                    unique=c.get("unique", False),
                    references=c.get("references"),
                )
            )
        indexes = []
        for i in t.get("indexes", []):
            indexes.append(
                Index(
                    name=i["name"],
                    table=i.get("table", t["name"]),
                    columns=i.get("columns", []),
                    unique=i.get("unique", False),
                )
            )
        tables.append(Table(name=t["name"], columns=columns, indexes=indexes))

    return Schema(tables=tables, orm_type=d.get("orm_type", "unknown"))
