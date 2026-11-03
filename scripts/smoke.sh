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

echo "== ask (501 expected until step 4)"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' \
  -d '{"course_id":"canvas-78600","question":"How do we differentiate a product?"}')
echo "status $code"
[[ "$code" == "501" || "$code" == "200" ]]

echo "SMOKE OK"
