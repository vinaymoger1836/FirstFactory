"""SQLAlchemy Core tables for the pipeline (schema owned by migration 0003)."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

project = Table(
    "project",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("token_hash", String(64)),
    Column("name", String(200)),
    Column("created_at", DateTime(timezone=True)),
    Column("last_activity_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True)),
    Column("owner_id", UUID(as_uuid=True)),
    Column("catalog_version", Text),
    Column("pack_versions", JSONB),
    Column("template", JSONB),
    Column("template_version", Integer),
    Column("status", Text),
    Column("stage_budgets", JSONB),
    Column("attachment_seq", Integer),
)

stage_run = Table(
    "stage_run",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("project_id", UUID(as_uuid=True), ForeignKey("project.id")),
    Column("stage", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("inputs_hash", String(64), nullable=False),
    Column("upstream", JSONB, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("artifact", JSONB),
    Column("summary", Text, nullable=False),
    Column("assumptions", JSONB, nullable=False),
    Column("questions", JSONB, nullable=False),
    Column("issues", JSONB, nullable=False),
    Column("notices", JSONB, nullable=False),
    Column("placeholder", Boolean, nullable=False),
    Column("tokens_used", Integer, nullable=False),
    Column("token_budget", Integer, nullable=False),
    Column("work", JSONB, nullable=False),
    Column("thread_id", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

attachment = Table(
    "attachment",
    metadata,
    Column("project_id", UUID(as_uuid=True), ForeignKey("project.id"), primary_key=True),
    Column("id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("filename", Text, nullable=False),
    Column("mime", Text, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("path", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("meta", JSONB, nullable=False),
    Column("derived_path", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

llm_call = Table(
    "llm_call",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("project_id", UUID(as_uuid=True)),
    Column("stage_run_id", UUID(as_uuid=True)),
    Column("role", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("fallback_from", Text),
    Column("prompt_tokens", Integer, nullable=False),
    Column("completion_tokens", Integer, nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("cassette_key", String(64)),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

change_set = Table(
    "change_set",
    metadata,
    Column("project_id", UUID(as_uuid=True), primary_key=True),
    Column("id", Text, primary_key=True),
    Column("stage", Text, nullable=False),
    Column("base_version", Integer, nullable=False),
    Column("result_version", Integer),
    Column("ops", JSONB, nullable=False),
    Column("diff", JSONB, nullable=False),
    Column("reason", Text, nullable=False),
    Column("origin", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("applied_at", DateTime(timezone=True)),
)

pipeline_thread = Table(
    "pipeline_thread",
    metadata,
    Column("thread_id", Text, primary_key=True),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("started_from", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

stage_event = Table(
    "stage_event",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("project_id", UUID(as_uuid=True), nullable=False),
    Column("stage", Text),
    Column("run_id", UUID(as_uuid=True)),
    Column("kind", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

vendor_cache = Table(
    "vendor_cache",
    metadata,
    Column("cache_key", Text, primary_key=True),
    Column("vendor_class", Text, nullable=False),
    Column("region", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

catalog_entry = Table(
    "catalog_entry",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("version_id", BigInteger),
    Column("code", Text),
    Column("kind", Text),
    Column("name", Text),
    Column("data", JSONB),
)

pack_value = Table(
    "pack_value",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("version_id", BigInteger),
    Column("key", Text),
    Column("value", JSONB),
)

library_version = Table(
    "library_version",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("kind", Text),
    Column("scope", Text),
    Column("version", Text),
    Column("loaded_at", DateTime(timezone=True)),
)
