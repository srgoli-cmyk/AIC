#!/usr/bin/env bash
# End-to-end smoke test against a RUNNING backend (make dev). Owned by the maintainer.
# Makes one real call to the answer service. Every endpoint needs a signed-in student, so it makes a
# short-lived session for a dedicated test account straight in the database (app.dev_session) and
# deletes that account, with its sessions and the fixture course, when it is done.
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="${BASE_URL:-http://localhost:8000}"

COURSE=canvas-999999
SMOKE_DB="${SMOKE_DB:-aic}"
SMOKE_EMAIL="smoke-test@ucsd.edu"
VENV="${VENV:-.venv}"

# Another app can hold the port and answer /health, so make sure this is our API before sending.
echo "== is this our backend?"
api=$(curl -fsS "$BASE/openapi.json")
[[ "$api" == *'"title":"AI Lecture Search"'* ]] || {
  echo "$BASE is not the AI Lecture Search backend; set BASE_URL" >&2
  exit 1
}

echo "== session for $SMOKE_EMAIL in $SMOKE_DB"
TOKEN=$(cd backend && DATABASE_URL="postgresql://aic:aic@localhost:5432/$SMOKE_DB" \
  "../$VENV/bin/python" -m app.dev_session --email "$SMOKE_EMAIL" --ttl-hours 1)
[[ -n "$TOKEN" ]] || { echo "could not make a session" >&2; exit 1; }
# The token goes to curl through a private config file, not the command line, where ps would show it.
AUTH_CONFIG=$(mktemp)
chmod 600 "$AUTH_CONFIG"
printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" > "$AUTH_CONFIG"
AUTH=(-K "$AUTH_CONFIG")

# The test account and its fixture course are synthetic: never leave them behind.
cleanup() {
  # Check through the API first: this also catches a backend running on a different database.
  if [[ "$(curl -fsS "${AUTH[@]}" "$BASE/courses" 2>/dev/null)" != *"\"course_id\":\"$COURSE\""* ]]; then
    echo "the backend does not see the smoke course; set SMOKE_DB to the backend's database" >&2
    status=1
  fi
  # Deleting the account removes its sessions and courses too (ON DELETE CASCADE).
  docker compose exec -T db psql -U aic -d "$SMOKE_DB" -qc \
    "DELETE FROM users WHERE email = '$SMOKE_EMAIL'" >/dev/null \
    || { echo "could not remove $SMOKE_EMAIL from $SMOKE_DB" >&2; status=1; }
  rm -f "$AUTH_CONFIG"
}
trap 'status=$?; cleanup; exit $status' EXIT

echo "== health"
curl -fsS "$BASE/health"; echo

echo "== sync fixture"
curl -fsS -X POST "$BASE/sync" "${AUTH[@]}" -H 'Content-Type: application/json' \
  --data @contracts/fixtures/sync_sample.json; echo

echo "== courses"
curl -fsS "${AUTH[@]}" "$BASE/courses"; echo

echo "== wait for indexing (first run loads the embedding model)"
indexed=""
for _ in $(seq 1 120); do
  if curl -fsS "${AUTH[@]}" "$BASE/courses" | grep -q '"course_id":"'"$COURSE"'"[^}]*"indexed":true'; then
    indexed=yes
    break
  fi
  sleep 1
done
if [[ -z "$indexed" ]]; then
  echo "course not indexed after 120 s; check the backend log and run make setup" >&2
  exit 1
fi
echo "indexed"

echo "== ask (one real call to the answer service)"
answer=$(curl -fsS -X POST "$BASE/ask" "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d '{"course_id":"'"$COURSE"'","question":"How do we differentiate a product?"}')
[[ "$answer" == *'"sources":[{'* ]] || {
  echo "/ask returned no sources" >&2
  exit 1
}
echo "answered with sources"

echo "SMOKE OK"
