.PHONY: up down db dev test lint format typecheck migrate revision check

# --- Docker ---
up:
	docker compose up --build -d
down:
	docker compose down
db:
	docker compose up -d postgres

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
