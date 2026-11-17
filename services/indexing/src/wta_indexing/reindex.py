"""Explicit migration to a new embedding model: ``make reindex``.

Embeds every existing chunk into a *new* embedding space, then flips the active space in one
transaction. Retrieval keeps using the old space until the switch, so vectors from different
models are never mixed. Resumable: chunks already embedded in the target space are skipped.
Old vectors are kept (roll back by restoring the previous configuration and re-running).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from sqlalchemy import exists, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wta_common.db import make_engine, make_session_factory
from wta_common.logging import configure_logging, get_logger
from wta_common.models import (
    EMBEDDING_COLUMN_DIMENSIONS,
    Chunk,
    ChunkEmbedding,
    EmbeddingSpace,
    PageVersion,
)
from wta_common.settings import ConfigError, get_settings

from wta_indexing.chunking import embedding_input
from wta_indexing.embeddings import EmbeddingProvider, build_embedding_provider
from wta_indexing.pipeline import SPACE_LOCK_KEY

log = get_logger("wta_indexing.reindex")


async def reindex_all(
    session_factory: async_sessionmaker[AsyncSession],
    embedder: EmbeddingProvider,
    *,
    batch_size: int = 64,
) -> int:
    if embedder.dimensions != EMBEDDING_COLUMN_DIMENSIONS:
        raise ConfigError(
            f"Embedding dimensions {embedder.dimensions} != database "
            f"vector({EMBEDDING_COLUMN_DIMENSIONS}). Add a migration for the new size first."
        )
    async with session_factory() as session, session.begin():
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SPACE_LOCK_KEY})
        space = (
            await session.execute(
                select(EmbeddingSpace).where(
                    EmbeddingSpace.provider == embedder.provider,
                    EmbeddingSpace.model == embedder.model,
                    EmbeddingSpace.dimensions == embedder.dimensions,
                )
            )
        ).scalar_one_or_none()
        if space is None:
            space = EmbeddingSpace(
                provider=embedder.provider,
                model=embedder.model,
                dimensions=embedder.dimensions,
                config={},
                active=False,
            )
            session.add(space)
            await session.flush()
        space_id = space.id

    total = 0
    while True:
        async with session_factory() as session, session.begin():
            rows: Any = (
                await session.execute(
                    select(Chunk, PageVersion.title)
                    .join(PageVersion, PageVersion.id == Chunk.page_version_id)
                    .where(
                        ~exists().where(
                            ChunkEmbedding.chunk_id == Chunk.id,
                            ChunkEmbedding.embedding_space_id == space_id,
                        )
                    )
                    .order_by(Chunk.id)
                    .limit(batch_size)
                )
            ).all()
            if not rows:
                break
            texts = [embedding_input(t, tuple(c.heading_path), c.text) for c, t in rows]
            vectors = await embedder.embed(texts, input_type="document")
            for (chunk, _), vector in zip(rows, vectors, strict=True):
                session.add(
                    ChunkEmbedding(
                        chunk_id=chunk.id,
                        embedding_space_id=space_id,
                        agent_id=chunk.agent_id,
                        embedding=vector,
                    )
                )
            total += len(rows)
        log.info("reindex_progress", embedded=total)

    async with session_factory() as session, session.begin():
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SPACE_LOCK_KEY})
        await session.execute(
            update(EmbeddingSpace).where(EmbeddingSpace.active.is_(True)).values(active=False)
        )
        await session.execute(
            update(EmbeddingSpace).where(EmbeddingSpace.id == space_id).values(active=True)
        )
    log.info("reindex_done", embedded=total, provider=embedder.provider, model=embedder.model)
    return total


async def _main() -> int:
    configure_logging()
    settings = get_settings()
    try:
        embedder = build_embedding_provider(settings)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    engine = make_engine(settings.database_url)
    try:
        total = await reindex_all(make_session_factory(engine), embedder)
    finally:
        await engine.dispose()
    print(f"re-indexed {total} chunks into {embedder.provider}/{embedder.model}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
