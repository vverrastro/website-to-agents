from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from wta_common.models import Agent, ChatMessage, Conversation, Workspace
from wta_common.retention import purge_expired_conversations

pytestmark = pytest.mark.integration


async def test_old_conversations_are_purged_with_their_messages(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as s, s.begin():
        ws = Workspace(name="w")
        s.add(ws)
        await s.flush()
        agent = Agent(workspace_id=ws.id, name="a", public_id="pub_x")
        s.add(agent)
        await s.flush()
        old, fresh = (Conversation(workspace_id=ws.id, agent_id=agent.id) for _ in range(2))
        s.add_all([old, fresh])
        await s.flush()
        for conv in (old, fresh):
            s.add(
                ChatMessage(
                    workspace_id=ws.id, agent_id=agent.id, conversation_id=conv.id,
                    role="user", content="hello",
                )
            )  # fmt: skip
        await s.execute(
            update(Conversation)
            .where(Conversation.id == old.id)
            .values(last_activity_at=datetime.now(UTC) - timedelta(days=40))
        )
        fresh_id = fresh.id

    assert await purge_expired_conversations(session_factory, 0) == 0  # disabled
    assert await purge_expired_conversations(session_factory, 30) == 1
    async with session_factory() as s:
        assert (await s.execute(select(Conversation.id))).scalars().all() == [fresh_id]
        assert (await s.execute(select(func.count()).select_from(ChatMessage))).scalar_one() == 1
    assert await purge_expired_conversations(session_factory, 30) == 0
