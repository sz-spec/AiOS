"""
IE-5 Semantic Schema Migrations — Test Suite
=============================================

Tests for:
- Schema data structures (Column, Table, Schema, etc.)
- ORM detection (Prisma, SQLAlchemy, raw SQL)
- Schema parsing (3 parsers)
- Schema diff engine
- Migration SQL generation
- Pipeline node integration

Run: pytest backend/tests/test_db_architect.py -v
"""

import os
import sys
import json

_backend = os.path.join(os.path.dirname(__file__), "..")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from ai.agents.db_architect import (
    Column,
    ColumnType,
    Index,
    Table,
    Schema,
    DiffType,
    SchemaDiff,
    detect_orm_type,
    parse_prisma,
    parse_sqlalchemy,
    parse_sql,
    extract_schema,
    diff_schemas,
    generate_migration,
    recall_previous_schema,
    _dict_to_schema,
    _column_to_sql,
    _summarize_diffs,
    _slugify,
)

# =====================================================================
# Fixtures
# =====================================================================

PRISMA_SCHEMA = """
datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

model User {
  id        String   @id @default(cuid())
  email     String   @unique
  name      String?
  posts     Post[]
  createdAt DateTime @default(now())

  @@index([email])
}

model Post {
  id        String   @id @default(cuid())
  title     String
  content   String?
  published Boolean  @default(false)
  authorId  String
  author    User     @relation(fields: [authorId], references: [id])
  createdAt DateTime @default(now())

  @@index([authorId])
}
"""

SQLALCHEMY_MODELS = """
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship, DeclarativeBase

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(100))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime)

    posts = relationship("Post", back_populates="author")

    __table_args__ = (
        Index("idx_users_email", "email"),
    )

class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    content = Column(String)
    published = Column(Boolean, default=False)
    author_id = Column(Integer, ForeignKey("users.id"))

    author = relationship("User", back_populates="posts")
"""

SQL_SCHEMA = """
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name TEXT,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE posts (
    id SERIAL PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    content TEXT,
    published BOOLEAN DEFAULT false,
    author_id INTEGER REFERENCES users(id)
);

CREATE INDEX idx_users_email ON users (email);
CREATE UNIQUE INDEX idx_posts_title ON posts (title);
"""


# =====================================================================
# Test Data Structures
# =====================================================================


class TestColumn:
    def test_column_creation(self):
        col = Column(name="email", type=ColumnType.TEXT, nullable=False, unique=True)
        assert col.name == "email"
        assert col.type == ColumnType.TEXT
        assert col.nullable is False
        assert col.unique is True

    def test_column_to_dict(self):
        col = Column(name="id", type=ColumnType.SERIAL, primary_key=True)
        d = col.to_dict()
        assert d["name"] == "id"
        assert d["type"] == "serial"
        assert d["primary_key"] is True

    def test_column_defaults(self):
        col = Column(name="test", type=ColumnType.TEXT)
        assert col.nullable is True
        assert col.default is None
        assert col.primary_key is False
        assert col.unique is False
        assert col.references is None


class TestTable:
    def test_table_column_names(self):
        cols = [
            Column(name="id", type=ColumnType.SERIAL),
            Column(name="email", type=ColumnType.TEXT),
            Column(name="name", type=ColumnType.TEXT),
        ]
        table = Table(name="users", columns=cols)
        assert table.column_names() == {"id", "email", "name"}

    def test_table_get_column(self):
        cols = [
            Column(name="id", type=ColumnType.SERIAL),
            Column(name="email", type=ColumnType.TEXT),
        ]
        table = Table(name="users", columns=cols)
        assert table.get_column("email").type == ColumnType.TEXT
        assert table.get_column("nonexistent") is None

    def test_table_to_dict(self):
        table = Table(
            name="users",
            columns=[Column(name="id", type=ColumnType.SERIAL)],
            indexes=[Index(name="idx_id", table="users", columns=["id"])],
        )
        d = table.to_dict()
        assert d["name"] == "users"
        assert len(d["columns"]) == 1
        assert len(d["indexes"]) == 1


class TestSchema:
    def test_schema_table_names(self):
        schema = Schema(
            tables=[
                Table(name="users"),
                Table(name="posts"),
            ]
        )
        assert schema.table_names() == {"users", "posts"}

    def test_schema_get_table(self):
        schema = Schema(
            tables=[
                Table(name="users"),
                Table(name="posts"),
            ]
        )
        assert schema.get_table("users").name == "users"
        assert schema.get_table("nonexistent") is None

    def test_schema_fingerprint_deterministic(self):
        schema1 = Schema(
            tables=[
                Table(
                    name="users", columns=[Column(name="id", type=ColumnType.SERIAL)]
                ),
            ]
        )
        schema2 = Schema(
            tables=[
                Table(
                    name="users", columns=[Column(name="id", type=ColumnType.SERIAL)]
                ),
            ]
        )
        assert schema1.fingerprint() == schema2.fingerprint()

    def test_schema_fingerprint_changes_on_modification(self):
        schema1 = Schema(
            tables=[
                Table(
                    name="users", columns=[Column(name="id", type=ColumnType.SERIAL)]
                ),
            ]
        )
        schema2 = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[
                        Column(name="id", type=ColumnType.SERIAL),
                        Column(name="email", type=ColumnType.TEXT),
                    ],
                ),
            ]
        )
        assert schema1.fingerprint() != schema2.fingerprint()

    def test_schema_to_dict_roundtrip(self):
        schema = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[
                        Column(name="id", type=ColumnType.SERIAL, primary_key=True)
                    ],
                    indexes=[Index(name="idx_id", table="users", columns=["id"])],
                )
            ],
            orm_type="prisma",
        )
        d = schema.to_dict()
        restored = _dict_to_schema(d)
        assert restored.table_names() == {"users"}
        assert restored.get_table("users").get_column("id").primary_key is True
        assert restored.orm_type == "prisma"

    def test_empty_schema(self):
        schema = Schema()
        assert schema.table_names() == set()
        assert schema.fingerprint()  # Should not crash


# =====================================================================
# Test ORM Detection
# =====================================================================


class TestORMDetection:
    def test_detect_prisma(self):
        files = {"schema.prisma": PRISMA_SCHEMA}
        assert detect_orm_type(files) == "prisma"

    def test_detect_prisma_by_content(self):
        files = {"db/schema.txt": "model User {\n  id String @id\n}"}
        assert detect_orm_type(files) == "prisma"

    def test_detect_sqlalchemy(self):
        files = {"models.py": SQLALCHEMY_MODELS}
        assert detect_orm_type(files) == "sqlalchemy"

    def test_detect_sqlalchemy_by_import(self):
        files = {"db.py": "from sqlalchemy import Column, Integer\nColumn(Integer)"}
        assert detect_orm_type(files) == "sqlalchemy"

    def test_detect_raw_sql(self):
        files = {"schema.sql": SQL_SCHEMA}
        assert detect_orm_type(files) == "raw_sql"

    def test_detect_drizzle(self):
        files = {
            "schema.ts": "export const users = pgTable('users', { id: serial('id') })"
        }
        assert detect_orm_type(files) == "drizzle"

    def test_detect_unknown(self):
        files = {"app.py": "print('hello')"}
        assert detect_orm_type(files) == "unknown"

    def test_detect_empty(self):
        assert detect_orm_type({}) == "unknown"


# =====================================================================
# Test Prisma Parser
# =====================================================================


class TestPrismaParser:
    def test_parse_two_models(self):
        schema = parse_prisma(PRISMA_SCHEMA)
        assert schema.table_names() == {"User", "Post"}

    def test_parse_user_columns(self):
        schema = parse_prisma(PRISMA_SCHEMA)
        user = schema.get_table("User")
        assert user is not None
        col_names = user.column_names()
        assert "id" in col_names
        assert "email" in col_names
        assert "name" in col_names
        assert "createdAt" in col_names

    def test_parse_primary_key(self):
        schema = parse_prisma(PRISMA_SCHEMA)
        user = schema.get_table("User")
        id_col = user.get_column("id")
        assert id_col.primary_key is True

    def test_parse_unique(self):
        schema = parse_prisma(PRISMA_SCHEMA)
        user = schema.get_table("User")
        email_col = user.get_column("email")
        assert email_col.unique is True

    def test_parse_nullable(self):
        schema = parse_prisma(PRISMA_SCHEMA)
        user = schema.get_table("User")
        name_col = user.get_column("name")
        assert name_col.nullable is True

    def test_parse_index(self):
        schema = parse_prisma(PRISMA_SCHEMA)
        user = schema.get_table("User")
        assert len(user.indexes) >= 1
        idx_names = {i.name for i in user.indexes}
        assert any("email" in name for name in idx_names)

    def test_parse_relation_fields_excluded(self):
        """Relation fields (Post[]) should not appear as columns."""
        schema = parse_prisma(PRISMA_SCHEMA)
        user = schema.get_table("User")
        # "posts" is a relation, not a column
        assert user.get_column("posts") is None

    def test_parse_default(self):
        schema = parse_prisma(PRISMA_SCHEMA)
        post = schema.get_table("Post")
        published = post.get_column("published")
        assert published.default == "false"


# =====================================================================
# Test SQLAlchemy Parser
# =====================================================================


class TestSQLAlchemyParser:
    def test_parse_two_tables(self):
        schema = parse_sqlalchemy(SQLALCHEMY_MODELS)
        assert "users" in schema.table_names()
        assert "posts" in schema.table_names()

    def test_parse_column_types(self):
        schema = parse_sqlalchemy(SQLALCHEMY_MODELS)
        users = schema.get_table("users")
        assert users.get_column("id").type == ColumnType.INTEGER
        assert users.get_column("email").type == ColumnType.TEXT
        assert users.get_column("is_active").type == ColumnType.BOOLEAN

    def test_parse_primary_key(self):
        schema = parse_sqlalchemy(SQLALCHEMY_MODELS)
        users = schema.get_table("users")
        assert users.get_column("id").primary_key is True

    def test_parse_nullable(self):
        schema = parse_sqlalchemy(SQLALCHEMY_MODELS)
        users = schema.get_table("users")
        assert users.get_column("email").nullable is False
        assert users.get_column("name").nullable is True

    def test_parse_unique(self):
        schema = parse_sqlalchemy(SQLALCHEMY_MODELS)
        users = schema.get_table("users")
        assert users.get_column("email").unique is True

    def test_parse_foreign_key(self):
        schema = parse_sqlalchemy(SQLALCHEMY_MODELS)
        posts = schema.get_table("posts")
        author_id = posts.get_column("author_id")
        assert author_id.references == "users.id"

    def test_parse_index(self):
        schema = parse_sqlalchemy(SQLALCHEMY_MODELS)
        users = schema.get_table("users")
        idx_names = {i.name for i in users.indexes}
        assert "idx_users_email" in idx_names


# =====================================================================
# Test SQL Parser
# =====================================================================


class TestSQLParser:
    def test_parse_two_tables(self):
        schema = parse_sql(SQL_SCHEMA)
        assert "users" in schema.table_names()
        assert "posts" in schema.table_names()

    def test_parse_column_types(self):
        schema = parse_sql(SQL_SCHEMA)
        users = schema.get_table("users")
        assert users.get_column("id").type == ColumnType.SERIAL
        assert users.get_column("email").type == ColumnType.TEXT
        assert users.get_column("is_active").type == ColumnType.BOOLEAN

    def test_parse_not_null(self):
        schema = parse_sql(SQL_SCHEMA)
        users = schema.get_table("users")
        assert users.get_column("email").nullable is False
        assert users.get_column("name").nullable is True

    def test_parse_primary_key(self):
        schema = parse_sql(SQL_SCHEMA)
        users = schema.get_table("users")
        assert users.get_column("id").primary_key is True

    def test_parse_unique(self):
        schema = parse_sql(SQL_SCHEMA)
        users = schema.get_table("users")
        assert users.get_column("email").unique is True

    def test_parse_foreign_key_reference(self):
        schema = parse_sql(SQL_SCHEMA)
        posts = schema.get_table("posts")
        author_id = posts.get_column("author_id")
        assert author_id.references == "users.id"

    def test_parse_indexes(self):
        schema = parse_sql(SQL_SCHEMA)
        users = schema.get_table("users")
        idx_names = {i.name for i in users.indexes}
        assert "idx_users_email" in idx_names

    def test_parse_unique_index(self):
        schema = parse_sql(SQL_SCHEMA)
        posts = schema.get_table("posts")
        idx = next((i for i in posts.indexes if i.name == "idx_posts_title"), None)
        assert idx is not None
        assert idx.unique is True


# =====================================================================
# Test extract_schema (main entry point)
# =====================================================================


class TestExtractSchema:
    def test_extract_prisma(self):
        files = {"prisma/schema.prisma": PRISMA_SCHEMA}
        schema = extract_schema(files)
        assert schema.orm_type == "prisma"
        assert len(schema.tables) == 2

    def test_extract_sqlalchemy(self):
        files = {"models.py": SQLALCHEMY_MODELS}
        schema = extract_schema(files)
        assert schema.orm_type == "sqlalchemy"
        assert len(schema.tables) == 2

    def test_extract_sql(self):
        files = {"schema.sql": SQL_SCHEMA}
        schema = extract_schema(files)
        assert schema.orm_type == "raw_sql"
        assert len(schema.tables) == 2

    def test_extract_empty(self):
        schema = extract_schema({})
        assert len(schema.tables) == 0

    def test_extract_no_schema_files(self):
        files = {"main.py": "print('hello')"}
        schema = extract_schema(files)
        assert len(schema.tables) == 0


# =====================================================================
# Test Schema Diff Engine
# =====================================================================


class TestSchemaDiff:
    def test_identical_schemas(self):
        schema = Schema(
            tables=[
                Table(
                    name="users", columns=[Column(name="id", type=ColumnType.SERIAL)]
                ),
            ]
        )
        diffs = diff_schemas(schema, schema)
        assert len(diffs) == 0

    def test_table_added(self):
        old = Schema()
        new = Schema(
            tables=[
                Table(
                    name="users", columns=[Column(name="id", type=ColumnType.SERIAL)]
                ),
            ]
        )
        diffs = diff_schemas(old, new)
        assert len(diffs) == 1
        assert diffs[0].diff_type == DiffType.TABLE_ADDED
        assert diffs[0].table_name == "users"
        assert diffs[0].data_loss_risk is False

    def test_table_removed(self):
        old = Schema(
            tables=[
                Table(
                    name="users", columns=[Column(name="id", type=ColumnType.SERIAL)]
                ),
            ]
        )
        new = Schema()
        diffs = diff_schemas(old, new)
        assert len(diffs) == 1
        assert diffs[0].diff_type == DiffType.TABLE_REMOVED
        assert diffs[0].data_loss_risk is True
        assert "dropped" in diffs[0].risk_description.lower()

    def test_column_added(self):
        old = Schema(
            tables=[
                Table(
                    name="users", columns=[Column(name="id", type=ColumnType.SERIAL)]
                ),
            ]
        )
        new = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[
                        Column(name="id", type=ColumnType.SERIAL),
                        Column(name="email", type=ColumnType.TEXT),
                    ],
                ),
            ]
        )
        diffs = diff_schemas(old, new)
        assert len(diffs) == 1
        assert diffs[0].diff_type == DiffType.COLUMN_ADDED
        assert diffs[0].column_name == "email"
        assert diffs[0].data_loss_risk is False  # nullable column

    def test_column_added_not_nullable_no_default_is_risky(self):
        old = Schema(
            tables=[
                Table(
                    name="users", columns=[Column(name="id", type=ColumnType.SERIAL)]
                ),
            ]
        )
        new = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[
                        Column(name="id", type=ColumnType.SERIAL),
                        Column(name="email", type=ColumnType.TEXT, nullable=False),
                    ],
                ),
            ]
        )
        diffs = diff_schemas(old, new)
        assert len(diffs) == 1
        assert diffs[0].data_loss_risk is True
        assert "non-nullable" in diffs[0].risk_description.lower()

    def test_column_removed(self):
        old = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[
                        Column(name="id", type=ColumnType.SERIAL),
                        Column(name="email", type=ColumnType.TEXT),
                    ],
                ),
            ]
        )
        new = Schema(
            tables=[
                Table(
                    name="users", columns=[Column(name="id", type=ColumnType.SERIAL)]
                ),
            ]
        )
        diffs = diff_schemas(old, new)
        assert len(diffs) == 1
        assert diffs[0].diff_type == DiffType.COLUMN_REMOVED
        assert diffs[0].column_name == "email"
        assert diffs[0].data_loss_risk is True

    def test_column_type_changed(self):
        old = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[
                        Column(name="age", type=ColumnType.TEXT),
                    ],
                ),
            ]
        )
        new = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[
                        Column(name="age", type=ColumnType.INTEGER),
                    ],
                ),
            ]
        )
        diffs = diff_schemas(old, new)
        assert len(diffs) == 1
        assert diffs[0].diff_type == DiffType.COLUMN_MODIFIED
        assert diffs[0].data_loss_risk is True
        assert "type change" in diffs[0].risk_description.lower()

    def test_column_nullable_changed(self):
        old = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[
                        Column(name="email", type=ColumnType.TEXT, nullable=True),
                    ],
                ),
            ]
        )
        new = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[
                        Column(name="email", type=ColumnType.TEXT, nullable=False),
                    ],
                ),
            ]
        )
        diffs = diff_schemas(old, new)
        assert len(diffs) == 1
        assert diffs[0].diff_type == DiffType.COLUMN_MODIFIED
        # Same type → no data loss risk from type change
        assert diffs[0].data_loss_risk is False

    def test_index_added(self):
        old = Schema(
            tables=[
                Table(
                    name="users", columns=[Column(name="email", type=ColumnType.TEXT)]
                ),
            ]
        )
        new = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[Column(name="email", type=ColumnType.TEXT)],
                    indexes=[Index(name="idx_email", table="users", columns=["email"])],
                ),
            ]
        )
        diffs = diff_schemas(old, new)
        assert len(diffs) == 1
        assert diffs[0].diff_type == DiffType.INDEX_ADDED

    def test_index_removed(self):
        old = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[Column(name="email", type=ColumnType.TEXT)],
                    indexes=[Index(name="idx_email", table="users", columns=["email"])],
                ),
            ]
        )
        new = Schema(
            tables=[
                Table(
                    name="users", columns=[Column(name="email", type=ColumnType.TEXT)]
                ),
            ]
        )
        diffs = diff_schemas(old, new)
        assert len(diffs) == 1
        assert diffs[0].diff_type == DiffType.INDEX_REMOVED

    def test_complex_diff(self):
        """Multiple changes at once: add table, add column, remove column."""
        old = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[
                        Column(name="id", type=ColumnType.SERIAL),
                        Column(name="old_field", type=ColumnType.TEXT),
                    ],
                ),
            ]
        )
        new = Schema(
            tables=[
                Table(
                    name="users",
                    columns=[
                        Column(name="id", type=ColumnType.SERIAL),
                        Column(name="new_field", type=ColumnType.TEXT),
                    ],
                ),
                Table(
                    name="posts",
                    columns=[
                        Column(name="id", type=ColumnType.SERIAL),
                    ],
                ),
            ]
        )
        diffs = diff_schemas(old, new)
        types = {d.diff_type for d in diffs}
        assert DiffType.TABLE_ADDED in types
        assert DiffType.COLUMN_ADDED in types
        assert DiffType.COLUMN_REMOVED in types


# =====================================================================
# Test Migration Generator
# =====================================================================


class TestMigrationGenerator:
    def test_no_diffs_returns_none(self):
        assert generate_migration([]) is None

    def test_add_column_generates_alter_table(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.COLUMN_ADDED,
                table_name="users",
                column_name="avatar_url",
                new_value={"name": "avatar_url", "type": "text", "nullable": True},
            )
        ]
        migration = generate_migration(diffs)
        assert migration is not None
        assert "ALTER TABLE users ADD COLUMN" in migration.content
        assert "avatar_url" in migration.content
        assert migration.filename.startswith("migrations/")
        assert migration.filename.endswith(".sql")

    def test_rollback_has_drop_column(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.COLUMN_ADDED,
                table_name="users",
                column_name="avatar_url",
                new_value={"name": "avatar_url", "type": "text", "nullable": True},
            )
        ]
        migration = generate_migration(diffs)
        assert "DROP COLUMN avatar_url" in migration.rollback_content

    def test_remove_column_has_warning(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.COLUMN_REMOVED,
                table_name="users",
                column_name="old_field",
                old_value={"name": "old_field", "type": "text", "nullable": True},
                data_loss_risk=True,
            )
        ]
        migration = generate_migration(diffs)
        assert "WARNING" in migration.content
        assert "DROP COLUMN old_field" in migration.content
        assert migration.has_data_loss_risk is True

    def test_add_table_generates_create_table(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.TABLE_ADDED,
                table_name="posts",
                new_value={
                    "name": "posts",
                    "columns": [
                        {
                            "name": "id",
                            "type": "serial",
                            "primary_key": True,
                            "nullable": False,
                        },
                        {"name": "title", "type": "text", "nullable": False},
                    ],
                    "indexes": [],
                },
            )
        ]
        migration = generate_migration(diffs)
        assert "CREATE TABLE posts" in migration.content
        assert "id" in migration.content
        assert "title" in migration.content

    def test_rollback_for_create_table_is_drop(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.TABLE_ADDED,
                table_name="posts",
                new_value={"name": "posts", "columns": [], "indexes": []},
            )
        ]
        migration = generate_migration(diffs)
        assert "DROP TABLE IF EXISTS posts" in migration.rollback_content

    def test_remove_table_has_warning(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.TABLE_REMOVED,
                table_name="old_table",
                old_value={
                    "name": "old_table",
                    "columns": [{"name": "id", "type": "serial"}],
                    "indexes": [],
                },
                data_loss_risk=True,
            )
        ]
        migration = generate_migration(diffs)
        assert "WARNING" in migration.content
        assert "DROP TABLE" in migration.content
        assert migration.has_data_loss_risk is True

    def test_modify_column_type(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.COLUMN_MODIFIED,
                table_name="users",
                column_name="age",
                old_value={"name": "age", "type": "text"},
                new_value={"name": "age", "type": "integer"},
            )
        ]
        migration = generate_migration(diffs)
        assert "ALTER TABLE users ALTER COLUMN age TYPE INTEGER" in migration.content
        assert (
            "ALTER TABLE users ALTER COLUMN age TYPE TEXT" in migration.rollback_content
        )

    def test_add_index(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.INDEX_ADDED,
                table_name="users",
                new_value={
                    "name": "idx_email",
                    "table": "users",
                    "columns": ["email"],
                    "unique": False,
                },
            )
        ]
        migration = generate_migration(diffs)
        assert "CREATE INDEX idx_email ON users (email)" in migration.content
        assert "DROP INDEX IF EXISTS idx_email" in migration.rollback_content

    def test_add_unique_index(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.INDEX_ADDED,
                table_name="users",
                new_value={
                    "name": "idx_email",
                    "table": "users",
                    "columns": ["email"],
                    "unique": True,
                },
            )
        ]
        migration = generate_migration(diffs)
        assert "CREATE UNIQUE INDEX idx_email ON users (email)" in migration.content

    def test_migration_file_structure(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.COLUMN_ADDED,
                table_name="users",
                column_name="bio",
                new_value={"name": "bio", "type": "text", "nullable": True},
            )
        ]
        migration = generate_migration(diffs)
        assert migration.description
        assert migration.rollback_filename.endswith("_down.sql")
        assert "VBuilder DB Architect" in migration.content
        assert "VBuilder DB Architect" in migration.rollback_content

    def test_migration_to_dict(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.COLUMN_ADDED,
                table_name="users",
                column_name="bio",
                new_value={"name": "bio", "type": "text", "nullable": True},
            )
        ]
        migration = generate_migration(diffs)
        d = migration.to_dict()
        assert "filename" in d
        assert "content" in d
        assert "rollback_filename" in d
        assert "rollback_content" in d
        assert "diffs" in d
        # Should be JSON-serializable
        json.dumps(d, default=str)

    def test_multiple_diffs_in_one_migration(self):
        diffs = [
            SchemaDiff(
                diff_type=DiffType.COLUMN_ADDED,
                table_name="users",
                column_name="avatar",
                new_value={"name": "avatar", "type": "text", "nullable": True},
            ),
            SchemaDiff(
                diff_type=DiffType.INDEX_ADDED,
                table_name="users",
                new_value={
                    "name": "idx_avatar",
                    "table": "users",
                    "columns": ["avatar"],
                    "unique": False,
                },
            ),
        ]
        migration = generate_migration(diffs)
        assert "ALTER TABLE users ADD COLUMN" in migration.content
        assert "CREATE INDEX idx_avatar" in migration.content


# =====================================================================
# Test Helper Functions
# =====================================================================


class TestHelpers:
    def test_slugify(self):
        assert (
            _slugify("add 2 column(s), modify 1 column(s)")
            == "add_2_column_s_modify_1_column_s"
        )

    def test_slugify_truncates(self):
        long_text = "a" * 200
        assert len(_slugify(long_text)) <= 60

    def test_summarize_diffs(self):
        diffs = [
            SchemaDiff(diff_type=DiffType.TABLE_ADDED, table_name="posts"),
            SchemaDiff(
                diff_type=DiffType.COLUMN_ADDED, table_name="users", column_name="email"
            ),
            SchemaDiff(
                diff_type=DiffType.COLUMN_ADDED, table_name="users", column_name="name"
            ),
        ]
        summary = _summarize_diffs(diffs)
        assert "1 table" in summary
        assert "2 column" in summary

    def test_summarize_empty(self):
        assert _summarize_diffs([]) == "no changes"

    def test_column_to_sql_basic(self):
        sql = _column_to_sql({"name": "email", "type": "text", "nullable": True})
        assert "email" in sql
        assert "TEXT" in sql

    def test_column_to_sql_not_null(self):
        sql = _column_to_sql({"name": "email", "type": "text", "nullable": False})
        assert "NOT NULL" in sql

    def test_column_to_sql_primary_key(self):
        sql = _column_to_sql({"name": "id", "type": "serial", "primary_key": True})
        assert "PRIMARY KEY" in sql

    def test_column_to_sql_with_reference(self):
        sql = _column_to_sql(
            {"name": "author_id", "type": "integer", "references": "users.id"}
        )
        assert "REFERENCES users(id)" in sql


# =====================================================================
# Test Previous Schema Retrieval
# =====================================================================


class TestRecallPreviousSchema:
    def test_recall_returns_none_when_memory_unavailable(self):
        """recall_previous_schema should return None gracefully."""
        result = recall_previous_schema("nonexistent_project")
        assert result is None

    def test_dict_to_schema(self):
        d = {
            "tables": [
                {
                    "name": "users",
                    "columns": [
                        {
                            "name": "id",
                            "type": "serial",
                            "nullable": False,
                            "primary_key": True,
                        },
                        {
                            "name": "email",
                            "type": "text",
                            "nullable": False,
                            "unique": True,
                        },
                    ],
                    "indexes": [
                        {
                            "name": "idx_email",
                            "table": "users",
                            "columns": ["email"],
                            "unique": False,
                        },
                    ],
                }
            ],
            "orm_type": "raw_sql",
        }
        schema = _dict_to_schema(d)
        assert schema.table_names() == {"users"}
        assert schema.get_table("users").get_column("id").primary_key is True
        assert schema.get_table("users").get_column("email").unique is True
        assert len(schema.get_table("users").indexes) == 1


# =====================================================================
# Test Pipeline Node Integration
# =====================================================================


class TestDBMigrationsNode:
    def test_node_with_no_backend_code(self):
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = {"backend_code": None}
        result = builder._db_migrations_node(state)
        assert result["schema_migration"] is None

    def test_node_with_empty_backend_code(self):
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = {"backend_code": {}}
        result = builder._db_migrations_node(state)
        assert result["schema_migration"] is None

    def test_node_with_schema_generates_migration(self):
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = {
            "backend_code": {"schema.sql": SQL_SCHEMA},
            "architecture": {"project_id": "test_proj_999"},
        }
        result = builder._db_migrations_node(state)
        # First build → empty previous schema → all tables are TABLE_ADDED
        assert result["schema_migration"] is not None
        migration = result["schema_migration"]
        assert "CREATE TABLE" in migration["content"]
        assert migration["has_data_loss_risk"] is False

        # Verify migration files added to backend_code
        assert result.get("backend_code") is not None
        filenames = list(result["backend_code"].keys())
        migration_files = [f for f in filenames if f.startswith("migrations/")]
        assert len(migration_files) >= 2  # up + down

    def test_node_survives_exception(self):
        """If extract_schema raises, node should return None gracefully."""
        from ai.agents.multi_agent import MultiAgentBuilder
        from unittest.mock import patch

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = {
            "backend_code": {"models.py": "invalid content"},
            "architecture": {},
        }

        with patch(
            "ai.agents.db_architect.extract_schema", side_effect=RuntimeError("boom")
        ):
            result = builder._db_migrations_node(state)
            assert result["schema_migration"] is None

    def test_route_after_guardrails_returns_db_migrations(self):
        """_route_after_guardrails should return 'db_migrations' instead of 'tester'."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = {"guardrails_violations": [], "guardrails_iteration": 0}
        route = builder._route_after_guardrails(state)
        assert route == "db_migrations"

    def test_route_after_guardrails_with_critical_over_limit(self):
        """Even when max cycles reached, should return 'db_migrations' not 'tester'."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = {
            "guardrails_violations": [
                {
                    "rule_id": "ARCH001",
                    "file_path": "a.tsx",
                    "line": 1,
                    "message": "test",
                    "fix_instruction": "fix",
                    "severity": "critical",
                }
            ],
            "guardrails_iteration": 3,
        }
        route = builder._route_after_guardrails(state)
        assert route == "db_migrations"


# =====================================================================
# Test End-to-End: Parse → Diff → Migrate
# =====================================================================


class TestEndToEnd:
    def test_add_column_full_flow(self):
        """
        Simulate a schema change: user table gains an avatar_url column.
        Verify the full pipeline produces correct ALTER TABLE SQL.
        """
        old_sql = """
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            name TEXT
        );
        """
        new_sql = """
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            name TEXT,
            avatar_url TEXT
        );
        """

        old_schema = parse_sql(old_sql)
        new_schema = parse_sql(new_sql)

        assert old_schema.table_names() == {"users"}
        assert new_schema.table_names() == {"users"}

        diffs = diff_schemas(old_schema, new_schema)
        assert len(diffs) == 1
        assert diffs[0].diff_type == DiffType.COLUMN_ADDED
        assert diffs[0].column_name == "avatar_url"

        migration = generate_migration(diffs)
        assert migration is not None
        assert "ALTER TABLE users ADD COLUMN avatar_url TEXT" in migration.content
        assert "DROP COLUMN avatar_url" in migration.rollback_content
        assert migration.has_data_loss_risk is False

    def test_remove_column_full_flow(self):
        """Column removal should flag data loss risk."""
        old_sql = (
            "CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT, old_field TEXT);"
        )
        new_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT);"

        old_schema = parse_sql(old_sql)
        new_schema = parse_sql(new_sql)

        diffs = diff_schemas(old_schema, new_schema)
        assert any(d.diff_type == DiffType.COLUMN_REMOVED for d in diffs)

        migration = generate_migration(diffs)
        assert migration.has_data_loss_risk is True
        assert "WARNING" in migration.content

    def test_add_table_full_flow(self):
        """Adding a new table from empty schema."""
        old_schema = Schema()
        new_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT NOT NULL);"
        new_schema = parse_sql(new_sql)

        diffs = diff_schemas(old_schema, new_schema)
        assert len(diffs) == 1
        assert diffs[0].diff_type == DiffType.TABLE_ADDED

        migration = generate_migration(diffs)
        assert "CREATE TABLE users" in migration.content
        assert "DROP TABLE IF EXISTS users" in migration.rollback_content

    def test_prisma_schema_evolution(self):
        """Simulate Prisma schema evolution: add a new field to User."""
        old_prisma = """
model User {
  id    String @id @default(cuid())
  email String @unique
  name  String?
}
"""
        new_prisma = """
model User {
  id        String   @id @default(cuid())
  email     String   @unique
  name      String?
  avatarUrl String?
  bio       String?
}
"""
        old_schema = parse_prisma(old_prisma)
        new_schema = parse_prisma(new_prisma)

        diffs = diff_schemas(old_schema, new_schema)
        added_cols = [d for d in diffs if d.diff_type == DiffType.COLUMN_ADDED]
        assert len(added_cols) == 2
        col_names = {d.column_name for d in added_cols}
        assert "avatarUrl" in col_names
        assert "bio" in col_names

        migration = generate_migration(diffs)
        assert "ALTER TABLE User ADD COLUMN" in migration.content
        assert migration.has_data_loss_risk is False

    def test_no_changes_no_migration(self):
        """Identical schemas should produce no diffs and no migration."""
        sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT);"
        schema = parse_sql(sql)
        diffs = diff_schemas(schema, schema)
        assert len(diffs) == 0
        assert generate_migration(diffs) is None
