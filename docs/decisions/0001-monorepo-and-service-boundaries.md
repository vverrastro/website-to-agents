# 0001 — Monorepo and service boundaries

Status: accepted

## Context
The product has a dashboard, an embeddable widget, an API, a crawler, an indexing pipeline and an agent runtime. They share contracts (the response protocol, settings, DB models) but must be buildable and runnable independently, and local development must stay easy.

## Decision
- One repository with two workspaces: **uv** for Python (`services/*`, each a separate package with its own `pyproject.toml`, one lockfile) and **npm workspaces** for TypeScript (`apps/*`, `packages/*`, one lockfile).
- Python services are separate *packages* with explicit dependencies (`common` ← `crawler` ← `indexing` ← `agent-runtime` ← `api`) but **not** separate processes: the crawler, indexing and runtime are libraries; the deployable processes are the API (`uvicorn`) and the worker (`arq`). The crawler is DB-free (it yields events); persistence lives in `indexing`.
- The dashboard, widget and API remain distinct applications. The widget is a dependency-light bundle that shares only `agent-protocol`/`widget-sdk` with the dashboard.
- Deviations from the initial suggestion: no `packages/ui` or `packages/shared-types` (no current use; types live in `agent-protocol`); added `services/common` for settings/DB/protocol shared by every service; helper test modules that other packages import live in the package source (`wta_common.testing`, `wta_indexing.testing`) rather than in test directories.
- Package managers: uv and npm (no pnpm), because they are the least extra tooling on a stock machine; lockfiles are committed.

## Consequences
- Logical boundaries are enforced by imports and tests, without the cost of network hops between services; splitting a service into its own process later is mechanical (the interfaces already take plain data).
- The API depends on the whole Python stack, so it is the single place where wiring happens (`wta_api.main.init_state`).
- Two lockfiles/toolchains to keep current; `make check` runs both.
