.PHONY: install up down migrate api worker reindex dashboard widget test lint format typecheck check build
export PATH := $(HOME)/.local/bin:$(PATH)

install:
	uv sync --all-packages
	npm install

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

dashboard:
	npm run dev -w @wta/dashboard

widget:
	npm run dev -w @wta/widget

build:
	npm run build

test:
	uv run pytest
	npm test

lint:
	uv run ruff check .
	uv run ruff format --check .
	npm run lint

format:
	uv run ruff check --fix .
	uv run ruff format .
	npm run format

typecheck:
	uv run mypy services/common/src services/crawler/src services/indexing/src services/agent-runtime/src services/api/src
	npm run typecheck

check: lint typecheck test
