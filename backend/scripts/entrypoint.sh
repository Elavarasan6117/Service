#!/usr/bin/env bash
# Container entrypoint: wait for the database, migrate, then serve.
set -euo pipefail

echo "[entrypoint] waiting for PostgreSQL at ${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}"
for attempt in $(seq 1 60); do
  if python - <<'PY' 2>/dev/null
import os, socket, sys
host = os.environ.get("POSTGRES_HOST", "postgres")
port = int(os.environ.get("POSTGRES_PORT", "5432"))
s = socket.create_connection((host, port), timeout=2)
s.close()
PY
  then
    echo "[entrypoint] database reachable"
    break
  fi
  if [ "$attempt" -eq 60 ]; then
    echo "[entrypoint] database not reachable after 60 attempts; aborting" >&2
    exit 1
  fi
  sleep 2
done

# Migrations run before workers start, so no worker ever serves traffic against
# a schema it does not expect. With multiple replicas, Alembic's version-table
# lock makes concurrent runners safe: one migrates, the rest wait.
echo "[entrypoint] applying database migrations"
alembic upgrade head

if [ "${RUN_SEED_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] seeding baseline data"
  python scripts/seed.py ${SEED_ARGS:-}
fi

WORKERS="${WEB_CONCURRENCY:-4}"
echo "[entrypoint] starting gunicorn with ${WORKERS} uvicorn worker(s)"
exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers "${WORKERS}" \
  --bind 0.0.0.0:8000 \
  --timeout 60 \
  --graceful-timeout 30 \
  --keep-alive 65 \
  --access-logfile - \
  --error-logfile - \
  --log-level "${LOG_LEVEL_GUNICORN:-info}"
