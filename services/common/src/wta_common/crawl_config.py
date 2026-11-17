"""Per-agent crawl configuration, validated at the API boundary and again in the worker.
Hard caps keep a misconfigured or hostile agent from running unbounded crawls."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AgentCrawlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_pages: int = Field(50, ge=1, le=500)
    max_depth: int = Field(3, ge=0, le=5)
    min_delay_seconds: float = Field(1.0, ge=0.2, le=30)
    max_duration_seconds: int = Field(600, ge=30, le=3600)
    use_sitemap: bool = True
    # URLs that must be rendered with the Playwright fallback (only if enabled server-side).
    render_urls: list[str] = Field(default_factory=list, max_length=50)
    # Scheduled recrawl; None disables it.
    recrawl_interval_hours: int | None = Field(None, ge=1, le=24 * 30)
