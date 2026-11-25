# One image for both halves of NakaVid: the web app (gunicorn) and the
# pipeline worker (manage.py run_worker). deploy/entrypoint.sh picks which.
# The React island bundles are committed under static/islands/, so no Node
# build happens here.
FROM python:3.12-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 568 is TrueNAS's "apps" user, so files the containers write on a host-path
# dataset are owned by the same account TrueNAS apps use.
RUN groupadd --gid 568 nakavid && useradd --uid 568 --gid 568 --no-create-home nakavid

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    NAKAVID_SERVE_STATIC=1 \
    NAKAVID_STORAGE_ROOT=/srv/nakavid \
    # librosa's numba kernels cache next to their source by default, which is
    # read-only for the runtime user.
    NUMBA_CACHE_DIR=/tmp/numba-cache \
    HOME=/tmp

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN python manage.py collectstatic --noinput \
    && chmod +x deploy/entrypoint.sh

USER 568:568
EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["web"]
