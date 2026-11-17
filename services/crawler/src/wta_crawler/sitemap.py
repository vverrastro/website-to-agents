"""sitemap.xml / sitemap index parsing with hardened XML handling and bounded recursion."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from lxml import etree

from wta_crawler.fetcher import Fetcher, FetchError
from wta_crawler.urls import same_site

SITEMAP_TYPES = (
    "text/xml",
    "application/xml",
    "application/gzip",
    "application/x-gzip",
    "text/plain",
    "application/octet-stream",
)
MAX_DECOMPRESSED_BYTES = 50_000_000


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    lastmod: datetime | None


@dataclass
class ParsedSitemap:
    entries: list[SitemapEntry] = field(default_factory=list)
    child_sitemaps: list[str] = field(default_factory=list)


class SitemapError(Exception):
    pass


def parse_w3c_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _maybe_gunzip(data: bytes) -> bytes:
    if data[:2] != b"\x1f\x8b":
        return data
    inflater = zlib.decompressobj(wbits=31)
    out = inflater.decompress(data, MAX_DECOMPRESSED_BYTES)
    if inflater.unconsumed_tail:
        raise SitemapError("sitemap_too_large_decompressed")
    return out


def parse_sitemap(data: bytes, *, max_entries: int = 50_000) -> ParsedSitemap:
    data = _maybe_gunzip(data)
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        recover=False,
    )
    try:
        root = etree.fromstring(data, parser)  # noqa: S320 (hardened parser above)
    except etree.XMLSyntaxError as exc:
        raise SitemapError("invalid_xml") from exc
    kind = etree.QName(root).localname
    if kind not in ("urlset", "sitemapindex"):
        raise SitemapError("not_a_sitemap")
    result = ParsedSitemap()
    for child in root:
        if not isinstance(child.tag, str):  # comments / processing instructions
            continue
        fields = {
            etree.QName(c).localname: (c.text or "").strip()
            for c in child
            if isinstance(c.tag, str)
        }
        loc = fields.get("loc")
        if not loc:
            continue
        if kind == "sitemapindex":
            result.child_sitemaps.append(loc)
        else:
            result.entries.append(SitemapEntry(loc, parse_w3c_datetime(fields.get("lastmod"))))
        if len(result.entries) + len(result.child_sitemaps) >= max_entries:
            break
    return result


async def collect_sitemap_entries(
    fetcher: Fetcher,
    roots: list[str],
    *,
    start_url: str,
    max_depth: int = 3,
    max_sitemaps: int = 20,
    max_urls: int = 5_000,
) -> tuple[list[SitemapEntry], list[tuple[str, str]]]:
    """Walk sitemap indexes. Returns (entries, [(sitemap_url, error_reason)])."""
    entries: list[SitemapEntry] = []
    errors: list[tuple[str, str]] = []
    seen: set[str] = set()
    frontier = [(u, 0) for u in roots]
    while frontier and len(seen) < max_sitemaps and len(entries) < max_urls:
        url, depth = frontier.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if not same_site(url, start_url):
            errors.append((url, "sitemap_off_site"))
            continue
        try:
            res = await fetcher.fetch(url, accept=SITEMAP_TYPES, max_bytes=10_000_000)
            if res.status != 200:
                errors.append((url, f"http_{res.status}"))
                continue
            parsed = parse_sitemap(res.body)
        except FetchError as exc:
            errors.append((url, exc.reason))
            continue
        except SitemapError as exc:
            errors.append((url, str(exc)))
            continue
        entries.extend(parsed.entries[: max_urls - len(entries)])
        if depth < max_depth:
            frontier.extend((child, depth + 1) for child in parsed.child_sitemaps)
    return entries, errors
