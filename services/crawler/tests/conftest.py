from collections.abc import Callable

import httpx
import pytest
from wta_crawler.fetcher import Fetcher, FetchLimits

PUBLIC_IP = "93.184.216.34"


async def public_resolver(host: str, port: int) -> list[str]:
    """Everything resolves to a public address except explicit internal names."""
    internal = {"internal.test": "10.0.0.8", "meta.test": "169.254.169.254"}
    return [internal.get(host, PUBLIC_IP)]


@pytest.fixture
def make_fetcher() -> Callable[..., Fetcher]:
    def factory(handler: Callable[[httpx.Request], httpx.Response], **limits: object) -> Fetcher:
        return Fetcher(
            user_agent="TestBot/1.0",
            resolver=public_resolver,
            transport=httpx.MockTransport(handler),
            limits=FetchLimits(**limits),  # type: ignore[arg-type]
        )

    return factory


def html(body: str, head: str = "") -> bytes:
    return f"<!doctype html><html><head><title>t</title>{head}</head><body>{body}</body></html>".encode()
