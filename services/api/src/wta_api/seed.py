"""Create a demo agent for local development: ``make seed``.

Idempotent. It prepares an unpublished-by-default agent pointing at a harmless public page and
prints the next steps. It never contacts the network itself; crawling is queued for the worker.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wta_common.crawl_config import AgentCrawlConfig
from wta_common.db import make_engine, make_session_factory
from wta_common.models import Agent, Source, Workspace
from wta_common.settings import get_settings

DEMO_NAME = "Demo agent (example.com)"
DEMO_URL = "https://example.com/"


async def seed_demo(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    async with session_factory() as session, session.begin():
        ws = (
            (await session.execute(select(Workspace).order_by(Workspace.created_at)))
            .scalars()
            .first()
        )
        if ws is None:
            ws = Workspace(name="Local workspace")
            session.add(ws)
            await session.flush()
        agent = (
            await session.execute(
                select(Agent).where(Agent.workspace_id == ws.id, Agent.name == DEMO_NAME)
            )
        ).scalar_one_or_none()
        created = agent is None
        if agent is None:
            agent = Agent(
                workspace_id=ws.id,
                name=DEMO_NAME,
                public_id=f"pub_{secrets.token_urlsafe(9)}",
                published=True,  # demo only; http://localhost origins are accepted in development
                allowed_origins=[],
                crawl_config=AgentCrawlConfig(max_pages=10, max_depth=1).model_dump(),
            )
            session.add(agent)
            await session.flush()
        source = (
            await session.execute(
                select(Source).where(Source.agent_id == agent.id, Source.url == DEMO_URL)
            )
        ).scalar_one_or_none()
        if source is None:
            session.add(Source(workspace_id=ws.id, agent_id=agent.id, url=DEMO_URL))
        return {"agent_id": str(agent.id), "public_id": agent.public_id, "created": created}


async def _main() -> int:
    settings = get_settings()
    if settings.is_production:
        print("refusing to seed demo data with APP_ENV=production", file=sys.stderr)  # noqa: T201
        return 2
    engine = make_engine(settings.database_url)
    try:
        info = await seed_demo(make_session_factory(engine))
    finally:
        await engine.dispose()
    verb = "Created" if info["created"] else "Found"
    print(f"{verb} demo agent {info['agent_id']} (public id {info['public_id']}).")  # noqa: T201
    print("Next: `make dashboard`, open the agent, click “Start crawl” (needs `make worker`),")  # noqa: T201
    print(f"then `make widget` and open http://127.0.0.1:5174/?agent={info['public_id']}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
