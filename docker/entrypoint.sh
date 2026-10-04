#!/bin/sh
# Startup sequence: migrate -> (optionally) seed -> serve. Fails fast: `set -e` stops on any error.
set -e
alembic upgrade head
[ "${SEED_USERS:-0}" = "1" ] && python -m app.cli seed-users
[ "${SEED_EPISODES:-0}" = "1" ] && python -m app.cli import-episodes seed/episodes.csv
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log --proxy-headers
