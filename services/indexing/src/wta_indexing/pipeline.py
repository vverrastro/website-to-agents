"""Persist crawl events: pages, versions, chunks and embeddings.

Principles: every query is scoped by agent; hashes decide what changed (never timestamps);
only chunks with unseen content are embedded; each page is committed independently so a crawl
is resumable and a retry converges to the same state (idempotent).
"""

from __future__ import annotations

import asyncio
import gzip
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wta_common.crawl_config import AgentCrawlConfig
from wta_common.logging import get_logger
from wta_common.models import (
    EMBEDDING_COLUMN_DIMENSIONS,
    Agent,
    Chunk,
    ChunkEmbedding,
    CrawlRun,
    EmbeddingSpace,
    Page,
    PageVersion,
    SkippedUrl,
    Source,
)
from wta_common.settings import ConfigError
from wta_crawler import (
    CrawlConfig,
    Crawler,
    Fetcher,
    KnownPage,
    PageFetched,
    SitemapSeen,
    UrlSkipped,
    normalize_url,
)
from wta_crawler.render import Renderer

from wta_indexing.chunking import chunk_markdown, embedding_input
from wta_indexing.embeddings import EmbeddingError, EmbeddingProvider
from wta_indexing.extract import ExtractedPage, extract_page

log = get_logger("wta_indexing.pipeline")
SPACE_LOCK_KEY = 7_340_001
MAX_PAGE_ERRORS = 20

# Skip reasons that mean "this page must no longer be served from the index".
REMOVAL_STATUS = {
    "http_404": "removed",
    "http_410": "removed",
    "http_401": "excluded",
    "http_403": "excluded",
    "robots_disallowed": "excluded",
    "noindex_meta": "excluded",
}


class EmbeddingSpaceMismatch(ConfigError):
    """Configured embedding model differs from the active index. Never mix vectors silently."""


@dataclass
class RunStats:
    pages_fetched: int = 0
    pages_indexed: int = 0
    pages_unchanged: int = 0
    pages_removed: int = 0
    pages_empty: int = 0
    urls_skipped: int = 0
    chunks_created: int = 0
    chunks_embedded: int = 0
    chunks_reused: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class RunContext:
    run_id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    source_id: uuid.UUID


def _now() -> datetime:
    return datetime.now(UTC)


async def resolve_space(session: AsyncSession, embedder: EmbeddingProvider) -> EmbeddingSpace:
    """Return the active embedding space for ``embedder``, creating it on first use.

    A different configured model is an error: switch with an explicit re-index (``make reindex``).
    """
    if embedder.dimensions != EMBEDDING_COLUMN_DIMENSIONS:
        raise ConfigError(
            f"EMBEDDING_DIMENSIONS={embedder.dimensions} does not match the database column "
            f"vector({EMBEDDING_COLUMN_DIMENSIONS}). A different size needs a migration + re-index."
        )
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
    active = (
        await session.execute(select(EmbeddingSpace).where(EmbeddingSpace.active.is_(True)))
    ).scalar_one_or_none()
    if active is None:
        if space is None:
            space = EmbeddingSpace(
                provider=embedder.provider,
                model=embedder.model,
                dimensions=embedder.dimensions,
                config={},
                active=True,
            )
            session.add(space)
        else:
            space.active = True
        await session.flush()
        return space
    if space is None or space.id != active.id:
        raise EmbeddingSpaceMismatch(
            f"Index uses {active.provider}/{active.model} ({active.dimensions}d) but configuration "
            f"selects {embedder.provider}/{embedder.model} ({embedder.dimensions}d). "
            "Run `make reindex` to migrate, or restore the previous configuration."
        )
    return active


async def _get_or_create_page(session: AsyncSession, ctx: RunContext, url: str) -> Page:
    page = (
        await session.execute(select(Page).where(Page.agent_id == ctx.agent_id, Page.url == url))
    ).scalar_one_or_none()
    if page is None:
        page = Page(
            workspace_id=ctx.workspace_id,
            agent_id=ctx.agent_id,
            source_id=ctx.source_id,
            url=url,
        )
        session.add(page)
        await session.flush()
    return page


class IndexingPipeline:
    def __init__(self, embedder: EmbeddingProvider) -> None:
        self._embedder = embedder

    async def handle(
        self,
        session: AsyncSession,
        ctx: RunContext,
        event: PageFetched | UrlSkipped,
        space: EmbeddingSpace,
        stats: RunStats,
        sitemap_seen: set[str],
    ) -> None:
        if isinstance(event, UrlSkipped):
            await self._skip(session, ctx, event, stats)
        else:
            await self._page(session, ctx, event, space, stats, sitemap_seen)

    async def _skip(
        self, session: AsyncSession, ctx: RunContext, ev: UrlSkipped, stats: RunStats
    ) -> None:
        session.add(
            SkippedUrl(
                workspace_id=ctx.workspace_id,
                agent_id=ctx.agent_id,
                crawl_run_id=ctx.run_id,
                url=ev.url[:2000],
                reason=ev.reason[:100],
            )
        )
        stats.urls_skipped += 1
        page = (
            await session.execute(
                select(Page).where(Page.agent_id == ctx.agent_id, Page.url == ev.url)
            )
        ).scalar_one_or_none()
        if page is None:
            return
        status = REMOVAL_STATUS.get(ev.reason)
        if status:
            page.status, page.error, page.last_crawled_at = status, ev.reason, _now()
            await session.execute(delete(Chunk).where(Chunk.page_id == page.id))
            stats.pages_removed += 1
        elif ev.reason.startswith(("fetch_error", "http_")):
            page.error, page.last_crawled_at = ev.reason, _now()

    async def _page(
        self,
        session: AsyncSession,
        ctx: RunContext,
        ev: PageFetched,
        space: EmbeddingSpace,
        stats: RunStats,
        sitemap_seen: set[str],
    ) -> None:
        now = _now()
        stats.pages_fetched += 1
        page = await _get_or_create_page(session, ctx, ev.url)
        page.last_crawled_at = now
        page.etag = ev.etag
        page.origin_last_modified = ev.origin_last_modified
        page.render_mode = ev.render_mode
        if ev.url in sitemap_seen or ev.sitemap_lastmod is not None:
            page.last_seen_in_sitemap = now
        if ev.not_modified:
            page.error = None
            stats.pages_unchanged += 1
            return

        extracted = extract_page(ev.html, ev.final_url)
        page.canonical_url = ev.canonical_url
        drafts = chunk_markdown(extracted.text) if extracted else []
        if extracted is None or not drafts:
            page.status, page.error = "empty", "no_extractable_content"
            await session.execute(delete(Chunk).where(Chunk.page_id == page.id))
            stats.pages_empty += 1
            return

        version, new_version = await self._get_or_create_version(session, ctx, page, ev, extracted)
        chunks = list(
            (
                await session.execute(
                    select(Chunk)
                    .where(Chunk.page_version_id == version.id)
                    .order_by(Chunk.chunk_index)
                )
            ).scalars()
        )
        new_chunks = not chunks
        if new_chunks:
            chunks = [
                Chunk(
                    workspace_id=ctx.workspace_id,
                    agent_id=ctx.agent_id,
                    page_id=page.id,
                    page_version_id=version.id,
                    chunk_index=d.index,
                    heading_path=list(d.heading_path),
                    text=d.text,
                    content_hash=d.content_hash,
                )
                for d in drafts
            ]
            session.add_all(chunks)
            await session.flush()
            stats.chunks_created += len(chunks)
        before = stats.chunks_embedded + stats.chunks_reused
        # Embed (or reuse vectors for) anything missing, *before* old chunks are deleted so that
        # unchanged chunks of the previous version can donate their vectors.
        await self._ensure_embeddings(session, ctx, space, chunks, extracted.title, stats)
        await session.execute(
            delete(Chunk).where(Chunk.page_id == page.id, Chunk.page_version_id != version.id)
        )
        page.content_hash = extracted.content_hash
        page.status, page.error, page.indexed_at = "indexed", None, now
        if new_version or new_chunks or stats.chunks_embedded + stats.chunks_reused > before:
            stats.pages_indexed += 1
        else:
            stats.pages_unchanged += 1

    async def _get_or_create_version(
        self,
        session: AsyncSession,
        ctx: RunContext,
        page: Page,
        ev: PageFetched,
        extracted: ExtractedPage,
    ) -> tuple[PageVersion, bool]:
        version = (
            await session.execute(
                select(PageVersion).where(
                    PageVersion.page_id == page.id,
                    PageVersion.agent_id == ctx.agent_id,
                    PageVersion.content_hash == extracted.content_hash,
                )
            )
        ).scalar_one_or_none()
        if version is not None:
            if version.meta.get("links") != list(ev.links):
                version.meta = {**version.meta, "links": list(ev.links)}
            return version, False
        version = PageVersion(
            workspace_id=ctx.workspace_id,
            agent_id=ctx.agent_id,
            page_id=page.id,
            content_hash=extracted.content_hash,
            raw_html_gz=gzip.compress(ev.html),
            text=extracted.text,
            title=extracted.title,
            description=extracted.description,
            meta={
                "language": extracted.language,
                "headings": extracted.headings[:50],
                "final_url": ev.final_url,
                "render_mode": ev.render_mode,
                "links": list(ev.links),
            },
        )
        session.add(version)
        await session.flush()
        return version, True

    async def _ensure_embeddings(
        self,
        session: AsyncSession,
        ctx: RunContext,
        space: EmbeddingSpace,
        chunks: list[Chunk],
        title: str | None,
        stats: RunStats,
    ) -> None:
        have = set(
            (
                await session.execute(
                    select(ChunkEmbedding.chunk_id).where(
                        ChunkEmbedding.embedding_space_id == space.id,
                        ChunkEmbedding.chunk_id.in_([c.id for c in chunks]),
                    )
                )
            ).scalars()
        )
        missing = [c for c in chunks if c.id not in have]
        if not missing:
            return
        reusable: dict[str, Any] = {}
        rows = await session.execute(
            select(Chunk.content_hash, ChunkEmbedding.embedding)
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == Chunk.id)
            .where(
                Chunk.agent_id == ctx.agent_id,
                ChunkEmbedding.embedding_space_id == space.id,
                Chunk.content_hash.in_({c.content_hash for c in missing}),
            )
        )
        for content_hash, vector in rows:
            reusable.setdefault(content_hash, vector)
        to_embed = {c.content_hash: c for c in missing if c.content_hash not in reusable}
        fresh: dict[str, list[float]] = {}
        if to_embed:
            hashes = list(to_embed)
            texts = [
                embedding_input(title, tuple(to_embed[h].heading_path), to_embed[h].text)
                for h in hashes
            ]
            vectors = await self._embedder.embed(texts, input_type="document")
            fresh = dict(zip(hashes, vectors, strict=True))
        for c in missing:
            vector = reusable.get(c.content_hash)
            if vector is None:
                vector = fresh[c.content_hash]
                stats.chunks_embedded += 1
            else:
                stats.chunks_reused += 1
            session.add(
                ChunkEmbedding(
                    chunk_id=c.id,
                    embedding_space_id=space.id,
                    agent_id=ctx.agent_id,
                    embedding=vector,
                )
            )


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, (ConfigError, EmbeddingError)):
        return str(exc)[:500]  # our own messages: actionable and free of secrets
    return "internal_error"


async def mark_run_failed(
    session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID, message: str
) -> None:
    async with session_factory() as session, session.begin():
        run = await session.get(CrawlRun, run_id)
        if run is not None:
            run.status, run.error, run.finished_at = "failed", message[:500], _now()


async def execute_crawl_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    fetcher: Fetcher,
    embedder: EmbeddingProvider,
    user_agent: str,
    renderer: Renderer | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> RunStats:
    """Run one crawl and index it. Safe to re-run: state converges via content hashes."""
    stats = RunStats()
    async with session_factory() as session, session.begin():
        run = await session.get(CrawlRun, run_id)
        if run is None:
            raise LookupError("crawl run not found")
        if run.status == "succeeded":
            return stats
        agent = (
            await session.execute(
                select(Agent).where(
                    Agent.id == run.agent_id, Agent.workspace_id == run.workspace_id
                )
            )
        ).scalar_one()
        source = (
            await session.execute(
                select(Source).where(Source.id == run.source_id, Source.agent_id == run.agent_id)
            )
        ).scalar_one()
        ctx = RunContext(run.id, run.workspace_id, run.agent_id, run.source_id)
        run.status, run.started_at, run.error = "running", _now(), None
        raw_cfg, source_url = dict(agent.crawl_config or {}), source.url
        known_rows = (
            await session.execute(
                select(
                    Page.url,
                    Page.etag,
                    Page.origin_last_modified,
                    Page.last_crawled_at,
                    PageVersion.meta,
                )
                .join(
                    PageVersion,
                    (PageVersion.page_id == Page.id)
                    & (PageVersion.content_hash == Page.content_hash),
                )
                .where(Page.agent_id == run.agent_id, Page.status == "indexed")
            )
        ).all()
    known = {
        url: KnownPage(
            etag=etag,
            last_modified=format_datetime(lm.astimezone(UTC), usegmt=True) if lm else None,
            last_crawled_at=crawled,
            links=tuple((meta or {}).get("links", ())),
        )
        for url, etag, lm, crawled, meta in known_rows
    }

    try:
        cfg = AgentCrawlConfig.model_validate(raw_cfg)
        render_urls = frozenset(u for r in cfg.render_urls if (u := normalize_url(r)))
        crawl_cfg = CrawlConfig(
            max_pages=cfg.max_pages,
            max_depth=cfg.max_depth,
            min_delay_seconds=cfg.min_delay_seconds,
            max_duration_seconds=cfg.max_duration_seconds,
            use_sitemap=cfg.use_sitemap,
            rendering_enabled=renderer is not None,
            render_urls=render_urls,
        )
        async with session_factory() as session, session.begin():
            space = await resolve_space(session, embedder)
        space_id = space.id

        pipeline = IndexingPipeline(embedder)
        crawler = Crawler(
            fetcher, crawl_cfg, user_agent=user_agent, renderer=renderer, sleep=sleep, clock=clock
        )
        sitemap_seen: set[str] = set()
        async for event in crawler.crawl(source_url, known):
            if isinstance(event, SitemapSeen):
                sitemap_seen.add(event.url)
                continue
            try:
                async with session_factory() as session, session.begin():
                    sp = await session.get(EmbeddingSpace, space_id)
                    assert sp is not None
                    await pipeline.handle(session, ctx, event, sp, stats, sitemap_seen)
            except EmbeddingError as exc:
                if exc.fatal:
                    raise
                stats.errors += 1
                await _record_page_error(session_factory, ctx, event, exc.code)
            except Exception:
                log.exception("page_indexing_failed", url=event.url, run_id=str(run_id))
                stats.errors += 1
                if stats.errors >= MAX_PAGE_ERRORS:
                    raise
    except Exception as exc:
        log.exception("crawl_run_failed", run_id=str(run_id))
        message = _safe_error(exc)
        async with session_factory() as session, session.begin():
            failed = await session.get(CrawlRun, run_id)
            if failed is not None:
                failed.status, failed.error = "failed", message
                failed.finished_at, failed.stats = _now(), stats.as_dict()
        return stats

    async with session_factory() as session, session.begin():
        done = await session.get(CrawlRun, run_id)
        assert done is not None
        done.status, done.finished_at, done.stats = "succeeded", _now(), stats.as_dict()
    return stats


async def _record_page_error(
    session_factory: async_sessionmaker[AsyncSession],
    ctx: RunContext,
    event: PageFetched | UrlSkipped,
    code: str,
) -> None:
    async with session_factory() as session, session.begin():
        page = await _get_or_create_page(session, ctx, event.url)
        page.status, page.error, page.last_crawled_at = "error", code, _now()
