# DoMe developer entry points. Each component is an independent package (own lockfile);
# this file only orchestrates. See README.md for first-run instructions.

PG_PORT ?= 54329
PG_DIR  ?= $(CURDIR)/.pgdata

.PHONY: help setup setup-python setup-node test test-python test-node lint typecheck fixtures \
        db-start db-stop db-create api agent dev-idp pwa ext clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

setup: setup-python setup-node ## Install every component's dependencies

setup-python: ## Create venvs for shared/python, cloud-api, pc-agent, tools/dev-idp, tests
	cd shared/python && uv sync --extra dev
	cd cloud-api && uv sync --extra dev
	cd pc-agent && uv sync --extra dev
	cd tools/dev-idp && uv sync
	cd tests && uv sync

setup-node: ## Install shared/ts, mobile-app, browser-extension
	cd shared/ts && pnpm install && pnpm gen:types
	cd mobile-app && pnpm install
	cd browser-extension && pnpm install

test: test-python test-node ## Run every unit/integration test suite

test-python:
	cd shared/python && uv run pytest -q
	cd cloud-api && uv run pytest -q
	cd pc-agent && uv run pytest -q
	cd tests && uv run pytest -q

test-node:
	cd shared/ts && pnpm test
	cd mobile-app && pnpm test
	cd browser-extension && pnpm test

typecheck: ## Type-check Python (mypy) and TypeScript (tsc)
	cd shared/ts && pnpm typecheck
	cd mobile-app && pnpm typecheck
	cd browser-extension && pnpm typecheck
	cd cloud-api && uv run mypy dome_api tests
	cd pc-agent && uv run mypy dome_agent

lint: ## Ruff + eslint
	cd cloud-api && uv run ruff check . && uv run ruff format --check .
	cd pc-agent && uv run ruff check . && uv run ruff format --check .
	cd shared/python && uv run ruff check .
	cd mobile-app && pnpm lint
	cd browser-extension && pnpm lint

fixtures: ## Regenerate cross-language signing fixtures (both directions)
	cd shared/python && uv run python scripts/make_fixtures.py
	cd shared/ts && pnpm fixtures

db-start: ## Start a local PostgreSQL 16 cluster for development/tests (needs initdb once)
	@test -d $(PG_DIR) || (initdb -D $(PG_DIR) -U dome --auth=trust >/dev/null && echo "initialised $(PG_DIR)")
	pg_ctl -D $(PG_DIR) -o "-p $(PG_PORT) -k /tmp" -l $(PG_DIR)/pg.log start

db-stop:
	pg_ctl -D $(PG_DIR) stop

db-create: ## Create dome_dev and dome_test databases
	-psql -h /tmp -p $(PG_PORT) -U dome -d postgres -c "create database dome_dev"
	-psql -h /tmp -p $(PG_PORT) -U dome -d postgres -c "create database dome_test"

dev-idp: ## Run the development-only OIDC issuer (never deployed)
	cd tools/dev-idp && uv run dome-dev-idp

api: ## Run the cloud API with reload
	cd cloud-api && uv run dome-api --reload

agent: ## Run the PC agent in development mode (non-Windows adapters report PLATFORM_UNSUPPORTED)
	cd pc-agent && uv run dome-agent

pwa: ## Run the mobile PWA dev server
	cd mobile-app && pnpm dev

ext: ## Build the browser extension (unpacked, development ID)
	cd browser-extension && pnpm build

clean:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	rm -rf shared/ts/node_modules mobile-app/node_modules browser-extension/node_modules
	rm -rf shared/python/.venv cloud-api/.venv pc-agent/.venv tools/dev-idp/.venv tests/.venv
