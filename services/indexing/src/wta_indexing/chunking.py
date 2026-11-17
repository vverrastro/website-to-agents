"""Structure-aware chunking: split by headings first, then by size with overlap."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChunkDraft:
    index: int
    heading_path: tuple[str, ...]
    text: str
    content_hash: str


def chunk_hash(heading_path: tuple[str, ...], text: str) -> str:
    payload = "\x1f".join(heading_path) + "\x1e" + text
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def embedding_input(title: str | None, heading_path: tuple[str, ...], text: str) -> str:
    """Text actually embedded: context (title > headings) + body, so short chunks stay findable."""
    context = " > ".join(p for p in (title, *heading_path) if p)
    return f"{context}\n\n{text}" if context else text


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return text if overlap > 0 else ""
    cut = text[-overlap:]
    space = cut.find(" ")
    return cut[space + 1 :] if 0 <= space < len(cut) - 1 else cut


def _pieces(paragraph: str, max_chars: int) -> list[str]:
    """Break an oversized paragraph on sentences, then hard-slice anything still too long."""
    out: list[str] = []
    buf = ""
    for sentence in _SENTENCE.split(paragraph):
        while len(sentence) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            out.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if buf and len(buf) + 1 + len(sentence) > max_chars:
            out.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}".strip()
    if buf:
        out.append(buf)
    return out


def _split_section(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        # Oversized paragraphs are cut smaller so the overlap carried over still fits.
        piece_size = max(max_chars - overlap, max_chars // 2)
        units.extend([para] if len(para) <= max_chars else _pieces(para, piece_size))
    chunks: list[str] = []
    buf = ""
    for unit in units:
        if buf and len(buf) + 2 + len(unit) > max_chars:
            chunks.append(buf)
            carry = _tail(buf, overlap)
            fits = bool(carry) and len(carry) + 2 + len(unit) <= max_chars
            buf = f"{carry}\n\n{unit}" if fits else unit
        else:
            buf = f"{buf}\n\n{unit}" if buf else unit
    if buf:
        chunks.append(buf)
    return chunks


def chunk_markdown(
    text: str, *, max_chars: int = 1200, overlap_chars: int = 150, min_chars: int = 80
) -> list[ChunkDraft]:
    # 1) sections keyed by heading path
    sections: list[tuple[tuple[str, ...], str]] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        buf.clear()
        if body:
            sections.append((tuple(t for _, t in stack), body))

    for line in text.split("\n"):
        m = _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2)))
        else:
            buf.append(line)
    flush()

    # 2) merge tiny sections into the following one so headings with one line don't become chunks
    merged: list[tuple[tuple[str, ...], str]] = []
    carry = ""
    for path, body in sections:
        body = f"{carry}\n\n{body}".strip() if carry else body
        carry = ""
        if len(body) < min_chars:
            carry = body if not path else f"{path[-1]}: {body}"
            continue
        merged.append((path, body))
    if carry:
        if merged and len(merged[-1][1]) + len(carry) + 2 <= max_chars:
            path, body = merged[-1]
            merged[-1] = (path, f"{body}\n\n{carry}")
        else:
            merged.append(((), carry))

    # 3) size-bound each section
    drafts: list[ChunkDraft] = []
    for path, body in merged:
        for piece in _split_section(body, max_chars, overlap_chars):
            drafts.append(ChunkDraft(len(drafts), path, piece, chunk_hash(path, piece)))
    return drafts
