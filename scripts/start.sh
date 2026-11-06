#!/usr/bin/env bash
# Starts the database and the backend, which also serves the search page. Ctrl+C stops both; the
# data is kept.
set -uo pipefail
cd "$(dirname "$0")/.."

VENV="${VENV:-.venv}"
PORT="${PORT:-8000}"
BASE="http://localhost:$PORT"
ROOT="$PWD"

fail() {
  echo "$1" >&2
  exit 1
}

[ -x "$VENV/bin/uvicorn" ] || fail "Not set up yet: run make setup first."
docker info >/dev/null 2>&1 || fail "Docker is not running: start Docker Desktop and try again."
if command -v lsof >/dev/null && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null; then
  echo "Port $PORT is already in use:" >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
  exit 1
fi

backend=""
stopping=""
stop() {
  [ -n "$stopping" ] && return
  stopping=1
  trap '' INT TERM
  echo
  echo "Stopping..."
  [ -n "$backend" ] && kill "$backend" 2>/dev/null
  wait 2>/dev/null
  docker compose stop db >/dev/null 2>&1
  echo "Stopped. Your data is kept."
}
trap 'stop; exit 0' INT TERM
trap stop EXIT

echo "Starting the database..."
docker compose up -d --wait db >/dev/null || fail "The database did not start."

echo "Starting the backend on $BASE (the first start takes a little longer)..."
(cd backend && exec "$ROOT/$VENV/bin/uvicorn" app.main:app --port "$PORT") &
backend=$!

for _ in $(seq 1 180); do
  curl -fsS "$BASE/health" >/dev/null 2>&1 && break
  kill -0 "$backend" 2>/dev/null || fail "The backend stopped while starting; see the messages above."
  sleep 1
done
curl -fsS "$BASE/health" >/dev/null 2>&1 || fail "The backend did not answer in 3 minutes."

echo
echo "Ready. Open $BASE/app/  (Ctrl+C stops everything)"

# `wait -n` isn't available in the bash that ships with macOS.
while kill -0 "$backend" 2>/dev/null; do
  sleep 1
done
echo "The backend stopped; see the messages above." >&2
exit 1
