"""URL normalisation and crawl filtering. Conservative: never drops params that may change
content, except an explicit tracking allowlist."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

DEFAULT_TRACKING_PARAMS = frozenset(
    {"fbclid", "gclid", "dclid", "msclkid", "yclid", "mc_cid", "mc_eid", "igshid", "_ga", "ref_src"}
)
# Percent-encoded unreserved characters: ALPHA / DIGIT / "-" / "." / "_" / "~"
_UNRESERVED = re.compile(
    r"%(2[dDeE]|5[fF]|7[eE]|3[0-9]|4[1-9a-fA-F]|5[0-9aA]|6[1-9a-fA-F]|7[0-9aA])"
)


def is_tracking_param(name: str, extra: frozenset[str] = frozenset()) -> bool:
    lowered = name.lower()
    return lowered.startswith("utm_") or lowered in DEFAULT_TRACKING_PARAMS or lowered in extra


def _normalise_path(path: str) -> str:
    if not path:
        return "/"
    # Decode only unreserved characters so equivalent URLs compare equal.
    path = _UNRESERVED.sub(lambda m: unquote(m.group(0)), path)
    trailing = path.endswith("/")
    cleaned = posixpath.normpath(path)
    if cleaned.startswith("//"):
        cleaned = "/" + cleaned.lstrip("/")
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    if trailing and not cleaned.endswith("/"):
        cleaned += "/"
    return cleaned


def normalize_url(
    url: str, base: str | None = None, *, extra_tracking: frozenset[str] = frozenset()
) -> str | None:
    """Return a canonical form of ``url`` or ``None`` if it is not a crawlable http(s) URL."""
    try:
        joined = urljoin(base, url.strip()) if base else url.strip()
        parts = urlsplit(joined)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    try:
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    scheme = parts.scheme.lower()
    netloc = host
    if ":" in host:  # IPv6 literal
        netloc = f"[{host}]"
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not is_tracking_param(k, extra_tracking)
    ]
    query = urlencode(sorted(pairs), quote_via=quote)
    return urlunsplit((scheme, netloc, _normalise_path(parts.path), query, ""))


def host_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def same_site(a: str, b: str) -> bool:
    """Same host, ignoring a leading ``www.``."""
    return host_key(a) == host_key(b)


DEFAULT_EXCLUDED_EXTENSIONS = frozenset(
    {
        ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif",
        ".css", ".js", ".mjs", ".map", ".json", ".xml", ".rss", ".atom",
        ".zip", ".gz", ".tar", ".tgz", ".rar", ".7z", ".dmg", ".exe", ".msi", ".apk",
        ".mp3", ".mp4", ".mov", ".avi", ".webm", ".wav", ".ogg",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
        ".woff", ".woff2", ".ttf", ".eot", ".otf",
    }
)  # fmt: skip
DEFAULT_EXCLUDED_PATH_PREFIXES = (
    "/wp-admin", "/wp-json", "/wp-login", "/cart", "/checkout", "/login", "/logout",
    "/signin", "/signup", "/account", "/admin", "/cdn-cgi",
)  # fmt: skip
DEFAULT_SKIPPED_QUERY_PARAMS = frozenset(
    {
        "sort", "orderby", "order", "filter", "filters", "view", "sessionid", "sid",
        "phpsessid", "jsessionid", "replytocom", "date", "month", "year", "cal", "calendar",
    }
)  # fmt: skip


@dataclass(frozen=True)
class UrlPolicy:
    """Decides whether a normalised URL should be crawled; returns an audit reason if not."""

    excluded_extensions: frozenset[str] = DEFAULT_EXCLUDED_EXTENSIONS
    excluded_path_prefixes: tuple[str, ...] = DEFAULT_EXCLUDED_PATH_PREFIXES
    skipped_query_params: frozenset[str] = DEFAULT_SKIPPED_QUERY_PARAMS
    max_query_params: int = 3
    max_path_depth: int = 10
    max_repeated_segment: int = 2
    extra_tracking: frozenset[str] = field(default_factory=frozenset)

    def check(self, url: str, start_url: str) -> str | None:
        if not same_site(url, start_url):
            return "off_site"
        parts = urlsplit(url)
        path = parts.path.lower()
        ext = posixpath.splitext(path)[1]
        if ext in self.excluded_extensions:
            return f"non_html_extension:{ext}"
        for prefix in self.excluded_path_prefixes:
            if path == prefix or path.startswith(prefix + "/"):
                return f"excluded_path:{prefix}"
        segments = [s for s in path.split("/") if s]
        if len(segments) > self.max_path_depth:
            return "path_too_deep"
        if any(segments.count(s) > self.max_repeated_segment for s in set(segments)):
            return "repeating_path_segments"
        params = parse_qsl(parts.query, keep_blank_values=True)
        if len(params) > self.max_query_params:
            return "too_many_query_params"
        for name, _ in params:
            if name.lower() in self.skipped_query_params:
                return f"query_param_denied:{name.lower()}"
        return None
