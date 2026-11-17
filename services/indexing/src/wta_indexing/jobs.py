"""arq jobs: crawl_site (idempotent) and scheduled recrawls."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wta_common.crawl_config import AgentCrawlConfig
from wta_common.logging import get_logger
from wta_common.models import Agent, CrawlRun, Source
from wta_common.retention import purge_expired_conversations
from wta_common.settings import ConfigError, Settings
from wta_crawler import Fetcher
from wta_crawler.render import PlaywrightRenderer

from wta_indexing.embeddings import build_embedding_provider
from wta_indexing.pipeline import RunStats, execute_crawl_run, mark_run_failed

log = get_logger("wta_indexing.jobs")
LOCK_PREFIX = "wta:crawl-lock:"


async def create_crawl_run(session: AsyncSession, agent: Agent, source: Source) -> CrawlRun:
    run = CrawlRun(
        workspace_id=agent.workspace_id, agent_id=agent.id, source_id=source.id, status="queued"
    )
    session.add(run)
    await session.flush()
    return run


async def enqueue_crawl(pool: ArqRedis, run_id: uuid.UUID) -> None:
    # Stable job id: enqueueing the same run twice is a no-op.
    await pool.enqueue_job("crawl_site", str(run_id), _job_id=f"crawl-{run_id}")


async def crawl_site(ctx: dict[str, Any], crawl_run_id: str) -> dict[str, int] | str:
    settings: Settings = ctx["settings"]
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    redis = ctx["redis"]
    run_id = uuid.UUID(crawl_run_id)

    async with session_factory() as session:
        run = await session.get(CrawlRun, run_id)
        if run is None:
            return "run_not_found"
        agent = (
            await session.execute(
                select(Agent).where(
                    Agent.id == run.agent_id, Agent.workspace_id == run.workspace_id
                )
            )
        ).scalar_one()
        duration = AgentCrawlConfig.model_validate(agent.crawl_config or {}).max_duration_seconds
    lock_key = f"{LOCK_PREFIX}{run.agent_id}"
    if not await redis.set(lock_key, crawl_run_id, nx=True, ex=duration + 600):
        await mark_run_failed(session_factory, run_id, "another_crawl_in_progress")
        return "locked"
    fetcher: Fetcher | None = None
    try:
        try:
            embedder = build_embedding_provider(settings)
        except ConfigError as exc:
            await mark_run_failed(session_factory, run_id, str(exc))
            return "embedding_provider_not_configured"
        fetcher = Fetcher(user_agent=settings.crawler_user_agent)
        renderer = (
            PlaywrightRenderer(settings.crawler_user_agent)
            if settings.crawler_rendering_enabled
            else None
        )
        stats: RunStats = await execute_crawl_run(
            session_factory,
            run_id,
            fetcher=fetcher,
            embedder=embedder,
            user_agent=settings.crawler_user_agent,
            renderer=renderer,
        )
        return stats.as_dict()
    finally:
        if fetcher is not None:
            await fetcher.aclose()
        await redis.delete(lock_key)


async def schedule_recrawls(ctx: dict[str, Any]) -> int:
    """Cron: enqueue a crawl for every source whose agent's recrawl interval has elapsed."""
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    pool: ArqRedis = ctx["redis"]
    enqueued: list[uuid.UUID] = []
    async with session_factory() as session, session.begin():
        agents = (await session.execute(select(Agent))).scalars().all()
        for agent in agents:
            crawl_cfg = AgentCrawlConfig.model_validate(agent.crawl_config or {})
            interval = crawl_cfg.recrawl_interval_hours
            if interval is None:
                continue
            for source in (
                await session.execute(
                    select(Source).where(
                        Source.agent_id == agent.id, Source.workspace_id == agent.workspace_id
                    )
                )
            ).scalars():
                last = (
                    await session.execute(
                        select(CrawlRun)
                        .where(CrawlRun.agent_id == agent.id, CrawlRun.source_id == source.id)
                        .order_by(CrawlRun.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if last is not None and (
                    last.status in ("queued", "running")
                    or last.created_at > datetime.now(UTC) - timedelta(hours=interval)
                ):
                    continue
                enqueued.append((await create_crawl_run(session, agent, source)).id)
    for run_id in enqueued:
        await enqueue_crawl(pool, run_id)
    log.info("recrawls_scheduled", count=len(enqueued))
    return len(enqueued)


async def purge_conversations(ctx: dict[str, Any]) -> int:
    """Cron: delete conversations older than CONVERSATION_RETENTION_DAYS."""
    settings: Settings = ctx["settings"]
    removed = await purge_expired_conversations(
        ctx["session_factory"], settings.conversation_retention_days
    )
    log.info("conversations_purged", count=removed)
    return removed
