import gzip
from datetime import UTC, datetime

import httpx
import pytest
from wta_crawler.sitemap import (
    SitemapError,
    collect_sitemap_entries,
    parse_sitemap,
    parse_w3c_datetime,
)

URLSET = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <!-- comment -->
  <url><loc>https://ok.test/a</loc><lastmod>2024-05-01</lastmod></url>
  <url><loc> https://ok.test/b </loc><lastmod>2024-05-01T10:00:00+02:00</lastmod></url>
  <url><loc>https://ok.test/c</loc><lastmod>garbage</lastmod></url>
  <url><lastmod>2024-01-01</lastmod></url>
</urlset>"""


def test_parse_urlset() -> None:
    r = parse_sitemap(URLSET)
    assert [e.url for e in r.entries] == [
        "https://ok.test/a",
        "https://ok.test/b",
        "https://ok.test/c",
    ]
    assert r.entries[0].lastmod == datetime(2024, 5, 1, tzinfo=UTC)
    assert r.entries[1].lastmod is not None and r.entries[2].lastmod is None


def test_parse_index_and_gzip() -> None:
    index = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://ok.test/s1.xml</loc></sitemap></sitemapindex>"""
    assert parse_sitemap(gzip.compress(index)).child_sitemaps == ["https://ok.test/s1.xml"]


@pytest.mark.parametrize("data", [b"<html/>", b"not xml", b"<urlset><url>"])
def test_invalid_sitemaps_raise(data: bytes) -> None:
    with pytest.raises(SitemapError):
        parse_sitemap(data)


def test_xxe_and_entity_expansion_are_inert() -> None:
    xxe = b"""<?xml version="1.0"?>
    <!DOCTYPE urlset [<!ENTITY x SYSTEM "file:///etc/passwd">]>
    <urlset><url><loc>https://ok.test/&x;</loc></url></urlset>"""
    try:
        r = parse_sitemap(xxe)
        assert all("root:" not in e.url for e in r.entries)
    except SitemapError:
        pass
    bomb = b"""<?xml version="1.0"?>
    <!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
    <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>
    <urlset><url><loc>https://ok.test/&c;</loc></url></urlset>"""
    try:
        r = parse_sitemap(bomb)
        assert all(len(e.url) < 200 for e in r.entries)
    except SitemapError:
        pass


def test_decompression_bomb_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("wta_crawler.sitemap.MAX_DECOMPRESSED_BYTES", 1000)
    with pytest.raises(SitemapError, match="too_large"):
        parse_sitemap(gzip.compress(b"<urlset>" + b" " * 100_000 + b"</urlset>"))


def test_w3c_datetime() -> None:
    assert parse_w3c_datetime("2024-05-01T10:00:00Z") == datetime(2024, 5, 1, 10, tzinfo=UTC)
    assert parse_w3c_datetime("") is None and parse_w3c_datetime("nope") is None


async def test_collect_walks_nested_indexes_with_limits(make_fetcher) -> None:  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        h = {"content-type": "application/xml"}
        if req.url.path == "/sitemap.xml":
            body = (
                b'<sitemapindex xmlns="x"><sitemap><loc>https://ok.test/p.xml</loc></sitemap>'
                b"<sitemap><loc>https://evil.test/e.xml</loc></sitemap></sitemapindex>"
            )
        elif req.url.path == "/p.xml":
            body = b'<urlset xmlns="x"><url><loc>https://ok.test/page1</loc></url></urlset>'
        else:
            return httpx.Response(404, headers=h)
        return httpx.Response(200, content=body, headers=h)

    async with make_fetcher(handler) as f:
        entries, errors = await collect_sitemap_entries(
            f, ["https://ok.test/sitemap.xml"], start_url="https://ok.test/"
        )
    assert [e.url for e in entries] == ["https://ok.test/page1"]
    assert ("https://evil.test/e.xml", "sitemap_off_site") in errors
