"""Test helper: seed an indexed agent directly in the DB (no crawl) for retrieval/RAG/API tests."""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from wta_common.models import (
    Agent,
    Chunk,
    ChunkEmbedding,
    EmbeddingSpace,
    Page,
    PageVersion,
    Source,
    Workspace,
)

from wta_indexing.chunking import chunk_hash, embedding_input
from wta_indexing.embeddings import FakeEmbeddingProvider

SF = async_sessionmaker[AsyncSession]


@dataclass
class SeedPage:
    url: str
    title: str
    sections: list[tuple[tuple[str, ...], str]]
    status: str = "indexed"


@dataclass
class Seeded:
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    embedder: FakeEmbeddingProvider = field(default_factory=FakeEmbeddingProvider)


PRICING = SeedPage(
    "https://acme.test/pricing",
    "Acme Pricing",
    [
        (("Pricing",), "The Pro plan costs 20 euros per month. The Basic plan is free."),
        (("Pricing", "Enterprise"), "Enterprise pricing is custom. Contact the sales team."),
    ],
)
CONTACT = SeedPage(
    "https://acme.test/contact",
    "Contact us",
    [(("Contact",), "Call our office at 555-0100 or write to hello at acme dot test.")],
)


async def seed_agent(
    sf: SF,
    pages: list[SeedPage],
    *,
    name: str = "Acme",
    embedder: FakeEmbeddingProvider | None = None,
) -> Seeded:
    embedder = embedder or FakeEmbeddingProvider()
    async with sf() as s, s.begin():
        ws = (await s.execute(Workspace.__table__.select())).first()
        if ws is None:
            workspace = Workspace(name="local")
            s.add(workspace)
            await s.flush()
            ws_id = workspace.id
        else:
            ws_id = ws.id
        agent = Agent(workspace_id=ws_id, name=name, public_id=f"pub_{uuid.uuid4().hex[:12]}")
        s.add(agent)
        await s.flush()
        source = Source(workspace_id=ws_id, agent_id=agent.id, url="https://acme.test/")
        s.add(source)
        await s.flush()

        space = (
            await s.execute(EmbeddingSpace.__table__.select().where(EmbeddingSpace.active))
        ).first()
        space_id = space.id if space else None
        if space_id is None:
            new_space = EmbeddingSpace(
                provider=embedder.provider,
                model=embedder.model,
                dimensions=embedder.dimensions,
                config={},
                active=True,
            )
            s.add(new_space)
            await s.flush()
            space_id = new_space.id

        for sp in pages:
            page = Page(
                workspace_id=ws_id,
                agent_id=agent.id,
                source_id=source.id,
                url=sp.url,
                status=sp.status,
            )
            s.add(page)
            await s.flush()
            version = PageVersion(
                workspace_id=ws_id,
                agent_id=agent.id,
                page_id=page.id,
                content_hash=uuid.uuid4().hex,
                text="\n\n".join(t for _, t in sp.sections),
                title=sp.title,
            )
            s.add(version)
            await s.flush()
            for i, (path, text) in enumerate(sp.sections):
                chunk = Chunk(
                    workspace_id=ws_id,
                    agent_id=agent.id,
                    page_id=page.id,
                    page_version_id=version.id,
                    chunk_index=i,
                    heading_path=list(path),
                    text=text,
                    content_hash=chunk_hash(path, text),
                )
                s.add(chunk)
                await s.flush()
                [vec] = await embedder.embed(
                    [embedding_input(sp.title, path, text)], input_type="document"
                )
                s.add(
                    ChunkEmbedding(
                        chunk_id=chunk.id,
                        embedding_space_id=space_id,
                        agent_id=agent.id,
                        embedding=vec,
                    )
                )
        return Seeded(ws_id, agent.id, embedder)
