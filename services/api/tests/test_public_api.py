import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from api_helpers import SF, Harness, make_harness, publish
from sqlalchemy import func, select
from wta_agent_runtime.llm import LLMError
from wta_common.models import ChatMessage, Conversation
from wta_common.protocol import ChatResponse
from wta_common.settings import ConfigError
from wta_indexing.pipeline import EmbeddingSpaceMismatch
from wta_indexing.testing import PRICING, seed_agent

pytestmark = pytest.mark.integration
ORIGIN = "https://shop.example"


@pytest.fixture
async def env(session_factory: SF) -> AsyncIterator[tuple[Harness, str, Any]]:
    h = make_harness(session_factory)
    seeded = await seed_agent(session_factory, [PRICING])
    public_id = await publish(session_factory, seeded.agent_id)
    yield h, public_id, seeded
    await h.close()


def chat(public_id: str) -> str:
    return f"/public/v1/agents/{public_id}/chat"


async def post(
    h: Harness, public_id: str, body: Any, origin: str | None = ORIGIN, **kw: Any
) -> Any:
    headers = {"content-type": "application/json", **kw.pop("headers", {})}
    if origin:
        headers["origin"] = origin
    async with h.client(kw.pop("ip", "127.0.0.1")) as c:
        content = body if isinstance(body, (bytes, str)) else json.dumps(body)
        return await c.post(chat(public_id), content=content, headers=headers)


async def test_chat_happy_path_contract_headers_and_persistence(
    env: Any, session_factory: SF
) -> None:
    h, pid, _ = env
    r = await post(h, pid, {"message": "How much is the Pro plan?"})
    assert r.status_code == 200, r.text
    body = ChatResponse.model_validate(r.json())  # response honours the public contract
    assert body.sources[0].url == "https://acme.test/pricing" and body.ui is not None
    assert (
        r.headers["access-control-allow-origin"] == ORIGIN and "origin" in r.headers["vary"].lower()
    )
    assert "access-control-allow-credentials" not in r.headers
    assert (
        r.headers["x-content-type-options"] == "nosniff"
        and r.headers["cache-control"] == "no-store"
    )
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert r.headers["x-request-id"]

    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(ChatMessage))).scalar_one() == 2
        roles = (
            (await s.execute(select(ChatMessage.role).order_by(ChatMessage.created_at)))
            .scalars()
            .all()
        )
        assert sorted(roles) == ["assistant", "user"]

    follow = await post(
        h, pid, {"message": "and the basic one?", "conversation_id": str(body.conversation_id)}
    )
    assert follow.json()["conversation_id"] == str(body.conversation_id)
    assert [m.role for m in h.llm.requests[-1].messages] == ["user", "assistant", "user"]


async def test_other_agents_conversation_id_is_not_adopted(env: Any, session_factory: SF) -> None:
    h, pid, _ = env
    mine = (await post(h, pid, {"message": "hello pricing"})).json()["conversation_id"]
    other = await seed_agent(session_factory, [PRICING], name="other")
    other_pid = await publish(session_factory, other.agent_id)
    r = await post(h, other_pid, {"message": "hello pricing", "conversation_id": mine})
    assert r.status_code == 200 and r.json()["conversation_id"] != mine
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(Conversation))).scalar_one() == 2


async def test_unknown_and_unpublished_agents_look_identical(env: Any, session_factory: SF) -> None:
    h, _, _ = env
    draft = await seed_agent(session_factory, [PRICING], name="draft")  # never published
    async with session_factory() as s:
        from wta_common.models import Agent

        draft_pid = (await s.get(Agent, draft.agent_id)).public_id  # type: ignore[union-attr]
    a = await post(h, "pub_doesnotexist", {"message": "hi"})
    b = await post(h, draft_pid, {"message": "hi"})
    assert a.status_code == b.status_code == 404
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "agent_not_found"


async def test_origin_allowlist_and_normalisation(env: Any, session_factory: SF) -> None:
    h, pid, seeded = env
    await publish(
        session_factory, seeded.agent_id, ["https://shop.example", "https://B.example:8443"]
    )
    bad = await post(h, pid, {"message": "hi"}, origin="https://evil.example")
    assert bad.status_code == 403 and bad.json()["error"]["code"] == "origin_not_allowed"
    assert "access-control-allow-origin" not in bad.headers
    assert (
        await post(h, pid, {"message": "hi"}, origin="https://b.example:8443")
    ).status_code == 200
    for weird in (
        "null",
        "https://shop.example.evil.test",
        "https://shop.example/path",
        "http://shop.example",
    ):
        assert (await post(h, pid, {"message": "hi"}, origin=weird)).status_code == 403, weird


async def test_localhost_exception_exists_only_in_development(session_factory: SF) -> None:
    seeded = await seed_agent(session_factory, [PRICING])
    pid = await publish(session_factory, seeded.agent_id)
    for env_name, expected in (("development", 200), ("test", 403), ("production", 403)):
        kw = {"app_env": env_name, "local_no_auth": env_name != "production"}
        h = make_harness(session_factory, **kw)
        try:
            r = await post(h, pid, {"message": "hi"}, origin="http://localhost:5173")
            assert r.status_code == expected, env_name
        finally:
            await h.close()


async def test_preflight(env: Any) -> None:
    h, pid, _ = env
    async with h.client() as c:
        ok = await c.options(
            chat(pid), headers={"origin": ORIGIN, "access-control-request-method": "POST"}
        )
        bad = await c.options(chat(pid), headers={"origin": "https://evil.example"})
    assert ok.status_code == 204 and ok.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in ok.headers["access-control-allow-methods"]
    assert ok.headers["access-control-allow-headers"] == "Content-Type"
    assert bad.status_code == 403 and "access-control-allow-origin" not in bad.headers


async def test_no_origin_header_is_allowed_but_still_metered(session_factory: SF) -> None:
    h = make_harness(session_factory, public_rate_limit_per_minute=2)
    seeded = await seed_agent(session_factory, [PRICING])
    pid = await publish(session_factory, seeded.agent_id)
    try:
        codes = [(await post(h, pid, {"message": "hi"}, origin=None)).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
    finally:
        await h.close()


async def test_public_config_exposes_only_name(env: Any) -> None:
    h, pid, _ = env
    async with h.client() as c:
        r = await c.get(f"/public/v1/agents/{pid}/config", headers={"origin": ORIGIN})
    assert r.json() == {"protocol_version": "1", "name": "Acme"}
    assert r.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.parametrize(
    "body",
    ["not json", "{}", '{"message": ""}', '{"message": "   "}', '{"message": 5}',
     '{"message": "hi", "role": "system"}', '{"message": "hi", "conversation_id": "nope"}',
     json.dumps({"message": "x" * 2001}), "[]", "null"],
)  # fmt: skip
async def test_invalid_bodies_get_generic_400_without_echo(env: Any, body: str) -> None:
    h, pid, _ = env
    r = await post(h, pid, body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    assert "input" not in r.text and "x" * 50 not in r.text


async def test_content_type_and_body_size_limits(session_factory: SF) -> None:
    h = make_harness(session_factory, public_max_body_bytes=200)
    seeded = await seed_agent(session_factory, [PRICING])
    pid = await publish(session_factory, seeded.agent_id)
    try:
        async with h.client() as c:
            wrong = await c.post(
                chat(pid),
                content='{"message":"hi"}',
                headers={"content-type": "text/plain", "origin": ORIGIN},
            )
            assert wrong.status_code == 415
            big = json.dumps({"message": "a" * 500})
            declared = await c.post(
                chat(pid),
                content=big,
                headers={"content-type": "application/json", "origin": ORIGIN},
            )
            assert (
                declared.status_code == 413
                and declared.json()["error"]["code"] == "payload_too_large"
            )

            async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: must still be bounded
                for _ in range(10):
                    yield b"a" * 100

            streamed = await c.post(
                chat(pid),
                content=chunks(),
                headers={"content-type": "application/json", "origin": ORIGIN},
            )
            assert streamed.status_code == 413
    finally:
        await h.close()


async def test_rate_limit_per_ip_and_daily_quota(session_factory: SF) -> None:
    h = make_harness(
        session_factory, public_rate_limit_per_minute=2, public_daily_quota_per_agent=3
    )
    seeded = await seed_agent(session_factory, [PRICING])
    pid = await publish(session_factory, seeded.agent_id)
    try:
        assert [
            (await post(h, pid, {"message": "hi"}, ip="198.51.100.1")).status_code for _ in range(2)
        ] == [200, 200]
        limited = await post(h, pid, {"message": "hi"}, ip="198.51.100.1")
        assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limited"
        assert int(limited.headers["retry-after"]) >= 1
        assert (
            await post(h, pid, {"message": "hi"}, ip="198.51.100.2")
        ).status_code == 200  # other client
        quota = await post(h, pid, {"message": "hi"}, ip="198.51.100.3")
        assert quota.status_code == 429 and quota.json()["error"]["code"] == "quota_exceeded"
    finally:
        await h.close()


async def test_xff_is_ignored_unless_proxies_are_trusted(session_factory: SF) -> None:
    seeded = await seed_agent(session_factory, [PRICING])
    pid = await publish(session_factory, seeded.agent_id)
    for hops, expect_second in ((0, 429), (1, 200)):
        h = make_harness(session_factory, public_rate_limit_per_minute=1, trusted_proxy_hops=hops)
        try:
            await post(
                h, pid, {"message": "hi"}, ip="10.1.1.1", headers={"x-forwarded-for": "203.0.113.1"}
            )
            r = await post(
                h, pid, {"message": "hi"}, ip="10.1.1.1", headers={"x-forwarded-for": "203.0.113.2"}
            )
            assert r.status_code == expect_second, hops
        finally:
            await h.close()


async def test_rate_limiter_fails_closed_when_redis_is_down(env: Any) -> None:
    import redis.asyncio as aioredis
    from wta_api.ratelimit import RateLimiter

    h, pid, _ = env
    h.app.state.rate_limiter = RateLimiter(aioredis.from_url("redis://127.0.0.1:1/0"), 10, 10)  # type: ignore[no-untyped-call]
    r = await post(h, pid, {"message": "hi"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "service_unavailable"
    assert h.llm.requests == []


async def test_concurrency_limit_returns_busy(session_factory: SF) -> None:
    h = make_harness(session_factory, public_max_concurrent_requests=1)
    seeded = await seed_agent(session_factory, [PRICING])
    pid = await publish(session_factory, seeded.agent_id)
    try:
        await h.app.state.public_semaphore.acquire()
        r = await post(h, pid, {"message": "hi"})
        assert (
            r.status_code == 503
            and r.json()["error"]["code"] == "busy"
            and r.headers["retry-after"]
        )
    finally:
        await h.close()


async def test_slow_assistant_times_out(session_factory: SF) -> None:
    h = make_harness(session_factory, public_request_timeout_seconds=0.2)
    seeded = await seed_agent(session_factory, [PRICING])
    pid = await publish(session_factory, seeded.agent_id)

    async def slow(*a: Any, **k: Any) -> Any:
        await asyncio.sleep(5)

    h.app.state.runtime.answer = slow
    try:
        r = await post(h, pid, {"message": "hi"})
        assert r.status_code == 504 and r.json()["error"]["code"] == "timeout"
    finally:
        await h.close()


@pytest.mark.parametrize(
    "exc,status,code",
    [
        (LLMError("llm_rate_limited", retryable=True), 503, "assistant_busy"),
        (LLMError("llm_auth_failed", fatal=True), 503, "assistant_unavailable"),
        (LLMError("llm_error"), 502, "assistant_unavailable"),
        (ConfigError("ANTHROPIC_API_KEY is not set: sk-ant-LEAK"), 503, "assistant_not_configured"),
        (
            EmbeddingSpaceMismatch("model changed: voyage-3 vs voyage-4"),
            503,
            "index_reindex_required",
        ),
        (RuntimeError("db password=hunter2 at /srv/app.py line 3"), 500, "internal_error"),
    ],
)
async def test_failures_are_mapped_and_never_leak_details(
    env: Any, exc: Exception, status: int, code: str
) -> None:
    h, pid, _ = env

    async def boom(*a: Any, **k: Any) -> Any:
        raise exc

    h.app.state.runtime.answer = boom
    r = await post(h, pid, {"message": "hi"})
    assert r.status_code == status and r.json()["error"]["code"] == code
    for secret in ("sk-ant", "hunter2", "voyage", "Traceback", "app.py", "llm_auth_failed"):
        assert secret not in r.text
    assert r.json()["error"]["request_id"] == r.headers["x-request-id"]


async def test_unconfigured_providers_give_generic_public_message(session_factory: SF) -> None:
    h = make_harness(session_factory)
    seeded = await seed_agent(session_factory, [PRICING])
    pid = await publish(session_factory, seeded.agent_id)
    h.app.state.runtime = (
        None  # force the lazy build, which fails without keys (.env-free settings)
    )
    try:
        r = await post(h, pid, {"message": "hi"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "assistant_not_configured"
        assert "ANTHROPIC_API_KEY" not in r.text
    finally:
        await h.close()


async def test_admin_surface_is_not_reachable_through_the_public_api(session_factory: SF) -> None:
    prod = make_harness(session_factory, app_env="production", local_no_auth=False)
    try:
        async with prod.client() as c:
            assert (await c.get("/admin/v1/agents")).status_code == 404
            assert (await c.get("/docs")).status_code == 404
            assert (await c.get("/openapi.json")).status_code == 404
    finally:
        await prod.close()

    # Not production and not local-no-auth: admin routes must not even be registered.
    closed = make_harness(session_factory, app_env="test", local_no_auth=False)
    try:
        paths = closed.app.openapi()["paths"]
        assert paths and not any(p.startswith("/admin") for p in paths)
        public = [p for p in paths if p.startswith("/public")]
        assert public and all(p.startswith("/public/v1/agents/{public_id}/") for p in public)
    finally:
        await closed.close()

    dev = make_harness(session_factory)
    try:
        async with dev.client("203.0.113.9") as c:  # remote client, even with spoofed forwarding
            r = await c.get(
                "/admin/v1/agents",
                headers={"x-forwarded-for": "127.0.0.1", "x-real-ip": "127.0.0.1"},
            )
        assert r.status_code == 404
    finally:
        await dev.close()
