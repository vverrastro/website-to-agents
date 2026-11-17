import httpx
import pytest
import redis.asyncio as aioredis
from fastapi import FastAPI
from wta_api.main import create_app
from wta_common.db import make_engine
from wta_common.settings import ConfigError, Settings


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


async def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_healthz_needs_no_dependencies() -> None:
    app = create_app(settings())
    async with await client_for(app) as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_production_with_no_auth_refuses_to_start() -> None:
    with pytest.raises(ConfigError):
        create_app(settings(app_env="production", local_no_auth=True))


def test_docs_disabled_in_production() -> None:
    app = create_app(settings(app_env="production"))
    assert app.docs_url is None and app.openapi_url is None


async def test_readyz_reports_unreachable_without_leaking_details() -> None:
    s = settings(
        database_url="postgresql+asyncpg://u:secretpw@127.0.0.1:1/none",
        redis_url="redis://127.0.0.1:1/0",
    )
    app = create_app(s)
    app.state.engine = make_engine(s.database_url)
    app.state.redis = aioredis.from_url(s.redis_url)  # type: ignore[no-untyped-call]
    async with await client_for(app) as c:
        r = await c.get("/readyz")
    assert r.status_code == 503
    body = r.text
    assert "secretpw" not in body
    assert r.json()["checks"]["database"] == {"ok": False, "detail": "unreachable"}
    await app.state.engine.dispose()
    await app.state.redis.aclose()


@pytest.mark.integration
async def test_readyz_ok_against_local_stack() -> None:
    s = Settings()  # reads .env (local docker compose)
    app = create_app(s)
    app.state.engine = make_engine(s.database_url)
    app.state.redis = aioredis.from_url(s.redis_url)  # type: ignore[no-untyped-call]
    try:
        async with await client_for(app) as c:
            r = await c.get("/readyz")
    finally:
        await app.state.engine.dispose()
        await app.state.redis.aclose()
    if r.status_code == 503 and r.json()["checks"]["database"]["detail"] == "unreachable":
        pytest.skip("local Postgres not running (make up)")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready"


async def test_hsts_only_in_production() -> None:
    for env, expected in (("production", True), ("development", False)):
        app = create_app(settings(app_env=env))
        async with await client_for(app) as c:
            r = await c.get("/healthz")
        assert ("strict-transport-security" in r.headers) is expected, env
        if expected:
            assert "max-age=" in r.headers["strict-transport-security"]
        assert r.headers["x-content-type-options"] == "nosniff"
