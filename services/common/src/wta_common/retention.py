"""Data-retention hooks. Conversations hold no IP or identifier; they are still purged."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from wta_common.models import Conversation


async def purge_expired_conversations(
    session_factory: async_sessionmaker[AsyncSession], retention_days: int
) -> int:
    """Delete conversations (and, by cascade, their messages) idle for longer than the window.
    Returns the number of conversations removed. ``retention_days <= 0`` disables purging."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    async with session_factory() as session, session.begin():
        result = await session.execute(
            delete(Conversation).where(Conversation.last_activity_at < cutoff)
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]
