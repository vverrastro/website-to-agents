"""Integration tests: crawl → extract → chunk → embed → store (needs Postgres: `make up`)."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wta_common.models import (
    Agent,
    Chunk,
    ChunkEmbedding,
    CrawlRun,
    EmbeddingSpace,
    Page,
    PageVersion,
    SkippedUrl,
    Source,
    Workspace,
)
from wta_crawler import Fetcher
from wta_crawler.fetcher import FetchLimits
from wta_indexing.embeddings import EmbeddingError, FakeEmbeddingProvider
from wta_indexing.pipeline import EmbeddingSpaceMismatch, execute_crawl_run, resolve_space

pytestmark = pytest.mark.integration
SF = async_sessionmaker[AsyncSession]


async def resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


def para(topic: str) -> str:
    return f"<p>{(topic + ' is explained in detail here. ') * 8}</p>"


class Site:
    """A mutable fake website served through httpx.MockTransport."""

    def __init__(self) -> None:
        self.pages: dict[str, str] = {
            "/": f"<h1>Home</h1>{para('welcome')}<a href='/pricing'>p</a><a href='/docs'>d</a>",
            "/pricing": f"<h1>Pricing</h1>{para('plans')}<h2>Enterprise</h2>{para('contracts')}",
            "/docs": f"<h1>Docs</h1>{para('installation')}<h2>Usage</h2>{para('commands')}",
        }
        self.requests: list[str] = []
        self.etags = True

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.requests.append(path)
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404)
        body = self.pages.get(path)
        if body is None:
            return httpx.Response(404, headers={"content-type": "text/html"})
        etag = f'"{hash(body) & 0xFFFFFF}"'
        if self.etags and req.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"etag": etag})
        doc = f"<html><head><title>Acme {path}</title></head><body><article>{body}</article></body></html>"
        headers = {"content-type": "text/html"}
        if self.etags:
            headers["etag"] = etag
        return httpx.Response(200, content=doc.encode(), headers=headers)

    def fetcher(self) -> Fetcher:
        return Fetcher(
            user_agent="TestBot/1.0",
            resolver=resolver,
            transport=httpx.MockTransport(self.handler),
            limits=FetchLimits(),
        )


async def make_agent(sf: SF, name: str = "acme", url: str = "https://acme.test/") -> uuid.UUID:
    """Create workspace(if needed)/agent/source/queued run; returns the run id."""
    async with sf() as s, s.begin():
        ws = (await s.execute(select(Workspace))).scalars().first() or Workspace(name="local")
        s.add(ws)
        await s.flush()
        agent = Agent(
            workspace_id=ws.id,
            name=name,
            public_id=f"pub_{uuid.uuid4().hex[:12]}",
            crawl_config={"min_delay_seconds": 0.2, "max_depth": 2},
        )
        s.add(agent)
        await s.flush()
        source = Source(workspace_id=ws.id, agent_id=agent.id, url=url)
        s.add(source)
        await s.flush()
        run = CrawlRun(workspace_id=ws.id, agent_id=agent.id, source_id=source.id)
        s.add(run)
        await s.flush()
        return run.id


async def new_run(sf: SF, previous: uuid.UUID) -> uuid.UUID:
    async with sf() as s, s.begin():
        old = await s.get(CrawlRun, previous)
        assert old
        run = CrawlRun(
            workspace_id=old.workspace_id, agent_id=old.agent_id, source_id=old.source_id
        )
        s.add(run)
        await s.flush()
        return run.id


async def crawl(
    sf: SF, run_id: uuid.UUID, site: Site, embedder: Any = None
) -> tuple[Any, FakeEmbeddingProvider]:
    embedder = embedder or FakeEmbeddingProvider()

    async def no_sleep(_: float) -> None:
        return None

    async with site.fetcher() as fetcher:
        stats = await execute_crawl_run(
            sf, run_id, fetcher=fetcher, embedder=embedder, user_agent="TestBot/1.0", sleep=no_sleep
        )
    return stats, embedder


async def count(sf: SF, model: Any, *where: Any) -> int:
    async with sf() as s:
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def test_full_pipeline_indexes_pages_chunks_and_embeddings(session_factory: SF) -> None:
    site = Site()
    run_id = await make_agent(session_factory)
    stats, embedder = await crawl(session_factory, run_id, site)

    assert stats.errors == 0 and stats.pages_indexed == 3 and stats.pages_fetched == 3
    assert await count(session_factory, Page, Page.status == "indexed") == 3
    assert await count(session_factory, PageVersion) == 3
    n_chunks = await count(session_factory, Chunk)
    assert n_chunks >= 5 and stats.chunks_created == n_chunks
    assert await count(session_factory, ChunkEmbedding) == n_chunks
    assert sum(size for _, size in embedder.calls) == stats.chunks_embedded == n_chunks

    async with session_factory() as s:
        run = await s.get(CrawlRun, run_id)
        assert run and run.status == "succeeded" and run.stats["pages_indexed"] == 3
        assert run.started_at and run.finished_at
        space = (await s.execute(select(EmbeddingSpace))).scalar_one()
        assert (space.provider, space.model, space.dimensions, space.active) == (
            "fake", "fake-hash-v1", 1024, True,
        )  # fmt: skip
        pricing = (
            await s.execute(select(Page).where(Page.url == "https://acme.test/pricing"))
        ).scalar_one()
        assert pricing.content_hash and pricing.indexed_at and pricing.last_crawled_at
        assert pricing.last_seen_in_sitemap is None  # no sitemap existed
        chunks = (await s.execute(select(Chunk).where(Chunk.page_id == pricing.id))).scalars().all()
        assert {tuple(c.heading_path) for c in chunks} >= {("Pricing",), ("Pricing", "Enterprise")}
        v = (
            await s.execute(select(PageVersion).where(PageVersion.page_id == pricing.id))
        ).scalar_one()
        assert v.title == "Acme /pricing" and v.raw_html_gz and v.text.startswith("# Pricing")


async def test_unchanged_recrawl_does_not_reindex_or_reembed(session_factory: SF) -> None:
    site = Site()
    run1 = await make_agent(session_factory)
    await crawl(session_factory, run1, site)
    versions = await count(session_factory, PageVersion)

    stats, embedder = await crawl(session_factory, await new_run(session_factory, run1), site)
    assert stats.pages_unchanged == 3 and stats.pages_indexed == 0
    assert embedder.calls == [] and await count(session_factory, PageVersion) == versions


async def test_recrawl_without_validators_still_detects_no_change_by_hash(
    session_factory: SF,
) -> None:
    site = Site()
    site.etags = False  # origin gives no ETag/Last-Modified: the content hash decides
    run1 = await make_agent(session_factory)
    await crawl(session_factory, run1, site)
    stats, embedder = await crawl(session_factory, await new_run(session_factory, run1), site)
    assert stats.pages_indexed == 0 and stats.pages_unchanged == 3 and embedder.calls == []
    assert await count(session_factory, PageVersion) == 3


async def test_changed_page_reembeds_only_new_chunks_and_drops_stale_ones(
    session_factory: SF,
) -> None:
    site = Site()
    run1 = await make_agent(session_factory)
    await crawl(session_factory, run1, site)
    site.pages["/pricing"] = (
        f"<h1>Pricing</h1>{para('plans')}<h2>Enterprise</h2>{para('brand new offer')}"
    )

    stats, embedder = await crawl(session_factory, await new_run(session_factory, run1), site)
    assert stats.pages_indexed == 1 and stats.pages_unchanged == 2
    assert stats.chunks_embedded == 1 and stats.chunks_reused >= 1  # unchanged section reused
    assert sum(size for _, size in embedder.calls) == 1

    async with session_factory() as s:
        pricing = (
            await s.execute(select(Page).where(Page.url == "https://acme.test/pricing"))
        ).scalar_one()
        texts = " ".join(
            c.text
            for c in (await s.execute(select(Chunk).where(Chunk.page_id == pricing.id))).scalars()
        )
        assert "brand new offer" in texts and "contracts" not in texts  # old version's chunks gone
        assert await count(session_factory, PageVersion, PageVersion.page_id == pricing.id) == 2
    n = await count(session_factory, Chunk)
    assert await count(session_factory, ChunkEmbedding) == n


async def test_deleted_and_blocked_pages_are_removed_from_index(session_factory: SF) -> None:
    site = Site()
    run1 = await make_agent(session_factory)
    await crawl(session_factory, run1, site)
    site.pages["/docs"] = ""  # keep linked but make it 404 below
    del site.pages["/docs"]

    stats, _ = await crawl(session_factory, await new_run(session_factory, run1), site)
    async with session_factory() as s:
        docs = (
            await s.execute(select(Page).where(Page.url == "https://acme.test/docs"))
        ).scalar_one()
        assert docs.status == "removed" and docs.error == "http_404"
        assert await count(session_factory, Chunk, Chunk.page_id == docs.id) == 0
        reasons = (await s.execute(select(SkippedUrl.reason))).scalars().all()
        assert "http_404" in reasons
    assert stats.pages_removed == 1


async def test_retrying_the_same_run_is_idempotent(session_factory: SF) -> None:
    site = Site()
    run1 = await make_agent(session_factory)
    await crawl(session_factory, run1, site)
    chunks, embeddings = (
        await count(session_factory, Chunk),
        await count(session_factory, ChunkEmbedding),
    )
    async with session_factory() as s, s.begin():  # simulate a crashed/retried run
        run = await s.get(CrawlRun, run1)
        assert run
        run.status = "running"
    await crawl(session_factory, run1, site)
    assert (await count(session_factory, Chunk), await count(session_factory, ChunkEmbedding)) == (
        chunks,
        embeddings,
    )
    assert await count(session_factory, PageVersion) == 3


async def test_agents_are_isolated_from_each_other(session_factory: SF) -> None:
    site = Site()
    run_a = await make_agent(session_factory, "a")
    run_b = await make_agent(session_factory, "b")
    await crawl(session_factory, run_a, site)
    stats_b, embedder_b = await crawl(session_factory, run_b, site)
    async with session_factory() as s:
        a, b = (await s.execute(select(Agent).order_by(Agent.name))).scalars().all()
        for agent in (a, b):
            assert await count(session_factory, Page, Page.agent_id == agent.id) == 3
            assert await count(session_factory, Chunk, Chunk.agent_id == agent.id) > 0
    # B reuses A's identical content vectors? No: reuse is scoped per agent, so B embeds its own.
    assert stats_b.chunks_embedded > 0 and stats_b.chunks_reused == 0
    assert sum(size for _, size in embedder_b.calls) == stats_b.chunks_embedded


async def test_embedding_model_change_is_refused_not_mixed(session_factory: SF) -> None:
    site = Site()
    run1 = await make_agent(session_factory)
    await crawl(session_factory, run1, site)

    other = FakeEmbeddingProvider()
    other.model = "different-model"
    async with session_factory() as s, s.begin():
        with pytest.raises(EmbeddingSpaceMismatch, match="make reindex"):
            await resolve_space(s, other)

    stats, _ = await crawl(
        session_factory, await new_run(session_factory, run1), site, embedder=other
    )
    async with session_factory() as s:
        failed = (
            (await s.execute(select(CrawlRun).order_by(CrawlRun.created_at.desc())))
            .scalars()
            .first()
        )
        assert failed and failed.status == "failed" and "make reindex" in (failed.error or "")
        assert (await s.execute(select(func.count()).select_from(EmbeddingSpace))).scalar_one() == 1


async def test_wrong_dimensions_refused_before_touching_data(session_factory: SF) -> None:
    run1 = await make_agent(session_factory)
    stats, _ = await crawl(session_factory, run1, Site(), embedder=FakeEmbeddingProvider(256))
    async with session_factory() as s:
        run = await s.get(CrawlRun, run1)
        assert run and run.status == "failed" and "vector(1024)" in (run.error or "")
    assert await count(session_factory, Chunk) == 0 and stats.pages_fetched == 0


class FlakyEmbedder(FakeEmbeddingProvider):
    def __init__(self, fail_on: Callable[[str], bool], *, fatal: bool = False) -> None:
        super().__init__()
        self._fail_on, self._fatal = fail_on, fatal

    async def embed(self, texts: Any, *, input_type: Any) -> list[list[float]]:
        if any(self._fail_on(t) for t in texts):
            raise EmbeddingError("embedding_rate_limited", retryable=True, fatal=self._fatal)
        return await super().embed(texts, input_type=input_type)


async def test_transient_embedding_failure_marks_only_that_page(session_factory: SF) -> None:
    run1 = await make_agent(session_factory)
    stats, _ = await crawl(session_factory, run1, Site(), FlakyEmbedder(lambda t: "contracts" in t))
    async with session_factory() as s:
        pricing = (
            await s.execute(select(Page).where(Page.url == "https://acme.test/pricing"))
        ).scalar_one()
        assert pricing.status == "error" and pricing.error == "embedding_rate_limited"
        assert await count(session_factory, Chunk, Chunk.page_id == pricing.id) == 0  # rolled back
        run = await s.get(CrawlRun, run1)
        assert run and run.status == "succeeded" and run.stats["errors"] == 1
    assert stats.pages_indexed == 2

    healed, _ = await crawl(session_factory, await new_run(session_factory, run1), Site())
    assert healed.pages_indexed == 1  # the failed page is picked up on the next crawl


async def test_fatal_embedding_error_fails_the_run_with_actionable_code(
    session_factory: SF,
) -> None:
    run1 = await make_agent(session_factory)
    await crawl(session_factory, run1, Site(), FlakyEmbedder(lambda t: True, fatal=True))
    async with session_factory() as s:
        run = await s.get(CrawlRun, run1)
        assert run and run.status == "failed" and run.error == "embedding_rate_limited"


async def test_empty_pages_are_recorded_not_indexed(session_factory: SF) -> None:
    site = Site()
    site.pages["/pricing"] = "<div id='root'></div>"
    run1 = await make_agent(session_factory)
    stats, _ = await crawl(session_factory, run1, site)
    async with session_factory() as s:
        page = (
            await s.execute(select(Page).where(Page.url == "https://acme.test/pricing"))
        ).scalar_one()
        assert page.status == "empty" and page.error == "no_extractable_content"
    assert stats.pages_empty == 1


async def test_reindex_switches_model_without_mixing_vectors(session_factory: SF) -> None:
    from wta_indexing.reindex import reindex_all

    site = Site()
    run1 = await make_agent(session_factory)
    await crawl(session_factory, run1, site)
    n_chunks = await count(session_factory, Chunk)

    new_model = FakeEmbeddingProvider()
    new_model.model = "fake-hash-v2"
    embedded = await reindex_all(session_factory, new_model, batch_size=2)
    assert embedded == n_chunks
    assert await count(session_factory, ChunkEmbedding) == 2 * n_chunks  # old vectors kept

    async with session_factory() as s:
        active = (await s.execute(select(EmbeddingSpace).where(EmbeddingSpace.active))).scalar_one()
        assert active.model == "fake-hash-v2"
    assert await reindex_all(session_factory, new_model) == 0  # resumable / idempotent

    # The new model now works for crawling; the old one is refused.
    stats, _ = await crawl(
        session_factory, await new_run(session_factory, run1), site, embedder=new_model
    )
    assert stats.errors == 0
    old = FakeEmbeddingProvider()
    async with session_factory() as s, s.begin():
        with pytest.raises(EmbeddingSpaceMismatch):
            await resolve_space(s, old)
