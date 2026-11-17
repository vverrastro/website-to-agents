"""Build a fully wired app for API tests: real Postgres (wta_test) + real local Redis,
fake LLM/embeddings, recorded crawl enqueues."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
import redis.asyncio as aioredis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wta_agent_runtime.llm import FakeLLMProvider
from wta_agent_runtime.rag import AgentRuntime
from wta_agent_runtime.retrieval import Retriever
from wta_api.main import create_app, init_state
from wta_common.models import Agent
from wta_common.settings import Settings
from wta_indexing.embeddings import FakeEmbeddingProvider

SF = async_sessionmaker[AsyncSession]
PRICING_TEXT = "The Pro plan costs 20 euros per month. The Basic plan is free."


def settings(**kw: Any) -> Settings:
    kw.setdefault("local_no_auth", True)
    return Settings(_env_file=None, **kw)


def good_answer(_: Any) -> dict[str, Any]:
    return {
        "answerable": True,
        "answer": "The Pro plan costs 20 euros per month.",
        "used_source_ids": [1],
        "follow_ups": ["Is there a free plan?"],
        "navigation": [{"source_id": 1, "label": "Pricing"}],
    }


class Harness:
    def __init__(
        self, app: Any, llm: FakeLLMProvider, enqueued: list[uuid.UUID], embedder: Any
    ) -> None:
        self.app, self.llm, self.enqueued, self.embedder = app, llm, enqueued, embedder
        self.redis: aioredis.Redis = app.state.redis

    def client(self, ip: str = "127.0.0.1") -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, client=(ip, 5000), raise_app_exceptions=False)
        return httpx.AsyncClient(transport=transport, base_url="http://api.test")

    async def close(self) -> None:
        await self.redis.aclose()


def make_harness(
    sf: SF,
    responder: Callable[[Any], Any] = good_answer,
    **settings_kw: Any,
) -> Harness:
    s = settings(**settings_kw)
    app = create_app(s)
    redis = aioredis.from_url(s.redis_url)  # type: ignore[no-untyped-call]
    init_state(app, s, sf, redis)
    embedder = FakeEmbeddingProvider()
    llm = FakeLLMProvider(responder)
    app.state.runtime = AgentRuntime(llm, Retriever(embedder))
    enqueued: list[uuid.UUID] = []

    async def record(run_id: uuid.UUID) -> None:
        enqueued.append(run_id)

    app.state.enqueue_crawl = record
    return Harness(app, llm, enqueued, embedder)


async def publish(sf: SF, agent_id: uuid.UUID, origins: list[str] | None = None) -> str:
    async with sf() as s, s.begin():
        await s.execute(
            update(Agent)
            .where(Agent.id == agent_id)
            .values(published=True, allowed_origins=origins or ["https://shop.example"])
        )
        agent = await s.get(Agent, agent_id)
        assert agent
        return agent.public_id
