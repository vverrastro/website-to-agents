"""Agent response protocol v1 (shared by API and widget; mirrored in Zod in agent-protocol).

UI output is untrusted structured data: a strict allowlist of components, bounded depth/size,
http(s)-only URLs, and explicit action types. Unknown component types or properties are
rejected (``extra="forbid"`` + discriminated unions) so consumers fail closed.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = "1"
UI_VERSION = "1"
MAX_UI_DEPTH = 4
MAX_UI_NODES = 40
MAX_MESSAGE_CHARS = 2000


def _check_http_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("url must be http(s)")
    if parts.username is not None or parts.password is not None:
        raise ValueError("url must not contain credentials")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Requests -----------------------------------------------------------------------------


class ChatRequest(_Strict):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    conversation_id: uuid.UUID | None = None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


# --- Actions ------------------------------------------------------------------------------


class OpenUrlAction(_Strict):
    type: Literal["open_url"]
    url: str = Field(max_length=2048)

    _url = field_validator("url")(_check_http_url)


class SendMessageAction(_Strict):
    type: Literal["send_message"]
    text: str = Field(min_length=1, max_length=200)


Action = Annotated[OpenUrlAction | SendMessageAction, Field(discriminator="type")]


# --- UI components (allowlist) --------------------------------------------------------------


class TextNode(_Strict):
    type: Literal["text"]
    text: str = Field(max_length=2000)


class LinkNode(_Strict):
    type: Literal["link"]
    label: str = Field(min_length=1, max_length=120)
    url: str = Field(max_length=2048)

    _url = field_validator("url")(_check_http_url)


class ButtonNode(_Strict):
    type: Literal["button"]
    label: str = Field(min_length=1, max_length=120)
    action: Action


class ListNode(_Strict):
    type: Literal["list"]
    ordered: bool = False
    items: list[UiNode] = Field(max_length=10)


class CardNode(_Strict):
    type: Literal["card"]
    title: str | None = Field(default=None, max_length=120)
    children: list[UiNode] = Field(max_length=10)


UiNode = Annotated[
    TextNode | LinkNode | ButtonNode | ListNode | CardNode, Field(discriminator="type")
]
ListNode.model_rebuild()
CardNode.model_rebuild()


def _measure(node: object, depth: int = 1) -> tuple[int, int]:
    """Return (max depth, node count) of a UI tree."""
    children: list[object] = []
    if isinstance(node, ListNode):
        children = list(node.items)
    elif isinstance(node, CardNode):
        children = list(node.children)
    max_depth, count = depth, 1
    for child in children:
        d, c = _measure(child, depth + 1)
        max_depth, count = max(max_depth, d), count + c
    return max_depth, count


class UiDocument(_Strict):
    version: Literal["1"]
    root: UiNode

    @model_validator(mode="after")
    def _bounded(self) -> UiDocument:
        depth, count = _measure(self.root)
        if depth > MAX_UI_DEPTH or count > MAX_UI_NODES:
            raise ValueError("ui tree too deep or too large")
        return self


# --- Responses ----------------------------------------------------------------------------


class Source(_Strict):
    id: str = Field(max_length=32)
    title: str = Field(max_length=300)
    url: str = Field(max_length=2048)
    section: str | None = Field(default=None, max_length=300)
    excerpt: str | None = Field(default=None, max_length=500)

    _url = field_validator("url")(_check_http_url)


class UsageInfo(_Strict):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ChatResponse(_Strict):
    protocol_version: Literal["1"]  # required on the wire; the server sets PROTOCOL_VERSION
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    answer: str = Field(max_length=8000)
    sources: list[Source] = Field(default_factory=list, max_length=10)
    ui: UiDocument | None = None
    actions: list[Action] = Field(default_factory=list, max_length=5)
    usage: UsageInfo | None = None


class ErrorBody(_Strict):
    code: str
    message: str
    request_id: str | None = None


class ErrorResponse(_Strict):
    error: ErrorBody


class AgentPublicConfig(_Strict):
    protocol_version: Literal["1"]  # required on the wire; the server sets PROTOCOL_VERSION
    name: str
