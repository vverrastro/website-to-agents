"""Pytest fixtures for DB-backed tests (loaded with ``-p wta_common.testing``).

Uses a separate ``wta_test`` database on the local compose Postgres; tables are truncated before
each test. Tests are skipped with a clear message when Postgres is not running.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from wta_common.models import Base
from wta_common.settings import Settings

ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI = ROOT / "infrastructure" / "database" / "alembic.ini"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = make_url(Settings().database_url).set(database="wta_test")
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def migrated_db(test_database_url: str) -> str:
    async def ping() -> None:
        engine = create_async_engine(test_database_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(ping())
    except Exception:
        pytest.skip("Postgres test database unavailable: run `make up` first")
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": test_database_url},
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"test database migration failed:\n{proc.stderr[-2000:]}")
    return test_database_url


@pytest.fixture
async def session_factory(migrated_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
