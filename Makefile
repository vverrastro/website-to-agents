.PHONY: install up down migrate api worker reindex test lint format typecheck check
export PATH := $(HOME)/.local/bin:$(PATH)

install:
	uv sync --all-packages
	@if [ -f package.json ]; then npm install; fi

up:
	docker compose up -d --wait

down:
	docker compose down

migrate:
	uv run alembic -c infrastructure/database/alembic.ini upgrade head

api:
	uv run uvicorn wta_api.main:create_app --factory --host 127.0.0.1 --port 8000 --reload

worker:
	uv run arq wta_indexing.worker.WorkerSettings

reindex:
	uv run python -m wta_indexing.reindex

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy services/common/src services/crawler/src services/indexing/src services/agent-runtime/src services/api/src

check: lint typecheck test
