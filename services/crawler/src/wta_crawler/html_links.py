"""Link/canonical/robots-meta discovery from fetched HTML (no content extraction here)."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

import lxml.html
from lxml import etree

_LOWER = "translate({0},'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"
MAX_LINKS = 2000


@dataclass
class PageLinks:
    canonical: str | None = None
    links: list[str] = field(default_factory=list)
    noindex: bool = False
    nofollow: bool = False


def parse_page_links(html: bytes | str, base_url: str) -> PageLinks:
    if not html or not html.strip():
        return PageLinks()
    try:
        doc = lxml.html.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return PageLinks()
    base = base_url
    base_href = doc.xpath("//base/@href")
    if base_href:
        base = urljoin(base_url, str(base_href[0]))
    out = PageLinks()
    for content in doc.xpath(f"//meta[{_LOWER.format('@name')}='robots']/@content"):
        tokens = {t.strip().lower() for t in str(content).split(",")}
        out.noindex = out.noindex or "noindex" in tokens or "none" in tokens
        out.nofollow = out.nofollow or "nofollow" in tokens or "none" in tokens
    canon = doc.xpath(f"//link[{_LOWER.format('@rel')}='canonical']/@href")
    if canon:
        out.canonical = urljoin(base, str(canon[0]).strip())
    for anchor in doc.xpath("//a[@href]"):
        if "nofollow" in (anchor.get("rel") or "").lower().split():
            continue
        out.links.append(urljoin(base, anchor.get("href", "").strip()))
        if len(out.links) >= MAX_LINKS:
            break
    return out
