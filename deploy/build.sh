#!/usr/bin/env bash
set -euo pipefail

# Invoke from the project root with: bash deploy/build.sh
# No database writes here: persistent disks are unavailable during Render builds.
python -m pip install -r requirements-production.txt
python manage.py collectstatic --noinput
python manage.py check --deploy --fail-level WARNING
