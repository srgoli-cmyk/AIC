#!/usr/bin/env bash
# Starts the database, the backend and the UI together. Ctrl+C stops all three; the data is kept.
set -uo pipefail
cd "$(dirname "$0")/.."

VENV="${VENV:-.venv}"
PORT="${PORT:-8000}"
BACKEND_URL="${BACKEND_URL:-http://localhost:$PORT}"
UI_PORT=8501
ROOT="$PWD"

fail() {
  echo "$1" >&2
  exit 1
}

[ -x "$VENV/bin/uvicorn" ] || fail "Not set up yet: run make setup first."
docker info >/dev/null 2>&1 || fail "Docker is not running: start Docker Desktop and try again."
if command -v lsof >/dev/null; then
  for port in "$PORT" "$UI_PORT"; do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null; then
      echo "Port $port is already in use:" >&2
      lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2
      exit 1
    fi
  done
fi

backend=""
ui=""
stopping=""
stop() {
  [ -n "$stopping" ] && return
  stopping=1
  trap '' INT TERM
  echo
  echo "Stopping..."
  [ -n "$ui" ] && kill "$ui" 2>/dev/null
  [ -n "$backend" ] && kill "$backend" 2>/dev/null
  wait 2>/dev/null
  docker compose stop db >/dev/null 2>&1
  echo "Stopped. Your data is kept."
}
trap 'stop; exit 0' INT TERM
trap stop EXIT

echo "Starting the database..."
docker compose up -d --wait db >/dev/null || fail "The database did not start."

echo "Starting the backend on $BACKEND_URL (the first start takes a little longer)..."
(cd backend && exec "$ROOT/$VENV/bin/uvicorn" app.main:app --port "$PORT") &
backend=$!

for _ in $(seq 1 180); do
  curl -fsS "$BACKEND_URL/health" >/dev/null 2>&1 && break
  kill -0 "$backend" 2>/dev/null || fail "The backend stopped while starting; see the messages above."
  sleep 1
done
curl -fsS "$BACKEND_URL/health" >/dev/null 2>&1 || fail "The backend did not answer in 3 minutes."

BACKEND_URL="$BACKEND_URL" "$VENV/bin/streamlit" run ui/app.py --server.address localhost \
  --server.port "$UI_PORT" --server.headless true --browser.gatherUsageStats false &
ui=$!

echo
echo "Ready. Open http://localhost:$UI_PORT  (Ctrl+C stops everything)"

# `wait -n` isn't available in the bash that ships with macOS.
while kill -0 "$backend" 2>/dev/null && kill -0 "$ui" 2>/dev/null; do
  sleep 1
done
echo "The backend or the UI stopped; see the messages above." >&2
exit 1
