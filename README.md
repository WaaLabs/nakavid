# NakaVid

Django + Postgres + Caddy bootstrap for local development.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker + Docker Compose

## Local development

1. Copy env values:

   ```bash
   cp .env.example .env
   ```

2. Start infra services (Postgres + Caddy only):

   ```bash
   docker compose up -d postgres caddy
   ```

3. Install dependencies and run checks from host:

   ```bash
   uv sync --all-groups
   uv run python3 manage.py check
   uv run pytest
   uv run ruff check .
   uv run black --check .
   ```

Django and the pipeline worker run on the host, not in Docker.

## Storage root

Set `NAKAVID_STORAGE_ROOT` to choose where media files live. Default in dev is `./data/nakavid/`.
