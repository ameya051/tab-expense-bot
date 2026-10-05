#!/usr/bin/env bash
# Dump the production database to S3. Run nightly from cron:
#   0 3 * * * ~/expense-bot/scripts/backup_db.sh >> ~/backup.log 2>&1
# Credentials come from the EC2 instance role; the bucket name from .env.
set -euo pipefail
export PATH="/usr/local/bin:/usr/bin:/bin:/snap/bin:$PATH"   # cron has a minimal PATH

cd "$(dirname "$0")/.."
BACKUP_BUCKET="$(grep -E '^BACKUP_BUCKET=' .env | cut -d= -f2-)"
: "${BACKUP_BUCKET:?set BACKUP_BUCKET in .env}"

key="expense_bot/$(date +%F_%H%M).dump"
docker compose -f compose.prod.yml exec -T db pg_dump -U postgres -Fc expense_bot \
  | aws s3 cp - "s3://${BACKUP_BUCKET}/${key}"
echo "$(date -Is) backup uploaded to s3://${BACKUP_BUCKET}/${key}"
