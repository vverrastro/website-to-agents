import uuid

import pytest
from sqlalchemy import update
from wta_agent_runtime.retrieval import Retriever, fts_terms, rrf_fuse
from wta_common.models import Page
from wta_indexing.embeddings import FakeEmbeddingProvider
from wta_indexing.pipeline import EmbeddingSpaceMismatch
from wta_indexing.testing import CONTACT, PRICING, SF, SeedPage, seed_agent

pytestmark = pytest.mark.integration


def test_rrf_fuse_rewards_agreement_between_rankings() -> None:
    a, b, c = (uuid.uuid4() for _ in range(3))
    fused = rrf_fuse([[a, b, c], [c, a]], k=60)
    assert [i for i, _ in fused] == [a, c, b]
    assert fused[0][1] > fused[1][1] > fused[2][1]


def test_fts_terms_only_emit_safe_words() -> None:
    assert fts_terms("What does the Pro plan cost?") == ["pro", "plan", "cost"]
    assert fts_terms("Come posso contattare il supporto?") == ["posso", "contattare", "supporto"]
    assert fts_terms("'; DROP TABLE x; -- & | ! ( ) :* <->") == ["drop", "table"]
    assert fts_terms("a b") == [] and len(fts_terms(" ".join(f"word{i}" for i in range(50)))) == 12


async def test_most_relevant_chunk_ranks_first(session_factory: SF) -> None:
    seeded = await seed_agent(session_factory, [PRICING, CONTACT])
    retriever = Retriever(seeded.embedder)
    async with session_factory() as s:
        hits = await retriever.retrieve(
            s, seeded.agent_id, "how much does the Pro plan cost per month"
        )
    assert hits and hits[0].url == "https://acme.test/pricing"
    assert hits[0].heading_path == ("Pricing",) and hits[0].title == "Acme Pricing"
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)

    async with session_factory() as s:
        phone = await retriever.retrieve(s, seeded.agent_id, "phone number of the office")
    assert phone[0].url == "https://acme.test/contact"


async def test_other_agents_data_is_never_returned(session_factory: SF) -> None:
    mine = await seed_agent(session_factory, [CONTACT], name="mine")
    other = await seed_agent(session_factory, [PRICING], name="other")
    retriever = Retriever(mine.embedder)
    async with session_factory() as s:
        hits = await retriever.retrieve(s, mine.agent_id, "Pro plan price enterprise sales")
        theirs = await retriever.retrieve(s, other.agent_id, "Pro plan price enterprise sales")
    assert all(h.url == "https://acme.test/contact" for h in hits)
    assert {h.url for h in theirs} == {"https://acme.test/pricing"}


async def test_non_indexed_pages_are_excluded(session_factory: SF) -> None:
    seeded = await seed_agent(
        session_factory,
        [PRICING, SeedPage(CONTACT.url, CONTACT.title, CONTACT.sections, "removed")],
    )
    retriever = Retriever(seeded.embedder)
    async with session_factory() as s:
        hits = await retriever.retrieve(s, seeded.agent_id, "phone office contact")
    assert "https://acme.test/contact" not in {h.url for h in hits}
    async with session_factory() as s, s.begin():
        await s.execute(update(Page).values(status="excluded"))
    async with session_factory() as s:
        assert await retriever.retrieve(s, seeded.agent_id, "pricing plan") == []


async def test_top_k_and_context_budget(session_factory: SF) -> None:
    pages = [
        SeedPage(
            f"https://acme.test/p{i}", f"P{i}", [((f"H{i}",), f"shared topic words {i} " * 40)]
        )
        for i in range(8)
    ]
    seeded = await seed_agent(session_factory, pages)
    async with session_factory() as s:
        assert (
            len(
                await Retriever(seeded.embedder, top_k=3).retrieve(
                    s, seeded.agent_id, "shared topic"
                )
            )
            == 3
        )
        tight = Retriever(seeded.embedder, top_k=8, context_chars=1000, max_chunk_chars=600)
        hits = await tight.retrieve(s, seeded.agent_id, "shared topic")
    assert 1 <= len(hits) <= 2 and all(len(h.text) <= 600 for h in hits)


async def test_empty_index_unknown_query_and_hostile_syntax(session_factory: SF) -> None:
    empty = await seed_agent(session_factory, [])
    retriever = Retriever(empty.embedder)
    async with session_factory() as s:
        assert await retriever.retrieve(s, empty.agent_id, "anything") == []
        assert await retriever.retrieve(s, empty.agent_id, "   ") == []
    seeded = await seed_agent(session_factory, [PRICING])
    async with session_factory() as s:
        hits = await Retriever(seeded.embedder).retrieve(
            s, seeded.agent_id, "pricing'); DROP TABLE chunks; -- & | ! ( ) :* <-> \\"
        )
    assert hits  # no SQL/tsquery error, still answers from the vector side
    async with session_factory() as s:
        assert await Retriever(seeded.embedder).retrieve(s, seeded.agent_id, "pricing")


async def test_no_active_space_returns_nothing_and_model_mismatch_is_refused(
    session_factory: SF,
) -> None:
    seeded = await seed_agent(session_factory, [PRICING])
    other = FakeEmbeddingProvider()
    other.model = "other-model"
    async with session_factory() as s:
        with pytest.raises(EmbeddingSpaceMismatch):
            await Retriever(other).retrieve(s, seeded.agent_id, "pricing")
