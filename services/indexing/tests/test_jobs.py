"""arq job behaviour: per-agent crawl lock, actionable config errors, scheduled recrawls, retention."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wta_common.models import Agent, ChatMessage, Conversation, CrawlRun, Source, Workspace
from wta_common.settings import Settings
from wta_crawler import Fetcher
from wta_indexing import jobs

pytestmark = pytest.mark.integration
SF = async_sessionmaker[AsyncSession]


def settings(**kw: Any) -> Settings:
    kw.setdefault("embedding_provider", "fake")
    return Settings(_env_file=None, **kw)


@pytest.fixture
async def redis_client():  # type: ignore[no-untyped-def]
    client = aioredis.from_url(Settings(_env_file=None).redis_url)  # type: ignore[no-untyped-call]
    yield client
    await client.aclose()


async def make_run(
    sf: SF, *, crawl_config: dict[str, Any] | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    async with sf() as s, s.begin():
        ws = Workspace(name="w")
        s.add(ws)
        await s.flush()
        agent = Agent(
            workspace_id=ws.id, name="a", public_id=f"pub_{uuid.uuid4().hex[:10]}",
            crawl_config=crawl_config or {"min_delay_seconds": 0.2, "max_depth": 0},
        )  # fmt: skip
        s.add(agent)
        await s.flush()
        source = Source(workspace_id=ws.id, agent_id=agent.id, url="https://acme.test/")
        s.add(source)
        await s.flush()
        run = CrawlRun(workspace_id=ws.id, agent_id=agent.id, source_id=source.id)
        s.add(run)
        await s.flush()
        return run.id, agent.id


def mock_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        "<html><head><title>Acme</title></head><body><article><h1>Acme</h1><p>"
        + "We sell widgets. " * 20
        + "</p></article></body></html>"
    )

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/html"})

    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(
        jobs,
        "Fetcher",
        lambda **kw: Fetcher(
            user_agent=kw["user_agent"], resolver=resolver, transport=httpx.MockTransport(handler)
        ),
    )


async def test_crawl_site_runs_and_releases_the_lock(
    session_factory: SF, redis_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_fetcher(monkeypatch)
    run_id, agent_id = await make_run(session_factory)
    ctx = {"settings": settings(), "session_factory": session_factory, "redis": redis_client}
    stats = await jobs.crawl_site(ctx, str(run_id))
    assert isinstance(stats, dict) and stats["pages_indexed"] == 1 and stats["errors"] == 0
    async with session_factory() as s:
        run = await s.get(CrawlRun, run_id)
        assert run and run.status == "succeeded"
    assert await redis_client.get(f"{jobs.LOCK_PREFIX}{agent_id}") is None


async def test_a_second_crawl_for_the_same_agent_is_refused_and_keeps_the_lock(
    session_factory: SF, redis_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_fetcher(monkeypatch)
    run_id, agent_id = await make_run(session_factory)
    key = f"{jobs.LOCK_PREFIX}{agent_id}"
    await redis_client.set(key, "another-run", ex=60)  # a crawl is already in progress
    ctx = {"settings": settings(), "session_factory": session_factory, "redis": redis_client}
    assert await jobs.crawl_site(ctx, str(run_id)) == "locked"
    async with session_factory() as s:
        run = await s.get(CrawlRun, run_id)
        assert run and run.status == "failed" and run.error == "another_crawl_in_progress"
    assert await redis_client.get(key) == b"another-run"  # the other crawl's lock is untouched
    await redis_client.delete(key)


async def test_missing_provider_key_fails_the_run_with_an_actionable_message(
    session_factory: SF, redis_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_fetcher(monkeypatch)
    run_id, agent_id = await make_run(session_factory)
    ctx = {
        "settings": settings(embedding_provider="voyage"),
        "session_factory": session_factory,
        "redis": redis_client,
    }
    assert await jobs.crawl_site(ctx, str(run_id)) == "embedding_provider_not_configured"
    async with session_factory() as s:
        run = await s.get(CrawlRun, run_id)
        assert run and run.status == "failed" and "VOYAGE_API_KEY" in (run.error or "")
    assert await redis_client.get(f"{jobs.LOCK_PREFIX}{agent_id}") is None


async def test_unknown_run_is_a_noop(session_factory: SF, redis_client: Any) -> None:
    ctx = {"settings": settings(), "session_factory": session_factory, "redis": redis_client}
    assert await jobs.crawl_site(ctx, str(uuid.uuid4())) == "run_not_found"


class Pool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, str, str | None]] = []

    async def enqueue_job(self, name: str, run_id: str, _job_id: str | None = None) -> None:
        self.jobs.append((name, run_id, _job_id))


async def test_schedule_recrawls_respects_interval_and_running_state(session_factory: SF) -> None:
    due, _ = await make_run(session_factory, crawl_config={"recrawl_interval_hours": 1})
    recent, _ = await make_run(session_factory, crawl_config={"recrawl_interval_hours": 1})
    busy, _ = await make_run(session_factory, crawl_config={"recrawl_interval_hours": 1})
    manual, _ = await make_run(session_factory)  # no interval: never scheduled
    async with session_factory() as s, s.begin():
        old = datetime.now(UTC) - timedelta(hours=3)
        await s.execute(
            update(CrawlRun).where(CrawlRun.id == due).values(status="succeeded", created_at=old)
        )
        await s.execute(update(CrawlRun).where(CrawlRun.id == recent).values(status="succeeded"))
        await s.execute(
            update(CrawlRun).where(CrawlRun.id == busy).values(status="running", created_at=old)
        )
    pool = Pool()
    n = await jobs.schedule_recrawls({"session_factory": session_factory, "redis": pool})
    assert n == 1 and len(pool.jobs) == 1
    name, new_run_id, job_id = pool.jobs[0]
    assert name == "crawl_site" and job_id == f"crawl-{new_run_id}"
    async with session_factory() as s:
        new = await s.get(CrawlRun, uuid.UUID(new_run_id))
        assert new and new.status == "queued" and new.id not in (due, recent, busy, manual)
        due_run = await s.get(CrawlRun, due)
        assert new.agent_id == due_run.agent_id  # type: ignore[union-attr]
    assert (
        await jobs.schedule_recrawls({"session_factory": session_factory, "redis": pool}) == 0
    )  # idempotent


async def test_purge_job_uses_the_configured_retention(session_factory: SF) -> None:
    run_id, agent_id = await make_run(session_factory)
    async with session_factory() as s, s.begin():
        agent = await s.get(Agent, agent_id)
        assert agent
        old = Conversation(workspace_id=agent.workspace_id, agent_id=agent.id)
        s.add(old)
        await s.flush()
        s.add(
            ChatMessage(
                workspace_id=agent.workspace_id,
                agent_id=agent.id,
                conversation_id=old.id,
                role="user",
                content="hi",
            )
        )
        await s.execute(
            update(Conversation).values(last_activity_at=datetime.now(UTC) - timedelta(days=45))
        )
    ctx = {"settings": settings(conversation_retention_days=30), "session_factory": session_factory}
    assert await jobs.purge_conversations(ctx) == 1
    async with session_factory() as s:
        assert (await s.execute(select(Conversation))).scalars().all() == []
        assert (await s.execute(select(ChatMessage))).scalars().all() == []
    assert run_id
