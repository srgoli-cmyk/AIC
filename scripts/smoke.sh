#!/usr/bin/env bash
# End-to-end smoke test against a RUNNING backend (make dev). Owned by the maintainer.
# Grows with the roadmap: step 4 adds a real /ask check.
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="${BASE_URL:-http://localhost:8000}"

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
  if curl -fsS "$BASE/courses" | grep -q '"course_id":"canvas-999999"[^}]*"indexed":true'; then
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

echo "== ask (501 expected until step 4)"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' \
  -d '{"course_id":"canvas-999999","question":"How do we differentiate a product?"}')
echo "status $code"
[[ "$code" == "501" || "$code" == "200" ]]

echo "SMOKE OK"
