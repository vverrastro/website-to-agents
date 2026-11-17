"""Liveness and readiness endpoints (no privileged data, safe to expose)."""

from __future__ import annotations

from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from wta_common.logging import get_logger
from wta_common.models import EMBEDDING_COLUMN_DIMENSIONS

router = APIRouter(tags=["health"])
log = get_logger("wta_api.health")


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Touches no dependency."""
    return {"status": "ok"}


async def _check_db(request: Request) -> dict[str, Any]:
    async with request.app.state.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        ext = (
            await conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
        ).scalar()
        if not ext:
            return {"ok": False, "detail": "pgvector extension missing"}
        current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    cfg = Config(str(request.app.state.alembic_ini))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if current != head:
        return {"ok": False, "detail": f"migrations not at head (db={current}, head={head})"}
    return {"ok": True}


async def _check_redis(request: Request) -> dict[str, Any]:
    await request.app.state.redis.ping()
    return {"ok": True}


def _check_embedding_config(request: Request) -> dict[str, Any]:
    dims = request.app.state.settings.embedding_dimensions
    if dims != EMBEDDING_COLUMN_DIMENSIONS:
        return {
            "ok": False,
            "detail": (
                f"EMBEDDING_DIMENSIONS={dims} != schema vector({EMBEDDING_COLUMN_DIMENSIONS}); "
                "a different dimension needs a migration and re-index"
            ),
        }
    return {"ok": True}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, Any]:
    """Readiness: dependencies reachable and schema/config consistent."""
    checks: dict[str, dict[str, Any]] = {}
    for name, fn in (("database", _check_db), ("redis", _check_redis)):
        try:
            checks[name] = await fn(request)
        except Exception:
            # Details go to logs only; clients get a generic failure.
            log.exception("readiness_check_failed", check=name)
            checks[name] = {"ok": False, "detail": "unreachable"}
    checks["embedding_config"] = _check_embedding_config(request)
    ready = all(c["ok"] for c in checks.values())
    if not ready:
        response.status_code = 503
    return {"status": "ready" if ready else "not_ready", "checks": checks}
