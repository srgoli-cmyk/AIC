.PHONY: setup db db-reset dev web start check smoke claim-local-data dev-session

VENV ?= .venv
PY := $(VENV)/bin/python
PORT ?= 8000

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install -r backend/requirements-ml.txt
	$(PY) -c "from sentence_transformers import SentenceTransformer as S; S('BAAI/bge-small-en-v1.5')"
	@test -f .env || cp .env.example .env
	@echo "Activate with: source $(VENV)/bin/activate"

db:
	docker compose up -d db

db-reset:
	docker compose down -v
	docker compose up -d db

dev:
	cd backend && ../$(VENV)/bin/uvicorn app.main:app --reload --port $(PORT)

web:
	python3 -m http.server 5173 --bind 127.0.0.1 --directory web

start:
	VENV="$(VENV)" PORT="$(PORT)" bash scripts/start.sh

check:
	PATH="$(abspath $(VENV))/bin:$$PATH" bash scripts/check_all.sh

smoke:
	BASE_URL="$${BASE_URL:-http://localhost:$(PORT)}" bash scripts/smoke.sh

# One time, after your first sign-in: move the data synced before login existed to your account.
# Add DRY_RUN=1 to only count what would move.
claim-local-data:
	@test -n "$(EMAIL)" || { echo "Usage: make claim-local-data EMAIL=you@ucsd.edu [DRY_RUN=1]"; exit 2; }
	cd backend && ../$(VENV)/bin/python -m app.claim_local_data --email "$(EMAIL)" $(if $(DRY_RUN),--dry-run,)

# Local testing only: print a short-lived Bearer token for EMAIL (an @ucsd.edu address), creating the
# account if needed. It writes straight to the database, so it needs the access that already means
# full control; it is never reachable over HTTP. TTL_HOURS=1 by default, 24 at most.
dev-session:
	@test -n "$(EMAIL)" || { echo "Usage: make dev-session EMAIL=smoke-test@ucsd.edu [TTL_HOURS=1]" >&2; exit 2; }
	@cd backend && ../$(VENV)/bin/python -m app.dev_session --email "$(EMAIL)" $(if $(TTL_HOURS),--ttl-hours $(TTL_HOURS),)
