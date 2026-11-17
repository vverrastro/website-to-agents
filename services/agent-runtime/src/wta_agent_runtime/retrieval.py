"""Hybrid retrieval: pgvector cosine similarity + PostgreSQL full-text, fused with RRF.

Every query is filtered by agent (and embedding space) *before* ranking, only indexed pages are
eligible, and the result is bounded by top-K and a context character budget.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from wta_common.models import Chunk, ChunkEmbedding, EmbeddingSpace, Page, PageVersion
from wta_indexing.embeddings import EmbeddingProvider
from wta_indexing.pipeline import EmbeddingSpaceMismatch

_WORD = re.compile(r"\w{2,}", re.UNICODE)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    page_id: uuid.UUID
    url: str
    title: str
    heading_path: tuple[str, ...]
    text: str
    score: float


def rrf_fuse(rankings: list[list[uuid.UUID]], k: int = 60) -> list[tuple[uuid.UUID, float]]:
    """Reciprocal rank fusion. Returns (id, score) best first; ties keep first-seen order."""
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: -kv[1])


# The 'simple' text-search config does no stop-word removal, and Postgres ranking has no IDF, so
# function words would match nearly every chunk. Drop the most common ones (en/it/es/fr/de).
_STOPWORD_TEXT = (
    # en
    "a an the of in on at to for and or is are was were be been do does did what which who "
    "whom how when where why can could would should will you your yours i me my we our it its "
    "this that these those with from by as about there any please tell "
    # it
    "il lo la le gli un una uno di da del della dei delle su per con che come cosa dove "
    "quando quali quale chi mi ti ci si sono sei siamo "
    # es / fr / de
    "el los las de en por que como cual donde cuando es son "
    "les des du pour avec qui comment quand est sont "
    "der die das ein eine und oder ist sind wie was wo wann mit von zu"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def fts_terms(query: str, limit: int = 12) -> list[str]:
    """Content words safe to put in a to_tsquery expression (letters/digits/underscore only)."""
    seen: list[str] = []
    for word in _WORD.findall(query.lower()):
        if word not in seen and word not in _STOPWORDS:
            seen.append(word)
    return seen[:limit]


class Retriever:
    def __init__(
        self,
        embedder: EmbeddingProvider,
        *,
        top_k: int = 6,
        candidate_k: int = 20,
        rrf_k: int = 60,
        context_chars: int = 8000,
        max_chunk_chars: int = 1500,
    ) -> None:
        self._embedder = embedder
        self.top_k = top_k
        self.candidate_k = candidate_k
        self.rrf_k = rrf_k
        self.context_chars = context_chars
        self.max_chunk_chars = max_chunk_chars

    async def _active_space(self, session: AsyncSession) -> EmbeddingSpace | None:
        space = (
            await session.execute(select(EmbeddingSpace).where(EmbeddingSpace.active.is_(True)))
        ).scalar_one_or_none()
        if space is None:
            return None
        e = self._embedder
        if (space.provider, space.model, space.dimensions) != (e.provider, e.model, e.dimensions):
            raise EmbeddingSpaceMismatch(
                "Query embeddings must use the index's embedding model; run `make reindex`."
            )
        return space

    async def retrieve(
        self, session: AsyncSession, agent_id: uuid.UUID, query: str
    ) -> list[RetrievedChunk]:
        space = await self._active_space(session)
        if space is None or not query.strip():
            return []
        [qvec] = await self._embedder.embed([query], input_type="query")

        distance = ChunkEmbedding.embedding.cosine_distance(qvec)
        vector_ids = list(
            (
                await session.execute(
                    select(ChunkEmbedding.chunk_id)
                    .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
                    .join(Page, Page.id == Chunk.page_id)
                    .where(
                        ChunkEmbedding.agent_id == agent_id,
                        ChunkEmbedding.embedding_space_id == space.id,
                        Chunk.agent_id == agent_id,
                        Page.agent_id == agent_id,
                        Page.status == "indexed",
                    )
                    .order_by(distance)
                    .limit(self.candidate_k)
                )
            ).scalars()
        )

        rankings = [vector_ids]
        terms = fts_terms(query)
        if terms:
            tsquery = func.to_tsquery("simple", " | ".join(terms))
            rank = func.ts_rank_cd(Chunk.tsv, tsquery)
            rankings.append(
                list(
                    (
                        await session.execute(
                            select(Chunk.id)
                            .join(Page, Page.id == Chunk.page_id)
                            .where(
                                Chunk.agent_id == agent_id,
                                Page.agent_id == agent_id,
                                Page.status == "indexed",
                                Chunk.tsv.op("@@")(tsquery),
                            )
                            .order_by(rank.desc())
                            .limit(self.candidate_k)
                        )
                    ).scalars()
                )
            )

        fused = rrf_fuse(rankings, self.rrf_k)[: self.top_k]
        if not fused:
            return []
        score = dict(fused)
        rows = (
            await session.execute(
                select(Chunk, Page.url, PageVersion.title)
                .join(Page, Page.id == Chunk.page_id)
                .join(PageVersion, PageVersion.id == Chunk.page_version_id)
                .where(Chunk.id.in_(score), Chunk.agent_id == agent_id, Page.agent_id == agent_id)
            )
        ).all()
        ordered = sorted(rows, key=lambda r: -score[r[0].id])

        out: list[RetrievedChunk] = []
        used = 0
        for chunk, url, title in ordered:
            text = chunk.text[: self.max_chunk_chars]
            if out and used + len(text) > self.context_chars:
                break
            used += len(text)
            out.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    page_id=chunk.page_id,
                    url=url,
                    title=title or url,
                    heading_path=tuple(chunk.heading_path),
                    text=text,
                    score=score[chunk.id],
                )
            )
        return out
