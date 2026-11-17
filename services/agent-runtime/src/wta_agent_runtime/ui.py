"""Server-side construction of the UI tree. The model never emits URLs or components: it can only
name retrieved source ids and short labels; the server maps them to allowlisted components."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from pydantic import ValidationError
from wta_common.protocol import (
    UI_VERSION,
    ButtonNode,
    CardNode,
    LinkNode,
    ListNode,
    SendMessageAction,
    UiDocument,
)

from wta_agent_runtime.retrieval import RetrievedChunk

_CONTROL = re.compile(r"[\x00-\x1f\x7f​-‏  ﻿]")
# A label that looks like a URL could disguise where the link really goes.
_LOOKS_LIKE_URL = re.compile(r"(https?:|www\.|//)", re.IGNORECASE)


def clean_label(text: str, limit: int) -> str:
    return " ".join(_CONTROL.sub(" ", text).split())[:limit]


def build_ui(
    navigation: Sequence[tuple[int, str]],
    follow_ups: Sequence[str],
    by_id: Mapping[int, RetrievedChunk],
) -> UiDocument | None:
    links: list[LinkNode] = []
    seen_urls: set[str] = set()
    for source_id, label in navigation:
        chunk = by_id.get(source_id)
        if chunk is None or chunk.url in seen_urls:
            continue
        text = clean_label(label, 120)
        if not text or _LOOKS_LIKE_URL.search(text):
            text = clean_label(chunk.title, 120)  # never let a label impersonate another URL
        if not text:
            continue
        seen_urls.add(chunk.url)
        try:
            links.append(LinkNode(type="link", label=text, url=chunk.url))
        except ValidationError:
            continue
    buttons: list[ButtonNode] = []
    for follow_up in follow_ups:
        text = clean_label(follow_up, 200)
        if text:
            buttons.append(
                ButtonNode(
                    type="button",
                    label=text[:120],
                    action=SendMessageAction(type="send_message", text=text),
                )
            )
    children: list[ListNode] = []
    if links:
        children.append(ListNode(type="list", items=list(links[:3])))
    if buttons:
        children.append(ListNode(type="list", items=list(buttons[:3])))
    if not children:
        return None
    try:
        return UiDocument(
            version=UI_VERSION,
            root=CardNode(type="card", title="Related", children=list(children)),
        )
    except ValidationError:
        return None
