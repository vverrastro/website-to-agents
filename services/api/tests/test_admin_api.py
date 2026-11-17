import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from api_helpers import SF, Harness, make_harness
from sqlalchemy import select
from wta_common.models import CrawlRun, SkippedUrl
from wta_indexing.testing import PRICING, seed_agent

pytestmark = pytest.mark.integration
A = "/admin/v1"


@pytest.fixture
async def h(session_factory: SF) -> AsyncIterator[Harness]:
    harness = make_harness(session_factory)
    yield harness
    await harness.close()


async def create(c: Any, **over: Any) -> dict[str, Any]:
    r = await c.post(f"{A}/agents", json={"name": "Acme bot", **over})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_agent_lifecycle(h: Harness) -> None:
    async with h.client() as c:
        agent = await create(
            c, allowed_origins=["HTTPS://Shop.Example/", "https://shop.example:443"],
            crawl_config={"max_pages": 20},
        )  # fmt: skip
        assert agent["public_id"].startswith("pub_") and agent["published"] is False
        assert agent["allowed_origins"] == ["https://shop.example"]  # normalised + deduped
        assert agent["crawl_config"]["max_pages"] == 20 and agent["crawl_config"]["max_depth"] == 3

        aid = agent["id"]
        assert [a["id"] for a in (await c.get(f"{A}/agents")).json()] == [aid]
        patched = (
            await c.patch(
                f"{A}/agents/{aid}", json={"name": "Renamed", "instructions": "Be brief."}
            )
        ).json()
        assert patched["name"] == "Renamed" and patched["instructions"] == "Be brief."
        assert (await c.post(f"{A}/agents/{aid}/publish")).json()["published"] is True
        assert (await c.post(f"{A}/agents/{aid}/unpublish")).json()["published"] is False
        assert (await c.delete(f"{A}/agents/{aid}")).status_code == 204
        assert (await c.get(f"{A}/agents/{aid}")).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"name": "x", "allowed_origins": ["https://shop.example/path"]},
        {"name": "x", "allowed_origins": ["javascript:alert(1)"]},
        {"name": "x", "allowed_origins": ["*"]},
        {"name": "x", "crawl_config": {"max_pages": 100000}},
        {"name": "x", "crawl_config": {"respect_robots": False}},  # not configurable
        {"name": "x", "published": True},  # cannot be set at creation
        {"name": "x", "workspace_id": str(uuid.uuid4())},
    ],
)
async def test_agent_validation_is_strict(h: Harness, payload: dict[str, Any]) -> None:
    async with h.client() as c:
        r = await c.post(f"{A}/agents", json=payload)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"


async def test_sources_are_validated_against_ssrf(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def public_dns(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    import wta_api.admin as admin
    import wta_crawler.ssrf as ssrf

    monkeypatch.setattr(admin, "validate_url", lambda url: ssrf.validate_url(url, public_dns))
    async with h.client() as c:
        aid = (await create(c))["id"]
        for bad in (
            "http://127.0.0.1/",
            "http://169.254.169.254/latest",
            "http://localhost:8000/",
            "file:///etc/passwd",
            "not a url",
            "ftp://x.test",
        ):
            r = await c.post(f"{A}/agents/{aid}/sources", json={"url": bad})
            assert r.status_code == 400, bad
            assert r.json()["error"]["code"] in ("url_not_allowed", "invalid_url"), bad
        ok = await c.post(
            f"{A}/agents/{aid}/sources", json={"url": "HTTPS://Docs.Example.com/?utm_source=x#top"}
        )
        assert ok.status_code == 201 and ok.json()["url"] == "https://docs.example.com/"
        assert (
            await c.post(f"{A}/agents/{aid}/sources", json={"url": "https://docs.example.com/"})
        ).status_code == 409
        sid = ok.json()["id"]
        assert (await c.get(f"{A}/agents/{aid}")).json()["sources"][0]["id"] == sid
        assert (await c.delete(f"{A}/agents/{aid}/sources/{sid}")).status_code == 204
        assert (await c.delete(f"{A}/agents/{aid}/sources/{sid}")).status_code == 404


async def test_crawl_start_enqueues_and_prevents_overlap(
    h: Harness, monkeypatch: pytest.MonkeyPatch, session_factory: SF
) -> None:
    async def public_dns(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    import wta_api.admin as admin
    import wta_crawler.ssrf as ssrf

    monkeypatch.setattr(admin, "validate_url", lambda url: ssrf.validate_url(url, public_dns))
    async with h.client() as c:
        aid = (await create(c))["id"]
        assert (await c.post(f"{A}/agents/{aid}/crawl")).json()["error"]["code"] == "no_sources"
        await c.post(f"{A}/agents/{aid}/sources", json={"url": "https://docs.example.com/"})

        r = await c.post(f"{A}/agents/{aid}/crawl")
        assert r.status_code == 202 and r.json()[0]["status"] == "queued"
        assert h.enqueued == [uuid.UUID(r.json()[0]["id"])]
        again = await c.post(f"{A}/agents/{aid}/crawl")
        assert again.status_code == 409 and again.json()["error"]["code"] == "crawl_in_progress"

        runs = (await c.get(f"{A}/agents/{aid}/crawl-runs")).json()
        assert [x["id"] for x in runs] == [r.json()[0]["id"]]
    async with session_factory() as s:
        assert len((await s.execute(select(CrawlRun))).scalars().all()) == 1


async def test_pages_runs_and_skipped_listing_is_scoped_per_agent(
    h: Harness, session_factory: SF
) -> None:
    mine = await seed_agent(session_factory, [PRICING], name="mine")
    other = await seed_agent(session_factory, [PRICING], name="other")
    async with session_factory() as s, s.begin():
        from wta_common.models import CrawlRun as Run
        from wta_common.models import Source

        for seeded in (mine, other):
            source = (
                await s.execute(select(Source).where(Source.agent_id == seeded.agent_id))
            ).scalar_one()
            run = Run(
                workspace_id=seeded.workspace_id,
                agent_id=seeded.agent_id,
                source_id=source.id,
                status="succeeded",
            )
            s.add(run)
            await s.flush()
            s.add(
                SkippedUrl(
                    workspace_id=seeded.workspace_id,
                    agent_id=seeded.agent_id,
                    crawl_run_id=run.id,
                    url=f"https://acme.test/skip-{seeded.agent_id}",
                    reason="robots_disallowed",
                )
            )
    async with h.client() as c:
        pages = (await c.get(f"{A}/agents/{mine.agent_id}/pages")).json()
        assert [p["url"] for p in pages] == ["https://acme.test/pricing"]
        assert pages[0]["chunk_count"] == 2 and pages[0]["status"] == "indexed"
        skipped = (await c.get(f"{A}/agents/{mine.agent_id}/skipped")).json()
        assert [x["reason"] for x in skipped] == ["robots_disallowed"]
        assert str(mine.agent_id) in skipped[0]["url"] and str(other.agent_id) not in json.dumps(
            skipped
        )
        assert (await c.get(f"{A}/agents/{mine.agent_id}/pages?limit=0")).status_code == 400
        assert (await c.get(f"{A}/agents/{mine.agent_id}/pages?limit=9999")).status_code == 400


async def test_preview_chat_works_for_unpublished_agents(h: Harness, session_factory: SF) -> None:
    seeded = await seed_agent(session_factory, [PRICING])
    async with h.client() as c:
        r = await c.post(
            f"{A}/agents/{seeded.agent_id}/preview-chat", json={"message": "How much is Pro?"}
        )
    assert r.status_code == 200 and r.json()["sources"][0]["url"] == "https://acme.test/pricing"


async def test_preview_chat_shows_actionable_config_errors(h: Harness, session_factory: SF) -> None:
    seeded = await seed_agent(session_factory, [PRICING])
    h.app.state.runtime = None
    async with h.client() as c:
        r = await c.post(f"{A}/agents/{seeded.agent_id}/preview-chat", json={"message": "hi"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["error"]["message"]


async def test_status_reports_configuration_without_secrets(session_factory: SF) -> None:
    h = make_harness(session_factory, anthropic_api_key="sk-ant-SECRET", voyage_api_key="")
    try:
        async with h.client() as c:
            body = (await c.get(f"{A}/status")).json()
        assert body["llm"] == {
            "provider": "anthropic",
            "model": "claude-opus-5-5",
            "configured": True,
        }
        assert body["embedding"]["configured"] is False
        assert "SECRET" not in json.dumps(body)
    finally:
        await h.close()


@pytest.mark.parametrize("client_ip", ["203.0.113.7", "10.0.0.5", "172.17.0.2"])
async def test_admin_requires_a_loopback_peer(h: Harness, client_ip: str) -> None:
    async with h.client(client_ip) as c:
        for method, path in (("GET", "/agents"), ("POST", "/agents"), ("GET", "/status")):
            r = await c.request(
                method,
                f"{A}{path}",
                json={"name": "x"} if method == "POST" else None,
                headers={"x-forwarded-for": "127.0.0.1"},
            )
            assert r.status_code == 404, (method, path)
    async with h.client("::1") as c:
        assert (await c.get(f"{A}/status")).status_code == 200  # IPv6 loopback is local too
