"""Public widget runtime API. Treat every request as hostile.

Only two narrowly scoped operations are exposed: read an agent's public config and send a message
to a *published* agent. Origin allowlists and the public agent id are abuse-reduction controls,
NOT authentication: any non-browser client can omit or forge ``Origin``.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError
from sqlalchemy import select
from wta_common.models import Agent
from wta_common.protocol import AgentPublicConfig, ChatRequest, ChatResponse
from wta_common.settings import Settings

from wta_api.chat_service import handle_chat
from wta_api.errors import ApiError
from wta_api.ratelimit import RateLimiter, client_ip

router = APIRouter(prefix="/public/v1/agents/{public_id}", tags=["public"])
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def normalize_origin(value: str) -> str | None:
    """scheme://host[:port], lowercased; None if it is not a bare http(s) origin."""
    try:
        parts = urlsplit(value.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        return None
    netloc = f"[{host}]" if ":" in host else host
    if port and not (
        (parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
    return f"{parts.scheme}://{netloc}".lower()


def origin_allowed(settings: Settings, agent: Agent, origin: str) -> bool:
    normalized = normalize_origin(origin)
    if normalized is None:
        return False
    if normalized in {normalize_origin(o) for o in agent.allowed_origins}:
        return True
    # Deliberate local-development exception (never active in production).
    host = urlsplit(normalized).hostname or ""
    return settings.app_env == "development" and host in _LOCAL_HOSTS


def cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


async def load_public_agent(request: Request, public_id: str) -> Agent:
    async with request.app.state.session_factory() as session:
        agent: Agent | None = (
            await session.execute(
                select(Agent).where(Agent.public_id == public_id, Agent.published.is_(True))
            )
        ).scalar_one_or_none()
    if agent is None:  # unknown and unpublished are indistinguishable on purpose
        raise ApiError(404, "agent_not_found", "Agent not found.")
    return agent


def check_origin(request: Request, agent: Agent) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return  # non-browser client: nothing to enforce (rate limits still apply)
    if not origin_allowed(request.app.state.settings, agent, origin):
        raise ApiError(403, "origin_not_allowed", "This site is not allowed to use this agent.")
    request.state.cors_origin = origin


async def read_body_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ApiError(413, "payload_too_large", "Request body is too large.")
    buf = bytearray()
    async for chunk in request.stream():  # also bounds chunked uploads with no Content-Length
        buf += chunk
        if len(buf) > limit:
            raise ApiError(413, "payload_too_large", "Request body is too large.")
    return bytes(buf)


@router.options("/chat")
@router.options("/config")
async def preflight(request: Request, public_id: str) -> Response:
    agent = await load_public_agent(request, public_id)
    check_origin(request, agent)
    origin = request.headers.get("origin")
    return Response(status_code=204, headers=cors_headers(origin) if origin else {})


@router.get("/config", response_model=AgentPublicConfig)
async def public_config(request: Request, response: Response, public_id: str) -> AgentPublicConfig:
    agent = await load_public_agent(request, public_id)
    check_origin(request, agent)
    if origin := request.headers.get("origin"):
        response.headers.update(cors_headers(origin))
    return AgentPublicConfig(name=agent.name)


@router.post("/chat", response_model=ChatResponse)
async def public_chat(request: Request, response: Response, public_id: str) -> ChatResponse:
    state = request.app.state
    settings: Settings = state.settings
    agent = await load_public_agent(request, public_id)
    check_origin(request, agent)
    limiter: RateLimiter = state.rate_limiter
    await limiter.check(agent.public_id, client_ip(request, settings.trusted_proxy_hops))

    if (request.headers.get("content-type") or "").split(";")[
        0
    ].strip().lower() != "application/json":
        raise ApiError(415, "unsupported_media_type", "Content-Type must be application/json.")
    body = await read_body_limited(request, settings.public_max_body_bytes)
    try:
        chat_request = ChatRequest.model_validate_json(body)
    except ValidationError:
        raise ApiError(400, "invalid_request", "The request is not valid.") from None

    semaphore: asyncio.Semaphore = state.public_semaphore
    if semaphore.locked():
        raise ApiError(
            503, "busy", "The assistant is busy. Try again shortly.", {"Retry-After": "5"}
        )
    async with semaphore:
        result = await handle_chat(
            settings=settings,
            session_factory=state.session_factory,
            get_runtime=state.get_runtime,
            agent=agent,
            request=chat_request,
        )
    if origin := request.headers.get("origin"):
        response.headers.update(cors_headers(origin))
    return result
