.PHONY: up down db test-db dev test lint format typecheck migrate revision check

# --- Docker ---
up:
	docker compose up --build -d
down:
	docker compose down
db:
	docker compose up -d postgres
# Create the dedicated test database (idempotent). Tests run against this, never dev data.
test-db:
	docker compose exec postgres psql -U notes -d notes_rag -tc "SELECT 1 FROM pg_database WHERE datname='notes_rag_test'" | grep -q 1 || docker compose exec postgres psql -U notes -d notes_rag -c "CREATE DATABASE notes_rag_test OWNER notes;"

# --- Backend (via uv) ---
dev:
	cd backend && uv run uvicorn app.main:app --reload --port 8000
test:
	cd backend && uv run pytest
lint:
	cd backend && uv run ruff check .
format:
	cd backend && uv run ruff format .
typecheck:
	cd backend && uv run mypy app
migrate:
	cd backend && uv run alembic upgrade head
revision:
	cd backend && uv run alembic revision --autogenerate -m "$(m)"
check: lint typecheck test
