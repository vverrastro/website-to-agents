"""Redis-backed abuse controls for public endpoints: per-IP rate limit and per-agent daily quota.
Fails closed: if Redis is unavailable the request is refused rather than served unmetered."""

from __future__ import annotations

import time

from fastapi import Request
from redis.asyncio import Redis
from wta_common.logging import get_logger

from wta_api.errors import ApiError

log = get_logger("wta_api.ratelimit")


def client_ip(request: Request, trusted_hops: int) -> str:
    """Peer address, or with N trusted proxies the Nth address from the right of X-Forwarded-For.
    Never trust XFF unless the deployment says how many proxies append to it."""
    if trusted_hops > 0:
        parts = [
            p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()
        ]
        if len(parts) >= trusted_hops:
            return parts[-trusted_hops]
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self, redis: Redis, per_minute: int, daily_quota: int) -> None:
        self._redis = redis
        self._per_minute = per_minute
        self._daily_quota = daily_quota

    async def check(self, agent_key: str, ip: str) -> None:
        now = time.time()
        minute = int(now // 60)
        day = time.strftime("%Y%m%d", time.gmtime(now))
        ip_key = f"wta:rl:{agent_key}:{ip}:{minute}"
        quota_key = f"wta:quota:{agent_key}:{day}"
        try:
            hits = int(await self._redis.incr(ip_key))
            if hits == 1:
                await self._redis.expire(ip_key, 70)
            if hits > self._per_minute:
                # Rejected requests must not consume the agent's shared daily quota, otherwise a
                # single abusive client could exhaust it for everyone.
                retry = max(1, int(60 - now % 60))
                raise ApiError(
                    429, "rate_limited", "Too many requests. Please slow down.",
                    {"Retry-After": str(retry)},
                )  # fmt: skip
            used = int(await self._redis.incr(quota_key))
            if used == 1:
                await self._redis.expire(quota_key, 90_000)
        except ApiError:
            raise
        except Exception:
            log.exception("ratelimit_backend_unavailable")
            raise ApiError(503, "service_unavailable", "Service temporarily unavailable.") from None
        if used > self._daily_quota:
            raise ApiError(
                429, "quota_exceeded", "This assistant has reached its daily limit.",
                {"Retry-After": "3600"},
            )  # fmt: skip
