import httpx
from conftest import html
from wta_crawler.robots import load_robots, parse_robots

ROBOTS = """
User-agent: *
Disallow: /private/
Crawl-delay: 2

User-agent: WebsiteToAgentsBot
Disallow: /no-bot/
Allow: /

Sitemap: https://ok.test/sitemap.xml
"""


def test_parse_matches_product_token() -> None:
    p = parse_robots(ROBOTS, "WebsiteToAgentsBot/0.1")
    assert p.allowed("https://ok.test/private/x")  # bot-specific group allows it
    assert not p.allowed("https://ok.test/no-bot/x")
    assert p.sitemaps == ["https://ok.test/sitemap.xml"]


def test_generic_group_and_crawl_delay() -> None:
    p = parse_robots(ROBOTS, "OtherBot/1")
    assert not p.allowed("https://ok.test/private/x")
    assert p.allowed("https://ok.test/public")
    assert p.crawl_delay == 2.0


def test_empty_robots_allows_everything() -> None:
    assert parse_robots("", "Bot/1").allowed("https://ok.test/anything")


async def _load(make_fetcher, status: int, body: str = "", ctype: str = "text/plain"):  # type: ignore[no-untyped-def]
    h = lambda r: httpx.Response(status, content=body.encode(), headers={"content-type": ctype})  # noqa: E731
    async with make_fetcher(h) as f:
        return await load_robots(f, "https://ok.test", "Bot/1")


async def test_availability_semantics(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    assert (await _load(make_fetcher, 404)).allowed("https://ok.test/x")
    assert (await _load(make_fetcher, 410)).allowed("https://ok.test/x")
    for status in (401, 403, 500, 503):
        p = await _load(make_fetcher, status)
        assert p.disallow_all and not p.allowed("https://ok.test/x")
    ok = await _load(make_fetcher, 200, "User-agent: *\nDisallow: /a")
    assert not ok.allowed("https://ok.test/a") and ok.allowed("https://ok.test/b")


async def test_unreachable_robots_means_disallow(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x")

    async with make_fetcher(boom) as f:
        p = await load_robots(f, "https://ok.test", "Bot/1")
    assert p.disallow_all and p.reason == "robots_network_error"
    assert html("x")  # keep helper import used
