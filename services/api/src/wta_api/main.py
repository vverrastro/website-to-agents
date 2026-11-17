"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wta_agent_runtime.rag import AgentRuntime
from wta_common.db import make_engine, make_session_factory
from wta_common.logging import configure_logging, get_logger
from wta_common.settings import Settings, get_settings

from wta_api.admin import router as admin_router
from wta_api.chat_service import build_runtime
from wta_api.errors import register_error_handlers
from wta_api.health import router as health_router
from wta_api.middleware import install_middleware
from wta_api.public import router as public_router
from wta_api.ratelimit import RateLimiter

log = get_logger("wta_api")

ALEMBIC_INI = Path(__file__).resolve().parents[4] / "infrastructure" / "database" / "alembic.ini"


def init_state(
    app: FastAPI,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    redis: aioredis.Redis,
) -> None:
    """Wire runtime dependencies onto ``app.state`` (used by the lifespan and by tests)."""
    app.state.session_factory = session_factory
    app.state.redis = redis
    app.state.rate_limiter = RateLimiter(
        redis, settings.public_rate_limit_per_minute, settings.public_daily_quota_per_agent
    )
    app.state.public_semaphore = asyncio.Semaphore(settings.public_max_concurrent_requests)
    app.state.runtime = None
    app.state.arq_pool = None

    async def get_runtime() -> AgentRuntime:
        # Built lazily so the API (crawling, dashboard) starts without provider keys; a missing
        # key then surfaces as an actionable error on the first chat instead of a crash.
        if app.state.runtime is None:
            app.state.runtime = build_runtime(settings)
        runtime: AgentRuntime = app.state.runtime
        return runtime

    async def enqueue_crawl(run_id: uuid.UUID) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings
        from wta_indexing.jobs import enqueue_crawl as enqueue

        if app.state.arq_pool is None:
            app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await enqueue(app.state.arq_pool, run_id)

    app.state.get_runtime = get_runtime
    app.state.enqueue_crawl = enqueue_crawl


def create_app(settings: Settings | None = None) -> FastAPI:
    # Settings validation fails closed (e.g. LOCAL_NO_AUTH in production) before serving.
    settings = settings or get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        redis = aioredis.from_url(settings.redis_url)  # type: ignore[no-untyped-call]
        init_state(app, settings, make_session_factory(engine), redis)
        log.info("startup", env=settings.app_env, no_auth=settings.local_no_auth)
        try:
            yield
        finally:
            if app.state.arq_pool is not None:
                await app.state.arq_pool.aclose()
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title="Website-to-Agents API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/openapi.json" if not settings.is_production else None,
    )
    app.state.settings = settings
    app.state.alembic_ini = ALEMBIC_INI
    install_middleware(app, hsts=settings.is_production)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(public_router)
    if settings.local_no_auth and not settings.is_production:
        app.include_router(admin_router)  # absent (404) unless explicitly in local no-auth mode
    return app
