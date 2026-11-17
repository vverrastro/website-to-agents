"""PlaywrightRenderer with a fake ``playwright`` module: verifies the isolation settings and the
per-request SSRF guard without downloading a browser."""

import sys
import types
from typing import Any

import pytest
from wta_crawler.render import PlaywrightRenderer


class FakeRoute:
    def __init__(self) -> None:
        self.action: str | None = None

    async def abort(self, reason: str) -> None:
        self.action = f"abort:{reason}"

    async def continue_(self) -> None:
        self.action = "continue"


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakePage:
    def __init__(self, ctx: "FakeContext") -> None:
        self.ctx = ctx

    async def goto(self, url: str, **kw: Any) -> None:
        self.ctx.goto = (url, kw)

    async def content(self) -> str:
        return "<html><body>rendered</body></html>"


class FakeContext:
    def __init__(self, **kw: Any) -> None:
        self.options = kw
        self.handler: Any = None
        self.goto: Any = None

    async def route(self, pattern: str, handler: Any) -> None:
        self.pattern, self.handler = pattern, handler

    async def new_page(self) -> FakePage:
        return FakePage(self)


class FakeBrowser:
    def __init__(self) -> None:
        self.context: FakeContext | None = None
        self.closed = False

    async def new_context(self, **kw: Any) -> FakeContext:
        self.context = FakeContext(**kw)
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakePlaywright:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = types.SimpleNamespace(launch=self._launch)
        self._browser = browser
        self.launch_args: Any = None

    async def _launch(self, **kw: Any) -> FakeBrowser:
        self.launch_args = kw
        return self._browser


class FakeManager:
    def __init__(self, pw: FakePlaywright) -> None:
        self.pw = pw

    async def __aenter__(self) -> FakePlaywright:
        return self.pw

    async def __aexit__(self, *a: Any) -> None:
        return None


@pytest.fixture
def fake_playwright(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeBrowser, FakePlaywright]:
    browser = FakeBrowser()
    pw = FakePlaywright(browser)
    module = types.ModuleType("playwright.async_api")
    module.async_playwright = lambda: FakeManager(pw)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    return browser, pw


async def public_resolver(host: str, port: int) -> list[str]:
    return {"internal.test": ["10.0.0.7"], "meta.test": ["169.254.169.254"]}.get(
        host, ["93.184.216.34"]
    )


async def test_renders_with_isolation_and_blocks_internal_requests(fake_playwright: Any) -> None:
    browser, pw = fake_playwright
    html = await PlaywrightRenderer("Bot/1", resolver=public_resolver).render(
        "https://acme.test/spa"
    )
    assert html == b"<html><body>rendered</body></html>"
    ctx = browser.context
    assert ctx and ctx.options["accept_downloads"] is False
    assert ctx.options["service_workers"] == "block" and ctx.options["user_agent"] == "Bot/1"
    assert ctx.goto[0] == "https://acme.test/spa" and ctx.pattern == "**/*"
    assert browser.closed  # always cleaned up

    verdicts = {}
    for url in (
        "https://acme.test/app.js",
        "http://internal.test/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:6379/",
        "file:///etc/passwd",
        "ftp://acme.test/x",
    ):
        route = FakeRoute()
        await ctx.handler(route, FakeRequest(url))
        verdicts[url] = route.action
    assert verdicts["https://acme.test/app.js"] == "continue"
    assert all(
        v == "abort:blockedbyclient" for u, v in verdicts.items() if u != "https://acme.test/app.js"
    )
    assert pw.launch_args is not None


async def test_browser_is_closed_even_when_navigation_fails(fake_playwright: Any) -> None:
    browser, _ = fake_playwright

    async def boom(self: FakePage, url: str, **kw: Any) -> None:
        raise RuntimeError("navigation failed")

    FakePage.goto = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await PlaywrightRenderer("Bot/1", resolver=public_resolver).render("https://acme.test/")
    assert browser.closed


async def test_missing_playwright_gives_an_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    with pytest.raises(RuntimeError, match="playwright install chromium"):
        await PlaywrightRenderer("Bot/1").render("https://acme.test/")
