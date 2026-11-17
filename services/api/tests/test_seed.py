import pytest
from sqlalchemy import func, select
from wta_api.seed import DEMO_URL, seed_demo
from wta_common.models import Agent, Source

pytestmark = pytest.mark.integration


async def test_seed_is_idempotent(session_factory) -> None:  # type: ignore[no-untyped-def]
    first = await seed_demo(session_factory)
    second = await seed_demo(session_factory)
    assert first["created"] is True and second["created"] is False
    assert first["public_id"] == second["public_id"] and first["public_id"].startswith("pub_")
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(Agent))).scalar_one() == 1
        sources = (await s.execute(select(Source.url))).scalars().all()
        assert sources == [DEMO_URL]
