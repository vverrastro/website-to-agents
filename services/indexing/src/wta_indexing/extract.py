"""Main-content extraction and normalisation. Deterministic; no LLM."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

import lxml.html
import trafilatura
from lxml import etree

MIN_TEXT_CHARS = 40
_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
_BLANKS = re.compile(r"\n{3,}")
# Boilerplate that survives main-content extraction and is volatile/noisy for retrieval.
_BOILERPLATE_LINE = re.compile(
    r"(we use cookies|accept (all )?cookies|cookie (policy|settings|preferences)"
    r"|skip to (main )?content)",
    re.IGNORECASE,
)
_DROP_TAGS = ("script", "style", "noscript", "template", "nav", "header", "footer", "aside", "form")


@dataclass(frozen=True)
class ExtractedPage:
    title: str | None
    description: str | None
    language: str | None
    canonical_url: str | None
    text: str  # normalised markdown-ish text; headings kept as '#'
    headings: list[str] = field(default_factory=list)
    content_hash: str = ""


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH.sub("", text)
    lines = []
    for raw in text.split("\n"):
        line = raw.rstrip().replace(" ", " ")
        if len(line) < 200 and _BOILERPLATE_LINE.search(line):
            continue
        lines.append(line)
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _meta(doc: lxml.html.HtmlElement, *names: str) -> str | None:
    for name in names:
        for xp in (f'//meta[@name="{name}"]/@content', f'//meta[@property="{name}"]/@content'):
            found = doc.xpath(xp)
            if found and str(found[0]).strip():
                return " ".join(str(found[0]).split())
    return None


def _fallback_markdown(doc: lxml.html.HtmlElement) -> str:
    for el in doc.xpath("|".join(f"//{t}" for t in _DROP_TAGS)):
        el.drop_tree()
    parts: list[str] = []
    for el in doc.iter("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote"):
        text = " ".join(el.text_content().split())
        if not text:
            continue
        if el.tag.startswith("h") and len(el.tag) == 2:
            parts.append(f"{'#' * int(el.tag[1])} {text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)


def extract_page(html: bytes | str, url: str) -> ExtractedPage | None:
    """Return the page's main content, or ``None`` if nothing meaningful could be extracted."""
    if not html or not html.strip():
        return None
    try:
        doc = lxml.html.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return None
    title_el = doc.xpath("//title/text()")
    title = (
        _meta(doc, "og:title") or (" ".join(str(title_el[0]).split()) if title_el else None) or None
    )
    canonical = doc.xpath('//link[@rel="canonical"]/@href')
    markup = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
    body = trafilatura.extract(
        markup,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_comments=False,
        include_links=False,
        favor_precision=True,
    )
    if not body or len(body.strip()) < MIN_TEXT_CHARS:
        body = _fallback_markdown(lxml.html.document_fromstring(html))
    text = normalize_text(body)
    if len(text) < MIN_TEXT_CHARS:
        return None
    headings = [m.group(2) for m in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", text, re.MULTILINE)]
    lang = doc.get("lang")
    return ExtractedPage(
        title=title,
        description=_meta(doc, "description", "og:description"),
        language=lang.strip().lower() if lang else None,
        canonical_url=str(canonical[0]).strip() if canonical else None,
        text=text,
        headings=headings,
        content_hash=content_hash(text),
    )
