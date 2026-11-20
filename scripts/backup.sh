#!/usr/bin/env bash
# Daily database backup on the production server: a compressed pg_dump of the database in the
# docker-compose.prod.yml stack, keeping the newest 7. Run from cron (DEPLOY.md):
#
#   15 3 * * * /home/deploy/aic/scripts/backup.sh >> /home/deploy/backups/backup.log 2>&1
#
# The dumps hold every student's synced courses, so they are readable by their owner only. A backup
# on the same disk does not survive losing the server: copy them off regularly, for example from
# your own computer:
#
#   rsync -av deploy@ailecture.app:backups/ ./aic-backups/
#
# Restore (into an empty database, with the backend stopped):
#   gunzip -c aic-YYYYmmdd-HHMMSS.sql.gz | docker compose -f docker-compose.prod.yml exec -T db psql -U aic -d aic
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
KEEP="${KEEP:-7}"
COMPOSE=(docker compose -f docker-compose.prod.yml)

umask 077
mkdir -p "$BACKUP_DIR"
stamp=$(date -u +%Y%m%d-%H%M%S)
out="$BACKUP_DIR/aic-$stamp.sql.gz"
tmp="$out.partial"

# --clean --if-exists: the dump can be replayed into an existing database.
"${COMPOSE[@]}" exec -T db pg_dump -U aic -d aic --clean --if-exists --no-owner | gzip -9 > "$tmp"
# A dump that is empty or cut off must not replace a good one.
gzip -t "$tmp"
[ "$(gunzip -c "$tmp" | head -c 1 | wc -c)" -eq 1 ] || { echo "empty dump" >&2; rm -f "$tmp"; exit 1; }
mv "$tmp" "$out"
echo "$(date -u +%FT%TZ) wrote $out ($(du -h "$out" | cut -f1))"

# Keep the newest $KEEP dumps.
ls -1t "$BACKUP_DIR"/aic-*.sql.gz | tail -n +"$((KEEP + 1))" | while read -r old; do
  rm -f -- "$old"
  echo "removed $old"
done
