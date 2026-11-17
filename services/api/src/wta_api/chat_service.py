"""Chat use-case shared by the public widget endpoint and the admin preview."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wta_agent_runtime.llm import LLMError, Message
from wta_agent_runtime.rag import AgentContext, AgentRuntime
from wta_common.logging import get_logger
from wta_common.models import Agent, ChatMessage, Conversation
from wta_common.protocol import PROTOCOL_VERSION, ChatRequest, ChatResponse
from wta_common.settings import ConfigError, Settings
from wta_indexing.embeddings import EmbeddingError
from wta_indexing.pipeline import EmbeddingSpaceMismatch

from wta_api.errors import ApiError

log = get_logger("wta_api.chat")


def build_runtime(settings: Settings) -> AgentRuntime:
    from wta_agent_runtime.llm import build_llm_provider
    from wta_agent_runtime.retrieval import Retriever
    from wta_indexing.embeddings import build_embedding_provider

    llm = build_llm_provider(settings)
    embedder = build_embedding_provider(settings)
    retriever = Retriever(
        embedder, top_k=settings.retrieval_top_k, context_chars=settings.retrieval_context_chars
    )
    return AgentRuntime(llm, retriever, history_turns=settings.history_turns)


async def _conversation(
    session: AsyncSession, agent: Agent, conversation_id: uuid.UUID | None
) -> tuple[Conversation, list[Message]]:
    if conversation_id is not None:
        conv = (
            await session.execute(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.agent_id == agent.id,
                    Conversation.workspace_id == agent.workspace_id,
                )
            )
        ).scalar_one_or_none()
        if conv is not None:
            rows = (
                await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.conversation_id == conv.id, ChatMessage.agent_id == agent.id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(12)
                )
            ).scalars()
            history = [Message(m.role, m.content) for m in reversed(list(rows))]  # type: ignore[arg-type]
            return conv, history
    # Unknown id (or another agent's id): start a fresh conversation, never reveal the difference.
    conv = Conversation(workspace_id=agent.workspace_id, agent_id=agent.id)
    session.add(conv)
    await session.flush()
    return conv, []


async def handle_chat(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    get_runtime: Callable[[], Awaitable[AgentRuntime]],
    agent: Agent,
    request: ChatRequest,
    detailed_errors: bool = False,
) -> ChatResponse:
    async with session_factory() as session, session.begin():
        conv, history = await _conversation(session, agent, request.conversation_id)
        conv_id = conv.id
    context = AgentContext(agent.id, agent.name, agent.instructions)

    try:
        runtime = await get_runtime()  # may raise ConfigError (missing provider key)
        async with asyncio.timeout(settings.public_request_timeout_seconds):
            async with session_factory() as session:
                result = await runtime.answer(session, context, request.message, history)
    except TimeoutError:
        raise ApiError(504, "timeout", "The assistant took too long to answer.") from None
    except EmbeddingSpaceMismatch as exc:
        detail = str(exc) if detailed_errors else "The assistant is being updated."
        raise ApiError(503, "index_reindex_required", detail) from None
    except (LLMError, EmbeddingError) as exc:
        log.warning("chat_provider_error", code=exc.code, retryable=exc.retryable)
        if exc.retryable:
            raise ApiError(
                503, "assistant_busy", "The assistant is busy. Try again shortly."
            ) from None
        message = (
            f"Provider error: {exc.code}." if detailed_errors else "The assistant is unavailable."
        )
        raise ApiError(502 if not exc.fatal else 503, "assistant_unavailable", message) from None
    except ConfigError as exc:
        message = str(exc) if detailed_errors else "The assistant is not configured."
        raise ApiError(503, "assistant_not_configured", message) from None

    message_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        now = datetime.now(UTC)
        session.add(
            ChatMessage(
                workspace_id=agent.workspace_id, agent_id=agent.id, conversation_id=conv_id,
                role="user", content=request.message, sources=[], created_at=now,
            )
        )  # fmt: skip
        session.add(
            ChatMessage(
                id=message_id, workspace_id=agent.workspace_id, agent_id=agent.id,
                conversation_id=conv_id, role="assistant", content=result.answer,
                sources=[{"title": s.title, "url": s.url} for s in result.sources],
                usage=result.usage.model_dump() if result.usage else None,
                created_at=now + timedelta(milliseconds=1),  # strictly after the question
            )
        )  # fmt: skip
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conv_id)
            .values(last_activity_at=now + timedelta(milliseconds=1))
        )
    return ChatResponse(
        protocol_version=PROTOCOL_VERSION,
        conversation_id=conv_id,
        message_id=message_id,
        answer=result.answer,
        sources=result.sources,
        ui=result.ui,
        usage=result.usage,
    )
