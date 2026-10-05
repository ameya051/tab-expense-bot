#!/usr/bin/env bash
# Pull the latest code, run migrations, and (re)start the production stack.
# Usage (on the server): ~/expense-bot/scripts/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."
compose() { docker compose -f compose.prod.yml "$@"; }

git pull --ff-only
compose build app
compose up -d db
# Migrate before the new app version starts serving.
compose run --rm app alembic upgrade head
compose up -d
docker image prune -f
compose ps
