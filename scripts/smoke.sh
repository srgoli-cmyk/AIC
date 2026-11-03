#!/usr/bin/env bash
# End-to-end smoke test against a RUNNING backend (make dev). Owned by the maintainer.
# Makes one real call to the answer service.
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="${BASE_URL:-http://localhost:8000}"

COURSE=canvas-999999
SMOKE_DB="${SMOKE_DB:-aic}"

# Another app can hold the port and answer /health, so make sure this is our API before sending.
echo "== is this our backend?"
api=$(curl -fsS "$BASE/openapi.json")
[[ "$api" == *'"title":"AI Lecture Search"'* ]] || {
  echo "$BASE is not the AI Lecture Search backend; set BASE_URL" >&2
  exit 1
}

# The fixture course is synthetic: never leave it behind in the database.
cleanup() {
  docker compose exec -T db psql -U aic -d "$SMOKE_DB" -qc \
    "DELETE FROM courses WHERE course_id = '$COURSE'" >/dev/null \
    || echo "could not remove $COURSE from $SMOKE_DB" >&2
  # Check through the API: this also catches a backend running on a different database.
  if [[ "$(curl -fsS "$BASE/courses")" == *"\"course_id\":\"$COURSE\""* ]]; then
    echo "$COURSE is still stored; set SMOKE_DB to the backend's database" >&2
    status=1
  fi
}
trap 'status=$?; cleanup; exit $status' EXIT

echo "== health"
curl -fsS "$BASE/health"; echo

echo "== sync fixture"
curl -fsS -X POST "$BASE/sync" -H 'Content-Type: application/json' \
  --data @contracts/fixtures/sync_sample.json; echo

echo "== courses"
curl -fsS "$BASE/courses"; echo

echo "== wait for indexing (first run loads the embedding model)"
indexed=""
for _ in $(seq 1 120); do
  if curl -fsS "$BASE/courses" | grep -q '"course_id":"'"$COURSE"'"[^}]*"indexed":true'; then
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
answer=$(curl -fsS -X POST "$BASE/ask" -H 'Content-Type: application/json' \
  -d '{"course_id":"'"$COURSE"'","question":"How do we differentiate a product?"}')
[[ "$answer" == *'"sources":[{'* ]] || {
  echo "/ask returned no sources" >&2
  exit 1
}
echo "answered with sources"

echo "SMOKE OK"
