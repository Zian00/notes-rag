FROM python:3.13-slim-bookworm

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# OCR system dependencies: tesseract (OCR engine) + its English language data +
# poppler (pdf2image rasterization for the scanned-PDF fallback).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (layer caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY app ./app
COPY alembic.ini ./

EXPOSE 8000

# Run migrations, apply procrastinate's own job-queue schema, then start the server.
# --no-sync stops uv re-resolving at runtime. Invoked as `python -m procrastinate`
# (not the bare `procrastinate` console script) — only the module form puts the
# CWD (/app, where the `app` package lives) on sys.path, so the dotted --app
# path resolves; the bare script fails with "Could not load app".
# `schema --apply` isn't idempotent (errors on already-existing types/tables),
# so `|| true` tolerates container restarts after it's already been applied once.
CMD ["sh", "-c", "uv run --no-sync alembic upgrade head && (uv run --no-sync python -m procrastinate --app app.jobs.ingestion_tasks.app schema --apply || true) && uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port 8000"]
