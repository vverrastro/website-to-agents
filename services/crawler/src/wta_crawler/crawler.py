"""Deterministic crawl orchestration. No LLM, no database: it yields events that the indexing
job persists. Bounded by pages, depth, duration, request rate and per-fetch limits."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from wta_crawler.fetcher import Fetcher, FetchError
from wta_crawler.html_links import parse_page_links
from wta_crawler.render import Renderer, needs_rendering
from wta_crawler.robots import RobotsPolicy, load_robots
from wta_crawler.sitemap import collect_sitemap_entries
from wta_crawler.urls import UrlPolicy, normalize_url, same_site

MAX_QUEUE = 10_000


@dataclass(frozen=True)
class CrawlConfig:
    max_pages: int = 50
    max_depth: int = 3
    min_delay_seconds: float = 1.0
    max_duration_seconds: float = 600.0
    use_sitemap: bool = True
    respect_robots: bool = True
    # Off by default: a sitemap lastmod is only a hint and may be wrong.
    trust_sitemap_lastmod: bool = False
    rendering_enabled: bool = False
    render_urls: frozenset[str] = frozenset()
    policy: UrlPolicy = field(default_factory=UrlPolicy)


@dataclass(frozen=True)
class KnownPage:
    """What we already know about a URL from a previous crawl (for conditional requests)."""

    etag: str | None = None
    last_modified: str | None = None
    last_crawled_at: datetime | None = None


@dataclass(frozen=True)
class PageFetched:
    url: str
    final_url: str
    canonical_url: str | None
    status: int
    html: bytes
    etag: str | None
    last_modified_header: str | None
    origin_last_modified: datetime | None
    sitemap_lastmod: datetime | None
    depth: int
    render_mode: str  # "static" | "rendered"
    not_modified: bool = False


@dataclass(frozen=True)
class UrlSkipped:
    url: str
    reason: str


@dataclass(frozen=True)
class SitemapSeen:
    """A URL listed in a sitemap (feeds ``last_seen_in_sitemap``)."""

    url: str
    lastmod: datetime | None


CrawlEvent = PageFetched | UrlSkipped | SitemapSeen


def _parse_http_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


class Crawler:
    def __init__(
        self,
        fetcher: Fetcher,
        config: CrawlConfig,
        *,
        user_agent: str,
        renderer: Renderer | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetcher = fetcher
        self._cfg = config
        self._ua = user_agent
        self._renderer = renderer
        self._sleep = sleep
        self._clock = clock

    async def crawl(
        self, start_url: str, known: Mapping[str, KnownPage] | None = None
    ) -> AsyncIterator[CrawlEvent]:
        cfg, known = self._cfg, known or {}
        start = normalize_url(start_url, extra_tracking=cfg.policy.extra_tracking)
        if start is None:
            yield UrlSkipped(start_url, "invalid_start_url")
            return
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(start))
        deadline = self._clock() + cfg.max_duration_seconds

        robots = RobotsPolicy(agent="*")
        if cfg.respect_robots:
            robots = await load_robots(self._fetcher, origin, self._ua)
            if robots.disallow_all:
                yield UrlSkipped(start, robots.reason or "robots_disallow_all")
                return
        delay = max(cfg.min_delay_seconds, robots.crawl_delay or 0.0)

        queue: deque[tuple[str, int, datetime | None]] = deque([(start, 0, None)])
        seen: set[str] = {start}
        canonicals: set[str] = set()
        skipped_reported: set[str] = set()

        def skip(url: str, reason: str) -> UrlSkipped | None:
            if url in skipped_reported:
                return None
            skipped_reported.add(url)
            return UrlSkipped(url, reason)

        if cfg.use_sitemap:
            roots = list(robots.sitemaps) or [origin + "/sitemap.xml"]
            entries, errors = await collect_sitemap_entries(self._fetcher, roots, start_url=start)
            for sm_url, err in errors:
                yield UrlSkipped(sm_url, f"sitemap:{err}")
            for entry in entries:
                norm = normalize_url(entry.url, extra_tracking=cfg.policy.extra_tracking)
                if norm is None or norm in seen:
                    continue
                reason = cfg.policy.check(norm, start)
                if reason is None and not robots.allowed(norm):
                    reason = "robots_disallowed"
                if reason:
                    ev = skip(norm, reason)
                    if ev:
                        yield ev
                    continue
                seen.add(norm)
                yield SitemapSeen(norm, entry.lastmod)
                if len(queue) < MAX_QUEUE:
                    queue.append((norm, 0, entry.lastmod))
            if start not in {e.url for e in entries}:
                pass  # start URL is always crawled; absence from the sitemap is not an error

        fetched = 0
        first_request = True
        while queue:
            url, depth, lastmod = queue.popleft()
            if fetched >= cfg.max_pages:
                ev = skip(url, "max_pages_reached")
                if ev:
                    yield ev
                continue
            if self._clock() >= deadline:
                ev = skip(url, "max_duration_reached")
                if ev:
                    yield ev
                continue
            if not robots.allowed(url):
                ev = skip(url, "robots_disallowed")
                if ev:
                    yield ev
                continue
            prior = known.get(url)
            if (
                cfg.trust_sitemap_lastmod
                and prior
                and prior.last_crawled_at
                and lastmod
                and lastmod <= prior.last_crawled_at
            ):
                ev = skip(url, "sitemap_lastmod_unchanged")
                if ev:
                    yield ev
                continue
            if not first_request:
                await self._sleep(delay)
            first_request = False

            try:
                res = await self._fetcher.fetch(
                    url,
                    etag=prior.etag if prior else None,
                    last_modified=prior.last_modified if prior else None,
                )
            except FetchError as exc:
                ev = skip(url, f"fetch_error:{exc.reason}")
                if ev:
                    yield ev
                continue
            fetched += 1

            final = normalize_url(res.final_url, extra_tracking=cfg.policy.extra_tracking) or url
            seen.add(final)
            if res.not_modified:
                yield PageFetched(
                    url=url, final_url=final, canonical_url=None, status=304, html=b"",
                    etag=res.etag or (prior.etag if prior else None),
                    last_modified_header=res.last_modified
                    or (prior.last_modified if prior else None),
                    origin_last_modified=_parse_http_date(res.last_modified),
                    sitemap_lastmod=lastmod, depth=depth, render_mode="static", not_modified=True,
                )  # fmt: skip
                continue
            if res.status != 200:
                ev = skip(url, f"http_{res.status}")
                if ev:
                    yield ev
                continue

            html, render_mode = res.body, "static"
            if (
                cfg.rendering_enabled
                and self._renderer is not None
                and (url in cfg.render_urls or needs_rendering(html))
            ):
                try:
                    html, render_mode = await self._renderer.render(final), "rendered"
                except Exception:  # renderer failures must not abort the crawl
                    ev = skip(url, "render_failed")
                    if ev:
                        yield ev

            links = parse_page_links(html, final)
            canonical = (
                normalize_url(links.canonical, extra_tracking=cfg.policy.extra_tracking)
                if links.canonical
                else None
            )
            if canonical and not same_site(canonical, start):
                canonical = None  # ignore off-site canonicals
            identity = canonical or final
            if identity in canonicals:
                ev = skip(url, "duplicate_canonical")
                if ev:
                    yield ev
                continue
            canonicals.add(identity)

            if depth < cfg.max_depth and not links.nofollow:
                for raw in links.links:
                    norm = normalize_url(raw, extra_tracking=cfg.policy.extra_tracking)
                    if norm is None or norm in seen:
                        continue
                    seen.add(norm)
                    reason = cfg.policy.check(norm, start)
                    if reason:
                        ev = skip(norm, reason)
                        if ev:
                            yield ev
                    elif len(queue) < MAX_QUEUE:
                        queue.append((norm, depth + 1, None))

            if links.noindex:
                ev = skip(url, "noindex_meta")
                if ev:
                    yield ev
                continue

            yield PageFetched(
                url=url, final_url=final, canonical_url=canonical, status=200, html=html,
                etag=res.etag, last_modified_header=res.last_modified,
                origin_last_modified=_parse_http_date(res.last_modified),
                sitemap_lastmod=lastmod, depth=depth, render_mode=render_mode,
            )  # fmt: skip
