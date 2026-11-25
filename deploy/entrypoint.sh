#!/bin/sh
# Container entrypoint. One image, three roles:
#   web          apply migrations, then serve the CMS with gunicorn
#   worker       run the pipeline worker
#   healthcheck  exit 0 if the web app answers (used by compose)
# Anything else is run as a command, e.g. `python manage.py createsuperuser`.
set -eu

case "${1:-web}" in
web)
    python manage.py migrate --noinput
    # gthread: uploads arrive as resumable chunks, so no request is long, but
    # threads keep a slow chunk from blocking page loads.
    exec gunicorn config.wsgi \
        --bind 0.0.0.0:8000 \
        --worker-class gthread \
        --workers "${GUNICORN_WORKERS:-2}" \
        --threads "${GUNICORN_THREADS:-4}" \
        --timeout "${GUNICORN_TIMEOUT:-120}" \
        --access-logfile -
    ;;
worker)
    exec python manage.py run_worker
    ;;
healthcheck)
    # Ask as the first allowed host, so ALLOWED_HOSTS doesn't need "localhost".
    exec python - <<'EOF'
import os
import sys
import urllib.request

host = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost").split(",")[0]
if host in ("", "*") or host.startswith("."):
    host = "localhost"
request = urllib.request.Request("http://127.0.0.1:8000/accounts/login/", headers={"Host": host})
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception:
    sys.exit(1)
EOF
    ;;
*)
    exec "$@"
    ;;
esac
