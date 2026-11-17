"""arq worker entrypoint: ``uv run arq wta_indexing.worker.WorkerSettings``."""

from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings
from wta_common.db import make_engine, make_session_factory
from wta_common.logging import configure_logging
from wta_common.settings import get_settings

from wta_indexing.jobs import crawl_site, schedule_recrawls


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    settings = get_settings()
    engine = make_engine(settings.database_url)
    ctx.update(settings=settings, engine=engine, session_factory=make_session_factory(engine))


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [crawl_site]
    cron_jobs = [cron(schedule_recrawls, minute={0, 30}, run_at_startup=False)]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 2
    job_timeout = 3900
    max_tries = 1
    keep_result = 3600
