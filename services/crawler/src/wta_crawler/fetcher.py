"""HTTP fetching with SSRF protection, bounded size/redirects/time and conditional requests."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from types import TracebackType
from urllib.parse import urljoin

import certifi
import httpcore
import httpx

from wta_crawler.ssrf import Resolver, SafeNetworkBackend, SSRFError, default_resolver, validate_url
from wta_crawler.urls import same_site

HTML_TYPES = ("text/html", "application/xhtml+xml")
_REDIRECTS = frozenset({301, 302, 303, 307, 308})


class FetchError(Exception):
    """A fetch failed. ``reason`` is a short stable code (never contains provider/secret data)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class FetchLimits:
    max_bytes: int = 5_000_000
    max_redirects: int = 5
    request_timeout: float = 15.0  # per network operation
    total_timeout: float = 30.0  # wall clock per hop, bounds slow-drip responses


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str | None
    body: bytes
    etag: str | None
    last_modified: str | None
    not_modified: bool
    redirects: int


class _Stream(httpx.AsyncByteStream):
    def __init__(self, stream: AsyncIterable[bytes]) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        closer = getattr(self._stream, "aclose", None)
        if closer is not None:
            await closer()


class SafeTransport(httpx.AsyncBaseTransport):
    """httpx transport whose connections are validated at connect time (anti DNS rebinding)."""

    def __init__(self, resolver: Resolver = default_resolver) -> None:
        context = ssl.create_default_context(cafile=certifi.where())
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=context,
            max_connections=10,
            max_keepalive_connections=5,
            keepalive_expiry=15.0,
            http1=True,
            http2=False,
            network_backend=SafeNetworkBackend(resolver),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(request.stream, httpx.AsyncByteStream)
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        resp = await self._pool.handle_async_request(req)
        assert isinstance(resp.stream, AsyncIterable)
        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            stream=_Stream(resp.stream),
            extensions=resp.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


@dataclass(frozen=True)
class _Hop:
    result: FetchResult | None = None
    redirect_to: str | None = None


def _matches(content_type: str | None, accept: tuple[str, ...]) -> bool:
    if not content_type:
        return False
    return any(
        content_type == a or (a.endswith("/") and content_type.startswith(a)) for a in accept
    )


class Fetcher:
    def __init__(
        self,
        *,
        user_agent: str,
        resolver: Resolver = default_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        limits: FetchLimits = FetchLimits(),  # noqa: B008 (frozen dataclass)
    ) -> None:
        self.limits = limits
        self._resolver = resolver
        self._client = httpx.AsyncClient(
            transport=transport or SafeTransport(resolver),
            follow_redirects=False,
            trust_env=False,  # never pick up proxy settings from the environment
            timeout=httpx.Timeout(limits.request_timeout, connect=5.0),
            headers={"User-Agent": user_agent},
        )

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(
        self,
        url: str,
        *,
        accept: tuple[str, ...] = HTML_TYPES,
        max_bytes: int | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        allow_off_site_redirects: bool = False,
    ) -> FetchResult:
        limit = max_bytes or self.limits.max_bytes
        current = url
        for hop_no in range(self.limits.max_redirects + 1):
            try:
                await validate_url(current, self._resolver)  # pre-flight on every hop
                async with asyncio.timeout(self.limits.total_timeout):
                    hop = await self._request(
                        current, url, hop_no, accept, limit, etag, last_modified
                    )
            except SSRFError as exc:
                raise FetchError(f"blocked:{exc.reason}") from exc
            except (TimeoutError, httpx.TimeoutException, httpcore.TimeoutException) as exc:
                raise FetchError("timeout") from exc
            except (httpx.HTTPError, httpcore.NetworkError, httpcore.ProtocolError) as exc:
                raise FetchError("network_error") from exc
            if hop.result is not None:
                return hop.result
            assert hop.redirect_to is not None
            if len(hop.redirect_to) > 2048:
                raise FetchError("redirect_url_too_long")
            if not allow_off_site_redirects and not same_site(hop.redirect_to, url):
                raise FetchError("redirect_off_site")
            current = hop.redirect_to
        raise FetchError("too_many_redirects")

    async def _request(
        self,
        current: str,
        original: str,
        hop_no: int,
        accept: tuple[str, ...],
        limit: int,
        etag: str | None,
        last_modified: str | None,
    ) -> _Hop:
        headers = {"Accept": ",".join(accept)}
        if hop_no == 0:  # conditional validators only apply to the originally requested URL
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified
        async with self._client.stream("GET", current, headers=headers) as resp:
            status = resp.status_code
            location = resp.headers.get("location")
            if status in _REDIRECTS and location:
                return _Hop(redirect_to=urljoin(current, location))
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower() or None

            def result(body: bytes = b"", *, not_modified: bool = False) -> _Hop:
                return _Hop(
                    result=FetchResult(
                        url=original,
                        final_url=current,
                        status=status,
                        content_type=ctype,
                        body=body,
                        etag=resp.headers.get("etag"),
                        last_modified=resp.headers.get("last-modified"),
                        not_modified=not_modified,
                        redirects=hop_no,
                    )
                )

            if status == 304:
                return result(not_modified=True)
            if status != 200:
                return result()
            if not _matches(ctype, accept):
                raise FetchError("unsupported_content_type")
            declared = resp.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise FetchError("too_large")
            buf = bytearray()
            # aiter_bytes yields *decoded* bytes, so this bounds decompressed size too.
            async for chunk in resp.aiter_bytes():
                buf += chunk
                if len(buf) > limit:
                    raise FetchError("too_large")
            return result(bytes(buf))
