"""Privileged management API for the local Agent Studio.

Local development only: mounted solely when LOCAL_NO_AUTH=true outside production, and each
request must come from a loopback client. There is NO authentication here. Real dashboard
authN/authZ is required before any internet-facing deployment (ADR 0004).
"""

from __future__ import annotations

import ipaddress
import secrets
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from wta_common.crawl_config import AgentCrawlConfig
from wta_common.models import (
    Agent,
    Chunk,
    CrawlRun,
    EmbeddingSpace,
    Page,
    SkippedUrl,
    Source,
    Workspace,
)
from wta_common.protocol import ChatRequest, ChatResponse
from wta_common.settings import Settings
from wta_crawler import normalize_url
from wta_crawler.ssrf import SSRFError, validate_url
from wta_indexing.jobs import create_crawl_run

from wta_api.chat_service import handle_chat
from wta_api.errors import ApiError
from wta_api.public import normalize_origin


async def require_local_admin(request: Request) -> None:
    settings: Settings = request.app.state.settings
    host = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    # Forwarded headers are deliberately ignored: only the direct socket peer counts.
    if settings.is_production or not settings.local_no_auth or not loopback:
        raise ApiError(404, "not_found", "Not found.")


router = APIRouter(prefix="/admin/v1", tags=["admin"], dependencies=[Depends(require_local_admin)])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _origins(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        normalized = normalize_origin(value)
        if normalized is None:
            raise ValueError(f"not a valid origin (expected https://host[:port]): {value!r}")
        if normalized not in out:
            out.append(normalized)
    return out


class AgentCreate(_Strict):
    name: str = Field(min_length=1, max_length=100)
    instructions: str | None = Field(default=None, max_length=4000)
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    crawl_config: AgentCrawlConfig = Field(default_factory=AgentCrawlConfig)

    _o = field_validator("allowed_origins")(_origins)


class AgentUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    instructions: str | None = Field(default=None, max_length=4000)
    allowed_origins: list[str] | None = Field(default=None, max_length=20)
    crawl_config: AgentCrawlConfig | None = None

    @field_validator("allowed_origins")
    @classmethod
    def _o(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _origins(value)


class SourceCreate(_Strict):
    url: str = Field(min_length=1, max_length=2000)


class CrawlStart(_Strict):
    source_id: uuid.UUID | None = None


class SourceOut(BaseModel):
    id: uuid.UUID
    url: str


class AgentOut(BaseModel):
    id: uuid.UUID
    public_id: str
    name: str
    published: bool
    instructions: str | None
    allowed_origins: list[str]
    crawl_config: dict[str, Any]
    sources: list[SourceOut]
    page_count: int = 0
    created_at: datetime


class RunOut(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    stats: dict[str, Any]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class PageOut(BaseModel):
    id: uuid.UUID
    url: str
    canonical_url: str | None
    status: str
    render_mode: str
    error: str | None
    chunk_count: int
    last_seen_in_sitemap: datetime | None
    last_crawled_at: datetime | None
    origin_last_modified: datetime | None
    indexed_at: datetime | None


class SkippedOut(BaseModel):
    url: str
    reason: str
    crawl_run_id: uuid.UUID


async def _session(request: Request):  # type: ignore[no-untyped-def]
    async with request.app.state.session_factory() as session:
        yield session


async def _workspace(session: AsyncSession) -> Workspace:
    ws = (await session.execute(select(Workspace).order_by(Workspace.created_at))).scalars().first()
    if ws is None:
        ws = Workspace(name="Local workspace")
        session.add(ws)
        await session.flush()
    return ws


async def _agent(session: AsyncSession, agent_id: uuid.UUID) -> Agent:
    ws = await _workspace(session)
    agent = (
        await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise ApiError(404, "agent_not_found", "Agent not found.")
    return agent


async def _agent_out(session: AsyncSession, agent: Agent) -> AgentOut:
    sources = (
        (
            await session.execute(
                select(Source).where(Source.agent_id == agent.id).order_by(Source.created_at)
            )
        )
        .scalars()
        .all()
    )
    pages = (
        await session.execute(
            select(func.count())
            .select_from(Page)
            .where(Page.agent_id == agent.id, Page.status == "indexed")
        )
    ).scalar_one()
    return AgentOut(
        id=agent.id, public_id=agent.public_id, name=agent.name, published=agent.published,
        instructions=agent.instructions, allowed_origins=list(agent.allowed_origins),
        crawl_config=dict(agent.crawl_config or {}),
        sources=[SourceOut(id=s.id, url=s.url) for s in sources],
        page_count=pages, created_at=agent.created_at,
    )  # fmt: skip


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    s: Settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        space = (
            await session.execute(select(EmbeddingSpace).where(EmbeddingSpace.active.is_(True)))
        ).scalar_one_or_none()
    return {
        "app_env": s.app_env,
        "llm": {
            "provider": s.llm_provider,
            "model": s.llm_model,
            "configured": s.llm_provider != "anthropic"
            or bool(s.anthropic_api_key.get_secret_value()),
        },
        "embedding": {
            "provider": s.embedding_provider,
            "model": s.embedding_model,
            "configured": s.embedding_provider != "voyage"
            or bool(s.voyage_api_key.get_secret_value()),
            "active_index": f"{space.provider}/{space.model}" if space else None,
        },
        "rendering_enabled": s.crawler_rendering_enabled,
    }


@router.get("/agents", response_model=list[AgentOut])
async def list_agents(session: AsyncSession = Depends(_session)) -> list[AgentOut]:
    ws = await _workspace(session)
    agents = (
        (
            await session.execute(
                select(Agent).where(Agent.workspace_id == ws.id).order_by(Agent.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    await session.commit()
    return [await _agent_out(session, a) for a in agents]


@router.post("/agents", response_model=AgentOut, status_code=201)
async def create_agent(body: AgentCreate, session: AsyncSession = Depends(_session)) -> AgentOut:
    ws = await _workspace(session)
    agent = Agent(
        workspace_id=ws.id, name=body.name, instructions=body.instructions,
        public_id=f"pub_{secrets.token_urlsafe(9)}", published=False,
        allowed_origins=body.allowed_origins, crawl_config=body.crawl_config.model_dump(),
    )  # fmt: skip
    session.add(agent)
    await session.commit()
    return await _agent_out(session, agent)


@router.get("/agents/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: uuid.UUID, session: AsyncSession = Depends(_session)) -> AgentOut:
    return await _agent_out(session, await _agent(session, agent_id))


@router.patch("/agents/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: uuid.UUID, body: AgentUpdate, session: AsyncSession = Depends(_session)
) -> AgentOut:
    agent = await _agent(session, agent_id)
    data = body.model_dump(exclude_unset=True)
    if "crawl_config" in data and body.crawl_config is not None:
        data["crawl_config"] = body.crawl_config.model_dump()
    for key, value in data.items():
        if key in ("name",) and value is None:
            continue
        setattr(agent, key, value)
    await session.commit()
    return await _agent_out(session, agent)


@router.post("/agents/{agent_id}/publish", response_model=AgentOut)
@router.post("/agents/{agent_id}/unpublish", response_model=AgentOut)
async def set_published(
    request: Request, agent_id: uuid.UUID, session: AsyncSession = Depends(_session)
) -> AgentOut:
    agent = await _agent(session, agent_id)
    agent.published = request.url.path.endswith("/publish")
    await session.commit()
    return await _agent_out(session, agent)


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: uuid.UUID, session: AsyncSession = Depends(_session)) -> None:
    await session.delete(await _agent(session, agent_id))
    await session.commit()


@router.post("/agents/{agent_id}/sources", response_model=SourceOut, status_code=201)
async def add_source(
    agent_id: uuid.UUID, body: SourceCreate, session: AsyncSession = Depends(_session)
) -> SourceOut:
    agent = await _agent(session, agent_id)
    url = normalize_url(body.url)
    if url is None:
        raise ApiError(400, "invalid_url", "Enter a valid http(s) URL.")
    try:
        await validate_url(url)  # early feedback; the crawler re-validates on every connection
    except SSRFError as exc:
        raise ApiError(
            400, "url_not_allowed", f"This address cannot be crawled ({exc.reason})."
        ) from None
    existing = (
        await session.execute(select(Source).where(Source.agent_id == agent.id, Source.url == url))
    ).scalar_one_or_none()
    if existing:
        raise ApiError(409, "source_exists", "This URL is already a source.")
    source = Source(workspace_id=agent.workspace_id, agent_id=agent.id, url=url)
    session.add(source)
    await session.commit()
    return SourceOut(id=source.id, url=source.url)


@router.delete("/agents/{agent_id}/sources/{source_id}", status_code=204)
async def delete_source(
    agent_id: uuid.UUID, source_id: uuid.UUID, session: AsyncSession = Depends(_session)
) -> None:
    agent = await _agent(session, agent_id)
    source = (
        await session.execute(
            select(Source).where(Source.id == source_id, Source.agent_id == agent.id)
        )
    ).scalar_one_or_none()
    if source is None:
        raise ApiError(404, "source_not_found", "Source not found.")
    await session.delete(source)
    await session.commit()


@router.post("/agents/{agent_id}/crawl", response_model=list[RunOut], status_code=202)
async def start_crawl(
    request: Request,
    agent_id: uuid.UUID,
    body: CrawlStart | None = None,
    session: AsyncSession = Depends(_session),
) -> list[RunOut]:
    agent = await _agent(session, agent_id)
    sources = (
        (await session.execute(select(Source).where(Source.agent_id == agent.id))).scalars().all()
    )
    if body and body.source_id:
        sources = [s for s in sources if s.id == body.source_id]
    if not sources:
        raise ApiError(400, "no_sources", "Add a source URL first.")
    active = (
        await session.execute(
            select(func.count())
            .select_from(CrawlRun)
            .where(CrawlRun.agent_id == agent.id, CrawlRun.status.in_(("queued", "running")))
        )
    ).scalar_one()
    if active:
        raise ApiError(409, "crawl_in_progress", "A crawl is already queued or running.")
    runs = [await create_crawl_run(session, agent, s) for s in sources]
    await session.commit()
    for run in runs:
        await request.app.state.enqueue_crawl(run.id)
    return [_run_out(r) for r in runs]


def _run_out(r: CrawlRun) -> RunOut:
    return RunOut(
        id=r.id, source_id=r.source_id, status=r.status, stats=dict(r.stats or {}), error=r.error,
        created_at=r.created_at, started_at=r.started_at, finished_at=r.finished_at,
    )  # fmt: skip


@router.get("/agents/{agent_id}/crawl-runs", response_model=list[RunOut])
async def list_runs(agent_id: uuid.UUID, session: AsyncSession = Depends(_session)) -> list[RunOut]:
    agent = await _agent(session, agent_id)
    runs = (
        (
            await session.execute(
                select(CrawlRun)
                .where(CrawlRun.agent_id == agent.id)
                .order_by(CrawlRun.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return [_run_out(r) for r in runs]


@router.get("/agents/{agent_id}/pages", response_model=list[PageOut])
async def list_pages(
    agent_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(_session),
) -> list[PageOut]:
    agent = await _agent(session, agent_id)
    rows = (
        await session.execute(
            select(Page, func.count(Chunk.id))
            .outerjoin(Chunk, Chunk.page_id == Page.id)
            .where(Page.agent_id == agent.id)
            .group_by(Page.id)
            .order_by(Page.url)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [
        PageOut(
            id=p.id,
            url=p.url,
            canonical_url=p.canonical_url,
            status=p.status,
            render_mode=p.render_mode,
            error=p.error,
            chunk_count=n,
            last_seen_in_sitemap=p.last_seen_in_sitemap,
            last_crawled_at=p.last_crawled_at,
            origin_last_modified=p.origin_last_modified,
            indexed_at=p.indexed_at,
        )  # fmt: skip
        for p, n in rows
    ]


@router.get("/agents/{agent_id}/skipped", response_model=list[SkippedOut])
async def list_skipped(
    agent_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(_session),
) -> list[SkippedOut]:
    agent = await _agent(session, agent_id)
    query = select(SkippedUrl).where(SkippedUrl.agent_id == agent.id)
    if run_id:
        query = query.where(SkippedUrl.crawl_run_id == run_id)
    rows = (
        await session.execute(query.order_by(SkippedUrl.created_at.desc()).limit(limit))
    ).scalars()
    return [SkippedOut(url=r.url, reason=r.reason, crawl_run_id=r.crawl_run_id) for r in rows]


@router.post("/agents/{agent_id}/preview-chat", response_model=ChatResponse)
async def preview_chat(
    request: Request,
    agent_id: uuid.UUID,
    body: ChatRequest,
    session: AsyncSession = Depends(_session),
) -> ChatResponse:
    agent = await _agent(session, agent_id)
    await session.commit()
    state = request.app.state
    return await handle_chat(
        settings=state.settings,
        session_factory=state.session_factory,
        get_runtime=state.get_runtime,
        agent=agent,
        request=body,
        detailed_errors=True,  # local admin only: show actionable configuration errors
    )
