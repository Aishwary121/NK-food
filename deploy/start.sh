#!/usr/bin/env bash
set -euo pipefail
umask 077

# The environment must already contain production settings and a unique secret.
python deploy/prepare_runtime.py
python manage.py migrate --noinput
python manage.py seed_data --menu-only
exec python -m gunicorn nk_foodcourt.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-1}" \
    --threads "${GUNICORN_THREADS:-2}" \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
