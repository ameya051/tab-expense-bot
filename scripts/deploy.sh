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

# Fail the deploy (and the CI run) unless the app comes up and reaches the DB.
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health 2>/dev/null | grep -q '"db":"ok"'; then
    echo "app is healthy"
    exit 0
  fi
  sleep 2
done
echo "app did not become healthy within 60s" >&2
compose logs --tail=50 app >&2
exit 1
