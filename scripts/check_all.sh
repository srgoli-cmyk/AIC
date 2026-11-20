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

echo "== extension: production build"
prod_zip=$(mktemp -d)/extension-prod.zip
python3 scripts/build_extension_prod.py --domain ailecture.app --out "$prod_zip"
python3 scripts/check_manifest.py --prod "$prod_zip" --domain ailecture.app
team_zip=$(dirname "$prod_zip")/extension-team.zip
python3 scripts/build_extension_prod.py --domain ailecture.app --team --out "$team_zip"
python3 scripts/check_manifest.py --prod "$team_zip" --domain ailecture.app --team

echo "== extension: payload matches the contract"
node extension/tests/emit_payload.mjs | python3 scripts/validate_payload.py

echo "== scripts: console sync snippet (mocked network)"
node --check scripts/console_sync.js
node --test scripts/console_sync.test.mjs

echo "== web: syntax and render tests"
for f in web/src/*.js web/site/*.js; do node --check "$f"; done
node --test web/tests/*.test.mjs

echo "== scripts: shell syntax"
for f in scripts/*.sh; do bash -n "$f"; done

echo "ALL CHECKS PASSED"
