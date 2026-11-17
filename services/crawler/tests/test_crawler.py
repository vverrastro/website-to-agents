from collections.abc import Callable

import httpx
from conftest import html
from wta_crawler import (
    CrawlConfig,
    Crawler,
    CrawlEvent,
    KnownPage,
    PageFetched,
    SitemapSeen,
    UrlSkipped,
)

SITEMAP = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://ok.test/</loc></url>
<url><loc>https://ok.test/about</loc><lastmod>2024-01-01</lastmod></url>
<url><loc>https://ok.test/secret/x</loc></url>
<url><loc>https://ok.test/doc.pdf</loc></url>
<url><loc>https://ok.test/blog/post?utm_source=nl</loc></url>
<url><loc>https://other.test/x</loc></url></urlset>"""

LONG = "<p>" + "Useful content. " * 30 + "</p>"
PAGES: dict[str, bytes] = {
    "/": html(
        LONG + '<a href="/about#team">a</a><a href="/contact">c</a><a href="/cart">cart</a>'
        '<a href="/about-us">dup</a><a href="/noindex">n</a><a href="/missing">m</a>'
        '<a href="/spa">s</a><a href="https://other.test/">o</a><a href="/?utm_source=x">self</a>'
    ),
    "/about": html(LONG, head='<link rel="canonical" href="/about-us">'),
    "/about-us": html(LONG, head='<link rel="canonical" href="/about-us">'),
    "/contact": html(LONG + '<a href="/deep1">d</a>'),
    "/deep1": html(LONG + '<a href="/deep2">d</a>'),
    "/deep2": html(LONG),
    "/blog/post": html(LONG),
    "/noindex": html(LONG, head='<meta name="robots" content="noindex">'),
    "/spa": html('<div id="root"></div><script src="app.js"></script>'),
}
ROBOTS = b"User-agent: *\nDisallow: /secret/\nSitemap: https://ok.test/sitemap.xml\n"


def site(requests: list[str] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    def handler(req: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(req.url.path)
        h = {"content-type": "text/html"}
        if req.url.path == "/robots.txt":
            return httpx.Response(200, content=ROBOTS, headers={"content-type": "text/plain"})
        if req.url.path == "/sitemap.xml":
            return httpx.Response(200, content=SITEMAP, headers={"content-type": "application/xml"})
        body = PAGES.get(req.url.path)
        if body is None:
            return httpx.Response(404, headers=h)
        return httpx.Response(200, content=body, headers={**h, "etag": '"v1"'})

    return handler


class FakeRenderer:
    def __init__(self) -> None:
        self.rendered: list[str] = []

    async def render(self, url: str) -> bytes:
        self.rendered.append(url)
        return html(LONG + '<a href="/rendered-link">r</a>')


async def run(make_fetcher, cfg: CrawlConfig, **kw) -> tuple[list[CrawlEvent], list[float]]:  # type: ignore[no-untyped-def]
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    handler = kw.pop("handler", site())
    known = kw.pop("known", None)
    async with make_fetcher(handler) as fetcher:
        crawler = Crawler(fetcher, cfg, user_agent="TestBot/1.0", sleep=fake_sleep, **kw)
        events = [e async for e in crawler.crawl("https://ok.test/", known)]
    return events, sleeps


def pages(events: list[CrawlEvent]) -> list[str]:
    return [e.url for e in events if isinstance(e, PageFetched)]


def skips(events: list[CrawlEvent]) -> dict[str, str]:
    return {e.url: e.reason for e in events if isinstance(e, UrlSkipped)}


async def test_full_crawl_discovery_filtering_and_audit_reasons(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    events, sleeps = await run(make_fetcher, CrawlConfig(max_depth=1, min_delay_seconds=0.5))
    got, why = pages(events), skips(events)

    assert "https://ok.test/" in got and "https://ok.test/about" in got
    assert "https://ok.test/blog/post" in got  # tracking param stripped, deduped
    assert "https://ok.test/contact" in got
    assert got.count("https://ok.test/") == 1  # /?utm_source=x is the same URL
    assert "https://ok.test/deep1" not in got  # beyond max_depth=1 (not linked at depth<=1)

    assert why["https://ok.test/secret/x"] == "robots_disallowed"
    assert why["https://ok.test/doc.pdf"] == "non_html_extension:.pdf"
    assert why["https://ok.test/cart"] == "excluded_path:/cart"
    assert why["https://ok.test/missing"] == "http_404"
    assert why["https://ok.test/noindex"] == "noindex_meta"
    assert why["https://other.test/"] == "off_site"
    assert "https://ok.test/about-us" in why.values() or "duplicate_canonical" in why.values()
    assert (
        all(s == 0.5 for s in sleeps)
        and len(sleeps)
        == len(got)
        + len([r for r in why.values() if r.startswith(("http_", "noindex", "duplicate"))])
        - 1
    )

    seen = {e.url: e.lastmod for e in events if isinstance(e, SitemapSeen)}
    assert seen["https://ok.test/about"] is not None


async def test_max_pages_and_depth_bounds(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    events, _ = await run(make_fetcher, CrawlConfig(max_pages=2, min_delay_seconds=0))
    assert len(pages(events)) == 2
    assert "max_pages_reached" in skips(events).values()

    events, _ = await run(
        make_fetcher, CrawlConfig(max_depth=0, use_sitemap=False, min_delay_seconds=0)
    )
    assert pages(events) == ["https://ok.test/"]


async def test_depth_limits_link_following(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    cfg = CrawlConfig(max_depth=2, use_sitemap=False, min_delay_seconds=0)
    events, _ = await run(make_fetcher, cfg)
    got = pages(events)
    assert "https://ok.test/deep1" in got  # depth 2
    assert "https://ok.test/deep2" not in got  # depth 3


async def test_crawl_delay_from_robots_is_honoured(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    base = site()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(
                200,
                content=b"User-agent: *\nCrawl-delay: 3\n",
                headers={"content-type": "text/plain"},
            )
        return base(req)

    _, sleeps = await run(
        make_fetcher,
        CrawlConfig(max_depth=1, min_delay_seconds=1, use_sitemap=False),
        handler=handler,
    )
    assert sleeps and set(sleeps) == {3.0}


async def test_robots_disallow_all_stops_crawl(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(403)
        raise AssertionError("no page may be requested")

    events, _ = await run(make_fetcher, CrawlConfig(), handler=handler)
    assert pages(events) == [] and "robots_http_403" in skips(events).values()


async def test_conditional_recrawl_yields_not_modified(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    base = site()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.headers.get("if-none-match") == '"v1"' and req.url.path not in (
            "/robots.txt",
            "/sitemap.xml",
        ):
            return httpx.Response(304, headers={"etag": '"v1"'})
        return base(req)

    cfg = CrawlConfig(max_depth=0, min_delay_seconds=0, max_pages=3)
    known = {"https://ok.test/about": KnownPage(etag='"v1"')}
    events, _ = await run(make_fetcher, cfg, handler=handler, known=known)
    about = next(e for e in events if isinstance(e, PageFetched) and e.url.endswith("/about"))
    assert about.not_modified and about.html == b"" and about.etag == '"v1"'


async def test_render_fallback_only_for_empty_shells(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    renderer = FakeRenderer()
    cfg = CrawlConfig(max_depth=1, use_sitemap=False, min_delay_seconds=0, rendering_enabled=True)
    events, _ = await run(make_fetcher, cfg, renderer=renderer)
    modes = {e.url: e.render_mode for e in events if isinstance(e, PageFetched)}
    assert modes["https://ok.test/spa"] == "rendered"
    assert modes["https://ok.test/"] == "static"
    assert renderer.rendered == ["https://ok.test/spa"]


async def test_rendering_is_off_by_default(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    renderer = FakeRenderer()
    cfg = CrawlConfig(max_depth=1, use_sitemap=False, min_delay_seconds=0)
    await run(make_fetcher, cfg, renderer=renderer)
    assert renderer.rendered == []


async def test_render_failure_does_not_abort_crawl(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    class Boom:
        async def render(self, url: str) -> bytes:
            raise RuntimeError("playwright exploded")

    cfg = CrawlConfig(max_depth=1, use_sitemap=False, min_delay_seconds=0, rendering_enabled=True)
    events, _ = await run(make_fetcher, cfg, renderer=Boom())
    assert "render_failed" in skips(events).values()
    assert "https://ok.test/contact" in pages(events)


async def test_duration_limit(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    ticks = iter(range(0, 1000, 100))
    cfg = CrawlConfig(max_duration_seconds=150, min_delay_seconds=0, use_sitemap=False)
    events, _ = await run(make_fetcher, cfg, clock=lambda: float(next(ticks)))
    assert "max_duration_reached" in skips(events).values()
    assert len(pages(events)) <= 2


async def test_internal_start_url_is_refused(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    async with make_fetcher(lambda r: httpx.Response(200)) as fetcher:
        crawler = Crawler(fetcher, CrawlConfig(), user_agent="T/1")
        events = [e async for e in crawler.crawl("http://internal.test/")]
    assert pages(events) == []
    assert any(r.startswith("robots_blocked:") for r in skips(events).values())
