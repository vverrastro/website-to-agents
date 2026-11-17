"""The same fixtures are validated by Zod in packages/agent-protocol (vitest): the two
implementations of the contract must agree on every valid and invalid payload."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from wta_common.protocol import (
    AgentPublicConfig,
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    UiDocument,
)

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "agent-protocol" / "fixtures"
MODELS: dict[str, type[BaseModel]] = {
    "ui_document": UiDocument,
    "chat_request": ChatRequest,
    "chat_response": ChatResponse,
    "error_response": ErrorResponse,
    "agent_config": AgentPublicConfig,
}
FILES = sorted(FIXTURES.glob("*.json"))


def test_fixtures_exist() -> None:
    assert len(FILES) > 60
    kinds = {json.loads(f.read_text())["valid"] for f in FILES}
    assert kinds == {True, False}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_fixture_agrees_with_pydantic(path: Path) -> None:
    fixture: dict[str, Any] = json.loads(path.read_text())
    model = MODELS[fixture["schema"]]
    try:
        model.model_validate(fixture["payload"])
        accepted = True
    except ValidationError:
        accepted = False
    assert accepted is fixture["valid"], fixture["description"]
