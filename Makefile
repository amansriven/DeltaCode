SHELL := /bin/sh

PYTHON := .venv/bin/python
PIP := .venv/bin/pip
PROCRASTINATE := PYTHONPATH=. $(PYTHON) -m procrastinate --app app.procrastinate_app.procrastinate_app
API_URL ?= http://localhost:8000
LIVE_API_URL ?= https://web-production-e59907.up.railway.app
RAILWAY_ENV ?= production
VERCEL_SCOPE ?=

.DEFAULT_GOAL := help

.PHONY: help setup backend-setup frontend-setup frontend-env sandbox-setup \
	db-up db-down db-logs db-schema api worker frontend-dev frontend-start \
	lint test test-backend test-postgres test-frontend test-sandbox benchmark build health health-live \
	deploy deploy-backend deploy-web deploy-worker deploy-frontend

help:
	@echo "Delta Code development commands"
	@echo ""
	@echo "  make setup             Install backend, frontend, and sandbox dependencies"
	@echo "  make db-up             Start local PostgreSQL"
	@echo "  make db-schema         Apply the Procrastinate database schema"
	@echo "  make api               Run the FastAPI server on :8000"
	@echo "  make worker            Run the background comparison worker"
	@echo "  make frontend-dev      Run Next.js on :3000 against LIVE_API_URL"
	@echo "  make lint              Lint backend and frontend"
	@echo "  make test              Run every backend and frontend check"
	@echo "  make test-sandbox      Type-check and test the Sandbox Worker"
	@echo "  make test-postgres     Run SQL tests against a disposable PostgreSQL container"
	@echo "  make benchmark         Run the labeled repository-impact release gate"
	@echo "  make build             Build the production frontend"
	@echo "  make health            Check the local backend health endpoint"
	@echo "  make health-live       Check the hosted backend health endpoint"
	@echo "  make deploy-backend    Deploy Railway web and worker services"
	@echo "  make deploy-frontend   Deploy the frontend to Vercel production"
	@echo "  make deploy            Deploy backend and frontend"

setup: backend-setup frontend-setup frontend-env sandbox-setup

backend-setup:
	python3 -m venv .venv
	$(PIP) install -e ".[dev]"

frontend-setup:
	cd frontend && npm ci

sandbox-setup:
	cd sandbox-worker && npm ci

frontend-env:
	@test -f frontend/.env || { \
		echo "NEXT_PUBLIC_DELTA_CODE_API_URL=$(LIVE_API_URL)" > frontend/.env; \
		echo "Created frontend/.env"; \
	}

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

db-logs:
	docker compose logs -f postgres

db-schema:
	$(PROCRASTINATE) schema --apply

api:
	@if [ -f .env.local ]; then \
		$(PYTHON) -m dotenv -f .env.local run -- \
		$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000; \
	else \
		$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000; \
	fi

worker:
	@if [ -f .env.local ]; then \
		$(PYTHON) -m dotenv -f .env.local run -- \
		$(PYTHON) -m procrastinate --app app.procrastinate_app.procrastinate_app worker; \
	else \
		$(PROCRASTINATE) worker; \
	fi

frontend-dev: frontend-env
	cd frontend && NEXT_PUBLIC_DELTA_CODE_API_URL="$(LIVE_API_URL)" npm run dev

frontend-start:
	cd frontend && npm run start

lint:
	$(PYTHON) -m ruff check app tests
	cd frontend && npm run lint

test: lint test-backend test-frontend test-sandbox benchmark

test-backend:
	$(PYTHON) -m pytest -q

test-postgres:
	docker run --rm -d --name delta-code-test-postgres -e POSTGRES_USER=deltacode \
		-e POSTGRES_PASSWORD=deltacode -e POSTGRES_DB=deltacode -p 127.0.0.1:55432:5432 \
		postgres:16-alpine
	@trap 'docker stop delta-code-test-postgres >/dev/null' EXIT; \
	until docker exec delta-code-test-postgres pg_isready -U deltacode -d deltacode -h 127.0.0.1 >/dev/null 2>&1; do sleep 1; done; \
	TEST_DATABASE_URL=postgresql://deltacode:deltacode@127.0.0.1:55432/deltacode \
		$(PYTHON) -m pytest -q tests/test_ingestion_postgres.py

test-frontend:
	cd frontend && npm test

test-sandbox:
	cd sandbox-worker && npm test && npm run check

benchmark:
	$(PYTHON) -m app.hardening.benchmark benchmarks/repository-impact-v1.json

build:
	cd frontend && npm run build

health:
	curl --fail --silent --show-error "$(API_URL)/health"
	@echo

health-live:
	curl --fail --silent --show-error "$(LIVE_API_URL)/health"
	@echo

deploy: deploy-backend deploy-frontend

deploy-backend: deploy-web deploy-worker

deploy-web:
	railway up --service web --environment "$(RAILWAY_ENV)" --ci -m "Deploy Delta Code web"

deploy-worker:
	railway up --service worker --environment "$(RAILWAY_ENV)" --ci -m "Deploy Delta Code worker"

deploy-frontend: build
	npx vercel deploy --prod $(if $(VERCEL_SCOPE),--scope "$(VERCEL_SCOPE)",)
