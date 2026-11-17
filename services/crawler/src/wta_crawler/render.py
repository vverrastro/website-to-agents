"""Controlled JavaScript-rendering fallback. Off by default; Playwright is an optional extra."""

from __future__ import annotations

import re
from typing import Protocol

import lxml.html
from lxml import etree

from wta_crawler.ssrf import Resolver, SSRFError, default_resolver, validate_url

MIN_TEXT_CHARS = 200
_SPA_MARKERS = re.compile(
    r'id=["\'](root|app|__next|__nuxt)["\']|ng-app|data-reactroot|enable javascript', re.I
)


class Renderer(Protocol):
    async def render(self, url: str) -> bytes: ...


def visible_text_length(html: bytes | str) -> int:
    try:
        doc = lxml.html.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return 0
    for el in doc.xpath("//head|//script|//style|//noscript|//template"):
        el.drop_tree()
    return len(" ".join(doc.text_content().split()))


def needs_rendering(html: bytes | str) -> bool:
    """Conservative: only when static HTML has almost no text AND looks like a client-rendered
    shell. Pages with real content are never rendered."""
    if visible_text_length(html) >= MIN_TEXT_CHARS:
        return False
    text = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
    return bool(_SPA_MARKERS.search(text)) or text.lower().count("<script") >= 3


class PlaywrightRenderer:
    """Renders with headless Chromium. Every browser request is validated against the same
    SSRF rules; downloads, service workers and non-http(s) schemes are blocked.

    Limitation: the browser resolves DNS itself, so route-level checks cannot fully prevent DNS
    rebinding inside the browser. Keep rendering disabled unless you need it (ADR 0005).
    """

    def __init__(
        self, user_agent: str, resolver: Resolver = default_resolver, timeout_ms: int = 20_000
    ) -> None:
        self._ua = user_agent
        self._resolver = resolver
        self._timeout_ms = timeout_ms

    async def render(self, url: str) -> bytes:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Playwright is not installed. Run: uv sync --extra render && "
                "uv run playwright install chromium"
            ) from exc
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=["--disable-extensions"])
            try:
                context = await browser.new_context(
                    user_agent=self._ua,
                    accept_downloads=False,
                    service_workers="block",
                    java_script_enabled=True,
                )

                async def guard(route, request):  # type: ignore[no-untyped-def]
                    try:
                        await validate_url(request.url, self._resolver)
                    except SSRFError:
                        await route.abort("blockedbyclient")
                        return
                    await route.continue_()

                await context.route("**/*", guard)
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=self._timeout_ms)
                content: str = await page.content()
                return content.encode("utf-8")
            finally:
                await browser.close()
