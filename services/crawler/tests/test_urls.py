import pytest
from wta_crawler.urls import UrlPolicy, normalize_url, same_site

START = "https://example.com/"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HTTPS://Example.COM", "https://example.com/"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("http://example.com:8080/a", "http://example.com:8080/a"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/a/./b/../c", "https://example.com/a/c"),
        ("https://example.com/a/", "https://example.com/a/"),
        ("https://example.com//a///b", "https://example.com/a/b"),
        ("https://example.com/%7Euser", "https://example.com/~user"),
        ("https://example.com/a%2Fb", "https://example.com/a%2Fb"),
        ("https://example.com/?b=2&a=1", "https://example.com/?a=1&b=2"),
        ("https://example.com/?utm_source=x&id=5&fbclid=z", "https://example.com/?id=5"),
        ("https://example.com/?id=5&id=3", "https://example.com/?id=3&id=5"),
        ("https://example.com./a", "https://example.com/a"),
        ("https://bücher.example/a", "https://xn--bcher-kva.example/a"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["mailto:a@b.c", "javascript:alert(1)", "tel:123", "ftp://x.test/", "file:///etc/passwd",
     "https://user:pw@example.com/", "https:///x", ""],
)  # fmt: skip
def test_normalize_rejects(raw: str) -> None:
    assert normalize_url(raw) is None


def test_normalize_relative_with_base() -> None:
    assert normalize_url("../x?utm_medium=a#f", "https://example.com/a/b/c") == (
        "https://example.com/a/x"
    )


def test_non_tracking_params_preserved() -> None:
    url = "https://example.com/p?page=2&lang=it"
    assert normalize_url(url) == "https://example.com/p?lang=it&page=2"


def test_extra_tracking_is_configurable() -> None:
    assert normalize_url("https://e.com/?ref=a&x=1", extra_tracking=frozenset({"ref"})) == (
        "https://e.com/?x=1"
    )


def test_same_site_ignores_www() -> None:
    assert same_site("https://www.example.com/a", "https://example.com/b")
    assert not same_site("https://blog.example.com/", "https://example.com/")
    assert not same_site("https://evil.com/", "https://example.com/")


@pytest.mark.parametrize(
    "url,reason",
    [
        ("https://other.com/a", "off_site"),
        ("https://example.com/doc.pdf", "non_html_extension:.pdf"),
        ("https://example.com/img/a.PNG", "non_html_extension:.png"),
        ("https://example.com/cart/items", "excluded_path:/cart"),
        ("https://example.com/wp-admin", "excluded_path:/wp-admin"),
        ("https://example.com/a/b/c/d/e/f/g/h/i/j/k", "path_too_deep"),
        ("https://example.com/a/b/a/b/a/b", "repeating_path_segments"),
        ("https://example.com/p?a=1&b=2&c=3&d=4", "too_many_query_params"),
        ("https://example.com/p?sort=price", "query_param_denied:sort"),
        ("https://example.com/events?month=3", "query_param_denied:month"),
    ],
)
def test_policy_skips_with_reason(url: str, reason: str) -> None:
    assert UrlPolicy().check(url, START) == reason


@pytest.mark.parametrize(
    "url",
    ["https://example.com/", "https://www.example.com/about", "https://example.com/blog?page=2",
     "https://example.com/cartography"],
)  # fmt: skip
def test_policy_allows(url: str) -> None:
    assert UrlPolicy().check(url, START) is None
