"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI
from wta_common.db import make_engine, make_session_factory
from wta_common.logging import configure_logging, get_logger
from wta_common.settings import Settings, get_settings

from wta_api.health import router as health_router

log = get_logger("wta_api")

ALEMBIC_INI = Path(__file__).resolve().parents[4] / "infrastructure" / "database" / "alembic.ini"


def create_app(settings: Settings | None = None) -> FastAPI:
    # Settings validation fails closed (e.g. LOCAL_NO_AUTH in production) before serving.
    settings = settings or get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        app.state.redis = aioredis.from_url(settings.redis_url)  # type: ignore[no-untyped-call]
        log.info("startup", env=settings.app_env, no_auth=settings.local_no_auth)
        try:
            yield
        finally:
            await app.state.redis.aclose()
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
    app.include_router(health_router)
    return app
