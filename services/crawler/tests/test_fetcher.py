import gzip

import httpx
import pytest
from conftest import html
from wta_crawler.fetcher import Fetcher, FetchError, SafeTransport
from wta_crawler.ssrf import SSRFError

OK_HEADERS = {"content-type": "text/html; charset=utf-8"}


async def test_basic_fetch_and_validators(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["user-agent"] == "TestBot/1.0"
        return httpx.Response(
            200, content=html("hi"), headers={**OK_HEADERS, "etag": '"a"', "last-modified": "x"}
        )

    async with make_fetcher(handler) as f:
        r = await f.fetch("https://ok.test/")
    assert r.status == 200 and b"hi" in r.body and r.etag == '"a"' and r.last_modified == "x"


async def test_conditional_request_headers_and_304(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        return httpx.Response(304, headers={"etag": '"a"'})

    async with make_fetcher(handler) as f:
        r = await f.fetch(
            "https://ok.test/", etag='"a"', last_modified="Mon, 01 Jan 2024 00:00:00 GMT"
        )
    assert r.not_modified and r.body == b""
    assert seen["if-none-match"] == '"a"'
    assert seen["if-modified-since"].startswith("Mon, 01 Jan 2024")


async def test_non_200_returns_status_without_body(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    async with make_fetcher(
        lambda r: httpx.Response(404, content=b"nope", headers=OK_HEADERS)
    ) as f:
        r = await f.fetch("https://ok.test/missing")
    assert r.status == 404 and r.body == b""


async def test_redirect_followed_on_same_site(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/a":
            return httpx.Response(301, headers={"location": "https://www.ok.test/b"})
        return httpx.Response(200, content=html("b"), headers=OK_HEADERS)

    async with make_fetcher(handler) as f:
        r = await f.fetch("https://ok.test/a")
    assert r.final_url == "https://www.ok.test/b" and r.redirects == 1


@pytest.mark.parametrize(
    "target",
    ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:6379/", "http://internal.test/",
     "http://meta.test/", "file:///etc/passwd", "http://[::1]/"],
)  # fmt: skip
async def test_redirect_to_internal_destination_is_blocked(make_fetcher, target: str) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(302, headers={"location": target})

    async with make_fetcher(handler) as f:
        with pytest.raises(FetchError) as exc:
            await f.fetch("https://ok.test/", allow_off_site_redirects=True)
    assert exc.value.reason.startswith("blocked:")
    assert calls == ["https://ok.test/"]  # the internal URL was never requested


async def test_off_site_redirect_rejected_by_default(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    h = lambda r: httpx.Response(302, headers={"location": "https://other.test/"})  # noqa: E731
    async with make_fetcher(h) as f:
        with pytest.raises(FetchError, match="redirect_off_site"):
            await f.fetch("https://ok.test/")


async def test_redirect_limit(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    h = lambda r: httpx.Response(302, headers={"location": "/loop"})  # noqa: E731
    async with make_fetcher(h, max_redirects=3) as f:
        with pytest.raises(FetchError, match="too_many_redirects"):
            await f.fetch("https://ok.test/")


async def test_internal_start_url_blocked_before_any_request(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be requested")

    async with make_fetcher(handler) as f:
        with pytest.raises(FetchError, match="blocked:forbidden_address"):
            await f.fetch("http://internal.test/admin")


async def test_content_type_allowlist(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    h = lambda r: httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})  # noqa: E731
    async with make_fetcher(h) as f:
        with pytest.raises(FetchError, match="unsupported_content_type"):
            await f.fetch("https://ok.test/x")


async def test_declared_and_streamed_size_limits(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    big = b"a" * 5000
    declared = lambda r: httpx.Response(  # noqa: E731
        200, content=big, headers={**OK_HEADERS, "content-length": "5000"}
    )
    async with make_fetcher(declared, max_bytes=1000) as f:
        with pytest.raises(FetchError, match="too_large"):
            await f.fetch("https://ok.test/")

    async def chunks():  # type: ignore[no-untyped-def]
        for _ in range(10):
            yield b"b" * 500

    streamed = lambda r: httpx.Response(200, content=chunks(), headers=OK_HEADERS)  # noqa: E731
    async with make_fetcher(streamed, max_bytes=1000) as f:
        with pytest.raises(FetchError, match="too_large"):
            await f.fetch("https://ok.test/")


async def test_decompressed_size_is_limited(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    bomb = gzip.compress(b"a" * 5_000_000)
    assert len(bomb) < 20_000
    h = lambda r: httpx.Response(  # noqa: E731
        200, content=bomb, headers={**OK_HEADERS, "content-encoding": "gzip"}
    )
    async with make_fetcher(h, max_bytes=100_000) as f:
        with pytest.raises(FetchError, match="too_large"):
            await f.fetch("https://ok.test/")


async def test_timeout_and_network_errors_are_mapped(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    def timeout(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret-detail")

    def broken(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-detail")

    async with make_fetcher(timeout) as f:
        with pytest.raises(FetchError) as e1:
            await f.fetch("https://ok.test/")
    async with make_fetcher(broken) as f:
        with pytest.raises(FetchError) as e2:
            await f.fetch("https://ok.test/")
    assert (e1.value.reason, e2.value.reason) == ("timeout", "network_error")
    assert "secret-detail" not in str(e1.value) + str(e2.value)


async def test_real_transport_blocks_dns_rebinding_at_connect() -> None:
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])  # pre-flight public, connect-time loopback

    async def rebinding(host: str, port: int) -> list[str]:
        return next(answers)

    f = Fetcher(user_agent="t", resolver=rebinding, transport=SafeTransport(rebinding))
    try:
        with pytest.raises(FetchError, match="blocked:forbidden_address"):
            await f.fetch("http://rebind.test/")
    finally:
        await f.aclose()
    assert issubclass(SSRFError, Exception)
