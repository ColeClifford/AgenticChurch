#!/bin/bash
# Reset the backup history, so that redacted or deleted text is gone from the backups too.
#
# Litestream's backups in R2 keep older versions of the database, and so does its local
# state next to the database. This stops Litestream, deletes both, and starts it again,
# which takes a fresh backup of the database as it is now. The live database is untouched;
# for the minute or so this takes, changes aren't being backed up.
#
# Run on the droplet, from ~/AgenticChurch:   deploy/reset-backups.sh
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml "$@"; }

set -a; . ./.env; set +a
: "${R2_ACCOUNT_ID:?missing from .env}" "${R2_BUCKET:?missing from .env}"
: "${R2_ACCESS_KEY_ID:?missing from .env}" "${R2_SECRET_ACCESS_KEY:?missing from .env}"

r2() {
  docker run --rm \
    -e AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" -e AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
    -e AWS_DEFAULT_REGION=auto \
    amazon/aws-cli:2.22.35 --endpoint-url "https://$R2_ACCOUNT_ID.r2.cloudflarestorage.com" "$@"
}

echo "This deletes every backup of the database in s3://$R2_BUCKET/church.db/ and starts a fresh one."
read -r -p "Type 'reset' to continue: " answer
[ "$answer" = "reset" ] || { echo "Nothing changed."; exit 1; }

compose stop litestream
r2 s3 rm "s3://$R2_BUCKET/church.db/" --recursive --only-show-errors
compose exec -T api rm -rf /data/.church.db-litestream
compose start litestream

echo "Waiting for the fresh backup..."
for _ in $(seq 1 30); do
  if compose logs --since 2m litestream 2>&1 | grep -q "snapshot complete"; then
    compose exec -T api python -m src.admin mark-backups-reset
    echo "Done: the backups now hold only the database as it is now."
    exit 0
  fi
  sleep 2
done
echo "Litestream hasn't reported a fresh snapshot yet. Check: docker compose ... logs litestream" >&2
exit 1
