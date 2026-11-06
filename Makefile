.PHONY: setup db db-reset dev ui web start check smoke

VENV ?= .venv
PY := $(VENV)/bin/python
PORT ?= 8000
BACKEND_URL ?= http://localhost:$(PORT)

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install -r backend/requirements-ml.txt
	$(PY) -m pip install -r ui/requirements.txt
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

ui:
	BACKEND_URL="$(BACKEND_URL)" $(VENV)/bin/streamlit run ui/app.py --server.address localhost --server.headless true --browser.gatherUsageStats false

web:
	python3 -m http.server 5173 --bind 127.0.0.1 --directory web

start:
	VENV="$(VENV)" PORT="$(PORT)" BACKEND_URL="$(BACKEND_URL)" bash scripts/start.sh

check:
	PATH="$(abspath $(VENV))/bin:$$PATH" bash scripts/check_all.sh

smoke:
	BASE_URL="$${BASE_URL:-http://localhost:$(PORT)}" bash scripts/smoke.sh
