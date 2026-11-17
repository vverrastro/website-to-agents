"""robots.txt handling (RFC 9309 semantics for availability, stdlib parser for rules).

Availability: 200 → parse; 404/410/other 4xx → allow all; 401/403 → disallow all (we do not
treat an access-controlled robots.txt as permission); 5xx / network failure → disallow all.
Known limitation: ``urllib.robotparser`` applies the first matching rule rather than RFC 9309's
longest-match rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.robotparser import RobotFileParser

from wta_crawler.fetcher import Fetcher, FetchError


@dataclass
class RobotsPolicy:
    agent: str
    allow_all: bool = True
    disallow_all: bool = False
    reason: str | None = None
    crawl_delay: float | None = None
    sitemaps: list[str] = field(default_factory=list)
    _parser: RobotFileParser | None = None

    def allowed(self, url: str) -> bool:
        if self.disallow_all:
            return False
        if self.allow_all or self._parser is None:
            return True
        return self._parser.can_fetch(self.agent, url)


def agent_token(user_agent: str) -> str:
    return user_agent.split("/")[0].strip() or "*"


def parse_robots(text: str, user_agent: str) -> RobotsPolicy:
    agent = agent_token(user_agent)
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    parser.modified()  # without this, can_fetch() refuses everything
    delay = parser.crawl_delay(agent)
    return RobotsPolicy(
        agent=agent,
        allow_all=False,
        crawl_delay=float(delay) if delay is not None else None,
        sitemaps=list(parser.site_maps() or []),
        _parser=parser,
    )


async def load_robots(fetcher: Fetcher, origin: str, user_agent: str) -> RobotsPolicy:
    agent = agent_token(user_agent)
    try:
        url = origin.rstrip("/") + "/robots.txt"
        res = await fetcher.fetch(url, accept=("text/",), max_bytes=500_000)
    except FetchError as exc:
        return RobotsPolicy(
            agent=agent, allow_all=False, disallow_all=True, reason=f"robots_{exc.reason}"
        )
    if res.status == 200:
        return parse_robots(res.body.decode("utf-8", errors="replace"), user_agent)
    if res.status in (401, 403) or res.status >= 500:
        return RobotsPolicy(
            agent=agent, allow_all=False, disallow_all=True, reason=f"robots_http_{res.status}"
        )
    return RobotsPolicy(agent=agent)  # 404/410/other 4xx: no restrictions published
