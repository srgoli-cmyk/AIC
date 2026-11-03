#!/usr/bin/env bash
# All automated gates. Run this before saying "done"; CI-style, fails fast.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== backend: lint"
(cd backend && ruff check . && ruff format --check .)

echo "== backend: tests (incl. contract + security)"
(cd backend && python3 -m pytest -q)

echo "== extension: syntax"
for f in extension/*.js extension/src/*.js; do node --check "$f"; done

echo "== extension: unit tests (incl. no-secrets test)"
(cd extension && node --test tests/*.test.mjs)

echo "== extension: manifest permissions"
python3 scripts/check_manifest.py

echo "== extension: payload matches the contract"
node extension/tests/emit_payload.mjs | python3 scripts/validate_payload.py

echo "== scripts: console sync snippet (mocked network)"
node --check scripts/console_sync.js
node --test scripts/console_sync.test.mjs

echo "ALL CHECKS PASSED"
