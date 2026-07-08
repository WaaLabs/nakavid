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
   ruff check .
   uv run black --check .
   ```

Django and the pipeline worker run on the host, not in Docker.

## Storage root

Set `NAKAVID_STORAGE_ROOT` to choose where media files live. Default in dev is `./data/nakavid/`.

## Playback (Caddy stream handoff)

Clip playback requires Caddy in front of Django so the proxy can intercept `X-Accel-Redirect` and range-serve files from disk.

1. Start infra (if not already running):

   ```bash
   docker compose up -d postgres caddy
   ```

2. Run Django on port 8000:

   ```bash
   uv run python3 manage.py runserver 0.0.0.0:8000
   ```

3. Open `http://localhost/clips/` (Caddy on port 80 → Django). Upload a Type B clip at `/ingest/type-b/`, then play it inline from the clips grid.

The `clip_stream` view checks session auth and returns an empty body with `X-Accel-Redirect` pointing at the on-disk path under `/srv/nakavid`. Caddy's `handle_response` block serves the file with HTTP range support. Unauthenticated requests to `/clips/<id>/stream/` are redirected to login by Django before any bytes are handed off.
