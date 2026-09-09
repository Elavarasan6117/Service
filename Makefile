.PHONY: help up down build logs test lint migrate seed backup shell psql redis clean fmt

help:
	@echo "Chennai Serviceability — common tasks"
	@echo ""
	@echo "  make up        Start the full stack (build if needed)"
	@echo "  make down      Stop the stack"
	@echo "  make logs      Follow backend logs"
	@echo "  make test      Run the backend test suite"
	@echo "  make lint      Ruff + mypy on the backend, tsc on the frontend"
	@echo "  make migrate   Apply database migrations"
	@echo "  make seed      Seed admin, warehouse, routes (+ --demo locations)"
	@echo "  make backup    Take a database backup"
	@echo "  make psql      Open a psql shell"
	@echo "  make monitor   Start Prometheus and Grafana"

up:
	docker compose up -d --build
	@echo "Dashboard: http://localhost:8080   API docs: http://localhost:8000/api/v1/docs"

down:
	docker compose down

build:
	docker compose build --pull

logs:
	docker compose logs -f backend

test:
	cd backend && python -m pytest

test-cov:
	cd backend && python -m pytest --cov=app --cov-report=term-missing

lint:
	cd backend && ruff check app tests && mypy app --ignore-missing-imports || true
	cd frontend && npx tsc --noEmit

fmt:
	cd backend && ruff format app tests && ruff check --fix app tests

migrate:
	docker compose exec backend alembic upgrade head

migrate-sql:
	docker compose run --rm backend alembic upgrade head --sql

seed:
	docker compose exec backend python scripts/seed.py --demo --count 200

backup:
	./scripts/backup.sh

psql:
	docker compose exec postgres psql -U $${POSTGRES_USER:-serviceability} -d $${POSTGRES_DB:-serviceability}

redis:
	docker compose exec redis redis-cli

shell:
	docker compose exec backend bash

monitor:
	docker compose --profile monitoring up -d
	@echo "Prometheus: http://localhost:9090   Grafana: http://localhost:3001"

clean:
	docker compose down -v
	@echo "Volumes removed. All data is gone."
