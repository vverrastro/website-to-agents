import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from wta_common.protocol import ChatRequest, ChatResponse, UiDocument


def doc(root: dict[str, Any], version: str = "1") -> dict[str, Any]:
    return {"version": version, "root": root}


LINK = {"type": "link", "label": "Pricing", "url": "https://acme.test/pricing"}


def test_valid_ui_tree() -> None:
    tree = doc(
        {
            "type": "card",
            "title": "Related",
            "children": [
                {"type": "text", "text": "hello"},
                {"type": "list", "ordered": False, "items": [LINK]},
                {
                    "type": "button",
                    "label": "More",
                    "action": {"type": "send_message", "text": "tell me more"},
                },
                {
                    "type": "button",
                    "label": "Open",
                    "action": {"type": "open_url", "url": "https://acme.test/x"},
                },
            ],
        }
    )
    assert UiDocument.model_validate(tree).root.type == "card"


@pytest.mark.parametrize(
    "bad",
    [
        doc({"type": "script", "src": "https://evil.test/x.js"}),  # unknown component
        doc({"type": "html", "html": "<img src=x onerror=alert(1)>"}),
        doc({"type": "text", "text": "x", "onclick": "alert(1)"}),  # unknown property
        doc({"type": "text", "text": "x", "style": "position:fixed"}),
        doc({"type": "link", "label": "x", "url": "javascript:alert(1)"}),
        doc({"type": "link", "label": "x", "url": "data:text/html,<script>1</script>"}),
        doc({"type": "link", "label": "x", "url": "file:///etc/passwd"}),
        doc({"type": "link", "label": "x", "url": "https://user:pw@acme.test/"}),
        doc({"type": "link", "label": "", "url": "https://acme.test/"}),
        doc({"type": "button", "label": "x", "action": {"type": "eval", "code": "1"}}),
        doc(
            {"type": "button", "label": "x", "action": {"type": "open_url", "url": "javascript:1"}}
        ),
        doc({"type": "button", "label": "x", "action": {"type": "fetch", "url": "https://x.test"}}),
        doc({"type": "text", "text": "x"}, version="2"),
        {"version": "1"},
        {"version": "1", "root": None},
    ],
)
def test_malicious_or_malformed_ui_is_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        UiDocument.model_validate(bad)


def nest(depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": "leaf"}
    for _ in range(depth):
        node = {"type": "card", "children": [node]}
    return node


def test_depth_and_size_are_bounded() -> None:
    UiDocument.model_validate(doc(nest(3)))  # depth 4 incl. leaf: allowed
    with pytest.raises(ValidationError):
        UiDocument.model_validate(doc(nest(4)))
    wide = {
        "type": "card",
        "children": [
            {"type": "list", "items": [{"type": "text", "text": "t"}] * 10} for _ in range(5)
        ],
    }  # 1 + 5 + 50 nodes > 40
    with pytest.raises(ValidationError):
        UiDocument.model_validate(doc(wide))


def test_chat_request_is_strict() -> None:
    assert ChatRequest.model_validate({"message": "hi"}).conversation_id is None
    cid = str(uuid.uuid4())
    assert (
        str(ChatRequest.model_validate({"message": "hi", "conversation_id": cid}).conversation_id)
        == cid
    )
    for bad in (
        {"message": ""},
        {"message": "   "},
        {"message": "x" * 2001},
        {"message": "hi", "role": "system"},
        {"message": "hi", "conversation_id": "not-a-uuid"},
        {},
    ):
        with pytest.raises(ValidationError):
            ChatRequest.model_validate(bad)


def test_response_round_trip_and_defaults() -> None:
    resp = ChatResponse(
        conversation_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        answer="ok",
        sources=[{"id": "s1", "title": "T", "url": "https://acme.test/"}],  # type: ignore[list-item]
    )
    data = resp.model_dump(mode="json")
    assert data["protocol_version"] == "1" and data["ui"] is None and data["actions"] == []
    assert ChatResponse.model_validate(data) == resp
    with pytest.raises(ValidationError):
        ChatResponse.model_validate({**data, "protocol_version": "9"})
    with pytest.raises(ValidationError):
        ChatResponse.model_validate(
            {**data, "sources": [{"id": "s", "title": "t", "url": "javascript:1"}]}
        )
