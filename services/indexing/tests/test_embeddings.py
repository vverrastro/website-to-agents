import json

import httpx
import pytest
from wta_common.settings import ConfigError, Settings
from wta_indexing.embeddings import (
    EmbeddingError,
    FakeEmbeddingProvider,
    VoyageEmbeddingProvider,
    build_embedding_provider,
    register_embedding_provider,
)

DIMS = 8


def voyage(handler, sleeps=None):  # type: ignore[no-untyped-def]
    async def fake_sleep(s: float) -> None:
        if sleeps is not None:
            sleeps.append(s)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return VoyageEmbeddingProvider(
        "pa-secret-key", "voyage-4-lite", DIMS, client=client, sleep=fake_sleep
    )


def ok_response(req: httpx.Request) -> httpx.Response:
    n = len(json.loads(req.content)["input"])
    data = [{"index": i, "embedding": [float(i)] * DIMS} for i in reversed(range(n))]  # shuffled
    return httpx.Response(200, json={"data": data})


async def test_voyage_request_shape_and_ordering() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return ok_response(req)

    vecs = await voyage(handler).embed(["a", "b", "c"], input_type="document")
    body = json.loads(seen[0].content)
    assert body == {
        "input": ["a", "b", "c"],
        "model": "voyage-4-lite",
        "input_type": "document",
        "output_dimension": DIMS,
    }
    assert seen[0].headers["authorization"] == "Bearer pa-secret-key"
    assert [v[0] for v in vecs] == [0.0, 1.0, 2.0]  # re-ordered by index


async def test_voyage_batches_inputs() -> None:
    sizes: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sizes.append(len(json.loads(req.content)["input"]))
        return ok_response(req)

    out = await voyage(handler).embed([str(i) for i in range(150)], input_type="query")
    assert sizes == [64, 64, 22] and len(out) == 150


async def test_voyage_retries_transient_errors_with_backoff() -> None:
    attempts = iter([429, 503, 200])
    sleeps: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        status = next(attempts)
        return ok_response(req) if status == 200 else httpx.Response(status, text="secret body")

    out = await voyage(handler, sleeps).embed(["x"], input_type="document")
    assert len(out) == 1 and sleeps == [1.0, 2.0]


async def test_voyage_gives_up_after_max_attempts_without_leaking() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom pa-secret-key")

    with pytest.raises(EmbeddingError) as exc:
        await voyage(handler).embed(["x"], input_type="document")
    assert exc.value.code == "embedding_provider_unavailable" and exc.value.retryable
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize(
    "status,code,fatal",
    [
        (401, "embedding_auth_failed", True),
        (403, "embedding_auth_failed", True),
        (400, "embedding_rejected_400", False),
    ],
)
async def test_voyage_non_retryable_errors(status: int, code: str, fatal: bool) -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="detail")

    with pytest.raises(EmbeddingError) as exc:
        await voyage(handler).embed(["x"], input_type="document")
    assert (exc.value.code, exc.value.fatal, calls) == (code, fatal, 1)


async def test_voyage_network_error_is_retried_then_raised() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns secret")

    with pytest.raises(EmbeddingError, match="embedding_network_error"):
        await voyage(handler).embed(["x"], input_type="document")


async def test_voyage_dimension_mismatch_is_fatal() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]})

    with pytest.raises(EmbeddingError) as exc:
        await voyage(handler).embed(["x"], input_type="document")
    assert exc.value.code == "embedding_dimension_mismatch" and exc.value.fatal


async def test_voyage_malformed_response() -> None:
    with pytest.raises(EmbeddingError, match="embedding_bad_response"):
        await voyage(lambda r: httpx.Response(200, json={"oops": 1})).embed(
            ["x"], input_type="document"
        )


async def test_fake_provider_similarity_and_determinism() -> None:
    f = FakeEmbeddingProvider(64)
    a, b, c = await f.embed(
        ["red apple pie", "red apple tart", "quantum chromodynamics"], input_type="document"
    )
    dot = lambda x, y: sum(p * q for p, q in zip(x, y, strict=True))  # noqa: E731
    assert dot(a, b) > dot(a, c)
    assert (await f.embed(["red apple pie"], input_type="query"))[0] == a
    assert abs(dot(a, a) - 1.0) < 1e-9


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_registry_errors_are_actionable() -> None:
    with pytest.raises(ConfigError, match="VOYAGE_API_KEY"):
        build_embedding_provider(settings(embedding_provider="voyage"))
    with pytest.raises(ConfigError, match="Unknown EMBEDDING_PROVIDER"):
        build_embedding_provider(settings(embedding_provider="nope"))
    with pytest.raises(ConfigError, match="production"):
        build_embedding_provider(settings(embedding_provider="fake", app_env="production"))


def test_registry_builds_and_is_extensible() -> None:
    p = build_embedding_provider(
        settings(voyage_api_key="k", embedding_model="m", embedding_dimensions=1024)
    )
    assert (p.provider, p.model, p.dimensions) == ("voyage", "m", 1024)
    register_embedding_provider("custom", lambda s: FakeEmbeddingProvider(4))
    assert build_embedding_provider(settings(embedding_provider="custom")).dimensions == 4
