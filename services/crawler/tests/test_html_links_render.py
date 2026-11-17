from conftest import html
from wta_crawler.html_links import parse_page_links
from wta_crawler.render import needs_rendering, visible_text_length


def test_links_canonical_and_meta() -> None:
    doc = html(
        '<a href="/a">A</a><a href="b#x">B</a><a href="mailto:x@y.z">M</a>'
        '<a rel="nofollow" href="/skip">S</a><a href="https://other.test/o">O</a>',
        head='<link rel="Canonical" href="/canon"><META name="Robots" content="NoIndex, follow">',
    )
    p = parse_page_links(doc, "https://ok.test/dir/page")
    assert p.canonical == "https://ok.test/canon"
    assert p.noindex and not p.nofollow
    assert "https://ok.test/a" in p.links and "https://ok.test/dir/b#x" in p.links
    assert "https://ok.test/skip" not in p.links


def test_base_href_and_nofollow_meta() -> None:
    p = parse_page_links(
        html(
            '<a href="x">x</a>',
            head='<base href="https://ok.test/base/"><meta name="robots" content="nofollow">',
        ),
        "https://ok.test/",
    )
    assert p.links == ["https://ok.test/base/x"] and p.nofollow


def test_garbage_input_is_safe() -> None:
    assert parse_page_links(b"", "https://ok.test/").links == []
    assert parse_page_links(b"\x00\x01<<<", "https://ok.test/").noindex is False


def test_rendering_detection_is_conservative() -> None:
    shell = html('<div id="root"></div><script src="a.js"></script>')
    assert needs_rendering(shell)
    assert needs_rendering(html("<noscript>Please enable JavaScript</noscript>"))
    article = html("<p>" + "real content " * 50 + "</p>" + '<div id="root"></div>')
    assert not needs_rendering(article)  # real text present: never render
    assert not needs_rendering(html("<p>short static page</p>"))  # short but not an SPA shell
    assert visible_text_length(html("<script>var a=1</script><p>hi</p>")) == 2
