# Thin wrappers. Every target also works without make (commands shown in CLAUDE.md).
COMPOSE := docker compose --env-file .env -f infra/docker-compose.yml
PY      := uv run --no-project python

.PHONY: help e2e e2e-down up down logs ps lint fmt typecheck web-dev spikes

help:
	@echo "e2e        build + start stack, run end-to-end checks (stack left running)"
	@echo "e2e-down   same, then stop the stack"
	@echo "up/down    start/stop the stack;  logs, ps"
	@echo "lint       ruff + eslint;  fmt: ruff format;  typecheck: mypy + tsc"

.env:
	cp .env.example .env

e2e: .env
	$(PY) scripts/e2e.py

e2e-down: .env
	$(PY) scripts/e2e.py --down

up: .env
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

lint:
	uv run ruff check .
	uv run ruff format --check .
	pnpm lint

fmt:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy packages/domain/src packages/engines/src packages/library/src packages/agents/src apps/api/src apps/worker/src
	pnpm typecheck

web-dev:
	pnpm dev
