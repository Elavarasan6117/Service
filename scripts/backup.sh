#!/usr/bin/env bash
# Compressed logical backup of the serviceability database.
#
# Run before every deployment and on a schedule (hourly is reasonable).
# A backup you have never restored is not a backup -- restore into QA quarterly.
set -euo pipefail

cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a

DB_USER="${POSTGRES_USER:-serviceability}"
DB_NAME="${POSTGRES_DB:-serviceability}"
TARGET="${BACKUP_DIR}/${DB_NAME}_${STAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[backup] dumping ${DB_NAME} -> ${TARGET}"
docker compose exec -T postgres \
  pg_dump -U "$DB_USER" -d "$DB_NAME" --clean --if-exists --create \
  | gzip -9 > "$TARGET"

SIZE_BYTES=$(stat -c '%s' "$TARGET" 2>/dev/null || stat -f '%z' "$TARGET")
if [ "$SIZE_BYTES" -lt 4096 ]; then
  echo "[backup] FAILED: dump is only ${SIZE_BYTES} bytes, which cannot be a real database." >&2
  rm -f "$TARGET"
  exit 1
fi

# Prove the archive is not truncated before trusting it.
if ! gzip -t "$TARGET"; then
  echo "[backup] FAILED: gzip integrity check did not pass." >&2
  exit 1
fi

echo "[backup] ok: $(du -h "$TARGET" | cut -f1)"

echo "[backup] pruning local backups older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -type f -mtime "+${RETENTION_DAYS}" -delete

# Copy offsite here (S3, rsync, whatever your org uses). A backup that lives
# only on the host it was taken from does not survive losing that host.
if [ -n "${BACKUP_OFFSITE_CMD:-}" ]; then
  echo "[backup] running offsite copy"
  eval "${BACKUP_OFFSITE_CMD} ${TARGET}"
fi
