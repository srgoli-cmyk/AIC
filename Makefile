.PHONY: setup db db-reset dev check smoke

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

check:
	PATH="$(abspath $(VENV))/bin:$$PATH" bash scripts/check_all.sh

smoke:
	BASE_URL="$${BASE_URL:-http://localhost:$(PORT)}" bash scripts/smoke.sh
