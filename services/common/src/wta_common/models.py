"""SQLAlchemy models. Every row is scoped by workspace_id (and agent_id where relevant)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Fixed by the chunk_embeddings.embedding column type. Changing the embedding model to one
# with different dimensions requires a new column/migration plus re-indexing (see ADR 0002).
EMBEDDING_COLUMN_DIMENSIONS = 1024


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _ws_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )


def _agent_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = _created()


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Public, non-secret identifier used by the widget. NOT authentication.
    public_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allowed_origins: Mapped[list[str]] = mapped_column(
        ARRAY(String(300)), nullable=False, default=list
    )
    instructions: Mapped[str | None] = mapped_column(Text)
    crawl_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    __table_args__ = (Index("ix_agents_workspace", "workspace_id"),)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    agent_id: Mapped[uuid.UUID] = _agent_fk()
    url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("agent_id", "url", name="uq_sources_agent_url"),)


class CrawlRun(Base):
    __tablename__ = "crawl_runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    agent_id: Mapped[uuid.UUID] = _agent_fk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_crawl_runs_agent", "agent_id", "created_at"),)


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    agent_id: Mapped[uuid.UUID] = _agent_fk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    render_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="static")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="discovered")
    error: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    # Distinct timestamps: a changed timestamp is never proof of changed content.
    last_seen_in_sitemap: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin_last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("agent_id", "url", name="uq_pages_agent_url"),
        Index("ix_pages_workspace", "workspace_id"),
    )


class PageVersion(Base):
    __tablename__ = "page_versions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    agent_id: Mapped[uuid.UUID] = _agent_fk()
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_html_gz: Mapped[bytes | None] = mapped_column(LargeBinary)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("page_id", "content_hash", name="uq_page_versions_page_hash"),
    )


class SkippedUrl(Base):
    __tablename__ = "skipped_urls"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    agent_id: Mapped[uuid.UUID] = _agent_fk()
    crawl_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_runs.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_skipped_urls_run", "crawl_run_id"),)


class EmbeddingSpace(Base):
    """Provider/model/dimensions a set of vectors belongs to. Vectors from different
    spaces are never compared or mixed."""

    __tablename__ = "embedding_spaces"
    id: Mapped[uuid.UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("provider", "model", "dimensions", name="uq_embedding_spaces"),
        Index(
            "uq_embedding_spaces_one_active",
            "active",
            unique=True,
            postgresql_where="active",
        ),
    )


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    agent_id: Mapped[uuid.UUID] = _agent_fk()
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
    )
    page_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("page_versions.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True)
    )
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("page_version_id", "chunk_index", name="uq_chunks_version_index"),
        Index("ix_chunks_agent", "agent_id"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_hash", "agent_id", "content_hash"),
    )


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    embedding_space_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("embedding_spaces.id", ondelete="CASCADE"), primary_key=True
    )
    # Denormalised for pre-filtering by agent before vector search.
    agent_id: Mapped[uuid.UUID] = _agent_fk()
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_COLUMN_DIMENSIONS), nullable=False)
    __table_args__ = (
        Index(
            "ix_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunk_embeddings_agent_space", "agent_id", "embedding_space_id"),
    )
